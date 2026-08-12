const $ = (id) => document.getElementById(id);
let attached = false;

const send = (message) => chrome.runtime.sendMessage(message);

function setAttached(state, detail) {
  attached = state;
  $("attachAll").disabled = state;
  $("detach").disabled = !state;
  $("export").disabled = !state;
  $("clear").disabled = false;
  $("status").textContent = state
    ? (detail || "已附着。") + "\n直接发送消息即可，无需标记。"
    : "未附着。打开 ChatGPT 测试会话后点「① 附着所有 ChatGPT 标签页」。";
}

(async function init() {
  const status = await send({type: "NR_STATUS"});
  if (status?.attached) setAttached(true, `已附着 ${status.tabIds.length} 个标签页。`);
})();

$("attachAll").addEventListener("click", async () => {
  const result = await send({type: "NR_ATTACH_ALL"});
  if (result.ok) setAttached(true, `已附着 ${result.count}/${result.total} 个标签页。`);
  else {
    $("status").textContent = "附着失败: " + (result.error || "unknown");
    $("status").className = "status bad";
  }
});

$("detach").addEventListener("click", async () => {
  await send({type: "NR_DETACH"});
  setAttached(false);
});

$("export").addEventListener("click", async () => {
  $("status").textContent = "正在导出…";
  const result = await send({type: "NR_EXPORT"});
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
  await send({type: "NR_CLEAR"});
  $("status").textContent = "记录已清空。";
});

setInterval(async () => {
  if (!attached) return;
  const result = await send({type: "NR_COUNT"});
  if (result) $("count").textContent = `已采集 ${result.count} 条记录（shadow，不提交 daemon）。`;
}, 3000);
