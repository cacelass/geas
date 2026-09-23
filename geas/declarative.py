"""geas.declarative — Estructura declarativa Enterprise (§43).

La estructura versionada en Git define el estado DESEADO de la
organización; la base de datos de GEAS es la única fuente de verdad del
estado OPERATIVO (tickets, locks, ejecuciones...). Este módulo lee,
renderiza y valida la estructura declarativa en un formato simple
`clave: valor` — sin YAML, zero deps, igual que `.orchestrator/config.yml`.

Árbol soportado (§43):

    organization.yml                            organización + perfil
    departments/<slug>/department.yml           departamento
    departments/<slug>/repositories/<slug>.yml  repositorio de un depto
    repositories/<slug>.yml                     repositorio de organización
    roles/<slug>.yml                            rol declarativo (§7)
    policies/<slug>.yml                         política declarativa
    .github/workflows/geas-sync.yml             CI opcional (geas sync)

`geas sync` aplica esta estructura contra la base de datos de forma
idempotente. El estado operativo NUNCA se escribe en este árbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from geas.models import ALL_PERMISSIONS, DEFAULT_ROLES


def _read_kv(path: Path) -> dict[str, str]:
    """Parser `clave: valor` — mismo estilo que WorkContext (§33)."""
    config: dict[str, str] = {}
    if not path.exists():
        return config
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        config[key.strip()] = value.strip()
    return config


def slug(name: str) -> str:
    """Slug de fichero a partir de un nombre declarativo (§43)."""
    cleaned = "".join(c if c.isalnum() else "-" for c in name.lower())
    return "-".join(part for part in cleaned.split("-") if part)


def _perm_list(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _perm_csv(permissions: list[str]) -> str:
    return ", ".join(permissions)


# ─── Modelos declarativos ────────────────────────────────────────────


@dataclass
class DeclarativeDepartment:
    name: str
    parent: str | None = None
    description: str = ""

    @property
    def slug(self) -> str:
        return slug(self.name)


@dataclass
class DeclarativeRepository:
    name: str
    department: str | None = None  # nombre del departamento ("" = org)
    url: str = ""
    provider: str = "local"
    default_branch: str = "main"
    visibility: str = "PRIVATE"

    @property
    def slug(self) -> str:
        return slug(self.name)


@dataclass
class DeclarativeRole:
    name: str
    permissions: list[str] = field(default_factory=list)


@dataclass
class DeclarativePolicy:
    name: str
    department: str | None = None
    description: str = ""
    permissions: list[str] = field(default_factory=list)


@dataclass
class DeclarativeStructure:
    """La organización deseada, tal y como la declara el árbol §43."""

    org_name: str
    org_description: str = ""
    profile: str = "individual"
    departments: list[DeclarativeDepartment] = field(default_factory=list)
    repositories: list[DeclarativeRepository] = field(default_factory=list)
    roles: list[DeclarativeRole] = field(default_factory=list)
    policies: list[DeclarativePolicy] = field(default_factory=list)


# ─── Loader ──────────────────────────────────────────────────────────


def _validate_permissions(section: str, owner: str, permissions: list[str]) -> None:
    unknown = sorted(set(permissions) - set(ALL_PERMISSIONS))
    if unknown:
        raise ValueError(
            f"UNKNOWN_PERMISSION: {section} '{owner}' declara permisos "
            f"inexistentes ({', '.join(unknown[:5])})"
        )


def load_structure(root: str | Path) -> DeclarativeStructure:
    """Lee el árbol declarativo §43 y lo valida.

    Raise ValueError con mensaje claro si falta organization.yml, hay
    duplicados o se declara un permiso fuera del catálogo §7."""
    root = Path(root)
    org_cfg = _read_kv(root / "organization.yml")
    if not org_cfg.get("name"):
        raise ValueError(
            "MISSING_ORGANIZATION: falta organization.yml con 'name' en "
            f"{root} (estructura declarativa §43)"
        )

    departments: list[DeclarativeDepartment] = []
    for dept_file in sorted((root / "departments").glob("*/department.yml")):
        dept_slug = dept_file.parent.name
        cfg = _read_kv(dept_file)
        departments.append(
            DeclarativeDepartment(
                name=cfg.get("name") or dept_slug,
                parent=cfg.get("parent") or None,
                description=cfg.get("description", ""),
            )
        )

    dept_by_slug = {d.slug: d for d in departments}

    repositories: list[DeclarativeRepository] = []
    # Repos de organización (sin departamento)
    for repo_file in sorted((root / "repositories").glob("*.yml")):
        cfg = _read_kv(repo_file)
        if not cfg.get("name"):
            raise ValueError(f"MISSING_NAME: repositorio {repo_file} sin 'name'")
        repositories.append(
            DeclarativeRepository(
                name=cfg["name"],
                department=None,
                url=cfg.get("url", ""),
                provider=cfg.get("provider", "local"),
                default_branch=cfg.get("default_branch", "main"),
                visibility=cfg.get("visibility", "PRIVATE"),
            )
        )
    # Repos por departamento — el nombre del depto sale de su department.yml
    departments_dir = root / "departments"
    if departments_dir.exists():
        for dept_dir in sorted(departments_dir.iterdir()):
            repos_dir = dept_dir / "repositories"
            if not repos_dir.exists():
                continue
            for repo_file in sorted(repos_dir.glob("*.yml")):
                cfg = _read_kv(repo_file)
                if not cfg.get("name"):
                    raise ValueError(
                        f"MISSING_NAME: repositorio {repo_file} sin 'name'"
                    )
                dept = dept_by_slug.get(dept_dir.name)
                if dept is None:
                    raise ValueError(
                        "REPOSITORY_UNKNOWN_DEPARTMENT: "
                        f"{repo_file} vive en un directorio sin department.yml"
                    )
                repositories.append(
                    DeclarativeRepository(
                        name=cfg["name"],
                        department=dept.name,
                        url=cfg.get("url", ""),
                        provider=cfg.get("provider", "local"),
                        default_branch=cfg.get("default_branch", "main"),
                        visibility=cfg.get("visibility", "PRIVATE"),
                    )
                )

    roles: list[DeclarativeRole] = []
    for role_file in sorted((root / "roles").glob("*.yml")):
        cfg = _read_kv(role_file)
        if not cfg.get("name"):
            raise ValueError(f"MISSING_NAME: rol {role_file} sin 'name'")
        permissions = _perm_list(cfg.get("permissions", ""))
        _validate_permissions("rol", cfg["name"], permissions)
        roles.append(DeclarativeRole(name=cfg["name"], permissions=permissions))

    policies: list[DeclarativePolicy] = []
    for policy_file in sorted((root / "policies").glob("*.yml")):
        cfg = _read_kv(policy_file)
        if not cfg.get("name"):
            raise ValueError(f"MISSING_NAME: política {policy_file} sin 'name'")
        permissions = _perm_list(cfg.get("permissions", ""))
        _validate_permissions("política", cfg["name"], permissions)
        policies.append(
            DeclarativePolicy(
                name=cfg["name"],
                department=cfg.get("department") or None,
                description=cfg.get("description", ""),
                permissions=permissions,
            )
        )

    # Validación global: duplicados y referencias a departamentos inexistentes
    names = [d.name for d in departments]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"DEPARTMENT_DUPLICATE: {', '.join(dupes)}")
    names = [r.name for r in roles]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"ROLE_DUPLICATE: {', '.join(dupes)}")
    # Repositorios: únicos por (departamento, nombre) — YouTube/Backend y
    # Gmail/Backend conviven (§43: dos deptos pueden tener repo 'backend').
    repo_seen: dict[tuple[str, str], bool] = {}
    for repo in repositories:
        key = (repo.department or "", repo.name)
        if key in repo_seen:
            raise ValueError(
                f"REPOSITORY_DUPLICATE: '{repo.name}' @ "
                f"{repo.department or 'organización'}"
            )
        repo_seen[key] = True
    names = [p.name for p in policies]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"POLICY_DUPLICATE: {', '.join(dupes)}")
    dept_names = {d.name for d in departments}
    for policy in policies:
        if policy.department and policy.department not in dept_names:
            raise ValueError(
                f"POLICY_UNKNOWN_DEPARTMENT: '{policy.name}' apunta a "
                f"'{policy.department}' que no existe"
            )
    for repo in repositories:
        if repo.department and repo.department not in dept_names:
            raise ValueError(
                f"REPOSITORY_UNKNOWN_DEPARTMENT: '{repo.name}' apunta a "
                f"'{repo.department}' que no existe"
            )

    return DeclarativeStructure(
        org_name=org_cfg["name"],
        org_description=org_cfg.get("description", ""),
        profile=org_cfg.get("profile", "individual"),
        departments=departments,
        repositories=repositories,
        roles=roles,
        policies=policies,
    )


# ─── Builder ─────────────────────────────────────────────────────────


def build_structure(
    org_name: str,
    *,
    profile: str = "enterprise",
    description: str = "",
    departments: list[str] | None = None,
    repositories: dict[str, list[str]] | None = None,
) -> DeclarativeStructure:
    """Construye la estructura declarativa a partir de datos de usuario (§43).

    `repositories` mapea nombre de departamento → repos; la clave "" son
    repos de organización. Los roles se siembran desde DEFAULT_ROLES (§7)
    para que `geas sync` tenga algo que sincronizar."""
    depts = [DeclarativeDepartment(name=name) for name in (departments or [])]
    repos: list[DeclarativeRepository] = []
    for dept_name, repo_names in (repositories or {}).items():
        for repo_name in repo_names:
            repos.append(
                DeclarativeRepository(name=repo_name, department=dept_name or None)
            )
    roles = [
        DeclarativeRole(name=name, permissions=list(perms))
        for name, perms in DEFAULT_ROLES.items()
    ]
    return DeclarativeStructure(
        org_name=org_name,
        org_description=description,
        profile=profile,
        departments=depts,
        repositories=repos,
        roles=roles,
        policies=[],
    )


# ─── Renderer ────────────────────────────────────────────────────────


def render_ci_workflow() -> str:
    """Workflow CI de ejemplo — `geas sync` es ligero y seguro para CI (§43).

    GEAS sigue siendo la fuente de verdad de la coordinación; CI solo
    sincroniza la estructura declarativa contra la base de datos."""
    return """\
name: geas-sync
on:
  push:
    paths:
      - "organization.yml"
      - "departments/**"
      - "repositories/**"
      - "roles/**"
      - "policies/**"
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Instala geas en CI (pip install geas o el canal de tu org) y
      # apunta GEAS_DB a la instancia Team/Enterprise (§43).
      - run: geas sync .
"""


def render_structure(struct: DeclarativeStructure) -> dict[str, str]:
    """Renderiza el árbol §43: ruta relativa → contenido.

    El estado operativo no aparece aquí por diseño: solo configuración
    declarativa versionable (§43)."""
    files: dict[str, str] = {}
    files["organization.yml"] = _kv_text(
        "# Geas — organización (§43, estructura declarativa)",
        {
            "name": struct.org_name,
            "description": struct.org_description,
            "profile": struct.profile,
        },
    )
    for dept in struct.departments:
        files[f"departments/{dept.slug}/department.yml"] = _kv_text(
            "# Geas — departamento (§43)",
            {
                "name": dept.name,
                "parent": dept.parent or "",
                "description": dept.description,
            },
        )
    for repo in struct.repositories:
        repo_cfg = {
            "name": repo.name,
            "url": repo.url,
            "provider": repo.provider,
            "default_branch": repo.default_branch,
            "visibility": repo.visibility,
        }
        if repo.department:
            dept_slug = slug(repo.department)
            files[
                f"departments/{dept_slug}/repositories/{repo.slug}.yml"
            ] = _kv_text("# Geas — repositorio (§43)", repo_cfg)
        else:
            files[f"repositories/{repo.slug}.yml"] = _kv_text(
                "# Geas — repositorio (§43)", repo_cfg
            )
    for role in struct.roles:
        files[f"roles/{slug(role.name)}.yml"] = _kv_text(
            "# Geas — rol declarativo (§7/§43)",
            {"name": role.name, "permissions": _perm_csv(role.permissions)},
        )
    if struct.policies:
        for policy in struct.policies:
            files[f"policies/{slug(policy.name)}.yml"] = _kv_text(
                "# Geas — política declarativa (§43)",
                {
                    "name": policy.name,
                    "department": policy.department or "",
                    "description": policy.description,
                    "permissions": _perm_csv(policy.permissions),
                },
            )
    else:
        files["policies/.gitkeep"] = ""
    return files


def _kv_text(header: str, config: dict[str, str]) -> str:
    lines = [header, ""]
    for key, value in config.items():
        lines.append(f"{key}: {value}" if value else f"{key}:")
    return "\n".join(lines) + "\n"