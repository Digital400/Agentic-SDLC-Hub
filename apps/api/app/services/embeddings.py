"""Embedding generation for the Knowledge Base's RAG pipeline.

Anthropic's API has no embeddings endpoint (their docs point to Voyage AI
as the recommended embeddings partner instead), and this project's
established convention (see app/services/ai_generation.py) is real-model-
when-configured, deterministic-fallback-otherwise so the system stays fully
testable without a paid key. Following that: `embed_text` implements the
hashing trick — a real, well-established deterministic embedding technique
(scikit-learn's HashingVectorizer uses the same idea), not a fake stand-in.
Each token is hashed into one of EMBEDDING_DIM buckets, occupancy counts
are accumulated, and the result is L2-normalized. Cosine similarity over
these vectors genuinely reflects shared vocabulary between texts, which is
enough for real (if unsophisticated) retrieval in this MVP.

Swap `embed_text`/`embed_texts` for a real hosted model (e.g. Voyage AI)
behind these same signatures when one is wanted — nothing else in the RAG
pipeline (app/services/retrieval.py, the KnowledgeChunk model, or the
search API) needs to change, other than EMBEDDING_DIM matching the new
model's output size and a migration to match.
"""

import hashlib
import math
import re

# Keep in sync with the KnowledgeChunk.embedding column's vector size
# (app/models/knowledge.py) — changing this requires a migration.
EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Common English function words carry no topical signal but appear in
# nearly every sentence — left in, they inflate cosine similarity between
# genuinely unrelated texts just from shared grammar. Filtering them is the
# single highest-leverage improvement to this hashing-trick embedding's
# precision (see module docstring).
_STOPWORDS = frozenset(
    """
    a an the this that these those and or but if then else so of to in on for with
    as by at from into over under is are was were be been being do does did
    have has had will would should could can may might must shall not no nor
    it its it's their our your his her they them he she we you i
    """.split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def embed_text(text: str) -> list[float]:
    """Deterministic hashing-trick embedding — see module docstring."""
    vector = [0.0] * EMBEDDING_DIM
    for token in _tokenize(text):
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
        vector[bucket] += 1.0

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]
