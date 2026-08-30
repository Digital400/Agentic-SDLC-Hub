"""Knowledge Base retrieval for agent runs — the "R" in RAG.

Builds one composite query from everything that should shape what's
relevant to a given run (project domain, current SDLC stage, the user's
freeform input, and any approved upstream artifacts), embeds it (see
app/services/embeddings.py), and finds the most similar KnowledgeChunks by
pgvector cosine distance. Chunks below MIN_SIMILARITY are dropped rather
than forced into context — per the product rule, a run with no relevant
knowledge simply proceeds on project context alone (see
app/services/ai_generation.py's build_input_context, which handles an
empty retrieval result by omitting the knowledge section entirely).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeSource, Project, WorkflowNode
from app.services.embeddings import embed_text

# How many chunks to inject into an agent's context at most — enough to be
# useful, small enough not to dominate the prompt over the real project
# content it's meant to support.
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


def retrieve_relevant_chunks(
    db: Session,
    *,
    project: Project,
    node: WorkflowNode,
    freeform_context: dict[str, Any],
    approved_inputs: dict[str, str],
    top_k: int = TOP_K,
) -> list[RetrievedChunk]:
    """Returns up to `top_k` relevant chunks, most similar first — or an
    empty list if nothing clears MAX_COSINE_DISTANCE (see module
    docstring: that's an expected, non-error outcome)."""
    query_text = build_retrieval_query(
        project=project, node=node, freeform_context=freeform_context, approved_inputs=approved_inputs
    )
    if not query_text.strip():
        return []

    query_vector = embed_text(query_text)
    distance = KnowledgeChunk.embedding.cosine_distance(query_vector)

    rows = (
        db.query(KnowledgeChunk, KnowledgeSource, distance.label("distance"))
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .filter(KnowledgeChunk.embedding.is_not(None))
        .filter(distance < MAX_COSINE_DISTANCE)
        .order_by(distance)
        .limit(top_k)
        .all()
    )

    return [
        RetrievedChunk(
            chunk_id=str(chunk.id),
            source_id=str(source.id),
            source_title=source.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            similarity=round(1.0 - float(dist), 4),
        )
        for chunk, source, dist in rows
    ]
