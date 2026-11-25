// SENSIQ - holds files a page is about to receive (upload button, drag and drop, paste),
// has the SENSIQ app on this computer check them, then gives the page either the
// original or the safe copy you chose. If the app isn't running, nothing is held.
(() => {
  if (window.__sensiqContent) return;
  window.__sensiqContent = true;

  const CHUNK = 3 * 1024 * 1024;        // bytes per message to the app
  const WAIT_MS = 10 * 60 * 1000;       // longest a page waits for your choice
  let port = null;
  let seq = 0;
  const pending = new Map();            // id -> { resolve, files: [], meta: [] }

  function connect() {
    if (port) return port;
    port = chrome.runtime.connect({ name: "sensiq" });
    port.onMessage.addListener(onReply);
    port.onDisconnect.addListener(() => {
      port = null;
      for (const p of pending.values()) p.resolve({ action: "original" });
      pending.clear();
    });
    return port;
  }

  function onReply(msg) {
    const p = pending.get(msg.id);
    if (!p) return;
    if (msg.op === "result") {
      p.action = msg.action;
      p.meta = msg.files || [];
      p.parts = p.meta.map(() => []);
    } else if (msg.op === "filechunk") {
      p.parts[msg.file][msg.index] = msg.data;
    } else if (msg.op === "end") {
      pending.delete(msg.id);
      const files = (p.meta || []).map((m, i) => {
        const bytes = (p.parts[i] || []).map(b64ToBytes);
        return new File(bytes, m.name, { type: m.type || "", lastModified: Date.now() });
      });
      p.resolve({ action: p.action || "original", files });
    }
  }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function bytesToB64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }

  // Ask the app about these files. Resolves to { action: "original" | "safe" | "cancel", files }.
  async function check(files, source) {
    let p;
    try { p = connect(); } catch (e) { return { action: "original" }; }
    const id = String(++seq);
    const done = new Promise((resolve) => {
      pending.set(id, { resolve });
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); resolve({ action: "original" }); } }, WAIT_MS);
    });
    try {
      p.postMessage({ op: "check", id, source, site: location.hostname,
                      files: files.map((f) => ({ name: f.name, type: f.type, size: f.size })) });
      for (let i = 0; i < files.length; i++) {
        const buf = new Uint8Array(await files[i].arrayBuffer());
        const n = Math.max(1, Math.ceil(buf.length / CHUNK));
        for (let k = 0; k < n; k++) {
          p.postMessage({ op: "chunk", id, file: i, index: k, count: n,
                          data: bytesToB64(buf.subarray(k * CHUNK, (k + 1) * CHUNK)) });
        }
      }
      p.postMessage({ op: "checkend", id });
    } catch (e) {
      pending.delete(id);
      return { action: "original" };
    }
    return done;
  }

  const mark = (ev) => { ev.__sensiq = true; return ev; };
  const ours = (ev) => ev.__sensiq === true;
  const listOf = (fileList) => Array.from(fileList || []);

  function dataTransferOf(files) {
    const dt = new DataTransfer();
    for (const f of files) dt.items.add(f);
    return dt;
  }

  // ---- upload buttons: <input type="file"> --------------------------------------------
  const held = new WeakSet();
  async function onInputEvent(ev) {
    const el = ev.target;
    if (ours(ev) || !(el instanceof HTMLInputElement) || el.type !== "file") return;
    if (!el.files || el.files.length === 0) return;
    ev.stopImmediatePropagation();
    if (held.has(el)) return;          // "input" and "change" arrive together: handle once
    held.add(el);
    const original = listOf(el.files);
    let r;
    try { r = await check(original, "upload"); } finally { held.delete(el); }
    if (r.action === "cancel") {
      el.value = "";
      return;
    }
    if (r.action === "safe" && r.files && r.files.length) el.files = dataTransferOf(r.files).files;
    el.dispatchEvent(mark(new Event("input", { bubbles: true, composed: true })));
    el.dispatchEvent(mark(new Event("change", { bubbles: true })));
  }
  window.addEventListener("input", onInputEvent, true);
  window.addEventListener("change", onInputEvent, true);

  // ---- drag and drop -----------------------------------------------------------------
  window.addEventListener("drop", async (ev) => {
    if (ours(ev) || !ev.dataTransfer || ev.dataTransfer.files.length === 0) return;
    const target = ev.target;
    const original = listOf(ev.dataTransfer.files);
    const at = { clientX: ev.clientX, clientY: ev.clientY, screenX: ev.screenX, screenY: ev.screenY };
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const r = await check(original, "drop");
    if (r.action === "cancel") return;
    const files = r.action === "safe" && r.files && r.files.length ? r.files : original;
    target.dispatchEvent(mark(new DragEvent("drop", { bubbles: true, cancelable: true, composed: true, ...at,
                                                      dataTransfer: dataTransferOf(files) })));
  }, true);

  // ---- paste ----------------------------------------------------------------------------
  window.addEventListener("paste", async (ev) => {
    if (ours(ev) || !ev.clipboardData || ev.clipboardData.files.length === 0) return;
    const target = ev.target;
    const original = listOf(ev.clipboardData.files);
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const r = await check(original, "paste");
    if (r.action === "cancel") return;
    const files = r.action === "safe" && r.files && r.files.length ? r.files : original;
    target.dispatchEvent(mark(new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true,
                                                            clipboardData: dataTransferOf(files) })));
  }, true);

  // ---- sites using the File System Access picker (page.js asks us) --------------------
  window.addEventListener("message", async (ev) => {
    const m = ev.data;
    if (!m || m.__sensiqAsk !== true || !Array.isArray(m.files)) return;
    window.postMessage({ __sensiqAck: true, id: m.id }, "*");
    const r = await check(m.files, m.source === "upload" ? "upload" : "picker");
    window.postMessage({ __sensiqAnswer: true, id: m.id, action: r.action,
                         files: r.action === "safe" ? r.files : null }, "*");
  });
})();
