"""Linux version: platform layer, desktop integration, browser link, updates. Every value is fake.
Tests that need a display or xclip run under xvfb-run and skip otherwise."""
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private XDG home so nothing touches the real user folders."""
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                     ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "run")):
        d = tmp_path / sub
        d.mkdir(mode=0o700)
        monkeypatch.setenv(var, str(d))
    return tmp_path


def test_xdg_folders(home):
    from sensiq import paths
    assert paths.data_dir() == home / "config" / "sensiq"
    assert paths.log_dir() == home / "state" / "sensiq"
    assert paths.default_safe_copies_dir() == home / "data" / "sensiq" / "Safe copies"
    assert paths.work_dir() == home / "data" / "sensiq" / "work"
    rt = paths.runtime_dir()
    assert rt == home / "run" / "sensiq" and stat.S_IMODE(rt.stat().st_mode) == 0o700


def test_platform_layer_picks_linux():
    from sensiq import platform as plat
    assert plat.IS_LINUX and not plat.IS_WIN and plat.NAME == "linux"
    assert plat.STARTUP_LABEL == "Start when I sign in"
    assert plat.process_name(os.getpid()).startswith("python")
    left, top, right, bottom = plat.work_area()
    assert right > left and bottom > top


def test_old_winutil_import_still_works():
    from sensiq import winutil
    assert winutil.IS_WIN is False and winutil.save_clipboard() == []


def test_start_when_i_sign_in(home):
    from sensiq import platform as plat
    assert not plat.is_startup_enabled()
    plat.set_startup(True)
    f = home / "config" / "autostart" / "sensiq.desktop"
    text = f.read_text()
    assert plat.is_startup_enabled() and "Exec=" in text and "-m sensiq" in text
    plat.set_startup(False)
    assert not f.exists() and not plat.is_startup_enabled()


def test_single_instance_lock(home):
    code = ("import sys; from sensiq import platform as p; "
            "sys.exit(0 if p.acquire_single_instance('t') else 3)")
    from sensiq import platform as plat
    assert plat.acquire_single_instance("t")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent,
                       env=dict(os.environ))
    assert r.returncode == 3       # a second SENSIQ sees the first one


def test_second_start_hands_files_to_running_app(home, tmp_path):
    from sensiq import control
    got = []
    stop = threading.Event()
    srv = control.ControlServer(got.append, stop)
    srv.start()
    sock = home / "run" / "sensiq" / "control.sock"
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600
    f = tmp_path / "card.png"
    f.write_bytes(b"x")
    assert control.send_check([str(f)])
    assert got == [[str(f)]]
    stop.set()


def test_send_check_without_running_app(home):
    from sensiq import control
    assert control.send_check(["/nonexistent"]) is False


def test_link_key_is_private(home):
    from sensiq.nativehost import link_key
    key = link_key(create=True)
    assert len(key) == 32
    assert stat.S_IMODE((home / "config" / "sensiq" / "link.key").stat().st_mode) == 0o600


def test_native_host_manifests(home):
    from sensiq import browserlink
    (home / "config" / "google-chrome").mkdir()
    (home / "config" / "BraveSoftware" / "Brave-Browser").mkdir(parents=True)
    assert browserlink.register_host()
    chrome = home / "config" / "google-chrome" / "NativeMessagingHosts" / "com.sensiq.host.json"
    brave = home / "config" / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts" / "com.sensiq.host.json"
    m = json.loads(chrome.read_text())
    assert brave.exists()
    import shutil as _sh
    if not any(_sh.which(p) for p in ("chromium", "chromium-browser")):
        assert not (home / "config" / "chromium").exists()   # only browsers that are installed or used
    assert m["name"] == "com.sensiq.host" and m["type"] == "stdio"
    assert m["allowed_origins"] == [f"chrome-extension://{browserlink.EXTENSION_ID}/"]
    launcher = Path(m["path"])
    assert launcher.is_absolute() and os.access(launcher, os.X_OK)
    browserlink.unregister_host()
    assert not chrome.exists() and not brave.exists()


def _frame(msg):
    import struct
    data = json.dumps(msg).encode()
    return struct.pack("<I", len(data)) + data


def _unframe(data):
    import struct
    out = []
    while data:
        (n,) = struct.unpack("<I", data[:4])
        out.append(json.loads(data[4:4 + n]))
        data = data[4 + n:]
    return out


def test_native_host_answers_go_ahead_when_sensiq_is_off(home):
    """The browser's host process with no SENSIQ running: uploads aren't blocked."""
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "sensiq", "chrome-extension://abc/"], cwd=root,
                       input=_frame({"op": "check", "id": "r1", "files": []}) + _frame({"op": "checkend", "id": "r1"}),
                       stdout=subprocess.PIPE, timeout=60, env=dict(os.environ, PYTHONPATH=str(root)))
    msgs = _unframe(r.stdout)
    assert msgs[0]["op"] == "result" and msgs[0]["action"] == "original" and msgs[-1]["op"] == "end"


def test_native_host_relays_to_the_app(home, tmp_path):
    """Browser -> host (stdio) -> Unix socket -> BrowserLink -> scanner, and the answer back."""
    import base64
    from sensiq import browserlink

    class FakeScanner:
        def submit_browser(self, source, paths, reply):
            self.seen = (source, [Path(p).read_bytes() for p in paths])
            reply("original")

    scanner = FakeScanner()
    stop = threading.Event()
    link = browserlink.BrowserLink(scanner, stop)
    link.start()
    sock = home / "run" / "sensiq" / "link.sock"
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.05)
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600
    root = Path(__file__).resolve().parent.parent
    data = b"Aadhaar 4918 3205 7615"
    msgs = (_frame({"op": "check", "id": "r1", "files": [{"name": "a.txt"}], "source": "upload", "site": "example.com"})
            + _frame({"op": "chunk", "id": "r1", "file": 0, "index": 0, "data": base64.b64encode(data).decode()})
            + _frame({"op": "checkend", "id": "r1", "source": "upload", "site": "example.com"}))
    p = subprocess.Popen([sys.executable, "-m", "sensiq", "chrome-extension://abc/"], cwd=root,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=dict(os.environ, PYTHONPATH=str(root)))
    p.stdin.write(msgs)
    p.stdin.flush()
    out = b""
    deadline = time.time() + 30
    while time.time() < deadline and b'"end"' not in out:
        chunk = p.stdout.read1(65536)
        if not chunk:
            break
        out += chunk
    p.stdin.close()
    p.wait(timeout=10)
    stop.set()
    assert scanner.seen == ("File added to a web page (example.com)", [data])
    answers = _unframe(out)
    assert answers[0]["action"] == "original" and answers[-1]["op"] == "end"


def test_files_script(home):
    from sensiq import linuxdesktop
    assert linuxdesktop.install_files_script(force=True)
    script = home / "data" / "nautilus" / "scripts" / "Check with SENSIQ"
    text = script.read_text()
    assert os.access(script, os.X_OK) and "--check" in text and "NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" in text
    script.write_text("#!/bin/sh\necho mine\n")       # the user's own script with this name is left alone
    assert not linuxdesktop.install_files_script(force=True)
    assert script.read_text() == "#!/bin/sh\necho mine\n"


def test_files_script_passes_selection(home, tmp_path):
    """Paths with spaces from Files reach `sensiq --check` one per argument."""
    from sensiq import linuxdesktop
    out = tmp_path / "args.txt"
    fake = tmp_path / "fake-sensiq"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "{out}"\n')
    fake.chmod(0o755)
    script = tmp_path / "s.sh"
    script.write_text(linuxdesktop.files_script_text(str(fake)))
    script.chmod(0o755)
    env = dict(os.environ, NAUTILUS_SCRIPT_SELECTED_FILE_PATHS="/home/x/My card.png\n/home/x/b*.pdf\n")
    subprocess.run([str(script), "My card.png", "b*.pdf"], env=env, check=True)
    assert out.read_text().splitlines() == ["--check", "/home/x/My card.png", "/home/x/b*.pdf"]


def test_clipboard_uri_list():
    from sensiq.platform.linux import uris_to_paths
    text = "copy\nfile:///home/x/My%20card.png\r\n# comment\nhttps://example.com/a.png\nfile:///tmp/b.pdf\n"
    assert uris_to_paths(text) == ["/home/x/My card.png", "/tmp/b.pdf"]


def test_recently_opened_files(tmp_path):
    from sensiq.sharewatch import recently_opened
    xbel = tmp_path / "recently-used.xbel"
    xbel.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<xbel version="1.0" xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">
  <bookmark href="file:///home/x/old.pdf" added="2020-01-01T10:00:00Z" modified="2020-01-01T10:00:00Z" visited="2020-01-01T10:00:00Z"/>
  <bookmark href="file:///home/x/My%20statement.pdf" added="2020-01-01T10:00:00Z" modified="2030-05-01T10:00:00.123456Z" visited="2030-05-01T10:00:00Z"/>
  <bookmark href="sftp://server/new.pdf" added="2030-05-01T10:00:00Z" modified="2030-05-01T10:00:00Z" visited="2030-05-01T10:00:00Z"/>
</xbel>""")
    since = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, -1))
    assert recently_opened(xbel, since) == [Path("/home/x/My statement.pdf")]


def test_updater_picks_the_deb(monkeypatch):
    from sensiq import updater
    assert updater.DEB_NAME.match("sensiq_1.7.0_amd64.deb")
    assert not updater.DEB_NAME.match("sensiq_1.7.0_amd64.deb.sha256")
    assert not updater.can_install()        # from source: Update now opens the release page instead


def test_safe_copies_keep_exact_case(home, tmp_path):
    """Linux file names are case-sensitive: the safe copy must come from the real file."""
    from sensiq.sharewatch import _pkey
    assert _pkey("/tmp/Card.PNG") != _pkey("/tmp/card.png")


# ---- clipboard on a real (virtual) X display -------------------------------------------
def _xclip_ok():
    return bool(os.environ.get("DISPLAY")) and shutil.which("xclip") is not None


needs_x = pytest.mark.skipif(not _xclip_ok(), reason="needs an X display and xclip (run under xvfb-run)")


@needs_x
def test_clipboard_image_round_trip(monkeypatch):
    from PIL import Image
    from sensiq import platform as plat
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    img = Image.new("RGB", (40, 30), (200, 10, 10))
    assert plat.set_clipboard_image(img)
    time.sleep(0.3)
    assert "image/png" in plat.clipboard_targets()
    kind, got = plat.read_clipboard()
    assert kind == "image" and got.size == (40, 30) and got.convert("RGB").getpixel((5, 5)) == (200, 10, 10)


@needs_x
def test_clipboard_files_round_trip(tmp_path, monkeypatch):
    from sensiq import platform as plat
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    f = tmp_path / "My card.png"
    f.write_bytes(b"x")
    assert plat.set_clipboard_files([f])
    time.sleep(0.3)
    assert plat.read_clipboard() == ("files", [str(f)])


@needs_x
def test_clipboard_watcher_sees_a_copied_image(monkeypatch):
    """Copy a picture -> the watcher hands it to the scanner (once)."""
    from PIL import Image
    from sensiq import platform as plat
    from sensiq.config import Config
    from sensiq.sharewatch import ClipboardWatcher
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    class FakeScanner:
        def __init__(self):
            self.got = []

        def clipboard_is_ours(self):
            return False

        def submit_image(self, source, image):
            self.got.append(("image", image.size))

        def submit_files(self, source, paths):
            self.got.append(("files", paths))

    plat.set_clipboard_image(Image.new("RGB", (10, 10), "white"))   # there before the watcher starts
    time.sleep(0.3)
    sc, stop = FakeScanner(), threading.Event()
    w = ClipboardWatcher(sc, Config.defaults(), stop)
    w.LINUX_TICK = 0.1
    w.start()
    time.sleep(0.6)
    plat.set_clipboard_image(Image.new("RGB", (64, 48), (0, 90, 200)))
    deadline = time.time() + 6
    while time.time() < deadline and not sc.got:
        time.sleep(0.1)
    time.sleep(0.8)
    stop.set()
    assert sc.got == [("image", (64, 48))]


# ---- updates (.deb through pkexec), never without the user's click -------------------------
def _fake_pkexec(tmp_path, monkeypatch, code):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "pkexec.log"
    (bin_dir / "pkexec").write_text(f'#!/bin/sh\necho "$@" > "{log}"\nexit {code}\n')
    (bin_dir / "pkexec").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def test_deb_update_installs_with_apt(tmp_path, monkeypatch, home):
    from sensiq import updater
    log = _fake_pkexec(tmp_path, monkeypatch, 0)
    deb = tmp_path / "sensiq_9.0.0_amd64.deb"
    deb.write_bytes(b"x")
    assert updater.install_linux(deb) == (True, "")
    assert log.read_text().split() == ["apt-get", "install", "-y", "--allow-downgrades", str(deb)]


def test_deb_update_password_refused(tmp_path, monkeypatch, home):
    from sensiq import updater
    _fake_pkexec(tmp_path, monkeypatch, 126)
    ok, why = updater.install_linux(tmp_path / "sensiq_9.0.0_amd64.deb")
    assert not ok and "password" in why


# ---- the whole app on a virtual display ------------------------------------------------------
def _app_ready():
    from sensiq import ocr
    return bool(os.environ.get("DISPLAY")) and ocr.tesseract_path() is not None


@pytest.mark.skipif(not _app_ready(), reason="needs a display (xvfb-run) and Tesseract")
def test_app_end_to_end(home, tmp_path):
    """Start SENSIQ, hand it a file the way Files' "Check with SENSIQ" does, accept the card,
    and get a safe copy without the Aadhaar number; a second start doesn't open a second app."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("arial.ttf", 30)
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 60), "Aadhaar 4918 3205 7615", fill="black", font=font)
    d.text((60, 140), "Mobile 9876543210", fill="black", font=font)
    card = tmp_path / "card.png"
    img.save(card)
    root = Path(__file__).resolve().parent.parent
    bin_dir = tmp_path / "bin"     # no real file manager / notifications during the test
    bin_dir.mkdir()
    for tool in ("xdg-open", "gdbus", "notify-send", "zenity"):
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / tool).chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(root), SENSIQ_TEST_AUTOREPLACE="1",
               PATH=f"{bin_dir}:{os.environ['PATH']}", PYSTRAY_BACKEND="xorg")
    app = subprocess.Popen([sys.executable, "-m", "sensiq", "--no-tray"], cwd=root, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = home / "state" / "sensiq" / "sensiq.log"
    try:
        deadline = time.time() + 90
        while time.time() < deadline and not (log.exists() and "ControlServer started" in log.read_text()):
            time.sleep(0.5)
        r = subprocess.run([sys.executable, "-m", "sensiq", "--check", str(card)], cwd=root, env=env, timeout=60)
        assert r.returncode == 0
        safe_dir = home / "data" / "sensiq" / "Safe copies"
        deadline = time.time() + 120
        safe = []
        while time.time() < deadline and not safe:
            safe = list(safe_dir.rglob("card.png")) if safe_dir.exists() else []
            time.sleep(0.5)
        assert safe, log.read_text()[-3000:]
        time.sleep(0.5)
        from sensiq import filescan
        left = filescan.extract_file(safe[0]).text
        assert "4918" not in left and "7615" not in left.replace("XXXX XXXX 7615", "")
        assert app.poll() is None          # still running
    finally:
        app.terminate()
        app.wait(timeout=10)


def test_link_key_created_once_by_racing_threads(home):
    """The browser link and the control channel ask for the key at the same moment at start-up."""
    from sensiq.nativehost import link_key
    keys, errors = [], []

    def ask():
        try:
            keys.append(link_key(create=True))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(keys)) == 1 and len(keys[0]) == 32


# ---- typing checks (AT-SPI), test version ---------------------------------------------------
def _atspi_ready():
    if not os.environ.get("DISPLAY") or not shutil.which("dbus-run-session"):
        return False
    try:
        r = subprocess.run(["/usr/bin/python3", "-c", "import gi; gi.require_version('Atspi', '2.0'); "
                            "gi.require_version('Gtk', '3.0')"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _atspi_ready(), reason="needs a display, dbus-run-session, AT-SPI and GTK (python3-gi)")
def test_typing_checks_with_atspi(tmp_path):
    """A GTK text box with an Aadhaar number: SENSIQ reads it, replaces the number inside the app,
    and never reads the password box."""
    helper = Path(__file__).resolve().parent / "atspi_helper.py"
    # a private home: switching accessibility on must not touch the real user's GNOME settings
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_STATE_HOME=str(tmp_path / "state"),
               GSETTINGS_BACKEND="memory")
    r = subprocess.run(["dbus-run-session", "--", "/usr/bin/python3", str(helper), "drive"],
                       capture_output=True, text=True, timeout=120, env=env)
    line = [l for l in r.stdout.splitlines() if l.startswith("REPORT ")]
    assert line, r.stdout[-2000:] + r.stderr[-2000:]
    rep = json.loads(line[0][7:])
    assert rep["enabled"]
    assert "My Aadhaar is 4918 3205 7615 ok" in rep["fields"]
    assert rep["replace"] == [1, 0, True]
    assert rep["entry_after"] == "My Aadhaar is XXXX XXXX 7615 ok"
    assert "4918 3205 7615" not in [f for f in rep["fields"] if f]           # the password box's text
    assert rep["fields"][-1] is None                                          # focus on it: no field


def test_installed_app_starts_at_sign_in_and_can_be_switched_off(home, tmp_path, monkeypatch):
    """The .deb's /etc/xdg/autostart entry starts SENSIQ for everyone; the tray switch writes
    a per-user override."""
    from sensiq.platform import linux
    system = tmp_path / "etc-autostart" / "sensiq.desktop"
    system.parent.mkdir()
    system.write_text("[Desktop Entry]\nExec=/usr/bin/sensiq\n")
    monkeypatch.setattr(linux, "SYSTEM_AUTOSTART", system)
    user = home / "config" / "autostart" / "sensiq.desktop"
    assert linux.is_startup_enabled()
    linux.set_startup(False)
    assert "Hidden=true" in user.read_text() and not linux.is_startup_enabled()
    linux.set_startup(True)
    assert not user.exists() and linux.is_startup_enabled()


def test_opening_sensiq_again_reaches_the_running_app(home):
    from sensiq import control
    shown = []
    stop = threading.Event()
    srv = control.ControlServer(lambda p: None, stop, on_show=lambda: shown.append(1))
    srv.start()
    sock = home / "run" / "sensiq" / "control.sock"
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.05)
    assert control.send_show() and shown == [1]
    stop.set()


def test_tray_host_check_never_complains_by_mistake(monkeypatch):
    from sensiq import linuxdesktop
    monkeypatch.setattr(linuxdesktop.shutil, "which", lambda name: None)
    assert linuxdesktop.tray_host_present() is True
    assert linuxdesktop.enable_appindicator_extension() is False


def _appindicator_ready():
    if not os.environ.get("DISPLAY") or not shutil.which("dbus-run-session"):
        return False
    r = subprocess.run([sys.executable, "-c", "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')"],
                       capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _appindicator_ready(), reason="needs a display, dbus-run-session and AyatanaAppIndicator3")
def test_tray_icon_registers_with_the_top_bar(home):
    """The shield must reach Ubuntu's top bar (StatusNotifierWatcher). With pystray's
    run_detached() the icon was created but never registered, so it stayed invisible."""
    root = Path(__file__).resolve().parent.parent
    helper = Path(__file__).resolve().parent / "sni_helper.py"
    env = dict(os.environ, PYTHONPATH=str(root), GSETTINGS_BACKEND="memory")
    r = subprocess.run(["dbus-run-session", "--", sys.executable, str(helper), sys.executable, "-m", "sensiq"],
                       cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert "REGISTERED" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]


def test_cards_go_on_the_monitor_not_the_whole_x_screen(tmp_path, monkeypatch):
    """A 2944 px X screen with one 1920 px monitor: the bottom-right corner must be the monitor's,
    or every card lands in the invisible part."""
    from sensiq.platform import linux
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xrandr").write_text("#!/bin/sh\necho 'Monitors: 1'\necho ' 0: +*DP-5 1920/598x1080/336+0+0  DP-5'\n")
    (bin_dir / "xrandr").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(linux, "_mon_cache", (0.0, []))
    monkeypatch.setattr(linux, "_work_cache", (0.0, None))
    monkeypatch.setattr(linux, "_screen", lambda: (2944, 1080))
    monkeypatch.setattr(linux, "_net_workarea", lambda: (0, 32, 2944, 1080))
    assert linux.monitors() == [(0, 0, 1920, 1080, True)]
    assert linux.work_area() == (0, 32, 1920, 1080)
    assert linux.virtual_screen() == (0, 0, 1920, 1080)


def test_debug_log_never_holds_document_words(home):
    """--debug must not let libraries (Presidio logs 'Context list is: <words>') write file contents."""
    import logging
    from sensiq import app
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        app.setup_logging(True)
        logging.getLogger("presidio-analyzer").debug("Context list is: ramesh kumar 4918")
        for h in root.handlers:
            h.flush()
        text = (home / "state" / "sensiq" / "sensiq.log").read_text()
        assert "ramesh" not in text
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(logging.WARNING)


def test_typing_in_chrome_based_browsers(home, tmp_path, monkeypatch):
    """Brave/Chrome share web pages' text boxes only with --force-renderer-accessibility: SENSIQ
    adds it to a per-user copy of their launchers (and leaves a launcher the user made alone)."""
    from sensiq import linuxdesktop
    system = tmp_path / "share"
    (system / "applications").mkdir(parents=True)
    (system / "applications" / "brave-browser.desktop").write_text(
        "[Desktop Entry]\nName=Brave Web Browser\nExec=/usr/bin/brave-browser-stable %U\n"
        "[Desktop Action new-window]\nExec=/usr/bin/brave-browser-stable\n")
    (system / "applications" / "google-chrome.desktop").write_text(
        "[Desktop Entry]\nName=Google Chrome\nExec=\"/opt/google/chrome/google chrome\" --incognito %U\n")
    monkeypatch.setenv("XDG_DATA_DIRS", str(system))
    mine = home / "data" / "applications" / "google-chrome.desktop"
    mine.parent.mkdir(parents=True)
    mine.write_text("[Desktop Entry]\nExec=my-own-chrome\n")
    assert linuxdesktop.enable_browser_typing() == ["Brave Web Browser"]
    brave = (home / "data" / "applications" / "brave-browser.desktop").read_text()
    assert "Exec=/usr/bin/brave-browser-stable --force-renderer-accessibility %U" in brave
    assert "Exec=/usr/bin/brave-browser-stable --force-renderer-accessibility\n" in brave
    assert mine.read_text() == "[Desktop Entry]\nExec=my-own-chrome\n"          # untouched
    assert linuxdesktop._with_flag('Exec="/opt/a b/chrome" %U') == 'Exec="/opt/a b/chrome" --force-renderer-accessibility %U'
    assert linuxdesktop.enable_browser_typing() == ["Brave Web Browser"]          # repeat: no double flag
    assert (home / "data" / "applications" / "brave-browser.desktop").read_text().count("--force-renderer") == 2
    linuxdesktop.disable_browser_typing()
    assert not (home / "data" / "applications" / "brave-browser.desktop").exists() and mine.exists()


class _FakeText:
    def __init__(self, acc):
        self.acc = acc

    def get_character_count(self):
        return len(self.acc.raw)

    def get_text(self, a, b):
        return self.acc.raw[a:b]


class _FakeLink:
    def __init__(self, child):
        self.child = child

    def get_object(self, i):
        return self.child


class _FakeHyper:
    def __init__(self, acc):
        self.acc = acc

    def get_link_index(self, offset):
        return [i for i, ch in enumerate(self.acc.raw) if ch == "￼"].index(offset)

    def get_link(self, k):
        return _FakeLink(self.acc.children[k])


class _FakeAcc:
    def __init__(self, role, raw, children=()):
        self.role, self.raw, self.children = role, raw, list(children)

    def get_text_iface(self):
        return _FakeText(self)

    def get_hypertext_iface(self):
        return _FakeHyper(self) if self.children else None

    def get_role_name(self):
        return self.role


def test_rich_text_boxes_in_browsers_are_read_through():
    """Chrome-based browsers describe claude.ai's / Gmail's box as placeholders with the words in
    child paragraphs: SENSIQ must read the words, and know which paragraph holds each value."""
    from sensiq.capture_linux import flatten_text, locate_piece
    p1 = _FakeAcc("paragraph", "hi im umesh kashyap")
    link = _FakeAcc("link", "4918 3205 7615")
    p2 = _FakeAcc("paragraph", "aadhaar ￼ ok", [link])
    box = _FakeAcc("entry", "￼￼", [p1, p2])
    text, pieces = flatten_text(box, 1000)
    assert text == "hi im umesh kashyap\naadhaar 4918 3205 7615 ok"
    owner, off = locate_piece(pieces, text.index("umesh"), 5)
    assert owner is p1 and off == p1.raw.index("umesh")
    owner, off = locate_piece(pieces, text.index("4918"), 14)
    assert owner is link and off == 0
    assert locate_piece(pieces, text.index("kashyap"), 15) is None          # spans two paragraphs
    plain = _FakeAcc("text", "just text")
    assert flatten_text(plain, 1000) == ("just text", [(0, plain, 0, 9)])


def test_browser_installed_but_never_opened_is_registered(home, tmp_path, monkeypatch):
    """Chrome installed but never started has no settings folder yet: register it anyway."""
    from sensiq import browserlink
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "google-chrome").write_text("#!/bin/sh\n")
    (bin_dir / "google-chrome").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    assert browserlink.register_host()
    assert (home / "config" / "google-chrome" / "NativeMessagingHosts" / "com.sensiq.host.json").exists()


def test_deb_update_without_a_password_window(tmp_path, monkeypatch, home):
    """No password window (e.g. not started from the desktop): say so and give the command."""
    from sensiq import updater
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pkexec").write_text("#!/bin/sh\necho 'Error creating textual authentication agent: no tty'\nexit 127\n")
    (bin_dir / "pkexec").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    ok, why = updater.install_linux(tmp_path / "sensiq_9.0.0_amd64.deb")
    assert not ok and "password window" in why and "sudo apt install" in why


def test_manual_install_is_noticed(monkeypatch):
    """`sudo apt install ./sensiq_X.deb` while SENSIQ runs: it restarts into the new version
    instead of still offering the update."""
    from sensiq import updater
    monkeypatch.setattr(updater, "can_install", lambda: True)
    monkeypatch.setattr(updater, "installed_version", lambda: "99.0.0")
    assert updater.updated_on_disk() == "99.0.0"
    monkeypatch.setattr(updater, "installed_version", lambda: updater.__version__)
    assert updater.updated_on_disk() == ""
    monkeypatch.setattr(updater, "can_install", lambda: False)        # run from source: never
    monkeypatch.setattr(updater, "installed_version", lambda: "99.0.0")
    assert updater.updated_on_disk() == ""
