import {daemonFetch, loadSettings, saveSettings} from "./lib.js";
const $ = (id) => document.getElementById(id);
let settings;
async function init(){settings=await loadSettings();$("url").value=settings.daemonUrl;$("token").value=settings.daemonToken;$("activate").checked=settings.activateTargetTab;$("open").checked=settings.openMissingTabs;$("duplicates").checked=settings.allowDuplicateBindings;$("connector").value=settings.connectorMode||"session_verified"}
function collect(){settings.daemonUrl=$("url").value.replace(/\/$/,"");settings.daemonToken=$("token").value.trim();settings.activateTargetTab=$("activate").checked;settings.openMissingTabs=$("open").checked;settings.allowDuplicateBindings=$("duplicates").checked;settings.connectorMode=$("connector").value;return settings}
$("save").addEventListener("click",async()=>{await saveSettings(collect());$("result").className="status ok";$("result").textContent="已保存。"});
$("test").addEventListener("click",async()=>{try{const result=await daemonFetch(collect(),"/api/v2/health");$("result").className="status ok";$("result").textContent=JSON.stringify(result,null,2)}catch(e){$("result").className="status bad";$("result").textContent=e.message||String(e)}});
$("doctor").addEventListener("click",async()=>{try{const result=await daemonFetch(collect(),"/api/v2/doctor?deep=true");$("result").className=result.ok?"status ok":"status bad";$("result").textContent=JSON.stringify(result,null,2)}catch(e){$("result").className="status bad";$("result").textContent=e.message||String(e)}});
init();
