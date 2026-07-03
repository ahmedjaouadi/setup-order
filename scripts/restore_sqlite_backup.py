from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a SQLite backup with a safety copy.")
    parser.add_argument("backup")
    parser.add_argument("--database", default="data/trading_state.sqlite")
    parser.add_argument("--yes", action="store_true", help="Confirm overwrite of the target database.")
    args = parser.parse_args()

    backup = Path(args.backup)
    database = Path(args.database)
    if not backup.exists():
        parser.error(f"Backup not found: {backup}")
    if not args.yes:
        parser.error("Refusing to overwrite without --yes")

    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_backup = database.with_name(f"{database.stem}.pre_restore.{stamp}.sqlite")
        shutil.copy2(database, safety_backup)
        print(f"safety_backup={safety_backup}")

    shutil.copy2(backup, database)
    print(f"restored={database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
