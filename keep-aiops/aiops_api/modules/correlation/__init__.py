"""Keeply Alert Correlation — Keep's external-AI contract, served locally.

Groups related alerts into one incident instead of leaving one incident
per alert. Registered in Keep as an AI plugin (see
keep/api/models/db/ai_external.py) so it appears on the native /ai page
alongside any other algorithm.
"""

from aiops_api.modules.correlation.router import router

__all__ = ["router"]
