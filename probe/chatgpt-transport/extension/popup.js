// Phase 2 probe popup controller.
const $ = (id) => document.getElementById(id);
let attached = false;

async function send(message) {
  return chrome.runtime.sendMessage(message);
}

function setAttached(state, detail) {
  attached = state;
  $("attach").disabled = state;
  $("attachAll").disabled = state;
  $("detach").disabled = !state;
  $("markA").disabled = !state;
  $("markB").disabled = !state;
  $("markG").disabled = !state;
  $("markConcurrent").disabled = !state;
  $("export").disabled = !state;
  $("clear").disabled = false;
  const el = $("status");
  el.textContent = state
    ? (detail || "已附着。") + "\n标记 → 发消息 → 标记下一项 → … → 停止并导出"
    : "未附着。打开 ChatGPT 测试会话后点「附着当前标签页」或「附着所有 ChatGPT 标签页」。";
  el.className = "status";
}

(async function init() {
  const status = await send({type: "PROBE_STATUS"});
  if (status?.attached) {
    setAttached(true, `已附着 ${status.tabIds.length} 个标签页。`);
  }
})();

async function attachTo(tab) {
  if (!tab || !tab.url || !tab.url.startsWith("https://chatgpt.com/")) {
    $("status").textContent = "请先打开 ChatGPT 测试会话标签页。";
    $("status").className = "status bad";
    return false;
  }
  const result = await send({type: "PROBE_ATTACH", tabId: tab.id});
  if (result.ok) {
    setAttached(true, "已附着当前标签页。");
    return true;
  }
  $("status").textContent = "附着失败: " + (result.error || "unknown");
  $("status").className = "status bad";
  return false;
}

$("attach").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  await attachTo(tab);
});

$("attachAll").addEventListener("click", async () => {
  const result = await send({type: "PROBE_ATTACH_ALL"});
  if (result.ok) {
    setAttached(true, `已附着 ${result.count}/${result.total} 个 ChatGPT 标签页。`);
  } else {
    $("status").textContent = "附着失败: " + (result.error || "unknown");
    $("status").className = "status bad";
  }
});

$("detach").addEventListener("click", async () => {
  await send({type: "PROBE_DETACH"});
  setAttached(false);
});

function mark(label, text) {
  $("markA").addEventListener("click", async () => {
    await send({type: "PROBE_MARK", case: "A", label: "TP2-A"});
    $("status").textContent = "已标记 A。现在向 TP2-A 发送: Reply with exactly: TP2_A_7K3M";
    $("status").className = "status ok";
  });
  $("markB").addEventListener("click", async () => {
    await send({type: "PROBE_MARK", case: "B", label: "TP2-B"});
    $("status").textContent = "已标记 B。现在向 TP2-B 发送: Reply with exactly: TP2_B_9Q2X";
    $("status").className = "status ok";
  });
  $("markG").addEventListener("click", async () => {
    await send({type: "PROBE_MARK", case: "GITHUB", label: "TP2-GITHUB"});
    $("status").textContent = "已标记 GITHUB。现在向 TP2-GITHUB 发送只读 @GitHub 请求。";
    $("status").className = "status ok";
  });
  $("markConcurrent").addEventListener("click", async () => {
    await send({type: "PROBE_MARK", case: "concurrent", label: "two-tab-concurrent"});
    $("status").textContent = "已标记并发。A 开始生成未完成时让 B 开始生成。";
    $("status").className = "status ok";
  });
}

mark();

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

setInterval(async () => {
  if (!attached) return;
  const result = await send({type: "PROBE_COUNT"});
  if (result) {
    $("count").textContent = `已采集 ${result.count} 条记录（脱敏元数据）。`;
  }
}, 3000);
