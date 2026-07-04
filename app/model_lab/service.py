from __future__ import annotations

from typing import Any

from app.conversion import canonicalize_setup_config
from app.models import utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id


class ModelLabService:
    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or {}

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        setup_id = str(payload.get("setup_id") or "")
        setup = self.repository.get_setup(setup_id) if setup_id else None
        setup = _canonical_setup(setup or {}) if setup else None
        symbol = str(payload.get("symbol") or (setup or {}).get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol or setup_id is required")
        metrics = self._synthetic_metrics(setup or {}, payload)
        run = {
            "backtest_id": new_id("bt"),
            "setup_id": setup_id or None,
            "scenario_id": payload.get("scenario_id"),
            "symbol": symbol,
            "timeframe": payload.get("timeframe")
            or _nested(setup or {}, "config", "timeframes", "signal")
            or "15m",
            "status": "COMPLETED",
            "metrics": metrics,
            "config": payload,
            "created_at": utc_now_iso(),
            "completed_at": utc_now_iso(),
        }
        self.repository.add_backtest_run(run)
        return run

    def list_backtests(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_backtest_runs(limit=limit)

    def get_backtest(self, backtest_id: str) -> dict[str, Any] | None:
        return self.repository.get_backtest_run(backtest_id)

    def run_backtest_mvp(self, payload: dict[str, Any]) -> dict[str, Any]:
        setup_id = str(payload.get("setup_id") or "")
        setup = self.repository.get_setup(setup_id) if setup_id else None
        setup = _canonical_setup(setup or {}) if setup else None
        symbol = str(payload.get("symbol") or (setup or {}).get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol or setup_id is required")
        candles = _candles(payload)
        backtest_id = new_id("bt")
        timeframe = str(
            payload.get("timeframe")
            or _nested(setup or {}, "config", "timeframes", "signal")
            or "15m"
        )
        self._record_backtest_event(
            backtest_id,
            "REPLAY_STARTED",
            symbol=symbol,
            payload={"candles": len(candles), "timeframe": timeframe},
        )
        simulation = self._simulate_stp_lmt(
            backtest_id=backtest_id,
            symbol=symbol,
            setup=setup or {},
            payload=payload,
            candles=candles,
        )
        for event in simulation["events"]:
            self.repository.add_backtest_event(event)
        for trade in simulation["trades"]:
            self.repository.add_backtest_trade(trade)
        metrics = self._backtest_metrics(simulation["trades"], payload)
        comparison = self._forecast_filter_comparison(metrics, payload)
        metrics_snapshot = dict(metrics)
        report = {
            "baseline_setup_rules_only": comparison["baseline_setup_rules_only"],
            "setup_rules_plus_timesfm_alignment": comparison["setup_rules_plus_timesfm_alignment"],
            "setup_rules_plus_ensemble_forecast_alignment": comparison[
                "setup_rules_plus_ensemble_forecast_alignment"
            ],
            "setup_rules_plus_score_threshold": comparison["setup_rules_plus_score_threshold"],
            "forecast_filter_value": comparison["forecast_filter_value"],
            "metrics": metrics_snapshot,
            "trades": simulation["trades"],
        }
        metrics["report"] = report
        run = {
            "backtest_id": backtest_id,
            "setup_id": setup_id or None,
            "scenario_id": payload.get("scenario_id"),
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "COMPLETED",
            "metrics": metrics,
            "config": {
                **payload,
                "replay_event_types": [
                    "REPLAY_STARTED",
                    "CANDLE_CLOSED",
                    "FEATURES_UPDATED",
                    "OPPORTUNITY_DETECTED",
                    "SCENARIO_SIGNAL_VALID",
                    "RISK_APPROVED",
                    "SIM_ORDER_PLACED",
                    "SIM_ORDER_FILLED",
                    "SIM_STOP_PLACED",
                    "SIM_STOP_HIT",
                    "SIM_POSITION_CLOSED",
                    "REPLAY_FINISHED",
                ],
            },
            "created_at": utc_now_iso(),
            "completed_at": utc_now_iso(),
        }
        self.repository.add_backtest_run(run)
        self._record_backtest_event(
            backtest_id,
            "REPLAY_FINISHED",
            symbol=symbol,
            payload={"status": "COMPLETED", "metrics": metrics},
        )
        return run

    def backtest_events(self, backtest_id: str) -> list[dict[str, Any]]:
        return self.repository.list_backtest_events(backtest_id)

    def backtest_trades(self, backtest_id: str) -> list[dict[str, Any]]:
        return self.repository.list_backtest_trades(backtest_id)

    def backtest_summary(self, backtest_id: str) -> dict[str, Any] | None:
        run = self.get_backtest(backtest_id)
        if run is None:
            return None
        return {
            "backtest_id": backtest_id,
            "symbol": run.get("symbol"),
            "timeframe": run.get("timeframe"),
            "status": run.get("status"),
            "metrics": run.get("metrics", {}),
        }

    def backtest_report(self, backtest_id: str) -> dict[str, Any] | None:
        run = self.get_backtest(backtest_id)
        if run is None:
            return None
        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        return {
            "backtest_id": backtest_id,
            "summary": metrics,
            "report": metrics.get("report", {}),
            "events": self.backtest_events(backtest_id),
            "trades": self.backtest_trades(backtest_id),
            "backtest": run,
        }

    def benchmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        model_name = str(payload.get("model_name") or "ensemble")
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        metrics = {
            "mae": float(payload.get("mae", 0.0) or 0.0),
            "rmse": float(payload.get("rmse", 0.0) or 0.0),
            "direction_accuracy": float(payload.get("direction_accuracy", 0.5) or 0.5),
            "entry_hit_accuracy": float(payload.get("entry_hit_accuracy", 0.5) or 0.5),
            "stop_before_entry_error": float(payload.get("stop_before_entry_error", 0.0) or 0.0),
            "pnl_when_used_as_filter": float(payload.get("pnl_when_used_as_filter", 0.0) or 0.0),
        }
        benchmark = {
            "benchmark_id": new_id("bench"),
            "model_name": model_name,
            "symbol": symbol,
            "timeframe": payload.get("timeframe", "15m"),
            "horizon": str(payload.get("horizon", "")),
            "metrics": metrics,
            "beats_baseline": bool(
                payload.get("beats_baseline", metrics["direction_accuracy"] > 0.5)
            ),
            "created_at": utc_now_iso(),
        }
        self.repository.add_model_benchmark(benchmark)
        return benchmark

    def benchmarks(self, *, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_model_benchmarks(symbol=symbol, limit=limit)

    def scorecard(self, symbol: str) -> dict[str, Any]:
        items = self.benchmarks(symbol=symbol, limit=50)
        return {
            "symbol": symbol.upper(),
            "benchmarks": items,
            "useful_models": [
                item.get("model_name") for item in items if bool(item.get("beats_baseline"))
            ],
        }

    def run_timesfm_benchmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_model_scorecard("timesfm", payload)

    def run_all_baselines(self, payload: dict[str, Any]) -> dict[str, Any]:
        scorecards = [
            self._run_model_scorecard(model_name, payload)
            for model_name in ("last_close_baseline", "atr_baseline", "trend_baseline")
        ]
        return {"items": scorecards}

    def model_scorecards(
        self,
        *,
        model_name: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository.list_model_scorecards(
            model_name=model_name,
            symbol=symbol,
            limit=limit,
        )

    def selection_policy(self) -> dict[str, Any]:
        return {"items": self.repository.list_model_selection_policy()}

    def recompute_selection_policy(self) -> dict[str, Any]:
        created = []
        for scorecard in self.repository.list_model_scorecards(limit=500):
            decision = str(scorecard.get("selection_decision") or "INSUFFICIENT_DATA")
            multiplier = _weight_multiplier(decision)
            policy = {
                "policy_id": "policy_{}_{}_{}_{}".format(
                    scorecard.get("model_name"),
                    scorecard.get("symbol"),
                    scorecard.get("timeframe"),
                    scorecard.get("horizon_bars"),
                ),
                "model_name": scorecard.get("model_name"),
                "symbol": scorecard.get("symbol"),
                "timeframe": scorecard.get("timeframe"),
                "horizon_bars": scorecard.get("horizon_bars"),
                "selection_decision": decision,
                "weight_multiplier": multiplier,
                "reason": _policy_reason(decision),
                "updated_at": utc_now_iso(),
            }
            self.repository.set_model_selection_policy(policy)
            created.append(policy)
        return {"items": created}

    @staticmethod
    def _synthetic_metrics(setup: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        risk_reward = _risk_reward(setup)
        conservative = bool(payload.get("conservative_fill_model", True))
        win_rate = 0.45 + min(max(risk_reward - 1.0, 0.0), 2.0) * 0.08
        if conservative:
            win_rate -= 0.03
        return {
            "win_rate": round(max(0.0, min(1.0, win_rate)), 4),
            "profit_factor": round(max(0.1, risk_reward * win_rate), 4),
            "max_drawdown": round(1.0 / max(risk_reward, 1.0), 4),
            "stop_hit_rate": round(1.0 - win_rate, 4),
            "entry_missed_rate": 0.15 if conservative else 0.08,
            "sample_source": "synthetic_runtime_snapshot",
        }

    def _simulate_stp_lmt(
        self,
        *,
        backtest_id: str,
        symbol: str,
        setup: dict[str, Any],
        payload: dict[str, Any],
        candles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not candles:
            metrics = self._synthetic_metrics(setup, payload)
            return {
                "events": [
                    _backtest_event(backtest_id, "FEATURES_UPDATED", symbol, metrics),
                    _backtest_event(backtest_id, "OPPORTUNITY_DETECTED", symbol, metrics),
                ],
                "trades": [],
            }
        entry = _entry_level(setup, payload)
        limit = _limit_level(setup, payload, entry)
        stop = _stop_level(setup, payload)
        quantity = int(payload.get("quantity") or 1)
        slippage = float(payload.get("slippage_per_share") or 0.0)
        events: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        position: dict[str, Any] | None = None
        order_placed = False
        for index, candle in enumerate(candles):
            events.append(_backtest_event(backtest_id, "CANDLE_CLOSED", symbol, candle))
            events.append(
                _backtest_event(
                    backtest_id,
                    "FEATURES_UPDATED",
                    symbol,
                    {"index": index, "close": candle.get("close")},
                )
            )
            if not order_placed:
                events.append(
                    _backtest_event(
                        backtest_id,
                        "OPPORTUNITY_DETECTED",
                        symbol,
                        {"entry": entry, "limit": limit, "stop": stop},
                    )
                )
                events.append(_backtest_event(backtest_id, "SCENARIO_SIGNAL_VALID", symbol, {}))
                events.append(_backtest_event(backtest_id, "RISK_APPROVED", symbol, {}))
                events.append(
                    _backtest_event(
                        backtest_id,
                        "SIM_ORDER_PLACED",
                        symbol,
                        {"order_type": "STP_LMT", "trigger": entry, "limit": limit},
                    )
                )
                order_placed = True
            if position is None and entry is not None and limit is not None:
                high = _number(candle.get("high"))
                low = _number(candle.get("low"))
                opened = _number(candle.get("open"))
                gap_above_limit = (
                    opened is not None and opened > limit and (low is None or low > limit)
                )
                if (
                    high is not None
                    and high >= entry
                    and not gap_above_limit
                    and low is not None
                    and low <= limit
                ):
                    fill_price = limit + slippage
                    position = {
                        "entry_time": candle.get("timestamp") or candle.get("date"),
                        "entry_price": fill_price,
                    }
                    events.append(
                        _backtest_event(
                            backtest_id,
                            "SIM_ORDER_FILLED",
                            symbol,
                            {"entry_price": fill_price},
                        )
                    )
                    if stop is not None:
                        events.append(
                            _backtest_event(
                                backtest_id,
                                "SIM_STOP_PLACED",
                                symbol,
                                {"stop": stop},
                            )
                        )
            if position is not None and stop is not None:
                low = _number(candle.get("low"))
                if low is not None and low <= stop:
                    exit_price = stop - slippage
                    trade = _trade(
                        backtest_id,
                        symbol,
                        position,
                        candle,
                        exit_price,
                        quantity,
                    )
                    trades.append(trade)
                    events.append(
                        _backtest_event(
                            backtest_id,
                            "SIM_STOP_HIT",
                            symbol,
                            {"exit_price": exit_price},
                        )
                    )
                    events.append(
                        _backtest_event(
                            backtest_id,
                            "SIM_POSITION_CLOSED",
                            symbol,
                            {"trade_id": trade["trade_id"]},
                        )
                    )
                    position = None
                    break
        if position is not None:
            final = candles[-1]
            exit_price = _number(final.get("close")) or position["entry_price"]
            trade = _trade(backtest_id, symbol, position, final, exit_price, quantity)
            trades.append(trade)
            events.append(
                _backtest_event(
                    backtest_id,
                    "SIM_POSITION_CLOSED",
                    symbol,
                    {"trade_id": trade["trade_id"], "reason": "end_of_replay"},
                )
            )
        return {"events": events, "trades": trades}

    def _backtest_metrics(
        self,
        trades: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not trades:
            return {
                "number_of_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "expectancy_r": 0.0,
                "max_drawdown_r": 0.0,
                "stop_hit_rate": 0.0,
                "missed_winners": 0,
                "filtered_losers": 0,
                "filtered_winners": 0,
                "sample_source": "replay_mvp",
            }
        pnl_values = [float(trade.get("pnl") or 0.0) for trade in trades]
        wins = [value for value in pnl_values if value > 0]
        losses = [abs(value) for value in pnl_values if value < 0]
        gross_wins = sum(wins)
        gross_losses = sum(losses)
        filtered_losers = int(payload.get("filtered_losers") or 0)
        filtered_winners = int(payload.get("filtered_winners") or 0)
        return {
            "number_of_trades": len(trades),
            "win_rate": round(len(wins) / len(trades), 4),
            "profit_factor": (
                round(gross_wins / gross_losses, 4) if gross_losses else float(len(wins))
            ),
            "expectancy_r": round(sum(pnl_values) / len(trades), 4),
            "max_drawdown_r": round(min(0.0, min(pnl_values)), 4),
            "stop_hit_rate": round(len(losses) / len(trades), 4),
            "missed_winners": int(payload.get("missed_winners") or filtered_winners),
            "filtered_losers": filtered_losers,
            "filtered_winners": filtered_winners,
            "sample_source": "replay_mvp",
        }

    @staticmethod
    def _forecast_filter_comparison(
        metrics: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        baseline = float(
            payload.get("baseline_performance_r") or metrics.get("expectancy_r") or 0.0
        )
        timesfm_delta = float(payload.get("timesfm_filter_delta_r") or 0.0)
        ensemble_delta = float(payload.get("ensemble_filter_delta_r") or timesfm_delta)
        score_delta = float(payload.get("score_threshold_delta_r") or 0.0)
        return {
            "baseline_setup_rules_only": baseline,
            "setup_rules_plus_timesfm_alignment": baseline + timesfm_delta,
            "setup_rules_plus_ensemble_forecast_alignment": baseline + ensemble_delta,
            "setup_rules_plus_score_threshold": baseline + score_delta,
            "forecast_filter_value": timesfm_delta,
        }

    def _run_model_scorecard(
        self,
        model_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        history = _candles(payload)
        min_history = int(payload.get("min_history_bars") or 30)
        timeframe = str(payload.get("timeframe") or "15m")
        horizon_bars = int(payload.get("horizon_bars") or payload.get("horizon") or 8)
        if len(history) < min_history:
            scorecard = self._scorecard_payload(
                model_name,
                symbol,
                timeframe,
                horizon_bars,
                sample_size=len(history),
                metrics={
                    "direction_accuracy": 0.0,
                    "mae": 0.0,
                    "hit_entry_accuracy": 0.0,
                    "stop_before_entry_accuracy": 0.0,
                    "calibration_error": 0.0,
                    "pnl_filter_effect_r": 0.0,
                },
                baseline_comparison={},
                selection_decision="INSUFFICIENT_DATA",
            )
            self.repository.add_model_scorecard(scorecard)
            return scorecard
        metrics = _model_metrics(history, payload)
        baseline = {
            "last_close_baseline": _format_delta(metrics["direction_accuracy"] - 0.5),
            "atr_baseline": _format_delta(metrics["direction_accuracy"] - 0.52),
            "trend_baseline": _format_delta(metrics["direction_accuracy"] - 0.51),
        }
        decision = _selection_decision(metrics)
        scorecard = self._scorecard_payload(
            model_name,
            symbol,
            timeframe,
            horizon_bars,
            sample_size=len(history),
            metrics=metrics,
            baseline_comparison=baseline,
            selection_decision=decision,
        )
        self.repository.add_model_scorecard(scorecard)
        self.repository.set_model_selection_policy(
            {
                "policy_id": f"policy_{model_name}_{symbol}_{timeframe}_{horizon_bars}",
                "model_name": model_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "horizon_bars": horizon_bars,
                "selection_decision": decision,
                "weight_multiplier": _weight_multiplier(decision),
                "reason": _policy_reason(decision),
                "updated_at": utc_now_iso(),
            }
        )
        return scorecard

    @staticmethod
    def _scorecard_payload(
        model_name: str,
        symbol: str,
        timeframe: str,
        horizon_bars: int,
        *,
        sample_size: int,
        metrics: dict[str, Any],
        baseline_comparison: dict[str, Any],
        selection_decision: str,
    ) -> dict[str, Any]:
        return {
            "scorecard_id": new_id("scorecard"),
            "model_name": model_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "horizon_bars": horizon_bars,
            "metrics": metrics,
            "baseline_comparison": baseline_comparison,
            "selection_decision": selection_decision,
            "sample_size": sample_size,
            "created_at": utc_now_iso(),
        }

    def _record_backtest_event(
        self,
        backtest_id: str,
        event_type: str,
        *,
        symbol: str,
        payload: dict[str, Any],
    ) -> None:
        self.repository.add_backtest_event(
            _backtest_event(backtest_id, event_type, symbol, payload)
        )


def _risk_reward(setup: dict[str, Any]) -> float:
    entry = _number(setup.get("worst_case_entry_price") or setup.get("entry_trigger"))
    stop = _first_number(
        _nested(setup, "config", "trailing_stop_loss", "initial_stop"),
    )
    target = _number(_nested(setup, "config", "targets", "first_target"))
    if entry is None or stop is None or target is None:
        return 1.0
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return reward / risk if risk > 0 else 1.0


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


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("candles")
    if rows is None:
        rows = payload.get("historical_bars")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _entry_level(setup: dict[str, Any], payload: dict[str, Any]) -> float | None:
    return _first_number(
        payload.get("entry_trigger"),
        payload.get("trigger_price"),
        setup.get("entry_trigger"),
        _nested(setup, "config", "entry", "trigger_price"),
        _nested(setup, "config", "entry", "entry_price"),
        _nested(setup, "config", "breakout", "resistance"),
        _nested(setup, "config", "breakout", "daily_close_above"),
    )


def _limit_level(
    setup: dict[str, Any],
    payload: dict[str, Any],
    entry: float | None,
) -> float | None:
    explicit = _first_number(
        payload.get("limit_price"),
        payload.get("maximum_limit_price"),
        setup.get("maximum_limit_price"),
        _nested(setup, "config", "entry", "maximum_limit_price"),
        _nested(setup, "config", "entry", "limit_price"),
    )
    if explicit is not None:
        return explicit
    offset = _first_number(
        payload.get("limit_offset"),
        _nested(setup, "config", "entry", "limit_offset"),
        0.05,
    )
    return entry + (offset or 0.0) if entry is not None else None


def _stop_level(setup: dict[str, Any], payload: dict[str, Any]) -> float | None:
    trailing_payload = payload.get("trailing_stop_loss")
    trailing_payload = trailing_payload if isinstance(trailing_payload, dict) else {}
    return _first_number(
        trailing_payload.get("initial_stop"),
        _nested(setup, "config", "trailing_stop_loss", "initial_stop"),
    )


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _backtest_event(
    backtest_id: str,
    event_type: str,
    symbol: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": new_id("btevt"),
        "backtest_id": backtest_id,
        "event_type": event_type,
        "symbol": symbol,
        "payload": payload,
        "created_at": utc_now_iso(),
    }


def _trade(
    backtest_id: str,
    symbol: str,
    position: dict[str, Any],
    candle: dict[str, Any],
    exit_price: float,
    quantity: int,
) -> dict[str, Any]:
    entry_price = float(position["entry_price"])
    pnl = (exit_price - entry_price) * quantity
    return {
        "trade_id": new_id("bttrade"),
        "backtest_id": backtest_id,
        "symbol": symbol,
        "entry_time": position.get("entry_time"),
        "exit_time": candle.get("timestamp") or candle.get("date"),
        "entry_price": round(entry_price, 4),
        "exit_price": round(exit_price, 4),
        "quantity": quantity,
        "pnl": round(pnl, 4),
        "payload": {
            "exit_reason": "stop_or_replay_end",
            "candle": candle,
        },
        "created_at": utc_now_iso(),
    }


def _model_metrics(
    history: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    closes = [_number(row.get("close")) for row in history]
    closes = [value for value in closes if value is not None]
    if len(closes) < 2:
        direction_accuracy = 0.0
        mae = 0.0
    else:
        up_moves = sum(1 for previous, current in zip(closes, closes[1:]) if current >= previous)
        direction_accuracy = up_moves / (len(closes) - 1)
        mae = sum(abs(current - previous) for previous, current in zip(closes, closes[1:])) / (
            len(closes) - 1
        )
    direction_accuracy = float(payload.get("direction_accuracy") or direction_accuracy)
    pnl_effect = float(payload.get("pnl_filter_effect_r") or (direction_accuracy - 0.5) * 10)
    return {
        "direction_accuracy": round(direction_accuracy, 4),
        "mae": round(float(payload.get("mae") or mae), 4),
        "hit_entry_accuracy": round(
            float(payload.get("hit_entry_accuracy") or direction_accuracy), 4
        ),
        "stop_before_entry_accuracy": round(
            float(payload.get("stop_before_entry_accuracy") or max(0.0, 1 - mae)), 4
        ),
        "calibration_error": round(
            float(
                payload.get("calibration_error") or max(0.0, 0.5 - abs(direction_accuracy - 0.5))
            ),
            4,
        ),
        "pnl_filter_effect_r": round(pnl_effect, 4),
    }


def _selection_decision(metrics: dict[str, Any]) -> str:
    sample_effect = float(metrics.get("pnl_filter_effect_r") or 0.0)
    direction_accuracy = float(metrics.get("direction_accuracy") or 0.0)
    if sample_effect >= 3 and direction_accuracy >= 0.58:
        return "USE_AS_STRONG_FILTER"
    if sample_effect > 0 and direction_accuracy >= 0.52:
        return "USE_AS_WEAK_FILTER"
    if sample_effect < 0:
        return "DISABLE_FOR_SYMBOL_TIMEFRAME"
    return "USE_FOR_DISPLAY_ONLY"


def _weight_multiplier(decision: str) -> float:
    return {
        "USE_AS_STRONG_FILTER": 1.0,
        "USE_AS_WEAK_FILTER": 0.5,
        "USE_FOR_DISPLAY_ONLY": 0.0,
        "DISABLE_FOR_SYMBOL_TIMEFRAME": 0.0,
        "INSUFFICIENT_DATA": 0.0,
        "NEEDS_RETRAIN_OR_REVIEW": 0.0,
    }.get(decision, 0.0)


def _policy_reason(decision: str) -> str:
    return {
        "USE_AS_STRONG_FILTER": "Model outperformed baselines with a positive filter effect.",
        "USE_AS_WEAK_FILTER": "Model has a positive but modest filter effect.",
        "USE_FOR_DISPLAY_ONLY": "Model is not strong enough for scoring weight.",
        "DISABLE_FOR_SYMBOL_TIMEFRAME": "Model underperformed baseline for this symbol/timeframe.",
        "INSUFFICIENT_DATA": "Not enough history to evaluate model quality.",
        "NEEDS_RETRAIN_OR_REVIEW": "Model needs additional review before use.",
    }.get(decision, "No policy reason available.")


def _format_delta(value: float) -> str:
    return f"{value * 100:+.1f}%"
