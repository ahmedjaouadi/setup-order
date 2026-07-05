from __future__ import annotations

import json
from typing import Any

from app.models import utc_now_iso
from app.opportunity_scanner.technique_repository import TechniqueRepository

# Faithful translation of the 7 hardcoded rules in `detectors.py` into declarative
# rule_json. Kept in strict sync with `detect_opportunity_types()` - the
# non-regression test in `tests/test_technique_seed.py` is the source of truth.
#
# SECTOR_LEADER is a special case: in `detectors.py` it only fires when
# RELATIVE_STRENGTH_LEADER already matched (rs_spy >= 3 OR rs_sector >= 2) AND
# rs_sector >= 2. Since rs_sector >= 2 alone already satisfies the OR condition,
# the combined condition simplifies to just `rs_sector >= 2`.
_BUILTIN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "technique_id": "intraday_momentum_anomaly_v1",
        "name": "Intraday momentum anomaly",
        "description": "Stock is up at least 5% intraday.",
        "rule": {
            "field": "perf_stock_1d",
            "op": ">=",
            "value": 5,
            "opportunity_type": "INTRADAY_MOMENTUM_ANOMALY",
        },
    },
    {
        "technique_id": "relative_strength_leader_v1",
        "name": "Relative strength leader",
        "description": "Stock outperforms SPY by >= 3% or its sector by >= 2%.",
        "rule": {
            "any": [
                {"field": "rs_spy", "op": ">=", "value": 3},
                {"field": "rs_sector", "op": ">=", "value": 2},
            ],
            "opportunity_type": "RELATIVE_STRENGTH_LEADER",
        },
    },
    {
        "technique_id": "volume_expansion_v1",
        "name": "Volume expansion",
        "description": "Volume is at least 1.5x its reference average.",
        "rule": {
            "field": "volume_ratio",
            "op": ">=",
            "value": 1.5,
            "opportunity_type": "VOLUME_EXPANSION",
        },
    },
    {
        "technique_id": "breakout_candidate_v1",
        "name": "Breakout candidate",
        "description": "Price is within 1.5% of a breakout level.",
        "rule": {
            "field": "breakout_proximity",
            "op": "<=",
            "value": 1.5,
            "opportunity_type": "BREAKOUT_CANDIDATE",
        },
    },
    {
        "technique_id": "gap_and_hold_v1",
        "name": "Gap and hold",
        "description": "Stock gapped up at least 3% and is still positive on the day.",
        "rule": {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {"field": "perf_stock_1d", "op": ">", "value": 0},
            ],
            "opportunity_type": "GAP_AND_HOLD",
        },
    },
    {
        "technique_id": "watchlist_anomaly_v1",
        "name": "Watchlist anomaly",
        "description": "Stock has printed a new intraday high.",
        "rule": {
            "field": "new_intraday_high",
            "op": "==",
            "value": True,
            "opportunity_type": "WATCHLIST_ANOMALY",
        },
    },
    {
        "technique_id": "sector_leader_v1",
        "name": "Sector leader",
        "description": "Stock outperforms its sector by >= 2% (implies relative strength leader).",
        "rule": {
            "field": "rs_sector",
            "op": ">=",
            "value": 2,
            "opportunity_type": "SECTOR_LEADER",
        },
    },
)


def builtin_technique_definitions() -> tuple[dict[str, Any], ...]:
    return _BUILTIN_DEFINITIONS


def seed_builtin_techniques(repository: TechniqueRepository) -> None:
    """Idempotently insert the 7 builtin techniques (INSERT OR IGNORE)."""
    now = utc_now_iso()
    for definition in _BUILTIN_DEFINITIONS:
        repository.insert_if_absent(
            {
                "technique_id": definition["technique_id"],
                "name": definition["name"],
                "description": definition["description"],
                "rule_json": json.dumps(definition["rule"]),
                "enabled": True,
                "origin": "builtin",
                "parent_id": None,
                "status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            }
        )
