from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_auto_off_prevents_automatic_decision_capture():
    source = (ROOT / "background.js").read_text(encoding="utf-8")
    guard = source.index("if (!settings.autoEnabled && !force)")
    capture = source.index("const decisionsBefore = await captureDecisions(settings)")
    assert guard < capture
    assert "onStartup.addListener(() => configureAlarm().then(() => runCycle())" in source


def test_reply_notification_channel_is_independent():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["background"]["service_worker"] == "service_worker.js"
    scripts = manifest["content_scripts"][0]["js"]
    assert "reply_watcher.js" in scripts
    worker = (ROOT / "service_worker.js").read_text(encoding="utf-8")
    assert 'import "./background.js";' in worker
    assert 'import "./reply_notifications.js";' in worker
    watcher = (ROOT / "reply_watcher.js").read_text(encoding="utf-8")
    notifications = (ROOT / "reply_notifications.js").read_text(encoding="utf-8")
    assert 'PORT_NAME = "SAT2_SESSION_REPLY_COMPLETE"' in watcher
    assert 'PORT_NAME = "SAT2_SESSION_REPLY_COMPLETE"' in notifications
    assert "session_not_bound" in notifications
    assert "replyNotificationHistory" in notifications
