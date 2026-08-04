from __future__ import annotations

import logging

from sat2_relay.logging_utils import JsonLineHandler


def test_json_log_rotation_is_bounded(tmp_path):
    path = tmp_path / "relay.jsonl"
    handler = JsonLineHandler(path, max_bytes=100, backup_count=2)
    logger = logging.getLogger("sat2-test-rotation")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    for index in range(20):
        logger.info("record-%s-%s", index, "x" * 30)
    assert path.exists()
    assert path.with_name("relay.jsonl.1").exists()
    assert not path.with_name("relay.jsonl.3").exists()
