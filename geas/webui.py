"""geas.webui — Panel web de estado (§42, MVP 2).

Web UI read-only que responde a «¿qué está pasando?»: organizaciones,
tickets por estado, locks activos, últimos eventos y auditoría. HTML
estático generado con stdlib pura — Geas no añade dependencias. La
escritura sigue pasando por la API/MCP (§26), no por la UI: la UI es
una ventana al estado, no otra superficie de mutación.
"""

from __future__ import annotations

import html as _html
from datetime import UTC, datetime

from geas.models import Repository, Resource, ResourceLock, Ticket, TicketStatus
from geas.storage import Storage

_STATUS_ORDER = [
    TicketStatus.FREE,
    TicketStatus.CLAIMED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.BLOCKED,
    TicketStatus.REVIEW,
    TicketStatus.DONE,
    TicketStatus.CANCELLED,
]

_STATUS_COLOR = {
    "FREE": "#8b949e",
    "CLAIMED": "#58a6ff",
    "IN_PROGRESS": "#3fb950",
    "BLOCKED": "#d29922",
    "REVIEW": "#bc8cff",
    "DONE": "#238636",
    "CANCELLED": "#f85149",
}

_PRIORITY = {0: "baja", 1: "media", 2: "alta", 3: "crítica"}

_CSS = """
body{font:15px/1.5 system-ui;max-width:980px;margin:0 auto;padding:1.5rem;color:#c9d1d9;background:#0d1117}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid #30363d;padding-bottom:.5rem;margin-bottom:1rem}
header h1{margin:0 0 .2rem}
h2{font-size:1.1rem;margin:1.5rem 0 .5rem;border-bottom:1px solid #21262d;padding-bottom:.25rem}
.badge{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.75rem;font-weight:600}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.875rem}
th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #21262d;vertical-align:top}
th{color:#8b949e;font-weight:600}
code{font-family:ui-monospace,monospace;font-size:.8rem;background:#161b22;padding:.05rem .35rem;border-radius:4px}
.empty{color:#8b949e}
.stats{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}
.stats span{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:.2rem .6rem;font-size:.8rem}
footer{margin-top:2rem;color:#8b949e;font-size:.8rem;border-top:1px solid #30363d;padding-top:.5rem}
"""


def _e(value: object) -> str:
    return _html.escape(str(value))


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang='es'><meta charset='utf-8'>"
        f"<title>{_e(title)}</title><style>{_CSS}</style>\n{body}\n</html>"
    )


def _status_badge(status: TicketStatus) -> str:
    name = status.name if hasattr(status, "name") else str(status)
    color = _STATUS_COLOR.get(name, "#8b949e")
    return f"<span class='badge' style='background:{color}22;color:{color};border:1px solid {color}'>{_e(name)}</span>"


def _tickets_by_status(tickets: list[Ticket]) -> dict[TicketStatus, int]:
    counts: dict[TicketStatus, int] = {}
    for ticket in tickets:
        counts[ticket.status] = counts.get(ticket.status, 0) + 1
    return counts


def _active_locks(
    storage: Storage, org
) -> list[tuple[ResourceLock, Resource, Repository]]:
    """Locks vivos de una organización (expired los purga get_lock_for_resource)."""
    locks: list[tuple[ResourceLock, Resource, Repository]] = []
    for repo in storage.list_repositories(org.id):
        for resource in storage.list_resources(repo.id):
            lock = storage.get_lock_for_resource(resource.id)
            if lock:
                locks.append((lock, resource, repo))
    return locks


def _ticket_table(tickets: list[Ticket], limit: int = 30) -> str:
    if not tickets:
        return "<p class='empty'>Sin tickets.</p>"
    rows = []
    for ticket in tickets[:limit]:
        actor = ticket.assigned_actor_id or ticket.creator_id or "—"
        rows.append(
            "<tr>"
            f"<td><code><a href='/ticket/{_e(ticket.id)}'>{_e(ticket.id)}</a></code></td>"
            f"<td>{_status_badge(ticket.status)}</td>"
            f"<td>{_e(ticket.title)}</td>"
            f"<td><code>{_e(actor)}</code></td>"
            f"<td><code>{_e(ticket.branch) if ticket.branch else '—'}</code></td>"
            f"<td><code>{_e(ticket.commit_before[:8]) if ticket.commit_before else '—'}</code> → "
            f"<code>{_e(ticket.commit_after[:8]) if ticket.commit_after else '—'}</code></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Ticket</th><th>Estado</th><th>Título</th>"
        "<th>Actor</th><th>Branch</th><th>commit_before → after</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _org_section(storage: Storage, org, tickets: list[Ticket]) -> str:
    depts = storage.list_departments(org.id)
    repos = storage.list_repositories(org.id)
    by_status = _tickets_by_status(tickets)
    locks = _active_locks(storage, org)
    events = storage.list_events(org.id, limit=15)
    audit = storage.list_audit(org.id, limit=15)

    stats = [f"<span>{len(depts)} deptos</span>", f"<span>{len(repos)} repos</span>"]
    for status in _STATUS_ORDER:
        count = by_status.get(status, 0)
        if count:
            name = status.name
            color = _STATUS_COLOR.get(name, "#8b949e")
            stats.append(
                f"<span style='color:{color}'>{name}: {count}</span>"
            )

    lock_rows = "".join(
        f"<tr><td><code>{_e(resource.path)}</code></td>"
        f"<td><code>{_e(lock.ticket_id)}</code></td>"
        f"<td><code>{_e(lock.actor_id)}</code></td>"
        f"<td><code>{_e(lock.expires_at)}</code></td></tr>"
        for lock, resource, _repo in locks
    ) or "<tr><td colspan='4' class='empty'>Sin locks activos.</td></tr>"

    event_rows = "".join(
        f"<tr><td><code>{_e(event.timestamp)}</code></td>"
        f"<td><b>{_e(event.event_type)}</b></td>"
        f"<td><code>{_e(event.actor_id)}</code></td>"
        f"<td><code>{_e(event.resource_id)}</code></td></tr>"
        for event in events
    ) or "<tr><td colspan='4' class='empty'>Sin eventos.</td></tr>"

    audit_rows = "".join(
        f"<tr><td><code>{_e(entry.timestamp)}</code></td>"
        f"<td><b>{_e(entry.action)}</b></td>"
        f"<td><code>{_e(entry.actor_id)}</code></td></tr>"
        for entry in audit
    ) or "<tr><td colspan='3' class='empty'>Sin auditoría.</td></tr>"

    active = "activa" if org.active else "<span style='color:#f85149'>inactiva</span>"
    return (
        f"<section><h2>{_e(org.name)} <span class='badge'>{active}</span></h2>"
        + f"<p class='empty'><code>{_e(org.id)}</code> · {_e(org.description or '')}</p>"
        + "<div class='stats'>" + "".join(stats) + "</div>"
        + "<h3>Tickets</h3>" + _ticket_table(tickets)
        + "<h3>Locks activos</h3>"
        + "<table><thead><tr><th>Recurso</th><th>Ticket</th><th>Actor</th>"
        + "<th>Expira</th></tr></thead><tbody>" + lock_rows + "</tbody></table>"
        + "<h3>Eventos recientes</h3>"
        + "<table><thead><tr><th>Cuándo</th><th>Evento</th><th>Actor</th>"
        + "<th>Recurso</th></tr></thead><tbody>" + event_rows + "</tbody></table>"
        + "<h3>Auditoría reciente</h3>"
        + "<table><thead><tr><th>Cuándo</th><th>Acción</th><th>Actor</th>"
        + "</tr></thead><tbody>" + audit_rows + "</tbody></table>"
        + "</section>"
    )


def render_dashboard(storage: Storage) -> str:
    """HTML del panel: una sección por organización."""
    orgs = storage.list_organizations()
    if not orgs:
        body = (
            "<header><h1>Geas</h1>"
            "<p>Quién puede hacer qué, sobre qué recurso, cuándo — y qué ha ocurrido.</p></header>"
            "<p class='empty'>Sin organizaciones todavía. Crea una con "
            "<code>geas init &quot;Mi Org&quot;</code>.</p>"
        )
    else:
        sections = "".join(
            _org_section(storage, org, storage.list_tickets(org.id)) for org in orgs
        )
        body = (
            "<header><h1>Geas</h1>"
            "<p>Quién puede hacer qué, sobre qué recurso, cuándo — y qué ha ocurrido.</p></header>"
            + sections
        )
    body += (
        "<footer>Panel de solo lectura · la escritura pasa por la API/MCP "
        "(<code>POST /api/tools/&lt;tool&gt;</code> con <code>X-Geas-Actor</code>)</footer>"
    )
    return _page("Geas", body)


def _resource_row(storage: Storage, resource_id: str) -> str:
    resource = storage.get_resource(resource_id)
    if resource is None:
        return f"<tr><td><code>{_e(resource_id)}</code></td><td class='empty'>desconocido</td><td>—</td></tr>"
    lock = storage.get_lock_for_resource(resource.id)
    lock_cell = "—"
    if lock:
        lock_cell = (
            f"<span class='badge' style='background:#d2992211;color:#d29922'>LOCKED</span> "
            f"por <code>{_e(lock.ticket_id)}</code> (expira {_e(lock.expires_at)})"
        )
    return (
        f"<tr><td><code>{_e(resource.path)}</code></td>"
        f"<td>{_e(resource.type)}</td><td>{lock_cell}</td></tr>"
    )


def render_ticket(storage: Storage, ticket_id: str) -> str | None:
    """HTML del detalle de un ticket — o None si no existe."""
    ticket = storage.get_ticket(ticket_id)
    if ticket is None:
        return None

    deps = storage.get_dependencies(ticket.id)
    blockers = storage.get_blocked_by(ticket.id)
    executions = storage.get_executions(ticket.id)
    tests = storage.get_test_results(ticket.id)

    def _link(id_: str) -> str:
        return f"<code><a href='/ticket/{_e(id_)}'>{_e(id_)}</a></code>"

    dep_cells = "".join(f"<li>{_link(t.id)} — {_e(t.title)} ({_status_badge(t.status)})</li>" for t in deps)
    if not deps:
        dep_cells = "<li class='empty'>Sin dependencias.</li>"
    blocker_cells = "".join(
        f"<li>{_link(t.id)} — {_e(t.title)} ({_status_badge(t.status)})</li>" for t in blockers
    )
    if not blockers:
        blocker_cells = "<li class='empty'>Nada bloqueado por este ticket.</li>"

    resource_rows = "".join(_resource_row(storage, r) for r in ticket.resources) or (
        "<tr><td colspan='3' class='empty'>Sin recursos declarados.</td></tr>"
    )

    exec_rows = "".join(
        f"<tr><td><code>{_e(ex.provider)}/{_e(ex.model)}</code></td>"
        f"<td><code>{_e(ex.actor_id)}</code></td>"
        f"<td>{_e(ex.tokens_input)}/{_e(ex.tokens_output)} tok</td>"
        f"<td>{_e(ex.cost)}€</td>"
        f"<td>{_e(ex.result)}</td></tr>"
        for ex in executions
    ) or "<tr><td colspan='5' class='empty'>Sin ejecuciones registradas.</td></tr>"

    test_rows = "".join(
        f"<tr><td><code>{_e(t.pipeline_id)}</code></td>"
        f"<td><code>{_e(t.commit_id)}</code></td>"
        f"<td>{_status_badge(TicketStatus.DONE) if t.status == 'passed' else _e(t.status)}</td>"
        f"<td><code>{_e(t.logs_reference)}</code></td></tr>"
        for t in tests
    ) or "<tr><td colspan='4' class='empty'>Sin resultados de tests.</td></tr>"

    body = (
        "<header>"
        f"<p><a href='/'>← Panel</a></p>"
        f"<h1>{_e(ticket.id)} {_status_badge(ticket.status)}</h1>"
        f"<h2 style='border:none'>{_e(ticket.title)}</h2>"
        f"<p class='empty'>{_e(ticket.description)}</p></header>"
        "<table><tbody>"
        f"<tr><th>Prioridad</th><td>{_e(_PRIORITY.get(ticket.priority, ticket.priority))}</td></tr>"
        f"<tr><th>Creador / asignado</th><td><code>{_e(ticket.creator_id)}</code> / "
        f"<code>{_e(ticket.assigned_actor_id or '—')}</code></td></tr>"
        f"<tr><th>Creado</th><td><code>{_e(ticket.created_at)}</code></td></tr>"
        f"<tr><th>Iniciado / completado</th><td><code>{_e(ticket.started_at or '—')}</code> / "
        f"<code>{_e(ticket.completed_at or '—')}</code></td></tr>"
        f"<tr><th>Branch</th><td><code>{_e(ticket.branch or '—')}</code></td></tr>"
        f"<tr><th>commit_before → after</th><td><code>{_e(ticket.commit_before or '—')}</code> → "
        f"<code>{_e(ticket.commit_after or '—')}</code></td></tr>"
        f"<tr><th>Result / feedback</th><td>{_e(ticket.result or '—')} / {_e(ticket.feedback or '—')}</td></tr>"
        "</tbody></table>"
        "<h2>Recursos y locks</h2>"
        "<table><thead><tr><th>Recurso</th><th>Tipo</th><th>Lock</th></tr></thead>"
        f"<tbody>{resource_rows}</tbody></table>"
        "<h2>Dependencias</h2><ul>" + dep_cells + "</ul>"
        "<h2>Bloqueados por este ticket</h2><ul>" + blocker_cells + "</ul>"
        "<h2>Ejecuciones (§28)</h2>"
        "<table><thead><tr><th>Modelo</th><th>Actor</th><th>Tokens</th>"
        "<th>Coste</th><th>Resultado</th></tr></thead><tbody>" + exec_rows + "</tbody></table>"
        "<h2>Tests (§20)</h2>"
        "<table><thead><tr><th>Pipeline</th><th>Commit</th><th>Estado</th>"
        "<th>Logs</th></tr></thead><tbody>" + test_rows + "</tbody></table>"
        "<footer><a href='/'>← Panel</a></footer>"
    )
    return _page(f"Geas · {ticket.id}", body)