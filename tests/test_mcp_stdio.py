from __future__ import annotations

import json

from geas.mcp import GeasMcp
from geas.mcp_stdio import handle_request
from geas.models import DEFAULT_PERMISSIONS, Agent, Organization, Role
from geas.storage import Storage


def _handler(tmp_path) -> GeasMcp:
    storage = Storage(tmp_path / "mcp.db")
    org = Organization(name="Test")
    storage.create_organization(org)
    role = Role(organization_id=org.id, name="admin", permissions=DEFAULT_PERMISSIONS)
    storage.create_role(role)
    agent = Agent(
        organization_id=org.id,
        name="agent",
        provider="test",
        model="test",
        role_id=role.id,
    )
    storage.create_agent(agent)
    return GeasMcp(storage, actor_id=agent.id, org_id=org.id)


def test_initialize_and_list_tools(tmp_path):
    handler = _handler(tmp_path)
    initialized = handle_request(
        handler,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert initialized["result"]["capabilities"] == {"tools": {}}

    tools = handle_request(
        handler,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "get_available_tasks" in names


def test_call_tool_returns_mcp_content(tmp_path):
    handler = _handler(tmp_path)
    response = handle_request(
        handler,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "create_ticket", "arguments": {"title": "MCP"}},
        },
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["success"] is True
