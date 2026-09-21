"""
geas.cli — Interfaz de línea de comandos de Geas.

Uso:
    geas init                    Crear organización
    geas org list                Listar organizaciones
    geas dept list <org>         Listar departamentos
    geas user list <org>         Listar usuarios
    geas agent list <org>        Listar agentes
    geas repo list <org>         Listar repositorios
    geas ticket list <org>       Listar tickets
    geas ticket show <id>        Ver un ticket
    geas ticket create <org>     Crear un ticket
    geas ticket start <id>       Empezar a trabajar en un ticket
    geas ticket complete <id>    Marcar ticket como done
    geas resource list <repo>    Listar recursos de un repo
    geas lock show <resource>    Ver lock de un recurso
    geas events <org>            Ver eventos recientes
    geas audit <org>             Ver log de auditoría
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from geas.models import (
    DEFAULT_ROLES,
    Agent,
    AuditLog,
    Department,
    Event,
    Organization,
    Resource,
    ResourceLock,
    Repository,
    Role,
    Ticket,
    User,
)
from geas.storage import Storage


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print(__doc__)
        return 0

    db_path = Path("geas.db")
    storage = Storage(db_path)

    cmd = argv[0]
    args = argv[1:]

    try:
        if cmd == "init":
            return _cmd_init(storage, args)
        elif cmd == "org":
            return _cmd_org(storage, args)
        elif cmd == "dept":
            return _cmd_dept(storage, args)
        elif cmd == "user":
            return _cmd_user(storage, args)
        elif cmd == "agent":
            return _cmd_agent(storage, args)
        elif cmd == "repo":
            return _cmd_repo(storage, args)
        elif cmd == "ticket":
            return _cmd_ticket(storage, args)
        elif cmd == "resource":
            return _cmd_resource(storage, args)
        elif cmd == "lock":
            return _cmd_lock(storage, args)
        elif cmd == "events":
            return _cmd_events(storage, args)
        elif cmd == "audit":
            return _cmd_audit(storage, args)
        else:
            print(f"Comando desconocido: {cmd}", file=sys.stderr)
            return 1
    finally:
        storage.close()


def _cmd_init(storage: Storage, args: list[str]) -> int:
    """Crear una organización con roles por defecto."""
    name = args[0] if args else "Mi Organización"
    org = Organization(name=name)
    storage.create_organization(org)

    # Crear roles por defecto
    for role_name, permissions in DEFAULT_ROLES.items():
        role = Role(
            organization_id=org.id,
            name=role_name,
            permissions=permissions,
        )
        storage.create_role(role)

    print(f"Organización creada: {org.name} ({org.id})")
    print(f"Roles creados: {', '.join(DEFAULT_ROLES.keys())}")
    return 0


def _cmd_org(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list":
        print("Uso: geas org list")
        return 1
    orgs = storage.list_organizations()
    if not orgs:
        print("No hay organizaciones. Ejecuta: geas init <nombre>")
        return 0
    for o in orgs:
        status = "activa" if o.active else "inactiva"
        print(f"  {o.id[:8]}  {o.name}  [{status}]")
    return 0


def _cmd_dept(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list" or len(args) < 2:
        print("Uso: geas dept list <org_id>")
        return 1
    depts = storage.list_departments(args[1])
    if not depts:
        print("No hay departamentos.")
        return 0
    for d in depts:
        print(f"  {d.id[:8]}  {d.name}")
    return 0


def _cmd_user(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list" or len(args) < 2:
        print("Uso: geas user list <org_id>")
        return 1
    users = storage.list_users(args[1])
    if not users:
        print("No hay usuarios.")
        return 0
    for u in users:
        status = "activo" if u.active else "inactivo"
        print(f"  {u.id[:8]}  {u.name} <{u.email}>  [{status}]")
    return 0


def _cmd_agent(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list" or len(args) < 2:
        print("Uso: geas agent list <org_id>")
        return 1
    agents = storage.list_agents(args[1])
    if not agents:
        print("No hay agentes.")
        return 0
    for a in agents:
        status = "activo" if a.active else "inactivo"
        print(f"  {a.id[:8]}  {a.name}  ({a.provider}/{a.model})  [{status}]")
    return 0


def _cmd_repo(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list" or len(args) < 2:
        print("Uso: geas repo list <org_id>")
        return 1
    repos = storage.list_repositories(args[1])
    if not repos:
        print("No hay repositorios.")
        return 0
    for r in repos:
        print(f"  {r.id[:8]}  {r.name}  ({r.provider})  [{r.visibility.value}]")
    return 0


def _cmd_ticket(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas ticket list|show|create|start|complete ...")
        return 1

    sub = args[0]
    if sub == "list":
        if len(args) < 2:
            print("Uso: geas ticket list <org_id>")
            return 1
        org_id = args[1]
        status_filter = args[2] if len(args) > 2 else None
        tickets = storage.list_tickets(org_id, status=status_filter)
        if not tickets:
            print("No hay tickets.")
            return 0
        for t in tickets:
            print(f"  {t.id[:8]}  [{t.status.value:12}]  {t.title}")
        return 0

    elif sub == "show":
        if len(args) < 2:
            print("Uso: geas ticket show <ticket_id>")
            return 1
        ticket = storage.get_ticket(args[1])
        if not ticket:
            print(f"Ticket no encontrado: {args[1]}", file=sys.stderr)
            return 1
        print(json.dumps({
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
        }, indent=2, ensure_ascii=False))
        return 0

    elif sub == "create":
        if len(args) < 3:
            print("Uso: geas ticket create <org_id> <title> [description]")
            return 1
        desc = args[3] if len(args) > 3 else ""
        ticket = Ticket(
            organization_id=args[1],
            title=args[2],
            description=desc,
        )
        storage.create_ticket(ticket)
        print(f"Ticket creado: {ticket.id[:8]}  {ticket.title}")
        return 0

    elif sub == "start":
        if len(args) < 2:
            print("Uso: geas ticket start <ticket_id> [commit_before] [branch]")
            return 1
        commit_before = args[2] if len(args) > 2 else ""
        branch = args[3] if len(args) > 3 else ""
        ok = storage.start_ticket(args[1], commit_before, branch)
        if ok:
            print(f"Ticket {args[1][:8]} iniciado.")
        else:
            print(f"No se pudo iniciar ticket {args[1][:8]}.", file=sys.stderr)
            return 1
        return 0

    elif sub == "complete":
        if len(args) < 2:
            print("Uso: geas ticket complete <ticket_id> [commit_after]")
            return 1
        commit_after = args[2] if len(args) > 2 else ""
        ok = storage.complete_ticket(args[1], commit_after)
        if ok:
            print(f"Ticket {args[1][:8]} completado.")
        else:
            print(f"No se pudo completar ticket {args[1][:8]}.", file=sys.stderr)
            return 1
        return 0

    else:
        print(f"Subcomando desconocido: ticket {sub}", file=sys.stderr)
        return 1


def _cmd_resource(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "list" or len(args) < 2:
        print("Uso: geas resource list <repo_id>")
        return 1
    resources = storage.list_resources(args[1])
    if not resources:
        print("No hay recursos.")
        return 0
    for r in resources:
        lock = storage.get_lock_for_resource(r.id)
        lock_info = f" [LOCKED by {lock.ticket_id[:8]}]" if lock else ""
        print(f"  {r.id[:8]}  {r.path}  ({r.type.value}){lock_info}")
    return 0


def _cmd_lock(storage: Storage, args: list[str]) -> int:
    if not args or args[0] != "show" or len(args) < 2:
        print("Uso: geas lock show <resource_id>")
        return 1
    lock = storage.get_lock_for_resource(args[1])
    if not lock:
        print("Recurso desbloqueado.")
        return 0
    print(json.dumps({
        "resource_id": lock.resource_id,
        "ticket_id": lock.ticket_id,
        "actor_id": lock.actor_id,
        "created_at": lock.created_at,
        "expires_at": lock.expires_at,
        "last_heartbeat": lock.last_heartbeat,
    }, indent=2))
    return 0


def _cmd_events(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas events <org_id>")
        return 1
    events = storage.list_events(args[0])
    if not events:
        print("No hay eventos.")
        return 0
    for e in events:
        print(f"  {e.timestamp[:19]}  {e.event_type:25}  {e.resource_type}:{e.resource_id[:8]}")
    return 0


def _cmd_audit(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas audit <org_id>")
        return 1
    logs = storage.list_audit(args[0])
    if not logs:
        print("No hay registros de auditoría.")
        return 0
    for a in logs:
        print(f"  {a.timestamp[:19]}  {a.actor_id[:8]}  {a.action:25}  {a.resource_type}:{a.resource_id[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
