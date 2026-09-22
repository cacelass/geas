"""Harness local para ejecutar agentes en worktrees aislados.

Geas conserva la política, los tickets y la traza. Este módulo prepara el
entorno físico mínimo (worktree, proceso y limpieza) sin gestionar secretos ni
credenciales: esas responsabilidades pertenecen al proveedor de runtime.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class WorktreeEnvironment:
    ticket_id: str
    branch: str
    repository: Path
    worktree: Path


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str


class LocalHarness:
    """Crea un worktree por ticket y ejecuta comandos sin usar un shell."""

    def __init__(self, worktrees_root: str | Path):
        self.worktrees_root = Path(worktrees_root)

    @staticmethod
    def _git(repository: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def prepare(
        self, ticket_id: str, repository: str | Path, branch: str | None = None
    ) -> WorktreeEnvironment:
        repo = Path(repository).resolve()
        valid = self._git(repo, ["rev-parse", "--is-inside-work-tree"])
        if valid.returncode != 0:
            raise ValueError(f"No es un repositorio Git: {repo}")
        branch_name = branch or f"geas/{ticket_id}"
        # Las rutas relativas se resuelven contra el repositorio: así
        # git worktree add y run() apuntan al mismo sitio.
        root = Path(self.worktrees_root)
        if not root.is_absolute():
            root = repo / root
        worktree = root / ticket_id
        if worktree.exists():
            raise FileExistsError(f"El worktree ya existe: {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        command = self._git(repo, ["worktree", "add", "-b", branch_name, str(worktree)])
        if command.returncode != 0:
            raise RuntimeError(command.stderr.strip() or "No se pudo crear worktree")
        return WorktreeEnvironment(ticket_id, branch_name, repo, worktree)

    def run(
        self,
        environment: WorktreeEnvironment,
        command: list[str],
        timeout: int | None = None,
    ) -> ProcessResult:
        if not command:
            raise ValueError("El comando del agente no puede estar vacío")
        started = datetime.now(UTC).isoformat()
        result = subprocess.run(
            command,
            cwd=environment.worktree,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GEAS_TICKET_ID": environment.ticket_id},
        )
        return ProcessResult(
            result.returncode,
            result.stdout,
            result.stderr,
            started,
            datetime.now(UTC).isoformat(),
        )

    def cleanup(self, environment: WorktreeEnvironment) -> None:
        result = self._git(
            environment.repository,
            ["worktree", "remove", "--force", str(environment.worktree)],
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "No se pudo limpiar worktree")
