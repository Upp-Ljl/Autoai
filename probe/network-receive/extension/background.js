// SAT2 Network-First Receive — Phase 3A SHADOW receiver.
//
// Network-first receive path (shadow):
//   1. track POST /backend-api/f/conversation via CDP Network events
//   2. when the SSE response finishes normally, read the completed body in
//      memory via the read-only command Network.getResponseBody
//   3. parse the final assistant message + SAT2_RELAY_DECISION from the SSE
//   4. run a DOM shadow detector on the same page and compare both results
//
// Safety (per Phase 3A contract):
//   - completion is never decided by Network.loadingFinished alone; it requires
//     turn attribution + semantic final assistant state + normal finish + no
//     loadingFailed (WS conversation-update recorded as secondary confirmation)
//   - response bodies are parsed in memory only; nothing persists raw SSE,
//     cookies, authorization, or tokens — only hashes/enums/metadata
//   - SHADOW: this extension never submits decisions to the daemon
//   - read-only: getResponseBody on a *finished* request does not intercept,
//     modify, or replay anything

const PREFIX = "sat3a:";
const FLUSH_AFTER = 50;
const MAX_RECORDS = 20000;
const TURN_RE = /\/backend-api\/f\/conversation/;

const attachedTabs = new Set();
let records = [];
let testCase = null; // short | long | github | capsule | concurrent

function iso() {
  return new Date().toISOString();
}

async function sha256Text(text) {
  const bytes = new TextEncoder().encode(String(text || ""));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function hashOf(value) {
  if (value == null) return Promise.resolve(null);
  const s = String(value);
  return s ? sha256Text(s) : Promise.resolve(null);
}

// ---- in-flight turn correlation ----------------------------------------

// key: tabId:requestId -> {conversation_id, parent_message_id, message_id, user_message_text_hash, case}
const pendingTurns = new Map();
// key: tabId:requestId -> conversation hash (from document URL)
const turnConvByUrl = new Map();

function parsePostBody(tabId, requestId, postData) {
  try {
    const body = JSON.parse(postData);
    const mid = body?.message?.id;
    return {
      conversation_id: body?.conversation_id || null,
      parent_message_id: body?.parent_message_id || null,
      message_id: Array.isArray(mid) ? (mid[0] || null) : (mid || null),
      user_text_hash: body?.message?.content ? sha256Text(JSON.stringify(body.message.content)) : null,
    };
  } catch {
    return null;
  }
}

function convFromUrl(rawUrl) {
  try {
    const m = new URL(rawUrl).pathname.match(/\/c\/([a-f0-9-]{10,})/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

// ---- SSE parsing -------------------------------------------------------

function parseSse(body) {
  // ChatGPT SSE: lines of "data: {json}" where events carry message state.
  const events = [];
  for (const line of String(body || "").split("\n")) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload) continue;
    try {
      const obj = JSON.parse(payload);
      events.push(obj);
    } catch {
      // non-JSON data lines are ignored structurally
    }
  }
  return events;
}

function extractAssistant(events) {
  // walk events for the last assistant message content and its final state
  let finalText = "";
  let messageId = null;
  let terminalStatus = null;
  let lastEventType = null;
  for (const ev of events) {
    const type = ev?.type || ev?.message?.type || null;
    lastEventType = type;
    const m = ev?.message;
    if (m) {
      if (m.id) messageId = m.id;
      if (m.status) terminalStatus = m.status;
      if (m.author?.role === "assistant" && m.content) {
        if (Array.isArray(m.content)) {
          const texts = m.content
            .filter((c) => c && (c.type === "text" || c.text))
            .map((c) => c.text || c.content || "");
          if (texts.length) finalText = texts.join("");
        } else if (typeof m.content === "string") {
          finalText = m.content;
        }
      }
    }
    if (ev?.message_ended || ev?.end_turn || type === "message_ended" || type === "done") {
      terminalStatus = terminalStatus || "ended";
    }
  }
  return {finalText, messageId, terminalStatus, lastEventType};
}

function parseDecision(text) {
  // Same contract as the DOM detector: a JSON object with only
  // delivery_token / decision / summary keys.
  if (!text) return null;
  const jsonish = text.match(/\{[\s\S]*\}/);
  if (!jsonish) return null;
  try {
    const value = JSON.parse(jsonish[0]);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const keys = Object.keys(value).sort();
    if (keys.some((k) => !["delivery_token", "decision", "summary"].includes(k))) return null;
    if (typeof value.decision !== "string" || typeof value.summary !== "string") return null;
    return {
      delivery_token: value.delivery_token,
      decision: value.decision,
      summary_hash: sha256Text(value.summary),
    };
  } catch {
    return null;
  }
}

// ---- DOM shadow detector ----------------------------------------------

async function domDetect(tabId, conversationKey) {
  try {
    const result = await chrome.tabs.sendMessage(tabId, {type: "NR_DOM_FIND", conversationKey});
    return result;
  } catch {
    return null; // content script not injected yet
  }
}

// ---- record & flush ----------------------------------------------------

async function push(kind, data) {
  const resolved = {};
  for (const [k, v] of Object.entries(data)) {
    resolved[k] = v instanceof Promise ? await v : v;
  }
  records.push({ts: iso(), case: testCase, kind, ...resolved});
  if (records.length >= FLUSH_AFTER) await flush();
}

async function flush() {
  if (!records.length) return;
  const key = PREFIX + "pending";
  const stored = (await chrome.storage.local.get(key))[key] || [];
  const combined = stored.concat(records);
  const keep = combined.slice(-MAX_RECORDS);
  await chrome.storage.local.set({[key]: keep});
  records = [];
}

async function recordCount() {
  const key = PREFIX + "pending";
  return ((await chrome.storage.local.get(key))[key] || []).length;
}

// ---- CDP handling ------------------------------------------------------

function onDebuggerEvent(source, method, params) {
  if (!attachedTabs.has(source.tabId)) return;
  const key = `${source.tabId}:${params.requestId}`;

  switch (method) {
    case "Network.requestWillBeSent": {
      const req = params.request || {};
      if (req.method !== "POST" || !TURN_RE.test(new URL(req.url).pathname)) return;
      const parsed = parsePostBody(source.tabId, params.requestId, req.postData || "");
      if (!parsed) return;
      const convUrl = convFromUrl(params.documentURL || "");
      pendingTurns.set(key, {...parsed, convUrl, requestId: params.requestId, tabId: source.tabId});
      if (convUrl) turnConvByUrl.set(key, convUrl);
      void push("turn_start", {
        tab_id: source.tabId,
        request_id: params.requestId,
        conversation_id_hash: parsed.conversation_id ? hashOf(parsed.conversation_id) : null,
        conv_url_hash: convUrl ? hashOf(convUrl) : null,
        parent_message_id_hash: parsed.parent_message_id ? hashOf(parsed.parent_message_id) : null,
        message_id_hash: parsed.message_id ? hashOf(parsed.message_id) : null,
        user_text_hash: parsed.user_text_hash,
      });
      break;
    }
    case "Network.responseReceived": {
      const turn = pendingTurns.get(key);
      if (!turn) return;
      const resp = params.response || {};
      if (String(resp.mimeType || "").includes("text/event-stream")) {
        turn.sse = true;
        turn.status = resp.status;
      }
      break;
    }
    case "Network.loadingFinished": {
      const turn = pendingTurns.get(key);
      if (!turn) return;
      turn.finished = params.encodedDataLength;
      void handleFinished(source.tabId, params.requestId, turn);
      break;
    }
    case "Network.loadingFailed": {
      const turn = pendingTurns.get(key);
      if (!turn) return;
      void push("turn_failed", {
        tab_id: source.tabId,
        request_id: params.requestId,
        error_text: params.errorText,
      });
      pendingTurns.delete(key);
      break;
    }
    case "Network.webSocketFrameReceived": {
      const payload = String((params.response || {}).payloadData || "");
      if (!payload.includes("conversation-update")) return;
      void push("ws_secondary", {
        tab_id: source.tabId,
        ws_request_id: params.requestId,
        frame_size: new TextEncoder().encode(payload).length,
        frame_sha256: sha256Text(payload),
      });
      break;
    }
    default:
      break;
  }
}

async function handleFinished(tabId, requestId, turn) {
  // 1. read the completed SSE body in memory (read-only, after finish)
  let body = null;
  let bodyError = null;
  try {
    const result = await chrome.debugger.sendCommand(
      {tabId},
      "Network.getResponseBody",
      {requestId}
    );
    body = result?.body ?? null;
  } catch (error) {
    bodyError = error.message || String(error);
  }

  // 2. parse assistant final message + decision
  let assistant = null;
  let decision = null;
  let sseEvents = 0;
  if (body && turn.sse) {
    const events = parseSse(body);
    sseEvents = events.length;
    assistant = extractAssistant(events);
    if (assistant.finalText) {
      decision = await parseDecision(assistant.finalText);
    }
  }

  // 3. semantic completion gate (never loadingFinished alone)
  const hasSemanticFinal = Boolean(
    assistant && (assistant.terminalStatus || assistant.lastEventType)
  );
  const completed = Boolean(
    turn.conversation_id &&
    turn.sse &&
    turn.status === 200 &&
    hasSemanticFinal &&
    turn.finished != null
  );

  // 4. DOM shadow comparison
  const domResult = await domDetect(tabId, null);
  const domDecision = domResult?.decision
    ? {
        decision: domResult.decision.decision,
        summary_hash: sha256Text(domResult.decision.summary),
        delivery_token: domResult.decision.delivery_token,
      }
    : null;

  const agreement =
    domResult?.decision && decision
      ? {
          decision_match: domResult.decision.decision === decision.decision,
          summary_hash_match: domResult.decision.summary === decision.summary_hash
            ? undefined
            : await sha256Text(domResult.decision.summary) === decision.summary_hash,
          token_match: domResult.decision.delivery_token === decision.delivery_token,
        }
      : null;

  await push("turn_complete", {
    tab_id: tabId,
    request_id: requestId,
    conversation_id_hash: turn.conversation_id ? hashOf(turn.conversation_id) : null,
    conv_url_hash: turn.convUrl ? hashOf(turn.convUrl) : null,
    message_id_hash: assistant?.messageId ? hashOf(assistant.messageId) : null,
    terminal_status: assistant?.terminalStatus,
    last_event_type: assistant?.lastEventType,
    sse_events: sseEvents,
    sse_bytes: body ? new TextEncoder().encode(body).length : 0,
    sse_sha256: body ? sha256Text(body) : null,
    transport_completed: completed,
    body_error: bodyError,
    decision_transport: decision,
    decision_dom: domDecision,
    agreement,
    encoded_data_length: turn.finished,
  });
  pendingTurns.delete(`${tabId}:${requestId}`);
}

// ---- lifecycle ---------------------------------------------------------

async function attach(tabId) {
  try {
    await chrome.debugger.attach({tabId}, "1.3");
    await chrome.debugger.sendCommand({tabId}, "Network.enable");
    attachedTabs.add(tabId);
    try {
      await chrome.scripting.executeScript({target: {tabId}, files: ["content_shadow.js"]});
    } catch {}
    return {ok: true};
  } catch (error) {
    return {ok: false, error: error.message || String(error)};
  }
}

async function attachAll() {
  const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
  const results = [];
  for (const tab of tabs) results.push(await attach(tab.id));
  return {ok: true, count: results.filter((r) => r.ok).length, total: tabs.length};
}

async function detachAll() {
  for (const tabId of [...attachedTabs]) {
    try {
      await chrome.debugger.detach({tabId});
    } catch {}
  }
  attachedTabs.clear();
  await flush();
}

chrome.debugger.onEvent.addListener(onDebuggerEvent);
chrome.debugger.onDetach.addListener((source) => attachedTabs.delete(source.tabId));

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message?.type) {
      case "NR_ATTACH":
        return attach(message.tabId);
      case "NR_ATTACH_ALL":
        return attachAll();
      case "NR_DETACH":
        await detachAll();
        return {ok: true};
      case "NR_MARK":
        testCase = message.case;
        records.push({ts: iso(), kind: "marker", case: testCase, label: message.label});
        return {ok: true};
      case "NR_STATUS":
        return {attached: attachedTabs.size > 0, tabIds: [...attachedTabs], case: testCase};
      case "NR_COUNT":
        return {count: await recordCount()};
      case "NR_EXPORT": {
        await flush();
        const key = PREFIX + "pending";
        const stored = (await chrome.storage.local.get(key))[key] || [];
        const rows = [...stored].sort((a, b) => (a.ts < b.ts ? -1 : 1));
        const jsonl = rows.map((row) => JSON.stringify(row)).join("\n");
        const blob = new Blob([jsonl], {type: "application/x-ndjson"});
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let binary = "";
        const chunk = 0x8000;
        for (let i = 0; i < bytes.length; i += chunk) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        const dataUrl = "data:application/x-ndjson;base64," + btoa(binary);
        const filename = `sat3a-shadow-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
        const id = await chrome.downloads.download({url: dataUrl, filename, saveAs: true});
        return {ok: true, rows: rows.length, downloadId: id};
      }
      case "NR_CLEAR":
        await chrome.storage.local.remove(PREFIX + "pending");
        records = [];
        return {ok: true};
      default:
        return {ok: false, error: "unknown message"};
    }
  })().then(sendResponse, (error) => {
    sendResponse({ok: false, error: error.message || String(error)});
  });
  return true;
});
