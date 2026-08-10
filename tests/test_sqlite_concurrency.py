"""SQLite must wait for a lock, not drop the event.

Fourteen Datadog alerts were lost during a provider backfill. The cause was
not the payloads — they replay cleanly — but two SQLite defaults acting
together: a writer locks the whole database file, and the default busy
timeout is zero, so a competing writer fails immediately instead of waiting
milliseconds for its turn.

The operator saw "Error processing event, contact Keep team for more
information", because the traceback ends in SQLAlchemy's `do_execute`
rather than a provider's `_format_alert`, and `process_event_task` reads
that as an internal bug.
"""

import sqlite3
import threading
import time

import pytest


def _write_lock(path, hold_seconds):
    """Hold an exclusive write lock, then release it."""
    holder = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    holder.execute("PRAGMA busy_timeout=0")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("CREATE TABLE IF NOT EXISTS t (x)")
    holder.execute("INSERT INTO t VALUES (1)")

    def release():
        time.sleep(hold_seconds)
        holder.execute("ROLLBACK")

    threading.Thread(target=release, daemon=True).start()
    return holder


def _try_write(path, journal, busy_timeout_ms):
    other = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    other.execute(f"PRAGMA journal_mode={journal}")
    other.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    other.execute("BEGIN IMMEDIATE")
    other.execute("ROLLBACK")
    other.close()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "probe.db")
    sqlite3.connect(path).execute("CREATE TABLE IF NOT EXISTS t (x)")
    return path


def _set_journal(path, journal):
    """Set the journal mode while nothing holds the lock.

    Deliberately separate from the writer under test: `journal_mode=WAL`
    needs the exclusive lock itself, so it cannot be applied *during*
    contention. In production the first connection at startup sets it, and
    it is a persistent property of the file from then on.
    """
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA journal_mode={journal}")
    connection.close()


def test_the_defaults_drop_the_event(db):
    # The behaviour that lost fourteen alerts. Asserted so the fix below is
    # measured against something real rather than assumed.
    holder = _write_lock(db, hold_seconds=0.4)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _try_write(db, journal="DELETE", busy_timeout_ms=0)
    time.sleep(0.5)
    holder.close()


def test_a_busy_timeout_waits_for_its_turn(db):
    # The lock was held for milliseconds. Waiting is all any of those
    # fourteen events needed.
    _set_journal(db, "WAL")
    holder = _write_lock(db, hold_seconds=0.3)
    started = time.time()
    _try_write(db, journal="WAL", busy_timeout_ms=15000)
    waited = time.time() - started
    assert waited >= 0.2, "returned too fast to have waited for the lock"
    time.sleep(0.3)
    holder.close()


def test_wal_lets_a_reader_through_while_a_writer_holds_the_lock(db):
    # The other half: under the default journal a reader blocks on a
    # writer, which is why GET /alerts degraded during ingestion.
    _set_journal(db, "WAL")
    holder = _write_lock(db, hold_seconds=0.4)

    reader = sqlite3.connect(db, check_same_thread=False)
    reader.execute("PRAGMA busy_timeout=0")
    started = time.time()
    reader.execute("SELECT count(*) FROM t").fetchone()
    assert time.time() - started < 0.1, "reader blocked on the writer"

    time.sleep(0.5)
    reader.close()
    holder.close()


def test_applying_wal_during_contention_fails_and_must_not_be_relied_on(db):
    """The pragma needs the very lock it is meant to relieve.

    So the connect hook can only set it when the database is quiet — which
    at startup it is, before the process serves traffic. Recorded as a test
    because "apply WAL on every connection" reads as though it always
    works, and on a busy file it never would.
    """
    _set_journal(db, "DELETE")
    holder = _write_lock(db, hold_seconds=0.4)

    connection = sqlite3.connect(db)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        connection.execute("PRAGMA journal_mode=WAL")

    time.sleep(0.5)
    connection.close()
    holder.close()
