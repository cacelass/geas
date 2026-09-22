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

import json
import os
import subprocess
from abc import ABC, abstractmethod
from base64 import b64encode
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


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


class ApiProviderError(RuntimeError):
    """Error de un proveedor remoto: token, red o respuesta de la API.

    Es la frontera honesta del §42: un proveedor remoto que no puede
    operar falla claro, NUNCA cae en silencio al CLI local — eso
    mentiría el contrato (§18: el proveedor es quien dice ser).
    """


class ApiGitProvider(LocalGitProvider):
    """Proveedor Git remoto por API REST (GitHub, GitLab, Bitbucket).

    La mitad local (fetch/pull/status/create_branch/commit/push/diff/
    rollback/get_head) la hereda de LocalGitProvider: se opera sobre el
    working copy real y `push` ya habla con el remote vía git CLI.

    La mitad remota (create_pr/merge/get_commits) usa la API del
    proveedor con stdlib pura (urllib) — el proyecto no añade deps.
    El token sale del entorno (GEAS_*_TOKEN) o del constructor; sin
    token la construcción falla claro en vez de degradarse al local
    (§42: los contratos no se mienten en silencio).
    """

    api_base = ""
    token_env = ""
    provider_name = ""

    def __init__(
        self,
        repo_path: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
        token: str | None = None,
    ):
        super().__init__(repo_path)
        self._token = token if token is not None else os.environ.get(self.token_env, "")
        if not self._token:
            raise ApiProviderError(
                f"{self.provider_name}: falta token de API — define {self.token_env}"
                " (o pásalo al constructor). Nunca se cae en silencio al proveedor"
                " local (§18/§42): el contrato del proveedor no se miente."
            )
        parsed_owner, parsed_repo = self._parse_remote()
        self.owner = owner or parsed_owner
        self.repo = repo or parsed_repo
        if not self.owner or not self.repo:
            raise ApiProviderError(
                f"{self.provider_name}: no se pudo identificar owner/repo — ¿tiene"
                " el repo un remote `origin` (o pásalos al constructor owner=/repo=)?"
            )

    # ─── Remote origin → owner/repo ────────────────────────────────────

    def _parse_remote(self) -> tuple[str | None, str | None]:
        r = self._run(["remote", "get-url", "origin"])
        url = r.stdout.strip()
        if not url:
            return None, None
        if "://" in url:  # https://host/owner/repo.git | ssh://git@host/owner/repo.git
            _, _, rest = url.partition("://")
            path = rest.split("/", 1)[1] if "/" in rest else ""
        elif "@" in url and ":" in url:  # git@host:owner/repo.git
            path = url.split("@", 1)[1].split(":", 1)[1]
        else:
            path = url
        path = path.split("?")[0].rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None, None

    # ─── HTTP (stdlib, zero deps) ──────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> dict | list | None:
        url = self.api_base + path
        if params:
            url += "?" + urlencode(sorted(params.items()))
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "geas",
            "Authorization": self._auth_header(),
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read()
                if not payload:
                    return None
                return json.loads(payload)
        except HTTPError as exc:
            detail = (
                exc.read().decode(errors="replace")[:300]
                if exc.fp is not None
                else str(exc)
            )
            raise ApiProviderError(
                f"{self.provider_name}: API {exc.code} en {path}: {detail}"
            ) from exc
        except URLError as exc:
            raise ApiProviderError(
                f"{self.provider_name}: sin red hacia {self.api_base}: {exc.reason}"
            ) from exc

    def _auth_header(self) -> str:
        return f"Bearer {self._token}"

    # Las operaciones locales se heredan de LocalGitProvider.
    # create_pr / merge / get_commits los implementa cada proveedor.


class GitHubProvider(ApiGitProvider):
    """GitHub vía REST (https://docs.github.com/rest)."""

    api_base = "https://api.github.com"
    token_env = "GEAS_GITHUB_TOKEN"
    provider_name = "github"

    def _find_open_pr(self, branch: str) -> dict | None:
        data = self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls",
            params={"state": "open", "head": f"{self.owner}:{branch}", "per_page": 100},
        )
        return data[0] if data else None

    def create_pr(self, title: str, branch: str, base: str = "main") -> str | None:
        data = self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/pulls",
            body={"title": title, "head": branch, "base": base},
        )
        return (data or {}).get("html_url")

    def merge(self, branch: str) -> bool:
        pr = self._find_open_pr(branch)
        if not pr:
            return False
        data = self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/pulls/{pr['number']}/merge"
        )
        return bool(data and data.get("merged"))

    def get_commits(
        self, since: str | None = None, limit: int = 10
    ) -> list[CommitInfo]:
        params: dict[str, str | int] = {"per_page": limit}
        if since:
            params["since"] = since  # fecha ISO-8601 (nativo de la API)
        data = self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/commits", params=params
        ) or []
        result: list[CommitInfo] = []
        for c in data:
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            message = (commit.get("message") or "").splitlines()[0]
            result.append(
                CommitInfo(
                    sha=c.get("sha", ""),
                    message=message,
                    author=author.get("name", ""),
                    timestamp=author.get("date", ""),
                )
            )
        return result


class GitLabProvider(ApiGitProvider):
    """GitLab vía REST (API v4)."""

    api_base = "https://gitlab.com/api/v4"
    token_env = "GEAS_GITLAB_TOKEN"
    provider_name = "gitlab"

    def _project(self) -> str:
        # GitLab codifica la ruta del proyecto (owner/repo) en el path.
        return quote(f"{self.owner}/{self.repo}", safe="")

    def _find_open_mr(self, branch: str) -> dict | None:
        data = self._request(
            "GET",
            f"/projects/{self._project()}/merge_requests",
            params={"state": "opened", "source_branch": branch, "per_page": 100},
        )
        return data[0] if data else None

    def create_pr(self, title: str, branch: str, base: str = "main") -> str | None:
        data = self._request(
            "POST",
            f"/projects/{self._project()}/merge_requests",
            body={"title": title, "source_branch": branch, "target_branch": base},
        )
        return (data or {}).get("web_url")

    def merge(self, branch: str) -> bool:
        mr = self._find_open_mr(branch)
        if not mr:
            return False
        data = self._request(
            "PUT", f"/projects/{self._project()}/merge_requests/{mr['iid']}/merge"
        )
        return bool(data and data.get("state") == "merged")

    def get_commits(
        self, since: str | None = None, limit: int = 10
    ) -> list[CommitInfo]:
        params: dict[str, str | int] = {"per_page": limit}
        if since:
            params["since"] = since  # fecha ISO-8601 (nativo de la API)
        data = self._request(
            "GET", f"/projects/{self._project()}/repository/commits", params=params
        ) or []
        result: list[CommitInfo] = []
        for c in data:
            result.append(
                CommitInfo(
                    sha=c.get("id", ""),
                    message=c.get("title", ""),
                    author=c.get("author_name", ""),
                    timestamp=c.get("committed_date", ""),
                )
            )
        return result


class BitbucketProvider(ApiGitProvider):
    """Bitbucket vía REST (API 2.0).

    Los app passwords usan HTTP Basic con `usuario:app_password` — el
    token del entorno GEAS_BITBUCKET_TOKEN lleva ese formato exacto.
    """

    api_base = "https://api.bitbucket.org/2.0"
    token_env = "GEAS_BITBUCKET_TOKEN"
    provider_name = "bitbucket"

    def _auth_header(self) -> str:
        return "Basic " + b64encode(self._token.encode()).decode()

    def _find_open_pr(self, branch: str) -> dict | None:
        data = self._request(
            "GET",
            f"/repositories/{self.owner}/{self.repo}/pullrequests",
            params={"state": "OPEN", "pagelen": 100},
        ) or {}
        for pr in data.get("values", []):
            source = (pr.get("source") or {}).get("branch") or {}
            if source.get("name") == branch:
                return pr
        return None

    def create_pr(self, title: str, branch: str, base: str = "main") -> str | None:
        data = self._request(
            "POST",
            f"/repositories/{self.owner}/{self.repo}/pullrequests",
            body={
                "title": title,
                "source": {"branch": {"name": branch}},
                "destination": {"branch": {"name": base}},
            },
        )
        return (data or {}).get("links", {}).get("html", {}).get("href")

    def merge(self, branch: str) -> bool:
        pr = self._find_open_pr(branch)
        if not pr:
            return False
        data = self._request(
            "POST",
            f"/repositories/{self.owner}/{self.repo}/pullrequests/{pr['id']}/merge",
        )
        return bool(data and data.get("state") == "MERGED")

    def get_commits(
        self, since: str | None = None, limit: int = 10
    ) -> list[CommitInfo]:
        params: dict[str, str | int] = {"pagelen": limit}
        data = self._request(
            "GET", f"/repositories/{self.owner}/{self.repo}/commits", params=params
        ) or {}
        result: list[CommitInfo] = []
        for c in data.get("values", []):
            result.append(
                CommitInfo(
                    sha=c.get("hash", ""),
                    message=(c.get("message") or "").splitlines()[0],
                    author=(c.get("author") or {}).get("raw", ""),
                    timestamp=c.get("date", ""),
                )
            )
        return result


def get_provider(provider: str = "local", repo_path: str | None = None, **kwargs) -> GitProvider:
    """Factory (§18): devuelve el proveedor según el nombre.

    local / self-hosted → CLI de git contra el working copy.
    github / gitlab / bitbucket → API REST del proveedor; exigen token
    (GEAS_*_TOKEN) y NUNCA degradan en silencio al local (§42).
    Un nombre desconocido falla claro — no se hace pasar por otro.
    """
    if provider in ("local", "self-hosted"):
        return LocalGitProvider(repo_path)
    providers = {
        "github": GitHubProvider,
        "gitlab": GitLabProvider,
        "bitbucket": BitbucketProvider,
    }
    cls = providers.get((provider or "").lower())
    if cls is None:
        raise ValueError(
            f"Proveedor Git desconocido: {provider!r} (local, self-hosted, "
            "github, gitlab, bitbucket)"
        )
    return cls(repo_path, **kwargs)
