// Probe extension popup controller.
const $ = (id) => document.getElementById(id);
let attached = false;

async function send(message) {
  return chrome.runtime.sendMessage(message);
}

function setAttached(state, url) {
  attached = state;
  $("attach").disabled = state;
  $("detach").disabled = !state;
  $("mark1").disabled = !state;
  $("mark2").disabled = !state;
  $("export").disabled = !state;
  $("clear").disabled = false;
  const el = $("status");
  el.textContent = state
    ? `已附着: ${url || "当前会话"}\n① 点「标记: 普通文本消息」→ 发一条普通消息\n② 点「标记: @GitHub 消息」→ 发一条 @GitHub 消息\n③ 「停止并导出 JSONL」`
    : "未附着。请打开 ChatGPT 测试会话标签页并点「附着到当前标签页」。";
  el.className = "status";
}

// Restore real attach state when the popup opens (popup is a fresh page each time).
(async function init() {
  const status = await send({type: "PROBE_STATUS"});
  if (status?.attached) {
    setAttached(true, status.tabUrl || null);
  }
})();

$("attach").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || !tab.url || !tab.url.startsWith("https://chatgpt.com/")) {
    $("status").textContent = "请先打开 ChatGPT 测试会话标签页（https://chatgpt.com/）。";
    $("status").className = "status bad";
    return;
  }
  const result = await send({type: "PROBE_ATTACH", tabId: tab.id});
  if (result.ok) {
    setAttached(true);
    $("status").textContent = "已附着: " + tab.url + "\n① 点「标记: 普通文本消息」→ 发一条普通消息\n② 点「标记: @GitHub 消息」→ 发一条 @GitHub 消息\n③ 「停止并导出 JSONL」";
  } else {
    $("status").textContent = "附着失败: " + (result.error || "unknown");
    $("status").className = "status bad";
  }
});

$("detach").addEventListener("click", async () => {
  await send({type: "PROBE_DETACH"});
  setAttached(false);
});

$("mark1").addEventListener("click", async () => {
  await send({type: "PROBE_MARK", case: 1, label: "plain-text-message"});
  $("status").textContent = "已标记「普通文本消息」。现在发送你的普通消息。";
  $("status").className = "status ok";
});

$("mark2").addEventListener("click", async () => {
  await send({type: "PROBE_MARK", case: 2, label: "at-github-message"});
  $("status").textContent = "已标记「@GitHub 消息」。现在发送你的 @GitHub 消息。";
  $("status").className = "status ok";
});

$("export").addEventListener("click", async () => {
  $("status").textContent = "正在导出…（浏览器会提示保存 JSONL）";
  const result = await send({type: "PROBE_EXPORT"});
  if (result?.ok) {
    $("status").textContent = `已导出 ${result.rows} 行记录。`;
    $("status").className = "status ok";
    setAttached(false);
  } else {
    $("status").textContent = "导出失败: " + ((result && result.error) || "unknown");
    $("status").className = "status bad";
  }
});

$("clear").addEventListener("click", async () => {
  await send({type: "PROBE_CLEAR"});
  $("status").textContent = "记录已清空。";
});

// live count while attached
setInterval(async () => {
  if (!attached) return;
  const result = await send({type: "PROBE_COUNT"});
  if (result) {
    $("count").textContent = `已采集 ${result.count} 条记录（脱敏元数据）。`;
  }
}, 3000);
