import {
  renderSetupCreationSnapshot,
  renderSetupDetail,
  renderSetupDetailJsonOutput,
  renderSetupDetailSummary,
  wireSetupChartTimeframeControls,
  wireSetupDetailJsonButton,
} from "./setup-detail.js";
import {
  buildSetupIntelligenceState,
  emptySetupIntelligencePage,
  ensureSetupIntelligenceAnalysisLoaded,
  fetchSetupIntelligence,
  loadSetupIntelligenceHistoryPage,
  renderSetupIntelligence,
  renderSetupIntelligencePanel,
  selectedIntelligenceAnalysis,
  showSetupIntelligenceMessage,
  syncCurrentSetupDetailIntelligence,
} from "./setup-intelligence.js";
import {
  buildSetupConfigFromForm,
  parseSetupConfigEditor,
  renderSetupConfigForm,
  showSetupConfigMessage,
  syncSetupConfigActions,
} from "./setup-config-editor.js";
import { refreshForecastWatchlist, renderSetupForecastPanel, wireSetupForecastPanel } from "./setup-forecast.js";
import {
  drawSetupChart,
  fetchSetupChartQuotes,
  fetchSetupSymbolEvents,
  renderSetupChart,
  renderSetupChartTimeframeControls,
  setupChartResizeTimer,
  setupChartState,
  setupChartTimeframeLabel,
  updateSetupChartTimeframeStatus,
} from "./setup-chart.js";
import {
  renderSetupPreview,
  renderSetupPreviewError,
  renderSetupToolsError,
  renderSetupToolsOutput,
  setupTextPayload,
  syncTickerFieldFromSetupResult,
  syncTickerFieldFromSetupText,
} from "./setup-form.js";
import { renderV2Page, renderV2Table } from "./hub-pages.js";
import { brokerPnlSourceLabel, initDashboardPremium, renderDashboardPremium } from "./dashboard-premium.js";
import { renderOpportunityRadar } from "./opportunity-radar.js";
import {
  formatRevalidatedAt,
  normalizeCheckState,
  opportunityScorePayload,
  renderSetups,
  renderSetupsColumnControls,
  revalidationReasonLabel,
  setupDetailPath,
  setupOpportunityState,
  wireSetupsColumnControls,
} from "./setups-list.js";
import {
  manualOrderPayload,
  renderExecutions,
  renderLocalOrderOrphans,
  renderManualOrderRisk,
  renderOrderHistory,
  renderOrders,
  renderPositions,
} from "./orders-positions.js";
import { renderSettings, wireMarketForm } from "./settings.js";
import { renderEvents, renderLogsPage } from "./events-logs.js";
import { renderMarketContextPage, scheduleMarketContextRefresh, wireMarketContextControls } from "./market-context.js";
import {
  SETUP_ENTRY_BLOCKING_STATUSES,
  analysisItemForSetup,
  analysisSnapshot,
  analysisTimelineEvents,
  displaySetupStatus,
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
  setupStatusReason,
  setupStatusTone,
  setupTradeLevels,
  setupVolumeThresholds,
  volumeConditionText,
  volumeThresholdText,
} from "./setup-analysis.js";
import {
  SETUP_CHART_TIMEFRAMES,
  addVolumeRatios,
  compareQuotesByTime,
  extractQuoteEvents,
  latestQuoteForSymbol,
  latestQuoteFromEvents,
  mergeMarketSnapshots,
  normalizeSetupChartTimeframe,
  parseChartDate,
  quoteFromHistoricalBar,
  quotePrice,
  quoteVolumeRatio,
} from "./market-quotes.js";
import { copySetupDetailInfoToClipboard, copySetupTemplateToClipboard } from "./clipboard.js";
import { api, optionalApi } from "./api-client.js";
import { formatSetupValidationDetail, normalizeDetailMessages, validationMessagesText } from "./setup-messages.js";
import {
  compactToastMessage,
  cssSafeId,
  dlRows,
  emptyRow,
  escapeHtml,
  firstNumber,
  formData,
  formatAge,
  formatDetailValue,
  formatStatusList,
  formatTime,
  isPlainObject,
  maybeMoney,
  maybePercent,
  maybeProbability,
  money,
  numberOrNull,
  numberText,
  onClick,
  pnlClass,
  removeUndefinedValues,
  secondsSince,
  setButtonDisabled,
  setPnlTone,
  setText,
  setToneData,
  signalBadgeStyle,
  signedPercent,
  statusBadge,
  statusBadgeStyle,
  statusClassName,
  statusLabel,
  structuredCloneSafe,
  syncAgeChipLabel,
  timeWithAge,
  toast,
  toneForAge,
  formatConfigLabel,
  wireModals,
  yesNo,
} from "./ui-helpers.js";
import {
  page,
  latestSnapshot,
  currentSetupConfig,
  currentSetupDetailInfo,
  currentSetupIntelligence,
  currentSetupIntelligenceSelectedId,
  currentSetupIntelligenceComparison,
  currentSetupArmStatus,
  setupConfigFormDirty,
  setupConfigEditorDirty,
  currentSetupDetailSetup,
  currentSetupSymbolEvents,
  forecastWatchlistBySymbol,
  setupChartTimeframe,
  setupChartDataMessage,
  setupChartDataMeta,
  SETUP_CHART_DEFAULT_TIMEFRAME,
  setLatestSnapshot,
  setCurrentSetupConfig,
  setCurrentSetupDetailInfo,
  setCurrentSetupIntelligence,
  setCurrentSetupIntelligenceSelectedId,
  setCurrentSetupIntelligenceComparison,
  setCurrentSetupArmStatus,
  setSetupConfigFormDirty,
  setSetupConfigEditorDirty,
  setCurrentSetupDetailSetup,
  setCurrentSetupSymbolEvents,
  setForecastWatchlistBySymbol,
  setSetupChartTimeframe,
  setSetupChartDataMessage,
  setSetupChartDataMeta,
} from "./state.js";

let appAutoRefreshTimer = null;
let appAutoRefreshInFlight = false;

const APP_AUTO_REFRESH_INTERVAL_MS = 30000;

const activeNav = document.querySelector(`[data-nav="${page}"]`);
if (activeNav) activeNav.classList.add("active");

function renderSnapshot(snapshot) {
  setLatestSnapshot(snapshot);
  renderRuntime(snapshot.runtime || {});
  renderEngineHealth(snapshot.health || {});
  renderMetrics(snapshot.metrics || {});
  renderDashboard(snapshot);
  renderOpportunityRadar(snapshot.setups || []);
  renderSetups(snapshot.setups || []);
  renderBrokerReality(snapshot.broker_reality || {});
  renderOrders(snapshot.orders || []);
  renderOrderHistory(snapshot.order_history || []);
  renderLocalOrderOrphans(snapshot.local_order_orphans || []);
  renderPositions(snapshot.positions || []);
  renderExecutions(snapshot.executions || []);
  renderEvents("dashboard-events", snapshot.events || []);
  renderSettings(snapshot);
  if (page === "dashboard") renderDashboardPremium(snapshot);
}

function renderRuntime(runtime) {
  const modeLabel = document.getElementById("mode-label");
  if (modeLabel) {
    modeLabel.textContent = runtime.mode || runtime.broker_account_mode || "paper";
    modeLabel.title = runtime.mode_label || runtime.broker_message || "";
  }
  setStatus("top-connection-status", runtime.connection_label || runtime.connection || "DISCONNECTED");
  setStatus("top-bot-status", runtime.status_label || runtime.status || "PAUSED");
  setText("dashboard-mode", runtime.mode || runtime.broker_account_mode || "paper");
}

function setStatus(id, value) {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = value;
  element.className = "pill";
  const status = String(value || "")
    .toUpperCase()
    .replace(/^(BROKER|TRACKER|CONNECTION|BOT|AUTO|RISK|EXECUTION|SYNC)\s+/, "");
  if (["CONNECTED", "RUNNING", "OK", "ALLOWED", "READY", "PROTECTED", "ACTIVE", "FRESH"].includes(status)) {
    element.classList.add("ok");
  }
  if (["PAUSED", "WAITING", "STALE", "UNKNOWN", "DISABLED", "UNAVAILABLE", "IDLE"].includes(status)) element.classList.add("warn");
  if (["DISCONNECTED", "ERROR", "EMERGENCY_STOP", "BLOCKED", "CRITICAL", "MISMATCH", "UNPROTECTED", "UNKNOWN_CRITICAL"].includes(status)) {
    element.classList.add("danger");
  }
}

function renderEngineHealth(health) {
  // Prefer the server-computed age so the heartbeat shares the same clock as
  // the broker-sync age (broker_sync_age_seconds is also server-side). Falling
  // back to secondsSince() uses the browser clock and reintroduces skew, so it
  // is only a last resort when the server omits the value.
  const heartbeatAge = health.heartbeat_age_seconds ?? secondsSince(health.last_heartbeat_at);
  const tickAge = secondsSince(health.last_market_tick_at) ?? health.market_tick_age_seconds;
  const analysisAge = secondsSince(health.last_market_analysis_at)
    ?? health.market_analysis_age_seconds;
  const stockPollAge = secondsSince(health.last_stock_poll_at) ?? health.stock_poll_age_seconds;
  const staleAfter = Number(health.heartbeat_stale_seconds || 20);
  let status = health.status || "STARTING";
  let label = health.label || "CHECKING";
  const brokerStatus = health.broker_status || "";
  if (brokerStatus === "DISCONNECTED" || brokerStatus === "ERROR") {
    status = "BROKER_DOWN";
    label = brokerStatus === "ERROR" ? "BROKER ERROR" : "TWS OFFLINE";
  } else if (health.last_error) {
    // A caught exception anywhere in the monitor tick (stock poll, revalidate,
    // snapshot broadcast) lands in health.last_error. It is a real engine error
    // but NOT a heartbeat/liveness failure, so label it as an engine error
    // instead of impersonating a dead connection. This is why State A could show
    // "HEARTBEAT ERROR" while the broker was OK and fresh. The broker-reality
    // gate (AUTO BLOCKED / RISK CRITICAL) is driven separately and stays armed.
    status = "ERROR";
    label = "ENGINE ERROR";
  } else if (heartbeatAge === null || heartbeatAge === undefined) {
    status = "STARTING";
    label = "CHECKING";
  } else if (heartbeatAge > staleAfter) {
    status = "STALE";
    label = `STALE ${formatAge(heartbeatAge)}`;
  } else {
    status = "OK";
    label = `LIVE ${formatAge(heartbeatAge)}`;
  }
  const pill = document.getElementById("engine-health-status");
  if (pill) {
    pill.textContent = label;
    pill.className = "pill";
    if (status === "OK") pill.classList.add("ok");
    else if (["ERROR", "STALE", "BROKER_DOWN"].includes(status)) {
      pill.classList.add("danger");
    }
    else pill.classList.add("warn");
    pill.title = [
      `Heartbeat: ${heartbeatAge == null ? "-" : formatAge(heartbeatAge)}`,
      `Broker: ${brokerStatus || "-"}`,
      health.last_error ? `Erreur: ${String(health.last_error).slice(0, 160)}` : null,
      health.last_reconciliation_error
        ? `Reconciliation: ${String(health.last_reconciliation_error).slice(0, 160)}`
        : null,
      `TWS audit: ${health.tws_audit_enabled ? "ON" : "OFF"}`,
    ].filter(Boolean).join(" | ");
  }

  // Dedicated reconciliation / revalidation voyant: signals the ROOT CAUSE at
  // the source (reconciliation failing, revalidation escalated) BEFORE the
  // broker report ages past stale_after and BROKER STALE lights up. Hidden when
  // healthy to keep the rail uncluttered. Threshold 3 mirrors the server-side
  // REVALIDATE_FAILURE_BLOCK_THRESHOLD.
  const reconPill = document.getElementById("top-reconciliation");
  if (reconPill) {
    const reconError = health.last_reconciliation_error;
    const revalFailures = Number(health.revalidate_consecutive_failures || 0);
    reconPill.className = "pill";
    if (revalFailures >= 3) {
      reconPill.hidden = false;
      reconPill.textContent = "REVALIDATION DOWN";
      reconPill.classList.add("danger");
      reconPill.title = `Revalidation pre-entree en echec (${revalFailures}x) - auto execution bloquee. ${health.last_revalidate_error || ""}`.trim();
    } else if (reconError) {
      reconPill.hidden = false;
      reconPill.textContent = "RECON ERROR";
      reconPill.classList.add("danger");
      reconPill.title = `Reconciliation broker en echec: ${String(reconError).slice(0, 200)}`;
    } else if (revalFailures > 0) {
      reconPill.hidden = false;
      reconPill.textContent = `REVALIDATION ${revalFailures}x`;
      reconPill.classList.add("warn");
      reconPill.title = health.last_revalidate_error || "Revalidation pre-entree en echec transitoire";
    } else {
      reconPill.hidden = true;
      reconPill.textContent = "";
    }
  }

  const detail = document.getElementById("dashboard-engine-health");
  if (!detail) return;
  detail.innerHTML = dlRows({
    Etat: status,
    "Etat broker": brokerStatus || "-",
    "Broker tracker": health.broker_tracker_status || "-",
    "Broker sync age": health.broker_sync_age_seconds == null
      ? "-"
      : formatAge(health.broker_sync_age_seconds),
    "Auto execution broker": health.broker_reality_blocked ? "BLOCKED" : "OK",
    "Mismatch broker": health.broker_reality_mismatch_count ?? 0,
    "Blocages broker": Array.isArray(health.broker_reality_blocking_reasons)
      ? health.broker_reality_blocking_reasons.join(", ") || "-"
      : "-",
    Heartbeat: timeWithAge(health.last_heartbeat_at, heartbeatAge),
    "Check broker": timeWithAge(health.last_broker_check_at, heartbeatAge),
    "Audit TWS": health.tws_audit_enabled ? "ON" : "OFF",
    "Requetes TWS": health.tws_request_count ?? 0,
    "Derniere requete TWS": health.last_tws_request
      ? `${health.last_tws_request} ${health.last_tws_request_status || ""}`.trim()
      : "-",
    "Detail requete TWS": health.last_tws_request_detail || "-",
    "Envoi requete TWS": timeWithAge(
      health.last_tws_request_sent_at,
      secondsSince(health.last_tws_request_sent_at),
    ),
    "Reponse TWS": timeWithAge(
      health.last_tws_response_at,
      secondsSince(health.last_tws_response_at),
    ),
    "Latence TWS": health.last_tws_latency_ms == null
      ? "-"
      : `${health.last_tws_latency_ms} ms`,
    "Check setups": timeWithAge(health.last_setup_check_at, heartbeatAge),
    "Setups controles": health.last_checked_setups ?? health.active_setup_count ?? 0,
    "Scan stocks TWS": health.last_stock_poll_at
      ? timeWithAge(health.last_stock_poll_at, stockPollAge)
      : (health.last_stock_poll_reason || "Aucun scan stock"),
    "Stocks interroges": health.last_stock_poll_count ?? 0,
    "Stocks OK": health.last_stock_poll_ok ?? 0,
    "Erreurs stocks": health.last_stock_poll_errors ?? 0,
    "Symboles TWS": Array.isArray(health.last_stock_poll_symbols)
      ? health.last_stock_poll_symbols.join(", ")
      : "-",
    "Analyses stock": health.last_stock_analysis_count ?? 0,
    "Dernier tick": health.last_market_tick_at
      ? `${health.last_market_symbol || "-"} - ${timeWithAge(
        health.last_market_tick_at,
        tickAge,
      )}`
      : "Aucun tick recu",
    "Derniere analyse": health.last_market_analysis_at
      ? timeWithAge(health.last_market_analysis_at, analysisAge)
      : "Aucun tick analyse",
    "Setups analyses": health.last_processed_setups ?? 0,
    "Erreur broker": health.last_broker_error || "-",
    "Erreur requete TWS": health.last_tws_request_error || "-",
    Erreur: health.last_error || "-",
  });
}

function renderMetrics(metrics) {
  const account = metrics.account || {};
  setText("metric-account-value", maybeMoney(account.net_liquidation));
  setText("metric-cash", maybeMoney(account.cash ?? account.available_funds));
  setText("metric-buying-power", maybeMoney(account.buying_power));
  setText("metric-margin-used", maybeMoney(account.gross_position_value));
  setText("metric-pnl-yesterday", maybeMoney(metrics.pnl_until_yesterday));
  setText("metric-pnl-today", renderPnlMetricText(metrics));
  setText("metric-pnl-unrealized", maybeMoney(metrics.positions_pnl));
  setText("metric-pnl-realized", maybeMoney(account.realized_pnl));
  setText("metric-pnl-total", maybeMoney((Number(account.realized_pnl || 0) + Number(metrics.positions_pnl || 0))));
  setText("metric-pnl-source", brokerPnlSourceLabel(metrics));
  setText("dashboard-pnl-source", brokerPnlSourceLabel(metrics));
  setText("metric-active-setups", metrics.active_setups);
  setText("metric-open-positions", metrics.open_positions);
  setText("metric-open-orders", metrics.open_orders);
  setText("metric-active-broker-orders", metrics.broker_active_orders ?? metrics.open_orders);
  setText("metric-prepared-orders", metrics.broker_prepared_not_transmitted_orders ?? 0);
  setText("metric-unprotected-positions", metrics.unprotected_positions ?? 0);
  setText("metric-unprotected-orders", metrics.unprotected_orders ?? 0);
  setText("metric-positions-pnl", maybeMoney(metrics.positions_pnl));
  setText("trading-book-open-positions", metrics.open_positions);
  setText("trading-book-open-orders", metrics.open_orders);
  setText("trading-book-prepared-orders", metrics.broker_prepared_not_transmitted_orders ?? 0);
  setText("trading-book-local-orphans", metrics.local_order_orphans ?? 0);
  setText("trading-book-positions-pnl", maybeMoney(metrics.positions_pnl));
  setText("broker-reality-status", metrics.broker_tracker_status || "UNKNOWN");
  setText(
    "metric-loss-remaining",
    renderRemainingRiskMetricText(metrics),
  );
  setText("metric-loss-remaining-status", statusLabel(metrics.remaining_risk_status || "-"));
  setStatus("top-broker-tracker", `BROKER ${metrics.broker_tracker_status || "UNKNOWN"}`);
  setText("top-sync-age", syncAgeChipLabel(metrics.broker_sync_age_seconds, metrics.broker_stale_after_seconds));
  setStatus("top-auto-execution", `AUTO ${metrics.auto_execution_blocked ? "BLOCKED" : "ALLOWED"}`);
  setStatus("top-emergency-risk", `RISK ${(metrics.unprotected_positions || metrics.unprotected_orders) ? "CRITICAL" : "OK"}`);
  setStatus("dashboard-broker-tracker", metrics.broker_tracker_status || "UNKNOWN");
  setText("dashboard-broker-sync-age", metrics.broker_sync_age_seconds == null ? "-" : formatAge(metrics.broker_sync_age_seconds));
  setStatus("dashboard-auto-execution", metrics.auto_execution_blocked ? "BLOCKED" : "ALLOWED");
  setStatus("dashboard-emergency-risk", (metrics.unprotected_positions || metrics.unprotected_orders) ? "CRITICAL" : "OK");
  setPnlTone("metric-pnl-yesterday", metrics.pnl_until_yesterday);
  setPnlTone("metric-pnl-today", metrics.broker_pnl_fresh ? metrics.today_pnl : undefined);
  setPnlTone("metric-pnl-unrealized", metrics.positions_pnl);
  setPnlTone("metric-pnl-realized", account.realized_pnl);
  setPnlTone("metric-pnl-total", Number(account.realized_pnl || 0) + Number(metrics.positions_pnl || 0));
  setPnlTone("metric-positions-pnl", metrics.positions_pnl);
  setPnlTone("trading-book-positions-pnl", metrics.positions_pnl);
}

function renderPnlMetricText(metrics) {
  if (metrics.broker_pnl_fresh && metrics.today_pnl != null) return maybeMoney(metrics.today_pnl);
  if (metrics.pnl_display_source === "LOCAL_FALLBACK" && metrics.today_pnl != null) {
    return `${maybeMoney(metrics.today_pnl)} local`;
  }
  return statusLabel(metrics.broker_pnl_status || "STALE");
}

function renderRemainingRiskMetricText(metrics) {
  if ((metrics.remaining_risk_status || "") === "OK") return maybeMoney(metrics.remaining_risk);
  return statusLabel(metrics.remaining_risk_status || "UNKNOWN_CRITICAL");
}

function renderDashboard(snapshot) {
  renderExecutiveBrief(snapshot);
  const tbody = document.getElementById("dashboard-setups");
  if (tbody) {
    const rows = (snapshot.setups || []).slice(0, 8).map((setup) => `
      <tr>
        <td>${escapeHtml(setup.symbol)}</td>
        <td>${escapeHtml(setup.setup_type)}</td>
        <td>${escapeHtml(setup.setup_role || "")}</td>
        <td>${statusBadge(setup.status)}</td>
        <td>${setup.maximum_risk == null ? money(setup.risk_amount) : money(setup.maximum_risk)}</td>
      </tr>
    `);
    tbody.innerHTML = rows.join("") || emptyRow(5, "Aucun setup charge");
  }
  renderStockPnl((snapshot.performance || {}).stock_pnl || []);
}

function renderExecutiveBrief(snapshot) {
  const runtime = snapshot.runtime || {};
  const metrics = snapshot.metrics || {};
  const health = snapshot.health || {};
  const broker = snapshot.broker_reality || {};
  const setups = Array.isArray(snapshot.setups) ? snapshot.setups : [];
  const briefing = buildExecutiveBrief(runtime, metrics, health, broker, setups);
  setText("dashboard-brief-headline", briefing.headline);
  setText("dashboard-brief-summary", briefing.summary);
  setText("dashboard-brief-blocker-count", briefing.blockerCount);
  setText("dashboard-brief-blocker-details", briefing.blockerDetails);
  setText("dashboard-brief-ready-count", briefing.readyCount);
  setText("dashboard-brief-ready-details", briefing.readyDetails);
  setText("dashboard-brief-freshness-value", briefing.freshnessValue);
  setText("dashboard-brief-freshness-note", briefing.freshnessNote);
  setToneData("dashboard-brief-card", briefing.tone);
  setToneData("dashboard-brief-blockers", briefing.blockerTone);
  setToneData("dashboard-brief-ready", briefing.readyTone);
  setToneData("dashboard-brief-freshness", briefing.freshnessTone);
}

function buildExecutiveBrief(runtime, metrics, health, broker, setups) {
  const connection = String(runtime.connection || runtime.connection_label || "").toUpperCase();
  const brokerTracker = String(metrics.broker_tracker_status || broker.broker_tracker_status || connection || "UNKNOWN").toUpperCase();
  const autoBlocked = Boolean(metrics.auto_execution_blocked ?? broker.auto_execution_blocked);
  const syncAge = metrics.broker_sync_age_seconds ?? broker.broker_sync_age_seconds;
  const pnlFresh = Boolean(metrics.broker_pnl_fresh);
  const pnlStatus = String(metrics.broker_pnl_status || "UNKNOWN").toUpperCase();
  const criticalCount = Number.isFinite(Number(broker.critical_count))
    ? Number(broker.critical_count)
    : Number(metrics.unprotected_positions || 0) + Number(metrics.unprotected_orders || 0);
  const mismatchCount = Number(metrics.broker_reality_mismatch_count ?? broker.mismatch_count ?? 0);
  const blockingReasons = compactReasonList(broker.blocking_reasons || health.broker_reality_blocking_reasons || []);
  const readySetups = setups.filter((setup) => setup && setup.enabled !== false && String(setup.status || "") === "ENTRY_READY");
  const nearSetups = setups.filter((setup) => setup && setup.enabled !== false && [
    "WAITING_CONFIRMATION",
    "WAITING_RETEST",
    "WAITING_ENTRY_SIGNAL",
  ].includes(String(setup.status || "")));
  const readySymbols = compactSymbols(readySetups);
  const nearSymbols = compactSymbols(nearSetups);
  const syncTone = toneForAge(syncAge);
  const blockedTone = !connection || connection === "DISCONNECTED" || autoBlocked || criticalCount > 0 || mismatchCount > 0 ? "danger" : pnlFresh ? "ok" : "warn";
  const decisionTone = !connection || connection === "DISCONNECTED"
    ? "danger"
    : autoBlocked || criticalCount
      ? "danger"
      : !pnlFresh
        ? "warn"
        : syncTone === "danger"
          ? "danger"
          : syncTone === "warn"
            ? "warn"
            : "ok";
  const readyTone = readySetups.length ? "ok" : nearSetups.length ? "warn" : "info";
  const freshnessTone = syncTone === "info" ? "ok" : syncTone;
  const headline = !connection || connection === "DISCONNECTED"
    ? "Broker disconnected"
    : autoBlocked
      ? "Auto execution blocked"
      : criticalCount || mismatchCount
        ? "Protection required"
        : !pnlFresh
          ? "P&L stale"
          : "System ready";
  const summary = !connection || connection === "DISCONNECTED"
    ? "TWS is offline. The cockpit stays read-only until the broker comes back."
    : autoBlocked
      ? `Execution is blocked. ${blockingReasons || "Broker reality and local intent are not aligned."}`
      : criticalCount
        ? `There are ${criticalCount} critical protection issue${criticalCount === 1 ? "" : "s"} that must be fixed before execution.`
        : mismatchCount
          ? `Broker reconciliation reports ${mismatchCount} mismatch${mismatchCount === 1 ? "" : "es"}; keep the bot in watch mode.`
          : syncTone === "danger"
            ? `Broker sync is stale at ${syncAge == null ? "unknown" : formatAge(syncAge)}. Refresh TWS before acting.`
            : syncTone === "warn"
              ? `Broker sync is aging at ${syncAge == null ? "unknown" : formatAge(syncAge)}. Keep watching, but confirm freshness before sending a new order.`
          : !pnlFresh
            ? "P&L is stale. Treat the dashboard as advisory until the next broker update lands."
            : "Broker reality, P&L and runtime are aligned. Review the hot zone and decide fast.";
  const blockerParts = [];
  if (criticalCount) blockerParts.push(`${criticalCount} critical`);
  if (mismatchCount) blockerParts.push(`${mismatchCount} mismatch${mismatchCount === 1 ? "" : "es"}`);
  if (!blockerParts.length && autoBlocked) blockerParts.push("safety gate");
  const blockerCount = blockerParts.length ? blockerParts.join(" / ") : "clear";
  const blockerDetails = blockingReasons
    || (autoBlocked
      ? "Use Sync or fix broker reconciliation before re-enabling execution."
      : criticalCount
        ? "Protect the open positions before rearming the bot."
        : mismatchCount
          ? "Reconcile TWS with local intent before sending new orders."
          : "No active blocker.");
  const readyCount = readySetups.length ? `${readySetups.length} ready` : "0 ready";
  const readyDetails = readySymbols || (nearSetups.length ? `${nearSetups.length} near entry: ${nearSymbols}` : "No setup is close to entry right now.");
  const freshnessValue = syncAge == null ? "unknown" : formatAge(syncAge);
  const freshnessNote = [
    `Broker tracker ${brokerTracker || "UNKNOWN"}`,
    `P&L ${pnlFresh ? "fresh" : pnlStatus.toLowerCase()}`,
  ].join(" | ");
  return {
    tone: decisionTone,
    headline,
    summary,
    blockerTone: blockedTone,
    blockerCount,
    blockerDetails,
    readyTone,
    readyCount,
    readyDetails,
    freshnessTone,
    freshnessValue,
    freshnessNote,
  };
}

function compactReasonList(values, limit = 2) {
  const items = Array.isArray(values)
    ? values.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  if (!items.length) return "";
  const visible = items.slice(0, limit);
  const extra = items.length - visible.length;
  return extra > 0 ? `${visible.join(", ")} +${extra} more` : visible.join(", ");
}

function compactSymbols(setups, limit = 3) {
  const symbols = (Array.isArray(setups) ? setups : [])
    .map((setup) => String(setup.symbol || "").trim().toUpperCase())
    .filter(Boolean);
  if (!symbols.length) return "";
  const visible = symbols.slice(0, limit);
  const extra = symbols.length - visible.length;
  return extra > 0 ? `${visible.join(", ")} +${extra}` : visible.join(", ");
}

function renderStockPnl(rows) {
  const tbody = document.getElementById("dashboard-stock-pnl");
  if (!tbody) return;
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.symbol)}</td>
      <td>${escapeHtml(row.quantity)}</td>
      <td>${maybeMoney(row.average_price)}</td>
      <td>${maybeMoney(row.current_price)}</td>
      <td>${maybeMoney(row.market_value)}</td>
      <td><span class="${pnlClass(row.unrealized_pnl)}">${maybeMoney(row.unrealized_pnl)}</span></td>
      <td><span class="${pnlClass(row.pnl_percent)}">${maybePercent(row.pnl_percent)}</span></td>
      <td>${maybeMoney(row.current_stop)}</td>
      <td>${row.setup_id ? `<a class="text-link" href="/setups/${encodeURIComponent(row.setup_id)}">${escapeHtml(row.setup_id)}</a>` : ""}</td>
    </tr>
  `).join("") || emptyRow(9, "Aucune position ouverte");
}

function renderBrokerReality(report) {
  const tbody = document.getElementById("broker-reality-table");
  const statusTarget = document.getElementById("broker-reality-status");
  const mismatchTarget = document.getElementById("broker-reality-mismatch-count");
  const blockedTarget = document.getElementById("broker-reality-blocked");
  const status = report.broker_tracker_status || report.broker_sync_status || "UNKNOWN";
  if (statusTarget) statusTarget.textContent = status;
  if (mismatchTarget) mismatchTarget.textContent = report.mismatch_count ?? 0;
  if (blockedTarget) {
    blockedTarget.textContent = report.auto_execution_blocked ? "TRANSMIT BLOCKED" : status;
    blockedTarget.className = `status ${statusClassName(report.auto_execution_blocked ? "RECONCILIATION_MISMATCH" : status)}`;
    blockedTarget.setAttribute("style", statusBadgeStyle(report.auto_execution_blocked ? "RECONCILIATION_MISMATCH" : status));
  }
  setStatus("top-broker-tracker", `BROKER ${status}`);
  setText("top-sync-age", syncAgeChipLabel(report.broker_sync_age_seconds, report.stale_after_seconds));
  setText(
    "trading-book-sync-age",
    report.broker_sync_age_seconds == null ? "-" : formatAge(report.broker_sync_age_seconds),
  );
  setStatus("top-auto-execution", `AUTO ${report.auto_execution_blocked ? "BLOCKED" : "ALLOWED"}`);
  setStatus("top-emergency-risk", `RISK ${report.critical_count ? "CRITICAL" : "OK"}`);
  setStatus("dashboard-broker-tracker", status);
  setText("dashboard-broker-sync-age", report.broker_sync_age_seconds == null ? "-" : formatAge(report.broker_sync_age_seconds));
  setStatus("dashboard-auto-execution", report.auto_execution_blocked ? "BLOCKED" : "ALLOWED");
  setStatus("dashboard-emergency-risk", report.critical_count ? "CRITICAL" : "OK");
  renderSafetyGate(report);
  renderRiskProtection(report);
  if (!tbody) return;
  const rows = Array.isArray(report.rows) ? report.rows : [];
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.symbol || "")}</td>
      <td>${escapeHtml(row.position_qty ?? row.position_quantity ?? 0)}</td>
      <td>${escapeHtml(row.average_price == null ? "-" : maybeMoney(row.average_price))}</td>
      <td>${escapeHtml(row.current_price == null ? "-" : maybeMoney(row.current_price))}</td>
      <td><span class="${pnlClass(row.daily_pnl)}">${escapeHtml(row.daily_pnl == null ? "-" : maybeMoney(row.daily_pnl))}</span></td>
      <td><span class="${pnlClass(row.unrealized_pnl)}">${escapeHtml(row.unrealized_pnl == null ? "-" : maybeMoney(row.unrealized_pnl))}</span></td>
      <td>${statusBadge(row.broker_entry_status || row.broker_entry_order_status || "-")}</td>
      <td>${statusBadge(row.broker_stop_status || row.broker_stop_order_status || "-")}</td>
      <td>${row.trailing_stop ? "YES" : "NO"}</td>
      <td>${statusBadge(row.protection_status || "-")}</td>
      <td>${row.mismatch ? statusBadge("MISMATCH") : statusBadge("OK")}</td>
      <td>${escapeHtml(brokerSyncLabel(row))}</td>
      <td>${escapeHtml(row.action_required || "-")}</td>
    </tr>
  `).join("") || emptyRow(13, "Aucune realite broker synchronisee");
}

const SAFETY_GATE_CONDITION_LABELS = {
  tws_disconnected: "TWS connected",
  broker_report_stale: "Broker report fresh",
  broker_tracker_missing: "Broker tracker running",
  broker_query_partial_failure: "TWS queries complete",
  critical_mismatch: "No critical mismatch",
  position_without_stop: "All positions stopped",
  entry_order_without_stop: "All entry orders stopped",
};

function renderSafetyGate(report) {
  const verdict = document.getElementById("safety-gate-verdict");
  const list = document.getElementById("safety-gate-conditions");
  const reasonCount = document.getElementById("safety-gate-reason-count");
  const gate = (report && report.safety_gate) || {};
  const conditions = gate.conditions || {};
  const blocked = Boolean(gate.auto_execution_blocked ?? report.auto_execution_blocked);
  const reasons = Array.isArray(gate.blocking_reasons)
    ? gate.blocking_reasons
    : (Array.isArray(report.blocking_reasons) ? report.blocking_reasons : []);
  if (verdict) {
    verdict.setAttribute("data-state", blocked ? "blocked" : "open");
    setText("safety-gate-headline", blocked ? "BLOCKED" : "OPEN");
    setText(
      "safety-gate-note",
      blocked
        ? `${reasons.length} blocking condition${reasons.length === 1 ? "" : "s"}`
        : "Execution allowed by broker reality",
    );
  }
  if (reasonCount) {
    reasonCount.textContent = blocked ? `${reasons.length} blocker(s)` : "clear";
  }
  if (list) {
    // Each condition flag is true when the risk is PRESENT, so the healthy
    // ("ok") state is the negation.
    list.innerHTML = Object.entries(SAFETY_GATE_CONDITION_LABELS).map(([key, label]) => {
      const ok = !conditions[key];
      return `<li data-ok="${ok}">${escapeHtml(label)}</li>`;
    }).join("");
  }
}

function renderRiskProtection(report) {
  const summary = document.getElementById("risk-protection-summary");
  if (summary) {
    summary.innerHTML = [
      ["Remaining risk", report.remaining_risk_status === "OK" ? maybeMoney(report.remaining_risk) : statusLabel(report.remaining_risk_status || "UNKNOWN_CRITICAL")],
      ["Open positions", report.broker_positions_count ?? 0],
      ["Active stops", report.active_stop_orders ?? 0],
      ["Unprotected positions", report.unprotected_positions ?? 0],
      ["Unprotected orders", report.unprotected_orders ?? 0],
    ].map(([label, value]) => `
      <article class="metric-card compact">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </article>
    `).join("");
  }
  const tbody = document.getElementById("risk-protection-table");
  if (!tbody) return;
  const rows = (Array.isArray(report.rows) ? report.rows : []).filter((row) => (
    Number(row.position_qty ?? row.position_quantity ?? 0) !== 0
    || row.protection_status === "ENTRY_ORDER_WITHOUT_STOP_CRITICAL"
    || row.protection_status === "STOP_PREPARED_NOT_TRANSMITTED"
    || row.trailing_stop
  ));
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.symbol || "")}</td>
      <td>${escapeHtml(row.position_qty ?? row.position_quantity ?? 0)}</td>
      <td>${escapeHtml(row.active_stop_price == null ? "-" : maybeMoney(row.active_stop_price))}</td>
      <td>${escapeHtml(row.stop_distance == null ? "-" : maybeMoney(row.stop_distance))}</td>
      <td>${escapeHtml(row.remaining_risk_status === "OK" ? maybeMoney(row.remaining_risk) : statusLabel(row.remaining_risk_status || "-"))}</td>
      <td>${row.trailing_stop ? "YES" : "NO"}</td>
      <td>${statusBadge(row.protection_status || "-")}</td>
      <td>${escapeHtml(row.action_required || "-")}</td>
    </tr>
  `).join("") || emptyRow(8, "Aucune position ou protection active");
}

function brokerSyncLabel(row) {
  const status = row.broker_sync_status || "-";
  const age = row.broker_sync_age_seconds == null ? "-" : formatAge(row.broker_sync_age_seconds);
  return `${status} ${age}`.trim();
}

function wireManualOrderForm() {
  const form = document.getElementById("manual-order-form");
  if (!form || form.dataset.wired) return;
  form.dataset.wired = "1";
  const previewButton = document.getElementById("manual-preview-button");
  const submitButton = document.getElementById("manual-submit-button");
  const invalidatePreview = () => {
    if (submitButton) submitButton.disabled = true;
  };
  form.addEventListener("input", invalidatePreview);
  previewButton?.addEventListener("click", async () => {
    try {
      const result = await api("/api/orders/manual/preview", {
        method: "POST",
        body: manualOrderPayload(),
      });
      renderManualOrderRisk(result);
      if (submitButton) submitButton.disabled = !result.ok;
    } catch (error) {
      renderManualOrderRisk({ block: { message: error.message } });
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = manualOrderPayload();
    const runtime = (latestSnapshot && latestSnapshot.runtime) || {};
    const mode = runtime.mode || runtime.broker_account_mode || "paper";
    const summary = `${payload.side} ${payload.quantity} ${payload.symbol} (${payload.order_type})`;
    if (!window.confirm(`Transmettre l'ordre ${summary} ?`)) return;
    if (mode === "live" && !window.confirm(`COMPTE REEL (${mode}) — confirmer une 2e fois ${summary} ?`)) return;
    try {
      const result = await api("/api/orders/manual", { method: "POST", body: payload });
      toast(`Ordre manuel transmis (${result.order_id || result.setup_id})`);
      renderManualOrderRisk(result);
      if (submitButton) submitButton.disabled = true;
      await refresh();
    } catch (error) {
      renderManualOrderRisk({ block: { message: error.message } });
      toast(compactToastMessage(error.message));
    }
  });
}

async function refresh() {
  const snapshot = await api("/api/dashboard");
  renderSnapshot(snapshot);
  await renderMarketContextPage();
  await refreshForecastWatchlist();
}

async function refreshActiveViews() {
  if (appAutoRefreshInFlight) return;
  appAutoRefreshInFlight = true;
  try {
    await refresh();
    await renderV2Page();
    const setupId = document.body.dataset.setupId;
    if (setupId && currentSetupDetailSetup) {
      if (setupConfigFormDirty || setupConfigEditorDirty) {
        renderSetupDetailSummary(currentSetupDetailSetup);
        await renderSetupForecastPanel(currentSetupDetailSetup, { cachedOnly: true });
        await renderSetupCreationSnapshot(setupId);
      } else {
        await renderSetupDetail();
      }
    }
  } finally {
    appAutoRefreshInFlight = false;
  }
}

function scheduleAutoRefresh() {
  if (appAutoRefreshTimer) window.clearInterval(appAutoRefreshTimer);
  appAutoRefreshTimer = window.setInterval(() => {
    refreshActiveViews().catch((error) => toast(error.message));
  }, APP_AUTO_REFRESH_INTERVAL_MS);
}

function connectWebSocket() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "snapshot") {
      renderSnapshot(event.payload);
      scheduleMarketContextRefresh();
    }
  };
  socket.onclose = () => {
    window.setTimeout(connectWebSocket, 2000);
  };
}

function wireRuntimeButtons() {
  onClick("sync-button", async () => {
    await api("/api/runtime/sync", { method: "POST" });
    toast("Synchronisation terminee");
    await refresh();
  });
  onClick("pause-button", async () => {
    await api("/api/runtime/pause", { method: "POST" });
    toast("Bot en pause");
  });
  onClick("resume-button", async () => {
    await api("/api/runtime/resume", { method: "POST" });
    toast("Bot relance");
  });
  onClick("emergency-button", async () => {
    await api("/api/runtime/emergency-stop", { method: "POST" });
    toast("Emergency stop active");
  });
}

function wireBrokerAccountForm() {
  const form = document.getElementById("broker-account-form");
  if (!form) return;
  const connector = document.getElementById("broker-connector-select");
  const port = document.getElementById("broker-port-input");
  if (connector && port) {
    connector.addEventListener("change", () => {
      if (connector.value === "paper") port.value = "7497";
      if (connector.value === "live") port.value = "7496";
    });
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    try {
      const snapshot = await api("/api/runtime/broker-connector", {
        method: "POST",
        body: {
          connector: data.connector,
          host: data.host,
          port: data.port ? Number(data.port) : null,
          client_id: data.client_id ? Number(data.client_id) : null,
        },
      });
      renderSnapshot(snapshot);
      toast("Broker account updated");
    } catch (error) {
      toast(error.message);
    }
  });
}

function wireTwsAuditForm() {
  const form = document.getElementById("tws-audit-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const snapshot = await api("/api/runtime/tws-audit", {
        method: "POST",
        body: { enabled: Boolean(form.elements.enabled.checked) },
      });
      renderSnapshot(snapshot);
      toast("Audit TWS mis a jour");
    } catch (error) {
      toast(error.message);
    }
  });
}

function wireSetupForm() {
  const form = document.getElementById("setup-form");
  if (!form) return;
  const textField = form.elements.text;
  if (textField) {
    // Keep Ticker filled as the user pastes/types the setup JSON, otherwise
    // the "symbol" field's native `required` validation blocks the submit
    // event (and our JS sync in it) before it ever runs.
    textField.addEventListener("input", () => syncTickerFieldFromSetupText(form));
  }
  const previewButton = document.getElementById("setup-preview-button");
  if (previewButton) {
    previewButton.addEventListener("click", async () => {
      try {
        syncTickerFieldFromSetupText(form);
        const result = await api("/api/setups/convert-text", {
          method: "POST",
          body: setupTextPayload(form),
        });
        syncTickerFieldFromSetupResult(form, result);
        renderSetupPreview(result);
      } catch (error) {
        renderSetupPreviewError(error.message);
      }
    });
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      syncTickerFieldFromSetupText(form);
      const result = await api("/api/setups/from-text", {
        method: "POST",
        body: setupTextPayload(form),
      });
      syncTickerFieldFromSetupResult(form, result);
      renderSetupPreview(result);
      toast("Setup sauvegarde");
      await refresh();
    } catch (error) {
      renderSetupPreviewError(error.message);
    }
  });
}

async function armSetupById(setupId, options = {}) {
  const result = await api(`/api/setups/${encodeURIComponent(setupId)}/arm`, {
    method: "POST",
  });
  toast("Setup arme");
  await refresh();
  if (options.renderDetail) await renderSetupDetail();
  return result;
}

async function disarmSetupById(setupId, options = {}) {
  const result = await api(`/api/setups/${encodeURIComponent(setupId)}/disarm`, {
    method: "POST",
  });
  toast("Setup desarme");
  await refresh();
  if (options.renderDetail) await renderSetupDetail();
  return result;
}

function wireActionButtons() {
  document.body.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    try {
      if (action === "enable-all-setups" || action === "disable-all-setups") {
        const enabled = action === "enable-all-setups";
        const result = await api(`/api/setups/${enabled ? "enable-all" : "disable-all"}`, {
          method: "POST",
        });
        renderSetupToolsOutput(
          result,
          `${result.updated_count} setup(s) ${enabled ? "Auto ON" : "Auto OFF / suivi seul"}.`,
          { messageOnly: true },
        );
        toast(enabled ? "Execution auto autorisee partout" : "Tous les stocks restent suivis, auto OFF");
        await refresh();
      }
      if (action === "show-setup-template") {
        const result = await api("/api/setups/config-template?template_type=universal");
        const template = result && result.skeleton
          ? result.skeleton
          : (result && result.template ? result.template : result);
        const label = "Squelette universel expert";
        const copied = await copySetupTemplateToClipboard(template);
        renderSetupToolsOutput(
          result,
          copied
            ? `${label} pret a completer. Copie dans le presse-papier effectuee.`
            : `${label} pret a completer.`,
        );
        toast(
          copied
            ? `${label} copie dans le presse-papier`
            : `${label} genere, mais copie impossible`,
        );
      }
      if (action === "enable" || action === "disable") {
        await api(`/api/setups/${encodeURIComponent(button.dataset.setup)}/${action}`, {
          method: "POST",
        });
        await refresh();
      }
      if (action === "arm-setup" || action === "disarm-setup") {
        const setupId = button.dataset.setup || "";
        if (!setupId) return;
        if (action === "arm-setup") {
          await armSetupById(setupId);
        } else {
          await disarmSetupById(setupId);
        }
      }
      if (action === "delete-setup") {
        const setupId = button.dataset.setup || "";
        const symbol = button.dataset.symbol || setupId;
        const confirmed = window.confirm(`Supprimer le setup ${symbol} ?`);
        if (!confirmed) return;
        await api(`/api/setups/${encodeURIComponent(setupId)}`, {
          method: "DELETE",
        });
        toast("Setup supprime");
        await refresh();
      }
      if (action === "toggle-order-detail") {
        const detailRow = document.getElementById(button.dataset.target || "");
        if (detailRow) {
          detailRow.hidden = !detailRow.hidden;
          button.textContent = detailRow.hidden ? "+" : "-";
        }
        return;
      }
      if (action === "cancel-order") {
        await api(`/api/orders/${encodeURIComponent(button.dataset.order)}/cancel`, {
          method: "POST",
        });
        await refresh();
      }
      if (action === "attach-stop") {
        const orderId = button.dataset.order || "";
        const symbol = button.dataset.symbol || orderId;
        const confirmed = window.confirm(`Attacher le SL du setup a ${symbol} ?`);
        if (!confirmed) return;
        await api(`/api/orders/${encodeURIComponent(orderId)}/attach-stop`, {
          method: "POST",
        });
        toast("SL envoye");
        await refresh();
      }
      if (action === "delete-order") {
        const orderId = button.dataset.order || "";
        const symbol = button.dataset.symbol || orderId;
        const confirmed = window.confirm(`Supprimer la ligne d'ordre ${symbol} ?`);
        if (!confirmed) return;
        await api(`/api/orders/${encodeURIComponent(orderId)}`, {
          method: "DELETE",
        });
        toast("Ordre supprime de l'historique local");
        await refresh();
      }
      if (action === "fill") {
        const price = window.prompt("Test fill price");
        if (!price) return;
        await api(`/api/orders/${encodeURIComponent(button.dataset.order)}/simulate-fill`, {
          method: "POST",
          body: { fill_price: Number(price) },
        });
        await refresh();
      }
      if (action === "move-stop") {
        const stop = window.prompt("New stop");
        if (!stop) return;
        await api(`/api/positions/${encodeURIComponent(button.dataset.symbol)}/move-stop`, {
          method: "POST",
          body: { new_stop: Number(stop) },
        });
        await refresh();
      }
      if (action === "resolve-intelligence-ambiguity") {
        const setupId = document.body.dataset.setupId;
        const analysisId = button.dataset.analysis;
        const ambiguityId = button.dataset.ambiguity;
        const resolution = button.dataset.resolution
          ? JSON.parse(decodeURIComponent(button.dataset.resolution))
          : {};
        const selectedOption = resolution.selected_option || {};
        const optionAction = String(selectedOption.action || "").toUpperCase();
        if (optionAction === "UPDATE_FIELD" || optionAction === "REVIEW_FIELD") {
          const fieldPath = selectedOption.field_path || "champ";
          const value = window.prompt(`Nouvelle valeur pour ${fieldPath}`);
          if (value === null || value === "") return;
          resolution.field_value = value;
        }
        const result = await api(
          `/api/intelligence/analyses/${encodeURIComponent(analysisId)}/ambiguities/${encodeURIComponent(ambiguityId)}/resolve`,
          {
            method: "POST",
            body: resolution,
          },
        );
        if (result && result.resolution_analysis) {
          setCurrentSetupIntelligenceSelectedId(result.resolution_analysis.analysis_id || null);
        }
        showSetupIntelligenceMessage(
          result && result.resolution_analysis
            ? "Ambiguite resolue et nouvelle revision creee."
            : "Ambiguite resolue.",
          "success",
        );
        toast(result && result.resolution_analysis ? "Revision de resolution creee" : "Ambiguite resolue");
        if (setupId) await renderSetupIntelligence(setupId);
      }
      if (action === "view-intelligence-analysis") {
        const analysisId = button.dataset.analysis || null;
        setCurrentSetupIntelligenceSelectedId(analysisId);
        setCurrentSetupIntelligenceComparison(null);
        if (analysisId) await ensureSetupIntelligenceAnalysisLoaded(analysisId);
        renderSetupIntelligencePanel(currentSetupIntelligence);
        syncCurrentSetupDetailIntelligence();
        renderSetupDetailJsonOutput();
      }
      if (action === "intelligence-history-page") {
        const setupId = document.body.dataset.setupId;
        const offset = numberOrNull(button.dataset.offset);
        if (setupId && offset !== null) {
          await loadSetupIntelligenceHistoryPage(setupId, Math.max(0, offset));
        }
      }
      if (action === "compare-intelligence-analysis") {
        const setupId = document.body.dataset.setupId;
        const rightAnalysisId = button.dataset.analysis || "";
        const leftAnalysisId = currentSetupIntelligenceSelectedId || "";
        if (!setupId || !leftAnalysisId || !rightAnalysisId || leftAnalysisId === rightAnalysisId) {
          showSetupIntelligenceMessage(
            "Affiche d'abord une autre revision pour lancer une comparaison.",
            "error",
          );
          return;
        }
        setCurrentSetupIntelligenceComparison(await api(
          `/api/intelligence/setups/${encodeURIComponent(setupId)}/compare`,
          {
            method: "POST",
            body: {
              left_analysis_id: leftAnalysisId,
              right_analysis_id: rightAnalysisId,
            },
          },
        ));
        showSetupIntelligenceMessage("Comparaison chargee.", "success");
        renderSetupIntelligencePanel(currentSetupIntelligence);
        syncCurrentSetupDetailIntelligence();
        renderSetupDetailJsonOutput();
      }
      if (action === "clear-intelligence-comparison") {
        setCurrentSetupIntelligenceComparison(null);
        renderSetupIntelligencePanel(currentSetupIntelligence);
        syncCurrentSetupDetailIntelligence();
        renderSetupDetailJsonOutput();
      }
      if (action === "rollback-intelligence-analysis") {
        const setupId = document.body.dataset.setupId;
        const analysisId = button.dataset.analysis || "";
        if (!setupId || !analysisId) return;
        const confirmed = window.confirm(
          "Restaurer cette revision comme nouvelle configuration active du setup ?",
        );
        if (!confirmed) return;
        const result = await api(
          `/api/intelligence/setups/${encodeURIComponent(setupId)}/rollback`,
          {
            method: "POST",
            body: { analysis_id: analysisId },
          },
        );
        setCurrentSetupIntelligenceComparison(null);
        setCurrentSetupIntelligenceSelectedId(result.rollback_analysis
          ? (result.rollback_analysis.analysis_id || null)
          : null);
        showSetupIntelligenceMessage(
          result.history_persisted
            ? "Revision restauree et historisee."
            : `Revision restauree, mais l'historique intelligence n'a pas pu etre persiste: ${result.history_warning || "erreur inconnue"}`,
          result.history_persisted ? "success" : "error",
        );
        toast(result.history_persisted ? "Revision restauree" : "Setup restaure avec avertissement");
        await renderSetupDetail();
      }
    } catch (error) {
      if (
        action === "show-setup-template"
        || action === "enable-all-setups"
        || action === "disable-all-setups"
      ) {
        renderSetupToolsError(error.message);
      }
      toast(error.message);
    }
  });
}

function wireSetupIntelligencePanel() {
  onClick("setup-intelligence-analyze", async () => {
    const setupId = document.body.dataset.setupId;
    const editor = document.getElementById("setup-config");
    const payload = setupConfigEditorDirty && !setupConfigFormDirty
      ? parseSetupConfigEditor(editor)
      : (buildSetupConfigFromForm() || currentSetupConfig);
    if (!setupId || !payload) return;
    const result = await api("/api/intelligence/analyze", {
      method: "POST",
      body: { payload },
    });
    showSetupIntelligenceMessage(
      result.reused
        ? "Analyse existante reutilisee."
        : "Nouvelle analyse intelligence enregistree.",
      "success",
    );
    setCurrentSetupIntelligenceComparison(null);
    setCurrentSetupIntelligenceSelectedId(result.analysis_id || null);
    toast(result.reused ? "Analyse reutilisee" : "Analyse intelligence enregistree");
    await renderSetupIntelligence(setupId);
  });
}

function wireSetupConfigEditor() {
  const editor = document.getElementById("setup-config");
  const form = document.getElementById("setup-config-form");
  if (!editor && !form) return;
  if (editor) {
    editor.addEventListener("input", () => {
      setSetupConfigEditorDirty(true);
      syncSetupConfigActions();
    });
  }
  if (form) {
    form.addEventListener("input", () => {
      setSetupConfigFormDirty(true);
      syncSetupConfigActions();
      const preview = buildSetupConfigFromForm();
      if (preview && editor && !setupConfigEditorDirty) {
        editor.value = JSON.stringify(preview, null, 2);
      }
    });
  }
  onClick("setup-config-format", () => {
    const parsed = parseSetupConfigEditor(editor);
    if (!parsed) return;
    setCurrentSetupConfig(structuredCloneSafe(parsed));
    setSetupConfigFormDirty(false);
    setSetupConfigEditorDirty(false);
    renderSetupConfigForm(parsed);
    editor.value = JSON.stringify(parsed, null, 2);
    syncSetupConfigActions();
    showSetupConfigMessage("JSON formate", "success");
  });
  onClick("setup-config-reset", async () => {
    await renderSetupDetail();
    showSetupConfigMessage("Configuration rechargee", "success");
  });
  onClick("setup-config-arm", async () => {
    const setupId = document.body.dataset.setupId;
    if (!setupId) return;
    if (setupConfigFormDirty || setupConfigEditorDirty) {
      showSetupConfigMessage("Sauvegarde les modifications avant d'armer le setup.", "error");
      toast("Sauvegarde d'abord avant d'armer le setup");
      return;
    }
    try {
      await armSetupById(setupId, { renderDetail: true });
      showSetupConfigMessage("Setup arme", "success");
    } catch (error) {
      showSetupConfigMessage(error.message, "error");
      toast(compactToastMessage(error.message));
    }
  });
  onClick("setup-config-disarm", async () => {
    const setupId = document.body.dataset.setupId;
    if (!setupId) return;
    try {
      await disarmSetupById(setupId, { renderDetail: true });
      showSetupConfigMessage("Setup desarme", "success");
    } catch (error) {
      showSetupConfigMessage(error.message, "error");
      toast(compactToastMessage(error.message));
    }
  });
  onClick("setup-config-save", async () => {
    const setupId = document.body.dataset.setupId;
    const parsed = setupConfigEditorDirty && !setupConfigFormDirty
      ? parseSetupConfigEditor(editor)
      : buildSetupConfigFromForm();
    if (!setupId || !parsed) return;
    try {
      const result = await api(`/api/setups/${encodeURIComponent(setupId)}`, {
        method: "PUT",
        body: parsed,
      });
      setCurrentSetupConfig(structuredCloneSafe(result.setup.config));
      setSetupConfigFormDirty(false);
      setSetupConfigEditorDirty(false);
      renderSetupConfigForm(currentSetupConfig);
      if (editor) editor.value = JSON.stringify(result.setup.config, null, 2);
      syncSetupConfigActions();
      toast("Setup sauvegarde");
      await refresh();
      await renderSetupDetail();
      showSetupConfigMessage("Configuration sauvegardee", "success");
    } catch (error) {
      showSetupConfigMessage(error.message, "error");
      toast(compactToastMessage(error.message));
    }
  });
}

async function init() {
  wireRuntimeButtons();
  wireSetupChartTimeframeControls();
  wireSetupsColumnControls();
  renderSetupsColumnControls();
  wireMarketForm();
  wireBrokerAccountForm();
  wireTwsAuditForm();
  wireSetupForm();
  wireModals();
  wireActionButtons();
  wireManualOrderForm();
  wireSetupConfigEditor();
  wireSetupForecastPanel();
  wireSetupDetailJsonButton();
  wireSetupIntelligencePanel();
  wireMarketContextControls();
  await refresh();
  await renderV2Page();
  await renderSetupDetail();
  await renderLogsPage();
  if (page === "dashboard") initDashboardPremium();
  window.setInterval(() => {
    if (latestSnapshot) renderEngineHealth(latestSnapshot.health || {});
  }, 1000);
  connectWebSocket();
  scheduleAutoRefresh();
}

/* ============================================================
   DASHBOARD PREMIUM — count-up, flash, LIVE, equity curve, donut
   ============================================================ */

init().catch((error) => toast(error.message));
