"""1.5.0: nothing is skipped without you seeing it. Values marked "not private" stay on file
cards (unticked), ID numbers can't be marked, only the tick box toggles, a red line names
what won't be replaced, the list can be reviewed, and the tiny Aadhaar copy is covered.
Every value is fake."""
import json
import logging
import queue
import threading

import pytest

from sensiq import filescan, visual
from sensiq.config import Config, IgnoreList
from sensiq.detector.engine import DetectionEngine
from sensiq.detector.types import Finding
from sensiq.sharewatch import FileScanWorker, ShareContext

AADHAAR = "2554 8151 2997"  # passes the checksum; fake


def _engine(ignore=None, never=()):
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["presidio"]["enabled"] = False
    cfg.data["never_flag_words"] = list(never)
    return DetectionEngine(cfg, ignore or IgnoreList(), "/nonexistent")


def _ignored_aadhaar():
    ign = IgnoreList()
    ign.add(Finding(0, 14, "AADHAAR", AADHAAR).fingerprint(), persist=False)
    return ign


# ---- A: marked values are listed (unticked) on files, still hidden while typing --------------
def test_typing_still_skips_marked_value():
    eng = _engine(_ignored_aadhaar())
    assert [f.kind for f in eng.detect(f"Aadhaar {AADHAAR}")] == []


def test_files_list_marked_value_as_ignored():
    eng = _engine(_ignored_aadhaar())
    found = eng.detect_all(f"Aadhaar {AADHAAR}", mark_ignored=True)
    assert [(f.kind, f.ignored) for f in found] == [("AADHAAR", True)]


def test_card_shown_for_marked_id_number(tmp_path):
    eng = _engine(_ignored_aadhaar())
    f = tmp_path / "card.txt"
    f.write_text(f"Aadhaar {AADHAAR}", encoding="utf-8")
    q = queue.Queue()
    w = FileScanWorker(eng.detect_all, eng.config, q, threading.Event())
    w._run_job(("files", ShareContext("dialog", "Upload", [str(f)]), [str(f)]))
    events = [q.get()[0] for _ in range(q.qsize())]
    assert "file_report" in events          # never silently passed through


def test_no_card_when_only_low_risk_marked_values(tmp_path):
    ign = IgnoreList()
    ign.add(Finding(0, 6, "PINCODE", "560001").fingerprint())
    eng = _engine(ign)
    f = tmp_path / "note.txt"
    f.write_text("my pin code 560001", encoding="utf-8")
    q = queue.Queue()
    w = FileScanWorker(eng.detect_all, eng.config, q, threading.Event())
    w._run_job(("files", ShareContext("dialog", "Upload", [str(f)]), [str(f)]))
    events = [q.get()[0] for _ in range(q.qsize())]
    assert "file_report" not in events and "scan_clean" in events   # no nagging for names/PINs you allowed


# ---- B, C, D: the card -------------------------------------------------------------------------
@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()


def _card(root, findings, text):
    from sensiq.overlay import Overlay
    from sensiq.sharewatch import FileResult
    ov = Overlay(root, on_replace=None, on_ignore=None, on_fix_all=None, on_style=None, get_style=lambda: "partial")
    ex = filescan.Extracted(text=text, kind="text")
    flagged = []
    ov.file_report(ShareContext("dialog", "Upload"), [FileResult("form.txt", findings, extracted=ex)],
                   on_apply=lambda c: None, on_not_private=lambda items: flagged.append(items),
                   on_flag_again=lambda items: None)
    root.update()
    return ov


def _labels(ov):
    import tkinter as tk

    def walk(x):
        yield x
        for c in x.winfo_children():
            yield from walk(c)
    return [c for c in walk(ov.report_win) if isinstance(c, tk.Label)]


def test_id_rows_have_no_not_private_link(tk_root):
    text = f"Aadhaar {AADHAAR} Rahul"
    ov = _card(tk_root, [Finding(8, 22, "AADHAAR", AADHAAR), Finding(23, 28, "PERSON", "Rahul")], text)
    assert [x.cget("text") for x in _labels(ov)].count("not private") == 1   # only the name row


def test_marked_id_shown_unticked_with_flag_again(tk_root):
    text = f"Aadhaar {AADHAAR}"
    ov = _card(tk_root, [Finding(8, 22, "AADHAAR", AADHAAR, ignored=True)], text)
    texts = [x.cget("text") for x in _labels(ov)]
    assert "☐" in texts and "marked not private" in texts and "flag again" in texts
    assert any(t.startswith("⚠ ") for t in texts)   # red line: nothing will be replaced
    flag = next(x for x in _labels(ov) if x.cget("text") == "flag again")
    flag.event_generate("<Button-1>", when="now")
    tk_root.update()
    texts = [x.cget("text") for x in _labels(ov)]
    assert "☑" in texts and "not private" not in texts   # ticked again; IDs can't be re-marked


def test_clicking_row_text_does_not_untick(tk_root):
    text = f"Aadhaar {AADHAAR}"
    ov = _card(tk_root, [Finding(8, 22, "AADHAAR", AADHAAR)], text)
    label = next(x for x in _labels(ov) if x.cget("text") == "Aadhaar number")
    label.event_generate("<Button-1>", when="now")
    tk_root.update()
    assert "☑" in [x.cget("text") for x in _labels(ov)]
    box = next(x for x in _labels(ov) if x.cget("text") == "☑")
    box.event_generate("<Button-1>", when="now")
    tk_root.update()
    warn = [x.cget("text") for x in _labels(ov) if x.cget("text").startswith("⚠ ")]
    assert warn and "Aadhaar number" in warn[0] or "Nothing is ticked" in warn[0]


def test_warning_names_unticked_kinds(tk_root):
    text = f"Aadhaar {AADHAAR} Rahul"
    ov = _card(tk_root, [Finding(8, 22, "AADHAAR", AADHAAR), Finding(23, 28, "PERSON", "Rahul")], text)
    assert not [x for x in _labels(ov) if x.cget("text").startswith("⚠ ") and x.winfo_ismapped()]
    boxes = [x for x in _labels(ov) if x.cget("text") == "☑"]
    boxes[1].event_generate("<Button-1>", when="now")   # untick the name
    tk_root.update()
    warn = [x.cget("text") for x in _labels(ov) if x.cget("text").startswith("⚠ ")]
    assert warn == ["⚠ Will NOT be replaced: Person's name"]


# ---- E: the not-private list keeps masked hints only -------------------------------------------
def test_ignore_list_hints_and_old_format(tmp_path):
    path = tmp_path / "ignored.json"
    ign = IgnoreList(["oldfingerprint"], path)
    ign.add("newfp", persist=True, hint="Aadhaar number: XXXX XXXX 2997")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"newfp": "Aadhaar number: XXXX XXXX 2997", "oldfingerprint": ""}
    again = IgnoreList(data, path)
    assert again.contains("oldfingerprint") and ("newfp", "Aadhaar number: XXXX XXXX 2997") in again.entries()
    again.remove("newfp")
    assert not again.contains("newfp")


def test_hint_never_holds_the_number():
    from sensiq.app import masked_hint
    hint = masked_hint(Finding(0, 14, "AADHAAR", AADHAAR))
    assert hint == "Aadhaar number: XXXX XXXX 2997" and "2554" not in hint


def test_not_private_window_lists_and_removes(tk_root):
    from sensiq.overlay import Overlay
    ov = Overlay(tk_root, on_replace=None, on_ignore=None, on_fix_all=None, on_style=None,
                 get_style=lambda: "partial")
    removed = []
    ov.not_private_list([("name", "Kerala", "Name or place: Kerala"),
                         ("fp", "abc", "Aadhaar number: XXXX XXXX 2997")], lambda k, key: removed.append((k, key)))
    tk_root.update()
    import tkinter as tk

    def walk(x):
        yield x
        for c in x.winfo_children():
            yield from walk(c)
    rm = [c for c in walk(ov.np_win) if isinstance(c, tk.Label) and c.cget("text") == "Remove"]
    assert len(rm) == 2
    rm[1].event_generate("<Button-1>", when="now")
    tk_root.update()
    assert removed == [("fp", "abc")]


# ---- F: the tiny number under the small photo ---------------------------------------------------
def _card_extracted():
    ex = filescan.Extracted(text=f"{AADHAAR}\n{filescan.MARK}\n{filescan.MARK}", kind="image")
    m = filescan.MARK
    s1 = len(AADHAAR) + 1
    s2 = s1 + len(m) + 1
    ex.words = [(0, 4, (240, 370, 320, 400)), (5, 9, (330, 370, 410, 400)), (10, 14, (420, 370, 510, 400)),
                (s1, s1 + len(m), (60, 140, 190, 280)),      # main photo
                (s2, s2 + len(m), (669, 118, 708, 164))]     # small ghost photo
    ex.visual = [("FACE", s1, s1 + len(m), ""), ("FACE", s2, s2 + len(m), "")]
    return ex


def test_strip_under_small_photo_is_covered():
    ex = _card_extracted()
    found = visual.small_photo_numbers(ex, [Finding(0, 14, "AADHAAR", AADHAAR)])
    assert [f.kind for f in found] == ["AADHAAR_COPY"]
    box = [b for s, e, b in ex.words if s == found[0].start][0]
    assert box[0] <= 654 and box[2] >= 726 and box[1] <= 194 and box[3] >= 201   # where the tiny number is


def test_no_strip_without_aadhaar_or_second_photo():
    assert visual.small_photo_numbers(_card_extracted(), [Finding(0, 5, "PERSON", "Rahul")]) == []
    ex = _card_extracted()
    ex.visual = ex.visual[:1]
    assert visual.small_photo_numbers(ex, [Finding(0, 14, "AADHAAR", AADHAAR)]) == []


# ---- G: the log says which kinds were left unticked (never values) -------------------------------
def test_log_names_unticked_kinds(tmp_path, caplog):
    eng = _engine()
    f = tmp_path / "id.txt"
    f.write_text(f"Aadhaar {AADHAAR}\nmy pin code 560001", encoding="utf-8")
    w = FileScanWorker(eng.detect_all, eng.config, queue.Queue(), threading.Event())
    res = w._scan_file(f)
    keep = [x for x in res.findings if x.kind != "AADHAAR"]
    with caplog.at_level(logging.INFO, logger="sensiq.sharewatch"):
        w._redact_job(ShareContext("browser", "x", [str(f)], reply=lambda *a, **k: None), [res], {0: keep})
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("safe copy:")][0]
    assert "not ticked {'AADHAAR': 1}" in line and AADHAAR not in line
