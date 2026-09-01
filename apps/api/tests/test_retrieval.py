"""Unit tests for the pure-Python parts of app/services/retrieval.py.

`retrieve_relevant_chunks` itself needs a real Postgres + pgvector
connection (cosine_distance is a Postgres-specific SQL function) — not
exercised here. `apply_max_rag_tokens` was pulled out specifically so the
token-budget trimming rule (requirement 4: maxRagTokens per workflow node)
is testable without one.
"""

from app.services.retrieval import RetrievedChunk, apply_max_rag_tokens


def _chunk(content: str, similarity: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c", source_id="s", source_title="Source", chunk_index=0, content=content, similarity=similarity
    )


def test_all_chunks_kept_when_well_under_the_token_cap():
    chunks = [_chunk("short chunk"), _chunk("another short chunk")]

    kept = apply_max_rag_tokens(chunks, max_rag_tokens=1000)

    assert kept == chunks


def test_chunks_dropped_once_cumulative_tokens_exceed_the_cap():
    # ~25 words each ≈ 30+ tokens at the 4-chars/token estimate — three of
    # them comfortably exceeds a 50-token cap.
    big_chunk = " ".join(["word"] * 25)
    chunks = [_chunk(big_chunk, similarity=0.9), _chunk(big_chunk, similarity=0.7), _chunk(big_chunk, similarity=0.5)]

    kept = apply_max_rag_tokens(chunks, max_rag_tokens=50)

    assert len(kept) < len(chunks)
    # Most-similar-first order is what makes dropping-from-the-end correct
    # — the least similar chunk is the one cut, not an arbitrary one.
    assert kept[0].similarity == 0.9


def test_a_chunk_that_alone_exceeds_the_cap_is_dropped_whole_not_truncated():
    huge_chunk = " ".join(["word"] * 1000)
    chunks = [_chunk(huge_chunk)]

    kept = apply_max_rag_tokens(chunks, max_rag_tokens=10)

    assert kept == []  # dropped entirely — never a partial/truncated chunk


def test_a_later_smaller_chunk_can_still_fit_after_an_earlier_one_is_skipped():
    huge_chunk = " ".join(["word"] * 1000)
    small_chunk = "tiny"
    chunks = [_chunk(huge_chunk, similarity=0.9), _chunk(small_chunk, similarity=0.6)]

    kept = apply_max_rag_tokens(chunks, max_rag_tokens=10)

    assert kept == [chunks[1]]


def test_empty_input_returns_empty_list():
    assert apply_max_rag_tokens([], max_rag_tokens=1000) == []
