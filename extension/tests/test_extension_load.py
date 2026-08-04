from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_chromium_pack_accepts_extension(tmp_path):
    copy = tmp_path / "extension"
    shutil.copytree(ROOT, copy)
    chromium = next((candidate for candidate in ("chromium", "chromium-browser", "google-chrome", "chrome") if shutil.which(candidate)), None)
    if chromium is None:
        pytest.skip("a Chromium executable is not available on PATH")
    result = subprocess.run(
        [chromium, "--no-sandbox", f"--pack-extension={copy}"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "extension.crx").exists()
    assert (tmp_path / "extension.pem").exists()
