from __future__ import annotations

from typing import Any

from app.conversion import canonicalize_setup_config
from app.forecasting.forecast_repository import ForecastRepository
from app.forecasting.forecast_signal_compiler import ForecastSignalCompiler
from app.forecasting.forecast_to_score_mapper import ForecastToScoreMapper
from app.models import utc_now_iso
from app.observability.decision_trace_models import DecisionTrace
from app.storage.repositories import TradingRepository
from app.setups.setup_roles import setup_allows_entry, setup_role_from_config
from app.utils.id_generator import new_id


DEFAULT_WEIGHTS = {
    "technical_score": 0.20,
    "volume_score": 0.15,
    "liquidity_score": 0.15,
    "risk_score": 0.15,
    "trend_score": 0.10,
    "market_context_score": 0.10,
    "forecast_alignment_score": 0.10,
    "backtest_score": 0.05,
}


class SetupQualityEngine:
    def __init__(
        self,
        repository: TradingRepository,
        forecast_repository: ForecastRepository | None = None,
        settings: dict[str, Any] | None = None,
        forecast_accuracy_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.forecast_repository = forecast_repository
        self.settings = settings or {}
        self.forecast_accuracy_service = forecast_accuracy_service
        self.weights = self._weights()
        self.forecast_signal_compiler = (
            ForecastSignalCompiler(forecast_repository)
            if forecast_repository is not None
            else None
        )
        self.forecast_to_score = ForecastToScoreMapper()

    def score_setup(self, setup_id: str) -> dict[str, Any]:
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        return self._score_payload(setup=setup)

    def score_opportunity(self, opportunity_id: str) -> dict[str, Any]:
        opportunity = self.repository.get_opportunity(opportunity_id)
        if opportunity is None:
            raise KeyError(opportunity_id)
        payload = opportunity.get("payload") if isinstance(opportunity.get("payload"), dict) else {}
        setup_id = str(payload.get("setup_id") or "")
        if setup_id:
            score = self.score_setup(setup_id)
        else:
            score = self._score_payload(
                setup={
                    "setup_id": None,
                    "symbol": opportunity.get("symbol"),
                    "setup_type": opportunity.get("opportunity_type"),
                    "config": payload.get("config", {}),
                },
                opportunity_id=opportunity_id,
            )
        self.repository.update_opportunity_status(
            opportunity_id,
            "SCORED" if score["overall_score"] >= 40 else "REJECTED",
        )
        return {**score, "opportunity_id": opportunity_id}

    def score_scenario(self, scenario_id: str) -> dict[str, Any]:
        score = self._score_payload(
            setup={
                "setup_id": None,
                "symbol": "",
                "setup_type": "scenario",
                "config": {},
            },
            scenario_id=scenario_id,
        )
        return score

    def latest_scores(
        self,
        *,
        setup_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository.list_setup_scores(
            setup_id=setup_id,
            symbol=symbol,
            limit=limit,
        )

    def _score_payload(
        self,
        *,
        setup: dict[str, Any],
        scenario_id: str | None = None,
        opportunity_id: str | None = None,
    ) -> dict[str, Any]:
        setup = _canonical_setup(setup)
        symbol = str(setup.get("symbol") or "").upper()
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        forecast_applicable = setup_allows_entry(
            setup_role_from_config(config, infer_position_management=True)
        )
        components = {
            "technical_score": self._technical_score(setup),
            "volume_score": self._volume_score(symbol),
            "liquidity_score": self._liquidity_score(symbol),
            "risk_score": self._risk_score(setup),
            "trend_score": self._trend_score(symbol),
            "market_context_score": self._market_context_score(symbol),
            "forecast_alignment_score": (
                self._forecast_score(
                    symbol,
                    direction=str(_nested(setup, "config", "direction") or setup.get("direction") or "long"),
                )
                if forecast_applicable else 50.0
            ),
            "backtest_score": self._backtest_score(str(setup.get("setup_id") or "")),
        }
        overall = sum(components[key] * self.weights[key] for key in self.weights)
        score = {
            "score_id": new_id("score"),
            "setup_id": setup.get("setup_id"),
            "scenario_id": scenario_id,
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "overall_score": round(overall, 2),
            "components": components,
            "weights": self.weights,
            "explanations": self._explanations(components),
            "forecast_signal": (
                self._forecast_signal(symbol)
                if forecast_applicable else {
                    "status": "NOT_APPLICABLE_MANAGEMENT_ONLY",
                    "alignment_score": 50.0,
                    "used_for_decision": False,
                    "decision_impact": "NONE",
                }
            ),
            "created_at": utc_now_iso(),
        }
        self.repository.add_setup_score(score)
        forecast_signal = score["forecast_signal"]
        reason_codes = ["FORECAST_SCORING_ONLY"]
        if forecast_signal.get("model"):
            reason_codes.append(f"FORECAST_MODEL_{str(forecast_signal['model']).upper()}")
        reliability = forecast_signal.get("historical_reliability")
        if isinstance(reliability, dict):
            reason_codes.append(
                f"FORECAST_RELIABILITY_{str(reliability.get('reliability_grade') or 'UNKNOWN').upper()}"
            )
        trace = DecisionTrace(
            entity_type="setup" if setup.get("setup_id") else "scoring",
            entity_id=str(setup.get("setup_id") or scenario_id or opportunity_id or score["score_id"]),
            decision_type="SETUP_QUALITY_SCORE",
            decision="SCORING_ONLY",
            reason_codes=reason_codes,
            symbol=symbol or None,
            setup_id=setup.get("setup_id"),
            scenario_id=scenario_id,
            opportunity_id=opportunity_id,
            inputs={
                "forecast_signal": forecast_signal,
                "execution_allowed": False,
            },
            outputs={
                "setup_quality_score": score["overall_score"],
                "forecast_alignment_score": components["forecast_alignment_score"],
                "order_action": None,
            },
            human_message="Forecast data enriched setup quality; no order action was permitted.",
        )
        self.repository.add_decision_trace(trace.to_record())
        score["decision_trace_id"] = trace.trace_id
        return score

    def _technical_score(self, setup: dict[str, Any]) -> float:
        score = 35.0
        if setup.get("entry_trigger") or _nested(setup, "config", "entry", "trigger_price"):
            score += 25
        if _nested(setup, "config", "trailing_stop_loss", "initial_stop"):
            score += 25
        if setup.get("setup_type"):
            score += 15
        return min(score, 100.0)

    def _risk_score(self, setup: dict[str, Any]) -> float:
        quantity = setup.get("maximum_quantity")
        max_risk = setup.get("maximum_risk")
        if quantity and max_risk is not None:
            return 90.0 if float(max_risk) > 0 else 40.0
        config = setup.get("config", {}) if isinstance(setup.get("config"), dict) else {}
        risk = config.get("risk", {}) if isinstance(config.get("risk"), dict) else {}
        trailing_stop = _nested(config, "trailing_stop_loss", "initial_stop")
        if risk.get("max_risk_usd") and trailing_stop:
            return 75.0
        return 35.0

    def _volume_score(self, symbol: str) -> float:
        analysis = self._latest_stock_analysis(symbol)
        ratio = _first_number(
            _nested(analysis, "metadata", "analysis", "volume_ratio"),
            _nested(analysis, "metadata", "snapshot", "volume_ratio"),
            analysis.get("volume_ratio") if isinstance(analysis, dict) else None,
        )
        if ratio is None:
            return 50.0
        return min(100.0, max(20.0, ratio * 50.0))

    def _liquidity_score(self, symbol: str) -> float:
        quote = self._latest_stock_quote(symbol)
        spread_bps = _first_number(
            quote.get("spread_bps") if isinstance(quote, dict) else None
        )
        spread_pct = _first_number(
            quote.get("spread_pct") if isinstance(quote, dict) else None,
            spread_bps / 100 if spread_bps is not None else None,
        )
        if spread_pct is None:
            return 60.0
        if spread_pct <= 0.20:
            return 95.0
        if spread_pct <= 0.35:
            return 80.0
        if spread_pct <= 0.75:
            return 45.0
        return 15.0

    def _trend_score(self, symbol: str) -> float:
        quote = self._latest_stock_quote(symbol)
        perf = _first_number(quote.get("change_pct") if isinstance(quote, dict) else None)
        if perf is None:
            return 50.0
        return min(100.0, max(0.0, 50.0 + perf * 10.0))

    def _market_context_score(self, symbol: str) -> float:
        analysis = self._latest_stock_analysis(symbol)
        context = _first_number(
            _nested(analysis, "metadata", "analysis", "market_context_score"),
            _nested(analysis, "trace", "market_context_score"),
        )
        if context is None:
            return 50.0
        return min(100.0, max(0.0, context))

    def _forecast_signal(self, symbol: str) -> dict[str, Any]:
        if self.forecast_signal_compiler is None or not symbol:
            return {
                "status": "NO_FORECAST_REPOSITORY",
                "alignment_score": 50.0,
                "used_for_decision": False,
                "decision_impact": "NONE",
            }
        signal = self.forecast_signal_compiler.latest_signal(symbol)
        signal["historical_reliability"] = self._forecast_reliability(signal, symbol)
        return signal

    def _forecast_score(self, symbol: str, *, direction: str = "long") -> float:
        if self.forecast_signal_compiler is None or not symbol:
            return 50.0
        signal = self.forecast_signal_compiler.latest_signal(symbol)
        mapped = self.forecast_to_score.component_score(
            signal,
            setup_direction=direction,
        )
        raw_score = float(mapped["score"])
        reliability = self._forecast_reliability(signal, symbol)
        factor = {
            "A": 1.0,
            "B": 1.0,
            "C": 0.35,
            "D": 0.0,
            "F": 0.0,
            "INSUFFICIENT_DATA": 0.0,
        }.get(str(reliability.get("reliability_grade")), 0.0)
        return 50.0 + (raw_score - 50.0) * factor

    def _forecast_reliability(self, signal: dict[str, Any], symbol: str) -> dict[str, Any]:
        if self.forecast_accuracy_service is None:
            return {"reliability_grade": "INSUFFICIENT_DATA", "sample_size": 0}
        model = str(signal.get("model") or "unknown").lower().replace("-", "_")
        if model.startswith("timesfm"):
            model = "timesfm"
        rows = self.forecast_accuracy_service.scorecards(
            model,
            symbol=symbol,
            timeframe=signal.get("timeframe"),
        )
        if not rows:
            return {"reliability_grade": "INSUFFICIENT_DATA", "sample_size": 0}
        row = rows[0]
        return {
            "reliability_grade": row.get("reliability_grade", "INSUFFICIENT_DATA"),
            "sample_size": int(row.get("sample_size") or 0),
            "direction_accuracy": row.get("direction_accuracy"),
            "mape": row.get("mape"),
        }

    def _backtest_score(self, setup_id: str) -> float:
        if not setup_id:
            return 50.0
        runs = [
            item
            for item in self.repository.list_backtest_runs(limit=50)
            if item.get("setup_id") == setup_id
        ]
        if not runs:
            return 50.0
        metrics = runs[0].get("metrics") if isinstance(runs[0].get("metrics"), dict) else {}
        profit_factor = _first_number(metrics.get("profit_factor"))
        win_rate = _first_number(metrics.get("win_rate"))
        score = 50.0
        if profit_factor is not None:
            score += min(25.0, max(-25.0, (profit_factor - 1.0) * 25.0))
        if win_rate is not None:
            score += min(25.0, max(-25.0, (win_rate - 0.5) * 100.0))
        return min(100.0, max(0.0, score))

    def _latest_stock_quote(self, symbol: str) -> dict[str, Any]:
        return self._latest_event_payload(symbol, "stock_quote")

    def _latest_stock_analysis(self, symbol: str) -> dict[str, Any]:
        event = self._latest_event_payload(symbol, "stock_analysis")
        processed = event.get("processed") if isinstance(event, dict) else None
        if isinstance(processed, list) and processed:
            first = processed[0]
            return first if isinstance(first, dict) else event
        return event

    def _latest_event_payload(self, symbol: str, event_type: str) -> dict[str, Any]:
        if not symbol:
            return {}
        events = self.repository.list_events(
            symbol=symbol,
            event_type=event_type,
            limit=1,
        )
        if not events:
            return {}
        data = events[0].get("data")
        return data if isinstance(data, dict) else {}

    def _weights(self) -> dict[str, float]:
        raw = self.settings.get("scoring", {}).get("weights", {})
        if not isinstance(raw, dict):
            raw = {}
        weights = {
            key: float(raw.get(key, value))
            for key, value in DEFAULT_WEIGHTS.items()
        }
        total = sum(weights.values()) or 1.0
        return {key: value / total for key, value in weights.items()}

    @staticmethod
    def _explanations(components: dict[str, float]) -> list[str]:
        explanations = []
        for key, value in components.items():
            if value >= 75:
                tone = "supportive"
            elif value >= 50:
                tone = "neutral"
            else:
                tone = "weak"
            explanations.append(f"{key}: {tone} ({round(value, 1)})")
        return explanations


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _canonical_setup(setup: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(setup)
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    try:
        normalized["config"] = canonicalize_setup_config(config).config
    except Exception:
        normalized["config"] = dict(config)
    return normalized


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
