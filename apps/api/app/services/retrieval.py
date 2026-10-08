"""Knowledge Base retrieval for agent runs — the "R" in RAG.

Builds one composite query from everything that should shape what's
relevant to a given run (project domain, current SDLC stage, the user's
freeform input, and any approved upstream artifacts), embeds it (see
app/services/embeddings.py), and finds the most similar KnowledgeChunks by
pgvector cosine distance. Chunks below MIN_SIMILARITY are dropped rather
than forced into context — per the product rule, a run with no relevant
knowledge simply proceeds on project context alone (see
app/services/ai_generation.py's build_prioritized_context, which handles an
empty retrieval result by omitting the knowledge section entirely).

Stage-aware: a chunk tagged with a specific `stage` (a WorkflowNode.node_key)
is only eligible for that stage; a chunk with no stage is eligible
everywhere (see KnowledgeChunk's docstring) — this is what keeps, say, a
UI-guideline chunk from surfacing during Infrastructure Provisioning while
still letting a company-wide coding standard apply to every stage.

RAG is required to support five kinds of content — see
KnowledgeContentType — and none of them are hard-filtered out by default:
company standards and architecture rules tend to be stage-agnostic, past
artifacts/UI guidelines/testing standards tend to be stage-tagged, but
which is which is a data/tagging decision, not something this module
enforces. `content_types` is available as an explicit opt-in filter for a
caller that wants to narrow to just one or two kinds (e.g. a "show me the
testing standards" debug view).

Two independent limits, both per-node (see app/models/workflow.py):
`top_k` caps how many chunks are even considered; `max_rag_tokens` is a
separate, dedicated budget on how many of those chunks' tokens retrieval
actually keeps, applied in similarity order so a token-limited call always
keeps the most relevant chunks and drops the least relevant ones — never a
partial/truncated chunk.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeContentType, KnowledgeSource, Project, WorkflowNode
from app.services.embeddings import embed_text
from app.services.token_budget import estimate_tokens

# How many chunks to consider at most before any token-budget trimming —
# see max_rag_tokens for the separate, dedicated token-level cap. Also the
# default when a WorkflowNode doesn't override rag_top_k.
TOP_K = 5

# Cosine distance is 1 - cosine_similarity for a unit-normalized vector, so
# this keeps only chunks with similarity > 0.22. Empirically tuned against
# the hashing-trick embedding (see embeddings.py) with stopwords filtered:
# genuinely related text scored 0.28-0.36 similarity in manual testing
# (project domain + stage description alone, no shared rare vocabulary),
# while a deliberately unrelated project/stage scored ~0.20 purely from
# incidental hash-bucket collisions on short texts — 0.22 sits between the
# two. Revisit this if embed_text is ever swapped for a real hosted
# embedding model, which won't have the same noise floor.
MAX_COSINE_DISTANCE = 0.78


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_id: str
    source_title: str
    chunk_index: int
    content: str
    similarity: float
    stage: str | None = None
    domain: str | None = None
    project_type: str | None = None
    content_type: str = KnowledgeContentType.OTHER.value
    tags: list[str] | None = None


def _truncate(text: str, max_chars: int = 1500) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def build_retrieval_query(
    *,
    project: Project,
    node: WorkflowNode,
    freeform_context: dict[str, Any],
    approved_inputs: dict[str, str],
) -> str:
    """Composes the text retrieval is run against, per the product's
    required signal set: project domain, current stage, user input, and
    previous (approved upstream) artifacts."""
    parts = [
        project.name,
        project.description or "",
        project.business_owner,
        node.name,
        node.description,
        node.output_artifact_type,
    ]
    parts += [str(v) for v in freeform_context.values()]
    # Full artifact bodies would dominate the query embedding for what's
    # meant to be a light relevance signal — a truncated excerpt of each is
    # enough to carry its topic.
    parts += [_truncate(content, 800) for content in approved_inputs.values()]

    return "\n".join(p for p in parts if p)


def apply_max_rag_tokens(chunks: list[RetrievedChunk], max_rag_tokens: int) -> list[RetrievedChunk]:
    """Keeps chunks in the given (similarity) order while their cumulative
    estimated token cost stays within `max_rag_tokens`, dropping the rest
    whole — never truncates a chunk's content mid-way. Pulled out as its
    own function (no DB access) so the trimming rule is unit-testable
    without a real Postgres/pgvector connection.
    """
    kept: list[RetrievedChunk] = []
    remaining = max_rag_tokens
    for chunk in chunks:
        cost = estimate_tokens(chunk.content)
        if cost > remaining:
            continue
        kept.append(chunk)
        remaining -= cost
    return kept


def retrieve_relevant_chunks(
    db: Session,
    *,
    project: Project,
    node: WorkflowNode,
    freeform_context: dict[str, Any],
    approved_inputs: dict[str, str],
    top_k: int | None = None,
    max_rag_tokens: int | None = None,
    content_types: list[KnowledgeContentType] | None = None,
) -> list[RetrievedChunk]:
    """Returns up to `top_k` relevant chunks, most similar first, trimmed
    to fit `max_rag_tokens` — or an empty list if nothing clears
    MAX_COSINE_DISTANCE (see module docstring: that's an expected,
    non-error outcome).

    `top_k`/`max_rag_tokens` default to the node's own
    rag_top_k/max_rag_tokens config (see app/models/workflow.py) when not
    given explicitly — a caller only needs to override them for something
    like a debug/preview call. `content_types` is an optional opt-in
    narrowing to specific KnowledgeContentType values (see module
    docstring); omitted means every content type is eligible.
    """
    top_k = node.rag_top_k if top_k is None else top_k
    max_rag_tokens = node.max_rag_tokens if max_rag_tokens is None else max_rag_tokens

    query_text = build_retrieval_query(
        project=project, node=node, freeform_context=freeform_context, approved_inputs=approved_inputs
    )
    if not query_text.strip():
        return []

    query_vector = embed_text(query_text)
    distance = KnowledgeChunk.embedding.cosine_distance(query_vector)

    query = (
        db.query(KnowledgeChunk, KnowledgeSource, distance.label("distance"))
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .filter(KnowledgeChunk.embedding.is_not(None))
        .filter(distance < MAX_COSINE_DISTANCE)
        # Stage-aware retrieval (see module docstring): eligible when the
        # chunk is stage-agnostic (null) or tagged for this exact stage.
        .filter((KnowledgeChunk.stage.is_(None)) | (KnowledgeChunk.stage == node.node_key))
        # Project-scoped retrieval (see KnowledgeSource.project_id):
        # eligible when the source is org-wide (null) or scoped to
        # exactly this run's project — never another project's.
        .filter((KnowledgeSource.project_id.is_(None)) | (KnowledgeSource.project_id == project.id))
    )
    if content_types:
        query = query.filter(KnowledgeChunk.content_type.in_(content_types))

    rows = query.order_by(distance).limit(top_k).all()

    chunks = [
        RetrievedChunk(
            chunk_id=str(chunk.id),
            source_id=str(source.id),
            source_title=source.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            similarity=round(1.0 - float(dist), 4),
            stage=chunk.stage,
            domain=chunk.domain,
            project_type=chunk.project_type,
            content_type=chunk.content_type.value,
            tags=chunk.tags,
        )
        for chunk, source, dist in rows
    ]
    return apply_max_rag_tokens(chunks, max_rag_tokens)
