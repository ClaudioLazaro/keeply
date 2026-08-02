# AIOps control plane image (aiops-api + mcp-gateway share the codebase)
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY aiops_api ./aiops_api
COPY mcp_gateway ./mcp_gateway
COPY packages ./packages
# Migrations (aiops-migrate service runs `alembic -c alembic.ini upgrade head`)
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
# Seed content for the Knowledge Engine (AIOPS_KNOWLEDGE_SEED_DIR)
COPY examples ./examples

RUN pip install --no-cache-dir '.[live]'
# Default command runs aiops-api; override with mcp_gateway.main:app for the gateway
CMD ["uvicorn", "aiops_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
