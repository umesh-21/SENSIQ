// SENSIQ - runs in the page itself (MAIN world) for uploads the content script can't see:
//  * upload boxes that aren't part of the page (Gmail, WhatsApp Web, React: created in code,
//    opened with input.click(), never added to the document) and upload boxes inside web
//    components (shadow DOM): their "file chosen" events never reach the window;
//  * sites that open files with window.showOpenFilePicker() instead of an upload box.
// The chosen files are handed to the content script (content.js) to be checked the same way;
// the page then gets either the original or the safe copy you chose.
(() => {
  if (window.__sensiqPage) return;
  window.__sensiqPage = true;
  let seq = 0;

  function ask(files, source) {
    const id = "p" + (++seq);
    return new Promise((resolve) => {
      let acked = false;
      const finish = (m) => { window.removeEventListener("message", onAnswer); resolve(m); };
      const onAnswer = (ev) => {
        const m = ev.data;
        if (!m || m.id !== id) return;
        if (m.__sensiqAck === true) { acked = true; return; }
        if (m.__sensiqAnswer === true) finish(m);
      };
      window.addEventListener("message", onAnswer);
      window.postMessage({ __sensiqAsk: true, id, files, source }, "*");
      // If the extension doesn't acknowledge at once, never hold the page: give it the originals.
      setTimeout(() => { if (!acked) finish({ action: "original" }); }, 3000);
    });
  }

  // ---- upload boxes outside the document or inside web components -------------------------
  const ours = new WeakSet();     // events SENSIQ dispatched itself
  const hooked = new WeakSet();
  const busy = new WeakSet();
  const inDocument = (el) => el.getRootNode() === document;  // content.js handles these

  function hookInput(el) {
    if (!(el instanceof HTMLInputElement) || el.type !== "file" || hooked.has(el)) return;
    hooked.add(el);
    const onChosen = async (ev) => {
      if (ours.has(ev) || inDocument(el) || !el.files || el.files.length === 0) return;
      ev.stopImmediatePropagation();
      if (busy.has(el)) return;    // "input" and "change" arrive together: handle once
      busy.add(el);
      let answer;
      try { answer = await ask(Array.from(el.files), "upload"); } finally { busy.delete(el); }
      if (answer.action === "cancel") { el.value = ""; return; }
      if (answer.action === "safe" && answer.files && answer.files.length) {
        const dt = new DataTransfer();
        for (const f of answer.files) dt.items.add(f);
        el.files = dt.files;
      }
      for (const type of ["input", "change"]) {
        const e = new Event(type, { bubbles: true, composed: type === "input" });
        ours.add(e);
        el.dispatchEvent(e);
      }
    };
    // capture listeners on the box itself run before the page's own listeners on it
    el.addEventListener("input", onChosen, true);
    el.addEventListener("change", onChosen, true);
  }

  // the page opens an upload box from code (input.click() / input.showPicker())
  const proto = HTMLInputElement.prototype;
  for (const name of ["click", "showPicker"]) {
    const real = proto[name];
    if (typeof real !== "function") continue;
    proto[name] = function (...args) {
      if (this.type === "file") hookInput(this);
      return real.apply(this, args);
    };
  }
  // the user clicks an upload box (or its label) inside a web component
  window.addEventListener("click", (ev) => {
    const t = ev.composedPath()[0];
    if (t instanceof HTMLInputElement) hookInput(t);
    else if (t instanceof Element) {
      const label = t.closest("label");
      if (label && label.control) hookInput(label.control);
    }
  }, true);

  // ---- File System Access picker (not in every browser: Brave turns it off) ----------------
  if (typeof window.showOpenFilePicker === "function") {
    const realPicker = window.showOpenFilePicker.bind(window);
    window.showOpenFilePicker = async function (...args) {
      const handles = await realPicker(...args);
      const files = await Promise.all(handles.map((h) => h.getFile()));
      const answer = await ask(files, "picker");
      if (answer.action === "cancel") throw new DOMException("The user aborted a request.", "AbortError");
      if (answer.action !== "safe" || !answer.files) return handles;
      return handles.map((h, i) => {
        const safe = answer.files[i] || null;
        return new Proxy(h, {
          get(target, prop) {
            if (prop === "getFile" && safe) return async () => safe;
            const v = Reflect.get(target, prop, target);
            return typeof v === "function" ? v.bind(target) : v;
          },
        });
      });
    };
  }
})();
