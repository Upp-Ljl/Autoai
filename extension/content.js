(() => {
  if (globalThis.__SAT2_RELAY_CONTENT_V22__) return;
  globalThis.__SAT2_RELAY_CONTENT_V22__ = true;

  const VERSION = "2.2.0";
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const sent = new Map();
  const visible = (element) => Boolean(element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length));
  const textOf = (element) => (element?.innerText || element?.textContent || "").trim();

  const SELECTORS = {
    composer: [
      '#prompt-textarea', '[data-testid="prompt-textarea"]', 'textarea[placeholder*="Message"]',
      'textarea[placeholder*="发送"]', 'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][data-lexical-editor="true"]', 'div[contenteditable="true"][role="textbox"]'
    ],
    send: [
      'button[data-testid="send-button"]', 'button[data-testid="composer-submit-button"]',
      'button[aria-label="Send prompt"]', 'button[aria-label="Send message"]',
      'button[aria-label*="Send"]', 'button[aria-label*="发送"]'
    ],
    stop: [
      'button[data-testid="stop-button"]', 'button[data-testid="composer-stop-button"]',
      'button[aria-label="Stop generating"]', 'button[aria-label="停止生成"]',
      'button[aria-label="Stop"]', 'button[aria-label="停止"]'
    ]
  };

  function firstVisible(selectors, root = document) {
    for (const selector of selectors) {
      const match = [...root.querySelectorAll(selector)].find(visible);
      if (match) return match;
    }
    return null;
  }

  function findComposer() {
    const direct = firstVisible(SELECTORS.composer);
    if (direct) return direct;
    for (const form of [...document.querySelectorAll("form")].filter(visible).reverse()) {
      const editor = firstVisible(SELECTORS.composer, form);
      if (editor) return editor;
    }
    return null;
  }

  function findSendButton(editor) {
    return firstVisible(SELECTORS.send, editor?.closest("form") || document);
  }

  function busy() { return Boolean(firstVisible(SELECTORS.stop)); }
  function loginRequired() {
    return /\/auth\/(login|signin)/.test(location.pathname) || Boolean(firstVisible(['a[href*="/auth/login"]', 'button[data-testid="login-button"]']));
  }
  function confirmationVisible() {
    return [...document.querySelectorAll('[role="dialog"]')].some((dialog) => visible(dialog) && /confirm|allow|approve|permission|确认|允许|批准|授权/i.test(textOf(dialog)));
  }
  function selectorDiagnostics() {
    return Object.fromEntries(Object.entries(SELECTORS).map(([name, selectors]) => [name, selectors.map((selector) => ({selector, count: document.querySelectorAll(selector).length, visible: [...document.querySelectorAll(selector)].filter(visible).length}))]));
  }
  function health() {
    const editor = findComposer();
    const sendButton = editor ? findSendButton(editor) : null;
    return {
      ok: true, version: VERSION, url: location.href, title: document.title,
      composerReady: Boolean(editor), busy: busy(), loginRequired: loginRequired(),
      confirmationVisible: confirmationVisible(),
      sendReady: Boolean(sendButton && !sendButton.disabled && sendButton.getAttribute("aria-disabled") !== "true"),
      assistantMessages: document.querySelectorAll('[data-message-author-role="assistant"]').length,
      selectors: selectorDiagnostics()
    };
  }

  function dispatchInput(editor, value) {
    editor.dispatchEvent(new InputEvent("beforeinput", {bubbles: true, cancelable: true, inputType: "insertText", data: value}));
    editor.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: value}));
    editor.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function setEditorText(editor, value) {
    editor.focus();
    if (editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement) {
      const prototype = editor instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (!setter) throw new Error("EDITOR_WRITE_FAILED");
      setter.call(editor, value); dispatchInput(editor, value); return;
    }
    if (!editor.isContentEditable) throw new Error("EDITOR_UNSUPPORTED");
    const selection = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(editor); selection.removeAllRanges(); selection.addRange(range);
    let inserted = false; try { inserted = document.execCommand("insertText", false, value); } catch {}
    if (!inserted) { editor.replaceChildren(document.createTextNode(value)); dispatchInput(editor, value); }
  }
  function appendEditorText(editor, value) {
    if (editor instanceof HTMLTextAreaElement || editor instanceof HTMLInputElement) { setEditorText(editor, `${editor.value}${value}`); return; }
    editor.focus(); const selection = window.getSelection(); const range = document.createRange();
    range.selectNodeContents(editor); range.collapse(false); selection.removeAllRanges(); selection.addRange(range);
    let inserted = false; try { inserted = document.execCommand("insertText", false, value); } catch {}
    if (!inserted) { editor.append(document.createTextNode(value)); dispatchInput(editor, value); }
  }

  async function attachApp(editor, appName) {
    setEditorText(editor, `@${appName}`);
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const candidate = [...document.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-collection-item], li, button')]
        .filter(visible).find((element) => textOf(element).toLowerCase() === appName.toLowerCase() && element.closest('[role="listbox"], [role="menu"], [data-radix-popper-content-wrapper], [data-floating-ui-portal]'));
      if (candidate) {
        candidate.click(); await sleep(350); const root = editor.closest("form") || document;
        return [...root.querySelectorAll("button,span,div")].some((element) => visible(element) && textOf(element).toLowerCase() === appName.toLowerCase());
      }
      await sleep(100);
    }
    return false;
  }
  async function fill(editor, text, requiredApps, connectorMode) {
    const normalized = String(text || ""); const apps = Array.isArray(requiredApps) ? requiredApps : [];
    if (apps.length === 0 || connectorMode === "session_verified") { setEditorText(editor, normalized); return {connectorAttached: false, connectorMode}; }
    const app = apps[0]; const withoutMention = normalized.replace(new RegExp(`^\\s*@${app}\\b\\s*`, "i"), "");
    const connectorAttached = await attachApp(editor, app);
    if (!connectorAttached) {
      if (connectorMode === "strict_attach") throw new Error("APP_ATTACHMENT_NOT_CONFIRMED");
      setEditorText(editor, normalized); return {connectorAttached: false, connectorMode};
    }
    appendEditorText(editor, `\n\n${withoutMention}`); return {connectorAttached: true, connectorMode};
  }
  async function waitSendReady(editor, timeout = 20000) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      if (confirmationVisible()) throw new Error("CONFIRMATION_VISIBLE");
      if (busy()) throw new Error("SESSION_BUSY");
      const button = findSendButton(editor);
      if (button && !button.disabled && button.getAttribute("aria-disabled") !== "true") return button;
      await sleep(150);
    }
    throw new Error("SEND_BUTTON_NOT_READY");
  }
  function transcriptHas(marker) {
    if (!marker) return false;
    const selectors = [
      '[data-message-author-role="user"]',
      '[data-testid="user-message"]',
      '[data-message-author-role][data-message-id]'
    ];
    for (const selector of selectors) {
      const match = [...document.querySelectorAll(selector)].find((el) => textOf(el).includes(marker));
      if (match) return true;
    }
    // fallback: any element whose data-message-id container text contains the marker
    for (const el of document.querySelectorAll('[data-message-id]')) {
      if (textOf(el).includes(marker)) return true;
    }
    return false;
  }

  function debugReport(event, extra = {}) {
    try {
      chrome.runtime.sendMessage({type: "SAT2_DEBUG_LOG", event, at: new Date().toISOString(), url: location.href, ...selectorDiagnostics(), ...extra});
    } catch {}
  }
  async function verifySubmission(marker, timeout = 25000) {
    const started = Date.now();
    while (Date.now() - started < timeout) { if (transcriptHas(marker)) return true; await sleep(250); }
    return false;
  }
  async function sendMessage({text, relayId, requiredApps, connectorMode, strictApps}) {
    try {
      if (!location.href.startsWith("https://chatgpt.com/") && !globalThis.__SAT2_RELAY_TEST__) throw new Error("NOT_CHATGPT");
      if (loginRequired()) throw new Error("LOGIN_REQUIRED");
      if (confirmationVisible()) throw new Error("CONFIRMATION_VISIBLE");
      if (busy()) throw new Error("SESSION_BUSY");
      const marker = `Relay delivery: ${relayId}`;
      if (relayId && (sent.has(relayId) || transcriptHas(marker))) { sent.set(relayId, Date.now()); return {ok: true, duplicate: true, marker, health: health()}; }
      const editor = findComposer(); if (!editor) throw new Error("COMPOSER_NOT_FOUND");
      const effectiveConnectorMode = connectorMode || (strictApps ? "strict_attach" : "session_verified");
      // The marker must be part of the sent message so verifySubmission can
      // confirm it in the transcript.
      const sendText = relayId ? `${marker}\n\n${text}` : text;
      const filled = await fill(editor, sendText, requiredApps, effectiveConnectorMode); const button = await waitSendReady(editor); button.click();
      if (!(await verifySubmission(marker))) throw new Error("SUBMISSION_MARKER_NOT_CONFIRMED");
      if (relayId) sent.set(relayId, Date.now());
      return {ok: true, connectorAttached: filled.connectorAttached, connectorMode: filled.connectorMode, marker, health: health()};
    } catch (error) {
      debugReport("content_send_failed", {code: error.message || String(error), relayId: relayId || null});
      throw error;
    }
  }

  function balancedJsonObjects(text) {
    const values = [];
    for (let start = 0; start < text.length; start += 1) {
      if (text[start] !== "{") continue;
      let depth = 0; let inString = false; let escaped = false;
      for (let index = start; index < text.length; index += 1) {
        const char = text[index];
        if (inString) {
          if (escaped) escaped = false;
          else if (char === "\\") escaped = true;
          else if (char === '"') inString = false;
          continue;
        }
        if (char === '"') inString = true;
        else if (char === "{") depth += 1;
        else if (char === "}") {
          depth -= 1;
          if (depth === 0) { values.push(text.slice(start, index + 1)); start = index; break; }
        }
      }
    }
    return values;
  }

  function messageIdentity(element, index) {
    const carrier = element.closest('[data-message-id]') || element.querySelector('[data-message-id]') || element;
    return carrier.getAttribute?.('data-message-id') || carrier.id || `assistant-${index}`;
  }

  function parseDecisionFromElement(element, deliveryToken, force) {
    const text = textOf(element); const html = element.innerHTML || "";
    const marked = /SAT2_RELAY_DECISION/i.test(text) || /SAT2_RELAY_DECISION/i.test(html);
    if (!force && !marked) return null;
    const candidates = [];
    for (const code of element.querySelectorAll("pre code, code")) candidates.push(textOf(code));
    candidates.push(...balancedJsonObjects(text));
    for (const raw of candidates) {
      try {
        const value = JSON.parse(raw.trim());
        if (!value || typeof value !== "object" || Array.isArray(value)) continue;
        const keys = Object.keys(value).sort();
        if (keys.some((key) => !["delivery_token", "decision", "summary"].includes(key))) continue;
        if (value.delivery_token !== deliveryToken) continue;
        if (typeof value.decision !== "string" || typeof value.summary !== "string" || !value.summary.trim()) continue;
        return {decision: value.decision, summary: value.summary.trim(), deliveryToken: value.delivery_token, text};
      } catch {}
    }
    return null;
  }

  function findDecision({deliveryToken, force = false}) {
    if (!deliveryToken) throw new Error("DELIVERY_TOKEN_REQUIRED");
    if (busy() && !force) return {ok: true, decision: null, busy: true};
    const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const parsed = parseDecisionFromElement(messages[index], deliveryToken, force);
      if (!parsed) continue;
      return {
        ok: true,
        decision: {delivery_token: parsed.deliveryToken, decision: parsed.decision, summary: parsed.summary},
        assistantMessageId: messageIdentity(messages[index], index),
        assistantMessageText: parsed.text,
        observedUrl: location.href
      };
    }
    return {ok: true, decision: null};
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "SAT2_HEALTH") { sendResponse(health()); return false; }
    if (message?.type === "SAT2_FIND_DECISION") {
      try { sendResponse(findDecision({deliveryToken: message.deliveryToken, force: Boolean(message.force)})); }
      catch (error) { sendResponse({ok: false, code: error.message || String(error), error: error.message || String(error)}); }
      return false;
    }
    if (message?.type !== "SAT2_SEND") return false;
    sendMessage(message).then(sendResponse).catch((error) => sendResponse({ok: false, code: error.message || String(error), error: error.message || String(error), health: health()}));
    return true;
  });
})();
