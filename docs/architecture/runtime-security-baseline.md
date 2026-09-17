# Runtime Security Baseline (Phase 07)

**Status:** Inspection findings + the new, additive `app/runtime_security`
package this phase built on top of them. No existing route's authorization
behavior was changed — see "What this phase does and does not change"
below.

## 1. Current authentication implementation: none

Confirmed by direct inspection (and already flagged in the Phase 00
baseline, section 10): **this codebase has no authentication layer.**
There is no session, no cookie, no bearer-token verification, no login
endpoint anywhere in `apps/api/app`. `app/services/permissions.py`'s own
module docstring states this explicitly:

> "No authentication exists yet... there's no session or token to derive
> 'the current user' from. Every check here instead takes the explicit
> actor id already present on the mutating request."

Every mutating endpoint across every phase inspected (Phase 00 through
06) trusts an explicit actor id supplied in the request body —
`triggered_by_user_id`, `created_by_id`, `reviewer_id`,
`approved_by_user_id`, `cancellation_requested_by_id`, and so on. A
caller can name ANY existing user id and the request is attributed to
that user with no proof they are actually that person. This is this
phase's central, literal finding — and its primary target.

## 2. Current authorization implementation

`app/services/permissions.py` is a real, structural authorization layer
over that *unverified* actor id: `STAGE_EDIT_ROLES` / `STAGE_APPROVE_ROLES`
(per-workflow-stage role tables), checked against
`app.models.enums.UserRole` (the actor's own row, looked up by the
trusted-but-unverified id). `ADMIN` always passes; `VIEWER` is denied by
construction. This IS real 403-enforcing logic — the gap is entirely
upstream, in *who the actor is claimed to be*, not in whether a role
check happens once identity is assumed.

## 3. A second, previously-undocumented finding: `ProjectMember`/`ProjectRole` is dormant

`app/models/project.py` defines `ProjectMember` (a `user_id` +
`project_id` + `role: ProjectRole` row) — real schema, clearly intended
as per-project RBAC. Direct inspection of every route and of
`permissions.py` found **exactly one write** to this table
(`app/api/routes/projects.py`'s `create_project`, which gives the
creator `ProjectRole.OWNER`) and **zero reads** anywhere in the
codebase. No route, no service, checks `ProjectMember` before allowing an
action on a project. A user with the right *global* `UserRole` can act on
*any* project regardless of whether they are a member of it at all —
there has never been real project isolation in this codebase.

## 4. Current credential handling

`app/core/security.py` (Fernet symmetric encryption) + `IntegrationConnection.
access_token_encrypted` — one long-lived GitHub PAT per organization,
encrypted at rest, decrypted transiently once per outbound call, never
logged (Phase 00 baseline, confirmed still accurate). No GitHub App
support, no short-lived token issuance, no credential broker of any kind
existed before this phase.

## 5. Current audit logging

`app/services/audit.py`'s `record_audit_log` — a real, already-used
mechanism (every phase in this arc uses it). It records *what happened*
(action, entity, actor id, extra_data) but nothing in the codebase before
this phase recorded *an authorization decision itself* — there was no
concept of "this action was evaluated against a role/policy and
allowed/denied," only "this action happened."

## 6. What this phase does and does not change

**Adds** (`apps/api/app/runtime_security/`, fully additive, fully
tested — see the `tests/test_runtime_security_*.py` files):

- Validated OIDC authentication (`oidc_provider.py`) — real JWKS-based
  signature verification, issuer/audience/expiry checks, resolved to a
  real platform `User` row. Works for any standards-compliant IdP.
- A Microsoft Entra ID preset (`entra_id.py`) over the same OIDC
  validator — Entra ID is a standard OIDC issuer, so no separate
  token-validation code exists for it.
- A local-development identity adapter (`local_dev_provider.py`),
  double-gated (`ENVIRONMENT=="local"` AND `ALLOW_LOCAL_DEV_AUTH=True`,
  both checked at construction).
- `get_current_actor` (`dependencies.py`) — the FastAPI dependency a
  route adopts to require server-derived identity instead of a trusted
  body field.
- A new `RuntimeRole` enum (8 roles) and `RuntimeRoleAssignment` table
  (`rbac.py`) — a REAL, enforced org/project-scoped grant, with genuine
  project isolation (`RBACService.is_project_member`), deliberately
  separate from both the existing `UserRole` (content-authoring) and
  `ProjectRole`/`ProjectMember` (dormant, per finding #3) systems.
- `AuthorizationService` (`authorization.py`) — role + project-isolation
  checks, a fixed and unconditional human-approval requirement for
  `REPOSITORY_PUSH` / `PULL_REQUEST_CREATE` / `PULL_REQUEST_COMMENT` /
  `INFRASTRUCTURE_ACTION` (including a **self-approval prohibition**,
  found and fixed while writing this phase's own tests), and a
  data-classification restriction for external runtimes. Every decision
  is audited via `record_audit_log`.
- `RuntimeCredentialBroker` (`credential_broker.py`) — prefers short-lived
  GitHub App installation tokens (real RS256 JWT signing + exchange),
  falls back to the existing encrypted PAT. Never issues a
  developer-local credential (there is no code path that could).
- `SecretProvider` abstraction (`secret_provider.py`) — one env-backed
  implementation today, the seam a real vault client replaces later.
- `SecurityGateService` (`security_gate.py`) — the enforcement point for
  "keep all external coding runtimes disabled until this security gate
  passes," wired into `app.services.agent_jobs.select_dispatcher`'s
  `"celery"` branch (Phase 06's only actual external-runtime boundary).

**Deliberately does not change:** every existing route from Phases 00-06
keeps trusting its body-supplied actor id exactly as before — confirmed
by the full regression suite passing unmodified. Retrofitting
`get_current_actor`/`AuthorizationService` onto the dozens of pre-existing
endpoints is a real, large migration this phase's own instructions
("implement only the requested phase") do not authorize; this phase
delivers the complete, tested infrastructure that migration would be
built on, and wires it into the one place named explicitly by this
phase's own instructions (the external-runtime gate).

## 7. Remaining risk / recommended next phase

Every pre-existing route remains trust-the-body-id. A future phase should
retrofit `get_current_actor` route-by-route (behind
`Settings.REQUIRE_SERVER_SIDE_IDENTITY`, already added this phase) and
decide how `RuntimeRoleAssignment` grants get provisioned in practice
(an admin UI/endpoint does not exist yet — only `RBACService.grant`/
`revoke` as a service-layer primitive).
