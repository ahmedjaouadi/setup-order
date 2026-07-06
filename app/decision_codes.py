"""Canonical ``status`` + ``reason_code`` vocabulary (docs/skills.md section 2.5).

This is the single source of truth for the decision codes shared by the
execution engine (``app/engine/trade_guards.py``) and the strictly-consultative
detection pipeline (the opportunity scanner and its data-quality gate).

It is a **pure** module: no I/O, no engine imports, no order-execution path.
Detection modules can therefore import these constants without coupling to the
order manager (see ``tests/test_techniques_security.py``): the codes are plain
strings, they carry no behaviour.
"""

from __future__ import annotations

# --- Canonical decision statuses (skills.md section 2.5, closed enum) ---
# ``OK`` is the "nothing to report" verdict used by consultative gates that do
# not drive the setup state machine (e.g. the scanner data-quality gate); the
# engine's setup lifecycle uses the remaining, ordered statuses.
STATUS_OK = "OK"
STATUS_GO = "GO"
STATUS_ARMED = "ARMED"
STATUS_WAIT = "WAIT"
STATUS_NO_GO = "NO_GO"
STATUS_INVALIDATED = "INVALIDATED"
STATUS_EXPIRED = "EXPIRED"
STATUS_PAUSED = "PAUSED"

CANONICAL_STATUSES = frozenset(
    {
        STATUS_GO,
        STATUS_ARMED,
        STATUS_WAIT,
        STATUS_NO_GO,
        STATUS_INVALIDATED,
        STATUS_EXPIRED,
        STATUS_PAUSED,
    }
)

# --- Canonical reason codes (skills.md section 2.5, extensible) ---
REASON_TOO_LATE = "TOO_LATE"
REASON_PRICE_TOO_EXTENDED = "PRICE_TOO_EXTENDED"
REASON_SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
REASON_MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
REASON_STALE_DATA = "STALE_DATA"
REASON_MARKET_CONTEXT_BAD = "MARKET_CONTEXT_BAD"
REASON_STOP_INVALID = "STOP_INVALID"
REASON_RISK_TOO_HIGH = "RISK_TOO_HIGH"
REASON_POSITION_SIZE_ZERO = "POSITION_SIZE_ZERO"
REASON_VOLUME_INSUFFICIENT = "VOLUME_INSUFFICIENT"
REASON_SUPPORT_BROKEN = "SUPPORT_BROKEN"
REASON_BREAKOUT_REJECTED = "BREAKOUT_REJECTED"
REASON_EARNINGS_IMMINENT = "EARNINGS_IMMINENT"
REASON_HALT_ACTIVE = "HALT_ACTIVE"
REASON_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
REASON_MAX_TRADES_REACHED = "MAX_TRADES_REACHED"
REASON_EXPOSURE_LIMIT = "EXPOSURE_LIMIT"
REASON_CONFLICT_WITH_OPEN_POSITION = "CONFLICT_WITH_OPEN_POSITION"
REASON_SETUP_NOT_CONFIRMED = "SETUP_NOT_CONFIRMED"
REASON_WAITING_FOR_RETEST = "WAITING_FOR_RETEST"
# Extension codes (the reason-code list is explicitly extensible in 2.5).
REASON_COOLDOWN_AFTER_STOP = "COOLDOWN_AFTER_STOP"
REASON_OUTSIDE_TRADING_WINDOW = "OUTSIDE_TRADING_WINDOW"
