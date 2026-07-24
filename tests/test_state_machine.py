from __future__ import annotations

import unittest

from app.engine.state_machine import InvalidTransitionError, StateMachine
from app.models import SetupRole, SetupStatus


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = StateMachine()

    def test_allows_expected_entry_flow(self) -> None:
        status = self.machine.transition(
            SetupStatus.WAITING_ACTIVATION,
            SetupStatus.WAITING_ENTRY_SIGNAL,
        )
        status = self.machine.transition(status, SetupStatus.ENTRY_ORDER_PLACED)
        status = self.machine.transition(status, SetupStatus.ENTRY_FILLED)
        status = self.machine.transition(status, SetupStatus.STOP_ORDER_PLACED)
        status = self.machine.transition(status, SetupStatus.IN_POSITION)

        self.assertEqual(status, SetupStatus.IN_POSITION)

    def test_allows_entry_filled_directly_to_in_position(self) -> None:
        # Real bracket path (audit 28 / S2): the protective stop is already
        # active at fill time, so STOP_ORDER_PLACED is never visited.
        self.assertTrue(
            self.machine.can_transition(
                SetupStatus.ENTRY_FILLED,
                SetupStatus.IN_POSITION,
            )
        )

    def test_allows_real_bracket_flow_end_to_end(self) -> None:
        status = self.machine.transition(
            SetupStatus.ENTRY_READY,
            SetupStatus.ENTRY_ORDER_PLACED,
        )
        status = self.machine.transition(status, SetupStatus.ENTRY_FILLED)
        status = self.machine.transition(status, SetupStatus.IN_POSITION)

        self.assertEqual(status, SetupStatus.IN_POSITION)

    def test_allows_management_only_reconciliation_flow(self) -> None:
        status = self.machine.transition(
            SetupStatus.RECONCILING_EXISTING_POSITION,
            SetupStatus.IN_POSITION,
        )

        self.assertEqual(status, SetupStatus.IN_POSITION)

    def test_allows_missed_breakout_retest_rearm_flow(self) -> None:
        status = self.machine.transition(
            SetupStatus.WAITING_ACTIVATION,
            SetupStatus.MISSED_BREAKOUT,
        )
        status = self.machine.transition(status, SetupStatus.WAITING_RETEST)
        status = self.machine.transition(status, SetupStatus.REARMED_ON_NEW_BASE)
        status = self.machine.transition(status, SetupStatus.ENTRY_READY)

        self.assertEqual(status, SetupStatus.ENTRY_READY)

    def test_rejects_closed_to_in_position(self) -> None:
        with self.assertRaises(InvalidTransitionError):
            self.machine.transition(SetupStatus.CLOSED, SetupStatus.IN_POSITION)

    def test_rejects_management_only_entry_flow_transition(self) -> None:
        with self.assertRaises(InvalidTransitionError):
            self.machine.transition(
                SetupStatus.RECONCILING_EXISTING_POSITION,
                SetupStatus.ENTRY_READY,
                SetupRole.MANAGEMENT_ONLY,
            )

    def test_explains_transition_decision(self) -> None:
        decision = self.machine.explain_transition(
            SetupStatus.CLOSED,
            SetupStatus.IN_POSITION,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("Invalid setup transition", decision.reason)


if __name__ == "__main__":
    unittest.main()
