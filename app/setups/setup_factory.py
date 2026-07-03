from __future__ import annotations

from typing import Any

from app.setups.aggressive_rebound import AggressiveReboundSetup
from app.setups.base_setup import BaseSetup
from app.setups.breakout_retest import BreakoutRetestSetup
from app.setups.momentum_breakout import MomentumBreakoutSetup
from app.setups.position_management import PositionManagementSetup
from app.setups.pullback_continuation import PullbackContinuationSetup
from app.setups.range_breakout import RangeBreakoutSetup
from app.setups.trailing_runner import RunnerSetup, TrailingRunnerSetup


class UnknownSetupTypeError(ValueError):
    pass


class SetupFactory:
    _registry: dict[str, type[BaseSetup]] = {
        AggressiveReboundSetup.setup_type: AggressiveReboundSetup,
        BreakoutRetestSetup.setup_type: BreakoutRetestSetup,
        PullbackContinuationSetup.setup_type: PullbackContinuationSetup,
        MomentumBreakoutSetup.setup_type: MomentumBreakoutSetup,
        PositionManagementSetup.setup_type: PositionManagementSetup,
        RangeBreakoutSetup.setup_type: RangeBreakoutSetup,
        RunnerSetup.setup_type: RunnerSetup,
        TrailingRunnerSetup.setup_type: TrailingRunnerSetup,
    }

    @classmethod
    def create(cls, setup_config: dict[str, Any]) -> BaseSetup:
        setup_type = setup_config.get("setup_type")
        strategy_class = cls._registry.get(str(setup_type))
        if strategy_class is None:
            raise UnknownSetupTypeError(f"Unknown setup type: {setup_type}")
        return strategy_class(setup_config)

    @classmethod
    def supported_types(cls) -> list[str]:
        return sorted(cls._registry)
