"""SENSIQ main program.

Threads:
  * Tk main thread  - overlay UI, owns all windows
  * FocusWatcher    - UI Automation (Windows) / AT-SPI (Linux): reads the focused field,
                      applies replacements
  * DetectorWorker  - runs rules + local NER model, debounced
  * pystray         - tray icon (posts menu clicks back to the main thread)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import queue
import sys
import threading
from pathlib import Path

from . import APP_NAME, __version__, platform as plat
from .detector.types import HIGH
from .masking import suggest
from .paths import data_dir, log_dir

log = logging.getLogger("sensiq")


def masked_hint(f) -> str:
    """What the not-private list shows for a value: its kind and masked form only."""
    return f"{f.label}: {suggest(f, 'partial')}"


def setup_logging(debug: bool) -> None:
    # The log records events and errors only. Typed text is never logged.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        log_dir() / "sensiq.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    # third-party libraries log document words at DEBUG level (Presidio: "Context list is: ...");
    # file contents must never reach the log, even with --debug
    for name in ("presidio-analyzer", "presidio_analyzer", "spacy", "PIL", "matplotlib", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    if debug and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)


class App:
    def __init__(self, args):
        import tkinter as tk

        from .config import Config, IgnoreList
        from .overlay import Overlay
        from .worker import DetectorWorker

        self.args = args
        self.renamed = False
        if plat.IS_WIN:
            from . import migrate
            self.renamed = migrate.run()      # PII Guard (<= 1.3.0) -> SENSIQ: settings, registry
        self.config = Config.load()
        if plat.IS_WIN:
            migrate.after_settings(self.config)
        if args.no_ner:
            self.config.data["ner"]["enabled"] = False
        self.ignore = IgnoreList.load()
        self._apply_safe_copies_folder(startup=True)
        self.events: "queue.Queue" = queue.Queue()
        self.stop = threading.Event()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.overlay = Overlay(self.root, on_replace=self.replace, on_ignore=self.ignore_finding,
                               on_fix_all=self.replace_all, on_style=self.toggle_style,
                               get_style=lambda: self.config.data.get("mask_style", "partial"))
        self.detector = DetectorWorker(self.config, self.ignore, self.events, self.stop)
        if plat.IS_WIN:
            from .capture import FocusWatcher
        else:
            from .capture_linux import FocusWatcher   # AT-SPI; test version
        self.watcher = FocusWatcher(self.events, self.config, self.stop)
        self.scanner = None
        self.share_threads = []
        fs = self.config.data.get("file_scan", {})
        if fs.get("enabled", True):
            try:
                from .sharewatch import ClipboardWatcher, DialogWatcher, DragWatcher, FileScanWorker
                self.scanner = FileScanWorker(self.detector.detect_now, self.config, self.events, self.stop)
                self.share_threads.append(self.scanner)
                skip = lambda: set()  # noqa: E731
                if fs.get("browser_extension", True):
                    from .browserlink import BrowserLink
                    self.browser_link = BrowserLink(self.scanner, self.stop)
                    self.share_threads.append(self.browser_link)
                    skip = self.browser_link.active_browsers
                if fs.get("clipboard", True):
                    self.share_threads.append(ClipboardWatcher(self.scanner, self.config, self.stop))
                # Linux has no global hooks for drags and Open dialogs: Files → Scripts →
                # "Check with SENSIQ", the tray's "Check a file…" and the browser extension instead
                if fs.get("drag_drop", True) and plat.IS_WIN:
                    self.share_threads.append(DragWatcher(self.scanner, self.config, self.stop, skip_apps=skip))
                if fs.get("file_dialogs", True) and plat.IS_WIN:
                    self.share_threads.append(DialogWatcher(self.scanner, self.config, self.stop, skip_apps=skip))
                from .sharewatch import OpenedFileWatcher  # does nothing until switched on in the tray
                self.share_threads.append(OpenedFileWatcher(self.scanner, self.config, self.events, self.stop))
            except Exception:
                log.exception("Image and file checks unavailable")
        try:
            from .control import ControlServer
            self.share_threads.append(ControlServer(lambda paths: self.events.put(("check_files", paths)), self.stop,
                                                    on_show=lambda: self.events.put(("show",))))
        except Exception:
            log.exception("control channel unavailable")
        try:
            from .updater import UpdateChecker
            self.share_threads.append(UpdateChecker(self.config, self.events, self.stop))
        except Exception:
            log.exception("Update checks unavailable")
        self.updating = False
        self.update_info = None   # an update found but not installed (tray: "Update available…")
        self.tray = None
        if not args.no_tray:
            try:
                from .tray import Tray
                self.tray = Tray(self)
            except Exception:
                log.exception("Tray icon unavailable")

        self.field = None
        self.findings = []
        self.alerted = set()
        self.report_queue = []   # file cards waiting while another one is open
        self.report_open = None  # context of the card on screen

    # ---- lifecycle -------------------------------------------------------------------
    def run(self) -> int:
        log.info("%s %s starting", APP_NAME, __version__)
        self.detector.start()
        self.watcher.start()
        for t in self.share_threads:
            t.start()
        if self.tray:
            self.tray.start()
        self.root.after(50, self._pump)
        self.root.after(3000, self._check_pause)
        if self.renamed:
            self.root.after(2500, lambda: self.overlay.toast(
                "PII Guard is now called SENSIQ. Your settings were kept. If you used the browser extension, "
                "add it again from its new folder: right-click the shield → Check uploads in my browser (set up)…",
                "info", ms=20000))
        if plat.IS_WIN:
            hello = ("SENSIQ is on. It checks what you type, and images and files you copy, drag or upload, "
                     "on this computer only. The shield in the system tray has the settings.")
        else:
            hello = ("SENSIQ is on. It checks images and files you copy or upload, on this computer only. "
                     "In Files, right-click a file → Scripts → Check with SENSIQ. "
                     "The shield in the top bar has the settings.")
        self.root.after(1500, lambda: self._notify(hello))
        if plat.IS_LINUX:
            self.root.after(800, self._linux_integration)
            self.root.after(6000, self._linux_tray_check)
            self.root.after(9000, lambda: self._offer_typing(first_time=True))
        if getattr(self.args, "check", None):
            paths = list(self.args.check)
            self.root.after(2000, lambda: self._handle(("check_files", paths)))
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        self.stop.set()
        log.info("%s stopped", APP_NAME)
        return 0

    def quit(self) -> None:
        self.stop.set()
        if self.tray:
            self.tray.stop()
        self.root.after(100, self.root.destroy)

    def _notify(self, message: str) -> None:
        if self.tray:
            self.tray.notify(message)
        else:
            self.overlay.toast(message, "info")

    # ---- event pump (main thread) --------------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            log.exception("event handling failed")
        if not self.stop.is_set():
            self.root.after(40, self._pump)

    def _handle(self, ev) -> None:
        kind = ev[0]
        if kind == "field":
            self._on_field(ev[1])
        elif kind == "nofield":
            self.field = None
            self.findings = []
            self.overlay.hide_all()
        elif kind == "findings":
            _, key, text, findings = ev
            if self.field is not None and self.field.key == key and self.field.text == text:
                self._on_findings(findings)
        elif kind == "replace_done":
            _, ok, failed, single = ev
            if failed:
                msg = ("SENSIQ couldn't edit this field directly. The safe text is on your clipboard: "
                       "select the highlighted value and press Ctrl+V." if single else
                       f"Replaced {ok} of {ok + failed}. Use Replace on the remaining ones one at a time.")
                self.overlay.toast(msg, "warn", ms=7000)
        elif kind == "engine_ready":
            status = ev[1]
            if not status.get("ner_loaded") and self.config.data["ner"].get("enabled", True):
                self.overlay.toast("Name detection is off because the model files are missing or damaged. "
                                   "IDs, numbers, emails and keys are still checked. Run the self-test from "
                                   "the tray menu for details.", "warn", ms=9000)
        elif kind == "file_report":
            _, ctx, results = ev
            if not self.config.data.get("enabled", True):
                if ctx.reply is not None:
                    ctx.reply("original")
            elif self.report_open is not None and self._same_dialog(self.report_open, ctx):
                # another file picked in the same upload window: its report replaces the old one
                self.report_queue = [q for q in self.report_queue if not self._same_dialog(q[0], ctx)]
                self.overlay.close_file_report("replaced")
                self.root.after(150, lambda c=ctx, r=results: self._show_report(c, r))
            elif self.report_open is not None:
                self.report_queue.append((ctx, results))  # one card at a time; the next follows
            else:
                self._show_report(ctx, results)
        elif kind == "scan_start":
            if self.config.data.get("enabled", True):
                self.overlay.checking(ev[1])
        elif kind == "scan_clean":
            self.overlay.close_checking()
            if self.config.data.get("enabled", True):
                self.overlay.toast(ev[1], "ok", ms=3500)
        elif kind == "scan_done":
            self.overlay.close_checking()
        elif kind == "offer_check":
            from pathlib import Path as _P
            path = ev[1]
            self.overlay.offer(f"You opened {_P(path).name}. Check it for private details before you share it?",
                               "Check it", lambda p=path: self.scanner.submit_files("File you opened", [p],
                                                                                    kind="opened"))
        elif kind == "files_unchecked":
            self.overlay.close_checking()
            if self.config.data.get("enabled", True):
                self.overlay.toast(ev[1], "warn", ms=12000)
        elif kind == "scan_progress":
            self.overlay.toast(ev[1], "info", ms=6000)
        elif kind == "redact_done":
            _, ok, message = ev
            self.overlay.close_file_report("done")
            self.overlay.toast(message, "ok" if ok else "warn", ms=10000)
        elif kind == "update_available":
            _, info, manual = ev
            if not self.updating:
                # never installs by itself: a notification, the card, and a tray item to open it again
                first = getattr(self, "update_info", None) is None or self.update_info.version != info.version
                self.update_info = info
                if first and not manual:
                    self._notify(f"Update found: SENSIQ {info.version} is available. Nothing is installed "
                                 "unless you choose Update now.")
                self._show_update_card()
                if self.tray:
                    self.tray.refresh()
        elif kind == "update_none":
            self.overlay.toast(f"You have the latest version of SENSIQ ({__version__}).", "ok", ms=4000)
        elif kind == "update_failed":
            self.updating = False
            self.overlay.update_status(None)
            self.overlay.toast(f"Couldn't update SENSIQ: {ev[1]}. Try again later from the tray menu.",
                               "warn", ms=9000)
        elif kind == "update_progress":
            self.overlay.update_status(ev[1])
        elif kind == "update_ready":
            from . import updater
            if not plat.IS_WIN:   # Linux: the system asks for your password, then SENSIQ restarts
                self.overlay.update_status("Installing… Enter your password when Ubuntu asks for it.")
                self._install_linux_update(ev[1])
                return
            self.overlay.update_status("Installing… SENSIQ will restart in a moment.")
            try:
                updater.install(ev[1])
                self.root.after(1500, self.quit)
            except Exception as exc:
                self._handle(("update_failed", str(exc)))
        elif kind == "update_installed":
            self.overlay.update_status("Installed. SENSIQ is restarting…")
            from . import updater
            updater.restart_later()
            self.root.after(800, self.quit)
        elif kind == "check_files":
            paths = [p for p in ev[1] if Path(p).exists()]
            if paths and self.scanner is not None:
                self.scanner.submit_files("File you chose to check", paths, kind="opened")
            elif self.scanner is None:
                self.overlay.toast("File checks are switched off in the settings.", "warn", ms=5000)
        elif kind == "tray":
            self._on_tray(ev[1])
        elif kind == "fatal":
            plat.message_box(ev[1], APP_NAME, error=True)
            self.quit()
        elif kind == "show":   # opened again from the app list
            self._on_tray("status")
            if plat.IS_LINUX:
                self.root.after(5500, lambda: self._offer_typing(first_time=False))
        elif kind == "typing_off":   # Linux: AT-SPI unavailable; files are still checked
            log.info("typing checks unavailable: %s", ev[1])

    def _on_field(self, st) -> None:
        if self.field is None or self.field.key != st.key:
            self.findings = []
            self.alerted.clear()
            self.overlay.hide_all()
        elif self.findings:
            still_there = [f for f in self.findings if f.text in st.text]
            if len(still_there) != len(self.findings):
                self.findings = still_there
                self.overlay.update(self.findings)
        self.field = st
        self.overlay.set_anchor(st.rect)
        self.detector.submit(st.key, st.text)

    def _on_findings(self, findings) -> None:
        self.findings = findings
        self.overlay.update(findings)
        if not self.config.data.get("show_alert_for_high_risk", True):
            return
        fresh = [f for f in findings if f.severity == HIGH and f.fingerprint() not in self.alerted]
        if fresh:
            self.alerted.update(f.fingerprint() for f in fresh)
            if not self.overlay.panel_open:
                self.overlay.toast(f"{fresh[0].label} detected in what you're typing. "
                                   "Click the red badge to replace it.", "high")

    # ---- overlay actions -----------------------------------------------------------------
    def _style(self) -> str:
        return self.config.data.get("mask_style", "partial")

    def replace(self, f) -> None:
        self.watcher.commands.put(("replace", [(f.start, f.end, f.text, suggest(f, self._style()))]))

    def replace_all(self) -> None:
        edits = [(f.start, f.end, f.text, suggest(f, self._style())) for f in self.findings]
        if edits:
            self.watcher.commands.put(("replace", edits))

    def ignore_finding(self, f, always: bool) -> None:
        self.ignore.add(f.fingerprint(), persist=always, hint=masked_hint(f))
        self.findings = [x for x in self.findings if x.fingerprint() != f.fingerprint()]
        self.overlay.update(self.findings)

    @staticmethod
    def _same_dialog(a, b) -> bool:
        return (getattr(a, "kind", "") == "dialog" and getattr(b, "kind", "") == "dialog"
                and getattr(a, "dialog_hwnd", 0) == getattr(b, "dialog_hwnd", 0))

    def _show_report(self, ctx, results) -> None:
        import os
        self.overlay.close_checking()
        self.report_open = ctx

        def apply(chosen, c=ctx, r=results):
            self.scanner.submit_redact(c, r, chosen)

        def closed(reason, c=ctx):
            # "original": Use original; "timeout": no choice in time; "done": safe copy finished
            if c.reply is not None and reason in ("original", "timeout", "dismissed", "replaced"):
                c.reply("original" if reason == "original" else "cancel")
                if reason == "timeout":
                    self.overlay.toast("No choice was made in time, so the file wasn't given to the page. "
                                       "Add it again when you're ready.", "warn", ms=8000)
            if reason == "dismissed":  # the card's ✕: nothing shared, pick another file
                if c.reply is not None:
                    msg = "Report closed. The file wasn't given to the page; choose another file when you're ready."
                elif c.kind == "dialog":
                    msg = "Report closed. Pick another file in the window to check it, or Cancel."
                elif c.kind == "uploaded":
                    msg = "Report closed. The file is already added to the page; remove it there if you need to."
                else:
                    msg = "Report closed. Nothing was changed."
                self.overlay.toast(msg, "info", ms=6000)
            self.report_open = None
            if self.report_queue:
                self.root.after(300, lambda: self._show_report(*self.report_queue.pop(0)))

        self.overlay.file_report(ctx, results, on_apply=apply, on_not_private=self.not_private,
                                 on_flag_again=self.flag_again, on_close=closed)
        if os.environ.get("SENSIQ_TEST_AUTOREPLACE") == "1":  # automated tests only
            self.root.after(500, lambda: self.overlay.apply_report())

    def not_private(self, findings) -> None:
        """From the file card: never flag this value again. Names/places go to the editable
        never_flag_words list in settings; other values are remembered only as a fingerprint."""
        words = self.config.data.setdefault("never_flag_words", [])
        for f in findings:
            if f.kind in ("PERSON", "LOCATION", "ORG"):
                norm = " ".join(f.text.split())
                if norm.lower() not in {w.lower() for w in words}:
                    words.append(norm)
            else:
                self.ignore.add(f.fingerprint(), persist=True, hint=masked_hint(f))
        self.config.save()

    def flag_again(self, findings) -> None:
        """Undo not_private() (the card's "undo" link): flag these values again."""
        words = self.config.data.setdefault("never_flag_words", [])
        for f in findings:
            if f.kind in ("PERSON", "LOCATION", "ORG"):
                norm = " ".join(f.text.split()).lower()
                words[:] = [w for w in words if " ".join(str(w).split()).lower() != norm]
            else:
                self.ignore.remove(f.fingerprint())
        self.config.save()

    # ---- updates ---------------------------------------------------------------------------
    def _check_updates_now(self) -> None:
        """Tray: check right away (also finds a version the user skipped before)."""
        from . import updater

        def work():
            try:
                opts = self.config.data.get("updates", {})
                info = updater.check(opts.get("repo") or updater.UPDATE_REPO)
                self.events.put(("update_available", info, True) if info else ("update_none",))
            except Exception as exc:
                self.events.put(("update_failed", f"GitHub couldn't be reached ({exc})"))
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _start_update(self, info) -> None:
        from . import updater
        self.updating = True

        def work():
            try:
                last = [-1]

                def progress(frac):
                    pct = int(frac * 100)
                    if pct != last[0] and pct % 5 == 0:
                        last[0] = pct
                        self.events.put(("update_progress", f"Downloading version {info.version}… {pct}%"))
                self.events.put(("update_progress", f"Downloading version {info.version}…"))
                path = updater.download(info, progress)
                self.events.put(("update_ready", path))
            except Exception as exc:
                log.exception("update download failed")
                self.events.put(("update_failed", str(exc)))
        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _show_update_card(self) -> None:
        info = getattr(self, "update_info", None)
        if info is None:
            return
        from . import updater
        if updater.can_install():
            on_update = lambda i=info: self._start_update(i)  # noqa: E731
        else:   # e.g. running from source on Linux: the release page has the new version
            on_update = lambda i=info: plat.open_path(i.page)  # noqa: E731
        self.overlay.update_card(info, __version__, on_update=on_update,
                                 on_skip=lambda i=info: self._skip_update(i),
                                 on_page=lambda i=info: plat.open_path(i.page))

    def _install_linux_update(self, package) -> None:
        from . import updater

        def work():
            ok, why = updater.install_linux(package)
            self.events.put(("update_installed",) if ok else ("update_failed", why))
        threading.Thread(target=work, name="update-install", daemon=True).start()

    def _skip_update(self, info) -> None:
        self.update_info = None
        if self.tray:
            self.tray.refresh()
        self.config.data.setdefault("updates", {})["skip_version"] = info.version
        self.config.save()
        self.overlay.toast(f"Version {info.version} skipped. Tray → Check for updates now still finds it.",
                           "info", ms=5000)

    # ---- safe copies folder ---------------------------------------------------------------
    def _apply_safe_copies_folder(self, startup: bool = False) -> None:
        """Use the folder from settings. At start-up, a different folder chosen on the
        installer's page (registry) wins, so reinstalling with a new choice takes effect."""
        from . import paths
        fs = self.config.data.setdefault("file_scan", {})
        folder = str(fs.get("safe_copies_folder") or "")
        default = str(paths.default_safe_copies_dir())
        norm = lambda s: os.path.normcase(os.path.normpath(s)) if s else ""  # noqa: E731
        if startup:
            reg = paths.registry_safe_copies_folder()
            if reg and norm(reg) != norm(folder or default):
                folder = "" if norm(reg) == norm(default) else reg
                fs["safe_copies_folder"] = folder
                try:
                    self.config.save()
                except Exception:
                    log.debug("could not save settings", exc_info=True)
        paths.set_safe_copies_folder(folder or None)
        paths.remember_safe_copies_folder(folder)

    def _change_safe_copies_folder(self) -> None:
        import tkinter as tk
        from tkinter import filedialog
        from . import paths
        current = paths.safe_copies_dir()
        holder = tk.Toplevel(self.root)   # keeps the folder picker in front of other windows
        holder.withdraw()
        holder.attributes("-topmost", True)
        try:
            chosen = filedialog.askdirectory(parent=holder, initialdir=str(current), mustexist=False,
                                             title="Choose where SENSIQ saves safe copies of your files")
        finally:
            holder.destroy()
        if not chosen:
            return
        target = Path(chosen)
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".sensiq-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            self.overlay.toast(f"SENSIQ can't save files in {target} ({exc.strerror or exc}). "
                               "Choose another folder.", "warn", ms=8000)
            return
        default = paths.default_safe_copies_dir()
        same_as_default = os.path.normcase(str(target.resolve())) == os.path.normcase(str(default.resolve()))
        self.config.data.setdefault("file_scan", {})["safe_copies_folder"] = "" if same_as_default else str(target)
        self.config.save()
        self._apply_safe_copies_folder()
        if same_as_default:
            self.overlay.toast("Safe copies will be saved in the default folder and deleted after 24 hours.",
                               "ok", ms=5000)
        else:
            self.overlay.toast(f"Safe copies will be saved in {target}. They're kept until you delete them.",
                               "ok", ms=6000)

    def _default_safe_copies_folder(self) -> None:
        self.config.data.setdefault("file_scan", {})["safe_copies_folder"] = ""
        self.config.save()
        self._apply_safe_copies_folder()
        self.overlay.toast("Safe copies will be saved in the default folder and deleted after 24 hours.",
                           "ok", ms=5000)

    def _show_not_private(self) -> None:
        """Tray: review and remove values marked "not private" (names and numbers)."""
        names = [str(w) for w in self.config.data.get("never_flag_words") or []]
        entries = [("name", w, f"Name or place: {w}") for w in names]
        entries += [("fp", fp, hint or "A value saved before version 1.5 (hidden)") for fp, hint in self.ignore.entries()]

        def remove(kind, key):
            if kind == "name":
                words = self.config.data.setdefault("never_flag_words", [])
                words[:] = [w for w in words if str(w) != key]
                self.config.save()
            else:
                self.ignore.remove(key)
            self.watcher.commands.put(("refresh",))
        self.overlay.not_private_list(entries, remove)

    def toggle_style(self) -> None:
        self.config.data["mask_style"] = "placeholder" if self._style() == "partial" else "partial"
        self.config.save()
        self.overlay.update(self.findings)
        if self.tray:
            self.tray.refresh()

    # ---- tray actions --------------------------------------------------------------------
    def _on_tray(self, action: str) -> None:
        if action == "status":
            if self.config.data.get("enabled", True) and plat.IS_WIN:
                self.overlay.toast("SENSIQ is on. It checks what you type and the images and files you "
                                   "copy, drag or upload. Right-click the shield for options.", "ok", ms=5000)
            elif self.config.data.get("enabled", True):
                self.overlay.toast("SENSIQ is on. It checks images and files you copy or upload, and what you "
                                   "type if typing checks are on. Click the shield in the top bar for options; "
                                   "in Files, right-click a file → Scripts → Check with SENSIQ.", "ok", ms=7000)
            else:
                self.overlay.toast("SENSIQ is PAUSED: nothing is being checked. Right-click the shield and "
                                   "choose Resume protection.", "warn", ms=7000)
        elif action == "toggle":
            self._set_enabled(not self.config.data.get("enabled", True))
        elif action == "style":
            self.toggle_style()
        elif action == "startup":
            enabled = not plat.is_startup_enabled()
            plat.set_startup(enabled)
            self.overlay.toast("SENSIQ will start when you sign in." if enabled else
                               "SENSIQ won't start automatically.", "info", ms=2500)
        elif action == "opened_files":
            fs = self.config.data.setdefault("file_scan", {})
            fs["opened_files"] = not fs.get("opened_files", False)
            self.config.save()
            self.overlay.toast("SENSIQ will offer to check documents and pictures you open." if fs["opened_files"]
                               else "SENSIQ won't offer checks when you open files.", "info", ms=3500)
        elif action == "browser_setup":
            self._browser_setup()
        elif action == "check_file":
            self._check_file_dialog()
        elif action == "typing_setup":
            self._typing_setup()
        elif action == "add_hindi":
            if plat.IS_WIN:
                self.overlay.toast("Installing Hindi reading for images. Windows will ask for permission; "
                                   "the download is about 2 MB.", "info", ms=6000)
                threading.Thread(target=self._install_ocr_language, args=("hi-IN",), daemon=True).start()
            else:
                self.overlay.toast("Installing Hindi reading for images. Ubuntu will ask for your password; "
                                   "the download is about 1 MB.", "info", ms=6000)
                threading.Thread(target=self._install_ocr_language_linux, args=("hin",), daemon=True).start()
        elif action == "safe_open":
            from .paths import safe_copies_dir
            plat.open_path(safe_copies_dir())
        elif action == "safe_change":
            self._change_safe_copies_folder()
        elif action == "safe_default":
            self._default_safe_copies_folder()
        elif action == "show_update":
            self._show_update_card()
        elif action == "check_updates":
            self.overlay.toast("Checking GitHub for a new version…", "info", ms=3000)
            self._check_updates_now()
        elif action == "auto_updates":
            up = self.config.data.setdefault("updates", {})
            up["check"] = not up.get("check", True)
            self.config.save()
            self.overlay.toast("SENSIQ will check for new versions twice a day." if up["check"] else
                               "Automatic update checks are off. Use Check for updates now any time.",
                               "info", ms=4000)
        elif action == "selftest":
            threading.Thread(target=self._selftest, daemon=True).start()
        elif action == "settings":
            plat.open_text_file(self.config.path)
        elif action == "reload":
            self.config.reload()
            self._apply_safe_copies_folder()
            self.detector.request_reload()
            self.watcher.commands.put(("refresh",))
            self.overlay.toast("Settings reloaded.", "ok", ms=2000)
        elif action == "not_private_list":
            self._show_not_private()
        elif action == "clear_ignore":
            self.ignore.clear()
            self.watcher.commands.put(("refresh",))
            self.overlay.toast("Ignored items cleared.", "ok", ms=2000)
        elif action == "logs":
            plat.open_path(log_dir())
        elif action == "quit":
            self.quit()
        elif action == "refresh":
            pass
        if self.tray:
            self.tray.refresh()

    def _browser_setup(self) -> None:
        """Opens a short guide for adding the SENSIQ extension to Chrome / Edge / Brave."""
        import html
        from .browserlink import extension_dir, register_host
        register_host()
        folder = extension_dir()
        page = data_dir() / "browser-setup.html"
        linux_note = ("" if plat.IS_WIN else " Chromium installed from the Snap Store can't talk to "
                      "programs outside it: use Google Chrome, Brave or Edge from their own .deb packages.")
        page.write_text(f"""<!doctype html><meta charset="utf-8"><title>SENSIQ for your browser</title>
<style>body{{font:15px/1.5 Segoe UI,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#1c2433}}
code{{background:#eef1f5;padding:2px 6px;border-radius:4px}} li{{margin:8px 0}} h1{{font-size:22px}}
.path{{display:block;padding:10px;background:#eef1f5;border-radius:6px;word-break:break-all}}</style>
<h1>Check uploads in your browser</h1>
<p>With the SENSIQ extension, files you upload, drop or paste into any website are checked before
the site gets them, and you can send a safe copy instead. Everything stays on this computer.</p>
<ol>
<li>In Chrome open <code>chrome://extensions</code> (Edge: <code>edge://extensions</code>,
Brave: <code>brave://extensions</code>).{linux_note}</li>
<li>Turn on <b>Developer mode</b> (top right in Chrome, left side in Edge).</li>
<li>Click <b>Load unpacked</b> and choose this folder:<span class="path">{html.escape(str(folder))}</span></li>
<li>Done. "SENSIQ" appears in the list. Keep SENSIQ running in the tray.</li>
</ol>
<p>To remove it later, click Remove on the extension in the same page.</p>""", encoding="utf-8")
        plat.open_path(page)
        plat.open_path(folder)
        self.overlay.toast("Follow the steps in the page that opened to add SENSIQ to your browser.",
                           "info", ms=7000)

    def _install_ocr_language(self, tag: str) -> None:
        """Adds a Windows OCR language (needs the user's OK in the Windows permission prompt)."""
        import subprocess
        from . import ocr
        cap = f"Language.OCR~~~{tag}~0.0.1.0"
        inner = f"Add-WindowsCapability -Online -Name '{cap}'"
        cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
               f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
               f"-ArgumentList '-NoProfile','-Command',\"{inner}\""]
        try:
            subprocess.run(cmd, timeout=900, creationflags=0x08000000)
        except Exception:
            log.exception("OCR language install failed")
        ocr.reset()
        ok = any(t.lower() == tag.lower() for t in ocr.installed_languages())
        self.events.put(("files_unchecked" if not ok else "scan_progress",
                         "Hindi reading is installed: images are now read in English and Hindi." if ok else
                         "Hindi reading wasn't installed (permission refused or no internet)."))
        if self.tray:
            self.tray.refresh()

    def _install_ocr_language_linux(self, code: str) -> None:
        """Adds a Tesseract language pack (Ubuntu asks for the user's password)."""
        import subprocess
        from . import ocr
        try:
            subprocess.run(["pkexec", "apt-get", "install", "-y", f"tesseract-ocr-{code}"], timeout=900,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=plat.child_env())
        except Exception:
            log.exception("OCR language install failed")
        ocr.reset()
        ok = code in ocr.installed_languages()
        self.events.put(("files_unchecked" if not ok else "scan_progress",
                         "Hindi reading is installed: images are now read in English and Hindi." if ok else
                         "Hindi reading wasn't installed (password not given or no internet)."))
        if self.tray:
            self.tray.refresh()

    def _check_file_dialog(self) -> None:
        """Tray: pick files or a folder to check (works on every system)."""
        import tkinter as tk
        from tkinter import filedialog
        holder = tk.Toplevel(self.root)
        holder.withdraw()
        holder.attributes("-topmost", True)
        try:
            chosen = filedialog.askopenfilenames(parent=holder, initialdir=str(Path.home()),
                                                 title="Choose files to check for private details")
        finally:
            holder.destroy()
        if chosen:
            self._handle(("check_files", list(chosen)))

    def _typing_setup(self) -> None:
        """Linux: typing checks read text fields through the desktop's accessibility service."""
        from .capture_linux import enable_accessibility
        if enable_accessibility():
            self.overlay.toast("Typing checks are turned on. Sign out and back in (or restart your apps) "
                               "so apps start sharing their text boxes with SENSIQ. This is a test version: "
                               "it works best in GNOME apps, LibreOffice, Chrome and Firefox.", "ok", ms=12000)
        else:
            self.overlay.toast("Couldn't turn on typing checks: this desktop has no accessibility service.",
                               "warn", ms=8000)

    def _offer_typing(self, first_time: bool) -> None:
        """Linux: typing checks need the desktop's accessibility switch; offer it with a button
        (once at the first start, and whenever SENSIQ is opened again while it's off)."""
        try:
            from .capture_linux import accessibility_enabled
            if accessibility_enabled():
                return
        except Exception:
            return
        if first_time:
            if self.config.data.get("linux_typing_offered"):
                return
            self.config.data["linux_typing_offered"] = True
            self.config.save()
        self.overlay.offer("Warn me while I type too? SENSIQ can check what you type in apps and websites "
                           "(test version). Ubuntu's accessibility setting is switched on for this.",
                           "Turn on", self._typing_setup, ms=30000)

    def _linux_tray_check(self) -> None:
        """Ubuntu shows tray icons through its AppIndicators extension: if nothing shows them,
        try switching it on, else say where SENSIQ's options are."""
        def work():
            from . import linuxdesktop
            host = linuxdesktop.tray_host_present()
            log.info("tray host on this desktop: %s", host)
            if self.tray is None or host:
                return
            if linuxdesktop.enable_appindicator_extension():
                import time
                for _ in range(10):
                    time.sleep(0.5)
                    if linuxdesktop.tray_host_present():
                        log.info("switched on the AppIndicators extension for the tray icon")
                        self.events.put(("tray", "refresh"))
                        return
            log.warning("no tray host (StatusNotifierWatcher) on this desktop")
            self.events.put(("files_unchecked",
                             "SENSIQ is on, but this desktop can't show its shield in the top bar. Turn on "
                             "\"Ubuntu AppIndicators\" in the Extensions app (or install "
                             "gnome-shell-extension-appindicator), then sign out and back in. "
                             "Files are still checked: right-click a file → Scripts → Check with SENSIQ."))
        threading.Thread(target=work, name="tray-check", daemon=True).start()

    def _linux_integration(self) -> None:
        """Files (Nautilus) right-click script "Check with SENSIQ"; kept in step with this install."""
        try:
            from .linuxdesktop import install_files_script
            install_files_script()
        except Exception:
            log.debug("Files script not installed", exc_info=True)

    PAUSE_SECONDS = 3600

    def _set_enabled(self, enabled: bool, auto: bool = False) -> None:
        import time
        self.config.data["enabled"] = enabled
        self.config.data["paused_until"] = 0 if enabled else time.time() + self.PAUSE_SECONDS
        self.config.save()
        if not enabled:
            self.overlay.hide_all()
            self.overlay.toast("Protection paused for 1 hour. Nothing is being checked until then.", "warn", ms=4000)
        else:
            self.overlay.toast("Protection resumed after the 1-hour pause." if auto else "Protection resumed.",
                               "ok", ms=4000)
        if self.tray:
            self.tray.refresh()

    def _check_pause(self) -> None:
        """Ends a pause after an hour; also ends pauses left over from before (no end time)."""
        import time
        if not self.config.data.get("enabled", True):
            until = float(self.config.data.get("paused_until") or 0)
            if time.time() >= until:
                self._set_enabled(True, auto=True)
        if not self.stop.is_set():
            self.root.after(30000, self._check_pause)

    def _selftest(self) -> None:
        from .selftest import run_selftest
        path = log_dir() / "selftest.txt"
        try:
            run_selftest(path, use_ner=self.config.data["ner"].get("enabled", True))
            plat.open_text_file(path)
        except Exception:
            log.exception("self-test failed")


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--native-host" in raw or any(a.startswith("chrome-extension://") for a in raw):
        # started by the browser for the SENSIQ extension: relay only, no window, no tray
        from .nativehost import main as host_main
        return host_main()
    parser = argparse.ArgumentParser(prog="SENSIQ", description="On-device personal data warnings while you type.")
    parser.add_argument("--selftest", action="store_true", help="test the detector on sample data and exit")
    parser.add_argument("--no-open", action="store_true", help="with --selftest: don't open the report")
    parser.add_argument("--no-ner", action="store_true", help="patterns only, skip the name model")
    parser.add_argument("--no-tray", action="store_true", help="run without a tray icon")
    parser.add_argument("--check", nargs="+", metavar="FILE",
                        help="check these files (hands them to SENSIQ if it is already running)")
    parser.add_argument("files", nargs="*", help=argparse.SUPPRESS)  # "Open with SENSIQ" (same as --check)
    parser.add_argument("--debug", action="store_true", help="verbose log")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    setup_logging(args.debug)
    if args.files:
        args.check = list(args.check or []) + list(args.files)
    if args.check:
        args.check = [str(Path(p).expanduser().resolve()) for p in args.check]

    if args.selftest:
        from .selftest import run_selftest
        path = log_dir() / "selftest.txt"
        ok, report = run_selftest(path, use_ner=not args.no_ner)
        print(report)
        if getattr(sys, "frozen", False) and not args.no_open:
            plat.open_text_file(path)
        return 0 if ok else 1

    if not plat.IS_WIN and not plat.IS_LINUX:
        print("SENSIQ runs on Windows and Linux. On this system you can run: python -m sensiq --selftest")
        return 2
    if plat.IS_LINUX and not plat.has_display():
        print("SENSIQ needs a desktop session (no display found). You can run: python -m sensiq --selftest")
        return 2

    plat.set_dpi_awareness()
    if not plat.acquire_single_instance():
        if args.check:
            from .control import send_check
            if send_check(args.check):
                return 0
        else:
            from .control import send_show
            if send_show():
                return 0   # the running SENSIQ shows that it's on
        plat.message_box("SENSIQ is already running. Look for the shield icon in the "
                         + ("system tray." if plat.IS_WIN else "top bar."))
        return 0
    try:
        return App(args).run()
    except Exception:
        log.exception("fatal error")
        plat.message_box("SENSIQ stopped because of an error. Details are in "
                         f"{log_dir() / 'sensiq.log'}", error=True)
        return 1
