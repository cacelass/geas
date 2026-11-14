"""geas.storage_mongo — almacén MongoDB con la misma API que ``Storage``.

GEAS nació con SQLite porque no necesita nada más. Este backend existe para
cuando la BD tiene que vivir fuera de la máquina (equipo, varias personas,
varios dispositivos) sin montar un PostgreSQL: MongoDB Atlas da la misma
garantía que aquí importa — **reclamar un ticket es atómico**.

Tres reglas que se mantienen igual que en SQLite:

  - ``start_ticket``/``complete_ticket``/``assign_ticket`` son UN
    ``find_one_and_update`` con guardia de estado. No hay check-then-act.
  - ``transaction()`` agrupa escrituras: ticket + evento + audit van juntas o
    no va ninguna. Usa transacciones de sesión de Mongo (réplica set/Atlas).
  - Los locks son todos o ninguno: índice único en ``resource_id`` y
    transacción; un segundo actor recibe el ``resource_id`` bloqueado.

El cliente es ``pymongo`` (dependencia opcional ``geas[mongo]``). Sin él,
construir este backend falla claro en vez de caer a SQLite en silencio.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from geas.models import (
    Agent,
    AuditLog,
    Department,
    Device,
    Event,
    Execution,
    Organization,
    Policy,
    Repository,
    Resource,
    ResourceLock,
    ResourceType,
    Role,
    TestResult,
    Ticket,
    TicketDependency,
    TicketStatus,
    User,
    Visibility,
)
from geas.storage import Storage


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ─── Modelo → documento ─────────────────────────────────────────────────────


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _doc(obj: Any) -> dict[str, Any]:
    """Dataclass → documento. ``_id`` es el UUID del modelo."""
    data = {"id": obj.id}
    for name in obj.__dataclass_fields__:
        if name == "id":
            continue
        data[name] = _value(getattr(obj, name))
    return {"_id": obj.id, **data}


# ─── Documento → modelo ─────────────────────────────────────────────────────


def _org(d: dict) -> Organization:
    return Organization(
        id=d["id"],
        name=d.get("name", ""),
        description=d.get("description", ""),
        created_at=d.get("created_at", ""),
        active=bool(d.get("active", True)),
        profile=d.get("profile", "individual"),
    )


def _dept(d: dict) -> Department:
    return Department(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        parent_department_id=d.get("parent_department_id"),
        name=d.get("name", ""),
        description=d.get("description", ""),
        created_at=d.get("created_at", ""),
        active=bool(d.get("active", True)),
    )


def _role(d: dict) -> Role:
    return Role(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        name=d.get("name", ""),
        permissions=list(d.get("permissions") or []),
    )


def _policy(d: dict) -> Policy:
    return Policy(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        department_id=d.get("department_id"),
        name=d.get("name", ""),
        description=d.get("description", ""),
        permissions=list(d.get("permissions") or []),
        created_at=d.get("created_at", ""),
        active=bool(d.get("active", True)),
    )


def _user(d: dict) -> User:
    return User(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        name=d.get("name", ""),
        email=d.get("email", ""),
        department_id=d.get("department_id"),
        role_id=d.get("role_id"),
        active=bool(d.get("active", True)),
        created_at=d.get("created_at", ""),
    )


def _agent(d: dict) -> Agent:
    return Agent(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        name=d.get("name", ""),
        provider=d.get("provider", ""),
        model=d.get("model", ""),
        model_version=d.get("model_version", ""),
        department_id=d.get("department_id"),
        role_id=d.get("role_id"),
        harness_id=d.get("harness_id", ""),
        active=bool(d.get("active", True)),
        configuration=dict(d.get("configuration") or {}),
        created_at=d.get("created_at", ""),
    )


def _device(d: dict) -> Device:
    return Device(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        name=d.get("name", ""),
        active=bool(d.get("active", True)),
        created_at=d.get("created_at", ""),
    )


def _repo(d: dict) -> Repository:
    return Repository(
        id=d["id"],
        organization_id=d.get("organization_id", ""),
        department_id=d.get("department_id"),
        name=d.get("name", ""),
        provider=d.get("provider", ""),
        url=d.get("url", ""),
        default_branch=d.get("default_branch", "main"),
        visibility=Visibility(d.get("visibility", "PRIVATE")),
        active=bool(d.get("active", True)),
    )


def _resource(d: dict) -> Resource:
    return Resource(
        id=d["id"],
        repository_id=d.get("repository_id"),
        path=d.get("path", ""),
        type=ResourceType(d.get("type", "FILE")),
        metadata=dict(d.get("metadata") or {}),
    )


def _lock(d: dict) -> ResourceLock:
    return ResourceLock(
        id=d["id"],
        resource_id=d.get("resource_id", ""),
        ticket_id=d.get("ticket_id", ""),
        actor_id=d.get("actor_id", ""),
        created_at=d.get("created_at", ""),
        expires_at=d.get("expires_at", ""),
        last_heartbeat=d.get("last_heartbeat", ""),
    )


def _ticket(d: dict) -> Ticket:
    return Ticket(
        id=d["id"],
        title=d.get("title", ""),
        description=d.get("description", ""),
        organization_id=d.get("organization_id", ""),
        department_id=d.get("department_id"),
        repository_id=d.get("repository_id"),
        creator_id=d.get("creator_id", ""),
        assigned_actor_id=d.get("assigned_actor_id", ""),
        created_at=d.get("created_at", ""),
        started_at=d.get("started_at"),
        completed_at=d.get("completed_at"),
        status=TicketStatus(d.get("status", "FREE")),
        priority=d.get("priority", 0),
        files=list(d.get("files") or []),
        resources=list(d.get("resources") or []),
        dependencies=list(d.get("dependencies") or []),
        blocked_tickets=list(d.get("blocked_tickets") or []),
        result=d.get("result", ""),
        feedback=d.get("feedback", ""),
        branch=d.get("branch", ""),
        commit_before=d.get("commit_before", ""),
        commit_after=d.get("commit_after", ""),
    )


def _execution(d: dict) -> Execution:
    return Execution(
        id=d["id"],
        ticket_id=d.get("ticket_id", ""),
        actor_id=d.get("actor_id", ""),
        harness_id=d.get("harness_id", ""),
        provider=d.get("provider", ""),
        model=d.get("model", ""),
        model_version=d.get("model_version", ""),
        started_at=d.get("started_at", ""),
        finished_at=d.get("finished_at"),
        tokens_input=d.get("tokens_input", 0),
        tokens_output=d.get("tokens_output", 0),
        cost=d.get("cost", 0.0),
        tools_used=list(d.get("tools_used") or []),
        iterations=d.get("iterations", 0),
        result=d.get("result", ""),
    )


def _test_result(d: dict) -> TestResult:
    return TestResult(
        id=d["id"],
        ticket_id=d.get("ticket_id", ""),
        commit_id=d.get("commit_id", ""),
        pipeline_id=d.get("pipeline_id", ""),
        status=d.get("status", ""),
        started_at=d.get("started_at", ""),
        finished_at=d.get("finished_at"),
        logs_reference=d.get("logs_reference", ""),
    )


def _event(d: dict) -> Event:
    return Event(
        id=d["id"],
        event_type=d.get("event_type", ""),
        organization_id=d.get("organization_id", ""),
        actor_id=d.get("actor_id", ""),
        resource_type=d.get("resource_type", ""),
        resource_id=d.get("resource_id", ""),
        timestamp=d.get("timestamp", ""),
        metadata=dict(d.get("metadata") or {}),
    )


def _audit(d: dict) -> AuditLog:
    return AuditLog(
        id=d["id"],
        actor_id=d.get("actor_id", ""),
        action=d.get("action", ""),
        resource_type=d.get("resource_type", ""),
        resource_id=d.get("resource_id", ""),
        timestamp=d.get("timestamp", ""),
        metadata=dict(d.get("metadata") or {}),
    )


# ─── Índices ────────────────────────────────────────────────────────────────

INDEXES: dict[str, list[tuple[list[tuple[str, int]] | str, bool]]] = {
    "organizations": [("name", False)],
    "departments": [([("organization_id", 1), ("name", 1)], False)],
    "roles": [([("organization_id", 1), ("name", 1)], False)],
    "policies": [([("organization_id", 1), ("name", 1)], False)],
    "users": [([("organization_id", 1), ("name", 1)], False)],
    "agents": [([("organization_id", 1), ("name", 1)], False)],
    "devices": [([("organization_id", 1), ("name", 1)], False)],
    "repositories": [([("organization_id", 1), ("name", 1)], False)],
    "resources": [([("repository_id", 1), ("path", 1)], False)],
    "resource_locks": [
        ("resource_id", True),
        ("ticket_id", False),
        ("expires_at", False),
    ],
    "tickets": [
        ([("organization_id", 1), ("status", 1)], False),
        ([("priority", -1), ("created_at", 1)], False),
        ("assigned_actor_id", False),
    ],
    "ticket_dependencies": [([("ticket_id", 1), ("depends_on_ticket_id", 1)], True)],
    "executions": [("ticket_id", False)],
    "test_results": [("ticket_id", False)],
    "events": [([("organization_id", 1), ("timestamp", -1)], False)],
    "audit_log": [("timestamp", False)],
}


class MongoStorage(Storage):
    """Implementación MongoDB de la API de ``Storage``.

    Hereda de ``Storage`` solo por contrato de tipos; no se llama a
    ``Storage.__init__`` ni se usa ``self.conn``. Cada método público está
    sobrescrito.
    """

    def __init__(
        self,
        url: str,
        database: str = "",
        *,
        server_selection_timeout_ms: int = 10000,
    ):
        try:
            import pymongo
            from pymongo.uri_parser import parse_uri
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise NotImplementedError(
                "El backend mongodb requiere pymongo: instala geas[mongo] "
                "(o `pip install pymongo`). Sin driver no se cae a SQLite en "
                "silencio: se falla claro."
            ) from exc

        self.url = url
        self.backend = "mongo"
        self.db_path = url
        self.conn = None  # no hay conexión SQLite
        self._depth = 0
        self._session = None
        self._transactions = True

        self.client = pymongo.MongoClient(
            url,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
            appname="geas",
        )
        # Valida la conexión AHORA: un error de red debe doler al construir,
        # no en la primera escritura a medias.
        self.client.admin.command("ping")
        parsed = parse_uri(url)
        self.database = (
            database or os.environ.get("GEAS_MONGO_DB") or parsed.get("database")
        )
        if not self.database:
            self.database = "geas"
        self.db = self.client[self.database]
        self._create_indexes()

    # ── infraestructura ────────────────────────────────────────────────

    def _c(self, name: str):
        return self.db[name]

    def _kw(self) -> dict[str, Any]:
        return {"session": self._session} if self._session is not None else {}

    def _create_indexes(self) -> None:
        from pymongo import ASCENDING, DESCENDING
        from pymongo.errors import OperationFailure

        order = {1: ASCENDING, -1: DESCENDING}
        for name, specs in INDEXES.items():
            for keys, unique in specs:
                try:
                    if isinstance(keys, str):
                        keys = [(keys, 1)]
                    pairs = [(field, order[direction]) for field, direction in keys]
                    self._c(name).create_index(pairs, unique=unique)
                except OperationFailure:
                    # Un índice incompatible ya existente no puede tumbar el
                    # arranque del servidor: se sigue y se ve en getIndexes().
                    continue

    def close(self) -> None:
        self.client.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Storage]:
        """Transacción de sesión de Mongo; anidable como la de SQLite."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return

        from pymongo.errors import OperationFailure

        session = self.client.start_session()
        try:
            session.start_transaction()
        except (OperationFailure, NotImplementedError):
            # Un mongod suelto (sin réplica) no soporta transacciones. Es
            # preferible degradar a operaciones sueltas que impedir trabajar,
            # pero se avisa por atributo para que quien pregunte lo sepa.
            self._transactions = False
            session.end_session()
            self._depth = 1
            try:
                yield self
            finally:
                self._depth = 0
            return

        self._session = session
        self._depth = 1
        try:
            yield self
        except BaseException:
            self._depth = 0
            self._session = None
            with contextlib.suppress(Exception):
                session.abort_transaction()
            session.end_session()
            raise
        else:
            self._depth = 0
            self._session = None
            session.commit_transaction()
            session.end_session()

    # ── Organizations ──────────────────────────────────────────────────

    def create_organization(self, org: Organization) -> Organization:
        self._c("organizations").insert_one(_doc(org), **self._kw())
        return org

    def get_organization(self, org_id: str) -> Organization | None:
        d = self._c("organizations").find_one({"id": org_id}, **self._kw())
        return _org(d) if d else None

    def get_organization_by_name(self, name: str) -> Organization | None:
        d = self._c("organizations").find_one(
            {"name": name}, sort=[("created_at", 1)], **self._kw()
        )
        return _org(d) if d else None

    def list_organizations(self) -> list[Organization]:
        return [_org(d) for d in self._c("organizations").find({}, sort=[("name", 1)])]

    def set_organization_profile(self, org_id: str, profile: str) -> bool:
        res = self._c("organizations").update_one(
            {"id": org_id}, {"$set": {"profile": profile}}, **self._kw()
        )
        return res.matched_count > 0

    def update_organization_fields(
        self, org_id: str, *, description: str | None = None, profile: str | None = None
    ) -> bool:
        updates: dict[str, Any] = {}
        differs: list[dict[str, Any]] = []
        if description is not None:
            updates["description"] = description
            differs.append({"description": {"$ne": description}})
        if profile is not None:
            updates["profile"] = profile
            differs.append({"profile": {"$ne": profile}})
        if not updates:
            return False
        query: dict[str, Any] = {"id": org_id, "$or": differs}
        res = self._c("organizations").update_one(
            query, {"$set": updates}, **self._kw()
        )
        return res.matched_count > 0

    # ── Departments ────────────────────────────────────────────────────

    def create_department(self, dept: Department) -> Department:
        self._c("departments").insert_one(_doc(dept), **self._kw())
        return dept

    def get_department(self, dept_id: str) -> Department | None:
        d = self._c("departments").find_one({"id": dept_id}, **self._kw())
        return _dept(d) if d else None

    def get_department_by_name(
        self, org_id: str, name: str, parent_id: str | None = None
    ) -> Department | None:
        query = {
            "organization_id": org_id,
            "name": name,
            "parent_department_id": parent_id,
        }
        d = self._c("departments").find_one(
            query, sort=[("created_at", 1)], **self._kw()
        )
        return _dept(d) if d else None

    def list_departments(self, org_id: str) -> list[Department]:
        rows = self._c("departments").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_dept(d) for d in rows]

    def list_child_departments(self, parent_id: str) -> list[Department]:
        rows = self._c("departments").find(
            {"parent_department_id": parent_id}, sort=[("name", 1)], **self._kw()
        )
        return [_dept(d) for d in rows]

    # ── Roles ──────────────────────────────────────────────────────────

    def create_role(self, role: Role) -> Role:
        self._c("roles").insert_one(_doc(role), **self._kw())
        return role

    def get_role(self, role_id: str) -> Role | None:
        d = self._c("roles").find_one({"id": role_id}, **self._kw())
        return _role(d) if d else None

    def get_role_by_name(self, org_id: str, name: str) -> Role | None:
        d = self._c("roles").find_one(
            {"organization_id": org_id, "name": name}, **self._kw()
        )
        return _role(d) if d else None

    def update_role_permissions(self, role_id: str, permissions: list[str]) -> bool:
        res = self._c("roles").update_one(
            {"id": role_id}, {"$set": {"permissions": list(permissions)}}, **self._kw()
        )
        return res.matched_count > 0

    def list_roles(self, org_id: str) -> list[Role]:
        rows = self._c("roles").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_role(d) for d in rows]

    def add_permission_to_role(self, role_id: str, permission: str) -> bool:
        role = self.get_role(role_id)
        if role is None:
            return False
        self._c("roles").update_one(
            {"id": role_id}, {"$addToSet": {"permissions": permission}}, **self._kw()
        )
        return True

    # ── Policies ───────────────────────────────────────────────────────

    def create_policy(self, policy: Policy) -> Policy:
        self._c("policies").insert_one(_doc(policy), **self._kw())
        return policy

    def get_policy(self, policy_id: str) -> Policy | None:
        d = self._c("policies").find_one({"id": policy_id}, **self._kw())
        return _policy(d) if d else None

    def list_policies(self, org_id: str) -> list[Policy]:
        rows = self._c("policies").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_policy(d) for d in rows]

    def get_policy_by_name(
        self, org_id: str, name: str, department_id: str | None = None
    ) -> Policy | None:
        query = {
            "organization_id": org_id,
            "name": name,
            "department_id": department_id,
        }
        d = self._c("policies").find_one(query, sort=[("created_at", 1)], **self._kw())
        return _policy(d) if d else None

    def update_policy_fields(
        self,
        policy_id: str,
        *,
        description: str | None = None,
        permissions: list[str] | None = None,
    ) -> bool:
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description
        if permissions is not None:
            updates["permissions"] = list(permissions)
        if not updates:
            return False
        res = self._c("policies").update_one(
            {"id": policy_id}, {"$set": updates}, **self._kw()
        )
        return res.matched_count > 0

    # ── Users ──────────────────────────────────────────────────────────

    def create_user(self, user: User) -> User:
        self._c("users").insert_one(_doc(user), **self._kw())
        return user

    def get_user(self, user_id: str) -> User | None:
        d = self._c("users").find_one({"id": user_id}, **self._kw())
        return _user(d) if d else None

    def list_users(self, org_id: str) -> list[User]:
        rows = self._c("users").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_user(d) for d in rows]

    # ── Agents ─────────────────────────────────────────────────────────

    def create_agent(self, agent: Agent) -> Agent:
        self._c("agents").insert_one(_doc(agent), **self._kw())
        return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        d = self._c("agents").find_one({"id": agent_id}, **self._kw())
        return _agent(d) if d else None

    def list_agents(self, org_id: str) -> list[Agent]:
        rows = self._c("agents").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_agent(d) for d in rows]

    # ── Devices ────────────────────────────────────────────────────────

    def create_device(self, device: Device) -> Device:
        self._c("devices").insert_one(_doc(device), **self._kw())
        return device

    def get_device(self, device_id: str) -> Device | None:
        d = self._c("devices").find_one({"id": device_id}, **self._kw())
        return _device(d) if d else None

    def get_device_by_name(self, org_id: str, name: str) -> Device | None:
        d = self._c("devices").find_one(
            {"organization_id": org_id, "name": name},
            sort=[("created_at", 1)],
            **self._kw(),
        )
        return _device(d) if d else None

    def list_devices(self, org_id: str) -> list[Device]:
        rows = self._c("devices").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_device(d) for d in rows]

    def deactivate_device(self, device_id: str) -> bool:
        res = self._c("devices").update_one(
            {"id": device_id}, {"$set": {"active": False}}, **self._kw()
        )
        return res.matched_count > 0

    # ── Autorización ───────────────────────────────────────────────────

    def get_actor_permissions(self, actor_id: str, organization_id: str) -> set[str]:
        actor = self.get_user(actor_id)
        if actor is not None:
            if not actor.active or actor.organization_id != organization_id:
                return set()
            role_id = actor.role_id
        else:
            agent = self.get_agent(actor_id)
            if (
                agent is None
                or not agent.active
                or agent.organization_id != organization_id
            ):
                return set()
            role_id = agent.role_id
        role = self.get_role(role_id) if role_id else None
        return (
            set(role.permissions)
            if role and role.organization_id == organization_id
            else set()
        )

    def actor_has_permission(
        self, actor_id: str, organization_id: str, permission: str
    ) -> bool:
        return permission in self.get_actor_permissions(actor_id, organization_id)

    # ── Repositories ───────────────────────────────────────────────────

    def create_repository(self, repo: Repository) -> Repository:
        self._c("repositories").insert_one(_doc(repo), **self._kw())
        return repo

    def get_repository(self, repo_id: str) -> Repository | None:
        d = self._c("repositories").find_one({"id": repo_id}, **self._kw())
        return _repo(d) if d else None

    def update_repository_fields(
        self,
        repository_id: str,
        *,
        url: str | None = None,
        provider: str | None = None,
        default_branch: str | None = None,
        visibility: str | None = None,
    ) -> bool:
        updates: dict[str, Any] = {}
        if url is not None:
            updates["url"] = url
        if provider is not None:
            updates["provider"] = provider
        if default_branch is not None:
            updates["default_branch"] = default_branch
        if visibility is not None:
            updates["visibility"] = _value(visibility)
        if not updates:
            return False
        res = self._c("repositories").update_one(
            {"id": repository_id}, {"$set": updates}, **self._kw()
        )
        return res.matched_count > 0

    def get_repository_by_name(
        self, org_id: str, name: str, department_id: str | None = None
    ) -> Repository | None:
        query = {
            "organization_id": org_id,
            "name": name,
            "department_id": department_id,
        }
        d = self._c("repositories").find_one(query, sort=[("_id", 1)], **self._kw())
        return _repo(d) if d else None

    def list_repositories(self, org_id: str) -> list[Repository]:
        rows = self._c("repositories").find(
            {"organization_id": org_id}, sort=[("name", 1)], **self._kw()
        )
        return [_repo(d) for d in rows]

    # ── Resources ──────────────────────────────────────────────────────

    def create_resource(self, res: Resource) -> Resource:
        self._c("resources").insert_one(_doc(res), **self._kw())
        return res

    def get_resource(self, res_id: str) -> Resource | None:
        d = self._c("resources").find_one({"id": res_id}, **self._kw())
        return _resource(d) if d else None

    def list_resources(self, repo_id: str) -> list[Resource]:
        rows = self._c("resources").find(
            {"repository_id": repo_id}, sort=[("path", 1)], **self._kw()
        )
        return [_resource(d) for d in rows]

    # ── Locks ──────────────────────────────────────────────────────────

    def create_lock(self, lock: ResourceLock) -> ResourceLock:
        self._c("resource_locks").insert_one(_doc(lock), **self._kw())
        return lock

    def acquire_locks(
        self,
        resource_ids: list[str],
        ticket_id: str,
        actor_id: str,
        ttl_seconds: int = 7200,
    ) -> tuple[bool, str | None]:
        """Todos los locks o ninguno, bajo transacción y con índice único."""
        if self._depth:
            return self._acquire_locks(resource_ids, ticket_id, actor_id, ttl_seconds)
        with self.transaction():
            return self._acquire_locks(resource_ids, ticket_id, actor_id, ttl_seconds)

    def _acquire_locks(
        self,
        resource_ids: list[str],
        ticket_id: str,
        actor_id: str,
        ttl_seconds: int,
    ) -> tuple[bool, str | None]:
        from pymongo.errors import DuplicateKeyError

        now = datetime.now(UTC)
        now_value = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        unique_ids = list(dict.fromkeys(resource_ids))
        if not unique_ids:
            return True, None

        locks = self._c("resource_locks")
        locks.delete_many({"expires_at": {"$lte": now_value}}, **self._kw())
        blocked = locks.find_one(
            {
                "resource_id": {"$in": unique_ids},
                "ticket_id": {"$ne": ticket_id},
            },
            **self._kw(),
        )
        if blocked:
            return False, blocked["resource_id"]
        for resource_id in unique_ids:
            try:
                locks.update_one(
                    {"resource_id": resource_id, "ticket_id": ticket_id},
                    {
                        "$setOnInsert": {
                            "_id": str(uuid.uuid4()),
                            "id": str(uuid.uuid4()),
                            "resource_id": resource_id,
                            "ticket_id": ticket_id,
                            "actor_id": actor_id,
                            "created_at": now_value,
                            "expires_at": expires,
                            "last_heartbeat": now_value,
                        }
                    },
                    upsert=True,
                    **self._kw(),
                )
            except DuplicateKeyError:
                return False, resource_id
        return True, None

    def get_lock_for_resource(self, resource_id: str) -> ResourceLock | None:
        now = datetime.now(UTC).isoformat()
        locks = self._c("resource_locks")
        locks.delete_many({"expires_at": {"$lte": now}}, **self._kw())
        d = locks.find_one(
            {"resource_id": resource_id, "expires_at": {"$gt": now}},
            sort=[("created_at", -1)],
            **self._kw(),
        )
        return _lock(d) if d else None

    def release_lock(self, lock_id: str) -> bool:
        res = self._c("resource_locks").delete_one({"id": lock_id}, **self._kw())
        return res.deleted_count > 0

    def release_locks_for_ticket(self, ticket_id: str) -> int:
        res = self._c("resource_locks").delete_many(
            {"ticket_id": ticket_id}, **self._kw()
        )
        return res.deleted_count

    def heartbeat_lock(self, lock_id: str, ttl_seconds: int = 7200) -> bool:
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        res = self._c("resource_locks").update_one(
            {"id": lock_id, "expires_at": {"$gt": now}},
            {"$set": {"last_heartbeat": now, "expires_at": expires}},
            **self._kw(),
        )
        return res.matched_count > 0

    def expire_locks(self) -> int:
        res = self._c("resource_locks").delete_many(
            {"expires_at": {"$lte": datetime.now(UTC).isoformat()}}, **self._kw()
        )
        return res.deleted_count

    # ── Tickets ────────────────────────────────────────────────────────

    def create_ticket(self, ticket: Ticket) -> Ticket:
        self._c("tickets").insert_one(_doc(ticket), **self._kw())
        return ticket

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        d = self._c("tickets").find_one({"id": ticket_id}, **self._kw())
        return _ticket(d) if d else None

    def list_tickets(self, org_id: str, status: str | None = None) -> list[Ticket]:
        query: dict[str, Any] = {"organization_id": org_id}
        if status:
            query["status"] = status
        rows = self._c("tickets").find(
            query, sort=[("priority", -1), ("created_at", 1)], **self._kw()
        )
        return [_ticket(d) for d in rows]

    def update_ticket_status(self, ticket_id: str, status: str) -> bool:
        now = datetime.now(UTC).isoformat()
        if status == "IN_PROGRESS":
            pipeline = [
                {
                    "$set": {
                        "status": status,
                        "started_at": {"$ifNull": ["$started_at", now]},
                    }
                }
            ]
        elif status in ("DONE", "CANCELLED"):
            pipeline = [{"$set": {"status": status, "completed_at": now}}]
        else:
            pipeline = [{"$set": {"status": status}}]
        res = self._c("tickets").update_one({"id": ticket_id}, pipeline, **self._kw())
        return res.matched_count > 0

    def update_ticket_fields(self, ticket_id: str, **fields: object) -> bool:
        allowed = {"title", "description", "priority", "result", "feedback", "branch"}
        updates = {
            name: _value(value) for name, value in fields.items() if name in allowed
        }
        if not updates:
            return False
        res = self._c("tickets").update_one(
            {"id": ticket_id}, {"$set": updates}, **self._kw()
        )
        return res.matched_count > 0

    def assign_ticket(self, ticket_id: str, actor_id: str) -> bool:
        res = self._c("tickets").update_one(
            {"id": ticket_id, "status": "FREE"},
            {"$set": {"assigned_actor_id": actor_id, "status": "CLAIMED"}},
            **self._kw(),
        )
        return res.matched_count > 0

    def start_ticket(
        self,
        ticket_id: str,
        commit_before: str = "",
        branch: str = "",
        actor_id: str = "",
    ) -> bool:
        """Claim atómico: el filtro de estado ES la guardia (TOCTOU-safe)."""
        now = datetime.now(UTC).isoformat()
        res = self._c("tickets").update_one(
            {"id": ticket_id, "status": "FREE"},
            {
                "$set": {
                    "status": "IN_PROGRESS",
                    "started_at": now,
                    "commit_before": commit_before,
                    "branch": branch,
                    "assigned_actor_id": actor_id or "",
                }
            },
            **self._kw(),
        )
        return res.matched_count > 0

    def complete_ticket(
        self, ticket_id: str, commit_after: str = "", result: str = ""
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        res = self._c("tickets").update_one(
            {"id": ticket_id, "status": "IN_PROGRESS"},
            {
                "$set": {
                    "status": "DONE",
                    "completed_at": now,
                    "commit_after": commit_after,
                    "result": result,
                }
            },
            **self._kw(),
        )
        return res.matched_count > 0

    # ── Dependencias ───────────────────────────────────────────────────

    def create_dependency(self, dep: TicketDependency) -> TicketDependency:
        if dep.ticket_id == dep.depends_on_ticket_id or self.would_create_cycle(
            dep.ticket_id, dep.depends_on_ticket_id
        ):
            raise ValueError("DEPENDENCY_CYCLE")
        self._c("ticket_dependencies").insert_one(_doc(dep), **self._kw())
        return dep

    def would_create_cycle(self, ticket_id: str, depends_on_ticket_id: str) -> bool:
        pending = [depends_on_ticket_id]
        seen: set[str] = set()
        deps = self._c("ticket_dependencies")
        while pending:
            current = pending.pop()
            if current == ticket_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(
                d["depends_on_ticket_id"]
                for d in deps.find({"ticket_id": current}, {"depends_on_ticket_id": 1})
            )
        return False

    def get_dependencies(self, ticket_id: str) -> list[Ticket]:
        ids = [
            d["depends_on_ticket_id"]
            for d in self._c("ticket_dependencies").find({"ticket_id": ticket_id})
        ]
        rows = self._c("tickets").find(
            {"id": {"$in": ids}}, sort=[("created_at", 1)], **self._kw()
        )
        return [_ticket(d) for d in rows]

    def get_blocked_by(self, ticket_id: str) -> list[Ticket]:
        ids = [
            d["ticket_id"]
            for d in self._c("ticket_dependencies").find(
                {"depends_on_ticket_id": ticket_id}
            )
        ]
        rows = self._c("tickets").find(
            {"id": {"$in": ids}}, sort=[("created_at", 1)], **self._kw()
        )
        return [_ticket(d) for d in rows]

    def are_dependencies_resolved(self, ticket_id: str) -> bool:
        deps = list(self._c("ticket_dependencies").find({"ticket_id": ticket_id}))
        if not deps:
            return True
        ids = [d["depends_on_ticket_id"] for d in deps]
        pending = self._c("tickets").count_documents(
            {"id": {"$in": ids}, "status": {"$ne": "DONE"}}, **self._kw()
        )
        return pending == 0

    # ── Executions ─────────────────────────────────────────────────────

    def create_execution(self, exe: Execution) -> Execution:
        self._c("executions").insert_one(_doc(exe), **self._kw())
        return exe

    def get_executions(self, ticket_id: str) -> list[Execution]:
        rows = self._c("executions").find(
            {"ticket_id": ticket_id}, sort=[("started_at", 1)], **self._kw()
        )
        return [_execution(d) for d in rows]

    def update_execution_result(
        self, execution_id: str, result: str, finished_at: str | None = None
    ) -> bool:
        if finished_at is None:
            finished_at = datetime.now(UTC).isoformat()
        res = self._c("executions").update_one(
            {"id": execution_id},
            {"$set": {"result": result, "finished_at": finished_at}},
            **self._kw(),
        )
        return res.matched_count > 0

    def update_execution_usage(
        self,
        execution_id: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost: float = 0.0,
    ) -> bool:
        res = self._c("executions").update_one(
            {"id": execution_id},
            {
                "$set": {
                    "tokens_input": tokens_input,
                    "tokens_output": tokens_output,
                    "cost": cost,
                }
            },
            **self._kw(),
        )
        return res.matched_count > 0

    # ── TestResults ────────────────────────────────────────────────────

    def create_test_result(self, tr: TestResult) -> TestResult:
        self._c("test_results").insert_one(_doc(tr), **self._kw())
        return tr

    def get_test_results(self, ticket_id: str) -> list[TestResult]:
        rows = self._c("test_results").find(
            {"ticket_id": ticket_id}, sort=[("started_at", 1)], **self._kw()
        )
        return [_test_result(d) for d in rows]

    # ── Events / Audit ─────────────────────────────────────────────────

    def create_event(self, event: Event) -> Event:
        self._c("events").insert_one(_doc(event), **self._kw())
        return event

    def list_events(self, org_id: str, limit: int = 100) -> list[Event]:
        rows = self._c("events").find(
            {"organization_id": org_id},
            sort=[("timestamp", -1)],
            limit=limit,
            **self._kw(),
        )
        return [_event(d) for d in rows]

    def create_audit(self, audit: AuditLog) -> AuditLog:
        self._c("audit_log").insert_one(_doc(audit), **self._kw())
        return audit

    def list_audit(self, org_id: str, limit: int = 100) -> list[AuditLog]:
        # Igual que SQLite: audit_log no guarda organization_id.
        rows = self._c("audit_log").find({}, sort=[("timestamp", -1)], limit=limit)
        return [_audit(d) for d in rows]
