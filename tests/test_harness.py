from __future__ import annotations

import subprocess
import sys

from geas.harness import LocalHarness


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_local_harness_isolates_worktree_and_cleans_up(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.email", "test@geas")
    _git(repository, "config", "user.name", "Geas Test")
    (repository / "README.md").write_text("base\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "base")

    harness = LocalHarness(tmp_path / "worktrees")
    environment = harness.prepare("YT-104", repository)
    result = harness.run(
        environment,
        [sys.executable, "-c", "import os; print(os.environ['GEAS_TICKET_ID'])"],
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "YT-104"
    assert environment.worktree != repository
    assert environment.worktree.exists()

    harness.cleanup(environment)
    assert not environment.worktree.exists()
