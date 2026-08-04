from __future__ import annotations

from sat2_relay.mcp_server import handle


class FakeClient:
    def call(self, name, args):
        return {"name": name, "args": args, "secret": None}


def test_mcp_initialize_and_tools():
    client = FakeClient()
    initialized = handle(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    tools = handle(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "relay_doctor" in names
    assert "relay_poll_once" in names
    assert all("token" not in name for name in names)


def test_mcp_tool_call_is_bounded():
    client = FakeClient()
    result = handle(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "relay_status", "arguments": {}}})
    assert result["result"]["isError"] is False
    assert "relay_status" in result["result"]["content"][0]["text"]
