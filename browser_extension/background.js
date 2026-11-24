// PII Guard - relays files between web pages and the PII Guard app on this computer.
// Pages talk to this worker through a port; this worker talks to the app through the
// browser's native-messaging channel (host "com.piiguard.host"). Nothing goes online.

const HOST = "com.piiguard.host";
let native = null;            // native-messaging port to the PII Guard app
const routes = new Map();     // request id -> { port, local id }

function release(id, reason) {
  const r = routes.get(id);
  if (!r) return;
  try { r.port.postMessage({ op: "result", id: r.local, action: "original", reason, files: [] }); } catch (e) {}
  try { r.port.postMessage({ op: "end", id: r.local }); } catch (e) {}
  routes.delete(id);
}

function nativePort() {
  if (native) return native;
  try {
    native = chrome.runtime.connectNative(HOST);
  } catch (e) {
    native = null;
    return null;
  }
  native.onMessage.addListener((msg) => {
    const r = routes.get(msg.id);
    if (!r) return;
    try { r.port.postMessage({ ...msg, id: r.local }); } catch (e) { /* page went away */ }
    if (msg.op === "end") routes.delete(msg.id);
  });
  native.onDisconnect.addListener(() => {
    native = null;
    // the app isn't running or isn't installed: let every waiting page go ahead normally
    for (const id of [...routes.keys()]) release(id, "unavailable");
  });
  native.postMessage({ op: "hello", agent: navigator.userAgent });
  return native;
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "piiguard") return;
  const tag = `${port.sender?.tab?.id ?? "x"}:${port.sender?.frameId ?? 0}:`;
  port.onMessage.addListener((msg) => {
    const id = tag + msg.id;
    if (msg.op === "check") routes.set(id, { port, local: msg.id });
    const n = nativePort();
    if (!n) { release(id, "unavailable"); return; }
    try { n.postMessage({ ...msg, id }); } catch (e) { release(id, "unavailable"); }
  });
  port.onDisconnect.addListener(() => {
    for (const [id, r] of routes) if (r.port === port) routes.delete(id);
  });
});

// Keep the app link open while the browser runs, so checks start instantly.
chrome.runtime.onStartup.addListener(() => nativePort());
chrome.runtime.onInstalled.addListener(() => nativePort());
