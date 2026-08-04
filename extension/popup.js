import {loadSettings, saveSettings} from "./lib.js";

const $ = (id) => document.getElementById(id);
let settings;

function renderBindings() {
  const root = $("bindings");
  root.replaceChildren();
  const seen = new Map();
  for (const role of ["mentor", "S1", "S2", "S3", "S4"]) {
    const binding = settings.bindings?.[role];
    const div = document.createElement("div");
    div.className = "binding muted";
    if (!binding?.url) {
      div.textContent = `${role}: 未绑定`;
    } else {
      const duplicate = binding.conversationKey && seen.has(binding.conversationKey);
      if (binding.conversationKey) seen.set(binding.conversationKey, role);
      const health = binding.lastHealth || {};
      const state = health.error ? health.error : health.busy ? "忙" : health.composerReady ? "可用" : "输入框未就绪";
      div.textContent = `${role}: ${binding.conversationKey || binding.url} · ${state}${duplicate ? " · 重复绑定" : ""}`;
      if (duplicate || health.error) div.className = "binding bad";
    }
    root.append(div);
  }
}

async function refresh() {
  settings = await loadSettings();
  $("auto").checked = settings.autoEnabled;
  renderBindings();
  try {
    const result = await chrome.runtime.sendMessage({type: "SAT2_TEST_DAEMON"});
    if (!result?.ok) throw new Error(result?.error || "daemon unavailable");
    const snapshot = await chrome.runtime.sendMessage({type: "SAT2_GET_STATUS"});
    const deliveries = snapshot?.deliveries || [];
    const count = (state) => deliveries.filter((row) => row.status === state).length;
    const openAlerts = (snapshot?.alerts || []).filter((row) => !row.resolved_at).length;
    $("status").className = result.last_poll_error ? "status bad" : "status ok";
    $("status").textContent = `Control Center 在线\n模式: ${result.mode}\n最近轮询: ${result.last_poll || "尚无"}\nToken: ${result.github_token_source || "unknown"} / ${result.github_token_fingerprint || "none"}`;
    const outbox = snapshot?.outbox || [];
    const pendingOutbox = outbox.filter((row) => !["published", "blocked", "cancelled"].includes(row.status)).length;
    $("queue").textContent = `待投递 ${count("pending") + count("retry")} · 租约中 ${count("leased")} · 待发布 ${pendingOutbox} · 失败 ${count("failed")} · 未解决警报 ${openAlerts}`;
  } catch (error) {
    $("status").className = "status bad";
    $("status").textContent = `Control Center 未连接：${error.message || error}`;
    $("queue").textContent = "";
  }
}

$("auto").addEventListener("change", async () => {
  settings.autoEnabled = $("auto").checked;
  await saveSettings(settings);
  if (settings.autoEnabled) await chrome.runtime.sendMessage({type: "SAT2_RUN_NOW"});
});

$("run").addEventListener("click", async () => {
  $("status").textContent = "运行中…";
  const result = await chrome.runtime.sendMessage({type: "SAT2_RUN_NOW"});
  $("diagnostic").textContent = JSON.stringify(result, null, 2);
  setTimeout(refresh, 300);
});


$("submitDecision").addEventListener("click", async () => {
  $("diagnostic").textContent = "读取当前 Session Decision…";
  const result = await chrome.runtime.sendMessage({type: "SAT2_SUBMIT_CURRENT_DECISION"});
  $("diagnostic").textContent = JSON.stringify(result, null, 2);
  if (!result?.ok && !result?.submitted) alert(result?.error || "Decision 提交失败");
  await refresh();
});

$("bind").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({type: "SAT2_BIND_CURRENT", role: $("role").value});
  if (!result?.ok) alert(result?.error || "绑定失败");
  await refresh();
});

$("unbind").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({type: "SAT2_UNBIND", role: $("role").value});
  await refresh();
});

$("doctor").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({type: "SAT2_DOCTOR"});
  $("diagnostic").textContent = JSON.stringify(result, null, 2);
  await refresh();
});

$("bundle").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({type: "SAT2_DIAGNOSTICS"});
  const text = JSON.stringify(result, null, 2);
  $("diagnostic").textContent = text;
  try { await navigator.clipboard.writeText(text); } catch {}
});

$("reload").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({type: "SAT2_RELOAD_CREDENTIALS"});
  $("diagnostic").textContent = JSON.stringify(result, null, 2);
  await refresh();
});

$("manual").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({type: "SAT2_MANUAL_SEND", role: $("manualRole").value, text: $("manualText").value});
  alert(result?.ok ? "发送已确认" : `失败：${result?.error || result?.code}`);
});

$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("dashboard").addEventListener("click", () => chrome.tabs.create({url: `${settings.daemonUrl}/`}));
refresh();
