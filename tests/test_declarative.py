"""tests.test_declarative — Estructura declarativa Enterprise (§43).

El árbol versionado define el estado DESEADO; la base de datos de GEAS
es la única fuente de verdad del estado operativo. Estos tests cubren el
loader (geas.declarative), el generador bootstrap (geas.enterprise) y la
regla de que NUNCA se escriben ficheros de estado operativo en el árbol
(ni tickets, ni locks, ni ejecuciones).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geas.declarative import (
    build_structure,
    load_structure,
    render_ci_workflow,
    render_structure,
    slug,
)
from geas.enterprise import generate_enterprise
from geas.models import DEFAULT_ROLES


def _write(root: Path, rel: str, content: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_slug_normaliza_nombres():
    assert slug("YouTube") == "youtube"
    assert slug("Mi Depto Backend") == "mi-depto-backend"
    assert slug("A/B") == "a-b"


def test_build_structure_render_carga_round_trip(tmp_path):
    struct = build_structure(
        "Google",
        profile="enterprise",
        description="Org de ejemplo",
        departments=["YouTube", "Gmail"],
        repositories={
            "YouTube": ["backend", "frontend"],
            "": ["core-lib"],
        },
    )
    files = render_structure(struct)
    for rel, content in files.items():
        _write(tmp_path, rel, content)

    loaded = load_structure(tmp_path)
    assert loaded.org_name == "Google"
    assert loaded.profile == "enterprise"
    assert {d.name for d in loaded.departments} == {"YouTube", "Gmail"}
    # Repo de YouTube liga al departamento por NOMBRE (§43)
    backend = next(r for r in loaded.repositories if r.name == "backend")
    assert backend.department == "YouTube"
    # core-lib es repositorio de organización (sin departamento)
    core = next(r for r in loaded.repositories if r.name == "core-lib")
    assert core.department is None


def test_build_structure_siembra_roles_por_defecto():
    struct = build_structure("Org", departments=["D"])
    names = {r.name for r in struct.roles}
    assert set(DEFAULT_ROLES) <= names


def test_render_structure_no_incluye_estado_operativo(tmp_path):
    """§43: el árbol solo declara configuración; el estado operativo
    (tickets, locks, ejecuciones) vive en la base de datos."""
    struct = build_structure("Org", departments=["D"])
    files = render_structure(struct)
    rels = "\n".join(files)
    assert "ticket" not in rels.lower()
    assert "lock" not in rels.lower()
    assert "execution" not in rels.lower()
    assert "organization.yml" in files
    assert "policies/.gitkeep" in files


def test_generate_enterprise_escribe_arbol_completo(tmp_path):
    written = generate_enterprise(
        tmp_path,
        "Google",
        departments=["YouTube"],
        repositories={"YouTube": ["backend"]},
    )
    assert (tmp_path / "organization.yml").exists()
    assert (tmp_path / "departments/youtube/department.yml").exists()
    assert (
        tmp_path / "departments/youtube/repositories/backend.yml"
    ).exists()
    assert (tmp_path / "roles/admin.yml").exists()
    assert (tmp_path / ".github/workflows/geas-sync.yml").exists()
    # Estado operativo nunca se escribe aquí
    tree = " ".join(str(p.relative_to(tmp_path)) for p in written)
    assert "active-ticket" not in tree
    # La carga del árbol generado es válida
    loaded = load_structure(tmp_path)
    assert loaded.org_name == "Google"
    assert loaded.profile == "enterprise"
    assert len(loaded.roles) == len(DEFAULT_ROLES)


def test_render_ci_workflow_es_sync_ligero():
    wf = render_ci_workflow()
    assert "geas sync ." in wf


def test_load_sin_organization_yml_falla_claro(tmp_path):
    with pytest.raises(ValueError, match="MISSING_ORGANIZATION"):
        load_structure(tmp_path)


def test_load_permiso_desconocido_falla(tmp_path):
    _write(
        tmp_path,
        "organization.yml",
        "name: Org\nprofile: enterprise\n",
    )
    _write(
        tmp_path,
        "roles/dev.yml",
        "name: dev\npermissions: ticket:wat\n",
    )
    with pytest.raises(ValueError, match="UNKNOWN_PERMISSION"):
        load_structure(tmp_path)


def test_load_departamento_duplicado_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(tmp_path, "departments/a/department.yml", "name: Duplicado\n")
    _write(tmp_path, "departments/b/department.yml", "name: Duplicado\n")
    with pytest.raises(ValueError, match="DEPARTMENT_DUPLICATE"):
        load_structure(tmp_path)


def test_load_policy_departamento_inexistente_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(
        tmp_path,
        "policies/x.yml",
        "name: code-owners\ndepartment: NoExiste\npermissions: ticket:read\n",
    )
    with pytest.raises(ValueError, match="POLICY_UNKNOWN_DEPARTMENT"):
        load_structure(tmp_path)


def test_load_repo_en_depto_sin_department_yml_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(
        tmp_path,
        "departments/huérfano/repositories/backend.yml",
        "name: backend\n",
    )
    with pytest.raises(ValueError, match="REPOSITORY_UNKNOWN_DEPARTMENT"):
        load_structure(tmp_path)


def test_load_estructura_por_nivel_departamento(tmp_path):
    """§43: la dependencia entre tickets coordina trabajo entre
    departamentos SIN conceder acceso al código del otro depto."""
    _write(tmp_path, "organization.yml", "name: Google\nprofile: enterprise\n")
    _write(
        tmp_path,
        "departments/youtube/department.yml",
        "name: YouTube\n",
    )
    _write(
        tmp_path,
        "departments/youtube/repositories/backend.yml",
        "name: backend\nurl: https://github.com/google/youtube\n",
    )
    _write(
        tmp_path,
        "departments/gmail/department.yml",
        "name: Gmail\n",
    )
    _write(
        tmp_path,
        "departments/gmail/repositories/backend.yml",
        "name: backend\n",
    )
    loaded = load_structure(tmp_path)
    you_backend = next(
        r
        for r in loaded.repositories
        if r.name == "backend" and r.department == "YouTube"
    )
    # Dos deptos pueden tener repos con el mismo NOMBRE: el árbol los
    # distingue por departamento (almacenados por id en la DB)
    assert {r.department for r in loaded.repositories} == {"YouTube", "Gmail"}
    assert you_backend.url == "https://github.com/google/youtube"

def test_load_departamento_con_padre_desconocido_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(tmp_path, "departments/a/department.yml", "name: A\nparent: NoExiste\n")
    with pytest.raises(ValueError, match="DEPARTMENT_UNKNOWN_PARENT"):
        load_structure(tmp_path)


def test_load_departamento_auto_padre_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(tmp_path, "departments/a/department.yml", "name: A\nparent: A\n")
    with pytest.raises(ValueError, match="DEPARTMENT_SELF_PARENT"):
        load_structure(tmp_path)


def test_load_departamento_ciclo_falla(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(tmp_path, "departments/a/department.yml", "name: A\nparent: B\n")
    _write(tmp_path, "departments/b/department.yml", "name: B\nparent: A\n")
    with pytest.raises(ValueError, match="DEPARTMENT_CYCLE"):
        load_structure(tmp_path)


def test_load_jerarquia_valida_carga_con_padre(tmp_path):
    _write(tmp_path, "organization.yml", "name: Org\n")
    _write(tmp_path, "departments/a/department.yml", "name: A\nparent: Raiz\n")
    _write(tmp_path, "departments/raiz/department.yml", "name: Raiz\n")
    loaded = load_structure(tmp_path)
    by_name = {d.name: d for d in loaded.departments}
    assert by_name["A"].parent == "Raiz"
    assert by_name["Raiz"].parent is None
