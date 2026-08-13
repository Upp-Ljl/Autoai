export const DEFAULTS = {
  daemonUrl: "http://127.0.0.1:8765",
  daemonToken: "",
  autoEnabled: true,
  notifyOnSessionComplete: true,
  activateTargetTab: false,
  openMissingTabs: true,
  allowDuplicateBindings: false,
  connectorMode: "session_verified",
  bindings: {},
  installationId: "",
  deliveryHistory: {},
  decisionContexts: {},
  decisionHistory: {},
  replyNotificationHistory: {},
  lastCycleAt: "",
  lastCycleResult: "",
  lastDaemonHealth: null,
  lastAlertId: 0
};

export const ROLES = ["mentor", "S1", "S2", "S3", "S4"];

export async function loadSettings() {
  const value = await chrome.storage.local.get({settings: DEFAULTS});
  const settings = {...DEFAULTS, ...(value.settings || {})};
  settings.bindings = settings.bindings || {};
  settings.deliveryHistory = settings.deliveryHistory || {};
  settings.decisionContexts = settings.decisionContexts || {};
  settings.decisionHistory = settings.decisionHistory || {};
  settings.replyNotificationHistory = settings.replyNotificationHistory || {};
  if (!settings.installationId) {
    settings.installationId = crypto.randomUUID();
    await saveSettings(settings);
  }
  return settings;
}

export async function saveSettings(settings) {
  await chrome.storage.local.set({settings});
}

export async function daemonFetch(settings, path, options = {}) {
  const url = new URL(path, settings.daemonUrl).toString();
  const headers = {...(options.headers || {}), "X-SAT2-Relay-Token": settings.daemonToken};
  const request = {...options, headers};
  if (request.body && typeof request.body !== "string") {
    headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, {...request, signal: controller.signal});
    const text = await response.text();
    if (!response.ok) throw new Error(`DAEMON_HTTP_${response.status}: ${text.slice(0, 1000)}`);
    return text ? JSON.parse(text) : null;
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("DAEMON_TIMEOUT");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function parseConversationKey(raw) {
  const url = new URL(raw);
  if (url.origin !== "https://chatgpt.com") throw new Error("NOT_CHATGPT_URL");
  const parts = url.pathname.split("/").filter(Boolean);
  const c = parts.lastIndexOf("c");
  if (c >= 0 && parts[c + 1]) return `c:${parts[c + 1]}`;
  throw new Error("CHAT_CONVERSATION_ID_NOT_FOUND: bind a concrete /c/<conversation-id> page, not a project home page");
}

export function canonicalChatUrl(raw) {
  const url = new URL(raw);
  if (url.origin !== "https://chatgpt.com") throw new Error("Only chatgpt.com sessions are supported");
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

export function pruneHistory(history, maximum = 500) {
  const entries = Object.entries(history || {}).sort((a, b) => String(b[1]).localeCompare(String(a[1])));
  return Object.fromEntries(entries.slice(0, maximum));
}

export async function daemonDebugLog(settings, event, data = {}) {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    await fetch(new URL("/api/v2/extension/debug-log", settings.daemonUrl).toString(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-SAT2-Relay-Token": settings.daemonToken,
      },
      body: JSON.stringify({event, at: new Date().toISOString(), ...data}),
      signal: controller.signal,
    });
    clearTimeout(timeout);
  } catch {
    clearTimeout(timeout);
  }
}
