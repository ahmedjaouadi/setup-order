from __future__ import annotations

from app.broker.ib_models import BrokerOrderRequest
from app.models import OrderRecord


def order_record_to_broker_request(order: OrderRecord) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=order.id,
        setup_id=order.setup_id,
        symbol=order.symbol,
        side=order.side,
        order_type=order.order_type,
        quantity=order.quantity,
        trigger_price=order.trigger_price,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        parent_id=order.parent_id,
        oca_group=order.oca_group,
    )

