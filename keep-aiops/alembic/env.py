"""Alembic environment for keep-aiops.

Metadata source: SQLModel (all module models imported below so their tables
register). URL source: aiops_api.settings (AIOPS_DATABASE_URL) — never
hardcode credentials here.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from aiops_api.settings import get_settings

# Import every module's models so tables register on SQLModel.metadata.
import aiops_api.modules.event_bridge.models  # noqa: F401
import aiops_api.modules.orchestrator.models  # noqa: F401

try:  # policy models land with the persisted-policy slice (M1)
    import aiops_api.modules.policy.models  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    pass

try:  # knowledge models land with the knowledge slice (M2)
    import aiops_api.modules.knowledge.models  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    pass

try:  # rca models land with the RCA slice (M2)
    import aiops_api.modules.rca.models  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    pass

try:  # knowledge models land with the knowledge-engine slice (M2)
    import aiops_api.modules.knowledge.models  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    pass

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
