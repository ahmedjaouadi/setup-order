from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from app.models import utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class DataQualityService:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}

    def evaluate_symbol(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.upper()
        snapshot = self._latest_quote(normalized)
        return self.evaluate_snapshot(normalized, snapshot, persist=True, source="latest")

    def evaluate_snapshot(
        self,
        symbol: str,
        snapshot: dict[str, Any],
        *,
        persist: bool = True,
        source: str = "snapshot",
    ) -> dict[str, Any]:
        normalized = symbol.upper()
        issues = self._issues(normalized, snapshot)
        status = self._status_for_issues(issues)
        for issue in issues:
            if persist:
                self._persist_issue(normalized, snapshot, issue, source=source)
        if persist:
            self.repository.set_bot_state(
                f"data_quality_latest_{normalized}",
                {
                    "symbol": normalized,
                    "status": status,
                    "issues": issues,
                    "snapshot": snapshot,
                    "source": source,
                    "updated_at": utc_now_iso(),
                },
            )
        return {
            "symbol": normalized,
            "status": status,
            "issues": issues,
            "snapshot": snapshot,
            "events": self.repository.list_data_quality_events(symbol=normalized, limit=25),
        }

    def events(self, *, symbol: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"items": self.repository.list_data_quality_events(symbol=symbol, limit=limit)}

    def record_tick(self, snapshot: Any) -> dict[str, Any]:
        payload = _snapshot_payload(snapshot)
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            return {"status": "INVALID_INPUT", "issues": [{"type": "missing_symbol"}]}
        return self.evaluate_snapshot(symbol, payload, persist=True, source="tick")

    def record_candle(
        self,
        symbol: str,
        timeframe: str,
        candle: dict[str, Any],
        *,
        closed: bool,
    ) -> dict[str, Any]:
        payload = {
            **(candle if isinstance(candle, dict) else {}),
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "candle_closed": bool(closed),
            "timestamp": (
                candle.get("timestamp")
                if isinstance(candle, dict)
                else None
            )
            or utc_now_iso(),
        }
        issues = []
        if self.require_closed_candle and not closed:
            issues.append(
                {
                    "type": "candle_not_closed",
                    "severity": "ERROR",
                    "message": f"{timeframe} candle is not closed.",
                }
            )
        issues.extend(self._issues(symbol.upper(), payload))
        status = self._status_for_issues(issues)
        for issue in issues:
            self._persist_issue(symbol.upper(), payload, issue, source="candle")
        self.repository.set_bot_state(
            f"data_quality_latest_candle_{symbol.upper()}_{timeframe}",
            {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "status": status,
                "issues": issues,
                "snapshot": payload,
                "updated_at": utc_now_iso(),
            },
        )
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": status,
            "issues": issues,
            "snapshot": payload,
        }

    @property
    def max_last_tick_age_seconds(self) -> float:
        return float(self._config().get("max_last_tick_age_seconds", 20))

    @property
    def max_spread_pct_default(self) -> float:
        return float(self._config().get("max_spread_pct_default", 0.35))

    @property
    def block_entry_on_unknown(self) -> bool:
        return bool(self._config().get("block_entry_on_unknown_quality", True))

    @property
    def require_closed_candle(self) -> bool:
        return bool(self._config().get("require_closed_candle_for_close_conditions", True))

    def _issues(self, symbol: str, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not snapshot:
            return [
                {
                    "type": "missing_market_data",
                    "severity": "ERROR",
                    "message": f"No market data snapshot is available for {symbol}",
                }
            ]
        issues: list[dict[str, Any]] = []
        price = _number(snapshot.get("price") or snapshot.get("last") or snapshot.get("close"))
        if price is not None and price <= 0:
            issues.append(
                {
                    "type": "bad_price",
                    "severity": "ERROR",
                    "message": "Last price is not positive.",
                }
            )
        bid = _number(snapshot.get("bid"))
        ask = _number(snapshot.get("ask"))
        if bid is None or ask is None:
            issues.append(
                {
                    "type": "missing_bid_ask",
                    "severity": "ERROR",
                    "message": "Bid/ask is missing.",
                }
            )
        elif self.reject_bid_greater_than_ask and bid > ask:
            issues.append(
                {
                    "type": "bid_greater_than_ask",
                    "severity": "ERROR",
                    "message": "Bid price is greater than ask price.",
                }
            )
        else:
            mid = (bid + ask) / 2
            spread_pct = ((ask - bid) / mid) * 100 if mid > 0 else None
            if spread_pct is not None and spread_pct > self.max_spread_pct_default:
                issues.append(
                    {
                        "type": "spread_too_wide",
                        "severity": "WARNING",
                        "message": (
                            f"Spread {spread_pct:.2f}% exceeds "
                            f"{self.max_spread_pct_default:.2f}%."
                        ),
                        "spread_pct": round(spread_pct, 4),
                    }
                )
        session = str(snapshot.get("session") or "").upper()
        if session in {"HALTED", "SUSPENDED"}:
            issues.append(
                {
                    "type": "market_halt",
                    "severity": "ERROR",
                    "message": f"Market session is {session}.",
                }
            )
        readiness = snapshot.get("market_data_readiness")
        if isinstance(readiness, dict) and readiness.get("status") in {"ERROR", "BLOCKED"}:
            issues.append(
                {
                    "type": "market_data_not_ready",
                    "severity": "ERROR",
                    "message": str(readiness.get("label") or "Market data readiness failed."),
                    "readiness": readiness,
                }
            )
        timestamp = (
            snapshot.get("timestamp")
            or snapshot.get("event_timestamp")
            or snapshot.get("last_update")
        )
        age = _age_seconds(timestamp)
        if age is None:
            issues.append(
                {
                    "type": "unknown_snapshot_age",
                    "severity": "WARNING",
                    "message": "Market data timestamp is missing or invalid.",
                }
            )
        elif age > self.max_last_tick_age_seconds:
            issues.append(
                {
                    "type": "stale_market_data",
                    "severity": "ERROR",
                    "message": f"Market data is stale: {age:.1f}s old.",
                    "age_seconds": round(age, 2),
                }
            )
        return issues

    def _latest_quote(self, symbol: str) -> dict[str, Any]:
        state = self.repository.get_bot_state(f"data_quality_latest_{symbol}", {})
        snapshot = state.get("snapshot") if isinstance(state, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
        events = self.repository.list_events(symbol=symbol, event_type="stock_quote", limit=1)
        if not events:
            return {}
        data = events[0].get("data")
        return data if isinstance(data, dict) else {}

    def _status_for_issues(self, issues: list[dict[str, Any]]) -> str:
        if not issues:
            return "OK"
        has_error = any(str(issue.get("severity")) == "ERROR" for issue in issues)
        if has_error or self.block_entry_on_unknown:
            return "BLOCKED"
        return "WARNING"

    def _persist_issue(
        self,
        symbol: str,
        snapshot: dict[str, Any],
        issue: dict[str, Any],
        *,
        source: str,
    ) -> None:
        signature = f"{issue.get('type')}:{issue.get('severity')}:{issue.get('message')}"
        state_key = f"data_quality_issue_signature_{symbol}_{issue.get('type')}"
        previous = self.repository.get_bot_state(state_key, {})
        if previous.get("signature") == signature:
            return
        self.repository.set_bot_state(state_key, {"signature": signature, "updated_at": utc_now_iso()})
        self.repository.add_data_quality_event(
            {
                "event_id": new_id("dq"),
                "symbol": symbol,
                "severity": issue["severity"],
                "event_type": issue["type"],
                "message": issue["message"],
                "payload": {"snapshot": snapshot, "source": source, **issue},
                "created_at": utc_now_iso(),
            }
        )

    def _config(self) -> dict[str, Any]:
        config = self.settings.get("data_quality", {})
        return config if isinstance(config, dict) else {}

    @property
    def reject_bid_greater_than_ask(self) -> bool:
        return bool(self._config().get("reject_bid_greater_than_ask", True))


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _age_seconds(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if is_dataclass(snapshot):
        return asdict(snapshot)
    payload = {
        key: getattr(snapshot, key)
        for key in dir(snapshot)
        if not key.startswith("_") and not callable(getattr(snapshot, key))
    }
    return payload
