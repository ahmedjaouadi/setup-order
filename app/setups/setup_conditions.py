from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.models import MarketSnapshot, SetupStatus
from app.setups.base_setup import bullish_confirmation

# Chaque condition reflete un calcul reellement effectue par le evaluate() du
# setup correspondant (app/setups/*.py). Ne jamais ajouter ici une condition
# que le moteur ne verifie pas: la checklist doit rester fidele au moteur.
# Catalogue de reference et ecarts vs cible: docs/21-setup-conditions-catalog.md.


@dataclass(slots=True)
class ConditionCheck:
    met: bool | None
    observed_value: str = ""
    target: str = ""


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    condition_id: str
    label: str
    description: str
    evaluator: Callable[[ConditionContext], ConditionCheck]


@dataclass(frozen=True, slots=True)
class SetupConditionsDefinition:
    setup_type: str
    setup_name: str
    direction: str
    conditions: tuple[ConditionDefinition, ...]
    # Statuts du state machine qui garantissent que les N premieres conditions
    # sont acquises (la transition a deja ete decidee par le moteur).
    status_floors: dict[str, int] = field(default_factory=dict)
    # Fragment de raison d'invalidation moteur -> condition mise en echec.
    invalidation_map: tuple[tuple[str, str], ...] = ()

    def floor_for(self, status: SetupStatus | None) -> int:
        if status is None:
            return 0
        return int(self.status_floors.get(status.value, 0))

    def failed_condition_for(self, reason: str) -> str:
        lowered = (reason or "").lower()
        for fragment, condition_id in self.invalidation_map:
            if fragment.lower() in lowered:
                return condition_id
        return ""


@dataclass(slots=True)
class ConditionContext:
    config: dict[str, Any]
    snapshot: MarketSnapshot | None
    analysis: dict[str, Any]
    current_status: SetupStatus | None


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}" if number is not None else "?"


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def _analysis_dict(analysis: dict[str, Any], key: str) -> dict[str, Any]:
    value = analysis.get(key)
    return value if isinstance(value, dict) else {}


def _bullish_check(ctx: ConditionContext, target: str) -> ConditionCheck:
    if ctx.snapshot is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    met = bullish_confirmation(ctx.snapshot)
    close = ctx.snapshot.close if ctx.snapshot.close is not None else ctx.snapshot.price
    observed = f"Cloture {_fmt(close)} / ouverture {_fmt(ctx.snapshot.open)}"
    return ConditionCheck(met=met, observed_value=observed, target=target)


# --- pullback_continuation (app/setups/pullback_continuation.py) -------------


def _pullback_uptrend(ctx: ConditionContext) -> ConditionCheck:
    target = "EMA20 au-dessus de l'EMA50"
    snapshot = ctx.snapshot
    if snapshot is None or snapshot.ema_20 is None or snapshot.ema_50 is None:
        return ConditionCheck(met=None, observed_value="Donnees EMA indisponibles", target=target)
    observed = f"EMA20 {_fmt(snapshot.ema_20)} / EMA50 {_fmt(snapshot.ema_50)}"
    return ConditionCheck(met=snapshot.ema_20 > snapshot.ema_50, observed_value=observed, target=target)


def _pullback_to_ema20(ctx: ConditionContext) -> ConditionCheck:
    snapshot = ctx.snapshot
    if snapshot is None or snapshot.ema_20 is None:
        return ConditionCheck(
            met=None,
            observed_value="Donnees EMA indisponibles",
            target="Retour du prix sur l'EMA20",
        )
    target = f"Retour du prix sur l'EMA20 ({_fmt(snapshot.ema_20)})"
    observed = f"Prix {_fmt(snapshot.price)} / EMA20 {_fmt(snapshot.ema_20)}"
    return ConditionCheck(met=snapshot.price <= snapshot.ema_20, observed_value=observed, target=target)


# --- momentum_breakout (app/setups/momentum_breakout.py) ---------------------
# Les evaluateurs lisent metadata["analysis"] produit par le setup lui-meme:
# ce sont les valeurs exactes calculees par le moteur, jamais recalculees ici.


def _momentum_market_data(ctx: ConditionContext) -> ConditionCheck:
    target = "Bid/ask, ATR et spread disponibles"
    decision_status = str(ctx.analysis.get("decision_status") or "")
    if not ctx.analysis:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    if decision_status.startswith("PAUSED_MISSING_MARKET_DATA") or decision_status.startswith(
        "VOLUME_DATA_MISSING"
    ):
        missing = ctx.analysis.get("missing_conditions")
        missing_text = ", ".join(str(item) for item in missing) if isinstance(missing, list) else ""
        return ConditionCheck(met=False, observed_value=f"Manquant: {missing_text}", target=target)
    if _analysis_dict(ctx.analysis, "spread_check"):
        market = _analysis_dict(ctx.analysis, "market")
        observed = f"Bid {_fmt(market.get('bid'))} / ask {_fmt(market.get('ask'))}"
        return ConditionCheck(met=True, observed_value=observed, target=target)
    return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)


def _momentum_spread(ctx: ConditionContext) -> ConditionCheck:
    target = "Spread sous les seuils configures"
    spread_check = _analysis_dict(ctx.analysis, "spread_check")
    if not spread_check:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    observed = (
        f"Spread {spread_check.get('spread_bps', '?')} bps "
        f"(max {spread_check.get('max_spread_bps', '?')})"
    )
    return ConditionCheck(met=bool(spread_check.get("ok")), observed_value=observed, target=target)


def _momentum_not_extended(ctx: ConditionContext) -> ConditionCheck:
    target = "Prix pas trop loin au-dessus de l'entree"
    stale = _analysis_dict(ctx.analysis, "stale")
    if ctx.current_status == SetupStatus.MISSED_BREAKOUT:
        retest = _analysis_dict(ctx.analysis, "missed_retest")
        zone = f"{_fmt(retest.get('zone_min'))}-{_fmt(retest.get('zone_max'))}"
        return ConditionCheck(
            met=False,
            observed_value="Breakout manque",
            target=f"Retour dans la zone de retest {zone}",
        )
    if not stale:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    observed = (
        f"Ask {_fmt(stale.get('ask'))} / limite max {_fmt(stale.get('maximum_limit_price'))} "
        f"(+{stale.get('buffer', '?')})"
    )
    return ConditionCheck(
        met=not bool(stale.get("is_missed_breakout")),
        observed_value=observed,
        target=target,
    )


def _momentum_breakout_confirmed(ctx: ConditionContext) -> ConditionCheck:
    breakout = _section(ctx.config, "breakout")
    target = (
        f"Cassure de {_fmt(breakout.get('resistance'))} confirmee "
        "(volume fort, tenue 2 barres ou retest)"
    )
    validation = _analysis_dict(ctx.analysis, "validation")
    if not validation:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    ratio = validation.get("volume_ratio_closed_bar")
    path = str(validation.get("path") or "")
    observed = f"Volume x{_fmt(ratio)} / statut {validation.get('volume_status', '?')}"
    if path:
        observed = f"Chemin {path} - {observed}"
    return ConditionCheck(met=bool(validation.get("valid")), observed_value=observed, target=target)


def _momentum_within_limit(ctx: ConditionContext) -> ConditionCheck:
    target = "Ask sous la limite maximale d'entree"
    market = _analysis_dict(ctx.analysis, "market")
    ask = _num(market.get("ask"))
    limit = _num(ctx.analysis.get("maximum_limit_price"))
    if ask is None or limit is None:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    observed = f"Ask {_fmt(ask)} / limite {_fmt(limit)}"
    return ConditionCheck(met=ask <= limit, observed_value=observed, target=target)


def _momentum_stop(ctx: ConditionContext) -> ConditionCheck:
    target = "Support structurel disponible sous l'entree"
    stop = _analysis_dict(ctx.analysis, "trailing_stop_loss")
    if not stop:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    missing = stop.get("missing")
    if isinstance(missing, list) and missing:
        return ConditionCheck(met=False, observed_value=f"Manquant: {', '.join(missing)}", target=target)
    return ConditionCheck(
        met=True,
        observed_value=f"Stop initial {_fmt(stop.get('initial_stop'))}",
        target=target,
    )


def _momentum_risk(ctx: ConditionContext) -> ConditionCheck:
    target = "Quantite >= 1 dans le budget risque"
    risk = _analysis_dict(ctx.analysis, "risk_preview")
    if not risk:
        return ConditionCheck(met=None, observed_value="En attente d'une analyse", target=target)
    risk_per_share = _num(risk.get("risk_per_share")) or 0.0
    quantity = _num(risk.get("maximum_quantity")) or 0.0
    observed = f"Quantite max {int(quantity)} / risque par action {_fmt(risk_per_share)}"
    return ConditionCheck(met=risk_per_share > 0 and quantity >= 1, observed_value=observed, target=target)


# --- breakout_retest (app/setups/breakout_retest.py) -------------------------


def _retest_breakout_confirmed(ctx: ConditionContext) -> ConditionCheck:
    level = _num(_section(ctx.config, "breakout").get("daily_close_above"))
    target = f"Cloture journaliere au-dessus de {_fmt(level)}"
    snapshot = ctx.snapshot
    if snapshot is None or level is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    close = snapshot.close if snapshot.close is not None else snapshot.price
    daily_close = snapshot.daily_close if snapshot.daily_close is not None else close
    observed = f"Cloture journaliere {_fmt(daily_close)}"
    return ConditionCheck(met=daily_close > level, observed_value=observed, target=target)


def _retest_zone(ctx: ConditionContext) -> ConditionCheck:
    retest = _section(ctx.config, "retest")
    zone_min = _num(retest.get("zone_min"))
    zone_max = _num(retest.get("zone_max"))
    target = f"Retour dans la zone {_fmt(zone_min)}-{_fmt(zone_max)}"
    snapshot = ctx.snapshot
    if snapshot is None or zone_min is None or zone_max is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    observed = f"Prix {_fmt(snapshot.price)}"
    return ConditionCheck(met=zone_min <= snapshot.price <= zone_max, observed_value=observed, target=target)


# --- range_breakout (app/setups/range_breakout.py) ---------------------------


def _range_holds(ctx: ConditionContext) -> ConditionCheck:
    low = _num(_section(ctx.config, "range").get("low"))
    target = f"Aucune cloture sous le bas du range ({_fmt(low)})"
    snapshot = ctx.snapshot
    if snapshot is None or low is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    close = snapshot.close if snapshot.close is not None else snapshot.price
    observed = f"Cloture {_fmt(close)} / bas du range {_fmt(low)}"
    return ConditionCheck(met=close >= low, observed_value=observed, target=target)


def _range_break(ctx: ConditionContext) -> ConditionCheck:
    high = _num(_section(ctx.config, "range").get("high"))
    target = f"Prix au-dessus du haut du range ({_fmt(high)})"
    snapshot = ctx.snapshot
    if snapshot is None or high is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    observed = f"Prix {_fmt(snapshot.price)} / haut du range {_fmt(high)}"
    return ConditionCheck(met=snapshot.price > high, observed_value=observed, target=target)


# --- aggressive_rebound (app/setups/aggressive_rebound.py) -------------------


def _rebound_support(ctx: ConditionContext) -> ConditionCheck:
    support = _section(ctx.config, "support_zone")
    zone_min = _num(support.get("min"))
    zone_max = _num(support.get("max"))
    target = f"Prix dans la zone de support {_fmt(zone_min)}-{_fmt(zone_max)}"
    snapshot = ctx.snapshot
    if snapshot is None or zone_min is None or zone_max is None:
        return ConditionCheck(met=None, observed_value="En attente de donnees", target=target)
    observed = f"Prix {_fmt(snapshot.price)}"
    return ConditionCheck(met=zone_min <= snapshot.price <= zone_max, observed_value=observed, target=target)


def _rebound_reclaim(ctx: ConditionContext) -> ConditionCheck:
    snapshot = ctx.snapshot
    zone_max = _num(_section(ctx.config, "support_zone").get("max"))
    if snapshot is None:
        return ConditionCheck(
            met=None,
            observed_value="En attente de donnees",
            target="Cloture au-dessus du precedent haut",
        )
    previous_high = snapshot.previous_high or snapshot.high or zone_max
    target = f"Cloture au-dessus du precedent haut ({_fmt(previous_high)})"
    close = snapshot.close if snapshot.close is not None else snapshot.price
    observed = f"Cloture {_fmt(close)} / precedent haut {_fmt(previous_high)}"
    if previous_high is None:
        return ConditionCheck(met=None, observed_value=observed, target=target)
    return ConditionCheck(met=close > float(previous_high), observed_value=observed, target=target)


SETUP_CONDITION_DEFINITIONS: dict[str, SetupConditionsDefinition] = {
    "pullback_continuation": SetupConditionsDefinition(
        setup_type="pullback_continuation",
        setup_name="Pullback Continuation",
        direction="long",
        conditions=(
            ConditionDefinition(
                "uptrend",
                "Tendance haussiere confirmee",
                "EMA20 au-dessus de l'EMA50 sur le flux de cotation",
                _pullback_uptrend,
            ),
            ConditionDefinition(
                "pullback_to_ema20",
                "Retour du prix sur l'EMA20",
                "Le prix revient au niveau ou sous l'EMA20 (zone de repli)",
                _pullback_to_ema20,
            ),
            ConditionDefinition(
                "bullish_rejection",
                "Rejet haussier",
                "Bougie haussiere (cloture au-dessus de l'ouverture) sur le repli",
                lambda ctx: _bullish_check(ctx, "Bougie haussiere sur le repli"),
            ),
        ),
        status_floors={SetupStatus.WAITING_ENTRY_SIGNAL.value: 1},
        invalidation_map=(("ema 50", "uptrend"),),
    ),
    "momentum_breakout": SetupConditionsDefinition(
        setup_type="momentum_breakout",
        setup_name="Momentum Breakout",
        direction="long",
        conditions=(
            ConditionDefinition(
                "market_data_ready",
                "Donnees de marche completes",
                "Bid/ask, ATR 15m/1h, tick et spread disponibles",
                _momentum_market_data,
            ),
            ConditionDefinition(
                "spread_ok",
                "Spread acceptable",
                "Spread sous le plafond en bps et sous la fraction d'ATR autorisee",
                _momentum_spread,
            ),
            ConditionDefinition(
                "price_not_extended",
                "Prix non etendu",
                "L'ask ne depasse pas la limite maximale plus le buffer anti-chase",
                _momentum_not_extended,
            ),
            ConditionDefinition(
                "breakout_confirmed",
                "Cassure confirmee par le volume",
                "Volume fort (fast), tenue 2 barres au-dessus (confirme) ou retest valide",
                _momentum_breakout_confirmed,
            ),
            ConditionDefinition(
                "price_within_limit",
                "Prix executable sous la limite",
                "L'ask reste sous la limite maximale d'entree calculee",
                _momentum_within_limit,
            ),
            ConditionDefinition(
                "structural_stop_available",
                "Stop structurel disponible",
                "Un support structurel sous le trigger permet de poser le stop initial",
                _momentum_stop,
            ),
            ConditionDefinition(
                "risk_approved",
                "Risque dimensionnable",
                "Le budget risque permet au moins 1 action au pire prix d'entree",
                _momentum_risk,
            ),
        ),
    ),
    "breakout_retest": SetupConditionsDefinition(
        setup_type="breakout_retest",
        setup_name="Breakout Retest",
        direction="long",
        conditions=(
            ConditionDefinition(
                "breakout_confirmed",
                "Cassure journaliere confirmee",
                "Cloture journaliere au-dessus du niveau de breakout configure",
                _retest_breakout_confirmed,
            ),
            ConditionDefinition(
                "retest_of_level",
                "Retest de l'ancien niveau",
                "Le prix revient dans la zone de retest configuree",
                _retest_zone,
            ),
            ConditionDefinition(
                "bullish_confirmation",
                "Confirmation haussiere",
                "Bougie haussiere pendant le retest de la zone",
                lambda ctx: _bullish_check(ctx, "Bougie haussiere dans la zone de retest"),
            ),
        ),
        status_floors={SetupStatus.WAITING_ENTRY_SIGNAL.value: 1},
        invalidation_map=(("retest invalidation", "retest_of_level"),),
    ),
    "range_breakout": SetupConditionsDefinition(
        setup_type="range_breakout",
        setup_name="Range Breakout",
        direction="long",
        conditions=(
            ConditionDefinition(
                "range_holds",
                "Le range tient",
                "Aucune cloture sous le bas du range configure",
                _range_holds,
            ),
            ConditionDefinition(
                "resistance_break",
                "Cassure du haut du range",
                "Le prix passe au-dessus du haut du range",
                _range_break,
            ),
        ),
        invalidation_map=(("range low", "range_holds"),),
    ),
    "aggressive_rebound": SetupConditionsDefinition(
        setup_type="aggressive_rebound",
        setup_name="Aggressive Rebound",
        direction="long",
        conditions=(
            ConditionDefinition(
                "price_at_support",
                "Prix dans la zone de support",
                "Le prix touche la zone de support configuree",
                _rebound_support,
            ),
            ConditionDefinition(
                "bullish_rejection",
                "Rejet haussier sur le support",
                "Bougie haussiere (cloture au-dessus de l'ouverture)",
                lambda ctx: _bullish_check(ctx, "Bougie haussiere sur le support"),
            ),
            ConditionDefinition(
                "reclaim_previous_high",
                "Reprise du precedent haut",
                "Cloture au-dessus du precedent haut (confirmation du rebond)",
                _rebound_reclaim,
            ),
        ),
        status_floors={SetupStatus.WAITING_ENTRY_SIGNAL.value: 1},
        invalidation_map=(("support invalidation", "price_at_support"),),
    ),
}

MANAGEMENT_ONLY_SETUP_TYPES = {"runner", "trailing_runner", "position_management"}


def definition_for(setup_type: str) -> SetupConditionsDefinition | None:
    return SETUP_CONDITION_DEFINITIONS.get(str(setup_type or ""))


def evaluate_setup_conditions(
    definition: SetupConditionsDefinition,
    config: dict[str, Any],
    snapshot: MarketSnapshot | None,
    analysis: dict[str, Any] | None,
    current_status: SetupStatus | None,
) -> list[ConditionCheck]:
    ctx = ConditionContext(
        config=config if isinstance(config, dict) else {},
        snapshot=snapshot,
        analysis=analysis if isinstance(analysis, dict) else {},
        current_status=current_status,
    )
    return [condition.evaluator(ctx) for condition in definition.conditions]
