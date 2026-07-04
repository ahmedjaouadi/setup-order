from __future__ import annotations

from typing import Any

from app.models import MarketSnapshot, SetupStatus, SignalAction, to_jsonable
from app.setups.setup_roles import setup_allows_entry, setup_role_from_config

TERMINAL_SETUP_STATUSES = {
    SetupStatus.CLOSED.value,
    SetupStatus.CANCELLED.value,
    SetupStatus.EXPIRED.value,
    SetupStatus.INVALIDATED.value,
    SetupStatus.ERROR.value,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
}

WAITING_SETUP_STATUSES = {
    SetupStatus.DRAFT.value,
    SetupStatus.LOADED.value,
    SetupStatus.VALIDATED.value,
    SetupStatus.WAITING_ACTIVATION.value,
    SetupStatus.WAITING_BREAKOUT.value,
    SetupStatus.MISSED_BREAKOUT.value,
    SetupStatus.WAITING_RETEST.value,
    SetupStatus.WAITING_REBOUND.value,
    SetupStatus.WAITING_CONFIRMATION.value,
    SetupStatus.REARMED_ON_NEW_BASE.value,
    SetupStatus.WAITING_ENTRY_SIGNAL.value,
    SetupStatus.RECONCILING_EXISTING_POSITION.value,
}


def build_setup_analysis_trace(
    setup: dict[str, Any],
    snapshot: MarketSnapshot,
    current_status: SetupStatus,
    signal: Any,
) -> dict[str, Any]:
    config = setup.get("config", {})
    if not isinstance(config, dict):
        config = {}
    entry = config.get("entry", {}) if isinstance(config.get("entry", {}), dict) else {}
    setup_type = str(setup.get("setup_type") or config.get("setup_type") or "")
    setup_role = setup_role_from_config(config)
    checks: list[dict[str, Any]] = []

    def add(
        label: str,
        state: str,
        actual: Any = None,
        expected: Any = None,
        detail: str = "",
    ) -> None:
        check = {
            "label": label,
            "state": state,
        }
        if actual not in (None, ""):
            check["actual"] = to_jsonable(actual)
        if expected not in (None, ""):
            check["expected"] = to_jsonable(expected)
        if detail:
            check["detail"] = detail
        checks.append(check)

    price = _float_value(snapshot.price)
    close = _float_value(snapshot.close if snapshot.close is not None else snapshot.price)
    entry_enabled = bool(entry.get("enabled", True))
    allows_entry = setup_allows_entry(setup_role)
    auto_execution_enabled = bool(setup.get("enabled")) and config.get("enabled", True) is not False
    metadata = signal.metadata if isinstance(signal.metadata, dict) else {}
    analysis = metadata.get("analysis") if isinstance(metadata.get("analysis"), dict) else {}
    entry_decision = (
        metadata.get("entry_decision") if isinstance(metadata.get("entry_decision"), dict) else {}
    )
    session_policy = (
        analysis.get("session_policy") if isinstance(analysis.get("session_policy"), dict) else {}
    )
    decision_status = str(analysis.get("decision_status") or "")
    status_text = current_status.value
    status_waiting = status_text.startswith("WAITING") or status_text in {
        SetupStatus.LOADED.value,
        SetupStatus.VALIDATED.value,
        SetupStatus.RECONCILING_EXISTING_POSITION.value,
    }

    add(
        "Suivi setup",
        "ok",
        "surveille",
        "surveille",
    )
    add(
        "Execution auto TWS",
        "ok" if auto_execution_enabled else "wait",
        "ON" if auto_execution_enabled else "OFF",
        "ON pour envoyer un ordre automatiquement",
    )
    add(
        "Role entree",
        "ok" if allows_entry and entry_enabled else "info" if not allows_entry else "bad",
        f"{setup_role.value} / {'entry ON' if entry_enabled else 'entry OFF'}",
        "ENTRY_AND_MANAGEMENT ou ENTRY_ONLY + entry ON",
    )
    add(
        "Statut suivi",
        "wait" if status_waiting else "ok",
        current_status.value,
        "statut non terminal",
    )
    add(
        "Donnees marche",
        "ok" if price is not None and price > 0 else "bad",
        price,
        "prix exploitable",
        f"timeframe={snapshot.timeframe} timestamp={snapshot.timestamp}",
    )
    session_value = str(session_policy.get("session") or snapshot.session or "").upper() or "-"
    session_detail = ""
    if decision_status == "WAITING_AFTER_OPEN_BARS":
        session_state = "wait"
        session_expected = "attendre la fenetre de revalidation RTH"
        session_detail = (
            f"minutes_since_open={session_policy.get('minutes_since_open')} "
            f"closed_bars_after_open={session_policy.get('closed_bars_after_open')}"
        ).strip()
    elif decision_status in {
        "PREMARKET_TRIGGER_DETECTED",
        "AFTER_HOURS_TRIGGER_DETECTED",
        "RTH_CONFIRMATION_REQUIRED",
    }:
        session_state = "bad"
        session_expected = "RTH"
    else:
        session_state = (
            "ok" if session_value == "RTH" else "info" if session_value != "-" else "wait"
        )
        session_expected = "RTH"
    add(
        "Session reguliere",
        session_state,
        session_value,
        session_expected,
        session_detail,
    )

    if setup_type == "momentum_breakout":
        market = analysis.get("market") if isinstance(analysis.get("market"), dict) else {}
        spread_check = (
            analysis.get("spread_check") if isinstance(analysis.get("spread_check"), dict) else {}
        )
        stale = analysis.get("stale") if isinstance(analysis.get("stale"), dict) else {}
        validation = (
            analysis.get("validation") if isinstance(analysis.get("validation"), dict) else {}
        )
        offsets = analysis.get("offsets") if isinstance(analysis.get("offsets"), dict) else {}
        stop_meta = (
            analysis.get("trailing_stop_loss")
            if isinstance(analysis.get("trailing_stop_loss"), dict)
            else {}
        )
        risk_preview = (
            analysis.get("risk_preview") if isinstance(analysis.get("risk_preview"), dict) else {}
        )
        missed_retest = (
            analysis.get("missed_retest") if isinstance(analysis.get("missed_retest"), dict) else {}
        )
        missing_conditions = analysis.get("missing_conditions")
        blocking_conditions = analysis.get("blocking_conditions")
        if not isinstance(missing_conditions, list):
            missing_conditions = []
        if not isinstance(blocking_conditions, list):
            blocking_conditions = []
        resistance = _float_value(analysis.get("resistance"))
        maximum_limit = _float_value(
            analysis.get("active_limit_price")
            if analysis.get("active_limit_price") is not None
            else analysis.get("maximum_limit_price")
        )
        add(
            "Donnees obligatoires",
            "bad" if missing_conditions else "ok",
            ", ".join(missing_conditions) if missing_conditions else "completes",
            "bid/ask/spread/ATR/tick/volume/structure",
        )
        add(
            "Spread acceptable",
            "ok" if spread_check.get("ok") else "bad" if spread_check else "wait",
            (f"spread={spread_check.get('spread')} " f"bps={spread_check.get('spread_bps')}"),
            (
                f"bps<={spread_check.get('max_spread_bps')} "
                f"spread<={spread_check.get('max_spread_atr')}"
            ),
        )
        add(
            "Trigger dynamique",
            (
                "bad"
                if "raw_trigger_offset above cap" in offsets.get("blocking", [])
                else "ok" if offsets else "wait"
            ),
            offsets.get("trigger_offset"),
            offsets.get("trigger_offset_cap"),
            f"raw={offsets.get('raw_trigger_offset')} tick={offsets.get('minimum_tick')}",
        )
        add(
            "Limite dynamique",
            (
                "bad"
                if "raw_limit_offset above cap" in offsets.get("blocking", [])
                else "ok" if offsets else "wait"
            ),
            offsets.get("limit_offset"),
            offsets.get("limit_offset_cap"),
            f"raw={offsets.get('raw_limit_offset')}",
        )
        add(
            "Prix vs resistance",
            threshold_state(price, resistance, ">"),
            price,
            f"> {resistance}",
        )
        add(
            "Transmission ask <= limite",
            threshold_state(market.get("ask"), maximum_limit, "<="),
            market.get("ask"),
            maximum_limit,
            "Le bot ne court pas apres le prix.",
        )
        add(
            "Setup depasse",
            "bad" if stale.get("is_missed_breakout") else "ok" if stale else "wait",
            market.get("ask"),
            f"{maximum_limit} + {stale.get('buffer')}",
            f"buffer_raw={stale.get('buffer_raw')} hard_cap={stale.get('hard_cap')}",
        )
        add(
            "Validation FAST_BREAKOUT",
            "ok" if validation.get("fast_breakout_valid") else "wait",
            validation.get("volume_ratio_closed_bar"),
            "close>resistance et RVOL>=1.50",
        )
        volume = (
            validation.get("volume_confirmation")
            if isinstance(validation.get("volume_confirmation"), dict)
            else {}
        )
        volume_state = (
            "ok"
            if volume.get("status")
            in {
                "FAST_VOLUME_CONFIRMED",
                "VOLUME_CONFIRMED",
            }
            else (
                "bad"
                if volume.get("status")
                in {
                    "WEAK_VOLUME",
                    "VOLUME_REJECTED",
                    "VOLUME_DATA_MISSING",
                }
                else "wait"
            )
        )
        add(
            "Volume",
            volume_state,
            (
                f"{volume.get('status') or '-'} "
                f"ratio={volume.get('ratio') if volume.get('ratio') is not None else '-'}x"
            ),
            "FAST_VOLUME_CONFIRMED, VOLUME_CONFIRMED ou confirmation progressive",
            str(volume.get("interpretation") or ""),
        )
        add(
            "Validation CONFIRMED_BREAKOUT",
            "ok" if validation.get("confirmed_breakout_valid") else "wait",
            (
                f"bars={validation.get('bars_above_resistance')} "
                f"avg_rvol={validation.get('average_volume_ratio_last_2_bars')}"
            ),
            "2 closes>resistance et RVOL moyen>=1.15",
        )
        if missed_retest:
            add(
                "Validation BREAKOUT_RETEST",
                "ok" if validation.get("breakout_retest_valid") else "wait",
                (
                    f"low={missed_retest.get('current_low')} "
                    f"higher_low={missed_retest.get('new_higher_low_confirmed')}"
                ),
                range_text(
                    _float_value(missed_retest.get("zone_min")),
                    _float_value(missed_retest.get("zone_max")),
                ),
                "retest + close>=resistance + higher low + RVOL>=1.00",
            )
        add(
            "Validation retenue",
            "ok" if validation.get("valid") else "wait",
            validation.get("path") or "aucune",
            "FAST, CONFIRMED ou RETEST",
        )
        add(
            "Trigger entree",
            "info",
            analysis.get("active_trigger_price", analysis.get("trigger_price")),
            "round_up(resistance + trigger_offset)",
        )
        add(
            "Stop structurel",
            "bad" if stop_meta.get("missing") else "ok" if stop_meta else "wait",
            stop_meta.get("initial_stop"),
            "support structurel - stop_buffer",
            f"support={stop_meta.get('structural_support')} buffer={stop_meta.get('stop_buffer')}",
        )
        add(
            "Risque worst-case",
            "ok" if risk_preview.get("risk_per_share", 0) > 0 else "wait",
            risk_preview.get("risk_per_share"),
            "maximum_limit_price - trailing_stop_loss.initial_stop",
        )
        add(
            "Quantite maximale",
            (
                "bad"
                if risk_preview.get("maximum_quantity") == 0
                else "ok" if risk_preview else "wait"
            ),
            (
                f"capital={risk_preview.get('quantity_by_capital')} "
                f"risk={risk_preview.get('quantity_by_risk')}"
            ),
            risk_preview.get("maximum_quantity"),
        )
        add(
            "Conditions bloquantes",
            "bad" if blocking_conditions else "ok",
            (
                " | ".join(str(item) for item in blocking_conditions)
                if blocking_conditions
                else "aucune"
            ),
            "aucune",
        )
    elif setup_type == "breakout_retest":
        breakout = (
            config.get("breakout", {}) if isinstance(config.get("breakout", {}), dict) else {}
        )
        retest = config.get("retest", {}) if isinstance(config.get("retest", {}), dict) else {}
        daily_level = _float_value(breakout.get("daily_close_above"))
        zone_min = _float_value(retest.get("zone_min"))
        zone_max = _float_value(retest.get("zone_max"))
        no_close_below = _float_value(retest.get("no_close_below")) or zone_min
        daily_close = _float_value(
            snapshot.daily_close if snapshot.daily_close is not None else close
        )
        bullish = snapshot_bullish_confirmation(snapshot)
        add(
            "Invalidation retest",
            threshold_state(close, no_close_below, ">="),
            close,
            f">= {no_close_below}",
        )
        add(
            "Breakout journalier",
            threshold_state(daily_close, daily_level, ">"),
            daily_close,
            f"> {daily_level}",
        )
        add(
            "Prix dans zone retest",
            range_state(price, zone_min, zone_max),
            price,
            range_text(zone_min, zone_max),
        )
        add(
            "Bougie de confirmation",
            "ok" if bullish else "wait",
            "haussiere" if bullish else "non confirmee",
            "close > open ou bullish_candle",
        )
    elif setup_type == "aggressive_rebound":
        support = (
            config.get("support_zone", {})
            if isinstance(config.get("support_zone", {}), dict)
            else {}
        )
        invalidation = (
            config.get("invalidation", {})
            if isinstance(config.get("invalidation", {}), dict)
            else {}
        )
        zone_min = _float_value(support.get("min"))
        zone_max = _float_value(support.get("max"))
        close_below = _float_value(invalidation.get("close_below")) or zone_min
        previous_high = _float_value(snapshot.previous_high or snapshot.high or zone_max)
        bullish = snapshot_bullish_confirmation(snapshot)
        add(
            "Invalidation support",
            threshold_state(close, close_below, ">="),
            close,
            f">= {close_below}",
        )
        add(
            "Prix dans support",
            range_state(price, zone_min, zone_max),
            price,
            range_text(zone_min, zone_max),
        )
        add(
            "Bougie haussiere",
            "ok" if bullish else "wait",
            "oui" if bullish else "non",
            "confirmation",
        )
        add(
            "Cloture au-dessus precedent high",
            threshold_state(close, previous_high, ">"),
            close,
            f"> {previous_high}",
        )
    elif setup_type == "range_breakout":
        range_config = config.get("range", {}) if isinstance(config.get("range", {}), dict) else {}
        high = _float_value(range_config.get("high"))
        low = _float_value(range_config.get("low"))
        add("Invalidation range", threshold_state(close, low, ">="), close, f">= {low}")
        add("Cassure range high", threshold_state(price, high, ">"), price, f"> {high}")
    elif setup_type == "pullback_continuation":
        ema20 = _float_value(snapshot.ema_20)
        ema50 = _float_value(snapshot.ema_50)
        bullish = snapshot_bullish_confirmation(snapshot)
        add(
            "EMA disponibles",
            "ok" if ema20 is not None and ema50 is not None else "wait",
            f"EMA20={ema20} EMA50={ema50}",
            "EMA20 + EMA50",
        )
        add("Filtre tendance EMA50", threshold_state(price, ema50, ">="), price, f">= {ema50}")
        add("Tendance EMA20 > EMA50", threshold_state(ema20, ema50, ">"), ema20, f"> {ema50}")
        add("Pullback vers EMA20", threshold_state(price, ema20, "<="), price, f"<= {ema20}")
        add(
            "Bougie de reprise",
            "ok" if bullish else "wait",
            "haussiere" if bullish else "non confirmee",
            "close > open ou bullish_candle",
        )
    elif setup_type in {"position_management", "runner", "trailing_runner"}:
        add(
            "Mode gestion",
            "info",
            setup_type,
            "position existante",
            "Ce setup gere une position; il ne cherche pas une nouvelle entree.",
        )

    action = signal.action.value
    if action == SignalAction.ENTRY_READY.value:
        signal_state = "ok" if entry_decision.get("can_send_order", True) else "wait"
        add(
            "Signal entree",
            signal_state,
            signal.entry_price,
            entry_decision.get("status") or "ENTRY_READY",
        )
        if auto_execution_enabled:
            if entry_decision.get("protective_stop_order_ready") is True and entry_decision.get(
                "can_send_order"
            ):
                add(
                    "Controle risque",
                    "ok",
                    "valide + bracket pret",
                    "avant envoi ordre",
                )
            elif entry_decision.get("status") == "BLOCKED_MISSING_PROTECTIVE_STOP_ORDER":
                add(
                    "Controle risque",
                    "bad",
                    "stop protecteur broker non pret",
                    "ordre bracket complet requis",
                )
            elif entry_decision.get("status") == "ACTIVE_ENTRY_ORDER_UNPROTECTED":
                add(
                    "Controle risque",
                    "bad",
                    "ordre actif sans stop attache",
                    "annuler ou attacher un stop",
                )
            elif entry_decision.get("status") == "DUPLICATE_ORDER_BLOCKED":
                add(
                    "Controle risque",
                    "info",
                    "ordre protege deja actif",
                    "aucun nouvel ordre a envoyer",
                )
            else:
                add("Controle risque", "info", "a lancer", "avant envoi ordre")
        else:
            add(
                "Controle risque",
                "wait",
                "execution auto OFF",
                "activer Auto ON si tu veux autoriser TWS",
            )
    elif action == SignalAction.INVALIDATE.value:
        add("Signal entree", "bad", signal.reason, "setup valide")
    else:
        add("Signal entree", "wait", signal.reason, "ENTRY_READY")

    return {
        "phase": analysis_phase_label(setup_type, current_status),
        "summary": f"{action}: {signal.reason}",
        "next_step": analysis_next_step(signal, auto_execution_enabled),
        "checks": checks,
    }


def analysis_phase_label(setup_type: str, status: SetupStatus) -> str:
    if status == SetupStatus.WAITING_ACTIVATION:
        return "Surveillance activation"
    if status == SetupStatus.WAITING_ENTRY_SIGNAL:
        return "Recherche signal entree"
    if status == SetupStatus.MISSED_BREAKOUT:
        return "Breakout manque"
    if status == SetupStatus.WAITING_RETEST:
        return "Attente retest apres breakout manque"
    if status == SetupStatus.REARMED_ON_NEW_BASE:
        return "Rearme sur nouvelle base"
    if status == SetupStatus.EXPIRED:
        return "Setup expire"
    if status in {SetupStatus.IN_POSITION, SetupStatus.MANAGING_POSITION}:
        return "Gestion position"
    if setup_type in {"position_management", "runner", "trailing_runner"}:
        return "Gestion uniquement"
    return status.value


def analysis_next_step(signal: Any, auto_execution_enabled: bool = True) -> str:
    action = signal.action
    if action == SignalAction.ENTRY_READY:
        if not auto_execution_enabled:
            return "Setup pret: continuer la surveillance, execution TWS bloquee tant que Auto est OFF."
        return (
            "Verifier le risque, construire le bracket entree + stop, puis envoyer l'ordre protege."
        )
    if action == SignalAction.STATUS_CHANGE and signal.target_status:
        if signal.target_status == SetupStatus.MISSED_BREAKOUT:
            return "Ne pas entrer au marche; attendre une zone de retest propre."
        if signal.target_status == SetupStatus.WAITING_RETEST:
            return "Observer le retest et exiger une confirmation avant de rearmer."
        if signal.target_status == SetupStatus.REARMED_ON_NEW_BASE:
            return "Surveiller la nouvelle resistance locale et le nouveau trigger."
        if signal.target_status == SetupStatus.EXPIRED:
            return "Arreter la recherche d'entree pour ce setup."
        return f"Passer au statut {signal.target_status.value} et continuer la surveillance."
    if action == SignalAction.INVALIDATE:
        return "Invalider le setup et stopper la recherche d'entree."
    if action == SignalAction.RAISE_STOP:
        return "Monter le stop de protection selon la regle de gestion."
    return f"Continuer a surveiller: {signal.reason}"


def threshold_state(actual: float | None, expected: float | None, operator: str) -> str:
    if actual is None or expected is None:
        return "wait"
    if operator == ">":
        return "ok" if actual > expected else "wait"
    if operator == ">=":
        return "ok" if actual >= expected else "wait"
    if operator == "<":
        return "ok" if actual < expected else "wait"
    if operator == "<=":
        return "ok" if actual <= expected else "wait"
    return "info"


def range_state(actual: float | None, low: float | None, high: float | None) -> str:
    if actual is None or low is None or high is None:
        return "wait"
    return "ok" if low <= actual <= high else "wait"


def range_text(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "zone non renseignee"
    return f"{low} - {high}"


def snapshot_bullish_confirmation(snapshot: MarketSnapshot) -> bool:
    if snapshot.bullish_candle:
        return True
    if snapshot.close is not None and snapshot.open is not None:
        return snapshot.close > snapshot.open
    return False


def market_snapshot_payload(snapshot: MarketSnapshot) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "symbol",
        "price",
        "timestamp",
        "timeframe",
        "open",
        "high",
        "low",
        "close",
        "bid",
        "ask",
        "spread",
        "spread_bps",
        "volume",
        "bar_volume_15m",
        "avg_volume_15m",
        "volume_ratio_15m",
        "current_bar_volume",
        "previous_high",
        "daily_close",
        "volume_ratio",
        "volume_ratio_closed_bar",
        "volume_ratio_live",
        "average_volume_ratio_last_2_bars",
        "volume_status",
        "volume_timeframe",
        "volume_comparison_mode",
        "volume_sample_days",
        "elapsed_ratio",
        "projected_volume",
        "bar_count",
        "bars_15m_count",
        "bars_1h_count",
        "bars_above_resistance",
        "minimum_tick",
        "atr_15m",
        "atr_1h",
        "atr_1h_status",
        "atr_1h_bar_size",
        "atr_1h_duration",
        "atr_1h_use_rth",
        "bars_required_for_atr",
        "historical_1h_available",
        "historical_1h_error",
        "last_successful_atr_1h",
        "last_successful_atr_1h_at",
        "atr_1h_age_seconds",
        "market_data_source",
        "live_quote_source",
        "market_data_type_requested",
        "market_data_type_actual",
        "live_market_data_status",
        "last_ibkr_error_code",
        "last_ibkr_error_message",
        "bar_date",
        "hybrid_signal_bar_size",
        "hybrid_atr_1h_bar_size",
        "hybrid_sources",
        "market_data_readiness",
        "session",
        "market_open_time",
        "current_time",
        "last_confirmed_higher_low",
        "support_level",
        "successful_retest_low",
        "structural_support",
        "breakout_already_detected",
        "new_higher_low_confirmed",
        "close_1h",
        "ema_20",
        "ema_50",
        "bullish_candle",
    ):
        value = getattr(snapshot, key)
        if key == "bullish_candle" or value not in (None, ""):
            payload[key] = to_jsonable(value)
    payload["symbol"] = snapshot.symbol.upper()
    return payload


def _float_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
