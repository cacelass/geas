"""
tests.test_work — Tests de la CLI work (spec §34) y los scripts (§25).

Cubre: work init, work sync, work status, work tasks, work start,
work finish, work diff, work rollback.
"""

from __future__ import annotations

import subprocess

import pytest

from geas.models import (
    Organization,
    Resource,
    Ticket,
    TicketDependency,
)
from geas.storage import Storage
from geas.work import (
    WorkContext,
    cmd_diff,
    cmd_finish,
    cmd_init,
    cmd_start,
    cmd_status,
    cmd_tasks,
)


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def git_repo(tmp_path):
    """Repo git real con un commit base."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@geas"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        capture_output=True,
        check=True,
    )
    (repo / "Chat.py").write_text("class Chat:\n    pass\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "base"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/test/app.git",
        ],
        capture_output=True,
        check=True,
    )
    return repo


@pytest.fixture
def org(storage):
    o = Organization(name="Google")
    storage.create_organization(o)
    return o


class TestWorkInit:
    def test_init_creates_config(self, storage, git_repo, org):
        assert cmd_init(storage, [str(git_repo)]) == 0
        ctx = WorkContext(git_repo)
        assert ctx.exists()
        config = ctx.read_config()
        assert config["organization"] == "Google"
        assert config["repository"] == "app"
        assert config["provider"] == "github"  # remote apunta a github.com

        # El repo queda registrado en storage
        repos = storage.list_repositories(org.id)
        assert len(repos) == 1
        assert repos[0].name == "app"
        assert repos[0].organization_id == org.id

    def test_init_not_git(self, storage, tmp_path):
        plain = tmp_path / "norepo"
        plain.mkdir()
        assert cmd_init(storage, [str(plain)]) != 0


class TestWorkStatus:
    def test_status_ready(self, storage, git_repo, org):
        cmd_init(storage, [str(git_repo)])
        assert cmd_status(storage, [str(git_repo)]) == 0

    def test_status_not_ready(self, storage, git_repo, org):
        # Sin .orchestrator/config.yml → NOT READY
        assert cmd_status(storage, [str(git_repo)]) != 0

    def test_status_json(self, storage, git_repo, org):
        cmd_init(storage, [str(git_repo)])
        import contextlib
        import io
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_status(storage, [str(git_repo), "--json"])
        data = json.loads(buf.getvalue())
        assert data["ready"] is True
        assert len(data["checks"]) >= 3


class TestWorkTasks:
    def test_available_when_free_and_no_locks(self, storage, org):
        t = Ticket(organization_id=org.id, title="Implementar Chat.py")
        storage.create_ticket(t)

        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_tasks(storage, ["me"])

        out = buf.getvalue()
        assert t.id[:8] in out
        assert "READY" in out

    def test_skipped_when_resource_locked(self, storage, org):
        from geas.models import Repository, ResourceLock

        repo = Repository(organization_id=org.id, name="backend")
        storage.create_repository(repo)
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)

        t1 = Ticket(
            organization_id=org.id, repository_id=repo.id, title="A", resources=[res.id]
        )
        t2 = Ticket(
            organization_id=org.id, repository_id=repo.id, title="B", resources=[res.id]
        )
        storage.create_ticket(t1)
        storage.create_ticket(t2)

        # t1 bloquea Chat.py
        storage.create_lock(
            ResourceLock(
                resource_id=res.id,
                ticket_id=t1.id,
                actor_id="a",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )

        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_tasks(storage, ["me"])

        out = buf.getvalue()
        assert "SKIPPED" in out  # t2 no está disponible


class TestWorkFlow:
    """El caso de la spec §13/§41: YT-104 bloquea Chat.py, YT-105 no puede."""

    def test_full_flow(self, storage, git_repo, org):
        # Registrar repo
        cmd_init(storage, [str(git_repo)])

        repos = storage.list_repositories(org.id)
        repo = repos[0]

        # Recurso Chat.py
        res = Resource(repository_id=repo.id, path="Chat.py")
        storage.create_resource(res)

        # YT-104 (agent A)
        t104 = Ticket(
            organization_id=org.id,
            repository_id=repo.id,
            title="Implementar Chat",
            resources=[res.id],
        )
        storage.create_ticket(t104)

        # YT-105 (agent B)
        t105 = Ticket(
            organization_id=org.id,
            repository_id=repo.id,
            title="Refactor Chat",
            resources=[res.id],
        )
        storage.create_ticket(t105)

        # Trabajar en YT-104 → adquiere lock, registra commit_before, branch
        r = cmd_start(storage, [t104.id, str(git_repo)])
        assert r == 0

        t104_after = storage.get_ticket(t104.id)
        assert t104_after.status.value == "IN_PROGRESS"
        assert t104_after.commit_before
        assert t104_after.branch

        # Chat.py está bloqueado por YT-104
        lock = storage.get_lock_for_resource(res.id)
        assert lock is not None
        assert lock.ticket_id == t104.id

        # Hacer cambios reales y terminar
        (git_repo / "Chat.py").write_text(
            "class Chat:\n    def send(self):\n        pass\n"
        )
        r = cmd_finish(storage, [t104.id, str(git_repo)])
        assert r == 0

        t104_done = storage.get_ticket(t104.id)
        assert t104_done.status.value == "DONE"
        assert t104_done.commit_after

        # Lock liberado
        assert storage.get_lock_for_resource(res.id) is None

    def test_dependency_blocks_start(self, storage, git_repo, org):
        cmd_init(storage, [str(git_repo)])

        t1 = Ticket(organization_id=org.id, title="Base")
        t2 = Ticket(organization_id=org.id, title="Depende")
        storage.create_ticket(t1)
        storage.create_ticket(t2)
        storage.create_dependency(
            TicketDependency(
                ticket_id=t2.id,
                depends_on_ticket_id=t1.id,
            )
        )

        # t2 no puede empezar si t1 no está DONE
        assert cmd_start(storage, [t2.id, str(git_repo)]) != 0
        assert storage.get_ticket(t2.id).status.value == "FREE"


class TestWorkBranchCommitPr:
    """Comandos del §34: work branch create, work commit, work pr create."""

    def test_branch_create(self, storage, git_repo, org):
        t = Ticket(organization_id=org.id, title="Implementar Chat")
        storage.create_ticket(t)

        from geas.work import cmd_branch

        assert cmd_branch(storage, ["create", t.id, str(git_repo)]) == 0

        ticket = storage.get_ticket(t.id)
        assert ticket.branch
        branch = ticket.branch

        # La branch existe en git
        r = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        assert branch in r.stdout

        # Queda auditoría
        assert any(a.action == "BRANCH_CREATED" for a in storage.list_audit(org.id))

    def test_branch_create_rejects_existing_branch(self, storage, git_repo, org):
        t = Ticket(organization_id=org.id, title="X", branch="ya-existe")
        storage.create_ticket(t)

        from geas.work import cmd_branch

        assert cmd_branch(storage, ["create", t.id, str(git_repo)]) != 0

    def test_commit(self, storage, git_repo, org):
        t = Ticket(organization_id=org.id, title="Cambio")
        storage.create_ticket(t)

        from geas.work import cmd_branch, cmd_commit

        cmd_branch(storage, ["create", t.id, str(git_repo)])
        (git_repo / "Chat.py").write_text(
            "class Chat:\n    def send(self):\n        pass\n"
        )

        assert cmd_commit(storage, [t.id, str(git_repo), "trabajo"]) == 0

        # El commit existe en la branch del ticket
        r = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline", "-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "trabajo" in r.stdout

        # Evento COMMIT_REGISTERED (§30)
        events = {e.event_type for e in storage.list_events(org.id)}
        assert "COMMIT_REGISTERED" in events

    def test_commit_no_changes(self, storage, git_repo, org):
        t = Ticket(organization_id=org.id, title="Sin cambios")
        storage.create_ticket(t)

        from geas.work import cmd_branch, cmd_commit

        cmd_branch(storage, ["create", t.id, str(git_repo)])
        assert cmd_commit(storage, [t.id, str(git_repo)]) != 0

    def test_pr_create_pushes_branch(self, storage, git_repo, org, tmp_path):
        t = Ticket(organization_id=org.id, title="Feature")
        storage.create_ticket(t)

        from geas.work import cmd_branch, cmd_pr

        cmd_branch(storage, ["create", t.id, str(git_repo)])
        # Hacer un commit para que haya algo que pushear
        (git_repo / "Chat.py").write_text("class Chat:\n    x = 1\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "wip"],
            capture_output=True,
            check=True,
        )

        # Remote local (bare) para que el push sea real
        bare = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)], capture_output=True, check=True
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "remote", "set-url", "origin", str(bare)],
            capture_output=True,
            check=True,
        )

        assert cmd_pr(storage, ["create", t.id, str(git_repo)]) == 0

        # La branch llegó al remote
        r = subprocess.run(
            ["git", "--git-dir", str(bare), "branch", "--list", t.branch],
            capture_output=True,
            text=True,
            check=False,
        )
        assert t.branch in r.stdout

        # Evento PR_CREATED (§30)
        events = {e.event_type for e in storage.list_events(org.id)}
        assert "PR_CREATED" in events


class TestWorkDiffRollback:
    def test_diff_and_rollback(self, storage, git_repo, org):
        from geas.git import LocalGitProvider

        cmd_init(storage, [str(git_repo)])

        t = Ticket(organization_id=org.id, title="Cambio")
        storage.create_ticket(t)

        g = LocalGitProvider(str(git_repo))
        g.get_head()

        # Trabajo real
        (git_repo / "Chat.py").write_text(
            "class Chat:\n    def send(self):\n        pass\n"
        )

        cmd_start(storage, [t.id, str(git_repo)])
        cmd_finish(storage, [t.id, str(git_repo), "trabajo de YT"])

        # diff
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_diff(storage, [t.id, str(git_repo)])
        out = buf.getvalue()
        assert "Chat.py" in out

        # rollback
        from geas.work import cmd_rollback

        assert cmd_rollback(storage, [t.id, str(git_repo)]) == 0
        # El fichero vuelve al estado de commit_before
        content = (git_repo / "Chat.py").read_text()
        assert content == "class Chat:\n    pass\n"
