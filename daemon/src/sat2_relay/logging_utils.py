from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonLineHandler(logging.Handler):
    """Structured JSONL logging with bounded local rotation."""

    def __init__(self, path: Path, max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5):
        super().__init__()
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current = self.path.stat().st_size
        except FileNotFoundError:
            current = 0
        if current + incoming_bytes <= self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                source.replace(target)
        if self.path.exists():
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    def emit(self, record: logging.LogRecord) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("code", "task_id", "pr_number", "stage", "delivery_id", "event_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = logging.Formatter().formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        encoded_size = len(line.encode("utf-8"))
        try:
            self._rotate_if_needed(encoded_size)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            self.handleError(record)


def configure_logging(path: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(handler, JsonLineHandler) and handler.path == path for handler in root.handlers):
        root.addHandler(JsonLineHandler(path))
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, JsonLineHandler) for handler in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)
