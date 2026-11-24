"""Checks images and files at the moment you are about to share them, and on request
swaps in a safe copy with the chosen details replaced.

Three watchers feed one scanner thread:
  * ClipboardWatcher - an image or screenshot is copied (Ctrl+C, Win+Shift+S,
    "Copy image" in a browser), or files are copied in File Explorer (to paste
    into a chat, mail or web page).
  * DragWatcher      - files are dragged out of File Explorer or the desktop
    (to drop onto a browser, WhatsApp, Teams, Outlook...).
  * DialogWatcher    - files are picked in an Open / Upload / Attach dialog;
    the check runs as soon as a file is selected, before you press Open.

When the report card's "Replace" is clicked, the scanner writes safe copies to
the safe-copies folder (chosen in setup or the tray) and hands them on instead of the originals:
  copied image  -> the clipboard now holds the safe image (Ctrl+V pastes it)
  copied files  -> the clipboard now holds the safe copies
  upload dialog -> the dialog is pointed at the safe copy and Open is pressed
  dragged files -> safe copies go on the clipboard and are shown in File Explorer
Originals are never modified. File contents never reach the log.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import filescan, winutil
from .paths import default_safe_copies_dir, safe_copies_dir, work_dir

log = logging.getLogger(__name__)

EXPLORER_ROOTS = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}
SAFE_COPY_HOURS = 24
# shares where the user is about to upload/attach: say "checking…" at once and "nothing found" after
ANNOUNCE_KINDS = {"dialog", "uploaded", "browser", "drag", "opened"}
LONG_FILE_PAGES = 15  # tell the user when checking files with this many pages or more
MANY_FILES = 6        # ...or this many files at once
MAX_FILES = 500       # files checked per action (folders and ZIPs included)
ZIP_MAX_BYTES = 1024 * 1024 * 1024  # unpacked size read from one ZIP


@dataclass
class ShareContext:
    """Where the shared items came from, so the safe version can be handed on the same way."""
    kind: str                      # "image", "copy", "drag", "dialog", "uploaded", "browser"
    source: str                    # text shown on the card
    paths: List[str] = field(default_factory=list)
    dialog_hwnd: int = 0
    reply: Optional[Callable] = None   # browser pages: reply(action, files) with "original"/"safe"/"cancel"


@dataclass
class FileResult:
    name: str
    findings: list = field(default_factory=list)
    note: Optional[str] = None
    method: str = ""
    path: Optional[Path] = None
    image: object = None           # copied image (PIL), for "image" shares
    extracted: Optional[filescan.Extracted] = None
    can_redact: bool = True        # False: details can be shown but no safe copy can be made
    unchecked: bool = False        # True: the file couldn't be checked (password, damaged...)


def _is_safe_copy(p) -> bool:
    try:
        rp = Path(p).resolve()
        return any(rp.is_relative_to(d.resolve()) for d in {safe_copies_dir(), default_safe_copies_dir(), work_dir()})
    except Exception:
        return False


# ---------------------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------------------
class _Logged(threading.Thread):
    """Thread whose crash is logged instead of vanishing silently (pythonw has no console)."""

    def run(self) -> None:
        log.info("%s started", self.name)
        try:
            self.watch()
        except Exception:
            log.exception("%s stopped because of an error", self.name)

    def watch(self) -> None:
        raise NotImplementedError


class FileScanWorker(_Logged):
    """Runs extraction + detection for queued files/images and posts ("file_report", ctx, results).
    Also makes and hands on safe copies when asked (("redact", ...) jobs)."""

    def __init__(self, detect: Callable[[str], list], config, events: "queue.Queue", stop_event: threading.Event):
        super().__init__(name="FileScanner", daemon=True)
        self.detect = detect
        self.config = config
        self.events = events
        self.stop_event = stop_event
        self.jobs: "queue.Queue" = queue.Queue()
        self._cache: Dict[tuple, FileResult] = {}
        self._own_images: set = set()     # digests of safe images we put on the clipboard
        self._quiet_until = 0.0           # ignore clipboard changes we made ourselves
        self._zip_members: Dict[str, Tuple[Path, str]] = {}   # unpacked file -> (zip, member name)
        self._zip_notes: Dict[str, str] = {}

    def _opts(self) -> dict:
        return self.config.data.get("file_scan", {})

    # ---- intake -------------------------------------------------------------------------
    def submit_files(self, source: str, paths: Sequence[str], kind: str = "copy", dialog_hwnd: int = 0) -> None:
        paths = [str(p) for p in paths if not _is_safe_copy(p)]
        if paths:
            self.jobs.put(("files", ShareContext(kind, source, paths, dialog_hwnd), paths))

    def submit_browser(self, source: str, paths: Sequence, reply: Callable) -> None:
        """Files a web page is about to receive (via the browser extension)."""
        paths = [str(p) for p in paths]
        self.jobs.put(("files", ShareContext("browser", source, paths, reply=reply), paths))

    def submit_image(self, source: str, image) -> None:
        self.jobs.put(("image", ShareContext("image", source), image))

    def submit_redact(self, ctx: ShareContext, results: List[FileResult], chosen: Dict[int, list]) -> None:
        self.jobs.put(("redact", ctx, (results, chosen)))

    def clipboard_is_ours(self) -> bool:
        return time.monotonic() < self._quiet_until

    def watch(self) -> None:
        self._cleanup()
        while not self.stop_event.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if job[0] == "redact":
                    self._redact_job(job[1], *job[2])
                else:
                    self._run_job(job)
            except Exception:
                log.exception("file job failed")
                ctx = job[1] if len(job) > 1 and isinstance(job[1], ShareContext) else None
                if job[0] == "redact":
                    if ctx is not None and ctx.reply is not None:
                        ctx.reply("cancel")  # never hand the page the original after you chose to replace
                    self.events.put(("redact_done", False, "Couldn't make the safe copy. The original was not shared."))
                elif ctx is not None and ctx.reply is not None:
                    ctx.reply("original")    # a failed check mustn't freeze the page
                    self.events.put(("files_unchecked", "SENSIQ couldn't check the file the page received. "
                                                        "Look through it yourself."))
            finally:
                if job[0] != "redact":
                    self.events.put(("scan_done",))  # closes the "checking…" note whatever happened

    def _configure(self) -> None:
        from . import ocr, visual
        visual.configure(self.config.data.get("visual"))
        ocr.configure(self.config.data.get("ocr_languages"))

    def _run_job(self, job) -> None:
        kind, ctx, payload = job
        self._configure()
        results: List[FileResult] = []
        t0 = time.perf_counter()
        if kind == "image":
            res = self._scan_image(payload)
            if res is None:
                return
            results.append(res)
        else:
            files = self._expand(payload)
            announce = ctx.kind in ANNOUNCE_KINDS and files
            if announce:  # say at once that the file is being checked, before it's shared
                what = (self._display_name(files[0]) if len(files) == 1 else f"{len(files)} files")
                self.events.put(("scan_start", f"Checking {what} for personal information… "
                                               "Please wait for the result before you upload or send it."))
            elif len(files) >= MANY_FILES:
                self.events.put(("scan_progress", f"Checking {len(files)} files for private details…"))
            for p in files:
                results.append(self._scan_file(p))
        flagged = [r for r in results if r.findings]
        unchecked = [r for r in results if r.unchecked]
        if kind != "image":  # problems inside ZIPs (encrypted members, damaged archive)
            for raw in payload:
                note = self._zip_notes.pop(str(raw).lower(), None)
                if note:
                    unchecked.append(FileResult(Path(raw).name, note=note, unchecked=True))
        log.info("%s: checked %d item(s) in %.0f ms, %d with private details, %d couldn't be checked",
                 ctx.source, len(results), (time.perf_counter() - t0) * 1000, len(flagged), len(unchecked))
        if unchecked:
            names = "; ".join(f"{r.name}: {r.note}" for r in unchecked[:4])
            more = f" (and {len(unchecked) - 4} more)" if len(unchecked) > 4 else ""
            self.events.put(("files_unchecked", f"SENSIQ couldn't check {names}{more}. "
                                                "Look through it yourself before you share it."))
        if flagged:
            self.events.put(("file_report", ctx, flagged))
        else:
            if ctx.reply is not None:
                ctx.reply("original")  # nothing private: the page gets the file straight away
            if kind != "image" and ctx.kind in ANNOUNCE_KINDS and results and not unchecked:
                what = results[0].name if len(results) == 1 else f"these {len(results)} files"
                self.events.put(("scan_clean", f"✓ No personal information found in {what}. It's fine to share."))

    # ---- which files ----------------------------------------------------------------------
    def _expand(self, paths: Sequence[str]) -> List[Path]:
        """Every supported file: folders are searched (sub-folders too), ZIPs are opened."""
        out: List[Path] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                try:
                    for c in sorted(p.rglob("*")):
                        if len(out) >= MAX_FILES:
                            break
                        if c.is_file() and filescan.is_supported(c):
                            out.extend(self._unzip(c) if c.suffix.lower() in filescan.ARCHIVE_EXT else [c])
                except OSError:
                    pass
            elif p.is_file() and filescan.is_supported(p):
                out.extend(self._unzip(p) if p.suffix.lower() in filescan.ARCHIVE_EXT else [p])
        return out[:MAX_FILES]

    def _unzip(self, archive: Path) -> List[Path]:
        """Supported files inside a ZIP, unpacked to a private work folder so they can be read.
        The safe copy of the ZIP is rebuilt from them later."""
        work = work_dir() / hashlib.sha1(f"{archive}{time.time()}".encode()).hexdigest()[:12]
        out: List[Path] = []
        try:
            with zipfile.ZipFile(archive) as z:
                total = 0
                for info in z.infolist():
                    name = info.filename
                    if info.is_dir() or info.flag_bits & 0x1:  # folders, encrypted members
                        continue
                    if not filescan.is_supported(Path(name)) or Path(name).suffix.lower() in filescan.ARCHIVE_EXT:
                        continue
                    total += info.file_size
                    if total > ZIP_MAX_BYTES or len(out) >= MAX_FILES:
                        break
                    target = work / Path(*[part for part in Path(name).parts if part not in ("..", "/", "\\")])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    self._zip_members[str(target).lower()] = (archive, name)
                    out.append(target)
                if any(i.flag_bits & 0x1 for i in z.infolist()):
                    self._zip_notes[str(archive).lower()] = "some files inside are password-protected"
        except (zipfile.BadZipFile, OSError) as exc:
            self._zip_notes[str(archive).lower()] = f"the ZIP couldn't be opened ({type(exc).__name__})"
        return out

    def _display_name(self, path: Path) -> str:
        member = self._zip_members.get(str(path).lower())
        return f"{member[0].name} › {member[1]}" if member else path.name

    # ---- checking ---------------------------------------------------------------------------
    def _scan_file(self, path: Path) -> FileResult:
        from . import visual
        try:
            st = path.stat()
            key = (str(path).lower(), st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        if key and key in self._cache:
            return self._cache[key]
        opts = self._opts()
        max_pages = int(opts.get("max_pdf_pages", 0))
        pages = filescan.count_pages(path)
        if pages >= LONG_FILE_PAGES:
            n = pages if not max_pages else min(pages, max_pages)
            self.events.put(("scan_progress", f"Checking all {n} pages of {self._display_name(path)} for "
                                              "private details. Long or scanned files can take a moment…"))
        ex = filescan.extract_file(path, float(opts.get("max_file_mb", 200)), max_pages)
        found = self.detect(ex.text) if ex.text.strip() else []
        found = found + visual.findings_for(ex, found)
        res = FileResult(self._display_name(path), found, ex.note, ex.method, path=path, extracted=ex,
                         can_redact=ex.can_redact, unchecked=ex.unchecked)
        if key:
            if len(self._cache) > 200:
                self._cache.clear()
            self._cache[key] = res
        return res

    def _scan_image(self, image) -> Optional[FileResult]:
        from . import visual
        try:
            digest = hashlib.sha1(image.convert("RGB").tobytes()).hexdigest()
        except Exception:
            digest = None
        if digest in self._own_images:
            return None  # the safe image we just put on the clipboard
        key = ("image", digest)
        if digest and key in self._cache:
            return self._cache[key]
        ex = filescan.extract_image(image)
        found = self.detect(ex.text) if ex.text.strip() else []
        found = found + visual.findings_for(ex, found)
        res = FileResult("Copied image", found, ex.note, ex.method, image=image, extracted=ex,
                         unchecked=ex.unchecked)
        if digest:
            self._cache[key] = res
        return res
    # ---- safe copies ------------------------------------------------------------------------
    def _style(self) -> str:
        return self.config.data.get("mask_style", "partial")

    def _cleanup(self) -> None:
        # only the default folder is emptied; a folder the user chose keeps its copies
        root = default_safe_copies_dir()
        cutoff = time.time() - SAFE_COPY_HOURS * 3600
        for d in root.iterdir() if root.exists() else []:
            try:
                if d.is_dir() and d.name != "_work" and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
        work = work_dir()  # unpacked ZIP contents, web uploads: kept only while a card may need them
        for d in list(work.iterdir()) + list((root / "_work").glob("*") if (root / "_work").exists() else []):
            try:
                if d.stat().st_mtime < time.time() - 2 * 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def _new_out_dir(self) -> Path:
        d = safe_copies_dir() / time.strftime("%Y%m%d-%H%M%S")
        n = 1
        base = d
        while d.exists():
            n += 1
            d = Path(f"{base}-{n}")
        d.mkdir(parents=True)
        return d

    def _redact_job(self, ctx: ShareContext, results: List[FileResult], chosen: Dict[int, list]) -> None:
        from . import redact
        style = self._style()
        replaced = missed = 0

        if ctx.kind == "image":
            r = results[0]
            img, outcome = redact.redact_image(r.image, r.extracted, chosen.get(0, []), style)
            self._own_images.add(hashlib.sha1(img.convert("RGB").tobytes()).hexdigest())
            self._quiet_until = time.monotonic() + 3
            ok = winutil.set_clipboard_image(img)
            replaced, missed = outcome.replaced, outcome.missed
            msg = (f"Replaced {replaced} detail{'s' if replaced != 1 else ''}. The safe image is on the clipboard: "
                   "press Ctrl+V to paste it." if ok else "Couldn't put the safe image on the clipboard.")
            self._finish(ok, msg, missed)
            return

        out_dir = self._new_out_dir()
        by_path = {str(r.path).lower(): (i, r) for i, r in enumerate(results) if r.path is not None}
        outputs: List[Path] = []
        kept: List[str] = []   # files whose format can't be rewritten: handed on unchanged
        tally = [0, 0]

        def one(src: Path, target: Path) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            hit = by_path.get(str(src).lower())
            picked = chosen.get(hit[0], []) if hit else []
            if not picked:
                shutil.copy2(src, target)  # nothing flagged/chosen in this one: unchanged
                return
            i, r = hit
            if not r.can_redact:
                shutil.copy2(src, target)
                kept.append(r.name)
                tally[1] += len(picked)
                return
            outcome = redact.redact_file(src, r.extracted, picked, target, style)
            tally[0] += outcome.replaced
            tally[1] += outcome.missed

        for raw in ctx.paths:
            p = Path(raw)
            if p.is_dir():          # a folder: same structure, every file copied, flagged ones made safe
                for c in sorted(p.rglob("*")):
                    if c.is_file():
                        one(c, out_dir / p.name / c.relative_to(p))
                outputs.append(out_dir / p.name)
            elif p.suffix.lower() in filescan.ARCHIVE_EXT and p.is_file():
                outputs.append(self._safe_zip(p, out_dir / p.name, one, kept))
            elif p.is_file():
                one(p, out_dir / p.name)
                outputs.append(out_dir / p.name)
        replaced, missed = tally
        log.info("safe copies: %d item(s), %d replaced, %d missed", len(outputs), replaced, missed)
        what = f"Replaced {replaced} detail{'s' if replaced != 1 else ''}"
        if kept:
            one_file = len(kept) == 1
            what += (f". {', '.join(kept[:3])} {'is' if one_file else 'are'} in a format SENSIQ can't change "
                     f"safely (old Office file or a ZIP with locked files), so {'it was' if one_file else 'they were'} "
                     "left as is. Save Office files as .docx/.xlsx/.pptx and try again")
            missed = 0  # already explained

        if ctx.kind == "dialog":
            ok = self._fill_dialog(ctx.dialog_hwnd, outputs)
            copies = "safe copy was" if len(outputs) == 1 else f"{len(outputs)} safe copies were"
            msg = (f"{what}. The {copies} selected in the upload box and submitted instead of the original." if ok else
                   f"{what}. The upload box closed, so the safe copies were put on the clipboard and opened "
                   "in File Explorer. Upload them from there.")
            if not ok:
                self._hand_over(outputs, show=True)
        elif ctx.kind == "browser":
            ok = True
            ctx.reply("safe", outputs)
            msg = f"{what}. The page received the safe copy instead of the original."
        elif ctx.kind == "opened":
            self._hand_over(outputs, show=True)
            ok = True
            msg = f"{what}. The safe copy is open in File Explorer and on the clipboard. Share that one instead."
        elif ctx.kind == "uploaded":
            self._hand_over(outputs, show=True)
            ok = True
            msg = (f"{what}. Remove the original from the page, then press Ctrl+V there to attach the safe "
                   "copy, or drag it from the folder that just opened.")
        elif ctx.kind == "drag":
            self._hand_over(outputs, show=True)
            ok = True
            msg = (f"{what}. A drag can't be changed once it starts, so drop it elsewhere or press Esc. "
                   "The safe copies are on the clipboard (Ctrl+V) and open in File Explorer to drag.")
        else:
            ok = self._hand_over(outputs, show=False)
            msg = (f"{what}. The clipboard now holds the safe copies: press Ctrl+V to paste them." if ok else
                   f"{what}, but the clipboard was busy. The safe copies are open in File Explorer.")
            if not ok:
                self._hand_over(outputs, show=True)
        self._finish(ok, msg, missed)

    def _safe_zip(self, archive: Path, target: Path, one, kept: List[str]) -> Path:
        """Rebuild a ZIP: every member copied, the checked ones replaced by their safe copies."""
        unpacked = {member: Path(tmp) for tmp, (arch, member) in self._zip_members.items()
                    if str(arch).lower() == str(archive).lower()}
        scratch = target.parent / f".{target.stem}-parts"
        with zipfile.ZipFile(archive) as zin:
            if any(i.flag_bits & 0x1 for i in zin.infolist()):  # locked members can't be copied over
                shutil.copy2(archive, target)
                kept.append(archive.name)
                return target
        with zipfile.ZipFile(archive) as zin, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                tmp = unpacked.get(info.filename)
                if tmp is not None and tmp.exists():
                    safe = scratch / info.filename
                    one(tmp, safe)
                    zout.write(safe, info.filename)
                else:
                    zout.writestr(info, zin.read(info.filename))
        shutil.rmtree(scratch, ignore_errors=True)
        return target

    def _finish(self, ok: bool, msg: str, missed: int) -> None:
        if missed:
            msg += (f" {missed} detail{'s' if missed != 1 else ''} couldn't be located in the file, "
                    "so check the copy before sending.")
        self.events.put(("redact_done", ok, msg))

    def _hand_over(self, outputs: List[Path], show: bool) -> bool:
        self._quiet_until = time.monotonic() + 3
        ok = winutil.set_clipboard_files(outputs)
        if show and outputs:
            subprocess.Popen(["explorer.exe", "/select,", str(outputs[0])])
        return ok

    def _fill_dialog(self, hwnd: int, outputs: List[Path]) -> bool:
        """Point the still-open file dialog at the safe copies and press Open."""
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread(debug=False):
            if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
                return False
            dlg = auto.ControlFromHandle(hwnd)
            box = dlg.EditControl(AutomationId="1148", searchDepth=4)
            if dlg is None or not box.Exists(0.5, 0.1):
                return False

            def press_open() -> None:
                try:
                    btn = dlg.Control(AutomationId="1", searchDepth=3)
                    btn.GetPattern(auto.PatternId.InvokePattern).Invoke()
                except Exception:
                    box.SetFocus()
                    auto.SendKeys("{Enter}", waitTime=0.1)

            if len(outputs) == 1:
                box.GetValuePattern().SetValue(str(outputs[0]))
            else:  # go to the safe-copies folder, then pick all files there
                box.GetValuePattern().SetValue(str(outputs[0].parent))
                press_open()
                time.sleep(1.0)
                box = dlg.EditControl(AutomationId="1148", searchDepth=4)
                box.GetValuePattern().SetValue(" ".join(f'"{p.name}"' for p in outputs))
            time.sleep(0.2)
            press_open()
            return True




# ---------------------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------------------
class ClipboardWatcher(_Logged):
    def __init__(self, scanner: FileScanWorker, config, stop_event: threading.Event):
        super().__init__(name="ClipboardWatcher", daemon=True)
        self.scanner, self.config, self.stop_event = scanner, config, stop_event

    def watch(self) -> None:
        from PIL import ImageGrab
        user32 = ctypes.windll.user32
        last = user32.GetClipboardSequenceNumber()
        while not self.stop_event.wait(0.4):
            seq = user32.GetClipboardSequenceNumber()
            if seq == last:
                continue
            last = seq
            if not self.config.data.get("enabled", True) or self.scanner.clipboard_is_ours():
                continue
            content = None
            for _ in range(3):  # the clipboard can be briefly locked by the app that wrote it
                try:
                    content = ImageGrab.grabclipboard()
                    break
                except Exception:
                    time.sleep(0.15)
            if isinstance(content, list) and content:
                self.scanner.submit_files("Copied file", content)
            elif content is not None and hasattr(content, "size"):
                self.scanner.submit_image("Copied image", content)


# ---------------------------------------------------------------------------------------
# Drag and drop out of File Explorer / desktop
# ---------------------------------------------------------------------------------------
class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _root_class(x: int, y: int) -> Tuple[int, str]:
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.WindowFromPoint.argtypes = [_POINT]
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    hwnd = user32.WindowFromPoint(_POINT(x, y))
    root = user32.GetAncestor(hwnd, 2) if hwnd else None  # GA_ROOT
    if not root:
        return 0, ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ctypes.c_void_p(root), buf, 256)
    return int(root), buf.value


class DragWatcher(_Logged):
    """Files dragged out of File Explorer / the desktop. The check runs when they are dropped:
    drops back into Explorer (moving files) are ignored, and drops onto a browser page that the
    SENSIQ extension protects are left to the extension (no double card)."""

    def __init__(self, scanner: FileScanWorker, config, stop_event: threading.Event, skip_apps=None):
        super().__init__(name="DragWatcher", daemon=True)
        self.scanner, self.config, self.stop_event = scanner, config, stop_event
        self.skip_apps = skip_apps or (lambda: set())

    def _drop_target(self, user32) -> str:
        pt = _POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        root, cls = _root_class(pt.x, pt.y)
        if cls in EXPLORER_ROOTS:
            return "explorer"
        return winutil.process_name(winutil.window_pid(root)) if root else ""

    def watch(self) -> None:
        import uiautomation as auto
        user32 = ctypes.windll.user32
        threshold = max(8, 2 * user32.GetSystemMetrics(68))  # SM_CXDRAG
        with auto.UIAutomationInitializerInThread(debug=False):
            shell = None
            down = None  # (x, y, root hwnd, root class, item name)
            fired = False
            dragged: List[str] = []
            while not self.stop_event.wait(0.03):
                pressed = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
                if not pressed:
                    if dragged:  # dropped: where?
                        target = self._drop_target(user32)
                        if target != "explorer" and target not in self.skip_apps():
                            self.scanner.submit_files("Dragged file", dragged, kind="drag")
                        dragged = []
                    down, fired = None, False
                    continue
                pt = _POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                if down is None:
                    down = self._on_press(auto, pt.x, pt.y)
                    continue
                if fired or not down or not down[4]:
                    continue
                if abs(pt.x - down[0]) + abs(pt.y - down[1]) < threshold:
                    continue
                fired = True
                if not self.config.data.get("enabled", True):
                    continue
                try:
                    if shell is None:
                        import comtypes.client
                        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
                    dragged = self._selected_paths(shell, down[2], down[3], down[4]) or []
                except Exception:
                    log.debug("drag selection lookup failed", exc_info=True)

    def _on_press(self, auto, x: int, y: int):
        """At mouse-down: is the cursor on a file item in Explorer or on the desktop?"""
        root, cls = _root_class(x, y)
        if cls not in EXPLORER_ROOTS or winutil.process_name(winutil.window_pid(root)) != "explorer.exe":
            return (x, y, root, cls, None)
        name = None
        try:
            c = auto.ControlFromPoint(x, y)
            for _ in range(4):
                if c is None:
                    break
                if c.ControlTypeName == "ListItemControl":
                    name = c.Name
                    break
                c = c.GetParentControl()
        except Exception:
            pass
        return (x, y, root, cls, name)

    @staticmethod
    def _selected_paths(shell, root: int, cls: str, item_name: str) -> List[str]:
        if cls in ("Progman", "WorkerW"):  # desktop: map the selected names to desktop items
            import uiautomation as auto
            names = set()
            try:
                lst = auto.ControlFromHandle(root).ListControl(searchDepth=6)
                names = {s.Name for s in lst.GetSelectionPattern().GetSelection()}
            except Exception:
                names = {item_name}
            names = names or {item_name}
            items = shell.NameSpace(0).Items()
            return [items.Item(i).Path for i in range(items.Count) if items.Item(i).Name in names]
        best: List[str] = []
        windows = shell.Windows()
        for i in range(windows.Count):
            try:
                w = windows.Item(i)
                if w is None or int(w.HWND) != root:
                    continue
                sel = w.Document.SelectedItems()
                paths = [sel.Item(j).Path for j in range(sel.Count)]
                names = [sel.Item(j).Name for j in range(sel.Count)]
                if item_name in names:  # with Explorer tabs, pick the tab that owns the dragged item
                    return paths
                best = best or paths
            except Exception:
                continue
        return best


# ---------------------------------------------------------------------------------------
# Open / Upload / Attach dialogs
# ---------------------------------------------------------------------------------------
_FOLDER_IDS = {  # Windows known folders (follow OneDrive / custom redirection)
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "profile": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",
}
_folders_cache: Optional[Dict[str, Path]] = None


def _known_folder(guid: str) -> Optional[Path]:
    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]
    g = GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g))
    out = ctypes.c_wchar_p()
    try:
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out))
        return Path(out.value) if out.value else None
    except Exception:
        return None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _display_name(path: Path) -> str:
    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", ctypes.c_void_p), ("iIcon", ctypes.c_int), ("dwAttributes", ctypes.c_ulong),
                    ("szDisplayName", ctypes.c_wchar * 260), ("szTypeName", ctypes.c_wchar * 80)]
    info = SHFILEINFOW()
    if ctypes.windll.shell32.SHGetFileInfoW(str(path), 0, ctypes.byref(info), ctypes.sizeof(info), 0x200):
        return info.szDisplayName
    return path.name


def _folders() -> Dict[str, Path]:
    """Lower-case names the dialog may show ("Downloads", "Priya Sharma", "OneDrive - Personal"...) -> folder."""
    global _folders_cache
    if _folders_cache is not None:
        return _folders_cache
    found: Dict[str, Path] = {}
    roots: List[Path] = []
    for key, guid in _FOLDER_IDS.items():
        p = _known_folder(guid)
        if p and p.is_dir():
            roots.append(p)
            found.setdefault(key, p)
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            roots.append(Path(os.environ[var]))
    for p in roots:
        for name in (p.name, _display_name(p)):
            if name:
                found.setdefault(name.strip().lower(), p)
    try:  # the names File Explorer really shows, e.g. the user folder as "Priya Sharma"
        import comtypes.client
        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        for key in ("Profile", "Desktop", "Personal", "Downloads", "My Pictures", "My Video", "My Music",
                    "OneDrive", "Public", "CommonDesktopDir"):
            try:
                ns = shell.NameSpace(f"shell:{key}")
                p = Path(ns.Self.Path)
                if p.is_dir():
                    found.setdefault(str(ns.Title).strip().lower(), p)
                    if p not in roots:
                        roots.append(p)
            except Exception:
                continue
    except Exception:
        log.debug("shell folder names unavailable", exc_info=True)
    _folders_cache = found
    return found


def _dialog_folder(address: str) -> Optional[Path]:
    """Folder shown in a file dialog's address bar: a real path, a breadcrumb like
    "Priya Sharma > Desktop > Forms", or a special name like "Downloads"."""
    value = address.split(": ", 1)[-1].strip()
    p = Path(value)
    if p.is_absolute() and p.is_dir():
        return p
    parts = [s.strip() for s in value.replace("\\", ">").split(">") if s.strip()]
    folders = _folders()
    for i in range(len(parts) - 1, -1, -1):  # deepest part that is a known folder, then walk down
        base = folders.get(parts[i].lower())
        if base is None:
            continue
        cand = base.joinpath(*parts[i + 1:])
        if cand.is_dir():
            return cand
    return None


def _resolve(folder: Path, name: str) -> List[Path]:
    p = folder / name
    if p.is_file():
        return [p]
    try:  # extensions are hidden in the dialog: "card" -> card.png
        return [c for c in folder.glob(f"{glob_escape(name)}.*") if c.is_file() and c.stem == name][:3]
    except OSError:
        return []


def glob_escape(s: str) -> str:
    return "".join(f"[{c}]" if c in "[]*?" else c for c in s)



_MRU_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32\OpenSavePidlMRU\*"


def _mru_stamp() -> int:
    """Last-write time of Windows' list of files opened through file dialogs. It changes
    whenever a dialog's Open is pressed, and not on Cancel."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MRU_KEY) as k:
            return winreg.QueryInfoKey(k)[2]
    except OSError:
        return 0


def _mru_latest(count: int = 1) -> List[str]:
    """The files most recently opened through a file dialog (newest first)."""
    import struct
    import winreg
    out: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MRU_KEY) as k:
            order, _ = winreg.QueryValueEx(k, "MRUListEx")
            for i in range(min(count, len(order) // 4)):
                idx = struct.unpack_from("<I", order, i * 4)[0]
                if idx == 0xFFFFFFFF:
                    break
                pidl, _ = winreg.QueryValueEx(k, str(idx))
                buf = ctypes.create_string_buffer(bytes(pidl), len(pidl) + 2)
                path = ctypes.create_unicode_buffer(32768)
                if ctypes.windll.shell32.SHGetPathFromIDListW(buf, path) and Path(path.value).is_file():
                    out.append(path.value)
    except OSError:
        pass
    return out


class CancelKeyWatch(threading.Thread):
    """Notices Esc / Alt+F4 (the keys that cancel a file dialog). It hooks the keyboard only
    while a file dialog is open (arm/disarm), looks at no other keys, and stores only the time
    of the last cancel key."""

    WM_APP_ARM, WM_APP_DISARM, WM_QUIT = 0x8001, 0x8002, 0x0012

    def __init__(self):
        super().__init__(name="CancelKeyWatch", daemon=True)
        self.last_cancel = 0.0
        self._tid = 0
        self._ready = threading.Event()

    def arm(self) -> None:
        self._post(self.WM_APP_ARM)

    def disarm(self) -> None:
        self._post(self.WM_APP_DISARM)

    def stop(self) -> None:
        self._post(self.WM_QUIT)

    def _post(self, msg: int) -> None:
        if self._ready.wait(2) and self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, msg, 0, 0)

    def run(self) -> None:
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        def proc(code, wparam, lparam):
            if code == 0 and wparam in (0x0100, 0x0104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == 0x1B or (kb.vkCode == 0x73 and kb.flags & 0x20):  # Esc, Alt+F4
                    self.last_cancel = time.monotonic()
            return user32.CallNextHookEx(None, code, wparam, lparam)

        callback = HOOKPROC(proc)
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create this thread's queue
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        hook = None
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_APP_ARM and not hook:
                hook = user32.SetWindowsHookExW(13, callback, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
            elif msg.message == self.WM_APP_DISARM and hook:
                user32.UnhookWindowsHookEx(hook)
                hook = None
        if hook:
            user32.UnhookWindowsHookEx(hook)


class OpenedFileWatcher(_Logged):
    """Optional ("check files when I open them", off by default): when you open a document or
    picture, Windows adds it to your Recent items. This notices that and offers a one-click check.
    Nothing is checked unless you click."""

    def __init__(self, scanner: FileScanWorker, config, events: "queue.Queue", stop_event: threading.Event):
        super().__init__(name="OpenedFileWatcher", daemon=True)
        self.scanner, self.config, self.events, self.stop_event = scanner, config, events, stop_event

    def watch(self) -> None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        since = time.time()
        offered: Dict[str, float] = {}
        while not self.stop_event.wait(2.0):
            if not (self.config.data.get("enabled", True)
                    and self.config.data.get("file_scan", {}).get("opened_files", False)):
                since = time.time()
                continue
            try:
                links = [l for l in recent.glob("*.lnk") if l.stat().st_mtime > since]
            except OSError:
                continue
            since = time.time()
            for link in links:
                try:
                    target = Path(shell.CreateShortcut(str(link)).TargetPath)
                except Exception:
                    continue
                if not target.is_file() or not filescan.is_supported(target) or _is_safe_copy(target):
                    continue
                key = str(target).lower()
                if time.time() - offered.get(key, 0) < 600:  # don't ask twice about the same file
                    continue
                offered[key] = time.time()
                self.events.put(("offer_check", str(target)))


class DialogWatcher(_Logged):
    """Watches Open / Upload / Attach dialogs.

    * As soon as the selection changes, it asks the dialog's own file view for the selected
      items' full paths (the view's Copy command, clipboard restored straight after), so it
      works in every folder without knowing where files live.
    * Once the selection has rested briefly, the files are checked and the card offers to
      upload a safe copy instead.
    * If the dialog closes first (double-click, quick Enter) and Windows recorded an Open,
      the files are still checked; the card then offers a safe copy to paste in place of the
      one that was just added.
    """

    TICK = 0.1      # seconds between looks at the foreground window...
    TICK_FAST = 0.05  # ...and while a file dialog is open (catches double-clicks)
    SETTLE = 0.25   # selection must rest this long before the card is shown

    def __init__(self, scanner: FileScanWorker, config, stop_event: threading.Event, skip_apps=None):
        super().__init__(name="DialogWatcher", daemon=True)
        self.scanner, self.config, self.stop_event = scanner, config, stop_event
        self.skip_apps = skip_apps or (lambda: set())  # browsers where the extension checks uploads

    def watch(self) -> None:
        import uiautomation as auto
        auto.SetGlobalSearchTimeout(0.3)
        user32 = ctypes.windll.user32
        keys = CancelKeyWatch()
        keys.start()
        with auto.UIAutomationInitializerInThread(debug=False):
            opened_at = 0.0
            dialog = None          # hwnd of the file dialog being watched
            mru_at_open = 0        # Windows' open-list stamp when it appeared
            state = None           # last selection state seen
            paths: List[str] = []  # full paths for that selection
            changed_at = 0.0
            submitted = False
            cancel_rects: List[Tuple[int, int, int, int]] = []  # Cancel button and the window's X
            cursor = (0, 0)
            esc_seen = False
            pt = _POINT()
            while not self.stop_event.wait(self.TICK_FAST if dialog else self.TICK):
                if not self.config.data.get("enabled", True):
                    dialog = None
                    continue
                try:
                    hwnd = winutil.foreground_window()
                    is_dialog = bool(hwnd) and self._class(hwnd) == "#32770"
                    if is_dialog and hwnd != dialog and winutil.window_pid(hwnd) == os.getpid():
                        continue  # SENSIQ's own window (e.g. choosing the safe-copies folder)
                    if is_dialog and hwnd != dialog and \
                            winutil.process_name(winutil.window_pid(hwnd)) in self.skip_apps():
                        continue  # the browser extension checks this upload when the page receives it

                    # the watched dialog has gone: was it Open/double-click/Enter, or Cancel?
                    if dialog and dialog != hwnd and not user32.IsWindow(dialog):
                        keys.disarm()
                        esc_seen = esc_seen or keys.last_cancel >= opened_at
                        cancelled = esc_seen or any(l <= cursor[0] <= r and t <= cursor[1] <= b
                                                    for l, t, r, b in cancel_rects)
                        opened = not cancelled or _mru_stamp() != mru_at_open
                        log.debug("file dialog closed: cancel key=%s cursor on cancel=%s opened=%s "
                                  "card shown=%s files=%d", keys.last_cancel >= opened_at, cancelled and not esc_seen,
                                  opened, submitted, len(paths))
                        if not submitted and opened and (state and (state[0] or state[1])):
                            files = [p for p in paths if Path(p).is_file()] or _mru_latest(1)
                            if files:
                                self.scanner.submit_files("File just added to the page", files, kind="uploaded")
                        dialog = None
                    if not is_dialog:
                        continue

                    if hwnd != dialog:
                        new = self._state(auto, hwnd)
                        if new is None:
                            continue  # some other dialog, not a file picker
                        dialog, mru_at_open = hwnd, _mru_stamp()
                        state, paths, submitted = None, [], False
                        cancel_rects, esc_seen = self._cancel_areas(auto, hwnd), False
                        opened_at = time.monotonic()
                        keys.arm()

                    # how might it close? remember where the mouse is (Cancel / X clicks)
                    user32.GetCursorPos(ctypes.byref(pt))
                    cursor = (pt.x, pt.y)

                    current = self._state(auto, hwnd)
                    if current is None:
                        continue
                    if current != state:
                        state, changed_at, submitted = current, time.monotonic(), False
                        paths = self._paths(hwnd, *current, scanner=self.scanner) if (current[0] or current[1]) else []
                        continue
                    if paths and not submitted and time.monotonic() - changed_at >= self.SETTLE:
                        submitted = True
                        self.scanner.submit_files("File chosen to upload or attach", paths,
                                                  kind="dialog", dialog_hwnd=hwnd)
                except Exception:
                    log.debug("dialog watch failed", exc_info=True)

    @staticmethod
    def _cancel_areas(auto, hwnd: int) -> List[Tuple[int, int, int, int]]:
        """Screen rectangles of the dialog's Cancel button and its title-bar X."""
        out: List[Tuple[int, int, int, int]] = []
        try:
            dlg = auto.ControlFromHandle(hwnd)
            cancel = dlg.Control(AutomationId="2", searchDepth=3)
            if cancel.Exists(0, 0):
                r = cancel.BoundingRectangle
                out.append((r.left, r.top, r.right, r.bottom))
            r = dlg.BoundingRectangle
            out.append((r.right - 70, r.top, r.right, r.top + 45))  # title-bar X (and a margin)
        except Exception:
            pass
        return out

    @staticmethod
    def _class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 64)
        return buf.value

    @staticmethod
    def _state(auto, hwnd: int):
        """(selected item names, typed file name, address) of an Open-type file dialog, else None."""
        dlg = auto.ControlFromHandle(hwnd)
        if dlg is None:
            return None
        name_box = dlg.EditControl(AutomationId="1148", searchDepth=4)
        if not name_box.Exists(0, 0):
            return None  # not a file dialog
        save = dlg.ButtonControl(AutomationId="1", searchDepth=2)
        if save.Exists(0, 0) and save.Name.lower().startswith("save"):
            return None  # Save dialogs don't share anything
        names: Tuple[str, ...] = ()
        items = dlg.ListControl(ClassName="UIItemsView", searchDepth=8)
        if items.Exists(0, 0):
            try:
                names = tuple(s.Name for s in items.GetSelectionPattern().GetSelection())
            except Exception:
                names = ()
        try:
            typed = (name_box.GetValuePattern().Value or "").strip()
        except Exception:
            typed = ""
        address = dlg.ToolBarControl(AutomationId="1001", searchDepth=8)
        addr = address.Name if address.Exists(0, 0) else ""
        return names, typed if not names else "", addr

    @staticmethod
    def _paths(hwnd: int, names, typed: str, address: str, scanner: Optional[FileScanWorker] = None) -> List[str]:
        paths: List[str] = []
        if names:
            if scanner is not None:
                scanner._quiet_until = time.monotonic() + 2  # our own brief clipboard use
            copied = winutil.copy_selection_paths(hwnd)
            if copied:  # the dialog told us exactly what is selected (folders are skipped)
                return [p for p in copied if Path(p).is_file()][:10]
        # fallback: a name typed into the "File name" box (or the copy was refused)
        raw = typed or " ".join(f'"{n}"' for n in names)
        wanted = [n for n in raw.split('"') if n.strip()] if '"' in raw else ([raw] if raw else [])
        folder = _dialog_folder(address) if address else None
        for name in wanted[:10]:
            p = Path(name)
            if p.is_absolute():
                hits = [p] if p.is_file() else []
            elif folder is not None:
                hits = _resolve(folder, name)
            else:
                hits = []
            if not hits:
                log.debug("upload dialog: couldn't locate a selected file")
            paths.extend(str(h) for h in hits)
        return sorted(set(paths))[:10]

    @staticmethod
    def _selection(auto, hwnd: int) -> Optional[Tuple[str, ...]]:
        """Full paths currently selected in the dialog (used by tests and tools)."""
        state = DialogWatcher._state(auto, hwnd)
        if state is None:
            return None
        return tuple(sorted(DialogWatcher._paths(hwnd, *state))) or None

