from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SerializableEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BotMode(SerializableEnum):
    PAPER = "paper"
    LIVE = "live"


class ConnectionStatus(SerializableEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    ERROR = "ERROR"


class BotStatus(SerializableEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ERROR = "ERROR"


class SetupStatus(SerializableEnum):
    DRAFT = "DRAFT"
    LOADED = "LOADED"
    VALIDATED = "VALIDATED"
    DISABLED = "DISABLED"
    WAITING_ACTIVATION = "WAITING_ACTIVATION"
    WAITING_BREAKOUT = "WAITING_BREAKOUT"
    MISSED_BREAKOUT = "MISSED_BREAKOUT"
    MISSED_BREAKOUT_WAIT_RETEST = "MISSED_BREAKOUT_WAIT_RETEST"
    STALE_SETUP = "STALE_SETUP"
    BLOCKED = "BLOCKED"
    WAITING_RETEST = "WAITING_RETEST"
    WAITING_REBOUND = "WAITING_REBOUND"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    REARMED_ON_NEW_BASE = "REARMED_ON_NEW_BASE"
    WAITING_ENTRY_SIGNAL = "WAITING_ENTRY_SIGNAL"
    ENTRY_READY = "ENTRY_READY"
    ENTRY_ORDER_PLACED = "ENTRY_ORDER_PLACED"
    ENTRY_PARTIALLY_FILLED = "ENTRY_PARTIALLY_FILLED"
    ENTRY_FILLED = "ENTRY_FILLED"
    STOP_ORDER_PLACED = "STOP_ORDER_PLACED"
    STOP_PLACED = "STOP_PLACED"
    RECONCILING_EXISTING_POSITION = "RECONCILING_EXISTING_POSITION"
    IN_POSITION = "IN_POSITION"
    MANAGING_POSITION = "MANAGING_POSITION"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    ERROR_REQUIRES_MANUAL_REVIEW = "ERROR_REQUIRES_MANUAL_REVIEW"
    ERROR = "ERROR"


class SetupType(SerializableEnum):
    AGGRESSIVE_REBOUND = "aggressive_rebound"
    BREAKOUT_RETEST = "breakout_retest"
    PULLBACK_CONTINUATION = "pullback_continuation"
    MOMENTUM_BREAKOUT = "momentum_breakout"
    RANGE_BREAKOUT = "range_breakout"
    RUNNER = "runner"
    TRAILING_RUNNER = "trailing_runner"
    POSITION_MANAGEMENT = "position_management"


class SetupRole(SerializableEnum):
    ENTRY_AND_MANAGEMENT = "ENTRY_AND_MANAGEMENT"
    ENTRY_ONLY = "ENTRY_ONLY"
    MANAGEMENT_ONLY = "MANAGEMENT_ONLY"


class SignalAction(SerializableEnum):
    HOLD = "HOLD"
    STATUS_CHANGE = "STATUS_CHANGE"
    ENTRY_READY = "ENTRY_READY"
    INVALIDATE = "INVALIDATE"
    RAISE_STOP = "RAISE_STOP"


class OrderSide(SerializableEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(SerializableEnum):
    MKT = "MKT"
    LMT = "LMT"
    STP = "STP"
    STP_LMT = "STP_LMT"
    TRAIL = "TRAIL"


class OrderStatus(SerializableEnum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class EventLevel(SerializableEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    TRADE = "TRADE"
    ORDER = "ORDER"
    RISK = "RISK"
    SYNC = "SYNC"


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        warnings: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(valid=True, warnings=warnings or [], details=details or {})

    @classmethod
    def failed(
        cls,
        errors: list[str],
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(valid=False, errors=errors, details=details or {})


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    price: float
    timestamp: str = field(default_factory=utc_now_iso)
    timeframe: str = "15m"
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    spread_bps: float | None = None
    volume: float | None = None
    bar_volume_15m: float | None = None
    avg_volume_15m: float | None = None
    volume_ratio_15m: float | None = None
    current_bar_volume: float | None = None
    previous_high: float | None = None
    daily_close: float | None = None
    volume_ratio: float | None = None
    volume_ratio_closed_bar: float | None = None
    volume_ratio_live: float | None = None
    average_volume_ratio_last_2_bars: float | None = None
    volume_status: str = ""
    volume_timeframe: str = ""
    volume_comparison_mode: str = ""
    volume_sample_days: int | None = None
    volume_sample_count: int | None = None
    elapsed_ratio: float | None = None
    projected_volume: float | None = None
    bar_count: int | None = None
    bars_above_resistance: int | None = None
    minimum_tick: float | None = None
    atr_15m: float | None = None
    atr_1h: float | None = None
    atr_1h_status: str = ""
    atr_1h_bar_size: str = ""
    atr_1h_duration: str = ""
    atr_1h_use_rth: bool | None = None
    bars_required_for_atr: int | None = None
    historical_1h_available: bool | None = None
    historical_1h_error: str = ""
    last_successful_atr_1h: float | None = None
    last_successful_atr_1h_at: str | None = None
    atr_1h_age_seconds: float | None = None
    session: str | None = None
    market_open_time: str | None = None
    current_time: str | None = None
    last_confirmed_higher_low: float | None = None
    support_level: float | None = None
    successful_retest_low: float | None = None
    structural_support: float | None = None
    breakout_already_detected: bool = False
    new_higher_low_confirmed: bool = False
    close_1h: float | None = None
    market_data_source: str = ""
    live_quote_source: str = ""
    market_data_type_requested: int | float | None = None
    market_data_type_actual: int | float | None = None
    live_market_data_status: str = ""
    last_ibkr_error_code: int | None = None
    last_ibkr_error_message: str = ""
    bar_date: str = ""
    bars_15m_count: int | float | None = None
    bars_1h_count: int | float | None = None
    hybrid_signal_bar_size: str = ""
    hybrid_atr_1h_bar_size: str = ""
    hybrid_sources: dict[str, Any] = field(default_factory=dict)
    market_data_readiness: dict[str, Any] = field(default_factory=dict)
    historical_bars: list[dict[str, Any]] = field(default_factory=list)
    ema_20: float | None = None
    ema_50: float | None = None
    bullish_candle: bool = False


@dataclass(slots=True)
class SetupSignal:
    action: SignalAction
    reason: str
    target_status: SetupStatus | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    new_stop: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def hold(cls, reason: str = "No actionable signal") -> "SetupSignal":
        return cls(action=SignalAction.HOLD, reason=reason)


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    quantity: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    position_amount_usd: float = 0.0
    risk_amount_usd: float = 0.0
    trigger_price: float | None = None


@dataclass(slots=True)
class SetupRecord:
    setup_id: str
    symbol: str
    setup_type: str
    enabled: bool
    mode: str
    status: str
    entry_zone: str
    stop_loss: float | None
    risk_amount: float | None
    order_status: str
    position_status: str
    last_event: str
    config: dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class OrderRecord:
    id: str
    setup_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    status: str
    trigger_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    broker_order_id: str | None = None
    broker_perm_id: str | None = None
    parent_id: str | None = None
    oca_group: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class PositionRecord:
    symbol: str
    setup_id: str
    quantity: int
    average_price: float
    current_price: float
    unrealized_pnl: float
    current_stop: float | None
    risk_remaining: float
    status: str
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class EventRecord:
    timestamp: str
    level: str
    event_type: str
    message: str
    setup_id: str | None = None
    symbol: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value
