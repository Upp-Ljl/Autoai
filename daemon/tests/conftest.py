from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sat2_relay.config import initialize_config, load_local_config


@pytest.fixture
def local_config(tmp_path: Path):
    path = tmp_path / "config.yml"
    initialize_config(path)
    raw = yaml.safe_load(path.read_text())
    raw["server"]["api_token"] = "test-local-token-0123456789abcdef"
    raw["storage"]["database"] = str(tmp_path / "state.sqlite3")
    raw["github"]["repository_config_ref"] = "test-ref"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return load_local_config(path)
