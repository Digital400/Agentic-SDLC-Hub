"""classify_failure — decides whether a FAILED AgentJob is worth retrying
as-is (TRANSIENT) or needs a corrected WorkPacket/human intervention
(PERMANENT). See app/models/enums.py's JobFailureCategory.

Reuses app.model_gateway's own exception vocabulary (Phase 05) directly —
this is the same distinction ModelGatewayTimeoutError already draws
against plain ModelGatewayError, and the same "retryable" boolean
app.agent_runtime.RuntimeFailure already carries (Phase 01) — this module
is what actually assigns that classification at the job-persistence
layer, for any exception a job handler raises.
"""

from __future__ import annotations

from app.model_gateway.base import ModelGatewayTimeoutError
from app.model_gateway.budget import BudgetExceededError
from app.models.enums import JobFailureCategory
from app.prompt_compiler.lint import PromptLintError

# Exception types this module treats as TRANSIENT by their type alone —
# every one of these represents "the same request might succeed later,"
# not "this request is fundamentally wrong." Anything not listed here
# defaults to PERMANENT (fail-closed: an unrecognized failure is treated
# as needing a human/corrected input, never silently retried forever).
_TRANSIENT_EXCEPTION_TYPES: tuple[type[Exception], ...] = (
    ModelGatewayTimeoutError,
    ConnectionError,
    TimeoutError,
)

# Exception types that are structurally never worth retrying as-is, even
# though they might otherwise resemble a transient network issue (e.g. a
# budget/lint failure could recur every retry if the underlying WorkPacket
# doesn't change) — checked BEFORE the transient list, so a subclass
# relationship can never accidentally reclassify one of these.
_PERMANENT_EXCEPTION_TYPES: tuple[type[Exception], ...] = (
    BudgetExceededError,
    PromptLintError,
    ValueError,
    TypeError,
)


def classify_failure(exc: Exception) -> JobFailureCategory:
    if isinstance(exc, _PERMANENT_EXCEPTION_TYPES):
        return JobFailureCategory.PERMANENT
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return JobFailureCategory.TRANSIENT
    return JobFailureCategory.PERMANENT
