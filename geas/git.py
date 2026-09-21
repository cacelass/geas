"""
geas.git — Integración con proveedores Git.

Geas no reemplaza Git: integra proveedores mediante una interfaz común
(spec §18). GitHub, GitLab, Bitbucket y self-hosted implementan GitProvider.

Operaciones (spec §18):
    fetch()  pull()  status()  create_branch()  commit()  push()
    create_pr()  merge()  get_commits()

El diff entre commit_before y commit_after permite rollback (§11):
    diff(commit_before, commit_after) → cambios exactos del trabajo
    rollback: restaurar commit_before sin borrar el historial
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CommitInfo:
    sha: str
    message: str
    author: str
    timestamp: str


@dataclass
class RepoStatus:
    branch: str
    clean: bool
    ahead: int
    behind: int
    untracked: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)


@dataclass
class DiffResult:
    """Diferencia entre dos commits (spec §11)."""

    base_sha: str
    target_sha: str
    files_changed: list[str]
    additions: int
    deletions: int
    patch: str  # diff completo


class GitProvider(ABC):
    """Interfaz común para todos los proveedores Git."""

    @abstractmethod
    def fetch(self) -> bool: ...

    @abstractmethod
    def pull(self) -> bool: ...

    @abstractmethod
    def status(self) -> RepoStatus: ...

    @abstractmethod
    def create_branch(self, name: str) -> bool: ...

    @abstractmethod
    def commit(self, message: str) -> CommitInfo | None: ...

    @abstractmethod
    def push(self, branch: str | None = None) -> bool: ...

    @abstractmethod
    def create_pr(self, title: str, branch: str, base: str = "main") -> str | None: ...

    @abstractmethod
    def merge(self, branch: str) -> bool: ...

    @abstractmethod
    def get_commits(
        self, since: str | None = None, limit: int = 10
    ) -> list[CommitInfo]: ...

    @abstractmethod
    def get_head(self) -> str: ...

    @abstractmethod
    def diff(self, base: str, target: str) -> DiffResult: ...


class LocalGitProvider(GitProvider):
    """Proveedor local sobre el CLI de git (MVP)."""

    def __init__(self, repo_path: str | None = None):
        self.repo_path = repo_path

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = ["git"]
        if self.repo_path:
            cmd += ["-C", self.repo_path]
        return subprocess.run(cmd + args, capture_output=True, text=True, check=False)

    # ─── Operaciones ───────────────────────────────────────────────────

    def fetch(self) -> bool:
        r = self._run(["fetch"])
        return r.returncode == 0

    def pull(self) -> bool:
        r = self._run(["pull", "--ff-only"])
        return r.returncode == 0

    def status(self) -> RepoStatus:
        r = self._run(["status", "--porcelain", "--branch"])
        lines = r.stdout.splitlines()
        branch = ""
        ahead = behind = 0
        untracked: list[str] = []
        modified: list[str] = []
        for line in lines:
            if line.startswith("##"):
                meta = line[2:]
                # "main...origin/main [ahead 1, behind 2]" o "main"
                parts = meta.split()
                branch = parts[0].split("...")[0]
                if len(parts) > 1 and parts[1].startswith("["):
                    info = parts[1][1:-1]
                    if "ahead" in info:
                        ahead = int(info.split("ahead ")[1].split(",")[0].split("]")[0])
                    if "behind" in info:
                        behind = int(info.split("behind ")[1].split("]")[0])
            elif line.startswith("??"):
                untracked.append(line[3:])
            else:
                modified.append(line[3:])
        clean = not untracked and not modified
        return RepoStatus(
            branch=branch,
            clean=clean,
            ahead=ahead,
            behind=behind,
            untracked=untracked,
            modified=modified,
        )

    def create_branch(self, name: str) -> bool:
        r = self._run(["checkout", "-b", name])
        return r.returncode == 0

    def commit(self, message: str) -> CommitInfo | None:
        r = self._run(["commit", "-m", message])
        if r.returncode != 0:
            return None
        head = self.get_head()
        return CommitInfo(sha=head, message=message, author="", timestamp="")

    def push(self, branch: str | None = None) -> bool:
        args = ["push"]
        if branch:
            args += ["origin", branch]
        r = self._run(args)
        return r.returncode == 0

    def create_pr(self, title: str, branch: str, base: str = "main") -> str | None:
        """Requiere el CLI del proveedor (gh, glab). Devuelve la URL del PR."""
        # Intentar GitHub CLI
        r = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--base", base],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return None

    def merge(self, branch: str) -> bool:
        r = self._run(["merge", branch])
        return r.returncode == 0

    def get_commits(
        self, since: str | None = None, limit: int = 10
    ) -> list[CommitInfo]:
        args = ["log", f"-{limit}", "--format=%H\t%s\t%an\t%aI"]
        if since:
            args = ["log", f"-{limit}", "--format=%H\t%s\t%an\t%aI", since + "..HEAD"]
        r = self._run(args)
        commits = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                commits.append(
                    CommitInfo(
                        sha=parts[0],
                        message=parts[1],
                        author=parts[2],
                        timestamp=parts[3],
                    )
                )
        return commits

    def get_head(self) -> str:
        r = self._run(["rev-parse", "HEAD"])
        return r.stdout.strip() if r.returncode == 0 else ""

    def diff(self, base: str, target: str) -> DiffResult:
        """diff(base, target) — los cambios producidos entre dos commits (§11)."""
        # Ficheros cambiados (--name-only es robusto a locales)
        r_names = self._run(["diff", "--name-only", base, target])
        files = [f for f in r_names.stdout.splitlines() if f.strip()]
        # Números de add/del
        additions = deletions = 0
        r2 = self._run(["diff", "--numstat", base, target])
        for line in r2.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
                additions += int(parts[0])
                deletions += int(parts[1])
        # Patch completo
        r3 = self._run(["diff", base, target])
        return DiffResult(
            base_sha=base,
            target_sha=target,
            files_changed=files,
            additions=additions,
            deletions=deletions,
            patch=r3.stdout,
        )

    def rollback(self, commit: str) -> bool:
        """Restaurar el estado de `commit` sin destruir historial (§11).

        Crea un commit inverso (revert) que deja el working tree en el
        estado de `commit`, y conserva el historial completo.
        """
        r = self._run(["revert", "--no-edit", commit + "..HEAD"])
        if r.returncode != 0:
            # Si no hay commits que revertir, probar checkout de los ficheros
            r = self._run(["checkout", commit, "--", "."])
        return r.returncode == 0


def get_provider(provider: str = "local") -> GitProvider:
    """Factory: devuelve el proveedor según el nombre."""
    providers = {
        "local": LocalGitProvider,
        "github": LocalGitProvider,  # MVP: GitHub vía CLI de git + gh
        "gitlab": LocalGitProvider,
        "bitbucket": LocalGitProvider,
    }
    cls = providers.get(provider, LocalGitProvider)
    return cls()
