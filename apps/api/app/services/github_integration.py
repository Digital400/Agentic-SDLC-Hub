"""GitHubIntegrationService — the read-only GitHub REST API client behind
the GitHub integration foundation (see app/models/repository.py and
app/models/integration_connection.py).

SECURITY (see also app/core/security.py and every caller in
app/api/routes/github_integration.py):
  - Every method here takes the *decrypted* token as a plain argument and
    uses it for exactly one outbound call. Nothing in this module ever
    logs it, stores it, or includes it in an exception message — every
    `GitHubIntegrationError` raised below is built only from the response
    status code and GitHub's own JSON error body, which never echoes back
    the credential that sent the request.
  - Read-only methods (verify_token through read_file, plus
    get_pull_request) are plain GETs. Six write methods now exist —
    create_branch/get_file_sha/create_or_update_file/delete_file/create_pull_request/create_issue_comment
    — for the Implementation Agent's "create a PR from an accepted
    diff" flow (see app/api/routes/implementation_runs.py) and the PR
    Review Agent's "post selected comments" action (see
    app/api/routes/pr_review_runs.py). None of them is ever called by
    this codebase against a repository's default branch, and none of them
    is a merge — there is no merge method here, by construction, not by a
    runtime check. create_issue_comment posts a general PR comment
    (GitHub's PR comments live under the issues API), not a line-anchored
    inline review comment — that would need a commit sha + diff position
    this codebase has no reliable way to compute without a real
    diff-parsing library, the same reasoning behind the PR-creation
    flow's disclosed one-commit-per-file simplification.

Real network calls only — no mock/heuristic fallback exists for this
service (unlike app/services/ai_generation.py's provider fallback):
reading or writing a real repository has no meaningful deterministic
stand-in, so a network/auth failure surfaces as a real, honest
`GitHubIntegrationError` instead.
"""

import base64
import binascii
from dataclasses import dataclass, field

import httpx

_API_BASE_URL = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS = 15.0
# Above this, read_file returns a "too large to preview" result rather
# than downloading/decoding the full blob — a repo can contain arbitrarily
# large binary assets that have no business being pulled into a Python
# process just to preview.
MAX_PREVIEWABLE_FILE_SIZE_BYTES = 500_000


class GitHubIntegrationError(Exception):
    """Raised for any GitHub API failure — auth, not found, rate limit, or
    network. The message is built only from the HTTP status and GitHub's
    own error body; never include the request's own Authorization header
    or any other caller-supplied secret when raising this."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class GitHubUser:
    login: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class GitHubRepo:
    default_branch: str
    description: str | None
    html_url: str
    is_private: bool


@dataclass
class GitHubRepoSummary:
    """One row of GET /user/repos — just enough to populate a repo picker
    (see list_repositories) without a second call per repo."""

    owner: str
    name: str
    full_name: str
    default_branch: str
    description: str | None
    is_private: bool
    html_url: str


@dataclass
class GitHubTreeEntry:
    path: str
    entry_type: str  # "FILE" | "DIRECTORY"
    size: int | None
    sha: str


@dataclass
class GitHubTree:
    commit_sha: str
    entries: list[GitHubTreeEntry]
    truncated: bool


@dataclass
class GitHubPullRequest:
    number: int
    html_url: str
    state: str


@dataclass
class GitHubPullRequestDetail:
    number: int
    title: str
    body: str
    html_url: str
    state: str


@dataclass
class GitHubComment:
    id: int
    html_url: str
    body: str


@dataclass
class GitHubFileContent:
    path: str
    sha: str
    size: int
    # None when the file is too large to preview or isn't decodable as
    # UTF-8 text (e.g. a binary asset) — `truncated`/`is_binary` explain
    # which, rather than silently returning nothing.
    content: str | None
    truncated: bool
    is_binary: bool


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request(
    method: str,
    path: str,
    *,
    token: str,
    params: dict | None = None,
    json_body: dict | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    # `transport` is None (real network) for every production call; tests
    # inject an `httpx.MockTransport` here so this service is fully
    # testable without ever reaching github.com — see tests/test_github_integration.py.
    try:
        with httpx.Client(transport=transport, timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.request(
                method, f"{_API_BASE_URL}{path}", headers=_headers(token), params=params, json=json_body
            )
    except httpx.HTTPError as exc:
        raise GitHubIntegrationError(f"Could not reach GitHub: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("message", detail)
        except ValueError:
            pass
        raise GitHubIntegrationError(f"GitHub API returned {response.status_code}: {detail}", status_code=response.status_code)
    return response


def verify_token(token: str, *, transport: httpx.BaseTransport | None = None) -> GitHubUser:
    """GET /user — the cheapest possible "is this token valid at all"
    check, and what a Connect action calls before ever saving anything."""
    response = _request("GET", "/user", token=token, transport=transport)
    data = response.json()
    scopes_header = response.headers.get("X-OAuth-Scopes", "")
    scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
    return GitHubUser(login=data["login"], scopes=scopes)


def list_repositories(
    token: str, *, transport: httpx.BaseTransport | None = None, max_repos: int = 300
) -> list[GitHubRepoSummary]:
    """GET /user/repos — every repository this token's account can see
    (owned, collaborator, and org repos it's a member of — GitHub's own
    default `affiliation`), newest-updated first. A read, used only to
    populate a repo picker so a project's owner/name is chosen from what
    actually exists rather than typed by hand (see
    app/api/routes/github_integration.py's list_connection_repositories).
    Capped at `max_repos` (default 300 — 3 pages) so one connection with
    an unusually large number of accessible repos can't turn this into an
    unbounded call."""
    repos: list[GitHubRepoSummary] = []
    page = 1
    while len(repos) < max_repos:
        data = _request(
            "GET", "/user/repos", token=token,
            params={"per_page": 100, "page": page, "sort": "updated"}, transport=transport,
        ).json()
        if not data:
            break
        repos.extend(
            GitHubRepoSummary(
                owner=item["owner"]["login"], name=item["name"], full_name=item["full_name"],
                default_branch=item["default_branch"], description=item.get("description"),
                is_private=item.get("private", False), html_url=item["html_url"],
            )
            for item in data
        )
        if len(data) < 100:
            break
        page += 1
    return repos[:max_repos]


def get_repository(token: str, owner: str, repo: str, *, transport: httpx.BaseTransport | None = None) -> GitHubRepo:
    data = _request("GET", f"/repos/{owner}/{repo}", token=token, transport=transport).json()
    return GitHubRepo(
        default_branch=data["default_branch"],
        description=data.get("description"),
        html_url=data["html_url"],
        is_private=data.get("private", False),
    )


def list_branches(token: str, owner: str, repo: str, *, transport: httpx.BaseTransport | None = None) -> list[str]:
    branches: list[str] = []
    page = 1
    while True:
        data = _request(
            "GET", f"/repos/{owner}/{repo}/branches", token=token, params={"per_page": 100, "page": page}, transport=transport
        ).json()
        if not data:
            break
        branches.extend(b["name"] for b in data)
        if len(data) < 100:
            break
        page += 1
    return branches


def get_default_branch(token: str, owner: str, repo: str, *, transport: httpx.BaseTransport | None = None) -> str:
    return get_repository(token, owner, repo, transport=transport).default_branch


def get_repository_tree(
    token: str, owner: str, repo: str, ref: str, *, transport: httpx.BaseTransport | None = None
) -> GitHubTree:
    """Resolves `ref` (a branch, tag, or sha) to a commit, then fetches
    that commit's full tree recursively. `ref` is passed straight through
    to GitHub — it is never used to construct a shell command or file
    path, only as a URL path/query segment in an httpx-built request."""
    commit_sha = _request("GET", f"/repos/{owner}/{repo}/commits/{ref}", token=token, transport=transport).json()["sha"]
    data = _request(
        "GET", f"/repos/{owner}/{repo}/git/trees/{commit_sha}", token=token, params={"recursive": "1"}, transport=transport
    ).json()

    entries = [
        GitHubTreeEntry(
            path=item["path"],
            entry_type="DIRECTORY" if item["type"] == "tree" else "FILE",
            size=item.get("size"),
            sha=item["sha"],
        )
        for item in data.get("tree", [])
        if item["type"] in ("blob", "tree")
    ]
    return GitHubTree(commit_sha=commit_sha, entries=entries, truncated=bool(data.get("truncated", False)))


def read_file(
    token: str, owner: str, repo: str, path: str, ref: str, *, transport: httpx.BaseTransport | None = None
) -> GitHubFileContent:
    data = _request(
        "GET", f"/repos/{owner}/{repo}/contents/{path}", token=token, params={"ref": ref}, transport=transport
    ).json()
    if isinstance(data, list):
        raise GitHubIntegrationError(f"'{path}' is a directory, not a file.")

    size = data.get("size", 0)
    sha = data["sha"]
    if size > MAX_PREVIEWABLE_FILE_SIZE_BYTES:
        return GitHubFileContent(path=path, sha=sha, size=size, content=None, truncated=True, is_binary=False)

    raw = base64.b64decode(data.get("content", ""))
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, binascii.Error):
        return GitHubFileContent(path=path, sha=sha, size=size, content=None, truncated=False, is_binary=True)

    return GitHubFileContent(path=path, sha=sha, size=size, content=text, truncated=False, is_binary=False)


# --- Write methods (see module docstring's SECURITY note) --------------------------


def create_branch(
    token: str, owner: str, repo: str, *, new_branch: str, base_ref: str, transport: httpx.BaseTransport | None = None
) -> str:
    """Creates `new_branch` pointing at `base_ref`'s current commit.
    `base_ref` is only ever read from, never written to — this call itself
    is the only thing that touches `refs/`, and it only ever creates a new
    ref, never moves or deletes an existing one. Returns the new branch's
    commit sha. A 422 (ref already exists) surfaces as a plain
    GitHubIntegrationError, same as any other failure — the caller decides
    what that means (see app/api/routes/implementation_runs.py)."""
    base_sha = _request("GET", f"/repos/{owner}/{repo}/commits/{base_ref}", token=token, transport=transport).json()["sha"]
    data = _request(
        "POST", f"/repos/{owner}/{repo}/git/refs", token=token,
        json_body={"ref": f"refs/heads/{new_branch}", "sha": base_sha}, transport=transport,
    ).json()
    return data["object"]["sha"]


def get_file_sha(
    token: str, owner: str, repo: str, path: str, ref: str, *, transport: httpx.BaseTransport | None = None
) -> str | None:
    """The blob sha `create_or_update_file` needs to update an existing
    file — None means the file doesn't exist at `ref` yet (a genuine
    create, not an update). Any other failure still raises."""
    try:
        return read_file(token, owner, repo, path, ref, transport=transport).sha
    except GitHubIntegrationError as exc:
        if exc.status_code == 404:
            return None
        raise


def create_or_update_file(
    token: str,
    owner: str,
    repo: str,
    path: str,
    *,
    content: str,
    message: str,
    branch: str,
    sha: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """PUT .../contents/{path} — creates `path` on `branch` if `sha` is
    None, otherwise updates the exact version `sha` names (GitHub rejects
    an update whose `sha` doesn't match the file's current version, which
    is the right failure mode for a stale/concurrent edit). Returns the
    new commit's sha. Never called by this codebase with `branch` equal to
    a repository's default branch — see module docstring."""
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    data = _request("PUT", f"/repos/{owner}/{repo}/contents/{path}", token=token, json_body=body, transport=transport).json()
    return data["commit"]["sha"]


def delete_file(
    token: str, owner: str, repo: str, path: str, *, message: str, branch: str, sha: str, transport: httpx.BaseTransport | None = None
) -> str:
    """DELETE .../contents/{path} — `sha` is required by GitHub's API
    (the exact version being deleted). Returns the new commit's sha. Never
    called by this codebase with `branch` equal to a repository's default
    branch — see module docstring."""
    body = {"message": message, "sha": sha, "branch": branch}
    data = _request("DELETE", f"/repos/{owner}/{repo}/contents/{path}", token=token, json_body=body, transport=transport).json()
    return data["commit"]["sha"]


def create_pull_request(
    token: str,
    owner: str,
    repo: str,
    *,
    title: str,
    head: str,
    base: str,
    body: str,
    transport: httpx.BaseTransport | None = None,
) -> GitHubPullRequest:
    """POST .../pulls — `head` is the feature branch, `base` is where it
    would merge into (commonly, but not necessarily, the repo's default
    branch). Opening a PR never itself writes to `base`; GitHub only
    merges a PR on an explicit, separate merge action this codebase never
    calls."""
    data = _request(
        "POST", f"/repos/{owner}/{repo}/pulls", token=token,
        json_body={"title": title, "head": head, "base": base, "body": body}, transport=transport,
    ).json()
    return GitHubPullRequest(number=data["number"], html_url=data["html_url"], state=data["state"])


def get_pull_request(
    token: str, owner: str, repo: str, pr_number: int, *, transport: httpx.BaseTransport | None = None
) -> GitHubPullRequestDetail:
    """GET .../pulls/{pr_number} — a single PR's title/description/state,
    for the PR Review Agent's "PR title and description" input (see
    app/services/pr_review_agent.py). A read; never called with a write
    intent."""
    data = _request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}", token=token, transport=transport).json()
    return GitHubPullRequestDetail(
        number=data["number"], title=data["title"], body=data.get("body") or "",
        html_url=data["html_url"], state=data["state"],
    )


def create_issue_comment(
    token: str, owner: str, repo: str, pr_number: int, *, body: str, transport: httpx.BaseTransport | None = None
) -> GitHubComment:
    """POST .../issues/{pr_number}/comments — a general PR comment, not a
    line-anchored inline review comment (see module docstring for why).
    Used only by the PR Review Agent's explicit, human-triggered "post
    selected comments" action — never automatically, and never a merge."""
    data = _request(
        "POST", f"/repos/{owner}/{repo}/issues/{pr_number}/comments", token=token,
        json_body={"body": body}, transport=transport,
    ).json()
    return GitHubComment(id=data["id"], html_url=data["html_url"], body=data.get("body", body))
