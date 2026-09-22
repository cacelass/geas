"""API HTTP y panel web mínimo de Geas.

La API conserva la misma frontera de autorización que MCP: cada petición que
modifica o consulta datos debe identificar al actor con ``X-Geas-Actor`` y,
opcionalmente, ``X-Geas-Organization``. En despliegues reales el proxy de
identidad debe inyectar esos encabezados tras autenticar al usuario.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from geas.mcp import TOOLS, GeasMcp
from geas.storage import Storage
from geas.webui import render_dashboard, render_ticket


class GeasHttpServer(HTTPServer):
    storage: Storage

    def __init__(self, address: tuple[str, int], storage: Storage):
        super().__init__(address, GeasHttpHandler)
        self.storage = storage


class GeasHttpHandler(BaseHTTPRequestHandler):
    server: GeasHttpServer

    def log_message(self, _format: str, *_args: object) -> None:
        """El despliegue decide el logging mediante el proxy o el proceso padre."""

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: HTTPStatus, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/tools":
            self._json(HTTPStatus.OK, {"tools": TOOLS})
            return
        if path == "/":
            self._html(HTTPStatus.OK, render_dashboard(self.server.storage))
            return
        if path.startswith("/ticket/"):
            ticket_id = path.removeprefix("/ticket/")
            page = render_ticket(self.server.storage, ticket_id)
            if page is not None:
                self._html(HTTPStatus.OK, page)
                return
            self._html(HTTPStatus.NOT_FOUND, "<h1>404</h1><p>Ticket no encontrado.</p>")
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:
        prefix = "/api/tools/"
        path = urlparse(self.path).path
        if not path.startswith(prefix):
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
            return
        actor_id = self.headers.get("X-Geas-Actor", "")
        if not actor_id:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "ACTOR_REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            params = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "INVALID_JSON"})
            return
        result = GeasMcp(
            self.server.storage,
            actor_id=actor_id,
            org_id=self.headers.get("X-Geas-Organization", ""),
        ).call(path.removeprefix(prefix), params)
        self._json(HTTPStatus.OK if result["success"] else HTTPStatus.FORBIDDEN, result)


def serve(storage: Storage, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Sirve la API y el panel hasta recibir una interrupción."""
    GeasHttpServer((host, port), storage).serve_forever()
