// SENSIQ - for sites that open files with window.showOpenFilePicker() instead of an
// upload button. The chosen files are checked the same way; the page then reads either
// the original or the safe copy you chose.
(() => {
  if (typeof window.showOpenFilePicker !== "function" || window.__sensiqPage) return;
  window.__sensiqPage = true;
  const realPicker = window.showOpenFilePicker.bind(window);
  let seq = 0;

  function ask(files) {
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
      window.postMessage({ __sensiqAsk: true, id, files }, "*");
      // If the extension doesn't acknowledge at once, never hold the page: give it the originals.
      setTimeout(() => { if (!acked) finish({ action: "original" }); }, 3000);
    });
  }

  window.showOpenFilePicker = async function (...args) {
    const handles = await realPicker(...args);
    const files = await Promise.all(handles.map((h) => h.getFile()));
    const answer = await ask(files);
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
})();
