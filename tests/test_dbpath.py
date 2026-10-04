"""Tests de `geas.dbpath` — dónde está la BD y quién puede abrirla (1ea3bcc7).

Dos cosas que se confundían:

1. La BD se deducía del cwd (`Path("geas.db")`), así que desde la raíz de un
   repo se abría una BD fantasma vacía que no da error: da «no hay tickets».
2. El guard contra el segundo escritor vivía solo en `geas_admin.py`, que está
   en el repo de edgebet. La CLI de GEAS no lo tenía, y `geas work start` desde
   el directorio de datos abría la BD buena con el servidor vivo.

Aquí se comprueba lo segundo de verdad: un lock tomado por otro proceso tiene
que hacer que la CLI se haga atrás, y `serve` tiene que reservarla.
"""

from __future__ import annotations

import fcntl
import subprocess
import sys
import time
from pathlib import Path

import pytest

from geas import dbpath
from geas.dbpath import (
    comensal,
    lock_path,
    reservar_servidor,
    resolve_db,
    ResolveDbConfig,
    servidor_vivo,
)


@pytest.fixture(autouse=True)
def _sin_instalacion(monkeypatch, tmp_path):
    """Todos los tests apuntan a su propia BD.

    Sin esto, un test que no fije `GEAS_DB` resolvería a la BD instalada y
    estaría leyendo o escribiendo la de producción. Un test que depende de la
    máquina donde corre es un test que un día hace daño de verdad.
    """
    monkeypatch.setenv("GEAS_DB", str(tmp_path / "geas.db"))
    monkeypatch.delenv("GEAS_DATA_DIR", raising=False)
    monkeypatch.delenv("GEAS_ALLOW_LIVE", raising=False)


class TestDondeEstaLaBd:
    def test_geas_db_gana_sobre_todo(self, tmp_path):
        config = ResolveDbConfig(
            geas_db=str(tmp_path / "otro.db"),
            geas_data_dir=str(tmp_path / "data"),
            instalada_existe=True,
        )
        assert resolve_db(config) == tmp_path / "otro.db"

    def test_geas_data_dir_si_no_hay_geas_db(self, tmp_path):
        config = ResolveDbConfig(
            geas_db=None,
            geas_data_dir=str(tmp_path / "data"),
            instalada_existe=True,
        )
        assert resolve_db(config) == tmp_path / "data" / "geas.db"

    def test_instalada_si_no_hay_entorno(self, tmp_path):
        """Sin vars de entorno, la instalada gana al fallback."""
        config = ResolveDbConfig(
            geas_db=None,
            geas_data_dir=None,
            instalada_existe=True,
        )
        assert resolve_db(config) == dbpath.DEFAULT_DATA_DIR / "geas.db"

    def test_sin_nada_cae_al_cwd(self):
        """Fallback: si no hay nada configurado, ./geas.db."""
        config = ResolveDbConfig(
            geas_db=None,
            geas_data_dir=None,
            instalada_existe=False,
        )
        assert resolve_db(config) == Path("geas.db")

    def test_geas_db_vacio_pasa_al_siguiente(self, tmp_path):
        """Variable vacía cuenta como no definida."""
        config = ResolveDbConfig(
            geas_db="",
            geas_data_dir=str(tmp_path / "data"),
            instalada_existe=True,
        )
        assert resolve_db(config) == tmp_path / "data" / "geas.db"

    def test_data_dir_vacio_pasa_a_instalada(self, tmp_path):
        """GEAS_DATA_DIR vacía, sigue a la instalada."""
        config = ResolveDbConfig(
            geas_db=None,
            geas_data_dir="",
            instalada_existe=True,
        )
        assert resolve_db(config) == dbpath.DEFAULT_DATA_DIR / "geas.db"

    def test_el_lock_va_contiguo_a_la_bd(self):
        assert lock_path("/x/geas.db") == Path("/x/geas.db.server")


class TestElSegundoEscritor:
    def test_sin_servidor_se_puede_abrir(self):
        assert servidor_vivo("/tmp/no-existe-esta/geas.db") is False

    def test_con_el_lock_tomado_no_se_puede(self, tmp_path):
        """flock(2) en dos fds del mismo proceso tambien se estorban."""
        db = tmp_path / "geas.db"
        tomado = open(lock_path(db), "a+", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(tomado.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert servidor_vivo(db) is True
            with pytest.raises(SystemExit) as exc, comensal(db):
                pass
            # El mensaje tiene que decir qué hacer, no solo negar.
            assert "segundo escritor" in str(exc.value)
            assert "GEAS_ALLOW_LIVE" in str(exc.value)
        finally:
            tomado.close()

    def test_allow_live_salta_el_guard(self, tmp_path, monkeypatch):
        db = tmp_path / "geas.db"
        tomado = open(lock_path(db), "a+", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(tomado.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            monkeypatch.setenv("GEAS_ALLOW_LIVE", "1")
            with comensal(db):
                pass  # no se hace atrás
        finally:
            tomado.close()

    def test_al_soltar_el_lock_vuelve_a_poder(self, tmp_path):
        db = tmp_path / "geas.db"
        tomado = open(lock_path(db), "a+", encoding="utf-8")  # noqa: SIM115
        fcntl.flock(tomado.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert servidor_vivo(db) is True
        tomado.close()
        assert servidor_vivo(db) is False


class TestElServidorSeReservaLaBd:
    def test_serve_toma_el_lock_y_lo_suelta_al_morir(self, tmp_path):
        db = tmp_path / "geas.db"
        with reservar_servidor(db):
            assert servidor_vivo(db) is True
        assert servidor_vivo(db) is False

    def test_dos_servidores_el_segundo_se_hace_atras(self, tmp_path):
        db = tmp_path / "geas.db"
        with reservar_servidor(db):
            with pytest.raises(SystemExit) as exc, reservar_servidor(db):
                pass
            assert "ya hay un servidor" in str(exc.value)


class TestDeVerdadConUnServidorArriba:
    """El escenario del ticket, con un proceso de verdad."""

    def test_la_cli_se_hace_atras_con_un_serve_vivo(self, tmp_path):
        db = tmp_path / "geas.db"
        entorno = {"PATH": "/usr/bin:/bin", "GEAS_DB": str(db), "HOME": str(tmp_path)}
        servidor = subprocess.Popen(
            [sys.executable, "-m", "geas.cli", "serve", "127.0.0.1", "8799"],
            env=entorno,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # El arranque tiene que ser visible, no un sleep a ciegas.
            limite = time.time() + 20
            while time.time() < limite and not servidor_vivo(db):
                time.sleep(0.1)
            assert servidor_vivo(db), "el serve no llegó a tomar el lock"

            hecho = subprocess.run(
                [sys.executable, "-m", "geas.cli", "org", "list"],
                env=entorno,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert hecho.returncode != 0
            assert "me hago atrás" in hecho.stderr
            # Sin este caso, todo lo de arriba podria estar pasando porque el
            # comando este roto y no por el guard: el mismo comando, sin
            # servidor, tiene que funcionar.
            libre = subprocess.run(
                [sys.executable, "-m", "geas.cli", "org", "list"],
                env={**entorno, "GEAS_DB": str(tmp_path / "libre.db")},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert libre.returncode == 0, libre.stderr
        finally:
            servidor.terminate()
            servidor.wait(timeout=20)

    def test_where_dice_donde_sin_abrir_la_bd(self, tmp_path, capsys):
        from geas.cli import main

        assert main(["where"]) == 0
        salida = capsys.readouterr().out
        assert str(tmp_path / "geas.db") in salida
        assert "servidor:" in salida

    def test_where_sigue_respondiendo_con_el_servidor_vivo(self, tmp_path, capsys):
        """Regresión: el guard llegó a negarle el paso a su propio diagnóstico.

        `where` es el comando que se consulta cuando la BD molesta. Si el guard
        lo rechaza, solo se puede usar cuando no hay nada que mirar, que es
        justo cuando no hace falta.
        """
        from geas.cli import main

        db = tmp_path / "geas.db"
        tomado = open(lock_path(db), "a+", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(tomado.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert main(["where"]) == 0
            salida = capsys.readouterr().out
            assert "vivo" in salida
        finally:
            tomado.close()
