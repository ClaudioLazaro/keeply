"""Pydantic structured I/O for the LLM RCA path (ADR-0007)."""

from pydantic import BaseModel, Field


class LlmHypothesis(BaseModel):
    """One hypothesis as returned by the model; refs are [E#]/[K#] markers."""

    title: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)


class LlmRcaResponse(BaseModel):
    summary: str
    hypotheses: list[LlmHypothesis]
