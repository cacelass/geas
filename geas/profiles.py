"""Perfiles de despliegue (spec §36) — MINIMAL / TEAM / ENTERPRISE.

Matriz lógica pura: qué features habilita cada perfil. Copier pregunta
`deployment_profile` al generar (§35) y este módulo traduce el nombre del
perfil a un `ProfileFlags` con las features que deja activas (§36).

Perfiles:
- MINIMAL:    CLI + Git + tickets + JSON/SQLite + dependencias + locks básicos
              (§36) — el núcleo de coordinación del MVP 1.
- TEAM:       añade Web UI, MCP, agentes de IA, departamentos, RBAC, audit,
              event log y PostgreSQL (§36/§42).
- ENTERPRISE: añade SSO, observabilidad, multi-departamento y RBAC avanzado
              (§36).
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
    departments=True,
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


def profile_flags(name: str | None) -> ProfileFlags:
    """Perfil para un nombre, normalizando mayúsculas y espacios.

    Desconocidos y vacíos caen en MINIMAL — el default de Copier (§35/§36)."""
    key = (name or "").strip().upper()
    return PROFILES.get(key, MINIMAL)
