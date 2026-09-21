"""
geas.models — Modelos de datos de Geas.

Todos los modelos del sistema de coordinación organizativa.
Cada modelo se persiste en SQLite (MVP) y将来 en PostgreSQL.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ─── Enums ──────────────────────────────────────────────────────────────────


class ActorType(enum.Enum):
    HUMAN = "HUMAN"
    AI_AGENT = "AI_AGENT"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"


class TicketStatus(enum.Enum):
    FREE = "FREE"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class ResourceType(enum.Enum):
    FILE = "FILE"
    DIRECTORY = "DIRECTORY"
    DATABASE = "DATABASE"
    API = "API"
    MODEL = "MODEL"
    CONFIG = "CONFIG"
    OTHER = "OTHER"


class Visibility(enum.Enum):
    PRIVATE = "PRIVATE"
    INTERNAL = "INTERNAL"
    PUBLIC = "PUBLIC"


# ─── Organization ───────────────────────────────────────────────────────────


@dataclass
class Organization:
    id: str = field(default_factory=_uuid)
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=_now)
    active: bool = True


# ─── Department ─────────────────────────────────────────────────────────────


@dataclass
class Department:
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    parent_department_id: str | None = None
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=_now)
    active: bool = True


# ─── Role ───────────────────────────────────────────────────────────────────


@dataclass
class Role:
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    name: str = ""
    permissions: list[str] = field(default_factory=list)


# ─── User ───────────────────────────────────────────────────────────────────


@dataclass
class User:
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    name: str = ""
    email: str = ""
    department_id: str | None = None
    role_id: str | None = None
    active: bool = True
    created_at: str = field(default_factory=_now)


# ─── Agent ──────────────────────────────────────────────────────────────────


@dataclass
class Agent:
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    name: str = ""
    provider: str = ""
    model: str = ""
    model_version: str = ""
    department_id: str | None = None
    harness_id: str = ""
    active: bool = True
    configuration: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)


# ─── Repository ─────────────────────────────────────────────────────────────


@dataclass
class Repository:
    id: str = field(default_factory=_uuid)
    organization_id: str = ""
    department_id: str | None = None
    name: str = ""
    provider: str = ""  # github, gitlab, bitbucket, self-hosted
    url: str = ""
    default_branch: str = "main"
    visibility: Visibility = Visibility.PRIVATE
    active: bool = True


# ─── Resource ───────────────────────────────────────────────────────────────


@dataclass
class Resource:
    id: str = field(default_factory=_uuid)
    repository_id: str | None = None
    path: str = ""
    type: ResourceType = ResourceType.FILE
    metadata: dict = field(default_factory=dict)


# ─── ResourceLock ───────────────────────────────────────────────────────────


@dataclass
class ResourceLock:
    id: str = field(default_factory=_uuid)
    resource_id: str = ""
    ticket_id: str = ""
    actor_id: str = ""
    created_at: str = field(default_factory=_now)
    expires_at: str = ""
    last_heartbeat: str = field(default_factory=_now)


# ─── Ticket ─────────────────────────────────────────────────────────────────


@dataclass
class Ticket:
    id: str = field(default_factory=_uuid)
    title: str = ""
    description: str = ""

    organization_id: str = ""
    department_id: str | None = None
    repository_id: str | None = None

    creator_id: str = ""
    assigned_actor_id: str = ""

    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    completed_at: str | None = None

    status: TicketStatus = TicketStatus.FREE
    priority: int = 0  # 0=baja, 1=media, 2=alta, 3=crítica

    files: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)  # resource ids

    dependencies: list[str] = field(
        default_factory=list
    )  # ticket ids de los que depende
    blocked_tickets: list[str] = field(
        default_factory=list
    )  # tickets que dependen de este

    result: str = ""
    feedback: str = ""

    branch: str = ""

    commit_before: str = ""
    commit_after: str = ""


# ─── TicketDependency ──────────────────────────────────────────────────────


@dataclass
class TicketDependency:
    """Relación: ticket_id depends_on depends_on_ticket_id."""

    id: str = field(default_factory=_uuid)
    ticket_id: str = ""
    depends_on_ticket_id: str = ""
    created_at: str = field(default_factory=_now)


# ─── Execution ──────────────────────────────────────────────────────────────


@dataclass
class Execution:
    id: str = field(default_factory=_uuid)
    ticket_id: str = ""
    actor_id: str = ""
    harness_id: str = ""

    provider: str = ""  # anthropic, openai, etc.
    model: str = ""
    model_version: str = ""

    started_at: str = field(default_factory=_now)
    finished_at: str | None = None

    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0

    tools_used: list[str] = field(default_factory=list)
    iterations: int = 0

    result: str = ""  # success, failure, cancelled


# ─── TestResult ─────────────────────────────────────────────────────────────


@dataclass
class TestResult:
    id: str = field(default_factory=_uuid)
    ticket_id: str = ""
    commit_id: str = ""
    pipeline_id: str = ""
    status: str = ""  # passed, failed, running
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    logs_reference: str = ""


# ─── Event ──────────────────────────────────────────────────────────────────


@dataclass
class Event:
    """Evento del sistema — qué ocurrió."""

    id: str = field(default_factory=_uuid)
    event_type: str = ""  # TICKET_CREATED, RESOURCE_LOCKED, etc.
    organization_id: str = ""
    actor_id: str = ""
    resource_type: str = ""  # ticket, resource, repository, etc.
    resource_id: str = ""
    timestamp: str = field(default_factory=_now)
    metadata: dict = field(default_factory=dict)


# ─── AuditLog ───────────────────────────────────────────────────────────────


@dataclass
class AuditLog:
    """Registro de auditoría — quién hizo qué."""

    id: str = field(default_factory=_uuid)
    actor_id: str = ""
    action: str = ""  # LOCK_RESOURCE, CREATE_TICKET, etc.
    resource_type: str = ""
    resource_id: str = ""
    timestamp: str = field(default_factory=_now)
    metadata: dict = field(default_factory=dict)


# ─── Permisos predefinidos ─────────────────────────────────────────────────

DEFAULT_PERMISSIONS = [
    "ticket:create",
    "ticket:read",
    "ticket:update",
    "ticket:assign",
    "ticket:start",
    "ticket:block",
    "ticket:complete",
    "resource:read",
    "resource:lock",
    "resource:unlock",
    "repository:read",
    "repository:write",
    "department:read",
    "department:manage",
    "user:manage",
    "permission:manage",
    "dependency:create",
    "dependency:update",
    "git:branch",
    "git:commit",
    "git:push",
    "git:pr",
    "git:merge",
    "audit:read",
    "execution:read",
]

# Roles predefinidos
DEFAULT_ROLES = {
    "developer": [
        "ticket:create",
        "ticket:read",
        "ticket:update",
        "resource:read",
        "resource:lock",
        "resource:unlock",
        "repository:read",
        "repository:write",
        "dependency:create",
        "dependency:update",
        "git:branch",
        "git:commit",
        "git:push",
        "git:pr",
    ],
    "manager": [
        "ticket:create",
        "ticket:read",
        "ticket:update",
        "ticket:assign",
        "resource:read",
        "resource:lock",
        "resource:unlock",
        "repository:read",
        "repository:write",
        "department:read",
        "department:manage",
        "user:manage",
        "dependency:create",
        "dependency:update",
        "git:branch",
        "git:commit",
        "git:push",
        "git:pr",
        "git:merge",
        "audit:read",
        "execution:read",
    ],
    "admin": DEFAULT_PERMISSIONS,
    "agent": [
        "ticket:read",
        "ticket:start",
        "ticket:block",
        "ticket:complete",
        "resource:read",
        "resource:lock",
        "resource:unlock",
        "repository:read",
        "repository:write",
        "git:branch",
        "git:commit",
        "git:push",
        "git:pr",
        "execution:read",
    ],
    "service_account": [
        "ticket:read",
        "resource:read",
        "repository:read",
        "audit:read",
        "execution:read",
    ],
}
