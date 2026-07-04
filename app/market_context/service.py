from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.market_context.relative_strength import (
    context_status,
    market_context_score,
    percent_change,
    relative_strength,
)
from app.market_context.repository import MarketContextRepository
from app.opportunity_scanner import MarketContextOpportunityScanner
from app.settings import load_yaml_file
from app.storage.repositories import TradingRepository

TERMINAL_SETUP_STATUSES = {
    "CLOSED",
    "EXPIRED",
    "INVALIDATED",
    "CANCELLED",
    "ERROR",
    "ERROR_REQUIRES_MANUAL_REVIEW",
}


class MarketContextService:
    def __init__(
        self,
        market_repository: MarketContextRepository,
        trading_repository: TradingRepository,
        sector_etfs_path: Path = Path("config/sector_etfs.yaml"),
        symbol_metadata_path: Path = Path("config/symbol_metadata.yaml"),
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.market_repository = market_repository
        self.trading_repository = trading_repository
        self.sector_etfs_path = sector_etfs_path
        self.symbol_metadata_path = symbol_metadata_path
        self.opportunity_context_scanner = MarketContextOpportunityScanner(settings)

    def overview(self) -> dict[str, Any]:
        heatmap = self.heatmap()
        nodes = heatmap["nodes"]
        sectors = self.sectors()["items"]
        return {
            "as_of": heatmap["as_of"],
            "symbols": len(nodes),
            "strong_context": len([node for node in nodes if node["status"] == "STRONG_CONTEXT"]),
            "weak_context": len(
                [
                    node
                    for node in nodes
                    if node["status"] in {"WEAK_CONTEXT", "BLOCKED_OR_RISKY_CONTEXT"}
                ]
            ),
            "auto_allowed": len([node for node in nodes if "AUTO_ALLOWED" in node["badges"]]),
            "watch_only": len([node for node in nodes if "WATCH_ONLY" in node["badges"]]),
            "top_symbols": nodes[:5],
            "sectors": sectors,
        }

    def heatmap(self, view: str = "WATCHLIST") -> dict[str, Any]:
        contexts = self._symbol_contexts()
        nodes = [self._heatmap_node(item) for item in contexts]
        nodes.sort(
            key=lambda item: (item["context_score"], item["performance"] or -999), reverse=True
        )
        return {
            "as_of": self._as_of(contexts),
            "view": view.upper(),
            "nodes": nodes,
        }

    def sectors(self) -> dict[str, Any]:
        contexts = self._symbol_contexts()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in contexts:
            grouped.setdefault(item["sector"] or "Unknown", []).append(item)
        sectors = []
        for sector, items in grouped.items():
            performances = [
                item["stock_perf_1d"] for item in items if item["stock_perf_1d"] is not None
            ]
            scores = [item["context_score"] for item in items]
            average_performance = (
                round(sum(performances) / len(performances), 4) if performances else None
            )
            average_score = round(sum(scores) / len(scores), 2) if scores else 0
            sectors.append(
                {
                    "sector": sector,
                    "symbols": len(items),
                    "average_performance": average_performance,
                    "average_context_score": average_score,
                    "status": context_status(int(round(average_score))),
                    "strong": len([item for item in items if item["context_score"] >= 60]),
                    "weak": len([item for item in items if item["context_score"] <= -20]),
                }
            )
        sectors.sort(key=lambda item: item["average_context_score"], reverse=True)
        return {"as_of": self._as_of(contexts), "items": sectors}

    def symbol_detail(self, symbol: str) -> dict[str, Any]:
        expected = symbol.upper()
        for item in self._symbol_contexts():
            if item["symbol"] == expected:
                return item
        return self._empty_symbol_context(expected)

    def events(self, symbol: str | None = None) -> dict[str, Any]:
        earnings = self.market_repository.upcoming_earnings(symbol=symbol, limit=10)
        dividends = self.market_repository.upcoming_dividends(symbol=symbol, limit=10)
        economic = self.market_repository.economic_events(limit=25)
        if symbol:
            return {
                "symbol": symbol.upper(),
                "earnings": self._next_earnings_payload(earnings),
                "dividends": self._next_dividend_payload(dividends),
                "economic": [
                    {
                        "event_name": event.get("event_name"),
                        "event_time": self._event_time(event),
                        "importance": event.get("importance") or "UNKNOWN",
                        "risk_status": self._economic_risk_status(event),
                    }
                    for event in economic
                ],
            }
        return {
            "symbol": None,
            "earnings": earnings,
            "dividends": dividends,
            "economic": economic,
        }

    def economic(self) -> dict[str, Any]:
        return {"items": self.market_repository.economic_events(limit=100)}

    def refresh(self) -> dict[str, Any]:
        # V1 recalculates from local market events; external providers can plug in here later.
        return {"ok": True, "heatmap": self.heatmap()}

    def _symbol_contexts(self) -> list[dict[str, Any]]:
        setups = [
            setup
            for setup in self.trading_repository.list_setups()
            if setup["status"] not in TERMINAL_SETUP_STATUSES
        ]
        metadata = self._metadata()
        sector_etfs = self._sector_etfs()
        quote_events = self.trading_repository.list_events(limit=1000, event_type="stock_quote")
        analysis_events = self.trading_repository.list_events(
            limit=1000, event_type="stock_analysis"
        )
        quotes = self._latest_quotes_by_symbol(quote_events)
        analyses = self._latest_analysis_by_setup(analysis_events)
        setup_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for setup in setups:
            setup_by_symbol.setdefault(str(setup["symbol"]).upper(), []).append(setup)
        symbols = sorted(set(setup_by_symbol) | set(metadata))
        return [
            self._build_symbol_context(
                symbol=symbol,
                setups=setup_by_symbol.get(symbol, []),
                metadata=metadata.get(symbol, {}),
                sector_etfs=sector_etfs,
                quotes=quotes,
                analyses=analyses,
            )
            for symbol in symbols
        ]

    def _build_symbol_context(
        self,
        symbol: str,
        setups: list[dict[str, Any]],
        metadata: dict[str, Any],
        sector_etfs: dict[str, str],
        quotes: dict[str, dict[str, Any]],
        analyses: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        quote = quotes.get(symbol, {})
        sector = (
            metadata.get("sector")
            or self._first_config_value(setups, "sector")
            or self._sector_from_etf(metadata.get("sector_etf"), sector_etfs)
            or self._sector_from_etf(self._first_config_value(setups, "sector_etf"), sector_etfs)
            or "Non classé"
        )
        industry = metadata.get("industry") or self._first_config_value(setups, "industry") or ""
        sector_etf = (
            metadata.get("sector_etf")
            or self._first_config_value(setups, "sector_etf")
            or sector_etfs.get(industry)
            or sector_etfs.get(sector)
            or ""
        )
        metadata_sector = str(metadata.get("sector") or "").strip()
        metadata_sector_etf = str(metadata.get("sector_etf") or "").strip().upper()
        setup_sector = self._first_config_value(setups, "sector")
        setup_sector_etf = self._first_config_value(setups, "sector_etf")
        inferred_sector_from_metadata_etf = self._sector_from_etf(metadata_sector_etf, sector_etfs)
        inferred_sector_from_setup_etf = self._sector_from_etf(setup_sector_etf, sector_etfs)
        sector = (
            metadata_sector
            or setup_sector
            or inferred_sector_from_metadata_etf
            or inferred_sector_from_setup_etf
            or "UNKNOWN"
        )
        sector_etf = (
            metadata_sector_etf
            or setup_sector_etf
            or sector_etfs.get(industry)
            or sector_etfs.get(sector)
            or ""
        )
        metadata_source = self._metadata_source(metadata, setups)
        metadata_status = self._metadata_status(
            metadata=metadata,
            sector=sector,
            sector_etf=sector_etf,
            metadata_sector=metadata_sector,
            setup_sector=setup_sector,
            inferred_sector=(inferred_sector_from_metadata_etf or inferred_sector_from_setup_etf),
        )
        stock_perf = self._performance_from_quote(quote)
        sector_perf = (
            self._performance_from_quote(quotes.get(str(sector_etf).upper(), {}))
            if sector_etf
            else None
        )
        spy_perf = self._performance_from_quote(quotes.get("SPY", {}))
        event_penalty = 0
        score = market_context_score(stock_perf, sector_perf, spy_perf, event_penalty=event_penalty)
        status = context_status(score)
        primary_setup = setups[0] if setups else None
        analysis = self._analysis_for(primary_setup, analyses) if primary_setup else {}
        auto_execution = any(self._auto_execution_enabled(setup) for setup in setups)
        watch_enabled = any(self._watch_enabled(setup) for setup in setups) if setups else True
        badges = self._badges(status, analysis, auto_execution, watch_enabled)
        warnings = self._warnings(stock_perf, sector_perf, spy_perf)
        warnings.extend(self._metadata_warnings(metadata_status))
        result = {
            "symbol": symbol,
            "company_name": metadata.get("company_name") or "",
            "sector": sector,
            "industry": industry,
            "sector_etf": sector_etf,
            "market_cap": self._number(metadata.get("market_cap")),
            "stock_perf_1d": stock_perf,
            "sector_perf_1d": sector_perf,
            "spy_perf_1d": spy_perf,
            "relative_strength_vs_sector": relative_strength(stock_perf, sector_perf),
            "relative_strength_vs_spy": relative_strength(stock_perf, spy_perf),
            "context_score": score,
            "context_status": status,
            "status": status,
            "badges": badges,
            "warnings": warnings,
            "blocking_events": [],
            "event_risk": "OK",
            "custom_priority": metadata.get("custom_priority"),
            "setup_count": len(setups),
            "setup_ids": [setup["setup_id"] for setup in setups],
            "setup_status": primary_setup["status"] if primary_setup else "",
            "setup_proximity_percent": self._analysis_percent(analysis),
            "auto_execution_enabled": auto_execution,
            "watch_enabled": watch_enabled,
            "last_price": self._first_number(quote, "price", "last", "close"),
            "previous_close": self._first_number(quote, "previous_close", "daily_close", "close"),
            "source": quote.get("source") or quote.get("market_data_source") or "UNKNOWN",
            "last_update": quote.get("timestamp") or quote.get("event_timestamp") or "",
            "metadata_source": metadata_source,
            "metadata_status": metadata_status,
        }
        opportunity = self.opportunity_context_scanner.evaluate(
            {
                **result,
                "perf_stock_1d": stock_perf,
                "perf_sector_1d": sector_perf,
                "perf_spy_1d": spy_perf,
                "rs_sector": result["relative_strength_vs_sector"],
                "rs_spy": result["relative_strength_vs_spy"],
                "market_context_score": score,
            }
        )
        result["badges"] = self._unique_badges([*result["badges"], *opportunity.get("badges", [])])
        result["opportunity"] = opportunity
        result["opportunity_status"] = opportunity.get("opportunity_status")
        result["opportunity_type"] = opportunity.get("opportunity_type")
        result["opportunity_score"] = opportunity.get("opportunity_score")
        result["opportunity_badges"] = opportunity.get("badges", [])
        result["recommended_next_action"] = opportunity.get("recommended_next_action")
        return result

    def _heatmap_node(self, item: dict[str, Any]) -> dict[str, Any]:
        value = item.get("custom_priority") or item.get("setup_count") or 1
        return {
            "id": item["symbol"],
            "type": "SYMBOL",
            "label": item["symbol"],
            "company_name": item.get("company_name") or "",
            "sector": item["sector"],
            "industry": item.get("industry") or "",
            "market_cap": item.get("market_cap"),
            "value": value,
            "performance": item["stock_perf_1d"],
            "context_score": item["context_score"],
            "status": item["status"],
            "badges": item["badges"],
            "drilldown_url": f"/api/market-context/symbols/{item['symbol']}",
            "auto_execution_enabled": item["auto_execution_enabled"],
            "setup_proximity_percent": item["setup_proximity_percent"],
            "metadata_status": item.get("metadata_status"),
            "metadata_source": item.get("metadata_source"),
            "opportunity_status": item.get("opportunity_status"),
            "opportunity_type": item.get("opportunity_type"),
            "opportunity_score": item.get("opportunity_score"),
            "opportunity_badges": item.get("opportunity_badges", []),
            "recommended_next_action": item.get("recommended_next_action"),
        }

    def _metadata(self) -> dict[str, dict[str, Any]]:
        metadata = self.market_repository.list_symbol_metadata()
        for item in self._metadata_from_yaml():
            symbol = str(item.get("symbol", "")).upper()
            if symbol:
                metadata[symbol] = {
                    **metadata.get(symbol, {}),
                    **item,
                    "symbol": symbol,
                    "_metadata_manual_override": True,
                    "_metadata_source": "manual_override",
                }
        for item in metadata.values():
            item.setdefault("_metadata_source", "symbol_metadata")
        return metadata

    def _metadata_from_yaml(self) -> list[dict[str, Any]]:
        if not self.symbol_metadata_path.exists():
            return []
        payload = load_yaml_file(self.symbol_metadata_path)
        items: list[dict[str, Any]] = []
        listed = (
            payload.get("symbols", [])
            if isinstance(payload, dict)
            else payload if isinstance(payload, list) else []
        )
        if isinstance(listed, list):
            items.extend(item for item in listed if isinstance(item, dict))
        overrides = payload.get("symbol_overrides") if isinstance(payload, dict) else None
        if isinstance(overrides, dict):
            for symbol, values in overrides.items():
                if not isinstance(values, dict):
                    continue
                items.append({"symbol": str(symbol).upper(), **values})
        return items

    def _sector_etfs(self) -> dict[str, str]:
        payload = load_yaml_file(self.sector_etfs_path)
        return {str(key).replace("_", " "): str(value).upper() for key, value in payload.items()}

    @staticmethod
    def _sector_from_etf(sector_etf: Any, sector_etfs: dict[str, str]) -> str:
        etf = str(sector_etf or "").upper().strip()
        if not etf:
            return ""
        for sector, mapped_etf in sector_etfs.items():
            if str(mapped_etf).upper().strip() == etf:
                return sector
        return ""

    @staticmethod
    def _metadata_source(metadata: dict[str, Any], setups: list[dict[str, Any]]) -> str:
        if metadata.get("_metadata_manual_override"):
            return "manual_override"
        if metadata:
            return str(metadata.get("_metadata_source") or "symbol_metadata")
        if setups:
            return "setup_config"
        return "fallback_unknown"

    @staticmethod
    def _metadata_status(
        *,
        metadata: dict[str, Any],
        sector: str,
        sector_etf: str,
        metadata_sector: str,
        setup_sector: str,
        inferred_sector: str,
    ) -> str:
        metadata_sector = "" if metadata_sector.upper() == "UNKNOWN" else metadata_sector
        setup_sector = "" if setup_sector.upper() == "UNKNOWN" else setup_sector
        if metadata.get("_metadata_manual_override") and sector != "UNKNOWN":
            return "SECTOR_MANUAL_OVERRIDE"
        if metadata_sector or setup_sector:
            return "SECTOR_OK" if sector_etf else "SECTOR_ETF_MISSING"
        if inferred_sector:
            return "SECTOR_PROVIDER_MISSING"
        if sector != "UNKNOWN":
            return "SECTOR_OK" if sector_etf else "SECTOR_ETF_MISSING"
        return "SECTOR_UNKNOWN"

    @staticmethod
    def _metadata_warnings(metadata_status: str) -> list[str]:
        if metadata_status == "SECTOR_UNKNOWN":
            return ["No sector metadata available"]
        if metadata_status == "SECTOR_PROVIDER_MISSING":
            return ["Sector inferred from ETF because provider metadata is missing"]
        if metadata_status == "SECTOR_ETF_MISSING":
            return ["Sector ETF is missing; sector-relative calculations may be unavailable"]
        if metadata_status == "SECTOR_MANUAL_OVERRIDE":
            return ["Sector metadata resolved from manual override"]
        return []

    def _latest_quotes_by_symbol(self, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        quotes: dict[str, dict[str, Any]] = {}
        for event in events:
            symbol = str(event.get("symbol") or "").upper()
            if not symbol or symbol in quotes:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            quotes[symbol] = {
                **data,
                "symbol": symbol,
                "event_timestamp": event.get("timestamp"),
            }
        return quotes

    def _latest_analysis_by_setup(self, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        analyses: dict[str, dict[str, Any]] = {}
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            processed = data.get("processed") if isinstance(data.get("processed"), list) else []
            for item in processed:
                if not isinstance(item, dict):
                    continue
                setup_id = str(item.get("setup_id") or event.get("setup_id") or "")
                if setup_id and setup_id not in analyses:
                    analyses[setup_id] = item
        return analyses

    def _analysis_for(
        self,
        setup: dict[str, Any] | None,
        analyses: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not setup:
            return {}
        return analyses.get(str(setup.get("setup_id"))) or {}

    def _analysis_percent(self, analysis: dict[str, Any]) -> float | None:
        score = analysis.get("opportunity_score") if isinstance(analysis, dict) else {}
        if not isinstance(score, dict):
            return None
        value = score.get("percent")
        number = self._number(value)
        return round(number, 2) if number is not None else None

    def _performance_from_quote(self, quote: dict[str, Any]) -> float | None:
        price = self._first_number(quote, "price", "last")
        previous_close = self._first_number(quote, "previous_close", "daily_close", "close")
        return percent_change(price, previous_close)

    def _first_number(self, payload: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            number = self._number(payload.get(key))
            if number is not None:
                return number
        return None

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _first_config_value(self, setups: list[dict[str, Any]], key: str) -> str:
        for setup in setups:
            config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
            market_context = (
                config.get("market_context")
                if isinstance(config.get("market_context"), dict)
                else {}
            )
            value = market_context.get(key) or config.get(key)
            if value and str(value).upper() != "AUTO":
                return str(value)
        return ""

    @staticmethod
    def _auto_execution_enabled(setup: dict[str, Any]) -> bool:
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        monitoring = config.get("monitoring") if isinstance(config.get("monitoring"), dict) else {}
        if "auto_execution_enabled" in monitoring:
            return bool(monitoring["auto_execution_enabled"])
        return bool(setup.get("enabled")) and config.get("enabled", True) is not False

    @staticmethod
    def _watch_enabled(setup: dict[str, Any]) -> bool:
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        monitoring = config.get("monitoring") if isinstance(config.get("monitoring"), dict) else {}
        return bool(monitoring.get("watch_enabled", True))

    @staticmethod
    def _badges(
        status: str,
        analysis: dict[str, Any],
        auto_execution: bool,
        watch_enabled: bool,
    ) -> list[str]:
        badges = ["AUTO_ALLOWED" if auto_execution else "WATCH_ONLY"]
        if not watch_enabled:
            badges.append("WATCH_DISABLED")
        action = str(analysis.get("action") or analysis.get("signal") or "")
        if action == "ENTRY_READY":
            badges.append("ENTRY_READY")
        if status == "STRONG_CONTEXT":
            badges.append("STRONG")
        elif status in {"WEAK_CONTEXT", "BLOCKED_OR_RISKY_CONTEXT"}:
            badges.append("WEAK")
            badges.append("WARNING" if status == "WEAK_CONTEXT" else "BLOCKED")
        return badges

    @staticmethod
    def _unique_badges(badges: list[str]) -> list[str]:
        result: list[str] = []
        for badge in badges:
            if badge and badge not in result:
                result.append(badge)
        return result

    @staticmethod
    def _warnings(
        stock_perf: float | None,
        sector_perf: float | None,
        spy_perf: float | None,
    ) -> list[str]:
        warnings = []
        if stock_perf is not None and stock_perf < 0:
            warnings.append("Stock negative on the day")
        if stock_perf is not None and sector_perf is not None and stock_perf < sector_perf:
            warnings.append("Stock lagging sector")
        if stock_perf is not None and spy_perf is not None and stock_perf < spy_perf:
            warnings.append("Stock lagging SPY")
        return warnings

    @staticmethod
    def _next_earnings_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
        event = events[0] if events else {}
        return {
            "next_event_date": event.get("event_date"),
            "timing": event.get("timing") or "UNKNOWN",
            "days_until": None,
            "risk_status": "UNKNOWN" if not event else "OK",
            "source": event.get("source") or "UNKNOWN",
        }

    @staticmethod
    def _next_dividend_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
        event = events[0] if events else {}
        return {
            "next_dividend_date": event.get("next_dividend_date"),
            "next_dividend_amount": event.get("next_dividend_amount"),
            "risk_status": "UNKNOWN" if not event else "OK",
            "source": event.get("source") or "UNKNOWN",
        }

    @staticmethod
    def _event_time(event: dict[str, Any]) -> str:
        if event.get("event_time"):
            return f"{event.get('event_date')}T{event.get('event_time')}"
        return str(event.get("event_date") or "")

    @staticmethod
    def _economic_risk_status(event: dict[str, Any]) -> str:
        importance = str(event.get("importance") or "").upper()
        if importance == "CRITICAL":
            return "BLOCKING"
        if importance == "HIGH":
            return "WARNING"
        return "OK"

    @staticmethod
    def _as_of(contexts: list[dict[str, Any]]) -> str:
        timestamps = [item.get("last_update") for item in contexts if item.get("last_update")]
        if timestamps:
            return max(timestamps)
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _empty_symbol_context(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "company_name": "",
            "sector": "Non classé",
            "industry": "",
            "sector_etf": "",
            "market_cap": None,
            "stock_perf_1d": None,
            "sector_perf_1d": None,
            "spy_perf_1d": None,
            "relative_strength_vs_sector": None,
            "relative_strength_vs_spy": None,
            "context_score": 0,
            "context_status": "NEUTRAL_CONTEXT",
            "status": "NEUTRAL_CONTEXT",
            "badges": ["WATCH_ONLY"],
            "warnings": ["No local market context data available"],
            "blocking_events": [],
            "event_risk": "UNKNOWN",
            "setup_count": 0,
            "setup_ids": [],
            "setup_status": "",
            "setup_proximity_percent": None,
            "auto_execution_enabled": False,
            "watch_enabled": True,
            "last_price": None,
            "previous_close": None,
            "source": "UNKNOWN",
            "last_update": "",
            "metadata_source": "fallback_unknown",
            "metadata_status": "SECTOR_UNKNOWN",
            "opportunity": {},
            "opportunity_status": "NO_OPPORTUNITY",
            "opportunity_type": "WATCHLIST_ANOMALY",
            "opportunity_score": 0,
            "opportunity_badges": [],
            "recommended_next_action": "NO_ACTION",
        }
