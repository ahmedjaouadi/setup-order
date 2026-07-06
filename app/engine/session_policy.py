from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from app.models import MarketSnapshot, SetupSignal, SignalAction
from app.utils.market_hours import US_EQUITY_TIMEZONE, current_us_equity_session_context

SESSION_BLOCKING_REASON = "BLOCKED_OUTSIDE_REGULAR_MARKET_HOURS"
WAIT_AFTER_OPEN_REASON = "WAIT_AFTER_OPEN_WINDOW_ACTIVE"
SESSION_UNKNOWN_REASON = "MARKET_SESSION_UNKNOWN"
TRADING_WINDOW_REASON = "BLOCKED_OUTSIDE_TRADING_WINDOW"

PREMARKET_BLOCKING_STATUSES = frozenset(
    {
        "PREMARKET_TRIGGER_DETECTED",
        "AFTER_HOURS_TRIGGER_DETECTED",
        "RTH_CONFIRMATION_REQUIRED",
        "WAITING_AFTER_OPEN_BARS",
        "ENTRY_BEFORE_TRADING_WINDOW",
        "LUNCH_WINDOW_RESTRICTED",
        "ENTRY_AFTER_CUTOFF",
    }
)


@dataclass(frozen=True, slots=True)
class ParsedMoment:
    value: datetime
    has_date: bool


def apply_entry_session_policy(
    signal: SetupSignal,
    snapshot: MarketSnapshot,
    settings: dict[str, Any] | None,
) -> SetupSignal:
    if not _is_entry_candidate(signal):
        return signal

    policy = _session_policy(settings)
    if policy.get("enabled", True) is False:
        return signal

    metadata = deepcopy(signal.metadata) if isinstance(signal.metadata, dict) else {}
    analysis = metadata.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
        metadata["analysis"] = analysis

    inferred_context = current_us_equity_session_context(
        snapshot.current_time or snapshot.timestamp
    )
    raw_session = snapshot.session or inferred_context.session
    current_time = snapshot.current_time or inferred_context.current_time
    market_open_time = snapshot.market_open_time or inferred_context.market_open_time
    session = _normalize_session(raw_session)
    analysis["session_policy"] = {
        "enabled": True,
        "session": session,
        "current_time": current_time,
        "market_open_time": market_open_time,
        "require_regular_trading_hours_for_entry": bool(
            policy.get("require_regular_trading_hours_for_entry", True)
        ),
        "allow_premarket_entry": bool(policy.get("allow_premarket_entry", False)),
        "allow_after_hours_entry": bool(policy.get("allow_after_hours_entry", False)),
        "extended_hours": _mapping(policy.get("extended_hours")),
        "derived_from_clock": bool(
            snapshot.session in (None, "")
            or snapshot.current_time in (None, "")
            or snapshot.market_open_time in (None, "")
        ),
    }

    require_rth = bool(policy.get("require_regular_trading_hours_for_entry", True))
    allow_premarket = bool(policy.get("allow_premarket_entry", False))
    allow_after_hours = bool(policy.get("allow_after_hours_entry", False))

    if session == "PRE_MARKET" and require_rth and not allow_premarket:
        return _blocked_signal(
            signal,
            metadata,
            status="PREMARKET_TRIGGER_DETECTED",
            reason=(
                "PREMARKET_TRIGGER_DETECTED: Le trigger a ete touche avant "
                "l'ouverture. Attente de confirmation en marche regulier."
            ),
            next_action="PENDING_RTH_CONFIRMATION",
            title="Signal detecte hors marche - entree bloquee",
            message=(
                "Le trigger a ete touche avant l'ouverture. Attente de "
                "confirmation en marche regulier."
            ),
            blocking=[SESSION_BLOCKING_REASON],
            readiness_label="WAITING",
        )

    if session == "AFTER_HOURS" and require_rth and not allow_after_hours:
        return _blocked_signal(
            signal,
            metadata,
            status="AFTER_HOURS_TRIGGER_DETECTED",
            reason=(
                "AFTER_HOURS_TRIGGER_DETECTED: Le setup parait confirme apres "
                "la cloture. Attente de la prochaine session reguliere."
            ),
            next_action="PENDING_NEXT_RTH_CONFIRMATION",
            title="Signal detecte after-hours - entree bloquee",
            message=(
                "Le setup parait confirme apres la cloture. Attente de la "
                "prochaine session reguliere."
            ),
            blocking=[SESSION_BLOCKING_REASON],
            readiness_label="WAITING",
        )

    if require_rth and session == "UNKNOWN":
        return _blocked_signal(
            signal,
            metadata,
            status="RTH_CONFIRMATION_REQUIRED",
            reason=(
                "RTH_CONFIRMATION_REQUIRED: La session reguliere doit etre "
                "confirmee avant toute entree automatique."
            ),
            next_action="PENDING_RTH_CONFIRMATION",
            title="Confirmation RTH requise",
            message=(
                "Le moteur ne peut pas confirmer que le marche est en session "
                "reguliere. Entree automatique bloquee par precaution."
            ),
            blocking=[SESSION_UNKNOWN_REASON, SESSION_BLOCKING_REASON],
            readiness_label="WAITING",
        )

    if session != "RTH":
        return signal

    wait_state = _after_open_wait_state(
        MarketSnapshot(
            symbol=snapshot.symbol,
            price=snapshot.price,
            timestamp=snapshot.timestamp,
            session=raw_session,
            market_open_time=market_open_time,
            current_time=current_time,
        ),
        policy,
    )
    analysis["session_policy"].update(wait_state["context"])
    if wait_state["blocked"]:
        return _blocked_signal(
            signal,
            metadata,
            status="WAITING_AFTER_OPEN_BARS",
            reason=(
                "WAITING_AFTER_OPEN_BARS: Le marche vient d'ouvrir. Le bot "
                "attend avant de revalider le setup."
            ),
            next_action="WAITING_RTH_CONFIRMATION",
            title="Attente de confirmation apres ouverture",
            message=wait_state["message"],
            blocking=[WAIT_AFTER_OPEN_REASON],
            readiness_label="WAITING",
        )

    window_block = _trading_window_state(
        policy,
        current_time,
        volume_ratio=_snapshot_volume_ratio(snapshot),
    )
    if window_block is not None:
        analysis["session_policy"]["trading_window"] = window_block["context"]
        return _blocked_signal(
            signal,
            metadata,
            status=window_block["status"],
            reason=f"{window_block['status']}: {window_block['message']}",
            next_action=window_block["next_action"],
            title=window_block["title"],
            message=window_block["message"],
            blocking=[TRADING_WINDOW_REASON],
            readiness_label="WAITING",
        )

    return signal


def signal_blocked_by_session_policy(signal: SetupSignal) -> bool:
    if not isinstance(signal.metadata, dict):
        return False
    analysis = signal.metadata.get("analysis")
    if not isinstance(analysis, dict):
        return False
    decision_status = str(analysis.get("decision_status") or "").upper()
    blocking = _string_list(analysis.get("blocking_conditions"))
    if decision_status in PREMARKET_BLOCKING_STATUSES:
        return True
    return (
        SESSION_BLOCKING_REASON in blocking
        or WAIT_AFTER_OPEN_REASON in blocking
        or TRADING_WINDOW_REASON in blocking
    )


def execution_window_block(
    settings: dict[str, Any] | None,
    *,
    current_time: datetime | str | None = None,
) -> dict[str, Any] | None:
    policy = _session_policy(settings)
    if policy.get("enabled", True) is False:
        return None

    context = current_us_equity_session_context(current_time)
    session = _normalize_session(context.session)
    payload = {
        "decision_status": "",
        "decision": "NO_ENTRY",
        "next_action": "",
        "display_title": "",
        "display_message": "",
        "readiness_label": "WAITING",
        "blocking_conditions": [],
        "session_policy": {
            "enabled": True,
            "session": session,
            "current_time": context.current_time,
            "market_open_time": context.market_open_time,
            "require_regular_trading_hours_for_entry": bool(
                policy.get("require_regular_trading_hours_for_entry", True)
            ),
            "allow_premarket_entry": bool(policy.get("allow_premarket_entry", False)),
            "allow_after_hours_entry": bool(policy.get("allow_after_hours_entry", False)),
            "extended_hours": _mapping(policy.get("extended_hours")),
            "derived_from_clock": True,
        },
    }

    require_rth = bool(policy.get("require_regular_trading_hours_for_entry", True))
    allow_premarket = bool(policy.get("allow_premarket_entry", False))
    allow_after_hours = bool(policy.get("allow_after_hours_entry", False))

    if session == "PRE_MARKET" and require_rth and not allow_premarket:
        payload.update(
            {
                "decision_status": "PREMARKET_TRIGGER_DETECTED",
                "next_action": "PENDING_RTH_CONFIRMATION",
                "display_title": "Signal detecte hors marche - entree bloquee",
                "display_message": (
                    "Le trigger a ete touche avant l'ouverture. Attente de "
                    "confirmation en marche regulier."
                ),
                "blocking_conditions": [SESSION_BLOCKING_REASON],
            }
        )
        return payload

    if session == "AFTER_HOURS" and require_rth and not allow_after_hours:
        payload.update(
            {
                "decision_status": "AFTER_HOURS_TRIGGER_DETECTED",
                "next_action": "PENDING_NEXT_RTH_CONFIRMATION",
                "display_title": "Signal detecte after-hours - entree bloquee",
                "display_message": (
                    "Le setup parait confirme apres la cloture. Attente de la "
                    "prochaine session reguliere."
                ),
                "blocking_conditions": [SESSION_BLOCKING_REASON],
            }
        )
        return payload

    if require_rth and session != "RTH":
        payload.update(
            {
                "decision_status": "BLOCKED_OUTSIDE_REGULAR_MARKET_HOURS",
                "next_action": "WAIT_NEXT_REGULAR_SESSION",
                "display_title": "Entree bloquee hors marche regulier",
                "display_message": (
                    "Le marche regulier est ferme. L'entree automatique reste "
                    "bloquee jusqu'a la prochaine session RTH."
                ),
                "blocking_conditions": [SESSION_BLOCKING_REASON],
            }
        )
        return payload

    wait_state = _after_open_wait_state(
        MarketSnapshot(
            symbol="EXECUTION_WINDOW",
            price=0.0,
            current_time=context.current_time,
            market_open_time=context.market_open_time,
            session=context.session,
        ),
        policy,
    )
    payload["session_policy"].update(wait_state["context"])
    if wait_state["blocked"]:
        payload.update(
            {
                "decision_status": "WAITING_AFTER_OPEN_BARS",
                "next_action": "WAITING_RTH_CONFIRMATION",
                "display_title": "Attente de confirmation apres ouverture",
                "display_message": wait_state["message"],
                "blocking_conditions": [WAIT_AFTER_OPEN_REASON],
            }
        )
        return payload

    window_block = _trading_window_state(
        policy,
        context.current_time,
        volume_ratio=None,
        runtime_check=True,
    )
    if window_block is not None:
        payload["session_policy"]["trading_window"] = window_block["context"]
        payload.update(
            {
                "decision_status": window_block["status"],
                "next_action": window_block["next_action"],
                "display_title": window_block["title"],
                "display_message": window_block["message"],
                "blocking_conditions": [TRADING_WINDOW_REASON],
            }
        )
        return payload

    return None


def _is_entry_candidate(signal: SetupSignal) -> bool:
    return signal.action == SignalAction.ENTRY_READY


def _session_policy(settings: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return {}
    return _mapping(settings.get("session_policy"))


def _blocked_signal(
    signal: SetupSignal,
    metadata: dict[str, Any],
    *,
    status: str,
    reason: str,
    next_action: str,
    title: str,
    message: str,
    blocking: list[str],
    readiness_label: str,
) -> SetupSignal:
    analysis = metadata.setdefault("analysis", {})
    existing_blocking = _string_list(analysis.get("blocking_conditions"))
    analysis.update(
        {
            "decision_status": status,
            "decision": "NO_ENTRY",
            "next_action": next_action,
            "display_title": title,
            "display_message": message,
            "readiness_label": readiness_label,
            "blocking_conditions": list(dict.fromkeys([*blocking, *existing_blocking])),
            "missing_conditions": _string_list(analysis.get("missing_conditions")),
        }
    )
    return SetupSignal(
        action=SignalAction.HOLD,
        reason=reason,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        metadata=metadata,
    )


def _after_open_wait_state(
    snapshot: MarketSnapshot,
    policy: dict[str, Any],
) -> dict[str, Any]:
    wait_minutes = max(int(_number(policy.get("wait_after_open_minutes"), 0) or 0), 0)
    wait_bars = max(int(_number(policy.get("wait_closed_bars_after_open"), 0) or 0), 0)
    timeframe = str(policy.get("wait_bars_timeframe") or "15m")
    minutes_since_open = _minutes_since_open(snapshot)
    timeframe_minutes = _timeframe_minutes(timeframe)
    closed_bars_after_open = (
        int(minutes_since_open // timeframe_minutes)
        if minutes_since_open is not None and timeframe_minutes
        else None
    )

    blocked_by_minutes = wait_minutes > 0 and (
        minutes_since_open is not None and minutes_since_open < wait_minutes
    )
    blocked_by_bars = wait_bars > 0 and (
        closed_bars_after_open is not None and closed_bars_after_open < wait_bars
    )
    blocked = blocked_by_minutes or blocked_by_bars

    message = "Le marche vient d'ouvrir. Le bot attend avant de revalider le setup."
    if wait_bars > 0 and timeframe:
        message = (
            "Le marche vient d'ouvrir. Le bot attend "
            f"{wait_bars} bougies {timeframe} cloturees avant de revalider le setup."
        )
    elif wait_minutes > 0:
        message = (
            "Le marche vient d'ouvrir. Le bot attend "
            f"{wait_minutes} minutes avant de revalider le setup."
        )

    return {
        "blocked": blocked,
        "message": message,
        "context": {
            "wait_after_open_minutes": wait_minutes,
            "wait_closed_bars_after_open": wait_bars,
            "wait_bars_timeframe": timeframe,
            "minutes_since_open": (
                round(minutes_since_open, 2) if minutes_since_open is not None else None
            ),
            "closed_bars_after_open": closed_bars_after_open,
        },
    }


def _trading_window_state(
    policy: dict[str, Any],
    current_time: Any,
    *,
    volume_ratio: float | None,
    runtime_check: bool = False,
) -> dict[str, Any] | None:
    """Hourly entry windows from docs/skills.md section 25bis.

    - no entry before ``no_entry_before`` (default 10:00 New York)
    - reinforced conditions during the lunch chop (11:30-14:00): the RVOL
      must confirm (>= min_volume_ratio) or the entry is refused
    - no new entry after ``no_new_entry_after`` (default 15:30)

    ``runtime_check`` marks the execution-time call where no snapshot (and
    therefore no RVOL) is available; the reinforced lunch rule is enforced at
    signal level, so only hard cutoffs apply there.
    """

    windows = _mapping(policy.get("trading_windows"))
    if not windows or windows.get("enabled", True) is False:
        return None
    clock = _ny_wall_clock(current_time)
    if clock is None:
        return None

    context: dict[str, Any] = {
        "enabled": True,
        "current_time_ny": clock.isoformat(),
        "volume_ratio": volume_ratio,
    }

    open_from = _parse_clock(windows.get("no_entry_before"))
    if open_from is not None and clock < open_from:
        context["window"] = "BEFORE_OPEN_WINDOW"
        return {
            "status": "ENTRY_BEFORE_TRADING_WINDOW",
            "next_action": "WAIT_TRADING_WINDOW",
            "title": "Entree bloquee - fenetre d'ouverture",
            "message": (
                "L'ouverture concentre volatilite et faux signaux. Pas "
                f"d'entree avant {open_from.strftime('%H:%M')} (heure de New York)."
            ),
            "context": context,
        }

    cutoff = _parse_clock(windows.get("no_new_entry_after"))
    if cutoff is not None and clock >= cutoff:
        context["window"] = "AFTER_ENTRY_CUTOFF"
        return {
            "status": "ENTRY_AFTER_CUTOFF",
            "next_action": "WAIT_NEXT_SESSION",
            "title": "Entree bloquee - trop tard dans la session",
            "message": (
                f"Aucune nouvelle entree apres {cutoff.strftime('%H:%M')} "
                "(heure de New York): temps insuffisant pour que le trade travaille."
            ),
            "context": context,
        }

    lunch = _mapping(windows.get("lunch"))
    lunch_start = _parse_clock(lunch.get("start") or "11:30")
    lunch_end = _parse_clock(lunch.get("end") or "14:00")
    mode = str(lunch.get("mode") or "REINFORCED").strip().upper()
    if (
        lunch_start is not None
        and lunch_end is not None
        and lunch_start <= clock < lunch_end
        and mode != "ALLOW"
    ):
        context["window"] = "LUNCH_CHOP"
        context["lunch_mode"] = mode
        if mode == "BLOCK":
            return {
                "status": "LUNCH_WINDOW_RESTRICTED",
                "next_action": "WAIT_TRADING_WINDOW",
                "title": "Entree bloquee - lunch chop",
                "message": (
                    "Fenetre 11:30-14:00 (New York): volume faible et "
                    "breakouts peu fiables. Entrees desactivees."
                ),
                "context": context,
            }
        # REINFORCED: the entry needs an abnormal participation (RVOL).
        if runtime_check:
            return None
        min_ratio = _number(lunch.get("min_volume_ratio"), 1.5) or 1.5
        context["min_volume_ratio"] = min_ratio
        if volume_ratio is None or volume_ratio < min_ratio:
            observed = "indisponible" if volume_ratio is None else f"{volume_ratio:.2f}"
            return {
                "status": "LUNCH_WINDOW_RESTRICTED",
                "next_action": "WAIT_TRADING_WINDOW",
                "title": "Entree bloquee - lunch chop sans volume",
                "message": (
                    "Pendant le lunch (11:30-14:00 New York) une entree exige "
                    f"un RVOL >= {min_ratio:g}. RVOL observe: {observed}."
                ),
                "context": context,
            }
    return None


def _snapshot_volume_ratio(snapshot: MarketSnapshot) -> float | None:
    for attribute in (
        "volume_ratio",
        "volume_ratio_closed_bar",
        "volume_ratio_live",
        "volume_ratio_15m",
    ):
        value = _number(getattr(snapshot, attribute, None))
        if value is not None:
            return value
    return None


def _ny_wall_clock(value: Any) -> time | None:
    parsed = _parse_moment(value)
    if parsed is None:
        return None
    moment = parsed.value
    if moment.tzinfo is not None:
        moment = moment.astimezone(US_EQUITY_TIMEZONE)
    return moment.timetz().replace(tzinfo=None)


def _parse_clock(value: Any) -> time | None:
    if value in (None, ""):
        return None
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _minutes_since_open(snapshot: MarketSnapshot) -> float | None:
    current = _parse_moment(snapshot.current_time or snapshot.timestamp)
    opened = _parse_moment(snapshot.market_open_time)
    if current is None or opened is None:
        return None
    current_dt, open_dt = _align_datetimes(current, opened)
    delta_minutes = (current_dt - open_dt).total_seconds() / 60.0
    if delta_minutes < 0 or delta_minutes > 24 * 60:
        return None
    return delta_minutes


def _parse_moment(value: Any) -> ParsedMoment | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return ParsedMoment(datetime.fromisoformat(normalized), has_date=True)
    except ValueError:
        pass
    try:
        parsed_time = time.fromisoformat(normalized)
    except ValueError:
        return None
    return ParsedMoment(
        datetime.combine(date(2000, 1, 1), parsed_time),
        has_date=False,
    )


def _align_datetimes(current: ParsedMoment, opened: ParsedMoment) -> tuple[datetime, datetime]:
    current_dt = current.value
    open_dt = opened.value
    if not opened.has_date and current.has_date:
        open_dt = datetime.combine(current_dt.date(), open_dt.timetz())
    if not current.has_date and opened.has_date:
        current_dt = datetime.combine(open_dt.date(), current_dt.timetz())
    if current_dt.tzinfo and open_dt.tzinfo is None:
        open_dt = open_dt.replace(tzinfo=current_dt.tzinfo)
    if open_dt.tzinfo and current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=open_dt.tzinfo)
    return current_dt, open_dt


def _normalize_session(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"RTH", "REGULAR", "REGULAR_HOURS", "OPEN"}:
        return "RTH"
    if text in {"PREMARKET", "PRE_MARKET", "PRE-MARKET"}:
        return "PRE_MARKET"
    if text in {"AFTERHOURS", "AFTER_HOURS", "AFTER-HOURS", "POST_MARKET"}:
        return "AFTER_HOURS"
    return "UNKNOWN"


def _timeframe_minutes(value: str) -> int | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        if text.endswith("m"):
            return int(text[:-1])
        if text.endswith("h"):
            return int(text[:-1]) * 60
        if text.endswith("d"):
            return int(text[:-1]) * 24 * 60
    except ValueError:
        return None
    return None


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]
