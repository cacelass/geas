"""geas.dbpath — dónde está la BD de GEAS, y quién tiene permiso de abrirla.

Dos fallos que parecen uno y no lo son:

1. **La BD se deducia del cwd.** `Path("geas.db")` relativo al directorio desde
   el que se lance. En la raíz de cualquier repo eso crea una BD fantasma,
   vacía, que no da error: da «no hay tickets». Ya ha pasado
   (`/home/cacelas/edgebet/geas.db`, 0 tickets, escrita un 2026-10-02).

2. **Nada impedía ser el segundo escritor.** El guard que protege la BD viva
   vive solo en `geas_admin.py`, un script de edgebet. Ni la CLI ni `work` lo
   tienen, así que `geas work start` desde el directorio de datos abría la BD
   buena mientras el servidor la tenía abierta: dos escritores sobre el mismo
   SQLite en WAL (GEAS-005/006).

Para (1) hay una variable, y ya existía: `GEAS_DATA_DIR` la usan `bootstrap.sh`
y `serve.sh` desde siempre, pero **Python nunca la ha leído**. Eso se arregla
resolviendo la ruta en un solo sitio, en este fichero.

Para (2), un `flock` sobre un fichero contiguo a la BD. Un lock de fichero y no
un sondeo de puerto a propósito: el puerto se le pasa a `serve` en la línea de
comandos, así que un cliente no lo conoce —`geas work` no tiene ni idea de que
el servidor esté en el 8791 o en el 8787— y si el guard dependiera del puerto
sería justo el guard que no protege el caso que importa. El lock lo toma el
servidor mientras vive, y lo suelta el kernel al morir: no hay fichero que
pueda quedar mintiendo.

`GEAS_ALLOW_LIVE=1` salta el guard, igual que en `geas_admin.py`, para depurar a
mano asumiendo el riesgo.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

# Sufijo del fichero de lock. Va contiguo a la BD a proposito: si la BD cambia
# de sitio, el lock va con ella en vez de quedarse|--o no estar-- en el sitio
# viejo.
LOCK_SUFFIX = ".server"

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "geas"
DB_NAME = "geas.db"


@dataclass(frozen=True)
class ResolveDbConfig:
    """Configuración para decidir dónde está la BD de GEAS.

    Quien construya este objeto (CLI, tests, scripts) decide cómo obtener
    los valores. ``resolve_db()`` solo decide cuál usar, sin leer de
    ``os.environ`` ni tocar el disco — es una función pura.
    """

    geas_db: str | None
    """Ruta explícita a un fichero de BD. Si no es None, se usa esta."""
    geas_data_dir: str | None
    """Directorio de datos. Si no es None, se usa ``<dir>/geas.db``."""
    instalada_existe: bool
    """``True`` si ``~/.local/share/geas/geas.db`` existe en disco."""


def lock_path(db_path: Path | str) -> Path:
    """Fichero de lock que acompaña a esta BD."""
    return Path(str(db_path) + LOCK_SUFFIX)


def resolve_db(config: ResolveDbConfig | None = None) -> Path:
    """Resuelve la BD de GEAS. En un solo sitio, y con un orden explícito.

    Prioridad (de mayor a menor):

    1. ``GEAS_DB`` — un fichero, para quien sepa exactamente qué quiere.
    2. ``GEAS_DATA_DIR/geas.db`` — la variable que ya usaban los scripts de shell.
    3. ``~/.local/share/geas/geas.db`` **si existe** — la instancia instalada.
    4. ``./geas.db`` — si ninguna opción está disponible.

    El paso 3 es el que cambia el comportamiento de verdad: antes, desde la raíz
    de un repo se abría un ``./geas.db`` fantasma y vacío; ahora se abre la
    instancia real y, si tiene un servidor vivo, el guard lo dice (G1) en vez de
    dejarte trabajar en silencio sobre una BD que no es la de nadie.

    Si no se pasa ``config`` se lee de ``os.environ`` y se consulta el disco
    (comportamiento por defecto, mantenido para compatibilidad).
    """
    if config is None:
        explicito = os.environ.get("GEAS_DB")
        if explicito:
            return Path(explicito)
        directorio = os.environ.get("GEAS_DATA_DIR")
        if directorio:
            return Path(directorio) / DB_NAME
        instalada = DEFAULT_DATA_DIR / DB_NAME
        if instalada.exists():
            return instalada
        return Path(DB_NAME)

    if config.geas_db:
        return Path(config.geas_db)
    if config.geas_data_dir:
        return Path(config.geas_data_dir) / DB_NAME
    if config.instalada_existe:
        return DEFAULT_DATA_DIR / DB_NAME
    return Path(DB_NAME)


def _abrir_lock(db_path: Path | str) -> IO[str]:
    ruta = lock_path(db_path)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    return open(ruta, "a+", encoding="utf-8")


def _tomar(db_path: Path | str) -> IO[str] | None:
    """Intenta el lock exclusivo sin esperar. `None` si otro lo tiene."""
    try:
        descriptor = _abrir_lock(db_path)
    except OSError:
        return None
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        descriptor.close()
        if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            return None
        raise
    return descriptor


def servidor_vivo(db_path: Path | str) -> bool:
    """¿Hay un servidor de GEAS con esta BD abierta?

    Toma el lock y lo suelta enseguida. No es una forma de mantener nada: es una
    pregunta, y la pregunta se hace de la única manera que no depende de que
    alguien se acuerde de pasar un puerto.
    """
    descriptor = _tomar(db_path)
    if descriptor is None:
        return True
    _soltar(descriptor)
    return False


def _soltar(descriptor: IO[str]) -> None:
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        descriptor.close()


class _ServidorReservando:
    """Contexto: el servidor se reserva la BD mientras vive (`geas serve`)."""

    def __init__(self, db_path: Path | str) -> None:
        self._db = db_path
        self._descriptor: IO[str] | None = None

    def __enter__(self) -> Path:
        descriptor = _tomar(self._db)
        if descriptor is None:
            raise SystemExit(
                f"geas: ya hay un servidor usando {self._db}.\n"
                "Dos servidores sobre la misma BD no tiene sentido, y el que "
                "pierda el puerto se queda con la BD abierta.\n"
                "  paralo con: systemctl --user stop geas-server\n"
                "  o arranca otro en otra BD: GEAS_DATA_DIR=/otra/dir geas serve"
            )
        self._descriptor = descriptor
        return Path(self._db)

    def __exit__(self, *_exc: object) -> None:
        if self._descriptor is not None:
            _soltar(self._descriptor)
            self._descriptor = None


def reservar_servidor(db_path: Path | str) -> _ServidorReservando:
    """`with reservar_servidor(db): serve(...)` — el servidor toma la BD."""
    return _ServidorReservando(db_path)


def aviso_servidor_vivo(db_path: Path | str, comando: str) -> str:
    """Mensaje para una CLI que se va a hacer atrás. Explica, no solo negate."""
    return (
        f"geas: me hago atrás. Hay un servidor de GEAS con {db_path} abierta, y "
        f"`{comando}` sería un segundo escritor sobre el mismo SQLite en WAL.\n"
        "Dos escritores se pisan, y así se han perdido tickets (GEAS-005/006).\n"
        "  el servidor es quien manda: usa el API o el proxy (port 8790), o\n"
        "  GEAS_ALLOW_LIVE=1 si de verdad quieres tocar la BD con el servidor vivo.\n"
        f"  lock: {lock_path(db_path)}"
    )


@contextlib.contextmanager
def comensal(db_path: Path | str) -> Iterator[None]:
    """Contexto: uso no-servidor. Se hace atrás si hay servidor vivo.

    Saltar el guard es posible a proposito (`GEAS_ALLOW_LIVE=1`) porque hay
    operaciones legitimas de solo lectura sobre la BD viva; lo que no se puede
    es que sea el comportamiento por defecto y que abrirla parezca inocua.
    """
    if os.environ.get("GEAS_ALLOW_LIVE") == "1" or not servidor_vivo(db_path):
        yield
        return
    raise SystemExit(
        aviso_servidor_vivo(db_path, os.environ.get("GEAS_COMANDO", "geas"))
    )
