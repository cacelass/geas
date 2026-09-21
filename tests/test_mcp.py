"""
tests.test_mcp — Tests del MCP de Geas (spec §26).

Cubre el flujo completo: crear ticket, dependencias, lock de recursos,
bloqueo de otro ticket, completar y liberar.
"""

from __future__ import annotations

import pytest

from geas.mcp import GeasMcp
from geas.models import (
    Organization,
    Repository,
    Resource,
)
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def org(storage):
    o = Organization(name="Google")
    storage.create_organization(o)
    return o


@pytest.fixture
def repo(storage, org):
    r = Repository(organization_id=org.id, name="youtube-backend")
    storage.create_repository(r)
    return r


@pytest.fixture
def mcp(storage, org):
    return GeasMcp(storage, actor_id="agent-A", org_id=org.id)


class TestTicketsViaMcp:
    def test_create_ticket(self, mcp, org):
        r = mcp.create_ticket(title="Implementar Chat.py", priority=2)
        assert r["success"] is True
        ticket_id = r["data"]["id"]
        assert mcp.get_ticket(ticket_id)["data"]["status"] == "FREE"

    def test_create_ticket_with_dependencies(self, mcp, org):
        r1 = mcp.create_ticket(title="Base")
        r2 = mcp.create_ticket(title="Depende de base", dependencies=[r1["data"]["id"]])
        assert r2["success"] is True
        deps = mcp.get_dependencies(r2["data"]["id"])
        assert len(deps["data"]["depends_on"]) == 1
        assert deps["data"]["resolved"] is False

    def test_complete_dependency_unblocks(self, mcp, org):
        r1 = mcp.create_ticket(title="Base")
        r2 = mcp.create_ticket(title="Depende", dependencies=[r1["data"]["id"]])
        t2_id = r2["data"]["id"]

        # No puede empezar: deps sin resolver
        r = mcp.start_ticket(t2_id)
        assert r["success"] is False
        assert "DEPENDENCIES" in r["error"]

        # Completar la base → ya se puede
        mcp.complete_ticket(r1["data"]["id"], commit_after="abc")
        r = mcp.start_ticket(t2_id)
        assert r["success"] is True

    def test_bad_dependency_rejected(self, mcp, org):
        r = mcp.create_ticket(title="Mal", dependencies=["no-existe"])
        assert r["success"] is False


class TestLockViaMcp:
    """La demo de la spec §13/§41 a través de MCP."""

    def test_resource_lock_blocks_other_ticket(self, mcp, storage, org, repo):
        # Recurso Chat.py
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)

        # YT-104 (agente A)
        r104 = mcp.create_ticket(
            title="Implementar Chat", resources=[res.id], priority=2
        )
        t104 = r104["data"]["id"]

        # YT-105 (agente B)
        mcp_b = GeasMcp(storage, actor_id="agent-B", org_id=org.id)
        r105 = mcp_b.create_ticket(title="Refactor Chat", resources=[res.id])
        t105 = r105["data"]["id"]

        # A empieza → lock adquirido
        assert mcp.start_ticket(t104)["success"] is True
        lock = storage.get_lock_for_resource(res.id)
        assert lock is not None
        assert lock.ticket_id == t104

        # B no puede empezar
        r = mcp_b.start_ticket(t105)
        assert r["success"] is False
        assert "RESOURCE_UNAVAILABLE" in r["error"]

        # available_tasks: B no ve YT-105 como disponible
        tasks = mcp_b.get_available_tasks()["data"]["available_tasks"]
        ids = [t["id"] for t in tasks]
        assert t105 not in ids

        # A completa → release locks → B ya puede
        mcp.complete_ticket(t104, commit_after="b71c4de")
        assert storage.get_lock_for_resource(res.id) is None
        assert mcp_b.start_ticket(t105)["success"] is True


class TestExecutionsViaMcp:
    def test_report_execution(self, mcp, org):
        r = mcp.create_ticket(title="Con ejecución")
        ticket_id = r["data"]["id"]
        r = mcp.report_execution(
            ticket_id,
            provider="anthropic",
            model="claude-4",
            model_version="1.0",
            tokens_input=1000,
            tokens_output=500,
            cost=0.012,
            tools_used=["bash", "edit"],
            iterations=3,
            result="success",
        )
        assert r["success"] is True
        execution = mcp.get_execution(ticket_id)["data"]["executions"]
        assert len(execution) == 1
        assert execution[0]["model"] == "claude-4"
        assert execution[0]["cost"] == 0.012


class TestRepositoryContext:
    def test_get_repository_context(self, mcp, storage, org, repo):
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)
        r = mcp.create_ticket(title="T", repository_id=repo.id, resources=[res.id])
        ticket_id = r["data"]["id"]
        mcp.start_ticket(ticket_id)

        ctx = mcp.get_repository_context(repo.id)["data"]
        assert ctx["name"] == "youtube-backend"
        assert len(ctx["tickets"]) == 1
        assert "Chat.py" in ctx["resources_locked"]


class TestDispatcher:
    def test_dispatch_unknown_tool(self, mcp):
        r = mcp.call("no_existe")
        assert r["success"] is False

    def test_dispatch_missing_param(self, mcp):
        r = mcp.call("get_ticket", {})
        assert r["success"] is False

    def test_dispatch_get_available_tasks(self, mcp, org):
        r = mcp.call("get_available_tasks")
        assert r["success"] is True
        assert "available_tasks" in r["data"]
