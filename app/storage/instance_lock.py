from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOW_MULTI_INSTANCE_ENV = "SETUP_ORDER_ALLOW_MULTI_INSTANCE"


class InstanceLockError(RuntimeError):
    """Another live process already owns this database."""


class InstanceLock:
    """Exclusive OS-level lock tying one process to one SQLite database.

    Why: the app must have a single writer per database file AND a single
    engine driving the TWS account. run.py only guarded a port range, so
    uvicorn instances launched on out-of-range ports piled up against the
    same database and broker (4 concurrent instances, 2026-07-08 incident:
    "database is locked" storms froze the engine heartbeat).

    The lock is an OS advisory lock held on `<database_file>.lock` for the
    process lifetime: it dies with the process (including crashes), so there
    are no stale locks to clean up. Instances pointed at different database
    files do not collide, which keeps tests and deliberate sandboxes working.
    Set SETUP_ORDER_ALLOW_MULTI_INSTANCE=1 to bypass explicitly.
    """

    def __init__(self, database_file: Path) -> None:
        self.path = Path(f"{database_file}.lock")
        self._handle = None

    def acquire(self) -> None:
        if os.environ.get(ALLOW_MULTI_INSTANCE_ENV, "").strip() in {"1", "true", "yes"}:
            logger.warning(
                "Instance lock BYPASSED via %s: concurrent instances on %s "
                "can duplicate orders and corrupt state",
                ALLOW_MULTI_INSTANCE_ENV,
                self.path,
            )
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            handle.seek(0)
            self._lock_first_byte(handle)
        except OSError as exc:
            try:
                # On Windows, reading a byte range locked by another handle
                # raises PermissionError too; the holder pid is best-effort.
                holder_pid = handle.read().strip() or "unknown"
            except OSError:
                holder_pid = "unknown"
            handle.close()
            raise InstanceLockError(
                f"Another Setup Order instance (pid {holder_pid}) already owns "
                f"{self.path}. Running two instances against the same database "
                "and TWS account risks duplicated orders. Stop the other "
                "instance first, or point this one at a different "
                "storage.database_file."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        logger.info("Instance lock acquired on %s (pid %s)", self.path, os.getpid())

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            self._unlock_first_byte(self._handle)
        except OSError:
            # The lock dies with the process anyway; never fail shutdown on it.
            logger.warning("Instance lock release failed on %s", self.path)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _lock_first_byte(handle) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_first_byte(handle) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_instance_lock(database_file: Path) -> InstanceLock:
    lock = InstanceLock(database_file)
    lock.acquire()
    return lock
