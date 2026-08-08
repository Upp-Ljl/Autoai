from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
WATCHER = (ROOT / "reply_watcher.js").read_text(encoding="utf-8")

HTML = """
<!doctype html><html><body>
<div id="messages">
  <div data-message-author-role="assistant" data-message-id="old-answer">old answer</div>
</div>
<script>
window.__ports=[];
window.chrome={runtime:{connect({name}){
  const port={name,messages:[],postMessage(value){this.messages.push(value);window.__ports.push({name,value});},disconnect(){}};
  return port;
}}};
</script>
</body></html>
"""


async def run_case():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(HTML)
        await page.add_script_tag(content=WATCHER)
        await page.wait_for_timeout(200)
        assert await page.evaluate("window.__ports.length") == 0, "historical reply must not notify on page load"

        await page.evaluate("""() => {
          const stop=document.createElement('button');
          stop.setAttribute('data-testid','stop-button');
          stop.textContent='Stop';
          document.body.append(stop);
          const m=document.createElement('div');
          m.setAttribute('data-message-author-role','assistant');
          m.setAttribute('data-message-id','new-answer');
          m.textContent='streaming';
          document.getElementById('messages').append(m);
        }""")
        await page.wait_for_timeout(150)
        await page.evaluate("""() => {
          document.querySelector('[data-testid="stop-button"]').remove();
          document.querySelector('[data-message-id="new-answer"]').textContent='completed reply';
        }""")
        await page.wait_for_timeout(1000)
        events = await page.evaluate("window.__ports")
        assert len(events) == 1
        assert events[0]["name"] == "SAT2_SESSION_REPLY_COMPLETE"
        assert events[0]["value"]["messageId"] == "new-answer"
        assert events[0]["value"]["assistantText"] == "completed reply"

        await page.wait_for_timeout(3000)
        assert await page.evaluate("window.__ports.length") == 1, "same reply must notify only once"
        await browser.close()


def test_reply_watcher():
    asyncio.run(run_case())
