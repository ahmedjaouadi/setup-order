from __future__ import annotations

from app.models import SetupStatus


ALLOWED_TRANSITIONS: dict[SetupStatus, set[SetupStatus]] = {
    SetupStatus.DRAFT: {SetupStatus.LOADED, SetupStatus.CANCELLED},
    SetupStatus.LOADED: {SetupStatus.VALIDATED, SetupStatus.ERROR},
    SetupStatus.VALIDATED: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.DISABLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.DISABLED: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.CANCELLED,
    },
    SetupStatus.WAITING_ACTIVATION: {
        SetupStatus.WAITING_BREAKOUT,
        SetupStatus.MISSED_BREAKOUT,
        SetupStatus.WAITING_RETEST,
        SetupStatus.WAITING_REBOUND,
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_BREAKOUT: {
        SetupStatus.MISSED_BREAKOUT,
        SetupStatus.WAITING_RETEST,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.MISSED_BREAKOUT: {
        SetupStatus.WAITING_RETEST,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_RETEST: {
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.REARMED_ON_NEW_BASE: {
        SetupStatus.ENTRY_READY,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_REBOUND: {
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_CONFIRMATION: {
        SetupStatus.ENTRY_READY,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_ENTRY_SIGNAL: {
        SetupStatus.ENTRY_READY,
        SetupStatus.ENTRY_ORDER_PLACED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_READY: {
        SetupStatus.ENTRY_ORDER_PLACED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_ORDER_PLACED: {
        SetupStatus.ENTRY_PARTIALLY_FILLED,
        SetupStatus.ENTRY_FILLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_PARTIALLY_FILLED: {
        SetupStatus.ENTRY_FILLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.ENTRY_FILLED: {
        SetupStatus.STOP_ORDER_PLACED,
        SetupStatus.STOP_PLACED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.STOP_ORDER_PLACED: {
        SetupStatus.IN_POSITION,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.STOP_PLACED: {
        SetupStatus.IN_POSITION,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.RECONCILING_EXISTING_POSITION: {
        SetupStatus.IN_POSITION,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
        SetupStatus.CANCELLED,
    },
    SetupStatus.IN_POSITION: {
        SetupStatus.MANAGING_POSITION,
        SetupStatus.PARTIAL_EXIT,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.MANAGING_POSITION: {
        SetupStatus.PARTIAL_EXIT,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.PARTIAL_EXIT: {
        SetupStatus.MANAGING_POSITION,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
    },
    SetupStatus.CLOSED: set(),
    SetupStatus.EXPIRED: set(),
    SetupStatus.INVALIDATED: set(),
    SetupStatus.CANCELLED: set(),
    SetupStatus.MANUAL_REVIEW_REQUIRED: {
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
    },
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW: {
        SetupStatus.CANCELLED,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.ERROR: {SetupStatus.CANCELLED, SetupStatus.MANUAL_REVIEW_REQUIRED},
}


class InvalidTransitionError(ValueError):
    pass


class StateMachine:
    def can_transition(self, current: SetupStatus, target: SetupStatus) -> bool:
        if current == target:
            return True
        return target in ALLOWED_TRANSITIONS.get(current, set())

    def transition(self, current: SetupStatus, target: SetupStatus) -> SetupStatus:
        if not self.can_transition(current, target):
            raise InvalidTransitionError(
                f"Invalid setup transition: {current.value} -> {target.value}"
            )
        return target
