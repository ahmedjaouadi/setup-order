from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.models import utc_now_iso
from app.settings import load_yaml_file
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class PortfolioRiskService:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}

    def analyze(self, *, persist: bool = True) -> dict[str, Any]:
        positions = self.repository.list_positions()
        setups = {setup["setup_id"]: setup for setup in self.repository.list_setups()}
        symbol_exposure: dict[str, float] = {}
        sector_exposure: dict[str, float] = {}
        setup_by_symbol: dict[str, dict[str, Any]] = {}
        total = 0.0
        metadata = self._metadata()
        for position in positions:
            symbol = str(position.get("symbol") or "").upper()
            exposure = abs(float(position.get("quantity") or 0)) * float(
                position.get("current_price") or position.get("average_price") or 0
            )
            symbol_exposure[symbol] = round(exposure, 2)
            total += exposure
            setup = setups.get(str(position.get("setup_id") or ""), {})
            setup_by_symbol[symbol] = setup
            sector = _sector_for_symbol(symbol, setup, metadata)
            sector_exposure[sector] = round(sector_exposure.get(sector, 0.0) + exposure, 2)
        correlation = self._correlation_matrix(sorted(symbol_exposure))
        size_reductions = self._size_reductions(
            total_exposure=total,
            symbol_exposure=symbol_exposure,
            sector_exposure=sector_exposure,
            correlation=correlation,
        )
        status, warnings = self._risk_status(total, sector_exposure, symbol_exposure, correlation)
        snapshot = {
            "snapshot_id": new_id("port"),
            "total_exposure_usd": round(total, 2),
            "open_positions_count": len(positions),
            "sector_exposure": sector_exposure,
            "symbol_exposure": symbol_exposure,
            "sector_concentration": _percentages(sector_exposure, total),
            "symbol_concentration": _percentages(symbol_exposure, total),
            "correlation": correlation,
            "size_reductions": size_reductions,
            "risk_status": status,
            "warnings": warnings,
            "created_at": utc_now_iso(),
        }
        if persist:
            self.repository.add_portfolio_snapshot(snapshot)
        return snapshot

    def latest(self) -> dict[str, Any]:
        return self.repository.latest_portfolio_snapshot() or self.analyze()

    def _risk_status(
        self,
        total_exposure: float,
        sector_exposure: dict[str, float],
        symbol_exposure: dict[str, float],
        correlation: dict[str, Any],
    ) -> tuple[str, list[str]]:
        config = self._config()
        warnings = []
        max_total = float(config.get("max_total_exposure_usd", self.settings.get("risk", {}).get("max_total_exposure_usd", 1000)))
        if total_exposure > max_total:
            warnings.append(f"Total exposure {total_exposure:.2f} exceeds {max_total:.2f}.")
        max_sector_pct = float(config.get("max_sector_exposure_pct", 40))
        max_symbol_pct = float(config.get("max_single_symbol_exposure_pct", 20))
        if total_exposure > 0:
            for sector, exposure in sector_exposure.items():
                pct = (exposure / total_exposure) * 100
                if pct > max_sector_pct:
                    warnings.append(f"Sector {sector} exposure is {pct:.1f}%.")
            for symbol, exposure in symbol_exposure.items():
                pct = (exposure / total_exposure) * 100
                if pct > max_symbol_pct:
                    warnings.append(f"Symbol {symbol} exposure is {pct:.1f}%.")
        corr_summary = correlation.get("summary") if isinstance(correlation, dict) else {}
        if isinstance(corr_summary, dict) and corr_summary.get("high_correlation_pairs"):
            warnings.append(
                f"High correlation pairs: {len(corr_summary['high_correlation_pairs'])}."
            )
        if isinstance(corr_summary, dict):
            max_cluster = int(corr_summary.get("max_correlated_positions") or 0)
            allowed = int(config.get("max_correlated_positions", 3) or 3)
            if max_cluster > allowed:
                warnings.append(
                    f"Correlated position cluster has {max_cluster} symbols; limit is {allowed}."
                )
        if any("exceeds" in item for item in warnings):
            return "BLOCK_NEW_ENTRIES", warnings
        if warnings:
            return "REDUCE_SIZE", warnings
        return "OK", warnings

    def _config(self) -> dict[str, Any]:
        config = self.settings.get("portfolio_risk", {})
        return config if isinstance(config, dict) else {}

    def _correlation_matrix(self, symbols: list[str]) -> dict[str, Any]:
        histories = {
            symbol: self._returns_for_symbol(symbol)
            for symbol in symbols
        }
        matrix: dict[str, dict[str, float | None]] = {}
        high_pairs = []
        threshold = float(self._config().get("high_correlation_threshold", 0.75))
        for left in symbols:
            matrix[left] = {}
            for right in symbols:
                if left == right:
                    matrix[left][right] = 1.0
                    continue
                corr = _correlation(histories.get(left, []), histories.get(right, []))
                matrix[left][right] = round(corr, 4) if corr is not None else None
                if left < right and corr is not None and corr >= threshold:
                    high_pairs.append({"symbols": [left, right], "correlation": round(corr, 4)})
        correlated_symbols = {
            symbol
            for pair in high_pairs
            for symbol in pair["symbols"]
        }
        return {
            "method": "pearson_returns_from_local_history",
            "symbols": symbols,
            "matrix": matrix,
            "summary": {
                "high_correlation_threshold": threshold,
                "high_correlation_pairs": high_pairs,
                "max_correlated_positions": len(correlated_symbols) if high_pairs else 0,
                "history_points": {
                    symbol: len(histories.get(symbol, []))
                    for symbol in symbols
                },
            },
        }

    def _returns_for_symbol(self, symbol: str) -> list[float]:
        closes: list[float] = []
        events = self.repository.list_events(symbol=symbol, event_type="stock_quote", limit=250)
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            bars = data.get("historical_bars")
            if isinstance(bars, list) and bars:
                closes = [
                    value
                    for value in (_number(bar.get("close")) for bar in bars if isinstance(bar, dict))
                    if value is not None and value > 0
                ]
                if closes:
                    break
            close = _number(data.get("close") or data.get("price") or data.get("last"))
            if close is not None and close > 0:
                closes.append(close)
        closes = list(reversed(closes)) if len(closes) > 1 and not _looks_chronological(closes) else closes
        return [
            (current - previous) / previous
            for previous, current in zip(closes, closes[1:])
            if previous
        ]

    def _size_reductions(
        self,
        *,
        total_exposure: float,
        symbol_exposure: dict[str, float],
        sector_exposure: dict[str, float],
        correlation: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._config()
        if not bool(config.get("reduce_size_if_high_correlation", True)):
            return {"enabled": False, "items": {}}
        max_sector_pct = float(config.get("max_sector_exposure_pct", 40))
        max_symbol_pct = float(config.get("max_single_symbol_exposure_pct", 20))
        corr_summary = correlation.get("summary") if isinstance(correlation, dict) else {}
        high_pairs = corr_summary.get("high_correlation_pairs", []) if isinstance(corr_summary, dict) else []
        correlated_symbols = {
            symbol
            for pair in high_pairs
            for symbol in pair.get("symbols", [])
            if isinstance(pair, dict)
        }
        reductions: dict[str, Any] = {}
        for symbol, exposure in symbol_exposure.items():
            multiplier = 1.0
            reasons = []
            if total_exposure > 0:
                symbol_pct = (exposure / total_exposure) * 100
                if symbol_pct > max_symbol_pct:
                    multiplier = min(multiplier, max_symbol_pct / symbol_pct)
                    reasons.append(f"single symbol concentration {symbol_pct:.1f}%")
            if symbol in correlated_symbols:
                multiplier = min(multiplier, 0.5)
                reasons.append("high return correlation with another open position")
            for sector, sector_total in sector_exposure.items():
                if total_exposure <= 0 or sector_total <= 0:
                    continue
                sector_pct = (sector_total / total_exposure) * 100
                if sector_pct > max_sector_pct:
                    multiplier = min(multiplier, max_sector_pct / sector_pct)
                    reasons.append(f"sector concentration {sector} {sector_pct:.1f}%")
            if multiplier < 1.0:
                reductions[symbol] = {
                    "size_multiplier": round(max(0.0, multiplier), 4),
                    "recommended_exposure_usd": round(exposure * max(0.0, multiplier), 2),
                    "reasons": reasons,
                }
        return {"enabled": True, "items": reductions}

    def position_size_adjustment(
        self,
        symbol: str,
        requested_exposure_usd: float,
    ) -> dict[str, Any]:
        snapshot = self.analyze(persist=False)
        reductions = snapshot.get("size_reductions", {}).get("items", {})
        symbol_reduction = reductions.get(symbol.upper())
        if symbol_reduction:
            multiplier = float(symbol_reduction.get("size_multiplier") or 1.0)
            return {
                "symbol": symbol.upper(),
                "requested_exposure_usd": requested_exposure_usd,
                "approved_exposure_usd": round(requested_exposure_usd * multiplier, 2),
                "size_multiplier": multiplier,
                "reasons": symbol_reduction.get("reasons", []),
            }
        return {
            "symbol": symbol.upper(),
            "requested_exposure_usd": requested_exposure_usd,
            "approved_exposure_usd": requested_exposure_usd,
            "size_multiplier": 1.0,
            "reasons": [],
        }

    def _metadata(self) -> dict[str, dict[str, Any]]:
        path = Path("config/symbol_metadata.yaml")
        if not path.exists():
            return {}
        payload = load_yaml_file(path)
        items = payload.get("symbols", [])
        if not isinstance(items, list):
            return {}
        metadata = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if symbol:
                metadata[symbol] = item
        return metadata


def _sector_for_symbol(
    symbol: str,
    setup: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
) -> str:
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    meta = metadata.get(symbol, {})
    return str(meta.get("sector") or config.get("sector") or setup.get("sector") or "Unknown")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentages(values: dict[str, float], total: float) -> dict[str, float]:
    if total <= 0:
        return {key: 0.0 for key in values}
    return {key: round((value / total) * 100, 2) for key, value in values.items()}


def _correlation(left: list[float], right: list[float]) -> float | None:
    length = min(len(left), len(right))
    if length < 3:
        return None
    left_tail = left[-length:]
    right_tail = right[-length:]
    left_avg = sum(left_tail) / length
    right_avg = sum(right_tail) / length
    numerator = sum((a - left_avg) * (b - right_avg) for a, b in zip(left_tail, right_tail))
    left_var = sum((a - left_avg) ** 2 for a in left_tail)
    right_var = sum((b - right_avg) ** 2 for b in right_tail)
    if left_var <= 0 or right_var <= 0:
        return None
    return numerator / math.sqrt(left_var * right_var)


def _looks_chronological(values: list[float]) -> bool:
    # Event-derived close lists are often newest-first; historical bars are usually oldest-first.
    return len(values) < 3 or abs(values[-1] - values[-2]) <= abs(values[0] - values[1])
