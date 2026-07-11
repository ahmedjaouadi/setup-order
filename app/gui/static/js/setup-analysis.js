import { latestQuoteForSymbol, quotePrice, quoteVolumeRatio } from "./market-quotes.js";
import { latestSnapshot } from "./state.js";
import { escapeHtml, firstNumber, formatDetailValue, maybeMoney, numberOrNull, numberText } from "./ui-helpers.js";

export function setupStatusReason(setup) {
  return String((setup && setup.status_reason) || "").trim();
}

export function setupAutoExecutionEnabled(setup) {
  const config = setup.config || {};
  return Boolean(setup.enabled) && config.enabled !== false;
}

export const SETUP_ENTRY_BLOCKING_STATUSES = new Set([
  "CANCELLED",
  "COMPLETED",
  "DELETED",
  "EMERGENCY_STOP",
  "EXPIRED",
  "FILLED",
  "IN_POSITION",
  "INVALIDATED",
  "MANAGING_POSITION",
  "REJECTED",
]);

export function setupMarketDataDiagnostic(quote) {
  if (!quote) {
    return {
      status: "NO_MARKET_DATA",
      missing: ["last", "bid", "ask", "spread", "bars_15m", "atr_15m", "bars_1h", "atr_1h"],
      fields: {},
      field_list: [],
    };
  }
  if (quote.market_data_readiness && typeof quote.market_data_readiness === "object") {
    return quote.market_data_readiness;
  }
  const bid = numberOrNull(quote.bid);
  const ask = numberOrNull(quote.ask);
  const spread = numberOrNull(quote.spread);
  const inferredSpread = spread !== null
    ? spread
    : (bid !== null && ask !== null && ask >= bid ? ask - bid : null);
  const liveStatus = quote.live_market_data_status
    || (Number(quote.market_data_type_actual) === 1
      ? "LIVE"
      : (Number(quote.market_data_type_actual) === 2
        ? "FROZEN"
        : (Number(quote.market_data_type_actual) === 3
          ? "DELAYED"
          : (Number(quote.market_data_type_actual) === 4
            ? "DELAYED_FROZEN"
            : "UNKNOWN"))));
  const fieldList = [
    marketDataDiagnosticField("last", firstNumber(quote.last, quote.price, quote.close), quote.source || quote.market_data_source),
    marketDataDiagnosticField("bid", bid, quote.live_quote_source || "reqMktData", true, "order_submission"),
    marketDataDiagnosticField("ask", ask, quote.live_quote_source || "reqMktData", true, "order_submission"),
    marketDataDiagnosticField("spread", inferredSpread, "local_calculation", true, "order_submission"),
    marketDataDiagnosticField("live_market_data", liveStatus === "LIVE" ? 1 : null, "reqMktData", true, "order_submission", null, liveStatus === "LIVE" ? "" : liveStatus),
    marketDataDiagnosticField("bars_15m", quote.bars_15m_count || quote.bar_count, "reqHistoricalData", true, "analysis", 15),
    marketDataDiagnosticField("atr_15m", quote.atr_15m, "local_ATR_14"),
    marketDataDiagnosticField("bars_1h", quote.bars_1h_count, "reqHistoricalData", true, "analysis", 15),
    marketDataDiagnosticField("atr_1h", quote.atr_1h, "local_ATR_14", true, "analysis", null, quote.atr_1h_status),
  ];
  const fields = Object.fromEntries(fieldList.map((field) => [field.name, field]));
  const missing = setupMarketDiagnosticMissing({ fields });
  return {
    status: marketReadinessStatusFromMissing(missing),
    missing,
    warmup_ready: fieldList
      .filter((field) => ["last", "bars_15m", "atr_15m", "bars_1h", "atr_1h"].includes(field.name))
      .every((field) => field.status === "OK"),
    signal_evaluation_ready: fieldList
      .filter((field) => ["last", "bars_15m", "atr_15m", "bars_1h", "atr_1h"].includes(field.name))
      .every((field) => field.status === "OK"),
    order_submission_ready: fieldList
      .filter((field) => ["bid", "ask", "spread", "live_market_data"].includes(field.name))
      .every((field) => field.status === "OK"),
    market_data_type_requested: quote.market_data_type_requested ?? null,
    market_data_type_actual: quote.market_data_type_actual ?? null,
    live_market_data_status: liveStatus,
    fields,
    field_list: fieldList,
  };
}

export function setupMarketDiagnosticMissing(diagnostic) {
  if (!diagnostic || typeof diagnostic !== "object") return [];
  const explicit = Array.isArray(diagnostic.missing)
    ? diagnostic.missing
    : (Array.isArray(diagnostic.missing_fields) ? diagnostic.missing_fields : []);
  if (explicit.length) return explicit.filter(Boolean);
  const fields = marketDiagnosticFieldsList(diagnostic);
  return fields
    .filter((field) => field && field.blocking && field.status !== "OK")
    .filter((field) => {
      if (field.name === "bars_1h") return !diagnosticFieldBlocks(diagnostic, "atr_1h");
      if (field.name === "bars_15m") return !diagnosticFieldBlocks(diagnostic, "atr_15m");
      return true;
    })
    .map((field) => field.name)
    .filter(Boolean);
}

export function diagnosticFieldBlocks(diagnostic, fieldName) {
  const fields = diagnostic && diagnostic.fields;
  if (fields && typeof fields === "object" && !Array.isArray(fields)) {
    const field = fields[fieldName];
    return Boolean(field && field.blocking && field.status !== "OK");
  }
  const field = marketDiagnosticFieldsList(diagnostic).find((item) => item && item.name === fieldName);
  return Boolean(field && field.blocking && field.status !== "OK");
}

export function marketDiagnosticFieldsList(diagnostic) {
  if (!diagnostic || typeof diagnostic !== "object") return [];
  if (Array.isArray(diagnostic.field_list)) return diagnostic.field_list;
  if (Array.isArray(diagnostic.fields)) return diagnostic.fields;
  if (diagnostic.fields && typeof diagnostic.fields === "object") {
    return Object.values(diagnostic.fields);
  }
  return [];
}

export function readinessFieldStatus(snapshot, fieldName) {
  const readiness = snapshot && snapshot.market_data_readiness;
  if (!readiness || typeof readiness !== "object") return "";
  const fields = readiness.fields;
  if (fields && typeof fields === "object" && !Array.isArray(fields)) {
    return fields[fieldName] && fields[fieldName].status ? fields[fieldName].status : "";
  }
  const field = marketDiagnosticFieldsList(readiness).find((item) => item && item.name === fieldName);
  return field && field.status ? field.status : "";
}

export function setupMarketReadinessIssue(quote) {
  const diagnostic = setupMarketDataDiagnostic(quote);
  const rawStatus = diagnostic && diagnostic.status ? String(diagnostic.status) : "";
  const status = rawStatus.toUpperCase();
  if (!status || ["READY", "OK"].includes(status)) return null;
  const missing = setupMarketDiagnosticMissing(diagnostic);
  const mode = String((diagnostic && diagnostic.mode) || (quote && quote.mode) || "").toUpperCase();
  const warnings = Array.isArray(diagnostic && diagnostic.warnings) ? diagnostic.warnings : [];
  const blockingReasons = Array.isArray(diagnostic && diagnostic.blocking_reasons)
    ? diagnostic.blocking_reasons
    : [];
  const liveWarning = warnings.includes("WARNING_NOT_LIVE_MARKET_DATA");
  const liveBlocked = blockingReasons.includes("BLOCKED_NOT_LIVE_MARKET_DATA")
    || missing.includes("live_market_data");
  const atrMissing = missing.includes("atr_1h") || missing.includes("bars_1h");
  if (mode === "PAPER" && (liveWarning || atrMissing)) {
    const parts = ["Mode PAPER : surveillance active."];
    if (liveWarning) {
      parts.push("Warning : donnee live non confirmee, utilisation possible pour test paper uniquement.");
    }
    if (atrMissing) {
      parts.push("ATR 1h : indisponible ou en cours d'initialisation.");
    }
    return {
      status: rawStatus,
      missing,
      message: parts.join(" "),
    };
  }
  if (mode === "LIVE" && liveBlocked) {
    return {
      status: rawStatus,
      missing,
      message: "Mode LIVE : ordre automatique bloque. Raison : donnee marche live non confirmee.",
    };
  }
  const prefix = status === "NO_MARKET_DATA"
    ? "donnees marche absentes"
    : "donnees marche incompletes";
  return {
    status: rawStatus,
    missing,
    message: `${prefix}: ${missing.length ? missing.join(", ") : rawStatus}`,
  };
}

export function marketDataDiagnosticField(
  name,
  value,
  source,
  blocking = true,
  requiredFor = "analysis",
  minimum = null,
  statusOverride = "",
) {
  const number = numberOrNull(value);
  const ok = minimum !== null
    ? number !== null && number >= minimum
    : value !== null && value !== undefined && value !== "";
  const status = statusOverride || (ok ? "OK" : "MISSING");
  return {
    name,
    status,
    value: value ?? null,
    source: source || "",
    last_update: "",
    blocking: Boolean(blocking && status !== "OK"),
    required_for: requiredFor,
  };
}

export function marketReadinessStatusFromMissing(missing) {
  if (!missing.length) return "READY";
  const liveMissing = missing.includes("live_market_data");
  const indicatorMissing = missing.some((item) => ["atr_15m", "atr_1h", "bars_15m", "bars_1h"].includes(item));
  const otherMissing = missing.some((item) => !["live_market_data", "atr_15m", "atr_1h", "bars_15m", "bars_1h"].includes(item));
  if (liveMissing && !indicatorMissing && !otherMissing) return "PAUSED_NOT_LIVE_MARKET_DATA";
  if (indicatorMissing && !liveMissing && !otherMissing) return "PAUSED_MISSING_INDICATOR_DATA";
  return "PAUSED_MISSING_MARKET_DATA";
}

export function renderAnalysisCheck(check) {
  const state = normalizeAnalysisState(check.state);
  const stateLabel = {
    ok: "OK",
    wait: "Attente",
    bad: "Bloque",
    info: "Info",
  }[state] || "Info";
  const detailParts = [];
  if (check.actual !== undefined) detailParts.push(`Actuel: ${formatDetailValue(check.actual)}`);
  if (check.expected !== undefined) detailParts.push(`Attendu: ${formatDetailValue(check.expected)}`);
  if (check.detail) detailParts.push(check.detail);
  return `
    <article class="analysis-check ${escapeHtml(state)}">
      <span class="analysis-check-state">${escapeHtml(stateLabel)}</span>
      <div>
        <span>${escapeHtml(check.label || "Condition")}</span>
        <strong>${escapeHtml(check.summary || check.label || "Condition")}</strong>
      </div>
      <p>${escapeHtml(detailParts.join(" | ") || "-")}</p>
    </article>
  `;
}

export function normalizeAnalysisState(value) {
  const state = String(value || "info").toLowerCase();
  if (["ok", "wait", "bad", "info"].includes(state)) return state;
  if (state === "warn" || state === "waiting") return "wait";
  if (state === "error" || state === "blocked") return "bad";
  return "info";
}

export function fallbackAnalysisTrace(setup, latestQuote, item) {
  const readiness = entryReadiness(setup, latestQuote);
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = quoteVolumeRatio(latestQuote);
  const checks = [
    {
      label: "Suivi setup",
      state: "ok",
      actual: "surveille",
      expected: "surveille",
    },
    {
      label: "Execution auto TWS",
      state: setupAutoExecutionEnabled(setup) ? "ok" : "wait",
      actual: setupAutoExecutionEnabled(setup) ? "ON" : "OFF",
      expected: "ON pour envoyer un ordre automatiquement",
    },
    {
      label: "Statut suivi",
      state: SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status) ? "bad" : "wait",
      actual: setup.status,
      expected: "statut non terminal",
    },
    {
      label: "Prix vs resistance",
      state: levels.resistance === null ? "info" : (price !== null && price >= levels.resistance ? "ok" : "wait"),
      actual: maybeMoney(price),
      expected: levels.resistance === null ? "non renseigne" : maybeMoney(levels.resistance),
    },
    {
      label: "Volume relatif",
      state: levels.volumeMin === null ? "info" : (volumeRatio !== null && volumeRatio >= levels.volumeMin ? "ok" : "wait"),
      actual: numberText(volumeRatio, 3),
      expected: volumeThresholdText(setupVolumeThresholds(setup)),
    },
    {
      label: "Signal entree",
      state: readiness.missing.length ? "wait" : "ok",
      actual: readiness.missing.length ? readiness.missing.join(" | ") : "conditions locales OK",
      expected: "ENTRY_READY",
    },
  ];
  return {
    phase: setup.status || "Surveillance",
    summary: item && item.reason ? item.reason : readiness.label,
    next_step: item ? nextStepFromAction(item.action, item.reason) : "Attendre le prochain scan stock.",
    checks,
  };
}

export function setupAnalysisCandleText(snapshot) {
  if (!snapshot) return "-";
  const open = maybeMoney(snapshot.open);
  const high = maybeMoney(snapshot.high);
  const low = maybeMoney(snapshot.low);
  const close = maybeMoney(snapshot.close);
  return `O ${open} H ${high} L ${low} C ${close}`;
}

export function nextStepFromAction(action, reason) {
  if (action === "ENTRY_READY") return "Verifier le risque puis envoyer l'ordre d'entree.";
  if (action === "STATUS_CHANGE") return "Changer de phase et continuer la surveillance.";
  if (action === "INVALIDATE") return "Invalider le setup.";
  if (action === "RAISE_STOP") return "Monter le stop de protection.";
  return reason ? `Continuer a surveiller: ${reason}` : "Attendre le prochain scan stock.";
}

export function analysisTimelineEvents(setup, events) {
  const interestingTypes = new Set([
    "stock_analysis",
    "stock_analysis_skipped",
    "stock_quote_missing",
    "entry_rejected_by_risk",
    "entry_signal_rejected",
    "entry_order_submitted",
    "entry_order_rejected",
    "duplicate_order_blocked",
    "broker_mode_mismatch",
    "setup_status_changed",
  ]);
  return (events || []).filter((event) => {
    if (!interestingTypes.has(event.event_type)) return false;
    if (event.setup_id && event.setup_id === setup.setup_id) return true;
    if (event.symbol && event.symbol === setup.symbol) {
      if (event.event_type !== "stock_analysis") return true;
      return Boolean(analysisItemForSetup(setup, event));
    }
    return false;
  });
}

export function latestAnalysisForSetup(setup, events) {
  return (events || []).find((event) => {
    if (event.event_type !== "stock_analysis") return false;
    if (event.setup_id === setup.setup_id) return true;
    const data = event.data && typeof event.data === "object" ? event.data : {};
    const processed = Array.isArray(data.processed) ? data.processed : [];
    if (!processed.length) return event.symbol === setup.symbol;
    return processed.some((item) => item.setup_id === setup.setup_id || item.symbol === setup.symbol);
  }) || null;
}

export function analysisItemForSetup(setup, event) {
  if (!event) return null;
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const processed = Array.isArray(data.processed) ? data.processed : [];
  if (!processed.length) return null;
  return processed.find((candidate) => (
    candidate.setup_id === setup.setup_id || candidate.symbol === setup.symbol
  )) || null;
}

export function analysisSnapshot(event) {
  const data = event && event.data && typeof event.data === "object" ? event.data : {};
  return data.snapshot && typeof data.snapshot === "object" ? data.snapshot : null;
}

export function setupAnalysisDecision(setup, event) {
  if (!event) return {};
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const processed = Array.isArray(data.processed) ? data.processed : [];
  const item = analysisItemForSetup(setup, event) || processed[0] || {};
  const entryDecision = entryDecisionFromAnalysisItem(item);
  return {
    action: item.action || item.signal || data.action || data.signal || "",
    reason: item.reason || data.reason || event.message || "",
    entry_decision: entryDecision,
    display_title: entryDecision ? entryDecision.display_title : "",
    display_message: entryDecision ? entryDecision.display_message : "",
  };
}

export function entryDecisionFromAnalysisItem(item) {
  if (!item || typeof item !== "object") return null;
  const metadata = item.metadata && typeof item.metadata === "object" ? item.metadata : {};
  if (metadata.entry_decision && typeof metadata.entry_decision === "object") {
    return metadata.entry_decision;
  }
  const analysis = metadata.analysis && typeof metadata.analysis === "object" ? metadata.analysis : {};
  if (analysis.entry_decision && typeof analysis.entry_decision === "object") {
    return analysis.entry_decision;
  }
  return null;
}

export function entryDecisionForSetup(setup, events) {
  const event = latestAnalysisForSetup(setup, events);
  const item = analysisItemForSetup(setup, event);
  return entryDecisionFromAnalysisItem(item);
}

export function entryReadiness(setup, latestQuote, entryDecision = null) {
  if (entryDecision && typeof entryDecision === "object") {
    const missing = [
      ...(Array.isArray(entryDecision.blocking_reasons) ? entryDecision.blocking_reasons : []),
      ...(Array.isArray(entryDecision.missing_conditions) ? entryDecision.missing_conditions : []),
    ];
    return {
      label: entryDecision.display_title || entryDecision.status || "-",
      missing,
    };
  }
  const config = setup.config || {};
  const entry = config.entry || {};
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = quoteVolumeRatio(latestQuote);
  const marketIssue = setupMarketReadinessIssue(latestQuote);
  const missing = [];
  if (entry.enabled === false) missing.push("entree OFF");
  if (SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status)) missing.push("statut bloque");
  if (marketIssue) missing.push(marketIssue.message);
  if (levels.resistance !== null) {
    if (price === null) missing.push("prix manquant");
    else if (price < levels.resistance) missing.push("prix sous resistance");
  }
  if ((setup.setup_type || config.setup_type) === "momentum_breakout") {
    if (volumeRatio === null) missing.push("volume manquant");
    else if (levels.volumeMin !== null && volumeRatio < levels.volumeMin) {
      missing.push("volume insuffisant");
    }
  }
  return {
    label: missing.length ? (marketIssue ? "Attente donnees" : "Attente") : "Entree possible",
    missing,
  };
}

export function setupTradeLevels(setup) {
  const config = setup.config || {};
  const breakout = config.breakout || {};
  const entry = config.entry || {};
  const trailing = config.trailing_stop_loss || {};
  const volumeThresholds = setupVolumeThresholds(setup);
  const resistance = firstNumber(breakout.resistance, config.resistance);
  const triggerOffset = firstNumber(entry.trigger_offset, config.trigger_offset, 0);
  const limitOffset = firstNumber(entry.limit_offset, config.limit_offset, 0);
  const trigger = firstNumber(
    setup.entry_trigger,
    resistance === null ? null : resistance + triggerOffset,
  );
  const limit = firstNumber(
    setup.maximum_limit_price,
    trigger === null ? null : trigger + limitOffset,
  );
  return {
    resistance,
    trigger,
    limit,
    stop: firstNumber(trailing.initial_stop),
    volumeMin: volumeThresholds.confirmed,
    volumeNormal: volumeThresholds.normal,
    volumeFast: volumeThresholds.fast,
    triggerOffset,
    limitOffset,
  };
}

export function setupVolumeThresholds(setup) {
  const config = setup.config || {};
  const breakout = config.breakout || {};
  const volume = config.volume_confirmation || {};
  return {
    confirmed: firstNumber(
      volume.confirmed_volume_ratio_min,
      breakout.confirmed_breakout_volume_ratio_min,
      breakout.volume_above_average,
      config.volume_above_average,
      0.8,
    ),
    normal: firstNumber(volume.normal_volume_ratio_min, 1.0),
    fast: firstNumber(
      volume.fast_volume_ratio_min,
      breakout.fast_breakout_volume_ratio_min,
      breakout.volume_above_average,
      1.5,
    ),
  };
}

export function volumeThresholdText(thresholds) {
  return [
    `confirm ${numberText(thresholds.confirmed, 2)}x`,
    `normal ${numberText(thresholds.normal, 2)}x`,
    `fast ${numberText(thresholds.fast, 2)}x`,
  ].join(" | ");
}

export function volumeConditionText(setup, latestQuote) {
  const ratio = quoteVolumeRatio(latestQuote);
  const thresholds = setupVolumeThresholds(setup);
  if (ratio === null) return `ratio - | seuil ${numberText(thresholds.confirmed, 2)}x`;
  let status = "faible";
  if (thresholds.fast !== null && ratio >= thresholds.fast) status = "fast";
  else if (thresholds.normal !== null && ratio >= thresholds.normal) status = "normal";
  else if (thresholds.confirmed !== null && ratio >= thresholds.confirmed) status = "a confirmer";
  return `${numberText(ratio, 2)}x ${status} | seuil ${numberText(thresholds.confirmed, 2)}x`;
}

export function setupPriceAtPlacement(setup) {
  const config = setup.config || {};
  const entry = config.entry || {};
  const placementPrice = firstNumber(
    entry.placement_price,
    entry.reference_price,
    entry.entry_price,
    entry.trigger_price,
    setup.entry_trigger,
  );
  return placementPrice;
}

export function setupStatusTone(status) {
  if (String(status || "").startsWith("WAITING")) return "warn";
  if (["PAUSED", "SUBMITTED"].includes(status)) return "warn";
  if (["ERROR"].includes(status)) return "bad";
  return "ok";
}

export function displaySetupStatus(setup) {
  const events = (latestSnapshot && Array.isArray(latestSnapshot.events))
    ? latestSnapshot.events
    : [];
  const latestQuote = latestQuoteForSymbol(events, setup.symbol);
  const currentPrice = quotePrice(latestQuote);
  const config = setup.config || {};
  const entry = config.entry || {};
  const levels = setupTradeLevels(setup);
  const autoExecutionEnabled = setupAutoExecutionEnabled(setup);
  const currentStatus = String(setup.status || "");

  // A bare "BLOCKED" badge hides what is actually wrong: surface the concrete
  // blocking reason (broker down, spread, missing data...) as the badge itself.
  if (currentStatus.toUpperCase() === "BLOCKED") {
    const reason = setupStatusReason(setup);
    if (reason) return { status: reason, detail: "Statut moteur: BLOCKED" };
  }

  if (currentStatus !== "WAITING_ACTIVATION" || currentPrice === null || levels.trigger === null) {
    return { status: currentStatus, detail: "" };
  }

  const trigger = levels.trigger;
  const limit = levels.limit;
  const staleSetup = config.stale_setup || {};
  const maxAllowedDistancePct = firstNumber(
    staleSetup.price_too_far_above_trigger_pct,
    1.5,
  );
  const maxAllowedDistanceAbs = firstNumber(
    staleSetup.price_too_far_above_trigger_abs,
    2.0,
  );
  const triggerDistanceAbs = currentPrice - trigger;
  const triggerDistancePct = trigger > 0 ? (triggerDistanceAbs / trigger) * 100 : null;
  const isTooFarAboveTrigger = (
    triggerDistanceAbs > maxAllowedDistanceAbs
    && (triggerDistancePct === null || triggerDistancePct > maxAllowedDistancePct)
  );

  if (currentPrice < trigger) {
    return { status: "WAITING_TRIGGER", detail: buildTriggerStatusDetail(trigger, currentPrice) };
  }

  if (limit !== null && currentPrice > limit) {
    return {
      status: "ENTRY_LIMIT_EXCEEDED",
      detail: buildEntryLimitExceededDetail(trigger, limit, currentPrice, triggerDistanceAbs, triggerDistancePct),
    };
  }

  if (isTooFarAboveTrigger) {
    return {
      status: "PRICE_TOO_FAR_ABOVE_TRIGGER",
      detail: buildPriceTooFarDetail(trigger, currentPrice, triggerDistanceAbs, triggerDistancePct),
    };
  }

  if (!autoExecutionEnabled) {
    return {
      status: "WATCH_ONLY_TRIGGERED",
      detail: buildWatchOnlyDetail(trigger, currentPrice),
    };
  }

  if (entry.enabled === false) {
    return {
      status: "TRIGGER_REACHED",
      detail: buildTriggerReachedDetail(trigger, currentPrice),
    };
  }

  return {
    status: "ENTRY_READY",
    detail: buildEntryReadyDetail(trigger, limit, currentPrice),
  };
}

export function buildTriggerStatusDetail(trigger, currentPrice) {
  return `Le prix est encore sous le trigger. Trigger: ${maybeMoney(trigger)} | Prix actuel: ${maybeMoney(currentPrice)}`;
}

export function buildTriggerReachedDetail(trigger, currentPrice) {
  return `Le trigger a ete atteint. Trigger: ${maybeMoney(trigger)} | Prix actuel: ${maybeMoney(currentPrice)}`;
}

export function buildWatchOnlyDetail(trigger, currentPrice) {
  return `Trigger atteint, mais execution auto desactivee. Trigger: ${maybeMoney(trigger)} | Prix actuel: ${maybeMoney(currentPrice)}`;
}

export function buildEntryReadyDetail(trigger, limit, currentPrice) {
  const limitText = limit === null ? "-" : maybeMoney(limit);
  return `Toutes les conditions sont valides. Trigger: ${maybeMoney(trigger)} | Limite: ${limitText} | Prix actuel: ${maybeMoney(currentPrice)}`;
}

export function buildEntryLimitExceededDetail(trigger, limit, currentPrice, distanceAbs, distancePct) {
  const pctText = distancePct === null ? "-" : `${distancePct >= 0 ? "+" : ""}${distancePct.toFixed(2)} %`;
  return `Le prix a depasse la limite d'entree. Trigger: ${maybeMoney(trigger)} | Limite: ${maybeMoney(limit)} | Prix actuel: ${maybeMoney(currentPrice)} | Ecart: ${distanceAbs >= 0 ? "+" : ""}${distanceAbs.toFixed(2)} / ${pctText}`;
}

export function buildPriceTooFarDetail(trigger, currentPrice, distanceAbs, distancePct) {
  const pctText = distancePct === null ? "-" : `${distancePct >= 0 ? "+" : ""}${distancePct.toFixed(2)} %`;
  return [
    "Le prix a deja depasse le trigger et se trouve trop loin de la zone d'entree prevue.",
    `Trigger: ${maybeMoney(trigger)}`,
    `Prix actuel: ${maybeMoney(currentPrice)}`,
    `Ecart: ${distanceAbs >= 0 ? "+" : ""}${distanceAbs.toFixed(2)} / ${pctText}`,
    "Action recommandee: corriger le setup, mettre a jour le trigger/entry limit, ou attendre un retest.",
  ].join(" | ");
}
