const $ = (id) => document.getElementById(id);
let attached = false;

const send = (message) => chrome.runtime.sendMessage(message);

function setAttached(state, detail) {
  attached = state;
  $("attachSel").disabled = state;
  $("detach").disabled = !state;
  $("export").disabled = !state;
  $("clear").disabled = false;
  $("status").textContent = state
    ? (detail || "已附着。") + "\n在选中的测试会话里发消息即可。"
    : "未附着。勾选要测试的 ChatGPT 标签页（TP 测试会话），再点「附着选中的标签页」。";
}

(async function init() {
  const status = await send({type: "NR_STATUS"});
  if (status?.attached) setAttached(true, `已附着 ${status.tabIds.length} 个标签页。`);
  await refreshTabs();
})();

async function refreshTabs() {
  const tabs = await chrome.tabs.query({url: "https://chatgpt.com/*"});
  const box = $("tabs");
  box.replaceChildren();
  if (!tabs.length) {
    box.textContent = "没有打开的 ChatGPT 标签页。";
    return;
  }
  for (const tab of tabs) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = String(tab.id);
    cb.dataset.url = tab.url || "";
    const title = document.createElement("span");
    title.textContent = ` [${tab.id}] ${(tab.title || tab.url || "").slice(0, 60)}`;
    label.append(cb, title);
    box.append(label);
  }
}

$("refreshTabs").addEventListener("click", refreshTabs);

$("attachSel").addEventListener("click", async () => {
  const checked = [...document.querySelectorAll("#tabs input:checked")];
  if (!checked.length) {
    $("status").textContent = "请先勾选要测试的标签页。";
    $("status").className = "status bad";
    return;
  }
  const tabIds = checked.map((cb) => Number(cb.value));
  const results = [];
  for (const id of tabIds) {
    results.push(await send({type: "NR_ATTACH", tabId: id}));
  }
  const ok = results.filter((r) => r?.ok).length;
  setAttached(ok > 0, `已附着 ${ok}/${tabIds.length} 个选中标签页。`);
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
