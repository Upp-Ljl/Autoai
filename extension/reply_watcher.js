(() => {
  if (globalThis.__SAT2_RELAY_REPLY_WATCHER__) return;
  globalThis.__SAT2_RELAY_REPLY_WATCHER__ = true;

  const STABLE_MS = 1600;
  const STOP_SELECTORS = [
    'button[data-testid="stop-button"]',
    'button[data-testid="composer-stop-button"]',
    'button[aria-label*="Stop generating"]',
    'button[aria-label*="停止生成"]',
    'button[aria-label*="停止"]'
  ];
  const visible = (element) => Boolean(element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length));
  const textOf = (element) => (element?.innerText || element?.textContent || "").trim();

  let initialized = false;
  let baseline = null;
  let sawBusy = false;
  let timer = null;
  let candidateKey = "";

  function isBusy() {
    return STOP_SELECTORS.some((selector) => [...document.querySelectorAll(selector)].some(visible));
  }

  function identity(element, index) {
    const carrier = element?.closest?.('[data-message-id]') || element?.querySelector?.('[data-message-id]') || element;
    return carrier?.getAttribute?.('data-message-id') || carrier?.id || `assistant-${index}`;
  }

  function latestAssistant() {
    const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
    if (!messages.length) return null;
    const index = messages.length - 1;
    const element = messages[index];
    const text = textOf(element);
    if (!text) return null;
    return {
      id: identity(element, index),
      text,
      count: messages.length
    };
  }

  function same(left, right) {
    return Boolean(left && right && left.id === right.id && left.text === right.text && left.count === right.count);
  }

  function clearCandidate() {
    candidateKey = "";
    if (timer) clearTimeout(timer);
    timer = null;
  }

  async function emitCompleted(snapshot) {
    try {
      await chrome.runtime.sendMessage({
        type: "SAT2_SESSION_REPLY_COMPLETE",
        messageId: snapshot.id,
        assistantText: snapshot.text.slice(0, 1200),
        assistantLength: snapshot.text.length,
        assistantCount: snapshot.count,
        pageTitle: document.title,
        observedUrl: location.href,
        completedAt: new Date().toISOString()
      });
    } catch {}
  }

  function inspect() {
    const current = latestAssistant();
    const busy = isBusy();

    if (!initialized) {
      initialized = true;
      baseline = current;
      sawBusy = busy;
      return;
    }

    if (busy) {
      sawBusy = true;
      clearCandidate();
      return;
    }

    if (!current || same(current, baseline)) {
      if (!current) clearCandidate();
      return;
    }

    // A changed/new assistant message after the baseline is eligible. sawBusy is
    // useful evidence but is not mandatory because ChatGPT DOM revisions may hide
    // the stop button from the watcher. Stability is checked again before emit.
    const key = `${current.id}:${current.count}:${current.text.length}:${current.text.slice(-96)}`;
    if (candidateKey === key && timer) return;
    clearCandidate();
    candidateKey = key;
    timer = setTimeout(() => {
      timer = null;
      const stable = latestAssistant();
      if (isBusy() || !stable || !same(stable, current)) {
        inspect();
        return;
      }
      baseline = stable;
      sawBusy = false;
      candidateKey = "";
      emitCompleted(stable);
    }, sawBusy ? 700 : STABLE_MS);
  }

  const observer = new MutationObserver(() => inspect());
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true});
  inspect();
  setInterval(inspect, 2500);
})();
