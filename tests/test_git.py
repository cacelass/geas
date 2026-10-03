"""
tests.test_git — Tests de la integración Git.

Crean un repo git temporal real y verifican que el proveedor local
funciona: status, commit, branch, diff y rollback.
"""

from __future__ import annotations

import subprocess

import pytest

from geas.git import LocalGitProvider


@pytest.fixture
def repo(tmp_path):
    """Crea un repo git real con un commit base."""
    subprocess.run(
        ["git", "init", "-b", "main", str(tmp_path)], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@geas"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "test"],
        capture_output=True,
        check=True,
    )
    (tmp_path / "file.txt").write_text("hola\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "base"],
        capture_output=True,
        check=True,
    )
    return tmp_path


class TestGitProvider:
    def test_status_clean(self, repo):
        g = LocalGitProvider(str(repo))
        s = g.status()
        assert s.clean is True
        assert s.branch == "main"

    def test_status_with_untracked(self, repo):
        (repo / "new.txt").write_text("nuevo\n")
        g = LocalGitProvider(str(repo))
        s = g.status()
        assert s.clean is False
        assert "new.txt" in s.untracked

    def test_commit_and_head(self, repo):
        g = LocalGitProvider(str(repo))
        (repo / "file.txt").write_text("hola mundo\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        commit = g.commit("mejora")
        assert commit is not None
        assert commit.sha == g.get_head()
        assert g.status().clean is True

    def test_branch_creation(self, repo):
        g = LocalGitProvider(str(repo))
        assert g.create_branch("feature/YT-104") is True
        assert g.status().branch == "feature/YT-104"

    def test_diff_between_commits(self, repo):
        g = LocalGitProvider(str(repo))
        base = g.get_head()

        (repo / "file.txt").write_text("hola mundo\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        g.commit("cambio")
        target = g.get_head()

        diff = g.diff(base, target)
        assert diff.base_sha == base
        assert diff.target_sha == target
        assert "file.txt" in diff.files_changed
        assert diff.additions >= 1
        assert "hola mundo" in diff.patch

    def test_get_commits(self, repo):
        g = LocalGitProvider(str(repo))
        commits = g.get_commits(limit=5)
        assert len(commits) >= 1
        assert commits[0].message == "base"

    def test_rollback(self, repo):
        """§11: revertir el trabajo restaura commit_before sin borrar historial."""
        g = LocalGitProvider(str(repo))
        before = g.get_head()

        (repo / "file.txt").write_text("cambiado\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
        )
        g.commit("trabajo")
        after = g.get_head()
        assert before != after

        ok = g.rollback(before)
        assert ok is True
        # El contenido vuelve al estado de commit_before
        assert (repo / "file.txt").read_text() == "hola\n"


class TestStatusNoConcatenaAheadYBehind:
    """`status()` devolvía ahead=11 con "ahead 1, behind 1" (6912f060).

    Hacía `int("".join(dígitos de todo el texto))`: con los dos contadores a
    la vez unía "1" y "1". Un `has_unpublished` que dice 11 en vez de 1 no es
    que dé un número raro: hace que un repositorio con un commit sin pushear
    parezca once veces más sucio de lo que es, y el que lo use para decidir
    si publica se equivoca de magnitud.
    """

    @pytest.mark.parametrize(
        "linea,ahead,behind",
        [
            ("## main...origin/main [ahead 1, behind 1]", 1, 1),
            ("## main...origin/main [ahead 3, behind 2]", 3, 2),
            ("## main...origin/main [ahead 7]", 7, 0),
            ("## main...origin/main [behind 4]", 0, 4),
            ("## main...origin/main [ahead 12, behind 34]", 12, 34),
            ("## main...origin/main", 0, 0),
            ("## main...origin/main [gone]", 0, 0),
        ],
    )
    def test_parsea_cada_contador_por_separado(self, monkeypatch, linea, ahead, behind):
        class R:
            stdout = linea + "\n"
            returncode = 0

        monkeypatch.setattr(
            "geas.git.LocalGitProvider._run", lambda self, *a, **k: R()
        )
        s = LocalGitProvider("/no/importa").status()
        assert (s.ahead, s.behind) == (ahead, behind)

    def test_ahead_real_de_un_repo_de_verdad(self, tmp_path):
        """Y no sólo el texto: un repo divergente de verdad da 1 y 1."""
        import pathlib

        env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
        }

        def run(args, cwd):
            return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                                  capture_output=True)

        bare, work = tmp_path / "b.git", tmp_path / "w"
        work.mkdir()
        run(["init", "-q", "--bare", "-b", "main", str(bare)], tmp_path)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"],
                  ["config", "user.name", "t"]):
            run(a, work)

        def commit(cwd, msg, txt):
            pathlib.Path(cwd / "f.txt").write_text(txt)
            run(["add", "-A"], cwd)
            run(["commit", "-qm", msg], cwd)

        commit(work, "c1", "a")
        commit(work, "c2", "b")
        run(["remote", "add", "origin", str(bare)], work)
        run(["push", "-q", "-u", "origin", "main"], work)
        commit(work, "c3", "c")

        otro = tmp_path / "otro"
        run(["clone", "-q", "-b", "main", str(bare), str(otro)], tmp_path)
        for a in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
            run(a, otro)
        commit(otro, "c9", "z")
        run(["push", "-q", "origin", "main"], otro)
        run(["fetch", "-q", "origin"], work)

        s = LocalGitProvider(str(work)).status()
        assert (s.ahead, s.behind) == (1, 1)
        assert LocalGitProvider(str(work)).has_unpublished() is True


class TestRemoteViewConsultaElRemotoDeVerdad:
    """`has_unpublished` contestaba sobre las refs cacheadas (5346f1c1).

    Medido con dos clones y un remoto real: si otro clon publica el commit que
    yo tenía sin pushear, HEAD local == remoto real pero `origin/main` en mi
    caché sigue en el valor viejo, y `status().ahead` devolvía 1. Es decir:
    «tienes trabajo sin publicar» para siempre, en cada consulta.
    """

    @pytest.fixture
    def escena(self, tmp_path):
        """(work, otro, git, commit) — remoto bare con un commit y dos clones."""
        import pathlib
        import subprocess

        env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
            "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path),
        }

        def git(args, cwd):
            return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                                  capture_output=True, text=True)

        def commit(cwd, msg, txt):
            pathlib.Path(cwd, "f.txt").write_text(txt)
            git(["add", "-A"], cwd)
            git(["commit", "-qm", msg], cwd)

        bare, work = tmp_path / "b.git", tmp_path / "w"
        work.mkdir()
        git(["init", "-q", "--bare", "-b", "main", str(bare)], tmp_path)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"],
                  ["config", "user.name", "t"]):
            git(a, work)
        commit(work, "c1", "a")
        git(["remote", "add", "origin", str(bare)], work)
        git(["push", "-q", "-u", "origin", "main"], work)
        otro = tmp_path / "o"
        git(["clone", "-q", "-b", "main", str(bare), str(otro)], tmp_path)
        for a in (["config", "user.email", "t@e"], ["config", "user.name", "t"]):
            git(a, otro)
        return work, otro, git, commit

    def test_otro_clon_publica_mi_commit_y_deja_de_decir_que_no(self, escena):
        """El caso exacto del falso positivo."""
        work, otro, git, commit = escena
        commit(work, "c2", "b")            # sin pushear
        git(["fetch", "-q"], otro)
        git(["merge", "-q", "--ff-only", "origin/main"], otro)
        commit(otro, "c2", "b")            # otro publica MI commit
        git(["push", "-q", "origin", "main"], otro)

        # La caché local sigue mintiendo: origin/main aqui no se ha movido.
        assert git(["rev-parse", "origin/main"], work).stdout.strip() != git(
            ["rev-parse", "HEAD"], work).stdout.strip()
        g = LocalGitProvider(str(work))
        assert g.status().ahead == 1        # elcached: miente
        v = g.remote_view()                 # el remoto: acierta
        assert v.ahead == 0
        assert v.has_unpublished is False
        assert v.basis == "remote"

    def test_lo_que_mismo_no_pusheado_sigue_visto(self, escena):
        work, _otro, _git, commit = escena
        commit(work, "c2", "b")
        v = LocalGitProvider(str(work)).remote_view()
        assert v.ahead == 1
        assert v.has_unpublished is True

    def test_rama_que_no_existe_en_local_no_es_cero(self, escena):
        work, _otro, _git, _commit = escena
        v = LocalGitProvider(str(work)).remote_view("no-existe")
        assert v.basis == "unknown"
        assert v.ahead is None              # no saber, no es 0
        assert v.has_unpublished is None
        assert "no existe en local" in v.note

    def test_rama_nueva_en_local_no_publicada_cuenta_commits(self, escena):
        """Rama que aún no existe en origin: todo lo suyo está sin publicar."""
        work, _otro, git, commit = escena
        git(["checkout", "-q", "-b", "feature"], work)
        commit(work, "f1", "x")
        commit(work, "f2", "y")
        v = LocalGitProvider(str(work)).remote_view("feature")
        assert v.remote_sha is None
        assert v.ahead == 2
        assert v.has_unpublished is True
        assert "no existe en origin" in v.note

    def test_la_rama_se_honra_de_verdad(self, escena):
        """`branch` no se tira: main y feature dan números distintos."""
        work, _otro, git, commit = escena
        git(["checkout", "-q", "-b", "feature"], work)
        commit(work, "f1", "x")
        main_v = LocalGitProvider(str(work)).remote_view("main")
        feat_v = LocalGitProvider(str(work)).remote_view("feature")
        assert (main_v.ahead, feat_v.ahead) == (0, 1)

    def test_remoto_inaccesible_dice_desconocido(self, escena, tmp_path):
        """Sin red: basis='unknown', no un 0 disfrazado de respuesta."""
        work, _otro, _git, _commit = escena
        g = LocalGitProvider(str(work))
        g._run = _sin_remoto(g._run)   # ls-remote y fetch fallan: sin red
        v = g.remote_view()
        assert v.ahead is None
        assert v.has_unpublished is None
        assert v.basis == "unknown"
        assert v.note

    def test_is_published_con_remoto_real(self, escena):
        work, _otro, git, commit = escena
        publicado = git(["rev-parse", "HEAD"], work).stdout.strip()
        commit(work, "c2", "b")
        local = git(["rev-parse", "HEAD"], work).stdout.strip()
        g = LocalGitProvider(str(work))
        assert g.published_view(publicado) is True
        assert g.published_view(local) is False

    def test_is_published_sin_remoto_no_inventa_false(self, escena):
        work, _otro, git, _commit = escena
        head = git(["rev-parse", "HEAD"], work).stdout.strip()
        g = LocalGitProvider(str(work))
        g._run = _sin_remoto(g._run)
        assert g.published_view(head) is None


def _sin_remoto(run_original):
    """Envuelve `_run` para que ls-remote/fetch fallen como si no hubiera red.

    Se asigna como atributo de instancia, así que no recibe `self` (un atributo
    de instancia no actúa de descriptor): se cierra sobre el método ligado.
    """
    import subprocess

    def _run(args):
        if args and args[0] in ("ls-remote", "fetch"):
            return subprocess.CompletedProcess(args, 128, "", "sin red")
        return run_original(args)

    return _run
