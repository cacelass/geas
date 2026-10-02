"""Tests de `geas.version` y del aviso del panel (fb4fdbe5).

El fallo que cubre esto no es un crash: es que el proceso **no diga** qué
código está sirviendo. Un servidor 25 horas por detrás da un HTTP 500 que
parece un bug de `list_resources` y un panel que parece la UI nueva. Estos
tests atacan esa afirmación: si el módulo no sabe, tiene que decir que no lo
sabe, nunca que "no está stale".
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from geas import version
from geas.server import GeasHttpServer
from geas.storage import Storage
from geas.webui import render_dashboard


@pytest.fixture(autouse=True)
def _cache_limpia():
    """El modulo cachea el HEAD: cada test parte de cero."""
    version.reset_cache()
    yield
    version.reset_cache()


@pytest.fixture
def checkout(monkeypatch):
    """Finge el estado: `served` es lo que cargo el proceso, `head` lo de ahora.

    Los dos se monkeypatchean porque los dos tienen que poder_move: `SERVED_COMMIT`
    se fija al importar el modulo de verdad, y `head` se relee con cache.
    """
    estado = {"served": "a" * 40, "head": "a" * 40}
    monkeypatch.setattr(version, "SERVED_COMMIT", estado["served"], raising=False)
    monkeypatch.setattr(version, "_checkout_and_head", lambda *a, **k: (Path("/tmp/x"), estado["head"]))
    return estado


class TestLoQueSirveElProceso:
    def test_al_dia_no_hay_banderas(self, checkout):
        checkout["head"] = "a" * 40
        info = version.report()
        assert info["served_commit"] == "a" * 40
        assert info["checkout_head"] == "a" * 40
        assert info["stale"] is False

    def test_stale_cuando_el_checkout_avanza(self, checkout):
        checkout["head"] = "b" * 40
        info = version.report()
        assert info["served_commit"] == "a" * 40
        assert info["stale"] is True

    def test_stale_tambien_si_el_checkout_atrasa(self, checkout):
        """Al reescribir o rebobinar no es 'no stale': es stale."""
        checkout["head"] = "9" * 40
        assert version.report()["stale"] is True


class TestCuandoNoSePuedeSaber:
    def test_sin_checkout_no_inventa(self, monkeypatch):
        """Lo importante: `None`, no `False`. Un false falso hace que nadie mire."""
        monkeypatch.setattr(version, "SERVED_COMMIT", None, raising=False)
        monkeypatch.setattr(version, "_checkout_and_head", lambda *a, **k: (None, None))
        info = version.report()
        assert info["served_commit"] is None
        assert info["stale"] is None

    def test_sin_head_pero_con_raiz_tampoco_inventa(self, checkout):
        checkout["head"] = None
        assert version.report()["stale"] is None

    def test_si_git_no_existe_no_revienta(self, monkeypatch, tmp_path):
        def revienta(*a, **k):
            raise FileNotFoundError("git no instalado")

        monkeypatch.setattr(version.subprocess, "run", revienta)
        assert version.head_commit(tmp_path) is None

    def test_el_report_never_lanza_si_git_falla(self, monkeypatch):
        monkeypatch.setattr(version, "SERVED_COMMIT", None, raising=False)
        monkeypatch.setattr(version, "_checkout_and_head", lambda *a, **k: (None, None))
        version.reset_cache()
        # Debe devolver algo con las claves, no lanzar: `/api/version` lo llama
        # en cada peticion y un endpoint de diagnostico que tumba el servidor
        # no sirve para diagnosticar.
        info = version.report()
        assert set(info) >= {"served_commit", "checkout_head", "stale"}


class TestElProcesoLoDiceSolo:
    """El requisito del ticket: que se pueda PREGUNTAR, no tener que mirar el HTML."""

    def test_endpoint_responde_el_commit_que_sirve(self, monkeypatch, tmp_path):
        # Se fija el estado en vez de confiar en el entorno: estos tests corren
        # DENTRO del repo, asi que aqui si hay checkout, y `stale` seria false.
        # Un test que depende de donde se ejecuta es un test que pasa por casualidad.
        monkeypatch.setattr(version, "SERVED_COMMIT", "c" * 40, raising=False)
        monkeypatch.setattr(
            version, "_checkout_and_head", lambda *a, **k: (Path("/srv/geas"), "c" * 40)
        )
        server = GeasHttpServer(("127.0.0.1", 0), Storage(tmp_path / "v.db"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            info = json.load(urlopen(f"{base}/api/version", timeout=5))
            assert info["served_commit"] == "c" * 40
            assert info["stale"] is False
            assert info["checkout_path"] == "/srv/geas"
        finally:
            server.shutdown()
            server.server_close()

    def test_endpoint_dice_desconocido_y_no_falla(self, monkeypatch, tmp_path):
        """Sin checkout responde 200 con stale=null: preguntar nunca rompe."""
        monkeypatch.setattr(version, "SERVED_COMMIT", None, raising=False)
        monkeypatch.setattr(version, "_checkout_and_head", lambda *a, **k: (None, None))
        server = GeasHttpServer(("127.0.0.1", 0), Storage(tmp_path / "v2.db"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            info = json.load(urlopen(f"{base}/api/version", timeout=5))
            assert info["stale"] is None
            assert "error" not in info
        finally:
            server.shutdown()
            server.server_close()

    def test_health_no_depende_de_git(self, tmp_path):
        """`/health` lo usa el guard de bootstrap: no puede depender de git."""
        server = GeasHttpServer(("127.0.0.1", 0), Storage(tmp_path / "h.db"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            assert json.load(urlopen(f"{base}/health", timeout=5)) == {"status": "ok"}
        finally:
            server.shutdown()
            server.server_close()


class TestElAvisoDelPanel:
    def test_avisa_cuando_hay_codigo_sin_desplegar(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "geas.webui.version_report",
            lambda: {
                "served_commit": "a" * 40,
                "checkout_head": "b" * 40,
                "stale": True,
                "checkout_path": str(tmp_path),
            },
        )
        page = render_dashboard(Storage(tmp_path / "p.db"))
        assert "sin desplegar" in page
        assert "aaaaaaaa" in page and "bbbbbbbb" in page

    def test_silencioso_cuando_cuadra(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "geas.webui.version_report",
            lambda: {
                "served_commit": "a" * 40,
                "checkout_head": "a" * 40,
                "stale": False,
                "checkout_path": str(tmp_path),
            },
        )
        page = render_dashboard(Storage(tmp_path / "p.db"))
        assert "sin desplegar" not in page
        assert "<div class='banner" not in page

    def test_sin_checkout_no_revienta_el_panel(self, monkeypatch, tmp_path):
        """Este caso reventaba con un NameError antes de que lo pillara ruff."""
        monkeypatch.setattr(
            "geas.webui.version_report",
            lambda: {
                "served_commit": None,
                "checkout_head": None,
                "stale": None,
                "checkout_path": None,
            },
        )
        page = render_dashboard(Storage(tmp_path / "p.db"))
        assert "no se encuentra el checkout" in page

    def test_si_el_diagnostico_falla_el_panel_sigue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "geas.webui.version_report", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        assert "Geas" in render_dashboard(Storage(tmp_path / "p.db"))


class TestElHelperDeGit:
    def test_head_de_un_repo_real(self, tmp_path):
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
        (tmp_path / "a.txt").write_text("x")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "x"], check=True)
        sha = version.head_commit(tmp_path)
        assert sha is not None and len(sha) == 40

    def test_un_repo_sin_commits_no_da_sha(self, tmp_path):
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        assert version.head_commit(tmp_path) is None

    def test_un_directorio_normal_no_da_sha(self, tmp_path):
        assert version.head_commit(tmp_path) is None

    def test_el_paquete_ve_su_propio_checkout(self):
        """El paquete esta instalado en modo editable, asi que si debe verse."""
        raiz, head = version._checkout_and_head()
        assert isinstance(raiz, Path), "checkout_root tiene que devolver un Path, no un str"
        assert raiz is not None and (raiz / ".git").exists()
        assert head and len(head) == 40
