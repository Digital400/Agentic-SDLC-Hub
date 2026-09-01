"""Unit tests for TokenBudgetService — see app/services/token_budget.py."""

from app.services.token_budget import ContextBlock, TokenBudgetService, estimate_tokens


def _block(priority: str, label: str, words: int, compressible: bool = False) -> ContextBlock:
    """A block of roughly `words` words — ~1 token per word at 4 chars/token
    with typical short English words, close enough for test purposes."""
    return ContextBlock(priority=priority, label=label, content=" ".join(["word"] * words), compressible=compressible)


def test_all_blocks_included_when_everything_fits_the_budget():
    service = TokenBudgetService(context_token_budget=1000, output_token_budget=500)
    blocks = [_block("P0", "instruction", 10), _block("P1", "rules", 10), _block("P3", "rag", 10)]

    result = service.build(blocks)

    assert result.estimated_tokens == result.raw_estimated_tokens
    assert result.dropped_labels == []
    assert result.truncated_labels == []
    assert not result.over_budget


def test_lower_priority_block_is_dropped_before_higher_priority_ones(monkeypatch):
    # Each "word " is 5 chars -> ~1 token/word at CHARS_PER_TOKEN=4, so 50
    # words ~= 62 tokens. Budget fits P0+P1 but not also P3.
    service = TokenBudgetService(context_token_budget=30, output_token_budget=500)
    p0 = _block("P0", "instruction", 5, compressible=True)  # small, fits easily
    p3 = _block("P3", "rag_chunk", 50)  # large, non-compressible

    result = service.build([p3, p0])  # order shouldn't matter — priority does

    assert "instruction" in [b.label for b in result.blocks if b.included]
    assert "rag_chunk" in result.dropped_labels
    assert result.over_budget


def test_compressible_block_is_truncated_to_fit_instead_of_dropped():
    service = TokenBudgetService(context_token_budget=10, output_token_budget=500)
    huge_instruction = _block("P0", "instruction", 200, compressible=True)

    result = service.build([huge_instruction])

    assert "instruction" in result.truncated_labels
    assert "instruction" not in result.dropped_labels
    fitted = next(b for b in result.blocks if b.label == "instruction")
    assert fitted.included is True
    assert "truncated to fit" in result.assembled_text()


def test_non_compressible_block_is_dropped_whole_when_it_does_not_fit():
    service = TokenBudgetService(context_token_budget=5, output_token_budget=500)
    rag_chunk = _block("P3", "rag_chunk", 100)  # not compressible

    result = service.build([rag_chunk])

    assert result.dropped_labels == ["rag_chunk"]
    assert result.estimated_tokens == 0
    assert result.assembled_text() == ""


def test_priority_order_is_respected_regardless_of_input_order():
    service = TokenBudgetService(context_token_budget=1000, output_token_budget=500)
    blocks = [
        _block("P5", "full_artifact", 5),
        _block("P0", "instruction", 5),
        _block("P2", "summary", 5),
        _block("P1", "rules", 5),
    ]

    result = service.build(blocks)

    assert [b.label for b in result.blocks] == ["instruction", "rules", "summary", "full_artifact"]


def test_empty_blocks_are_skipped_entirely():
    service = TokenBudgetService(context_token_budget=1000, output_token_budget=500)
    blocks = [ContextBlock(priority="P4", label="no_comments", content="   ")]

    result = service.build(blocks)

    assert result.blocks == []
    assert result.raw_estimated_tokens == 0


def test_estimate_tokens_is_never_zero_for_non_empty_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hi") >= 1
    assert estimate_tokens("a" * 400) == 100
