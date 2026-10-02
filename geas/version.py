"""geas.version — qué código está sirviendo este proceso, y si va por detrás.

Python carga el código al importar el módulo y no lo recarga nunca. Un fix
commiteado, pusheado y con los tests en verde **sigue sin estar desplegado**
hasta que alguien reinicia el proceso (fb4fdbe5). El síntoma es invisible:
`/health` dice `ok`, el panel se ve bien, y lo que se acaba es tomando
decisiones sobre código que nadie está ejecutando. Durante 25 horas este
servidor dio dos bugs falsos por eso: un HTTP 500 en `list_resources` y una UI
que se creía nueva y no lo era.

Para que no dependa de que alguien se acuerde, el proceso dice la verdad
sobre sí mismo:

  - `served_commit`: el HEAD del checkout **en el momento en que se importó este
    módulo**. Ese es el código que hay en memoria, que es lo único que
    respondería de verdad si alguien preguntara.
  - `checkout_head`: el HEAD ahora, leído de disco.
  - `stale`: si los dos son distintos. **Nunca `False` por defecto**: si no se puede
    saber, es `None`. Un `stale: false` inventado es peor que un `null`, porque
    un `null` pregunta y un `false` miente.
  - `checkout_path`: dónde se ha leído, que es lo que permite saber si el
    proceso está sirviendo *este* checkout y no otro.

Nada de esto puede lanzar. Es un endpoint de diagnóstico: si falla, tiene que
decir que no lo sabe, no tumbar el servidor ni el guard de `bootstrap`, que usa
`/health` y no debe depender de que git esté instalado.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

# Momento en que se carga este módulo. Con precisión de import: lo que hay en
# memoria es lo que había en disco en este instante.
LOADED_AT = time.time()

_TIMEOUT = 5.0
_CACHE_TTL = 5.0
_cache: dict[str, Any] = {"at": 0.0}  # {"at": float, "head": str|None, "root": str|None}


def _run_git(args: list[str], cwd: Path) -> str | None:
    """Ejecuta git y devuelve stdout limpio, o `None` si no se puede.

    `None` significa "no lo sé", nunca "vacío": un repo sin commits da error de
    git, y un git que no existe da `FileNotFoundError`. Los dos son lo mismo
    para este propósito.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def checkout_root(start: Path | None = None) -> Path | None:
    """Directorio del checkout que contiene el paquete, o `None`.

    Se sube desde el propio paquete en vez de suponer el directorio de trabajo:
    el servidor arranca con cwd en el data-dir, y el código puede venir de un
    `uv tool install` en otro sitio.
    """
    aqui = (start or Path(__file__)).resolve()
    if not aqui.is_dir():
        aqui = aqui.parent
    # Se delega en git, que ya sabe subir por .git, Sparse-checkout y
    # worktrees, y que devuelve el toplevel *real*: si el paquete cuelga de un
    # checkout ajeno, sale ese, y por eso se devuelve el path.
    salida = _run_git(["rev-parse", "--show-toplevel"], aqui)
    return Path(salida) if salida else None


def head_commit(root: Path) -> str | None:
    """HEAD actual del checkout, o `None` si no se puede leer."""
    return _run_git(["rev-parse", "HEAD"], root)


def _checkout_and_head(start: Path | None = None) -> tuple[Path | None, str | None]:
    """(raiz, HEAD) del checkout, en una sola llamada a git."""
    aqui = (start or Path(__file__)).resolve()
    if not aqui.is_dir():
        aqui = aqui.parent
    # `rev-parse` con dos patrones imprime dos lineas: la primera el toplevel,
    # la segunda el HEAD. Un git en vez de dos.
    salida = _run_git(["rev-parse", "--show-toplevel", "HEAD"], aqui)
    if not salida:
        return None, None
    lineas = salida.splitlines()
    if len(lineas) < 2:
        # Repo recien hecho, sin commits: hay raiz pero no hay HEAD.
        return (Path(lineas[0]) if lineas else None), None
    return Path(lineas[0]), lineas[1].strip()


# ── Lo que este proceso carga, fijado en el momento de cargarlo ──────────
# Se resuelve AQUI, al importarse el módulo, y no en la primera llamada. Es la
# diferencia entre todo y nada: si se resolviera más tarde, bastaría con que
# nadie preguntara hasta después de un `git checkout` para que "lo que sirve el
# proceso" fuera el código nuevo — y diría `stale: false` cuando lo cierto es
# que lleva 25 horas sirviendo el viejo. El bug de fb4fdbe5, en el propio
# detector del bug de fb4fdbe5.
_CHECKOUT_ROOT, SERVED_COMMIT = _checkout_and_head()


def report() -> dict[str, Any]:
    """Qué código sirve este proceso. Nunca lanza.

    El HEAD del checkout se cachea unos segundos: el panel lo pide en cada
    carga y no tiene sentido pagar un `git` por visita cuando la respuesta no
    puede cambiar más rápido que eso.
    """
    servido = SERVED_COMMIT
    ahora = time.monotonic()
    if ahora - float(_cache.get("at") or 0.0) >= _CACHE_TTL:
        _raiz, head = _checkout_and_head()
        _cache["at"] = ahora
        _cache["root"] = str(_raiz) if _raiz else None
        _cache["head"] = head
    head = _cache.get("head")
    return {
        "served_commit": servido,
        "loaded_at": LOADED_AT,
        "checkout_head": head,
        "checkout_path": _cache.get("root"),
        # `None` = no lo sé. Distinguir "no lo sé" de "no" es el punto entero
        # de este módulo: un stale=falso sin comprobar hace que nadie mire.
        "stale": None if servido is None or head is None else servido != head,
    }


def reset_cache() -> None:
    """Solo para tests: vacía la cache del HEAD, no lo que se carga al importar."""
    _cache.clear()
    _cache.update({"at": 0.0})
