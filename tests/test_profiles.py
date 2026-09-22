"""Tests §36 — perfiles de despliegue MINIMAL/TEAM/ENTERPRISE.

La matriz es lógica pura (geas/profiles.py): qué features habilita cada
perfil. Copier la usa para condicionar los defaults al generar (§36).
"""

from __future__ import annotations

import pytest

from geas.profiles import (
    ENTERPRISE,
    MINIMAL,
    PROFILES,
    TEAM,
    backend_for,
    profile_flags,
)


def test_minimal_no_habilita_features_de_equipo():
    assert MINIMAL.web_ui is False
    assert MINIMAL.mcp_enabled is False
    assert MINIMAL.ai_agents is False
    assert MINIMAL.departments is False
    assert not MINIMAL.sso
    assert not MINIMAL.observability


def test_team_activa_colaboracion_no_sso():
    assert TEAM.web_ui is True
    assert TEAM.mcp_enabled is True
    assert TEAM.ai_agents is True
    assert TEAM.departments is True
    assert TEAM.sso is False  # SSO es de ENTERPRISE (§36)
    assert TEAM.observability is False


def test_enterprise_incluye_sso_y_observabilidad():
    assert ENTERPRISE.sso is True
    assert ENTERPRISE.observability is True
    # Hereda todo TEAM
    assert ENTERPRISE.web_ui and ENTERPRISE.mcp_enabled
    assert ENTERPRISE.ai_agents and ENTERPRISE.departments


def test_profiles_dict_contiene_los_tres_perfiles():
    assert set(PROFILES) == {"MINIMAL", "TEAM", "ENTERPRISE"}


def test_profile_defaults_a_minimal_sin_argumento():
    assert profile_flags("") is MINIMAL
    assert profile_flags(None) is MINIMAL


def test_profile_desconocido_vuelve_a_minimal():
    assert profile_flags("TYPO") is MINIMAL


def test_profile_es_case_insensitive():
    assert profile_flags("enterprise") is ENTERPRISE
    assert profile_flags("  Team ") is TEAM


def test_enabled_enumera_solo_features_activas():
    enabled = ENTERPRISE.enabled()
    assert "web_ui" in enabled
    assert "sso" in enabled
    assert "observability" in enabled


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("MINIMAL", MINIMAL),
        ("TEAM", TEAM),
        ("ENTERPRISE", ENTERPRISE),
    ],
)
def test_profile_flags_mapeo_directo(name, expected):
    assert profile_flags(name) is expected


# §42 — PostgreSQL (MVP 2, infra declarativa)
#
# La spec §42 (docs/SPEC.md) declara PostgreSQL como backend de
# TEAM/ENTERPRISE (§602: "los locks se gestionan mediante operaciones
# transaccionales en PostgreSQL"; §1046: "los agentes no acceden
# directamente a PostgreSQL"). El flag `postgres` vive en profiles.py
# (§36) pero ningún test lo fija todavía: es lo que añade este bloque.
# El driver real (psycopg + instancia) queda como deuda de infra,
# igual que copier §35: se fija el contrato, no la infra.


@pytest.mark.parametrize(
    ("profile", "postgres"),
    [
        (MINIMAL, False),
        (TEAM, True),        # §42 MVP 2: TEAM usa PostgreSQL por defecto
        (ENTERPRISE, True),  # §42 MVP 2: ENTERPRISE usa PostgreSQL
    ],
)
def test_profile_postgres_flag_contrato_42(profile, postgres):
    """§42: el flag `postgres` declara qué perfiles cablean PG (#602/§1046)."""
    assert profile.postgres is postgres


def test_profile_postgres_no_afecta_flags_existentes():
    """§42: `postgres` es aditivo — TEAM/ENTERPRISE lo activan sin apagar
    nada de lo que §36 ya declaró (web_ui, mcp, rbac, audit...)."""
    team_enabled = {f for f in TEAM.enabled()}
    assert "web_ui" in team_enabled
    assert "mcp_enabled" in team_enabled
    assert "rbac" in team_enabled
    assert "audit" in team_enabled
    assert "postgres" in team_enabled  # §42 añade la feature, no la sustituye


def test_backend_for_costura_42():
    """§42: la costura flag→backend. `backend_for` traduce el flag
    declarativo §36 (`postgres: bool` de TEAM/ENTERPRISE) a la elección
    de backend que Storage debe usar (§602). Nunca devuelve algo fuera
    de la matriz declarada.

    MINIMAL → sqlite (§15, retrocompatible). TEAM/ENTERPRISE → postgres
    (§602: locks transaccionales en PostgreSQL). El driver real queda
    como deuda de infra anotada (patrón copier §35/§36): pedir postgres
    sin driver falla claro en Storage, nunca cae en silencio a SQLite.
    """
    assert backend_for("MINIMAL") == "sqlite"
    assert backend_for("TEAM") == "postgres"
    assert backend_for("ENTERPRISE") == "postgres"
    # Desconocidos y vacíos → MINIMAL (§35/§36 default) → sqlite
    assert backend_for(None) == "sqlite"
    assert backend_for("") == "sqlite"
    assert backend_for("NO_EXISTE") == "sqlite"
    # §42 aditivo NO se duplica aquí: que pedir postgres no apaga
    # web_ui/rbac/audit lo asegura test_profile_postgres_no_afecta_
    # flags_existentes (§36 ya lo cablea). Estas líneas eran AttributeError
    # = .team/.enterprise no son flags de ProfileFlags (son NOMBRES de
    # perfil) — el contrato aditivo vive en el test §36.
