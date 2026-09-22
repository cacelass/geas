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
