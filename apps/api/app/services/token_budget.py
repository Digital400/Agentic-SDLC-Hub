"""TokenBudgetService — keeps an agent run's assembled context inside a
workflow node's configured token budget (WorkflowNode.context_token_budget
/ output_token_budget — see app/models/workflow.py).

Deliberately simple: token counts are estimated with a fixed
characters-per-token ratio (see `estimate_tokens`), not a real tokenizer —
good enough to keep a prompt roughly within budget, not an exact accounting
of what the provider will actually charge. Fitting content into the budget
is a single pass over priority-ordered blocks (see `PRIORITY_ORDER`), not a
knapsack/optimization problem — a real LLM prompt has an obvious priority
order already (rule 4 below), so there's nothing to optimize for beyond
"fill it in that order and stop."

Priority order (rule 4 of the token-budget requirements):
    P0  current user instruction     — never dropped
    P1  node rules                   — never dropped
    P2  approved artifact summaries  — never dropped, compressed if tight
    P3  relevant RAG chunks          — dropped whole (least-similar first,
                                        since retrieve_relevant_chunks
                                        already orders most-similar first)
    P4  review comments              — dropped whole if no room
    P5  full artifact content        — dropped whole if no room; only ever
                                        added at all "if required" — see
                                        app/services/ai_generation.py's
                                        build_prioritized_context, which
                                        decides that per-artifact, not here

"Compress or remove lower priority content" (rule 5) maps directly onto
`ContextBlock.compressible`: P0-P2 are compressible (truncated to fit
rather than dropped — foundational content, worth keeping *some* of over
losing entirely); P3-P5 are not (either the whole block fits, or it's
dropped — a half a RAG chunk or half a review comment isn't worth the
complexity of mid-block truncation for something this replaceable).
"""

from dataclasses import dataclass, field

# A practical rule of thumb (~4 characters per token for English text),
# not a real tokenizer — see module docstring. Good enough to keep a
# prompt roughly within budget without adding a tokenizer dependency.
CHARS_PER_TOKEN = 4

# A compressible block is never truncated down below this many tokens —
# a heavily-cut instruction is worse than a prompt that runs slightly over
# budget. Only matters when the budget itself is unrealistically small.
MIN_COMPRESSED_TOKENS = 40

PRIORITY_ORDER = ["P0", "P1", "P2", "P3", "P4", "P5"]


def estimate_tokens(text: str) -> int:
    """Rough token estimate — see module docstring. Never zero for
    non-empty text, so a tiny block still "costs" something."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(0, max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n…(truncated to fit the context token budget)"


@dataclass
class ContextBlock:
    """One named piece of context at one priority tier. `compressible`
    controls what happens when it doesn't fit — see module docstring."""

    priority: str  # one of PRIORITY_ORDER
    label: str  # e.g. "current_instruction", "node_rules", "approved:hld_document"
    content: str
    compressible: bool = False

    def __post_init__(self) -> None:
        if self.priority not in PRIORITY_ORDER:
            raise ValueError(f"Unknown context priority '{self.priority}' — must be one of {PRIORITY_ORDER}")


@dataclass
class FittedBlock:
    priority: str
    label: str
    estimated_tokens: int
    included: bool
    truncated: bool = False
    # The block's final content — the original text if included as-is, the
    # truncated text if compressed, or "" if dropped. Lets a caller
    # reconstruct "what ended up in the prompt for label X" without
    # re-deriving it from assembled_text()'s flat, unlabeled concatenation
    # — see app/services/context_builder.py, which looks blocks up by
    # label to populate its own named output fields.
    content: str = ""


@dataclass
class TokenBudgetResult:
    context_token_budget: int
    output_token_budget: int
    raw_estimated_tokens: int  # what every block would have cost, uncompressed
    estimated_tokens: int  # what the assembled (post-fit) context actually costs
    blocks: list[FittedBlock] = field(default_factory=list)
    _assembled_parts: list[str] = field(default_factory=list, repr=False)

    @property
    def over_budget(self) -> bool:
        """Whether anything had to be compressed or dropped to fit."""
        return self.raw_estimated_tokens > self.context_token_budget

    @property
    def dropped_labels(self) -> list[str]:
        return [b.label for b in self.blocks if not b.included]

    @property
    def truncated_labels(self) -> list[str]:
        return [b.label for b in self.blocks if b.truncated]

    def assembled_text(self) -> str:
        return "\n\n".join(self._assembled_parts)

    def to_report_dict(self) -> dict:
        """The "token budget details" for AgentRun's response (rule 7) —
        one row per block plus the totals, so a human can see exactly what
        was included, compressed, or dropped and why."""
        return {
            "context_token_budget": self.context_token_budget,
            "output_token_budget": self.output_token_budget,
            "raw_estimated_tokens": self.raw_estimated_tokens,
            "estimated_tokens": self.estimated_tokens,
            "over_budget": self.over_budget,
            "blocks": [
                {
                    "priority": b.priority,
                    "label": b.label,
                    "estimated_tokens": b.estimated_tokens,
                    "included": b.included,
                    "truncated": b.truncated,
                }
                for b in self.blocks
            ],
        }


class TokenBudgetService:
    def __init__(self, *, context_token_budget: int, output_token_budget: int):
        self.context_token_budget = context_token_budget
        self.output_token_budget = output_token_budget

    def build(self, blocks: list[ContextBlock]) -> TokenBudgetResult:
        """Fits `blocks` into context_token_budget, processing them in
        priority order (P0 first). One linear pass: each block either fits
        as-is, gets truncated to whatever's left (compressible blocks
        only), or is dropped — see module docstring."""
        ordered = sorted((b for b in blocks if b.content.strip()), key=lambda b: PRIORITY_ORDER.index(b.priority))

        raw_total = sum(estimate_tokens(b.content) for b in ordered)
        remaining = self.context_token_budget
        fitted: list[FittedBlock] = []
        assembled_parts: list[str] = []

        for block in ordered:
            tokens = estimate_tokens(block.content)

            if tokens <= remaining:
                fitted.append(FittedBlock(block.priority, block.label, tokens, included=True, content=block.content))
                assembled_parts.append(block.content)
                remaining -= tokens
                continue

            if block.compressible:
                # Never compress a foundational block down to nothing —
                # see MIN_COMPRESSED_TOKENS.
                budget_for_block = max(remaining, MIN_COMPRESSED_TOKENS)
                truncated_content = _truncate_to_tokens(block.content, budget_for_block)
                truncated_tokens = estimate_tokens(truncated_content)
                fitted.append(
                    FittedBlock(
                        block.priority, block.label, truncated_tokens, included=True, truncated=True,
                        content=truncated_content,
                    )
                )
                assembled_parts.append(truncated_content)
                remaining = max(0, remaining - truncated_tokens)
            else:
                fitted.append(FittedBlock(block.priority, block.label, tokens, included=False))
                # Not included — remaining budget is unchanged; content stays "".

        estimated_tokens_total = sum(b.estimated_tokens for b in fitted if b.included)

        return TokenBudgetResult(
            context_token_budget=self.context_token_budget,
            output_token_budget=self.output_token_budget,
            raw_estimated_tokens=raw_total,
            estimated_tokens=estimated_tokens_total,
            blocks=fitted,
            _assembled_parts=assembled_parts,
        )
