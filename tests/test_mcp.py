"""
tests.test_mcp — Tests del MCP de Geas (spec §26).

Cubre el flujo completo: crear ticket, dependencias, lock de recursos,
bloqueo de otro ticket, completar y liberar.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from geas.mcp import GeasMcp
from geas.models import (
    DEFAULT_PERMISSIONS,
    Agent,
    Organization,
    Repository,
    Resource,
    ResourceType,
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


class TestElEsquemaTOOLSBasta:
    """Lo que TOOLS declara tiene que bastar para llamar a la herramienta.

    El dispatch `call` traduce un KeyError a "Falta parametro: 'x'",
    asi que si el dispatch lee un parametro que TOOLS no declara, cualquier
    cliente que se guie por el esquema recibe un error alucionante en una
    herramienta que, segun TOOLS, no necesita ese parametro. Pasó con
    `commit_after`, `commit_before` y `reason` (ticket 3662e133).

    El test llama cada herramienta del esquema con SUS params declarados y
    ninguno, y solo exige que no salte "Falta parametro": el resto de errores
    (ticket inexistente, FORBIDDEN) son legítimos y los cubre la suite.
    """

    # Valores de mentira que pasan la validación de presencia. El objetivo es
    # comprobar el contrato, no el comportamiento de cada herramienta.
    FALSOS: ClassVar[dict] = {
        "ticket_id": "no-existe",
        "commit_before": "abc123",
        "commit_after": "def456",
        "commit_sha": "abc123",
        "commit_id": "abc123",
        "commit": "abc123",
        "result": "hecho",
        "reason": "motivo",
        "message": "mensaje",
        "branch": "main",
        "org_id": "no-existe",
        "resource_id": "no-existe",
        "repository_id": "no-existe",
        "organization_id": "no-existe",
        "department_id": "",
        "title": "titulo",
        "description": "descripcion",
        "priority": 0,
        "resources": [],
        "dependencies": [],
        "fields": {"title": "otro"},
        "name": "google",
        "status": "ok",
        "provider": "openai",
        "model": "gpt",
    }

    @staticmethod
    def _faltaria(respuesta: dict) -> str | None:
        """Devuelve el nombre del parametro que falta, o None si no falta."""
        error = respuesta.get("error", "") or ""
        if respuesta.get("success"):
            return None
        if error.startswith("Falta parametro") or "Falta par" in error:
            return error
        return None

    def test_toda_herramienta_del_esquema_se_puede_llamar_con_sus_params(self, mcp):
        from geas.mcp import TOOLS

        sin_probar = []
        for herramienta in TOOLS:
            nombre = herramienta["name"]
            declarados = herramienta.get("params", [])
            faltando = [p for p in declarados if p not in self.FALSOS]
            if faltando:
                sin_probar.append(f"{nombre}: sin valor de mentira para {faltando}")
                continue
            params = {p: self.FALSOS[p] for p in declarados}
            try:
                respuesta = mcp.call(nombre, params)
            except KeyError as exc:
                # El unico parametro que puede faltar sin estar en TOOLS es el
                # que el dispatch pide con `params["x"]`. El dispatch se traga
                # el KeyError y responde "Falta parametro"; si algo se escapa
                # hasta aqui, es justo el fallo que buscamos.
                raise AssertionError(
                    f"{nombre}: TOOLS declara {declarados} pero el dispatch ha "
                    f"pedido '{exc.args[0] if exc.args else exc}', que no esta "
                    f"declarado. Anadelo a TOOLS o no lo leas."
                ) from exc
            except Exception:  # noqa: BLE001, S112
                # Cualquier otra excepcion es de la herramienta con datos de
                # mentira (sync sobre un repo que no es git, por ejemplo), no
                # del contrato, que es lo unico que este test comprueba. Los
                # tests de cada herramienta cubren el comportamiento.
                continue
            problema = self._faltaria(respuesta)
            assert problema is None, (
                f"TOOLS declara {declarados} para {nombre}, pero el dispatch "
                f"pide algo mas: {respuesta.get('error')}. O el dispatch lee un "
                f"parametro que TOOLS no declara, o falta el valor de mentira."
            )
        assert not sin_probar, "; ".join(sin_probar)

    def test_cancel_ticket_conserva_el_motivo(self, mcp):
        """El motivo se guardaba y se perdia en silencio (3662e133)."""
        ticket = mcp.create_ticket(title="Se cancela")["data"]["id"]
        r = mcp.call("cancel_ticket", {"ticket_id": ticket, "reason": "obsoleto"})
        assert r["success"] is True
        assert r["data"]["reason"] == "obsoleto"

        eventos = mcp.storage.conn.execute(
            "SELECT metadata FROM events WHERE resource_id = ? "
            "AND event_type = 'TICKET_CANCELLED'",
            (ticket,),
        ).fetchall()
        assert eventos, "no se registro ningun evento TICKET_CANCELLED"
        import json

        assert json.loads(eventos[-1]["metadata"])["reason"] == "obsoleto"

    def test_cancel_ticket_sin_motivo_todavia_funciona(self, mcp):
        ticket = mcp.create_ticket(title="Sin motivo")["data"]["id"]
        r = mcp.call("cancel_ticket", {"ticket_id": ticket})
        assert r["success"] is True

    def test_report_commit_conserva_el_mensaje(self, mcp):
        ticket = mcp.create_ticket(title="Con commit")["data"]["id"]
        r = mcp.call(
            "report_commit",
            {"ticket_id": ticket, "commit_sha": "abc123", "message": "primer commit"},
        )
        assert r["success"] is True

        eventos = mcp.storage.conn.execute(
            "SELECT metadata FROM events WHERE resource_id = ? "
            "AND event_type = 'COMMIT_REGISTERED'",
            (ticket,),
        ).fetchall()
        import json

        metadata = json.loads(eventos[-1]["metadata"])
        assert metadata["message"] == "primer commit"
        assert metadata["commit"] == "abc123"


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

    # GEAS-003: un resource que no existe se rechaza diciendo cual es, en
    # vez de guardar el ticket y petar al reclamar con una FK.
    def test_create_ticket_rejects_missing_resource(self, mcp, org):
        r = mcp.create_ticket(title="Con recurso fantasma", resources=["no-existe"])
        assert r["success"] is False
        assert "no-existe" in r["error"]
        # Y el id truncado de un recurso real tambien se rechaza: sin el
        # prefijo completo la FK de resource_locks no tendria a quien apuntar.
        r2 = mcp.create_ticket(title="Con prefijo", resources=["abc12345"])
        assert r2["success"] is False
        assert "abc12345" in r2["error"]

    # GEAS-003 media: la rama se genera de un titulo con caracteres que git
    # rechaza (':', '~', etc.) y el ticket quedaba IN_PROGRESS sin rama.
    def test_start_ticket_generates_safe_branch(self, mcp, org, repo):
        r = mcp.create_ticket(
            title="GEAS-003: fix (urgente)", repository_id=repo.id
        )
        ticket_id = r["data"]["id"]
        out = mcp.start_ticket(ticket_id, commit_before="abc")
        assert out["success"] is True
        branch = out["data"]["branch"]
        assert ":" not in branch
        assert " " not in branch
        assert "~" not in branch
        assert branch.startswith(ticket_id[:8])


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
        # GEAS-004: get_permissions devuelve los permisos del actor que
        # pregunta (los del rol), no el catalogo completo del perfil.
        r = mcp.get_permissions()
        assert r["success"] is True
        assert set(r["data"]["permissions"]) == set(DEFAULT_PERMISSIONS)

    # GEAS-004: el agente ve 14 y el manager 21, los mismos numeros que
    # storage.get_actor_permissions y geas_admin.py show. Antes ambos veian
    # el conjunto del perfil (25) y la ejecucion real les negaba con
    # FORBIDDEN lo que la tool decian poder hacer.
    def test_get_permissions_distingue_agente_de_manager(self, storage, org):
        from geas.models import User

        permisos = {
            "agent": [
                "ticket:read",
                "ticket:start",
                "ticket:complete",
                "resource:read",
            ],
            "manager": [
                "ticket:read",
                "ticket:create",
                "ticket:update",
                "ticket:start",
                "ticket:complete",
                "resource:read",
                "resource:lock",
                "repository:read",
                "user:manage",
                "audit:read",
                "execution:read",
            ],
        }
        roles = {}
        for nombre in ("agent", "manager"):
            rol = Role(
                organization_id=org.id,
                name=nombre,
                permissions=permisos[nombre],
            )
            storage.create_role(rol)
            roles[nombre] = rol

        actor_agent = Agent(
            id="agent-X",
            organization_id=org.id,
            name="agente",
            provider="test",
            model="test",
            role_id=roles["agent"].id,
        )
        storage.create_agent(actor_agent)
        actor_manager = User(
            id="user-X",
            organization_id=org.id,
            name="manager",
            role_id=roles["manager"].id,
        )
        storage.create_user(actor_manager)

        for actor, esperados in (
            (actor_agent, set(permisos["agent"])),
            (actor_manager, set(permisos["manager"])),
        ):
            mcp_actor = GeasMcp(storage, actor_id=actor.id, org_id=org.id)
            r = mcp_actor.get_permissions()
            assert r["success"] is True
            got = set(r["data"]["permissions"])
            assert got == esperados, (actor.id, got, esperados)
            # Y coincide con el computo real de storage, no con el perfil.
            assert got == storage.get_actor_permissions(actor.id, org.id)

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

    # ─── GEAS-007 ────────────────────────────────────────────────────────

    def test_list_actors_y_list_resources_desde_rol_agent(self, storage, org, repo):
        from geas.mcp import TOOL_NAMES, GeasMcp

        role = Role(
            organization_id=org.id,
            name="agente-007",
            permissions=["user:read", "resource:read"],
        )
        storage.create_role(role)
        actor = Agent(
            id="agent-007",
            organization_id=org.id,
            name="agent-007",
            provider="test",
            model="test",
            role_id=role.id,
        )
        storage.create_agent(actor)
        assert "user:read" in DEFAULT_PERMISSIONS  # catalogo lo acepta

        mcp_agent = GeasMcp(storage, actor_id=actor.id, org_id=org.id)

        # registradas en el catalogo y en el despacho
        assert {"list_actors", "list_resources"} <= TOOL_NAMES

        # recursos del repo
        rr = mcp_agent.call("list_resources", {"repository_id": repo.id})
        assert rr["success"] is True
        assert rr["data"]["resources"] == []
        storage.create_resource(
            Resource(
                repository_id=repo.id,
                path="agents/agents/",
                type=ResourceType.DIRECTORY,
            )
        )
        rr = mcp_agent.call("list_resources", {"repository_id": repo.id})
        assert [x["path"] for x in rr["data"]["resources"]] == ["agents/agents/"]

        # actores de la org (usuarios + agentes)
        ar = mcp_agent.call("list_actors", {"organization_id": org.id})
        assert ar["success"] is True
        kinds = {a["kind"] for a in ar["data"]["actors"]}
        assert kinds == {"agent"}
        assert any(a["id"] == "agent-007" for a in ar["data"]["actors"])
        # un repo inexistente da error legible
        assert (
            mcp_agent.call("list_resources", {"repository_id": "no-existe"})[
                "success"
            ]
            is False
        )

    def test_list_actors_sin_user_read_deniega(self, storage, org):
        from geas.mcp import GeasMcp
        from geas.models import Agent, Role

        role = Role(
            organization_id=org.id,
            name="solo-resources",
            permissions=["resource:read"],
        )
        storage.create_role(role)
        actor = Agent(
            id="agent-sin-users",
            organization_id=org.id,
            name="agent-sin-users",
            provider="test",
            model="test",
            role_id=role.id,
        )
        storage.create_agent(actor)
        restringido = GeasMcp(storage, actor_id=actor.id, org_id=org.id)
        assert restringido.call("list_actors", {"organization_id": org.id})[
            "success"
        ] is False


# ─── GEAS-005: crear un ticket es atomico ───────────────────────────────────
# create_ticket escribia en cuatro `commit()` sueltos (ticket, dependencias,
# evento, audit). Con eso, un fallo entre medias dejaba el audit_log afirmando
# un CREATE_TICKET cuyo ticket no existia. Ahora van en una transaccion.


class TestCrearTicketEsAtomico:
    def test_un_ticket_nuevo_deja_ticket_evento_y_audit(self, mcp, storage):
        r = mcp.create_ticket(title="Atomico")
        ticket_id = r["data"]["id"]

        assert storage.get_ticket(ticket_id) is not None
        for sql, params in [
            ("SELECT COUNT(*) FROM audit_log WHERE action='CREATE_TICKET' AND resource_id=?",
             (ticket_id,)),
            ("SELECT COUNT(*) FROM events WHERE event_type='TICKET_CREATED' AND resource_id=?",
             (ticket_id,)),
        ]:
            assert storage.conn.execute(sql, params).fetchone()[0] == 1

    def test_si_el_audit_falla_no_queda_ticket_ni_evento(self, mcp, storage):
        """El fallo se propaga y no deja ni media fila (GEAS-005)."""
        antes_tickets = storage.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        antes_audits = storage.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='CREATE_TICKET'"
        ).fetchone()[0]
        antes_eventos = storage.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='TICKET_CREATED'"
        ).fetchone()[0]

        def audit_que_peta(*args, **kwargs):
            raise RuntimeError("disco lleno")

        storage.create_audit = audit_que_peta
        with pytest.raises(RuntimeError, match="disco lleno"):
            mcp.create_ticket(title="No debe sobrevivir")

        assert storage.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == antes_tickets
        assert storage.conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='CREATE_TICKET'"
        ).fetchone()[0] == antes_audits
        assert storage.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='TICKET_CREATED'"
        ).fetchone()[0] == antes_eventos

        # Y la conexion sigue viva para el siguiente ticket (no se dejo suelta).
        del storage.create_audit
        r = mcp.create_ticket(title="El siguiente si")
        assert r["success"] is True
        assert storage.get_ticket(r["data"]["id"]) is not None

    def test_si_una_dependencia_falla_no_queda_el_ticket(self, mcp, storage):
        """Las dependencias van tambien dentro de la misma transaccion."""
        # La dependencia tiene que existir de verdad: create_ticket la valida
        # antes de escribir, y si no existe ni se llega a tocar la transaccion.
        previa = mcp.create_ticket(title="Dependencia real")["data"]["id"]

        def dependencia_que_peta(*args, **kwargs):
            raise RuntimeError("FK fallo")

        storage.create_dependency = dependencia_que_peta
        with pytest.raises(RuntimeError, match="FK fallo"):
            mcp.create_ticket(title="Con dependencia rota", dependencies=[previa])

        assert storage.conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE title = 'Con dependencia rota'"
        ).fetchone()[0] == 0

    def test_el_audit_log_no_afirma_tickets_que_no_existen(self, mcp, storage):
        """Invariante de GEAS-005 sobre el camino real de la API."""
        for i in range(5):
            mcp.create_ticket(title=f"Ticket {i}")

        huerfanos = storage.conn.execute(
            "SELECT a.resource_id FROM audit_log a WHERE a.action='CREATE_TICKET' "
            "AND NOT EXISTS (SELECT 1 FROM tickets t WHERE t.id = a.resource_id)"
        ).fetchall()
        assert [row["resource_id"] for row in huerfanos] == []
