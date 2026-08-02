"""Specialist agents for M3 (ADR-0002 + milestones.mdx).

A *specialist* is a typed wrapper around a related set of MCP read tools. The
coordinator runs specialists in order, asking each to gather evidence; each
specialist decides which of its tools to invoke and how to chain their
results (e.g. ``get_pods`` first, then ``get_logs`` with the discovered pod
name). Specialists never raise out of a tool failure — they translate the
failure into a structured result so the coordinator can record an evidence
gap and continue.

The split is conceptual today (one process, one Python module) but the
interface is designed so individual specialists can later be moved into
their own processes without changing the coordinator. The contract is:

* ``name``     — registry key, e.g. ``"k8s"``
* ``tools``    — the set of MCP tool names this specialist owns
* ``gather``   — given the live MCP catalog + a budget, return one
                 ``SpecialistResult`` with zero or more ``ToolCall`` items
                 (results + evidence-gap notes).

Specialists MUST NOT mutate the catalog or call Keep directly; that is the
coordinator's job. They only call the MCP gateway through the supplied
client.
"""

from aiops_api.modules.specialists.base import (
    Specialist,
    SpecialistResult,
    ToolCall,
)
from aiops_api.modules.specialists.registry import (
    default_specialists,
    get_specialist,
)

__all__ = [
    "Specialist",
    "SpecialistResult",
    "ToolCall",
    "default_specialists",
    "get_specialist",
]
