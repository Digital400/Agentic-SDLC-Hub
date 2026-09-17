"""OutboxService — the write-ahead pattern for external writes an
AgentJob's completion might trigger (e.g. creating a GitHub pull request,
posting a Jira comment).

THE PROBLEM THIS SOLVES: a job handler that both (a) marks a job COMPLETED
and (b) directly calls out to GitHub/Jira in the same step risks two
failure modes if the process crashes between them — the external write
never happens (job says done, nothing external happened), or a retry
double-sends it (two PRs created for one job). The outbox pattern splits
this into two transactionally-safe steps: (1) record the INTENT to write,
in the same database transaction as the job's own state change — see
`enqueue`; (2) a separate step actually performs the write and marks it
SENT — see `mark_sent`/`mark_failed`. `idempotency_key` on
AgentJobOutboxEntry (unique, DB-enforced) makes step 2 safe to retry: a
second attempt with the same key is a conflict, not a duplicate send.

SCOPE: this module provides the enqueue/list-pending/mark-sent/mark-failed
primitives and is itself provider-agnostic — it does not call
app.services.github_integration or any other integration directly. A
caller (a job handler, or a future dedicated outbox-draining worker) reads
PENDING entries via `list_pending` and is responsible for actually
performing each entry's `external_target`-specific write.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import AgentJobOutboxEntry, OutboxEntryStatus


class OutboxConflictError(Exception):
    """Raised by enqueue when an entry with this idempotency_key already
    exists — the caller should treat this as "already recorded," not as a
    new attempt, and look up the existing entry instead (see
    get_by_idempotency_key)."""


class OutboxService:
    def __init__(self, db: Session):
        self.db = db

    def enqueue(self, *, job_id: uuid.UUID, idempotency_key: str, external_target: str, payload: dict) -> AgentJobOutboxEntry:
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            raise OutboxConflictError(f"An outbox entry with idempotency_key '{idempotency_key}' already exists (id={existing.id}).")

        entry = AgentJobOutboxEntry(
            job_id=job_id, idempotency_key=idempotency_key, external_target=external_target, payload=payload,
            status=OutboxEntryStatus.PENDING,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentJobOutboxEntry | None:
        return self.db.query(AgentJobOutboxEntry).filter(AgentJobOutboxEntry.idempotency_key == idempotency_key).first()

    def list_pending(self, *, external_target: str | None = None, limit: int = 100) -> list[AgentJobOutboxEntry]:
        query = self.db.query(AgentJobOutboxEntry).filter(AgentJobOutboxEntry.status == OutboxEntryStatus.PENDING)
        if external_target is not None:
            query = query.filter(AgentJobOutboxEntry.external_target == external_target)
        return query.order_by(AgentJobOutboxEntry.created_at).limit(limit).all()

    def mark_sent(self, entry: AgentJobOutboxEntry) -> AgentJobOutboxEntry:
        entry.status = OutboxEntryStatus.SENT
        entry.sent_at = datetime.now(timezone.utc)
        entry.attempt_count += 1
        return entry

    def mark_failed(self, entry: AgentJobOutboxEntry, *, error: str) -> AgentJobOutboxEntry:
        """Stays PENDING (not a terminal FAILED-forever state) so a later
        retry attempt can still pick it up via list_pending — `status`
        only ever moves to FAILED via mark_permanently_failed, a
        deliberate separate call, so a transient delivery failure never
        silently stops retrying on its own."""
        entry.attempt_count += 1
        entry.last_error = error
        return entry

    def mark_permanently_failed(self, entry: AgentJobOutboxEntry, *, error: str) -> AgentJobOutboxEntry:
        entry.status = OutboxEntryStatus.FAILED
        entry.attempt_count += 1
        entry.last_error = error
        return entry
