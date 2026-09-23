"""
tests.test_webui — Panel web de estado (§42, MVP 2).

La Web UI es de solo lectura y responde a «¿qué está pasando?»: orgs,
tickets por estado, locks activos, eventos y auditoría. El detalle de
ticket reconstruye la trazabilidad §29: commits before/after, recursos
con locks, dependencias, ejecuciones (§28) y tests (§20).
"""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from geas.models import (
    DEFAULT_PERMISSIONS,
    Agent,
    Event,
    Execution,
    Organization,
    Role,
    Ticket,
    TicketStatus,
)
from geas.server import GeasHttpServer
from geas.storage import Storage
from geas.webui import render_dashboard, render_ticket


def _seed(tmp_path) -> tuple[Storage, Organization, Agent]:
    storage = Storage(tmp_path / "webui.db")
    org = Organization(name="YouTube", description="Vídeos")
    storage.create_organization(org)
    role = Role(organization_id=org.id, name="admin", permissions=DEFAULT_PERMISSIONS)
    storage.create_role(role)
    actor = Agent(
        organization_id=org.id, name="agent", provider="x", model="y", role_id=role.id
    )
    storage.create_agent(actor)

    ticket = Ticket(
        organization_id=org.id,
        title="Implementar chat",
        description="El cliente pide chat en vivo",
        creator_id=actor.id,
        assigned_actor_id=actor.id,
        status=TicketStatus.IN_PROGRESS,
        priority=2,
        branch="geas/YT-104",
        commit_before="a82f91c",
        commit_after="b71c4de",
    )
    storage.create_ticket(ticket)
    storage.create_event(
        Event(
            organization_id=org.id,
            event_type="TICKET_STARTED",
            actor_id=actor.id,
            resource_type="ticket",
            resource_id=ticket.id,
        )
    )
    storage.create_execution(
        Execution(
            ticket_id=ticket.id,
            actor_id=actor.id,
            provider="anthropic",
            model="claude",
            tokens_input=100,
            tokens_output=50,
            cost=0.01,
            result="success",
        )
    )
    return storage, org, actor


def _server(storage: Storage) -> tuple[GeasHttpServer, threading.Thread]:
    server = GeasHttpServer(("127.0.0.1", 0), storage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class TestRenderDashboard:
    def test_sin_organizaciones(self, tmp_path):
        storage = Storage(tmp_path / "empty.db")
        page = render_dashboard(storage)
        assert "Geas" in page
        assert "Sin organizaciones" in page

    def test_secciones_por_org(self, tmp_path):
        storage, org, _actor = _seed(tmp_path)
        page = render_dashboard(storage)
        assert org.name in page
        assert "IN_PROGRESS" in page
        assert "geas/YT-104" in page
        assert "TICKET_STARTED" in page  # evento
        assert "a82f91c" in page  # commit_before en la fila del ticket

    def test_escapa_html(self, tmp_path):
        storage = Storage(tmp_path / "xss.db")
        org = Organization(name="<script>alert(1)</script>")
        storage.create_organization(org)
        page = render_dashboard(storage)
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page


class TestRenderTicket:
    def test_detalle_trazabilidad(self, tmp_path):
        storage, _org, _actor = _seed(tmp_path)
        ticket = storage.list_tickets(_org.id)[0]
        page = render_ticket(storage, ticket.id)
        assert page is not None
        assert ticket.id in page
        assert "Implementar chat" in page
        assert "a82f91c" in page and "b71c4de" in page  # commits §10/§29
        assert "geas/YT-104" in page  # branch
        assert "anthropic" in page and "claude" in page  # ejecución §28
        assert "Sin resultados de tests" in page  # §20 sin resultados

    def test_no_existe_devuelve_none(self, tmp_path):
        storage, _org, _actor = _seed(tmp_path)
        assert render_ticket(storage, "NO-EXISTE") is None


class TestServerRoutes:
    def test_dashboard_servido(self, tmp_path):
        storage, _org, _actor = _seed(tmp_path)
        server, thread = _server(storage)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            body = urlopen(base).read().decode()
            assert "Geas" in body
            assert "YouTube" in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ticket_detail_servido(self, tmp_path):
        storage, org, _actor = _seed(tmp_path)
        ticket = storage.list_tickets(org.id)[0]
        server, thread = _server(storage)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            body = urlopen(f"{base}/ticket/{ticket.id}").read().decode()
            assert "Implementar chat" in body
            assert "anthropic" in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ticket_404(self, tmp_path):
        storage, _org, _actor = _seed(tmp_path)
        server, thread = _server(storage)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with pytest.raises(HTTPError) as excinfo:
                urlopen(f"{base}/ticket/NO-EXISTE")
            assert excinfo.value.code == 404
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_api_sigue_funcionando(self, tmp_path):
        """La UI no rompe la API MCP del §26."""
        storage, org, actor = _seed(tmp_path)
        server, thread = _server(storage)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            from urllib.request import Request

            request = Request(
                f"{base}/api/tools/get_available_tasks",
                data=b"{}",
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
            thread.join(timeout=2)