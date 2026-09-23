"""
tests.test_storage — Tests de la capa de almacenamiento de Geas.

Cubre el modelo de datos completo: organizaciones, departamentos, roles,
usuarios, agentes, repos, recursos, locks, tickets, dependencias,
ejecuciones y auditoría.
"""

from __future__ import annotations

import pytest

from geas.models import (
    Agent,
    AuditLog,
    Department,
    Event,
    Execution,
    Organization,
    Policy,
    Repository,
    Resource,
    ResourceLock,
    ResourceType,
    Role,
    Ticket,
    TicketDependency,
    TicketStatus,
    User,
    Visibility,
)
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def org(storage):
    o = Organization(name="TestOrg")
    storage.create_organization(o)
    return o


@pytest.fixture
def dept(storage, org):
    d = Department(organization_id=org.id, name="Backend")
    storage.create_department(d)
    return d


@pytest.fixture
def repo(storage, org, dept):
    r = Repository(
        organization_id=org.id,
        department_id=dept.id,
        name="api",
        provider="github",
        url="https://github.com/test/api",
    )
    storage.create_repository(r)
    return r


# ─── Organizations ──────────────────────────────────────────────────────────


class TestOrganizations:
    def test_create_and_get(self, storage, org):
        found = storage.get_organization(org.id)
        assert found is not None
        assert found.name == "TestOrg"
        assert found.active is True

    def test_list(self, storage, org):
        orgs = storage.list_organizations()
        assert len(orgs) == 1
        assert orgs[0].id == org.id

    def test_empty_list(self, storage):
        assert storage.list_organizations() == []


# ─── Departments ────────────────────────────────────────────────────────────


class TestDepartments:
    def test_create_and_get(self, storage, org):
        d = Department(organization_id=org.id, name="Backend")
        storage.create_department(d)
        found = storage.get_department(d.id)
        assert found is not None
        assert found.name == "Backend"
        assert found.organization_id == org.id

    def test_hierarchy(self, storage, org):
        parent = Department(organization_id=org.id, name="YouTube")
        storage.create_department(parent)
        child = Department(
            organization_id=org.id,
            parent_department_id=parent.id,
            name="Backend",
        )
        storage.create_department(child)
        children = storage.list_child_departments(parent.id)
        assert len(children) == 1
        assert children[0].id == child.id

    def test_list_by_org(self, storage, org):
        storage.create_department(Department(organization_id=org.id, name="A"))
        storage.create_department(Department(organization_id=org.id, name="B"))
        assert len(storage.list_departments(org.id)) == 2


# ─── Roles ──────────────────────────────────────────────────────────────────


class TestRoles:
    def test_create_with_permissions(self, storage, org):
        r = Role(
            organization_id=org.id,
            name="developer",
            permissions=["ticket:create", "ticket:read"],
        )
        storage.create_role(r)
        found = storage.get_role(r.id)
        assert found is not None
        assert found.permissions == ["ticket:create", "ticket:read"]

    def test_default_roles_exist(self, storage, org):
        # El rol por defecto debe tener todos los permisos
        from geas.models import DEFAULT_PERMISSIONS, DEFAULT_ROLES

        assert "admin" in DEFAULT_ROLES
        assert set(DEFAULT_ROLES["admin"]) == set(DEFAULT_PERMISSIONS)
        assert "ticket:manage" not in DEFAULT_PERMISSIONS


# ─── Users y Agents ─────────────────────────────────────────────────────────


class TestUsers:
    def test_create_and_get(self, storage, org, dept):
        role = Role(
            organization_id=org.id,
            name="developer",
            permissions=["ticket:create", "ticket:read"],
        )
        storage.create_role(role)
        u = User(
            organization_id=org.id,
            department_id=dept.id,
            name="Ana",
            email="ana@test.com",
            role_id=role.id,
        )
        storage.create_user(u)
        found = storage.get_user(u.id)
        assert found is not None
        assert found.name == "Ana"
        assert found.email == "ana@test.com"


class TestAgents:
    def test_create_and_get(self, storage, org, dept):
        role = Role(organization_id=org.id, name="agent", permissions=["ticket:read"])
        storage.create_role(role)
        a = Agent(
            organization_id=org.id,
            department_id=dept.id,
            name="agent-1",
            provider="anthropic",
            model="claude-4",
            model_version="1.0",
            role_id=role.id,
            harness_id="harness-a",
            configuration={"temperature": 0.5},
        )
        storage.create_agent(a)
        found = storage.get_agent(a.id)
        assert found is not None
        assert found.provider == "anthropic"
        assert found.role_id == role.id
        assert found.configuration["temperature"] == 0.5


# ─── Repositories ───────────────────────────────────────────────────────────


class TestRepositories:
    def test_create_and_get(self, storage, org, dept):
        r = Repository(
            organization_id=org.id,
            department_id=dept.id,
            name="api",
            provider="github",
            url="https://github.com/test/api",
            visibility=Visibility.INTERNAL,
        )
        storage.create_repository(r)
        found = storage.get_repository(r.id)
        assert found is not None
        assert found.visibility == Visibility.INTERNAL
        assert found.default_branch == "main"


# ─── Resources y Locks ──────────────────────────────────────────────────────


class TestResources:
    def test_create_and_get(self, storage, repo):
        res = Resource(
            repository_id=repo.id,
            path="src/auth/",
            type=ResourceType.DIRECTORY,
            metadata={"owner": "auth-team"},
        )
        storage.create_resource(res)
        found = storage.get_resource(res.id)
        assert found is not None
        assert found.path == "src/auth/"
        assert found.type == ResourceType.DIRECTORY


class TestLocks:
    def test_lock_resource(self, storage, repo):
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)
        ticket = Ticket(
            organization_id=repo.organization_id,
            repository_id=repo.id,
            title="Fix chat",
        )
        storage.create_ticket(ticket)

        lock = ResourceLock(
            resource_id=res.id,
            ticket_id=ticket.id,
            actor_id="agent-1",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        storage.create_lock(lock)

        found = storage.get_lock_for_resource(res.id)
        assert found is not None
        assert found.ticket_id == ticket.id

    def test_release_lock(self, storage, repo):
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)
        ticket = Ticket(organization_id=repo.organization_id, title="T")
        storage.create_ticket(ticket)
        lock = ResourceLock(
            resource_id=res.id,
            ticket_id=ticket.id,
            actor_id="agent-1",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        storage.create_lock(lock)

        assert storage.release_lock(lock.id) is True
        assert storage.get_lock_for_resource(res.id) is None

    def test_release_locks_for_ticket(self, storage, repo):
        res1 = Resource(repository_id=repo.id, path="a.py")
        res2 = Resource(repository_id=repo.id, path="b.py")
        storage.create_resource(res1)
        storage.create_resource(res2)
        ticket = Ticket(organization_id=repo.organization_id, title="T")
        storage.create_ticket(ticket)

        for res in (res1, res2):
            storage.create_lock(
                ResourceLock(
                    resource_id=res.id,
                    ticket_id=ticket.id,
                    actor_id="agent-1",
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            )

        assert storage.release_locks_for_ticket(ticket.id) == 2
        assert storage.get_lock_for_resource(res1.id) is None
        assert storage.get_lock_for_resource(res2.id) is None

    def test_heartbeat(self, storage, repo):
        res = Resource(repository_id=repo.id, path="a.py")
        storage.create_resource(res)
        ticket = Ticket(organization_id=repo.organization_id, title="T")
        storage.create_ticket(ticket)
        lock = ResourceLock(
            resource_id=res.id,
            ticket_id=ticket.id,
            actor_id="agent-1",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        storage.create_lock(lock)
        assert storage.heartbeat_lock(lock.id) is True

    def test_acquire_locks_is_all_or_nothing(self, storage, repo):
        first = Resource(repository_id=repo.id, path="first.py")
        second = Resource(repository_id=repo.id, path="second.py")
        storage.create_resource(first)
        storage.create_resource(second)
        owner = Ticket(organization_id=repo.organization_id, title="Owner")
        other = Ticket(organization_id=repo.organization_id, title="Other")
        storage.create_ticket(owner)
        storage.create_ticket(other)

        assert storage.acquire_locks([first.id], owner.id, "agent-a") == (True, None)
        acquired, blocked = storage.acquire_locks(
            [first.id, second.id], other.id, "agent-b"
        )
        assert acquired is False
        assert blocked == first.id
        assert storage.get_lock_for_resource(second.id) is None


# ─── Tickets y dependencias ─────────────────────────────────────────────────


class TestTickets:
    def test_create_free(self, storage, org, repo):
        t = Ticket(
            organization_id=org.id,
            repository_id=repo.id,
            title="Implementar auth",
            description="Login con JWT",
            priority=2,
        )
        storage.create_ticket(t)
        found = storage.get_ticket(t.id)
        assert found is not None
        assert found.status == TicketStatus.FREE
        assert found.priority == 2

    def test_state_machine(self, storage, org):
        t = Ticket(organization_id=org.id, title="Estado")
        storage.create_ticket(t)

        # CLAIMED: reserva ligera vía assign_ticket (no es el claim de
        # trabajo — los flujos work start / mcp.start_ticket parten de FREE)
        ok = storage.assign_ticket(t.id, "actor-1")
        assert ok is True
        assert storage.get_ticket(t.id).status == TicketStatus.CLAIMED

        # El claim real es FREE→IN_PROGRESS en UNA operación atómica (§39)
        t2 = Ticket(organization_id=org.id, title="Estado 2")
        storage.create_ticket(t2)
        ok = storage.start_ticket(
            t2.id, commit_before="a82f91c", branch="YT-104", actor_id="me"
        )
        assert ok is True
        found = storage.get_ticket(t2.id)
        assert found.status == TicketStatus.IN_PROGRESS
        assert found.assigned_actor_id == "me"
        assert found.commit_before == "a82f91c"
        assert found.branch == "YT-104"
        assert found.started_at is not None

        # §39: el segundo claim del mismo ticket FREE falla (guardia de
        # estado en el propio UPDATE — TOCTOU-safe)
        assert storage.start_ticket(t2.id) is False

        # DONE con commit_after
        ok = storage.complete_ticket(t2.id, commit_after="b71c4de", result="OK")
        assert ok is True
        found = storage.get_ticket(t2.id)
        assert found.status == TicketStatus.DONE
        assert found.commit_after == "b71c4de"
        assert found.completed_at is not None

    def test_dependencies(self, storage, org):
        t1 = Ticket(organization_id=org.id, title="Dependencia base")
        t2 = Ticket(organization_id=org.id, title="Depende de t1")
        storage.create_ticket(t1)
        storage.create_ticket(t2)

        storage.create_dependency(
            TicketDependency(
                ticket_id=t2.id,
                depends_on_ticket_id=t1.id,
            )
        )

        # t2 depende de t1 → no resuelto hasta que t1 esté DONE
        assert storage.are_dependencies_resolved(t2.id) is False

        # t1 no depende de nada → resuelto
        assert storage.are_dependencies_resolved(t1.id) is True

        deps = storage.get_dependencies(t2.id)
        assert len(deps) == 1
        assert deps[0].id == t1.id

        # Completar t1 → dependencia resuelta
        storage.complete_ticket(t1.id, commit_after="abc")
        assert storage.are_dependencies_resolved(t2.id) is True

    def test_dependency_cycle_is_rejected(self, storage, org):
        first = Ticket(organization_id=org.id, title="First")
        second = Ticket(organization_id=org.id, title="Second")
        storage.create_ticket(first)
        storage.create_ticket(second)
        storage.create_dependency(
            TicketDependency(ticket_id=second.id, depends_on_ticket_id=first.id)
        )
        with pytest.raises(ValueError, match="DEPENDENCY_CYCLE"):
            storage.create_dependency(
                TicketDependency(ticket_id=first.id, depends_on_ticket_id=second.id)
            )


# ─── Executions y TestResults ───────────────────────────────────────────────


class TestExecutions:
    def test_create_and_get(self, storage, org):
        t = Ticket(organization_id=org.id, title="Con ejecución")
        storage.create_ticket(t)

        exe = Execution(
            ticket_id=t.id,
            actor_id="agent-1",
            harness_id="harness-a",
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
        storage.create_execution(exe)

        executions = storage.get_executions(t.id)
        assert len(executions) == 1
        assert executions[0].model == "claude-4"
        assert executions[0].cost == 0.012
        assert executions[0].tools_used == ["bash", "edit"]

    def test_test_results(self, storage, org):
        t = Ticket(organization_id=org.id, title="Con tests")
        storage.create_ticket(t)

        from geas.models import TestResult

        storage.create_test_result(
            TestResult(
                ticket_id=t.id,
                commit_id="b71c4de",
                pipeline_id="ci-1",
                status="passed",
                logs_reference="s3://logs/ci-1",
            )
        )

        results = storage.get_test_results(t.id)
        assert len(results) == 1
        assert results[0].status == "passed"


# ─── Eventos y auditoría ────────────────────────────────────────────────────


class TestEvents:
    def test_create_and_list(self, storage, org):
        storage.create_event(
            Event(
                event_type="TICKET_STARTED",
                organization_id=org.id,
                actor_id="agent-1",
                resource_type="ticket",
                resource_id="t-1",
                metadata={"branch": "YT-104"},
            )
        )
        events = storage.list_events(org.id)
        assert len(events) == 1
        assert events[0].event_type == "TICKET_STARTED"
        assert events[0].metadata["branch"] == "YT-104"


class TestAudit:
    def test_create_and_list(self, storage, org):
        storage.create_audit(
            AuditLog(
                actor_id="agent-1",
                action="LOCK_RESOURCE",
                resource_type="resource",
                resource_id="r-1",
                metadata={"ticket": "YT-104"},
            )
        )
        logs = storage.list_audit(org.id)
        assert len(logs) == 1
        assert logs[0].action == "LOCK_RESOURCE"


def test_storage_backend_postgres_sin_driver_falla_claro():
    """§42: pedir backend postgres SIN driver/instancia lanza error claro
    (NotImplementedError con mensaje §602/§1046), NUNCA cae en silencio
    a SQLite — eso mentiría el contrato §1046 («los agentes no acceden
    directamente a PostgreSQL»). El driver psycopg + instancia real queda
    como deuda de infra explícita (patrón copier §35/§36), pero el error
    es honesto y no se puede confundir con un fallo del perfil."""
    with pytest.raises(NotImplementedError, match="§42"):
        Storage("nunca-se-crea.db", backend="postgres")


# §43 — perfil de despliegue, lookups declarativos y políticas


def test_organizacion_guardar_perfil_por_defecto_y_leerlo(storage):
    o = Organization(name="Default")
    storage.create_organization(o)
    found = storage.get_organization(o.id)
    assert found.profile == "individual"
    assert storage.set_organization_profile(o.id, "enterprise") is True
    assert storage.get_organization(o.id).profile == "enterprise"


def test_get_organization_by_name_idempotente(storage, org):
    assert storage.get_organization_by_name(org.name).id == org.id
    assert storage.get_organization_by_name("No existe") is None


def test_update_organization_fields_converge_descripcion_y_perfil(storage, org):
    ok = storage.update_organization_fields(
        org.id, description="desc nueva", profile="enterprise"
    )
    assert ok is True
    found = storage.get_organization(org.id)
    assert found.description == "desc nueva"
    assert found.profile == "enterprise"


def test_lookups_declarativos_por_nombre(storage, org, dept, repo):
    assert storage.get_department_by_name(org.id, dept.name).id == dept.id
    assert storage.get_department_by_name(org.id, "otro") is None
    assert (
        storage.get_department_by_name(org.id, dept.name, parent_id="x-none") is None
    )
    # §43: la unicidad de repos es por (org, nombre, departamento)
    assert (
        storage.get_repository_by_name(org.id, repo.name, repo.department_id).id
        == repo.id
    )
    assert storage.get_repository_by_name(org.id, "no") is None


def test_get_role_by_name_y_update_permissions(storage, org):
    r = Role(organization_id=org.id, name="dev", permissions=["ticket:read"])
    storage.create_role(r)
    assert storage.get_role_by_name(org.id, "dev").id == r.id
    assert storage.get_role_by_name(org.id, "nope") is None
    assert storage.update_role_permissions(r.id, ["ticket:create", "ticket:read"])
    assert storage.get_role(r.id).permissions == ["ticket:create", "ticket:read"]


def test_policies_crud(storage, org, dept):
    p = Policy(
        organization_id=org.id,
        department_id=dept.id,
        name="code-owners",
        description="Cambios de código",
        permissions=["repository:write", "git:push"],
    )
    storage.create_policy(p)
    assert storage.get_policy(p.id).name == "code-owners"
    assert storage.get_policy_by_name(org.id, "code-owners", dept.id).id == p.id
    assert storage.get_policy_by_name(org.id, "code-owners") is None
    assert storage.get_policy_by_name(org.id, "otra") is None

    assert storage.update_policy_fields(
        p.id, description="Nuevo", permissions=["repository:read"]
    )
    found = storage.get_policy(p.id)
    assert found.description == "Nuevo"
    assert found.permissions == ["repository:read"]
    assert len(storage.list_policies(org.id)) == 1


def test_policies_org_sin_departamento(storage, org):
    p = Policy(organization_id=org.id, name="global", permissions=["ticket:read"])
    storage.create_policy(p)
    assert storage.get_policy_by_name(org.id, "global").id == p.id
    assert (
        storage.get_policy_by_name(org.id, "global", department_id="d-nonexistent")
        is None
    )
