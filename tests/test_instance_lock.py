from __future__ import annotations

import pytest

from app.models import EventLevel
from app.storage.event_store import EventStore
from app.storage.instance_lock import (
    ALLOW_MULTI_INSTANCE_ENV,
    InstanceLock,
    InstanceLockError,
)


def test_second_instance_is_refused(tmp_path):
    database_file = tmp_path / "trading_state.sqlite"
    first = InstanceLock(database_file)
    first.acquire()
    try:
        second = InstanceLock(database_file)
        with pytest.raises(InstanceLockError, match="already owns"):
            second.acquire()
    finally:
        first.release()


def test_lock_is_reacquirable_after_release(tmp_path):
    database_file = tmp_path / "trading_state.sqlite"
    first = InstanceLock(database_file)
    first.acquire()
    first.release()
    second = InstanceLock(database_file)
    second.acquire()
    second.release()


def test_different_databases_do_not_collide(tmp_path):
    first = InstanceLock(tmp_path / "one.sqlite")
    second = InstanceLock(tmp_path / "two.sqlite")
    first.acquire()
    try:
        second.acquire()
        second.release()
    finally:
        first.release()


def test_env_override_bypasses_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(ALLOW_MULTI_INSTANCE_ENV, "1")
    database_file = tmp_path / "trading_state.sqlite"
    first = InstanceLock(database_file)
    first.acquire()
    second = InstanceLock(database_file)
    second.acquire()  # must not raise
    first.release()
    second.release()


class _FailingRepository:
    def add_event(self, record):
        raise RuntimeError("database is locked")


def test_event_store_record_never_raises():
    # A failed telemetry write must never abort the flow being observed
    # (a raising write froze the engine heartbeat, 2026-07-08 incident).
    store = EventStore(_FailingRepository())
    store.record(EventLevel.WARNING, "stock_poll_timeout", "boom")
    store.record(EventLevel.INFO, "tws_request", "boom again")
    assert store.failed_writes == 2
