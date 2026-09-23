"""
geas.storage — Persistencia en SQLite.

Capa de almacenamiento para el MVP. Cada modelo se guarda como tabla.
Operaciones CRUD básicas + búsquedas por campos clave.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geas.models import (
    Agent,
    AuditLog,
    Department,
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
    User,
)


class Storage:
    """Almacén SQLite para Geas."""

    def __init__(self, db_path: str | Path = "geas.db", backend: str = "sqlite"):
        self.db_path = Path(db_path)
        # §42: backend declarado por el perfil (§1046 — los agentes
        # no acceden directamente a PostgreSQL). SQLite es el
        # default retrocompatible; pedir postgres sin driver
        # falla claro en vez de caer en silencio a SQLite.
        self.backend = backend
        if backend == "postgres":
            raise NotImplementedError(
                "§42: backend postgres requiere driver psycopg + "
                "instancia PostgreSQL (deuda de infra, igual que copier "
                "§35/§36). Añade geas[postgres] a tus deps e inyecta la "
                "conexión para cablearlo."
            )
        # El servidor HTTP atiende en un hilo distinto al de inicialización.
        # La API usa HTTPServer (un solo consumidor), y SQLite sigue
        # serializando escrituras mediante BEGIN IMMEDIATE para los locks.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def close(self):
        self.conn.close()

    def _create_tables(self):
        self.conn.executescript(SCHEMA)
        # Las primeras bases SQLite no tenían rol para los agentes. Mantener la
        # migración aquí evita que una actualización deje instancias existentes
        # sin RBAC.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(agents)")}
        if "role_id" not in columns:
            self.conn.execute("ALTER TABLE agents ADD COLUMN role_id TEXT")
        # §43: perfil de despliegue (individual|team|enterprise). Las bases
        # creadas antes de los perfiles no tienen la columna; sin migración,
        # una actualización perdería el perfil de la organización.
        org_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(organizations)")}
        if "profile" not in org_columns:
            self.conn.execute(
                "ALTER TABLE organizations ADD COLUMN profile TEXT DEFAULT 'individual'"
            )
        self.conn.commit()

    # ─── Organizations ──────────────────────────────────────────────────

    def create_organization(self, org: Organization) -> Organization:
        self.conn.execute(
            "INSERT INTO organizations (id, name, description, created_at, active, "
            "profile) VALUES (?, ?, ?, ?, ?, ?)",
            (org.id, org.name, org.description, org.created_at, org.active, org.profile),
        )
        self.conn.commit()
        return org

    def get_organization(self, org_id: str) -> Organization | None:
        row = self.conn.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
        return _row_to_org(row) if row else None

    def get_organization_by_name(self, name: str) -> Organization | None:
        """§43: lookup para `geas sync` — idempotente por nombre."""
        row = self.conn.execute(
            "SELECT * FROM organizations WHERE name = ? "
            "ORDER BY created_at LIMIT 1",
            (name,),
        ).fetchone()
        return _row_to_org(row) if row else None

    def list_organizations(self) -> list[Organization]:
        rows = self.conn.execute("SELECT * FROM organizations ORDER BY name").fetchall()
        return [_row_to_org(r) for r in rows]

    def set_organization_profile(self, org_id: str, profile: str) -> bool:
        """§43: cambia el perfil de despliegue de una organización."""
        cur = self.conn.execute(
            "UPDATE organizations SET profile = ? WHERE id = ?", (profile, org_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_organization_fields(
        self, org_id: str, *, description: str | None = None, profile: str | None = None
    ) -> bool:
        """§43: convergencia de `geas sync` — description y profile declarativos.

        Devuelve True SOLO si hubo un cambio real: el UPDATE exige que al
        menos un campo difiera del valor actual, así el segundo `geas sync`
        no cuenta una convergencia que ya ocurrió (idempotencia §43)."""
        sets: list[str] = []
        set_params: list[object] = []
        where: list[str] = []
        where_params: list[object] = []
        if description is not None:
            sets.append("description = ?")
            set_params.append(description)
            where.append("description != ?")
            where_params.append(description)
        if profile is not None:
            sets.append("profile = ?")
            set_params.append(profile)
            where.append("profile != ?")
            where_params.append(profile)
        if not sets:
            return False
        sql = f"UPDATE organizations SET {', '.join(sets)} WHERE id = ?"
        if where:
            sql += " AND (" + " OR ".join(where) + ")"
        cur = self.conn.execute(sql, [*set_params, org_id, *where_params])
        self.conn.commit()
        return cur.rowcount > 0

    # ─── Departments ────────────────────────────────────────────────────

    def create_department(self, dept: Department) -> Department:
        self.conn.execute(
            "INSERT INTO departments (id, organization_id, parent_department_id, "
            "name, description, created_at, active) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dept.id,
                dept.organization_id,
                dept.parent_department_id or None,
                dept.name,
                dept.description,
                dept.created_at,
                dept.active,
            ),
        )
        self.conn.commit()
        return dept

    def get_department(self, dept_id: str) -> Department | None:
        row = self.conn.execute(
            "SELECT * FROM departments WHERE id = ?", (dept_id,)
        ).fetchone()
        return _row_to_dept(row) if row else None

    def get_department_by_name(
        self, org_id: str, name: str, parent_id: str | None = None
    ) -> Department | None:
        """§43: lookup para `geas sync` — idempotente por nombre y padre."""
        if parent_id:
            row = self.conn.execute(
                "SELECT * FROM departments WHERE organization_id = ? AND name = ? "
                "AND parent_department_id = ? ORDER BY created_at LIMIT 1",
                (org_id, name, parent_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM departments WHERE organization_id = ? AND name = ? "
                "AND parent_department_id IS NULL ORDER BY created_at LIMIT 1",
                (org_id, name),
            ).fetchone()
        return _row_to_dept(row) if row else None

    def list_departments(self, org_id: str) -> list[Department]:
        rows = self.conn.execute(
            "SELECT * FROM departments WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_dept(r) for r in rows]

    def list_child_departments(self, parent_id: str) -> list[Department]:
        rows = self.conn.execute(
            "SELECT * FROM departments WHERE parent_department_id = ? ORDER BY name",
            (parent_id,),
        ).fetchall()
        return [_row_to_dept(r) for r in rows]

    # ─── Roles ──────────────────────────────────────────────────────────

    def create_role(self, role: Role) -> Role:
        self.conn.execute(
            "INSERT INTO roles (id, organization_id, name, permissions) "
            "VALUES (?, ?, ?, ?)",
            (role.id, role.organization_id, role.name, json.dumps(role.permissions)),
        )
        self.conn.commit()
        return role

    def get_role(self, role_id: str) -> Role | None:
        row = self.conn.execute(
            "SELECT * FROM roles WHERE id = ?", (role_id,)
        ).fetchone()
        return _row_to_role(row) if row else None

    def get_role_by_name(self, org_id: str, name: str) -> Role | None:
        """§43: lookup para `geas sync` — idempotente por nombre."""
        row = self.conn.execute(
            "SELECT * FROM roles WHERE organization_id = ? AND name = ?",
            (org_id, name),
        ).fetchone()
        return _row_to_role(row) if row else None

    def update_role_permissions(self, role_id: str, permissions: list[str]) -> bool:
        """§43: convergencia de `geas sync` — los permisos declarativos mandan."""
        cur = self.conn.execute(
            "UPDATE roles SET permissions = ? WHERE id = ?",
            (json.dumps(permissions), role_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_roles(self, org_id: str) -> list[Role]:
        rows = self.conn.execute(
            "SELECT * FROM roles WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_role(r) for r in rows]

    def add_permission_to_role(self, role_id: str, permission: str) -> bool:
        """§7: añade un permiso a un rol existente (idempotente)."""
        role = self.get_role(role_id)
        if role is None:
            return False
        if permission not in role.permissions:
            role.permissions.append(permission)
            self.conn.execute(
                "UPDATE roles SET permissions = ? WHERE id = ?",
                (json.dumps(role.permissions), role_id),
            )
            self.conn.commit()
        return True

    # ─── Policies ───────────────────────────────────────────────────────

    def create_policy(self, policy: Policy) -> Policy:
        self.conn.execute(
            "INSERT INTO policies (id, organization_id, department_id, name, "
            "description, permissions, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                policy.id,
                policy.organization_id,
                policy.department_id or None,
                policy.name,
                policy.description,
                json.dumps(policy.permissions),
                policy.created_at,
                policy.active,
            ),
        )
        self.conn.commit()
        return policy

    def get_policy(self, policy_id: str) -> Policy | None:
        row = self.conn.execute(
            "SELECT * FROM policies WHERE id = ?", (policy_id,)
        ).fetchone()
        return _row_to_policy(row) if row else None

    def list_policies(self, org_id: str) -> list[Policy]:
        rows = self.conn.execute(
            "SELECT * FROM policies WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_policy(r) for r in rows]

    def get_policy_by_name(
        self, org_id: str, name: str, department_id: str | None = None
    ) -> Policy | None:
        """§43: lookup para `geas sync` — idempotente por nombre."""
        if department_id:
            row = self.conn.execute(
                "SELECT * FROM policies WHERE organization_id = ? AND name = ? "
                "AND department_id = ? ORDER BY created_at LIMIT 1",
                (org_id, name, department_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM policies WHERE organization_id = ? AND name = ? "
                "AND department_id IS NULL ORDER BY created_at LIMIT 1",
                (org_id, name),
            ).fetchone()
        return _row_to_policy(row) if row else None

    def update_policy_fields(
        self,
        policy_id: str,
        *,
        description: str | None = None,
        permissions: list[str] | None = None,
    ) -> bool:
        """§43: convergencia de `geas sync` para políticas declarativas.

        El binding a departamento se decide en creación; mover una política
        de departamento se hace recreándola (update no re-binding).
        """
        sets: list[str] = []
        values: list[str] = []
        if description is not None:
            sets.append("description = ?")
            values.append(description)
        if permissions is not None:
            sets.append("permissions = ?")
            values.append(json.dumps(permissions))
        if not sets:
            return False
        values.append(policy_id)
        cur = self.conn.execute(
            f"UPDATE policies SET {', '.join(sets)} WHERE id = ?", values
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ─── Users ──────────────────────────────────────────────────────────

    def create_user(self, user: User) -> User:
        self.conn.execute(
            "INSERT INTO users (id, organization_id, name, email, department_id, "
            "role_id, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user.id,
                user.organization_id,
                user.name,
                user.email,
                user.department_id or None,
                user.role_id or None,
                user.active,
                user.created_at,
            ),
        )
        self.conn.commit()
        return user

    def get_user(self, user_id: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self, org_id: str) -> list[User]:
        rows = self.conn.execute(
            "SELECT * FROM users WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_user(r) for r in rows]

    # ─── Agents ─────────────────────────────────────────────────────────

    def create_agent(self, agent: Agent) -> Agent:
        self.conn.execute(
            "INSERT INTO agents (id, organization_id, name, provider, model, "
            "model_version, department_id, role_id, harness_id, active, configuration, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                agent.id,
                agent.organization_id,
                agent.name,
                agent.provider,
                agent.model,
                agent.model_version,
                agent.department_id or None,
                agent.role_id or None,
                agent.harness_id,
                agent.active,
                json.dumps(agent.configuration),
                agent.created_at,
            ),
        )
        self.conn.commit()
        return agent

    def get_agent(self, agent_id: str) -> Agent | None:
        row = self.conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        return _row_to_agent(row) if row else None

    def list_agents(self, org_id: str) -> list[Agent]:
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_agent(r) for r in rows]

    # ─── Autorización ──────────────────────────────────────────────────

    def get_actor_permissions(self, actor_id: str, organization_id: str) -> set[str]:
        """Permisos efectivos de un usuario o agente activo de la organización."""
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

    # ─── Repositories ───────────────────────────────────────────────────

    def create_repository(self, repo: Repository) -> Repository:
        self.conn.execute(
            "INSERT INTO repositories (id, organization_id, department_id, name, "
            "provider, url, default_branch, visibility, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                repo.id,
                repo.organization_id,
                repo.department_id or None,
                repo.name,
                repo.provider,
                repo.url,
                repo.default_branch,
                repo.visibility.value,
                repo.active,
            ),
        )
        self.conn.commit()
        return repo

    def get_repository(self, repo_id: str) -> Repository | None:
        row = self.conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        return _row_to_repo(row) if row else None

    def get_repository_by_name(
        self, org_id: str, name: str, department_id: str | None = None
    ) -> Repository | None:
        """§43: lookup para `geas sync` — idempotente por nombre.

        La unicidad de repositorios es por (org, nombre, departamento):
        YouTube/backend y Gmail/backend conviven (§43). `department_id`
        None busca repos de organización (sin departamento)."""
        if department_id is None:
            row = self.conn.execute(
                "SELECT * FROM repositories WHERE organization_id = ? AND name = ? "
                "AND department_id IS NULL ORDER BY rowid LIMIT 1",
                (org_id, name),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM repositories WHERE organization_id = ? AND name = ? "
                "AND department_id = ? ORDER BY rowid LIMIT 1",
                (org_id, name, department_id),
            ).fetchone()
        return _row_to_repo(row) if row else None

    def list_repositories(self, org_id: str) -> list[Repository]:
        rows = self.conn.execute(
            "SELECT * FROM repositories WHERE organization_id = ? ORDER BY name",
            (org_id,),
        ).fetchall()
        return [_row_to_repo(r) for r in rows]

    # ─── Resources ──────────────────────────────────────────────────────

    def create_resource(self, res: Resource) -> Resource:
        self.conn.execute(
            "INSERT INTO resources (id, repository_id, path, type, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                res.id,
                res.repository_id or None,
                res.path,
                res.type.value,
                json.dumps(res.metadata),
            ),
        )
        self.conn.commit()
        return res

    def get_resource(self, res_id: str) -> Resource | None:
        row = self.conn.execute(
            "SELECT * FROM resources WHERE id = ?", (res_id,)
        ).fetchone()
        return _row_to_resource(row) if row else None

    def list_resources(self, repo_id: str) -> list[Resource]:
        rows = self.conn.execute(
            "SELECT * FROM resources WHERE repository_id = ? ORDER BY path",
            (repo_id,),
        ).fetchall()
        return [_row_to_resource(r) for r in rows]

    # ─── ResourceLocks ──────────────────────────────────────────────────

    def create_lock(self, lock: ResourceLock) -> ResourceLock:
        self.conn.execute(
            "INSERT INTO resource_locks (id, resource_id, ticket_id, actor_id, "
            "created_at, expires_at, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lock.id,
                lock.resource_id,
                lock.ticket_id,
                lock.actor_id,
                lock.created_at,
                lock.expires_at,
                lock.last_heartbeat,
            ),
        )
        self.conn.commit()
        return lock

    def acquire_locks(
        self,
        resource_ids: list[str],
        ticket_id: str,
        actor_id: str,
        ttl_seconds: int = 7200,
    ) -> tuple[bool, str | None]:
        """Adquiere todos los locks o ninguno bajo una transacción SQLite."""
        now = datetime.now(UTC)
        now_value = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        unique_ids = list(dict.fromkeys(resource_ids))
        if not unique_ids:
            return True, None
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(
                "DELETE FROM resource_locks WHERE expires_at <= ?", (now_value,)
            )
            marks = ", ".join("?" for _ in unique_ids)
            blocked = self.conn.execute(
                f"SELECT resource_id FROM resource_locks WHERE resource_id IN ({marks}) "
                "AND ticket_id != ? LIMIT 1",
                (*unique_ids, ticket_id),
            ).fetchone()
            if blocked:
                self.conn.rollback()
                return False, blocked["resource_id"]
            for resource_id in unique_ids:
                existing = self.conn.execute(
                    "SELECT id FROM resource_locks WHERE resource_id = ? AND ticket_id = ?",
                    (resource_id, ticket_id),
                ).fetchone()
                if existing is None:
                    self.conn.execute(
                        "INSERT INTO resource_locks "
                        "(id, resource_id, ticket_id, actor_id, created_at, expires_at, last_heartbeat) "
                        "VALUES (lower(hex(randomblob(16))), ?, ?, ?, ?, ?, ?)",
                        (
                            resource_id,
                            ticket_id,
                            actor_id,
                            now_value,
                            expires,
                            now_value,
                        ),
                    )
            self.conn.commit()
            return True, None
        except sqlite3.Error:
            self.conn.rollback()
            raise

    def get_lock_for_resource(self, resource_id: str) -> ResourceLock | None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute("DELETE FROM resource_locks WHERE expires_at <= ?", (now,))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM resource_locks WHERE resource_id = ? "
            "AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
            (resource_id, now),
        ).fetchone()
        return _row_to_lock(row) if row else None

    def release_lock(self, lock_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM resource_locks WHERE id = ?", (lock_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def release_locks_for_ticket(self, ticket_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM resource_locks WHERE ticket_id = ?", (ticket_id,)
        )
        self.conn.commit()
        return cur.rowcount

    def heartbeat_lock(self, lock_id: str, ttl_seconds: int = 7200) -> bool:
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        cur = self.conn.execute(
            "UPDATE resource_locks SET last_heartbeat = ?, expires_at = ? "
            "WHERE id = ? AND expires_at > ?",
            (now, expires, lock_id, now),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def expire_locks(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM resource_locks WHERE expires_at <= ?",
            (datetime.now(UTC).isoformat(),),
        )
        self.conn.commit()
        return cur.rowcount

    # ─── Tickets ────────────────────────────────────────────────────────

    def create_ticket(self, ticket: Ticket) -> Ticket:
        self.conn.execute(
            "INSERT INTO tickets (id, title, description, organization_id, "
            "department_id, repository_id, creator_id, assigned_actor_id, "
            "created_at, started_at, completed_at, status, priority, files, "
            "resources, dependencies, blocked_tickets, result, feedback, "
            "branch, commit_before, commit_after) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket.id,
                ticket.title,
                ticket.description,
                ticket.organization_id,
                ticket.department_id or None,
                ticket.repository_id or None,
                ticket.creator_id,
                ticket.assigned_actor_id,
                ticket.created_at,
                ticket.started_at,
                ticket.completed_at,
                ticket.status.value,
                ticket.priority,
                json.dumps(ticket.files),
                json.dumps(ticket.resources),
                json.dumps(ticket.dependencies),
                json.dumps(ticket.blocked_tickets),
                ticket.result,
                ticket.feedback,
                ticket.branch,
                ticket.commit_before,
                ticket.commit_after,
            ),
        )
        self.conn.commit()
        return ticket

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        row = self.conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return _row_to_ticket(row) if row else None

    def list_tickets(self, org_id: str, status: str | None = None) -> list[Ticket]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM tickets WHERE organization_id = ? AND status = ? "
                "ORDER BY priority DESC, created_at",
                (org_id, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tickets WHERE organization_id = ? "
                "ORDER BY priority DESC, created_at",
                (org_id,),
            ).fetchall()
        return [_row_to_ticket(r) for r in rows]

    def update_ticket_status(self, ticket_id: str, status: str) -> bool:
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        if status == "IN_PROGRESS":
            cur = self.conn.execute(
                "UPDATE tickets SET status = ?, started_at = COALESCE(started_at, ?) "
                "WHERE id = ?",
                (status, now, ticket_id),
            )
        elif status in ("DONE", "CANCELLED"):
            cur = self.conn.execute(
                "UPDATE tickets SET status = ?, completed_at = ? WHERE id = ?",
                (status, now, ticket_id),
            )
        else:
            cur = self.conn.execute(
                "UPDATE tickets SET status = ? WHERE id = ?",
                (status, ticket_id),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def update_ticket_fields(self, ticket_id: str, **fields: object) -> bool:
        """Actualiza únicamente los campos de ticket modificables por la API."""
        allowed = {"title", "description", "priority", "result", "feedback", "branch"}
        updates = {name: value for name, value in fields.items() if name in allowed}
        if not updates:
            return False
        assignments = ", ".join(f"{name} = ?" for name in updates)
        cur = self.conn.execute(
            f"UPDATE tickets SET {assignments} WHERE id = ?",
            (*updates.values(), ticket_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def assign_ticket(self, ticket_id: str, actor_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tickets SET assigned_actor_id = ?, status = 'CLAIMED' "
            "WHERE id = ? AND status = 'FREE'",
            (actor_id, ticket_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def start_ticket(
        self,
        ticket_id: str,
        commit_before: str = "",
        branch: str = "",
        actor_id: str = "",
    ) -> bool:
        """§24/§39: claim atómico de un ticket FREE.

        El UPDATE con guardia de estado ES la operación atómica: dos
        procesos concurrentes que lean FREE no pueden reclamar los dos el
        mismo ticket (check-then-act TOCTOU-safe en una sola sentencia).
        Devuelve False si el ticket ya no está FREE.
        """
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            "UPDATE tickets SET status = 'IN_PROGRESS', started_at = ?, "
            "commit_before = ?, branch = ?, assigned_actor_id = ? "
            "WHERE id = ? AND status = 'FREE'",
            (now, commit_before, branch, actor_id or "", ticket_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def complete_ticket(
        self, ticket_id: str, commit_after: str = "", result: str = ""
    ) -> bool:
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            "UPDATE tickets SET status = 'DONE', completed_at = ?, "
            "commit_after = ?, result = ? WHERE id = ?",
            (now, commit_after, result, ticket_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ─── TicketDependencies ─────────────────────────────────────────────

    def create_dependency(self, dep: TicketDependency) -> TicketDependency:
        if dep.ticket_id == dep.depends_on_ticket_id or self.would_create_cycle(
            dep.ticket_id, dep.depends_on_ticket_id
        ):
            raise ValueError("DEPENDENCY_CYCLE")
        self.conn.execute(
            "INSERT INTO ticket_dependencies (id, ticket_id, depends_on_ticket_id, "
            "created_at) VALUES (?, ?, ?, ?)",
            (dep.id, dep.ticket_id, dep.depends_on_ticket_id, dep.created_at),
        )
        self.conn.commit()
        return dep

    def would_create_cycle(self, ticket_id: str, depends_on_ticket_id: str) -> bool:
        """Comprueba si ``ticket_id -> depends_on`` cerraría un ciclo."""
        pending = [depends_on_ticket_id]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == ticket_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            rows = self.conn.execute(
                "SELECT depends_on_ticket_id FROM ticket_dependencies WHERE ticket_id = ?",
                (current,),
            ).fetchall()
            pending.extend(row["depends_on_ticket_id"] for row in rows)
        return False

    def get_dependencies(self, ticket_id: str) -> list[Ticket]:
        """Devuelve los tickets de los que depende ticket_id."""
        rows = self.conn.execute(
            "SELECT t.* FROM tickets t "
            "JOIN ticket_dependencies td ON t.id = td.depends_on_ticket_id "
            "WHERE td.ticket_id = ? ORDER BY t.created_at",
            (ticket_id,),
        ).fetchall()
        return [_row_to_ticket(r) for r in rows]

    def get_blocked_by(self, ticket_id: str) -> list[Ticket]:
        """Devuelve los tickets que están bloqueados por ticket_id."""
        rows = self.conn.execute(
            "SELECT t.* FROM tickets t "
            "JOIN ticket_dependencies td ON t.id = td.ticket_id "
            "WHERE td.depends_on_ticket_id = ? ORDER BY t.created_at",
            (ticket_id,),
        ).fetchall()
        return [_row_to_ticket(r) for r in rows]

    def are_dependencies_resolved(self, ticket_id: str) -> bool:
        """True si todos los tickets de los que depende están DONE."""
        unresolved = self.conn.execute(
            "SELECT COUNT(*) FROM tickets t "
            "JOIN ticket_dependencies td ON t.id = td.depends_on_ticket_id "
            "WHERE td.ticket_id = ? AND t.status != 'DONE'",
            (ticket_id,),
        ).fetchone()
        return unresolved[0] == 0 if unresolved else True

    # ─── Executions ─────────────────────────────────────────────────────

    def create_execution(self, exe: Execution) -> Execution:
        self.conn.execute(
            "INSERT INTO executions (id, ticket_id, actor_id, harness_id, "
            "provider, model, model_version, started_at, finished_at, "
            "tokens_input, tokens_output, cost, tools_used, iterations, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exe.id,
                exe.ticket_id,
                exe.actor_id,
                exe.harness_id,
                exe.provider,
                exe.model,
                exe.model_version,
                exe.started_at,
                exe.finished_at,
                exe.tokens_input,
                exe.tokens_output,
                exe.cost,
                json.dumps(exe.tools_used),
                exe.iterations,
                exe.result,
            ),
        )
        self.conn.commit()
        return exe

    def get_executions(self, ticket_id: str) -> list[Execution]:
        rows = self.conn.execute(
            "SELECT * FROM executions WHERE ticket_id = ? ORDER BY started_at",
            (ticket_id,),
        ).fetchall()
        return [_row_to_execution(r) for r in rows]

    def update_execution_result(
        self, execution_id: str, result: str, finished_at: str | None = None
    ) -> bool:
        """Registra el resultado final de una ejecución (§28)."""
        if finished_at is None:
            finished_at = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            "UPDATE executions SET result = ?, finished_at = ? WHERE id = ?",
            (result, finished_at, execution_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ─── TestResults ────────────────────────────────────────────────────

    def create_test_result(self, tr: TestResult) -> TestResult:
        self.conn.execute(
            "INSERT INTO test_results (id, ticket_id, commit_id, pipeline_id, "
            "status, started_at, finished_at, logs_reference) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tr.id,
                tr.ticket_id,
                tr.commit_id,
                tr.pipeline_id,
                tr.status,
                tr.started_at,
                tr.finished_at,
                tr.logs_reference,
            ),
        )
        self.conn.commit()
        return tr

    def get_test_results(self, ticket_id: str) -> list[TestResult]:
        rows = self.conn.execute(
            "SELECT * FROM test_results WHERE ticket_id = ? ORDER BY started_at",
            (ticket_id,),
        ).fetchall()
        return [_row_to_test_result(r) for r in rows]

    # ─── Events ─────────────────────────────────────────────────────────

    def create_event(self, event: Event) -> Event:
        self.conn.execute(
            "INSERT INTO events (id, event_type, organization_id, actor_id, "
            "resource_type, resource_id, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.event_type,
                event.organization_id,
                event.actor_id,
                event.resource_type,
                event.resource_id,
                event.timestamp,
                json.dumps(event.metadata),
            ),
        )
        self.conn.commit()
        return event

    def list_events(self, org_id: str, limit: int = 100) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE organization_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    # ─── AuditLog ───────────────────────────────────────────────────────

    def create_audit(self, audit: AuditLog) -> AuditLog:
        self.conn.execute(
            "INSERT INTO audit_log (id, actor_id, action, resource_type, "
            "resource_id, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                audit.id,
                audit.actor_id,
                audit.action,
                audit.resource_type,
                audit.resource_id,
                audit.timestamp,
                json.dumps(audit.metadata),
            ),
        )
        self.conn.commit()
        return audit

    def list_audit(self, org_id: str, limit: int = 100) -> list[AuditLog]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_audit(r) for r in rows]


# ─── Schema SQL ─────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    profile TEXT DEFAULT 'individual'  -- §43: individual|team|enterprise
);

CREATE TABLE IF NOT EXISTS departments (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    parent_department_id TEXT REFERENCES departments(id),
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    permissions TEXT DEFAULT '[]'  -- JSON array
);

CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    department_id TEXT REFERENCES departments(id),
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    permissions TEXT DEFAULT '[]',  -- JSON array
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    department_id TEXT REFERENCES departments(id),
    role_id TEXT REFERENCES roles(id),
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    department_id TEXT REFERENCES departments(id),
    role_id TEXT REFERENCES roles(id),
    harness_id TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    configuration TEXT DEFAULT '{}',  -- JSON object
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    department_id TEXT REFERENCES departments(id),
    name TEXT NOT NULL,
    provider TEXT DEFAULT '',
    url TEXT DEFAULT '',
    default_branch TEXT DEFAULT 'main',
    visibility TEXT DEFAULT 'PRIVATE',
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL REFERENCES repositories(id),
    path TEXT NOT NULL,
    type TEXT DEFAULT 'FILE',
    metadata TEXT DEFAULT '{}'  -- JSON object
);

CREATE TABLE IF NOT EXISTS resource_locks (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(id),
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    department_id TEXT REFERENCES departments(id),
    repository_id TEXT REFERENCES repositories(id),
    creator_id TEXT DEFAULT '',
    assigned_actor_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT DEFAULT 'FREE',
    priority INTEGER DEFAULT 0,
    files TEXT DEFAULT '[]',        -- JSON array
    resources TEXT DEFAULT '[]',    -- JSON array of resource ids
    dependencies TEXT DEFAULT '[]', -- JSON array of ticket ids
    blocked_tickets TEXT DEFAULT '[]',
    result TEXT DEFAULT '',
    feedback TEXT DEFAULT '',
    branch TEXT DEFAULT '',
    commit_before TEXT DEFAULT '',
    commit_after TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ticket_dependencies (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    depends_on_ticket_id TEXT NOT NULL REFERENCES tickets(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    actor_id TEXT NOT NULL,
    harness_id TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    model_version TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    tools_used TEXT DEFAULT '[]',  -- JSON array
    iterations INTEGER DEFAULT 0,
    result TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_results (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    commit_id TEXT DEFAULT '',
    pipeline_id TEXT DEFAULT '',
    status TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    logs_reference TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    actor_id TEXT DEFAULT '',
    resource_type TEXT DEFAULT '',
    resource_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'  -- JSON object
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT DEFAULT '',
    resource_id TEXT DEFAULT '',
    timestamp TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'  -- JSON object
);
"""


# ─── Row → Model converters ─────────────────────────────────────────────────


def _row_to_org(row: sqlite3.Row) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        active=bool(row["active"]),
        profile=row["profile"],
    )


def _row_to_dept(row: sqlite3.Row) -> Department:
    return Department(
        id=row["id"],
        organization_id=row["organization_id"],
        parent_department_id=row["parent_department_id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        active=bool(row["active"]),
    )


def _row_to_role(row: sqlite3.Row) -> Role:
    return Role(
        id=row["id"],
        organization_id=row["organization_id"],
        name=row["name"],
        permissions=json.loads(row["permissions"]),
    )


def _row_to_policy(row: sqlite3.Row) -> Policy:
    return Policy(
        id=row["id"],
        organization_id=row["organization_id"],
        department_id=row["department_id"],
        name=row["name"],
        description=row["description"],
        permissions=json.loads(row["permissions"]),
        created_at=row["created_at"],
        active=bool(row["active"]),
    )


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        organization_id=row["organization_id"],
        name=row["name"],
        email=row["email"],
        department_id=row["department_id"],
        role_id=row["role_id"],
        active=bool(row["active"]),
        created_at=row["created_at"],
    )


def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        id=row["id"],
        organization_id=row["organization_id"],
        name=row["name"],
        provider=row["provider"],
        model=row["model"],
        model_version=row["model_version"],
        department_id=row["department_id"],
        role_id=row["role_id"],
        harness_id=row["harness_id"],
        active=bool(row["active"]),
        configuration=json.loads(row["configuration"]),
        created_at=row["created_at"],
    )


def _row_to_repo(row: sqlite3.Row) -> Repository:
    from geas.models import Visibility

    return Repository(
        id=row["id"],
        organization_id=row["organization_id"],
        department_id=row["department_id"],
        name=row["name"],
        provider=row["provider"],
        url=row["url"],
        default_branch=row["default_branch"],
        visibility=Visibility(row["visibility"]),
        active=bool(row["active"]),
    )


def _row_to_resource(row: sqlite3.Row) -> Resource:
    from geas.models import ResourceType

    return Resource(
        id=row["id"],
        repository_id=row["repository_id"],
        path=row["path"],
        type=ResourceType(row["type"]),
        metadata=json.loads(row["metadata"]),
    )


def _row_to_lock(row: sqlite3.Row) -> ResourceLock:
    return ResourceLock(
        id=row["id"],
        resource_id=row["resource_id"],
        ticket_id=row["ticket_id"],
        actor_id=row["actor_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_heartbeat=row["last_heartbeat"],
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    from geas.models import TicketStatus

    return Ticket(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        organization_id=row["organization_id"],
        department_id=row["department_id"],
        repository_id=row["repository_id"],
        creator_id=row["creator_id"],
        assigned_actor_id=row["assigned_actor_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        status=TicketStatus(row["status"]),
        priority=row["priority"],
        files=json.loads(row["files"]),
        resources=json.loads(row["resources"]),
        dependencies=json.loads(row["dependencies"]),
        blocked_tickets=json.loads(row["blocked_tickets"]),
        result=row["result"],
        feedback=row["feedback"],
        branch=row["branch"],
        commit_before=row["commit_before"],
        commit_after=row["commit_after"],
    )


def _row_to_execution(row: sqlite3.Row) -> Execution:
    return Execution(
        id=row["id"],
        ticket_id=row["ticket_id"],
        actor_id=row["actor_id"],
        harness_id=row["harness_id"],
        provider=row["provider"],
        model=row["model"],
        model_version=row["model_version"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        tokens_input=row["tokens_input"],
        tokens_output=row["tokens_output"],
        cost=row["cost"],
        tools_used=json.loads(row["tools_used"]),
        iterations=row["iterations"],
        result=row["result"],
    )


def _row_to_test_result(row: sqlite3.Row) -> TestResult:
    return TestResult(
        id=row["id"],
        ticket_id=row["ticket_id"],
        commit_id=row["commit_id"],
        pipeline_id=row["pipeline_id"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        logs_reference=row["logs_reference"],
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        event_type=row["event_type"],
        organization_id=row["organization_id"],
        actor_id=row["actor_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        timestamp=row["timestamp"],
        metadata=json.loads(row["metadata"]),
    )


def _row_to_audit(row: sqlite3.Row) -> AuditLog:
    return AuditLog(
        id=row["id"],
        actor_id=row["actor_id"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        timestamp=row["timestamp"],
        metadata=json.loads(row["metadata"]),
    )
