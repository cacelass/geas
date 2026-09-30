from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from geas.models import DEFAULT_PERMISSIONS, Agent, Organization, Role
from geas.server import GeasHttpServer
from geas.storage import Storage


def _server(tmp_path):
    storage = Storage(tmp_path / "server.db")
    org = Organization(name="Test")
    storage.create_organization(org)
    role = Role(organization_id=org.id, name="admin", permissions=DEFAULT_PERMISSIONS)
    storage.create_role(role)
    actor = Agent(
        organization_id=org.id,
        name="agent",
        provider="test",
        model="test",
        role_id=role.id,
    )
    storage.create_agent(actor)
    server = GeasHttpServer(("127.0.0.1", 0), storage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, org, actor


def test_health_and_dashboard(tmp_path):
    server, _thread, _org, _actor = _server(tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert json.load(urlopen(f"{base}/health")) == {"status": "ok"}
        assert b"Geas" in urlopen(base).read()
    finally:
        server.shutdown()
        server.server_close()


def test_tool_api_requires_actor_and_dispatches(tmp_path):
    server, _thread, org, actor = _server(tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(f"{base}/api/tools/create_ticket", data=b"{}", method="POST")
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("La API aceptó una petición sin actor")

        request = Request(
            f"{base}/api/tools/create_ticket",
            data=b'{"title": "Desde HTTP"}',
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Geas-Actor": actor.id,
                "X-Geas-Organization": org.id,
            },
        )
        result = json.load(urlopen(request))
        assert result["success"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_handler_returns_json_on_internal_error(tmp_path):
    """GEAS-003: un fallo interno responde 500 JSON, nunca corta la conexion.

    El bug era un FOREIGN KEY constraint failed en start_ticket que subia al
    handler y el cliente veia HTTP 000 (conexion cortada sin cuerpo). Aqui se
    fuerza un error interno (BD cerrada) y se comprueba que la respuesta es
    JSON legible, no una conexion cortada.
    """
    server, _thread, org, actor = _server(tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        # Primero creamos un ticket por HTTP (BD viva): su id servira para
        # llamar a get_ticket despues de cerrar la BD y provocar el SQL
        # que falla por dentro.
        create = Request(
            f"{base}/api/tools/create_ticket",
            data=b'{"title": "Para romper"}',
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Geas-Actor": actor.id,
                "X-Geas-Organization": org.id,
            },
        )
        created = json.load(urlopen(create))
        assert created["success"] is True
        ticket_id = created["data"]["id"]

        # Cerramos la BD del servidor: cualquier tool MCP que haga SQL
        # reventara dentro del handler.
        server.storage.close()

        request = Request(
            f"{base}/api/tools/get_ticket",
            data=('{"ticket_id": "' + ticket_id + '"}').encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Geas-Actor": actor.id,
                "X-Geas-Organization": org.id,
            },
        )
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 500
            body = json.loads(error.read())
            assert body.get("success") is False
            assert body.get("error") == "INTERNAL_ERROR"
        else:
            raise AssertionError("La API debía responder 500, no 200")
    finally:
        server.shutdown()
        server.server_close()
