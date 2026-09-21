"""Servidor MCP por stdio para conectar clientes de agentes con Geas.

El transporte stdio de MCP usa un mensaje JSON-RPC por línea. No depende de
un SDK concreto: así Codex, Claude Code u OpenCode pueden iniciarlo con
``geas mcp serve`` y negociar ``initialize``, ``tools/list`` y ``tools/call``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from geas.mcp import TOOLS, GeasMcp
from geas.storage import Storage


def _tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    properties = {name: {"type": "string"} for name in tool.get("params", [])}
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": {"type": "object", "properties": properties},
    }


def handle_request(handler: GeasMcp, request: dict[str, Any]) -> dict[str, Any] | None:
    """Procesa una petición JSON-RPC MCP sin acoplarse al stream."""
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None:
        return None

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "geas", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [_tool_schema(tool) for tool in TOOLS]}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        call_result = handler.call(name, params.get("arguments", {}))
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(call_result, ensure_ascii=False),
                }
            ],
            "isError": not call_result["success"],
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Método no soportado: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(storage: Storage, actor_id: str, org_id: str = "") -> None:
    """Atiende MCP sobre stdin/stdout hasta EOF."""
    handler = GeasMcp(storage, actor_id=actor_id, org_id=org_id)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_request(handler, request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except (json.JSONDecodeError, TypeError) as exc:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": str(exc)},
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
