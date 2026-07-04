from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup.")
    parser.add_argument("--database", default="data/trading_state.sqlite")
    parser.add_argument("--output-dir", default="data/backups")
    parser.add_argument("--keep", type=int, default=30)
    args = parser.parse_args()

    database = Path(args.database)
    if not database.exists():
        parser.error(f"Database not found: {database}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = output_dir / f"{database.stem}.{stamp}.sqlite"

    source = sqlite3.connect(database)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    prune_backups(output_dir, database.stem, max(1, args.keep))
    print(backup_path)
    return 0


def prune_backups(output_dir: Path, database_stem: str, keep: int) -> None:
    backups = sorted(
        output_dir.glob(f"{database_stem}.*.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in backups[keep:]:
        backup.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
