"""Seed loading for the Knowledge Engine (ADR-0005).

Runbook seeding: markdown files under ``AIOPS_KNOWLEDGE_SEED_DIR`` (default
``examples/runbooks``, resolved relative to the keep-aiops package root) are
chunked by ATX heading section and upserted as KnowledgeDocument rows with
deterministic ids, so seeding is safe to run at every startup.

Incident history: ``sync_incident_history`` is the M2 stub of the learning
loop — packages/keep_client cannot yet list resolved incidents or their
comments, so callers hand in resolved-incident payloads (id + RCA comment)
which are indexed as documents for future retrieval.
"""

import hashlib
import logging
import re
from pathlib import Path

from sqlmodel import Session, select

from aiops_api.db import session_scope
from aiops_api.modules.knowledge.embedder import Embedder, get_embedder
from aiops_api.modules.knowledge.models import KnowledgeDocument, _utcnow
from aiops_api.modules.policy.models import GLOBAL_TENANT
from aiops_api.settings import get_settings

logger = logging.getLogger(__name__)

# keep-aiops package root (…/keep-aiops/aiops_api/modules/knowledge/ingest.py).
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<heading>.+?)\s*$")

# Sentinel: embedder argument omitted -> resolve from settings (auto mode).
_AUTO = object()


def _document_id(tenant_id: str, source: str, title: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}|{source}|{title}".encode()).hexdigest()
    return digest[:32]


def _resolve_seed_dir(seed_dir: str | Path | None) -> Path:
    path = Path(seed_dir) if seed_dir is not None else Path(get_settings().knowledge_seed_dir)
    if not path.is_absolute():
        path = _PACKAGE_ROOT / path
    return path


def chunk_markdown(text: str, source: str) -> list[tuple[str, str]]:
    """Split runbook markdown into (title, chunk) per ATX-heading section.

    Content before the first heading becomes an ``Overview`` section titled
    after the source. Section titles are ``"<source> :: <heading>"``.
    """
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if not body and current_heading is None:
            return
        if current_heading is None:
            if body:
                sections.append((source, [body]))
        elif body or current_heading:
            sections.append((f"{source} :: {current_heading}", [body]))

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_heading = match.group("heading").strip()
            buffer = []
        else:
            buffer.append(line)
    flush()

    chunks: list[tuple[str, str]] = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        heading = title.split(" :: ", 1)[-1]
        chunks.append((title, f"{heading}\n{body}" if title != source else body))
    return chunks


def _upsert_document(
    session: Session,
    *,
    tenant_id: str,
    source: str,
    title: str,
    chunk: str,
    embedding: list[float] | None,
    doc_metadata: dict,
) -> bool:
    """Insert or update-in-place. Returns True when a new row was inserted."""
    doc_id = _document_id(tenant_id, source, title)
    existing = session.get(KnowledgeDocument, doc_id)
    if existing is None:
        session.add(
            KnowledgeDocument(
                id=doc_id,
                tenant_id=tenant_id,
                source=source,
                title=title,
                chunk=chunk,
                embedding=embedding,
                doc_metadata=doc_metadata,
            )
        )
        return True
    if existing.chunk != chunk or existing.embedding != embedding or existing.doc_metadata != doc_metadata:
        existing.chunk = chunk
        existing.embedding = embedding
        existing.doc_metadata = doc_metadata
        session.add(existing)
    return False


def seed_runbooks(
    session: Session,
    tenant_id: str,
    seed_dir: str | Path | None = None,
    *,
    embedder: Embedder | None | object = _AUTO,
) -> int:
    """Upsert runbook chunks for a tenant; returns the number of NEW rows.

    Idempotent: re-seeding unchanged content inserts nothing. Missing seed
    dir is a no-op with a warning (never crashes callers/startup).
    """
    if embedder is _AUTO:
        embedder = get_embedder()
    directory = _resolve_seed_dir(seed_dir)
    if not directory.is_dir():
        logger.warning("knowledge seed dir not found, skipping", extra={"seed_dir": str(directory)})
        return 0

    inserted = 0
    for path in sorted(directory.glob("*.md")):
        source = path.name
        chunks = chunk_markdown(path.read_text(encoding="utf-8"), source)
        texts = [f"{title}\n{chunk}" for title, chunk in chunks]
        embeddings = embedder(texts) if embedder is not None and texts else None
        for index, (title, chunk) in enumerate(chunks):
            inserted += _upsert_document(
                session,
                tenant_id=tenant_id,
                source=source,
                title=title,
                chunk=chunk,
                embedding=embeddings[index] if embeddings is not None else None,
                doc_metadata={"seed_dir": str(directory), "file": source, "section_index": index},
            )
    logger.info(
        "runbooks seeded",
        extra={"tenant_id": tenant_id, "seed_dir": str(directory), "inserted": inserted},
    )
    return inserted


def seed_global_runbooks() -> None:
    """Idempotent startup seed of shared runbooks under the global '*' tenant.

    Never raises: a missing seed dir or an unmigrated schema must not block
    api startup (mirrors the policy seeding pattern in main.lifespan).
    """
    try:
        with session_scope() as session:
            already_seeded = session.exec(
                select(KnowledgeDocument.id).where(KnowledgeDocument.tenant_id == GLOBAL_TENANT).limit(1)
            ).first()
            if already_seeded is not None:
                return
            inserted = seed_runbooks(session, tenant_id=GLOBAL_TENANT)
        if inserted:
            logger.info("seeded global runbooks", extra={"inserted": inserted})
    except Exception:
        logger.warning("knowledge seed skipped (seed dir or schema not ready?)", exc_info=True)


def sync_incident_history(
    session: Session,
    tenant_id: str,
    incidents: list[dict] | None = None,
    *,
    keep_client: object | None = None,
    embedder: Embedder | None | object = _AUTO,
) -> int:
    """Learning-loop stub (ADR-0005): index resolved-incident RCA comments.

    M2 keeps this caller-driven: packages/keep_client exposes no endpoint to
    list resolved incidents or their comments, so callers (e.g. the M3
    postmortem generator) pass ``incidents`` as a list of
    ``{"incident_id", "title", "rca_comment"}`` mappings; each non-empty RCA
    comment becomes a KnowledgeDocument with ``source="incident:<id>"``.
    ``keep_client`` is accepted for forward compatibility and unused for now.
    Returns the number of NEW rows inserted.
    """
    if not incidents:
        logger.info(
            "incident history sync skipped (no resolved incidents supplied)",
            extra={"tenant_id": tenant_id, "keep_client": keep_client is not None},
        )
        return 0
    if embedder is _AUTO:
        embedder = get_embedder()

    prepared = [
        (item, str(item.get("rca_comment", "")).strip())
        for item in incidents
        if str(item.get("rca_comment", "")).strip()
    ]
    embeddings = (
        embedder([comment for _, comment in prepared]) if embedder is not None and prepared else None
    )

    inserted = 0
    for index, (item, comment) in enumerate(prepared):
        incident_id = str(item.get("incident_id", "unknown"))
        title = str(item.get("title") or f"incident {incident_id}") + " (RCA)"
        inserted += _upsert_document(
            session,
            tenant_id=tenant_id,
            source=f"incident:{incident_id}",
            title=title,
            chunk=comment,
            embedding=embeddings[index] if embeddings is not None else None,
            doc_metadata={"incident_id": incident_id, "kind": "rca_comment", "indexed_at": _utcnow().isoformat()},
        )
    logger.info("incident history synced", extra={"tenant_id": tenant_id, "inserted": inserted})
    return inserted
