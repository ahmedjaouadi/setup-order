from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.decision_codes import (
    REASON_MISSING_MARKET_DATA,
    REASON_PRICE_TOO_EXTENDED,
    REASON_SPREAD_TOO_WIDE,
    REASON_VOLUME_INSUFFICIENT,
    STATUS_NO_GO,
    STATUS_PAUSED,
)
from app.models import utc_now_iso
from app.opportunities.opportunity_expiration_policy import OpportunityExpirationPolicy
from app.opportunities.opportunity_explainer import OpportunityExplainer
from app.opportunities.opportunity_lifecycle_service import OpportunityLifecycleService
from app.opportunities.scenario_generator import ScenarioGenerator
from app.opportunities.shortlist_service import OpportunityShortlistService
from app.opportunity_scanner import MarketContextOpportunityScanner
from app.opportunity_scanner.context_tags import snapshot_time_bucket
from app.opportunity_scanner.data_quality_gate import (
    DEFAULT_STALENESS_MAX_SECONDS,
    STATUS_OK,
    evaluate_snapshot_quality,
)
from app.opportunity_scanner.feature_math import (
    atr_pct,
    dist_vwap_pct,
    price_above,
    rth_session_bars,
    session_vwap,
)
from app.opportunity_scanner.outcome_repository import OutcomeRepository
from app.opportunity_scanner.outcome_tracker import OutcomeTracker
from app.opportunity_scanner.technique_repository import TechniqueRepository
from app.scoring.service import SetupQualityEngine
from app.settings import load_yaml_file
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from app.utils.market_hours import classify_us_equity_session


class OpportunityScannerService:
    def __init__(
        self,
        repository: TradingRepository,
        scoring: SetupQualityEngine,
        event_store: EventStore,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.scoring = scoring
        self.event_store = event_store
        self.settings = settings or {}
        self._paused = False
        self._last_run: dict[str, Any] = {}
        self.technique_repository = TechniqueRepository(repository.database)
        self.outcome_tracker = OutcomeTracker(OutcomeRepository(repository.database))
        self.context_scanner = MarketContextOpportunityScanner(
            self.settings,
            technique_provider=self.technique_repository.list_active,
        )
        self.expiration_policy = OpportunityExpirationPolicy(self.settings)
        self.shortlist_service = OpportunityShortlistService(
            repository,
            self.settings,
            self.expiration_policy,
        )
        self.scenario_generator = ScenarioGenerator(
            repository,
            event_store,
            self.settings,
        )
        self.lifecycle = OpportunityLifecycleService(
            repository,
            event_store,
            self.expiration_policy,
        )
        self.explainer = OpportunityExplainer(
            repository,
            getattr(scoring, "forecast_repository", None),
        )

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            "enabled": bool(config.get("enabled", True)),
            "paused": self._paused,
            "last_run": self._last_run,
            "opportunity_count": len(self.repository.list_opportunities(limit=500)),
        }

    def config(self) -> dict[str, Any]:
        raw = self.settings.get("opportunity_scanner", {})
        if not isinstance(raw, dict):
            raw = {}
        default_universe = {
            "source": "setups",
            "max_symbols": 200,
            "default_watchlist_file": "",
            "include_active_setups": True,
            "include_open_positions": True,
            "include_recent_quotes": False,
        }
        return {
            "enabled": bool(raw.get("enabled", True)),
            "default_timeframes": raw.get("default_timeframes", ["15m", "1h", "1d"]),
            "max_candidates_per_scan": int(raw.get("max_candidates_per_scan", 100)),
            "max_shortlisted": int(raw.get("max_shortlisted", 20)),
            "scan_interval_seconds": int(raw.get("scan_interval_seconds", 30)),
            "universe": raw.get("universe", default_universe),
            "filters": raw.get(
                "filters",
                {
                    "min_price": 1.0,
                    "max_price": 1000.0,
                    "min_volume": 100000,
                    "min_volume_ratio": 0.8,
                    "max_spread_pct": raw.get("max_spread_pct_default", 0.35),
                    "allow_missing_quote_for_setup": True,
                },
            ),
            "scanners": raw.get(
                "scanners",
                {
                    "momentum_breakout": {"enabled": True, "min_volume_ratio": 1.5},
                    "breakout_retest": {"enabled": True, "retest_max_distance_atr": 1.0},
                    "reclaim": {"enabled": True, "hold_bars_after_reclaim": 1},
                    "pullback_continuation": {"enabled": True},
                },
            ),
        }

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings.setdefault("opportunity_scanner", {})
        if not isinstance(current, dict):
            current = {}
            self.settings["opportunity_scanner"] = current
        for key in {
            "enabled",
            "default_timeframes",
            "max_candidates_per_scan",
            "max_shortlisted",
            "scan_interval_seconds",
            "universe",
            "filters",
            "scanners",
        }:
            if key in payload:
                current[key] = payload[key]
        return self.config()

    def pause(self) -> dict[str, Any]:
        self._paused = True
        return self.status()

    def resume(self) -> dict[str, Any]:
        self._paused = False
        return self.status()

    def scan(self, *, limit: int | None = None) -> dict[str, Any]:
        config = self.config()
        if self._paused:
            return {"ok": False, "reason": "scanner_paused", "items": []}
        if not config["enabled"]:
            return {"ok": False, "reason": "scanner_disabled", "items": []}

        max_candidates = limit or config["max_candidates_per_scan"]
        candidates = self._candidate_universe(config)[:max_candidates]
        opportunities = []
        rejected_by_liquidity = 0
        for candidate in candidates:
            opportunity = self._opportunity_from_candidate(candidate, config)
            self.repository.upsert_opportunity(opportunity)
            opportunities.append(opportunity)
            if opportunity.get("status") == "REJECTED":
                rejected_by_liquidity += int(
                    "liquidity" in str(opportunity.get("payload", {}).get("reason", "")).lower()
                )
        opportunities.sort(key=lambda item: item.get("score") or 0, reverse=True)
        shortlisted = opportunities[: config["max_shortlisted"]]
        self._last_run = {
            "ran_at": utc_now_iso(),
            "candidates": len(candidates),
            "created_or_updated": len(opportunities),
            "shortlisted": len(shortlisted),
            "rejected_by_liquidity": rejected_by_liquidity,
            "universe_sources": sorted(
                {source for candidate in candidates for source in candidate.get("sources", [])}
            ),
        }
        self.event_store.record_runtime(
            "opportunity_scan_completed",
            payload=self._last_run,
        )
        return {"ok": True, "items": shortlisted, "summary": self._last_run}

    def list_opportunities(
        self,
        *,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.repository.list_opportunities(status=status, symbol=symbol, limit=limit)

    def top(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.repository.list_opportunities(limit=limit)

    def shortlist(self, *, limit: int | None = None) -> dict[str, Any]:
        self.lifecycle.expire_stale()
        return self.shortlist_service.build(limit=limit)

    def rebuild_shortlist(self, *, limit: int | None = None) -> dict[str, Any]:
        scan_result = self.scan(limit=limit)
        shortlist = self.shortlist(limit=limit)
        return {
            "ok": bool(scan_result.get("ok")),
            "scan": scan_result,
            "shortlist": shortlist,
            "items": shortlist["items"],
        }

    def get(self, opportunity_id: str) -> dict[str, Any] | None:
        return self.repository.get_opportunity(opportunity_id)

    def ignore(self, opportunity_id: str) -> dict[str, Any]:
        self.repository.update_opportunity_status(opportunity_id, "IGNORED")
        return {"ok": True, "opportunity_id": opportunity_id, "status": "IGNORED"}

    def archive(self, opportunity_id: str) -> dict[str, Any]:
        self.repository.update_opportunity_status(opportunity_id, "ARCHIVED")
        return {"ok": True, "opportunity_id": opportunity_id, "status": "ARCHIVED"}

    def generate_scenario(self, opportunity_id: str) -> dict[str, Any]:
        return self.generate_scenario_draft(opportunity_id)

    def generate_scenario_draft(self, opportunity_id: str) -> dict[str, Any]:
        return self.scenario_generator.generate_draft(opportunity_id)

    def create_setup_candidate(self, symbol: str) -> dict[str, Any]:
        opportunities = self.repository.list_opportunities(
            symbol=str(symbol or "").upper(),
            limit=1,
        )
        if not opportunities:
            raise KeyError(symbol)
        return self.generate_scenario_draft(str(opportunities[0]["opportunity_id"]))

    def mark_reviewed(self, opportunity_id: str) -> dict[str, Any]:
        return self.lifecycle.mark_reviewed(opportunity_id)

    def expire(self, opportunity_id: str, *, reason: str = "manual") -> dict[str, Any]:
        return self.lifecycle.expire(opportunity_id, reason=reason)

    def explain(self, opportunity_id: str) -> dict[str, Any]:
        return self.explainer.explain(opportunity_id)

    def _candidate_universe(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        universe_config = _as_dict(config.get("universe"))
        max_symbols = int(universe_config.get("max_symbols", 200) or 200)
        setups = [
            setup
            for setup in self.repository.list_setups()
            if setup.get("status") not in {"CLOSED", "CANCELLED", "EXPIRED", "INVALIDATED"}
        ]
        positions = self.repository.list_positions()
        quotes = self._latest_quotes()
        metadata = self._metadata()
        candidates: dict[str, dict[str, Any]] = {}

        def add_symbol(symbol: str, source: str, setup: dict[str, Any] | None = None) -> None:
            normalized = str(symbol or "").strip().upper()
            if not normalized:
                return
            candidate = candidates.setdefault(
                normalized,
                {
                    "symbol": normalized,
                    "sources": [],
                    "setup": None,
                    "metadata": metadata.get(normalized, {}),
                    "quote": quotes.get(normalized, {}),
                },
            )
            if source not in candidate["sources"]:
                candidate["sources"].append(source)
            if setup and candidate.get("setup") is None:
                candidate["setup"] = setup

        if universe_config.get("include_active_setups", True):
            for setup in setups:
                add_symbol(str(setup.get("symbol") or ""), "setup", setup)
        if universe_config.get("include_open_positions", True):
            for position in positions:
                add_symbol(str(position.get("symbol") or ""), "position")
        if universe_config.get("include_recent_quotes", True):
            for symbol in quotes:
                add_symbol(symbol, "recent_quote")
        for symbol in self._watchlist_symbols(universe_config):
            matched_setup = next(
                (item for item in setups if str(item.get("symbol") or "").upper() == symbol),
                None,
            )
            add_symbol(symbol, "watchlist", matched_setup)

        def priority(candidate: dict[str, Any]) -> tuple[int, str]:
            metadata_priority = _number(candidate.get("metadata", {}).get("custom_priority")) or 0
            source_score = sum(
                {
                    "setup": 100,
                    "position": 90,
                    "watchlist": 70,
                    "recent_quote": 50,
                }.get(source, 0)
                for source in candidate.get("sources", [])
            )
            return (int(source_score + metadata_priority), candidate["symbol"])

        return sorted(candidates.values(), key=priority, reverse=True)[:max_symbols]

    def _opportunity_from_candidate(
        self,
        candidate: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        setup = candidate.get("setup") if isinstance(candidate.get("setup"), dict) else None
        quote = candidate.get("quote") if isinstance(candidate.get("quote"), dict) else {}
        if setup is not None:
            score = self.scoring.score_setup(str(setup["setup_id"]))
            opportunity = self._opportunity_from_setup(setup, score, quote=quote)
        else:
            opportunity = self._opportunity_from_market(candidate, config)
        payload = opportunity.setdefault("payload", {})
        payload["universe_sources"] = candidate.get("sources", [])
        payload["market_snapshot"] = quote
        detection_snapshot = payload.pop("_detection_snapshot", None)
        # A data-quality failure (raised in _opportunity_from_market) is terminal:
        # the candidate is already REJECTED and gate-traced, so we neither run the
        # liquidity filter again nor record an outcome on suspect data (skills.md
        # 28bis). This also keeps the gate trace to exactly one per scan.
        data_quality = payload.get("data_quality")
        if isinstance(data_quality, dict) and data_quality.get("status") == STATUS_PAUSED:
            return opportunity
        filters = self._liquidity_filter(candidate, config)
        payload["liquidity_filter"] = filters
        if filters["blocked"]:
            opportunity["status"] = "REJECTED"
            opportunity["score"] = min(float(opportunity.get("score") or 0), 39.0)
            payload["reason"] = "Rejected by liquidity/data filters."
            self._trace_liquidity_rejection(opportunity, filters)
        elif isinstance(detection_snapshot, dict):
            # Outcomes are only recorded once the candidate has cleared BOTH
            # the data-quality gate (in _opportunity_from_market) and the
            # liquidity filter: never learn from a rejected/suspect candidate
            # (skills.md 28bis).
            self._record_detection_outcomes(
                str(opportunity.get("symbol") or ""),
                detection_snapshot,
                payload.get("market_context_signal", {}),
            )
        return opportunity

    def _opportunity_from_setup(
        self,
        setup: dict[str, Any],
        score: dict[str, Any],
        *,
        quote: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        symbol = str(setup.get("symbol") or "").upper()
        setup_type = str(setup.get("setup_type") or "setup")
        timeframe = str(
            _nested(setup, "config", "timeframes", "signal")
            or _nested(setup, "config", "timeframe")
            or "15m"
        )
        opportunity_id = f"opp_{symbol}_{setup_type}_{setup.get('setup_id')}"
        selection = self._selection_context(setup_type, quote or {}, setup)
        return {
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "opportunity_type": setup_type,
            "timeframe": timeframe,
            "status": (
                "DETECTED" if score["overall_score"] >= 40 and selection["selected"] else "REJECTED"
            ),
            "score": _bounded_score(float(score["overall_score"]) + selection["score_bonus"]),
            "detected_at": utc_now_iso(),
            "payload": {
                "setup_id": setup.get("setup_id"),
                "setup_status": setup.get("status"),
                "config": setup.get("config", {}),
                "score": score,
                "selection": selection,
                "executable": False,
                "reason": "Opportunities are discovery objects and cannot submit orders.",
            },
        }

    def _opportunity_from_market(
        self,
        candidate: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = str(candidate.get("symbol") or "").upper()
        raw_quote = candidate.get("quote")
        quote: dict[str, Any] = raw_quote if isinstance(raw_quote, dict) else {}
        context_snapshot = self._context_snapshot_from_candidate(candidate, quote)
        quality = self._data_quality_verdict(context_snapshot)
        if quality["status"] != STATUS_OK:
            return self._data_quality_rejected(symbol, quote, context_snapshot, quality, config)
        context_signal = self.context_scanner.evaluate(context_snapshot)
        scanners = _as_dict(config.get("scanners"))
        selections = [
            self._selection_context(scanner_name, quote, None, scanner_config)
            for scanner_name, scanner_config in scanners.items()
            if _as_dict(scanner_config).get("enabled", True)
        ]
        selected = [item for item in selections if item["selected"]]
        best = max(
            selected or selections or [self._selection_context("watchlist", quote, None)],
            key=lambda item: item["score_bonus"],
        )
        opportunity_type = str(context_signal.get("opportunity_type") or "WATCHLIST_ANOMALY")
        setup_type = _setup_type_for_context_signal(context_signal, best)
        score = _bounded_score(float(context_signal.get("opportunity_score") or 0))
        return {
            "opportunity_id": f"opp_{symbol}_{opportunity_type}_scanner",
            "symbol": symbol,
            "opportunity_type": opportunity_type,
            "timeframe": str(
                _first_value(quote.get("timeframe"), config.get("default_timeframes", ["15m"])[0])
            ),
            "status": _table_status_for_context_signal(context_signal),
            "score": score,
            "detected_at": utc_now_iso(),
            "payload": {
                "setup_id": None,
                "opportunity_status": context_signal.get("opportunity_status"),
                "opportunity_type": context_signal.get("opportunity_type"),
                "opportunity_types": context_signal.get("opportunity_types", []),
                "opportunity_score": score,
                "reasons": context_signal.get("reasons", []),
                "warnings": context_signal.get("warnings", []),
                "recommended_next_action": context_signal.get("recommended_next_action"),
                "can_send_order": False,
                "config": {
                    "symbol": symbol,
                    "setup_type": setup_type,
                    "timeframe": str(_first_value(quote.get("timeframe"), "15m")),
                },
                "score": {
                    "overall_score": score,
                    "components": {
                        "technical_score": context_signal.get("discovery_score", score),
                        "volume_score": _volume_score(quote),
                        "liquidity_score": _spread_score(quote),
                        "market_context_score": score,
                    },
                    # Weighted quality score (skills.md 9.1) - observational,
                    # shipped alongside the legacy scores, never a gate.
                    "quality_score": context_signal.get("quality_score"),
                    "score_grade": context_signal.get("score_grade"),
                    "quality_breakdown": context_signal.get("score_breakdown", {}),
                },
                "selection": best,
                "all_selection_rules": selections,
                "market_context_signal": context_signal,
                "source_snapshot": context_signal.get("source_snapshot", {}),
                "detected_by": context_signal.get("detected_by"),
                "executable": False,
                "reason": "Scanner opportunity; generate a setup candidate before any setup can be armed.",
                # Carried to _opportunity_from_candidate, which records detection
                # outcomes only once the liquidity filter has also passed. Popped
                # there so it never leaks into the persisted payload.
                "_detection_snapshot": context_snapshot,
            },
        }

    def _staleness_max_seconds(self) -> float:
        raw = self.settings.get("opportunity_scanner", {})
        data_quality = raw.get("data_quality", {}) if isinstance(raw, dict) else {}
        value = (
            _number(data_quality.get("staleness_max_seconds"))
            if isinstance(data_quality, dict)
            else None
        )
        if value is not None and value > 0:
            return value
        return float(DEFAULT_STALENESS_MAX_SECONDS)

    def _data_quality_verdict(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Verdict of the data-quality gate on a scanner snapshot (skills.md 28bis)."""
        return evaluate_snapshot_quality(
            snapshot,
            staleness_max_seconds=self._staleness_max_seconds(),
        )

    def _data_quality_rejected(
        self,
        symbol: str,
        quote: dict[str, Any],
        snapshot: dict[str, Any],
        quality: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a REJECTED opportunity for a snapshot that failed the gate.

        No technique is evaluated and no detection outcome is recorded: learning
        must never start from a suspect price. The refusal is traced as a
        qualified SCANNER_GATE decision (status + reason_code, skills.md 2.5).
        """
        reason_code = str(quality.get("reason_code") or REASON_MISSING_MARKET_DATA)
        issues = list(quality.get("issues") or [])
        opportunity_id = f"opp_{symbol}_DATA_QUALITY_scanner"
        self._trace_scanner_gate(
            symbol=symbol,
            opportunity_id=opportunity_id,
            status=STATUS_PAUSED,
            reason_code=reason_code,
            issues=issues,
            snapshot=snapshot,
        )
        return {
            "opportunity_id": opportunity_id,
            "symbol": symbol,
            "opportunity_type": "DATA_QUALITY_PAUSED",
            "timeframe": str(_first_value(quote.get("timeframe"), "15m")),
            "status": "REJECTED",
            "score": 0.0,
            "detected_at": utc_now_iso(),
            "payload": {
                "setup_id": None,
                "can_send_order": False,
                "executable": False,
                "reason": "Paused by the data-quality gate; no detection on suspect data.",
                "data_quality": {
                    "status": STATUS_PAUSED,
                    "reason_code": reason_code,
                    "issues": issues,
                },
            },
        }

    def _record_detection_outcomes(
        self,
        symbol: str,
        snapshot: dict[str, Any],
        context_signal: dict[str, Any],
    ) -> None:
        """Record a pending outcome per matched technique, RTH only.

        Outside regular trading hours nothing is recorded, so outcome stats stay
        frozen. Never raises: outcome tracking must not break a scan.
        """
        if classify_us_equity_session(datetime.now(UTC)) != "RTH":
            return
        technique_ids = context_signal.get("detected_by_techniques") or []
        if not technique_ids:
            return
        with suppress(Exception):
            self.outcome_tracker.record_matches(technique_ids, symbol, snapshot)

    def _context_snapshot_from_candidate(
        self,
        candidate: dict[str, Any],
        quote: dict[str, Any],
    ) -> dict[str, Any]:
        raw_metadata = candidate.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        sector = str(metadata.get("sector") or quote.get("sector") or "").strip()
        sector_etf = str(metadata.get("sector_etf") or quote.get("sector_etf") or "").strip()
        metadata_status = (
            "SECTOR_OK" if sector and sector.upper() != "UNKNOWN" else "SECTOR_UNKNOWN"
        )
        snapshot = {
            **quote,
            "symbol": candidate.get("symbol"),
            "sector": sector or "UNKNOWN",
            "sector_etf": sector_etf,
            "metadata_status": quote.get("metadata_status") or metadata_status,
            "perf_stock_1d": _first_value(
                quote.get("perf_stock_1d"),
                quote.get("stock_perf_1d"),
            ),
            "perf_sector_1d": _first_value(
                quote.get("perf_sector_1d"),
                quote.get("sector_perf_1d"),
            ),
            "perf_spy_1d": _first_value(
                quote.get("perf_spy_1d"),
                quote.get("spy_perf_1d"),
            ),
            "rs_sector": _first_value(
                quote.get("rs_sector"),
                quote.get("relative_strength_vs_sector"),
            ),
            "rs_spy": _first_value(
                quote.get("rs_spy"),
                quote.get("relative_strength_vs_spy"),
            ),
            "event_risk": quote.get("event_risk") or "OK",
            "setup_status": quote.get("setup_status") or "",
            "universe_sources": candidate.get("sources", []),
        }
        snapshot.update(self._f1_features(quote, snapshot))
        return snapshot

    @staticmethod
    def _f1_features(quote: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """F1 feature lot injected into the scanner snapshot (skills.md 4.1/5.1/6.2bis/7.1/25bis).

        Every feature degrades to ``None`` when an ingredient is missing: the
        rule interpreter treats an absent field as a non-match, never an error.
        ``rvol`` is the canonical relative-volume field (SAME_TIME_OF_DAY);
        the session VWAP is recomputed on each scan from the RTH 15m bars the
        quote already carries (no incremental state). The daily-trend booleans
        read the daily enrichment keys (``historical_ema_20``/``historical_sma_50``,
        produced by ``FeatureStore._features_from_bars`` on daily bars); until a
        daily feed populates them on the scanner quote they stay ``None``.
        """
        price = _number(_first_value(quote.get("price"), quote.get("last"), quote.get("close")))
        vwap = session_vwap(rth_session_bars(quote.get("historical_bars")))
        return {
            "rvol": _first_value(
                quote.get("rvol"),
                quote.get("relative_volume"),
                quote.get("volume_ratio"),
                quote.get("volume_ratio_15m"),
            ),
            "atr_pct": atr_pct(quote),
            "vwap": vwap,
            "dist_vwap_pct": dist_vwap_pct(price, vwap),
            "time_bucket": snapshot_time_bucket(snapshot),
            "price_above_ema20": price_above(
                price,
                _first_value(quote.get("historical_ema_20"), quote.get("daily_ema_20")),
            ),
            "price_above_sma50": price_above(
                price,
                _first_value(quote.get("historical_sma_50"), quote.get("daily_sma_50")),
            ),
        }

    def _selection_context(
        self,
        opportunity_type: str,
        quote: dict[str, Any],
        setup: dict[str, Any] | None = None,
        scanner_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del setup
        scanner_config = _as_dict(scanner_config)
        price = _number(_first_value(quote.get("price"), quote.get("last"), quote.get("close")))
        previous_high = _number(quote.get("previous_high"))
        ema_20 = _number(quote.get("ema_20"))
        ema_50 = _number(quote.get("ema_50"))
        atr = _number(_first_value(quote.get("atr_15m"), quote.get("atr_1h")))
        support = _number(
            _first_value(
                quote.get("support_level"),
                quote.get("successful_retest_low"),
                quote.get("structural_support"),
            )
        )
        volume_ratio = _number(
            _first_value(
                quote.get("volume_ratio"),
                quote.get("volume_ratio_15m"),
                quote.get("volume_ratio_closed_bar"),
            )
        )
        min_volume_ratio = float(scanner_config.get("min_volume_ratio", 1.0) or 1.0)
        selected = False
        reasons: list[str] = []
        bonus = 0.0
        if opportunity_type == "momentum_breakout":
            selected = bool(
                price is not None
                and previous_high is not None
                and price >= previous_high
                and (volume_ratio is None or volume_ratio >= min_volume_ratio)
            )
            reasons.append(
                "price above previous_high" if selected else "waiting for breakout/volume"
            )
            bonus = 28.0 if selected else 4.0
        elif opportunity_type == "breakout_retest":
            max_distance_atr = float(scanner_config.get("retest_max_distance_atr", 1.0) or 1.0)
            distance = abs(price - support) if price is not None and support is not None else None
            selected = bool(
                quote.get("breakout_already_detected")
                and distance is not None
                and (atr is None or distance <= atr * max_distance_atr)
            )
            reasons.append(
                "breakout retest near support" if selected else "waiting for retest quality"
            )
            bonus = 24.0 if selected else 3.0
        elif opportunity_type == "reclaim":
            selected = bool(
                price is not None
                and ema_20 is not None
                and price >= ema_20
                and (volume_ratio is None or volume_ratio >= min_volume_ratio)
            )
            reasons.append("price reclaimed EMA20" if selected else "waiting for reclaim")
            bonus = 20.0 if selected else 2.0
        elif opportunity_type == "pullback_continuation":
            selected = bool(
                price is not None
                and ema_20 is not None
                and ema_50 is not None
                and ema_20 >= ema_50
                and price >= ema_20
                and price <= ema_20 * 1.05
            )
            reasons.append(
                "orderly pullback above EMA20"
                if selected
                else "waiting for trend/pullback alignment"
            )
            bonus = 18.0 if selected else 2.0
        else:
            selected = bool(price is not None)
            reasons.append("symbol is observable" if selected else "no quote yet")
            bonus = 5.0 if selected else 0.0
        return {
            "opportunity_type": opportunity_type,
            "selected": selected,
            "score_bonus": bonus,
            "rules": reasons,
            "inputs": {
                "price": price,
                "previous_high": previous_high,
                "volume_ratio": volume_ratio,
                "ema_20": ema_20,
                "ema_50": ema_50,
                "atr": atr,
                "support": support,
            },
        }

    def _liquidity_filter(
        self,
        candidate: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        filters = _as_dict(config.get("filters"))
        raw_quote = candidate.get("quote")
        quote: dict[str, Any] = raw_quote if isinstance(raw_quote, dict) else {}
        has_setup = isinstance(candidate.get("setup"), dict)
        issues = []
        price = _number(_first_value(quote.get("price"), quote.get("last"), quote.get("close")))
        volume = _number(quote.get("volume"))
        volume_ratio = _number(
            _first_value(quote.get("volume_ratio"), quote.get("volume_ratio_15m"))
        )
        spread_pct = _spread_pct(quote)
        if not quote:
            if not (has_setup and filters.get("allow_missing_quote_for_setup", True)):
                issues.append("missing_quote")
        if price is not None:
            min_price = float(filters.get("min_price", 1.0) or 1.0)
            max_price = float(filters.get("max_price", 1000.0) or 1000.0)
            if price < min_price or price > max_price:
                issues.append("price_out_of_range")
        min_volume = _number(filters.get("min_volume"))
        if volume is not None and min_volume is not None and volume < min_volume:
            issues.append("volume_below_minimum")
        min_volume_ratio = _number(filters.get("min_volume_ratio"))
        if (
            volume_ratio is not None
            and min_volume_ratio is not None
            and volume_ratio < min_volume_ratio
        ):
            issues.append("volume_ratio_below_minimum")
        max_spread_pct = float(filters.get("max_spread_pct", 0.35) or 0.35)
        if spread_pct is not None and spread_pct > max_spread_pct:
            issues.append("spread_too_wide")
        return {
            "blocked": bool(issues),
            "issues": issues,
            "price": price,
            "volume": volume,
            "volume_ratio": volume_ratio,
            "spread_pct": spread_pct,
            "thresholds": {
                "max_spread_pct": max_spread_pct,
                "min_volume": min_volume,
                "min_volume_ratio": min_volume_ratio,
            },
        }

    def _trace_liquidity_rejection(
        self, opportunity: dict[str, Any], filters: dict[str, Any]
    ) -> None:
        """Trace a liquidity refusal as a qualified SCANNER_GATE (skills.md 2.5)."""
        issues = list(filters.get("issues", []))
        self._trace_scanner_gate(
            symbol=str(opportunity.get("symbol") or ""),
            opportunity_id=str(opportunity.get("opportunity_id") or ""),
            status=STATUS_NO_GO,
            reason_code=_liquidity_reason_code(issues),
            issues=issues,
            snapshot=opportunity.get("payload", {}).get("market_snapshot", {}),
            filters=filters,
        )

    def _trace_scanner_gate(
        self,
        *,
        symbol: str,
        opportunity_id: str,
        status: str,
        reason_code: str,
        issues: list[str],
        snapshot: dict[str, Any],
        filters: dict[str, Any] | None = None,
    ) -> None:
        """Record a qualified scanner refusal (status + reason_code, skills.md 2.5).

        A silent non-match of a technique is never traced (one scan touches
        dozens of symbols every 30s); only *qualified* refusals — the data
        quality gate and the liquidity filter — leave an audit trail.
        """
        trace: dict[str, Any] = {
            "status": status,
            "reason_code": reason_code,
            "issues": issues,
            "input_snapshot": snapshot if isinstance(snapshot, dict) else {},
        }
        if filters is not None:
            trace["liquidity_filter"] = filters
        self.event_store.record_decision_trace(
            decision_type="SCANNER_GATE",
            final_decision=f"{status}:{reason_code}",
            symbol=symbol,
            opportunity_id=opportunity_id,
            trace=trace,
        )

    def _watchlist_symbols(self, universe_config: dict[str, Any]) -> list[str]:
        raw_path = str(universe_config.get("default_watchlist_file") or "").strip()
        if not raw_path:
            return []
        path = Path(raw_path)
        payload = load_yaml_file(path)
        raw_items = payload.get("symbols", payload.get("watchlist", []))
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        symbols = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, dict):
                    item = item.get("symbol")
                symbol = str(item or "").strip().upper()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def _metadata(self) -> dict[str, dict[str, Any]]:
        path = Path("config/symbol_metadata.yaml")
        payload = load_yaml_file(path)
        items = payload.get("symbols", [])
        metadata = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").upper()
                if symbol:
                    metadata[symbol] = item
        return metadata

    def _latest_quotes(self) -> dict[str, dict[str, Any]]:
        quotes = {}
        for event in self.repository.list_events(limit=1000, event_type="stock_quote"):
            symbol = str(event.get("symbol") or "").upper()
            if not symbol or symbol in quotes:
                continue
            raw_data = event.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            quotes[symbol] = {
                **data,
                "event_timestamp": event.get("timestamp"),
                "source": data.get("source") or data.get("market_data_source") or "event_store",
            }
        return quotes


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_pct(quote: dict[str, Any]) -> float | None:
    if not quote:
        return None
    spread_pct = _number(quote.get("spread_pct"))
    if spread_pct is not None:
        return round(spread_pct, 4)
    spread_bps = _number(quote.get("spread_bps"))
    if spread_bps is not None:
        return round(spread_bps / 100, 4)
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    if bid is None or ask is None or bid > ask:
        return None
    mid = (bid + ask) / 2
    return round(((ask - bid) / mid) * 100, 4) if mid > 0 else None


def _volume_score(quote: dict[str, Any]) -> float:
    ratio = _number(_first_value(quote.get("volume_ratio"), quote.get("volume_ratio_15m")))
    if ratio is None:
        return 50.0
    return min(100.0, max(0.0, ratio * 50))


def _spread_score(quote: dict[str, Any]) -> float:
    spread = _spread_pct(quote)
    if spread is None:
        return 60.0
    if spread <= 0.2:
        return 95.0
    if spread <= 0.35:
        return 80.0
    if spread <= 0.75:
        return 45.0
    return 15.0


def _bounded_score(score: float) -> float:
    return round(min(100.0, max(0.0, score)), 2)


# Maps the first liquidity-filter issue to a canonical reason code (skills.md 2.5).
_LIQUIDITY_REASON_BY_ISSUE: dict[str, str] = {
    "spread_too_wide": REASON_SPREAD_TOO_WIDE,
    "volume_below_minimum": REASON_VOLUME_INSUFFICIENT,
    "volume_ratio_below_minimum": REASON_VOLUME_INSUFFICIENT,
    "missing_quote": REASON_MISSING_MARKET_DATA,
    "price_out_of_range": REASON_PRICE_TOO_EXTENDED,
}


def _liquidity_reason_code(issues: list[str]) -> str:
    for issue in issues:
        reason = _LIQUIDITY_REASON_BY_ISSUE.get(issue)
        if reason is not None:
            return reason
    return REASON_VOLUME_INSUFFICIENT


def _setup_type_for_context_signal(
    signal: dict[str, Any],
    selection: dict[str, Any],
) -> str:
    types = signal.get("opportunity_types")
    types = types if isinstance(types, list) else []
    if "BREAKOUT_CANDIDATE" in types:
        return "breakout_retest"
    if "PULLBACK_AFTER_MOMENTUM" in types:
        return "pullback_continuation"
    selected_type = str(selection.get("opportunity_type") or "")
    if selected_type in {
        "momentum_breakout",
        "breakout_retest",
        "reclaim",
        "pullback_continuation",
    }:
        return selected_type
    return "momentum_breakout"


def _table_status_for_context_signal(signal: dict[str, Any]) -> str:
    status = str(signal.get("opportunity_status") or "").upper()
    if status == "OPPORTUNITY_DETECTED":
        return "DETECTED"
    if status == "NO_OPPORTUNITY":
        return "REJECTED"
    return "WATCHLIST"
