"""Knowledge Engine module (ADR-0005): RAG over runbooks + incident history.

M2 scope: runbook seeding from markdown files, pluggable embedding (LiteLLM
when AIOPS_LLM_EMBEDDING_MODEL is set, else deterministic keyword-overlap
retrieval), tenant-scoped query API. pgvector is an M3+ optimization; the
embedding column is a portable JSON list[float] for now.
"""

from aiops_api.modules.knowledge.retriever import keyword_retrieve, query_knowledge

__all__ = ["keyword_retrieve", "query_knowledge"]
