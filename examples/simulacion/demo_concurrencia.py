"""Demo de concurrencia multi-persona (§39/§24).

Simula dos personas (Ana, humana, y Bot, agente) trabajando a la vez
sobre la misma base de datos SQLite y el mismo documento. Demuestra:

1. Claim atómico de tickets: dos personas no reclaman el mismo ticket.
2. Lock de recursos: dos personas no editan el mismo documento a la vez.
3. Liberación al terminar: el reintento tras el finish sí funciona.

Sin servidor: `Storage` abre una SQLite local y las operaciones de
claim/lock son atómicas en una sola sentencia/transacción SQL.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from geas.models import (
    Agent,
    Organization,
    Repository,
    Resource,
    ResourceType,
    Role,
    Ticket,
    User,
    Visibility,
)
from geas.storage import Storage

DOCUMENTO_COMPARTIDO = "docs/diseno.md"


def _mk(storage: Storage, org: Organization) -> None:
    """Crea la organización, un repo, dos personas y dos documentos."""
    repo = Repository(
        organization_id=org.id,
        name="web-ui",
        provider="github",
        url="https://github.com/acme/web-ui",
        default_branch="main",
        visibility=Visibility.PRIVATE,
    )
    storage.create_repository(repo)

    r_developer = Role(organization_id=org.id, name="developer", permissions=[])
    storage.create_role(r_developer)

    ana = User(
        organization_id=org.id,
        name="Ana",
        email="ana@acme.test",
        role_id=r_developer.id,
    )
    storage.create_user(ana)
    bot = Agent(
        organization_id=org.id,
        name="Bot",
        provider="anthropic",
        model="claude",
        role_id=r_developer.id,
    )
    storage.create_agent(bot)

    res_shared = Resource(
        repository_id=repo.id,
        path=DOCUMENTO_COMPARTIDO,
        type=ResourceType.FILE,
    )
    res_api = Resource(
        repository_id=repo.id,
        path="docs/api.md",
        type=ResourceType.FILE,
    )
    storage.create_resource(res_shared)
    storage.create_resource(res_api)
    return ana, bot, repo, res_shared, res_api  # type: ignore[return-value]


def main() -> int:
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="geas-demo-"))
    if keep:
        print(f"BD temporal: {tmp / 'geas.db'}  (--keep la conserva)")
    db = Storage(tmp / "geas.db")

    org = Organization(name="Acme", profile="team")
    db.create_organization(org)
    ana, bot, repo, res_shared, res_api = _mk(db, org)  # type: ignore[misc]

    print("== Escenario: dos personas, un solo documento compartido ==\n")

    # ── Paso 1: Ana reclama el documento compartido ────────────────────
    ticket_ana = Ticket(
        organization_id=org.id,
        repository_id=repo.id,
        title="Rediseñar el header",
        resources=[res_shared.id, res_api.id],
        assigned_actor_id=ana.id,
    )
    db.create_ticket(ticket_ana)
    claimed = db.start_ticket(ticket_ana.id, commit_before="aaa111", actor_id=ana.id)
    ok, _ = db.acquire_locks(
        [res_shared.id, res_api.id], ticket_ana.id, ana.id
    )
    print(
        f"→ Ana (humana) empieza {ticket_ana.id} "
        f"y reclama {DOCUMENTO_COMPARTIDO} + docs/api.md "
        f"→ claim={claimed} locks={'OK' if ok else 'BLOQUEADO'}"
    )

    # ── Paso 2: Bot intenta el MISMO documento a la vez ────────────────
    ticket_bot = Ticket(
        organization_id=org.id,
        repository_id=repo.id,
        title="Mover el CTA",
        resources=[res_shared.id],
        assigned_actor_id=bot.id,
    )
    db.create_ticket(ticket_bot)
    claimed_bot = db.start_ticket(ticket_bot.id, commit_before="bbb222", actor_id=bot.id)
    ok_bot, blocked_bot = db.acquire_locks(
        [res_shared.id], ticket_bot.id, bot.id
    )
    if ok_bot:
        print("→ Bot (agente) reclama el mismo documento → OK (¡inesperado!)")
    else:
        print(
            f"→ Bot (agente) reclama el MISMO documento a la vez → "
            f"claim={claimed_bot} lock=BLOQUEADO por {blocked_bot} ✓\n"
            "  (Bot sí reclama SU ticket, pero el documento ya está "
            "lockeado — la BD decide, no la cola)"
        )

    # ── Paso 3: Ana termina → libera sus locks ─────────────────────────
    db.complete_ticket(ticket_ana.id, commit_after="ccc333", result="header listo")
    db.release_locks_for_ticket(ticket_ana.id)
    print(f"\n→ Ana termina {ticket_ana.id} (commit_after + libera locks) → OK")

    # ── Paso 4: Bot reintenta ahora que el documento está libre ────────
    ok_retry, _ = db.acquire_locks(
        [res_shared.id], ticket_bot.id, bot.id
    )
    print(
        f"→ Bot reintenta {ticket_bot.id} tras el finish de Ana "
        f"→ locks={'OK ✓ (ya puede editar)' if ok_retry else 'BLOQUEADO'}"
    )

    # ── Estado final ───────────────────────────────────────────────────
    via_ana = db.get_ticket(ticket_ana.id)
    via_bot = db.get_ticket(ticket_bot.id)
    print("\nEstado final:")
    print(f"  {ticket_ana.id} → {via_ana.status.value}  (Ana)")
    print(f"  {ticket_bot.id} → {via_bot.status.value}  (Bot, con lock del documento)")
    print(
        "  docs/diseno.md → lock de "
        + (via_bot.id if ok_retry else "nadie")
        + " (recurso compartido)"
    )

    if claimed and not ok_bot and ok_retry:
        print("\n✓ demo correcta: el lock de recurso impidió la edición simultánea")
        return 0
    print("\n✗ la demo no se comportó como se esperaba", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())