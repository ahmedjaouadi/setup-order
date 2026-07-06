from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from datetime import UTC
from typing import Any

from app.models import utc_now_iso
from app.opportunity_scanner.context_tags import build_context_tags
from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.utils.id_generator import new_id
from app.utils.market_hours import (
    REGULAR_MARKET_CLOSE,
    US_EQUITY_TIMEZONE,
    coerce_datetime,
    next_trading_day,
)

# Trading-day horizons tracked per detection.
HORIZONS: dict[str, int] = {"1d": 1, "3d": 3}
FALLBACK_R_UNIT_PCT = 2.0
DEFAULT_MIN_SAMPLES = 30

# A bar returned by the history provider: chronological high/low/close.
Bar = dict[str, Any]
BarsProvider = Callable[[str, str, str], list[Bar]]

_PRICE_KEYS = ("price", "last", "close", "price_at_detection")
_ATR_KEYS = ("atr", "atr_15m", "atr_1h")


class OutcomeTracker:
    """Records detections as pending outcomes and evaluates them at horizon.

    The metric maths (triple barrier, MFE/MAE, aggregation) are pure module
    functions so they can be unit-tested on synthetic series without a broker
    or a database.
    """

    def __init__(
        self,
        repository: OutcomeRepository,
        *,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ) -> None:
        self.repository = repository
        self.min_samples = min_samples

    def record_detection(
        self,
        technique_id: str,
        symbol: str,
        snapshot: dict[str, Any],
        *,
        now: str | None = None,
        opportunity_id: str | None = None,
    ) -> list[str]:
        """Create one PENDING outcome per horizon. Returns the ids created."""
        detected_at = now or utc_now_iso()
        price = _first_number(snapshot, _PRICE_KEYS)
        if price is None or price <= 0:
            return []
        r_unit_pct, atr_fallback = _resolve_r_unit(snapshot, price)
        # Context tags (skills.md 32.2bis) travel inside the stored snapshot so
        # the learning engine can slice outcomes by time bucket, rvol, spread,
        # etc. without re-collecting.
        features_snapshot = {
            **snapshot,
            "context_tags": build_context_tags(snapshot, detected_at),
        }
        created: list[str] = []
        for horizon, sessions in HORIZONS.items():
            due = _due_at(detected_at, sessions)
            if self.repository.pending_exists(technique_id, symbol, horizon, due[:10]):
                continue
            outcome_id = new_id("dco")
            self.repository.create_outcome(
                {
                    "outcome_id": outcome_id,
                    "technique_id": technique_id,
                    "symbol": symbol,
                    "detected_at": detected_at,
                    "price_at_detection": price,
                    "features_snapshot": features_snapshot,
                    "r_unit_pct": r_unit_pct,
                    "horizon": horizon,
                    "evaluation_due_at": due,
                    "status": "PENDING",
                    "payload": {"atr_fallback_used": atr_fallback},
                    "created_at": detected_at,
                    "opportunity_id": opportunity_id,
                }
            )
            created.append(outcome_id)
        return created

    def record_matches(
        self,
        technique_ids: Iterable[str],
        symbol: str,
        snapshot: dict[str, Any],
        *,
        now: str | None = None,
        opportunity_id: str | None = None,
    ) -> list[str]:
        created: list[str] = []
        for technique_id in dict.fromkeys(technique_ids):
            created.extend(
                self.record_detection(
                    technique_id,
                    symbol,
                    snapshot,
                    now=now,
                    opportunity_id=opportunity_id,
                )
            )
        return created

    def evaluate_due(
        self,
        bars_provider: BarsProvider,
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        evaluated = 0
        expired = 0
        as_of = now or utc_now_iso()
        for row in self.repository.due_outcomes(as_of):
            entry = _number(row.get("price_at_detection"))
            r_unit = _number(row.get("r_unit_pct"))
            bars = _clean_bars(
                bars_provider(
                    str(row["symbol"]), str(row["detected_at"]), str(row["evaluation_due_at"])
                )
            )
            if entry is None or entry <= 0 or r_unit is None or not bars:
                # No usable data at horizon: expire rather than crash the job.
                self.repository.update_outcome(
                    row["outcome_id"],
                    {
                        **row,
                        "status": "EXPIRED",
                        "payload": {**row.get("payload", {}), "expired_reason": "no_data"},
                    },
                )
                expired += 1
                continue
            metrics = evaluate_window(entry, r_unit, bars)
            self.repository.update_outcome(
                row["outcome_id"],
                {
                    **row,
                    **metrics,
                    "status": "EVALUATED",
                    "payload": {
                        **row.get("payload", {}),
                        "granularity": _granularity(bars),
                        "evaluated_at": as_of,
                    },
                },
            )
            evaluated += 1
        return {"evaluated": evaluated, "expired": expired}

    def technique_stats(self) -> dict[str, dict[str, Any]]:
        grouped = self.repository.evaluated_grouped_by_technique()
        return {
            technique_id: aggregate_stats(rows, self.min_samples)
            for technique_id, rows in grouped.items()
        }

    def reliability_summary(self) -> dict[str, Any]:
        """Correct/wrong/pending counters per technique and global (etape 13.2).

        Counters come from one SQL aggregation; hit_rate/expectancy reuse
        ``aggregate_stats`` on the evaluated rows — nothing is recomputed in
        SQL that the module already computes.
        """
        counts = self.repository.counts_by_technique()
        stats = self.technique_stats()
        techniques: list[dict[str, Any]] = []
        totals = {
            "detections_total": 0,
            "pending": 0,
            "evaluated": 0,
            "expired": 0,
            "correct": 0,
            "wrong": 0,
            "indeterminate": 0,
        }
        for technique_id in sorted(counts):
            technique_counts = counts[technique_id]
            for key in totals:
                totals[key] += technique_counts[key]
            technique_stats = stats.get(technique_id) or {}
            sample_size = int(technique_stats.get("sample_size") or 0)
            techniques.append(
                {
                    "technique_id": technique_id,
                    **technique_counts,
                    "hit_rate": technique_stats.get("hit_rate"),
                    "expectancy_r": technique_stats.get("expectancy_r"),
                    "sample_size": sample_size,
                    "min_samples_reached": sample_size >= self.min_samples,
                    "status_label": technique_stats.get("status_label") or "WARMUP",
                }
            )
        labeled_total = totals["correct"] + totals["wrong"]
        global_hit_rate = round(totals["correct"] / labeled_total, 4) if labeled_total else None
        return {
            "global": {**totals, "hit_rate": global_hit_rate},
            "techniques": techniques,
            "min_samples": self.min_samples,
        }


# --- pure metric helpers -------------------------------------------------


def compute_r_unit_pct(price: float | None, atr: float | None) -> float | None:
    if price is None or price <= 0 or atr is None or atr <= 0:
        return None
    return round(atr / price * 100, 4)


def evaluate_window(entry: float, r_unit_pct: float, bars: list[Bar]) -> dict[str, Any]:
    """Triple-barrier evaluation over the detection window.

    label_1r: 1 if +1R (entry * (1 + r)) is reached before -1R, 0 if -1R is
    reached first, None if neither barrier is touched. When both barriers fall
    inside the same (daily) bar the order is unknown, so we take the
    conservative view that the adverse barrier came first (label 0).
    """
    closes = [bar["close"] for bar in bars]
    price_at_horizon = closes[-1]
    forward_return_pct = round((price_at_horizon - entry) / entry * 100, 4)
    mfe_pct = round(max((bar["high"] - entry) / entry * 100 for bar in bars), 4)
    mae_pct = round(min((bar["low"] - entry) / entry * 100 for bar in bars), 4)
    label = _label_1r(entry, r_unit_pct, bars)
    return {
        "price_at_horizon": round(price_at_horizon, 6),
        "forward_return_pct": forward_return_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "label_1r": label,
    }


def _label_1r(entry: float, r_unit_pct: float, bars: list[Bar]) -> int | None:
    if r_unit_pct <= 0:
        return None
    up = entry * (1 + r_unit_pct / 100)
    down = entry * (1 - r_unit_pct / 100)
    for bar in bars:
        hi_hit = bar["high"] >= up
        lo_hit = bar["low"] <= down
        if hi_hit and lo_hit:
            return 0
        if hi_hit:
            return 1
        if lo_hit:
            return 0
    return None


def aggregate_stats(rows: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    sample_size = len(rows)
    labeled = [int(row["label_1r"]) for row in rows if row.get("label_1r") in (0, 1)]
    forwards = _numbers(row.get("forward_return_pct") for row in rows)
    mfes = _numbers(row.get("mfe_pct") for row in rows)
    maes = [abs(value) for value in _numbers(row.get("mae_pct") for row in rows)]
    winners = _numbers(row.get("mfe_pct") for row in rows if row.get("label_1r") == 1)
    losers = [
        abs(value)
        for value in _numbers(row.get("mae_pct") for row in rows if row.get("label_1r") == 0)
    ]
    hit_rate = round(sum(labeled) / len(labeled), 4) if labeled else None
    expectancy_r = _expectancy_r(hit_rate, winners, losers)
    return {
        "sample_size": sample_size,
        "hit_rate": hit_rate,
        "avg_forward_return_pct": _round_mean(forwards),
        "median_forward_return_pct": round(statistics.median(forwards), 4) if forwards else None,
        "avg_mfe_pct": _round_mean(mfes),
        "avg_mae_pct": _round_mean(maes),
        "expectancy_r": expectancy_r,
        "status_label": "WARMUP" if sample_size < min_samples else "READY",
    }


def _expectancy_r(
    hit_rate: float | None, winners: list[float], losers: list[float]
) -> float | None:
    if hit_rate is None:
        return None
    avg_win = statistics.mean(winners) if winners else 0.0
    avg_loss = statistics.mean(losers) if losers else 0.0
    return round(hit_rate * avg_win - (1 - hit_rate) * avg_loss, 4)


def _resolve_r_unit(snapshot: dict[str, Any], price: float) -> tuple[float, bool]:
    atr = _first_number(snapshot, _ATR_KEYS)
    computed = compute_r_unit_pct(price, atr)
    if computed is not None:
        return computed, False
    return FALLBACK_R_UNIT_PCT, True


def _due_at(detected_at: str, sessions: int) -> str:
    reference = coerce_datetime(detected_at)
    if reference is None:
        reference = coerce_datetime(utc_now_iso())
    assert reference is not None
    target = next_trading_day(reference, sessions)
    close = target.astimezone(US_EQUITY_TIMEZONE).replace(
        hour=REGULAR_MARKET_CLOSE.hour,
        minute=REGULAR_MARKET_CLOSE.minute,
        second=0,
        microsecond=0,
    )
    return close.astimezone(UTC).isoformat()


def _clean_bars(bars: Any) -> list[Bar]:
    if not isinstance(bars, list):
        return []
    clean: list[Bar] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        high = _number(bar.get("high"))
        low = _number(bar.get("low"))
        close = _number(bar.get("close", bar.get("price")))
        if high is None or low is None or close is None:
            continue
        clean.append({"high": high, "low": low, "close": close})
    return clean


def _granularity(bars: list[Bar]) -> str:
    return "intraday" if len(bars) > 3 else "daily"


def _first_number(snapshot: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(snapshot.get(key))
        if value is not None:
            return value
    return None


def _numbers(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        number = _number(value)
        if number is not None:
            result.append(number)
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None
