"""Unit tests for JiraIntegrationService — see
app/services/jira_integration.py. Every test injects an
`httpx.MockTransport`, so NO test in this file ever makes a real network
call to a Jira instance — mirrors test_github_integration.py's exact
"never actually call out" testing philosophy.
"""

import json

import httpx
import pytest

from app.services.jira_integration import (
    JiraIntegrationError,
    add_comment,
    create_issue,
    get_issue_status,
    get_project,
    list_projects,
    verify_credentials,
)

REAL_TOKEN = "ATATT3xFfGF0ThisIsARealSecretJiraApiToken1234567890"
BASE_URL = "https://example.atlassian.net"


def _json_response(status_code: int, data) -> httpx.Response:
    return httpx.Response(status_code, json=data)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- verify_credentials ------------------------------------------------------------------


def test_verify_credentials_returns_account_info():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/myself"
        assert request.headers["authorization"].startswith("Basic ")
        return _json_response(200, {"accountId": "abc123", "displayName": "Suru", "emailAddress": "suru@example.com"})

    user = verify_credentials(BASE_URL, "suru@example.com", "fake-token", transport=_transport(handler))

    assert user.account_id == "abc123"
    assert user.display_name == "Suru"


def test_verify_credentials_raises_on_401_without_leaking_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"errorMessages": ["Unauthorized"]})

    with pytest.raises(JiraIntegrationError) as exc_info:
        verify_credentials(BASE_URL, "suru@example.com", REAL_TOKEN, transport=_transport(handler))

    assert REAL_TOKEN not in str(exc_info.value)
    assert exc_info.value.status_code == 401


# --- get_project -------------------------------------------------------------------------


def test_get_project_returns_key_and_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/project/PROJ"
        return _json_response(200, {"key": "PROJ", "name": "My Project", "id": "10000"})

    project = get_project(BASE_URL, "suru@example.com", "fake-token", "PROJ", transport=_transport(handler))

    assert project.key == "PROJ"
    assert project.name == "My Project"


def test_get_project_404_maps_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"errorMessages": ["No project could be found"]})

    with pytest.raises(JiraIntegrationError) as exc_info:
        get_project(BASE_URL, "suru@example.com", "fake-token", "NOPE", transport=_transport(handler))
    assert exc_info.value.status_code == 404


# --- create_issue --------------------------------------------------------------------------


def test_create_issue_sends_adf_description_and_parent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/rest/api/3/issue"
        body = json.loads(request.content)
        fields = body["fields"]
        assert fields["project"] == {"key": "PROJ"}
        assert fields["issuetype"] == {"name": "Story"}
        assert fields["summary"] == "As a user I want to log in"
        assert fields["description"]["type"] == "doc"
        assert fields["description"]["content"][0]["content"][0]["text"] == "Some description."
        assert fields["parent"] == {"key": "PROJ-1"}
        return _json_response(201, {"key": "PROJ-42", "id": "10042", "self": "..."})

    issue = create_issue(
        BASE_URL, "suru@example.com", "fake-token", project_key="PROJ", issue_type="Story",
        summary="As a user I want to log in", description="Some description.", parent_key="PROJ-1",
        transport=_transport(handler),
    )

    assert issue.key == "PROJ-42"
    assert issue.url == f"{BASE_URL}/browse/PROJ-42"


def test_create_issue_without_parent_omits_the_field():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "parent" not in body["fields"]
        return _json_response(201, {"key": "PROJ-1"})

    create_issue(
        BASE_URL, "suru@example.com", "fake-token", project_key="PROJ", issue_type="Epic",
        summary="Epic summary", description="", transport=_transport(handler),
    )


def test_create_issue_validation_error_maps_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(400, {"errors": {"summary": "Summary is required."}})

    with pytest.raises(JiraIntegrationError) as exc_info:
        create_issue(
            BASE_URL, "suru@example.com", REAL_TOKEN, project_key="PROJ", issue_type="Bug",
            summary="", description="", transport=_transport(handler),
        )
    assert REAL_TOKEN not in str(exc_info.value)


# --- get_issue_status ----------------------------------------------------------------------


def test_get_issue_status_returns_status_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/PROJ-42"
        assert dict(request.url.params)["fields"] == "status"
        return _json_response(200, {"fields": {"status": {"name": "In Progress"}}})

    status_name = get_issue_status(BASE_URL, "suru@example.com", "fake-token", "PROJ-42", transport=_transport(handler))

    assert status_name == "In Progress"


# --- add_comment (Done gate's "optionally update Jira status") ---------------------------


def test_add_comment_posts_to_the_issues_comment_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/PROJ-42/comment"
        captured["body"] = json.loads(request.content)
        return _json_response(201, {"id": "10001"})

    add_comment(BASE_URL, "suru@example.com", "fake-token", "PROJ-42", "Story is DONE.", transport=_transport(handler))

    # ADF-wrapped, not a raw status field write — see this function's own
    # docstring for why this isn't the forbidden update/transition call.
    assert captured["body"]["body"]["content"][0]["content"][0]["text"] == "Story is DONE."


def test_add_comment_failure_raises_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"errorMessages": ["Issue does not exist"]})

    with pytest.raises(JiraIntegrationError):
        add_comment(BASE_URL, "suru@example.com", "fake-token", "NOPE-1", "x", transport=_transport(handler))


def test_write_methods_never_leak_the_token_in_an_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(500, {"errorMessages": ["Internal error"]})

    with pytest.raises(JiraIntegrationError) as exc_info:
        create_issue(
            BASE_URL, "suru@example.com", REAL_TOKEN, project_key="PROJ", issue_type="Bug",
            summary="x", description="x", transport=_transport(handler),
        )
    assert REAL_TOKEN not in str(exc_info.value)


# --- list_projects -------------------------------------------------------------------------


def test_list_projects_maps_key_and_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/project/search"
        return _json_response(200, {
            "isLast": True,
            "values": [{"key": "LOYAL", "name": "Loyalty Portal", "id": "1"}, {"key": "ENG", "name": "Engineering", "id": "2"}],
        })

    projects = list_projects(BASE_URL, "suru@example.com", "fake-token", transport=_transport(handler))
    assert [(p.key, p.name) for p in projects] == [("LOYAL", "Loyalty Portal"), ("ENG", "Engineering")]


def test_list_projects_pages_until_last():
    pages = [
        {"isLast": False, "values": [{"key": "A", "name": "A", "id": "1"}]},
        {"isLast": True, "values": [{"key": "B", "name": "B", "id": "2"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        start_at = int(request.url.params.get("startAt", "0"))
        return _json_response(200, pages[0 if start_at == 0 else 1])

    projects = list_projects(BASE_URL, "suru@example.com", "fake-token", transport=_transport(handler))
    assert [p.key for p in projects] == ["A", "B"]


def test_list_projects_never_leaks_the_token_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(403, {"errorMessages": ["Forbidden"]})

    with pytest.raises(JiraIntegrationError) as exc_info:
        list_projects(BASE_URL, "suru@example.com", REAL_TOKEN, transport=_transport(handler))
    assert REAL_TOKEN not in str(exc_info.value)
