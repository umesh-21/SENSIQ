"""1.3.0: closing a report to pick another file, a choosable safe-copies folder, and the
"checking…" / "nothing found" notes for uploads. Every value is fake."""
import os
import queue
import threading
import time
import types

import pytest

from sensiq import paths
from sensiq.config import Config, IgnoreList
from sensiq.detector.engine import DetectionEngine
from sensiq.sharewatch import FileScanWorker, ShareContext


@pytest.fixture(scope="module")
def engine():
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["presidio"]["enabled"] = False
    return DetectionEngine(cfg, IgnoreList(), "/nonexistent")


@pytest.fixture(autouse=True)
def default_folder():
    yield
    paths.set_safe_copies_folder(None)


def _events(engine, kind, files):
    q = queue.Queue()
    w = FileScanWorker(engine.detect_all, engine.config, q, threading.Event())
    w._run_job(("files", ShareContext(kind, "test", [str(f) for f in files]), [str(f) for f in files]))
    out = []
    while not q.empty():
        out.append(q.get())
    return out


# ---- "checking…" and "nothing found" ----------------------------------------------------------
def test_upload_says_checking_then_report(tmp_path, engine):
    f = tmp_path / "form.txt"
    f.write_text("Aadhaar 4918 3205 7615", encoding="utf-8")
    kinds = [e[0] for e in _events(engine, "dialog", [f])]
    assert kinds[0] == "scan_start" and "file_report" in kinds and "scan_clean" not in kinds


def test_clean_upload_says_nothing_found(tmp_path, engine):
    f = tmp_path / "menu.txt"
    f.write_text("Lunch menu: rice, dal, salad", encoding="utf-8")
    ev = _events(engine, "browser", [f])
    assert [e[0] for e in ev] == ["scan_start", "scan_clean"]
    assert "menu.txt" in ev[0][1] and "No personal information" in ev[1][1]


def test_copying_files_stays_quiet(tmp_path, engine):
    f = tmp_path / "menu.txt"
    f.write_text("Lunch menu", encoding="utf-8")
    assert _events(engine, "copy", [f]) == []   # no note on every Ctrl+C


# ---- safe-copies folder ------------------------------------------------------------------------
def test_chosen_folder_is_used_and_default_otherwise(tmp_path):
    assert paths.safe_copies_dir() == paths.default_safe_copies_dir()
    mine = tmp_path / "My safe copies"
    paths.set_safe_copies_folder(str(mine))
    assert paths.safe_copies_dir() == mine and mine.is_dir() and paths.safe_copies_is_custom()
    assert paths.work_dir() != mine and not paths.work_dir().is_relative_to(mine)


def test_unusable_folder_falls_back_to_default(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    paths.set_safe_copies_folder(str(blocker / "sub"))   # can't create a folder inside a file
    assert paths.safe_copies_dir() == paths.default_safe_copies_dir()


def test_chosen_folder_is_never_cleaned(tmp_path, engine):
    mine = tmp_path / "keep"
    paths.set_safe_copies_folder(str(mine))
    old = mine / "20200101-000000"
    old.mkdir(parents=True)
    (old / "safe.pdf").write_text("x")
    past = time.time() - 30 * 24 * 3600
    os.utime(old, (past, past))
    FileScanWorker(engine.detect_all, engine.config, queue.Queue(), threading.Event())._cleanup()
    assert (old / "safe.pdf").exists()


def test_safe_copies_are_written_to_chosen_folder(tmp_path, engine):
    mine = tmp_path / "chosen"
    paths.set_safe_copies_folder(str(mine))
    w = FileScanWorker(engine.detect_all, engine.config, queue.Queue(), threading.Event())
    assert w._new_out_dir().parent == mine


def test_installer_choice_wins_at_startup(tmp_path, monkeypatch):
    from sensiq.app import App
    cfg = Config.defaults()
    cfg.save = lambda: None
    chosen = str(tmp_path / "from installer")
    monkeypatch.setattr(paths, "registry_safe_copies_folder", lambda: chosen)
    remembered = []
    monkeypatch.setattr(paths, "remember_safe_copies_folder", remembered.append)
    App._apply_safe_copies_folder(types.SimpleNamespace(config=cfg), startup=True)
    assert cfg.data["file_scan"]["safe_copies_folder"] == chosen
    assert paths.safe_copies_dir() == tmp_path / "from installer" and remembered == [chosen]


# ---- closing a report ------------------------------------------------------------------------------
def test_reports_from_same_dialog_replace_each_other():
    from sensiq.app import App
    a = ShareContext("dialog", "x", dialog_hwnd=101)
    assert App._same_dialog(a, ShareContext("dialog", "y", dialog_hwnd=101))
    assert not App._same_dialog(a, ShareContext("dialog", "y", dialog_hwnd=202))
    assert not App._same_dialog(a, ShareContext("copy", "y"))


def test_close_button_dismisses_card(tmp_path, engine):
    tk = pytest.importorskip("tkinter")
    from sensiq.overlay import Overlay
    f = tmp_path / "id.txt"
    f.write_text("Aadhaar 4918 3205 7615", encoding="utf-8")
    w = FileScanWorker(engine.detect_all, engine.config, queue.Queue(), threading.Event())
    res = [w._scan_file(f)]
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    ov = Overlay(root, on_replace=None, on_ignore=None, on_fix_all=None, on_style=None, get_style=lambda: "partial")
    reasons = []
    ov.file_report(ShareContext("dialog", "Upload"), res, on_apply=lambda c: None, on_close=reasons.append)

    def walk(x):
        yield x
        for c in x.winfo_children():
            yield from walk(c)
    root.update()
    x = [c for c in walk(ov.report_win) if isinstance(c, tk.Label) and c.cget("text") == "✕"]
    assert len(x) == 1
    x[0].event_generate("<Button-1>", when="now")
    root.update()
    assert reasons == ["dismissed"] and ov.report_win is None
    root.destroy()
