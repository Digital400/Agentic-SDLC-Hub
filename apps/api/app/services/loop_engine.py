"""LoopEngineService — drives a DRAFT-action agent run through a
self-improvement loop instead of a single generate-and-stop call.

Every run follows these steps (see LoopStepType):
    PLAN -> RETRIEVE_CONTEXT -> GENERATE_DRAFT -> VALIDATE
        -> [IMPROVE -> VALIDATE]* -> READY_FOR_REVIEW

PLAN and RETRIEVE_CONTEXT happen once. GENERATE_DRAFT produces iteration
1's draft; from there, VALIDATE scores it — via an independent validator
agent, one per workflow stage (see app/services/validator_agent.py and
app/models/validator.py's ValidatorDefinition) — and one of three things
happens:

  1. quality_score >= quality_threshold           -> stop (COMPLETED_QUALITY_MET)
  2. iteration >= max_iterations                  -> stop (COMPLETED_MAX_ITERATIONS)
  3. no critical issues (just below threshold)     -> stop (COMPLETED_NO_CRITICAL_ISSUES)
  4. otherwise (critical issues, iterations left)  -> run IMPROVE, then VALIDATE again

This is rule 5 in the requirements ("if critical validation issues exist,
run improve step") read together with rule 4 ("stop when qualityScore >=
threshold or maxIterations reached"): those are the only two unconditional
stop conditions; short of them, the loop only keeps going while there's a
critical issue actually worth spending another iteration on — a draft
that's merely short of threshold but has nothing critical wrong with it
stops too, rather than churning for iterations it has no real feedback to
act on.

Rule 5 ("send suggestions to the improve step") means literally that: when
the loop continues past VALIDATE, it's the validator's own `suggestions`
list — not a generic restatement of the critical issues — that's threaded
into the next GENERATE_DRAFT/IMPROVE call as `validation_feedback` (see
app/services/ai_generation.py's `generate`).

A draft that comes back asking for clarification (see
app/services/ai_generation.py's CLARIFICATION_MARKER) stops the loop
immediately, at whichever step produced it — there's nothing to validate
or improve until a human answers.

Every step of every iteration is persisted as one AgentRunLoopEvent (rule
6) — see app/api/routes/agent_runs.py's GET /agent-runs/{id}/loop-events
for how that's surfaced (rule 7). Only the run's DRAFT action currently
goes through this loop (see agent_runs.py's start_agent_run) — a VALIDATE
or IMPROVE run is already a single well-defined human-triggered agent
step, not a thing to loop.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    AgentPrompt,
    AgentPromptRole,
    AgentRun,
    AgentRunLoopEvent,
    LoopStatus,
    LoopStepType,
    Project,
    ValidatorDefinition,
    WorkflowNode,
)
from app.services.ai_generation import generate
from app.services.retrieval import RetrievedChunk
from app.services.validator_agent import run_validator

DEFAULT_MAX_ITERATIONS = 3
DEFAULT_QUALITY_THRESHOLD = 0.8


@dataclass
class LoopResult:
    content_markdown: str
    needs_clarification: bool
    clarification_questions: list[str] = field(default_factory=list)
    loop_status: LoopStatus = LoopStatus.NOT_STARTED
    iterations_run: int = 0
    quality_score: float = 0.0
    # The last VALIDATE step's critical issues (see
    # app/services/validator_agent.py); validation_result is the full
    # structured ValidatorResult (all four scores, suggestions,
    # approval_recommendation) — what the Agent Run Detail API shows.
    validation_issues: list[str] = field(default_factory=list)
    validation_result: dict = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    used_mock: bool = False
    # Token Budget Service (see app/services/token_budget.py) — summed
    # across every generate() call this loop made, mirroring how
    # prompt_tokens/etc. above are also totals across iterations rather
    # than just the last one. token_budget_report is the last iteration's
    # per-block breakdown (the most relevant one — earlier iterations'
    # context has already been superseded).
    estimated_context_tokens: int = 0
    token_budget_report: dict = field(default_factory=dict)


class LoopEngineService:
    def __init__(self, db: Session):
        self.db = db

    def _log(
        self,
        run: AgentRun,
        *,
        iteration: int,
        step: LoopStepType,
        quality_score: float | None = None,
        validation_issues: list[str] | None = None,
        validation_result: dict | None = None,
        content_snapshot: str | None = None,
        notes: str | None = None,
    ) -> AgentRunLoopEvent:
        event = AgentRunLoopEvent(
            agent_run_id=run.id,
            iteration=iteration,
            step=step,
            quality_score=quality_score,
            validation_issues=validation_issues,
            validation_result=validation_result,
            content_snapshot=content_snapshot,
            notes=notes,
        )
        self.db.add(event)
        run.loop_current_step = step
        return event

    def run_loop(
        self,
        *,
        run: AgentRun,
        project: Project,
        node: WorkflowNode,
        action: AgentPromptRole,
        active_prompt: AgentPrompt,
        approved_artifact_content: dict[str, str],
        approved_artifact_summaries: dict[str, str],
        freeform_context: dict[str, Any],
        full_content_artifact_types: set[str] | None = None,
        retrieved_chunks: list[RetrievedChunk] | None = None,
        review_comments: list[str] | None = None,
        validator: ValidatorDefinition | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
    ) -> LoopResult:
        run.loop_status = LoopStatus.RUNNING
        run.loop_max_iterations = max_iterations
        run.loop_quality_threshold = quality_threshold
        run.loop_iteration = 0
        self.db.flush()

        # --- PLAN (once) -----------------------------------------------------------
        self._log(
            run,
            iteration=0,
            step=LoopStepType.PLAN,
            notes=(
                f"Plan: draft '{node.output_artifact_type}' for stage '{node.node_key}' using "
                f"{len(approved_artifact_content)} approved upstream artifact(s); validate against "
                f"the active prompt's checklist; improve up to {max_iterations} time(s) until "
                f"quality >= {quality_threshold}."
            ),
        )

        # --- RETRIEVE_CONTEXT (once) -------------------------------------------------
        retrieved_chunks = retrieved_chunks or []
        self._log(
            run,
            iteration=0,
            step=LoopStepType.RETRIEVE_CONTEXT,
            notes=(
                f"Retrieved {len(retrieved_chunks)} relevant knowledge chunk(s)."
                if retrieved_chunks
                else "No relevant knowledge chunks found — proceeding on project context alone."
            ),
        )

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost = 0.0
        total_estimated_context_tokens = 0
        latest_token_budget_report: dict = {}
        used_mock = False
        content = ""
        quality_score = 0.0
        validation_issues: list[str] = []
        validation_result_dict: dict = {}
        pending_feedback: list[str] | None = None
        iteration = 1

        while True:
            run.loop_iteration = iteration
            step = LoopStepType.GENERATE_DRAFT if iteration == 1 else LoopStepType.IMPROVE

            result = generate(
                project=project,
                node=node,
                action=action,
                active_prompt=active_prompt,
                approved_artifact_content=approved_artifact_content,
                approved_artifact_summaries=approved_artifact_summaries,
                freeform_context=freeform_context,
                context_token_budget=node.context_token_budget,
                output_token_budget=node.output_token_budget,
                full_content_artifact_types=full_content_artifact_types,
                retrieved_chunks=retrieved_chunks,
                review_comments=review_comments,
                iteration=iteration,
                validation_feedback=pending_feedback,
            )
            content = result.content_markdown
            total_prompt_tokens += result.prompt_tokens
            total_completion_tokens += result.completion_tokens
            total_cost += result.cost
            total_estimated_context_tokens += result.estimated_context_tokens
            latest_token_budget_report = result.token_budget_report
            used_mock = used_mock or result.used_mock

            self._log(
                run,
                iteration=iteration,
                step=step,
                content_snapshot=content,
                notes=(
                    "Revision addressing prior validation feedback." if pending_feedback else "Initial draft."
                ),
            )

            if result.needs_clarification:
                run.loop_status = LoopStatus.WAITING_FOR_CLARIFICATION
                self._log(
                    run,
                    iteration=iteration,
                    step=LoopStepType.READY_FOR_REVIEW,
                    notes="Loop stopped: draft requested clarification from a human.",
                )
                return LoopResult(
                    content_markdown=content,
                    needs_clarification=True,
                    clarification_questions=result.clarification_questions,
                    loop_status=run.loop_status,
                    iterations_run=iteration,
                    quality_score=quality_score,
                    validation_issues=validation_issues,
                    validation_result=validation_result_dict,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    cost=total_cost,
                    used_mock=used_mock,
                    estimated_context_tokens=total_estimated_context_tokens,
                    token_budget_report=latest_token_budget_report,
                )

            # --- VALIDATE (rule 4: independent validator agent, one per stage) --------
            validation = run_validator(validator=validator, stage_name=node.name, content_markdown=content)
            quality_score = validation.quality_score
            validation_issues = validation.critical_issues
            validation_result_dict = validation.to_dict()
            self._log(
                run,
                iteration=iteration,
                step=LoopStepType.VALIDATE,
                quality_score=quality_score,
                validation_issues=validation_issues,
                validation_result=validation_result_dict,
                notes=(
                    f"{len(validation.critical_issues)} critical issue(s); "
                    f"recommendation={validation.approval_recommendation}."
                ),
            )

            # --- Rule: stop conditions ---------------------------------------------------
            if quality_score >= quality_threshold:
                run.loop_status = LoopStatus.COMPLETED_QUALITY_MET
                stop_reason = f"Quality score {quality_score} met threshold {quality_threshold}."
                break
            if iteration >= max_iterations:
                run.loop_status = LoopStatus.COMPLETED_MAX_ITERATIONS
                stop_reason = f"Reached max iterations ({max_iterations}) without meeting threshold {quality_threshold}."
                break
            if not validation.has_critical_issues:
                run.loop_status = LoopStatus.COMPLETED_NO_CRITICAL_ISSUES
                stop_reason = "Below threshold but no critical issues remain — nothing further to improve."
                break

            # Rule 5: qualityScore is below threshold and critical issues remain —
            # send the validator's own suggestions (not a generic restatement of
            # the critical issues) into the next IMPROVE call, and iterate.
            pending_feedback = validation.suggestions or validation.critical_issues
            iteration += 1

        self._log(
            run,
            iteration=run.loop_iteration,
            step=LoopStepType.READY_FOR_REVIEW,
            quality_score=quality_score,
            validation_issues=validation_issues,
            validation_result=validation_result_dict,
            notes=f"Loop stopped: {run.loop_status.value}. {stop_reason}",
        )

        run.loop_quality_score = quality_score
        run.loop_validation_issues = validation_issues
        run.loop_validation_result = validation_result_dict

        return LoopResult(
            content_markdown=content,
            needs_clarification=False,
            loop_status=run.loop_status,
            iterations_run=run.loop_iteration,
            quality_score=quality_score,
            validation_issues=validation_issues,
            validation_result=validation_result_dict,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            cost=total_cost,
            used_mock=used_mock,
            estimated_context_tokens=total_estimated_context_tokens,
            token_budget_report=latest_token_budget_report,
        )
