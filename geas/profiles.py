"""Perfiles de despliegue (spec §36) — MINIMAL / TEAM / ENTERPRISE.

Matriz lógica pura: qué features habilita cada perfil. Copier pregunta
`deployment_profile` al generar (§35) y este módulo traduce el nombre del
perfil a un `ProfileFlags` con las features que deja activas (§36).

Perfiles (§43: un único GEAS modular — el despliegue activa solo lo
necesario, sin arrastrar dependencias de otro perfil):
- MINIMAL ("Individual"): CLI + Git + tickets + SQLite + dependencias +
  locks básicos (§36) — el núcleo de coordinación del MVP 1. Sin
  servidor central, sin SSO, sin organización multiusuario.
- TEAM:       añade Web UI, API, MCP, agentes de IA, RBAC (roles y
              permisos), audit, event log y PostgreSQL (§36/§42).
              Varios desarrolladores y agentes contra la misma
              instancia compartida. SIN departamentos (§43).
- ENTERPRISE: añade organización multi-departamento, políticas,
              SSO, observabilidad y RBAC avanzado (§36/§43).

Contradicción resuelta (§43 vs matriz §36 previa): TEAM.departments
era True en §36; la definición de producto §43 define Team SIN
departamentos (Enterprise los tiene). Manda §43.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileFlags:
    """Features que deja activas un perfil de despliegue (§36)."""

    # MVP 1 — núcleo de coordinación (siempre activo, todo perfil)
    cli: bool = True
    git: bool = True
    tickets: bool = True
    json_sqlite: bool = True  # almacenamiento "archivos + SQLite" (§8)
    dependencies: bool = True  # bloqueo de dependencias entre tickets (§14)
    locks: bool = True  # locks básicos por recurso (§15)

    # MVP 2 — coordinación de equipo
    web_ui: bool = False
    mcp_enabled: bool = False
    ai_agents: bool = False
    departments: bool = False
    rbac: bool = False
    audit: bool = False
    event_log: bool = False
    postgres: bool = False  # almacenamiento PostgreSQL (§42)

    # MVP 3 — empresa
    policies: bool = False  # políticas declarativas (§43)
    sso: bool = False
    observability: bool = False
    multi_department: bool = False
    advanced_rbac: bool = False

    def enabled(self) -> list[str]:
        """Nombres de las features que el perfil deja activas (§36).

        `cli` no cuenta: el CLI es el punto de entrada y se asume en todo
        perfil (el comment del test §36 lo dice)."""
        return [
            name
            for name, value in self.__dict__.items()
            if value and name != "cli"
        ]


MINIMAL = ProfileFlags()

TEAM = ProfileFlags(
    web_ui=True,
    mcp_enabled=True,
    ai_agents=True,
    # §43: Team SIN departamentos — la matriz §36 previa lo dejaba True;
    # la definición de producto §43 mueve departamentos/policies a ENTERPRISE.
    departments=False,
    rbac=True,
    audit=True,
    event_log=True,
    postgres=True,  # §42 MVP 2: TEAM usa PostgreSQL por defecto
)

ENTERPRISE = ProfileFlags(
    web_ui=True,
    mcp_enabled=True,
    ai_agents=True,
    departments=True,
    rbac=True,
    audit=True,
    event_log=True,
    postgres=True,
    policies=True,
    sso=True,
    observability=True,
    multi_department=True,
    advanced_rbac=True,
)

# El dict canónico. Copier.yml usa `deployment_profile` y `profile_flags`
# traduce el nombre elegido a MINIMAL/TEAM/ENTERPRISE (§36). Los tres
# nombres (PROFILE/PROFILES/profiles) son alias del MISMO dict para que el
# import de cualquier test/template apunte siempre al mismo objeto.
_PROFILES: dict[str, ProfileFlags] = {
    "MINIMAL": MINIMAL,
    "TEAM": TEAM,
    "ENTERPRISE": ENTERPRISE,
}

PROFILE = _PROFILES
PROFILES = _PROFILES
profiles = _PROFILES


def _key(name: str | None) -> str:
    """Normaliza un nombre de perfil al canon interno.

    §43: el usuario elige `1. Individual 2. Team 3. Enterprise` — se
    aceptan los tres nombres y sus números. El canon interno sigue
    siendo MINIMAL/TEAM/ENTERPRISE (§36, retrocompatible)."""
    raw = (name or "").strip().upper()
    return _ALIASES.get(raw, raw)


def profile_flags(name: str | None) -> ProfileFlags:
    """Perfil para un nombre, normalizando mayúsculas y espacios.

    Desconocidos y vacíos caen en MINIMAL — el default de Copier (§35/§36)."""
    return PROFILES.get(_key(name), MINIMAL)


def backend_for(name: str | None) -> str:
    """Backend de almacenamiento que el perfil reclama (§42).

    LEE el flag `postgres` que §36 ya declaró en el perfil (TEAM y
    ENTERPRISE → True), pero la traducción flag→backend es lógica pura:
    MINIMAL → "sqlite", TEAM/ENTERPRISE → "postgres". El driver real
    (psycopg + instancia) queda como deuda de infra anotada — igual que
    copier §35/§36: el contrato se fija, la infra se declara pendiente."""
    return "postgres" if profile_flags(name).postgres else "sqlite"


# Alias de nombre (§43): los nombres de producto Individual/Team/Enterprise
# y el selector numérico 1/2/3 traducen al canon interno MINIMAL/TEAM/
# ENTERPRISE (§36, retrocompatible con tests y templates).
_ALIASES: dict[str, str] = {
    "INDIVIDUAL": "MINIMAL",
    "1": "MINIMAL",
    "2": "TEAM",
    "3": "ENTERPRISE",
}

# Nombre de producto por canon — lo que se guarda en la columna
# organizations.profile y en organization.yml (§43).
PROFILE_NAMES: dict[str, str] = {
    "MINIMAL": "individual",
    "TEAM": "team",
    "ENTERPRISE": "enterprise",
}


def normalize_profile(name: str | None) -> str:
    """Nombre de producto normalizado (individual|team|enterprise).

    §43: acepta Individual/Team/Enterprise y 1/2/3. Perfiles desconocidos
    fallan claro con ValueError — nunca caen en silencio a Individual."""
    key = _key(name)
    if key not in PROFILE_NAMES:
        raise ValueError(
            f"PROFILE_UNKNOWN: '{name}' — usa individual, team o "
            "enterprise (1/2/3)"
        )
    return PROFILE_NAMES[key]

# Secciones declarativas (§43) que cada perfil puede sincronizar con la
# base de datos vía `geas sync`. Una sección que el perfil de la
# organización no soporta se rechaza con error claro — nunca se ignora en
# silencio. Individual: repositorios sí (Git integration es núcleo);
# departamentos y policies son Enterprise.
_SECTIONS: dict[str, frozenset[str]] = {
    "MINIMAL": frozenset({"organization", "repositories"}),
    "TEAM": frozenset({"organization", "repositories", "roles"}),
    "ENTERPRISE": frozenset(
        {"organization", "repositories", "roles", "departments", "policies"}
    ),
}


def sections_for(name: str | None) -> frozenset[str]:
    """Secciones declarativas que el perfil deja sincronizar (§43)."""
    return _SECTIONS.get(_key(name), _SECTIONS["MINIMAL"])


def profile_supports(name: str | None, section: str) -> bool:
    """¿Puede este perfil sincronizar la sección declarativa dada? (§43)."""
    return section in sections_for(name)


# Componentes que activa cada perfil (§43) — el mapa de producto, para
# `geas org show` y la documentación. Individual no arrastra nada de
# Enterprise: aquí no hay Departments/Policies/SSO/Governance.
_COMPONENTS: dict[str, tuple[str, ...]] = {
    "MINIMAL": (
        "CLI",
        "SQLite",
        "Git integration",
        "Tickets",
        "Dependencies",
        "Locks",
    ),
    "TEAM": (
        "CLI",
        "API",
        "PostgreSQL",
        "MCP",
        "Users/Agents",
        "Tickets",
        "Dependencies",
        "Resource locks",
        "Permissions",
        "Audit",
        "Git/CI integration",
    ),
    "ENTERPRISE": (
        "Organization",
        "Departments",
        "Users",
        "Agents",
        "Roles",
        "Policies",
        "Repositories",
        "Tickets",
        "Dependencies",
        "Resources",
        "Locks",
        "Executions",
        "Audit/Events",
        "MCP",
        "Git integrations",
        "CI/CD",
        "SSO/OIDC",
        "Governance",
    ),
}


def components(name: str | None) -> list[str]:
    """Componentes que el perfil activa, en orden de producto (§43)."""
    return list(_COMPONENTS.get(_key(name), _COMPONENTS["MINIMAL"]))
