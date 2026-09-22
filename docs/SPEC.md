esta todo? # ORCHESTRATOR

## 1. Concepto

**Orchestrator** es una plataforma de coordinación del trabajo de desarrolladores humanos y agentes de IA dentro de una organización.

Su función es coordinar:

* personas;
* agentes de IA;
* tickets;
* departamentos;
* repositorios;
* dependencias;
* recursos compartidos;
* locks;
* permisos;
* Git;
* ejecuciones;
* trazabilidad;
* auditoría.

Orchestrator se sitúa **por encima de Git y de los sistemas de ejecución de agentes**.

No pretende sustituir:

* Git;
* GitHub/GitLab;
* CI/CD;
* Docker/Kubernetes;
* sistemas de secretos;
* sistemas de identidad;
* bases de datos;
* Agent Harness.

Su función principal es responder:

> **Quién puede hacer qué, sobre qué recurso, cuándo puede hacerlo y qué dependencias existen.**

---

# 2. Arquitectura general

```text
                         ORGANIZATION
                              │
             ┌────────────────┼────────────────┐
             │                │                │
        Department A     Department B     Department C
             │                │                │
        Repositories      Repositories      Repositories
             │                │                │
           Tickets          Tickets          Tickets
             │                │                │
        ┌────┴────┐      ┌────┴────┐      ┌────┴────┐
        │ Humans  │      │ Humans  │      │ Agents  │
        │ Agents  │      │ Agents  │      │ Humans  │
        └────┬────┘      └────┬────┘      └────┬────┘
             │                │                │
             └────────────────┼────────────────┘
                              │
                         ORCHESTRATOR
                              │
                  ┌───────────┼───────────┐
                  │           │           │
                API/MCP      Git       Database
                  │           │           │
                  │        GitHub      PostgreSQL
                  │        GitLab
                  │
                  ▼
             AGENT HARNESS
                  │
        ┌─────────┼─────────┐
        │         │         │
      Agent     CI/CD    Runtime
```

---

# 3. Separación Orchestrator / Agent Harness

Esta separación es fundamental.

## Orchestrator

Es el **cerebro organizativo**.

Gestiona:

```text
Tickets
Dependencies
Resources
Locks
Users
Agents
Roles
Permissions
Departments
Repositories
Audit
Events
Executions
Model traceability
Git metadata
```

## Agent Harness

Es el **entorno de ejecución del agente**.

Gestiona:

```text
Agent lifecycle
Worktree
Container
Sandbox
Credentials
Secrets
Network access
Database access
CI/CD
Tests
Runtime
Process isolation
Cleanup
```

El Orchestrator decide:

> "Este agente puede ejecutar este ticket."

El Harness decide:

> "Este es el entorno concreto en el que ese agente puede ejecutarlo y estos son sus permisos efectivos."

---

# 4. Organización

Entidad raíz:

```text
Organization
- id
- name
- description
- created_at
- active
```

Una organización contiene:

```text
Organization
├── Departments
├── Users
├── Agents
├── Roles
├── Permissions
├── Repositories
├── Tickets
├── Resources
├── Executions
└── Audit Events
```

---

# 5. Departamentos

```text
Department
- id
- organization_id
- parent_department_id
- name
- description
- created_at
- active
```

Permite estructuras jerárquicas:

```text
Organization
└── Google
    ├── YouTube
    │   ├── Backend
    │   └── Frontend
    ├── Gmail
    ├── Chrome
    └── Photos
```

Los departamentos permiten establecer límites organizativos y de acceso.

---

# 6. Usuarios y agentes

Todos los participantes del sistema se representan mediante:

```text
Actor
```

Tipos:

```text
HUMAN
AI_AGENT
SERVICE_ACCOUNT
```

## User

```text
User
- id
- name
- email
- department_id
- role_id
- active
- created_at
```

## Agent

```text
Agent
- id
- name
- provider
- model
- model_version
- department_id
- harness_id
- active
- configuration
- created_at
```

El Agent no tiene necesariamente acceso directo a toda la infraestructura.

Su acceso efectivo será proporcionado por el Harness.

---

# 7. Roles y permisos

Orchestrator mantiene el modelo de autorización organizativa.

Roles iniciales:

```text
developer
manager
admin
agent
service_account
```

Permisos:

```text
ticket:create
ticket:read
ticket:update
ticket:assign
ticket:start
ticket:block
ticket:complete

resource:read
resource:lock
resource:unlock

repository:read
repository:write

department:read
department:manage

user:manage
permission:manage

dependency:create
dependency:update

git:branch
git:commit
git:push
git:pr
git:merge

audit:read
execution:read
```

## Importante

Orchestrator define:

```text
POLICY
```

pero no pretende sustituir los mecanismos reales de seguridad de infraestructura.

Ejemplo:

```text
Orchestrator
    ↓
"Agent-03 puede modificar repository-X"
    ↓
Harness
    ↓
credenciales reales
    ↓
Git / DB / APIs
```

---

# 8. Tickets

El ticket es la unidad principal de trabajo.

```text
Ticket
- id
- title
- description

- organization_id
- department_id
- repository_id

- creator_id
- assigned_actor_id

- created_at
- started_at
- completed_at

- status
- priority

- files
- resources

- dependencies
- blocked_tickets

- result
- feedback

- branch

- commit_before
- commit_after
```

---

# 9. Estados del ticket

```text
FREE
CLAIMED
IN_PROGRESS
BLOCKED
REVIEW
DONE
CANCELLED
```

Flujo:

```text
FREE
  │
  ▼
CLAIMED
  │
  ▼
IN_PROGRESS
  │
  ├──────────────► BLOCKED
  │                    │
  │                    ▼
  │                IN_PROGRESS
  │
  ▼
REVIEW
  │
  ▼
DONE
```

Todas las transiciones son controladas por Orchestrator.

---

# 10. Commit antes y después del ticket

Cada ticket registra dos referencias Git:

```text
commit_before
commit_after
```

## commit_before

Commit que representa el estado del repositorio **antes de comenzar el trabajo**.

Se registra cuando el ticket pasa a ejecución.

## commit_after

Commit correspondiente al estado producido al finalizar el trabajo.

Ejemplo:

```text
Ticket: YT-104

commit_before:
a82f91c

commit_after:
b71c4de
```

Esto permite:

* conocer el estado inicial;
* conocer el estado final;
* calcular el diff;
* auditar modificaciones;
* identificar exactamente los cambios;
* facilitar rollback;
* reconstruir el trabajo realizado.

El historial real del código continúa estando en Git.

---

# 11. Rollback

El sistema puede obtener:

```text
diff(commit_before, commit_after)
```

y utilizar `commit_before` como referencia para recuperar el estado anterior.

Ejemplo:

```text
commit_before
      │
      ▼
   trabajo
      │
      ▼
commit_after
      │
      ▼
   resultado
```

Si posteriormente se necesita revertir el trabajo:

```text
commit_after
      │
      ▼
rollback
      │
      ▼
commit_before
```

El Orchestrator registra el rollback como un nuevo evento de auditoría.

No se elimina el historial anterior.

---

# 12. Recursos

Un ticket puede declarar recursos necesarios:

```text
Resource
- id
- repository_id
- path
- type
- metadata
```

Ejemplos:

```text
Chat.py
src/auth/
database/users
API /users
model.pkl
config.yaml
```

Un recurso puede estar bloqueado por un ticket.

---

# 13. Gestión de concurrencia

La coordinación de concurrencia a nivel organizativo pertenece a Orchestrator.

Ejemplo:

```text
Agent A
   │
   ▼
YT-104
   │
   ▼
Chat.py
   │
   ▼
LOCKED
```

Otro agente:

```text
Agent B
   │
   ▼
YT-105
   │
   ▼
Chat.py
```

recibe:

```text
RESOURCE_UNAVAILABLE

locked_by: YT-104
```

El segundo agente no debe comenzar el trabajo sobre ese recurso.

---

# 14. Locks

```text
ResourceLock
- id
- resource_id
- ticket_id
- actor_id
- created_at
- expires_at
- last_heartbeat
```

Los locks tienen:

* TTL;
* heartbeat;
* renovación;
* liberación automática;
* liberación manual por administrador.

Si un agente desaparece:

```text
Agent dies
   ↓
heartbeat timeout
   ↓
lock expires
   ↓
resource available
```

En TEAM/ENTERPRISE los locks se gestionan mediante operaciones transaccionales en PostgreSQL.

Git no se utiliza como base de datos de locking.

---

# 15. Dependencias

Un ticket puede depender de otros tickets.

```text
YT-042
   │
   └── depends_on
             ↓
         GMAIL-118
```

Mientras `GMAIL-118` no esté terminado:

```text
YT-042 = BLOCKED
```

Al completarse:

```text
GMAIL-118 = DONE
        ↓
YT-042 = IN_PROGRESS
```

Las dependencias pueden cruzar departamentos.

---

# 16. Comunicación entre departamentos

Los departamentos no necesitan acceder directamente al código de otros departamentos.

La comunicación se realiza mediante tickets.

```text
YouTube
   │
   ▼
YT-042
   │
   │ dependency
   ▼
Gmail
   │
   ▼
GMAIL-118
   │
   ▼
resultado
   │
   ▼
YT-042 continúa
```

Esto permite colaboración entre departamentos manteniendo aislamiento del código.

---

# 17. Available Tasks

Cada actor puede consultar las tareas que tiene autorización para ejecutar.

```text
work tasks
```

Ejemplo:

```text
YT-104
Status: FREE
Permissions: OK
Dependencies: RESOLVED
Resources: AVAILABLE
Repository: SYNCHRONIZED

READY
```

Un ticket que no puede ejecutarse no debe aparecer como disponible.

---

# 18. Git

Orchestrator no reemplaza Git.

Integra proveedores mediante una interfaz común:

```text
GitProvider
├── GitHub
├── GitLab
├── Bitbucket
└── Self-hosted Git
```

Operaciones:

```text
fetch()
pull()
status()
create_branch()
commit()
push()
create_pr()
merge()
get_commits()
```

---

# 19. Git Worktree

Cuando varios agentes trabajan simultáneamente sobre el mismo repositorio, el Harness puede utilizar worktrees:

```text
repository
│
├── worktree-agent-01
├── worktree-agent-02
└── worktree-agent-03
```

Esto proporciona aislamiento del filesystem.

Orchestrator mantiene la coordinación lógica mediante tickets y locks.

---

# 20. CI/CD

**CI/CD no forma parte del núcleo de Orchestrator.**

El CI/CD ejecuta:

```text
commit
 ↓
lint
 ↓
tests
 ↓
security checks
 ↓
build
 ↓
PR
 ↓
merge
```

El Harness puede integrarse con el CI/CD existente.

Orchestrator recibe los resultados:

```text
TestResult
- id
- ticket_id
- commit_id
- pipeline_id
- status
- started_at
- finished_at
- logs_reference
```

Orchestrator conoce el resultado, pero no necesita convertirse en otro sistema CI/CD.

---

# 21. Agent Harness

El Harness es responsable del entorno de ejecución.

```text
Agent Harness
├── Agent lifecycle
├── Worktree
├── Container / Sandbox
├── Credentials
├── Secrets
├── Network policy
├── Database access
├── Git access
├── CI/CD
├── Tests
├── Resource limits
└── Cleanup
```

Flujo:

```text
Orchestrator
     │
     │ execution request
     ▼
Harness
     │
     ├── create environment
     ├── configure identity
     ├── configure credentials
     ├── create worktree
     ├── launch agent
     ├── monitor
     ├── execute tests
     └── cleanup
```

---

# 22. Permisos reales de infraestructura

Orchestrator no debe ser el único mecanismo de seguridad.

Ejemplo:

```text
User
 ↓
Orchestrator RBAC
 ↓
Agent authorized?
 ↓
Harness identity
 ↓
Infrastructure permissions
 ↓
Database / Git / API
```

Si un agente no tiene permisos reales sobre una base de datos:

```text
DB
 ↓
DENY
```

aunque Orchestrator tenga registrada una política que permita ejecutar el ticket.

La seguridad efectiva debe estar aplicada en la infraestructura.

---

# 23. Acceso a bases de datos

El Harness gestiona las credenciales y permisos efectivos.

Ejemplo:

```text
Agent-03
   ↓
Harness identity
   ↓
Database role
   ↓
permissions
```

Orchestrator registra la política organizativa:

```text
Agent-03
can execute
Ticket YT-104
```

pero no almacena necesariamente las credenciales de la base de datos ni sustituye su sistema de autorización.

---

# 24. Sincronización

Antes de trabajar:

```text
Git sync
     ↓
Orchestrator sync
     ↓
check ticket
     ↓
check permissions
     ↓
check dependencies
     ↓
check resources
     ↓
acquire locks
     ↓
record commit_before
     ↓
create branch/worktree
     ↓
START
```

Después:

```text
tests
 ↓
commit
 ↓
push
 ↓
record commit_after
 ↓
PR
 ↓
CI/CD
 ↓
review
 ↓
merge
 ↓
release locks
 ↓
DONE
```

---

# 25. status.sh

Cada repositorio integrado puede disponer de:

```text
status.sh
sync.sh
start.sh
finish.sh
```

`status.sh` comprueba:

```text
Git repository       ✓
Remote               ✓
Local branch         ✓
Git synchronization  ✓
Orchestrator         ✓
MCP                  ✓
Ticket               ✓
Dependencies         ✓
Resources            ✓
Permissions          ✓
```

Resultado:

```text
READY TO WORK
```

o:

```text
NOT READY

Reason:
Chat.py locked by YT-104
```

Modo para agentes:

```bash
./status.sh --json
```

Regla:

```text
NOT READY → NO WORK
READY     → WORK ALLOWED
```

---

# 26. MCP

MCP es la interfaz estándar entre los agentes y Orchestrator.

Herramientas:

```text
get_available_tasks()

get_ticket(ticket_id)

create_ticket(...)

update_ticket(...)

start_ticket(ticket_id)

block_ticket(ticket_id)

complete_ticket(ticket_id)

get_dependencies(ticket_id)

lock_resource(resource_id)

release_resource(resource_id)

sync()

get_repository_context(repository_id)

report_commit(...)

report_test_result(...)

get_execution(ticket_id)
```

Arquitectura:

```text
AI Agent
   ↓
MCP
   ↓
Orchestrator API
   ↓
RBAC
   ↓
Database
```

Los agentes no acceden directamente a PostgreSQL.

---

# 27. Agent Runner

El Harness puede utilizar distintos agentes:

```text
Codex
Claude Code
OpenCode
Aider
Copilot
otros
```

Orchestrator registra la ejecución, mientras que el Harness controla el proceso.

```text
Orchestrator
      ↓
Execution
      ↓
Harness
      ↓
Agent
      ↓
Worktree
      ↓
Tests
      ↓
Commit
```

---

# 28. Model Traceability

Cada ejecución registra:

```text
Execution
- id
- ticket_id
- actor_id
- harness_id

- provider
- model
- model_version

- started_at
- finished_at

- tokens_input
- tokens_output
- cost

- tools_used
- iterations

- result
```

Esto permite saber exactamente qué modelo participó en cada ticket.

---

# 29. Trazabilidad completa

Cada ticket debe poder reconstruirse:

```text
Organization
      ↓
Department
      ↓
Repository
      ↓
Ticket
      ↓
Actor
      ↓
Agent
      ↓
Harness
      ↓
Model
      ↓
Execution
      ↓
Branch / Worktree
      ↓
Resources
      ↓
commit_before
      ↓
Changes
      ↓
Tests
      ↓
commit_after
      ↓
Pull Request
      ↓
CI/CD
      ↓
Review
      ↓
Merge
      ↓
DONE
```

---

# 30. Event Log

Además del estado actual, Orchestrator mantiene un historial de eventos.

Eventos:

```text
ORGANIZATION_CREATED
TICKET_CREATED
TICKET_ASSIGNED
TICKET_CLAIMED
TICKET_STARTED

RESOURCE_LOCKED
RESOURCE_RELEASED

DEPENDENCY_CREATED
TICKET_BLOCKED

AGENT_STARTED
AGENT_STOPPED

COMMIT_REGISTERED

TEST_STARTED
TEST_PASSED
TEST_FAILED

PR_CREATED
PR_MERGED

TICKET_COMPLETED
TICKET_CANCELLED

ROLLBACK_REQUESTED
ROLLBACK_COMPLETED
```

Ejemplo:

```text
10:01 TICKET_STARTED
10:02 RESOURCE_LOCKED
10:25 TEST_STARTED
10:27 TEST_PASSED
10:30 COMMIT_REGISTERED
10:31 PR_CREATED
10:40 PR_MERGED
10:41 RESOURCE_RELEASED
10:41 TICKET_COMPLETED
```

El estado actual indica:

> qué está pasando.

El Event Log indica:

> qué ocurrió.

---

# 31. Auditoría

```text
AuditLog
- id
- actor_id
- action
- resource_type
- resource_id
- timestamp
- metadata
```

Ejemplo:

```text
Agent-03
LOCK_RESOURCE
Chat.py
2026-09-19 14:31
```

Permite saber:

* quién creó un ticket;
* quién lo ejecutó;
* qué agente lo ejecutó;
* qué modelo utilizó;
* qué recursos bloqueó;
* qué commits generó;
* qué tests ejecutó;
* qué PR creó;
* quién hizo merge;
* quién realizó un rollback.

---

# 32. Repositorios

```text
Repository
- id
- organization_id
- department_id
- name
- provider
- url
- default_branch
- visibility
- active
```

Cada repositorio puede contener:

```text
.orchestrator/
└── config.yml
```

Ejemplo:

```yaml
organization: my-company
department: youtube
repository: youtube-backend

mcp:
  url: https://orchestrator.company.com/mcp
```

---

# 33. Registro de repositorios

Comando:

```bash
work init
```

Proceso:

```text
detect Git repository
        ↓
identify remote
        ↓
register repository
        ↓
associate organization
        ↓
associate department
        ↓
generate .orchestrator/config.yml
```

---

# 34. CLI

CLI propuesta:

```bash
work init

work sync

work status

work status --json

work tasks

work ticket show YT-104

work start YT-104

work branch create YT-104

work commit YT-104

work pr create YT-104

work finish YT-104

work context
```

`work start`:

```text
sync
 ↓
validate permissions
 ↓
validate dependencies
 ↓
validate resources
 ↓
acquire locks
 ↓
record commit_before
 ↓
create branch/worktree
 ↓
claim ticket
 ↓
START
```

---

# 35. Copier

Orchestrator se distribuirá mediante un template Copier.

```text
orchestrator-template/
├── copier.yml
├── backend/
├── frontend/
├── mcp/
├── cli/
├── database/
├── docker/
├── harness/
└── config/
```

Instalación:

```bash
copier copy <orchestrator-template> company-orchestrator
```

Variables:

```text
Organization
Git provider
Deployment profile
Database
MCP
Web UI
AI agents
Departments
Authentication
Harness
```

Cada empresa obtiene su propia instancia.

```text
Orchestrator Template
        │
        ├── Company A
        ├── Company B
        └── Company C
```

Actualización:

```bash
copier update
```

manteniendo las personalizaciones de la organización.

---

# 36. Perfiles

## MINIMAL

```text
CLI
Git
Tickets
JSON / SQLite
Dependencies
Basic locks
status.sh
sync.sh
```

Pensado para un proyecto pequeño.

---

## TEAM

```text
Todo MINIMAL

PostgreSQL
Web UI
MCP
Users
Agents
Roles
Permissions
Resource locks
Audit
Event Log
Git integration
```

---

## ENTERPRISE

```text
Todo TEAM

SSO / OIDC
Advanced RBAC
Multi-department
Multi-repository
Cross-department workflows
Full audit
Observability
Enterprise Git
AI governance
Organization policies
```

Los perfiles comparten el mismo modelo:

```text
MINIMAL
   ↓
TEAM
   ↓
ENTERPRISE
```

---

# 37. Repositorio privado de organización

Una empresa puede tener:

```text
company-orchestrator
```

como repositorio privado.

Puede contener:

```text
configuration
policies
departments
permissions
custom integrations
deployment
```

Los agentes trabajan en repositorios de código independientes:

```text
youtube-backend
gmail-backend
chrome-backend
```

pero consultan:

```text
company-orchestrator
        ↓
MCP
        ↓
Orchestrator
```

para conocer:

```text
available tasks
permissions
dependencies
resource locks
policies
repository context
```

---

# 38. Seguridad

Principio:

> **El Orchestrator define la autorización organizativa; el Harness y la infraestructura aplican el aislamiento y los permisos efectivos.**

Capas:

```text
User identity
      ↓
Orchestrator RBAC
      ↓
Ticket authorization
      ↓
Harness identity
      ↓
Container / sandbox
      ↓
Network policy
      ↓
Credentials
      ↓
Git / DB / APIs
```

Nunca se debe confiar únicamente en prompts enviados al agente.

---

# 39. Concurrencia

Orchestrator gestiona la concurrencia lógica:

```text
Ticket ownership
Resource locks
Dependencies
State transitions
```

El Harness gestiona la concurrencia física:

```text
Processes
Containers
Worktrees
Filesystem
Network
Credentials
Runtime
```

CI/CD gestiona la validación concurrente del código:

```text
Branches
Builds
Tests
PR checks
```

Cada sistema tiene una responsabilidad distinta.

---

# 40. Arquitectura final

```text
                         ORGANIZATION
                              │
                              ▼
                       ORCHESTRATOR
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
     Tickets               RBAC                 Resources
        │                     │                     │
 Dependencies              Audit                  Locks
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                         MCP / API
                              │
                              ▼
                       AGENT HARNESS
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
     Runtime              Credentials             Git
        │                     │                     │
     Sandbox             DB access             Worktree
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                            AGENT
                              │
                              ▼
                             CODE
                              │
                              ▼
                            CI/CD
                              │
                              ▼
                              PR
                              │
                              ▼
                            MERGE
```

---

# 41. MVP

El MVP debe demostrar el núcleo del sistema sin intentar construir toda la infraestructura empresarial.

## MVP 1

```text
CLI
SQLite
Tickets
Dependencies
Resources
Locks
Git
commit_before
commit_after
status.sh
sync.sh
MCP
```

Caso de prueba:

```text
Agent A
   ↓
YT-104
   ↓
Chat.py
   ↓
LOCK

Agent B
   ↓
YT-105
   ↓
Chat.py
   ↓
BLOCKED
```

Al terminar A:

```text
commit_before
     ↓
changes
     ↓
commit_after
     ↓
release lock
     ↓
B puede continuar
```

---

# 42. MVP 2

Añadir:

```text
PostgreSQL
Users
Agents
Roles
Permissions
Web UI
Audit
Event Log
GitHub / GitLab
```

---

# 43. MVP 3

Añadir:

```text
Agent Harness
Agent Runner
Multiple AI providers
Model traceability
Git worktrees
Cross-department workflows
Copier
```

---

# 44. Enterprise

Finalmente:

```text
SSO
OIDC
Advanced RBAC
Observability
Enterprise Git
Policies
Governance
Multi-organization
```

---

# 45. Principio fundamental

Orchestrator debe separar cuatro responsabilidades:

```text
Git
│
└── Versionado del código

Orchestrator
│
└── Coordinación organizativa

MCP
│
└── Interfaz de los agentes

Agent Harness
│
└── Ejecución segura

CI/CD
│
└── Validación y entrega
```

Git responde:

> ¿Qué código existe y qué cambios se hicieron?

Orchestrator responde:

> ¿Quién puede trabajar, sobre qué, cuándo y con qué dependencias?

MCP responde:

> ¿Cómo consulta y modifica esa información un agente?

Harness responde:

> ¿En qué entorno y con qué permisos efectivos se ejecuta el agente?

CI/CD responde:

> ¿El cambio cumple las validaciones necesarias para integrarse?

---

# 46. Objetivo final

Orchestrator no pretende ser otro gestor de tareas ni simplemente otro sistema para lanzar agentes.

Es una **capa de coordinación organizativa para humanos y agentes de IA**.

Cada trabajo debe poder responder:

```text
¿Qué ticket era?
¿Quién lo creó?
¿Quién lo ejecutó?
¿Qué agente?
¿Qué modelo?
¿Qué Harness?
¿Qué repositorio?
¿Qué branch?
¿Qué recursos utilizó?
¿Qué recursos bloqueó?
¿Qué dependencias tenía?
¿Qué commit había antes?
¿Qué cambios produjo?
¿Qué tests pasó?
¿Qué commit produjo al finalizar?
¿Qué PR generó?
¿Qué CI/CD ejecutó?
¿Quién hizo merge?
¿Qué eventos ocurrieron?
¿Quién puede hacer rollback?
```

El núcleo de Orchestrator es, por tanto:

```text
ORGANIZATION
      ↓
DEPARTMENTS
      ↓
USERS / AGENTS
      ↓
PERMISSIONS
      ↓
TICKETS
      ↓
DEPENDENCIES
      ↓
RESOURCES
      ↓
LOCKS
      ↓
EXECUTIONS
      ↓
GIT TRACEABILITY
      ↓
AUDIT / EVENTS
```

Mientras que el **Agent Harness** se ocupa de convertir esa autorización en una ejecución real, aislada y controlada.

