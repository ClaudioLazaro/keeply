"""RCA module: hypothesis generation with citations (ADR-0007)."""

from aiops_api.modules.rca.engine import generate_rca
from aiops_api.modules.rca.fallback import deterministic_rca
from aiops_api.modules.rca.models import Hypothesis

__all__ = ["Hypothesis", "deterministic_rca", "generate_rca"]
