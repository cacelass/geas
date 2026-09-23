"""
tests.test_mcp — Tests del MCP de Geas (spec §26).

Cubre el flujo completo: crear ticket, dependencias, lock de recursos,
bloqueo de otro ticket, completar y liberar.
"""

from __future__ import annotations

import pytest

from geas.mcp import GeasMcp
from geas.models import (
    DEFAULT_PERMISSIONS,
    Agent,
    Organization,
    Repository,
    Resource,
    Role,
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
def admin_role(storage, org):
    role = Role(
        organization_id=org.id,
        name="test-admin",
        permissions=DEFAULT_PERMISSIONS,
    )
    storage.create_role(role)
    return role


@pytest.fixture
def agent(storage, org, admin_role):
    actor = Agent(
        id="agent-A",
        organization_id=org.id,
        name="agent-A",
        provider="test",
        model="test",
        role_id=admin_role.id,
    )
    storage.create_agent(actor)
    return actor


@pytest.fixture
def mcp(storage, org, agent):
    return GeasMcp(storage, actor_id=agent.id, org_id=org.id)


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

    def test_resource_lock_blocks_other_ticket(
        self, mcp, storage, org, repo, admin_role
    ):
        # Recurso Chat.py
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)

        # YT-104 (agente A)
        r104 = mcp.create_ticket(
            title="Implementar Chat", resources=[res.id], priority=2
        )
        t104 = r104["data"]["id"]

        # YT-105 (agente B)
        agent_b = Agent(
            id="agent-B",
            organization_id=org.id,
            name="agent-B",
            provider="test",
            model="test",
            role_id=admin_role.id,
        )
        storage.create_agent(agent_b)
        mcp_b = GeasMcp(storage, actor_id=agent_b.id, org_id=org.id)
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


class TestAuthorization:
    def test_missing_permission_is_rejected(self, storage, org):
        role = Role(organization_id=org.id, name="reader", permissions=["ticket:read"])
        storage.create_role(role)
        actor = Agent(
            organization_id=org.id,
            name="reader-agent",
            provider="test",
            model="test",
            role_id=role.id,
        )
        storage.create_agent(actor)

        result = GeasMcp(storage, actor_id=actor.id, org_id=org.id).create_ticket(
            title="No autorizado"
        )
        assert result["success"] is False
        assert result["error"] == "FORBIDDEN"

    def test_update_persists_all_documented_fields(self, mcp):
        ticket_id = mcp.create_ticket(title="Antes", description="vieja")["data"]["id"]
        result = mcp.update_ticket(
            ticket_id, title="Después", description="nueva", priority=3
        )
        assert result["success"] is True
        ticket = mcp.get_ticket(ticket_id)["data"]
        assert ticket["title"] == "Después"
        assert ticket["description"] == "nueva"
        assert ticket["priority"] == 3


class TestOrgContextViaMcp:
    """Contexto organizativo del MCP (§43): perfil, componentes y estructura."""

    def test_get_organization_perfil_y_componentes(self, mcp, capsys):
        r = mcp.get_organization()
        assert r["success"] is True
        data = r["data"]
        assert data["name"] == "Google"
        assert data["profile"] == "individual"
        assert data["backend"] == "sqlite"
        assert "SQLite" in data["components"]
        assert "Departments" not in data["components"]

    def test_get_organization_id_explicita(self, mcp, org):
        r = mcp.get_organization(org.id)
        assert r["success"] is True
        assert r["data"]["id"] == org.id

    def test_get_organization_enterprise_expone_departments(self, storage):
        from geas.models import Agent, Role

        ent = Organization(name="Ent", profile="enterprise")
        storage.create_organization(ent)
        ent_role = Role(
            organization_id=ent.id,
            name="ent-admin",
            permissions=DEFAULT_PERMISSIONS,
        )
        storage.create_role(ent_role)
        actor_ent = Agent(
            id="agent-ent",
            organization_id=ent.id,
            name="agent-ent",
            provider="test",
            model="test",
            role_id=ent_role.id,
        )
        storage.create_agent(actor_ent)
        from geas.mcp import GeasMcp

        mcp_ent = GeasMcp(storage, actor_id=actor_ent.id, org_id=ent.id)
        r = mcp_ent.get_organization(ent.id)
        assert r["success"] is True
        assert r["data"]["profile"] == "enterprise"
        assert r["data"]["backend"] == "postgres"
        assert "Departments" in r["data"]["components"]
        assert "Policies" in r["data"]["components"]

    def test_get_organization_sin_org_falla(self, storage):
        from geas.mcp import GeasMcp

        solo = GeasMcp(storage, actor_id="x")
        r = solo.get_organization()
        assert r["success"] is False

    def test_list_departments(self, mcp, storage, org):
        from geas.models import Department

        storage.create_department(Department(organization_id=org.id, name="YouTube"))
        storage.create_department(Department(organization_id=org.id, name="Gmail"))
        r = mcp.list_departments()
        assert r["success"] is True
        names = {d["name"] for d in r["data"]["departments"]}
        assert names == {"YouTube", "Gmail"}

    def test_get_department_incluye_repos(self, mcp, storage, org):
        from geas.models import Department, Repository

        dept = Department(organization_id=org.id, name="YouTube")
        storage.create_department(dept)
        storage.create_department(Department(organization_id=org.id, name="Gmail"))
        storage.create_repository(
            Repository(organization_id=org.id, department_id=dept.id, name="backend")
        )
        storage.create_repository(
            Repository(organization_id=org.id, department_id=dept.id, name="frontend")
        )
        r = mcp.get_department("YouTube")
        assert r["success"] is True
        assert {x["name"] for x in r["data"]["repositories"]} == {
            "backend",
            "frontend",
        }

    def test_get_department_inexistente_falla(self, mcp):
        assert mcp.get_department("NoExiste")["success"] is False

    def test_list_repositories(self, mcp, repo):
        r = mcp.list_repositories()
        assert r["success"] is True
        assert {x["name"] for x in r["data"]["repositories"]} == {"youtube-backend"}

    def test_get_permissions_es_el_catalogo_7(self, mcp):
        r = mcp.get_permissions()
        assert r["success"] is True
        assert set(r["data"]["permissions"]) == set(DEFAULT_PERMISSIONS)

    def test_dispatch_expone_las_herramientas_nuevas(self, mcp, org):
        from geas.mcp import TOOL_NAMES

        for name in (
            "get_organization",
            "list_departments",
            "list_repositories",
        ):
            assert name in TOOL_NAMES
            assert mcp.call(name, {"organization_id": org.id})["success"] is True
        assert "get_permissions" in TOOL_NAMES
        assert mcp.call("get_permissions")["success"] is True
        assert "get_department" in TOOL_NAMES
        assert (
            mcp.call("get_department", {"name": "NoExiste"})["success"] is False
        )  # herramienta accesible, depto inexistente

    def test_sin_permiso_department_read_se_deniega(self, storage, org):
        from geas.mcp import GeasMcp
        from geas.models import Agent, Role

        role = Role(
            organization_id=org.id,
            name="solo-tickets",
            permissions=["ticket:read"],
        )
        storage.create_role(role)
        actor = Agent(
            id="agent-tickets",
            organization_id=org.id,
            name="agent-tickets",
            provider="test",
            model="test",
            role_id=role.id,
        )
        storage.create_agent(actor)
        restringido = GeasMcp(storage, actor_id=actor.id, org_id=org.id)
        r = restringido.get_organization(org.id)
        assert r["success"] is False
        assert r["error"] == "FORBIDDEN"
