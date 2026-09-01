"""ConfluenceIntegrationService — the real Confluence Cloud REST API client
behind the Confluence integration (see app/models/confluence_space_link.py
and app/models/confluence_page_link.py).

SECURITY (see also app/core/security.py and every caller in
app/api/routes/confluence_integration.py):
  - Confluence Cloud authenticates via HTTP Basic (account email + API
    token) — the same scheme as Jira Cloud, since both are Atlassian
    products sharing one auth model. Every method here takes the
    *decrypted* token (and the email, non-secret, stored in
    Integration.config_json) and uses them for exactly one outbound call.
    Nothing in this module ever logs the token, stores it, or includes it
    in an exception message.
  - Only five methods exist: verify_credentials, get_space, get_page (all
    reads), create_page and update_page (writes — each touches exactly
    the one page described). There is no delete, move, or
    permissions-change method anywhere in this module — enforced by
    construction, same as app/services/jira_integration.py.

Real network calls only — no mock/heuristic fallback exists for this
service (unlike app/services/ai_generation.py's provider fallback): a
network/auth failure surfaces as a real, honest ConfluenceIntegrationError
instead.
"""

from dataclasses import dataclass

import httpx

_REQUEST_TIMEOUT_SECONDS = 15.0
_API_PREFIX = "/wiki/rest/api"


class ConfluenceIntegrationError(Exception):
    """Raised for any Confluence API failure — auth, not found, validation,
    or network. The message is built only from the HTTP status and
    Confluence's own error body; never include the API token or any other
    caller-supplied secret when raising this."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ConfluenceUser:
    account_id: str
    display_name: str
    email: str | None


@dataclass
class ConfluenceSpace:
    key: str
    name: str
    id: str


@dataclass
class ConfluencePage:
    id: str
    title: str
    url: str
    version: int


def _auth(email: str, api_token: str) -> httpx.BasicAuth:
    return httpx.BasicAuth(email, api_token)


def _to_storage_format(markdown_text: str) -> str:
    """Wraps the artifact's Markdown in Confluence's own `code` structured
    macro. Confluence pages use XHTML "storage format", not Markdown — a
    real Markdown-to-storage-format renderer (headings/bold/tables/lists)
    is a nontrivial piece of infrastructure this codebase doesn't have and
    isn't adding for one field. The code macro preserves the text exactly,
    readably, monospaced — not rendered as rich Confluence formatting, but
    never lossy or garbled. Same "don't hand-roll a full rich-format
    renderer for one field" reasoning as jira_integration.py's `_to_adf`."""
    escaped = (markdown_text or "").replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        '<ac:parameter ac:name="language">markdown</ac:parameter>'
        f"<ac:plain-text-body><![CDATA[{escaped}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def _request(
    method: str,
    path: str,
    *,
    base_url: str,
    email: str,
    api_token: str,
    params: dict | None = None,
    json_body: dict | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    # `transport` is None (real network) for every production call; tests
    # inject an `httpx.MockTransport` here so this service is fully
    # testable without ever reaching a real Confluence instance — see
    # tests/test_confluence_integration.py.
    try:
        with httpx.Client(auth=_auth(email, api_token), timeout=_REQUEST_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.request(method, f"{base_url.rstrip('/')}{path}", params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise ConfluenceIntegrationError(f"Could not reach Confluence: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        detail = response.text
        try:
            data = response.json()
            detail = data.get("message") or detail
        except ValueError:
            pass
        raise ConfluenceIntegrationError(
            f"Confluence API returned {response.status_code}: {detail}", status_code=response.status_code
        )
    return response


def verify_credentials(
    base_url: str, email: str, api_token: str, *, transport: httpx.BaseTransport | None = None
) -> ConfluenceUser:
    """GET /wiki/rest/api/user/current — the cheapest possible "are these
    credentials valid" check, called before ever saving a connection."""
    data = _request(
        "GET", f"{_API_PREFIX}/user/current", base_url=base_url, email=email, api_token=api_token, transport=transport
    ).json()
    return ConfluenceUser(
        account_id=data.get("accountId", ""), display_name=data.get("displayName", ""), email=data.get("email")
    )


def get_space(
    base_url: str, email: str, api_token: str, space_key: str, *, transport: httpx.BaseTransport | None = None
) -> ConfluenceSpace:
    data = _request(
        "GET", f"{_API_PREFIX}/space/{space_key}", base_url=base_url, email=email, api_token=api_token,
        transport=transport,
    ).json()
    return ConfluenceSpace(key=data["key"], name=data.get("name", data["key"]), id=str(data["id"]))


def create_page(
    base_url: str,
    email: str,
    api_token: str,
    *,
    space_key: str,
    title: str,
    body_markdown: str,
    parent_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> ConfluencePage:
    """POST /wiki/rest/api/content — creates exactly the one page
    described. `parent_id` sets Confluence's `ancestors` field, used for
    every artifact page's parent (the project's root hierarchy page — see
    app/models/confluence_space_link.py's root_page_id). Never called by
    this codebase with anything but a single, explicitly human-selected
    artifact type — see app/api/routes/confluence_integration.py's
    /confluence/publish."""
    body: dict = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {"storage": {"value": _to_storage_format(body_markdown), "representation": "storage"}},
    }
    if parent_id:
        body["ancestors"] = [{"id": parent_id}]

    data = _request(
        "POST", f"{_API_PREFIX}/content", base_url=base_url, email=email, api_token=api_token, json_body=body,
        transport=transport,
    ).json()
    page_id = data["id"]
    return ConfluencePage(
        id=page_id,
        title=data.get("title", title),
        url=f"{base_url.rstrip('/')}/wiki{data.get('_links', {}).get('webui', '')}",
        version=data.get("version", {}).get("number", 1),
    )


def get_page(
    base_url: str, email: str, api_token: str, page_id: str, *, transport: httpx.BaseTransport | None = None
) -> ConfluencePage:
    """GET /wiki/rest/api/content/{id}?expand=version — a read, used to
    fetch the live version number immediately before an update_page call
    (Confluence rejects an update whose `version.number` doesn't exactly
    equal the current version + 1)."""
    data = _request(
        "GET", f"{_API_PREFIX}/content/{page_id}", base_url=base_url, email=email, api_token=api_token,
        params={"expand": "version"}, transport=transport,
    ).json()
    return ConfluencePage(
        id=data["id"],
        title=data.get("title", ""),
        url=f"{base_url.rstrip('/')}/wiki{data.get('_links', {}).get('webui', '')}",
        version=data.get("version", {}).get("number", 1),
    )


def update_page(
    base_url: str,
    email: str,
    api_token: str,
    *,
    page_id: str,
    title: str,
    body_markdown: str,
    version: int,
    transport: httpx.BaseTransport | None = None,
) -> ConfluencePage:
    """PUT /wiki/rest/api/content/{id} — updates exactly the one existing
    page described, at `version` (the next version number: current + 1).
    Never creates a new page; the caller (/confluence/publish) is
    responsible for deciding create vs. update via ConfluencePageLink."""
    body = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version},
        "body": {"storage": {"value": _to_storage_format(body_markdown), "representation": "storage"}},
    }
    data = _request(
        "PUT", f"{_API_PREFIX}/content/{page_id}", base_url=base_url, email=email, api_token=api_token, json_body=body,
        transport=transport,
    ).json()
    return ConfluencePage(
        id=data["id"],
        title=data.get("title", title),
        url=f"{base_url.rstrip('/')}/wiki{data.get('_links', {}).get('webui', '')}",
        version=data.get("version", {}).get("number", version),
    )
