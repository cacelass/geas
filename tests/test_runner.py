"""Tests del ExecutionRunner (§27/§28/§30): ejecución aislada y trazada."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geas.harness import LocalHarness, WorktreeEnvironment
from geas.models import Agent, Organization, Repository, Ticket
from geas.runner import ExecutionRunner, RunnerConfig
from geas.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


@pytest.fixture
def org(storage):
    org = Organization(name="Acme")
    storage.create_organization(org)
    return org


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(storage, org):
    """Registro de Repository en Geas (el ticket lo referencia)."""
    repository_data = Repository(
        organization_id=org.id,
        name="demo-repo",
        provider="local",
    )
    storage.create_repository(repository_data)
    return repository_data


@pytest.fixture
def repo_path(tmp_path):
    """Repo git real donde el runner crea el worktree."""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.email", "test@geas")
    _git(repository, "config", "user.name", "Geas Test")
    (repository / "README.md").write_text("base\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "base")
    return repository


def _ticket(storage, org, repo, title="YT-104"):
    ticket = Ticket(
        organization_id=org.id,
        title=title,
        repository_id=repo.id,
    )
    storage.create_ticket(ticket)
    return ticket


def test_run_ticket_executes_in_worktree_and_records_execution(
    storage, org, repo, repo_path
):
    ticket = _ticket(storage, org, repo)
    runner = ExecutionRunner(storage, harness=LocalHarness(".geas/worktrees"))

    config = RunnerConfig(
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('resultado.txt').write_text('ok')",
        ],
        worktrees_root=".geas/worktrees",
        actor_id="agent-01",
    )
    summary = runner.run_ticket(ticket.id, config, repo_path)

    assert summary.result == "success"
    assert summary.returncode == 0
    assert summary.test_status is None
    assert not summary.kept_worktree
    # El worktree se limpió
    assert not Path(summary.worktree).exists()

    # La ejecución quedó registrada con traceability (§28)
    executions = storage.get_executions(ticket.id)
    assert len(executions) == 1
    exe = executions[0]
    assert exe.actor_id == "agent-01"
    assert exe.result == "success"
    assert exe.finished_at

    # Eventos (§30)
    types = {e.event_type for e in storage.list_events(org.id)}
    assert "AGENT_STARTED" in types
    assert "AGENT_STOPPED" in types

    # El ticket fue reclamado e iniciado
    ticket_after = storage.get_ticket(ticket.id)
    assert ticket_after.status.value == "IN_PROGRESS"


def test_run_ticket_failure_is_recorded(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    runner = ExecutionRunner(storage)
    config = RunnerConfig(
        command=[sys.executable, "-c", "import sys; sys.exit(3)"],
        worktrees_root=".geas/worktrees",
    )
    summary = runner.run_ticket(ticket.id, config, repo_path)

    assert summary.result == "failure"
    assert summary.returncode == 3
    executions = storage.get_executions(ticket.id)
    assert executions[0].result == "failure"
    types = {e.event_type for e in storage.list_events(org.id)}
    assert "AGENT_STOPPED" in types


def test_run_ticket_with_tests_registers_test_result(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    runner = ExecutionRunner(storage)

    config = RunnerConfig(
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('app.py').write_text('x = 1')",
        ],
        test_command=[sys.executable, "-c", "print('tests ok')"],
        worktrees_root=".geas/worktrees",
    )
    summary = runner.run_ticket(ticket.id, config, repo_path)

    assert summary.result == "success"
    assert summary.test_status == "passed"
    results = storage.get_test_results(ticket.id)
    assert len(results) == 1
    assert results[0].status == "passed"
    types = {e.event_type for e in storage.list_events(org.id)}
    assert "TEST_STARTED" in types
    assert "TEST_PASSED" in types


def test_run_ticket_failing_tests_record_failed_result(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    runner = ExecutionRunner(storage)
    config = RunnerConfig(
        command=[sys.executable, "-c", "print('work done')"],
        test_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        worktrees_root=".geas/worktrees",
    )
    summary = runner.run_ticket(ticket.id, config, repo_path)

    assert summary.test_status == "failed"
    results = storage.get_test_results(ticket.id)
    assert results[0].status == "failed"
    types = {e.event_type for e in storage.list_events(org.id)}
    assert "TEST_FAILED" in types


def test_run_ticket_keeps_worktree_when_requested(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    runner = ExecutionRunner(storage)
    config = RunnerConfig(
        command=[sys.executable, "-c", "print('hola')"],
        worktrees_root=".geas/worktrees",
        keep_worktree=True,
    )
    summary = runner.run_ticket(ticket.id, config, repo_path)

    assert summary.kept_worktree
    assert Path(summary.worktree).exists()
    # Limpieza manual posterior
    runner.harness.cleanup(
        WorktreeEnvironment(
            ticket.id, summary.branch, repo_path, Path(summary.worktree)
        )
    )
    assert not Path(summary.worktree).exists()


def test_run_ticket_uses_agent_traceability(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    agent = Agent(
        organization_id=org.id,
        name="Codex",
        provider="openai",
        model="gpt-5",
        model_version="2026-01",
    )
    storage.create_agent(agent)
    runner = ExecutionRunner(storage)
    config = RunnerConfig(
        command=[sys.executable, "-c", "print('hola')"],
        worktrees_root=".geas/worktrees",
        tokens_input=100,
        tokens_output=50,
        cost=0.02,
        tools_used=["bash", "read"],
    )
    runner.run_ticket(ticket.id, config, repo_path, agent=agent)

    exe = storage.get_executions(ticket.id)[0]
    assert exe.provider == "openai"
    assert exe.model == "gpt-5"
    assert exe.model_version == "2026-01"
    assert exe.tokens_input == 100
    assert exe.tokens_output == 50
    assert exe.cost == 0.02
    assert exe.tools_used == ["bash", "read"]


def test_run_ticket_rejects_unrunnable_state(storage, org, repo, repo_path):
    ticket = _ticket(storage, org, repo)
    storage.update_ticket_status(ticket.id, "DONE")
    runner = ExecutionRunner(storage)
    config = RunnerConfig(command=[sys.executable, "-c", "pass"])
    with pytest.raises(ValueError):
        runner.run_ticket(ticket.id, config, repo_path)


def test_run_ticket_raises_when_missing(storage, org, repo, repo_path):
    runner = ExecutionRunner(storage)
    config = RunnerConfig(command=[sys.executable, "-c", "pass"])
    with pytest.raises(ValueError):
        runner.run_ticket("NO-EXISTE", config, repo_path)
