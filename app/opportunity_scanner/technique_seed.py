from __future__ import annotations

import json
from contextlib import suppress
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

# Time buckets where volume needs no extra confirmation; during LUNCH the
# `any` clause forces the rvol leg instead (lunch penalty, skills.md 25bis).
_NON_LUNCH_BUCKETS: list[str] = ["OPEN", "MORNING", "AFTERNOON", "POWER_HOUR"]

# F1 techniques (TODO step 7.6): consumers of the new features. They are ONLY
# seeded here - the 7 original builtins above are left untouched; their spread
# filter is added by the one-shot migration below (step 7.7), never by editing
# this seed (INSERT OR IGNORE would not update existing rows anyway).
_F1_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "technique_id": "gap_and_go_full_v1",
        "name": "Gap and go (full)",
        "description": (
            "Gap >= 3% that holds above the session VWAP with rvol >= 1.5 and a "
            "tradeable spread - the complete 'holds above VWAP' version of gap-and-hold "
            "(skills.md section 16; lunch penalty per section 25bis)."
        ),
        "rule": {
            "all": [
                {"field": "gap_pct", "op": ">=", "value": 3},
                {"field": "perf_stock_1d", "op": ">", "value": 0},
                {"field": "dist_vwap_pct", "op": ">=", "value": 0},
                {"field": "rvol", "op": ">=", "value": 1.5},
                {"field": "spread_pct", "op": "<=", "value": 0.5},
                {
                    "any": [
                        {"field": "time_bucket", "op": "in", "value": _NON_LUNCH_BUCKETS},
                        {"field": "rvol", "op": ">=", "value": 1.5},
                    ]
                },
            ],
            "opportunity_type": "GAP_AND_GO_FULL",
        },
    },
    {
        "technique_id": "momentum_rvol_confirmed_v1",
        "name": "Momentum confirmed by rvol",
        "description": (
            "Intraday momentum (>= 5% on the day, positive RS vs SPY) confirmed by "
            "rvol >= 1.5 on the canonical SAME_TIME_OF_DAY measure and a tradeable "
            "spread (skills.md sections 6.2bis and 10; lunch penalty per section 25bis)."
        ),
        "rule": {
            "all": [
                {"field": "perf_stock_1d", "op": ">=", "value": 5},
                {"field": "rs_spy", "op": ">", "value": 0},
                {"field": "rvol", "op": ">=", "value": 1.5},
                {"field": "spread_pct", "op": "<=", "value": 0.5},
                {
                    "any": [
                        {"field": "time_bucket", "op": "in", "value": _NON_LUNCH_BUCKETS},
                        {"field": "rvol", "op": ">=", "value": 1.5},
                    ]
                },
            ],
            "opportunity_type": "MOMENTUM_RVOL_CONFIRMED",
        },
    },
)


def builtin_technique_definitions() -> tuple[dict[str, Any], ...]:
    return _BUILTIN_DEFINITIONS


def f1_technique_definitions() -> tuple[dict[str, Any], ...]:
    return _F1_DEFINITIONS


def seed_builtin_techniques(repository: TechniqueRepository) -> None:
    """Idempotently insert the builtin techniques (INSERT OR IGNORE)."""
    now = utc_now_iso()
    for definition in (*_BUILTIN_DEFINITIONS, *_F1_DEFINITIONS):
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


# --- one-shot spread-filter migration (TODO step 7.7, mapping section 1) -----

SPREAD_FILTER_MIGRATION_STATE_KEY = "technique_builtin_spread_filter_migration_v1"
_SPREAD_FILTER_CONDITION: dict[str, Any] = {"field": "spread_pct", "op": "<=", "value": 0.5}


def apply_builtin_spread_filter_migration(
    repository: TechniqueRepository,
    state_store: Any,
    event_store: Any | None = None,
) -> dict[str, Any]:
    """Add ``spread_pct <= 0.5`` to every builtin rule that lacks it.

    Explicit application migration, run at startup: the seed is INSERT OR
    IGNORE, so changing the seed code would never touch rows already in base.
    Each rewritten rule bumps ``revision`` and leaves a TECHNIQUE_REVISION
    trace with before/after (skills.md 30bis). One-shot: a bot_state marker
    prevents replays. This is NOT learning - it runs regardless of the
    ``learning.enabled`` kill-switch, but stays fully audited.

    ``state_store`` is any object with get_bot_state/set_bot_state
    (TradingRepository); ``event_store`` records the decision traces.
    """
    state = state_store.get_bot_state(SPREAD_FILTER_MIGRATION_STATE_KEY, {})
    if isinstance(state, dict) and state.get("applied"):
        return {"applied": False, "reason": "already_applied", "migrated": []}
    now = utc_now_iso()
    migrated: list[str] = []
    for definition in _BUILTIN_DEFINITIONS:
        technique_id = str(definition["technique_id"])
        row = repository.get(technique_id)
        if row is None:
            continue
        try:
            rule = json.loads(str(row.get("rule_json") or ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(rule, dict) or _mentions_field(rule, "spread_pct"):
            continue
        rule_after = _with_spread_filter(rule)
        revision_from = int(row.get("revision") or 1)
        repository.update_fields(
            technique_id,
            {"rule_json": json.dumps(rule_after), "updated_at": now},
        )
        revision_to = repository.bump_revision(technique_id, updated_at=now)
        if revision_to is None:
            revision_to = revision_from + 1
        migrated.append(technique_id)
        if event_store is not None:
            with suppress(Exception):
                event_store.record_decision_trace(
                    decision_type="TECHNIQUE_REVISION",
                    final_decision=f"REVISION_{revision_from}_TO_{revision_to}",
                    trace={
                        "technique_id": technique_id,
                        "revision_from": revision_from,
                        "revision_to": revision_to,
                        "rule_before": rule,
                        "rule_after": rule_after,
                        "migration": SPREAD_FILTER_MIGRATION_STATE_KEY,
                    },
                )
    state_store.set_bot_state(
        SPREAD_FILTER_MIGRATION_STATE_KEY,
        {"applied": True, "applied_at": now, "migrated": migrated},
    )
    return {"applied": True, "migrated": migrated}


def _with_spread_filter(rule: dict[str, Any]) -> dict[str, Any]:
    """Wrap a rule in ``all(<rule>, spread_pct <= 0.5)``, keeping opportunity_type.

    A rule that already has a top-level ``all`` gets the condition appended
    instead of an extra nesting level.
    """
    opportunity_type = rule.get("opportunity_type")
    body = {key: value for key, value in rule.items() if key != "opportunity_type"}
    if isinstance(body.get("all"), list):
        wrapped: dict[str, Any] = {"all": [*body["all"], dict(_SPREAD_FILTER_CONDITION)]}
    else:
        wrapped = {"all": [body, dict(_SPREAD_FILTER_CONDITION)]}
    if opportunity_type is not None:
        wrapped["opportunity_type"] = opportunity_type
    return wrapped


def _mentions_field(node: Any, field: str) -> bool:
    if isinstance(node, dict):
        if node.get("field") == field:
            return True
        return any(_mentions_field(value, field) for value in node.values())
    if isinstance(node, list):
        return any(_mentions_field(item, field) for item in node)
    return False
