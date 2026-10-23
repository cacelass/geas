"""geas.storage_mongo - Persistencia en MongoDB.

Capa de almacenamiento para GEAS con MongoDB como backend.
API compatible con Storage (SQLite) - mismos metodos, mismos signatures.

Se activa con backend='mongodb' en Storage() o leyendo MONGO_URI del .env.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pymongo import MongoClient

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
    Role,
    TestResult,
    Ticket,
    TicketDependency,
    TicketStatus,
    User,
    Visibility,
    ResourceType,
)


# ─── Conversores ────────────────────────────────────────────────────────────

def _b(v: Any) -> bool:
    if v is None:
        return True
    return bool(v)

def _i(v: Any) -> int:
    if v is None:
        return 0
    return int(v)

def _f(v: Any) -> float:
    if v is None:
        return 0.0
    return float(v)

def _l(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return []

def _d(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return {}


# ─── Doc -> Model ───────────────────────────────────────────────────────────

def _org(d: dict) -> Organization:
    return Organization(id=d["id"], name=d.get("name",""), description=d.get("description",""),
        created_at=d.get("created_at",""), active=_b(d.get("active")),
        profile=d.get("profile","individual"))

def _dept(d: dict) -> Department:
    return Department(id=d["id"], organization_id=d.get("organization_id",""),
        parent_department_id=d.get("parent_department_id"), name=d.get("name",""),
        description=d.get("description",""), created_at=d.get("created_at",""),
        active=_b(d.get("active")))

def _role(d: dict) -> Role:
    return Role(id=d["id"], organization_id=d.get("organization_id",""),
        name=d.get("name",""), permissions=_l(d.get("permissions")))

def _policy(d: dict) -> Policy:
    return Policy(id=d["id"], organization_id=d.get("organization_id",""),
        department_id=d.get("department_id"), name=d.get("name",""),
        description=d.get("description",""), permissions=_l(d.get("permissions")),
        created_at=d.get("created_at",""), active=_b(d.get("active")))

def _user(d: dict) -> User:
    return User(id=d["id"], organization_id=d.get("organization_id",""),
        name=d.get("name",""), email=d.get("email",""),
        department_id=d.get("department_id"), role_id=d.get("role_id"),
        active=_b(d.get("active")), created_at=d.get("created_at",""))

def _agent(d: dict) -> Agent:
    return Agent(id=d["id"], organization_id=d.get("organization_id",""),
        name=d.get("name",""), provider=d.get("provider",""),
        model=d.get("model",""), model_version=d.get("model_version",""),
        department_id=d.get("department_id"), role_id=d.get("role_id"),
        harness_id=d.get("harness_id",""), active=_b(d.get("active")),
        configuration=_d(d.get("configuration")), created_at=d.get("created_at",""))

def _device(d: dict) -> Device:
    return Device(id=d["id"], organization_id=d.get("organization_id",""),
        name=d.get("name",""), active=_b(d.get("active")),
        created_at=d.get("created_at",""))

def _repo(d: dict) -> Repository:
    return Repository(id=d["id"], organization_id=d.get("organization_id",""),
        department_id=d.get("department_id"), name=d.get("name",""),
        provider=d.get("provider",""), url=d.get("url",""),
        default_branch=d.get("default_branch","main"),
        visibility=Visibility(d.get("visibility","PRIVATE")),
        active=_b(d.get("active")))

def _resource(d: dict) -> Resource:
    return Resource(id=d["id"], repository_id=d.get("repository_id"),
        path=d.get("path",""), type=ResourceType(d.get("type","FILE")),
        metadata=_d(d.get("metadata")))

def _lock(d: dict) -> ResourceLock:
    return ResourceLock(id=d["id"], resource_id=d.get("resource_id",""),
        ticket_id=d.get("ticket_id",""), actor_id=d.get("actor_id",""),
        created_at=d.get("created_at",""), expires_at=d.get("expires_at",""),
        last_heartbeat=d.get("last_heartbeat",""))

def _ticket(d: dict) -> Ticket:
    return Ticket(id=d["id"], title=d.get("title",""), description=d.get("description",""),
        organization_id=d.get("organization_id",""), department_id=d.get("department_id"),
        repository_id=d.get("repository_id"), creator_id=d.get("creator_id",""),
        assigned_actor_id=d.get("assigned_actor_id",""), created_at=d.get("created_at",""),
        started_at=d.get("started_at"), completed_at=d.get("completed_at"),
        status=TicketStatus(d.get("status","FREE")), priority=_i(d.get("priority")),
        files=_l(d.get("files")), resources=_l(d.get("resources")),
        dependencies=_l(d.get("dependencies")), blocked_tickets=_l(d.get("blocked_tickets")),
        result=d.get("result",""), feedback=d.get("feedback",""),
        branch=d.get("branch",""), commit_before=d.get("commit_before",""),
        commit_after=d.get("commit_after",""))

def _dependency(d: dict) -> TicketDependency:
    return TicketDependency(id=d["id"], ticket_id=d.get("ticket_id",""),
        depends_on_ticket_id=d.get("depends_on_ticket_id",""),
        created_at=d.get("created_at",""))

def _execution(d: dict) -> Execution:
    return Execution(id=d["id"], ticket_id=d.get("ticket_id",""),
        actor_id=d.get("actor_id",""), harness_id=d.get("harness_id",""),
        provider=d.get("provider",""), model=d.get("model",""),
        model_version=d.get("model_version",""), started_at=d.get("started_at",""),
        finished_at=d.get("finished_at"), tokens_input=_i(d.get("tokens_input")),
        tokens_output=_i(d.get("tokens_output")), cost=_f(d.get("cost")),
        tools_used=_l(d.get("tools_used")), iterations=_i(d.get("iterations")),
        result=d.get("result",""))

def _test_result(d: dict) -> TestResult:
    return TestResult(id=d["id"], ticket_id=d.get("ticket_id",""),
        commit_id=d.get("commit_id",""), pipeline_id=d.get("pipeline_id",""),
        status=d.get("status",""), started_at=d.get("started_at",""),
        finished_at=d.get("finished_at"), logs_reference=d.get("logs_reference",""))

def _event(d: dict) -> Event:
    return Event(id=d["id"], event_type=d.get("event_type",""),
        organization_id=d.get("organization_id",""), actor_id=d.get("actor_id",""),
        resource_type=d.get("resource_type",""), resource_id=d.get("resource_id",""),
        timestamp=d.get("timestamp",""), metadata=_d(d.get("metadata")))

def _audit(d: dict) -> AuditLog:
    return AuditLog(id=d["id"], actor_id=d.get("actor_id",""),
        action=d.get("action",""), resource_type=d.get("resource_type",""),
        resource_id=d.get("resource_id",""), timestamp=d.get("timestamp",""),
        metadata=_d(d.get("metadata")))


def _resolve_id(collection_name: str, short_id: str, db) -> str | None:
    """Si short_id tiene 8+ chars, busca coincidencia por prefijo. Devuelve el ID completo o None."""
    if len(short_id) >= 8:
        docs = list(db[collection_name].find({"id": {"$regex": f"^{short_id}"}}).limit(2))
        if len(docs) == 1:
            return docs[0]["id"]
    return None


# ─── Storage MongoDB ────────────────────────────────────────────────────────

class Storage:
    """Almacen MongoDB para Geas."""

    def __init__(self, db_uri: str = "", db_name: str = "geas"):
        if not db_uri:
            # .env esta un nivel arriba: /home/cancelas/geas/.env
            env_path = Path(__file__).parent.parent / ".env"
            if not env_path.exists():
                env_path = Path(__file__).parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("MONGODB_URI="):
                        db_uri = line.split("=", 1)[1].strip().strip("\"'")
                        break
        if not db_uri:
            raise ValueError("db_uri es requerido para MongoDB")
        self.client = MongoClient(db_uri, serverSelectionTimeoutMS=10000)
        self.db = self.client[db_name]
        self._depth = 0

    def close(self):
        self.client.close()

    @contextmanager
    def transaction(self) -> Iterator[Storage]:
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        try:
            with self.db.start_session() as session:
                with session.start_transaction():
                    yield self
        except Exception:
            yield self

    def _commit(self) -> None:
        pass  # MongoDB write individual es auto-commit

    # ── Helpers ───────────────────────────────────────────────────────

    def _resolve_org_id(self, short_id: str) -> str:
        """Resuelve un org_id corto (8+ chars) a su UUID completo."""
        resolved = _resolve_id("organizations", short_id, self.db)
        if resolved:
            return resolved
        return short_id  # fallback: usar el original

    def _resolve_id(self, short_id: str, collection_name: str = "") -> str:
        """Resuelve un ID corto a su valor completo si hay match unico."""
        if len(short_id) >= 8 and collection_name:
            resolved = _resolve_id(collection_name, short_id, self.db)
            if resolved:
                return resolved
        return short_id

    # ── Organizations ─────────────────────────────────────────────────

    def create_organization(self, org: Organization) -> Organization:
        self.db["organizations"].insert_one({
            "id": org.id, "name": org.name, "description": org.description,
            "created_at": org.created_at, "active": org.active, "profile": org.profile})
        return org

    def get_organization(self, org_id: str) -> Organization | None:
        d = self.db["organizations"].find_one({"id": org_id})
        return _org(d) if d else None

    def get_organization_by_name(self, name: str) -> Organization | None:
        d = self.db["organizations"].find_one({"name": name}, sort=[("created_at", 1)])
        return _org(d) if d else None

    def list_organizations(self) -> list[Organization]:
        docs = self.db["organizations"].find().sort("name", 1)
        return [_org(d) for d in docs]

    def set_organization_profile(self, org_id: str, profile: str) -> bool:
        r = self.db["organizations"].update_one({"id": org_id}, {"$set": {"profile": profile}})
        return r.modified_count > 0

    def update_organization_fields(self, org_id: str, *, description: str | None = None,
                                   profile: str | None = None) -> bool:
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description
        if profile is not None:
            updates["profile"] = profile
        if not updates:
            return False
        cur = self.db["organizations"].find_one({"id": org_id})
        if cur is None:
            return False
        changed = False
        if description is not None and cur.get("description") != description:
            changed = True
        if profile is not None and cur.get("profile") != profile:
            changed = True
        if changed:
            self.db["organizations"].update_one({"id": org_id}, {"$set": updates})
            return True
        return False

    # ── Departments ───────────────────────────────────────────────────

    def create_department(self, dept: Department) -> Department:
        self.db["departments"].insert_one({
            "id": dept.id, "organization_id": dept.organization_id,
            "parent_department_id": dept.parent_department_id,
            "name": dept.name, "description": dept.description,
            "created_at": dept.created_at, "active": dept.active})
        return dept

    def get_department(self, dept_id: str) -> Department | None:
        d = self.db["departments"].find_one({"id": dept_id})
        return _dept(d) if d else None

    def get_department_by_name(self, org_id: str, name: str, parent_id: str | None = None) -> Department | None:
        q = {"organization_id": org_id, "name": name}
        if parent_id:
            q["parent_department_id"] = parent_id
        else:
            q["parent_department_id"] = {"$exists": False}
        d = self.db["departments"].find_one(q, sort=[("created_at", 1)])
        return _dept(d) if d else None

    def list_departments(self, org_id: str) -> list[Department]:
        org_id = self._resolve_org_id(org_id)
        docs = self.db["departments"].find({"organization_id": org_id}).sort("name", 1)
        return [_dept(d) for d in docs]

    def list_child_departments(self, parent_id: str) -> list[Department]:
        docs = self.db["departments"].find({"parent_department_id": parent_id}).sort("name", 1)
        return [_dept(d) for d in docs]

    # ── Roles ─────────────────────────────────────────────────────────

    def create_role(self, role: Role) -> Role:
        self.db["roles"].insert_one({
            "id": role.id, "organization_id": role.organization_id,
            "name": role.name, "permissions": role.permissions})
        return role

    def get_role(self, role_id: str) -> Role | None:
        d = self.db["roles"].find_one({"id": role_id})
        return _role(d) if d else None

    def get_role_by_name(self, org_id: str, name: str) -> Role | None:
        d = self.db["roles"].find_one({"organization_id": org_id, "name": name})
        return _role(d) if d else None

    def update_role_permissions(self, role_id: str, permissions: list[str]) -> bool:
        r = self.db["roles"].update_one({"id": role_id}, {"$set": {"permissions": permissions}})
        return r.modified_count > 0

    def list_roles(self, org_id: str) -> list[Role]:
        org_id = self._resolve_org_id(org_id)
        docs = self.db["roles"].find({"organization_id": org_id}).sort("name", 1)
        return [_role(d) for d in docs]

    def add_permission_to_role(self, role_id: str, permission: str) -> bool:
        role = self.get_role(role_id)
        if role is None or permission in role.permissions:
            return False
        role.permissions.append(permission)
        self.db["roles"].update_one({"id": role_id}, {"$set": {"permissions": role.permissions}})
        return True

    # ── Policies ──────────────────────────────────────────────────────

    def create_policy(self, policy: Policy) -> Policy:
        self.db["policies"].insert_one({
            "id": policy.id, "organization_id": policy.organization_id,
            "department_id": policy.department_id, "name": policy.name,
            "description": policy.description, "permissions": policy.permissions,
            "created_at": policy.created_at, "active": policy.active})
        return policy

    def get_policy(self, policy_id: str) -> Policy | None:
        d = self.db["policies"].find_one({"id": policy_id})
        return _policy(d) if d else None

    def list_policies(self, org_id: str) -> list[Policy]:
        docs = self.db["policies"].find({"organization_id": org_id}).sort("name", 1)
        return [_policy(d) for d in docs]

    def get_policy_by_name(self, org_id: str, name: str, department_id: str | None = None) -> Policy | None:
        q = {"organization_id": org_id, "name": name}
        if department_id:
            q["department_id"] = department_id
        else:
            q["department_id"] = {"$exists": False}
        d = self.db["policies"].find_one(q, sort=[("created_at", 1)])
        return _policy(d) if d else None

    def update_policy_fields(self, policy_id: str, *, description: str | None = None,
                             permissions: list[str] | None = None) -> bool:
        updates: dict[str, Any] = {}
        if description is not None:
            updates["description"] = description
        if permissions is not None:
            updates["permissions"] = permissions
        if not updates:
            return False
        r = self.db["policies"].update_one({"id": policy_id}, {"$set": updates})
        return r.modified_count > 0

    # ── Users ─────────────────────────────────────────────────────────

    def create_user(self, user: User) -> User:
        self.db["users"].insert_one({
            "id": user.id, "organization_id": user.organization_id,
            "name": user.name, "email": user.email,
            "department_id": user.department_id, "role_id": user.role_id,
            "active": user.active, "created_at": user.created_at})
        return user

    def get_user(self, user_id: str) -> User | None:
        d = self.db["users"].find_one({"id": user_id})
        return _user(d) if d else None

    def list_users(self, org_id: str) -> list[User]:
        org_id = self._resolve_org_id(org_id)
        docs = self.db["users"].find({"organization_id": org_id}).sort("name", 1)
        return [_user(d) for d in docs]

    # ── Agents ────────────────────────────────────────────────────────

    def create_agent(self, agent: Agent) -> Agent:
        self.db["agents"].insert_one({
            "id": agent.id, "organization_id": agent.organization_id,
            "name": agent.name, "provider": agent.provider, "model": agent.model,
            "model_version": agent.model_version, "department_id": agent.department_id,
            "role_id": agent.role_id, "harness_id": agent.harness_id,
            "active": agent.active, "configuration": agent.configuration,
            "created_at": agent.created_at})
        return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        d = self.db["agents"].find_one({"id": agent_id})
        return _agent(d) if d else None

    def list_agents(self, org_id: str) -> list[Agent]:
        org_id = self._resolve_org_id(org_id)
        docs = self.db["agents"].find({"organization_id": org_id}).sort("name", 1)
        return [_agent(d) for d in docs]

    # ── Devices ───────────────────────────────────────────────────────

    def create_device(self, device: Device) -> Device:
        self.db["devices"].insert_one({
            "id": device.id, "organization_id": device.organization_id,
            "name": device.name, "active": device.active, "created_at": device.created_at})
        return device

    def get_device(self, device_id: str) -> Device | None:
        d = self.db["devices"].find_one({"id": device_id})
        return _device(d) if d else None

    def get_device_by_name(self, org_id: str, name: str) -> Device | None:
        d = self.db["devices"].find_one({"organization_id": org_id, "name": name}, sort=[("created_at", 1)])
        return _device(d) if d else None

    def list_devices(self, org_id: str) -> list[Device]:
        docs = self.db["devices"].find({"organization_id": org_id}).sort("name", 1)
        return [_device(d) for d in docs]

    def deactivate_device(self, device_id: str) -> bool:
        r = self.db["devices"].update_one({"id": device_id}, {"$set": {"active": False}})
        return r.modified_count > 0

    # ── Autorizacion ──────────────────────────────────────────────────

    def get_actor_permissions(self, actor_id: str, organization_id: str) -> set[str]:
        actor = self.get_user(actor_id)
        if actor is not None:
            if not actor.active or actor.organization_id != organization_id:
                return set()
            role_id = actor.role_id
        else:
            agent = self.get_agent(actor_id)
            if agent is None or not agent.active or agent.organization_id != organization_id:
                return set()
            role_id = agent.role_id
        role = self.get_role(role_id) if role_id else None
        return set(role.permissions) if role and role.organization_id == organization_id else set()

    def actor_has_permission(self, actor_id: str, organization_id: str, permission: str) -> bool:
        return permission in self.get_actor_permissions(actor_id, organization_id)

    # ── Repositories ──────────────────────────────────────────────────

    def create_repository(self, repo: Repository) -> Repository:
        self.db["repositories"].insert_one({
            "id": repo.id, "organization_id": repo.organization_id,
            "department_id": repo.department_id, "name": repo.name,
            "provider": repo.provider, "url": repo.url,
            "default_branch": repo.default_branch,
            "visibility": repo.visibility.value, "active": repo.active})
        return repo

    def get_repository(self, repo_id: str) -> Repository | None:
        d = self.db["repositories"].find_one({"id": repo_id})
        return _repo(d) if d else None

    def update_repository_fields(self, repository_id: str, *, url: str | None = None,
                                 provider: str | None = None, default_branch: str | None = None,
                                 visibility: str | None = None) -> bool:
        updates: dict[str, Any] = {}
        if url is not None:
            updates["url"] = url
        if provider is not None:
            updates["provider"] = provider
        if default_branch is not None:
            updates["default_branch"] = default_branch
        if visibility is not None:
            updates["visibility"] = visibility
        if not updates:
            return False
        r = self.db["repositories"].update_one({"id": repository_id}, {"$set": updates})
        return r.modified_count > 0

    def get_repository_by_name(self, org_id: str, name: str, department_id: str | None = None) -> Repository | None:
        q = {"organization_id": org_id, "name": name}
        if department_id:
            q["department_id"] = department_id
        else:
            q["department_id"] = {"$exists": False}
        d = self.db["repositories"].find_one(q, sort=[("created_at", 1)])
        return _repo(d) if d else None

    def list_repositories(self, org_id: str) -> list[Repository]:
        org_id = self._resolve_org_id(org_id)
        docs = self.db["repositories"].find({"organization_id": org_id}).sort("name", 1)
        return [_repo(d) for d in docs]

    # ── Resources ─────────────────────────────────────────────────────

    def create_resource(self, res: Resource) -> Resource:
        self.db["resources"].insert_one({
            "id": res.id, "repository_id": res.repository_id,
            "path": res.path, "type": res.type.value, "metadata": res.metadata})
        return res

    def get_resource(self, res_id: str) -> Resource | None:
        d = self.db["resources"].find_one({"id": res_id})
        return _resource(d) if d else None

    def list_resources(self, repo_id: str) -> list[Resource]:
        docs = self.db["resources"].find({"repository_id": repo_id}).sort("path", 1)
        return [_resource(d) for d in docs]

    # ── ResourceLocks ─────────────────────────────────────────────────

    def create_lock(self, lock: ResourceLock) -> ResourceLock:
        self.db["resource_locks"].insert_one({
            "id": lock.id, "resource_id": lock.resource_id, "ticket_id": lock.ticket_id,
            "actor_id": lock.actor_id, "created_at": lock.created_at,
            "expires_at": lock.expires_at, "last_heartbeat": lock.last_heartbeat})
        return lock

    def acquire_locks(self, resource_ids: list[str], ticket_id: str, actor_id: str,
                      ttl_seconds: int = 7200) -> tuple[bool, str | None]:
        now = datetime.now(UTC)
        now_value = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        unique_ids = list(dict.fromkeys(resource_ids))
        if not unique_ids:
            return True, None

        self.db["resource_locks"].delete_many({"expires_at": {"$lte": now_value}})

        for rid in unique_ids:
            existing = self.db["resource_locks"].find_one({
                "resource_id": rid, "ticket_id": {"$ne": ticket_id}, "expires_at": {"$gt": now_value}})
            if existing:
                return False, existing["resource_id"]

        for rid in unique_ids:
            existing = self.db["resource_locks"].find_one({"resource_id": rid, "ticket_id": ticket_id})
            if existing is None:
                lid = str(uuid.uuid4())
                self.db["resource_locks"].insert_one({
                    "id": lid, "resource_id": rid, "ticket_id": ticket_id,
                    "actor_id": actor_id, "created_at": now_value,
                    "expires_at": expires, "last_heartbeat": now_value})
        return True, None

    def get_lock_for_resource(self, resource_id: str) -> ResourceLock | None:
        now = datetime.now(UTC).isoformat()
        self.db["resource_locks"].delete_many({"expires_at": {"$lte": now}})
        d = self.db["resource_locks"].find_one({
            "resource_id": resource_id, "expires_at": {"$gt": now}}, sort=[("created_at", -1)])
        return _lock(d) if d else None

    def release_lock(self, lock_id: str) -> bool:
        r = self.db["resource_locks"].delete_one({"id": lock_id})
        return r.deleted_count > 0

    def release_locks_for_ticket(self, ticket_id: str) -> int:
        r = self.db["resource_locks"].delete_many({"ticket_id": ticket_id})
        return r.deleted_count

    def heartbeat_lock(self, lock_id: str, ttl_seconds: int = 7200) -> bool:
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        r = self.db["resource_locks"].update_one({
            "id": lock_id, "expires_at": {"$gt": now}}, {"$set": {"last_heartbeat": now, "expires_at": expires}})
        return r.modified_count > 0

    def expire_locks(self) -> int:
        r = self.db["resource_locks"].delete_many({"expires_at": {"$lte": datetime.now(UTC).isoformat()}})
        return r.deleted_count

    # ── Tickets ───────────────────────────────────────────────────────

    def create_ticket(self, ticket: Ticket) -> Ticket:
        self.db["tickets"].insert_one({
            "id": ticket.id, "title": ticket.title, "description": ticket.description,
            "organization_id": ticket.organization_id, "department_id": ticket.department_id,
            "repository_id": ticket.repository_id, "creator_id": ticket.creator_id,
            "assigned_actor_id": ticket.assigned_actor_id, "created_at": ticket.created_at,
            "started_at": ticket.started_at, "completed_at": ticket.completed_at,
            "status": ticket.status.value, "priority": ticket.priority,
            "files": ticket.files, "resources": ticket.resources,
            "dependencies": ticket.dependencies, "blocked_tickets": ticket.blocked_tickets,
            "result": ticket.result, "feedback": ticket.feedback,
            "branch": ticket.branch, "commit_before": ticket.commit_before,
            "commit_after": ticket.commit_after})
        return ticket

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        d = self.db["tickets"].find_one({"id": ticket_id})
        return _ticket(d) if d else None

    def list_tickets(self, org_id: str, status: str | None = None) -> list[Ticket]:
        org_id = self._resolve_org_id(org_id)
        q = {"organization_id": org_id}
        if status:
            q["status"] = status
        docs = self.db["tickets"].find(q).sort([("priority", -1), ("created_at", 1)])
        return [_ticket(d) for d in docs]

    def update_ticket_status(self, ticket_id: str, status: str) -> bool:
        now = datetime.now(UTC).isoformat()
        if status == "IN_PROGRESS":
            r = self.db["tickets"].update_one({"id": ticket_id}, {
                "$set": {"status": status}, "$setOnInsert": {"started_at": now}})
        elif status in ("DONE", "CANCELLED"):
            r = self.db["tickets"].update_one({"id": ticket_id}, {
                "$set": {"status": status, "completed_at": now}})
        else:
            r = self.db["tickets"].update_one({"id": ticket_id}, {"$set": {"status": status}})
        return r.modified_count > 0

    def update_ticket_fields(self, ticket_id: str, **fields: object) -> bool:
        allowed = {"title", "description", "priority", "result", "feedback", "branch"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        r = self.db["tickets"].update_one({"id": ticket_id}, {"$set": updates})
        return r.modified_count > 0

    def assign_ticket(self, ticket_id: str, actor_id: str) -> bool:
        r = self.db["tickets"].update_one({"id": ticket_id, "status": "FREE"}, {
            "$set": {"assigned_actor_id": actor_id, "status": "CLAIMED"}})
        return r.modified_count > 0

    def start_ticket(self, ticket_id: str, commit_before: str = "", branch: str = "",
                     actor_id: str = "") -> bool:
        now = datetime.now(UTC).isoformat()
        r = self.db["tickets"].update_one({"id": ticket_id, "status": "FREE"}, {
            "$set": {"status": "IN_PROGRESS", "started_at": now,
                     "commit_before": commit_before, "branch": branch,
                     "assigned_actor_id": actor_id or ""}})
        return r.modified_count > 0

    def complete_ticket(self, ticket_id: str, commit_after: str = "", result: str = "") -> bool:
        now = datetime.now(UTC).isoformat()
        r = self.db["tickets"].update_one({"id": ticket_id}, {
            "$set": {"status": "DONE", "completed_at": now,
                     "commit_after": commit_after, "result": result}})
        return r.modified_count > 0

    # ── TicketDependencies ────────────────────────────────────────────

    def create_dependency(self, dep: TicketDependency) -> TicketDependency:
        if dep.ticket_id == dep.depends_on_ticket_id or self.would_create_cycle(
                dep.ticket_id, dep.depends_on_ticket_id):
            raise ValueError("DEPENDENCY_CYCLE")
        self.db["ticket_dependencies"].insert_one({
            "id": dep.id, "ticket_id": dep.ticket_id,
            "depends_on_ticket_id": dep.depends_on_ticket_id,
            "created_at": dep.created_at})
        return dep

    def would_create_cycle(self, ticket_id: str, depends_on_ticket_id: str) -> bool:
        pending = [depends_on_ticket_id]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == ticket_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            rows = self.db["ticket_dependencies"].find({
                "ticket_id": current}).project({"depends_on_ticket_id": 1, "_id": 0})
            pending.extend(r["depends_on_ticket_id"] for r in rows)
        return False

    def get_dependencies(self, ticket_id: str) -> list[Ticket]:
        pipeline = [
            {"$match": {"ticket_id": ticket_id}},
            {"$lookup": {"from": "tickets", "localField": "depends_on_ticket_id",
                         "foreignField": "id", "as": "ticket"}},
            {"$unwind": "$ticket"},
            {"$replaceRoot": {"newRoot": "$ticket"}},
            {"$sort": {"created_at": 1}},
        ]
        docs = self.db["ticket_dependencies"].aggregate(pipeline)
        return [_ticket(d) for d in docs]

    def get_blocked_by(self, ticket_id: str) -> list[Ticket]:
        pipeline = [
            {"$match": {"depends_on_ticket_id": ticket_id}},
            {"$lookup": {"from": "tickets", "localField": "ticket_id",
                         "foreignField": "id", "as": "ticket"}},
            {"$unwind": "$ticket"},
            {"$replaceRoot": {"newRoot": "$ticket"}},
            {"$sort": {"created_at": 1}},
        ]
        docs = self.db["ticket_dependencies"].aggregate(pipeline)
        return [_ticket(d) for d in docs]

    def are_dependencies_resolved(self, ticket_id: str) -> bool:
        count = self.db["ticket_dependencies"].aggregate([
            {"$match": {"ticket_id": ticket_id}},
            {"$lookup": {"from": "tickets", "localField": "depends_on_ticket_id",
                         "foreignField": "id", "as": "ticket"}},
            {"$unwind": "$ticket"},
            {"$match": {"ticket.status": {"$ne": "DONE"}}},
            {"$count": "unresolved"},
        ])
        result = list(count)
        return result[0]["unresolved"] == 0 if result else True

    # ── Executions ────────────────────────────────────────────────────

    def create_execution(self, exe: Execution) -> Execution:
        self.db["executions"].insert_one({
            "id": exe.id, "ticket_id": exe.ticket_id, "actor_id": exe.actor_id,
            "harness_id": exe.harness_id, "provider": exe.provider, "model": exe.model,
            "model_version": exe.model_version, "started_at": exe.started_at,
            "finished_at": exe.finished_at, "tokens_input": exe.tokens_input,
            "tokens_output": exe.tokens_output, "cost": exe.cost,
            "tools_used": exe.tools_used, "iterations": exe.iterations,
            "result": exe.result})
        return exe

    def get_executions(self, ticket_id: str) -> list[Execution]:
        docs = self.db["executions"].find({"ticket_id": ticket_id}).sort("started_at", 1)
        return [_execution(d) for d in docs]

    def update_execution_result(self, execution_id: str, result: str, finished_at: str | None = None) -> bool:
        if finished_at is None:
            finished_at = datetime.now(UTC).isoformat()
        r = self.db["executions"].update_one({"id": execution_id}, {
            "$set": {"result": result, "finished_at": finished_at}})
        return r.modified_count > 0

    def update_execution_usage(self, execution_id: str, tokens_input: int = 0,
                               tokens_output: int = 0, cost: float = 0.0) -> bool:
        r = self.db["executions"].update_one({"id": execution_id}, {
            "$set": {"tokens_input": tokens_input, "tokens_output": tokens_output, "cost": cost}})
        return r.modified_count > 0

    # ── TestResults ───────────────────────────────────────────────────

    def create_test_result(self, tr: TestResult) -> TestResult:
        self.db["test_results"].insert_one({
            "id": tr.id, "ticket_id": tr.ticket_id, "commit_id": tr.commit_id,
            "pipeline_id": tr.pipeline_id, "status": tr.status,
            "started_at": tr.started_at, "finished_at": tr.finished_at,
            "logs_reference": tr.logs_reference})
        return tr

    def get_test_results(self, ticket_id: str) -> list[TestResult]:
        docs = self.db["test_results"].find({"ticket_id": ticket_id}).sort("started_at", 1)
        return [_test_result(d) for d in docs]

    # ── Events ────────────────────────────────────────────────────────

    def create_event(self, event: Event) -> Event:
        self.db["events"].insert_one({
            "id": event.id, "event_type": event.event_type,
            "organization_id": event.organization_id, "actor_id": event.actor_id,
            "resource_type": event.resource_type, "resource_id": event.resource_id,
            "timestamp": event.timestamp, "metadata": event.metadata})
        return event

    def list_events(self, org_id: str, limit: int = 100) -> list[Event]:
        docs = self.db["events"].find({"organization_id": org_id}).sort("timestamp", -1).limit(limit)
        return [_event(d) for d in docs]

    # ── AuditLog ──────────────────────────────────────────────────────

    def create_audit(self, audit: AuditLog) -> AuditLog:
        self.db["audit_log"].insert_one({
            "id": audit.id, "actor_id": audit.actor_id, "action": audit.action,
            "resource_type": audit.resource_type, "resource_id": audit.resource_id,
            "timestamp": audit.timestamp, "metadata": audit.metadata})
        return audit

    def list_audit(self, org_id: str, limit: int = 100) -> list[AuditLog]:
        # audit_log no tiene organization_id, devolver todos los últimos
        docs = self.db["audit_log"].find().sort("timestamp", -1).limit(limit)
        return [_audit(d) for d in docs]
