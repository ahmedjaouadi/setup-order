class BrokerError(RuntimeError):
    """Base broker connector error."""


class BrokerDisconnectedError(BrokerError):
    """Raised when an order operation is attempted without a connection."""


class BrokerRejectedOrderError(BrokerError):
    """Raised when a broker rejects an order request."""
