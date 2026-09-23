"""
geas.cli — Interfaz de línea de comandos de Geas.

Uso:
    geas init [nombre] [--profile <p>]  Crear organización (perfil §43)
    geas org list                Listar organizaciones
    geas org show <id>           Perfil y componentes de una organización (§43)
    geas dept list <org>         Listar departamentos
    geas user list <org>         Listar usuarios
    geas agent list <org>        Listar agentes
    geas role list <org>         Listar roles y permisos
    geas role create <org> <name> [permiso...]   Crear rol (§7)
    geas role grant <org> <role> <permiso>       Conceder permiso (§7)
    geas permission list         Catálogo de permisos (§7)
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
    geas mcp list                Herramientas MCP (§26)
    geas serve                   Panel web de estado (§42)
    geas sync [path]             Estructura declarativa → base de datos (§43)
    geas enterprise init         Genera el árbol declarativo Enterprise (§43)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from geas.models import (
    ALL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    DEFAULT_ROLES,
    Agent,
    Department,
    Organization,
    Policy,
    Repository,
    ResourceLock,
    Role,
    Ticket,
    User,
    Visibility,
)
from geas.storage import Storage


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _lock_active(storage: Storage, resource_id: str, now: str) -> ResourceLock | None:
    """§15: un lock está activo si no ha expirado (expires_at > now)."""
    lock = storage.get_lock_for_resource(resource_id)
    if lock and lock.expires_at > now:
        return lock
    return None


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
        elif cmd == "work":
            from geas.work import main as work_main

            return work_main(args)
        elif cmd == "harness":
            return _cmd_harness(storage, args)
        elif cmd == "mcp":
            return _cmd_mcp(storage, args)
        elif cmd == "serve":
            return _cmd_serve(storage, args)
        elif cmd == "org":
            return _cmd_org(storage, args)
        elif cmd == "dept":
            return _cmd_dept(storage, args)
        elif cmd == "role":
            return _cmd_role(storage, args)
        elif cmd == "permission":
            return _cmd_permission(storage, args)
        elif cmd == "sync":
            return _cmd_sync(storage, args)
        elif cmd == "enterprise":
            return _cmd_enterprise(storage, args)
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
    """Crear una organización con roles por defecto y perfil de despliegue.

    §43: `geas init "Mi Org" --profile team` (o 1/2/3). El perfil decide
    qué componentes se activan y qué secciones declarativas sincroniza."""
    from geas.profiles import backend_for, components, normalize_profile

    profile: str | None = None
    name_parts: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--profile", "-p") and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        elif arg == "--individual":
            profile = "individual"
            i += 1
        elif arg == "--team":
            profile = "team"
            i += 1
        elif arg == "--enterprise":
            profile = "enterprise"
            i += 1
        else:
            name_parts.append(arg)
            i += 1

    try:
        product = normalize_profile(profile) if profile else "individual"
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    name = " ".join(name_parts) if name_parts else "Mi Organización"
    org = Organization(name=name, profile=product)
    storage.create_organization(org)

    # Roles por defecto — data inocua en todo perfil; las secciones que
    # cada perfil sincroniza de forma declarativa las filtra `geas sync`
    for role_name, permissions in DEFAULT_ROLES.items():
        role = Role(
            organization_id=org.id,
            name=role_name,
            permissions=permissions,
        )
        storage.create_role(role)

    print(f"Organización creada: {org.name} ({org.id})")
    print(f"Perfil: {product}")
    print(f"Componentes: {', '.join(components(product))}")
    if backend_for(product) == "postgres":
        print(
            "  backend declarado: postgres (§42) — driver psycopg pendiente "
            "(deuda de infra); esta build usa sqlite"
        )
    print(f"Roles creados: {', '.join(DEFAULT_ROLES.keys())}")
    return 0


def _cmd_org(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas org list|show <id>")
        return 1
    if args[0] == "list":
        orgs = storage.list_organizations()
        if not orgs:
            print("No hay organizaciones. Ejecuta: geas init <nombre>")
            return 0
        for o in orgs:
            status = "activa" if o.active else "inactiva"
            print(f"  {o.id[:8]}  {o.name}  [{status}]")
        return 0
    if args[0] == "show":
        if len(args) < 2:
            print("Uso: geas org show <org_id>", file=sys.stderr)
            return 1
        from geas.profiles import backend_for, components

        org = storage.get_organization(args[1])
        if not org:
            print(f"Organización no encontrada: {args[1]}", file=sys.stderr)
            return 1
        print(f"id: {org.id}")
        print(f"name: {org.name}")
        print(f"profile: {org.profile}  (backend declarado: {backend_for(org.profile)})")
        print(f"components: {', '.join(components(org.profile))}")
        print(f"departments: {len(storage.list_departments(org.id))}")
        print(f"repositories: {len(storage.list_repositories(org.id))}")
        print(f"roles: {len(storage.list_roles(org.id))}")
        print(f"policies: {len(storage.list_policies(org.id))}")
        print(f"users: {len(storage.list_users(org.id))}")
        print(f"agents: {len(storage.list_agents(org.id))}")
        return 0
    print("Uso: geas org list|show <id>", file=sys.stderr)
    return 1


def _cmd_enterprise(storage: Storage, args: list[str]) -> int:
    """geas enterprise init — bootstrap de la estructura declarativa (§43).

    Genera organization.yml + departments/ + repositories/ + roles/ +
    policies/ + workflow CI en el árbol de ficheros. NO toca la base de
    datos: primero `geas init <org> --profile enterprise`, después
    `geas sync` aplica la estructura."""
    from geas.enterprise import generate_enterprise

    if not args or args[0] != "init":
        print(
            "Uso: geas enterprise init --org <nombre> [--dir <ruta>] "
            "[--description <texto>] [--departments <a,b,c>] "
            "[--repos <depto:a,b;c:d>]",
            file=sys.stderr,
        )
        return 1

    org_name = ""
    root_dir = "."
    description = ""
    departments: list[str] = []
    repos: dict[str, list[str]] = {}
    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--org" and i + 1 < len(args):
            org_name = args[i + 1]
            i += 2
        elif arg == "--dir" and i + 1 < len(args):
            root_dir = args[i + 1]
            i += 2
        elif arg == "--description" and i + 1 < len(args):
            description = args[i + 1]
            i += 2
        elif arg == "--departments" and i + 1 < len(args):
            departments = [p.strip() for p in args[i + 1].split(",") if p.strip()]
            i += 2
        elif arg == "--repos" and i + 1 < len(args):
            repos = _parse_repos_spec(args[i + 1])
            i += 2
        else:
            print(f"Opción desconocida: {arg}", file=sys.stderr)
            return 1

    if not org_name:
        print("Falta --org <nombre>", file=sys.stderr)
        return 1

    written = generate_enterprise(
        root_dir,
        org_name,
        description=description,
        departments=departments,
        repositories=repos,
    )
    for path in written:
        print(f"  generado: {path}")
    print(f"estructura generada: {len(written)} ficheros en {root_dir}/")
    print("siguiente:")
    print(f"  geas init \"{org_name}\" --profile enterprise")
    print(f"  geas sync {root_dir}" if root_dir != "." else "  geas sync .")
    return 0


def _parse_repos_spec(spec: str) -> dict[str, list[str]]:
    """'depto:a,b;c:d' → {depto: ['a','b'], 'c': ['d']}; sueltos → org."""
    repos: dict[str, list[str]] = {}
    for token in [t.strip() for t in spec.split(";") if t.strip()]:
        if ":" in token:
            dept, _, names = token.partition(":")
            repos.setdefault(dept.strip(), []).extend(
                n.strip() for n in names.split(",") if n.strip()
            )
        else:
            repos.setdefault("", []).append(token)
    return repos


def _cmd_sync(storage: Storage, args: list[str]) -> int:
    """geas sync [path] — estructura declarativa → base de datos (§43).

    Idempotente: crea lo que falta, converge description/profile/
    permisos, y deja intacto lo que ya está. Las secciones que el
    perfil de la organización no soporta se rechazan con error claro."""
    from geas.declarative import load_structure
    from geas.profiles import normalize_profile, sections_for

    root = Path(args[0]) if args else Path(".")
    try:
        struct = load_structure(root)
        product = normalize_profile(struct.profile)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # §43: validación ANTES de tocar la base de datos
    present: set[str] = set()
    if struct.departments:
        present.add("departments")
    if struct.roles:
        present.add("roles")
    if struct.policies:
        present.add("policies")
    unsupported = sorted(present - sections_for(product))
    if unsupported:
        needs = (
            "enterprise"
            if {"departments", "policies"} & set(unsupported)
            else "team"
        )
        print(
            "SECTION_NOT_SUPPORTED: el perfil "
            f"'{product}' no sincroniza {', '.join(unsupported)}. "
            f"Ejecuta 'geas init <org> --profile {needs}' o elimina esas "
            "secciones del árbol declarativo",
            file=sys.stderr,
        )
        return 1

    created = 0
    updated = 0
    unchanged = 0

    # Organización — idempotente: crea si falta, converge si existe
    org = storage.get_organization_by_name(struct.org_name)
    if org is None:
        org = Organization(
            name=struct.org_name,
            description=struct.org_description,
            profile=product,
        )
        storage.create_organization(org)
        for role_name, permissions in DEFAULT_ROLES.items():
            storage.create_role(
                Role(organization_id=org.id, name=role_name, permissions=permissions)
            )
        created += 1
        print(f"+ organización {org.name} (profile: {product})")
    elif storage.update_organization_fields(
        org.id, description=struct.org_description, profile=product
    ):
        updated += 1
        print(f"~ organización {org.name} (profile/description convergidos)")
    else:
        unchanged += 1

    # Departamentos — jerárquicos: padres antes que hijos, idempotente
    pending = list(struct.departments)
    while pending:
        progress = False
        for dept in list(pending):
            parent_id = None
            if dept.parent:
                parent = storage.get_department_by_name(org.id, dept.parent)
                if parent is None:
                    continue  # el padre se crea en otra pasada
                parent_id = parent.id
            existing = storage.get_department_by_name(
                org.id, dept.name, parent_id=parent_id
            )
            if existing is not None:
                pending.remove(dept)
                unchanged += 1
                continue
            storage.create_department(
                Department(
                    organization_id=org.id,
                    parent_department_id=parent_id,
                    name=dept.name,
                    description=dept.description,
                )
            )
            pending.remove(dept)
            created += 1
            print(f"+ departamento {dept.name}")
            progress = True
        if not progress and pending:
            print(
                "DEPARTMENT_CYCLE: jerarquía de departamentos inválida "
                "(parentes sin crear o ciclo)",
                file=sys.stderr,
            )
            return 1

    # Repositorios — únicos por (org, nombre, departamento)
    for repo in struct.repositories:
        dept_id = None
        if repo.department:
            dept = storage.get_department_by_name(org.id, repo.department)
            if dept is None:
                print(
                    f"REPOSITORY_UNKNOWN_DEPARTMENT: '{repo.name}' → "
                    f"'{repo.department}'",
                    file=sys.stderr,
                )
                return 1
            dept_id = dept.id
        existing = storage.get_repository_by_name(org.id, repo.name, dept_id)
        if existing is None:
            storage.create_repository(
                Repository(
                    organization_id=org.id,
                    department_id=dept_id,
                    name=repo.name,
                    url=repo.url,
                    provider=repo.provider,
                    default_branch=repo.default_branch,
                    visibility=Visibility(repo.visibility),
                )
            )
            created += 1
            print(f"+ repositorio {repo.name}" + (f" @ {repo.department}" if repo.department else ""))
        else:
            unchanged += 1

    # Roles — convergen permisos declarativos
    for role in struct.roles:
        existing = storage.get_role_by_name(org.id, role.name)
        if existing is None:
            storage.create_role(
                Role(
                    organization_id=org.id,
                    name=role.name,
                    permissions=role.permissions,
                )
            )
            created += 1
            print(f"+ rol {role.name}")
        elif existing.permissions != role.permissions:
            storage.update_role_permissions(existing.id, role.permissions)
            updated += 1
            print(f"~ rol {role.name} (permisos convergidos)")
        else:
            unchanged += 1

    # Policies — convergen description y permisos
    for policy in struct.policies:
        dept_id = None
        if policy.department:
            dept = storage.get_department_by_name(org.id, policy.department)
            if dept is None:
                print(
                    f"POLICY_UNKNOWN_DEPARTMENT: '{policy.name}' → "
                    f"'{policy.department}'",
                    file=sys.stderr,
                )
                return 1
            dept_id = dept.id
        existing = storage.get_policy_by_name(org.id, policy.name, dept_id)
        if existing is None:
            storage.create_policy(
                Policy(
                    organization_id=org.id,
                    department_id=dept_id,
                    name=policy.name,
                    description=policy.description,
                    permissions=policy.permissions,
                )
            )
            created += 1
            print(f"+ política {policy.name}")
        elif (
            existing.description != policy.description
            or existing.permissions != policy.permissions
        ):
            storage.update_policy_fields(
                existing.id,
                description=policy.description,
                permissions=policy.permissions,
            )
            updated += 1
            print(f"~ política {policy.name}")
        else:
            unchanged += 1

    print(
        f"sync ok: {created} creados, {updated} actualizados, "
        f"{unchanged} sin cambios ({root})"
    )
    return 0


def _cmd_dept(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas dept list|create ...")
        return 1
    sub = args[0]
    if sub == "list":
        if len(args) < 2:
            print("Uso: geas dept list <org_id>")
            return 1
        depts = storage.list_departments(args[1])
        if not depts:
            print("No hay departamentos.")
            return 0
        for d in depts:
            print(f"  {d.id[:8]}  {d.name}")
        return 0
    if sub == "create":
        if len(args) < 3:
            print("Uso: geas dept create <org_id> <name> [parent_dept_id]")
            return 1
        parent = args[3] if len(args) > 3 else None
        d = Department(
            organization_id=args[1], name=args[2], parent_department_id=parent
        )
        storage.create_department(d)
        print(f"Departamento creado: {d.id[:8]}  {d.name}")
        return 0
    print(f"Subcomando desconocido: dept {sub}", file=sys.stderr)
    return 1


def _cmd_permission(storage: Storage, args: list[str]) -> int:
    """geas permission list — catálogo de permisos del §7."""
    if not args or args[0] != "list":
        print("Uso: geas permission list")
        return 1
    for permission in DEFAULT_PERMISSIONS:
        print(f"  {permission}")
    return 0


def _cmd_role(storage: Storage, args: list[str]) -> int:
    """geas role list|create|grant — RBAC del §7/§42."""
    if not args:
        print("Uso: geas role list|create|grant ...")
        return 1
    sub = args[0]
    if sub == "list":
        if len(args) < 2:
            print("Uso: geas role list <org_id>")
            return 1
        roles = storage.list_roles(args[1])
        if not roles:
            print("No hay roles.")
            return 0
        for r in roles:
            perms = ", ".join(r.permissions) if r.permissions else "(sin permisos)"
            print(f"  {r.id[:8]}  {r.name}  [{perms}]")
        return 0
    if sub == "create":
        if len(args) < 3:
            print("Uso: geas role create <org_id> <name> [permission...]")
            return 1
        requested = args[3:]
        unknown = [p for p in requested if p not in ALL_PERMISSIONS]
        if unknown:
            print(
                f"Permisos desconocidos (§7): {', '.join(unknown)}",
                file=sys.stderr,
            )
            return 1
        role = Role(
            organization_id=args[1], name=args[2], permissions=list(requested)
        )
        storage.create_role(role)
        print(f"Rol creado: {role.id[:8]}  {role.name}")
        return 0
    if sub == "grant":
        if len(args) < 4:
            print("Uso: geas role grant <org_id> <role_id> <permission>")
            return 1
        if args[3] not in ALL_PERMISSIONS:
            print(f"Permiso desconocido (§7): {args[3]}", file=sys.stderr)
            return 1
        ok = storage.add_permission_to_role(args[2], args[3])
        if not ok:
            print(f"Rol no encontrado: {args[2]}", file=sys.stderr)
            return 1
        print(f"Permiso concedido: {args[2][:8]} + {args[3]}")
        return 0
    print(f"Subcomando desconocido: role {sub}", file=sys.stderr)
    return 1


def _cmd_user(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas user list|create ...")
        return 1
    sub = args[0]
    if sub == "list":
        if len(args) < 2:
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
    if sub == "create":
        if len(args) < 3:
            print("Uso: geas user create <org_id> <name> [email] [dept_id] [role_id]")
            return 1
        email = args[3] if len(args) > 3 else ""
        dept_id = args[4] if len(args) > 4 else None
        role_id = args[5] if len(args) > 5 else None
        u = User(
            organization_id=args[1],
            name=args[2],
            email=email,
            department_id=dept_id,
            role_id=role_id,
        )
        storage.create_user(u)
        print(f"Usuario creado: {u.id[:8]}  {u.name}")
        return 0
    print(f"Subcomando desconocido: user {sub}", file=sys.stderr)
    return 1


def _cmd_agent(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas agent list|create ...")
        return 1
    sub = args[0]
    if sub == "list":
        if len(args) < 2:
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
    if sub == "create":
        if len(args) < 4:
            print("Uso: geas agent create <org_id> <name> <provider> <model> [dept_id] [role_id]")
            return 1
        dept_id = args[5] if len(args) > 5 else None
        role_id = args[6] if len(args) > 6 else None
        a = Agent(
            organization_id=args[1],
            name=args[2],
            provider=args[3],
            model=args[4],
            department_id=dept_id,
            role_id=role_id,
        )
        storage.create_agent(a)
        print(f"Agente creado: {a.id[:8]}  {a.name} ({args[3]}/{args[4]})")
        return 0
    print(f"Subcomando desconocido: agent {sub}", file=sys.stderr)
    return 1


def _cmd_mcp(storage: Storage, args: list[str]) -> int:
    """geas mcp <tool> [--param value ...] — invoca una herramienta MCP (§26).

    Uso:
        geas mcp get_available_tasks
        geas mcp create_ticket --title "Implementar X"
        geas mcp start_ticket --ticket_id <id>
        geas mcp list          → catálogo de herramientas
    """
    if args and args[0] == "serve":
        from geas.mcp_stdio import serve

        actor_id = args[1] if len(args) > 1 else ""
        if not actor_id:
            print("Uso: geas mcp serve <actor_id> [org_id]", file=sys.stderr)
            return 1
        serve(storage, actor_id=actor_id, org_id=args[2] if len(args) > 2 else "")
        return 0

    if not args or args[0] == "list":
        from geas.mcp import TOOLS

        print("Herramientas MCP:")
        for tool in TOOLS:
            print(f"  {tool['name']:25} {tool['description']}")
        return 0

    tool = args[0]
    params: dict = {}
    i = 1
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1]
                # Intenta parsear JSON (listas, números)
                try:
                    params[key] = json.loads(value)
                except json.JSONDecodeError:
                    params[key] = value
                i += 2
            else:
                params[key] = True
                i += 1
        else:
            i += 1

    from geas.mcp import TOOL_NAMES, GeasMcp

    if tool not in TOOL_NAMES:
        print(f"Herramienta desconocida: {tool}", file=sys.stderr)
        print("Usa: geas mcp list", file=sys.stderr)
        return 1

    orgs = storage.list_organizations()
    org_id = orgs[0].id if orgs else ""
    mcp = GeasMcp(
        storage,
        actor_id=params.pop("actor_id", "cli"),
        org_id=params.pop("org_id", org_id),
    )
    result = mcp.call(tool, params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["success"] else 1


def _cmd_harness(storage: Storage, args: list[str]) -> int:
    """geas harness run <ticket_id> — ejecuta un ticket en un worktree aislado (§27).

    Uso:
        geas harness run <ticket_id> --repo <path> --cmd "<comando>..."
                                    [--tests "<comando>..."]
                                    [--agent <agent_id>] [--actor <id>]
                                    [--keep] [--timeout <s>]
    """
    if not args or args[0] != "run" or len(args) < 2:
        print(
            "Uso: geas harness run <ticket_id> --repo <path> --cmd ...", file=sys.stderr
        )
        return 1

    ticket_id = args[1]
    pos = 2
    repo_path = "."
    command: list[str] | None = None
    test_command: list[str] | None = None
    agent_id: str | None = None
    actor_id = "runner"
    keep = False
    timeout: int | None = None

    def _split(value: str) -> list[str]:
        import shlex

        return shlex.split(value)

    while pos < len(args):
        arg = args[pos]
        if arg == "--repo" and pos + 1 < len(args):
            repo_path = args[pos + 1]
            pos += 2
        elif arg == "--cmd" and pos + 1 < len(args):
            command = _split(args[pos + 1])
            pos += 2
        elif arg == "--tests" and pos + 1 < len(args):
            test_command = _split(args[pos + 1])
            pos += 2
        elif arg == "--agent" and pos + 1 < len(args):
            agent_id = args[pos + 1]
            pos += 2
        elif arg == "--actor" and pos + 1 < len(args):
            actor_id = args[pos + 1]
            pos += 2
        elif arg == "--timeout" and pos + 1 < len(args):
            timeout = int(args[pos + 1])
            pos += 2
        elif arg == "--keep":
            keep = True
            pos += 1
        else:
            print(f"Argumento desconocido: {arg}", file=sys.stderr)
            return 1

    if not command:
        print('Falta --cmd "<comando del agente>"', file=sys.stderr)
        return 1

    from geas.runner import ExecutionRunner, RunnerConfig

    agent = storage.get_agent(agent_id) if agent_id else None
    config = RunnerConfig(
        command=command,
        test_command=test_command,
        keep_worktree=keep,
        timeout=timeout,
        actor_id=actor_id,
    )
    runner = ExecutionRunner(storage)
    try:
        summary = runner.run_ticket(ticket_id, config, repo_path, agent=agent)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"execution: {summary.execution_id}")
    print(f"ticket:    {summary.ticket_id}")
    print(f"branch:    {summary.branch}")
    print(f"result:    {summary.result} (rc={summary.returncode})")
    if summary.test_status:
        print(f"tests:     {summary.test_status}")
    if summary.stdout.strip():
        print("stdout:")
        for line in summary.stdout.splitlines()[:20]:
            print(f"  {line}")
    if summary.stderr.strip():
        print("stderr:")
        for line in summary.stderr.splitlines()[:20]:
            print(f"  {line}")
    if summary.kept_worktree:
        print(f"worktree:  {summary.worktree} (conservado)")
    return 0 if summary.result == "success" else 1


def _cmd_serve(storage: Storage, args: list[str]) -> int:
    """Inicia API HTTP y panel web: geas serve [host] [port]."""
    from geas.server import serve

    host = args[0] if args else "127.0.0.1"
    try:
        port = int(args[1]) if len(args) > 1 else 8787
    except ValueError:
        print("El puerto debe ser un entero", file=sys.stderr)
        return 1
    print(f"Geas disponible en http://{host}:{port}")
    serve(storage, host, port)
    return 0


def _cmd_repo(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas repo list|create ...")
        return 1
    sub = args[0]
    if sub == "list":
        if len(args) < 2:
            print("Uso: geas repo list <org_id>")
            return 1
        repos = storage.list_repositories(args[1])
        if not repos:
            print("No hay repositorios.")
            return 0
        for r in repos:
            print(f"  {r.id[:8]}  {r.name}  ({r.provider})  [{r.visibility.value}]")
        return 0
    if sub == "create":
        if len(args) < 3:
            print("Uso: geas repo create <org_id> <name> [provider] [url] [dept_id]")
            return 1
        provider = args[3] if len(args) > 3 else "local"
        url = args[4] if len(args) > 4 else ""
        dept_id = args[5] if len(args) > 5 else None
        r = Repository(
            organization_id=args[1],
            name=args[2],
            provider=provider,
            url=url,
            department_id=dept_id,
        )
        storage.create_repository(r)
        print(f"Repositorio creado: {r.id[:8]}  {r.name}")
        return 0
    print(f"Subcomando desconocido: repo {sub}", file=sys.stderr)
    return 1


def _cmd_ticket(storage: Storage, args: list[str]) -> int:
    if not args:
        print("Uso: geas ticket list|show|create|start|complete ...")
        return 1

    sub = args[0]
    if sub == "list":
        if len(args) < 2:
            print("Uso: geas ticket list <org_id> [free|busy]")
            return 1
        org_id = args[1]
        status_filter = args[2] if len(args) > 2 and args[2] not in ("free", "busy") else None
        availability = args[2] if len(args) > 2 and args[2] in ("free", "busy") else None
        tickets = storage.list_tickets(org_id, status=status_filter)
        if not tickets:
            print("No hay tickets.")
            return 0

        now = _now()
        for t in tickets:
            # §15: un ticket está O C U P A D O si al menos uno de sus recursos
            # tiene un lock activo; LIBRE si todos sus recursos están disponibles.
            locks = [_lock_active(storage, rid, now) for rid in t.resources]
            locks = [l for l in locks if l is not None]
            if availability == "free" and locks:
                continue
            if availability == "busy" and not locks:
                continue
            if availability is None:
                # §34 contrato previo: `ticket list <org>` sin filtro → sin badges
                print(f"  {t.id[:8]}  [{t.status.value:12}]  {t.title}")
                continue
            if availability is None:
                # §34 previo: `ticket list <org>` sin flags → sin badges.
                # El contrato existente pinta solo id/status/título.
                print(f"  {t.id[:8]}  [{t.status.value:12}]  {t.title}")
                continue
            if locks:
                lock = locks[0]
                actor = storage.get_user(lock.actor_id)
                actor_name = actor.name if actor else lock.actor_id[:8]
                badge = f"  [ocupado desde {lock.created_at} por {actor_name}]"
            else:
                badge = "  [libre]"
            print(f"  {t.id[:8]}  [{t.status.value:12}]  {t.title}{badge}")
        return 0

    elif sub == "show":
        if len(args) < 2:
            print("Uso: geas ticket show <ticket_id>")
            return 1
        ticket = storage.get_ticket(args[1])
        if not ticket:
            print(f"Ticket no encontrado: {args[1]}", file=sys.stderr)
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
                },
                indent=2,
                ensure_ascii=False,
            )
        )
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
    print(
        json.dumps(
            {
                "resource_id": lock.resource_id,
                "ticket_id": lock.ticket_id,
                "actor_id": lock.actor_id,
                "created_at": lock.created_at,
                "expires_at": lock.expires_at,
                "last_heartbeat": lock.last_heartbeat,
            },
            indent=2,
        )
    )
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
        print(
            f"  {e.timestamp[:19]}  {e.event_type:25}  {e.resource_type}:{e.resource_id[:8]}"
        )
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
        print(
            f"  {a.timestamp[:19]}  {a.actor_id[:8]}  {a.action:25}  {a.resource_type}:{a.resource_id[:8]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
