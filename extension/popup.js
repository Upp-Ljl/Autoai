import {loadSettings, saveSettings} from "./lib.js";

const $ = (id) => document.getElementById(id);
const NATIVE_HOST = "com.sat2.relay.host";
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

function nativeRegistrationCommand() {
  return `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\\SAT2Relay\\on-demand\\REGISTER_NATIVE_HOST.ps1" -ExtensionId ${chrome.runtime.id}`;
}

async function nativeMessage(command) {
  try {
    const result = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {command});
    if (!result?.ok) {
      return {
        ok: false,
        code: result?.code || "NATIVE_HOST_FAILED",
        error: result?.detail || "Native host returned an error.",
        native: result,
        registration_command: nativeRegistrationCommand()
      };
    }
    return {ok: true, native: result};
  } catch (error) {
    return {
      ok: false,
      code: "NATIVE_HOST_UNAVAILABLE",
      error: error?.message || String(error),
      extension_id: chrome.runtime.id,
      registration_command: nativeRegistrationCommand()
    };
  }
}

async function waitForDaemon(timeoutMs = 20000) {
  const started = Date.now();
  let last = null;
  while (Date.now() - started < timeoutMs) {
    last = await chrome.runtime.sendMessage({type: "SAT2_TEST_DAEMON"});
    if (last?.ok) return last;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return {ok: false, error: last?.error || "DAEMON_START_TIMEOUT"};
}

async function startCollaboration() {
  let health = await chrome.runtime.sendMessage({type: "SAT2_TEST_DAEMON"});
  let native = {ok: true, native: {action: "already_running"}};

  if (!health?.ok) {
    native = await nativeMessage("ensure_running");
    if (!native.ok) return native;
    health = await waitForDaemon();
    if (!health?.ok) {
      return {ok: false, code: "DAEMON_START_TIMEOUT", error: health?.error || "Relay did not become healthy.", native};
    }
  }

  settings = await loadSettings();
  settings.autoEnabled = true;
  await saveSettings(settings);
  $("auto").checked = true;

  const cycle = await chrome.runtime.sendMessage({type: "SAT2_RUN_NOW"});
  if (!cycle?.ok && !cycle?.skipped) {
    return {ok: false, code: "START_CYCLE_FAILED", error: cycle?.error || "Initial Relay cycle failed.", native, health, cycle};
  }

  const doctor = await chrome.runtime.sendMessage({type: "SAT2_DOCTOR"});
  return {ok: true, native, health, cycle, doctor, extension_id: chrome.runtime.id};
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
    $("status").textContent = `Control Center 在线\n模式: ${result.mode}\n最近轮询: ${result.last_poll || "尚无"}\nToken: ${result.github_token_source || "unknown"} / ${result.github_token_fingerprint || "none"}\nExtension ID: ${chrome.runtime.id}`;
    const outbox = snapshot?.outbox || [];
    const pendingOutbox = outbox.filter((row) => !["published", "blocked", "cancelled"].includes(row.status)).length;
    $("queue").textContent = `待投递 ${count("pending") + count("retry")} · 租约中 ${count("leased")} · 待发布 ${pendingOutbox} · 失败 ${count("failed")} · 未解决警报 ${openAlerts}`;
  } catch (error) {
    $("status").className = "status bad";
    $("status").textContent = `Control Center 未连接：${error.message || error}\n点击“一键启动协作”可启动本地 Relay。\nExtension ID: ${chrome.runtime.id}`;
    $("queue").textContent = "";
  }
}

$("startCollab").addEventListener("click", async () => {
  $("startCollab").disabled = true;
  $("status").className = "status muted";
  $("status").textContent = "正在启动本地 Relay、开启自动推进并执行首轮轮询…";
  try {
    const result = await startCollaboration();
    $("diagnostic").textContent = JSON.stringify(result, null, 2);
    if (!result?.ok) {
      $("status").className = "status bad";
      $("status").textContent = `启动失败：${result?.code || result?.error || "unknown"}`;
      if (result?.registration_command) {
        try { await navigator.clipboard.writeText(result.registration_command); } catch {}
      }
    } else {
      $("status").className = "status ok";
      $("status").textContent = "Relay 已启动，自动推进已开启，首轮轮询已执行。";
    }
  } finally {
    $("startCollab").disabled = false;
    setTimeout(refresh, 300);
  }
});

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
