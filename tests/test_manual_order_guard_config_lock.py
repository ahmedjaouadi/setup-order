from __future__ import annotations

import unittest

from app.settings import load_settings


class ManualBuyPositionGuardConfigLockTests(unittest.TestCase):
    """Audit 32/S4: the manual BUY path (manual_order_service.py) has no
    current_status/setup_id to gate on -- a fresh setup_id is minted on
    every call, so protection_snapshot_for_setup/DuplicateOrderError never
    sees a prior manual order on the same symbol.

    The ONLY thing that stops a manual BUY from stacking on a symbol
    already held is trade_guards._exposure_verdict's
    block_if_position_on_same_symbol rule (trade_guards.py:454-467), which
    only fires when trade_guards.enabled and trade_guards.exposure.enabled
    are also true. All three are plain configuration booleans, not code
    invariants -- flipping any one of them to False in config.yaml or
    DEFAULT_CONFIG silently removes the only barrier against manual
    position stacking, with no other check catching it (audit 32 Q1/Q2).

    This test loads the configuration exactly as the running application
    would (via load_settings(), which merges config.yaml over
    DEFAULT_CONFIG) and fails loudly if any of the three switches is off.
    """

    def test_symbol_level_exposure_guard_is_enabled_in_loaded_config(self) -> None:
        settings = load_settings()
        trade_guards = settings.raw.get("trade_guards", {})
        exposure = trade_guards.get("exposure", {})

        self.assertIs(
            trade_guards.get("enabled"),
            True,
            "trade_guards.enabled must be True: it is the master switch for "
            "every system gate, including the manual buy same-symbol guard.",
        )
        self.assertIs(
            exposure.get("enabled"),
            True,
            "trade_guards.exposure.enabled must be True: it is the section "
            "that carries block_if_position_on_same_symbol.",
        )
        self.assertIs(
            exposure.get("block_if_position_on_same_symbol"),
            True,
            "trade_guards.exposure.block_if_position_on_same_symbol must be "
            "True: this is the only rule that blocks a manual BUY on a "
            "symbol already in an open position (audit 32).",
        )


if __name__ == "__main__":
    unittest.main()
