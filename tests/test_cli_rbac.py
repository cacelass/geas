"""
tests.test_cli_rbac — CLI de roles y permisos (§7/§42).

El modelo RBAC existía (Role + check_permission) pero no había forma de
crear roles ni conceder permisos desde la CLI. Estos tests fijan el
contrato: catálogo §7, creación con validación, grant idempotente y
role_id en user/agent create.
"""

from __future__ import annotations

import pytest

from geas.cli import _cmd_agent, _cmd_permission, _cmd_role, _cmd_user
from geas.models import Organization, Role
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path) -> Storage:
    return Storage(tmp_path / "geas.db")


@pytest.fixture
def org(storage: Storage) -> Organization:
    o = Organization(name="RBAC Test", description="permisos", profile="team")
    storage.create_organization(o)
    return o


class TestPermissionList:
    def test_catalogo_seccion_7(self, capsys):
        assert _cmd_permission(storage=None, args=["list"]) == 0
        out = capsys.readouterr().out
        assert "ticket:create" in out
        assert "git:merge" in out
        assert "execution:read" in out

    def test_sin_subcomando_falla(self):
        assert _cmd_permission(storage=None, args=[]) == 1


class TestRoleCreate:
    def test_crea_rol_con_permisos(self, storage, org, capsys):
        assert (
            _cmd_role(
                storage,
                ["create", org.id, "revisor", "ticket:read", "ticket:update"],
            )
            == 0
        )
        assert "Rol creado" in capsys.readouterr().out
        roles = storage.list_roles(org.id)
        assert len(roles) == 1
        assert roles[0].name == "revisor"
        assert set(roles[0].permissions) == {"ticket:read", "ticket:update"}

    def test_permiso_desconocido_falla_claro(self, storage, org, capsys):
        assert _cmd_role(storage, ["create", org.id, "hacker", "ticket:hack"]) == 1
        assert "ticket:hack" in capsys.readouterr().err
        assert storage.list_roles(org.id) == []

    def test_list_muestra_permisos(self, storage, org, capsys):
        _cmd_role(storage, ["create", org.id, "admin2", "audit:read", "git:pr"])
        capsys.readouterr()
        assert _cmd_role(storage, ["list", org.id]) == 0
        out = capsys.readouterr().out
        assert "admin2" in out
        assert "audit:read" in out


class TestRoleGrant:
    def test_grant_anade_permiso(self, storage, org, capsys):
        _cmd_role(storage, ["create", org.id, "autor", "ticket:create"])
        capsys.readouterr()
        role = storage.list_roles(org.id)[0]
        assert _cmd_role(storage, ["grant", org.id, role.id, "ticket:complete"]) == 0
        assert "ticket:complete" in capsys.readouterr().out
        assert "ticket:complete" in storage.get_role(role.id).permissions

    def test_grant_idempotente(self, storage, org, capsys):
        role = Role(organization_id=org.id, name="autor", permissions=["ticket:read"])
        storage.create_role(role)
        assert _cmd_role(storage, ["grant", org.id, role.id, "ticket:read"]) == 0
        capsys.readouterr()
        assert storage.get_role(role.id).permissions == ["ticket:read"]

    def test_grant_rol_inexistente_falla(self, storage, org, capsys):
        assert _cmd_role(storage, ["grant", org.id, "NO-EXISTE", "audit:read"]) == 1
        assert "Rol no encontrado" in capsys.readouterr().err

    def test_grant_permiso_desconocido_falla(self, storage, org, capsys):
        role = Role(organization_id=org.id, name="autor", permissions=[])
        storage.create_role(role)
        assert _cmd_role(storage, ["grant", org.id, role.id, "ticket:hack"]) == 1
        assert "ticket:hack" in capsys.readouterr().err
        assert storage.get_role(role.id).permissions == []


class TestRoleIdEnActores:
    def test_agent_create_acepta_role_id(self, storage, org, capsys):
        role = Role(organization_id=org.id, name="agente", permissions=["ticket:read"])
        storage.create_role(role)
        assert (
            _cmd_agent(
                storage, ["create", org.id, "Codex", "openai", "gpt-5", "", role.id]
            )
            == 0
        )
        agent = storage.list_agents(org.id)[0]
        assert agent.role_id == role.id

    def test_user_create_rol_sigue_funcionando(self, storage, org, capsys):
        role = Role(organization_id=org.id, name="dev", permissions=["ticket:start"])
        storage.create_role(role)
        assert (
            _cmd_user(storage, ["create", org.id, "Ana", "ana@x.com", "", role.id])
            == 0
        )
        user = storage.list_users(org.id)[0]
        assert user.role_id == role.id