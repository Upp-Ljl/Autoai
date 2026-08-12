// DOM shadow detector content script for the 3A receiver.
// Reports the latest assistant message text hash + parsed decision.
(() => {
  if (globalThis.__SAT2_NR_DOM_SHADOW__) return;
  globalThis.__SAT2_NR_DOM_SHADOW__ = true;

  const textOf = (el) => (el?.innerText || el?.textContent || "").trim();

  function parseDecision(text) {
    if (!text) return null;
    const m = text.match(/\{[\s\S]*\}/);
    if (!m) return null;
    try {
      const v = JSON.parse(m[0]);
      if (!v || typeof v !== "object" || Array.isArray(v)) return null;
      const keys = Object.keys(v).sort();
      if (keys.some((k) => !["delivery_token", "decision", "summary"].includes(k))) return null;
      if (typeof v.decision !== "string" || typeof v.summary !== "string") return null;
      return {decision: v.decision, summary: v.summary, delivery_token: v.delivery_token};
    } catch {
      return null;
    }
  }

  async function sha256Hex(text) {
    const bytes = new TextEncoder().encode(String(text || ""));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "NR_DOM_FIND") return false;
    const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
    const last = messages[messages.length - 1];
    if (!last) {
      sendResponse({decision: null, found: false, text_hash: null});
      return false;
    }
    const text = textOf(last);
    sha256Hex(text).then((hash) => {
      sendResponse({decision: parseDecision(text), found: true, text_hash: hash});
    });
    return true;
  });
})();
