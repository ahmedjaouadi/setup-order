from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        return self._connection

    def initialize(self) -> None:
        with self._lock:
            conn = self.connection
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS setups (
                    setup_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_zone TEXT NOT NULL DEFAULT '',
                    stop_loss REAL,
                    risk_amount REAL,
                    order_status TEXT NOT NULL DEFAULT '',
                    position_status TEXT NOT NULL DEFAULT '',
                    last_event TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    trigger_price REAL,
                    limit_price REAL,
                    stop_price REAL,
                    broker_order_id TEXT,
                    broker_perm_id TEXT,
                    parent_id TEXT,
                    oca_group TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    average_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    current_stop REAL,
                    risk_remaining REAL NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    setup_id TEXT,
                    symbol TEXT,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_setups_status ON setups(status);
                CREATE INDEX IF NOT EXISTS idx_orders_setup_id ON orders(setup_id);
                CREATE INDEX IF NOT EXISTS idx_orders_broker_perm_id ON orders(broker_perm_id);
                CREATE INDEX IF NOT EXISTS idx_events_setup_id ON events(setup_id);
                CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
                CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);
                """
            )

    def execute(
        self,
        statement: str,
        parameters: Iterable[Any] = (),
    ) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.execute(statement, tuple(parameters))

    def executemany(
        self,
        statement: str,
        rows: Iterable[Iterable[Any]],
    ) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.executemany(statement, rows)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

