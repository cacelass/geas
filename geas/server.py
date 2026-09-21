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

INDEX_HTML = """<!doctype html>
<html lang="es"><meta charset="utf-8"><title>Geas</title>
<style>body{font:16px system-ui;max-width:800px;margin:3rem auto}input,button{padding:.5rem;margin:.2rem}pre{background:#111;color:#ddd;padding:1rem;overflow:auto}</style>
<h1>Geas · Orchestrator</h1>
<p>Consulta las tareas que puede ejecutar un actor.</p>
<label>Actor <input id="actor" autocomplete="off"></label>
<label>Organización <input id="org" autocomplete="off"></label>
<button onclick="tasks()">Ver tareas disponibles</button>
<pre id="output">Listo.</pre>
<script>
async function tasks() {
  const output = document.querySelector('#output');
  const response = await fetch('/api/tools/get_available_tasks', {
    method: 'POST', headers: {'Content-Type':'application/json',
      'X-Geas-Actor': actor.value, 'X-Geas-Organization': org.value}, body: '{}'});
  output.textContent = JSON.stringify(await response.json(), null, 2);
}
</script></html>"""


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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/tools":
            self._json(HTTPStatus.OK, {"tools": TOOLS})
            return
        if path == "/":
            body = INDEX_HTML.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
