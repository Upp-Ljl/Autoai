import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_permissions_are_scoped():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "background.js"
    assert "https://chatgpt.com/*" in manifest["host_permissions"]
    assert "http://127.0.0.1/*" in manifest["host_permissions"]
    assert "https://api.github.com/*" not in manifest["host_permissions"]
    assert "webRequest" not in manifest["permissions"]
