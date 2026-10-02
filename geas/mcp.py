"""
geas.mcp — Interfaz MCP de Geas (spec §26).

MCP es la interfaz estándar entre los agentes y Geas. Los agentes no
acceden directamente a PostgreSQL/SQLite: todo pasa por estas herramientas.

Herramientas (spec §26):
    get_available_tasks()      → tickets que un actor puede ejecutar
    get_ticket(ticket_id)      → detalle de un ticket
    create_ticket(...)         → crear un ticket
    update_ticket(...)         → actualizar campos
    start_ticket(ticket_id)    → claim + commit_before + locks
    block_ticket(ticket_id)    → marcar bloqueado
    complete_ticket(ticket_id) → done + commit_after + release locks
    get_dependencies(ticket_id)
    lock_resource(resource_id)
    release_resource(resource_id)
    sync()
    get_repository_context(repository_id)
    report_commit(...)
    report_test_result(...)
    get_execution(ticket_id)

Contexto organizativo (§43):
    get_organization(organization_id?) → perfil, componentes, backend
    list_departments(organization_id?)  → departamentos declarados
    get_department(name, org?)          → departamento + sus repos
    list_repositories(organization_id?) → repositorios
    get_permissions()                   → catálogo §7

Arquitectura (§26):
    AI Agent → MCP → Geas API → RBAC → Database
"""

from __future__ import annotations

from typing import Any

from geas.models import (
    AuditLog,
    Event,
    Execution,
    TestResult,
    Ticket,
    TicketDependency,
    TicketStatus,
)
from geas.storage import Storage


class McpError(Exception):
    """Error de una herramienta MCP. message y data se devuelven al agente."""

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.message = message
        self.data = data or {}


def _ok(data: Any = None) -> dict:
    return {"success": True, "data": data or {}}


def _err(message: str, data: dict | None = None) -> dict:
    return {"success": False, "error": message, "data": data or {}}


class GeasMcp:
    """Implementación de las herramientas MCP de Geas."""

    def __init__(self, storage: Storage, actor_id: str = "agent", org_id: str = ""):
        self.storage = storage
        self.actor_id = actor_id
        self.org_id = org_id

    def _authorize(self, organization_id: str, permission: str) -> dict | None:
        if self.storage.actor_has_permission(
            self.actor_id, organization_id, permission
        ):
            return None
        return _err(
            "FORBIDDEN",
            {"actor_id": self.actor_id, "permission": permission},
        )

    @staticmethod
    def _safe_branch(title: str) -> str:
        """Nombre de rama seguro: minusculas, guiones, sin caracteres ilegales.

        git rechaza refnames con ':', '~', '^', ' ', '..', '*' entre otros
        (GEAS-003 media). Antes se hacia `title.replace(' ', '-')` y un titulo
        'GEAS-003: fix' producia 'GEAS-003:-fix', una rama que git no crea y
        que dejaba el ticket IN_PROGRESS sin rama fisica.
        """
        import re

        slug = title.lower().strip()
        slug = re.sub(r"[^a-z0-9._-]+", "-", slug)
        slug = slug.strip(".-")
        return (slug or "ticket")[:32]

    # ─── Tickets ───────────────────────────────────────────────────────

    def get_available_tasks(self) -> dict:
        """§17: tickets que el actor puede ejecutar ahora mismo."""
        if not self.org_id:
            orgs = self.storage.list_organizations()
            if not orgs:
                return _err("No hay organizaciones")
            self.org_id = orgs[0].id

        denied = self._authorize(self.org_id, "ticket:start")
        if denied:
            return denied

        tickets = self.storage.list_tickets(self.org_id)
        available = []
        for t in tickets:
            if t.status not in (TicketStatus.FREE, TicketStatus.CLAIMED):
                continue
            deps_ok = self.storage.are_dependencies_resolved(t.id)
            resources_ok = True
            blocked_by = ""
            for res_id in t.resources:
                lock = self.storage.get_lock_for_resource(res_id)
                if lock and lock.ticket_id != t.id:
                    resources_ok = False
                    blocked_by = lock.ticket_id
                    break
            if deps_ok and resources_ok:
                available.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status.value,
                        "priority": t.priority,
                        "branch": t.branch,
                    }
                )
            else:
                print(
                    f"[mcp] {t.id[:8]} SKIPPED: deps={'OK' if deps_ok else 'PENDING'} "
                    f"resources={'OK' if resources_ok else 'LOCKED by ' + blocked_by[:8]}"
                )
        return _ok({"available_tasks": available})

    def get_ticket(self, ticket_id: str) -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:read")
        if denied:
            return denied
        return _ok(
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status.value,
                "priority": t.priority,
                "assigned_actor_id": t.assigned_actor_id,
                "branch": t.branch,
                "commit_before": t.commit_before,
                "commit_after": t.commit_after,
                "dependencies": t.dependencies,
                "resources": t.resources,
                "result": t.result,
                "created_at": t.created_at,
            }
        )

    def create_ticket(
        self,
        title: str,
        description: str = "",
        repository_id: str = "",
        department_id: str = "",
        priority: int = 0,
        resources: list[str] | None = None,
        dependencies: list[str] | None = None,
    ) -> dict:
        orgs = self.storage.list_organizations()
        if not orgs:
            return _err("No hay organizaciones")
        org_id = self.org_id or orgs[0].id
        denied = self._authorize(org_id, "ticket:create")
        if denied:
            return denied

        # Validar dependencias: no circular, deben existir
        for dep in dependencies or []:
            if not self.storage.get_ticket(dep):
                return _err(f"Dependencia no existe: {dep}")

        # GEAS-003: validar que cada resource exista ANTES de guardar. Un id
        # truncado se colaba y el fallo aparecia al reclamar, como un
        # FOREIGN KEY constraint failed / HTTP 000. Aqui se rechaza el ticket
        # y se dice cual es el id que no existe.
        for res_id in resources or []:
            if not self.storage.get_resource(res_id):
                return _err(
                    f"Recurso no existe: {res_id}",
                    {"resource_id": res_id},
                )

        t = Ticket(
            organization_id=org_id,
            department_id=department_id or None,
            repository_id=repository_id or None,
            title=title,
            description=description,
            priority=priority,
            resources=resources or [],
            dependencies=dependencies or [],
            creator_id=self.actor_id,
        )
        # GEAS-005: ticket, dependencias, evento y audit van en UNA sola
        # transaccion. Antes eran cuatro `commit()` sueltos: si fallaba el
        # tercero (el evento), el ticket ya estaba confirmado en su propia
        # transaccion y el audit no llegaba a escribirse, dejando filas a medias.
        # Ahora entra todo o no entra nada.
        with self.storage.transaction():
            self.storage.create_ticket(t)

            for dep in dependencies or []:
                self.storage.create_dependency(
                    TicketDependency(
                        ticket_id=t.id,
                        depends_on_ticket_id=dep,
                    )
                )

            self.storage.create_event(
                Event(
                    event_type="TICKET_CREATED",
                    organization_id=org_id,
                    actor_id=self.actor_id,
                    resource_type="ticket",
                    resource_id=t.id,
                )
            )
            self.storage.create_audit(
                AuditLog(
                    actor_id=self.actor_id,
                    action="CREATE_TICKET",
                    resource_type="ticket",
                    resource_id=t.id,
                )
            )
        return _ok({"id": t.id, "title": t.title, "status": t.status.value})

    def update_ticket(self, ticket_id: str, **fields: Any) -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:update")
        if denied:
            return denied
        allowed = {"title", "description", "priority", "result", "feedback"}
        updates = {k: v for k, v in fields.items() if k in allowed and k != "id"}
        self.storage.update_ticket_fields(ticket_id, **updates)
        return _ok({"id": ticket_id, "updated": list(updates.keys())})

    def start_ticket(self, ticket_id: str, commit_before: str = "") -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:start")
        if denied:
            return denied
        denied = self._authorize(t.organization_id, "resource:lock")
        if denied:
            return denied
        if t.status != TicketStatus.FREE:
            return _err(f"Ticket no está FREE: {t.status.value}")
        if not self.storage.are_dependencies_resolved(ticket_id):
            return _err("DEPENDENCIES_UNRESOLVED")

        acquired, blocked_resource = self.storage.acquire_locks(
            t.resources, ticket_id, self.actor_id
        )
        if not acquired:
            lock = self.storage.get_lock_for_resource(blocked_resource or "")
            return _err(
                "RESOURCE_UNAVAILABLE",
                {
                    "resource_id": blocked_resource,
                    "locked_by": lock.ticket_id if lock else "",
                },
            )

        branch = f"{ticket_id[:8]}-{self._safe_branch(t.title)}"
        self.storage.start_ticket(
            ticket_id,
            commit_before=commit_before,
            branch=branch,
            actor_id=self.actor_id,
        )
        self.storage.create_event(
            Event(
                event_type="TICKET_STARTED",
                organization_id=t.organization_id,
                actor_id=self.actor_id,
                resource_type="ticket",
                resource_id=ticket_id,
                metadata={"branch": branch, "commit_before": commit_before},
            )
        )
        return _ok({"id": ticket_id, "status": "IN_PROGRESS", "branch": branch})

    def block_ticket(self, ticket_id: str, reason: str = "") -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:block")
        if denied:
            return denied
        self.storage.update_ticket_status(ticket_id, "BLOCKED")
        self.storage.create_event(
            Event(
                event_type="TICKET_BLOCKED",
                organization_id=t.organization_id,
                actor_id=self.actor_id,
                resource_type="ticket",
                resource_id=ticket_id,
                metadata={"reason": reason},
            )
        )
        return _ok({"id": ticket_id, "status": "BLOCKED"})

    def complete_ticket(
        self, ticket_id: str, commit_after: str = "", result: str = ""
    ) -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:complete")
        if denied:
            return denied
        denied = self._authorize(t.organization_id, "resource:unlock")
        if denied:
            return denied
        # GEAS-005: mismo motivo que en create_ticket: el cambio de estado, la
        # liberacion de locks y el evento van juntos o no va ninguno.
        with self.storage.transaction():
            self.storage.complete_ticket(
                ticket_id, commit_after=commit_after, result=result
            )
            released = self.storage.release_locks_for_ticket(ticket_id)
            self.storage.create_event(
                Event(
                    event_type="TICKET_COMPLETED",
                    organization_id=t.organization_id,
                    actor_id=self.actor_id,
                    resource_type="ticket",
                    resource_id=ticket_id,
                    metadata={"commit_after": commit_after, "locks_released": released},
                )
            )
        return _ok({"id": ticket_id, "status": "DONE", "locks_released": released})

    def cancel_ticket(self, ticket_id: str, reason: str = "") -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:update")
        if denied:
            return denied
        # GEAS-005: estado, locks y evento en una sola transaccion.
        with self.storage.transaction():
            self.storage.update_ticket_status(ticket_id, "CANCELLED")
            released = self.storage.release_locks_for_ticket(ticket_id)
            self.storage.create_event(
                Event(
                    event_type="TICKET_CANCELLED",
                    organization_id=t.organization_id,
                    actor_id=self.actor_id,
                    resource_type="ticket",
                    resource_id=ticket_id,
                    # El motivo va en el evento y no en una columna nueva del
                    # ticket: `tickets` no tiene campo para el y a nadie le
                    # hace falta uno, mientras que perder el motivo era
                    # precisamente el fallo (3662e133).
                    metadata={"reason": reason} if reason else {},
                )
            )
        return _ok(
            {
                "id": ticket_id,
                "status": "CANCELLED",
                "locks_released": released,
                "reason": reason,
            }
        )

    def get_dependencies(self, ticket_id: str) -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:read")
        if denied:
            return denied
        deps = self.storage.get_dependencies(ticket_id)
        blocked = self.storage.get_blocked_by(ticket_id)
        return _ok(
            {
                "depends_on": [
                    {"id": d.id, "title": d.title, "status": d.status.value}
                    for d in deps
                ],
                "blocked_by_me": [
                    {"id": b.id, "title": b.title, "status": b.status.value}
                    for b in blocked
                ],
                "resolved": self.storage.are_dependencies_resolved(ticket_id),
            }
        )

    # ─── Resources y locks ─────────────────────────────────────────────

    def lock_resource(self, resource_id: str, ticket_id: str = "") -> dict:
        res = self.storage.get_resource(resource_id)
        if not res:
            return _err(f"Recurso no encontrado: {resource_id}")
        repo = self.storage.get_repository(res.repository_id or "")
        if not repo:
            return _err("Repositorio no encontrado")
        denied = self._authorize(repo.organization_id, "resource:lock")
        if denied:
            return denied
        ticket = self.storage.get_ticket(ticket_id)
        if not ticket:
            return _err("ticket_id es obligatorio para bloquear un recurso")
        acquired, _ = self.storage.acquire_locks(
            [resource_id], ticket_id, self.actor_id
        )
        if not acquired:
            existing = self.storage.get_lock_for_resource(resource_id)
            return _err(
                "RESOURCE_UNAVAILABLE",
                {
                    "locked_by": existing.ticket_id if existing else "",
                    "expires_at": existing.expires_at if existing else "",
                },
            )
        self.storage.create_event(
            Event(
                event_type="RESOURCE_LOCKED",
                organization_id=self.org_id
                or (
                    self.storage.list_organizations()[0].id
                    if self.storage.list_organizations()
                    else ""
                ),
                actor_id=self.actor_id,
                resource_type="resource",
                resource_id=resource_id,
                metadata={"ticket_id": ticket_id},
            )
        )
        lock = self.storage.get_lock_for_resource(resource_id)
        return _ok(
            {
                "resource_id": resource_id,
                "locked": True,
                "expires_at": lock.expires_at if lock else "",
            }
        )

    def release_resource(self, resource_id: str) -> dict:
        lock = self.storage.get_lock_for_resource(resource_id)
        if not lock:
            return _ok({"resource_id": resource_id, "locked": False})
        ticket = self.storage.get_ticket(lock.ticket_id)
        if not ticket:
            return _err("Ticket del lock no encontrado")
        denied = self._authorize(ticket.organization_id, "resource:unlock")
        if denied:
            return denied
        self.storage.release_lock(lock.id)
        self.storage.create_event(
            Event(
                event_type="RESOURCE_RELEASED",
                organization_id=self.org_id or "",
                actor_id=self.actor_id,
                resource_type="resource",
                resource_id=resource_id,
                metadata={"ticket_id": lock.ticket_id},
            )
        )
        return _ok({"resource_id": resource_id, "locked": False})

    # ─── Repos ──────────────────────────────────────────────────────

    def get_repository_context(self, repository_id: str) -> dict:
        repo = self.storage.get_repository(repository_id)
        if not repo:
            return _err(f"Repository no encontrado: {repository_id}")
        denied = self._authorize(repo.organization_id, "repository:read")
        if denied:
            return denied
        tickets = [
            t
            for t in self.storage.list_tickets(repo.organization_id)
            if t.repository_id == repository_id
        ]
        resources = self.storage.list_resources(repository_id)
        locks = {}
        for res in resources:
            lock = self.storage.get_lock_for_resource(res.id)
            if lock:
                locks[res.path] = {
                    "ticket_id": lock.ticket_id,
                    "expires_at": lock.expires_at,
                }
        return _ok(
            {
                "id": repo.id,
                "name": repo.name,
                "provider": repo.provider,
                "url": repo.url,
                "default_branch": repo.default_branch,
                "tickets": [
                    {"id": t.id, "title": t.title, "status": t.status.value}
                    for t in tickets
                ],
                "resources_locked": locks,
            }
        )

    # ─── Contexto organizativo (§43) ──────────────────────────────────

    def _resolve_org_id(self, organization_id: str = "") -> str:
        """Org explícita → org del actor → primera; '' si no hay ninguna."""
        return (
            organization_id
            or self.org_id
            or (
                self.storage.list_organizations()[0].id
                if self.storage.list_organizations()
                else ""
            )
        )

    def get_organization(self, organization_id: str = "") -> dict:
        """Perfil de despliegue, componentes y backend declarado (§43)."""
        from geas.profiles import backend_for, components

        org_id = self._resolve_org_id(organization_id)
        org = self.storage.get_organization(org_id) if org_id else None
        if not org:
            return _err("No hay organización — ejecuta 'geas init'")
        denied = self._authorize(org.id, "department:read")
        if denied:
            return denied
        return _ok(
            {
                "id": org.id,
                "name": org.name,
                "description": org.description,
                "profile": org.profile,
                "backend": backend_for(org.profile),
                "components": sorted(components(org.profile)),
                "active": org.active,
            }
        )

    def list_departments(self, organization_id: str = "") -> dict:
        org_id = self._resolve_org_id(organization_id)
        if not org_id:
            return _err("No hay organización — ejecuta 'geas init'")
        denied = self._authorize(org_id, "department:read")
        if denied:
            return denied
        depts = self.storage.list_departments(org_id)
        return _ok(
            {
                "organization_id": org_id,
                "departments": [
                    {
                        "id": d.id,
                        "name": d.name,
                        "description": d.description,
                        "parent_department_id": d.parent_department_id,
                    }
                    for d in depts
                ],
            }
        )

    def get_department(self, name: str, organization_id: str = "") -> dict:
        """Departamento por nombre (§43) + los repos que cuelgan de él."""
        org_id = self._resolve_org_id(organization_id)
        if not org_id:
            return _err("No hay organización — ejecuta 'geas init'")
        denied = self._authorize(org_id, "department:read")
        if denied:
            return denied
        dept = self.storage.get_department_by_name(org_id, name)
        if not dept:
            return _err(f"Departamento no encontrado: {name}")
        repos = [
            r
            for r in self.storage.list_repositories(org_id)
            if r.department_id == dept.id
        ]
        return _ok(
            {
                "id": dept.id,
                "name": dept.name,
                "description": dept.description,
                "parent_department_id": dept.parent_department_id,
                "repositories": [
                    {"id": r.id, "name": r.name, "url": r.url, "provider": r.provider}
                    for r in repos
                ],
            }
        )

    def list_repositories(self, organization_id: str = "") -> dict:
        org_id = self._resolve_org_id(organization_id)
        if not org_id:
            return _err("No hay organización — ejecuta 'geas init'")
        denied = self._authorize(org_id, "repository:read")
        if denied:
            return denied
        repos = self.storage.list_repositories(org_id)
        return _ok(
            {
                "organization_id": org_id,
                "repositories": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "department_id": r.department_id,
                        "url": r.url,
                        "provider": r.provider,
                        "default_branch": r.default_branch,
                    }
                    for r in repos
                ],
            }
        )

    # ─── Actores y recursos (GEAS-007) ──────────────────────────────

    def list_actors(self, organization_id: str = "") -> dict:
        """Actores (users + agents) de una organización (§GEAS-007).

        Antes la única forma de saber "quién hay" era leer roster.env, un
        fichero de bootstrap. Con esto, un dispositivo nuevo pregunta a la
        API en vez de depender de que alguien re-ejecute bootstrap.
        Rol agent incluido (permiso user:read).
        """
        org_id = self._resolve_org_id(organization_id)
        if not org_id:
            return _err("No hay organización — ejecuta 'geas init'")
        denied = self._authorize(org_id, "user:read")
        if denied:
            return denied
        users = self.storage.list_users(org_id)
        agents = self.storage.list_agents(org_id)
        return _ok(
            {
                "organization_id": org_id,
                "actors": [
                    {
                        "id": u.id,
                        "name": u.name,
                        "kind": "user",
                        "email": u.email,
                        "department_id": u.department_id,
                        "role_id": u.role_id,
                        "active": u.active,
                    }
                    for u in users
                ]
                + [
                    {
                        "id": a.id,
                        "name": a.name,
                        "kind": "agent",
                        "provider": a.provider,
                        "model": a.model,
                        "department_id": a.department_id,
                        "role_id": a.role_id,
                        "active": a.active,
                    }
                    for a in agents
                ],
            }
        )

    def list_resources(self, repository_id: str) -> dict:
        """Recursos bloqueables de un repo (§GEAS-007).

        Antes había que abrir roster.env (o la BD) para montar un
        create_ticket con los ids completos de resource. Esto cierra el
        hueco: la API responde lo mismo que `geas_admin.py show` sin abrir
        la BD. Rol agent incluido (permiso resource:read).
        """
        if not repository_id:
            return _err("repository_id requerido")
        repo = self.storage.get_repository(repository_id)
        if not repo:
            return _err(f"Repositorio no existe: {repository_id}")
        denied = self._authorize(repo.organization_id, "resource:read")
        if denied:
            return denied
        resources = self.storage.list_resources(repository_id)
        return _ok(
            {
                "repository_id": repository_id,
                "resources": [
                    {
                        "id": r.id,
                        "path": r.path,
                        "type": r.type.value,
                        "metadata": r.metadata,
                    }
                    for r in resources
                ],
            }
        )

    def list_tickets(self, status: str | None = None, org_id: str | None = None) -> dict:
        """Listar tickets, con filtros opcionales por estado y organización."""
        if not org_id:
            return _err("org_id requerido")
        denied = self._authorize(org_id, "ticket:read")
        if denied:
            return denied
        try:
            tickets = self.storage.list_tickets(org_id)
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudieron listar tickets: {exc}")
        if status:
            s = status.upper()
            tickets = [t for t in tickets if t.status.value.upper() == s]
        return _ok(
            {
                "org_id": org_id,
                "count": len(tickets),
                "tickets": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status.value,
                        "priority": t.priority,
                        "assignee": t.assignee,
                        "branch": t.branch,
                        "repository_id": t.repository_id,
                        "department_id": t.department_id,
                    }
                    for t in tickets
                ],
            }
        )
    def has_unpublished(self, repository_id: str, branch: str | None = None) -> dict:
        """¿Hay trabajo sin publicar en este repo?

        «Sin publicar» = commits locales por delante de origin (ahead > 0). No
        obliga a hacer fetch: status() ya lee el upstream local si existe.
        Requiere permiso `git:read` sobre el repo.
        """
        if not repository_id:
            return _err("repository_id requerido")
        repo = self.storage.get_repository(repository_id)
        if not repo:
            return _err(f"Repositorio no existe: {repository_id}")
        denied = self._authorize(repo.organization_id, "git:read")
        if denied:
            return denied
        from geas.git import LocalGitProvider

        try:
            g = LocalGitProvider(repo.path)
        except Exception as exc:  # noqa: BLE001
            return _err(f"No es un repo Git en {repo.path}: {exc}")
        try:
            if branch:
                pass
            ahead = g.status().ahead
        except Exception as exc:  # noqa: BLE001
            return _err(f"git status falló en {repo.path}: {exc}")
        return _ok({"repository_id": repository_id, "ahead": ahead, "has_unpublished": ahead > 0})

    def is_published(self, commit: str, repository_id: str | None = None) -> dict:
        """¿Está este commit visible en alguna rama del remoto (origin)?

        Requiere permiso `git:read` sobre algún repo (o el indicado).
        """
        if not commit:
            return _err("commit requerido")
        org_id = None
        repo_path = None
        if repository_id:
            repo = self.storage.get_repository(repository_id)
            if not repo:
                return _err(f"Repositorio no existe: {repository_id}")
            org_id = repo.organization_id
            repo_path = repo.path
        if org_id:
            denied = self._authorize(org_id, "git:read")
            if denied:
                return denied
        from geas.git import LocalGitProvider

        try:
            if repo_path:
                g = LocalGitProvider(repo_path)
            else:
                g = LocalGitProvider()
        except Exception as exc:  # noqa: BLE001
            return _err(f"No se pudo inicializar git: {exc}")
        try:
            published = g.is_published(commit)
        except Exception as exc:  # noqa: BLE001
            return _err(f"git falló: {exc}")
        return _ok({"commit": commit, "published": published})


    def get_permissions(self) -> dict:
        """Permisos EFECTIVOS del actor que pregunta (§7).

        GEAS-004: antes devolvia ALL_PERMISSIONS (el conjunto del perfil) a
        todos los actores, de modo que un agente creia poder llamar
        update_ticket y luego recibia FORBIDDEN. Ahora devuelve
        storage.get_actor_permissions(actor, org): 21 al manager, 14 al
        agente, los mismos numeros que geas_admin.py show.
        """
        org_id = self.org_id or (
            self.storage.list_organizations()[0].id
            if self.storage.list_organizations()
            else ""
        )
        if not org_id:
            return _err("No hay organizaciones")
        permissions = sorted(
            self.storage.get_actor_permissions(self.actor_id, org_id)
        )
        return _ok({"permissions": permissions})

    # ─── Sync ──────────────────────────────────────────────────────

    def sync(self) -> dict:
        from geas.git import LocalGitProvider

        org_id = self.org_id or (
            self.storage.list_organizations()[0].id
            if self.storage.list_organizations()
            else ""
        )
        if not org_id:
            return _err("No hay organizaciones")
        denied = self._authorize(org_id, "repository:read")
        if denied:
            return denied

        g = LocalGitProvider()
        s = g.status()
        return _ok(
            {
                "branch": s.branch,
                "clean": s.clean,
                "ahead": s.ahead,
                "behind": s.behind,
                "modified": s.modified,
                "untracked": s.untracked,
            }
        )

    # ─── Ejecuciones y tests ───────────────────────────────────────────

    def report_commit(self, ticket_id: str, commit_sha: str, message: str = "") -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "git:commit")
        if denied:
            return denied
        if not t.commit_before:
            self.storage.conn.execute(
                "UPDATE tickets SET commit_before = ? WHERE id = ?",
                (commit_sha, ticket_id),
            )
        else:
            self.storage.conn.execute(
                "UPDATE tickets SET commit_after = ? WHERE id = ?",
                (commit_sha, ticket_id),
            )
        self.storage.conn.commit()
        self.storage.create_event(
            Event(
                event_type="COMMIT_REGISTERED",
                organization_id=t.organization_id,
                actor_id=self.actor_id,
                resource_type="ticket",
                resource_id=ticket_id,
                metadata={"commit": commit_sha}
                | ({"message": message} if message else {}),
            )
        )
        return _ok({"ticket_id": ticket_id, "commit": commit_sha})

    def report_test_result(
        self, ticket_id: str, commit_id: str, status: str, logs_reference: str = ""
    ) -> dict:
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:update")
        if denied:
            return denied
        self.storage.create_test_result(
            TestResult(
                ticket_id=ticket_id,
                commit_id=commit_id,
                pipeline_id="",
                status=status,
                logs_reference=logs_reference,
            )
        )
        self.storage.create_event(
            Event(
                event_type=f"TEST_{'PASSED' if status == 'passed' else 'FAILED'}",
                organization_id=t.organization_id,
                actor_id=self.actor_id,
                resource_type="ticket",
                resource_id=ticket_id,
                metadata={"commit": commit_id},
            )
        )
        return _ok({"ticket_id": ticket_id, "status": status})

    def get_execution(self, ticket_id: str) -> dict:
        ticket = self.storage.get_ticket(ticket_id)
        if not ticket:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(ticket.organization_id, "execution:read")
        if denied:
            return denied
        executions = self.storage.get_executions(ticket_id)
        return _ok(
            {
                "executions": [
                    {
                        "id": e.id,
                        "actor_id": e.actor_id,
                        "harness_id": e.harness_id,
                        "provider": e.provider,
                        "model": e.model,
                        "model_version": e.model_version,
                        "tokens_input": e.tokens_input,
                        "tokens_output": e.tokens_output,
                        "cost": e.cost,
                        "result": e.result,
                        "started_at": e.started_at,
                        "finished_at": e.finished_at,
                    }
                    for e in executions
                ]
            }
        )

    def report_execution(
        self,
        ticket_id: str,
        provider: str = "",
        model: str = "",
        model_version: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost: float = 0.0,
        tools_used: list[str] | None = None,
        iterations: int = 0,
        result: str = "",
    ) -> dict:
        """Registra una ejecución (spec §28: model traceability)."""
        t = self.storage.get_ticket(ticket_id)
        if not t:
            return _err(f"Ticket no encontrado: {ticket_id}")
        denied = self._authorize(t.organization_id, "ticket:start")
        if denied:
            return denied
        exe = Execution(
            ticket_id=ticket_id,
            actor_id=self.actor_id,
            provider=provider,
            model=model,
            model_version=model_version,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost=cost,
            tools_used=tools_used or [],
            iterations=iterations,
            result=result,
        )
        self.storage.create_execution(exe)
        self.storage.create_event(
            Event(
                event_type="AGENT_STARTED",
                organization_id=t.organization_id,
                actor_id=self.actor_id,
                resource_type="execution",
                resource_id=exe.id,
                metadata={"model": f"{provider}/{model}", "cost": cost},
            )
        )
        return _ok({"execution_id": exe.id, "ticket_id": ticket_id})

    # ─── Despacho único ────────────────────────────────────────────────

    def call(self, tool: str, params: dict | None = None) -> dict:
        """Despacha una llamada MCP por nombre de herramienta."""
        params = params or {}
        try:
            if tool == "get_available_tasks":
                return self.get_available_tasks()
            if tool == "get_ticket":
                return self.get_ticket(params["ticket_id"])
            if tool == "create_ticket":
                return self.create_ticket(
                    title=params.get("title", ""),
                    description=params.get("description", ""),
                    repository_id=params.get("repository_id", ""),
                    department_id=params.get("department_id", ""),
                    priority=params.get("priority", 0),
                    resources=params.get("resources"),
                    dependencies=params.get("dependencies"),
                )
            if tool == "update_ticket":
                return self.update_ticket(
                    params["ticket_id"], **params.get("fields", {})
                )
            if tool == "start_ticket":
                return self.start_ticket(
                    params["ticket_id"], params.get("commit_before", "")
                )
            if tool == "block_ticket":
                return self.block_ticket(params["ticket_id"], params.get("reason", ""))
            if tool == "complete_ticket":
                return self.complete_ticket(
                    params["ticket_id"],
                    params.get("commit_after", ""),
                    params.get("result", ""),
                )
            if tool == "cancel_ticket":
                return self.cancel_ticket(
                    params["ticket_id"], params.get("reason", "")
                )
            if tool == "get_dependencies":
                return self.get_dependencies(params["ticket_id"])
            if tool == "lock_resource":
                return self.lock_resource(
                    params["resource_id"], params.get("ticket_id", "")
                )
            if tool == "release_resource":
                return self.release_resource(params["resource_id"])
            if tool == "get_repository_context":
                return self.get_repository_context(params["repository_id"])
            if tool == "get_organization":
                return self.get_organization(params.get("organization_id", ""))
            if tool == "list_departments":
                return self.list_departments(params.get("organization_id", ""))
            if tool == "get_department":
                return self.get_department(
                    params["name"], params.get("organization_id", "")
                )
            if tool == "list_repositories":
                return self.list_repositories(params.get("organization_id", ""))
            if tool == "list_actors":
                return self.list_actors(params.get("organization_id", ""))
            if tool == "list_resources":
                return self.list_resources(params["repository_id"])
            if tool == "has_unpublished":
                return self.has_unpublished(
                    params.get("repository_id", ""), params.get("branch")
                )
            if tool == "is_published":
                return self.is_published(params["commit"], params.get("repository_id"))
            if tool == "list_tickets":
                return self.list_tickets(
                    params.get("status"), params.get("org_id")
                )
            if tool == "get_permissions":
                return self.get_permissions()
            if tool == "sync":
                return self.sync()
            if tool == "report_commit":
                return self.report_commit(
                    params["ticket_id"],
                    params["commit_sha"],
                    params.get("message", ""),
                )
            if tool == "report_test_result":
                return self.report_test_result(
                    params["ticket_id"],
                    params.get("commit_id", ""),
                    params.get("status", ""),
                    params.get("logs_reference", ""),
                )
            if tool == "get_execution":
                return self.get_execution(params["ticket_id"])
            if tool == "report_execution":
                return self.report_execution(
                    params["ticket_id"],
                    params.get("provider", ""),
                    params.get("model", ""),
                    params.get("model_version", ""),
                    params.get("tokens_input", 0),
                    params.get("tokens_output", 0),
                    params.get("cost", 0.0),
                    params.get("tools_used"),
                    params.get("iterations", 0),
                    params.get("result", ""),
                )
            return _err(f"Herramienta desconocida: {tool}")
        except KeyError as exc:
            return _err(f"Falta parámetro: {exc}")


# ─── Tools disponibles (para documentar el contrato MCP) ───────────────────

TOOLS = [
    {
        "name": "get_available_tasks",
        "description": "Tickets que el actor puede ejecutar (§17)",
    },
    {
        "name": "get_ticket",
        "description": "Detalle de un ticket",
        "params": ["ticket_id"],
    },
    {
        "name": "create_ticket",
        "description": "Crear un ticket",
        "params": [
            "title",
            "description",
            "repository_id",
            "department_id",
            "priority",
            "resources",
            "dependencies",
        ],
    },
    {
        "name": "update_ticket",
        "description": "Actualizar un ticket",
        "params": ["ticket_id", "fields"],
    },
    {
        "name": "start_ticket",
        "description": "Empezar ticket: locks + commit_before",
        "params": ["ticket_id", "commit_before"],
    },
    {
        "name": "block_ticket",
        "description": "Bloquear ticket",
        "params": ["ticket_id", "reason"],
    },
    {
        "name": "complete_ticket",
        "description": "Completar: commit_after + release locks",
        "params": ["ticket_id", "commit_after", "result"],
    },
    {
        "name": "cancel_ticket",
        "description": "Cancelar ticket",
        "params": ["ticket_id", "reason"],
    },
    {
        "name": "get_dependencies",
        "description": "Dependencias de un ticket",
        "params": ["ticket_id"],
    },
    {
        "name": "lock_resource",
        "description": "Bloquear recurso",
        "params": ["resource_id"],
    },
    {
        "name": "release_resource",
        "description": "Liberar recurso",
        "params": ["resource_id"],
    },
    {
        "name": "get_repository_context",
        "description": "Contexto de un repo",
        "params": ["repository_id"],
    },
    {"name": "sync", "description": "Estado Git del repo"},
    {
        "name": "report_commit",
        "description": "Registrar commit",
        "params": ["ticket_id", "commit_sha", "message"],
    },
    {
        "name": "report_test_result",
        "description": "Registrar resultado de tests",
        "params": ["ticket_id", "commit_id", "status"],
    },
    {
        "name": "get_execution",
        "description": "Ejecuciones de un ticket",
        "params": ["ticket_id"],
    },
    {
        "name": "report_execution",
        "description": "Registrar ejecución (model traceability)",
        "params": ["ticket_id", "provider", "model"],
    },
    {
        "name": "get_organization",
        "description": "Perfil, componentes y backend declarado de la org (§43)",
        "params": ["organization_id"],
    },
    {
        "name": "list_departments",
        "description": "Departamentos de la organización (§43)",
        "params": ["organization_id"],
    },
    {
        "name": "get_department",
        "description": "Departamento por nombre + sus repos",
        "params": ["name", "organization_id"],
    },
    {
        "name": "list_repositories",
        "description": "Repositorios de la organización (§43)",
        "params": ["organization_id"],
    },
    {
        "name": "list_actors",
        "description": "Actores (users + agents) de la organización (GEAS-007)",
        "params": ["organization_id"],
    },
    {
        "name": "list_resources",
        "description": "Recursos bloqueables de un repo (GEAS-007)",
        "params": ["repository_id"],
    },
    {
        "name": "has_unpublished",
        "description": "¿Hay trabajo sin publicar en el repo (commits ahead de origin)?",
        "params": ["repository_id", "branch"],
    },
    {
        "name": "is_published",
        "description": "¿Está un commit visible en alguna rama del remoto (origin)?",
        "params": ["commit", "repository_id"],
    },
    {
        "name": "list_tickets",
        "description": "Listar tickets (opcionalmente filtrados por estado/organización)",
        "params": ["status", "org_id"],
    },
    {
        "name": "get_permissions",
        "description": "Permisos efectivos del actor que pregunta §7",
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def mcp_server(storage: Storage, actor_id: str = "agent") -> dict:
    """Punto de entrada MCP: expone las herramientas (§26).

    Para transporte stdio real, ejecuta ``geas mcp serve <actor_id>``.
    """
    return {
        "protocol": "mcp",
        "tools": TOOLS,
        "handler": GeasMcp(storage, actor_id=actor_id),
    }
