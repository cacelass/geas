"""tests.test_cli_profiles — CLI de perfiles, estructura Enterprise y sync (§43).

Cubre `geas init --profile`, `geas enterprise init` (generador bootstrap),
`geas sync` (estructura declarativa → base de datos, idempotente) y
`geas org show`. La base de datos es la única fuente de verdad del estado
operativo; sync crea/converge solo lo declarativo.
"""

from __future__ import annotations

import pytest

from geas.cli import _cmd_enterprise, _cmd_init, _cmd_org, _cmd_sync
from geas.declarative import build_structure, render_structure
from geas.models import DEFAULT_ROLES, Organization
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "geas.db")
    yield s
    s.close()


def _write_tree(root, struct) -> None:
    for rel, content in render_structure(struct).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


# ─── init --profile ──────────────────────────────────────────────────


def test_init_default_es_individual(storage, capsys):
    assert _cmd_init(storage, ["Demo"]) == 0
    org = storage.list_organizations()[0]
    assert org.name == "Demo"
    assert org.profile == "individual"
    out = capsys.readouterr().out
    assert "Perfil: individual" in out
    assert "Componentes:" in out


def test_init_profile_flag_manual(storage):
    assert _cmd_init(storage, ["OrgA", "--profile", "team"]) == 0
    org = storage.list_organizations()[0]
    assert org.profile == "team"
    assert len(storage.list_roles(org.id)) == len(DEFAULT_ROLES)


def test_init_profile_atributos_cortos(storage):
    assert _cmd_init(storage, ["OrgB", "-p", "enterprise"]) == 0
    assert storage.list_organizations()[0].profile == "enterprise"


def test_init_banderas_sin_nombre(storage):
    assert _cmd_init(storage, ["--individual"]) == 0
    assert storage.list_organizations()[0].profile == "individual"
    assert _cmd_init(storage, ["--team"]) == 0
    assert storage.list_organizations()[-1].profile == "team"
    assert _cmd_init(storage, ["--enterprise"]) == 0
    assert storage.list_organizations()[-1].profile == "enterprise"


def test_init_alias_numericos(storage):
    assert _cmd_init(storage, ["N1", "--profile", "1"]) == 0
    assert storage.list_organizations()[0].profile == "individual"
    assert _cmd_init(storage, ["N2", "--profile", "2"]) == 0
    assert storage.list_organizations()[1].profile == "team"
    assert _cmd_init(storage, ["N3", "--profile", "3"]) == 0
    assert storage.list_organizations()[2].profile == "enterprise"


def test_init_perfil_desconocido_falla(storage):
    assert _cmd_init(storage, ["Bad", "--profile", "TYPO"]) == 1
    assert storage.list_organizations() == []


def test_init_team_avisa_backend_postgres_pendiente(storage, capsys):
    assert _cmd_init(storage, ["OrgC", "--team"]) == 0
    out = capsys.readouterr().out
    assert "postgres" in out
    assert "sqlite" in out


# ─── org show ────────────────────────────────────────────────────────


def test_org_list_sigue_funcionando(storage, capsys):
    assert _cmd_init(storage, ["Org"]) == 0
    org = storage.list_organizations()[0]
    assert _cmd_org(storage, ["list"]) == 0
    assert org.name in capsys.readouterr().out


def test_org_show_muestra_perfil_componentes_y_conteos(storage, capsys):
    assert _cmd_init(storage, ["Org", "--enterprise"]) == 0
    org = storage.list_organizations()[0]
    assert _cmd_org(storage, ["show", org.id]) == 0
    out = capsys.readouterr().out
    assert "profile: enterprise" in out
    assert "Departments" in out
    assert "backend declarado: postgres" in out
    assert "roles: 5" in out
    assert "policies: 0" in out


def test_org_show_org_inexistente_falla(storage):
    assert _cmd_org(storage, ["show", "nope"]) == 1
    assert _cmd_org(storage, []) == 1


# ─── enterprise init ─────────────────────────────────────────────────


def test_enterprise_init_requiere_org(tmp_path):
    assert _cmd_enterprise(None, ["init", "--dir", str(tmp_path)]) == 1


def test_enterprise_init_genera_arbol(tmp_path):
    assert (
        _cmd_enterprise(
            None,
            [
                "init",
                "--org",
                "Google",
                "--dir",
                str(tmp_path),
                "--description",
                "Org demo",
                "--departments",
                "YouTube,Gmail",
                "--repos",
                "YouTube:backend,frontend;core-lib",
            ],
        )
        == 0
    )
    assert (tmp_path / "organization.yml").exists()
    assert (tmp_path / "departments/youtube/department.yml").exists()
    assert (tmp_path / "departments/youtube/repositories/backend.yml").exists()
    assert (tmp_path / "roles/admin.yml").exists()
    assert (tmp_path / ".github/workflows/geas-sync.yml").exists()
    # core-lib va a repos de organización
    assert (tmp_path / "repositories/core-lib.yml").exists()


def test_enterprise_init_opcion_desconocida_falla(tmp_path):
    assert _cmd_enterprise(None, ["init", "--bogus", "x"]) == 1


# ─── sync ────────────────────────────────────────────────────────────


def _enterprise_tree(root, with_policy: bool = True):
    struct = build_structure(
        "Google",
        profile="enterprise",
        description="Org demo",
        departments=["YouTube", "Gmail"],
        repositories={"YouTube": ["backend", "frontend"], "": ["core-lib"]},
    )
    _write_tree(root, struct)
    if with_policy:
        (root / "policies").mkdir(parents=True, exist_ok=True)
        (root / "policies/code-owners.yml").write_text(
            "name: code-owners\ndepartment: YouTube\n"
            "description: Cambios de código\npermissions: repository:write\n"
        )
    return struct


def test_sync_crea_org_departamentos_repos_roles_y_policy(tmp_path, storage):
    _enterprise_tree(tmp_path)
    assert _cmd_sync(storage, [str(tmp_path)]) == 0

    org = storage.get_organization_by_name("Google")
    assert org is not None
    assert org.profile == "enterprise"
    assert org.description == "Org demo"

    depts = storage.list_departments(org.id)
    assert {d.name for d in depts} == {"YouTube", "Gmail"}

    repos = storage.list_repositories(org.id)
    assert {r.name for r in repos} == {"backend", "frontend", "core-lib"}
    youtube = storage.get_department_by_name(org.id, "YouTube")
    backend = storage.get_repository_by_name(org.id, "backend", youtube.id)
    assert backend is not None
    core = storage.get_repository_by_name(org.id, "core-lib")
    assert core is not None and core.department_id is None

    roles = storage.list_roles(org.id)
    assert len(roles) == len(DEFAULT_ROLES)

    policy = storage.get_policy_by_name(org.id, "code-owners", youtube.id)
    assert policy is not None
    assert policy.department_id == youtube.id
    assert policy.permissions == ["repository:write"]


def test_sync_es_idempotente(tmp_path, storage, capsys):
    _enterprise_tree(tmp_path)
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    org_id = storage.get_organization_by_name("Google").id
    before = (
        len(storage.list_departments(org_id)),
        len(storage.list_repositories(org_id)),
        len(storage.list_roles(org_id)),
        len(storage.list_policies(org_id)),
    )

    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    out = capsys.readouterr().out
    after = (
        len(storage.list_departments(org_id)),
        len(storage.list_repositories(org_id)),
        len(storage.list_roles(org_id)),
        len(storage.list_policies(org_id)),
    )
    assert after == before
    assert "0 creados" in out
    assert "0 actualizados" in out


def test_sync_converge_permisos_de_rol(tmp_path, storage):
    _enterprise_tree(tmp_path)
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    org_id = storage.get_organization_by_name("Google").id

    # El árbol declara admin sin git:merge → la DB converge
    admin_file = tmp_path / "roles/admin.yml"
    admin_file.write_text(
        "name: admin\npermissions: git:merge, repository:write\n"
    )
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    admin = storage.get_role_by_name(org_id, "admin")
    assert admin.permissions == ["git:merge", "repository:write"]

    # Segundo pase sin cambios → no actualiza
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    assert storage.get_role_by_name(org_id, "admin").permissions == [
        "git:merge",
        "repository:write",
    ]


def test_sync_converge_url_branch_y_provider_de_repositorio(tmp_path, storage):
    _enterprise_tree(tmp_path)
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    org_id = storage.get_organization_by_name("Google").id
    core = storage.get_repository_by_name(org_id, "core-lib")
    assert core.url == ""

    # El árbol cambia la url/provider/branch → la DB converge
    (tmp_path / "repositories/core-lib.yml").write_text(
        "name: core-lib\nurl: https://github.com/google/core\n"
        "provider: github\ndefault_branch: develop\n"
    )
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    core = storage.get_repository_by_name(org_id, "core-lib")
    assert core.url == "https://github.com/google/core"
    assert core.provider == "github"
    assert core.default_branch == "develop"

    # Segundo pase sin cambios → no actualiza
    assert _cmd_sync(storage, [str(tmp_path)]) == 0
    assert (
        storage.get_repository_by_name(org_id, "core-lib").url
        == "https://github.com/google/core"
    )


def test_sync_rechaza_secciones_no_soportadas_por_perfil(tmp_path, storage):
    struct = build_structure(
        "OrgPequena",
        profile="individual",
        departments=["D"],
        repositories={"D": ["r1"]},
    )
    _write_tree(tmp_path, struct)
    assert _cmd_sync(storage, [str(tmp_path)]) == 1
    assert storage.get_organization_by_name("OrgPequena") is None


def test_dept_create_rechazado_en_individual_y_team(tmp_path, storage, capsys):
    """§43 punto 10: departamentos son Enterprise — la CLI imperativa lo hace cumplir."""
    from geas.cli import _cmd_dept

    org = Organization(name="Solo", profile="individual")
    storage.create_organization(org)
    assert _cmd_dept(storage, ["create", org.id, "D"]) == 1
    err = capsys.readouterr().err
    assert "PERFIL_NO_SOPORTA" in err and "departamentos" in err
    assert storage.list_departments(org.id) == []

    team = Organization(name="Equipo", profile="team")
    storage.create_organization(team)
    assert _cmd_dept(storage, ["create", team.id, "D"]) == 1
    assert "PERFIL_NO_SOPORTA" in capsys.readouterr().err
    assert storage.list_departments(team.id) == []


def test_dept_create_permite_enterprise(tmp_path, storage, capsys):
    from geas.cli import _cmd_dept

    org = Organization(name="Corp", profile="enterprise")
    storage.create_organization(org)
    assert _cmd_dept(storage, ["create", org.id, "YouTube"]) == 0
    assert {d.name for d in storage.list_departments(org.id)} == {"YouTube"}


def test_role_create_rechazado_en_individual(tmp_path, storage, capsys):
    """§43 punto 10: RBAC (roles/permisos) es Team/Enterprise."""
    from geas.cli import _cmd_role

    org = Organization(name="Solo", profile="individual")
    storage.create_organization(org)
    assert _cmd_role(storage, ["create", org.id, "dev", "ticket:read"]) == 1
    err = capsys.readouterr().err
    assert "PERFIL_NO_SOPORTA" in err and "roles" in err
    assert storage.list_roles(org.id) == []


def test_user_agent_create_rechazado_en_individual(tmp_path, storage, capsys):
    """§43 punto 10: usuarios/agentes son Team/Enterprise."""
    from geas.cli import _cmd_agent, _cmd_user

    org = Organization(name="Solo", profile="individual")
    storage.create_organization(org)
    assert _cmd_user(storage, ["create", org.id, "Ana"]) == 1
    assert "PERFIL_NO_SOPORTA" in capsys.readouterr().err
    assert _cmd_agent(storage, ["create", org.id, "bot", "anthropic", "claude"]) == 1
    assert "PERFIL_NO_SOPORTA" in capsys.readouterr().err
    assert storage.list_users(org.id) == []
    assert storage.list_agents(org.id) == []


def test_repo_create_permite_individual(tmp_path, storage, capsys):
    """§43: repositorios son núcleo — Individual los soporta (Git integration)."""
    from geas.cli import _cmd_repo

    org = Organization(name="Solo", profile="individual")
    storage.create_organization(org)
    assert _cmd_repo(storage, ["create", org.id, "app"]) == 0
    assert {r.name for r in storage.list_repositories(org.id)} == {"app"}