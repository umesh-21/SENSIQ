"""Helper for tests/test_linux.py::test_typing_checks_with_atspi (Linux; run inside its own
dbus-run-session on a virtual display). Not a test file itself.

  python atspi_helper.py gtk OUT    a GTK window: a text box with a (fake) Aadhaar number, then
                                    a password box with the same number gets the focus
  python atspi_helper.py drive      turns AT-SPI on, starts the window, runs SENSIQ's watcher,
                                    replaces the number, prints a JSON report
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

FAKE = "4918 3205 7615"


def gtk_window(out: str) -> None:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    w = Gtk.Window(title="Form")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.Entry()
    entry.set_text(f"My Aadhaar is {FAKE} ok")
    secret = Gtk.Entry()
    secret.set_visibility(False)
    secret.set_text(FAKE)
    for x in (entry, secret):
        box.pack_start(x, False, False, 4)
    w.add(box)
    w.set_default_size(500, 100)
    w.show_all()

    def dump():
        Path(out).write_text(entry.get_text())
        return True

    def focus(widget):
        w.get_window().focus(0)   # no window manager on the virtual display
        widget.grab_focus()
        return False
    GLib.timeout_add(150, dump)
    GLib.timeout_add(1500, focus, entry)
    GLib.timeout_add(7000, focus, secret)
    GLib.timeout_add(20000, Gtk.main_quit)
    Gtk.main()


def drive() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sensiq.capture_linux import FocusWatcher, accessibility_enabled, enable_accessibility
    from sensiq.config import Config
    report = {"enabled": enable_accessibility() and accessibility_enabled(), "fields": [], "replace": None}
    events, stop = queue.Queue(), threading.Event()
    watcher = FocusWatcher(events, Config.defaults(), stop)
    watcher.start()
    time.sleep(1.0)
    out = Path(tempfile.mkdtemp()) / "entry.txt"
    gtk = subprocess.Popen([sys.executable, __file__, "gtk", str(out)])
    deadline = time.time() + 15
    replaced = False
    while time.time() < deadline:
        try:
            ev = events.get(timeout=0.3)
        except queue.Empty:
            continue
        if ev[0] == "field":
            report["fields"].append(ev[1].text)
            if FAKE in ev[1].text and not replaced:
                i = ev[1].text.index(FAKE)
                watcher.commands.put(("replace", [(i, i + len(FAKE), FAKE, "XXXX XXXX 7615")]))
                replaced = True
        elif ev[0] == "replace_done":
            report["replace"] = list(ev[1:])
        elif ev[0] == "nofield":
            report["fields"].append(None)
    time.sleep(0.5)
    report["entry_after"] = out.read_text() if out.exists() else ""
    stop.set()
    gtk.terminate()
    print("REPORT " + json.dumps(report))


if __name__ == "__main__":
    if sys.argv[1] == "gtk":
        gtk_window(sys.argv[2])
    else:
        drive()
