from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage.database import Database


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SQLite migrations or a dry-run copy.")
    parser.add_argument("--database", default="data/trading_state.sqlite")
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    database = Path(args.database)
    if not database.exists():
        parser.error(f"Database not found: {database}")

    if args.apply:
        backup = backup_database(database, Path(args.backup_dir))
        print(f"backup={backup}")
        target = database
    else:
        temp_dir = tempfile.TemporaryDirectory()
        target = Path(temp_dir.name) / database.name
        shutil.copy2(database, target)

    db = Database(target)
    try:
        db.initialize()
    finally:
        db.close()

    print(f"migrated={target}")
    print(f"mode={'apply' if args.apply else 'dry-run'}")
    return 0


def backup_database(database: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{database.stem}.pre_migration.{stamp}.sqlite"
    shutil.copy2(database, backup)
    return backup


if __name__ == "__main__":
    raise SystemExit(main())
