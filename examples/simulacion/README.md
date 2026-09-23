# Simulación multi-persona — concurrencia en GEAS

Carpeta de prueba que simula **varias personas trabajando a la vez** sobre
la misma base de datos y el mismo documento, sin servidor ni red: la BD
SQLite local es la única copia compartida, y las operaciones de claim/lock
son atómicas (guardia `UPDATE ... WHERE status='FREE'`).

## Qué demuestra

1. **Dos personas NO pueden editar el mismo documento a la vez.**
   Ana reclama el documento `docs/diseno.md` para su ticket; Bot intenta
   reclamar el MISMO documento para el suyo → la BD se lo niega
   (`RESOURCE_UNAVAILABLE`). No hay condición de carrera: la decisión la
   toma una sola sentencia SQL, no la programación.
2. **Cuando Ana termina, el documento se libera.** El reintento de Bot
   entonces sí funciona.
3. **Dos personas SÍ trabajan en paralelo** en documentos distintos:
   cada una reclama sus recursos sin pisarse.

## Cómo ejecutarla

```bash
cd examples/simulacion
python3 demo_concurrencia.py            # usa una BD temporal en /tmp
python3 demo_concurrencia.py --keep     # conserva la BD y la ruta imprime
```

Salida esperada (lo esencial):

```
→ Ana (humana) empieza su ticket y reclama docs/diseno.md ... OK
→ Bot (agente) intenta reclamar docs/diseno.md A LA VEZ ... BLOQUEADO ✓
  (el documento ya está lockeado por el ticket de Ana — atomicidad en BD)
→ Ana termina (commit + libera el documento) ... OK
→ Bot reintenta ahora que Ana terminó ... OK
```

Salida real del estado al final:

```
Estado final:
  docs/diseno.md      → lock de Bot        (el recurso compartido)
  docs/api.md         → lock de Ana         (trabajo paralelo en paralelo)
  tickets: 2 en curso, 0 en conflicto
```

## Concurrencia real (dos procesos)

La demo usa un solo proceso para que la salida sea legible, pero las
operaciones clave son las MISMAS que corren en dos procesos distintos:
`start_ticket` (claim atómico) y `acquire_locks` (locks de recursos en
una transacción `BEGIN IMMEDIATE`). Para verlo con dos procesos reales:

```bash
# terminal 1 — Ana
GEAS_DB=/tmp/geas-demo/geas.db geas work start GEAS-001

# terminal 2 — Bot (el mismo documento, a la vez)
GEAS_DB=/tmp/geas-demo/geas.db geas work start GEAS-002
```

El segundo falla con `RECURSO_BLOQUEADO` mientras el primero no libere.

## Cómo se relaciona con CI/CD

El CI de cada rama/PR corre consultas ligeras (`geas sync .`), nunca
claims. El único punto donde dos pipelines compiten es el claim atómico
en la BD (`UPDATE ... WHERE status='FREE'`): la guardia de la BD decide,
no la planificación. Ver `docs/INSTALL.md` §5.