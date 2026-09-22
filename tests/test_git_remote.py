"""
tests.test_git_remote — Proveedores Git remotos (§18/§42).

GitHub, GitLab y Bitbucket hablan con su API REST vía urllib (stdlib
pura — el proyecto no añade deps). Estos tests congelan los contratos
HTTP (URL, método, body, auth) con urlopen mockeado: sin red y sin
credenciales reales. El contrato honesto del §42 se prueba aquí:
sin token → error claro, nunca degradación silenciosa al local.
"""

from __future__ import annotations

import base64
import json
import subprocess
from urllib.error import HTTPError

import pytest

from geas.git import (
    ApiProviderError,
    BitbucketProvider,
    GitHubProvider,
    GitLabProvider,
    LocalGitProvider,
    get_provider,
)


def _resp(payload: dict | list) -> object:
    body = json.dumps(payload).encode()

    class FakeResponse:
        def __init__(self) -> None:
            self._body = body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> bool:
            return False

        def read(self) -> bytes:
            return self._body

    return FakeResponse()


def _fake_urlopen(monkeypatch: pytest.MonkeyPatch, payload: dict | list) -> dict:
    """Mockea geas.git.urlopen y captura la Request para inspeccionarla."""
    captured: dict = {}

    def fake(request: object, timeout: int = 30) -> object:
        captured["request"] = request
        return _resp(payload)

    monkeypatch.setattr("geas.git.urlopen", fake)
    return captured


@pytest.fixture
def repo(tmp_path):
    """Repo git con un remote origin real (https de GitHub)."""
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
    (tmp_path / "f.txt").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "base"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "https://github.com/org/chat.git",
        ],
        capture_output=True,
        check=True,
    )
    return tmp_path


class TestFactory:
    def test_nombres_correctos(self, repo):
        assert isinstance(get_provider("local", repo_path=str(repo)), LocalGitProvider)
        assert isinstance(
            get_provider("self-hosted", repo_path=str(repo)), LocalGitProvider
        )
        assert isinstance(
            get_provider("github", repo_path=str(repo), token="t"), GitHubProvider
        )
        assert isinstance(
            get_provider("gitlab", repo_path=str(repo), token="t"), GitLabProvider
        )
        assert isinstance(
            get_provider("bitbucket", repo_path=str(repo), token="t"),
            BitbucketProvider,
        )

    def test_nombre_desconocido_falla_claro(self):
        with pytest.raises(ValueError, match="desconocido"):
            get_provider("svn")

    def test_sin_token_falla_claro(self, repo, monkeypatch):
        """§42: sin token, error claro — nunca cae en silencio al local."""
        monkeypatch.delenv("GEAS_GITHUB_TOKEN", raising=False)
        with pytest.raises(ApiProviderError, match="GEAS_GITHUB_TOKEN"):
            GitHubProvider(str(repo))


class TestRemoteParsing:
    def test_parse_https(self, repo):
        g = GitHubProvider(str(repo), token="t")
        assert g.owner == "org"
        assert g.repo == "chat"

    def test_parse_ssh_scp(self, tmp_path):
        subprocess.run(
            ["git", "init", str(tmp_path)], capture_output=True, check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "remote",
                "add",
                "origin",
                "git@gitlab.com:group/proj.git",
            ],
            capture_output=True,
            check=True,
        )
        g = GitLabProvider(str(tmp_path), token="t")
        assert g.owner == "group"
        assert g.repo == "proj"

    def test_sin_remote_falla_claro(self, tmp_path):
        subprocess.run(
            ["git", "init", str(tmp_path)], capture_output=True, check=True
        )
        with pytest.raises(ApiProviderError, match="owner/repo"):
            GitHubProvider(str(tmp_path), token="t")


class TestGitHubProvider:
    def test_create_pr(self, repo, monkeypatch):
        captured = _fake_urlopen(
            monkeypatch, {"html_url": "https://github.com/org/chat/pull/7"}
        )
        g = GitHubProvider(str(repo), token="t")
        url = g.create_pr("añade chat", "geas/YT-104")
        assert url == "https://github.com/org/chat/pull/7"
        req = captured["request"]
        assert req.method == "POST"
        assert req.full_url == "https://api.github.com/repos/org/chat/pulls"
        assert json.loads(req.data) == {
            "title": "añade chat",
            "head": "geas/YT-104",
            "base": "main",
        }
        assert req.headers["Authorization"] == "Bearer t"

    def test_merge_encuentra_pr_y_lo_funde(self, repo, monkeypatch):
        calls: list = []

        def fake(request: object, timeout: int = 30) -> object:
            calls.append(request)
            if request.method == "GET":
                return _resp([{"number": 7}])
            return _resp({"merged": True})

        monkeypatch.setattr("geas.git.urlopen", fake)
        g = GitHubProvider(str(repo), token="t")
        assert g.merge("geas/YT-104") is True
        put_req = calls[1]
        assert put_req.method == "PUT"
        assert (
            put_req.full_url
            == "https://api.github.com/repos/org/chat/pulls/7/merge"
        )

    def test_merge_sin_pr_devuelve_false(self, repo, monkeypatch):
        _fake_urlopen(monkeypatch, [])
        g = GitHubProvider(str(repo), token="t")
        assert g.merge("geas/YT-104") is False

    def test_get_commits(self, repo, monkeypatch):
        payload = [
            {
                "sha": "abc123",
                "commit": {
                    "message": "base\n\nDetalle",
                    "author": {"name": "Ana", "date": "2026-09-22T10:00:00Z"},
                },
            }
        ]
        captured = _fake_urlopen(monkeypatch, payload)
        g = GitHubProvider(str(repo), token="t")
        commits = g.get_commits(limit=5)
        assert commits[0].sha == "abc123"
        assert commits[0].message == "base"  # sólo la primera línea
        assert commits[0].author == "Ana"
        assert "per_page=5" in captured["request"].full_url

    def test_error_api_401_falla_claro(self, repo, monkeypatch):
        def fake(request: object, timeout: int = 30) -> object:
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        monkeypatch.setattr("geas.git.urlopen", fake)
        g = GitHubProvider(str(repo), token="t")
        with pytest.raises(ApiProviderError, match="401"):
            g.create_pr("x", "b")


class TestGitLabProvider:
    def test_create_mr_ruta_encoded(self, repo, monkeypatch):
        captured = _fake_urlopen(
            monkeypatch,
            {"web_url": "https://gitlab.com/group/proj/-/merge_requests/3"},
        )
        g = GitLabProvider(str(repo), token="t")
        url = g.create_pr("mr", "geas/YT-104")
        assert url == "https://gitlab.com/group/proj/-/merge_requests/3"
        req = captured["request"]
        assert req.method == "POST"
        assert "org%2Fchat" in req.full_url  # path del proyecto codificado
        assert json.loads(req.data) == {
            "title": "mr",
            "source_branch": "geas/YT-104",
            "target_branch": "main",
        }

    def test_merge_mr(self, repo, monkeypatch):
        calls: list = []

        def fake(request: object, timeout: int = 30) -> object:
            calls.append(request)
            if request.method == "GET":
                return _resp([{"iid": 3}])
            return _resp({"state": "merged"})

        monkeypatch.setattr("geas.git.urlopen", fake)
        g = GitLabProvider(str(repo), token="t")
        assert g.merge("geas/YT-104") is True
        assert calls[1].method == "PUT"
        assert "merge_requests/3/merge" in calls[1].full_url

    def test_get_commits(self, repo, monkeypatch):
        payload = [
            {
                "id": "def456",
                "title": "fix",
                "author_name": "Bot",
                "committed_date": "2026-09-22T09:00:00Z",
            }
        ]
        _fake_urlopen(monkeypatch, payload)
        g = GitLabProvider(str(repo), token="t")
        commits = g.get_commits()
        assert commits[0].sha == "def456"
        assert commits[0].message == "fix"
        assert commits[0].author == "Bot"


class TestBitbucketProvider:
    def test_authorization_basic(self, repo, monkeypatch):
        captured = _fake_urlopen(monkeypatch, {})
        g = BitbucketProvider(str(repo), token="user:app_password")
        g.create_pr("t", "b")
        auth = captured["request"].headers["Authorization"]
        assert auth.startswith("Basic ")
        decoded = base64.b64decode(auth.split()[1]).decode()
        assert decoded == "user:app_password"

    def test_create_pr_body_y_url(self, repo, monkeypatch):
        captured = _fake_urlopen(
            monkeypatch,
            {"links": {"html": {"href": "https://bitbucket.org/org/chat/pullrequests/9"}}},
        )
        g = BitbucketProvider(str(repo), token="u:p")
        url = g.create_pr("t", "geas/YT-104")
        assert url == "https://bitbucket.org/org/chat/pullrequests/9"
        body = json.loads(captured["request"].data)
        assert body["source"]["branch"]["name"] == "geas/YT-104"
        assert body["destination"]["branch"]["name"] == "main"

    def test_merge_pr(self, repo, monkeypatch):
        calls: list = []

        def fake(request: object, timeout: int = 30) -> object:
            calls.append(request)
            if request.method == "GET":
                return _resp(
                    {
                        "values": [
                            {
                                "id": 9,
                                "source": {"branch": {"name": "geas/YT-104"}},
                            }
                        ]
                    }
                )
            return _resp({"state": "MERGED"})

        monkeypatch.setattr("geas.git.urlopen", fake)
        g = BitbucketProvider(str(repo), token="u:p")
        assert g.merge("geas/YT-104") is True
        assert calls[1].method == "POST"
        assert "pullrequests/9/merge" in calls[1].full_url

    def test_merge_sin_pr_devuelve_false(self, repo, monkeypatch):
        _fake_urlopen(monkeypatch, {"values": []})
        g = BitbucketProvider(str(repo), token="u:p")
        assert g.merge("geas/YT-104") is False