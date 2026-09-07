"""Unit tests for GitHubIntegrationService — see
app/services/github_integration.py. Every test injects an
`httpx.MockTransport`, so NO test in this file ever makes a real network
call to github.com — mirrors this codebase's established "never actually
call out" testing philosophy (see test_github_export.py's own
network-guard test).
"""

import base64
import json

import httpx
import pytest

from app.api.routes.github_integration import (
    connect_github,
    create_repository,
    create_repository_snapshot,
    disconnect_github,
    list_connection_repositories,
    remove_repository,
    set_primary_repository,
)
from app.models import AuditLog, Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus, Repository, RepositoryFileIndex
from app.schemas.github_integration import ConnectGitHubRequest, CreateRepositoryRequest, CreateSnapshotRequest
from app.services import github_integration as github_api
from app.services.github_integration import (
    MAX_PREVIEWABLE_FILE_SIZE_BYTES,
    GitHubIntegrationError,
    GitHubRepo,
    GitHubTree,
    GitHubTreeEntry,
    GitHubUser,
    create_branch,
    create_issue_comment,
    create_or_update_file,
    create_pull_request,
    delete_file,
    get_default_branch,
    get_file_sha,
    get_pull_request,
    GitHubRepoSummary,
    get_repository,
    get_repository_tree,
    list_branches,
    list_repositories,
    read_file,
    verify_token,
)

REAL_TOKEN = "ghp_ThisIsARealSecretTokenValue1234567890"


def _json_response(status_code: int, data, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=data, headers=headers or {})


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- verify_token --------------------------------------------------------------------


def test_verify_token_returns_login_and_scopes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        assert request.headers["Authorization"] == "Bearer fake-token"
        return _json_response(200, {"login": "octocat"}, headers={"X-OAuth-Scopes": "repo, read:user"})

    user = verify_token("fake-token", transport=_transport(handler))

    assert user.login == "octocat"
    assert user.scopes == ["repo", "read:user"]


def test_verify_token_raises_on_401_without_leaking_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"message": "Bad credentials"})

    with pytest.raises(GitHubIntegrationError) as exc_info:
        verify_token("a-real-secret-token-value", transport=_transport(handler))

    assert "a-real-secret-token-value" not in str(exc_info.value)
    assert exc_info.value.status_code == 401
    assert "Bad credentials" in str(exc_info.value)


# --- get_repository / get_default_branch ----------------------------------------------


def test_get_repository_returns_default_branch_and_visibility():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world"
        return _json_response(
            200,
            {"default_branch": "main", "description": "A sample repo", "html_url": "https://github.com/octocat/hello-world", "private": False},
        )

    repo = get_repository("token", "octocat", "hello-world", transport=_transport(handler))

    assert repo.default_branch == "main"
    assert repo.is_private is False


def test_get_default_branch_delegates_to_get_repository():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"default_branch": "develop", "description": None, "html_url": "https://x", "private": True})

    branch = get_default_branch("token", "octocat", "hello-world", transport=_transport(handler))

    assert branch == "develop"


def test_get_repository_raises_404_for_unknown_repo():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"message": "Not Found"})

    with pytest.raises(GitHubIntegrationError) as exc_info:
        get_repository("token", "octocat", "does-not-exist", transport=_transport(handler))

    assert exc_info.value.status_code == 404


# --- list_repositories ------------------------------------------------------------------


def test_list_repositories_maps_owner_name_and_visibility():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/repos"
        if request.url.params.get("page") == "2":
            return _json_response(200, [])
        return _json_response(
            200,
            [
                {
                    "owner": {"login": "erik-corder"}, "name": "sdlc-agentic", "full_name": "erik-corder/sdlc-agentic",
                    "default_branch": "main", "description": "A repo", "private": False, "html_url": "https://github.com/erik-corder/sdlc-agentic",
                }
            ],
        )

    repos = list_repositories("token", transport=_transport(handler))

    assert len(repos) == 1
    assert repos[0].owner == "erik-corder"
    assert repos[0].name == "sdlc-agentic"
    assert repos[0].full_name == "erik-corder/sdlc-agentic"


def test_list_repositories_stops_paging_once_a_short_page_comes_back():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("page"))
        return _json_response(200, [{"owner": {"login": "o"}, "name": "r", "full_name": "o/r", "default_branch": "main", "description": None, "private": False, "html_url": "https://x"}])

    repos = list_repositories("token", transport=_transport(handler))

    assert len(calls) == 1  # a page with fewer than 100 items ends pagination
    assert len(repos) == 1


# --- list_branches ---------------------------------------------------------------------


def test_list_branches_returns_branch_names():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return _json_response(200, [])
        return _json_response(200, [{"name": "main"}, {"name": "develop"}])

    branches = list_branches("token", "octocat", "hello-world", transport=_transport(handler))

    assert branches == ["main", "develop"]


# --- get_repository_tree ---------------------------------------------------------------


def test_get_repository_tree_maps_blob_and_tree_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return _json_response(200, {"sha": "abc123"})
        assert request.url.path == "/repos/octocat/hello-world/git/trees/abc123"
        assert request.url.params.get("recursive") == "1"
        return _json_response(
            200,
            {
                "sha": "abc123",
                "truncated": False,
                "tree": [
                    {"path": "src", "type": "tree", "sha": "t1"},
                    {"path": "src/main.py", "type": "blob", "size": 123, "sha": "b1"},
                ],
            },
        )

    tree = get_repository_tree("token", "octocat", "hello-world", "main", transport=_transport(handler))

    assert tree.commit_sha == "abc123"
    assert tree.truncated is False
    assert {e.path: e.entry_type for e in tree.entries} == {"src": "DIRECTORY", "src/main.py": "FILE"}
    assert next(e for e in tree.entries if e.path == "src/main.py").size == 123


def test_get_repository_tree_surfaces_the_truncated_flag_honestly():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return _json_response(200, {"sha": "sha1"})
        return _json_response(200, {"sha": "sha1", "truncated": True, "tree": []})

    tree = get_repository_tree("token", "octocat", "big-repo", "main", transport=_transport(handler))

    assert tree.truncated is True


# --- read_file ---------------------------------------------------------------------------


def test_read_file_decodes_utf8_content():
    content = "print('hello world')\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("ref") == "main"
        return _json_response(
            200, {"sha": "filesha", "size": len(content), "content": base64.b64encode(content.encode()).decode()}
        )

    result = read_file("token", "octocat", "hello-world", "main.py", "main", transport=_transport(handler))

    assert result.content == content
    assert result.is_binary is False
    assert result.truncated is False


def test_read_file_flags_oversized_files_as_truncated_without_downloading_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"sha": "bigsha", "size": MAX_PREVIEWABLE_FILE_SIZE_BYTES + 1, "content": ""})

    result = read_file("token", "octocat", "hello-world", "huge.bin", "main", transport=_transport(handler))

    assert result.truncated is True
    assert result.content is None


def test_read_file_flags_binary_content_instead_of_crashing():
    binary_bytes = bytes([0xFF, 0xFE, 0x00, 0x01, 0x80])

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"sha": "binsha", "size": len(binary_bytes), "content": base64.b64encode(binary_bytes).decode()})

    result = read_file("token", "octocat", "hello-world", "image.png", "main", transport=_transport(handler))

    assert result.is_binary is True
    assert result.content is None


def test_read_file_rejects_a_directory_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, [{"name": "file1.py"}, {"name": "file2.py"}])

    with pytest.raises(GitHubIntegrationError, match="directory"):
        read_file("token", "octocat", "hello-world", "src", "main", transport=_transport(handler))


# --- network failure mapping -------------------------------------------------------------


def test_network_failure_is_wrapped_without_leaking_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(GitHubIntegrationError) as exc_info:
        verify_token("a-real-secret-token", transport=_transport(handler))

    assert "a-real-secret-token" not in str(exc_info.value)


# --- Route-level security-regression tests (direct function calls — no TestClient
# exists in this repo; same technique tests/test_lld_workflow.py established) --------


def _all_audit_extra_data_json(db) -> str:
    return json.dumps([row.extra_data for row in db.query(AuditLog).all()])


def test_connect_github_never_writes_the_token_to_any_audit_log(db, actor, monkeypatch):
    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))

    result = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)

    assert result.token_hint == "****7890"
    assert REAL_TOKEN not in _all_audit_extra_data_json(db)

    connection = db.get(IntegrationConnection, result.id)
    assert REAL_TOKEN not in connection.access_token_encrypted
    integration = db.get(Integration, connection.integration_id)
    assert integration.provider == IntegrationProvider.GITHUB
    assert integration.status == IntegrationStatus.CONNECTED


def test_list_connection_repositories_returns_the_picker_options(db, actor, monkeypatch):
    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))
    connection = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)

    monkeypatch.setattr(
        github_api, "list_repositories",
        lambda token, **kwargs: [
            GitHubRepoSummary(
                owner="octocat", name="hello-world", full_name="octocat/hello-world", default_branch="main",
                description=None, is_private=False, html_url="https://github.com/octocat/hello-world",
            )
        ],
    )

    repos = list_connection_repositories(connection.id, db)

    assert len(repos) == 1
    assert repos[0].full_name == "octocat/hello-world"


def test_list_connection_repositories_rejects_a_disconnected_connection(db, actor, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))
    connection = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)
    disconnect_github(connection.id, db)

    with pytest.raises(HTTPException) as exc_info:
        list_connection_repositories(connection.id, db)
    assert exc_info.value.status_code == 409


def test_create_repository_snapshot_persists_files_and_never_logs_the_token(db, project, actor, monkeypatch):
    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))
    connect_result = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)

    monkeypatch.setattr(
        github_api,
        "get_repository",
        lambda token, owner, repo, **kwargs: GitHubRepo(default_branch="main", description=None, html_url="https://x", is_private=False),
    )
    repo_result = create_repository(
        CreateRepositoryRequest(project_id=project.id, connection_id=connect_result.id, owner="octocat", name="hello-world"), db
    )

    monkeypatch.setattr(
        github_api,
        "get_repository_tree",
        lambda token, owner, repo, ref, **kwargs: GitHubTree(
            commit_sha="deadbeef",
            truncated=False,
            entries=[
                GitHubTreeEntry(path="README.md", entry_type="FILE", size=42, sha="s1"),
                GitHubTreeEntry(path="src", entry_type="DIRECTORY", size=None, sha="s2"),
            ],
        ),
    )
    snapshot_result = create_repository_snapshot(repo_result.id, CreateSnapshotRequest(triggered_by_id=actor.id), db)

    assert snapshot_result.file_count == 2
    assert snapshot_result.commit_sha == "deadbeef"
    assert REAL_TOKEN not in _all_audit_extra_data_json(db)

    files = db.query(RepositoryFileIndex).filter(RepositoryFileIndex.snapshot_id == snapshot_result.id).all()
    assert {f.path for f in files} == {"README.md", "src"}


def test_disconnect_clears_the_stored_token(db, actor, monkeypatch):
    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))
    connect_result = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)

    disconnect_github(connect_result.id, db)

    connection = db.get(IntegrationConnection, connect_result.id)
    assert connection.status == IntegrationStatus.NOT_CONNECTED


# --- Write methods (branch/commit/PR creation) ----------------------------------------


def test_create_branch_resolves_base_ref_then_creates_the_ref():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/repos/octocat/hello-world/commits/main":
            return _json_response(200, {"sha": "base-sha-123"})
        assert request.url.path == "/repos/octocat/hello-world/git/refs"
        body = json.loads(request.content)
        assert body == {"ref": "refs/heads/agent/my-branch", "sha": "base-sha-123"}
        return _json_response(201, {"object": {"sha": "base-sha-123"}})

    sha = create_branch("token", "octocat", "hello-world", new_branch="agent/my-branch", base_ref="main", transport=_transport(handler))

    assert sha == "base-sha-123"
    assert calls == ["/repos/octocat/hello-world/commits/main", "/repos/octocat/hello-world/git/refs"]


def test_create_branch_raises_cleanly_when_the_ref_already_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        if "commits" in request.url.path:
            return _json_response(200, {"sha": "base-sha"})
        return _json_response(422, {"message": "Reference already exists"})

    with pytest.raises(GitHubIntegrationError) as exc_info:
        create_branch("token", "octocat", "hello-world", new_branch="agent/dup", base_ref="main", transport=_transport(handler))
    assert exc_info.value.status_code == 422


def test_get_file_sha_returns_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"message": "Not Found"})

    assert get_file_sha("token", "octocat", "hello-world", "new/file.py", "agent/branch", transport=_transport(handler)) is None


def test_get_file_sha_returns_sha_when_file_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"sha": "abc123", "size": 10, "content": base64.b64encode(b"hi").decode()})

    assert get_file_sha("token", "octocat", "hello-world", "existing.py", "agent/branch", transport=_transport(handler)) == "abc123"


def test_create_or_update_file_sends_base64_content_and_optional_sha():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert base64.b64decode(body["content"]).decode() == "print('hi')\n"
        assert body["branch"] == "agent/my-branch"
        assert body["sha"] == "old-sha"
        return _json_response(200, {"commit": {"sha": "new-commit-sha"}})

    sha = create_or_update_file(
        "token", "octocat", "hello-world", "app.py",
        content="print('hi')\n", message="Update app.py", branch="agent/my-branch", sha="old-sha",
        transport=_transport(handler),
    )
    assert sha == "new-commit-sha"


def test_create_or_update_file_omits_sha_for_a_genuine_create():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "sha" not in body
        return _json_response(201, {"commit": {"sha": "new-commit-sha"}})

    create_or_update_file(
        "token", "octocat", "hello-world", "new.py",
        content="x = 1\n", message="Add new.py", branch="agent/my-branch", sha=None, transport=_transport(handler),
    )


def test_delete_file_sends_sha_and_branch():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        body = json.loads(request.content)
        assert body == {"message": "Remove old.py", "sha": "old-sha", "branch": "agent/my-branch"}
        return _json_response(200, {"commit": {"sha": "delete-commit-sha"}})

    sha = delete_file(
        "token", "octocat", "hello-world", "old.py",
        message="Remove old.py", branch="agent/my-branch", sha="old-sha", transport=_transport(handler),
    )
    assert sha == "delete-commit-sha"


def test_create_pull_request_returns_number_url_and_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {"title": "[ASH] Add feature", "head": "agent/my-branch", "base": "main", "body": "Body text"}
        return _json_response(201, {"number": 42, "html_url": "https://github.com/octocat/hello-world/pull/42", "state": "open"})

    pr = create_pull_request(
        "token", "octocat", "hello-world",
        title="[ASH] Add feature", head="agent/my-branch", base="main", body="Body text", transport=_transport(handler),
    )
    assert pr.number == 42
    assert pr.html_url == "https://github.com/octocat/hello-world/pull/42"
    assert pr.state == "open"


def test_write_methods_never_leak_the_token_in_an_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"message": "Bad credentials"})

    with pytest.raises(GitHubIntegrationError) as exc_info:
        create_pull_request(
            REAL_TOKEN, "octocat", "hello-world", title="t", head="h", base="main", body="b", transport=_transport(handler)
        )
    assert REAL_TOKEN not in str(exc_info.value)


# --- PR Review Agent support: get_pull_request / create_issue_comment ------------------


def test_get_pull_request_returns_title_body_and_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/pulls/7"
        return _json_response(200, {"number": 7, "title": "Add feature", "body": "Description here", "html_url": "https://github.com/octocat/hello-world/pull/7", "state": "open"})

    pr = get_pull_request("token", "octocat", "hello-world", 7, transport=_transport(handler))

    assert pr.number == 7
    assert pr.title == "Add feature"
    assert pr.body == "Description here"
    assert pr.state == "open"


def test_get_pull_request_handles_a_null_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, {"number": 7, "title": "Add feature", "body": None, "html_url": "https://github.com/octocat/hello-world/pull/7", "state": "open"})

    pr = get_pull_request("token", "octocat", "hello-world", 7, transport=_transport(handler))

    assert pr.body == ""


def test_create_issue_comment_posts_the_body_and_returns_the_new_comment():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/octocat/hello-world/issues/7/comments"
        body = json.loads(request.content)
        assert body == {"body": "Consider handling the 404 case."}
        return _json_response(201, {"id": 999, "html_url": "https://github.com/octocat/hello-world/pull/7#issuecomment-999", "body": "Consider handling the 404 case."})

    comment = create_issue_comment("token", "octocat", "hello-world", 7, body="Consider handling the 404 case.", transport=_transport(handler))

    assert comment.id == 999
    assert comment.html_url.endswith("issuecomment-999")


def test_pull_request_and_comment_methods_never_leak_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"message": "Not Found"})

    with pytest.raises(GitHubIntegrationError) as exc_info:
        get_pull_request(REAL_TOKEN, "octocat", "hello-world", 7, transport=_transport(handler))
    assert REAL_TOKEN not in str(exc_info.value)

    with pytest.raises(GitHubIntegrationError) as exc_info:
        create_issue_comment(REAL_TOKEN, "octocat", "hello-world", 7, body="x", transport=_transport(handler))
    assert REAL_TOKEN not in str(exc_info.value)


# --- Multi-repo support: is_primary, set_primary_repository, remove_repository ------------


def _connect_and_create_repo(db, actor, project, monkeypatch, *, owner: str, name: str):
    monkeypatch.setattr(github_api, "verify_token", lambda token, **kwargs: GitHubUser(login="octocat", scopes=["repo"]))
    connection = connect_github(ConnectGitHubRequest(access_token=REAL_TOKEN, connected_by_id=actor.id), db)
    monkeypatch.setattr(
        github_api, "get_repository",
        lambda token, o, r, **kwargs: GitHubRepo(default_branch="main", description=None, html_url="https://x", is_private=False),
    )
    return create_repository(CreateRepositoryRequest(project_id=project.id, connection_id=connection.id, owner=owner, name=name), db)


def test_first_repository_connected_to_a_project_becomes_primary_automatically(db, project, actor, monkeypatch):
    repo = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="backend")
    assert repo.is_primary is True


def test_second_repository_does_not_disturb_the_existing_primary(db, project, actor, monkeypatch):
    first = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="backend")
    second = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="frontend")

    assert first.is_primary is True
    assert second.is_primary is False


def test_set_primary_repository_unsets_every_sibling_in_the_same_project(db, project, actor, monkeypatch):
    first = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="backend")
    second = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="frontend")

    result = set_primary_repository(second.id, db)

    assert result.is_primary is True
    refreshed_first = db.get(Repository, first.id)
    assert refreshed_first.is_primary is False


def test_removing_the_primary_repository_promotes_the_most_recent_remaining_one(db, project, actor, monkeypatch):
    first = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="backend")
    second = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="frontend")
    assert first.is_primary is True

    remove_repository(first.id, db)

    refreshed_second = db.get(Repository, second.id)
    assert refreshed_second.is_primary is True
    assert db.get(Repository, first.id) is None


def test_removing_a_non_primary_repository_leaves_the_primary_untouched(db, project, actor, monkeypatch):
    first = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="backend")
    second = _connect_and_create_repo(db, actor, project, monkeypatch, owner="octocat", name="frontend")

    remove_repository(second.id, db)

    refreshed_first = db.get(Repository, first.id)
    assert refreshed_first.is_primary is True
