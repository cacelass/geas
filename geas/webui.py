"""geas.webui — Panel web de estado (§42, MVP 2).

Panel web interactivo con:
- Filtros por estado de tickets
- Vista detallada al hacer clic en un ticket
- Creación de usuarios desde la UI
- Lista de dispositivos permitidos
- locks activos, eventos y auditoría

HTML estático generado con stdlib pura — Geas no añade dependencias.
La escritura pasa por la UI (usuarios, dispositivos) y la API/MCP (§26).
"""

from __future__ import annotations

import html as _html
import json as _json
from datetime import UTC, datetime
from urllib.parse import parse_qs

from geas.models import Repository, Resource, ResourceLock, Ticket, TicketStatus
from geas.storage import Storage
from geas.version import report as version_report

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
body{font:15px/1.5 system-ui;max-width:1100px;margin:0 auto;padding:1.5rem;color:#c9d1d9;background:#0d1117}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
header{border-bottom:1px solid #30363d;padding-bottom:.5rem;margin-bottom:1rem}
header h1{margin:0 0 .2rem}
h2{font-size:1.1rem;margin:1.5rem 0 .5rem;border-bottom:1px solid #21262d;padding-bottom:.25rem}
.badge{display:inline-block;padding:.15rem .6rem;border-radius:999px;font-size:.75rem;font-weight:600}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.875rem}
th,td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #21262d;vertical-align:top}
th{color:#8b949e;font-weight:600}
tr:hover td{background:#161b22}
td.ticket-id{cursor:pointer;color:#58a6ff}td.ticket-id:hover{text-decoration:underline}
td.ticket-title{max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
code{font-family:ui-monospace,monospace;font-size:.8rem;background:#161b22;padding:.05rem .35rem;border-radius:4px}
.empty{color:#8b949e}
.banner{margin:.75rem 0;padding:.55rem .8rem;border-radius:6px;font-size:.85rem;border:1px solid #30363d;background:#161b22}
.banner-warn{border-color:#9e6a03;background:#2d1b00;color:#f0d68a}
.banner-info{border-color:#30363d;color:#8b949e}
.stats{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}
.stats span{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:.25rem .7rem;font-size:.8rem}
.stats .count{font-weight:700}
.filter-bar{display:flex;gap:.4rem;flex-wrap:wrap;margin:.75rem 0;align-items:center}
.filter-bar select{background:#161b22;border:1px solid #30363d;color:#c9d1d9;padding:.25rem .5rem;border-radius:6px;font-size:.85rem}
.filter-bar select:focus{outline:none;border-color:#58a6ff}
.filter-bar a{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:.2rem .6rem;font-size:.8rem;color:#58a6ff;text-decoration:none}
.filter-bar a:hover{background:#1c2128;text-decoration:none}
.filter-bar a.active{background:#58a6ff;color:#0d1117;border-color:#58a6ff;font-weight:600}
.user-form{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1rem;margin:1rem 0}
.user-form h3{margin:0 0 .75rem;font-size:1rem;color:#c9d1d9}
.form-row{display:flex;gap:.75rem;margin-bottom:.5rem;align-items:center;flex-wrap:wrap}
.form-row input,.form-row select{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:.3rem .6rem;border-radius:6px;font-size:.85rem;flex:1;min-width:150px}
.form-row input:focus,.form-row select:focus{outline:none;border-color:#58a6ff}
.form-row button{background:#238636;color:#fff;border:none;padding:.3rem .8rem;border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600}
.form-row button:hover{background:#2ea043}
.device-list{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1rem;margin:.75rem 0}
.device-list h4{margin:0 0 .5rem;font-size:.95rem}
.device-item{display:flex;justify-content:space-between;align-items:center;padding:.3rem 0;border-bottom:1px solid #21262d}
.device-item:last-child{border-bottom:none}
.device-name{font-weight:600}
.device-actions{display:flex;gap:.4rem}
.btn-sm{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:.15rem .5rem;border-radius:5px;font-size:.75rem;cursor:pointer}
.btn-sm:hover{background:#30363d}
.btn-danger{color:#f85149;border-color:#f85149}
.btn-danger:hover{background:#f8514922}
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(13,17,23,.92);display:flex;align-items:center;justify-content:center;z-index:1000}
.modal{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:0;max-width:800px;width:92%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.modal-header{padding:1rem 1.5rem;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:#161b22;z-index:1}
.modal-header h2{margin:0;font-size:1.1rem}
.modal-body{padding:1.5rem}
.modal .close{background:none;border:none;color:#8b949e;font-size:1.8rem;cursor:pointer;padding:0 .3rem;line-height:1}
.modal .close:hover{color:#c9d1d9}
.ticket-detail{margin-top:.5rem}
.ticket-detail table{width:100%;background:transparent}
.ticket-detail table tr:hover td{background:transparent}
.ticket-detail table th{text-align:left;padding:.3rem .5rem;color:#8b949e;font-weight:600;border:none}
.ticket-detail table td{padding:.3rem .5rem;border:none}
.modal .form-actions{display:flex;gap:.5rem;margin-top:1rem;flex-wrap:wrap}
.modal .form-actions button{padding:.4rem 1rem;border-radius:6px;font-size:.85rem;cursor:pointer;border:none;font-weight:600}
.modal form{margin-top:1rem}
#modal-content{display:none}
.join-modal-content{display:none}
.join-cmd-box{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:1rem;margin:.75rem 0}
.join-cmd-box textarea{width:100%;background:transparent;border:none;color:#c9d1d9;font-family:ui-monospace,monospace;font-size:.85rem;resize:none;height:2.5rem;outline:none}
.join-cmd-box .copy-btn{margin-top:.5rem;background:#21262d;border:1px solid #30363d;color:#58a6ff;padding:.3rem .8rem;border-radius:6px;cursor:pointer;font-size:.8rem}
.join-cmd-box .copy-btn:hover{background:#30363d}
.join-copy-msg{color:#3fb950;font-size:.85rem;margin-left:.5rem;display:none}
.join-info{color:#8b949e;font-size:.9rem;margin:.5rem 0}
footer{margin-top:2rem;color:#8b949e;font-size:.8rem;border-top:1px solid #30363d;padding-top:.5rem}
"""


def _e(value: object, *, quote: bool = True) -> str:
    """Escapa para HTML.

    `quote=True` por defecto porque casi todo el escapado aqui va a atributos
    (title, onclick, value), donde una comilla sin escapar cambia el atributo.
    """
    return _html.escape(str(value), quote=quote)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_filter(args: list[str]) -> str:
    """Extrae el filtro ?status= de los args (simula query string)."""
    for arg in args:
        if arg.startswith("?"):
            parsed = parse_qs(arg.lstrip("?"))
            if "status" in parsed:
                return parsed["status"][0]
    return ""


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


def _filter_bar(current_status: str, tickets: list[Ticket]) -> str:
    """Barra de filtros por estado."""
    counts = _tickets_by_status(tickets)
    links = []
    total = len(tickets)
    all_active = "active" if not current_status else ""
    links.append(
        f"<a href='/?status=' class='{all_active}' style='color:#c9d1d9'>"
        f"Todos ({total})</a>"
    )
    for status in _STATUS_ORDER:
        name = status.name
        count = counts.get(status, 0)
        if count:
            active = "active" if current_status == name else ""
            color = _STATUS_COLOR.get(name, "#8b949e")
            links.append(
                f"<a href='/?status={_e(name)}' class='{active}' "
                f"style='color:{color if active else '#8b949e'}'>"
                f"{name} ({count})</a>"
            )
    return f"<div class='filter-bar'>{''.join(links)}</div>"


def _user_creation_form(storage: Storage, org_id: str) -> str:
    """Formulario para crear usuarios desde la UI."""
    roles = storage.list_roles(org_id)
    depts = storage.list_departments(org_id)
    role_opts = "".join(
        f"<option value='{_e(r.id)}'>{_e(r.name)}</option>" for r in roles
    )
    dept_opts = '<option value="">(sin departamento)</option>' + "".join(
        f"<option value='{_e(d.id)}'>{_e(d.name)}</option>" for d in depts
    )
    return (
        f"<div class='user-form'>"
        "<h3>Crear usuario</h3>"
        "<form id='create-user-form' onsubmit='createUser(event)'>"
        f"<div class='form-row'><input type='text' name='name' placeholder='Nombre' required>"
        f"<input type='email' name='email' placeholder='Email'>"
        f"<select name='department_id'>{dept_opts}</select>"
        f"<select name='role_id'>{role_opts}</select></div>"
        "<div class='form-actions'><button type='submit'>Crear usuario</button></div>"
        "<div id='user-result'></div></form></div>"
    )


def _device_management(storage: Storage, org_id: str) -> str:
    """Gestión de dispositivos permitidos."""
    # Los devices se almacenan como metadata en la organización
    org = storage.get_organization(org_id)
    devices_str = org.metadata.get("devices", "[]") if hasattr(org, "metadata") and org.metadata else "[]"
    import json
    try:
        devices = json.loads(devices_str) if devices_str != "[]" else []
    except (json.JSONDecodeError, ValueError):
        devices = []

    devices_html = "".join(
        f"<div class='device-item'><span class='device-name'>{_e(d)}</span>"
        f"<div class='device-actions'><span style='color:#8b949e;font-size:.75rem'>"
        f"Creado: {_e(d.get('created_at', ''))}</span></div></div>"
        for d in devices
    ) or "<p class='empty'>Sin dispositivos registrados.</p>"

    return (
        f"<div class='device-list'>"
        "<h4>Dispositivos permitidos</h4>"
        f"<form id='add-device-form' onsubmit='addDevice(event)'>"
        "<div class='form-row'>"
        "<input type='text' name='device_name' placeholder='Nombre del dispositivo (ej: Portatil-Cesar)'>"
        f"<select name='org_id' style='max-width:160px'><option value='{_e(org_id)}'>{_e(org.name)}</option></select>"
        "<button type='submit' class='btn-sm'>Añadir</button></div>"
        "</form>"
        f"<div id='devices-list'>{devices_html}</div></div>"
    )


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


def _version_banner() -> str:
    """Aviso si el proceso no sirve el codigo que hay en el checkout.

    Silencioso cuando todo cuadra: un aviso permanente deja de leerse, y este
    solo existe para el caso que de verdad importa (fb4fdbe5).
    """
    try:
        info = version_report()
    except Exception:  # noqa: BLE001
        return ""
    servido, head = info.get("served_commit"), info.get("checkout_head")
    if info.get("stale") is True:
        return (
            "<div class='banner banner-warn'>Este proceso sirve <code>"
            f"{_e((servido or '?')[:8])}</code> pero el checkout esta en <code>"
            f"{_e((head or '?')[:8])}</code>: hay codigo sin desplegar. "
            "Reinicia el servidor para que sirva el actual.</div>"
        )
    if servido is None or head is None:
        return (
            "<div class='banner banner-info'>No se puede decir que codigo sirve "
            "este proceso: no se encuentra el checkout de git. Sin esto, un fix "
            "puede quedar sin desplegar sin que nadie lo note.</div>"
        )
    return ""


def _priority_label(p: int) -> str:
    labels = {0: "baja", 1: "media", 2: "alta", 3: "crítica"}
    return _html.escape(labels.get(p, str(p)))


def _priority_badge(p: int) -> str:
    colors = {0: "#8b949e", 1: "#58a6ff", 2: "#d29922", 3: "#f85149"}
    color = colors.get(p, "#8b949e")
    label = _priority_label(p)
    return f"<span style='color:{color};font-size:.75rem' title='{label}'>●</span>"


def _ticket_table(tickets: list[Ticket], limit: int = 50) -> str:
    if not tickets:
        return "<p class='empty'>Sin tickets.</p>"
    rows = []
    for ticket in tickets[:limit]:
        actor = ticket.assigned_actor_id or ticket.creator_id or "—"
        branch_cell = _e(ticket.branch[:18]) if ticket.branch else '<span class="empty">—</span>'
        actor_trunc = _e(actor[:15])
        actor_rest = _e(actor[15:]) if len(actor) > 15 else ""
        
        rows.append(
            "<tr>"
            f"<td class='ticket-id' onclick=\"showTicket('{_e(ticket.id)}')\"><code>{_e(ticket.id[:12])}</code></td>"
            f"<td>{_status_badge(ticket.status)}</td>"
            f"<td class='ticket-title' onclick=\"showTicket('{_e(ticket.id)}')\" title='{_e(ticket.title)}'>{_e(ticket.title)}</td>"
            f"<td>{_priority_badge(ticket.priority)}</td>"
            f"<td><code>{actor_trunc}</code>"
            f"<br><span style='color:#8b949e;font-size:.7rem'>{actor_rest}</span></td>"
            f"<td><code>{branch_cell}</code></td>"
            f"<td><span style='color:#8b949e;font-size:.75rem'>{_e(ticket.created_at[:10])}</span></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>ID</th><th>Estado</th><th>Título</th>"
        "<th>Pri.</th><th>Actor</th><th>Branch</th><th>Creado</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _org_section(storage: Storage, org, tickets: list[Ticket], status_filter: str = "") -> str:
    depts = storage.list_departments(org.id)
    repos = storage.list_repositories(org.id)
    by_status = _tickets_by_status(tickets)
    locks = _active_locks(storage, org)
    events = storage.list_events(org.id, limit=15)
    audit = storage.list_audit(org.id, limit=15)

    # Aplicar filtro por estado si existe
    if status_filter:
        filtered = [t for t in tickets if t.status.name == status_filter]
    else:
        filtered = tickets

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
        + _filter_bar(status_filter, tickets)
        + "<h3>Tickets" + (f" — filtrado: <b>{_e(status_filter)}</b>" if status_filter else "") + "</h3>"
        + _ticket_table(filtered)
        + _user_creation_form(storage, org.id)
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


def render_dashboard(storage: Storage, args: list[str] | None = None) -> str:
    """HTML del panel: una sección por organización con filtros."""
    if args is None:
        args = []
    status_filter = _parse_filter(args)
    orgs = storage.list_organizations()
    
    # JavaScript para interactividad
    # Raw string a proposito: las expresiones regulares de JS usan \s y \/, que
    # en una cadena normal de Python son escapes invalidos (SyntaxWarning) y
    # dependen de que Python los deje tal cual.
    _JS = r"""
    <script>
    function extractBody(html) {
        var s=html.indexOf('<header>');
        var e=html.indexOf('</html>');
        if(s>-1&&e>-1) return html.substring(s,e+7);
        return html;
    }
    function cleanModal(html) {
        // Remove <style> blocks and <title> from modal content
        return html.replace(/<style[\s\S]*?<\/style>/gi,'').replace(/<title[\s\S]*?<\/title>/gi,'');
    }
    function showTicket(id) {
        fetch('/ticket/'+id)
            .then(r=>r.text())
            .then(html=>{
                var m=document.getElementById('modal-content');
                document.getElementById('modal-body').innerHTML=cleanModal(extractBody(html));
                m.style.display='flex';
            });
    }
    function closeModal() {
        document.getElementById('modal-content').style.display='none';
    }
    function createUser(ev) {
        ev.preventDefault();
        var f=document.getElementById('create-user-form');
        var fd=new FormData(f);
        var data={name:fd.get('name'),email:fd.get('email'),department_id:fd.get('department_id')||'',role_id:fd.get('role_id')||''};
        fetch('/api/create-user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
            .then(r=>r.json())
            .then(j=>{
                var el=document.getElementById('user-result');
                if(j.success){el.innerHTML='<p style=color:#3fb950>✓ Usuario creado: '+j.data.id+'</p>';f.reset();}
                else{el.innerHTML='<p style=color:#f85149>✗ '+j.error+'</p>';}
            })
            .catch(e=>{document.getElementById('user-result').innerHTML='<p style=color:#f85149>Error: '+e+'</p>'});
    }
    function addDevice(ev) {
        ev.preventDefault();
        var f=document.getElementById('add-device-form');
        var fd=new FormData(f);
        var data={name:fd.get('device_name'),org_id:fd.get('org_id')};
        fetch('/api/add-device',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
            .then(r=>r.json())
            .then(j=>{
                if(j.success){location.reload();}
                else{alert('Error: '+j.error);}
            })
            .catch(e=>alert('Error: '+e));
    }
    function updateTicket(ev) {
        ev.preventDefault();
        var f=document.getElementById('update-ticket-form');
        var fd=new FormData(f);
        var data={id:fd.get('id'),title:fd.get('title'),description:fd.get('description'),priority:fd.get('priority'),result:fd.get('result'),feedback:fd.get('feedback')};
        fetch('/api/update-ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
            .then(r=>r.json())
            .then(j=>{
                var el=document.getElementById('ticket-update-result');
                if(j.success){el.innerHTML='<p style=color:#3fb950>✓ Actualizado: '+j.data.updated.join(', ')+'</p>';setTimeout(()=>location.reload(),1000);}
                else{el.innerHTML='<p style=color:#f85149>✗ '+j.error+'</p>';}
            })
            .catch(e=>{document.getElementById('ticket-update-result').innerHTML='<p style=color:#f85149>Error: '+e+'</p>'});
    }
    function changeStatus(id, status) {
        if(!confirm('Cambiar estado a '+status+'?'))return;
        fetch('/api/update-ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:status})})
            .then(r=>r.json())
            .then(j=>{
                if(j.success){location.reload();}
                else{alert('Error: '+j.error);}
            })
            .catch(e=>alert('Error: '+e));
    }
    function joinTeam(org) {
        var modal=document.getElementById('join-modal');
        var cmdEl=document.getElementById('join-cmd');
        var token=generateToken();
        cmdEl.value='export GEAS_TOKEN="'+token+'" && geas user list "'+org.name+'"';
        modal.style.display='flex';
        document.getElementById('join-token').value=token;
        document.getElementById('join-org-id').value=org.id;
    }
    function copyCmd() {
        var el=document.getElementById('join-cmd');
        el.select();document.execCommand('copy');
        document.getElementById('join-copy-msg').style.display='block';
        setTimeout(()=>document.getElementById('join-copy-msg').style.display='none',2000);
    }
    function generateToken() {
        var c='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
        var t='geas_';
        for(var i=0;i<32;i++) t+=c[Math.floor(Math.random()*c.length)];
        return t;
    }
    function closeModal() {
        document.getElementById('modal-content').style.display='none';
    }
    function closeJoinModal() {
        document.getElementById('join-modal').style.display='none';
    }
    window.onclick=function(ev){
        var m=document.getElementById('modal-content');
        var j=document.getElementById('join-modal');
        if(ev.target==m)closeModal();
        if(ev.target==j)closeJoinModal();
    }
    </script>
    """
    
    # Header con botón "unirse al equipo"
    join_btn = ""
    if orgs:
        # Los valores van como un unico objeto JSON pasado a joinTeam(), no
        # interpolados en literales sueltos: antes org.name se metia tal cual
        # en onclick='joinTeam("id","nombre")', de modo que un nombre con
        # comilla simple escapaba del atributo y uno con </script> ejecutaba
        # JavaScript (XSS almacenado, porque el nombre viene de la BD).
        # json.dumps escapa para JS; _e(quote=True) escapa para el atributo.
        payload = _e(
            _json.dumps({"id": orgs[0].id, "name": orgs[0].name}), quote=True
        )
        join_btn = (
            f"<button id='join-btn' onclick='joinTeam({payload})' "
            "style='background:#58a6ff;color:#0d1117;border:none;padding:.3rem .8rem;"
            "border-radius:6px;cursor:pointer;font-size:.85rem;font-weight:600;margin-left:1rem'>"
            "Unirse al equipo</button>"
        )
    
    if not orgs:
        body = (
            "<header><h1>Geas</h1>"
            "<p>Quién puede hacer qué, sobre qué recurso, cuándo — y qué ha ocurrido.</p></header>"
            + _version_banner()
            + "<p class='empty'>Sin organizaciones todavía. Crea una con "
            "<code>geas init &quot;Mi Org&quot;</code>.</p>"
        )
    else:
        sections = "".join(
            _org_section(storage, org, storage.list_tickets(org.id), status_filter) for org in orgs
        )
        body = (
            "<header><h1>Geas</h1>"
            "<p>Quién puede hacer qué, sobre qué recurso, cuándo — y qué ha ocurrido.</p>"
            + join_btn + "</header>"
            + _version_banner()
            + sections
        )
    
    ticket_modal = (
        "<div id='modal-content' class='modal-overlay' onclick='closeModal()'>"
        "<div class='modal' onclick='event.stopPropagation()'>"
        "<div class='modal-header'><h2>Detalle del ticket</h2>"
        "<span class='close' onclick='closeModal()'>×</span></div>"
        "<div class='modal-body' id='modal-body'></div></div></div>"
    )
    
    join_modal = (
        "<div id='join-modal' class='modal-overlay' onclick='closeJoinModal()'>"
        "<div class='modal' onclick='event.stopPropagation()'>"
        "<div class='modal-header'><h2>Unirse al equipo</h2>"
        "<span class='close' onclick='closeJoinModal()'>×</span></div>"
        "<div class='modal-body'>"
        "<p class='join-info'>Copia y pega este comando en tu terminal para unirte al equipo. "
        "Tu contraseña por defecto es <code>abc123.</code></p>"
        "<div class='join-cmd-box'>"
        "<textarea id='join-cmd' readonly onclick='this.select()'></textarea>"
        "<div style='display:flex;align-items:center'>"
        "<button class='copy-btn' onclick='copyCmd()'>Copiar comando</button>"
        "<span id='join-copy-msg' class='join-copy-msg'>✓ Copiado</span>"
        "</div></div>"
        "<input type='hidden' id='join-token'>"
        "<input type='hidden' id='join-org-id'>"
        "</div></div></div>"
    )
    
    body += (
        "<footer>Panel interactivo · Escritura: UI (usuarios/dispositivos) y API/MCP "
        "(<code>POST /api/tools/&lt;tool&gt;</code> con <code>X-Geas-Actor</code>)</footer>"
    )
    return _page("Geas", _JS + body + ticket_modal + join_modal)


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

    # Formulario de actualización
    status_opts = "".join(
        f"<option value='{s.name}' {'selected' if s.name == ticket.status.name else ''}>{s.name}</option>"
        for s in _STATUS_ORDER
    )
    priority_opts = "".join(
        f"<option value='{p}' {'selected' if p == str(ticket.priority) else ''}>{_e(_PRIORITY.get(int(p), p))}</option>"
        for p in ("0", "1", "2", "3")
    )
    
    update_form = (
        f"<div class='user-form' style='margin-top:1.5rem'>"
        "<h3>Actualizar ticket</h3>"
        "<form id='update-ticket-form' onsubmit='updateTicket(event)'>"
        f"<input type='hidden' name='id' value='{_e(ticket.id)}'>"
        "<div class='form-row'>"
        f"<label>Estado: <select name='status'>{status_opts}</select></label>"
        f"<label>Prioridad: <select name='priority'>{priority_opts}</select></label>"
        "</div>"
        "<div class='form-row'>"
        f"<input type='text' name='title' placeholder='Título' value='{_e(ticket.title)}' style='flex:2'>"
        "</div>"
        "<div class='form-row'>"
        f"<input type='text' name='description' placeholder='Descripción' value='{_e(ticket.description[:100])}' style='flex:3'>"
        "</div>"
        "<div class='form-row'>"
        f"<input type='text' name='result' placeholder='Resultado' value='{_e(ticket.result)}' style='flex:2'>"
        f"<input type='text' name='feedback' placeholder='Feedback' value='{_e(ticket.feedback)}' style='flex:2'>"
        "</div>"
        "<div class='form-actions'><button type='submit'>Actualizar</button>"
        f"<button type='button' class='btn-sm' style='color:#d29922;border-color:#d29922' onclick=\"changeStatus('{ticket.id}','DONE')\">Marcar DONE</button>"
        f"<button type='button' class='btn-sm btn-danger' onclick=\"changeStatus('{ticket.id}','CANCELLED')\">Cancelar</button></div>"
        "<div id='ticket-update-result'></div></form></div>"
    )
    
    body = (
        "<header>"
        f"<p><a href='/'>← Panel</a></p>"
        f"<h1>{_e(ticket.id)} {_status_badge(ticket.status)}</h1>"
        f"<h2 style='border:none'>{_e(ticket.title)}</h2>"
        f"<p class='empty'>{_e(ticket.description)}</p></header>"
        "<div class='ticket-detail'>"
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
        "</div>"
        + update_form
        + "<footer><a href='/'>← Panel</a></footer>"
    )
    return _page(f"Geas · {ticket.id}", body)