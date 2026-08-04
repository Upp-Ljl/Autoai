import {canonicalChatUrl, daemonFetch, loadSettings, parseConversationKey, pruneHistory, saveSettings} from "./lib.js";

const ALARM = "sat2-relay-cycle"; /*
const NOTIFICATION_ICON = chrome.runtime.getURL("icon128.png");base64,iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAAD+UlEQVR4nO2dQU7dQBBEhygnYMU6Qiy5/zlYI65CNmnJcmx/M9M9XT1Vb5kg/rjrTX3bH8zT88vbdxO0/MpegMhFApAjAciRAORIAHIkADkSgBwJQI4EIEcCkCMByJEA5EgAcn5nL+Dr8yN7Cen8eX1Pe+2njI+DFfo5s2WYJoBC/zkzZJhyDqDw+5gxt9AGUPB+RLVBmAB3w888AUIhc1YhAjw6IIV+zuzZuQtwdQAK/j6z5uh6Eqjw/bial+e5lZsACt+fGRKEXwYq/DGi5+ciwJmNCt+Hszl6tMCwAAp/DlEShLwFKPwYIuaqj4PJGRLgqH6q7P792qvctj6a78ja038eYDZXw7L/qyKxBzRvAV+fH6e7/ujfqzTCKN23gqvUv0eQVY6rZ51LN4DXLl65DZY8B4gIbNXzg6UEmLFTVxNhCQEyKnoVEUoLgPDeXF2EkgIgBL+nqgilrgI8rs8fBTQaYLV7CCUaYPa1vH3tyOtWaQT4Bsi8keMRHnobwDYAyh281dsATgCU4M++52oiwAiAGvzZa6wiQroAVYI/e83qIqQLMALCDvIQIZP0q4CeEP+8vkOEv6V3TdnHUaoBsod1h2qNkN4Ard0LtkL4W6ocE3wDIAyplwptANEArf0fNOL7fC9Hx4JybHANgDKYCBAbAaYBWls7/C1IxwklgJiPBCBHApAjAciRAORIAHIkADkSgBwJQI4EIEcCkCMByJEA5EgAciQAORKAHAlAjgQgRwKQIwHIkQDkSAByJAA5cAIg/dJEBGjHB/ebQa1hPDjBG7TgDagGWPG5/Vd/pwAByAbYU7ERkEK+AqYB7gysylArHUuJBtiC3AYoof4EiAboGRzS+UHvWhDWX64B9mQ2AkKAo0AIUO2Ze1WfbXgEhAAGuggrBW9ACWCgibBi8AakAEa2CCsHb0BcBTwiYxczhN8aeANs8WyD0a95RIXgjTICGIiPWjMqBW+UE8BAEqFi8EZZAYxMESoHb5QXwJgpwgrBG8sIYESKsFLwxnICGJ4irBi80X0f4GgoCCdke0bDQwz/aM6961y2Abb0tAFi8BGUuBPoxZ3n9q/0dwruMCRAlbeBPWciVAjes/5bI2uAPTa4CsFHESJAhRaoSMRchwU42z2SwJezeY62l0sDSIJYosJvbcI5gCQYI3p+bgJc2SgJ+riam9eJq2sDSAI/ZoTfWmtPzy9v327f7R+Pwma+7HrE7NmFCNDa/R0vGXJnFSZAa6p9T6I2SqgAhkToJ7ohp9wKVs33MWNuUxpgjxrhnNmbJUWALZIhtyHTBRC5UH8cLCQAPRKAHAlAjgQgRwKQIwHIkQDkSAByJAA5EoAcCUDOX5HYy1YYL6iWAAAAAElFTkSuQmCC";
*/
const NOTIFICATION_ICON = chrome.runtime.getURL("icon128.png");
const FIXED_RETRYABLE_CODES = new Set([
  "SESSION_BUSY",
  "CONFIRMATION_VISIBLE",
  "COMPOSER_NOT_FOUND",
  "SEND_BUTTON_NOT_READY",
  "SUBMISSION_MARKER_NOT_CONFIRMED",
  "BOUND_TAB_MISSING",
  "TAB_LOAD_TIMEOUT",
  "DAEMON_TIMEOUT"
]);
let processing = false;
let lastDaemonErrorNotice = 0;

async function configureAlarm() {
  if (chrome.storage?.local?.setAccessLevel) {
    await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
  }
  await chrome.alarms.clear(ALARM);
  await chrome.alarms.create(ALARM, {delayInMinutes: 0.1, periodInMinutes: 0.5});
}

chrome.runtime.onInstalled.addListener(() => configureAlarm().then(() => runCycle({force: true})).catch(() => {}));
chrome.runtime.onStartup.addListener(() => configureAlarm().then(() => runCycle({force: true})).catch(() => {}));
configureAlarm().catch(() => {});

async function notify(title, message) {
  try {
    await chrome.notifications.create({type: "basic", iconUrl: NOTIFICATION_ICON, title, message: String(message).slice(0, 450)});
  } catch {}
}

async function resolveTab(binding, settings) {
  const expected = canonicalChatUrl(binding.url);
  if (Number.isInteger(binding.tabId)) {
    try {
      const tab = await chrome.tabs.get(binding.tabId);
      if (tab.url && canonicalChatUrl(tab.url) === expected) return tab;
    } catch {}
  }
  const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
  for (const tab of tabs) {
    try {
      if (tab.url && canonicalChatUrl(tab.url) === expected) return tab;
    } catch {}
  }
  if (!settings.openMissingTabs) throw new Error("BOUND_TAB_MISSING");
  return chrome.tabs.create({url: expected, active: false});
}

async function waitTab(tabId, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error("TAB_LOAD_TIMEOUT");
}

async function ensureContent(tabId) {
  try {
    const health = await chrome.tabs.sendMessage(tabId, {type: "SAT2_HEALTH"});
    if (health?.ok && String(health?.version || "").startsWith("2.")) return health;
  } catch {}
  await chrome.scripting.executeScript({target: {tabId}, files: ["content.js"]});
  return chrome.tabs.sendMessage(tabId, {type: "SAT2_HEALTH"});
}

function bindingPayload(binding, health, tab) {
  return {
    url: binding.url,
    conversation_key: binding.conversationKey || null,
    tab_id: tab?.id || binding.tabId || null,
    composer_ready: Boolean(health?.composerReady),
    send_ready: Boolean(health?.sendReady),
    busy: Boolean(health?.busy),
    login_required: Boolean(health?.loginRequired),
    confirmation_visible: Boolean(health?.confirmationVisible),
    error: health?.error || null,
    checked_at: new Date().toISOString()
  };
}

async function heartbeat(settings) {
  const activeRoles = [];
  const payloadBindings = {};
  for (const [role, binding] of Object.entries(settings.bindings || {})) {
    if (!binding?.url) continue;
    try {
      const tab = await resolveTab(binding, {...settings, openMissingTabs: false});
      const health = await ensureContent(tab.id);
      binding.tabId = tab.id;
      binding.lastHealth = health;
      binding.lastHealthAt = new Date().toISOString();
      payloadBindings[role] = bindingPayload(binding, health, tab);
      if (health.composerReady && !health.loginRequired) activeRoles.push(role);
    } catch (error) {
      const health = {ok: false, error: error.message || String(error)};
      binding.lastHealth = health;
      binding.lastHealthAt = new Date().toISOString();
      payloadBindings[role] = bindingPayload(binding, health, null);
    }
  }
  settings.lastCycleAt = new Date().toISOString();
  await saveSettings(settings);
  const result = await daemonFetch(settings, "/api/v2/extension/heartbeat", {
    method: "POST",
    body: {
      installation_id: settings.installationId,
      extension_version: chrome.runtime.getManifest().version,
      auto_enabled: settings.autoEnabled,
      bindings: payloadBindings,
      active_roles: activeRoles,
      browser: navigator.userAgent,
      last_cycle_at: settings.lastCycleAt,
      last_cycle_result: settings.lastCycleResult || ""
    }
  });
  settings.lastDaemonHealth = result;
  const latestAlert = result?.latest_open_alert;
  if (latestAlert?.id && Number(latestAlert.id) > Number(settings.lastAlertId || 0)) {
    settings.lastAlertId = Number(latestAlert.id);
    await notify(`SAT2 Relay: ${latestAlert.code || "alert"}`, `${latestAlert.task_id || "control"} · ${String(latestAlert.detail || "").slice(0, 300)}`);
  }
  await saveSettings(settings);
  return result;
}

async function reportResult(settings, deliveryId, result) {
  return daemonFetch(settings, `/api/v2/deliveries/${deliveryId}/result`, {method: "POST", body: result});
}


async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(String(text || ""));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function decisionContextKey(role, conversationKey) {
  return `${role}:${conversationKey || "unknown"}`;
}

function rememberDecisionContext(settings, delivery, binding) {
  if (!delivery?.delivery_token || !binding?.conversationKey) return;
  const key = decisionContextKey(delivery.target_role, binding.conversationKey);
  settings.decisionContexts[key] = {
    deliveryId: delivery.id,
    deliveryToken: delivery.delivery_token,
    eventId: delivery.event_id,
    role: delivery.target_role,
    conversationKey: binding.conversationKey,
    createdAt: new Date().toISOString(),
    ackSubmitted: false
  };
}

async function submitDetectedDecision(settings, context, found, manual = false) {
  if (!found?.decision) return {empty: true};
  const messageHash = await sha256Hex(found.assistantMessageText || JSON.stringify(found.decision));
  const historyKey = `${context.deliveryId}:${messageHash}:${found.decision.decision}`;
  if (settings.decisionHistory?.[historyKey]) return {duplicate: true, result: settings.decisionHistory[historyKey]};
  const result = await daemonFetch(settings, "/api/v2/decisions/submit", {
    method: "POST",
    body: {
      installation_id: settings.installationId,
      role: context.role,
      conversation_key: context.conversationKey,
      delivery_id: context.deliveryId,
      delivery_token: context.deliveryToken,
      assistant_message_id: found.assistantMessageId || `assistant-${messageHash.slice(0, 16)}`,
      assistant_message_hash: messageHash,
      decision: found.decision.decision,
      summary: found.decision.summary,
      manual: Boolean(manual)
    }
  });
  settings.decisionHistory[historyKey] = {at: new Date().toISOString(), result};
  settings.decisionHistory = pruneHistory(settings.decisionHistory, 1000);
  if (found.decision.decision === "WORKER_ACK") context.ackSubmitted = true;
  else context.completedAt = new Date().toISOString();
  await saveSettings(settings);
  await notify("SAT2 Relay decision submitted", `${context.role}: ${found.decision.decision}${result?.waiting_for_human ? " · 等待人工确认" : ""}`);
  return {submitted: true, decision: found.decision.decision, result};
}

async function captureDecisions(settings, {force = false, onlyKey = null} = {}) {
  const results = [];
  for (const [key, context] of Object.entries(settings.decisionContexts || {})) {
    if (onlyKey && key !== onlyKey) continue;
    if (context.completedAt) continue;
    const binding = settings.bindings?.[context.role];
    if (!binding?.url || binding.conversationKey !== context.conversationKey) continue;
    try {
      const tab = await resolveTab(binding, {...settings, openMissingTabs: false});
      await ensureContent(tab.id);
      const found = await chrome.tabs.sendMessage(tab.id, {
        type: "SAT2_FIND_DECISION",
        deliveryToken: context.deliveryToken,
        force
      });
      if (!found?.ok) throw new Error(found?.code || found?.error || "DECISION_SCAN_FAILED");
      if (!found.decision) { results.push({key, empty: true}); continue; }
      const submitted = await submitDetectedDecision(settings, context, found, force);
      results.push({key, ...submitted});
    } catch (error) {
      results.push({key, error: error.message || String(error)});
    }
  }
  for (const [key, context] of Object.entries(settings.decisionContexts || {})) {
    if (context.completedAt && Date.now() - Date.parse(context.completedAt) > 60 * 60 * 1000) delete settings.decisionContexts[key];
  }
  await saveSettings(settings);
  return results;
}

async function processOne(settings) {
  const response = await daemonFetch(settings, `/api/v2/deliveries/next?installation_id=${encodeURIComponent(settings.installationId)}`);
  const delivery = response?.delivery;
  if (!delivery) return {empty: true};
  const relayId = `${delivery.event_id}:${delivery.target_role}`;
  if (settings.deliveryHistory?.[relayId]) {
    const duplicateBinding = settings.bindings?.[delivery.target_role];
    rememberDecisionContext(settings, delivery, duplicateBinding);
    await saveSettings(settings);
    await reportResult(settings, delivery.id, {
      success: true,
      code: "DELIVERED_DUPLICATE_HISTORY",
      detail: "Extension persistent history already contains this relay marker.",
      observed_message_marker: `Relay delivery: ${relayId}`
    });
    return {empty: false, delivered: true, duplicate: true};
  }
  const binding = settings.bindings?.[delivery.target_role];
  if (!binding?.url) {
    await reportResult(settings, delivery.id, {success: false, code: "ROLE_NOT_BOUND", detail: `Role ${delivery.target_role} is not bound`, retryable: true});
    await notify("SAT2 Relay blocked", `角色 ${delivery.target_role} 未绑定。`);
    return {empty: false, blocked: true};
  }
  try {
    const tab = await resolveTab(binding, settings);
    await waitTab(tab.id);
    if (settings.activateTargetTab) {
      await chrome.windows.update(tab.windowId, {focused: true});
      await chrome.tabs.update(tab.id, {active: true});
    }
    const health = await ensureContent(tab.id);
    if (health.loginRequired) throw new Error("LOGIN_REQUIRED");
    if (health.confirmationVisible) throw new Error("CONFIRMATION_VISIBLE");
    if (health.busy) throw new Error("SESSION_BUSY");
    if (!health.composerReady) throw new Error("COMPOSER_NOT_FOUND");
    const sent = await chrome.tabs.sendMessage(tab.id, {
      type: "SAT2_SEND",
      text: delivery.body,
      relayId,
      requiredApps: delivery.required_apps || [],
      connectorMode: settings.connectorMode || "session_verified"
    });
    if (!sent?.ok) throw new Error(sent?.code || sent?.error || "SEND_NOT_CONFIRMED");
    binding.tabId = tab.id;
    binding.lastSentAt = new Date().toISOString();
    binding.lastHealth = sent.health || null;
    settings.deliveryHistory[relayId] = new Date().toISOString();
    settings.deliveryHistory = pruneHistory(settings.deliveryHistory);
    rememberDecisionContext(settings, delivery, binding);
    await saveSettings(settings);
    await reportResult(settings, delivery.id, {
      success: true,
      code: sent.duplicate ? "DELIVERED_DUPLICATE_TRANSCRIPT" : "DELIVERED",
      detail: sent.connectorAttached ? "GitHub app attached" : `Message confirmed (${sent.connectorMode || settings.connectorMode})`,
      observed_url: tab.url,
      observed_message_marker: sent.marker,
      diagnostics: {content_health: sent.health || null}
    });
    return {empty: false, delivered: true, duplicate: Boolean(sent.duplicate)};
  } catch (error) {
    const code = String(error.message || error).split(/\s/)[0].slice(0, 80);
    const retryable = FIXED_RETRYABLE_CODES.has(code);
    await reportResult(settings, delivery.id, {
      success: false,
      code,
      detail: error.message || String(error),
      retryable,
      observed_url: binding.url,
      diagnostics: {binding, lastHealth: binding.lastHealth || null}
    });
    if (!retryable || ["LOGIN_REQUIRED", "CONFIRMATION_VISIBLE", "COMPOSER_NOT_FOUND", "BOUND_TAB_MISSING"].includes(code)) {
      await notify("SAT2 Relay needs attention", `${delivery.target_role}: ${code}`);
    }
    return {empty: false, delivered: false, code, retryable};
  }
}

export async function runCycle({force = false} = {}) {
  if (processing) return {skipped: "already_processing"};
  processing = true;
  try {
    const settings = await loadSettings();
    if (!settings.daemonToken) return {skipped: "not_paired"};
    const heartbeatResult = await heartbeat(settings);
    const decisionsBefore = await captureDecisions(settings);
    if (!settings.autoEnabled && !force) return {skipped: "disabled", heartbeat: heartbeatResult, decisions: decisionsBefore};
    const results = [];
    for (let i = 0; i < 20; i += 1) {
      const result = await processOne(settings);
      results.push(result);
      if (result.empty) break;
      if (!result.delivered && !result.retryable) break;
      if (!result.delivered) break;
    }
    const decisionsAfter = await captureDecisions(settings);
    settings.lastCycleAt = new Date().toISOString();
    settings.lastCycleResult = JSON.stringify({deliveries: results.slice(-5), decisions: [...decisionsBefore, ...decisionsAfter].slice(-10)});
    await saveSettings(settings);
    return {ok: true, heartbeat: heartbeatResult, results, decisions: [...decisionsBefore, ...decisionsAfter]};
  } catch (error) {
    const settings = await loadSettings();
    settings.lastCycleAt = new Date().toISOString();
    settings.lastCycleResult = error.message || String(error);
    await saveSettings(settings);
    throw error;
  } finally {
    processing = false;
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== ALARM) return;
  runCycle().catch(async (error) => {
    if (Date.now() - lastDaemonErrorNotice > 10 * 60 * 1000) {
      lastDaemonErrorNotice = Date.now();
      await notify("SAT2 Relay Control Center offline", error.message || String(error));
    }
  });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "SAT2_BIND_CURRENT") {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab?.id || !tab.url?.startsWith("https://chatgpt.com/")) throw new Error("当前页不是 chatgpt.com 会话。");
      const health = await ensureContent(tab.id);
      if (!health.composerReady) throw new Error("当前页未找到 ChatGPT 输入框。");
      const settings = await loadSettings();
      const url = canonicalChatUrl(tab.url);
      const conversationKey = parseConversationKey(url);
      const duplicate = Object.entries(settings.bindings || {}).find(([role, binding]) => role !== message.role && binding?.conversationKey === conversationKey);
      if (duplicate && !settings.allowDuplicateBindings) throw new Error(`DUPLICATE_SESSION_BINDING:${duplicate[0]}`);
      settings.bindings[message.role] = {
        url,
        conversationKey,
        tabId: tab.id,
        title: tab.title || "",
        boundAt: new Date().toISOString(),
        lastHealth: health
      };
      await saveSettings(settings);
      return {ok: true, binding: settings.bindings[message.role]};
    }
    if (message?.type === "SAT2_UNBIND") {
      const settings = await loadSettings();
      delete settings.bindings[message.role];
      await saveSettings(settings);
      return {ok: true};
    }
    if (message?.type === "SAT2_RUN_NOW") return runCycle({force: true});
    if (message?.type === "SAT2_TEST_DAEMON") {
      const settings = await loadSettings();
      return daemonFetch(settings, "/api/v2/health");
    }
    if (message?.type === "SAT2_GET_STATUS") {
      const settings = await loadSettings();
      return daemonFetch(settings, "/api/v2/status");
    }
    if (message?.type === "SAT2_DOCTOR") {
      const settings = await loadSettings();
      return daemonFetch(settings, "/api/v2/doctor?deep=true");
    }
    if (message?.type === "SAT2_DIAGNOSTICS") {
      const settings = await loadSettings();
      return daemonFetch(settings, "/api/v2/diagnostics/export");
    }
    if (message?.type === "SAT2_RELOAD_CREDENTIALS") {
      const settings = await loadSettings();
      return daemonFetch(settings, "/api/v2/control/reload-credentials", {method: "POST"});
    }
    if (message?.type === "SAT2_SUBMIT_CURRENT_DECISION") {
      const settings = await loadSettings();
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab?.url) throw new Error("ACTIVE_TAB_MISSING");
      const conversationKey = parseConversationKey(canonicalChatUrl(tab.url));
      const entry = Object.entries(settings.decisionContexts || {}).find(([, context]) => context.conversationKey === conversationKey && !context.completedAt);
      if (!entry) throw new Error("NO_ACTIVE_DECISION_CONTEXT");
      await ensureContent(tab.id);
      const found = await chrome.tabs.sendMessage(tab.id, {type: "SAT2_FIND_DECISION", deliveryToken: entry[1].deliveryToken, force: true});
      if (!found?.decision) throw new Error("DECISION_NOT_FOUND");
      return submitDetectedDecision(settings, entry[1], found, true);
    }
    if (message?.type === "SAT2_MANUAL_SEND") {
      const settings = await loadSettings();
      const binding = settings.bindings?.[message.role];
      if (!binding) throw new Error("角色未绑定。");
      const tab = await resolveTab(binding, settings);
      await waitTab(tab.id);
      await ensureContent(tab.id);
      return chrome.tabs.sendMessage(tab.id, {
        type: "SAT2_SEND",
        text: message.text,
        relayId: `manual-${crypto.randomUUID()}`,
        requiredApps: [],
        connectorMode: "session_verified"
      });
    }
    throw new Error("UNKNOWN_MESSAGE");
  })().then(sendResponse).catch((error) => sendResponse({ok: false, error: error.message || String(error)}));
  return true;
});
