# GitHub Integration Setup

The GitHub integration is a **read-only foundation**: connect a personal
access token (PAT), configure a project's repository, and pull branches,
the file tree, individual file contents, and point-in-time snapshots.
**No push. No branch creation. No write scope is requested or used.**

See `app/services/github_integration.py` for the client, `app/api/routes/github_integration.py`
for the endpoints, and `apps/web/app/settings/integrations/github` for the UI.

## 1. Create a token

Use a **classic PAT** with only the `repo` scope (or, for a public-only
repo, no scope at all is required for read access — but `repo` is the
simplest choice that works for both public and private repos), or a
**fine-grained PAT** scoped to the specific repository with:
- **Contents: Read-only**
- **Metadata: Read-only** (required by fine-grained tokens for any access)

Do **not** grant any write permission (Contents: Read and write,
Administration, Webhooks, etc.) — this integration never uses one, and a
token that can't write can't be misused by a bug here to write either.

Generate one at <https://github.com/settings/tokens> (classic) or
<https://github.com/settings/personal-access-tokens/new> (fine-grained).

## 2. Connect it

In the app: **Settings → Integrations → GitHub → Configure**, paste the
token, and click **Connect**. The token is verified against GitHub's own
`GET /user` endpoint before anything is saved — a bad or over-scoped
token is rejected immediately, not stored and discovered broken later.

Once connected, the app shows your GitHub username, the token's reported
scopes, and a masked hint (`****d3f9`) — **the real token is never shown
again, by this app or any API response it returns**, after the moment you
paste it.

## 3. Configure a repository

Pick a project, enter the repo's `owner` and `name` (e.g. `octocat` /
`hello-world`), and save. The app calls GitHub once to confirm the repo
exists and is reachable with your token, and records its default branch,
visibility, and description.

## 4. Scan it

- **List branches** / **default branch** — read live from GitHub.
- **Create Snapshot** — pulls the full recursive file tree for the
  configured branch (or a specific ref) and persists it as a
  `RepositorySnapshot` + one `RepositoryFileIndex` row per file/directory
  found. A snapshot never stores file *content* — only path/type/size/sha.
  If GitHub reports the tree as `truncated` (a very large repo), the
  snapshot honestly records that rather than silently presenting a
  partial scan as complete.
- **Read a file** — fetched on demand, not stored; capped at 500 KB and
  gracefully reported (not crashed on) for binary content.

## Environment variables

```env
# apps/api/.env — optional. See app/core/security.py for what happens
# when this is unset (a dev-only fallback, NOT safe for a real token).
# Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GITHUB_TOKEN_ENCRYPTION_KEY=
```

## How this differs from `docs/architecture.md`'s vault plan

`docs/architecture.md`'s MCP integrations section states the intended end
state plainly: *"credentials in a vault... never persisted in this table
or logged."* No vault exists anywhere in this codebase yet, and building
one is out of scope for a read-only foundation. So, as a disclosed,
deliberate bridge:

- The PAT is encrypted at rest (Fernet, `app/core/security.py`) in
  Postgres — not plaintext, but still an app-managed secret in this
  app's own database, not a vault-resolved one.
- It is decrypted only transiently, once per outbound GitHub call, by
  `app/api/routes/github_integration.py` — never cached, never logged,
  never included in any API response or audit log entry.
- `docs/architecture.md` itself has been updated to flag this as a known
  exception, not silently glossed over.

If a real secrets manager is ever integrated, `IntegrationConnection.access_token_encrypted`
should be replaced by a vault reference, not extended.

## Security summary

| Rule | How it's enforced |
|---|---|
| Do not push code | No method in `GitHubIntegrationService` performs a write — there is nothing to disable, the capability doesn't exist |
| Do not create branches | Same — no branch/ref-mutation method exists |
| Do not expose secrets in logs | Every audit log call in `app/api/routes/github_integration.py` is hand-checked to log only non-secret metadata (username, scopes, owner/repo, ref, file count) — never the token, encrypted or not; see `tests/test_github_integration.py`'s dedicated regression tests for this |
