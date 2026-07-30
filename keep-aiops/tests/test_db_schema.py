"""M1: alembic schema ownership — 0001 upgrade/downgrade round-trips, no drift.

Runs migrations programmatically against a scratch SQLite file (no server
needed). Scoped to the 0001 tables (investigation, evidence, processed_event):
the policy slice owns 0002_policy_tables and the full chain is verified in
integration. An optional Postgres smoke test runs only when docker can start
a postgres:16-alpine container.
"""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect
from sqlmodel import SQLModel

# Import owned model modules so their tables register on SQLModel.metadata —
# mirrors what alembic/env.py does.
import aiops_api.modules.event_bridge.models  # noqa: F401
import aiops_api.modules.orchestrator.models  # noqa: F401
from aiops_api.settings import get_settings

REPO_AIOPS_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_AIOPS_DIR / "alembic.ini"
REV_0001 = "0001_initial_schema"
# Tables created by 0001 (this slice). env.py tolerantly imports other slices'
# models (e.g. policy), which can grow SQLModel.metadata in-process — drift
# comparisons below are restricted to these tables.
OWNED_TABLES = {"investigation", "evidence", "processed_event"}


def _make_config(db_url: str) -> Config:
    # env.py reads the URL from settings (AIOPS_DATABASE_URL, set by the
    # caller's monkeypatch); clear the lru_cache so it is re-read.
    get_settings.cache_clear()
    return Config(str(ALEMBIC_INI))


@pytest.fixture()
def scratch_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migrated.db"
    monkeypatch.setenv("AIOPS_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_owned_tables(scratch_url):
    command.upgrade(_make_config(scratch_url), REV_0001)
    assert _table_names(scratch_url) - {"alembic_version"} == OWNED_TABLES


def test_upgrade_schema_details(scratch_url):
    """Constraints/indexes/fks promised by the models survive the migration."""
    command.upgrade(_make_config(scratch_url), REV_0001)
    engine = create_engine(scratch_url)
    try:
        insp = inspect(engine)
        uniques = {uc["name"] for uc in insp.get_unique_constraints("investigation")}
        assert "uq_investigation_tenant_incident" in uniques
        indexes = {ix["name"] for ix in insp.get_indexes("investigation")}
        assert {"ix_investigation_tenant_id", "ix_investigation_incident_id"} <= indexes
        fks = insp.get_foreign_keys("evidence")
        assert any(fk["referred_table"] == "investigation" for fk in fks)
        assert "ix_evidence_investigation_id" in {ix["name"] for ix in insp.get_indexes("evidence")}
    finally:
        engine.dispose()


def test_downgrade_base_removes_tables(scratch_url):
    cfg = _make_config(scratch_url)
    command.upgrade(cfg, REV_0001)
    command.downgrade(cfg, "base")
    assert _table_names(scratch_url) - {"alembic_version"} == set()


def test_upgrade_downgrade_upgrade_roundtrip(scratch_url):
    cfg = _make_config(scratch_url)
    command.upgrade(cfg, REV_0001)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, REV_0001)
    assert _table_names(scratch_url) - {"alembic_version"} == OWNED_TABLES


def test_no_autogenerate_drift(scratch_url):
    """At head, owned models and schema agree (autogenerate emits no ops).

    Compares against a fresh MetaData holding only this slice's tables and
    filters diffs to owned tables: env.py may have pulled other slices' models
    (e.g. policy) into SQLModel.metadata, and later revisions (0004/0005) add
    their own tables to the database — neither is this slice's concern. Owned
    table *columns* grow via later revisions too (0003 context_pack), so the
    check runs at head rather than at 0001.
    """
    command.upgrade(_make_config(scratch_url), "head")
    owned_metadata = MetaData()
    for name in OWNED_TABLES:
        SQLModel.metadata.tables[name].tometadata(owned_metadata)
    engine = create_engine(scratch_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            diffs = compare_metadata(context, owned_metadata)
    finally:
        engine.dispose()
    owned_diffs = [diff for diff in diffs if getattr(diff, "table_name", None) in OWNED_TABLES]
    assert owned_diffs == []


def _start_docker_postgres() -> tuple[str, str] | None:
    """Start a throwaway postgres:16 container; (url, name) or None.

    The caller owns the container lifecycle (docker rm -f <name>); --rm keeps
    no residue once stopped.
    """
    if not shutil.which("docker"):
        return None
    name = f"aiops-m1-test-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name,
             "-e", "POSTGRES_USER=aiops", "-e", "POSTGRES_PASSWORD=aiops", "-e", "POSTGRES_DB=aiops",
             "-p", "127.0.0.1::5432", "postgres:16-alpine"],
            check=True, capture_output=True, timeout=180,
        )
        port = subprocess.run(
            ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True,
        ).stdout.strip().rsplit(":", 1)[-1]
        url = f"postgresql+psycopg://aiops:aiops@127.0.0.1:{port}/aiops"
        # pg_isready inside the container answers against the entrypoint's
        # temporary init server — poll a real TCP connection instead.
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                probe = create_engine(url)
                with probe.connect():
                    probe.dispose()
                    return url, name
            except Exception:
                time.sleep(1)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
        pass
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    return None


@pytest.mark.skipif(not shutil.which("docker"), reason="docker not available")
def test_postgres_upgrade_smoke(tmp_path, monkeypatch):
    started = _start_docker_postgres()
    if started is None:
        pytest.skip("could not start postgres:16-alpine container")
    url, container = started
    try:
        monkeypatch.setenv("AIOPS_DATABASE_URL", url)
        command.upgrade(_make_config(url), REV_0001)
        assert OWNED_TABLES <= _table_names(url)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
