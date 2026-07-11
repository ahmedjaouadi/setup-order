import { api, optionalApi } from "./api-client.js";
import { copySetupDetailInfoToClipboard } from "./clipboard.js";
import { renderEvents } from "./events-logs.js";
import {
  latestQuoteFromEvents,
  mergeMarketSnapshots,
  normalizeSetupChartTimeframe,
  quotePrice,
  quoteVolumeRatio,
} from "./market-quotes.js";
import {
  SETUP_ENTRY_BLOCKING_STATUSES,
  analysisItemForSetup,
  analysisSnapshot,
  analysisTimelineEvents,
  entryDecisionForSetup,
  entryReadiness,
  fallbackAnalysisTrace,
  latestAnalysisForSetup,
  nextStepFromAction,
  readinessFieldStatus,
  renderAnalysisCheck,
  setupAnalysisCandleText,
  setupAnalysisDecision,
  setupAutoExecutionEnabled,
  setupMarketDataDiagnostic,
  setupMarketReadinessIssue,
  setupPriceAtPlacement,
  setupStatusTone,
  setupTradeLevels,
  setupVolumeThresholds,
  volumeConditionText,
  volumeThresholdText,
} from "./setup-analysis.js";
import {
  fetchSetupChartQuotes,
  fetchSetupSymbolEvents,
  renderSetupChart,
  renderSetupChartTimeframeControls,
  setupChartTimeframeLabel,
  updateSetupChartTimeframeStatus,
} from "./setup-chart.js";
import { renderSetupConfigForm, showSetupConfigMessage, syncSetupConfigActions } from "./setup-config-editor.js";
import { renderSetupForecastPanel } from "./setup-forecast.js";
import {
  buildSetupIntelligenceState,
  emptySetupIntelligencePage,
  fetchSetupIntelligence,
  renderSetupIntelligencePanel,
  selectedIntelligenceAnalysis,
  syncCurrentSetupDetailIntelligence,
} from "./setup-intelligence.js";
import { validationMessagesText } from "./setup-messages.js";
import { formatRevalidatedAt, revalidationReasonLabel, setupOpportunityState } from "./setups-list.js";
import {
  currentSetupArmStatus,
  currentSetupConfig,
  currentSetupDetailInfo,
  currentSetupDetailSetup,
  currentSetupIntelligence,
  currentSetupSymbolEvents,
  setCurrentSetupArmStatus,
  setCurrentSetupConfig,
  setCurrentSetupDetailInfo,
  setCurrentSetupDetailSetup,
  setCurrentSetupIntelligence,
  setCurrentSetupIntelligenceComparison,
  setCurrentSetupIntelligenceSelectedId,
  setCurrentSetupSymbolEvents,
  setSetupChartTimeframe,
  setSetupConfigEditorDirty,
  setSetupConfigFormDirty,
  setupChartDataMeta,
  setupChartTimeframe,
} from "./state.js";
import {
  dlRows,
  escapeHtml,
  firstNumber,
  formatDetailValue,
  formatTime,
  maybeMoney,
  maybePercent,
  numberOrNull,
  numberText,
  onClick,
  removeUndefinedValues,
  setText,
  signedPercent,
  structuredCloneSafe,
  toast,
  yesNo,
} from "./ui-helpers.js";

export async function refreshSetupChartOnly() {
  const setup = currentSetupDetailSetup;
  if (!setup) {
    await renderSetupDetail();
    return;
  }
  const symbolEvents = currentSetupSymbolEvents || [];
  const chartQuotes = await fetchSetupChartQuotes(
    setup.symbol,
    setupChartTimeframe,
    symbolEvents,
  );
  const latestEventQuote = latestQuoteFromEvents(symbolEvents);
  const latestChartQuote = chartQuotes.length ? chartQuotes[chartQuotes.length - 1] : null;
  const latestQuote = mergeMarketSnapshots(latestEventQuote, latestChartQuote);
  updateSetupChartTimeframeStatus(setupChartTimeframe, chartQuotes);
  renderSetupMarketSummary(setup, symbolEvents, latestQuote, setupChartTimeframe);
  renderSetupChart(setup, symbolEvents, chartQuotes, setupChartTimeframe);
  if (currentSetupDetailInfo && currentSetupDetailInfo.source) {
    currentSetupDetailInfo.source.chart_timeframe = setupChartTimeframe;
    currentSetupDetailInfo.source.chart_quotes_count = chartQuotes.length;
    if (currentSetupDetailInfo.entree) {
      currentSetupDetailInfo.entree.latest_quote = latestQuote || null;
    }
    currentSetupDetailInfo.diagnostic_marche = setupMarketDataDiagnostic(latestQuote);
    renderSetupDetailJsonOutput();
  }
}

export function wireSetupChartTimeframeControls() {
  const container = document.getElementById("setup-chart-timeframes");
  if (!container) return;
  container.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-chart-timeframe]");
    if (!button) return;
    const nextTimeframe = normalizeSetupChartTimeframe(button.dataset.chartTimeframe);
    if (nextTimeframe === setupChartTimeframe) return;
    setSetupChartTimeframe(nextTimeframe);
    renderSetupChartTimeframeControls();
    try {
      await refreshSetupChartOnly();
    } catch (error) {
      toast(error.message);
    }
  });
}

export function setupDetailSummaryValues(setup) {
  const config = setup.config || {};
  const entry = config.entry || {};
  const breakout = config.breakout || {};
  const risk = config.risk || {};
  const trailing = config.trailing_stop_loss || {};
  const management = config.management || {};
  const stopManagement = management.stop_management || config.stop_management || {};
  const positionSource = config.position_source || {};
  const targets = Array.isArray(management.targets)
    ? management.targets
    : (Array.isArray(config.targets) ? config.targets : []);
  return removeUndefinedValues({
    symbol: setup.symbol,
    setup_id: setup.setup_id,
    setup_type: setup.setup_type,
    setup_role: setup.setup_role,
    direction: config.direction,
    mode: config.mode,
    enabled_db: setup.enabled,
    enabled_config: config.enabled,
    status: setup.status,
    status_reason: (() => {
      const reason = (currentSetupArmStatus && currentSetupArmStatus.status_reason)
        || setup.status_reason;
      return reason ? revalidationReasonLabel(reason) : undefined;
    })(),
    last_revalidated_at: (() => {
      const at = (currentSetupArmStatus && currentSetupArmStatus.last_revalidated_at)
        || setup.last_revalidated_at;
      return at ? `${formatTime(at)} (${formatRevalidatedAt(at)})` : undefined;
    })(),
    armable: currentSetupArmStatus ? currentSetupArmStatus.armable : undefined,
    disarmable: currentSetupArmStatus ? currentSetupArmStatus.disarmable : undefined,
    arm_target_status: currentSetupArmStatus ? currentSetupArmStatus.target_status : undefined,
    arm_errors: currentSetupArmStatus
      ? validationMessagesText(currentSetupArmStatus.arm_validation && currentSetupArmStatus.arm_validation.errors)
      : undefined,
    disarm_errors: currentSetupArmStatus
      ? validationMessagesText(currentSetupArmStatus.disarm_validation && currentSetupArmStatus.disarm_validation.errors)
      : undefined,
    resistance: breakout.resistance,
    volume_required: breakout.volume_above_average,
    relative_strength_required: breakout.relative_strength_required,
    entry_enabled: entry.enabled,
    order_type: entry.order_type,
    trigger_offset: entry.trigger_offset,
    limit_offset: entry.limit_offset,
    entry_trigger: setup.entry_trigger,
    maximum_limit_price: setup.maximum_limit_price,
    worst_case_entry_price: setup.worst_case_entry_price,
    initial_trailing_stop: firstNumber(trailing.initial_stop),
    maximum_quantity: setup.maximum_quantity,
    maximum_risk: setup.maximum_risk,
    max_risk_usd: risk.max_risk_usd,
    max_position_amount_usd: risk.max_position_amount_usd,
    position_source: setup.position_source || positionSource.mode,
    reconciliation_status: setup.reconciliation_status,
    stop_management: stopManagement.mode,
    never_lower_stop: management.never_lower_stop ?? stopManagement.never_lower_stop,
    take_profit_mode: management.take_profit_mode,
    targets: targets.length,
    last_event: setup.last_event,
    setup_added_at: formatTime(setup.created_at) || setup.created_at,
    created_at: setup.created_at,
    updated_at: setup.updated_at,
  });
}

export function renderSetupDetailSummary(setup) {
  const summary = document.getElementById("setup-detail-summary");
  if (!summary) return;
  summary.innerHTML = dlRows(setupDetailSummaryValues(setup), {
    enabled_db: "execution auto runtime",
    enabled_config: "execution auto config",
    status_reason: "status reason",
    last_revalidated_at: "last revalidated at",
    arm_target_status: "arm target status",
    arm_errors: "arm blockers",
    disarm_errors: "disarm blockers",
    entry_enabled: "entry enabled",
    maximum_limit_price: "maximum limit price",
    worst_case_entry_price: "worst case entry",
    initial_trailing_stop: "trailing_stop_loss.initial_stop",
    setup_added_at: "setup added",
    maximum_quantity: "maximum quantity",
    maximum_risk: "maximum risk",
    max_risk_usd: "max risk usd",
    max_position_amount_usd: "max position amount usd",
    relative_strength_required: "relative strength required",
    volume_required: "volume above average",
  });
}

export function setupEntryPlanValues(setup, latestQuote, entryDecision = null) {
  const levels = setupTradeLevels(setup);
  const readiness = entryReadiness(setup, latestQuote, entryDecision);
  const price = quotePrice(latestQuote);
  const volumeRatio = quoteVolumeRatio(latestQuote);
  const volumeThresholds = setupVolumeThresholds(setup);
  const currentDistanceToStop = price !== null && levels.stop !== null
    ? Math.max(price - levels.stop, 0)
    : null;
  const worstCaseRiskPerShare = levels.limit !== null && levels.stop !== null
    ? Math.max(levels.limit - levels.stop, 0)
    : null;
  const entry = ((setup.config || {}).entry || {});
  const riskDetails = entryDecision && entryDecision.planned_vs_current_risk
    ? entryDecision.planned_vs_current_risk
    : {};
  return removeUndefinedValues({
    decision: readiness.label,
    decision_status: entryDecision && entryDecision.status,
    decision_message: entryDecision && entryDecision.display_message,
    can_send_order: entryDecision ? (entryDecision.can_send_order ? "YES" : "NO") : undefined,
    next_action: entryDecision && entryDecision.next_action,
    blocking_reasons: entryDecision && Array.isArray(entryDecision.blocking_reasons) && entryDecision.blocking_reasons.length
      ? entryDecision.blocking_reasons.join(" | ")
      : undefined,
    missing: readiness.missing.length ? readiness.missing.join(" | ") : "OK",
    current_price: maybeMoney(price),
    current_executable_price: maybeMoney(riskDetails.current_executable_price),
    setup_price: maybeMoney(setupPriceAtPlacement(setup)),
    resistance: maybeMoney(levels.resistance),
    trigger_price: maybeMoney(levels.trigger),
    trigger_offset: numberText(levels.triggerOffset, 3),
    limit_price: maybeMoney(levels.limit),
    limit_offset: numberText(levels.limitOffset, 3),
    stop: maybeMoney(levels.stop),
    current_distance_to_stop: maybeMoney(currentDistanceToStop),
    worst_case_risk_per_share: maybeMoney(worstCaseRiskPerShare),
    current_risk_per_share: maybeMoney(riskDetails.current_risk_per_share),
    current_risk_for_planned_quantity: maybeMoney(riskDetails.current_risk_for_planned_quantity),
    risk_status: riskDetails.risk_status,
    volume_ratio: numberText(volumeRatio, 3),
    volume_required: volumeThresholdText(volumeThresholds),
    cancel_after_minutes: entry.cancel_if_not_filled_after_minutes,
    last_quote: latestQuote ? formatTime(latestQuote.timestamp) : "-",
  });
}

export function renderSetupEntryPlan(setup, latestQuote, entryDecision = null) {
  const container = document.getElementById("setup-entry-plan");
  if (!container) return;
  container.innerHTML = dlRows(setupEntryPlanValues(setup, latestQuote, entryDecision), {
    decision_status: "decision status",
    decision_message: "decision message",
    can_send_order: "can send order",
    next_action: "next action",
    blocking_reasons: "blocking reasons",
    current_price: "current price",
    current_executable_price: "current executable price",
    setup_price: "setup price",
    trigger_price: "trigger price",
    limit_price: "limit price",
    current_distance_to_stop: "current distance to stop",
    worst_case_risk_per_share: "worst case risk/share",
    current_risk_per_share: "current risk/share",
    current_risk_for_planned_quantity: "current risk for planned qty",
    risk_status: "risk status",
    volume_required: "volume required",
    cancel_after_minutes: "cancel after minutes",
    last_quote: "last quote",
  });
}

export function setupEntryConditions(setup, latestQuote, entryDecision = null) {
  const config = setup.config || {};
  const entry = config.entry || {};
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = quoteVolumeRatio(latestQuote);
  const marketIssue = setupMarketReadinessIssue(latestQuote);
  const autoExecution = setupAutoExecutionEnabled(setup);
  const entryEnabled = entry.enabled !== false;
  const priceReady = price !== null && levels.resistance !== null && price >= levels.resistance;
  const volumeReady = volumeRatio !== null && levels.volumeMin !== null && volumeRatio >= levels.volumeMin;
  const status = setup.status || "-";
  const blockedStatus = SETUP_ENTRY_BLOCKING_STATUSES.has(status);
  const conditions = [];
  if (entryDecision) {
    const readiness = entryDecision.readiness_label || entryDecision.status || "";
    conditions.push({
      label: "Decision moteur",
      value: entryDecision.display_title || entryDecision.status || "-",
      state: entryDecision.can_send_order
        ? "ok"
        : (String(readiness).startsWith("WAIT") ? "warn" : "bad"),
    });
  }
  return [
    ...conditions,
    {
      label: "Suivi",
      value: "ON",
      state: "ok",
    },
    {
      label: "Auto TWS",
      value: autoExecution ? "ON" : "OFF",
      state: autoExecution ? "ok" : "warn",
    },
    {
      label: "Entree",
      value: entryEnabled ? "ON" : "OFF",
      state: entryEnabled ? "ok" : "bad",
    },
    {
      label: "Prix",
      value: levels.resistance === null
        ? maybeMoney(price)
        : `${maybeMoney(price)} / ${maybeMoney(levels.resistance)}`,
      state: levels.resistance === null ? "warn" : (priceReady ? "ok" : "warn"),
    },
    {
      label: "Volume",
      value: volumeConditionText(setup, latestQuote),
      state: volumeReady ? "ok" : "warn",
    },
    {
      label: "Donnees",
      value: marketIssue ? marketIssue.status : "READY",
      state: marketIssue ? "bad" : "ok",
    },
    {
      label: "Statut",
      value: status,
      state: blockedStatus ? "bad" : setupStatusTone(status),
    },
  ];
}

export function renderSetupConditionGrid(setup, latestQuote, entryDecision = null) {
  const container = document.getElementById("setup-condition-grid");
  if (!container) return;
  const conditions = setupEntryConditions(setup, latestQuote, entryDecision);
  container.innerHTML = conditions.map((condition) => `
    <div class="condition-chip ${escapeHtml(condition.state)}">
      <span>${escapeHtml(condition.label)}</span>
      <strong>${escapeHtml(condition.value)}</strong>
    </div>
  `).join("");
}

export function renderSetupMarketSummary(setup, symbolEvents, latestQuote, timeframe = setupChartTimeframe) {
  const container = document.getElementById("setup-market-summary");
  if (!container) return;
  const analysis = latestAnalysisForSetup(setup, symbolEvents);
  const decision = setupAnalysisDecision(setup, analysis);
  const price = quotePrice(latestQuote);
  const items = [
    ["Timeframe graphe", setupChartTimeframeLabel(timeframe)],
    ["Dernier prix", maybeMoney(price)],
    ["Open", maybeMoney(latestQuote && latestQuote.open)],
    ["High", maybeMoney(latestQuote && latestQuote.high)],
    ["Low", maybeMoney(latestQuote && latestQuote.low)],
    ["Close", maybeMoney(latestQuote && latestQuote.close)],
    ["Volume ratio", numberText(quoteVolumeRatio(latestQuote), 3)],
    ["Bar size", latestQuote && latestQuote.historical_bar_size
      ? latestQuote.historical_bar_size
      : (setupChartDataMeta.historical_bar_size || "-")],
    ["Bar date", latestQuote && latestQuote.bar_date ? latestQuote.bar_date : "-"],
    ["Source", latestQuote && (latestQuote.market_data_source || latestQuote.source)
      ? (latestQuote.market_data_source || latestQuote.source)
      : "-"],
    ["Flux live", latestQuote && latestQuote.live_quote_source ? latestQuote.live_quote_source : "-"],
    ["Readiness", latestQuote && latestQuote.market_data_readiness
      ? latestQuote.market_data_readiness.status
      : "-"],
    ["Derniere quote", latestQuote ? formatTime(latestQuote.timestamp) : "-"],
    ["Derniere analyse", analysis ? formatTime(analysis.timestamp) : "-"],
    ["Signal", decision.action || "-"],
    ["Raison", decision.reason || "-"],
  ];
  container.innerHTML = items.map(([label, value]) => `
    <div class="market-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatDetailValue(value))}</strong>
    </div>
  `).join("");
}

export const SETUP_ANALYSIS_OVERVIEW_LABELS = {
  phase: "Phase",
  proximite_setup: "Proximite setup",
  decision_moteur: "Decision moteur",
  raison: "Raison",
  prochaine_action: "Prochaine action",
  execution_auto_tws: "Auto TWS",
  derniere_analyse: "Derniere analyse",
  dernier_prix_analyse: "Dernier prix analyse",
  bid: "Bid",
  ask: "Ask",
  bougie: "Bougie",
  volume_ferme: "Volume ferme",
  volume_live: "Volume live",
  volume_status: "Statut volume",
  liquidity_status: "Liquidite execution",
  volume_ratio: "Ratio volume",
  volume_current: "Volume courant",
  volume_average: "Volume moyen",
  volume_projected: "Volume projete",
  volume_projected_ratio: "Ratio projete",
  volume_sample_count: "Echantillon volume",
  volume_bar_closed: "Bougie volume fermee",
  volume_comparison_mode: "Comparaison volume",
  volume_interpretation: "Interpretation volume",
  session: "Session",
  spread: "Spread",
  spread_bps: "Spread bps",
  atr_15m: "ATR 15m",
  atr_1h: "ATR 1h",
  atr_1h_status: "Statut ATR 1h",
  atr_1h_source: "Source ATR 1h",
  historique_1h: "Historique 1h",
  dernier_atr_1h_ok: "Dernier ATR 1h OK",
  atr_1h_age: "Age ATR 1h",
  source_marche: "Source marche",
  flux_live: "Flux live",
  type_donnees_marche: "Type donnees",
  statut_live: "Statut live",
  readiness_level: "Readiness",
  missing_fields: "Champs manquants",
  warnings_marche: "Warnings marche",
  blocages_marche: "Blocages marche",
  erreur_ibkr: "Erreur IBKR",
  bougies_15m: "Bougies 15m",
  bougies_1h: "Bougies 1h",
  bougies_au_dessus_seuil: "Bougies au-dessus seuil",
};

export function setupAnalysisPanelValues(setup, symbolEvents, latestQuote) {
  const analysis = latestAnalysisForSetup(setup, symbolEvents);
  const item = analysisItemForSetup(setup, analysis);
  const trace = item && item.trace
    ? item.trace
    : fallbackAnalysisTrace(setup, latestQuote, item);
  const snapshot = mergeMarketSnapshots(analysisSnapshot(analysis), latestQuote) || {};
  const decision = item || setupAnalysisDecision(setup, analysis);
  const action = decision.action || "-";
  const reason = decision.reason || trace.summary || "Aucune analyse recente";
  const nextStep = trace.next_step || nextStepFromAction(action, reason);
  const opportunity = setupOpportunityState(setup, item, trace);
  const analysisMetadata = item && item.metadata && typeof item.metadata === "object"
    ? item.metadata
    : {};
  const analysisPayload = analysisMetadata.analysis && typeof analysisMetadata.analysis === "object"
    ? analysisMetadata.analysis
    : {};
  const validation = analysisPayload.validation && typeof analysisPayload.validation === "object"
    ? analysisPayload.validation
    : {};
  const volumeConfirmation = validation.volume_confirmation && typeof validation.volume_confirmation === "object"
    ? validation.volume_confirmation
    : {};
  const bid = numberOrNull(snapshot.bid);
  const ask = numberOrNull(snapshot.ask);
  const spread = numberOrNull(snapshot.spread) !== null
    ? numberOrNull(snapshot.spread)
    : (bid !== null && ask !== null ? Math.max(ask - bid, 0) : null);
  const midPrice = bid !== null && ask !== null ? (bid + ask) / 2 : null;
  const spreadBps = numberOrNull(snapshot.spread_bps) !== null
    ? numberOrNull(snapshot.spread_bps)
    : (spread !== null && midPrice !== null && midPrice > 0
    ? (spread / midPrice) * 10000
    : null);
  const timelineEvents = analysisTimelineEvents(setup, symbolEvents).slice(0, 8);
  return {
    overview: removeUndefinedValues({
      phase: trace.phase || setup.status || "-",
      proximite_setup: maybePercent(opportunity.percent),
      decision_moteur: action,
      raison: reason,
      prochaine_action: nextStep,
      execution_auto_tws: opportunity.autoExecution ? "ON" : "OFF",
      derniere_analyse: analysis ? formatTime(analysis.timestamp) : "-",
      dernier_prix_analyse: maybeMoney(firstNumber(snapshot.price, snapshot.close)),
      bid: maybeMoney(snapshot.bid),
      ask: maybeMoney(snapshot.ask),
      bougie: setupAnalysisCandleText(snapshot),
      volume_status: volumeConfirmation.status || snapshot.volume_status || "-",
      liquidity_status: volumeConfirmation.liquidity_status || "-",
      volume_ratio: numberText(firstNumber(volumeConfirmation.ratio, snapshot.volume_ratio_15m, snapshot.volume_ratio_closed_bar, snapshot.volume_ratio), 3),
      volume_current: maybeMoney(firstNumber(volumeConfirmation.current_bar_volume, snapshot.bar_volume_15m, snapshot.current_bar_volume, snapshot.volume)),
      volume_average: maybeMoney(firstNumber(volumeConfirmation.average_bar_volume, snapshot.avg_volume_15m)),
      volume_projected: maybeMoney(firstNumber(volumeConfirmation.projected_bar_volume, snapshot.projected_volume)),
      volume_projected_ratio: numberText(firstNumber(volumeConfirmation.live_projected_volume_ratio, snapshot.volume_ratio_live), 3),
      volume_sample_count: volumeConfirmation.sample_count ?? snapshot.volume_sample_count ?? "-",
      volume_bar_closed: volumeConfirmation.current_bar_is_closed === undefined
        ? "-"
        : yesNo(volumeConfirmation.current_bar_is_closed),
      volume_comparison_mode: volumeConfirmation.comparison_mode || snapshot.volume_comparison_mode || "-",
      volume_interpretation: volumeConfirmation.interpretation || "-",
      volume_ferme: numberText(firstNumber(snapshot.volume_ratio_15m, snapshot.volume_ratio_closed_bar, snapshot.volume_ratio), 3),
      volume_live: numberText(snapshot.volume_ratio_live, 3),
      session: snapshot.session || "-",
      spread: maybeMoney(spread),
      spread_bps: numberText(spreadBps, 2),
      atr_15m: maybeMoney(snapshot.atr_15m),
      atr_1h: maybeMoney(snapshot.atr_1h),
      atr_1h_status: snapshot.atr_1h_status || readinessFieldStatus(snapshot, "atr_1h") || "-",
      atr_1h_source: snapshot.atr_1h_bar_size
        ? `${snapshot.atr_1h_bar_size} / ${snapshot.atr_1h_duration || "-"} / RTH ${yesNo(snapshot.atr_1h_use_rth)}`
        : "-",
      historique_1h: snapshot.historical_1h_available === undefined
        ? "-"
        : `${yesNo(snapshot.historical_1h_available)} (${snapshot.bars_1h_count ?? 0}/${snapshot.bars_required_for_atr ?? 15})`,
      dernier_atr_1h_ok: snapshot.last_successful_atr_1h
        ? `${maybeMoney(snapshot.last_successful_atr_1h)} @ ${formatTime(snapshot.last_successful_atr_1h_at)}`
        : "-",
      atr_1h_age: snapshot.atr_1h_age_seconds !== undefined
        ? `${numberText(snapshot.atr_1h_age_seconds, 0)}s`
        : "-",
      source_marche: snapshot.market_data_source || snapshot.source || "-",
      flux_live: snapshot.live_quote_source || "-",
      type_donnees_marche: snapshot.market_data_type_actual ?? snapshot.market_data_type_requested ?? "-",
      statut_live: snapshot.live_market_data_status || readinessFieldStatus(snapshot, "live_market_data") || "-",
      readiness_level: (snapshot.market_data_readiness || {}).status || "-",
      missing_fields: Array.isArray((snapshot.market_data_readiness || {}).missing)
        ? snapshot.market_data_readiness.missing.join(", ") || "OK"
        : "-",
      warnings_marche: Array.isArray((snapshot.market_data_readiness || {}).warnings)
        ? snapshot.market_data_readiness.warnings.join(", ") || "-"
        : "-",
      blocages_marche: Array.isArray((snapshot.market_data_readiness || {}).blocking_reasons)
        ? snapshot.market_data_readiness.blocking_reasons.join(", ") || "-"
        : "-",
      erreur_ibkr: snapshot.last_ibkr_error_message
        ? `${snapshot.last_ibkr_error_code || "-"} ${snapshot.last_ibkr_error_message}`
        : "-",
      bougies_15m: snapshot.bars_15m_count ?? "-",
      bougies_1h: snapshot.bars_1h_count ?? "-",
      bougies_au_dessus_seuil: snapshot.bars_above_resistance ?? "-",
    }),
    checks: Array.isArray(trace.checks) ? trace.checks : [],
    timeline: timelineEvents.map((event) => {
      const eventItem = analysisItemForSetup(setup, event);
      return {
        timestamp: event.timestamp || "",
        event_type: event.event_type || "",
        level: event.level || "",
        action: eventItem ? eventItem.action : event.event_type,
        reason: eventItem ? eventItem.reason : event.message,
        message: event.message || "",
      };
    }),
    trace,
    latest_analysis_event: analysis || null,
    latest_analysis_item: item || null,
    snapshot,
  };
}

export function renderSetupAnalysisPanel(setup, symbolEvents, latestQuote) {
  const overview = document.getElementById("setup-analysis-overview");
  const checks = document.getElementById("setup-analysis-checks");
  const timeline = document.getElementById("setup-analysis-timeline");
  if (!overview && !checks && !timeline) return;
  const data = setupAnalysisPanelValues(setup, symbolEvents, latestQuote);

  if (overview) {
    overview.innerHTML = Object.entries(data.overview).map(([key, value]) => `
      <div class="analysis-cell">
        <span>${escapeHtml(SETUP_ANALYSIS_OVERVIEW_LABELS[key] || key)}</span>
        <strong>${escapeHtml(formatDetailValue(value))}</strong>
      </div>
    `).join("");
  }

  if (checks) {
    const checkItems = data.checks;
    checks.innerHTML = checkItems.map((check) => renderAnalysisCheck(check)).join("")
      || `<article class="analysis-check info">
        <span class="analysis-check-state">INFO</span>
        <div><strong>Aucune trace detaillee</strong></div>
        <p>Le prochain scan stock remplira cette section.</p>
      </article>`;
  }

  if (timeline) {
    timeline.innerHTML = data.timeline.map((event) => `
        <article class="analysis-event">
          <time class="analysis-event-meta">${escapeHtml(formatTime(event.timestamp))}</time>
          <span class="analysis-event-meta">${escapeHtml(event.action || event.level || "-")}</span>
          <div>
            <strong>${escapeHtml(event.event_type)}</strong>
            <div>${escapeHtml(event.reason || event.message || "-")}</div>
          </div>
        </article>
      `).join("") || `<article class="analysis-event">
      <span class="analysis-event-meta">-</span>
      <span class="analysis-event-meta">INFO</span>
      <div><strong>Aucune analyse</strong><div>Aucun evenement d'analyse pour ce setup.</div></div>
    </article>`;
  }
}

export function buildSetupDetailInfo(setup, symbolEvents, latestQuote, chartQuotes, setupEvents, intelligence) {
  const entryDecision = entryDecisionForSetup(setup, symbolEvents);
  return {
    generated_at: new Date().toISOString(),
    setup_id: setup.setup_id,
    symbol: setup.symbol,
    setup_type: setup.setup_type,
    entree: {
      entry_decision: entryDecision,
      conditions: setupEntryConditions(setup, latestQuote, entryDecision),
      plan: setupEntryPlanValues(setup, latestQuote, entryDecision),
      latest_quote: latestQuote || null,
    },
    suivi_analyse: setupAnalysisPanelValues(setup, symbolEvents, latestQuote),
    diagnostic_marche: setupMarketDataDiagnostic(latestQuote),
    etat: setupDetailSummaryValues(setup),
    intelligence: intelligence || null,
    source: {
      setup_endpoint: `/api/setups/${encodeURIComponent(setup.setup_id)}`,
      intelligence_latest_endpoint: `/api/intelligence/setups/${encodeURIComponent(setup.setup_id)}/latest`,
      intelligence_history_endpoint: `/api/intelligence/setups/${encodeURIComponent(setup.setup_id)}/analyses`,
      symbol_events_count: Array.isArray(symbolEvents) ? symbolEvents.length : 0,
      setup_events_count: Array.isArray(setupEvents) ? setupEvents.length : 0,
      chart_timeframe: setupChartTimeframe,
      chart_quotes_count: Array.isArray(chartQuotes) ? chartQuotes.length : 0,
    },
  };
}

export function wireSetupDetailJsonButton() {
  onClick("setup-detail-json-button", async () => {
    try {
      // navigator.clipboard writes (and their execCommand fallback) only work
      // within the brief user-gesture window. renderSetupDetail() below makes
      // several sequential API calls and can easily outlast that window, so a
      // write issued only after awaiting it can silently become a no-op.
      // ClipboardItem accepts a Blob *promise*, which keeps the write tied to
      // this click's activation even though the data resolves later.
      const infoPromise = (async () => {
        await renderSetupDetail();
        renderSetupDetailJsonOutput(true);
        return currentSetupDetailInfo;
      })();
      const copied = await copySetupDetailInfoToClipboard(infoPromise);
      toast(copied ? "Infos detaillees copiees dans le presse-papiers" : "Infos detaillees chargees en JSON");
    } catch (error) {
      toast(error.message);
    }
  });
}

export async function renderSetupDetail() {
  const setupId = document.body.dataset.setupId;
  if (!setupId) return;
  const intelligencePromise = fetchSetupIntelligence(setupId).catch(() => emptySetupIntelligencePage());
  const result = await api(`/api/setups/${encodeURIComponent(setupId)}`);
  const setup = result.setup;
  setCurrentSetupDetailSetup(setup);
  setCurrentSetupArmStatus(null);
  try {
    setCurrentSetupArmStatus(await optionalApi(`/api/setups/${encodeURIComponent(setupId)}/arm-status`));
  } catch (error) {
    toast(`Statut armement indisponible: ${error.message}`);
  }
  setCurrentSetupIntelligence(buildSetupIntelligenceState(null, setupId, emptySetupIntelligencePage()));
  setCurrentSetupIntelligenceComparison(null);
  setCurrentSetupIntelligenceSelectedId(selectedIntelligenceAnalysis(currentSetupIntelligence)?.analysis_id || null);
  let symbolEvents = [];
  try {
    symbolEvents = await fetchSetupSymbolEvents(setup.symbol);
  } catch (error) {
    toast(`Events symbole indisponibles: ${error.message}`);
  }
  setCurrentSetupSymbolEvents(symbolEvents);
  renderSetupChartTimeframeControls();
  const chartQuotes = await fetchSetupChartQuotes(
    setup.symbol,
    setupChartTimeframe,
    symbolEvents,
  );
  updateSetupChartTimeframeStatus(setupChartTimeframe, chartQuotes);
  setText("detail-title", setup.setup_id);
  setText("detail-subtitle", `${setup.symbol} - ${setup.setup_type}`);
  const latestEventQuote = latestQuoteFromEvents(symbolEvents);
  const latestChartQuote = chartQuotes.length ? chartQuotes[chartQuotes.length - 1] : null;
  const latestQuote = mergeMarketSnapshots(latestEventQuote, latestChartQuote);
  const entryDecision = entryDecisionForSetup(setup, symbolEvents);
  setCurrentSetupDetailInfo(buildSetupDetailInfo(
    setup,
    symbolEvents,
    latestQuote,
    chartQuotes,
    result.events || [],
    currentSetupIntelligence,
  ));
  syncCurrentSetupDetailIntelligence();
  renderSetupDetailSummary(setup);
  renderSetupConditionGrid(setup, latestQuote, entryDecision);
  renderSetupEntryPlan(setup, latestQuote, entryDecision);
  renderSetupAnalysisPanel(setup, symbolEvents, latestQuote);
  renderSetupMarketSummary(setup, symbolEvents, latestQuote, setupChartTimeframe);
  renderSetupChart(setup, symbolEvents, chartQuotes, setupChartTimeframe);
  const config = document.getElementById("setup-config");
  setCurrentSetupConfig(structuredCloneSafe(setup.config));
  setSetupConfigFormDirty(false);
  setSetupConfigEditorDirty(false);
  renderSetupConfigForm(currentSetupConfig);
  if (config) config.value = JSON.stringify(setup.config, null, 2);
  showSetupConfigMessage("");
  syncSetupConfigActions();
  renderSetupDetailJsonOutput();
  renderEvents("setup-events", result.events || []);
  renderSetupIntelligencePanel(currentSetupIntelligence);
  renderSetupForecastPanel(setup, { cachedOnly: true }).catch((error) => toast(error.message));
  renderSetupCreationSnapshot(setupId).catch((error) => toast(error.message));
  intelligencePromise.then((intelligence) => {
    if (!currentSetupDetailSetup || currentSetupDetailSetup.setup_id !== setupId) return;
    setCurrentSetupIntelligence(buildSetupIntelligenceState(null, setupId, intelligence));
    setCurrentSetupIntelligenceComparison(null);
    setCurrentSetupIntelligenceSelectedId(selectedIntelligenceAnalysis(currentSetupIntelligence)?.analysis_id || null);
    renderSetupIntelligencePanel(currentSetupIntelligence);
    syncCurrentSetupDetailIntelligence();
    renderSetupDetailJsonOutput();
  }).catch((error) => toast(error.message));
}

export async function renderSetupCreationSnapshot(setupId) {
  const target = document.getElementById("setup-creation-snapshot");
  if (!target) return;
  const snapshot = await optionalApi(`/api/setups/${encodeURIComponent(setupId)}/creation-snapshot`);
  const setup = currentSetupDetailSetup || {};
  if (!snapshot) {
    target.innerHTML = dlRows({
      status: "NOT_CAPTURED",
      setup_added_at: formatTime(setup.created_at) || setup.created_at || "-",
    }, {
      setup_added_at: "setup added",
    });
    return;
  }
  const drift = await optionalApi(`/api/setups/${encodeURIComponent(setupId)}/price-drift`);
  const trailing = snapshot.trailing_stop_loss || {};
  target.innerHTML = dlRows({
    setup_added_at: formatTime(setup.created_at) || setup.created_at || "-",
    creation_price: formatNumber(snapshot.last_price),
    current_price: formatNumber(drift && drift.current_price),
    move_since_creation: signedPercent(drift && drift.move_since_creation_pct),
    entry_trigger: formatNumber(snapshot.entry_trigger_price),
    initial_trailing_stop: formatNumber(trailing.initial_stop),
    distance_current_to_trigger: signedPercent(drift && drift.distance_current_to_trigger_pct),
    distance_creation_to_stop: signedPercent(snapshot.distance_to_stop_pct),
    data_quality: snapshot.data_quality_status,
    source: snapshot.source,
    captured_at: formatTime(snapshot.captured_at),
  }, {
    setup_added_at: "setup added",
    creation_price: "creation price",
    current_price: "current price",
    move_since_creation: "move since creation",
    entry_trigger: "entry trigger",
    initial_trailing_stop: "trailing_stop_loss.initial_stop",
    distance_current_to_trigger: "distance current to trigger",
    distance_creation_to_stop: "distance creation to stop",
    data_quality: "data quality",
    captured_at: "captured at",
  });
}

export function renderSetupDetailJsonOutput(forceShow = false) {
  const output = document.getElementById("setup-detail-json-output");
  if (!output || !currentSetupDetailInfo) return;
  const visible = forceShow || !output.hidden;
  output.hidden = !visible;
  if (visible) {
    output.textContent = JSON.stringify(currentSetupDetailInfo, null, 2);
  }
}
