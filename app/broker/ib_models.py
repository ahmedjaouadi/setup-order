from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BrokerOrderRequest:
    client_order_id: str
    setup_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    trigger_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    parent_id: str | None = None
    oca_group: str | None = None
    transmit: bool = True
    status: str = ""
    raw_status: str = ""
    broker_status: str = ""
    broker_order_id: str | None = None
    broker_perm_id: str | None = None
    filled_quantity: float | None = None
    remaining_quantity: float | None = None


@dataclass(slots=True)
class BrokerOrderResult:
    accepted: bool
    status: str
    broker_order_id: str | None = None
    broker_perm_id: str | None = None
    reason: str = ""


@dataclass(slots=True)
class BrokerPosition:
    symbol: str
    quantity: int
    average_price: float
    current_price: float
    market_price: float | None = None
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    daily_pnl: float | None = None


@dataclass(slots=True)
class BrokerExecution:
    execution_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    order_id: str | None = None
    broker_perm_id: str | None = None
    timestamp: str | None = None
