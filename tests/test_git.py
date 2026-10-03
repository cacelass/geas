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
