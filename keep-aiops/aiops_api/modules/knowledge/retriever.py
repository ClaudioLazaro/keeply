"""Retrieval for the Knowledge Engine (ADR-0005): cosine or keyword overlap.

Two deterministic modes:

- **Keyword mode** (no embedder configured): score = |query ∩ doc tokens| /
  |query tokens| over lowercase ``[a-z0-9]+`` tokens of title + chunk.
- **Embedding mode** (embedder configured): cosine similarity between the
  query embedding and stored chunk embeddings; rows without embeddings score
  0.0.

Both modes sort by (-score, title, id) so results are fully deterministic and
unit-testable. Every DB query filters ``tenant_id IN (tenant, '*')`` — the
mandatory tenant filter; ``"*"`` carries global shared runbooks (same
convention as the policy module's GLOBAL_TENANT).
"""

import math
import re
from typing import Any

from sqlmodel import Session, select

from aiops_api.db import session_scope
from aiops_api.modules.knowledge.embedder import Embedder, get_embedder
from aiops_api.modules.knowledge.models import KnowledgeDocument
from aiops_api.modules.policy.models import GLOBAL_TENANT

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Sentinel: embedder argument omitted -> resolve from settings (auto mode).
_AUTO = object()


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def keyword_score(query_tokens: set[str], doc_tokens: set[str]) -> float:
    """|query ∩ doc| / |query|; 0.0 for an empty query."""
    if not query_tokens:
        return 0.0
    return len(query_tokens & doc_tokens) / len(query_tokens)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 on dimension mismatch or a zero vector."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0.0:
        return 0.0
    return dot / norm


def keyword_retrieve(query: str, docs: list[dict[str, Any]], k: int = 5) -> list[dict[str, Any]]:
    """Pure in-process keyword retrieval (no DB) — used by the eval harness.

    ``docs`` carry at least {id, title, chunk}; extra keys pass through.
    Returns the top-k doc dicts with an added ``score`` key; zero-scoring
    docs are dropped. Deterministic: ties broken by (title, id).
    """
    query_tokens = tokenize(query)
    scored: list[dict[str, Any]] = []
    for doc in docs:
        doc_tokens = tokenize(f"{doc.get('title', '')}\n{doc.get('chunk', '')}")
        score = keyword_score(query_tokens, doc_tokens)
        if score <= 0.0:
            continue
        scored.append({**doc, "score": score})
    scored.sort(key=lambda d: (-d["score"], str(d.get("title", "")), str(d.get("id", ""))))
    return scored[: max(k, 0)]


def _to_result(doc: KnowledgeDocument, score: float) -> dict[str, Any]:
    return {"id": doc.id, "title": doc.title, "source": doc.source, "chunk": doc.chunk, "score": score}


def query(
    session: Session,
    tenant_id: str,
    query_text: str,
    k: int = 5,
    *,
    embedder: Embedder | None | object = _AUTO,
) -> list[dict[str, Any]]:
    """Tenant-scoped retrieval: {id, title, source, chunk, score} dicts.

    Mode is selected by ``embedder``: the default resolves it from settings
    (LiteLLM when AIOPS_LLM_EMBEDDING_MODEL is set); pass None to force
    keyword mode or a callable to inject one (tests).
    """
    if embedder is _AUTO:
        embedder = get_embedder()
    statement = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_([tenant_id, GLOBAL_TENANT]))
    docs = list(session.exec(statement).all())

    if embedder is not None and query_text.strip():
        query_vector = embedder([query_text])[0]
        scored = [
            _to_result(doc, cosine_similarity(query_vector, doc.embedding) if doc.embedding else 0.0)
            for doc in docs
        ]
    else:
        query_tokens = tokenize(query_text)
        scored = [
            _to_result(doc, keyword_score(query_tokens, tokenize(f"{doc.title}\n{doc.chunk}"))) for doc in docs
        ]
    scored = [item for item in scored if item["score"] > 0.0]
    scored.sort(key=lambda item: (-item["score"], item["title"], item["id"]))
    return scored[: max(k, 0)]


# Alias bound before query_knowledge's `query` parameter shadows the name.
_session_query = query


def query_knowledge(tenant_id: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    """In-process entry point for the orchestrator (opens its own session)."""
    with session_scope() as session:
        return _session_query(session, tenant_id, query, k)
