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
       └── Users / Agents · Roles + Permissions (RBAC)
            └── Tickets
                 ├── Dependencies (entre tickets)
                 ├── Resources (archivos, APIs, DBs)
                 ├── Locks (TTL + heartbeat)
                 ├── Commits (before / after)
                 └── Executions (modelo, tokens, coste)
                      └── AuditLog + EventLog
```

## Estados del ticket

```
FREE → CLAIMED → IN_PROGRESS → BLOCKED → REVIEW → DONE
                              ↘ CANCELLED
```

## Uso

```bash
# Instalación
uv venv
uv pip install -e ".[dev]"

# CLI
geas init "Mi Org"                # crear organización + roles por defecto
geas org list                     # listar organizaciones
geas dept list <org_id>           # listar departamentos
geas ticket create <org_id> "Título"
geas ticket list <org_id>
geas ticket show <ticket_id>
geas ticket start <ticket_id> <commit_before> <branch>
geas ticket complete <ticket_id> <commit_after>
geas resource list <repo_id>      # ver recursos y sus locks
geas lock show <resource_id>      # ver lock de un recurso
geas events <org_id>              # event log
geas audit <org_id>               # log de auditoría
```

## Concurrencia

Un recurso (archivo, dirección, API...) puede estar bloqueado por un ticket:

- **Agent A** → YT-104 → `Chat.py` → **LOCKED**
- **Agent B** → YT-105 → `Chat.py` → **RESOURCE_UNAVAILABLE** (`locked_by: YT-104`)

Al terminar A (commit registrado + lock liberado), B puede continuar.

## Tests

```bash
uv run pytest tests/ -q
```

## Roadmap

- **MVP 1** (actual): CLI · SQLite · Tickets · Dependencias · Recursos · Locks · Git (commit_before/after)
- **MVP 2**: PostgreSQL · Users/Agents · Roles · Permisos · Web UI · Audit · Event Log · GitHub/GitLab
- **MVP 3**: Agent Harness · Agent Runner · Multi-provider · Model traceability · Worktrees · Copier
- **Enterprise**: SSO/OIDC · Advanced RBAC · Observability · Multi-organization