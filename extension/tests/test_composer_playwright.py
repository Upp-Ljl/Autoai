from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


CONTENT = Path(__file__).resolve().parents[1] / "content.js"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content(
            """
            <!doctype html><html><body>
              <form id="composer-form">
                <div id="prompt-textarea" contenteditable="true" role="textbox"></div>
                <button type="button" data-testid="send-button" disabled>Send</button>
              </form>
              <main id="transcript"></main>
              <script>
                globalThis.__SAT2_RELAY_TEST__ = true;
                globalThis.__sat2Listener = null;
                globalThis.chrome = {runtime:{onMessage:{addListener(fn){globalThis.__sat2Listener=fn;}}}};
                const editor=document.getElementById('prompt-textarea');
                const send=document.querySelector('[data-testid="send-button"]');
                editor.addEventListener('input',()=>{send.disabled=!editor.textContent.trim();});
                send.addEventListener('click',()=>{
                  const message=document.createElement('div');
                  message.dataset.messageAuthorRole='user';
                  message.textContent=editor.textContent;
                  document.getElementById('transcript').append(message);
                  editor.textContent='';
                  send.disabled=true;
                });
              </script>
            </body></html>
            """
        )
        page.add_script_tag(path=str(CONTENT))
        health = page.evaluate("""new Promise((resolve)=>globalThis.__sat2Listener({type:'SAT2_HEALTH'},null,resolve))""")
        assert health["ok"] is True
        assert health["composerReady"] is True
        result = page.evaluate(
            """new Promise((resolve)=>globalThis.__sat2Listener({
              type:'SAT2_SEND',
              text:'SAT2 test capsule\\n\\nRelay delivery: event-1:S3',
              relayId:'event-1:S3',
              requiredApps:[],
              connectorMode:'session_verified'
            },null,resolve))"""
        )
        assert result["ok"] is True, result
        assert result["marker"] == "Relay delivery: event-1:S3"
        count = page.locator('[data-message-author-role="user"]', has_text="Relay delivery: event-1:S3").count()
        assert count == 1
        duplicate = page.evaluate(
            """new Promise((resolve)=>globalThis.__sat2Listener({
              type:'SAT2_SEND',
              text:'SAT2 test capsule\\n\\nRelay delivery: event-1:S3',
              relayId:'event-1:S3',
              requiredApps:[],
              connectorMode:'session_verified'
            },null,resolve))"""
        )
        assert duplicate["ok"] is True and duplicate["duplicate"] is True
        assert page.locator('[data-message-author-role="user"]', has_text="Relay delivery: event-1:S3").count() == 1
        browser.close()
    print("composer-marker-e2e-ok")


if __name__ == "__main__":
    main()
