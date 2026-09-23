# GEAS

Plataforma de coordinación del trabajo de desarrolladores humanos y agentes
de IA dentro de una organización.

GEAS se sitúa **por encima de Git y de los sistemas de ejecución de agentes**:
no sustituye Git, CI/CD, Docker ni agent harness. Su función es responder, de
forma **atómica y verificable**, a una sola pregunta:

> **Quién puede hacer qué, sobre qué recurso, cuándo puede hacerlo y qué dependencias existen.**

## Cómo funciona, en 30 segundos

```
1. DECLARAS  → un árbol de texto versionado en Git describe tu organización
               (nombre, perfil, departamentos, repos, roles) — nunca toca la BD
2. SINCRONIZAS → `geas sync` aplica esa declaración a la base de datos
3. TRABAJAS  → humanos y agentes reclaman tickets (`geas work start`),
               con locks atómicos que impiden editar el mismo recurso a la vez
```

La **base de datos** es la única fuente de verdad del estado operativo
(tickets, locks, ejecuciones). Lo que está en Git es solo la **configuración
deseada**. Los dos nunca se mezclan.

## Quickstart

```bash
# 1. Instalar (desde el repo — ver "Instalación")
uv tool install --editable .        # o: pipx install .

# 2. Crear la organización (perfil por defecto: individual)
geas init "Mi Org"

# 3. Registrar un repositorio de trabajo
cd mi-repo
geas work init .

# 4. Crear un ticket y ver quién puede cogerlo
geas ticket create <org_id> "Rediseñar el header"
geas work tasks <actor_id>

# 5. Reclamarlo, trabajar y cerrarlo
geas work start <ticket_id>         # claim atómico + rama
geas work finish <ticket_id>        # commit + libera locks
```

La guía paso a paso está en [`docs/INSTALL.md`](docs/INSTALL.md).

## Conceptos clave

| Concepto | Qué es |
|----------|--------|
| **Organización** | Un equipo (o persona) con un perfil de despliegue: Individual, Team o Enterprise |
| **Árbol declarativo** | Ficheros YAML versionados (`organization.yml`, `departments/`, `roles/`...) que describen la org deseada |
| **Ticket** | Una unidad de trabajo con dependencias, recursos y trazabilidad |
| **Recurso + Lock** | Un archivo, API o BD que puede estar bloqueado por un ticket (TTL + heartbeat) |
| **Permisos (RBAC)** | Catálogo de permisos (§7); cada rol declara cuáles puede ejercer |
| **MCP** | Herramientas para que agentes de IA consulten tickets, permisos y contexto |

### Estados del ticket

El claim de trabajo es **atómico** (`UPDATE ... WHERE status='FREE'`, §39):
dos agentes concurrentes no pueden reclamar el mismo ticket.

```
FREE → IN_PROGRESS → BLOCKED → REVIEW → DONE    (claim atómico §39)
FREE → CLAIMED                                  (reserva ligera)
                                    ↘ CANCELLED
```

## Concurrencia

Un recurso (archivo, dirección, API...) puede estar bloqueado por un ticket:

- **Agent A** → YT-104 → `Chat.py` → **LOCKED**
- **Agent B** → YT-105 → `Chat.py` → **RESOURCE_UNAVAILABLE** (`locked_by: YT-104`)

Al terminar A (commit registrado + lock liberado), B puede continuar.
Hay una demo ejecutable en `examples/simulacion/`:

```bash
python3 examples/simulacion/demo_concurrencia.py
```

El claim de un ticket es atómico: dos pipelines que reclaman el mismo ticket
no compiten por programación, compiten por la guardia de la BD. Los pipelines
hacen consultas ligeras (`geas sync .`); el claim vive siempre en GEAS.
El detalle está en [`docs/INSTALL.md`](docs/INSTALL.md) §5.

## Ejemplo de uso: una hackatón con 4 personas (y 4 LLMs)

Cuatro personas comparten el MISMO repositorio durante una hackatón. Cada
una trabaja con su propio LLM. Sin coordinación, dos agentes acaban tocando
`chat.py` a la vez: uno commitea la v2, el otro sigue en la v1, sincroniza…
y peta. No es un problema de git: es un problema de *quién toca qué y
cuándo*.

Con GEAS el flujo es:

```bash
# 1. Montar el proyecto
geas init "Hackatón 2026"              # org + roles por defecto
cd mi-repo && geas work init .         # registrar el repo de trabajo

# 2. Registrar personas y LLMs
geas user create <org_id> "Ana" ana@example.com
geas agent create <org_id> "Codex" openai gpt-5
geas agent create <org_id> "Claude" anthropic claude-4

# 3. Crear tickets (cada uno declara sus recursos: archivos, APIs, BDs)
geas ticket create <org_id> "Rediseñar chat.py" "UI + streaming"
geas ticket create <org_id> "Refactor de auth"   "sessiones + tokens"

# 4. ANTES de tocar: preguntar qué se puede coger
geas work tasks <codex_id>
#   → tickets FREE, con recursos desbloqueados y dependencias resueltas.
#     Si chat.py ya está bloqueado por otro ticket, ese ticket no sale:
#     te devuelve los libres que NO tocan ese archivo.

# 5. Empezar: `work start` valida recursos y bloquea los del ticket en la BD
geas work start <ticket_id>            # claim atómico + locks adquiridos
# ... trabajas ...
geas work finish <ticket_id>           # commit registrado + locks liberados
```

La base de datos registra **qué es cada ticket, qué archivos bloquea, quién
lo hace, qué LLM lo ejecutó y los commits antes/después** — si algo sale
mal, `geas work rollback` revierte con el versionado de git. El claim es
atómico y los tickets se ordenan por **prioridad** (los que bloquean a más
tickets se atienden antes, §15). Todo el flujo es robusto ante concurrencia
también desde CI/CD: el runner hace consultas ligeras (`geas sync .`) y el
claim vive en la BD, no en la programación del pipeline.

## Perfiles de despliegue (§43)

Un único GEAS modular: el perfil elige qué componentes se activan.

| Perfil | Alias | Componentes |
|--------|-------|-------------|
| **Individual** | `1` / `MINIMAL` | Organización · Git integration · SQLite |
| **Team** | `2` | Individual + Agent coordination · Roles · RBAC · Locks · Web UI · MCP |
| **Enterprise** | `3` | Team + Departments (jerárquicos) · Policies · SSO/OIDC · Observability · Governance |

El perfil se guarda en `organizations.profile`; `geas sync` sincroniza solo
las secciones declarativas que el perfil soporta — una estructura Enterprise
ante un perfil Individual se rechaza con error claro **antes** de tocar la BD.
`backend_for(profile)` declara el backend: TEAM/ENTERPRISE piden PostgreSQL
(§42); el driver psycopg es deuda de infra anotada — esta build sigue sobre
SQLite con *fail-fast*, nunca cae en silencio.

## Estructura declarativa

```
organization.yml                    nombre, descripción, perfil
departments/<slug>/department.yml   departamento (parent opcional)
departments/<slug>/repositories/    repos de un departamento
repositories/<slug>.yml             repos de organización
roles/<slug>.yml                    rol declarativo (§7)
policies/<slug>.yml                 política declarativa
.github/workflows/geas-sync.yml     CI: geas sync .
```

```bash
geas enterprise init --org "Google" --departments YouTube,Gmail \
  --repos "YouTube:backend,frontend;core-lib"   # genera el árbol
geas init "Google" --profile enterprise         # org con el perfil
geas sync .                                     # aplica el árbol a la BD (idempotente)
```

`geas sync` es idempotente: crea lo que falta, converge description/perfil/
permisos y deja intacto lo que ya está. Un re-sync no cuenta cambios que ya
ocurrieron.

## Referencia de comandos

### Organización, departamentos, personas y roles

```bash
geas init "Mi Org"                # crear organización + roles por defecto (perfil: individual)
geas init "Mi Org" --profile team # perfil Team (o 2); --individual/--team/--enterprise/1/2/3
geas org list                     # listar organizaciones
geas org show <org_id>            # perfil, componentes y conteos (§43)
geas dept list <org_id>           # listar departamentos
geas dept create <org_id> "Backend"
geas user create <org_id> "Ana" ana@example.com
geas agent create <org_id> "Codex" openai gpt-5
geas role list <org_id>                    # roles y sus permisos
geas role create <org_id> "revisor" ticket:read ticket:update
geas role grant <org_id> <role_id> git:pr  # concede un permiso (idempotente)
geas permission list                       # catálogo completo de permisos (§7)
```

### Tickets, recursos y locks

```bash
geas repo create <org_id> "mi-repo" github https://github.com/org/mi-repo
geas ticket create <org_id> "Título"
geas ticket list <org_id>            # [free|busy] → badge de ocupación (§15)
geas ticket show <ticket_id>
geas ticket start <ticket_id> [commit_before] [branch]
geas ticket complete <ticket_id> [commit_after]
geas resource list <repo_id>         # recursos y sus locks
geas lock show <resource_id>         # lock de un recurso
geas events <org_id>                 # event log
geas audit <org_id>                  # log de auditoría
```

### Trabajo desde un repositorio registrado

```bash
geas work init .                  # registrar repo y crear .geas.yml
geas work status .                # ¿el entorno está listo?
geas work sync .                  # estado de Git y tickets
geas work tasks <actor_id>        # tickets disponibles para un actor
geas work start <ticket_id>       # reclamar e iniciar
geas work finish <ticket_id>      # registrar commit y liberar locks
geas work diff <ticket_id>        # cambios entre los commits del ticket
geas work rollback <ticket_id>    # revertir el trabajo de un ticket (§11)

# Branches y PRs por ticket (§34)
geas work branch create <ticket_id> [path]
geas work commit <ticket_id> [path] [msg]
geas work pr create <ticket_id> [path]       # push + PR (gh/glab)
```

### Herramientas MCP para agentes

```bash
geas mcp list
geas mcp create_ticket --title "Implementar X"
geas mcp get_available_tasks
geas mcp get_organization          # perfil, componentes, backend (§43)
geas mcp list_departments
geas mcp get_department --name YouTube
geas mcp list_repositories
geas mcp get_permissions           # catálogo §7
```

### Panel web (solo lectura)

```bash
geas serve                       # http://127.0.0.1:8787/ → dashboard
# http://127.0.0.1:8787/ticket/<id> → detalle con trazabilidad §29
```

## Proveedores Git

La mitad local (fetch/pull/status/branch/commit/push/diff/rollback) la
resuelve el CLI de git. La mitad remota (PR, merge, commits) se delega
al proveedor por API REST — stdlib pura, sin dependencias:

```bash
GEAS_GITHUB_TOKEN=ghp_...     geas ...   # GitHub  → REST api.github.com
GEAS_GITLAB_TOKEN=glpat-...   geas ...   # GitLab  → REST gitlab.com/api/v4
GEAS_BITBUCKET_TOKEN=user:app geas ...   # Bitbucket → REST API 2.0 (Basic)
```

Sin token, el proveedor remoto falla claro — **nunca** degrada en silencio
al proveedor local (§42: los contratos no se mienten).

## Instalación

GEAS es un paquete Python con entry point `geas` (stdlib pura, cero
dependencias de runtime). **Se instala desde el repositorio**, no desde
PyPI: el nombre `geas` en PyPI pertenece a otro proyecto no relacionado
(⚠️ `pipx install geas` descargaría ese paquete, no este).

```bash
git clone https://github.com/cacelass/geas && cd geas

# comando global aislado (cualquiera de las dos)
uv tool install .               # o: pipx install .
uv tool install --editable .    # editable, para desarrollo
```

O desde el repo en desarrollo:

```bash
uv venv && uv pip install -e ".[dev]"
```

## Tests

```bash
uv run pytest tests/ -q
```

## Roadmap

- **MVP 1** — ✅ hecho: CLI · SQLite · Tickets · Dependencias · Recursos · Locks · Git (commit_before/after)
- **MVP 2** — ✅ hecho (driver PostgreSQL pendiente, §42): Users/Agents · Roles · Permisos · Web UI · Audit · Event Log · Git/GitHub
- **MVP 3** — 🟡 parcial: Agent Harness y Runner con worktrees (implementados) · Multi-provider y Copier (pendientes)
- **Perfiles (§43)** — ✅ implementado: Individual/Team/Enterprise, estructura declarativa + `geas sync` + MCP de contexto + gates de perfil en la CLI
- **Enterprise (pendiente)**: SSO/OIDC · Advanced RBAC · enforcement de Policies · Observability · driver PostgreSQL (§42)