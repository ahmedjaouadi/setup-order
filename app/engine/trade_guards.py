"""System-level trade guards defined by docs/skills.md v2.0.

Implements, in the section 29 decision order (system gates run before setup
gates):

- the canonical ``status + reason_code`` decision model (section 2.5 / 40)
- the halt gate (section 25ter.1)
- the daily circuit breakers / kill switch (section 34.3)
- the PDT day-trade limit (section 25ter.3)
- exposure and correlation limits (section 34.4)

Circuit-breaker state is persisted through the repository ``bot_state`` so a
process restart never resets a tripped breaker intraday: the reset only
happens on the next New York trading day (section 34.3, "jamais d'auto-reset
intraday").
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Canonical status + reason codes live in the pure ``app.decision_codes`` module
# so the consultative detection pipeline can share the exact same vocabulary
# without importing anything from the execution engine (skills.md section 2.5).
# Re-exported here (explicit ``as`` aliases) for the engine's existing call sites.
from app.decision_codes import CANONICAL_STATUSES as CANONICAL_STATUSES
from app.decision_codes import REASON_BREAKOUT_REJECTED as REASON_BREAKOUT_REJECTED
from app.decision_codes import (
    REASON_CONFLICT_WITH_OPEN_POSITION as REASON_CONFLICT_WITH_OPEN_POSITION,
)
from app.decision_codes import REASON_COOLDOWN_AFTER_STOP as REASON_COOLDOWN_AFTER_STOP
from app.decision_codes import REASON_DAILY_LOSS_LIMIT as REASON_DAILY_LOSS_LIMIT
from app.decision_codes import REASON_EARNINGS_IMMINENT as REASON_EARNINGS_IMMINENT
from app.decision_codes import REASON_EXPOSURE_LIMIT as REASON_EXPOSURE_LIMIT
from app.decision_codes import REASON_HALT_ACTIVE as REASON_HALT_ACTIVE
from app.decision_codes import REASON_MARKET_CONTEXT_BAD as REASON_MARKET_CONTEXT_BAD
from app.decision_codes import REASON_MAX_TRADES_REACHED as REASON_MAX_TRADES_REACHED
from app.decision_codes import REASON_MISSING_MARKET_DATA as REASON_MISSING_MARKET_DATA
from app.decision_codes import REASON_OUTSIDE_TRADING_WINDOW as REASON_OUTSIDE_TRADING_WINDOW
from app.decision_codes import REASON_POSITION_SIZE_ZERO as REASON_POSITION_SIZE_ZERO
from app.decision_codes import REASON_PRICE_TOO_EXTENDED as REASON_PRICE_TOO_EXTENDED
from app.decision_codes import REASON_RISK_TOO_HIGH as REASON_RISK_TOO_HIGH
from app.decision_codes import REASON_SETUP_NOT_CONFIRMED as REASON_SETUP_NOT_CONFIRMED
from app.decision_codes import REASON_SPREAD_TOO_WIDE as REASON_SPREAD_TOO_WIDE
from app.decision_codes import REASON_STALE_DATA as REASON_STALE_DATA
from app.decision_codes import REASON_STOP_INVALID as REASON_STOP_INVALID
from app.decision_codes import REASON_SUPPORT_BROKEN as REASON_SUPPORT_BROKEN
from app.decision_codes import REASON_TOO_LATE as REASON_TOO_LATE
from app.decision_codes import REASON_VOLUME_INSUFFICIENT as REASON_VOLUME_INSUFFICIENT
from app.decision_codes import REASON_WAITING_FOR_RETEST as REASON_WAITING_FOR_RETEST
from app.decision_codes import STATUS_ARMED as STATUS_ARMED
from app.decision_codes import STATUS_EXPIRED as STATUS_EXPIRED
from app.decision_codes import STATUS_GO as STATUS_GO
from app.decision_codes import STATUS_INVALIDATED as STATUS_INVALIDATED
from app.decision_codes import STATUS_NO_GO as STATUS_NO_GO
from app.decision_codes import STATUS_PAUSED as STATUS_PAUSED
from app.decision_codes import STATUS_WAIT as STATUS_WAIT
from app.models import MarketSnapshot, SetupSignal, SignalAction, utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.market_hours import US_EQUITY_TIMEZONE, coerce_datetime

CIRCUIT_BREAKER_STATE_KEY = "trade_guards_circuit_breakers"
HALT_STATE_KEY_PREFIX = "halt_state_"

TRADE_GUARD_BLOCKING_PREFIX = "TRADE_GUARD_"


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """A blocking decision expressed with the canonical vocabulary."""

    status: str
    reason_code: str
    decision_status: str
    title: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "decision_status": self.decision_status,
            "title": self.title,
            "message": self.message,
            "context": self.context,
        }


def _ny_trading_day(now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    return moment.astimezone(US_EQUITY_TIMEZONE).date().isoformat()


class CircuitBreakerTracker:
    """Daily kill switch state (skills.md section 34.3).

    Counters roll over only when the New York trading day changes; a tripped
    breaker therefore stays tripped until the next session even across
    restarts.
    """

    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings if isinstance(settings, dict) else {}

    # -- configuration -------------------------------------------------
    def config(self) -> dict[str, Any]:
        guards = _mapping(self.settings.get("trade_guards"))
        return _mapping(guards.get("circuit_breakers"))

    def max_daily_loss_usd(self) -> float:
        config = self.config()
        explicit = _number(config.get("max_daily_loss_usd"))
        if explicit is not None:
            return abs(explicit)
        max_daily_loss_r = _number(config.get("max_daily_loss_R"), 3.0) or 3.0
        return abs(max_daily_loss_r * self._risk_unit_usd())

    def _risk_unit_usd(self) -> float:
        risk = _mapping(self.settings.get("risk"))
        return abs(_number(risk.get("max_risk_per_trade_usd"), 15.0) or 15.0)

    # -- state ----------------------------------------------------------
    def state(self, now: datetime | None = None) -> dict[str, Any]:
        stored = self.repository.get_bot_state(CIRCUIT_BREAKER_STATE_KEY, {})
        if not isinstance(stored, dict):
            stored = {}
        return self._rolled(stored, now)

    def _rolled(self, stored: dict[str, Any], now: datetime | None) -> dict[str, Any]:
        today = _ny_trading_day(now)
        if stored.get("trading_day") == today:
            state = deepcopy(stored)
            state.setdefault("last_loss_exit_by_symbol", {})
            state.setdefault("recent_day_trades", [])
            return state
        recent = stored.get("recent_day_trades")
        recent = list(recent) if isinstance(recent, list) else []
        previous_day = stored.get("trading_day")
        previous_trades = int(_number(stored.get("trades_opened"), 0) or 0)
        if previous_day and previous_trades > 0:
            recent.append({"day": previous_day, "count": previous_trades})
        return {
            "trading_day": today,
            "trades_opened": 0,
            "consecutive_losses": 0,
            "realized_pnl_usd": 0.0,
            "wins": 0,
            "losses": 0,
            "last_loss_exit_by_symbol": {},
            "tripped": None,
            "recent_day_trades": recent[-5:],
            "updated_at": utc_now_iso(),
        }

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = utc_now_iso()
        self.repository.set_bot_state(CIRCUIT_BREAKER_STATE_KEY, state)
        return state

    # -- recording hooks -------------------------------------------------
    def record_entry_submitted(
        self,
        symbol: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        state = self.state(now)
        state["trades_opened"] = int(_number(state.get("trades_opened"), 0) or 0) + 1
        self._maybe_trip(state, now)
        return self._save(state)

    def record_position_closed(
        self,
        symbol: str,
        realized_pnl_usd: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        state = self.state(now)
        pnl = _number(realized_pnl_usd, 0.0) or 0.0
        state["realized_pnl_usd"] = round(
            (_number(state.get("realized_pnl_usd"), 0.0) or 0.0) + pnl, 2
        )
        if pnl < 0:
            state["losses"] = int(_number(state.get("losses"), 0) or 0) + 1
            state["consecutive_losses"] = int(_number(state.get("consecutive_losses"), 0) or 0) + 1
            exits = state.setdefault("last_loss_exit_by_symbol", {})
            if isinstance(exits, dict):
                exits[symbol.upper()] = (now or datetime.now(UTC)).isoformat()
        else:
            state["wins"] = int(_number(state.get("wins"), 0) or 0) + 1
            state["consecutive_losses"] = 0
        self._maybe_trip(state, now)
        return self._save(state)

    def _maybe_trip(self, state: dict[str, Any], now: datetime | None) -> None:
        if state.get("tripped"):
            return
        config = self.config()
        if config.get("enabled", True) is False:
            return
        realized = _number(state.get("realized_pnl_usd"), 0.0) or 0.0
        max_loss = self.max_daily_loss_usd()
        max_consecutive = int(_number(config.get("max_consecutive_losses"), 3) or 3)
        max_trades = int(_number(config.get("max_trades_per_day"), 5) or 5)
        tripped: dict[str, Any] | None = None
        if max_loss > 0 and realized <= -max_loss:
            tripped = {
                "reason_code": REASON_DAILY_LOSS_LIMIT,
                "detail": f"Realized daily loss {realized:.2f} USD <= -{max_loss:.2f} USD",
            }
        elif 0 < max_consecutive <= int(_number(state.get("consecutive_losses"), 0) or 0):
            tripped = {
                "reason_code": REASON_DAILY_LOSS_LIMIT,
                "detail": (
                    f"{state.get('consecutive_losses')} consecutive losing trades"
                    f" (max {max_consecutive})"
                ),
            }
        elif 0 < max_trades <= int(_number(state.get("trades_opened"), 0) or 0):
            tripped = {
                "reason_code": REASON_MAX_TRADES_REACHED,
                "detail": (f"{state.get('trades_opened')} trades opened today (max {max_trades})"),
            }
        if tripped is not None:
            tripped["at"] = (now or datetime.now(UTC)).isoformat()
            state["tripped"] = tripped

    # -- gates -----------------------------------------------------------
    def breaker_verdict(self, now: datetime | None = None) -> GuardVerdict | None:
        config = self.config()
        if config.get("enabled", True) is False:
            return None
        state = self.state(now)
        # Trip conditions can be crossed by settings changes too, so
        # re-evaluate before answering.
        self._maybe_trip(state, now)
        tripped = state.get("tripped")
        if not isinstance(tripped, dict):
            return None
        reason_code = str(tripped.get("reason_code") or REASON_DAILY_LOSS_LIMIT)
        return GuardVerdict(
            status=STATUS_PAUSED,
            reason_code=reason_code,
            decision_status="CIRCUIT_BREAKER_ACTIVE",
            title="Circuit breaker journalier actif",
            message=(
                "Le kill switch journalier est declenche: "
                f"{tripped.get('detail')}. Aucune nouvelle entree avant la "
                "prochaine session (pas de reset intraday)."
            ),
            context={
                "tripped": tripped,
                "trading_day": state.get("trading_day"),
                "trades_opened": state.get("trades_opened"),
                "consecutive_losses": state.get("consecutive_losses"),
                "realized_pnl_usd": state.get("realized_pnl_usd"),
                "max_daily_loss_usd": self.max_daily_loss_usd(),
            },
        )

    def cooldown_verdict(
        self,
        symbol: str,
        now: datetime | None = None,
    ) -> GuardVerdict | None:
        config = self.config()
        if config.get("enabled", True) is False:
            return None
        cooldown_minutes = _number(config.get("cooldown_after_stop_minutes"), 30) or 0
        if cooldown_minutes <= 0:
            return None
        state = self.state(now)
        exits = state.get("last_loss_exit_by_symbol")
        if not isinstance(exits, dict):
            return None
        stamp = coerce_datetime(exits.get(symbol.upper()))
        if stamp is None:
            return None
        moment = now or datetime.now(UTC)
        elapsed = moment - stamp
        remaining = timedelta(minutes=cooldown_minutes) - elapsed
        if remaining.total_seconds() <= 0:
            return None
        return GuardVerdict(
            status=STATUS_WAIT,
            reason_code=REASON_COOLDOWN_AFTER_STOP,
            decision_status="COOLDOWN_AFTER_STOP",
            title="Cooldown apres stop",
            message=(
                f"Un stop vient d'etre touche sur {symbol.upper()}. "
                f"Pas de re-entree avant {int(cooldown_minutes)} minutes "
                f"(reste {int(remaining.total_seconds() // 60) + 1} min)."
            ),
            context={
                "symbol": symbol.upper(),
                "cooldown_after_stop_minutes": cooldown_minutes,
                "last_loss_exit_at": exits.get(symbol.upper()),
            },
        )

    def pdt_verdict(self, now: datetime | None = None) -> GuardVerdict | None:
        guards = _mapping(self.settings.get("trade_guards"))
        config = _mapping(guards.get("pdt"))
        if config.get("enabled", False) is not True:
            return None
        max_day_trades = int(_number(config.get("max_day_trades_per_5_days"), 3) or 3)
        state = self.state(now)
        recent = state.get("recent_day_trades")
        recent = recent if isinstance(recent, list) else []
        # Conservative approximation: every entry opened counts as a
        # potential day trade (skills.md 25ter.3 asks to simulate the
        # constraint even in paper).
        used = int(_number(state.get("trades_opened"), 0) or 0)
        for item in recent[-4:]:
            if isinstance(item, dict):
                used += int(_number(item.get("count"), 0) or 0)
        if used < max_day_trades:
            return None
        return GuardVerdict(
            status=STATUS_NO_GO,
            reason_code=REASON_MAX_TRADES_REACHED,
            decision_status="PDT_LIMIT_REACHED",
            title="Limite PDT atteinte",
            message=(
                f"{used} day trades sur les 5 derniers jours ouvres "
                f"(max {max_day_trades}). Regle PDT: aucune nouvelle entree."
            ),
            context={
                "day_trades_used": used,
                "max_day_trades_per_5_days": max_day_trades,
            },
        )


class TradeGuardsService:
    """Ordered system gates from skills.md section 29.

    ``evaluate_entry`` returns the first blocking :class:`GuardVerdict`, or
    ``None`` when every system gate passes. Setup-level gates (anti-chase,
    volume, stop validity, ...) remain the responsibility of the setup
    strategies and the risk engine.
    """

    def __init__(
        self,
        repository: TradingRepository,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings if isinstance(settings, dict) else {}
        self.circuit_breakers = CircuitBreakerTracker(repository, self.settings)

    def _config(self) -> dict[str, Any]:
        return _mapping(self.settings.get("trade_guards"))

    def enabled(self) -> bool:
        config = self._config()
        if not config:
            return False
        return config.get("enabled", True) is not False

    # -- recording hooks -------------------------------------------------
    def record_entry_submitted(self, symbol: str) -> None:
        self.circuit_breakers.record_entry_submitted(symbol)

    def record_position_closed(self, symbol: str, realized_pnl_usd: float) -> None:
        self.circuit_breakers.record_position_closed(symbol, realized_pnl_usd)

    # -- halt state -------------------------------------------------------
    def set_halt_state(self, symbol: str, *, halted: bool) -> None:
        key = f"{HALT_STATE_KEY_PREFIX}{symbol.upper()}"
        previous = self.repository.get_bot_state(key, {})
        resumed_at = None
        if not halted and isinstance(previous, dict) and previous.get("halted"):
            resumed_at = utc_now_iso()
        elif isinstance(previous, dict):
            resumed_at = previous.get("resumed_at")
        self.repository.set_bot_state(
            key,
            {"halted": bool(halted), "resumed_at": resumed_at, "updated_at": utc_now_iso()},
        )

    def _halt_verdict(
        self,
        symbol: str,
        now: datetime | None = None,
    ) -> GuardVerdict | None:
        config = _mapping(self._config().get("halt"))
        if config.get("enabled", True) is False:
            return None
        state = self.repository.get_bot_state(f"{HALT_STATE_KEY_PREFIX}{symbol.upper()}", {})
        if not isinstance(state, dict):
            return None
        if state.get("halted") is True:
            return GuardVerdict(
                status=STATUS_PAUSED,
                reason_code=REASON_HALT_ACTIVE,
                decision_status="HALT_ACTIVE",
                title="Titre halte (LULD)",
                message=(
                    f"{symbol.upper()} est halte. Aucun ordre envoye ou modifie "
                    "tant que le halt est actif."
                ),
                context={"halt_state": state},
            )
        resumed_at = coerce_datetime(state.get("resumed_at"))
        cooldown_minutes = _number(config.get("resume_cooldown_minutes"), 5) or 0
        if resumed_at is not None and cooldown_minutes > 0:
            moment = now or datetime.now(UTC)
            if moment - resumed_at < timedelta(minutes=cooldown_minutes):
                return GuardVerdict(
                    status=STATUS_WAIT,
                    reason_code=REASON_HALT_ACTIVE,
                    decision_status="HALT_RESUME_COOLDOWN",
                    title="Reprise apres halt - revalidation requise",
                    message=(
                        f"{symbol.upper()} vient de reprendre apres un halt. "
                        f"Attente de {int(cooldown_minutes)} minutes (au moins une "
                        "bougie 5m complete) avant toute decision."
                    ),
                    context={"halt_state": state},
                )
        return None

    # -- exposure ----------------------------------------------------------
    def _exposure_verdict(
        self,
        symbol: str,
        setup: dict[str, Any] | None,
    ) -> GuardVerdict | None:
        config = _mapping(self._config().get("exposure"))
        if config.get("enabled", True) is False:
            return None
        positions = [
            position
            for position in self.repository.list_positions()
            if int(_number(position.get("quantity"), 0) or 0) > 0
            and str(position.get("status") or "OPEN").upper() != "CLOSED"
        ]
        normalized = symbol.upper()

        if config.get("block_if_position_on_same_symbol", True) is not False:
            for position in positions:
                if str(position.get("symbol") or "").upper() == normalized:
                    return GuardVerdict(
                        status=STATUS_NO_GO,
                        reason_code=REASON_CONFLICT_WITH_OPEN_POSITION,
                        decision_status="CONFLICT_WITH_OPEN_POSITION",
                        title="Position deja ouverte sur ce titre",
                        message=(
                            f"Une position est deja ouverte sur {normalized}. "
                            "Pas d'empilement sur le meme titre."
                        ),
                        context={"symbol": normalized},
                    )

        max_open = int(_number(config.get("max_open_positions"), 0) or 0)
        if max_open > 0 and len(positions) >= max_open:
            return GuardVerdict(
                status=STATUS_NO_GO,
                reason_code=REASON_EXPOSURE_LIMIT,
                decision_status="EXPOSURE_LIMIT",
                title="Nombre maximal de positions atteint",
                message=(
                    f"{len(positions)} positions ouvertes (max {max_open}). "
                    "Aucune nouvelle entree autorisee."
                ),
                context={"open_positions": len(positions), "max_open_positions": max_open},
            )

        risk_unit = abs(
            _number(_mapping(self.settings.get("risk")).get("max_risk_per_trade_usd"), 15.0) or 15.0
        )
        max_open_risk_r = _number(config.get("max_total_open_risk_R"), 0.0) or 0.0
        if max_open_risk_r > 0:
            open_risk = sum(
                abs(_number(position.get("risk_remaining"), 0.0) or 0.0) for position in positions
            )
            candidate_risk = self._candidate_risk_usd(setup, risk_unit)
            max_open_risk_usd = max_open_risk_r * risk_unit
            if open_risk + candidate_risk > max_open_risk_usd:
                return GuardVerdict(
                    status=STATUS_NO_GO,
                    reason_code=REASON_EXPOSURE_LIMIT,
                    decision_status="EXPOSURE_LIMIT",
                    title="Risque ouvert total trop eleve",
                    message=(
                        f"Risque ouvert {open_risk:.2f} USD + nouveau trade "
                        f"{candidate_risk:.2f} USD depasse la limite de "
                        f"{max_open_risk_usd:.2f} USD ({max_open_risk_r:g}R)."
                    ),
                    context={
                        "open_risk_usd": round(open_risk, 2),
                        "candidate_risk_usd": round(candidate_risk, 2),
                        "max_total_open_risk_R": max_open_risk_r,
                        "risk_unit_usd": risk_unit,
                    },
                )

        max_same_sector = int(_number(config.get("max_positions_same_sector"), 0) or 0)
        candidate_sector = self._sector_for_setup(setup)
        if max_same_sector > 0 and candidate_sector:
            same_sector = 0
            for position in positions:
                sector = self._sector_for_setup_id(str(position.get("setup_id") or ""))
                if sector and sector == candidate_sector:
                    same_sector += 1
            if same_sector >= max_same_sector:
                return GuardVerdict(
                    status=STATUS_NO_GO,
                    reason_code=REASON_EXPOSURE_LIMIT,
                    decision_status="EXPOSURE_LIMIT",
                    title="Trop de positions dans le meme secteur",
                    message=(
                        f"{same_sector} positions deja ouvertes dans le secteur "
                        f"{candidate_sector} (max {max_same_sector})."
                    ),
                    context={
                        "sector": candidate_sector,
                        "same_sector_positions": same_sector,
                        "max_positions_same_sector": max_same_sector,
                    },
                )

        groups = config.get("correlated_groups")
        if isinstance(groups, list):
            open_symbols = {str(position.get("symbol") or "").upper() for position in positions}
            for group in groups:
                if not isinstance(group, list):
                    continue
                members = {str(item).upper() for item in group if item not in (None, "")}
                if normalized not in members:
                    continue
                correlated_open = sorted(open_symbols & (members - {normalized}))
                if correlated_open:
                    return GuardVerdict(
                        status=STATUS_NO_GO,
                        reason_code=REASON_CONFLICT_WITH_OPEN_POSITION,
                        decision_status="CONFLICT_WITH_OPEN_POSITION",
                        title="Titre correle a une position ouverte",
                        message=(
                            f"{normalized} est correle a des positions deja "
                            f"ouvertes ({', '.join(correlated_open)}). Des titres "
                            "correles comptent comme une seule position."
                        ),
                        context={
                            "symbol": normalized,
                            "correlated_open_positions": correlated_open,
                        },
                    )
        return None

    def _candidate_risk_usd(self, setup: dict[str, Any] | None, risk_unit: float) -> float:
        config = setup.get("config") if isinstance(setup, dict) else None
        if isinstance(config, dict):
            risk = _mapping(config.get("risk"))
            value = _number(risk.get("max_risk_usd"))
            if value is not None and value > 0:
                return abs(value)
        return risk_unit

    def _sector_for_setup(self, setup: dict[str, Any] | None) -> str:
        if not isinstance(setup, dict):
            return ""
        raw_config = setup.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else setup
        for section in ("market_context", "fundamental_context", "technical_context"):
            sector = _mapping(config.get(section)).get("sector")
            if sector not in (None, "", "unknown"):
                return str(sector).strip().upper()
        sector = config.get("sector")
        if sector not in (None, "", "unknown"):
            return str(sector).strip().upper()
        return ""

    def _sector_for_setup_id(self, setup_id: str) -> str:
        if not setup_id:
            return ""
        try:
            setup = self.repository.get_setup(setup_id)
        except Exception:
            return ""
        return self._sector_for_setup(setup if isinstance(setup, dict) else None)

    # -- main gate ----------------------------------------------------------
    def evaluate_entry(
        self,
        symbol: str,
        *,
        setup: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> GuardVerdict | None:
        if not self.enabled():
            return None
        verdict = self._halt_verdict(symbol, now)
        if verdict is not None:
            return verdict
        verdict = self.circuit_breakers.breaker_verdict(now)
        if verdict is not None:
            return verdict
        verdict = self.circuit_breakers.pdt_verdict(now)
        if verdict is not None:
            return verdict
        verdict = self.circuit_breakers.cooldown_verdict(symbol, now)
        if verdict is not None:
            return verdict
        return self._exposure_verdict(symbol, setup)


def blocked_signal_from_verdict(
    signal: SetupSignal,
    verdict: GuardVerdict,
) -> SetupSignal:
    """Convert an ENTRY_READY signal into a HOLD signal carrying the verdict.

    Mirrors the shape produced by ``session_policy._blocked_signal`` so the
    entry-decision layer and the GUI render trade-guard blocks the same way.
    """

    metadata = deepcopy(signal.metadata) if isinstance(signal.metadata, dict) else {}
    analysis = metadata.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
        metadata["analysis"] = analysis
    blocking = analysis.get("blocking_conditions")
    blocking = [str(item) for item in blocking if item] if isinstance(blocking, list) else []
    blocking_code = f"{TRADE_GUARD_BLOCKING_PREFIX}{verdict.reason_code}"
    analysis.update(
        {
            "decision_status": verdict.decision_status,
            "decision": "NO_ENTRY",
            "next_action": "WAIT_NEXT_SESSION" if verdict.status == STATUS_PAUSED else "WAIT",
            "display_title": verdict.title,
            "display_message": verdict.message,
            "readiness_label": "BLOCKED" if verdict.status == STATUS_NO_GO else "WAITING",
            "blocking_conditions": list(dict.fromkeys([blocking_code, *blocking])),
            "skills_decision": {
                "status": verdict.status,
                "reason_code": verdict.reason_code,
            },
            "trade_guards": verdict.as_payload(),
        }
    )
    return SetupSignal(
        action=SignalAction.HOLD,
        reason=f"{verdict.decision_status}: {verdict.message}",
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        metadata=metadata,
    )


def snapshot_symbol(snapshot: MarketSnapshot | None, fallback: str = "") -> str:
    if snapshot is not None and snapshot.symbol:
        return str(snapshot.symbol).upper()
    return fallback.upper()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
