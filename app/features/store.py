from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from app.models import utc_now_iso

# Single source of truth for ATR-as-percent-of-price (skills.md 7.1): the
# scanner snapshot and the feature store must never disagree on this number.
from app.opportunity_scanner.feature_math import atr_pct as _atr_pct
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class FeatureStore:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}

    def snapshot_symbol(self, symbol: str, *, timeframe: str = "15m") -> dict[str, Any]:
        normalized = symbol.upper()
        quote = self._latest_quote(normalized)
        features = self._features_from_quote(quote)
        snapshot = {
            "snapshot_id": new_id("feat"),
            "symbol": normalized,
            "timeframe": timeframe,
            "features": features,
            "created_at": utc_now_iso(),
        }
        self.repository.add_feature_snapshot(snapshot)
        return snapshot

    def latest(self, symbol: str, *, timeframe: str | None = None) -> dict[str, Any]:
        normalized = symbol.upper()
        selected_timeframe = timeframe or "15m"
        snapshot = self.repository.latest_feature_snapshot(normalized, timeframe=selected_timeframe)
        if (
            snapshot is None
            or self._is_expired(snapshot, selected_timeframe)
            or self._is_invalidated(snapshot, normalized, selected_timeframe)
        ):
            return self.snapshot_symbol(normalized, timeframe=selected_timeframe)
        return snapshot

    def ingest_tick(self, snapshot: Any, *, timeframe: str | None = None) -> dict[str, Any]:
        payload = _snapshot_payload(snapshot)
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        selected_timeframe = str(timeframe or payload.get("timeframe") or "15m")
        features = self._features_from_quote(payload)
        result = {
            "snapshot_id": new_id("feat"),
            "symbol": symbol,
            "timeframe": selected_timeframe,
            "features": {
                **features,
                "source": payload.get("source") or payload.get("market_data_source") or "tick",
            },
            "created_at": utc_now_iso(),
        }
        self.repository.set_bot_state(
            f"feature_store_latest_tick_{symbol}",
            {
                "symbol": symbol,
                "timeframe": selected_timeframe,
                "features": result["features"],
                "snapshot": payload,
                "updated_at": result["created_at"],
            },
        )
        if self.persist_snapshots:
            self.repository.add_feature_snapshot(result)
        return result

    def enrich_historical(
        self,
        symbol: str,
        bars: list[dict[str, Any]],
        *,
        timeframe: str = "1d",
    ) -> dict[str, Any]:
        normalized = symbol.upper()
        features = self._features_from_bars(bars)
        snapshot = {
            "snapshot_id": new_id("feat"),
            "symbol": normalized,
            "timeframe": timeframe,
            "features": {
                **features,
                "source": "historical_enrichment",
                "bars": len(bars),
            },
            "created_at": utc_now_iso(),
        }
        if self.persist_snapshots:
            self.repository.add_feature_snapshot(snapshot)
        return snapshot

    def invalidate(
        self,
        symbol: str | None = None,
        *,
        timeframe: str | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        key = self._invalidation_key(symbol.upper() if symbol else "*", timeframe or "*")
        payload = {
            "symbol": symbol.upper() if symbol else None,
            "timeframe": timeframe,
            "reason": reason,
            "invalidated_at": utc_now_iso(),
        }
        self.repository.set_bot_state(key, payload)
        return payload

    def _latest_quote(self, symbol: str) -> dict[str, Any]:
        state = self.repository.get_bot_state(f"feature_store_latest_tick_{symbol}", {})
        snapshot = state.get("snapshot") if isinstance(state, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
        events = self.repository.list_events(symbol=symbol, event_type="stock_quote", limit=1)
        if not events:
            return {}
        data = events[0].get("data")
        return data if isinstance(data, dict) else {}

    @property
    def persist_snapshots(self) -> bool:
        config = self.settings.get("features", {})
        return bool(config.get("persist_snapshots", True)) if isinstance(config, dict) else True

    def _is_expired(self, snapshot: dict[str, Any], timeframe: str) -> bool:
        created_at = snapshot.get("created_at")
        age = _age_seconds(created_at)
        if age is None:
            return True
        return age > self._ttl_seconds(timeframe)

    def _ttl_seconds(self, timeframe: str) -> float:
        config = self.settings.get("features", {})
        max_age = config.get("max_age_seconds", {}) if isinstance(config, dict) else {}
        if not isinstance(max_age, dict):
            max_age = {}
        key = {
            "1m": "candle_1m",
            "5m": "candle_5m",
            "15m": "candle_15m",
            "1d": "daily",
        }.get(str(timeframe), str(timeframe))
        return float(max_age.get(key, max_age.get("tick_features", 20)) or 20)

    def _is_invalidated(self, snapshot: dict[str, Any], symbol: str, timeframe: str) -> bool:
        created_at = snapshot.get("created_at")
        invalidations = [
            self.repository.get_bot_state(self._invalidation_key(symbol, timeframe), {}),
            self.repository.get_bot_state(self._invalidation_key(symbol, "*"), {}),
            self.repository.get_bot_state(self._invalidation_key("*", "*"), {}),
        ]
        for invalidation in invalidations:
            invalidated_at = (
                invalidation.get("invalidated_at") if isinstance(invalidation, dict) else None
            )
            if invalidated_at and _compare_iso(invalidated_at, created_at) >= 0:
                return True
        return False

    @staticmethod
    def _invalidation_key(symbol: str, timeframe: str) -> str:
        return f"feature_store_invalidated_{symbol}_{timeframe}"

    @staticmethod
    def _features_from_quote(quote: dict[str, Any]) -> dict[str, Any]:
        bid = _number(quote.get("bid"))
        ask = _number(quote.get("ask"))
        spread_pct = None
        if bid is not None and ask is not None and bid <= ask:
            mid = (bid + ask) / 2
            spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else None
        features = {
            "price": _number(quote.get("price") or quote.get("last") or quote.get("close")),
            "open": _number(quote.get("open")),
            "high": _number(quote.get("high")),
            "low": _number(quote.get("low")),
            "close": _number(quote.get("close")),
            "bid": bid,
            "ask": ask,
            "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
            "volume": _number(quote.get("volume")),
            "volume_ratio": _number(quote.get("volume_ratio") or quote.get("volume_ratio_15m")),
            "volume_ratio_closed_bar": _number(quote.get("volume_ratio_closed_bar")),
            "relative_volume_live": _number(quote.get("volume_ratio_live")),
            "atr_15m": _number(quote.get("atr_15m")),
            "atr_1h": _number(quote.get("atr_1h")),
            "atr_pct": _atr_pct(quote),
            "ema_20": _number(quote.get("ema_20")),
            "ema_50": _number(quote.get("ema_50")),
            "previous_high": _number(quote.get("previous_high")),
            "support_level": _number(quote.get("support_level")),
            "range_pct": _range_pct(quote),
            "closed_candle": bool(quote.get("candle_closed", quote.get("bar_closed", False))),
            "source": quote.get("source") or quote.get("market_data_source") or "UNKNOWN",
            "timestamp": quote.get("timestamp") or quote.get("event_timestamp"),
        }
        bars = quote.get("historical_bars")
        if isinstance(bars, list) and bars:
            features.update(FeatureStore._features_from_bars(bars))
        return features

    @staticmethod
    def _features_from_bars(bars: list[dict[str, Any]]) -> dict[str, Any]:
        closes = [_number(bar.get("close")) for bar in bars if isinstance(bar, dict)]
        closes = [value for value in closes if value is not None and value > 0]
        volumes = [_number(bar.get("volume")) for bar in bars if isinstance(bar, dict)]
        volumes = [value for value in volumes if value is not None and value >= 0]
        highs = [_number(bar.get("high")) for bar in bars if isinstance(bar, dict)]
        highs = [value for value in highs if value is not None]
        lows = [_number(bar.get("low")) for bar in bars if isinstance(bar, dict)]
        lows = [value for value in lows if value is not None]
        returns = [
            (current - previous) / previous
            for previous, current in zip(closes, closes[1:])
            if previous
        ]
        return {
            "historical_close": closes[-1] if closes else None,
            "return_1_bar_pct": round(returns[-1] * 100, 4) if returns else None,
            "return_5_bar_pct": _window_return(closes, 5),
            "return_20_bar_pct": _window_return(closes, 20),
            "realized_volatility_20": _stddev(returns[-20:]) if returns else None,
            "historical_ema_20": _ema(closes, 20),
            "historical_ema_50": _ema(closes, 50),
            "historical_sma_50": _sma(closes, 50),
            "average_volume_20": (
                round(sum(volumes[-20:]) / len(volumes[-20:]), 4) if volumes else None
            ),
            "relative_volume_20": (
                round(volumes[-1] / (sum(volumes[-20:]) / len(volumes[-20:])), 4)
                if len(volumes) >= 2 and sum(volumes[-20:]) > 0
                else None
            ),
            "high_20": max(highs[-20:]) if highs else None,
            "low_20": min(lows[-20:]) if lows else None,
        }


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if is_dataclass(snapshot):
        return asdict(snapshot)
    return {
        key: getattr(snapshot, key)
        for key in dir(snapshot)
        if not key.startswith("_") and not callable(getattr(snapshot, key))
    }


def _range_pct(quote: dict[str, Any]) -> float | None:
    high = _number(quote.get("high"))
    low = _number(quote.get("low"))
    close = _number(quote.get("close") or quote.get("price") or quote.get("last"))
    if high is None or low is None or close is None or close <= 0:
        return None
    return round(((high - low) / close) * 100, 4)


def _window_return(closes: list[float], window: int) -> float | None:
    if len(closes) <= window or closes[-window - 1] <= 0:
        return None
    return round(((closes[-1] - closes[-window - 1]) / closes[-window - 1]) * 100, 4)


def _sma(values: list[float], period: int) -> float | None:
    # Simple average of the last `period` closes; None below `period` bars
    # (skills.md 4.1 — a 50-day SMA on 30 bars is not an SMA 50).
    if len(values) < period:
        return None
    window = values[-period:]
    return round(sum(window) / period, 4)


def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    alpha = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * alpha + ema * (1 - alpha)
    return round(ema, 4)


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return round(variance**0.5, 6)


def _age_seconds(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()


def _compare_iso(left: Any, right: Any) -> int:
    left_age = _timestamp(left)
    right_age = _timestamp(right)
    if left_age is None:
        return -1
    if right_age is None:
        return 1
    return (left_age > right_age) - (left_age < right_age)


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
