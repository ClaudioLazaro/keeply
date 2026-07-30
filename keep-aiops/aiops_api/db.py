"""Shared SQLModel engine/session helpers.

SQLite via settings.database_url (M0); the same setting moves to Postgres in M1
without touching call sites. ``reset_engine`` exists for tests that repoint
AIOPS_DATABASE_URL.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from aiops_api.settings import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db() -> None:
    """create_all for every module's tables (dev/test only).

    Skipped when AIOPS_AUTO_CREATE_TABLES=false — production Postgres schemas
    are owned by alembic migrations instead.
    """
    if not get_settings().auto_create_tables:
        return
    # Import model modules so their tables register on SQLModel.metadata.
    import aiops_api.modules.event_bridge.models  # noqa: F401
    import aiops_api.modules.feedback.models  # noqa: F401
    import aiops_api.modules.knowledge.models  # noqa: F401
    import aiops_api.modules.orchestrator.models  # noqa: F401
    import aiops_api.modules.policy.models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    # expire_on_commit=False: background-task results (evidence summaries,
    # investigation fields) are consumed after the session closes.
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
