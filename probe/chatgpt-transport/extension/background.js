// SAT2 ChatGPT Web Transport Probe — read-only CDP Network collector.
//
// Collects transport METADATA only:
//   - request method / URL path / query parameter names / resource type
//   - header NAMES (never values; cookie/authorization values are never read)
//   - payload schema / size / sha256 (never the payload content)
//   - WebSocket frame metadata (opcode / size / hash), never frame content
//   - response status / mime / encoded data length / termination info
//
// This extension never intercepts, modifies, or replays requests, and never
// calls Network.getResponseBody or any body-capturing command.

const PREFIX = "sat2-probe:";
const FLUSH_AFTER = 200;
const MAX_RECORDS = 40000;

let records = [];
let attachedTabId = null;
let activeCase = null; // 1 = plain text message, 2 = @GitHub message

function iso() {
  return new Date().toISOString();
}

async function sha256Text(text) {
  const bytes = new TextEncoder().encode(String(text || ""));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function urlMeta(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return {
      path: url.pathname,
      query_keys: [...url.searchParams.keys()],
    };
  } catch {
    return {path: String(rawUrl).slice(0, 500), query_keys: []};
  }
}

function headerNames(headers) {
  // Names only; values (including cookies/authorization) are never captured.
  try {
    return Object.keys(headers || {}).sort();
  } catch {
    return [];
  }
}

function payloadSchema(text) {
  // Structural schema only: JSON key skeleton, or primitive type for non-JSON.
  const sample = String(text || "");
  if (!sample) return {type: "empty"};
  if (sample.startsWith("{") || sample.startsWith("[")) {
    try {
      return jsonSkeleton(JSON.parse(sample), 0);
    } catch {
      return {type: "json_unparsed"};
    }
  }
  return {type: "text", length: sample.length};
}

function jsonSkeleton(value, depth) {
  if (depth > 3) return {type: "depth_limit"};
  if (value === null) return {type: "null"};
  if (Array.isArray(value)) {
    return {
      type: "array",
      length: value.length,
      items: value.length ? jsonSkeleton(value[0], depth + 1) : null,
    };
  }
  if (typeof value === "object") {
    const keys = {};
    for (const [k, v] of Object.entries(value)) {
      keys[k] = jsonSkeleton(v, depth + 1);
    }
    return {type: "object", keys};
  }
  return {type: typeof value};
}

function eventTypeFrom(resourceType) {
  return resourceType || "other";
}

async function push(kind, data) {
  records.push({ts: iso(), kind, case: activeCase, ...data});
  if (records.length >= FLUSH_AFTER) await flush();
  // keep the in-memory window bounded; full history lives in storage
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
  const stored = (await chrome.storage.local.get(key))[key] || [];
  return stored.length;
}

// ---- CDP event handling ----------------------------------------------

function onDebuggerEvent(source, method, params) {
  if (source.tabId !== attachedTabId) return;
  switch (method) {
    case "Network.requestWillBeSent": {
      const req = params.request || {};
      const meta = urlMeta(req.url);
      void push("request", {
        method: req.method,
        url_path: meta.path,
        url_query_keys: meta.query_keys,
        resource_type: eventTypeFrom(params.type),
        header_names: headerNames(req.headers),
        payload_size: req.postData ? new TextEncoder().encode(req.postData).length : 0,
        payload_sha256: req.postData ? sha256Text(req.postData) : null,
        payload_schema: payloadSchema(req.postData),
        request_id: params.requestId,
      });
      break;
    }
    case "Network.responseReceived": {
      void push("response", {
        request_id: params.requestId,
        status: (params.response || {}).status,
        mime: (params.response || {}).mimeType,
        resource_type: eventTypeFrom(params.type),
      });
      break;
    }
    case "Network.loadingFinished": {
      void push("finished", {
        request_id: params.requestId,
        encoded_data_length: params.encodedDataLength,
        blocks_count: params.blockCount,
      });
      break;
    }
    case "Network.loadingFailed": {
      void push("failed", {
        request_id: params.requestId,
        error_text: params.errorText,
        canceled: Boolean(params.canceled),
      });
      break;
    }
    case "Network.webSocketCreated": {
      const meta = urlMeta(params.url);
      void push("ws_created", {
        ws_url_path: meta.path,
        ws_url_query_keys: meta.query_keys,
      });
      break;
    }
    case "Network.webSocketFrameSent":
    case "Network.webSocketFrameReceived": {
      const frame = params.response || params.request || {};
      const payload = String(frame.payloadData || "");
      const sent = method === "Network.webSocketFrameSent";
      void push(sent ? "ws_frame_sent" : "ws_frame_received", {
        opcode: frame.opcode,
        mask: frame.mask,
        payload_size: new TextEncoder().encode(payload).length,
        payload_sha256: payload ? sha256Text(payload) : null,
        payload_schema: payloadSchema(payload),
        ws_request_id: params.requestId,
      });
      break;
    }
    case "Network.webSocketClosed": {
      void push("ws_closed", {
        ws_request_id: params.requestId,
        code: params.code,
        reason_size: String(params.reason || "").length,
      });
      break;
    }
    default:
      break;
  }
}

// ---- lifecycle --------------------------------------------------------

async function attach(tabId) {
  try {
    await chrome.debugger.attach({tabId}, "1.3");
    await chrome.debugger.sendCommand({tabId}, "Network.enable");
    attachedTabId = tabId;
    activeCase = null;
    return {ok: true};
  } catch (error) {
    return {ok: false, error: error.message || String(error)};
  }
}

async function detach() {
  if (attachedTabId !== null) {
    try {
      await chrome.debugger.detach({tabId: attachedTabId});
    } catch {}
    attachedTabId = null;
  }
  await flush();
}

chrome.debugger.onEvent.addListener(onDebuggerEvent);
chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId === attachedTabId) attachedTabId = null;
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message?.type) {
      case "PROBE_ATTACH":
        return attach(message.tabId);
      case "PROBE_DETACH":
        await detach();
        return {ok: true};
      case "PROBE_MARK":
        activeCase = message.case;
        records.push({ts: iso(), kind: "marker", case: activeCase, label: message.label});
        return {ok: true};
      case "PROBE_COUNT":
        return {count: await recordCount()};
      case "PROBE_EXPORT": {
        await flush();
        const key = PREFIX + "pending";
        const stored = (await chrome.storage.local.get(key))[key] || [];
        const rows = [...stored].sort((a, b) => (a.ts < b.ts ? -1 : 1));
        const jsonl = rows.map((row) => JSON.stringify(row)).join("\n");
        const blob = new Blob([jsonl], {type: "application/x-ndjson"});
        const url = URL.createObjectURL(blob);
        const filename = `chatgpt-transport-probe-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`;
        const id = await chrome.downloads.download({url, filename, saveAs: true});
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        return {ok: true, rows: rows.length, downloadId: id};
      }
      case "PROBE_CLEAR": {
        await chrome.storage.local.remove(PREFIX + "pending");
        records = [];
        return {ok: true};
      }
      default:
        return {ok: false, error: "unknown message"};
    }
  })().then(sendResponse);
  return true;
});
