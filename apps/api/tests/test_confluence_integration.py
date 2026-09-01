"""Unit tests for ConfluenceIntegrationService — see
app/services/confluence_integration.py. Every test injects an
`httpx.MockTransport`, so NO test in this file ever makes a real network
call to a Confluence instance — mirrors test_jira_integration.py's exact
"never actually call out" testing philosophy.
"""

import json

import httpx
import pytest

from app.services.confluence_integration import (
    ConfluenceIntegrationError,
    create_page,
    get_page,
    get_space,
    update_page,
    verify_credentials,
)

REAL_TOKEN = "ATATT3xFfGF0ThisIsARealSecretConfluenceApiToken1234567890"
BASE_URL = "https://example.atlassian.net"


def _json_response(status_code: int, data) -> httpx.Response:
    return httpx.Response(status_code, json=data)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- verify_credentials ------------------------------------------------------------------


def test_verify_credentials_returns_account_info():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/user/current"
        assert request.headers["authorization"].startswith("Basic ")
        return _json_response(200, {"accountId": "abc123", "displayName": "Suru", "email": "suru@example.com"})

    user = verify_credentials(BASE_URL, "suru@example.com", "fake-token", transport=_transport(handler))

    assert user.account_id == "abc123"
    assert user.display_name == "Suru"


def test_verify_credentials_raises_on_401_without_leaking_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(401, {"message": "Unauthorized"})

    with pytest.raises(ConfluenceIntegrationError) as exc_info:
        verify_credentials(BASE_URL, "suru@example.com", REAL_TOKEN, transport=_transport(handler))

    assert REAL_TOKEN not in str(exc_info.value)
    assert exc_info.value.status_code == 401


# --- get_space -----------------------------------------------------------------------------


def test_get_space_returns_key_and_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/space/ENG"
        return _json_response(200, {"key": "ENG", "name": "Engineering", "id": 12345})

    space = get_space(BASE_URL, "suru@example.com", "fake-token", "ENG", transport=_transport(handler))

    assert space.key == "ENG"
    assert space.name == "Engineering"


def test_get_space_404_maps_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(404, {"message": "No space could be found"})

    with pytest.raises(ConfluenceIntegrationError) as exc_info:
        get_space(BASE_URL, "suru@example.com", "fake-token", "NOPE", transport=_transport(handler))
    assert exc_info.value.status_code == 404


# --- create_page -----------------------------------------------------------------------------


def test_create_page_wraps_markdown_in_code_macro_and_sends_ancestor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/wiki/rest/api/content"
        body = json.loads(request.content)
        assert body["space"] == {"key": "ENG"}
        assert body["title"] == "My Project — HLD"
        assert "ac:structured-macro" in body["body"]["storage"]["value"]
        assert "# Heading" in body["body"]["storage"]["value"]
        assert body["ancestors"] == [{"id": "999"}]
        return _json_response(
            200,
            {"id": "555", "title": "My Project — HLD", "version": {"number": 1}, "_links": {"webui": "/spaces/ENG/pages/555"}},
        )

    page = create_page(
        BASE_URL, "suru@example.com", "fake-token", space_key="ENG", title="My Project — HLD",
        body_markdown="# Heading\ncontent", parent_id="999", transport=_transport(handler),
    )

    assert page.id == "555"
    assert page.version == 1
    assert page.url == f"{BASE_URL}/wiki/spaces/ENG/pages/555"


def test_create_page_without_parent_omits_ancestors():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "ancestors" not in body
        return _json_response(200, {"id": "1", "version": {"number": 1}, "_links": {"webui": "/x"}})

    create_page(
        BASE_URL, "suru@example.com", "fake-token", space_key="ENG", title="Root",
        body_markdown="root content", transport=_transport(handler),
    )


def test_create_page_validation_error_maps_cleanly():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(400, {"message": "A page with this title already exists"})

    with pytest.raises(ConfluenceIntegrationError) as exc_info:
        create_page(
            BASE_URL, "suru@example.com", REAL_TOKEN, space_key="ENG", title="Dup",
            body_markdown="x", transport=_transport(handler),
        )
    assert REAL_TOKEN not in str(exc_info.value)


# --- get_page / update_page ------------------------------------------------------------------


def test_get_page_returns_live_version():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/content/555"
        assert dict(request.url.params)["expand"] == "version"
        return _json_response(200, {"id": "555", "title": "HLD", "version": {"number": 3}, "_links": {"webui": "/x"}})

    page = get_page(BASE_URL, "suru@example.com", "fake-token", "555", transport=_transport(handler))

    assert page.version == 3


def test_update_page_sends_next_version_number():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/wiki/rest/api/content/555"
        body = json.loads(request.content)
        assert body["version"] == {"number": 4}
        assert "ac:structured-macro" in body["body"]["storage"]["value"]
        return _json_response(200, {"id": "555", "title": "HLD", "version": {"number": 4}, "_links": {"webui": "/x"}})

    page = update_page(
        BASE_URL, "suru@example.com", "fake-token", page_id="555", title="HLD",
        body_markdown="updated content", version=4, transport=_transport(handler),
    )

    assert page.version == 4


def test_write_methods_never_leak_the_token_in_an_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(500, {"message": "Internal error"})

    with pytest.raises(ConfluenceIntegrationError) as exc_info:
        create_page(
            BASE_URL, "suru@example.com", REAL_TOKEN, space_key="ENG", title="x",
            body_markdown="x", transport=_transport(handler),
        )
    assert REAL_TOKEN not in str(exc_info.value)
