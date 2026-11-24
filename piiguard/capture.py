"""Reads the focused text field in any app through Windows UI Automation,
and writes replacements back into it.

All UI Automation (COM) calls happen on this one thread. The UI thread talks to it
through the `commands` queue, and it reports back through the shared events queue.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import winutil

log = logging.getLogger(__name__)

Edit = Tuple[int, int, str, str]  # start, end, old text, new text


@dataclass(frozen=True)
class FieldState:
    key: tuple
    text: str
    rect: Tuple[int, int, int, int]
    process: str


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def _locate(text: str, needle: str, hint: int) -> int:
    """Index of `needle` in `text`, preferring the occurrence closest to `hint`."""
    if not needle:
        return -1
    if text[hint:hint + len(needle)] == needle:
        return hint
    best, best_d = -1, None
    i = text.find(needle)
    while i != -1:
        d = abs(i - hint)
        if best_d is None or d < best_d:
            best, best_d = i, d
        i = text.find(needle, i + 1)
    return best


class FocusWatcher(threading.Thread):
    def __init__(self, events: "queue.Queue", config, stop_event: threading.Event):
        super().__init__(name="FocusWatcher", daemon=True)
        self.events = events
        self.config = config
        self.stop_event = stop_event
        self.commands: "queue.Queue" = queue.Queue()
        self.own_pid = os.getpid()
        self.auto = None
        self._last_sig = None
        self._control = None
        self._process = ""

    # ---- thread loop ----------------------------------------------------------------
    def run(self) -> None:
        try:
            import uiautomation as auto
        except Exception:
            log.exception("uiautomation import failed")
            self.events.put(("fatal", "Windows UI Automation is not available, so PII Guard can't read text fields."))
            return
        self.auto = auto
        try:
            auto.SetGlobalSearchTimeout(1)
        except Exception:
            pass
        with auto.UIAutomationInitializerInThread(debug=False):
            while not self.stop_event.is_set():
                self._drain_commands()
                try:
                    if self.config.data.get("enabled", True):
                        self._poll()
                    else:
                        self._emit_none()
                except Exception:
                    log.debug("poll failed", exc_info=True)
                    self._emit_none()
                interval = max(100, int(self.config.data.get("poll_interval_ms", 300))) / 1000.0
                self.stop_event.wait(interval)

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
    def _pattern(self, control, pattern_id):
        try:
            return control.GetPattern(pattern_id)
        except Exception:
            return None

    def _poll(self) -> None:
        auto = self.auto
        c = auto.GetFocusedControl()
        if not c:
            self._emit_none()
            return
        pid = c.ProcessId
        if pid == self.own_pid:
            return  # our own overlay; keep the current field
        proc = winutil.process_name(pid)
        if proc in self.config.denied:
            self._emit_none()
            return
        try:
            if c.IsPassword:
                self._emit_none()
                return
        except Exception:
            pass
        text = self._read(c, proc)
        if text is None:
            self._emit_none()
            return
        r = c.BoundingRectangle
        rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        key = (pid, c.NativeWindowHandle, c.ControlType, c.ClassName, c.AutomationId)
        self._control, self._process = c, proc
        sig = (key, text, rect)
        if sig != self._last_sig:
            self._last_sig = sig
            self.events.put(("field", FieldState(key, text, rect, proc)))

    def _read(self, c, proc: str) -> Optional[str]:
        """Text of an editable control, or None if it isn't something the user types into."""
        auto = self.auto
        limit = int(self.config.data.get("max_chars", 20000))
        vp = self._pattern(c, auto.PatternId.ValuePattern)
        tp = self._pattern(c, auto.PatternId.TextPattern)
        ctype = c.ControlType

        editable = False
        if vp is not None:
            try:
                editable = not vp.IsReadOnly
            except Exception:
                editable = False
        if not editable:
            if ctype == auto.ControlType.EditControl and vp is None and tp is not None:
                editable = True
            elif ctype == auto.ControlType.DocumentControl and proc in self.config.document_editors:
                editable = True
        if not editable:
            return None

        text = ""
        if tp is not None:
            try:
                text = tp.DocumentRange.GetText(limit) or ""
            except Exception:
                text = ""
        if vp is not None:
            try:
                value = vp.Value or ""
            except Exception:
                value = ""
            if len(value) > len(text):
                text = value[:limit]
        return text

    # ---- writing ----------------------------------------------------------------------
    def _apply(self, edits: List[Edit]) -> None:
        c = self._control
        ok, failed = 0, []
        if c is None:
            failed = [e[3] for e in edits]
        else:
            for start, end, old, new in sorted(edits, key=lambda e: e[0], reverse=True):
                try:
                    if self._replace_one(c, start, old, new):
                        ok += 1
                    else:
                        failed.append(new)
                except Exception:
                    log.debug("replace failed", exc_info=True)
                    failed.append(new)
        if failed and len(edits) == 1:
            try:
                self.auto.SetClipboardText(failed[0])
            except Exception:
                pass
        self._last_sig = None  # force a fresh read
        self.events.put(("replace_done", ok, len(failed), len(edits) == 1))

    def _replace_one(self, c, start: int, old: str, new: str) -> bool:
        text = self._read(c, self._process) or ""
        idx = _locate(text, old, start)
        if idx < 0:
            return False
        nth = text.count(old, 0, idx)
        if self._via_text_pattern(c, text, idx, old, new, nth):
            return True
        return self._via_value_pattern(c, idx, old, new)

    def _focus(self, c) -> None:
        try:
            if winutil.foreground_pid() != c.ProcessId:
                top = c.GetTopLevelControl()
                if top is not None and top.NativeWindowHandle:
                    self.auto.SetForegroundWindow(top.NativeWindowHandle)
                c.SetFocus()
                time.sleep(0.08)
        except Exception:
            log.debug("focus failed", exc_info=True)

    def _find_nth(self, tp, needle: str, nth: int):
        auto = self.auto
        search = tp.DocumentRange.Clone()
        for i in range(nth + 1):
            found = search.FindText(needle, False, False)
            if not found:
                return None
            if i == nth:
                return found
            search.MoveEndpointByRange(auto.TextPatternRangeEndpoint.Start, found,
                                       auto.TextPatternRangeEndpoint.End, waitTime=0)
        return None

    def _range_by_offset(self, tp, start: int, length: int):
        auto = self.auto
        rng = tp.DocumentRange.Clone()
        rng.MoveEndpointByRange(auto.TextPatternRangeEndpoint.End, rng, auto.TextPatternRangeEndpoint.Start, waitTime=0)
        rng.MoveEndpointByUnit(auto.TextPatternRangeEndpoint.End, auto.TextUnit.Character, start + length, waitTime=0)
        rng.MoveEndpointByUnit(auto.TextPatternRangeEndpoint.Start, auto.TextUnit.Character, start, waitTime=0)
        return rng

    def _selected_text(self, tp) -> str:
        try:
            sel = tp.GetSelection()
            return sel[0].GetText(-1) if sel else ""
        except Exception:
            return ""

    def _via_text_pattern(self, c, text: str, idx: int, old: str, new: str, nth: int) -> bool:
        """Select exactly the detected value and paste the replacement over it.
        Pasting fires the same events as a user paste, so web apps (React etc.) see it."""
        auto = self.auto
        tp = self._pattern(c, auto.PatternId.TextPattern)
        if tp is None:
            return False
        self._focus(c)
        candidates = []
        try:
            candidates.append(self._find_nth(tp, old, nth))
        except Exception:
            log.debug("FindText unsupported", exc_info=True)
        try:
            crlf_before = text[:idx].count("\r\n")
            candidates.append(self._range_by_offset(tp, idx, len(old)))
            if crlf_before:
                candidates.append(self._range_by_offset(tp, idx - crlf_before, len(old)))
        except Exception:
            log.debug("offset selection unsupported", exc_info=True)

        for rng in candidates:
            if rng is None:
                continue
            try:
                rng.Select(waitTime=0)
            except Exception:
                continue
            time.sleep(0.05)
            if _norm(self._selected_text(tp)) != _norm(old):
                continue
            before = (self._read(c, self._process) or "").count(old)
            self._paste(new)
            for _ in range(8):
                time.sleep(0.08)
                if (self._read(c, self._process) or "").count(old) < before:
                    return True
            return False
        return False

    def _via_value_pattern(self, c, hint: int, old: str, new: str) -> bool:
        auto = self.auto
        vp = self._pattern(c, auto.PatternId.ValuePattern)
        if vp is None:
            return False
        try:
            if vp.IsReadOnly:
                return False
            current = vp.Value or ""
        except Exception:
            return False
        idx = _locate(current, old, hint)
        if idx < 0:
            return False
        vp.SetValue(current[:idx] + new + current[idx + len(old):], waitTime=0)
        time.sleep(0.08)
        try:
            return (vp.Value or "")[idx:idx + len(new)] == new
        except Exception:
            return False

    def _paste(self, new: str) -> None:
        auto = self.auto
        previous = None
        try:
            previous = auto.GetClipboardText()
        except Exception:
            pass
        auto.SetClipboardText(new)
        auto.SendKeys("{Ctrl}v", waitTime=0.05)
        time.sleep(0.2)
        if previous:
            try:
                auto.SetClipboardText(previous)
            except Exception:
                pass
