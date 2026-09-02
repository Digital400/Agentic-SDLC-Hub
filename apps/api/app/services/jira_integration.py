"""JiraIntegrationService — the real Jira Cloud REST API client behind the
Jira integration (see app/models/jira_project_link.py and
app/models/jira_issue_link.py).

SECURITY (see also app/core/security.py and every caller in
app/api/routes/jira_integration.py):
  - Jira Cloud authenticates via HTTP Basic (account email + API token),
    not a bearer token — every method here takes the *decrypted* token
    (and the email, which is non-secret and stored in
    Integration.config_json) and uses them for exactly one outbound call.
    Nothing in this module ever logs the token, stores it, or includes it
    in an exception message — every `JiraIntegrationError` raised below is
    built only from the response status code and Jira's own JSON error
    body, which never echoes back the credential that sent the request.
  - Five methods exist: verify_credentials, get_project (both reads),
    create_issue (a write — creates exactly the one issue described, never
    more), get_issue_status (a read, for status sync), and add_comment (a
    write, but purely additive activity — see its own docstring for why
    this doesn't count as the update it sounds like). There is no update,
    delete, transition, or merge-equivalent method anywhere in this
    module that mutates an issue's own fields or workflow status —
    enforced by construction, the same way
    app/services/github_integration.py has no merge method.

Real network calls only — no mock/heuristic fallback exists for this
service (unlike app/services/ai_generation.py's provider fallback):
reading or writing a real Jira project has no meaningful deterministic
stand-in, so a network/auth failure surfaces as a real, honest
`JiraIntegrationError` instead.
"""

from dataclasses import dataclass

import httpx

_REQUEST_TIMEOUT_SECONDS = 15.0
_API_VERSION = "3"


class JiraIntegrationError(Exception):
    """Raised for any Jira API failure — auth, not found, validation, or
    network. The message is built only from the HTTP status and Jira's
    own error body; never include the API token or any other
    caller-supplied secret when raising this."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class JiraUser:
    account_id: str
    display_name: str
    email: str | None


@dataclass
class JiraProject:
    key: str
    name: str
    id: str


@dataclass
class JiraIssue:
    key: str
    url: str
    id: str = ""


def _auth(email: str, api_token: str) -> httpx.BasicAuth:
    return httpx.BasicAuth(email, api_token)


def _to_adf(text: str) -> dict:
    """Wraps plain text in the minimal Atlassian Document Format Jira's
    v3 issue API requires for `description` — a single paragraph, no
    bold/checklist/heading formatting. A disclosed simplification: this
    module doesn't render Markdown (bullet lists, bold) into real ADF
    nodes, just plain text — same "don't hand-roll a full rich-format
    renderer for one field" reasoning as other simplifications in this
    codebase."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}] if text else [],
    }


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
    # testable without ever reaching a real Jira instance — see
    # tests/test_jira_integration.py.
    try:
        with httpx.Client(auth=_auth(email, api_token), timeout=_REQUEST_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.request(method, f"{base_url.rstrip('/')}{path}", params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise JiraIntegrationError(f"Could not reach Jira: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        detail = response.text
        try:
            data = response.json()
            detail = data.get("errorMessages") or data.get("errors") or detail
        except ValueError:
            pass
        raise JiraIntegrationError(f"Jira API returned {response.status_code}: {detail}", status_code=response.status_code)
    return response


def verify_credentials(
    base_url: str, email: str, api_token: str, *, transport: httpx.BaseTransport | None = None
) -> JiraUser:
    """GET /rest/api/3/myself — the cheapest possible "are these
    credentials valid" check, called before ever saving a connection."""
    data = _request(
        "GET", f"/rest/api/{_API_VERSION}/myself", base_url=base_url, email=email, api_token=api_token, transport=transport
    ).json()
    return JiraUser(account_id=data["accountId"], display_name=data.get("displayName", ""), email=data.get("emailAddress"))


def get_project(
    base_url: str, email: str, api_token: str, project_key: str, *, transport: httpx.BaseTransport | None = None
) -> JiraProject:
    data = _request(
        "GET", f"/rest/api/{_API_VERSION}/project/{project_key}", base_url=base_url, email=email, api_token=api_token,
        transport=transport,
    ).json()
    return JiraProject(key=data["key"], name=data.get("name", data["key"]), id=data["id"])


def create_issue(
    base_url: str,
    email: str,
    api_token: str,
    *,
    project_key: str,
    issue_type: str,
    summary: str,
    description: str,
    parent_key: str | None = None,
    labels: list[str] | None = None,
    priority: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> JiraIssue:
    """POST /rest/api/3/issue — creates exactly the one issue described.
    `parent_key` sets Jira's `parent` field, used for a Sub-task's parent
    Story/Task or a Story's parent Epic. `priority` is Jira's real,
    standard field (one of Jira's 5-point scale — see
    app/services/jira_export.py's normalize_jira_priority) — unlike Story
    Points/Sprint (see app/services/story_jira_sync.py's module docstring
    for why those are description text instead, never a guessed custom
    field). Never called by this codebase with anything but a single,
    explicitly human-selected item — see
    app/api/routes/jira_integration.py's /jira/push and
    /jira/stories/{id}/sync."""
    fields: dict = {
        "project": {"key": project_key},
        "summary": summary,
        "description": _to_adf(description),
        "issuetype": {"name": issue_type},
    }
    if parent_key:
        fields["parent"] = {"key": parent_key}
    if labels:
        fields["labels"] = labels
    if priority:
        fields["priority"] = {"name": priority}

    data = _request(
        "POST", f"/rest/api/{_API_VERSION}/issue", base_url=base_url, email=email, api_token=api_token,
        json_body={"fields": fields}, transport=transport,
    ).json()
    issue_key = data["key"]
    return JiraIssue(key=issue_key, id=str(data.get("id", "")), url=f"{base_url.rstrip('/')}/browse/{issue_key}")


def add_comment(
    base_url: str, email: str, api_token: str, issue_key: str, body: str, *, transport: httpx.BaseTransport | None = None
) -> None:
    """POST /rest/api/3/issue/{key}/comment — the Done gate's "optionally
    update Jira status" (see app/services/story_done_gate.py). This is
    NOT the update/delete/transition/merge-equivalent call this module's
    HARD RULE forbids — a comment never mutates the issue's own fields or
    workflow status, it only appends activity, the same category GitHub's
    create_issue_comment already is for PR review comments in this
    codebase. There is still no way to flip the issue's actual status
    field anywhere in this client — a completion comment is the honest
    equivalent this integration can offer."""
    _request(
        "POST", f"/rest/api/{_API_VERSION}/issue/{issue_key}/comment", base_url=base_url, email=email, api_token=api_token,
        json_body={"body": _to_adf(body)}, transport=transport,
    )


def get_issue_status(
    base_url: str, email: str, api_token: str, issue_key: str, *, transport: httpx.BaseTransport | None = None
) -> str:
    """GET /rest/api/3/issue/{key}?fields=status — for requirement 8's
    "sync status from Jira" action. A read; never called to transition
    an issue's status, only to observe it."""
    data = _request(
        "GET", f"/rest/api/{_API_VERSION}/issue/{issue_key}", base_url=base_url, email=email, api_token=api_token,
        params={"fields": "status"}, transport=transport,
    ).json()
    return data["fields"]["status"]["name"]
