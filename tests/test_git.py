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
