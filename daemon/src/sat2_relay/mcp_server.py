from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import DEFAULT_CONFIG_PATH, load_local_config


TOOLS = [
    {
        "name": "relay_status",
        "description": "Read SAT2 Relay health, tasks, deliveries, heartbeats, alerts, and recent comment-processing outcomes.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "relay_doctor",
        "description": "Run the exact deep doctor checks used by the local daemon, including repository config and every task file at its resolved ref.",
        "inputSchema": {
            "type": "object",
            "properties": {"deep": {"type": "boolean", "default": True}},
            "additionalProperties": False,
        },
    },
    {
        "name": "relay_poll_once",
        "description": "Trigger one immediate GitHub poll and Relay state-machine pass.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "relay_reload_credentials",
        "description": "Reload the local credential store into the daemon without restarting it. Secret values are never returned.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "relay_replay_comment",
        "description": "Mark one previously retryable/failed GitHub control comment for replay on the next poll.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "minimum": 1},
                "comment_id": {"type": "integer", "minimum": 1},
            },
            "required": ["pr_number", "comment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "relay_resolve_alert",
        "description": "Mark a local Relay alert as resolved after its cause has been verified and corrected.",
        "inputSchema": {
            "type": "object",
            "properties": {"alert_id": {"type": "integer", "minimum": 1}},
            "required": ["alert_id"],
            "additionalProperties": False,
        },
    },
]


class LocalRelayClient:
    def __init__(self, config_path: Path):
        self.config = load_local_config(config_path)
        self.base_url = f"http://{self.config.host}:{self.config.port}"
        self.headers = {"X-SAT2-Relay-Token": self.config.api_token}

    def request(self, method: str, path: str) -> Any:
        with httpx.Client(base_url=self.base_url, headers=self.headers, timeout=30) as client:
            response = client.request(method, path)
            response.raise_for_status()
            return response.json() if response.content else None

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name == "relay_status":
            return self.request("GET", "/api/v2/status")
        if name == "relay_doctor":
            deep = str(bool(args.get("deep", True))).lower()
            return self.request("GET", f"/api/v2/doctor?deep={deep}")
        if name == "relay_poll_once":
            return self.request("POST", "/api/v2/control/poll")
        if name == "relay_reload_credentials":
            return self.request("POST", "/api/v2/control/reload-credentials")
        if name == "relay_replay_comment":
            return self.request("POST", f"/api/v2/comments/{int(args['pr_number'])}/{int(args['comment_id'])}/replay")
        if name == "relay_resolve_alert":
            return self.request("POST", f"/api/v2/alerts/{int(args['alert_id'])}/resolve")
        raise ValueError(f"unknown tool: {name}")


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def handle(client: LocalRelayClient, message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        requested = str((message.get("params") or {}).get("protocolVersion") or "2025-11-25")
        supported = requested if requested in {"2025-11-25", "2025-06-18", "2024-11-05"} else "2025-11-25"
        return response(
            request_id,
            {
                "protocolVersion": supported,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sat2-relay-local-agent", "version": "2.2.0"},
                "instructions": "This server exposes only bounded local Relay diagnostics and control operations. It never reveals GitHub tokens and cannot merge, dispatch workflows, or modify scientific evidence.",
            },
        )
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            value = client.call(name, args)
            return response(
                request_id,
                {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2, default=str)}], "isError": False},
            )
        except Exception as exc:  # noqa: BLE001
            return response(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
    if request_id is None:
        return None
    return response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sat2-relay-mcp")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    client = LocalRelayClient(args.config)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            result = handle(client, message)
        except Exception as exc:  # noqa: BLE001
            result = response(None, error={"code": -32700, "message": str(exc)})
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
