"""Typing checks on Linux (test version): reads the focused text box in other apps through
the desktop's accessibility service (AT-SPI), and writes replacements back into it.

Works for apps that share their text boxes with AT-SPI: GTK and GNOME apps, Qt/KDE apps,
LibreOffice, Firefox, and Chrome/Chromium/Electron apps once accessibility is switched on
(org.gnome.desktop.interface toolkit-accessibility, set from the tray). Password boxes and
the apps in the settings' deny list are never read.

On Wayland apps can't tell where their windows are on screen, so the badge sits in the
bottom-right corner instead of next to the text box.

All AT-SPI calls happen on this one thread, on a private GLib main context (the tray runs
GTK's own loop on another thread). Same interface as capture.FocusWatcher: the UI thread sends
commands through `commands`; results go to the shared events queue.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import List, Optional, Tuple

from . import platform as plat
from .capture import Edit, FieldState, _locate

log = logging.getLogger(__name__)

A11Y_SCHEMA = ("org.gnome.desktop.interface", "toolkit-accessibility")


def _run(cmd, timeout: float = 3.0) -> Optional[str]:
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
                           env=plat.child_env())
    except Exception:
        return None
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None


def _bus_enabled() -> Optional[bool]:
    out = _run(["gdbus", "call", "--session", "--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus",
                "--method", "org.freedesktop.DBus.Properties.Get", "org.a11y.Status", "IsEnabled"])
    if out is None:
        return None
    return "true" in out


def accessibility_enabled() -> bool:
    """Do apps share their text boxes (AT-SPI on, and the GNOME switch for Chrome & co.)?"""
    if _bus_enabled():
        return True
    return _run(["gsettings", "get", *A11Y_SCHEMA]) == "true"


def enable_accessibility() -> bool:
    """Switch AT-SPI on for this user (no root). Apps started afterwards share their text boxes."""
    ok = _run(["gsettings", "set", *A11Y_SCHEMA, "true"]) is not None
    ok = _run(["gdbus", "call", "--session", "--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus",
               "--method", "org.freedesktop.DBus.Properties.Set", "org.a11y.Status", "IsEnabled",
               "<true>"]) is not None or ok
    return ok


def corner_rect() -> Tuple[int, int, int, int]:
    """Stand-in field area at the bottom-right of the screen (Wayland: no window positions)."""
    left, top, right, bottom = plat.work_area()
    return (right - 380, bottom - 150, right - 24, bottom - 24)


OBJECT_CHAR = "\ufffc"   # stands for a child object in an AT-SPI text (a paragraph, a link...)
BLOCK_ROLES = {"paragraph", "section", "heading", "list item", "list", "block quote", "form", "panel",
               "table row", "table cell", "article"}


def flatten_text(acc, limit: int, depth: int = 0):
    """The words of a rich text box. Chrome-based browsers describe a box like claude.ai's or
    Gmail's as one placeholder character per paragraph, with the words in child objects:
    follow each placeholder (AT-SPI hypertext links) to its child. Returns (text, pieces) with
    pieces = [(start in text, object holding the words, offset in that object, length)]."""
    t = acc.get_text_iface()
    if t is None:
        return "", []
    raw = t.get_text(0, min(t.get_character_count(), limit)) or ""
    if OBJECT_CHAR not in raw or depth > 8:
        return raw, [(0, acc, 0, len(raw))]
    hyper = acc.get_hypertext_iface()
    parts: list = []
    pieces: list = []
    pos = 0

    def add(text, owner, offset):
        nonlocal pos
        if text:
            parts.append(text)
            if owner is not None:
                pieces.append((pos, owner, offset, len(text)))
            pos += len(text)

    start = 0
    for i, ch in enumerate(raw):
        if ch != OBJECT_CHAR:
            continue
        add(raw[start:i], acc, start)
        child = None
        try:
            k = hyper.get_link_index(i) if hyper is not None else -1
            link = hyper.get_link(k) if k >= 0 else None
            child = link.get_object(0) if link is not None else None
        except Exception:
            child = None
        if child is not None:
            try:
                block = child.get_role_name() in BLOCK_ROLES
            except Exception:
                block = False
            if block and pos and not parts[-1].endswith("\n"):
                add("\n", None, 0)
            text, sub = flatten_text(child, max(0, limit - pos), depth + 1)
            for (s0, owner, off, n) in sub:
                pieces.append((pos + s0, owner, off, n))
            parts.append(text)
            pos += len(text)
        start = i + 1
        if pos >= limit:
            break
    add(raw[start:], acc, start)
    return "".join(parts)[:limit], pieces


def locate_piece(pieces, idx: int, length: int):
    """(object, offset in it) holding text[idx:idx+length] in one piece, else None."""
    for s0, owner, off, n in pieces:
        if s0 <= idx and idx + length <= s0 + n:
            return owner, off + (idx - s0)
    return None


class FocusWatcher(threading.Thread):
    POLL_TICK = 0.05   # how often AT-SPI events are handled

    def __init__(self, events: "queue.Queue", config, stop_event: threading.Event):
        super().__init__(name="FocusWatcher", daemon=True)
        self.events = events
        self.config = config
        self.stop_event = stop_event
        self.commands: "queue.Queue" = queue.Queue()
        self.own_pid = os.getpid()
        self.Atspi = None
        self._focus = None        # accessible that last gained the keyboard focus
        self._last_sig = None
        self._control = None
        self._process = ""
        self._pieces: list = []
        self.wayland = plat.session_type() == "wayland" if hasattr(plat, "session_type") else False

    # ---- thread loop ----------------------------------------------------------------
    def run(self) -> None:
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi, GLib
        except Exception as exc:
            log.info("typing checks unavailable on this system (AT-SPI): %s", exc)
            self.events.put(("typing_off", "AT-SPI (gir1.2-atspi-2.0) is not installed"))
            return
        self.Atspi = Atspi
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self._ctx = ctx
        try:
            Atspi.init()
            # after init(): init() attaches AT-SPI's D-Bus connection to the default context,
            # and events would then only arrive during our own calls
            Atspi.set_main_context(ctx)
            listener = Atspi.EventListener.new(self._on_event)
            listener.register("object:state-changed:focused")
        except Exception as exc:
            log.info("typing checks unavailable: AT-SPI didn't start (%s)", exc)
            self.events.put(("typing_off", str(exc)))
            return
        log.info("FocusWatcher (AT-SPI) started; apps share text boxes: %s", accessibility_enabled())
        next_poll = 0.0
        while not self.stop_event.is_set():
            n = 0
            while ctx.pending() and n < 200:
                ctx.iteration(False)
                n += 1
            self._drain_commands()
            if time.monotonic() >= next_poll:
                try:
                    if self.config.data.get("enabled", True):
                        self._poll()
                    else:
                        self._emit_none()
                except Exception:
                    log.debug("poll failed", exc_info=True)
                    self._emit_none()
                next_poll = time.monotonic() + max(100, int(self.config.data.get("poll_interval_ms", 300))) / 1000.0
            self.stop_event.wait(self.POLL_TICK)
        try:
            listener.deregister("object:state-changed:focused")
        except Exception:
            pass

    def _on_event(self, event) -> None:
        # only remember it: AT-SPI calls made while an event is being delivered fail; _poll()
        # reads the box (and skips SENSIQ's own windows) right after
        if event.detail1 == 1 and event.source is not None:
            self._focus = event.source

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd[0] == "replace":
                    self._apply(cmd[1])
                elif cmd[0] == "refresh":
                    self._last_sig = None
            except Exception:
                log.exception("command failed: %s", cmd[0])

    def _emit_none(self) -> None:
        if self._last_sig is not None:
            self._last_sig = None
            self._control = None
            self.events.put(("nofield",))

    # ---- reading ----------------------------------------------------------------------
    def _editable_text(self, acc) -> Optional[str]:
        """Text of an editable box, or None if it isn't something the user types into."""
        Atspi = self.Atspi
        try:
            acc.clear_cache()
        except Exception:
            pass
        role = acc.get_role()
        if role == Atspi.Role.PASSWORD_TEXT:
            return None
        states = acc.get_state_set()
        if not states.contains(Atspi.StateType.EDITABLE) or states.contains(Atspi.StateType.DEFUNCT):
            return None
        if acc.get_text_iface() is None:
            return None
        limit = int(self.config.data.get("max_chars", 20000))
        text, self._pieces = flatten_text(acc, limit)
        return text

    def _rect(self, acc) -> Tuple[int, int, int, int]:
        if not self.wayland:
            try:
                comp = acc.get_component_iface()
                if comp is not None:
                    e = comp.get_extents(self.Atspi.CoordType.SCREEN)
                    if e.width > 0 and e.height > 0 and (e.x > 0 or e.y > 0):
                        return (int(e.x), int(e.y), int(e.x + e.width), int(e.y + e.height))
            except Exception:
                pass
        return corner_rect()

    def _poll(self) -> None:
        acc = self._focus
        if acc is None:
            self._emit_none()
            return
        try:
            pid = acc.get_process_id()
        except Exception:
            self._focus = None
            self._emit_none()
            return
        if pid == self.own_pid:
            return
        proc = plat.process_name(pid)
        if proc in self.config.denied:
            self._emit_none()
            return
        text = self._editable_text(acc)
        if text is None:
            self._emit_none()
            return
        rect = self._rect(acc)
        key = (pid, id(acc), str(acc.get_role()))
        self._control, self._process = acc, proc
        sig = (key, text, rect)
        if sig != self._last_sig:
            self._last_sig = sig
            self.events.put(("field", FieldState(key, text, rect, proc)))

    # ---- writing ----------------------------------------------------------------------
    def _apply(self, edits: List[Edit]) -> None:
        acc = self._control
        ok, failed = 0, []
        if acc is None:
            failed = [e[3] for e in edits]
        else:
            for start, end, old, new in sorted(edits, key=lambda e: e[0], reverse=True):
                try:
                    if self._replace_one(acc, start, old, new):
                        ok += 1
                    else:
                        failed.append(new)
                except Exception:
                    log.debug("replace failed", exc_info=True)
                    failed.append(new)
        if failed and len(edits) == 1:
            plat.set_clipboard_text(failed[0])
        self._last_sig = None
        self.events.put(("replace_done", ok, len(failed), len(edits) == 1))

    def _replace_one(self, acc, start: int, old: str, new: str) -> bool:
        text = self._editable_text(acc) or ""
        idx = _locate(text, old, start)
        if idx < 0:
            return False
        before = text.count(old)
        spot = locate_piece(getattr(self, "_pieces", []), idx, len(old))
        if spot is None:
            return False                # the value runs across two paragraphs or links
        owner, local = spot
        editable = owner.get_editable_text_iface()
        if editable is not None:
            try:
                if editable.delete_text(local, local + len(old)) and editable.insert_text(local, new, len(new)):
                    if self._gone(acc, old, before):
                        return True
            except Exception:
                log.debug("EditableText failed", exc_info=True)
        return self._via_paste(acc, owner, local, old, new, before)

    def _gone(self, acc, old: str, before: int) -> bool:
        """Did the value disappear? Browsers may replace the box's accessible object when its
        text changes; then the object they focus next is read instead."""
        for _ in range(16):
            ctx = getattr(self, "_ctx", None)
            n = 0
            while ctx is not None and ctx.pending() and n < 100:
                ctx.iteration(False)
                n += 1
            for target in (acc, self._focus):
                if target is None:
                    continue
                try:
                    text = self._editable_text(target)
                except Exception:
                    continue          # gone; try the newly focused one
                if text is not None and text.count(old) < before:
                    if target is not acc:
                        self._control = target
                    return True
            time.sleep(0.06)
        return False

    def _via_paste(self, acc, owner, idx: int, old: str, new: str, before: int) -> bool:
        """Select the value (in the paragraph holding it) and paste over it (Ctrl+V through
        AT-SPI; X11 sessions only)."""
        if self.wayland:
            return False
        Atspi = self.Atspi
        text_iface = owner.get_text_iface()
        if text_iface is None:
            return False
        try:
            comp = acc.get_component_iface()
            focused = comp.grab_focus() if comp is not None else None
            time.sleep(0.05)
            if text_iface.get_n_selections() > 0:
                selected = text_iface.set_selection(0, idx, idx + len(old))
            else:
                selected = text_iface.add_selection(idx, idx + len(old))
        except Exception:
            log.debug("paste: selecting the value failed", exc_info=True)
            return False
        log.debug("paste: focus=%s select=%s (in %s at %d+%d)", focused, selected, owner.get_role_name(), idx, len(old))
        time.sleep(0.05)
        if not plat.set_clipboard_text(new):
            log.debug("paste: clipboard not set")
            return False
        time.sleep(0.1)
        try:
            ctrl = 37  # X11 keycode of the left Ctrl key
            keys = (Atspi.generate_keyboard_event(ctrl, None, Atspi.KeySynthType.PRESS),
                    Atspi.generate_keyboard_event(0x76, None, Atspi.KeySynthType.SYM),  # keysym 'v'
                    Atspi.generate_keyboard_event(ctrl, None, Atspi.KeySynthType.RELEASE))
        except Exception:
            log.debug("paste: Ctrl+V couldn't be sent", exc_info=True)
            return False
        gone = self._gone(acc, old, before)
        log.debug("paste: keys=%s replaced=%s", keys, gone)
        return gone
