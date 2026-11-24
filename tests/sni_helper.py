"""Helper for tests/test_linux.py::test_tray_icon_registers_with_the_top_bar (Linux; run inside
its own dbus-run-session on a virtual display). Not a test file itself.

Plays the part of Ubuntu's top bar (a StatusNotifierWatcher, as the AppIndicators extension
provides), starts SENSIQ and prints "REGISTERED <item>" when SENSIQ's tray icon registers.
"""
import os
import subprocess
import sys

from gi.repository import Gio, GLib

XML = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
<property name="RegisteredStatusNotifierItems" type="as" access="read"/>
<property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
<property name="ProtocolVersion" type="i" access="read"/>
</interface></node>"""
items = []
loop = GLib.MainLoop()


def on_call(conn, sender, path, iface, method, params, invocation):
    if method == "RegisterStatusNotifierItem":
        items.append(sender + params.unpack()[0])
        print("REGISTERED", params.unpack()[0], flush=True)
        GLib.timeout_add(500, loop.quit)
    invocation.return_value(None)


def on_prop(conn, sender, path, iface, name):
    return {"RegisteredStatusNotifierItems": GLib.Variant("as", items),
            "IsStatusNotifierHostRegistered": GLib.Variant("b", True),
            "ProtocolVersion": GLib.Variant("i", 0)}[name]


def on_bus(conn, name):
    conn.register_object("/StatusNotifierWatcher", Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                         on_call, on_prop, None)


def on_name(conn, name):
    app = subprocess.Popen(sys.argv[1:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    GLib.timeout_add_seconds(60, loop.quit)
    loop.app = app


Gio.bus_own_name(Gio.BusType.SESSION, "org.kde.StatusNotifierWatcher", 0, on_bus, on_name, None)
loop.app = None
loop.run()
if loop.app is not None:
    loop.app.terminate()
print("ITEMS", len(items), flush=True)
