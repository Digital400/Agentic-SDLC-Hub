"""Unit tests for app/services/document_ingestion.py's chunking logic —
extract_text (PDF/txt) needs real file bytes and isn't exercised here."""

from app.services.document_ingestion import chunk_text


def test_empty_content_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_short_content_stays_in_one_chunk():
    content = "First paragraph.\n\nSecond paragraph."
    chunks = chunk_text(content, max_chars=1000)

    assert len(chunks) == 1
    assert "First paragraph." in chunks[0]
    assert "Second paragraph." in chunks[0]


def test_paragraphs_split_once_the_max_size_is_exceeded():
    para_a = "A" * 40
    para_b = "B" * 40
    para_c = "C" * 40
    content = f"{para_a}\n\n{para_b}\n\n{para_c}"

    # Small enough that each paragraph must be its own chunk (two
    # paragraphs together already exceed 50 chars).
    chunks = chunk_text(content, max_chars=50)

    assert len(chunks) == 3
    assert chunks[0] == para_a
    assert chunks[1] == para_b
    assert chunks[2] == para_c


def test_a_single_oversized_paragraph_is_split_on_word_boundaries():
    long_paragraph = " ".join(["word"] * 50)  # 5 chars each incl. space = 250 chars

    chunks = chunk_text(long_paragraph, max_chars=60)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 60
        assert not chunk.startswith(" ") and not chunk.endswith(" ")
    # No word was dropped or corrupted by the split.
    assert " ".join(chunks).split() == ["word"] * 50


def test_chunks_never_exceed_max_chars_for_mixed_content():
    content = "\n\n".join(["Short one.", "A" * 900, "Short two.", "B" * 900])

    chunks = chunk_text(content, max_chars=1000)

    assert all(len(c) <= 1000 for c in chunks)
