"""
geas.work — Comandos de trabajo de Geas (spec §34).

    work init            Registrar repo + generar .orchestrator/config.yml (§33)
    work sync            Sincronizar con Geas y con Git (§24)
    work status          Estado del entorno (README/NOT READY) (§25)
    work tasks           Tickets disponibles para el actor actual (§17)
    work context         Contexto del repo actual
    work ticket <id>     Mostrar un ticket
    work branch create   Crear la branch del ticket (§34)
    work commit <id>     Commit en la branch del ticket (§34)
    work pr create <id>  Push + PR de la branch del ticket (§34)
    work start <id>      Empezar ticket: claim + commit_before (§24)
    work finish <id>     Terminar ticket: commit_after + release locks (§24)
    work diff <id>       Diff entre commit_before y commit_after (§11)
    work rollback <id>   Revertir el trabajo del ticket (§11)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geas.git import LocalGitProvider
from geas.models import (
    AuditLog,
    Event,
    ResourceLock,
    TicketStatus,
)
from geas.storage import Storage


class WorkContext:
    """Contexto local de un repositorio: lee .orchestrator/config.yml."""

    def __init__(self, repo_path: str | Path | None = None):
        self.repo_path = Path(repo_path or ".")
        self.config_file = self.repo_path / ".orchestrator" / "config.yml"

    def exists(self) -> bool:
        return self.config_file.exists()

    def read_config(self) -> dict:
        if not self.exists():
            return {}
        config: dict = {}
        for line in self.config_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            config[key.strip()] = value.strip()
        return config

    def write_config(self, config: dict) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Geas — configuración del repositorio", ""]
        for key, value in config.items():
            lines.append(f"{key}: {value}")
        self.config_file.write_text("\n".join(lines) + "\n")


def _storage(db_path: str = "geas.db") -> Storage:
    return Storage(db_path)


def cmd_init(storage: Storage, args: list[str]) -> int:
    """work init — detecta el repo git, lo registra y genera config.yml (§33)."""
    path = args[0] if args else "."
    repo_path = Path(path)

    # 1. Detectar repo git
    r = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        print("NOT A GIT REPOSITORY", file=sys.stderr)
        return 1

    # 2. Identificar remote
    r = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    remote = r.stdout.strip() if r.returncode == 0 else ""
    provider = "local"
    if "github.com" in remote:
        provider = "github"
    elif "gitlab" in remote:
        provider = "gitlab"
    elif "bitbucket" in remote:
        provider = "bitbucket"
    name = repo_path.name or "repo"

    # 3. Buscar la organización — si hay varias, pedirla
    orgs = storage.list_organizations()
    if not orgs:
        print(
            "No hay organizaciones. Crea una primero: geas init '<nombre>'",
            file=sys.stderr,
        )
        return 1
    org = orgs[0]  # MVP: primera org

    # 4. Registrar repository
    from geas.models import Repository

    repo = Repository(
        organization_id=org.id,
        name=name,
        provider=provider,
        url=remote,
    )
    storage.create_repository(repo)

    # 5. Generar .orchestrator/config.yml
    ctx = WorkContext(repo_path)
    ctx.write_config(
        {
            "organization": org.name,
            "organization_id": org.id,
            "department": "",
            "repository": name,
            "repository_id": repo.id,
            "provider": provider,
            "url": remote,
            "mcp_url": "",
        }
    )

    # 6. Generar scripts status.sh / sync.sh / start.sh / finish.sh (§25)
    from geas.scripts import generate_scripts

    scripts = generate_scripts(repo_path)

    print(f"Repository registrado: {repo.id[:8]} ({name})")
    print(f"Config: {ctx.config_file}")
    for s in scripts:
        print(f"Script: {s}")
    return 0


def cmd_sync(storage: Storage, args: list[str]) -> int:
    """work sync — sincroniza con Git y valida el entorno (§24)."""
    path = args[0] if args else "."
    ctx = WorkContext(path)

    if not ctx.exists():
        print("NOT INITIALIZED — ejecuta 'work init' primero", file=sys.stderr)
        return 1

    config = ctx.read_config()
    repo_id = config.get("repository_id", "")

    # Git sync
    g = LocalGitProvider(path)
    status = g.status()
    print(
        f"Git: {status.branch} clean={status.clean} "
        f"ahead={status.ahead} behind={status.behind}"
    )

    repo = storage.get_repository(repo_id) if repo_id else None
    if repo is None:
        print("REPOSITORY_NOT_REGISTERED", file=sys.stderr)
        return 1

    # Estado de tickets del repo
    tickets = [
        t
        for t in storage.list_tickets(repo.organization_id)
        if t.repository_id == repo_id
    ]
    for t in tickets:
        print(f"  {t.id[:8]} [{t.status.value:12}] {t.title}")

    return 0


def cmd_status(storage: Storage, args: list[str]) -> int:
    """work status — READY TO WORK / NOT READY (§25)."""
    path = args[0] if args else "."
    ctx = WorkContext(path)
    as_json = "--json" in args

    checks: list[tuple[str, bool, str]] = []

    # Git repository
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    checks.append(
        (
            "Git repository",
            r.returncode == 0,
            "no es un repo git" if r.returncode else "ok",
        )
    )

    # Remoto
    r = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    checks.append(
        (
            "Remote",
            r.returncode == 0,
            "sin remote origin" if r.returncode else r.stdout.strip(),
        )
    )

    # Config
    checks.append(
        (
            "Orchestrator",
            ctx.exists(),
            "falta .orchestrator/config.yml" if not ctx.exists() else "ok",
        )
    )

    if as_json:
        print(
            json.dumps(
                {
                    "ready": all(ok for _, ok, _ in checks),
                    "checks": [
                        {"name": name, "ok": ok, "detail": detail}
                        for name, ok, detail in checks
                    ],
                },
                indent=2,
            )
        )
        return 0 if all(ok for _, ok, _ in checks) else 1

    for name, ok, detail in checks:
        mark = "✓" if ok else "✘"
        print(f"  {mark} {name:15} {detail if not ok else ''}")
    if all(ok for _, ok, _ in checks):
        print("\nREADY TO WORK")
        return 0
    print("\nNOT READY")
    return 1


def cmd_tasks(storage: Storage, args: list[str]) -> int:
    """work tasks — tickets disponibles para el actor (§17).

    Un ticket está READY si:
      - está FREE o CLAIMED por mí
      - tengo permisos (MVP: se asume por departamento)
      - sus dependencias están resueltas
      - sus recursos están disponibles (no bloqueados por otro ticket)
    """
    args[0] if args else "me"
    path = args[1] if len(args) > 1 else "."
    ctx = WorkContext(path)
    config = ctx.read_config()
    org_id = config.get("organization_id", "")

    if not org_id:
        # Fallback MVP: si solo hay una organización, usarla
        orgs = storage.list_organizations()
        if len(orgs) == 1:
            org_id = orgs[0].id
        else:
            print("NOT INITIALIZED — ejecuta 'work init' primero", file=sys.stderr)
            return 1

    tickets = storage.list_tickets(org_id)
    ready: list[dict] = []
    for t in tickets:
        if t.status not in (TicketStatus.FREE, TicketStatus.CLAIMED):
            continue
        # Dependencias
        deps_ok = storage.are_dependencies_resolved(t.id)
        # Recursos: ¿alguno bloqueado por otro ticket?
        resources_ok = True
        blocked_by = ""
        for res_id in t.resources:
            lock = storage.get_lock_for_resource(res_id)
            if lock and lock.ticket_id != t.id:
                resources_ok = False
                blocked_by = lock.ticket_id
                break
        if deps_ok and resources_ok:
            ready.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "priority": t.priority,
                }
            )
        else:
            print(
                f"  {t.id[:8]} SKIPPED: "
                f"deps={'OK' if deps_ok else 'PENDING'} "
                f"resources={'OK' if resources_ok else 'LOCKED by ' + blocked_by[:8]}"
            )

    if not ready:
        print("No hay tickets disponibles.")
        return 0
    for t in ready:
        print(f"{t['id']} [{t['status']}] prio={t['priority']} {t['title']}")
    print(f"\nREADY: {len(ready)} ticket(s)")
    return 0


def cmd_context(storage: Storage, args: list[str]) -> int:
    """work context — contexto del repo actual."""
    path = args[0] if args else "."
    ctx = WorkContext(path)
    if not ctx.exists():
        print("NOT INITIALIZED")
        return 1
    config = ctx.read_config()
    for key, value in config.items():
        print(f"  {key}: {value}")
    return 0


def cmd_ticket(storage: Storage, args: list[str]) -> int:
    """work ticket <id> — mostrar un ticket completo."""
    if not args:
        print("Uso: work ticket <ticket_id>", file=sys.stderr)
        return 1
    ticket = storage.get_ticket(args[0])
    if not ticket:
        print(f"Ticket no encontrado: {args[0]}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "status": ticket.status.value,
                "priority": ticket.priority,
                "assigned": ticket.assigned_actor_id,
                "branch": ticket.branch,
                "commit_before": ticket.commit_before,
                "commit_after": ticket.commit_after,
                "dependencies": ticket.dependencies,
                "resources": ticket.resources,
                "created_at": ticket.created_at,
                "started_at": ticket.started_at,
                "completed_at": ticket.completed_at,
                "feedback": ticket.feedback,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_start(storage: Storage, args: list[str]) -> int:
    """work start <id> — §24: valida, adquiere locks, registra commit_before,
    crea branch y pone el ticket IN_PROGRESS."""
    if not args:
        print("Uso: work start <ticket_id> [path]", file=sys.stderr)
        return 1
    ticket_id = args[0]
    path = args[1] if len(args) > 1 else "."
    WorkContext(path)

    ticket = storage.get_ticket(ticket_id)
    if not ticket:
        print(f"Ticket no encontrado: {ticket_id}", file=sys.stderr)
        return 1

    # ── sync ──
    g = LocalGitProvider(path)
    g.fetch()

    # ── validate permissions / dependencies / resources ──
    if ticket.status != TicketStatus.FREE:
        print(f"Ticket no está FREE: {ticket.status.value}", file=sys.stderr)
        return 1
    if not storage.are_dependencies_resolved(ticket.id):
        print("DEPENDENCIES UNRESOLVED", file=sys.stderr)
        return 1
    for res_id in ticket.resources:
        lock = storage.get_lock_for_resource(res_id)
        if lock and lock.ticket_id != ticket.id:
            print(
                f"RESOURCE_UNAVAILABLE: locked_by {lock.ticket_id[:8]}", file=sys.stderr
            )
            return 1

    # ── acquire locks (TTL 2h + heartbeat) ──
    now = datetime.now(UTC)
    expires = (now + timedelta(hours=2)).isoformat()
    for res_id in ticket.resources:
        storage.create_lock(
            ResourceLock(
                resource_id=res_id,
                ticket_id=ticket.id,
                actor_id="me",
                expires_at=expires,
            )
        )
        print(f"LOCKED: {res_id[:8]} (TTL 2h)")

    # ── record commit_before ──
    before = g.get_head()

    # ── create branch ──
    branch = f"{ticket.id[:8]}-{ticket.title.replace(' ', '-')[:20]}"
    g.create_branch(branch)
    storage.start_ticket(ticket.id, commit_before=before, branch=branch)

    print(f"START {ticket.id[:8]}")
    print(f"  branch: {branch}")
    print(f"  commit_before: {before}")
    print(f"  locks: {len(ticket.resources)} adquiridos")
    return 0


def cmd_finish(storage: Storage, args: list[str]) -> int:
    """work finish <id> — §24: commit, push, commit_after, release locks, DONE."""
    if not args:
        print("Uso: work finish <ticket_id> [path] [commit_message]", file=sys.stderr)
        return 1
    ticket_id = args[0]
    path = args[1] if len(args) > 1 else "."
    message = " ".join(args[2:]) if len(args) > 2 else f"Geas: completa {ticket_id}"

    ticket = storage.get_ticket(ticket_id)
    if not ticket:
        print(f"Ticket no encontrado: {ticket_id}", file=sys.stderr)
        return 1

    g = LocalGitProvider(path)

    # commit
    status = g.status()
    if status.clean and not status.untracked:
        print("NO CHANGES — nada que commitear", file=sys.stderr)
        return 1
    g._run(["add", "."])
    commit = g.commit(message)
    if commit is None:
        print("COMMIT FAILED", file=sys.stderr)
        return 1

    # push
    g.push(status.branch)

    # commit_after + DONE
    after = g.get_head()
    storage.complete_ticket(ticket.id, commit_after=after, result="OK")

    # release locks
    released = storage.release_locks_for_ticket(ticket.id)

    print(f"FINISH {ticket.id[:8]}")
    print(f"  commit_after: {after}")
    print("  push: OK")
    print(f"  locks liberados: {released}")
    print("  status: DONE")
    return 0


def cmd_diff(storage: Storage, args: list[str]) -> int:
    """work diff <id> — diff entre commit_before y commit_after (§11)."""
    if not args:
        print("Uso: work diff <ticket_id>", file=sys.stderr)
        return 1
    ticket = storage.get_ticket(args[0])
    if not ticket:
        print(f"Ticket no encontrado: {args[0]}", file=sys.stderr)
        return 1
    if not ticket.commit_before or not ticket.commit_after:
        print(
            "El ticket no tiene commit_before/commit_after completos.", file=sys.stderr
        )
        return 1

    repo_path = args[1] if len(args) > 1 else "."
    g = LocalGitProvider(repo_path)
    diff = g.diff(ticket.commit_before, ticket.commit_after)
    print(f"diff({ticket.commit_before[:8]}, {ticket.commit_after[:8]})")
    print(f"  ficheros: {len(diff.files_changed)}")
    print(f"  +{diff.additions} / -{diff.deletions}")
    for f in diff.files_changed:
        print(f"    {f}")
    return 0


def cmd_rollback(storage: Storage, args: list[str]) -> int:
    """work rollback <id> — §11: revertir el trabajo del ticket."""
    if not args:
        print("Uso: work rollback <ticket_id>", file=sys.stderr)
        return 1
    ticket = storage.get_ticket(args[0])
    if not ticket:
        print(f"Ticket no encontrado: {args[0]}", file=sys.stderr)
        return 1
    if not ticket.commit_before:
        print("El ticket no tiene commit_before.", file=sys.stderr)
        return 1

    repo_path = args[1] if len(args) > 1 else "."
    g = LocalGitProvider(repo_path)

    # §11/§30: el rollback se registra como evento de auditoría. Primero
    # ROLLBACK_REQUESTED; ROLLBACK_COMPLETED solo si realmente ocurrió —
    # registrar un COMPLETED que falló mentiría el histórico.
    from geas.models import AuditLog, Event

    storage.create_audit(
        AuditLog(
            actor_id="me",
            action="ROLLBACK_REQUESTED",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"to_commit": ticket.commit_before},
        )
    )
    storage.create_event(
        Event(
            event_type="ROLLBACK_REQUESTED",
            organization_id=ticket.organization_id,
            actor_id="me",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"to_commit": ticket.commit_before},
        )
    )

    ok = g.rollback(ticket.commit_before)
    print(f"ROLLBACK {'OK' if ok else 'FAILED'} a {ticket.commit_before[:8]}")
    if not ok:
        # El REQUESTED queda registrado; no se inventa el COMPLETED (§30).
        return 1

    storage.create_audit(
        AuditLog(
            actor_id="me",
            action="ROLLBACK_COMPLETED",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"to_commit": ticket.commit_before},
        )
    )
    storage.create_event(
        Event(
            event_type="ROLLBACK_COMPLETED",
            organization_id=ticket.organization_id,
            actor_id="me",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"to_commit": ticket.commit_before},
        )
    )
    return 0


def cmd_branch(storage: Storage, args: list[str]) -> int:
    """work branch create <id> [path] — crea la branch del ticket (§34).

    No reclama el ticket: solo crea la branch y la registra. El claim lo
    hace `work start` o el runner.
    """
    if len(args) < 2 or args[0] != "create":
        print("Uso: work branch create <ticket_id> [path]", file=sys.stderr)
        return 1
    ticket_id, path = args[1], args[2] if len(args) > 2 else "."

    ticket = storage.get_ticket(ticket_id)
    if not ticket:
        print(f"Ticket no encontrado: {ticket_id}", file=sys.stderr)
        return 1
    if ticket.branch:
        print(f"El ticket ya tiene branch: {ticket.branch}", file=sys.stderr)
        return 1

    branch = f"{ticket.id[:8]}-{ticket.title.replace(' ', '-')[:20]}"
    g = LocalGitProvider(path)
    if not g.create_branch(branch):
        print("BRANCH CREATE FAILED", file=sys.stderr)
        return 1
    storage.update_ticket_fields(ticket.id, branch=branch)
    storage.create_audit(
        AuditLog(
            actor_id="me",
            action="BRANCH_CREATED",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"branch": branch},
        )
    )

    print(f"BRANCH: {branch}")
    return 0


def cmd_commit(storage: Storage, args: list[str]) -> int:
    """work commit <id> [path] [mensaje] — commit en la branch del ticket (§34).

    Registra el evento COMMIT_REGISTERED (§30). El commit_after del ticket
    solo lo fija `work finish`, cuando el trabajo está completo.
    """
    if not args:
        print("Uso: work commit <ticket_id> [path] [mensaje]", file=sys.stderr)
        return 1
    ticket_id = args[0]
    path = args[1] if len(args) > 1 else "."
    message = " ".join(args[2:]) if len(args) > 2 else f"Geas: trabajo {ticket_id}"

    ticket = storage.get_ticket(ticket_id)
    if not ticket:
        print(f"Ticket no encontrado: {ticket_id}", file=sys.stderr)
        return 1

    g = LocalGitProvider(path)
    if ticket.branch:
        current = g.status().branch
        if current != ticket.branch:
            g._run(["checkout", ticket.branch])
    if g.status().clean and not g.status().untracked:
        print("NO CHANGES — nada que commitear", file=sys.stderr)
        return 1

    g._run(["add", "."])
    commit = g.commit(message)
    if commit is None:
        print("COMMIT FAILED", file=sys.stderr)
        return 1

    storage.create_event(
        Event(
            event_type="COMMIT_REGISTERED",
            organization_id=ticket.organization_id,
            actor_id="me",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"commit": commit.sha},
        )
    )
    storage.create_audit(
        AuditLog(
            actor_id="me",
            action="COMMIT_REGISTERED",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"commit": commit.sha},
        )
    )
    print(f"COMMIT {commit.sha[:8]} en {ticket.branch or g.status().branch}")
    return 0


def cmd_pr(storage: Storage, args: list[str]) -> int:
    """work pr create <id> [path] — push + PR de la branch del ticket (§34).

    El push es contra el remote del repo. El PR requiere el CLI del
    proveedor (gh, glab); sin él, el push queda hecho y se avisa.
    """
    if len(args) < 2 or args[0] != "create":
        print("Uso: work pr create <ticket_id> [path]", file=sys.stderr)
        return 1
    ticket_id, path = args[1], args[2] if len(args) > 2 else "."

    ticket = storage.get_ticket(ticket_id)
    if not ticket:
        print(f"Ticket no encontrado: {ticket_id}", file=sys.stderr)
        return 1
    if not ticket.branch:
        print(
            "El ticket no tiene branch — usa 'work branch create' o 'work start'",
            file=sys.stderr,
        )
        return 1

    g = LocalGitProvider(path)
    if g.status().branch != ticket.branch:
        g._run(["checkout", ticket.branch])
    push_ok = g.push(ticket.branch)
    if not push_ok:
        print("PUSH FAILED", file=sys.stderr)
        return 1

    pr_url = g.create_pr(f"Ticket {ticket.id}: {ticket.title}", ticket.branch)
    storage.create_event(
        Event(
            event_type="PR_CREATED",
            organization_id=ticket.organization_id,
            actor_id="me",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"branch": ticket.branch, "pr_url": pr_url or ""},
        )
    )
    storage.create_audit(
        AuditLog(
            actor_id="me",
            action="PR_CREATED",
            resource_type="ticket",
            resource_id=ticket.id,
            metadata={"branch": ticket.branch, "pr_url": pr_url or ""},
        )
    )

    print(f"PUSH: {ticket.branch}")
    if pr_url:
        print(f"PR: {pr_url}")
    else:
        print("PR: no creado (falta el CLI del proveedor: gh/glab)")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print(__doc__)
        return 0

    cmd = argv[0]
    args = argv[1:]

    storage = _storage()
    try:
        handlers = {
            "init": cmd_init,
            "sync": cmd_sync,
            "status": cmd_status,
            "tasks": cmd_tasks,
            "context": cmd_context,
            "ticket": cmd_ticket,
            "branch": cmd_branch,
            "commit": cmd_commit,
            "pr": cmd_pr,
            "start": cmd_start,
            "finish": cmd_finish,
            "diff": cmd_diff,
            "rollback": cmd_rollback,
        }
        handler = handlers.get(cmd)
        if handler is None:
            print(f"Comando desconocido: {cmd}", file=sys.stderr)
            return 1
        return handler(storage, args)
    finally:
        storage.close()
