# Instalación e integración de GEAS en un repositorio

Documento de instalación (§43) — cómo instalar la tool, incluirla en un
repositorio y dársela a un asistente de codificación para que lo haga
todo solo.

---

## 1. Instalar la herramienta

GEAS es un paquete Python con entry point `geas` (stdlib pura, cero
dependencias de runtime). **Se instala desde el repositorio, no desde
PyPI**: el nombre `geas` en PyPI pertenece a otro proyecto no relacionado,
así que `pipx install geas` descargaría ese paquete, no este.

```bash
# Desde el repo GEAS (fuente canónica)
cd <ruta-al-repo-geas>

# Con pipx (recomendado — aislado, comando global)
pipx install .

# Con uv tool
uv tool install .

# Editable, para desarrollo
pipx install --editable .     # o: uv tool install --editable .

# Desde el repo en desarrollo (venv local)
uv venv && uv pip install -e ".[dev]"
```

> ⚠️ Si ya probaste `pipx install geas` y te instaló un paquete ajeno
> (v0.3.0, github.com/teka1905/geas — generador de contratos OpenAPI),
> desinstálalo: `pipx uninstall geas`. Luego reinstala desde el repo con
> `pipx install .`.

Comprueba que quedó instalado:

```bash
geas --help        # o: geas (docstring de la CLI)
```

## 2. Elegir el perfil

Al configurar, se elige **dónde** se va a usar GEAS. El perfil activa solo
los componentes necesarios — un único paquete, no tres productos.

```bash
# Individual — instalación mínima local: CLI + SQLite + Git + tickets + locks
geas init "Mi Org"

# Team — instancia compartida: + API + MCP + Users/Agents + RBAC + Audit
geas init "Mi Org" --profile team          # o: --profile 2

# Enterprise — organización completa: + Departments + Policies + Governance
geas init "Mi Org" --profile enterprise    # o: --profile 3
```

| Perfil | Alias | Backend | Componentes que activa |
|--------|-------|---------|------------------------|
| Individual | `1` | SQLite | CLI · Git · Tickets · Dependencies · Locks |
| Team | `2` | PostgreSQL (§42, driver pendiente) | Individual + API · MCP · Users/Agents · RBAC · Audit |
| Enterprise | `3` | PostgreSQL | Team + Departments · Policies · Governance |

`geas init` imprime los componentes activados; `geas org show <id>` los
muestra en cualquier momento.

## 3. Incluir GEAS en un repositorio

Desde la raíz del repo que se quiere coordinar:

```bash
cd tu-repo
geas work init .
```

Detecta el repo git, identifica el remote, registra el repositorio en la
BD y genera:

```
.geas.yml   identidad del repo (org, departamento, repo, MCP)
status.sh                  §25 — READY TO WORK / NOT READY (--json para agentes)
sync.sh                    sincroniza repo ↔ GEAS ↔ Git
start.sh <ticket_id>       claim atómico + commit_before + rama
finish.sh <ticket_id>      commit_after + release de locks
```

Verificación rápida:

```bash
./status.sh          # READY TO WORK
./status.sh --json   # salida estructurada para agentes
```

### 3.1 Simulación multi-persona (prueba de concurrencia)

`examples/simulacion/` simula dos personas (humana + agente) editando el
MISMO documento a la vez y demuestra que la BD se lo impide (lock de
recurso atómico) y que el reintento tras terminar sí funciona:

```bash
python3 examples/simulacion/demo_concurrencia.py
```

## 4. Prompt de instalación para tu asistente

Pégalo tal cual a Claude Code, opencode o cualquier asistente con acceso
al shell. Automatiza la inclusión de GEAS en un repo existente:

```text
Integra GEAS en este repositorio. GEAS coordina el trabajo de humanos y
agentes de IA: tickets, dependencias, recursos, locks, permisos.

Pasos:
1. Si `geas` no está instalado, instálalo desde el repo: `pipx install .`
   (o `uv tool install .`; nunca `pipx install geas` — el nombre en PyPI
   pertenece a otro proyecto no relacionado).
2. Crea la organización con el perfil correcto:
   - un solo agente/persona en local  → `geas init "Mi Org"`            (Individual)
   - equipo compartido                → `geas init "Mi Org" --profile team`
   - varios departamentos/empresa     → `geas init "Mi Org" --profile enterprise`
3. Registra el repo: `geas work init .` — genera .geas.yml
   y los scripts status/sync/start/finish (§25). Commitéalos junto con
   esta guía si procede.
4. Crea un ticket de humo y verifica el ciclo completo:
   - `geas ticket create <org_id> "Setup inicial"`
   - `geas work start <ticket_id>`      # claim atómico + commit_before
   - haz un cambio real en el código
   - `geas work commit <ticket_id>`     # commit en rama geas/<id>
   - `geas work finish <ticket_id>`     # commit_after + liberar locks
5. Si el perfil es Team/Enterprise, deja el CI del repo consultando a
   GEAS con operaciones ligeras (`geas sync .` en un workflow — nunca
   estado operativo en el pipeline).

Reglas:
- NUNCA conviertas el estado operativo (tickets, locks, ejecuciones) en
  ficheros versionados. La BD de GEAS (SQLite local o PostgreSQL en
  Team/Enterprise) es la única fuente de verdad del estado.
- El árbol versionado solo lleva configuración deseada (organization,
  departments, repositories, roles, policies) que se aplica con
  `geas sync .`.
- No inventes subcomandos; usa `geas work --help` y `geas --help` para
  confirmar la interfaz real antes de llamar.
```

## 5. Concurrencia: BD local, `.geas.yml` y CI/CD

La pregunta recurrente: en Individual, ¿el estado va en la BD local o en
un `.geas.yml` del repo? ¿Hace falta un CI/CD dentro del repo?

### 5.1 El estado operativo NUNCA va en ficheros

Tickets, locks, ejecuciones, assignments, heartbeats: **solo en la BD**
(deliberado desde §43 — ver SPEC.md). El fichero declarativo del repo
(`.geas.yml`, y los `organization.yml/roles/...` de
Enterprise) describe el estado **deseado**; `geas sync` lo aplica a la
BD. Un `.geas.yml` que guardara tickets sería una segunda fuente de
verdad y reintroduciría exactamente los problemas de concurrencia que la
BD resuelve.

Por perfil:

| Perfil | Estado operativo en |
|--------|---------------------|
| Individual | `geas.db` local (SQLite), un repo, un workspace |
| Team | instancia PostgreSQL compartida (driver pendiente §42; esta build SQLite con fail-fast) |
| Enterprise | instancia PostgreSQL compartida + estructura declarativa versionada |

### 5.2 El CI/CD consulta, no decide

Cada agente/desarrollador trabaja en su propia rama/PR; cada rama puede
tener su propio pipeline. Los pipelines corren **en paralelo** y solo
hacen consultas ligeras a GEAS:

```text
LLM A → rama A → CI/CD A    ┐
LLM B → rama B → CI/CD B    ├── consultas ligeras → GEAS (BD)
LLM C → rama C → CI/CD C    ┘

CI A → ¿Ticket 10 libre? → Sí  → claim (UPDATE … WHERE status='FREE')
CI B → ¿Ticket 20 libre? → Sí  → claim
CI C → ¿Ticket 10 libre? → No  → espera
```

La única operación que se serializa es el **claim atómico** (§39), que ya
es una sola sentencia `UPDATE` con guardia de estado: dos pipelines que
intentan reclamar el mismo ticket no compiten por programación, compiten
por la restricción de la BD. Así, decenas de LLM pueden trabajar
simultáneamente sin convertir el pipeline en cuello de botella.

Un workflow de ejemplo (Team/Enterprise) queda en el repo como
`.github/workflows/geas-sync.yml` — generado por
`geas enterprise init`. Los checks de estado del repo los hace
`./status.sh --json`, no el CI.

### 5.3 Onboarding de un agent en un repo

1. `pipx install .` (desde el repo GEAS; ver sección 1)
2. `geas init "Mi Org" --profile team` (o el perfil de la org)
3. `geas work init .` — registra el repo, genera config + scripts
4. `geas work tasks <actor_id>` — qué tickets puede ejecutar
5. `geas work start YT-104` — claim atómico; empieza
6. `geas work finish YT-104` — commit_after; libera locks