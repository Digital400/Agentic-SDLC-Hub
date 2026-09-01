"""Extracts text from an uploaded file and splits it into retrieval-sized
chunks — the missing half of the Knowledge Base pipeline (see
app/models/knowledge.py's KnowledgeChunk docstring, which previously
documented this as not built yet). Used by
POST /knowledge-sources/upload (see app/api/routes/knowledge.py).

Deliberately simple, matching this codebase's other heuristic text
processing (see app/services/artifact_summary.py): paragraph-based
chunking with a max size, not a smart semantic splitter — good enough to
get real, retrievable chunks out of a real document without a chunking
model or library.

Text extraction supports .txt/.md (read as UTF-8) and .pdf (via pypdf).
The uploaded file's raw bytes are never stored anywhere — there's no
object storage configured in this app yet (see KnowledgeSource.file_url's
docstring) — only its extracted, chunked text.
"""

import io

from pypdf import PdfReader

# A chunk stays under this many characters — large enough to carry a full
# thought, small enough to keep retrieval's similarity signal focused (see
# app/services/retrieval.py's MAX_COSINE_DISTANCE tuning, which assumes
# chunks of roughly this size).
DEFAULT_MAX_CHUNK_CHARS = 1000

SUPPORTED_EXTENSIONS = (".txt", ".md", ".markdown", ".pdf")


class DocumentIngestionError(Exception):
    """An uploaded file couldn't be read as text — an unsupported
    extension, or content pypdf couldn't parse. A caller error (bad
    upload), not a server fault."""


def extract_text(*, filename: str, raw_bytes: bytes) -> str:
    """Returns the file's plain-text content. Raises DocumentIngestionError
    for an unsupported extension or unreadable PDF."""
    lower_name = filename.lower()

    if lower_name.endswith((".txt", ".md", ".markdown")):
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentIngestionError(f"'{filename}' isn't valid UTF-8 text.") from exc

    if lower_name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:  # pypdf raises several distinct error types for malformed PDFs
            raise DocumentIngestionError(f"Couldn't read '{filename}' as a PDF: {exc}") from exc

    raise DocumentIngestionError(
        f"Unsupported file type for '{filename}' — supported: {', '.join(SUPPORTED_EXTENSIONS)}."
    )


def chunk_text(content: str, *, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    """Splits `content` into paragraph-based chunks, each up to
    `max_chars`. Paragraphs (blank-line-separated) are merged together
    while they still fit; a single paragraph longer than `max_chars` is
    split on its own (word-boundary, not mid-word) rather than dropped or
    left oversized. Empty/whitespace-only input yields no chunks."""
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        flush()
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            # A single oversized paragraph — split on word boundaries.
            words = paragraph.split(" ")
            piece = ""
            for word in words:
                candidate_piece = f"{piece} {word}".strip()
                if len(candidate_piece) > max_chars:
                    chunks.append(piece)
                    piece = word
                else:
                    piece = candidate_piece
            current = piece

    flush()
    return chunks
