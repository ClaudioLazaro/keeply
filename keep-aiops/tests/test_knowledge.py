"""Knowledge Engine tests (ADR-0005): retrieval modes, tenant isolation, seeding, API contract."""

import pytest
from sqlmodel import Session, func, select

from tests.conftest import TENANT_ID


@pytest.fixture()
def db(settings_env):
    """Isolated SQLite with all tables created (no app/lifespan seeding)."""
    from aiops_api.db import init_db

    init_db()
    yield


@pytest.fixture()
def models():
    from aiops_api.modules.knowledge import ingest, retriever
    from aiops_api.modules.knowledge.models import KnowledgeDocument

    return ingest, retriever, KnowledgeDocument


def _add_doc(session, KnowledgeDocument, **overrides):
    doc = KnowledgeDocument(
        id=overrides.get("id", f"doc-{overrides['title']}"),
        tenant_id=overrides.get("tenant_id", TENANT_ID),
        source=overrides.get("source", "test"),
        title=overrides["title"],
        chunk=overrides.get("chunk", ""),
        embedding=overrides.get("embedding"),
        doc_metadata=overrides.get("doc_metadata", {}),
    )
    session.add(doc)
    return doc


# --- keyword retrieval -------------------------------------------------------


def test_keyword_retrieval_ranks_oom_runbook_first(db, models):
    """'payment-api OOMKilled memory' must rank the OOM runbook above all others."""
    ingest, retriever, KnowledgeDocument = models
    from aiops_api.db import session_scope

    with session_scope() as session:
        ingest.seed_runbooks(session, TENANT_ID)
        results = retriever.query(session, TENANT_ID, "payment-api OOMKilled memory", k=10)

    assert results, "expected runbook hits"
    assert results[0]["source"] == "payment-api-oom-restarts.md"
    assert results[0]["score"] == pytest.approx(1.0)
    scores = [item["score"] for item in results]
    assert scores == sorted(scores, reverse=True)
    # Unrelated runbook scores strictly below the OOM runbook.
    assert all(item["score"] < results[0]["score"] for item in results if item["source"] == "tls-certificate-rotation.md")


def test_keyword_retrieve_pure_function(models):
    """Pure in-process helper used by the eval harness: scoring, tie-break, drops."""
    _, retriever, _ = models

    docs = [
        {"id": "b", "title": "Beta guide", "chunk": "memory restart pod", "extra": 1},
        {"id": "a", "title": "Alpha guide", "chunk": "memory restart pod"},
        {"id": "c", "title": "Gamma", "chunk": "unrelated content entirely"},
    ]
    results = retriever.keyword_retrieve("memory restart", docs, k=5)

    # Zero-overlap doc dropped; ties broken by title (Alpha before Beta);
    # extra keys pass through; scores are exact token-overlap ratios.
    assert [r["id"] for r in results] == ["a", "b"]
    assert results[0]["score"] == pytest.approx(1.0)
    assert results[1]["extra"] == 1

    partial = retriever.keyword_retrieve("memory restart missing tokens", docs)
    assert partial[0]["score"] == pytest.approx(0.5)
    assert retriever.keyword_retrieve("", docs) == []
    # k truncates after ranking.
    single = retriever.keyword_retrieve("memory", docs, k=1)
    assert len(single) == 1 and single[0]["id"] == "a" and single[0]["score"] == pytest.approx(1.0)


def test_tenant_isolation(db, models):
    """A tenant's private docs are never returned to another tenant; global '*' docs are shared."""
    ingest, retriever, KnowledgeDocument = models
    from aiops_api.db import session_scope

    with session_scope() as session:
        _add_doc(session, KnowledgeDocument, title="alpha-secret", tenant_id="tenant-a",
                 chunk="payment-api oomkilled memory leak playbook")
        _add_doc(session, KnowledgeDocument, title="beta-secret", tenant_id="tenant-b",
                 chunk="payment-api oomkilled memory leak playbook")
        _add_doc(session, KnowledgeDocument, title="global-runbook", tenant_id="*",
                 chunk="payment-api oomkilled memory shared runbook")

        results_a = retriever.query(session, "tenant-a", "payment-api oomkilled memory")
        results_b = retriever.query(session, "tenant-b", "payment-api oomkilled memory")

    assert {r["id"] for r in results_a} == {"doc-alpha-secret", "doc-global-runbook"}
    assert {r["id"] for r in results_b} == {"doc-beta-secret", "doc-global-runbook"}


# --- embedding mode ----------------------------------------------------------


def test_embedding_mode_used_when_embedder_configured(db, models):
    """With an embedder, cosine similarity (not keyword overlap) decides ranking."""
    ingest, retriever, KnowledgeDocument = models
    from aiops_api.db import session_scope

    # Keyword overlap favours 'keyword-hit'; the fake embedder favours 'vector-hit'.
    with session_scope() as session:
        _add_doc(session, KnowledgeDocument, title="keyword-hit", chunk="apple apple apple", embedding=[1.0, 0.0])
        _add_doc(session, KnowledgeDocument, title="vector-hit", chunk="zebra", embedding=[0.0, 1.0])
        _add_doc(session, KnowledgeDocument, title="no-embedding", chunk="apple", embedding=None)

        fake_embedder = lambda texts: [[0.0, 1.0] for _ in texts]  # noqa: E731
        results = retriever.query(session, TENANT_ID, "apple", embedder=fake_embedder)

    assert [r["id"] for r in results] == ["doc-vector-hit"]
    assert results[0]["score"] == pytest.approx(1.0)


def test_get_embedder_disabled_by_default(db):
    from aiops_api.modules.knowledge.embedder import get_embedder

    assert get_embedder() is None  # AIOPS_LLM_EMBEDDING_MODEL unset -> keyword mode


def test_get_embedder_configured(settings_env, monkeypatch):
    monkeypatch.setenv("AIOPS_LLM_EMBEDDING_MODEL", "text-embedding-3-small")
    from aiops_api.settings import get_settings

    get_settings.cache_clear()
    from aiops_api.modules.knowledge.embedder import get_embedder

    embedder = get_embedder()
    assert callable(embedder)
    get_settings.cache_clear()


# --- ingestion ---------------------------------------------------------------


def test_chunk_markdown_splits_sections(models):
    ingest, _, _ = models

    chunks = ingest.chunk_markdown("intro text\n\n# Title\n\ntitle body\n\n## Sec A\n\nbody a\n\n## Sec B\n\nbody b\n", "rb.md")
    titles = [title for title, _ in chunks]
    assert titles == ["rb.md", "rb.md :: Title", "rb.md :: Sec A", "rb.md :: Sec B"]
    assert chunks[0][1] == "intro text"
    assert chunks[2][1].startswith("Sec A\nbody a")


def test_seed_idempotent(db, models):
    ingest, _, KnowledgeDocument = models
    from aiops_api.db import get_engine, session_scope

    with session_scope() as session:
        first = ingest.seed_runbooks(session, TENANT_ID)
    assert first > 0

    with session_scope() as session:
        second = ingest.seed_runbooks(session, TENANT_ID)
    assert second == 0

    with Session(get_engine()) as session:
        count = session.exec(select(func.count()).select_from(KnowledgeDocument)).one()
    assert count == first  # upserts, never duplicates


def test_seed_missing_dir_is_noop(db, models, tmp_path):
    ingest, _, _ = models
    from aiops_api.db import session_scope

    with session_scope() as session:
        assert ingest.seed_runbooks(session, TENANT_ID, seed_dir=tmp_path / "nope") == 0


def test_seed_embeds_chunks_when_embedder_configured(db, models):
    ingest, retriever, KnowledgeDocument = models
    from aiops_api.db import session_scope

    fake_embedder = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]  # noqa: E731
    with session_scope() as session:
        ingest.seed_runbooks(session, TENANT_ID, embedder=fake_embedder)
        results = retriever.query(session, TENANT_ID, "anything at all", embedder=fake_embedder)
    assert results, "embedded docs must be retrievable in embedding mode"
    assert all(item["score"] == pytest.approx(1.0) for item in results)


def test_sync_incident_history_stub(db, models):
    ingest, retriever, KnowledgeDocument = models
    from aiops_api.db import session_scope

    incidents = [
        {"incident_id": "inc-1", "title": "payment-api OOM", "rca_comment": "Root cause: memory leak in settlement batch [E1]."},
        {"incident_id": "inc-2", "title": "no comment", "rca_comment": ""},
    ]
    with session_scope() as session:
        inserted = ingest.sync_incident_history(session, TENANT_ID, incidents, embedder=None)
        assert inserted == 1
        again = ingest.sync_incident_history(session, TENANT_ID, incidents, embedder=None)
        assert again == 0
        assert ingest.sync_incident_history(session, TENANT_ID, [], embedder=None) == 0
        results = retriever.query(session, TENANT_ID, "memory leak settlement", embedder=None)
    assert results and results[0]["source"] == "incident:inc-1"
    assert results[0]["id"]  # citation-friendly stable id


def test_startup_seed_resilient_and_idempotent(db, monkeypatch, tmp_path):
    """seed_global_runbooks never raises (missing dir) and skips when rows exist."""
    from aiops_api.modules.knowledge.ingest import seed_global_runbooks
    from aiops_api.settings import get_settings

    monkeypatch.setenv("AIOPS_KNOWLEDGE_SEED_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    seed_global_runbooks()  # missing dir -> warn only, no raise

    monkeypatch.setenv("AIOPS_KNOWLEDGE_SEED_DIR", "examples/runbooks")
    get_settings.cache_clear()
    seed_global_runbooks()  # real seed under '*' tenant
    from aiops_api.db import session_scope
    from aiops_api.modules.knowledge.models import KnowledgeDocument

    with session_scope() as session:
        global_rows = session.exec(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == "*")).all()
    assert global_rows
    seed_global_runbooks()  # rows exist -> skip (no duplicate work)


# --- API contract ------------------------------------------------------------


def test_query_api_contract(client):
    tenant = "tenant-api"
    client.post("/v1/knowledge/sources:seed", json={"tenant_id": tenant})

    response = client.post("/v1/knowledge/query", json={"tenant_id": tenant, "query": "payment-api OOMKilled memory", "k": 3})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"results"}
    assert 1 <= len(payload["results"]) <= 3
    for item in payload["results"]:
        assert set(item) == {"id", "title", "source", "chunk", "score"}
        assert 0.0 < item["score"] <= 1.0
    scores = [item["score"] for item in payload["results"]]
    assert scores == sorted(scores, reverse=True)

    # Default k=5
    response = client.post("/v1/knowledge/query", json={"tenant_id": tenant, "query": "connection pool"})
    assert response.status_code == 200
    assert response.json()["results"][0]["source"] == "db-connection-pool-exhaustion.md"


def test_query_api_requires_tenant_when_auth_disabled(client):
    response = client.post("/v1/knowledge/query", json={"query": "anything"})
    assert response.status_code == 422


def test_sources_seed_endpoint_idempotent(client):
    first = client.post("/v1/knowledge/sources:seed", json={"tenant_id": "tenant-seed"})
    assert first.status_code == 200
    assert first.json()["seeded"] > 0

    second = client.post("/v1/knowledge/sources:seed", json={"tenant_id": "tenant-seed"})
    assert second.status_code == 200
    assert second.json() == {"tenant_id": "tenant-seed", "seeded": 0}
