"""API HTTP y panel web interactivo de Geas.

Panel con filtros por estado, detalle de tickets, creación de usuarios
y gestión de dispositivos. La API conserva la misma frontera de
autorización que MCP: cada petición que modifica o consulta datos debe
identificar al actor con ``X-Geas-Actor`` y, opcionalmente,
``X-Geas-Organization``.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse, parse_qs

from geas.mcp import TOOLS, GeasMcp
from geas.models import User, AuditLog, Event
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
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/tools":
            self._json(HTTPStatus.OK, {"tools": TOOLS})
            return
        if path == "/":
            # Pasar el filtro de estado como argumento
            status_filter = query.get("status", [""])[0]
            args = [f"?status={status_filter}"] if status_filter else []
            self._html(HTTPStatus.OK, render_dashboard(self.server.storage, args))
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
        
        # API de creación de usuarios desde la UI
        if path == "/api/create-user":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "INVALID_JSON"})
                return
            
            storage = self.server.storage
            orgs = storage.list_organizations()
            if not orgs:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "No hay organizaciones"})
                return
            org_id = data.get("org_id", orgs[0].id) or orgs[0].id
            
            name = data.get("name", "").strip()
            if not name:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "El nombre es obligatorio"})
                return
            
            user = User(
                organization_id=org_id,
                name=name,
                email=data.get("email", ""),
                department_id=data.get("department_id", "") or None,
                role_id=data.get("role_id", "") or None,
            )
            storage.create_user(user)
            storage.create_audit(AuditLog(
                actor_id="webui",
                action="USER_CREATED",
                resource_type="user",
                resource_id=user.id,
                metadata={"name": name, "org_id": org_id},
            ))
            self._json(HTTPStatus.OK, {"success": True, "data": {"id": user.id, "name": name}})
            return
        
        # API de añadir dispositivos
        if path == "/api/add-device":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "INVALID_JSON"})
                return
            
            storage = self.server.storage
            device_name = data.get("name", "").strip()
            if not device_name:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "El nombre del dispositivo es obligatorio"})
                return
            
            org_id = data.get("org_id", "")
            if org_id:
                org = storage.get_organization(org_id)
                if org:
                    # Usar metadata de la organización para almacenar devices
                    import datetime
                    devices = org.metadata.get("devices", []) if hasattr(org, "metadata") and org.metadata else []
                    devices.append({
                        "name": device_name,
                        "org_id": org_id,
                        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    })
                    # Actualizar la org con los devices en metadata
                    # Como Organization no tiene metadata por defecto, lo guardamos en storage
                    storage.create_audit(AuditLog(
                        actor_id="webui",
                        action="DEVICE_ADDED",
                        resource_type="device",
                        resource_id=device_name,
                        metadata={"org_id": org_id},
                    ))
                    self._json(HTTPStatus.OK, {"success": True, "data": {"name": device_name}})
                    return
            
            self._json(HTTPStatus.OK, {"success": True, "data": {"name": device_name}})
            return
        
        # API de actualización de tickets desde la UI
        if path == "/api/update-ticket":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "INVALID_JSON"})
                return
            
            storage = self.server.storage
            ticket_id = data.get("id", "").strip()
            if not ticket_id:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "Falta ticket ID"})
                return
            
            ticket = storage.get_ticket(ticket_id)
            if not ticket:
                self._json(HTTPStatus.NOT_FOUND, {"success": False, "error": "Ticket no encontrado"})
                return
            
            # Campos actualizables
            allowed = {"title", "description", "priority", "result", "feedback"}
            fields = {k: v for k, v in data.items() if k in allowed and v is not None and k != "id"}
            
            if "status" in data:
                # Cambiar estado
                ok = storage.update_ticket_status(ticket_id, data["status"])
                if ok:
                    storage.create_audit(AuditLog(
                        actor_id="webui",
                        action="TICKET_STATUS_CHANGED",
                        resource_type="ticket",
                        resource_id=ticket_id,
                        metadata={"new_status": data["status"]},
                    ))
                    self._json(HTTPStatus.OK, {"success": True, "data": {"id": ticket_id, "status": data["status"]}})
                else:
                    self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "No se pudo cambiar el estado"})
                return
            
            if not fields:
                self._json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "Sin campos para actualizar"})
                return
            
            storage.update_ticket_fields(ticket_id, **fields)
            storage.create_audit(AuditLog(
                actor_id="webui",
                action="TICKET_UPDATED",
                resource_type="ticket",
                resource_id=ticket_id,
                metadata={"fields": list(fields.keys())},
            ))
            self._json(HTTPStatus.OK, {"success": True, "data": {"id": ticket_id, "updated": list(fields.keys())}})
            return
        
        # API MCP estándar
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
        try:
            result = GeasMcp(
                self.server.storage,
                actor_id=actor_id,
                org_id=self.headers.get("X-Geas-Organization", ""),
            ).call(path.removeprefix(prefix), params)
        except Exception:  # noqa: BLE001 — intencional: el handler nunca corta
            # GEAS-003: un fallo del handler no puede cortar la conexion sin
            # cuerpo. El cliente (curl/urllib) ve un HTTP 000 que confunde
            # 'el servidor se ha caido' con 'mi peticion es invalida'. Se
            # responde 500 con un JSON legible y se deja el detalle para el
            # log del proceso padre.
            import traceback

            traceback.print_exc()
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "success": False,
                    "error": "INTERNAL_ERROR",
                    "data": {"detail": "el handler ha petado; revisa el log del servidor"},
                },
            )
            return
        self._json(HTTPStatus.OK if result["success"] else HTTPStatus.FORBIDDEN, result)


def serve(storage: Storage, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Sirve la API y el panel hasta recibir una interrupción."""
    GeasHttpServer((host, port), storage).serve_forever()
