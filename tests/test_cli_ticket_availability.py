"""§34/§15: `geas ticket list <org> [free|busy]` — filtro libre/ocupado + badge.

Un ticket está:
- **libre** → ninguno de sus recursos tiene un lock vivo (§15: expires_at > ahora).
- **ocupado** → al menos un recurso tiene lock activo; el badge muestra
  `desde <created_at> por <actor>`.

El test prueba el handler real `geas.cli._cmd_ticket` (no lógica duplicada):
captura su stdout con `capsys` y verifica los badges literales.
"""

from __future__ import annotations

import pytest

from geas.cli import _cmd_ticket
from geas.models import (
    Department,
    Organization,
    Repository,
    Resource,
    ResourceLock,
    Ticket,
)
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path) -> Storage:
    return Storage(tmp_path / "geas.db")


@pytest.fixture
def org(storage: Storage) -> Organization:
    o = Organization(name="Acme")
    storage.create_organization(o)
    return o


@pytest.fixture
def repo(storage: Storage, org: Organization) -> Repository:
    dept = Department(organization_id=org.id, name="Backend")
    storage.create_department(dept)
    r = Repository(
        organization_id=org.id,
        department_id=dept.id,
        name="api",
        provider="github",
        url="https://github.com/acme/api",
    )
    storage.create_repository(r)
    return r


def _ticket_with_resource(storage: Storage, repo: Repository, title: str) -> tuple[Ticket, Resource]:
    res = Resource(repository_id=repo.id, path=f"{title}.py")
    storage.create_resource(res)
    t = Ticket(
        organization_id=repo.organization_id,
        repository_id=repo.id,
        title=title,
        resources=[res.id],
    )
    storage.create_ticket(t)
    return t, res


def _lock_for(storage: Storage, res: Resource, ticket: Ticket, actor: str) -> ResourceLock:
    # §15: lock vivo con expires_at en 2099; created_at queda en el "ahora" de creación.
    lock = ResourceLock(
        resource_id=res.id,
        ticket_id=ticket.id,
        actor_id=actor,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    storage.create_lock(lock)
    return lock


def test_ticket_list_all_keeps_contract(storage: Storage, org, repo, capsys):
    """Sin flag: contrato previo intacto — libres y ocupados, sin badges."""
    _ticket_with_resource(storage, repo, "Chat")
    _ticket_with_resource(storage, repo, "Auth")
    assert _cmd_ticket(storage, ["list", org.id]) == 0
    out = capsys.readouterr().out
    assert "Chat" in out and "Auth" in out
    assert "libre" not in out and "ocupado" not in out


def test_ticket_list_free_filters_busy(storage: Storage, org, repo, capsys):
    """`free` → solo tickets sin locks vivos (todos sus archivos disponibles)."""
    t_free, _ = _ticket_with_resource(storage, repo, "Chat")
    t_busy, res_busy = _ticket_with_resource(storage, repo, "Auth")
    _lock_for(storage, res_busy, t_busy, actor="agent-1")

    assert _cmd_ticket(storage, ["list", org.id, "free"]) == 0
    out = capsys.readouterr().out
    assert t_free.title in out
    assert t_busy.title not in out  # ocupado → excluido
    assert "[libre]" in out


def test_ticket_list_busy_show_actor_and_since(storage: Storage, org, repo, capsys):
    """`busy` → solo ocupados, con `desde <created_at> por <actor>` (§15)."""
    t_free, _ = _ticket_with_resource(storage, repo, "Chat")
    t_busy, res_busy = _ticket_with_resource(storage, repo, "Auth")
    lock = _lock_for(storage, res_busy, t_busy, actor="agent-1")

    assert _cmd_ticket(storage, ["list", org.id, "busy"]) == 0
    out = capsys.readouterr().out
    assert t_busy.title in out
    assert t_free.title not in out  # libre → excluido
    # badge: desde el created_at real del lock y por el actor fallback (id corto)
    assert "ocupado desde" in out
    assert f"desde {lock.created_at}" in out
    assert "por agent-1" in out


def test_ticket_list_busy_resolves_actor_name(storage: Storage, org, repo, capsys):
    """Si el actor existe como User, el badge usa su nombre (no el id corto)."""
    from geas.models import User

    actor = User(organization_id=org.id, name="Marta", email="marta@acme.dev")
    storage.create_user(actor)
    t_busy, res_busy = _ticket_with_resource(storage, repo, "Auth")
    _lock_for(storage, res_busy, t_busy, actor=actor.id)

    assert _cmd_ticket(storage, ["list", org.id, "busy"]) == 0
    out = capsys.readouterr().out
    assert "por Marta" in out


def test_ticket_list_free_ignores_expired_locks(storage: Storage, org, repo, capsys):
    """§15: un lock expirado (expires_at <= ahora) no ocupa — el ticket es libre."""
    t_free, res = _ticket_with_resource(storage, repo, "Chat")

    expired = ResourceLock(
        resource_id=res.id,
        ticket_id=t_free.id,
        actor_id="agent-x",
        created_at="2000-01-01T00:00:00+00:00",
        expires_at="2000-01-02T00:00:00+00:00",  # caducado hace años
    )
    storage.create_lock(expired)

    assert _cmd_ticket(storage, ["list", org.id, "free"]) == 0
    out = capsys.readouterr().out
    assert t_free.title in out
    assert "[libre]" in out
