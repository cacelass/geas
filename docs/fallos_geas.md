# Fallos y observaciones de GEAS (corte 2026-09-30)

Recopilacion de fallos conocidos del servidor GEAS, de las observaciones de
los reviewers y de deudas tecnicas detectadas durante el trabajo. El backlog
del arnes vive en `harness/featureslist.json`; este fichero es la lista de
fallos "abiertos" que no tienen feature, o que la tienen y estan pendientes.

## Pendientes de feature (backlog del arnes)

### GEAS-005 — 10 tickets fantasma en el audit_log
- **Estado:** pending (feature creada, ticket `5fab7d1d…` FREE).
- **Sintoma:** el `audit_log` da por creados 10 tickets que no existen en la BD.
- **Causa probable:** dos escritores sobre el mismo SQLite en WAL se pisaron
  (transaccion sin commit efectivo); los revisores del ticket GEAS-005
  investigaran con una copia previa de la BD en `~/.local/share/geas/backups/`.
- **Leccion ya aplicada:** la BD solo la abre el servidor; `ops/geas` deja de
  abrirla con `Storage()` (se movio a la API en GEAS-007).

## Sin feature (observaciones de reviewers, decisiones pendientes)

### O1 — README decia "agente ve 14 permisos"
- **Estado:** RESUELTO en GEAS-007 (ahora 15 con `user:read`, medido en vivo:
  RASPY = 15, CESAR = 21).

### O2 — El rol manager no tiene `user:read`
- **Estado:** RESUELTO (commit `5aafafe` — 2026-10-01).
- **Sintoma:** con el token de Cesar (manager), `list_actors` respondía
  HTTP 403. El manager tiene `user:manage` pero no `user:read`.
- **Impacto:** no rompe nada hoy (los scripts usan actores con rol agent para
  listar); pero si alguien quiere listar actores con un rol manager, no puede.
- **Solución:** se añadió `user:read` al rol manager en `models.py`.

### O3 — `serve.sh` puede dejar el proxy sin org-id en silencio
- **Estado:** abierto, decision pendiente.
- **Sintoma:** `serve.sh` lanza el proxy sin esperar el health del servidor;
  si el descubrimiento por API falla, `_discover_org_id()` devuelve `""` y el
  proxy se queda sin `X-Geas-Organization` toda su vida, con un aviso solo en
  `proxy.log`. Aviso medido: `[proxy] ATENCION: no he podido descubrir el
  org-id por API`.
- **Nota:** la unit de produccion no sufre esto (pasa `--org-id` desde
  `.geas.yml` via `install-systemd.sh`).

### O4 — Guard del criterio 3 no cubre el acceso por atributo
- **Estado:** abierto, decision pendiente.
- **Sintoma:** el test de `ops/geas` que impide volver a `roster.env` busca
  variables AST con "roster" en el nombre (endurecido en GEAS-007), pero un
  acceso por atributo (`cfg.roster`) no salta. Agujero estrecho, las tres
  vias realistas estan cubiertas.

### O5 — Alcance de recursos en el create_ticket
- **Estado:** abierto, decision pendiente.
- **Sintoma:** los tickets GEAS-003/004/005/007 declaran como recursos
  `agents/agents` y `agents/tests`, pero el trabajo real tocaba `ops/geas`,
  docs y `tests/`. En GEAS no existe un recurso `ops/geas` bloqueable, asi
  que el lock no protege lo que de verdad se toca.
- **Nota:** es un agujero estructural del modelo de recursos, no de este
  implementer.

## Mejoras aplicadas (2026-10-01)

### Web UI
- Filtros por estado de tickets con barra de navegación interactiva.
- Tabla de tickets mejorada: prioridad, fecha, actor truncado, clic para detalle.
- Formulario de actualización de tickets (estado, prioridad, título, descripción,
  result, feedback) directamente desde el detalle.
- Cambio rápido de estado (DONE / CANCELLED) desde el detalle del ticket.
- Formulario de creación de usuarios desde el dashboard.
- Modal de detalle de ticket con carga asíncrona.

### CLI — Dispositivos
- Nuevo comando `geas device` para gestionar dispositivos permitidos:
  - `geas device list <org>` — listar dispositivos
  - `geas device add <org> <name>` — añadir dispositivo (ej: `geas device add <org> Portatil`)
  - `geas device remove <org> <name>` — desactivar dispositivo

## Externos / no-GEAS

### Odds API agotada
- **Estado:** bloqueo externo.
- **Impacto:** imposible `make scan` real con datos frescos; el comportamiento
  esta cubierto por SCAN-002 (feature done).

### API key filtrada
- **Estado:** pendiente de rotar.
- **Sintoma:** aparece `apiKey` en claro en `logs/grab_20260928.log`.
- **Accion:** rotar la clave y no incluir el log en commits.

## Resueltos (para no re-abrir)

- GEAS-003: create_ticket no valida ids de resource (HTTP 000) — resuelto.
- GEAS-004: get_permissions devolvia los mismos 25 a agent y manager — resuelto
  (devuelve 15/21 medido).
- GEAS-006: proxy/organizacion — resuelto antes de GEAS-007.
- GEAS-007: no habia herramientas list_actors/list_resources — resuelto.