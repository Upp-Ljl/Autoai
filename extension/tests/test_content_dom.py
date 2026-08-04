from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CONTENT = (ROOT / "content.js").read_text(encoding="utf-8")

HTML = """
<!doctype html><html><body>
<form id="composer"><textarea id="prompt-textarea"></textarea><button type="button" data-testid="send-button">Send</button></form>
<div id="messages"></div>
<script>
window.__SAT2_RELAY_TEST__ = true;
window.__listener = null;
window.chrome = {runtime:{onMessage:{addListener(fn){window.__listener=fn;}}}};
const editor=document.getElementById('prompt-textarea');
editor.addEventListener('input',()=>{
  const old=document.getElementById('apps'); if(old)old.remove();
  if(editor.value==='@GitHub'){
    const list=document.createElement('div'); list.id='apps'; list.setAttribute('role','listbox');
    const option=document.createElement('button'); option.type='button'; option.setAttribute('role','option'); option.textContent='GitHub';
    option.onclick=()=>{list.remove(); editor.value=''; const chip=document.createElement('span');chip.textContent='GitHub';document.getElementById('composer').prepend(chip);};
    list.append(option); document.body.append(list);
  }
});
document.querySelector('[data-testid="send-button"]').onclick=()=>{
  const m=document.createElement('div');m.setAttribute('data-message-author-role','user');m.textContent=editor.value;document.getElementById('messages').append(m);editor.value='';
};
window.invoke=(message)=>new Promise((resolve)=>window.__listener(message,null,resolve));
</script></body></html>
"""


async def run_case():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(HTML)
        await page.add_script_tag(content=CONTENT)
        health = await page.evaluate("invoke({type:'SAT2_HEALTH'})")
        assert health["composerReady"] is True
        body = "@GitHub\nSAT2 Execution Capsule\nRelay delivery: E-1:mentor\nTask: WP-B3"
        result = await page.evaluate("body => invoke({type:'SAT2_SEND',text:body,relayId:'E-1:mentor',requiredApps:['GitHub'],strictApps:true})", body)
        assert result["ok"] is True
        assert result["connectorAttached"] is True
        assert "Relay delivery: E-1:mentor" in await page.locator("#messages").inner_text()
        duplicate = await page.evaluate("body => invoke({type:'SAT2_SEND',text:body,relayId:'E-1:mentor',requiredApps:['GitHub'],strictApps:true})", body)
        assert duplicate["duplicate"] is True
        # Re-inject the content script to simulate a content-script restart; transcript marker still deduplicates.
        await page.add_script_tag(content=CONTENT)
        duplicate_after_restart = await page.evaluate("body => invoke({type:'SAT2_SEND',text:body,relayId:'E-1:mentor',requiredApps:['GitHub'],strictApps:true})", body)
        assert duplicate_after_restart["duplicate"] is True
        await page.evaluate("""() => {
          const m=document.createElement('div');
          m.setAttribute('data-message-author-role','assistant');
          m.setAttribute('data-message-id','assistant-decision-1');
          m.innerHTML='<p>SAT2_RELAY_DECISION</p><pre><code>{"delivery_token":"token-1234567890123456","decision":"WORKER_CHECKPOINT","summary":"ready for review"}</code></pre>';
          document.getElementById('messages').append(m);
        }""")
        decision = await page.evaluate("invoke({type:'SAT2_FIND_DECISION',deliveryToken:'token-1234567890123456',force:false})")
        assert decision["ok"] is True
        assert decision["decision"]["decision"] == "WORKER_CHECKPOINT"
        assert decision["assistantMessageId"] == "assistant-decision-1"
        wrong = await page.evaluate("invoke({type:'SAT2_FIND_DECISION',deliveryToken:'different-token-123456',force:false})")
        assert wrong["decision"] is None
        await page.evaluate("document.body.insertAdjacentHTML('beforeend','<button data-testid=stop-button>Stop</button>')")
        busy = await page.evaluate("invoke({type:'SAT2_SEND',text:'x',relayId:'E-2',requiredApps:[],strictApps:false})")
        assert busy["ok"] is False and busy["code"] == "SESSION_BUSY"
        await browser.close()


def test_content_dom():
    asyncio.run(run_case())
