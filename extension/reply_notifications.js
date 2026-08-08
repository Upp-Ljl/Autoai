import {canonicalChatUrl, loadSettings, parseConversationKey, pruneHistory, saveSettings} from "./lib.js";

const NOTIFICATION_ICON = chrome.runtime.getURL("icon128.png");
const PREFIX = "sat2-session-reply:";
const PORT_NAME = "SAT2_SESSION_REPLY_COMPLETE";

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(String(text || ""));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function boundRoleFor(settings, conversationKey, tabId) {
  for (const [role, binding] of Object.entries(settings.bindings || {})) {
    if (!binding?.url) continue;
    if (binding.conversationKey && binding.conversationKey === conversationKey) return {role, binding};
    if (Number.isInteger(binding.tabId) && binding.tabId === tabId) return {role, binding};
  }
  return null;
}

async function handleReplyComplete(message, sender) {
  const settings = await loadSettings();
  if (settings.notifyOnSessionComplete === false) return {ok: true, ignored: "notifications_disabled"};
  const tab = sender.tab;
  if (!tab?.id || !tab.url?.startsWith("https://chatgpt.com/")) return {ok: true, ignored: "not_chatgpt_tab"};

  let conversationKey;
  try {
    conversationKey = parseConversationKey(canonicalChatUrl(tab.url));
  } catch {
    return {ok: true, ignored: "not_concrete_conversation"};
  }

  const bound = boundRoleFor(settings, conversationKey, tab.id);
  if (!bound) return {ok: true, ignored: "session_not_bound"};

  const text = String(message.assistantText || "");
  const messageId = String(message.messageId || "unknown");
  const digest = await sha256Hex(`${bound.role}\n${conversationKey}\n${messageId}\n${text}`);
  const historyKey = `${bound.role}:${conversationKey}:${digest}`;
  settings.replyNotificationHistory = settings.replyNotificationHistory || {};
  if (settings.replyNotificationHistory[historyKey]) return {ok: true, duplicate: true};

  settings.replyNotificationHistory[historyKey] = new Date().toISOString();
  settings.replyNotificationHistory = pruneHistory(settings.replyNotificationHistory, 1000);
  await saveSettings(settings);

  const notificationId = `${PREFIX}${tab.id}:${digest.slice(0, 20)}`;
  const preview = text.replace(/\s+/g, " ").trim().slice(0, 220);
  await chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title: `SAT2 ${bound.role} 回复完成`,
    message: preview ? `${preview}${text.length > 220 ? "…" : ""}\n点击返回该 Session。` : "已完成回复。点击返回该 Session。",
    priority: 1
  });
  return {ok: true, notified: true, role: bound.role, conversationKey};
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) return;
  const sender = port.sender;
  port.onMessage.addListener((message) => {
    handleReplyComplete(message, sender).catch(() => {});
  });
});

chrome.notifications.onClicked.addListener(async (notificationId) => {
  if (!notificationId.startsWith(PREFIX)) return;
  const tail = notificationId.slice(PREFIX.length);
  const tabId = Number(tail.split(":")[0]);
  if (!Number.isInteger(tabId)) return;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (Number.isInteger(tab.windowId)) await chrome.windows.update(tab.windowId, {focused: true});
    await chrome.tabs.update(tabId, {active: true});
    await chrome.notifications.clear(notificationId);
  } catch {}
});
