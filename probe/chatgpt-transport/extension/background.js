// SAT2 ChatGPT Web Transport Probe 鈥?Phase 2 semantic correlation collector.
//
// Read-only CDP Network collector. Captures transport METADATA plus, for
// Phase 2, in-memory structural parsing of HTTP/WebSocket payloads:
//   - never persists raw payloads, message text, cookies, authorization,
//     session/account tokens, or nonce context text
//   - persists only hashes (SHA-256), top-level keys, field paths,
//     non-sensitive enum fields (event_type/status/role), sizes
//   - never intercepts, modifies, replays requests, never calls
//     Network.getResponseBody or any body-capturing command

const PREFIX = "sat2-probe:";
const FLUSH_AFTER = 200;
const MAX_RECORDS = 60000;

const PROBE_NONCES = {
  "A": "TP2_A_7K3M",
  "B": "TP2_B_9Q2X",
  "GITHUB": "TP2_GITHUB_OK",
};

let records = [];
const attachedTabs = new Set();
let activeCase = null; // "A" | "B" | "GITHUB" | "concurrent"

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

function urlMeta(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return {path: url.pathname, query_keys: [...url.searchParams.keys()]};
  } catch {
    return {path: String(rawUrl).slice(0, 500), query_keys: []};
  }
}

function headerNames(headers) {
  try {
    return Object.keys(headers || {}).sort();
  } catch {
    return [];
  }
}

function fieldPaths(obj, depth = 4, limit = 120) {
  const paths = [];
  const walk = (node, prefix, d) => {
    if (paths.length >= limit) return;
    if (d > depth) {
      paths.push(prefix + ":*");
      return;
    }
    if (Array.isArray(node)) {
      if (node.length) walk(node[0], prefix + "[]", d + 1);
      else paths.push(prefix + "[]");
      return;
    }
    if (node && typeof node === "object") {
      for (const k of Object.keys(node)) {
        walk(node[k], prefix ? prefix + "." + k : k, d + 1);
      }
      return;
    }
    paths.push(prefix + ":" + typeof node);
  };
  walk(obj, "", 0);
  return paths.slice(0, limit);
}

function detectNonce(text) {
  for (const [label, nonce] of Object.entries(PROBE_NONCES)) {
    if (text.includes(nonce)) return label;
  }
  return null;
}

function conversationHashFromUrl(rawUrl) {
  try {
    const m = new URL(rawUrl).pathname.match(/\/c\/([a-f0-9-]{10,})/);
    return m ? hashOf(m[1]) : Promise.resolve(null);
  } catch {
    return Promise.resolve(null);
  }
}

// document URL per (tab, network requestId) 鈥?requestIds are per-target,
// so they must be keyed together with the tab id to avoid cross-tab clashes.
const docUrlByRequest = new Map();

function docKey(tabId, requestId) {
  return tabId + ":" + requestId;
}

async function semanticHttp(params) {
  const req = params.request || {};
  if (req.method !== "POST" || !req.postData) return null;
  const meta = urlMeta(req.url);
  if (!/\/backend-api\/(f\/)?conversation/.test(meta.path)) return null;
  let body = null;
  try {
    body = JSON.parse(req.postData);
  } catch {
    return null;
  }
  const mid = body?.message?.id;
  const messageIdHashes = Array.isArray(mid)
    ? await Promise.all(mid.map((x) => hashOf(x)))
    : [await hashOf(mid)];
  return {
    request_id: params.requestId,
    endpoint: meta.path,
    action: body?.action || null,
    conversation_id_hash: await hashOf(body?.conversation_id),
    parent_message_id_hash: await hashOf(body?.parent_message_id),
    message_id_hashes: messageIdHashes,
    top_level_keys: Object.keys(body || {}).sort(),
    field_paths: fieldPaths(body),
    body_size: new TextEncoder().encode(req.postData).length,
    body_sha256: await sha256Text(req.postData),
    contains_probe_nonce: detectNonce(req.postData) !== null,
    probe_nonce_label: detectNonce(req.postData),
  };
}

async function semanticWs(payloadText) {
  let parsed = null;
  try {
    parsed = JSON.parse(payloadText);
  } catch {
    return {
      parsed: false,
      contains_probe_nonce: detectNonce(payloadText) !== null,
      probe_nonce_label: detectNonce(payloadText),
    };
  }
  let convHash = null;
  let msgHash = null;
  let parentHash = null;
  let eventType = null;
  let status = null;
  let role = null;
  const pending = [];
  const scan = (node) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(scan);
      return;
    }
    if (node.conversation_id != null) convHash = convHash || hashOf(node.conversation_id);
    const m = node.message;
    if (m && typeof m === "object") {
      if (m.id != null) msgHash = msgHash || hashOf(m.id);
      if (m.parent_id != null) parentHash = parentHash || hashOf(m.parent_id);
      if (!role && m.author?.role) role = m.author.role;
      if (!status && m.status) status = m.status;
      if (!eventType && m.content_type) eventType = m.content_type;
    }
    if (!eventType && typeof node.type === "string") eventType = node.type;
    if (!status && node.status) status = node.status;
    for (const v of Object.values(node)) if (v && typeof v === "object") scan(v);
  };
  scan(parsed);
  const nonce = detectNonce(payloadText);
  return {
    parsed: true,
    conversation_id_hash: await convHash,
    message_id_hash: await msgHash,
    parent_id_hash: await parentHash,
    event_type: eventType,
    status,
    role,
    top_level_keys: Array.isArray(parsed)
      ? [...new Set(parsed.filter((p) => p && typeof p === "object").flatMap((p) => Object.keys(p)))].sort()
      : Object.keys(parsed).sort(),
    field_paths: fieldPaths(parsed),
    contains_probe_nonce: nonce !== null,
    probe_nonce_label: nonce,
  };
}

async function push(kind, data) {
  const resolved = {};
  for (const [k, v] of Object.entries(data)) {
    resolved[k] = v instanceof Promise ? await v : v;
  }
  records.push({ts: iso(), kind, case: activeCase, ...resolved});
  if (records.length >= FLUSH_AFTER) await flush();
  if (records.length > 2000) await flush();
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

// ---- CDP events --------------------------------------------------------

function onDebuggerEvent(source, method, params) {
  if (!attachedTabs.has(source.tabId)) return;
  switch (method) {
    case "Network.requestWillBeSent": {
      const req = params.request || {};
      const meta = urlMeta(req.url);
      const docHashPromise = conversationHashFromUrl(params.documentURL || "");
      void docHashPromise.then((h) => {
        if (h) docUrlByRequest.set(docKey(source.tabId, params.requestId), h);
        if (docUrlByRequest.size > 500) {
          const first = docUrlByRequest.keys().next().value;
          docUrlByRequest.delete(first);
        }
      });
      void push("request", {
        tab_id: source.tabId,
        method: req.method,
        url_path: meta.path,
        url_query_keys: meta.query_keys,
        resource_type: params.type || "other",
        header_names: headerNames(req.headers),
        payload_size: req.postData ? new TextEncoder().encode(req.postData).length : 0,
        payload_sha256: req.postData ? sha256Text(req.postData) : null,
        doc_conversation_id_hash: docHashPromise,
        request_id: params.requestId,
        semantic: semanticHttp(params),
      });
      break;
    }
    case "Network.responseReceived": {
      void push("response", {
        tab_id: source.tabId,
        request_id: params.requestId,
        status: (params.response || {}).status,
        mime: (params.response || {}).mimeType,
        resource_type: params.type || "other",
      });
      break;
    }
    case "Network.loadingFinished": {
      void push("finished", {
        tab_id: source.tabId,
        request_id: params.requestId,
        encoded_data_length: params.encodedDataLength,
      });
      break;
    }
    case "Network.loadingFailed": {
      void push("failed", {
        tab_id: source.tabId,
        request_id: params.requestId,
        error_text: params.errorText,
        canceled: Boolean(params.canceled),
      });
      break;
    }
    case "Network.webSocketCreated": {
      const meta = urlMeta(params.url);
      void push("ws_created", {
        tab_id: source.tabId,
        ws_request_id: params.requestId,
        ws_url_path: meta.path,
        ws_url_query_keys: meta.query_keys,
        doc_conversation_id_hash: docUrlByRequest.get(docKey(source.tabId, params.requestId)) || null,
      });
      break;
    }
    case "Network.webSocketFrameSent":
    case "Network.webSocketFrameReceived": {
      const frame = params.response || params.request || {};
      const payload = String(frame.payloadData || "");
      const sent = method === "Network.webSocketFrameSent";
      void push(sent ? "ws_frame_sent" : "ws_frame_received", {
        tab_id: source.tabId,
        ws_request_id: params.requestId,
        doc_conversation_id_hash: docUrlByRequest.get(docKey(source.tabId, params.requestId)) || null,
        opcode: frame.opcode,
        mask: frame.mask,
        frame_size: new TextEncoder().encode(payload).length,
        frame_sha256: payload ? sha256Text(payload) : null,
        semantic: semanticWs(payload),
      });
      break;
    }
    case "Network.webSocketClosed": {
      void push("ws_closed", {
        tab_id: source.tabId,
        ws_request_id: params.requestId,
        code: params.code,
      });
      break;
    }
    default:
      break;
  }
}

// ---- lifecycle ---------------------------------------------------------

async function attach(tabId) {
  try {
    await chrome.debugger.attach({tabId}, "1.3");
    await chrome.debugger.sendCommand({tabId}, "Network.enable");
    attachedTabs.add(tabId);
    return {ok: true};
  } catch (error) {
    return {ok: false, error: error.message || String(error)};
  }
}

async function detachOne(tabId) {
  try {
    await chrome.debugger.detach({tabId});
  } catch {}
  attachedTabs.delete(tabId);
  await flush();
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
chrome.debugger.onDetach.addListener((source) => {
  attachedTabs.delete(source.tabId);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message?.type) {
      case "PROBE_ATTACH":
        return attach(message.tabId);
      case "PROBE_ATTACH_ALL": {
        const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
        const results = [];
        for (const tab of tabs) results.push(await attach(tab.id));
        return {ok: true, count: results.filter((r) => r.ok).length, total: tabs.length};
      }
      case "PROBE_DETACH":
        if (message.tabId) await detachOne(message.tabId);
        else await detachAll();
        return {ok: true};
      case "PROBE_MARK":
        activeCase = message.case;
        records.push({ts: iso(), kind: "marker", case: activeCase, label: message.label});
        return {ok: true};
      case "PROBE_STATUS":
        return {
          attached: attachedTabs.size > 0,
          tabIds: [...attachedTabs],
          activeCase,
        };
      case "PROBE_COUNT":
        return {count: await recordCount()};
      case "PROBE_EXPORT": {
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
        const filename = `chatgpt-transport-probe-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
        const id = await chrome.downloads.download({url: dataUrl, filename, saveAs: true});
        return {ok: true, rows: rows.length, downloadId: id};
      }
      case "PROBE_CLEAR":
        await chrome.storage.local.remove(PREFIX + "pending");
        records = [];
        docUrlByRequest.clear();
        return {ok: true};
      default:
        return {ok: false, error: "unknown message"};
    }
  })().then(sendResponse, (error) => {
    sendResponse({ok: false, error: error.message || String(error)});
  });
  return true;
});

