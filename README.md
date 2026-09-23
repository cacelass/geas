# Geas

Plataforma de coordinación del trabajo de desarrolladores humanos y agentes
de IA dentro de una organización.

Geas se sitúa **por encima de Git y de los sistemas de ejecución de agentes**.
No sustituye Git, CI/CD, Docker, sistemas de secretos ni agent harness.
Su función es responder:

> **Quién puede hacer qué, sobre qué recurso, cuándo puede hacerlo y qué dependencias existen.**

## Modelo de datos

```
Organization
  └── Departments (jerárquicos)
       └── Users / Agents · Roles + Permissions (RBAC) · Policies
            └── Tickets
                 ├── Dependencies (entre tickets)
                 ├── Resources (archivos, APIs, DBs)
                 ├── Locks (TTL + heartbeat)
                 ├── Commits (before / after)
                 └── Executions (modelo, tokens, coste)
                      └── AuditLog + EventLog
```

## Estados del ticket

El claim de trabajo es **atómico** (`UPDATE ... WHERE status='FREE'`,
§39) — dos agentes concurrentes no pueden reclamar el mismo ticket:

```
FREE → IN_PROGRESS → BLOCKED → REVIEW → DONE     (claim atómico §39)
FREE → CLAIMED                                   (reserva ligera assign_ticket)
                                      ↘ CANCELLED
```

## Perfiles de despliegue (§43)

Un único Geas modular: el perfil elige qué componentes se activan.

| Perfil | Alias | Componentes |
|--------|-------|-------------|
| **Individual** | `1` / `MINIMAL` | Organización · Git integration · SQLite |
| **Team** | `2` | Individual + Agent coordination · Roles · RBAC · Locks (conflict-free) · Web UI · MCP |
| **Enterprise** | `3` | Team + Departments (jerárquicos) · Policies · SSO/OIDC · Observability · Governance |

El perfil se guarda en `organizations.profile`; `geas sync` sincroniza solo
las secciones declarativas que el perfil soporta — una estructura Enterprise
ante un perfil Individual se rechaza con error claro antes de tocar la base
de datos. `backend_for(profile)` declara el backend: TEAM/ENTERPRISE piden
PostgreSQL (§42); el driver psycopg es deuda de infra anotada y esta build
sigue sobre SQLite con fail-fast (nunca cae en silencio).

## Estructura declarativa Enterprise (§43)

El árbol versionado en Git es el estado **deseado**; la base de datos de
Geas es la única fuente de verdad del estado **operativo** (tickets, locks,
ejecuciones — nunca se escriben en el árbol):

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

## Instalación

GEAS es un paquete Python con entry point `geas` (stdlib pura, cero
dependencias de runtime):

```bash
pipx install geas          # o: uv tool install geas
```

O desde el repo en desarrollo:

```bash
git clone <repo> && cd geas
uv venv && uv pip install -e ".[dev]"
```

La **guía completa** — perfiles, inclusión en un repositorio, prompt de
instalación para tu asistente y cómo encaja la concurrencia — está en
[`docs/INSTALL.md`](docs/INSTALL.md).

## Uso

```bash
# Instalación
uv venv
uv pip install -e ".[dev]"

# CLI
geas init "Mi Org"                # crear organización + roles por defecto (perfil: individual)
geas init "Mi Org" --profile team # perfil Team (o 2); --individual/--team/--enterprise/1/2/3
geas org list                     # listar organizaciones
geas org show <org_id>            # perfil, componentes y conteos (§43)
geas dept list <org_id>           # listar departamentos
geas dept create <org_id> "Backend"
geas user create <org_id> "Ana" ana@example.com
geas user create <org_id> "Ana" ana@example.com <dept_id> <role_id>   # rol opcional
geas agent create <org_id> "Codex" openai gpt-5
geas agent create <org_id> "Codex" openai gpt-5 <dept_id> <role_id>   # rol opcional
geas role list <org_id>                    # roles y sus permisos
geas role create <org_id> "revisor" ticket:read ticket:update   # valida contra el catálogo §7
geas role grant <org_id> <role_id> git:pr  # concede un permiso (idempotente)
geas permission list                       # catálogo completo de permisos (§7)
geas repo create <org_id> "mi-repo" github https://github.com/org/mi-repo
geas ticket create <org_id> "Título"
geas ticket list <org_id>              # [free|busy] → " [libre]" / " [ocupado desde <ts> por <actor>]" (§15)
geas ticket list <org_id> free         # solo tickets sin locks vivos (§15: todos sus archivos disponibles)
geas ticket list <org_id> busy         # solo ocupados: badge "desde <created_at> por <actor>"
geas ticket show <ticket_id>
geas ticket start <ticket_id> <commit_before> <branch>
geas ticket complete <ticket_id> <commit_after>
geas resource list <repo_id>      # ver recursos y sus locks
geas lock show <resource_id>      # ver lock de un recurso
geas events <org_id>              # event log
geas audit <org_id>               # log de auditoría

# Trabajo desde un repositorio registrado
geas work init .                  # registrar repo y crear .geas.yml
geas work status .                # comprobar que el entorno está listo
geas work sync .                  # estado de Git y tickets del repositorio
geas work tasks <actor_id>        # tickets disponibles para un actor
geas work start <ticket_id>       # reclamar e iniciar un ticket
geas work finish <ticket_id>      # registrar commit y liberar locks
geas work diff <ticket_id>        # cambios entre los commits del ticket
geas work rollback <ticket_id>    # revertir el trabajo de un ticket (§11)

# Branches y PRs por ticket (§34)
geas work branch create <ticket_id> [path]   # branch geas/<id> + BRANCH_CREATED
geas work commit <ticket_id> [path] [msg]    # commit en la branch + §30
geas work pr create <ticket_id> [path]       # push + PR (gh/glab) + PR_CREATED

# Herramientas MCP para agentes
geas mcp list
geas mcp create_ticket --title "Implementar X"
geas mcp get_available_tasks
geas mcp get_organization          # perfil, componentes, backend (§43)
geas mcp list_departments          # estructura declarada
geas mcp get_department --name YouTube
geas mcp list_repositories
geas mcp get_permissions           # catálogo §7

# Panel web de estado (§42, solo lectura)
geas serve                       # http://127.0.0.1:8787/ → dashboard
# http://127.0.0.1:8787/ticket/<id> → detalle con trazabilidad §29
```

## Proveedores Git (§18/§42)

La mitad local (fetch/pull/status/branch/commit/push/diff/rollback) la
resuelve el CLI de git. La mitad remota (PR, merge, commits) se delega
al proveedor por API REST — stdlib pura, sin dependencias:

```bash
GEAS_GITHUB_TOKEN=ghp_...     geas ...   # GitHub  → REST api.github.com
GEAS_GITLAB_TOKEN=glpat-...   geas ...   # GitLab  → REST gitlab.com/api/v4
GEAS_BITBUCKET_TOKEN=user:app geas ...   # Bitbucket → REST API 2.0 (Basic)
```

Sin token, el proveedor remoto falla claro — **nunca** degrada en
silencio al proveedor local (§42: los contratos no se mienten).
`get_provider("local"|"self-hosted")` usa el CLI; un nombre desconocido
también falla claro.

## Concurrencia

Un recurso (archivo, dirección, API...) puede estar bloqueado por un ticket:

- **Agent A** → YT-104 → `Chat.py` → **LOCKED**
- **Agent B** → YT-105 → `Chat.py` → **RESOURCE_UNAVAILABLE** (`locked_by: YT-104`)

Al terminar A (commit registrado + lock liberado), B puede continuar.

El claim de un ticket es **atómico** (`UPDATE ... WHERE status='FREE'`):
dos pipelines que reclaman el mismo ticket no compiten por programación,
compiten por la guardia de la BD. Consultas ligeras en CI/CD, claim en
GEAS. El patrón completo — BD local vs `.geas.yml`, CI/CD por rama/PR,
onboarding de agentes — está en [`docs/INSTALL.md`](docs/INSTALL.md) §5.

## Tests

```bash
uv run pytest tests/ -q
```

## Roadmap

- **MVP 1** (actual): CLI · SQLite · Tickets · Dependencias · Recursos · Locks · Git (commit_before/after)
- **MVP 2**: PostgreSQL · Users/Agents · Roles · Permisos · Web UI · Audit · Event Log · GitHub/GitLab
- **MVP 3**: Agent Harness · Agent Runner · Multi-provider · Model traceability · Worktrees · Copier
- **Perfiles (§43)**: Individual/Team/Enterprise — estructura declarativa + `geas sync` + MCP de contexto (implementado)
- **Enterprise**: SSO/OIDC · Advanced RBAC · enforcement de Policies · Observability · driver PostgreSQL (§42)
