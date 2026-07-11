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
  wireModals,
  yesNo,
} from "./ui-helpers.js";
import {
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

const page = document.body.dataset.page;
let setupChartState = null;
let setupChartResizeTimer = null;
let setupChartInteractionsWired = false;
let marketContextState = { view: "WATCHLIST", heatmap: null, selectedSymbol: "" };
let marketContextRefreshTimer = null;
let appAutoRefreshTimer = null;
let appAutoRefreshInFlight = false;

const APP_AUTO_REFRESH_INTERVAL_MS = 30000;
const SETUP_CHART_MIN_VISIBLE_CANDLES = 10;
const SETUP_CHART_INITIAL_VISIBLE_CANDLES = 60;
const SETUP_CHART_MAX_SOURCE_CANDLES = 180;
const SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE = 8;

const CONFIG_FIELD_OPTIONS = {
  direction: ["long", "short"],
  mode: ["paper", "live"],
  order_type: ["MKT", "LMT", "STP", "STP_LMT", "TRAIL"],
  setup_role: ["ENTRY_AND_MANAGEMENT", "ENTRY_ONLY", "MANAGEMENT_ONLY"],
  setup_type: [
    "aggressive_rebound",
    "breakout_retest",
    "momentum_breakout",
    "pullback_continuation",
    "range_breakout",
    "runner",
    "trailing_runner",
    "position_management",
  ],
  take_profit_mode: ["none", "fixed", "partial", "trailing"],
  trigger_source: [
    "confirmation_candle_high",
    "resistance",
    "range_high",
    "entry_price",
    "manual",
  ],
  timeframe: ["1m", "5m", "15m", "30m", "1h", "1d"],
};

const CONFIG_PATH_OPTIONS = {
  "position_source.mode": ["adopt_existing_ibkr_position", "manual", "bot"],
  "management.stop_management.mode": ["step_based", "trailing", "none"],
  "stop_management.mode": ["step_based", "trailing", "none"],
  "timeframes.signal": CONFIG_FIELD_OPTIONS.timeframe,
  "timeframes.confirmation": CONFIG_FIELD_OPTIONS.timeframe,
};

const CONFIG_ROOT_ORDER = [
  "setup_id",
  "symbol",
  "enabled",
  "mode",
  "setup_type",
  "setup_role",
  "direction",
];

const SETTINGS_RISK_LABELS = {
  max_open_positions: "Positions ouvertes max",
  max_position_amount_usd: "Montant max par position (USD)",
  max_risk_per_trade_usd: "Risque max par trade (USD)",
  max_daily_loss_usd: "Perte journaliere max (USD)",
  max_total_exposure_usd: "Exposition totale max (USD)",
  allow_short: "Short autorise",
};

const SETUPS_COLUMNS_STORAGE_KEY = "setup-order:setups-columns";

const SETUPS_TABLE_COLUMNS = [
  {
    id: "symbol",
    label: "Symbole",
    render: (setup) => `<a class="text-link" href="${setupDetailPath(setup)}">${escapeHtml(setup.symbol)}</a>`,
  },
  {
    id: "setup_type",
    label: "Type",
    render: (setup) => escapeHtml(setup.setup_type),
  },
  {
    id: "setup_role",
    label: "Role",
    render: (setup) => escapeHtml(setup.setup_role || ""),
  },
  {
    id: "status",
    label: "Statut",
    render: (setup) => {
      const displayStatus = displaySetupStatus(setup);
      const reason = setupStatusReason(setup);
      const detail = [displayStatus.detail, reason ? `Raison: ${reason}` : ""]
        .filter(Boolean)
        .join(" | ");
      return statusBadge(displayStatus.status, detail);
    },
  },
  {
    id: "revalidation",
    label: "Revalidation",
    render: (setup) => renderSetupRevalidationCell(setup),
  },
  {
    id: "entry_trigger",
    label: "Trigger",
    render: (setup) => setup.entry_trigger == null ? "" : money(setup.entry_trigger),
  },
  {
    id: "setup_price",
    label: "Prix setup",
    render: (setup) => renderSetupPriceCell(setup),
  },
  {
    id: "maximum_limit_price",
    label: "Limite max",
    render: (setup) => setup.maximum_limit_price == null ? "" : money(setup.maximum_limit_price),
  },
  {
    id: "initial_trailing_stop",
    label: "Stop initial",
    render: (setup) => {
      const initialStop = setupInitialTrailingStop(setup);
      return initialStop == null ? "" : money(initialStop);
    },
  },
  {
    id: "maximum_quantity",
    label: "Quantite max",
    render: (setup) => setup.maximum_quantity == null ? "" : escapeHtml(setup.maximum_quantity),
  },
  {
    id: "maximum_risk",
    label: "Risque max",
    render: (setup) => setup.maximum_risk == null ? "" : money(setup.maximum_risk),
  },
  {
    id: "reconciliation_status",
    label: "Reconciliation",
    render: (setup) => escapeHtml(setup.reconciliation_status || ""),
  },
  {
    id: "signal",
    label: "Signal",
    render: (setup) => renderSetupSignalCell(setup),
  },
  {
    id: "timesfm_score",
    label: "TimesFM",
    render: (setup) => renderTimesfmScoreCell(setupForecastForSetup(setup)),
  },
  {
    id: "timesfm_move",
    label: "Move 1h",
    render: (setup) => renderTimesfmMoveCell(setupForecastForSetup(setup)),
  },
  {
    id: "actions",
    label: "Actions",
    render: (setup) => {
      const enabled = Boolean(setup.enabled);
      const action = enabled ? "disable" : "enable";
      const state = enabled ? "Auto ON" : "Auto OFF";
      const disarmed = String(setup.status || "").toUpperCase() === "DISABLED";
      const lifecycleAction = disarmed ? "arm-setup" : "disarm-setup";
      const lifecycleLabel = disarmed ? "Armer" : "Desarmer";
      const reason = setupStatusReason(setup);
      const armBlocked = disarmed && !setupIsArmable(setup);
      const lifecycleTitle = disarmed
        ? (armBlocked
            ? `Armement impossible: statut incompatible${reason ? ` (${revalidationReasonLabel(reason)})` : ""}`
            : "Armer le setup pour relancer son suivi runtime")
        : "Desarmer le setup et passer son statut a DISABLED";
      const title = enabled
        ? "Passer en suivi seul: aucun ordre automatique TWS"
        : "Autoriser l'execution automatique TWS si le setup est confirme";
      return `
        <div class="row-actions">
          <a href="${setupDetailPath(setup)}">Voir</a>
          <button
            class="lifecycle-button ${disarmed ? "arm-button" : "disarm-button"}"
            type="button"
            data-action="${lifecycleAction}"
            data-setup="${escapeHtml(setup.setup_id)}"
            title="${lifecycleTitle}"
            aria-label="${lifecycleTitle}"
            ${armBlocked ? "disabled" : ""}
          >${lifecycleLabel}</button>
          <button
            class="toggle-button ${enabled ? "on-button" : "off-button"}"
            type="button"
            data-action="${action}"
            data-setup="${escapeHtml(setup.setup_id)}"
            title="${title}"
            aria-label="${title}"
          >${state}</button>
          <button
            class="danger-small"
            type="button"
            data-action="delete-setup"
            data-setup="${escapeHtml(setup.setup_id)}"
            data-symbol="${escapeHtml(setup.symbol)}"
          >Suppr</button>
        </div>
      `;
    },
  },
];

const DEFAULT_SETUPS_COLUMN_ORDER = SETUPS_TABLE_COLUMNS.map((column) => column.id);
let setupsColumnOrder = loadSetupsColumnOrder();
let setupsSearchQuery = "";

const activeNav = document.querySelector(`[data-nav="${page}"]`);
if (activeNav) activeNav.classList.add("active");

window.addEventListener("resize", () => {
  if (!setupChartState) return;
  window.clearTimeout(setupChartResizeTimer);
  setupChartResizeTimer = window.setTimeout(() => {
    drawSetupChart(setupChartState.setup, setupChartState.quotes);
  }, 120);
});

const SETUP_NON_ARMABLE_STATUSES = new Set([
  "INVALIDATED",
  "EXPIRED",
  "STALE_SETUP",
  "MISSED_BREAKOUT_WAIT_RETEST",
]);

const SETUP_REVALIDATION_REASON_LABELS = {
  SETUP_VALID: "Setup valide",
  NOT_REVALIDATED: "Non revalide",
  SUPPORT_BROKEN: "Support casse",
  INVALIDATION_LEVEL_BROKEN: "Niveau d'invalidation casse",
  TECHNICAL_THESIS_BROKEN: "These technique cassee",
  STOP_ABOVE_ENTRY_FOR_LONG: "Stop au-dessus de l'entree (long)",
  STOP_BELOW_ENTRY_FOR_SHORT: "Stop sous l'entree (short)",
  PRICE_TOO_FAR_ABOVE_ENTRY: "Prix trop au-dessus de l'entree",
  PRICE_TOO_FAR_BELOW_ENTRY: "Prix trop sous l'entree",
  SETUP_TOO_OLD: "Setup trop ancien",
  TIME_EXPIRED: "Expiration atteinte",
  MISSING_MARKET_DATA: "Donnees marche manquantes",
  BROKER_DISCONNECTED: "Broker deconnecte",
  BROKER_TRACKER_STALE: "Broker tracker obsolete",
  RISK_UNKNOWN: "Risque inconnu",
  SPREAD_TOO_WIDE: "Spread trop large",
  TRAILING_STOP_NOT_READY: "Trailing stop pas pret",
  MANAGEMENT_ONLY_POSITION_MISSING: "Position IBKR absente",
  POSITION_FOUND: "Position retrouvee",
  REVALIDATION_SKIPPED_NO_DATA: "Revalidation ignoree (pas de donnees)",
  MARKET_CLOSED: "Marche ferme",
};

function setupLastRevalidatedAt(setup) {
  return (setup && setup.last_revalidated_at) || "";
}

function revalidationReasonLabel(reason) {
  const key = String(reason || "").trim().toUpperCase();
  if (!key) return "";
  return SETUP_REVALIDATION_REASON_LABELS[key] || key;
}

function setupIsArmable(setup) {
  const status = String((setup && setup.status) || "").toUpperCase();
  return !SETUP_NON_ARMABLE_STATUSES.has(status);
}

function formatRevalidatedAt(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const deltaSeconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (deltaSeconds < 0) return date.toLocaleString();
  if (deltaSeconds < 60) return `il y a ${deltaSeconds}s`;
  if (deltaSeconds < 3600) return `il y a ${Math.floor(deltaSeconds / 60)}min`;
  if (deltaSeconds < 86400) return `il y a ${Math.floor(deltaSeconds / 3600)}h`;
  return `il y a ${Math.floor(deltaSeconds / 86400)}j`;
}

function renderSetupRevalidationCell(setup) {
  const reason = setupStatusReason(setup);
  const revalidatedAt = setupLastRevalidatedAt(setup);
  if (!reason && !revalidatedAt) return "<span class=\"muted\">-</span>";
  const label = reason ? escapeHtml(revalidationReasonLabel(reason)) : "-";
  const reasonTitle = reason ? ` title="${escapeHtml(reason)}"` : "";
  const timeText = revalidatedAt ? escapeHtml(formatRevalidatedAt(revalidatedAt)) : "";
  const timeTitle = revalidatedAt ? ` title="${escapeHtml(formatTime(revalidatedAt))}"` : "";
  const timeHtml = timeText
    ? `<div class="revalidation-time muted"${timeTitle}>${timeText}</div>`
    : "";
  return `<div class="revalidation-cell"><span class="revalidation-reason"${reasonTitle}>${label}</span>${timeHtml}</div>`;
}

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

function brokerPnlSourceLabel(metrics) {
  const source = metrics.broker_pnl_source || metrics.pnl_display_source || "-";
  const status = metrics.broker_pnl_status || "-";
  const age = metrics.broker_pnl_age_seconds == null ? "-" : formatAge(metrics.broker_pnl_age_seconds);
  const reason = metrics.broker_pnl_reason ? ` ${metrics.broker_pnl_reason}` : "";
  return `${source} ${status} ${age}${reason}`.trim();
}

function renderRemainingRiskMetricText(metrics) {
  if ((metrics.remaining_risk_status || "") === "OK") return maybeMoney(metrics.remaining_risk);
  return statusLabel(metrics.remaining_risk_status || "UNKNOWN_CRITICAL");
}

function setupDetailPath(setup) {
  return `/setups/${encodeURIComponent(setup.setup_id || "")}`;
}

function loadSetupsColumnOrder() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SETUPS_COLUMNS_STORAGE_KEY) || "[]");
    return normalizeSetupsColumnOrder(saved);
  } catch {
    return [...DEFAULT_SETUPS_COLUMN_ORDER];
  }
}

function normalizeSetupsColumnOrder(order) {
  const validIds = new Set(DEFAULT_SETUPS_COLUMN_ORDER);
  const normalized = [];
  if (Array.isArray(order)) {
    order.forEach((id) => {
      if (validIds.has(id) && !normalized.includes(id)) normalized.push(id);
    });
  }
  DEFAULT_SETUPS_COLUMN_ORDER.forEach((id) => {
    if (!normalized.includes(id)) normalized.push(id);
  });
  return normalized;
}

function saveSetupsColumnOrder() {
  try {
    window.localStorage.setItem(
      SETUPS_COLUMNS_STORAGE_KEY,
      JSON.stringify(setupsColumnOrder),
    );
  } catch {
    // localStorage can be blocked by the browser; column order still works for the session.
  }
}

function orderedSetupsColumns() {
  const columnsById = new Map(SETUPS_TABLE_COLUMNS.map((column) => [column.id, column]));
  return setupsColumnOrder
    .map((id) => columnsById.get(id))
    .filter(Boolean);
}

function renderSetupsColumnControls() {
  const controls = document.getElementById("setups-column-controls");
  if (!controls) return;
  const columns = orderedSetupsColumns();
  controls.innerHTML = columns.map((column, index) => `
    <div class="column-chip" draggable="true" data-column-id="${escapeHtml(column.id)}">
      <button
        type="button"
        data-column-move="left"
        data-column-id="${escapeHtml(column.id)}"
        aria-label="Deplacer ${escapeHtml(column.label)} a gauche"
        ${index === 0 ? "disabled" : ""}
      >&lt;</button>
      <span>${escapeHtml(column.label)}</span>
      <button
        type="button"
        data-column-move="right"
        data-column-id="${escapeHtml(column.id)}"
        aria-label="Deplacer ${escapeHtml(column.label)} a droite"
        ${index === columns.length - 1 ? "disabled" : ""}
      >&gt;</button>
    </div>
  `).join("");
}

function renderSetupsColumnHeader(columns = orderedSetupsColumns()) {
  const head = document.getElementById("setups-head");
  if (!head) return columns;
  head.innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>`;
  return columns;
}

function moveSetupsColumn(columnId, direction) {
  const index = setupsColumnOrder.indexOf(columnId);
  const targetIndex = index + direction;
  if (index === -1 || targetIndex < 0 || targetIndex >= setupsColumnOrder.length) return;
  const nextOrder = [...setupsColumnOrder];
  [nextOrder[index], nextOrder[targetIndex]] = [nextOrder[targetIndex], nextOrder[index]];
  setupsColumnOrder = nextOrder;
  saveSetupsColumnOrder();
  renderSetupsColumnControls();
  renderSetups((latestSnapshot || {}).setups || []);
}

function reorderSetupsColumn(sourceId, targetId, insertAfter) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  const nextOrder = setupsColumnOrder.filter((id) => id !== sourceId);
  const targetIndex = nextOrder.indexOf(targetId);
  if (targetIndex === -1) return;
  nextOrder.splice(targetIndex + (insertAfter ? 1 : 0), 0, sourceId);
  setupsColumnOrder = nextOrder;
  saveSetupsColumnOrder();
  renderSetupsColumnControls();
  renderSetups((latestSnapshot || {}).setups || []);
}

function resetSetupsColumns() {
  setupsColumnOrder = [...DEFAULT_SETUPS_COLUMN_ORDER];
  saveSetupsColumnOrder();
  renderSetupsColumnControls();
  renderSetups((latestSnapshot || {}).setups || []);
}

function filterSetups(setups) {
  const query = setupsSearchQuery.trim().toLowerCase();
  if (!query) return setups;
  return setups.filter((setup) => setupSearchText(setup).includes(query));
}

function setupSearchText(setup) {
  return [
    setup.setup_id,
    setup.symbol,
    setup.setup_type,
    setup.setup_role,
    setup.status,
    setup.entry_trigger,
    setup.maximum_limit_price,
    setupInitialTrailingStop(setup),
    setup.maximum_quantity,
    setup.maximum_risk,
    setup.reconciliation_status,
    setup.enabled ? "auto on execution tws" : "auto off suivi seul",
  ].map((value) => String(value ?? "").toLowerCase()).join(" ");
}

function setupInitialTrailingStop(setup) {
  const trailing = ((setup.config || {}).trailing_stop_loss || {});
  return firstNumber(trailing.initial_stop);
}

function renderSetupsCount(visibleCount, totalCount) {
  const count = document.getElementById("setups-count");
  if (!count) return;
  count.textContent = setupsSearchQuery.trim()
    ? `${visibleCount} / ${totalCount} setups`
    : `${totalCount} setups`;
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

const OPPORTUNITY_RADAR_LIMIT = 6;
const OPPORTUNITY_RADAR_TERMINAL_STATUSES = new Set([
  "CANCELLED",
  "CLOSED",
  "COMPLETED",
  "DELETED",
  "EMERGENCY_STOP",
  "ERROR",
  "ERROR_REQUIRES_MANUAL_REVIEW",
  "EXPIRED",
  "FILLED",
  "IN_POSITION",
  "INVALIDATED",
  "MANAGING_POSITION",
  "REJECTED",
]);

async function renderMarketContextPage() {
  const heatmap = document.getElementById("market-context-heatmap");
  if (!heatmap) return;
  try {
    const data = await api(`/api/market-context/heatmap?view=${encodeURIComponent(marketContextState.view)}`);
    marketContextState.heatmap = data;
    renderMarketContextHeatmap(data);
    renderMarketContextMarketMap(data);
    renderMarketContextSectors(data.nodes || []);
    const selected = marketContextState.selectedSymbol
      || ((data.nodes || [])[0] && data.nodes[0].id)
      || "";
    if (selected) {
      await renderMarketContextDetail(selected);
    } else {
      renderMarketContextEmptyDetail();
    }
  } catch (error) {
    heatmap.innerHTML = `<div class="market-context-empty">${escapeHtml(error.message)}</div>`;
    const marketMap = document.getElementById("market-context-market-map");
    if (marketMap) marketMap.innerHTML = `<div class="market-context-empty">${escapeHtml(error.message)}</div>`;
  }
}

function scheduleMarketContextRefresh() {
  if (!document.getElementById("market-context-heatmap")) return;
  window.clearTimeout(marketContextRefreshTimer);
  marketContextRefreshTimer = window.setTimeout(() => {
    renderMarketContextPage().catch((error) => toast(error.message));
  }, 500);
}

function renderMarketContextHeatmap(data) {
  const heatmap = document.getElementById("market-context-heatmap");
  if (!heatmap) return;
  const nodes = filterMarketContextNodes(data.nodes || []);
  heatmap.innerHTML = nodes.map((node) => {
    const tone = marketContextTone(node.status);
    const selected = node.id === marketContextState.selectedSymbol ? " selected" : "";
    return `
      <button
        type="button"
        class="market-context-tile ${escapeHtml(tone)}${selected}"
        data-market-context-symbol="${escapeHtml(node.id)}"
        title="${escapeHtml(marketContextNodeTitle(node))}"
      >
        <span class="market-context-tile-sector">${escapeHtml(displaySectorLabel(node.sector))}</span>
        <strong>${escapeHtml(node.label || node.id)}</strong>
        <span class="market-context-tile-score">${escapeHtml(String(node.context_score ?? 0))}</span>
        <span class="market-context-tile-perf">${escapeHtml(maybePercent(node.performance))}</span>
        <span class="market-context-tile-badges">
          ${(node.badges || []).slice(0, 3).map((badge) => `
            <em>${escapeHtml(marketContextBadgeLabel(badge))}</em>
          `).join("")}
        </span>
      </button>
    `;
  }).join("") || `<div class="market-context-empty">Aucun symbole dans cette vue.</div>`;
}

function renderMarketContextMarketMap(data) {
  const map = document.getElementById("market-context-market-map");
  if (!map) return;
  const nodes = filterMarketContextNodes(data.nodes || []);
  const count = document.getElementById("market-map-count");
  if (count) {
    count.textContent = nodes.length ? `${nodes.length} ticker${nodes.length > 1 ? "s" : ""}` : "";
  }
  if (!nodes.length) {
    map.innerHTML = `<div class="market-context-empty">Aucun symbole dans cette vue.</div>`;
    return;
  }
  const sectors = marketContextSectorGroups(nodes);
  map.innerHTML = sectors.map((sector) => `
    <section class="market-map-sector ${escapeHtml(marketContextPerformanceTone(sector.performance))}">
      <header class="market-map-sector-head">
        <div>
          <strong>${escapeHtml(sector.name)}</strong>
          <span>${escapeHtml(String(sector.count))} ticker${sector.count > 1 ? "s" : ""}</span>
        </div>
        <em>${escapeHtml(maybePercent(sector.performance))}</em>
      </header>
      <div class="market-map-industries">
        ${sector.industries.map((industry) => `
          <article class="market-map-industry">
            <span class="market-map-industry-label">${escapeHtml(industry.name)}</span>
            <div class="market-map-industry-tiles">
              ${industry.nodes.map(renderMarketContextMarketTile).join("")}
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `).join("");
}

function renderMarketContextMarketTile(node) {
  const weight = marketContextTileWeight(node);
  const basis = Math.round(72 + (weight * 18));
  const selected = node.id === marketContextState.selectedSymbol ? " selected" : "";
  return `
    <button
      type="button"
      class="market-map-tile ${escapeHtml(marketContextPerformanceTone(node.performance))}${selected}"
      data-market-context-symbol="${escapeHtml(node.id)}"
      title="${escapeHtml(marketContextNodeTitle(node))}"
      style="--market-map-grow:${escapeHtml(String(weight))}; --market-map-basis:${escapeHtml(String(basis))}px"
    >
      <strong>${escapeHtml(node.label || node.id)}</strong>
      <span>${escapeHtml(maybePercent(node.performance))}</span>
      <em>${escapeHtml(marketContextMapBadge(node))}</em>
    </button>
  `;
}

function marketContextSectorGroups(nodes) {
  const sectorMap = new Map();
  nodes.forEach((node) => {
    const sectorName = displaySectorLabel(node.sector);
    const industryName = node.industry || "General";
    const sector = sectorMap.get(sectorName) || {
      name: sectorName,
      nodes: [],
      industries: new Map(),
    };
    const industry = sector.industries.get(industryName) || { name: industryName, nodes: [] };
    sector.nodes.push(node);
    industry.nodes.push(node);
    sector.industries.set(industryName, industry);
    sectorMap.set(sectorName, sector);
  });
  return Array.from(sectorMap.values()).map((sector) => ({
    ...sector,
    count: sector.nodes.length,
    performance: marketContextAverage(sector.nodes.map((node) => node.performance)),
    industries: Array.from(sector.industries.values())
      .map((industry) => ({
        ...industry,
        performance: marketContextAverage(industry.nodes.map((node) => node.performance)),
      }))
      .sort((left, right) => marketContextIndustryWeight(right) - marketContextIndustryWeight(left)),
  })).sort((left, right) => {
    const scoreDelta = marketContextAverage(right.nodes.map((node) => node.context_score))
      - marketContextAverage(left.nodes.map((node) => node.context_score));
    return scoreDelta || right.count - left.count;
  });
}

function marketContextAverage(values) {
  const numbers = values
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter((value) => Number.isFinite(value));
  if (!numbers.length) return null;
  return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
}

function marketContextIndustryWeight(industry) {
  return industry.nodes.reduce((sum, node) => sum + marketContextTileWeight(node), 0);
}

function marketContextTileWeight(node) {
  const marketCap = Number(node.market_cap);
  if (Number.isFinite(marketCap) && marketCap > 0) {
    return Math.max(1, Math.min(9, Math.log10(marketCap) - 7));
  }
  const value = Number(node.value || 1);
  return Math.max(1, Math.min(6, value));
}

function marketContextPerformanceTone(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "no-data";
  if (number >= 3) return "gain-strong";
  if (number >= 0.75) return "gain";
  if (number > 0) return "gain-soft";
  if (number <= -3) return "loss-strong";
  if (number <= -0.75) return "loss";
  if (number < 0) return "loss-soft";
  return "flat";
}

function marketContextMapBadge(node) {
  if ((node.badges || []).includes("AUTO_ALLOWED")) return "AUTO";
  if ((node.badges || []).includes("WATCH_ONLY")) return "WATCH";
  if (node.metadata_status && node.metadata_status !== "SECTOR_OK") {
    return marketContextMetadataLabel(node.metadata_status);
  }
  return "SETUP";
}

function marketContextNodeTitle(node) {
  const name = node.company_name ? `${node.label || node.id} - ${node.company_name}` : (node.label || node.id);
  const metadata = node.metadata_status ? ` | Metadata: ${marketContextMetadataLabel(node.metadata_status)}` : "";
  return `${name} | Perf stock 1D: ${maybePercent(node.performance)} | Score contexte: ${node.context_score ?? 0}${metadata}`;
}

function renderMarketContextSectors(nodes) {
  const container = document.getElementById("market-context-sectors");
  if (!container) return;
  const grouped = new Map();
  (nodes || []).forEach((node) => {
    const sector = displaySectorLabel(node.sector);
    const item = grouped.get(sector) || { sector, count: 0, score: 0 };
    item.count += 1;
    item.score += Number(node.context_score || 0);
    grouped.set(sector, item);
  });
  const sectors = Array.from(grouped.values())
    .map((item) => ({ ...item, average: Math.round(item.score / Math.max(item.count, 1)) }))
    .sort((left, right) => right.average - left.average);
  container.innerHTML = sectors.map((item) => `
    <article class="market-context-sector ${escapeHtml(marketContextToneFromScore(item.average))}">
      <span>${escapeHtml(item.sector)}</span>
      <strong>${escapeHtml(String(item.average))}</strong>
      <small>${escapeHtml(String(item.count))} setup${item.count > 1 ? "s" : ""}</small>
    </article>
  `).join("") || `<article class="market-context-sector idle">Aucun secteur</article>`;
}

async function renderMarketContextDetail(symbol) {
  const container = document.getElementById("market-context-detail");
  if (!container) return;
  marketContextState.selectedSymbol = symbol;
  const detail = await api(`/api/market-context/symbols/${encodeURIComponent(symbol)}`);
  const tone = marketContextTone(detail.status || detail.context_status);
  renderMarketContextHeatmap(marketContextState.heatmap || { nodes: [] });
  renderMarketContextMarketMap(marketContextState.heatmap || { nodes: [] });
  container.innerHTML = `
    <div class="market-context-detail-head">
      <div>
        <span>${escapeHtml(displaySectorLabel(detail.sector))}</span>
        <strong>${escapeHtml(detail.symbol || symbol)}</strong>
      </div>
      <span class="market-context-score ${escapeHtml(tone)}">${escapeHtml(String(detail.context_score ?? 0))}</span>
    </div>
    <div class="market-context-detail-badges">
      ${(detail.badges || []).map((badge) => `<span>${escapeHtml(marketContextBadgeLabel(badge))}</span>`).join("")}
    </div>
    <dl class="market-context-detail-grid">
      <div title="Variation du stock entre le prix actuel et la cloture precedente."><dt>Perf stock 1D</dt><dd>${escapeHtml(maybePercent(detail.stock_perf_1d))}</dd></div>
      <div title="Variation 1D de l'ETF secteur utilise comme reference."><dt>Perf secteur 1D</dt><dd>${escapeHtml(maybePercent(detail.sector_perf_1d))}</dd></div>
      <div title="Variation 1D de SPY, reference marche large."><dt>Perf SPY 1D</dt><dd>${escapeHtml(maybePercent(detail.spy_perf_1d))}</dd></div>
      <div><dt>RS secteur</dt><dd>${escapeHtml(maybePercent(detail.relative_strength_vs_sector))}</dd></div>
      <div><dt>RS SPY</dt><dd>${escapeHtml(maybePercent(detail.relative_strength_vs_spy))}</dd></div>
      <div><dt>ETF secteur</dt><dd>${escapeHtml(detail.sector_etf || "-")}</dd></div>
      <div><dt>Metadata</dt><dd>${escapeHtml(marketContextMetadataLabel(detail.metadata_status))}</dd></div>
      <div><dt>Source metadata</dt><dd>${escapeHtml(detail.metadata_source || "-")}</dd></div>
      <div><dt>Prix</dt><dd>${escapeHtml(maybeMoney(detail.last_price))}</dd></div>
      <div><dt>Setup</dt><dd>${escapeHtml(detail.setup_status || "-")}</dd></div>
    </dl>
    <div class="market-context-detail-notes">
      ${(detail.warnings || []).map((warning) => `<p>${escapeHtml(warning)}</p>`).join("") || "<p>Aucun warning contexte.</p>"}
      <p>Source: ${escapeHtml(detail.source || "UNKNOWN")} · ${escapeHtml(formatTime(detail.last_update) || "-")}</p>
    </div>
  `;
}

function marketContextDisplaySector(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Non classé";
  const normalized = raw.toLowerCase();
  if (normalized === "unknown" || normalized === "non classe") return "Non classé";
  return raw;
}

function displaySectorLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Secteur inconnu";
  const normalized = raw.toLowerCase();
  if (normalized === "unknown" || normalized === "non classe" || normalized === "non classã©") {
    return "Secteur inconnu";
  }
  return raw;
}

function marketContextMetadataLabel(value) {
  const normalized = String(value || "SECTOR_UNKNOWN").trim().toUpperCase();
  if (normalized === "SECTOR_OK") return "sector ok";
  if (normalized === "SECTOR_MANUAL_OVERRIDE") return "manual override";
  if (normalized === "SECTOR_PROVIDER_MISSING") return "provider missing";
  if (normalized === "SECTOR_ETF_MISSING") return "sector ETF missing";
  if (normalized === "SECTOR_UNKNOWN") return "sector unknown";
  return normalized.replaceAll("_", " ").toLowerCase();
}

function renderMarketContextEmptyDetail() {
  const container = document.getElementById("market-context-detail");
  if (!container) return;
  container.innerHTML = `<div class="market-context-empty">Aucun detail disponible.</div>`;
}

function filterMarketContextNodes(nodes) {
  const view = marketContextState.view;
  if (view === "AUTO_ALLOWED") return nodes.filter((node) => (node.badges || []).includes("AUTO_ALLOWED"));
  if (view === "WATCH_ONLY") return nodes.filter((node) => (node.badges || []).includes("WATCH_ONLY"));
  if (view === "ENTRY_READY") return nodes.filter((node) => (node.badges || []).includes("ENTRY_READY"));
  if (view === "BLOCKED") {
    return nodes.filter((node) => (
      (node.badges || []).includes("BLOCKED")
      || (node.badges || []).includes("WARNING")
      || node.status === "BLOCKED_OR_RISKY_CONTEXT"
    ));
  }
  return nodes;
}

function wireMarketContextControls() {
  const roots = document.querySelectorAll("[data-market-context-root]");
  if (!roots.length) return;
  roots.forEach((root) => root.addEventListener("click", (event) => {
    const filterButton = event.target.closest("[data-market-context-view]");
    if (filterButton) {
      marketContextState.view = filterButton.dataset.marketContextView || "WATCHLIST";
      document.querySelectorAll("[data-market-context-view]").forEach((button) => {
        button.classList.toggle("active", button === filterButton);
      });
      renderMarketContextHeatmap(marketContextState.heatmap || { nodes: [] });
      renderMarketContextMarketMap(marketContextState.heatmap || { nodes: [] });
      return;
    }
    const tile = event.target.closest("[data-market-context-symbol]");
    if (!tile) return;
    renderMarketContextDetail(tile.dataset.marketContextSymbol).catch((error) => toast(error.message));
  }));
}

function marketContextTone(status) {
  if (status === "STRONG_CONTEXT") return "strong";
  if (status === "POSITIVE_CONTEXT") return "positive";
  if (status === "WEAK_CONTEXT") return "weak";
  if (status === "BLOCKED_OR_RISKY_CONTEXT") return "blocked";
  return "neutral";
}

function marketContextToneFromScore(score) {
  if (score >= 60) return "strong";
  if (score >= 20) return "positive";
  if (score <= -60) return "blocked";
  if (score <= -20) return "weak";
  return "neutral";
}

function marketContextBadgeLabel(badge) {
  const labels = {
    AUTO_ALLOWED: "Auto",
    WATCH_ONLY: "Watch",
    ENTRY_READY: "Ready",
    STRONG: "Strong",
    WEAK: "Weak",
    WARNING: "Warning",
    BLOCKED: "Blocked",
    EARNINGS_SOON: "Earnings",
    DIVIDEND_SOON: "Dividend",
    MACRO_RISK: "Macro",
  };
  return labels[badge] || badge;
}

function renderSetups(setups) {
  const tbody = document.getElementById("setups-table");
  if (!tbody) return;
  const columns = renderSetupsColumnHeader();
  const filteredSetups = filterSetups(setups);
  renderSetupsCount(filteredSetups.length, setups.length);
  tbody.innerHTML = filteredSetups.map((setup) => `
    <tr class="${escapeHtml(setupRowClass(setup))}">
      ${columns.map((column) => `<td>${column.render(setup)}</td>`).join("")}
    </tr>
  `).join("") || emptyRow(
    columns.length,
    setupsSearchQuery.trim() ? "Aucun setup ne correspond a la recherche" : "Aucun setup",
  );
}

function setupForecastForSetup(setup) {
  const symbol = String((setup && setup.symbol) || "").toUpperCase();
  return symbol ? forecastWatchlistBySymbol[symbol] || null : null;
}

function renderTimesfmScoreCell(forecast) {
  if (!forecast) return `<span class="forecast-mini muted">-</span>`;
  const status = forecast.forecast_status || forecast.status || "-";
  const score = forecast.metric_score ?? "-";
  return `<span class="forecast-mini ${escapeHtml(forecastTone(status))}">
    <strong>${escapeHtml(score)}</strong>
    <em>${escapeHtml(status)}</em>
  </span>`;
}

function renderTimesfmMoveCell(forecast) {
  if (!forecast) return "";
  return `<span class="${pnlClass(forecast.expected_return_pct)}">${escapeHtml(signedPercent(forecast.expected_return_pct))}</span>`;
}

function renderOpportunityRadar(setups) {
  const list = document.getElementById("opportunity-radar-list");
  const launchpad = document.getElementById("opportunity-radar-launchpad");
  const summary = document.getElementById("opportunity-radar-summary");
  const count = document.getElementById("opportunity-radar-count");
  const secondaryCount = document.getElementById("opportunity-radar-secondary-count");
  if (!list && !launchpad && !summary && !count && !secondaryCount) return;
  const events = (latestSnapshot && Array.isArray(latestSnapshot.events))
    ? latestSnapshot.events
    : [];
  const items = (setups || [])
    .filter((setup) => !setupRadarTerminal(setup))
    .map((setup) => opportunityRadarItem(setup, events))
    .sort(compareOpportunityRadarItems);
  const focusItems = opportunityRadarFocusItems(items);
  const focusKeys = new Set(focusItems.map(opportunityRadarItemKey));
  const detailItems = items
    .filter((item) => !focusKeys.has(opportunityRadarItemKey(item)))
    .slice(0, OPPORTUNITY_RADAR_LIMIT);

  if (count) {
    count.textContent = opportunityRadarCountText(focusItems.length, "dans la hot zone");
  }
  if (summary) {
    summary.innerHTML = renderOpportunityRadarSummary(items, focusItems);
  }
  if (secondaryCount) {
    secondaryCount.textContent = detailItems.length
      ? opportunityRadarCountText(detailItems.length, "en surveillance")
      : "";
  }
  if (launchpad) {
    launchpad.innerHTML = focusItems.map(renderOpportunityRadarFocusCard).join("")
      || `<article class="opportunity-radar-empty opportunity-radar-empty-focus">
        ${items.length
          ? "Aucun setup en hot zone pour le moment. Le radar continue la surveillance."
          : "Aucun setup surveille. Charge un setup pour alimenter le radar."}
      </article>`;
  }
  if (!list) return;
  list.innerHTML = detailItems.map(renderOpportunityRadarCard).join("")
    || `<article class="opportunity-radar-empty">
      ${items.length
        ? "Tous les setups suivis sont deja visibles dans la hot zone."
        : "Aucun setup surveille. Charge un setup pour alimenter le radar."}
    </article>`;
}

function opportunityRadarItem(setup, events) {
  const analysis = latestAnalysisForSetup(setup, events);
  const item = analysisItemForSetup(setup, analysis);
  const latestQuote = latestQuoteForSymbol(events, setup.symbol);
  const trace = item && item.trace
    ? item.trace
    : fallbackAnalysisTrace(setup, latestQuote, item);
  const signal = setupOpportunityState(setup, item, trace);
  const scorePayload = opportunityScorePayload(item);
  const remaining = opportunityRadarRemainingChecks(scorePayload, trace);
  const state = opportunityRadarState(signal);
  const analysisAge = analysis ? secondsSince(analysis.timestamp) : null;
  return {
    setup,
    analysis,
    item,
    trace,
    signal,
    scorePayload,
    remaining,
    state,
    analysisAge,
    latestQuote,
  };
}

function renderOpportunityRadarSummary(items, focusItems) {
  const readyAuto = items.filter((item) => item.state.key === "ready-auto").length;
  const readyWatch = items.filter((item) => item.state.key === "ready-watch").length;
  const nearReady = items.filter((item) => item.state.key === "near").length;
  const watchOnly = items.filter((item) => !item.signal.autoExecution).length;
  const leader = items[0] || null;
  const topScore = leader ? leader.signal.percent : null;
  return [
    opportunityRadarSummaryCell(
      "Hot zone",
      focusItems.length,
      focusItems.length ? "ready" : "idle",
      focusItems.length ? "setup(s) a regarder maintenant" : "zone calme pour le moment",
    ),
    opportunityRadarSummaryCell(
      "Ready Auto",
      readyAuto,
      readyAuto ? "ready" : "idle",
      readyAuto ? "peuvent declencher via TWS" : "aucune execution auto prete",
    ),
    opportunityRadarSummaryCell(
      "Ready Watch",
      readyWatch + nearReady,
      readyWatch + nearReady ? "near" : "idle",
      "surveillance proche du depart",
    ),
    opportunityRadarSummaryCell(
      "Suivi seul",
      watchOnly,
      watchOnly ? "watch" : "idle",
      "observes sans ordre automatique",
    ),
    opportunityRadarSummaryCell(
      "Leader",
      leader ? leader.setup.symbol || "-" : "-",
      leader ? leader.state.tone : "idle",
      leader && topScore !== null ? `${maybePercent(topScore)} de proximite` : "aucun setup charge",
    ),
  ].join("");
}

function opportunityRadarSummaryCell(label, value, tone, note) {
  return `
    <article class="opportunity-radar-stat ${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(note || "")}</small>
    </article>
  `;
}

function opportunityRadarFocusItems(items) {
  const hotItems = items.filter((item) => ["ready-auto", "ready-watch", "near"].includes(item.state.key));
  return (hotItems.length ? hotItems : items).slice(0, 4);
}

function opportunityRadarItemKey(item) {
  return `${item.setup.setup_id || ""}::${item.setup.symbol || ""}`;
}

function opportunityRadarCountText(count, suffix) {
  return `${count} setup${count > 1 ? "s" : ""} ${suffix}`;
}

function renderOpportunityRadarFocusCard(item) {
  const setup = item.setup;
  const signal = item.signal;
  const state = item.state;
  const remaining = opportunityRadarRemainingText(item);
  const age = item.analysisAge === null ? "Aucune analyse" : `Analyse ${formatAge(item.analysisAge)}`;
  const nextStep = opportunityRadarNextStep(item);
  const priceText = maybeMoney(quotePrice(item.latestQuote));
  const width = Math.max(0, Math.min(signal.percent || 0, 100));
  return `
    <article class="opportunity-radar-focus-card ${escapeHtml(state.tone)}">
      <div class="opportunity-radar-focus-head">
        <span class="setup-signal-pill ${escapeHtml(state.tone)}" style="${escapeHtml(signalBadgeStyle(signal))}">${escapeHtml(state.label)}</span>
        <span class="opportunity-radar-focus-mode">${escapeHtml(signal.autoExecution ? "Auto TWS ON" : "Watch only")}</span>
      </div>
      <div class="opportunity-radar-card-head">
        <div>
          <a class="opportunity-radar-symbol" href="${setupDetailPath(setup)}">${escapeHtml(setup.symbol || "-")}</a>
          <span class="opportunity-radar-meta">${escapeHtml(setup.setup_type || "-")} · ${escapeHtml(setup.status || "-")}</span>
        </div>
        <div class="opportunity-radar-score">
          <strong>${escapeHtml(maybePercent(signal.percent))}</strong>
          <span>proximite</span>
        </div>
      </div>
      <div class="opportunity-radar-bar" aria-label="Proximite ${escapeHtml(maybePercent(signal.percent))}">
        <span style="width:${escapeHtml(String(width))}%"></span>
      </div>
      <div class="opportunity-radar-focus-metrics">
        <article>
          <span>Dernier prix</span>
          <strong>${escapeHtml(priceText)}</strong>
        </article>
        <article>
          <span>Blocage principal</span>
          <strong>${escapeHtml(remaining)}</strong>
        </article>
        <article>
          <span>Prochaine etape</span>
          <strong>${escapeHtml(nextStep)}</strong>
        </article>
        <article>
          <span>Derniere analyse</span>
          <strong>${escapeHtml(age)}</strong>
        </article>
      </div>
    </article>
  `;
}

function renderOpportunityRadarCard(item) {
  const setup = item.setup;
  const signal = item.signal;
  const state = item.state;
  const remaining = opportunityRadarRemainingText(item);
  const age = item.analysisAge === null ? "Aucune analyse" : `Analyse ${formatAge(item.analysisAge)}`;
  const nextStep = opportunityRadarNextStep(item);
  const priceText = maybeMoney(quotePrice(item.latestQuote));
  const width = Math.max(0, Math.min(signal.percent || 0, 100));
  return `
    <article class="opportunity-radar-card ${escapeHtml(state.tone)}">
      <div class="opportunity-radar-card-head">
        <div>
          <a class="opportunity-radar-symbol" href="${setupDetailPath(setup)}">${escapeHtml(setup.symbol || "-")}</a>
          <span class="opportunity-radar-meta">${escapeHtml(setup.setup_type || "-")} · ${escapeHtml(setup.status || "-")}</span>
        </div>
        <span class="setup-signal-pill ${escapeHtml(state.tone)}" style="${escapeHtml(signalBadgeStyle(signal))}">${escapeHtml(state.label)}</span>
      </div>
      <div class="opportunity-radar-score">
        <strong>${escapeHtml(maybePercent(signal.percent))}</strong>
        <span>${escapeHtml(signal.autoExecution ? "Auto TWS ON" : "Suivi seul - aucun ordre auto")}</span>
      </div>
      <div class="opportunity-radar-bar" aria-label="Proximite ${escapeHtml(maybePercent(signal.percent))}">
        <span style="width:${escapeHtml(String(width))}%"></span>
      </div>
      <dl class="opportunity-radar-detail">
        <div>
          <dt>Dernier prix</dt>
          <dd>${escapeHtml(priceText)}</dd>
        </div>
        <div>
          <dt>Blocage restant</dt>
          <dd>${escapeHtml(remaining)}</dd>
        </div>
        <div>
          <dt>Derniere analyse</dt>
          <dd>${escapeHtml(age)}</dd>
        </div>
        <div>
          <dt>Prochaine action</dt>
          <dd>${escapeHtml(nextStep)}</dd>
        </div>
      </dl>
    </article>
  `;
}

function opportunityRadarState(signal) {
  if (signal.action === "ENTRY_READY") {
    return signal.autoExecution
      ? { key: "ready-auto", label: "READY AUTO", tone: "ready" }
      : { key: "ready-watch", label: "READY WATCH", tone: "near" };
  }
  if (signal.score >= signal.nearReadyThreshold) {
    return { key: "near", label: "NEAR READY", tone: "near" };
  }
  if (signal.score >= 0.70) {
    return { key: "watching", label: "WATCHING", tone: "watch" };
  }
  return { key: "waiting", label: "WAITING", tone: "idle" };
}

function opportunityRadarRemainingChecks(scorePayload, trace) {
  const fromScore = [];
  if (scorePayload && typeof scorePayload === "object") {
    ["blocking_checks", "waiting_checks"].forEach((key) => {
      if (Array.isArray(scorePayload[key])) fromScore.push(...scorePayload[key]);
    });
  }
  if (fromScore.length) return fromScore;
  const checks = Array.isArray(trace && trace.checks) ? trace.checks : [];
  return checks.filter((check) => {
    const state = normalizeCheckState(check && check.state);
    const label = String((check && check.label) || "");
    return ["wait", "bad", "error"].includes(state) && !opportunityRadarIgnoredCheck(label);
  });
}

function opportunityRadarIgnoredCheck(label) {
  return [
    "Suivi setup",
    "Setup actif",
    "Execution auto TWS",
    "Controle risque",
  ].includes(String(label || ""));
}

function opportunityRadarRemainingText(item) {
  if (item.signal.action === "ENTRY_READY") {
    return item.signal.autoExecution
      ? "Pret: execution automatique autorisee."
      : "Pret: Auto TWS OFF, aucune execution automatique.";
  }
  const check = item.remaining[0];
  if (check) {
    const label = check.label || "Condition";
    const actual = check.actual === undefined || check.actual === null ? "" : String(check.actual);
    const expected = check.expected === undefined || check.expected === null ? "" : String(check.expected);
    if (actual && expected) return `${label}: ${actual} / attendu ${expected}`;
    if (actual) return `${label}: ${actual}`;
    return String(label);
  }
  return item.signal.reason || "Aucun blocage detaille, attendre le prochain scan.";
}

function opportunityRadarNextStep(item) {
  const scoreNextStep = item.scorePayload && item.scorePayload.next_step
    ? String(item.scorePayload.next_step)
    : "";
  return scoreNextStep
    || (item.trace && item.trace.next_step)
    || nextStepFromAction(item.signal.action, item.signal.reason);
}

function compareOpportunityRadarItems(left, right) {
  if (right.signal.score !== left.signal.score) return right.signal.score - left.signal.score;
  if (right.signal.autoExecution !== left.signal.autoExecution) {
    return right.signal.autoExecution ? 1 : -1;
  }
  return String(left.setup.symbol || "").localeCompare(String(right.setup.symbol || ""));
}

function setupRadarTerminal(setup) {
  return OPPORTUNITY_RADAR_TERMINAL_STATUSES.has(setup.status);
}

function setupRowClass(setup) {
  const signal = setupSignalState(setup);
  if (signal.action === "ENTRY_READY") return "setup-row-ready";
  if (signal.score >= signal.nearReadyThreshold) return "setup-row-nearly-ready";
  return "";
}

function renderSetupSignalCell(setup) {
  const signal = setupSignalState(setup);
  const tone = signal.action === "ENTRY_READY"
    ? "ready"
    : signal.score >= signal.nearReadyThreshold
      ? "near"
      : signal.score >= 0.7
        ? "watch"
        : "idle";
  const percentText = signal.percent !== null
    ? `${Math.round(signal.percent)}%`
    : `${Math.round(signal.score * 100)}%`;
  const label = signal.action === "ENTRY_READY"
    ? (signal.autoExecution ? "READY AUTO" : "READY WATCH")
    : percentText;
  const detailParts = [
    `Proximite ${maybePercent(signal.percent)}`,
    signal.autoExecution ? "Auto TWS ON" : "Suivi seul",
  ];
  if (signal.reason) detailParts.push(signal.reason);
  const detail = detailParts.join(" | ");
  return `
    <span class="setup-signal-pill ${escapeHtml(tone)}" style="${escapeHtml(signalBadgeStyle(signal))}" title="${escapeHtml(detail)}">
      ${escapeHtml(label)}
    </span>
  `;
}

function renderSetupPriceCell(setup) {
  const price = setupPriceAtPlacement(setup);
  return price === null ? "" : money(price);
}

function setupSignalState(setup) {
  const events = (latestSnapshot && Array.isArray(latestSnapshot.events))
    ? latestSnapshot.events
    : [];
  const analysis = latestAnalysisForSetup(setup, events);
  const item = analysisItemForSetup(setup, analysis);
  const trace = item && item.trace ? item.trace : null;
  return setupOpportunityState(setup, item, trace);
}

function setupOpportunityState(setup, item, trace) {
  const scorePayload = opportunityScorePayload(item);
  const backendScore = scorePayload ? numberOrNull(scorePayload.score) : null;
  const traceScore = trace ? analysisTraceScore(trace) : null;
  const score = backendScore !== null
    ? backendScore
    : (traceScore !== null ? traceScore : fallbackSetupProgress(setup));
  const backendPercent = scorePayload ? numberOrNull(scorePayload.percent) : null;
  const percent = backendPercent !== null
    ? backendPercent
    : Math.round(score * 1000) / 10;
  const backendThreshold = scorePayload
    ? numberOrNull(scorePayload.near_ready_threshold)
    : null;
  const backendAuto = scorePayload && typeof scorePayload.auto_execution_enabled === "boolean"
    ? scorePayload.auto_execution_enabled
    : null;
  return {
    action: item && item.action ? String(item.action) : "",
    reason: item && item.reason ? String(item.reason) : "",
    score,
    percent,
    label: scorePayload && scorePayload.label ? String(scorePayload.label) : "",
    nearReadyThreshold: backendThreshold !== null ? backendThreshold : 0.96,
    autoExecution: backendAuto !== null ? backendAuto : setupAutoExecutionEnabled(setup),
  };
}

function opportunityScorePayload(item) {
  if (!item || typeof item !== "object") return null;
  const score = item.opportunity_score;
  return score && typeof score === "object" ? score : null;
}

function analysisTraceScore(trace) {
  const checks = Array.isArray(trace && trace.checks) ? trace.checks : [];
  const relevant = checks.filter((check) => {
    const label = String((check && check.label) || "");
    return ![
      "Suivi setup",
      "Setup actif",
      "Execution auto TWS",
      "Controle risque",
    ].includes(label);
  });
  if (!relevant.length) return 0;
  const score = relevant.reduce((total, check) => {
    const state = normalizeCheckState(check && check.state);
    if (state === "ok") return total + 1;
    if (state === "info") return total + 0.85;
    if (state === "wait") return total + 0.45;
    return total;
  }, 0);
  return Math.max(0, Math.min(score / relevant.length, 1));
}

function normalizeCheckState(value) {
  const state = String(value || "wait").toLowerCase();
  if (["ok", "info", "wait", "bad", "error"].includes(state)) return state;
  if (state === "warn" || state === "waiting") return "wait";
  if (state === "blocked") return "bad";
  return "wait";
}

function fallbackSetupProgress(setup) {
  if (SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status)) return 0;
  if (setup.status === "ENTRY_READY") return 1;
  if (setup.status === "WAITING_ENTRY_SIGNAL") return 0.82;
  if (setup.status === "WAITING_ACTIVATION") return 0.45;
  return 0.25;
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

function renderOrders(orders) {
  const tbody = document.getElementById("orders-table");
  if (!tbody) return;
  const rows = Array.isArray(orders) ? orders : [];
  const activeOrders = rows.filter((order) => orderIsBrokerActive(order));
  const preparedOrders = rows.filter((order) => (
    String(order.broker_order_status || order.broker_live_status || "") === "PREPARED_NOT_TRANSMITTED"
  ));
  setText("orders-active-count", activeOrders.length);
  setText("orders-prepared-count", preparedOrders.length);
  const runtime = (latestSnapshot || {}).runtime || {};
  const connection = String(runtime.connection || runtime.connection_label || "").toUpperCase();
  const emptyText = connection === "DISCONNECTED" || connection === "ERROR"
    ? "TWS deconnecte: ordres actifs non verifiables."
    : "Aucun ordre actif TWS";
  const sorted = [...rows].sort((a, b) => {
    const activeDelta = Number(orderIsBrokerActive(b)) - Number(orderIsBrokerActive(a));
    if (activeDelta !== 0) return activeDelta;
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
  tbody.innerHTML = renderOrderRows(sorted, {
    detailPrefix: "order-detail",
    emptyText,
  });
}

function renderOrderHistory(orders) {
  const tbody = document.getElementById("orders-history-table");
  const rows = Array.isArray(orders) ? orders : [];
  setText("orders-history-count", rows.length);
  if (!tbody) return;
  const sorted = [...rows].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  tbody.innerHTML = renderOrderRows(sorted, {
    detailPrefix: "order-history-detail",
    emptyText: "Aucun historique local",
    history: true,
  });
}

function renderOrderRows(orders, options = {}) {
  const rows = Array.isArray(orders) ? orders : [];
  const history = Boolean(options.history);
  const detailPrefix = options.detailPrefix || "order-detail";
  const emptyText = options.emptyText || "Aucun ordre";
  const allowInternalFill = !history && ((latestSnapshot || {}).runtime || {}).broker_connector === "simulated";
  return rows.map((order) => {
    const safeDetailId = `${detailPrefix}-${cssSafeId(order.id)}`;
    const actionButtons = history
      ? `${canDeleteOrder(order) ? `<button class="danger-small" type="button" data-action="delete-order" data-order="${escapeHtml(order.id)}" data-symbol="${escapeHtml(order.symbol)}">Suppr</button>` : ""}`
      : `
          ${allowInternalFill && order.status === "SUBMITTED" ? `<button type="button" data-action="fill" data-order="${escapeHtml(order.id)}">Test fill</button>` : ""}
          ${canAttachMissingStop(order) ? `<button type="button" data-action="attach-stop" data-order="${escapeHtml(order.id)}" data-symbol="${escapeHtml(order.symbol)}">Attach SL</button>` : ""}
          ${orderIsBrokerActive(order) || order.status === "SUBMITTED" ? `<button class="danger-small" type="button" data-action="cancel-order" data-order="${escapeHtml(order.id)}">Cancel</button>` : ""}
          ${canDeleteOrder(order) ? `<button class="danger-small" type="button" data-action="delete-order" data-order="${escapeHtml(order.id)}" data-symbol="${escapeHtml(order.symbol)}">Suppr</button>` : ""}
        `;
    return `
    <tr>
      <td>
        <button type="button" class="link-like" data-action="toggle-order-detail" data-target="${safeDetailId}" title="Voir le detail">+</button>
      </td>
      <td>${escapeHtml(order.symbol)}</td>
      <td>${escapeHtml(order.side)}</td>
      <td>${escapeHtml(order.order_type)}</td>
      <td>${escapeHtml(order.quantity)}</td>
      <td>${escapeHtml(describeOrderPrice(order))}</td>
      <td>${escapeHtml(describeOrderStop(order))}</td>
      <td>${orderSourceBadge(order)}</td>
      <td>${escapeHtml(order.setup_id)}</td>
      <td>
        <div class="row-actions">
          ${actionButtons}
        </div>
      </td>
    </tr>
    <tr id="${safeDetailId}" class="order-detail-row" hidden>
      <td colspan="10">
        <div class="order-detail">
          <span><strong>ID local:</strong> ${escapeHtml(order.id)}</span>
          <span><strong>Broker ID:</strong> ${escapeHtml(order.broker_order_id || "-")}</span>
          <span><strong>Perm ID:</strong> ${escapeHtml(order.broker_perm_id || "-")}</span>
          <span><strong>Parent:</strong> ${escapeHtml(order.parent_id || "-")}</span>
          <span><strong>Stop lie:</strong> ${escapeHtml(order.stop_order_id || "-")}</span>
          <span><strong>Bracket:</strong> ${order.bracket_order ? "OUI" : "NON"}</span>
          <span><strong>Statut local:</strong> ${statusBadge(order.status)}</span>
          <span><strong>Protection:</strong> ${escapeHtml(describeProtectionStatus(order.protection_status))}</span>
          <span><strong>Diagnostic:</strong> ${escapeHtml(describeOrderDiagnostic(order))}</span>
        </div>
      </td>
    </tr>
  `;
  }).join("") || emptyRow(10, emptyText);
}

function renderLocalOrderOrphans(orders) {
  const tbody = document.getElementById("local-order-orphans-table");
  setText("local-order-orphans-count", Array.isArray(orders) ? orders.length : 0);
  if (!tbody) return;
  const rows = Array.isArray(orders) ? orders : [];
  tbody.innerHTML = rows.map((order) => `
    <tr>
      <td>${escapeHtml(order.symbol || "")}</td>
      <td>${escapeHtml(order.side || "")}</td>
      <td>${escapeHtml(order.order_type || "")}</td>
      <td>${escapeHtml(order.quantity ?? "")}</td>
      <td>${escapeHtml(describeOrderPrice(order))}</td>
      <td>${statusBadge(order.status || "-")}</td>
      <td>${statusBadge(order.broker_order_status || "LOCAL_ORPHAN")}</td>
      <td>${escapeHtml(describeOrderDiagnostic(order))}</td>
    </tr>
  `).join("") || emptyRow(8, "Aucune intention locale orpheline");
}

function describeOrderPrice(order) {
  const parts = [];
  if (order.trigger_price != null) parts.push(`T ${maybeMoney(order.trigger_price)}`);
  if (order.limit_price != null) parts.push(`L ${maybeMoney(order.limit_price)}`);
  if (!parts.length) return order.order_type === "MKT" ? "MKT" : "-";
  return parts.join(" / ");
}

function orderSourceBadge(order) {
  const brokerStatus = order.broker_order_status || order.broker_live_status || "";
  if (brokerStatus === "NO_BROKER_ORDER") {
    return statusBadge("LOCAL_ONLY");
  }
  return statusBadge(brokerStatus || order.status || "UNKNOWN");
}

function renderExecutions(executions) {
  const tbody = document.getElementById("executions-table");
  if (!tbody) return;
  const rows = Array.isArray(executions) ? executions : [];
  tbody.innerHTML = rows.map((execution) => `
    <tr>
      <td>${escapeHtml(formatTime(execution.timestamp))}</td>
      <td>${escapeHtml(execution.symbol)}</td>
      <td>${escapeHtml(execution.side)}</td>
      <td>${escapeHtml(execution.quantity)}</td>
      <td>${money(execution.price)}</td>
      <td>${escapeHtml(execution.order_id || execution.broker_perm_id || "-")}</td>
    </tr>
  `).join("") || emptyRow(6, "Aucune execution aujourd'hui");
}

function manualOrderPayload() {
  const numberOrNull = (id) => {
    const raw = document.getElementById(id)?.value;
    if (raw === undefined || raw === null || String(raw).trim() === "") return null;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : null;
  };
  return {
    symbol: (document.getElementById("manual-symbol")?.value || "").trim().toUpperCase(),
    side: document.getElementById("manual-side")?.value || "BUY",
    quantity: Number(document.getElementById("manual-quantity")?.value || 0),
    order_type: document.getElementById("manual-order-type")?.value || "LMT",
    limit_price: numberOrNull("manual-limit"),
    trigger_price: numberOrNull("manual-trigger"),
    stop_loss: numberOrNull("manual-stop"),
  };
}

function renderManualOrderRisk(result) {
  const container = document.getElementById("manual-order-risk");
  if (!container) return;
  container.hidden = false;
  const refusal = result.validation_error || result.block;
  if (refusal) {
    container.innerHTML = `<span class="risk-blocked">${escapeHtml(refusal.message || refusal.reason_code || "Refuse")}</span>`;
    return;
  }
  const risk = result.risk || {};
  const parts = [];
  if (risk.reference_entry_price != null) parts.push(`<span><strong>Entree (pire cas):</strong> ${money(risk.reference_entry_price)}</span>`);
  if (risk.risk_per_share != null) parts.push(`<span><strong>R/share:</strong> ${money(risk.risk_per_share)}</span>`);
  if (risk.risk_usd != null) parts.push(`<span><strong>Risque:</strong> $${money(risk.risk_usd)}</span>`);
  if (risk.risk_pct_of_account != null) parts.push(`<span><strong>% compte:</strong> ${maybePercent(risk.risk_pct_of_account)}</span>`);
  if (risk.position_amount_usd != null) parts.push(`<span><strong>Taille position:</strong> $${money(risk.position_amount_usd)}</span>`);
  const costGate = risk.cost_gate || {};
  if (costGate.cost_to_risk_ratio != null) {
    parts.push(`<span><strong>Couts/risque:</strong> ${maybePercent(costGate.cost_to_risk_ratio * 100)}${costGate.gate && costGate.gate !== "OK" ? ` (${escapeHtml(costGate.gate)})` : ""}</span>`);
  }
  container.innerHTML = parts.join("") || "<span>Risque non calculable (ordre SELL ou donnees manquantes).</span>";
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

function orderIsBrokerActive(order) {
  const brokerStatus = order.broker_order_status || order.broker_live_status || "";
  if (["PENDING_SUBMIT", "TRANSMITTED", "SUBMITTED", "PARTIALLY_FILLED"].includes(brokerStatus)) {
    return true;
  }
  if (brokerStatus === "PREPARED_NOT_TRANSMITTED") return false;
  return Boolean(order.is_active);
}

function describeOrderStop(order) {
  if (order.stop_price != null) {
    return maybeMoney(order.stop_price);
  }
  if (order.stop_order_status) {
    return order.stop_order_status;
  }
  return "MISSING";
}

function describeProtectionStatus(status) {
  return String(status || "NO_ENTRY_ORDER").replaceAll("_", " ");
}

function describeOrderDiagnostic(order) {
  if (order.diagnostic_message) return order.diagnostic_message;
  const brokerStatus = String(order.broker_order_status || "");
  if (["PENDING_SUBMIT", "TRANSMITTED", "PARTIALLY_FILLED"].includes(brokerStatus)) {
    return `Broker confirms working order: ${brokerStatus}`;
  }
  if (brokerStatus === "PREPARED_NOT_TRANSMITTED") {
    return "Prepared in TWS but not transmitted";
  }
  if (brokerStatus === "NO_BROKER_ORDER") {
    return "Local intent only; TWS has no matching working order";
  }
  if (["CREATED", "SUBMITTED"].includes(String(order.status || ""))) {
    return "Local intent only; broker confirmation unavailable";
  }
  return "Historique local";
}

function canDeleteOrder(order) {
  return ["REJECTED", "CANCELLED", "FILLED", "ERROR"].includes(order.status);
}

function canAttachMissingStop(order) {
  return String(order.side || "").toUpperCase() === "BUY"
    && ["CREATED", "SUBMITTED"].includes(String(order.status || ""))
    && !order.stop_order_id
    && order.stop_price == null;
}

function renderPositions(positions) {
  const tbody = document.getElementById("positions-table");
  if (!tbody) return;
  tbody.innerHTML = positions.map((position) => `
    <tr>
      <td>${escapeHtml(position.symbol)}</td>
      <td>${escapeHtml(position.quantity)}</td>
      <td>${money(position.average_price)}</td>
      <td>${money(position.current_price)}</td>
      <td>${money(position.unrealized_pnl)}</td>
      <td>${position.current_stop == null ? "" : money(position.current_stop)}</td>
      <td>${money(position.risk_remaining)}</td>
      <td>${escapeHtml(position.setup_id)}</td>
      <td>
        <div class="row-actions">
          <button type="button" data-action="move-stop" data-symbol="${escapeHtml(position.symbol)}">Stop</button>
        </div>
      </td>
    </tr>
  `).join("") || emptyRow(9, "Aucune position");
}

function renderEvents(containerId, events) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = events.map((event) => {
    const data = event.data && Object.keys(event.data).length
      ? `<pre class="event-data">${escapeHtml(JSON.stringify(event.data, null, 2))}</pre>`
      : "";
    return `
      <article class="event-item">
        <time>${escapeHtml(formatTime(event.timestamp))}</time>
        <span>${escapeHtml(event.level)}</span>
        <div>
          <strong>${escapeHtml(event.event_type)}</strong>
          <div>${escapeHtml(event.message)}</div>
          ${data}
        </div>
      </article>
    `;
  }).join("") || `<article class="event-item"><span>Aucun evenement</span></article>`;
}

function renderTwsEvents(containerId, events) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = events.map((event) => {
    const data = event.data || {};
    const extra = data.extra && Object.keys(data.extra).length
      ? `<pre class="event-data">${escapeHtml(JSON.stringify(data.extra, null, 2))}</pre>`
      : "";
    const detailParts = [
      data.request ? `Req: ${data.request}` : "",
      data.detail ? `Detail: ${data.detail}` : "",
      data.sent_at ? `Envoyee: ${formatTime(data.sent_at)}` : "",
      data.response_at ? `Reponse: ${formatTime(data.response_at)}` : "",
      data.latency_ms != null ? `Latence: ${data.latency_ms} ms` : "",
      data.status ? `Statut: ${data.status}` : "",
      data.error ? `Erreur: ${data.error}` : "",
    ].filter(Boolean);
    const meta = detailParts.length
      ? `<div class="event-meta">${escapeHtml(detailParts.join(" | "))}</div>`
      : "";
    return `
      <article class="event-item">
        <time>${escapeHtml(formatTime(event.timestamp))}</time>
        <span>${escapeHtml(event.level)}</span>
        <div>
          <strong>${escapeHtml(event.message || event.event_type)}</strong>
          ${meta}
          ${extra}
        </div>
      </article>
    `;
  }).join("") || `<article class="event-item"><span>Aucun echange TWS</span></article>`;
}

function renderSettings(snapshot) {
  const runtime = document.getElementById("settings-runtime");
  const risk = document.getElementById("settings-risk");
  const brokerSelect = document.getElementById("broker-connector-select");
  const brokerHost = document.getElementById("broker-host-input");
  const brokerPort = document.getElementById("broker-port-input");
  const brokerClientId = document.getElementById("broker-client-id-input");
  const brokerMessage = document.getElementById("broker-account-message");
  const twsAuditEnabled = document.getElementById("tws-audit-enabled");
  const twsAuditMessage = document.getElementById("tws-audit-message");
  const brokerConfig = ((snapshot.config || {}).broker || {});
  const twsAudit = ((snapshot.config || {}).tws_audit || {});
  if (runtime) {
    runtime.innerHTML = dlRows(snapshot.runtime || {});
  }
  if (risk) {
    risk.innerHTML = dlRows((snapshot.config || {}).risk || {}, SETTINGS_RISK_LABELS);
  }
  if (brokerSelect) {
    brokerSelect.value = (snapshot.runtime || {}).broker_connector
      || brokerConfig.connector
      || "paper";
  }
  if (brokerHost) brokerHost.value = brokerConfig.host || "127.0.0.1";
  if (brokerPort) brokerPort.value = brokerConfig.port || "";
  if (brokerClientId) brokerClientId.value = (snapshot.runtime || {}).broker_client_id
    || brokerConfig.client_id
    || "";
  if (brokerMessage) {
    brokerMessage.textContent = (snapshot.runtime || {}).broker_message || "";
    brokerMessage.classList.toggle(
      "error",
      ((snapshot.runtime || {}).connection || "") === "ERROR",
    );
  }
  if (twsAuditEnabled) {
    twsAuditEnabled.checked = Boolean(twsAudit.enabled);
  }
  if (twsAuditMessage) {
    twsAuditMessage.textContent = twsAudit.enabled
      ? "Audit actif: les appels TWS detailles sont visibles dans Logs. Les heartbeats OK restent hors evenements."
      : "Audit desactive: les quotes stock et les erreurs TWS restent journalisees.";
    twsAuditMessage.classList.toggle("success", Boolean(twsAudit.enabled));
  }
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

async function refreshForecastWatchlist() {
  const tbody = document.getElementById("setups-table");
  if (!tbody) return;
  try {
    const rows = await api("/api/forecast/watchlist");
    setForecastWatchlistBySymbol(Object.fromEntries(
      (Array.isArray(rows) ? rows : []).map((item) => [String(item.symbol || "").toUpperCase(), item]),
    ));
    renderSetups((latestSnapshot || {}).setups || []);
  } catch (error) {
    setForecastWatchlistBySymbol({});
  }
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

function wireSetupsColumnControls() {
  const controls = document.getElementById("setups-column-controls");
  const resetButton = document.getElementById("setups-columns-reset");
  const search = document.getElementById("setups-search");
  if (resetButton) resetButton.addEventListener("click", resetSetupsColumns);
  if (search) {
    setupsSearchQuery = search.value;
    search.addEventListener("input", () => {
      setupsSearchQuery = search.value;
      renderSetups((latestSnapshot || {}).setups || []);
    });
  }
  if (!controls) return;

  controls.addEventListener("click", (event) => {
    const button = event.target.closest("[data-column-move]");
    if (!button) return;
    moveSetupsColumn(button.dataset.columnId, button.dataset.columnMove === "left" ? -1 : 1);
  });

  controls.addEventListener("dragstart", (event) => {
    const chip = event.target.closest(".column-chip");
    if (!chip || !event.dataTransfer) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", chip.dataset.columnId);
    chip.classList.add("dragging");
  });

  controls.addEventListener("dragend", () => {
    controls.querySelectorAll(".column-chip.dragging").forEach((chip) => {
      chip.classList.remove("dragging");
    });
  });

  controls.addEventListener("dragover", (event) => {
    if (!event.target.closest(".column-chip")) return;
    event.preventDefault();
  });

  controls.addEventListener("drop", (event) => {
    const chip = event.target.closest(".column-chip");
    if (!chip || !event.dataTransfer) return;
    event.preventDefault();
    const sourceId = event.dataTransfer.getData("text/plain");
    const rect = chip.getBoundingClientRect();
    const insertAfter = event.clientX > rect.left + rect.width / 2;
    reorderSetupsColumn(sourceId, chip.dataset.columnId, insertAfter);
  });
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

function wireMarketForm() {
  const form = document.getElementById("market-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    data.bullish_candle = form.elements.bullish_candle.checked;
    try {
      await api("/api/market/snapshot", { method: "POST", body: data });
      toast("Tick envoye");
    } catch (error) {
      toast(error.message);
    }
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

function setupTextPayload(form) {
  const data = formData(form);
  return {
    symbol: data.symbol,
    text: data.text,
    enabled: true,
  };
}

function syncTickerFieldFromSetupText(form) {
  if (!form) return;
  const text = String(form.elements.text && form.elements.text.value || "").trim();
  if (!text.startsWith("{")) return;
  try {
    const parsed = JSON.parse(text);
    const skeleton = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed.skeleton && typeof parsed.skeleton === "object" ? parsed.skeleton : parsed)
      : null;
    const symbol = String((skeleton && skeleton.symbol) || "").trim().toUpperCase();
    if (!symbol) return;
    const symbolField = form.elements.symbol;
    if (symbolField && symbolField.value !== symbol) {
      symbolField.value = symbol;
    }
  } catch (error) {
    // Ignore invalid JSON here; the server-side conversion will report it.
  }
}

function syncTickerFieldFromSetupResult(form, result) {
  if (!form) return;
  const config = result && (result.config || (result.setup && result.setup.config));
  const symbol = String((config && config.symbol) || "").trim().toUpperCase();
  if (!symbol) return;
  const symbolField = form.elements.symbol;
  if (symbolField && symbolField.value !== symbol) {
    symbolField.value = symbol;
  }
}

function renderSetupToolsOutput(payload, messageText = "", options = {}) {
  const message = document.getElementById("setup-tools-message");
  const output = document.getElementById("setup-tools-output");
  if (message) {
    message.hidden = !messageText;
    message.textContent = messageText;
    message.classList.remove("error");
  }
  if (output) {
    output.hidden = Boolean(options.messageOnly);
    output.textContent = options.messageOnly ? "" : JSON.stringify(payload, null, 2);
  }
}

function renderSetupToolsError(messageText) {
  const message = document.getElementById("setup-tools-message");
  const output = document.getElementById("setup-tools-output");
  if (message) {
    message.hidden = false;
    message.textContent = messageText;
    message.classList.add("error");
  }
  if (output) {
    output.hidden = true;
    output.textContent = "";
  }
}

function renderSetupPreview(result) {
  const message = document.getElementById("setup-conversion-result");
  const preview = document.getElementById("setup-preview");
  if (!message || !preview) return;
  const config = result.config || (result.setup && result.setup.config);
  const setupId = config ? config.setup_id : "";
  const extracted = result.extracted || {};
  const label = extracted.json_detected ? "JSON OK" : "Conversion OK";
  const warnings = result.warnings && result.warnings.length
    ? ` | ${result.warnings.join(" | ")}`
    : "";
  message.hidden = false;
  message.classList.remove("error");
  message.textContent = `${label}${setupId ? `: ${setupId}` : ""}${warnings}`;
  preview.hidden = false;
  preview.textContent = JSON.stringify(config || result, null, 2);
}

function renderSetupPreviewError(messageText) {
  const message = document.getElementById("setup-conversion-result");
  const preview = document.getElementById("setup-preview");
  if (!message || !preview) return;
  message.hidden = false;
  message.classList.add("error");
  message.textContent = messageText;
  preview.hidden = true;
  preview.textContent = "";
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

async function fetchSetupSymbolEvents(symbol) {
  if (!symbol) return [];
  const params = new URLSearchParams({ limit: "600", symbol });
  const result = await api(`/api/events?${params.toString()}`);
  return result.items || [];
}

async function fetchSetupChartQuotes(symbol, timeframe, fallbackEvents) {
  setSetupChartDataMessage("");
  setSetupChartDataMeta({});
  const normalized = normalizeSetupChartTimeframe(timeframe);
  const fallbackQuotes = extractQuoteEvents(fallbackEvents, normalized);
  setSetupChartDataMeta({
    timeframe: normalized,
    timeframe_label: setupChartTimeframeLabel(normalized),
  });
  if (!symbol) return fallbackQuotes;
  const params = new URLSearchParams({ timeframe: normalized });
  try {
    const result = await api(`/api/market/history/${encodeURIComponent(symbol)}?${params.toString()}`);
    setSetupChartDataMeta(result || setupChartDataMeta);
    const quotes = historicalQuotesFromPayload(result);
    if (quotes.length) return quotes.slice(-SETUP_CHART_MAX_SOURCE_CANDLES);
    setSetupChartDataMessage(result.message
      || `Aucune bougie ${setupChartTimeframeLabel(normalized)} disponible`);
  } catch (error) {
    setSetupChartDataMessage(error.message);
  }
  if (fallbackQuotes.length) {
    const latest = fallbackQuotes[fallbackQuotes.length - 1];
    setSetupChartDataMeta({
      ...setupChartDataMeta,
      historical_bar_size: latest.historical_bar_size,
      historical_duration: latest.historical_duration,
      timeframe: normalized,
      timeframe_label: setupChartTimeframeLabel(normalized),
      source: latest.source || "events",
    });
    return fallbackQuotes;
  }
  return [];
}

async function fetchSetupForecast(setup, options = {}) {
  if (!setup || !setup.symbol) return null;
  const params = new URLSearchParams({
    timeframe: "15m",
    horizon: "4",
    target: "log_return",
  });
  if (setup.setup_id) params.set("setup_id", setup.setup_id);
  if (options.cachedOnly) params.set("cached_only", "true");
  if (options.forceRefresh) {
    const result = await api("/api/forecasting/run", {
      method: "POST",
      body: {
        symbol: setup.symbol,
        setup_id: setup.setup_id,
        timeframe: "15m",
        horizon: 4,
        target: "log_return",
        force_refresh: true,
        ensemble: true,
      },
    });
    return result.forecast || null;
  }
  const result = await api(`/api/forecast/${encodeURIComponent(setup.symbol)}?${params.toString()}`);
  return result.forecast || null;
}

async function renderSetupForecastPanel(setup = currentSetupDetailSetup, options = {}) {
  const summary = document.getElementById("setup-forecast-summary");
  const message = document.getElementById("setup-forecast-message");
  if (!summary) return;
  if (!setup) {
    summary.innerHTML = "";
    drawTimesfmForecastChart(null);
    return;
  }
  try {
    const forecast = await fetchSetupForecast(setup, options);
    renderSetupForecastSummary(forecast);
    await renderSetupForecastStackSummary(setup);
    drawTimesfmForecastChart(forecast);
    if (message) {
      const nonBlockingStatus = forecast && forecast.status === "NO_CACHED_FORECAST";
      const forecastOk = forecast && ["OK", "PARTIAL"].includes(forecast.status);
      message.hidden = !forecast || forecastOk;
      message.classList.toggle(
        "error",
        Boolean(forecast && !forecastOk && !nonBlockingStatus),
      );
      message.textContent = forecast && forecast.status !== "OK"
        ? (
          nonBlockingStatus
            ? "Aucun forecast en cache. Clique Recalculer pour lancer le forecast stack."
            : `${forecast.status}: ${forecast.error || "forecast indisponible"}`
        )
        : "";
    }
    await refreshForecastWatchlist();
  } catch (error) {
    summary.innerHTML = "";
    drawTimesfmForecastChart(null, error.message);
    if (message) {
      message.hidden = false;
      message.classList.add("error");
      message.textContent = error.message;
    }
  }
}

async function renderSetupForecastStackSummary(setup) {
  const target = document.getElementById("setup-forecast-stack-summary");
  if (!target || !setup || !setup.symbol) return;
  const params = new URLSearchParams({ timeframe: "15m" });
  if (setup.setup_id) params.set("setup_id", setup.setup_id);
  const result = await api(`/api/forecasting/stack-summary/${encodeURIComponent(setup.symbol)}?${params.toString()}`);
  target.innerHTML = dlRows({
    consensus: result.consensus,
    score_impact: `${Number(result.score_impact || 0) >= 0 ? "+" : ""}${result.score_impact || 0}`,
    warnings: (result.warnings || []).join(" | ") || "NONE",
    successful_models: `${result.successful_model_count || 0} / ${result.model_count || 0}`,
    forecast_policy: result.forecast_execution_policy || "ADVISORY_ONLY",
    display: result.forecast_available_for_display ? "YES" : "NO",
    execution: result.forecast_eligible_for_execution ? "YES" : "NO",
    execution_reasons: formatStatusList(result.forecast_execution_block_reasons, "NONE"),
  });
  renderV2Table("setup-forecast-stack-members", [
    ["model_name", "Model"],
    ["status", "Status"],
    ["direction", "Direction"],
    ["direction_confidence", "Confidence"],
    ["reliability_status", "Reliability"],
    ["samples_display", "Samples"],
    ["eligible_for_display", "Display"],
    ["eligible_for_execution", "Execution"],
    ["execution_block_reason", "Reason"],
    ["prob_touch_entry", "P(entry)"],
    ["prob_touch_stop_before_entry", "P(stop first)"],
    ["uncertainty_width_pct", "Interval %"],
  ], (result.members || []).map((item) => {
    const forecastOk = String(item.status || "").toUpperCase() === "OK";
    return {
      ...item,
      status: statusBadge(item.status),
      direction: forecastOk ? (item.direction || "-") : "-",
      direction_confidence: forecastOk ? maybeProbability(item.direction_confidence) : "-",
      reliability_status: statusBadge(item.reliability_status || item.reliability_grade || "-"),
      samples_display: `${item.accuracy_samples ?? item.sample_size ?? 0}/${item.min_accuracy_samples_required ?? 30}`,
      eligible_for_display: item.eligible_for_display ? "YES" : "NO",
      eligible_for_execution: item.eligible_for_execution ? "YES" : "NO",
      execution_block_reason: statusLabel(item.execution_block_reason || "-"),
      prob_touch_entry: forecastOk ? maybeProbability(item.prob_touch_entry) : "-",
      prob_touch_stop_before_entry: forecastOk ? maybeProbability(item.prob_touch_stop_before_entry) : "-",
      uncertainty_width_pct: forecastOk ? maybePercent(item.uncertainty_width_pct) : "-",
    };
  }));
  return result;
}

function renderSetupForecastSummary(forecast) {
  const summary = document.getElementById("setup-forecast-summary");
  if (!summary) return;
  if (!forecast) {
    summary.innerHTML = dlRows({ status: "NO_FORECAST", used_for_decision: "NO" });
    return;
  }
  summary.innerHTML = dlRows({
    status: forecast.forecast_status || forecast.status || "-",
    score: `${forecast.metric_score ?? 0} / 100`,
    direction: forecast.direction || "-",
    expected_move_1h: signedPercent(forecast.forecast_expected_return_pct),
    horizon: `${forecast.horizon_bars || 4} x ${forecast.timeframe || "15m"}`,
    target: forecast.target || "-",
    confidence: forecast.confidence || "-",
    median_above_entry_trigger: yesNo(forecast.median_above_entry_trigger),
    q10_above_support: yesNo(forecast.q10_above_support),
    q10_above_stop: yesNo(forecast.q10_above_stop),
    used_for_decision: forecast.used_for_decision ? "YES" : "NO",
    last_update: formatTime(forecast.generated_at) || "-",
    reference_price: maybeMoney(forecast.reference_price),
    median_end_price: maybeMoney(forecast.median_end_price),
    median_return_pct: signedPercent(forecast.median_return_pct),
    q10_end_price: maybeMoney(forecast.q10_end_price),
    q50_end_price: maybeMoney(forecast.q50_end_price),
    q90_end_price: maybeMoney(forecast.q90_end_price),
    direction_basis: forecast.direction_basis || "q50_last_vs_reference_price",
  }, {
    direction: "direction",
    expected_move_1h: "expected move 1h",
    median_above_entry_trigger: "median above entry",
    q10_above_support: "q10 above support",
    q10_above_stop: "q10 above stop",
    used_for_decision: "used for decision",
    last_update: "last update",
    reference_price: "reference_price",
    median_end_price: "median_end_price",
    median_return_pct: "median_return_pct",
    q10_end_price: "q10_end_price",
    q50_end_price: "q50_end_price",
    q90_end_price: "q90_end_price",
    direction_basis: "direction_basis",
  });
}

function wireSetupForecastPanel() {
  onClick("setup-forecast-refresh", async () => {
    await renderSetupForecastPanel(currentSetupDetailSetup, { forceRefresh: true });
  });
}

function drawTimesfmForecastChart(forecast, message = "") {
  const canvas = document.getElementById("setup-forecast-chart");
  const empty = document.getElementById("setup-forecast-empty");
  if (!canvas) return;
  const parent = canvas.parentElement;
  const width = Math.max(320, Math.floor((parent && parent.clientWidth) || canvas.clientWidth || 620));
  const height = window.innerWidth < 720 ? 260 : 320;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = "100%";
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fbfcfa";
  ctx.fillRect(0, 0, width, height);

  const q10 = Array.isArray(forecast && forecast.q10_path) ? forecast.q10_path.map(numberOrNull).filter((value) => value !== null) : [];
  const q50 = Array.isArray(forecast && forecast.q50_path) ? forecast.q50_path.map(numberOrNull).filter((value) => value !== null) : [];
  const q90 = Array.isArray(forecast && forecast.q90_path) ? forecast.q90_path.map(numberOrNull).filter((value) => value !== null) : [];
  if (!forecast || !["OK", "PARTIAL"].includes(forecast.status) || !q50.length) {
    if (empty) {
      empty.hidden = false;
      empty.textContent = message || (forecast && forecast.error) || "Forecast TimesFM indisponible";
    }
    ctx.fillStyle = "#7d8778";
    ctx.font = "700 13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(message || (forecast && forecast.status) || "No forecast", width / 2, height / 2);
    return;
  }
  if (empty) empty.hidden = true;

  const current = numberOrNull(forecast.current_price);
  const reference = numberOrNull(forecast.reference_price);
  const entry = numberOrNull(forecast.entry_trigger_reference);
  const support = numberOrNull(forecast.support_level_reference);
  const stop = numberOrNull(forecast.stop_level_reference);
  const values = [...q10, ...q50, ...q90, current, reference, entry, support, stop].filter((value) => value !== null);
  let minValue = Math.min(...values);
  let maxValue = Math.max(...values);
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue) || minValue === maxValue) {
    minValue = (current || 0) - 1;
    maxValue = (current || 0) + 1;
  }
  const padding = Math.max((maxValue - minValue) * 0.12, 0.01);
  minValue -= padding;
  maxValue += padding;
  const margins = { top: 24, right: 82, bottom: 34, left: 28 };
  const plotWidth = width - margins.left - margins.right;
  const plotHeight = height - margins.top - margins.bottom;
  const xForIndex = (index) => margins.left + (plotWidth * index) / Math.max(q50.length - 1, 1);
  const yForPrice = (price) => margins.top + ((maxValue - price) / (maxValue - minValue)) * plotHeight;

  ctx.strokeStyle = "#d9ded5";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#7d8778";
  ctx.font = "11px sans-serif";
  for (let index = 0; index <= 4; index += 1) {
    const y = margins.top + (plotHeight * index) / 4;
    const value = maxValue - ((maxValue - minValue) * index) / 4;
    ctx.beginPath();
    ctx.moveTo(margins.left, y);
    ctx.lineTo(margins.left + plotWidth, y);
    ctx.stroke();
    ctx.fillText(value.toFixed(2), margins.left + plotWidth + 8, y + 4);
  }

  if (q10.length === q50.length && q90.length === q50.length) {
    ctx.beginPath();
    q90.forEach((value, index) => {
      const x = xForIndex(index);
      const y = yForPrice(value);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    [...q10].reverse().forEach((value, reverseIndex) => {
      const index = q10.length - 1 - reverseIndex;
      ctx.lineTo(xForIndex(index), yForPrice(value));
    });
    ctx.closePath();
    ctx.fillStyle = "rgba(18, 116, 106, 0.14)";
    ctx.fill();
  }

  if (q10.length === q50.length) drawForecastLine(ctx, q10, xForIndex, yForPrice, "rgba(18, 116, 106, 0.45)", 1.5);
  if (q90.length === q50.length) drawForecastLine(ctx, q90, xForIndex, yForPrice, "rgba(18, 116, 106, 0.45)", 1.5);
  drawForecastLine(ctx, q50, xForIndex, yForPrice, "#12746a", 3);
  drawForecastReference(ctx, reference, "Reference", xForIndex, yForPrice, q50.length, "#20231f");
  drawForecastReference(ctx, current, "Prix", xForIndex, yForPrice, q50.length, "#6b7280");
  drawForecastReference(ctx, entry, "Entry", xForIndex, yForPrice, q50.length, "#3f4a8a");
  drawForecastReference(ctx, support, "Support", xForIndex, yForPrice, q50.length, "#a16207");
  drawForecastReference(ctx, stop, "Stop", xForIndex, yForPrice, q50.length, "#b42318");
  drawForecastEndpoint(ctx, q10[q10.length - 1], xForIndex(q50.length - 1), yForPrice, "q10", "rgba(18, 116, 106, 0.68)");
  drawForecastEndpoint(ctx, q50[q50.length - 1], xForIndex(q50.length - 1), yForPrice, "q50", "#12746a");
  drawForecastEndpoint(ctx, q90[q90.length - 1], xForIndex(q50.length - 1), yForPrice, "q90", "rgba(18, 116, 106, 0.68)");
  drawForecastLegend(ctx, margins.left, margins.top - 10, forecast);
  ctx.fillStyle = "#20231f";
  ctx.font = "800 12px sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(
    `${forecast.forecast_status || "OK"} - ${signedPercent(forecast.forecast_expected_return_pct)} - Dir ${forecast.direction || "FLAT"} via q50`,
    margins.left,
    height - 10,
  );
}

function drawForecastLine(ctx, values, xForIndex, yForPrice, color, width) {
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = xForIndex(index);
    const y = yForPrice(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function drawForecastReference(ctx, value, label, xForIndex, yForPrice, count, color) {
  if (value === null || value === undefined || !Number.isFinite(value)) return;
  const y = yForPrice(value);
  ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.moveTo(xForIndex(0), y);
  ctx.lineTo(xForIndex(Math.max(count - 1, 0)), y);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = color;
  ctx.font = "800 11px sans-serif";
  ctx.fillText(label, xForIndex(Math.max(count - 1, 0)) + 8, y + 4);
}

function drawForecastEndpoint(ctx, value, x, yForPrice, label, color) {
  if (value === null || value === undefined || !Number.isFinite(value)) return;
  const y = yForPrice(value);
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.textAlign = "left";
  ctx.font = "700 11px sans-serif";
  ctx.fillText(`${label} ${value.toFixed(2)}`, x + 8, y + 4);
}

function drawForecastLegend(ctx, x, y, forecast) {
  const parts = [
    "Median line = q50",
    `Ref ${maybeMoney(forecast.reference_price)}`,
    `Median end ${maybeMoney(forecast.median_end_price)}`,
    `Return ${signedPercent(forecast.median_return_pct)}`,
    "Direction based on q50, not q90",
  ];
  ctx.fillStyle = "#344054";
  ctx.textAlign = "left";
  ctx.font = "700 10px sans-serif";
  ctx.fillText(parts.join(" | "), x, Math.max(12, y));
}

function forecastTone(status) {
  if (status === "BULLISH" || status === "NEUTRAL_BULLISH") return "good";
  if (status === "NEUTRAL") return "neutral";
  if (status === "WEAK" || status === "BEARISH") return "bad";
  return "muted";
}

function historicalQuotesFromPayload(payload) {
  const data = payload && typeof payload === "object" ? payload : {};
  const bars = Array.isArray(data.historical_bars) ? data.historical_bars : [];
  const event = {
    timestamp: data.bar_date || data.timestamp || "",
    data,
  };
  return addVolumeRatios(
    bars
      .map((bar) => quoteFromHistoricalBar(event, bar, data))
      .filter(Boolean)
      .sort(compareQuotesByTime),
  );
}

function setupChartTimeframeLabel(value) {
  const normalized = normalizeSetupChartTimeframe(value);
  const item = SETUP_CHART_TIMEFRAMES.find((option) => option.id === normalized);
  return item ? item.label : "1D";
}

function renderSetupChartTimeframeControls() {
  const container = document.getElementById("setup-chart-timeframes");
  if (!container) return;
  const active = normalizeSetupChartTimeframe(setupChartTimeframe);
  container.innerHTML = SETUP_CHART_TIMEFRAMES.map((item) => `
    <button
      class="chart-timeframe-button ${item.id === active ? "active" : ""}"
      type="button"
      data-chart-timeframe="${escapeHtml(item.id)}"
      aria-pressed="${item.id === active ? "true" : "false"}"
    >${escapeHtml(item.label)}</button>
  `).join("");
  updateSetupChartTimeframeStatus(active);
}

function updateSetupChartTimeframeStatus(timeframe = setupChartTimeframe, quotes = null) {
  const element = document.getElementById("setup-chart-timeframe-status");
  if (!element) return;
  const normalized = normalizeSetupChartTimeframe(timeframe);
  const latest = Array.isArray(quotes) && quotes.length ? quotes[quotes.length - 1] : {};
  const barSize = latest.historical_bar_size || setupChartDataMeta.historical_bar_size || "";
  const count = Array.isArray(quotes) ? quotes.length : null;
  const detail = [];
  if (barSize) detail.push(barSize);
  if (count !== null) detail.push(`${count} bougies`);
  if (setupChartDataMessage && (!Array.isArray(quotes) || !quotes.length)) {
    detail.push(setupChartDataMessage);
  }
  element.textContent = `Actif: ${setupChartTimeframeLabel(normalized)}`
    + (detail.length ? ` - ${detail.join(" - ")}` : "");
}

async function refreshSetupChartOnly() {
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

function wireSetupChartTimeframeControls() {
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

function setupDetailSummaryValues(setup) {
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

function renderSetupDetailSummary(setup) {
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

function setupEntryPlanValues(setup, latestQuote, entryDecision = null) {
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

function renderSetupEntryPlan(setup, latestQuote, entryDecision = null) {
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

function setupEntryConditions(setup, latestQuote, entryDecision = null) {
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

function renderSetupConditionGrid(setup, latestQuote, entryDecision = null) {
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

function renderSetupMarketSummary(setup, symbolEvents, latestQuote, timeframe = setupChartTimeframe) {
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

const SETUP_ANALYSIS_OVERVIEW_LABELS = {
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

function setupAnalysisPanelValues(setup, symbolEvents, latestQuote) {
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

function renderSetupAnalysisPanel(setup, symbolEvents, latestQuote) {
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

async function fetchSetupIntelligence(setupId, options = {}) {
  const limitValue = numberOrNull(options.limit);
  const offsetValue = numberOrNull(options.offset);
  const limit = limitValue === null ? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE : limitValue;
  const offset = offsetValue === null ? 0 : offsetValue;
  const latestPath = `/api/intelligence/setups/${encodeURIComponent(setupId)}/latest`;
  const params = new URLSearchParams({
    summary: "true",
    limit: String(limit),
    offset: String(offset),
  });
  const listPath = `/api/intelligence/setups/${encodeURIComponent(setupId)}/analyses?${params.toString()}`;
  const [latest, history] = await Promise.all([
    optionalApi(latestPath),
    api(listPath).catch(() => ({
      items: [],
      limit,
      offset,
      has_more: false,
      total_count: 0,
    })),
  ]);
  const analyses = ((history && history.items) || []).map((analysis) => (
    latest && analysis.analysis_id === latest.analysis_id ? latest : analysis
  ));
  return {
    latest,
    analyses,
    history: {
      items: analyses,
      limit: numberOrNull(history && history.limit) ?? limit,
      offset: numberOrNull(history && history.offset) ?? offset,
      has_more: Boolean(history && history.has_more),
      total_count: numberOrNull(history && history.total_count),
    },
  };
}

function emptySetupIntelligencePage() {
  return {
    latest: null,
    analyses: [],
    history: {
      items: [],
      limit: SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE,
      offset: 0,
      has_more: false,
      total_count: 0,
    },
  };
}

async function renderSetupIntelligence(setupId) {
  const previousState = currentSetupIntelligence && currentSetupIntelligence.setup_id === setupId
    ? currentSetupIntelligence
    : null;
  const page = await fetchSetupIntelligence(setupId, {
    limit: SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE,
    offset: 0,
  });
  setCurrentSetupIntelligence(buildSetupIntelligenceState(previousState, setupId, page));
  setCurrentSetupIntelligenceComparison(null);
  setCurrentSetupIntelligenceSelectedId(null);
  setCurrentSetupIntelligenceSelectedId(selectedIntelligenceAnalysis(currentSetupIntelligence)?.analysis_id || null);
  renderSetupIntelligencePanel(currentSetupIntelligence);
  syncCurrentSetupDetailIntelligence();
  renderSetupDetailJsonOutput();
  return currentSetupIntelligence;
}

function setupIntelligenceHistoryItems(data) {
  if (data && data.history && Array.isArray(data.history.items)) {
    return data.history.items;
  }
  return Array.isArray(data && data.analyses) ? data.analyses : [];
}

function upsertIntelligenceAnalyses(analyses, nextAnalyses) {
  let updated = Array.isArray(analyses) ? analyses : [];
  if (!Array.isArray(nextAnalyses)) return updated;
  for (let index = nextAnalyses.length - 1; index >= 0; index -= 1) {
    updated = upsertIntelligenceAnalysis(updated, nextAnalyses[index]);
  }
  return updated;
}

function buildSetupIntelligenceState(previousState, setupId, page) {
  const sameSetup = previousState && previousState.setup_id === setupId;
  const previousCache = sameSetup && Array.isArray(previousState.analyses)
    ? previousState.analyses
    : [];
  const pageAnalyses = Array.isArray(page && page.analyses) ? page.analyses : [];
  let analyses = upsertIntelligenceAnalyses(previousCache, pageAnalyses);
  if (page && page.latest) {
    analyses = upsertIntelligenceAnalysis(analyses, page.latest);
  }
  return {
    ...(sameSetup ? previousState : {}),
    setup_id: setupId,
    latest: page ? page.latest : null,
    analyses,
    history: {
      ...(page && page.history ? page.history : {}),
      items: pageAnalyses,
    },
  };
}

async function loadSetupIntelligenceHistoryPage(setupId, offset) {
  if (!setupId) return currentSetupIntelligence;
  const previousState = currentSetupIntelligence && currentSetupIntelligence.setup_id === setupId
    ? currentSetupIntelligence
    : null;
  const currentLimit = numberOrNull(previousState && previousState.history && previousState.history.limit);
  const limit = currentLimit === null ? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE : currentLimit;
  const requestedOffset = numberOrNull(offset);
  const page = await fetchSetupIntelligence(setupId, {
    limit,
    offset: requestedOffset === null ? 0 : Math.max(0, requestedOffset),
  });
  const previousSelectedId = currentSetupIntelligenceSelectedId;
  setCurrentSetupIntelligence(buildSetupIntelligenceState(previousState, setupId, page));
  const selected = selectedIntelligenceAnalysis(currentSetupIntelligence);
  setCurrentSetupIntelligenceSelectedId(selected ? selected.analysis_id : null);
  if (previousSelectedId !== currentSetupIntelligenceSelectedId) {
    setCurrentSetupIntelligenceComparison(null);
  }
  renderSetupIntelligencePanel(currentSetupIntelligence);
  syncCurrentSetupDetailIntelligence();
  renderSetupDetailJsonOutput();
  return currentSetupIntelligence;
}

async function ensureSetupIntelligenceAnalysisLoaded(analysisId) {
  if (!currentSetupIntelligence || !analysisId) return null;
  if (
    currentSetupIntelligence.latest
    && currentSetupIntelligence.latest.analysis_id === analysisId
  ) {
    return currentSetupIntelligence.latest;
  }
  const analyses = Array.isArray(currentSetupIntelligence.analyses)
    ? currentSetupIntelligence.analyses
    : [];
  const existing = analyses.find((analysis) => analysis.analysis_id === analysisId);
  if (existing && existing.detail_loaded !== false && Array.isArray(existing.scenarios)) {
    return existing;
  }
  const full = await api(`/api/intelligence/analyses/${encodeURIComponent(analysisId)}`);
  currentSetupIntelligence.analyses = upsertIntelligenceAnalysis(analyses, full);
  if (currentSetupIntelligence.history && Array.isArray(currentSetupIntelligence.history.items)) {
    currentSetupIntelligence.history.items = upsertIntelligenceAnalysis(
      currentSetupIntelligence.history.items,
      full,
    );
  }
  return full;
}

function upsertIntelligenceAnalysis(analyses, analysis) {
  if (!analysis || !analysis.analysis_id) return analyses;
  let replaced = false;
  const updated = analyses.map((item) => {
    if (item.analysis_id !== analysis.analysis_id) return item;
    replaced = true;
    return analysis;
  });
  if (!replaced) updated.unshift(analysis);
  return updated;
}

function renderSetupIntelligencePanel(data) {
  const overview = document.getElementById("setup-intelligence-overview");
  const compare = document.getElementById("setup-intelligence-compare");
  const scenarios = document.getElementById("setup-intelligence-scenarios");
  const ambiguities = document.getElementById("setup-intelligence-ambiguities");
  const fields = document.getElementById("setup-intelligence-fields");
  const history = document.getElementById("setup-intelligence-history");
  if (!overview && !compare && !scenarios && !ambiguities && !fields && !history) return;

  const latest = data && data.latest ? data.latest : null;
  const analysisHistory = setupIntelligenceHistoryItems(data);
  const historyMeta = data && data.history ? data.history : null;

  if (!latest) {
    const empty = `<div class="intelligence-empty">Aucune analyse intelligence enregistree pour ce setup.</div>`;
    if (overview) overview.innerHTML = empty;
    if (compare) compare.innerHTML = "";
    if (scenarios) scenarios.innerHTML = "";
    if (ambiguities) ambiguities.innerHTML = "";
    if (fields) fields.innerHTML = "";
    if (history) history.innerHTML = "";
    return;
  }

  const selectedAnalysis = selectedIntelligenceAnalysis(data);
  const selectedConfidence = selectedAnalysis.confidence || {};
  const selectedScenarios = Array.isArray(selectedAnalysis.scenarios) ? selectedAnalysis.scenarios : [];
  const selectedAmbiguities = Array.isArray(selectedAnalysis.ambiguities) ? selectedAnalysis.ambiguities : [];
  const selectedFields = Array.isArray(selectedAnalysis.extracted_fields) ? selectedAnalysis.extracted_fields : [];
  const openAmbiguities = selectedAmbiguities.filter((item) => item.status === "OPEN");
  const errorCount = intelligenceIssueCount(selectedAnalysis.issues, "ERROR");
  const warningCount = intelligenceIssueCount(selectedAnalysis.issues, "WARNING");
  const isLatestView = selectedAnalysis.analysis_id === latest.analysis_id;

  if (overview) {
    overview.innerHTML = [
      intelligenceCell("Confiance", renderConfidencePill(selectedConfidence), true),
      intelligenceCell("Affichage", escapeHtml(isLatestView ? "Latest" : shortId(selectedAnalysis.analysis_id)), true),
      intelligenceCell("Derniere analyse", escapeHtml(formatTime(selectedAnalysis.created_at) || "-"), true),
      intelligenceCell("Scenarios", escapeHtml(String(selectedScenarios.length)), true),
      intelligenceCell("Ambiguites ouvertes", escapeHtml(String(openAmbiguities.length)), true),
      intelligenceCell("Save validation", escapeHtml(validationStateText(selectedAnalysis.save_validation)), true),
      intelligenceCell("Arm validation", escapeHtml(validationStateText(selectedAnalysis.arm_validation)), true),
      intelligenceCell("Parser", escapeHtml(selectedAnalysis.parser_version || "-"), true),
      intelligenceCell("Schema", escapeHtml(selectedAnalysis.schema_version || "-"), true),
      intelligenceCell("Provider", escapeHtml(selectedAnalysis.provider_name || "-"), true),
      intelligenceCell("Issues", escapeHtml(`${errorCount} erreur(s), ${warningCount} warning(s)`), true),
      intelligenceCell("Resume", escapeHtml(selectedConfidence.summary || "-"), true),
      intelligenceCell("Analyse ID", `<code>${escapeHtml(shortId(selectedAnalysis.analysis_id))}</code>`, true),
    ].join("");
  }

  renderSetupIntelligenceComparison(compare, currentSetupIntelligenceComparison);

  if (scenarios) {
    scenarios.innerHTML = selectedScenarios.map((scenario) => {
      const confidence = scenario.confidence || {};
      const config = scenario.canonical_config || {};
      return `
        <article class="intelligence-scenario">
          <div class="intelligence-scenario-head">
            <div>
              <h3>${escapeHtml(scenario.scenario_name || scenario.scenario_id)}</h3>
              <span class="intelligence-scenario-meta">${escapeHtml(scenario.scenario_role || "-")} · ${escapeHtml(scenario.setup_type || "-")}</span>
            </div>
            ${renderConfidencePill(confidence)}
          </div>
          <div class="intelligence-scenario-grid">
            <div>
              <span class="intelligence-scenario-meta">Statut</span>
              <strong>${escapeHtml(scenario.status || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Setup ID</span>
              <strong>${escapeHtml(config.setup_id || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Save</span>
              <strong>${escapeHtml(validationStateText(scenario.save_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Arm</span>
              <strong>${escapeHtml(validationStateText(scenario.arm_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Score</span>
              <strong>${escapeHtml(numberText(confidence.score, 3))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Resume</span>
              <strong>${escapeHtml(confidence.summary || "-")}</strong>
            </div>
          </div>
        </article>
      `;
    }).join("") || `<div class="intelligence-empty">Aucun scenario extrait.</div>`;
  }

  if (ambiguities) {
    ambiguities.innerHTML = selectedAmbiguities.map((ambiguity) => {
      const metadata = ambiguity.metadata || {};
      const evidence = metadata.evidence || {};
      const severity = String(ambiguity.severity || metadata.severity || "REVIEW").toUpperCase();
      const kind = String(ambiguity.kind || metadata.kind || "USER_PROVIDED").toUpperCase();
      const impact = numberOrNull(ambiguity.confidence_impact ?? metadata.confidence_impact);
      const action = ambiguity.suggested_action || metadata.suggested_action || "-";
      const sourceLine = evidence.source_line ? `L${evidence.source_line}` : "-";
      return `
      <article class="intelligence-ambiguity">
        <div class="intelligence-ambiguity-head">
          <div>
            <h3>${escapeHtml(ambiguity.message || "Ambiguite")}</h3>
            <span class="intelligence-ambiguity-meta">${escapeHtml(ambiguity.field_path || "-")} · ${escapeHtml(ambiguity.status || "-")}</span>
          </div>
          ${ambiguity.status === "RESOLVED" ? `<span class="confidence-pill high">RESOLVED</span>` : `<span class="confidence-pill ${escapeHtml(ambiguityTone(ambiguity.status, severity))}">${escapeHtml(severity)}</span>`}
        </div>
        <div class="intelligence-ambiguity-grid">
          <div>
            <span class="intelligence-ambiguity-meta">Type</span>
            <strong>${escapeHtml(kind)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Impact confiance</span>
            <strong>${escapeHtml(impact === null ? "-" : `-${Math.round(impact * 100)}%`)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Source</span>
            <strong>${escapeHtml(sourceLine)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Action</span>
            <strong>${escapeHtml(action)}</strong>
          </div>
        </div>
        ${ambiguity.status === "OPEN" && Array.isArray(ambiguity.options) && ambiguity.options.length ? `
          <div class="intelligence-ambiguity-options">
            ${ambiguity.options.map((option, index) => `
              <button
                type="button"
                data-action="resolve-intelligence-ambiguity"
                data-analysis="${escapeHtml(selectedAnalysis.analysis_id || "")}"
                data-ambiguity="${escapeHtml(ambiguity.ambiguity_id || "")}"
                data-resolution="${escapeHtml(encodeURIComponent(JSON.stringify({ selected_option: option })))}"
              >${escapeHtml(intelligenceOptionLabel(option, index))}</button>
            `).join("")}
          </div>
        ` : ""}
      </article>
    `;
    }).join("") || `<div class="intelligence-empty">Aucune ambiguite ouverte pour cette analyse.</div>`;
  }

  if (fields) {
    const sortedFields = [...selectedFields].sort((left, right) => {
      const leftRank = fieldValidationRank(left.validation_status);
      const rightRank = fieldValidationRank(right.validation_status);
      if (leftRank !== rightRank) return leftRank - rightRank;
      return String(left.canonical_path || "").localeCompare(String(right.canonical_path || ""));
    }).slice(0, 12);
    fields.innerHTML = sortedFields.map((field) => `
      <article class="intelligence-field">
        <span class="intelligence-field-meta">${escapeHtml(field.validation_status || "-")} · ${escapeHtml(field.extraction_method || "-")}</span>
        <strong>${escapeHtml(field.canonical_path || "-")}</strong>
        <code>${escapeHtml(formatFieldValue(field.parsed_value))}</code>
        <div class="intelligence-field-grid">
          <div>
            <span class="intelligence-field-meta">Raw key</span>
            <strong>${escapeHtml(field.raw_key || "-")}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Confiance</span>
            <strong>${escapeHtml(numberText(field.confidence, 3))}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Source</span>
            <strong>${escapeHtml(lineRangeLabel(field.source_line_start, field.source_line_end))}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Texte</span>
            <strong>${escapeHtml(field.source_text || "-")}</strong>
          </div>
        </div>
      </article>
    `).join("") || `<div class="intelligence-empty">Aucune provenance exploitable.</div>`;
  }

  if (history) {
    const historyLimit = numberOrNull(historyMeta && historyMeta.limit) ?? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE;
    const historyOffset = numberOrNull(historyMeta && historyMeta.offset) ?? 0;
    const historyTotal = numberOrNull(historyMeta && historyMeta.total_count);
    const historyHasMore = Boolean(historyMeta && historyMeta.has_more);
    const historyStart = analysisHistory.length ? historyOffset + 1 : 0;
    const historyEnd = analysisHistory.length ? historyOffset + analysisHistory.length : 0;
    const historyRange = analysisHistory.length
      ? `Revisions ${historyStart}-${historyEnd}${historyTotal !== null ? ` / ${historyTotal}` : ""}`
      : "Aucun historique d'analyse pour ce setup.";
    history.innerHTML = analysisHistory.length ? `
      <div class="intelligence-history-toolbar">
        <span class="intelligence-history-range">${escapeHtml(historyRange)}</span>
        <div class="intelligence-history-pagination">
          <button
            type="button"
            class="secondary-button"
            data-action="intelligence-history-page"
            data-offset="${escapeHtml(String(Math.max(0, historyOffset - historyLimit)))}"
            ${historyOffset <= 0 ? "disabled" : ""}
          >Plus recents</button>
          <button
            type="button"
            class="secondary-button"
            data-action="intelligence-history-page"
            data-offset="${escapeHtml(String(historyOffset + historyLimit))}"
            ${historyHasMore ? "" : "disabled"}
          >Plus anciens</button>
        </div>
      </div>
      ${analysisHistory.map((analysis) => `
        <article class="intelligence-history-item ${analysis.analysis_id === selectedAnalysis.analysis_id ? "active" : ""}">
          <div class="intelligence-history-head">
            <div>
              <h3>${escapeHtml(shortId(analysis.analysis_id))}</h3>
              <span class="intelligence-history-meta">${escapeHtml(formatTime(analysis.created_at) || "-")}</span>
            </div>
            ${renderConfidencePill(analysis.confidence || {})}
          </div>
          <div class="intelligence-history-grid">
            <div>
              <span class="intelligence-history-meta">Scenarios</span>
              <strong>${escapeHtml(String(analysisScenarioCount(analysis)))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Ambiguites ouvertes</span>
              <strong>${escapeHtml(String(analysisOpenAmbiguityCount(analysis)))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Save</span>
              <strong>${escapeHtml(validationStateText(analysis.save_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Arm</span>
              <strong>${escapeHtml(validationStateText(analysis.arm_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Parser</span>
              <strong>${escapeHtml(analysis.parser_version || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Schema</span>
              <strong>${escapeHtml(analysis.schema_version || "-")}</strong>
            </div>
          </div>
          <div class="intelligence-history-actions">
            <button
              type="button"
              data-action="view-intelligence-analysis"
              data-analysis="${escapeHtml(analysis.analysis_id || "")}"
            >${analysis.analysis_id === selectedAnalysis.analysis_id ? "Analyse affichee" : "Afficher"}</button>
            ${analysis.analysis_id !== selectedAnalysis.analysis_id ? `
              <button
                type="button"
                class="secondary-button"
                data-action="compare-intelligence-analysis"
                data-analysis="${escapeHtml(analysis.analysis_id || "")}"
              >Comparer</button>
            ` : ""}
            ${analysis.analysis_id !== latest.analysis_id ? `
              <button
                type="button"
                class="secondary-button"
                data-action="rollback-intelligence-analysis"
                data-analysis="${escapeHtml(analysis.analysis_id || "")}"
              >Restaurer</button>
            ` : ""}
          </div>
        </article>
      `).join("")}
    ` : `<div class="intelligence-empty">${escapeHtml(historyRange)}</div>`;
  }
}

function selectedIntelligenceAnalysis(data) {
  const latest = data && data.latest ? data.latest : null;
  const analysisCache = data && Array.isArray(data.analyses) ? data.analyses : [];
  const analysisHistory = setupIntelligenceHistoryItems(data);
  if (!analysisHistory.length) {
    setCurrentSetupIntelligenceSelectedId(latest ? latest.analysis_id : null);
    return latest;
  }
  if (latest && currentSetupIntelligenceSelectedId === latest.analysis_id) {
    return latest;
  }
  const selected = analysisCache.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId)
    || analysisHistory.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId);
  if (selected) return selected;
  setCurrentSetupIntelligenceSelectedId(latest ? latest.analysis_id : analysisHistory[0].analysis_id);
  if (latest && currentSetupIntelligenceSelectedId === latest.analysis_id) {
    return latest;
  }
  return analysisHistory.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId) || latest || analysisHistory[0];
}

function syncCurrentSetupDetailIntelligence() {
  if (!currentSetupDetailInfo) return;
  currentSetupDetailInfo.intelligence = currentSetupIntelligence
    ? {
      ...currentSetupIntelligence,
      selected_analysis_id: currentSetupIntelligenceSelectedId,
      comparison: currentSetupIntelligenceComparison,
    }
    : null;
}

function intelligenceCell(label, value, allowHtml = false) {
  return `
    <div class="intelligence-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${allowHtml ? value : escapeHtml(value)}</strong>
    </div>
  `;
}

function renderSetupIntelligenceComparison(container, comparison) {
  if (!container) return;
  if (!comparison) {
    container.innerHTML = "";
    return;
  }
  const summary = comparison.summary || {};
  const fieldChanges = Array.isArray(comparison.field_changes) ? comparison.field_changes : [];
  container.innerHTML = `
    <article class="intelligence-compare-card">
      <div class="intelligence-compare-head">
        <div>
          <h3>Comparaison de revisions</h3>
          <span class="intelligence-history-meta">${escapeHtml(shortId(comparison.left?.analysis_id || ""))} -> ${escapeHtml(shortId(comparison.right?.analysis_id || ""))}</span>
        </div>
        <div class="intelligence-history-actions">
          <button type="button" class="secondary-button" data-action="clear-intelligence-comparison">Masquer</button>
        </div>
      </div>
      <div class="intelligence-overview">
        ${[
          intelligenceCell("Champs modifies", String(summary.field_change_count || 0)),
          intelligenceCell("Valeurs changees", String(summary.changed_count || 0)),
          intelligenceCell("Ajouts", String(summary.added_count || 0)),
          intelligenceCell("Suppressions", String(summary.removed_count || 0)),
          intelligenceCell("Delta confiance", formatComparisonDelta(summary.confidence_delta), true),
          intelligenceCell("Delta erreurs", formatComparisonDelta(summary.error_delta), true),
          intelligenceCell("Delta warnings", formatComparisonDelta(summary.warning_delta), true),
          intelligenceCell("Delta ambiguites", formatComparisonDelta(summary.open_ambiguity_delta), true),
        ].join("")}
      </div>
      <div class="intelligence-compare-columns">
        <div class="intelligence-compare-side">
          <span class="intelligence-history-meta">Revision affichee</span>
          <h4>${escapeHtml(comparison.left?.scenario_name || "-")}</h4>
          <div>${renderConfidencePill(comparison.left?.confidence || {})}</div>
          <p><strong>${escapeHtml(comparison.left?.status || "-")}</strong></p>
        </div>
        <div class="intelligence-compare-side">
          <span class="intelligence-history-meta">Revision comparee</span>
          <h4>${escapeHtml(comparison.right?.scenario_name || "-")}</h4>
          <div>${renderConfidencePill(comparison.right?.confidence || {})}</div>
          <p><strong>${escapeHtml(comparison.right?.status || "-")}</strong></p>
        </div>
      </div>
      <div class="intelligence-compare-fields">
        ${fieldChanges.map((change) => `
          <article class="intelligence-compare-field">
            <div class="intelligence-compare-field-head">
              <code>${escapeHtml(change.field_path || "-")}</code>
              <span class="confidence-pill ${comparisonTone(change.change_type)}">${escapeHtml(change.change_type || "-")}</span>
            </div>
            <div class="intelligence-compare-field-grid">
              <div>
                <span class="intelligence-field-meta">Avant</span>
                <strong>${formatComparisonValue(change.left_value)}</strong>
              </div>
              <div>
                <span class="intelligence-field-meta">Apres</span>
                <strong>${formatComparisonValue(change.right_value)}</strong>
              </div>
            </div>
          </article>
        `).join("") || `<div class="intelligence-empty">Aucune difference detectee entre ces deux revisions.</div>`}
      </div>
    </article>
  `;
}

function formatComparisonValue(value) {
  if (value === null || typeof value === "undefined") {
    return `<span class="intelligence-compare-empty">absent</span>`;
  }
  if (typeof value === "string") {
    return `<code>${escapeHtml(value)}</code>`;
  }
  return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
}

function formatComparisonDelta(value) {
  const numeric = numberOrNull(value);
  if (numeric === null) return escapeHtml("-");
  const prefix = numeric > 0 ? "+" : "";
  return escapeHtml(`${prefix}${numeric}`);
}

function comparisonTone(changeType) {
  const normalized = String(changeType || "").toUpperCase();
  if (normalized === "ADDED") return "high";
  if (normalized === "REMOVED") return "review";
  return "medium";
}

function renderConfidencePill(confidence) {
  const score = numberOrNull(confidence && confidence.score);
  const label = String((confidence && confidence.label) || "REVIEW").toUpperCase();
  const tone = confidenceTone(label);
  const text = score === null
    ? label
    : `${label} · ${Math.round(score * 100)}%`;
  return `<span class="confidence-pill ${escapeHtml(tone)}">${escapeHtml(text)}</span>`;
}

function confidenceTone(label) {
  if (label === "HIGH") return "high";
  if (label === "MEDIUM") return "medium";
  if (label === "INVALID") return "invalid";
  return "review";
}

function ambiguityTone(status, severity) {
  if (status === "RESOLVED") return "high";
  if (severity === "BLOCKER") return "invalid";
  if (severity === "INFO") return "medium";
  return "review";
}

function validationStateText(validation) {
  if (!validation) return "-";
  return validation.allowed ? "ALLOWED" : "REVIEW";
}

function intelligenceIssueCount(issues, severity) {
  if (!Array.isArray(issues)) return 0;
  return issues.filter((item) => item && item.severity === severity).length;
}

function analysisScenarioCount(analysis) {
  const summaryCount = numberOrNull(analysis && analysis.scenario_count);
  if (summaryCount !== null) return summaryCount;
  return Array.isArray(analysis && analysis.scenarios) ? analysis.scenarios.length : 0;
}

function analysisOpenAmbiguityCount(analysis) {
  const summaryCount = numberOrNull(analysis && analysis.open_ambiguity_count);
  if (summaryCount !== null) return summaryCount;
  const ambiguities = Array.isArray(analysis && analysis.ambiguities) ? analysis.ambiguities : [];
  return ambiguities.filter((item) => item.status === "OPEN").length;
}

function intelligenceOptionLabel(option, index) {
  if (option && typeof option === "object") {
    return option.scenario_name
      || option.label
      || option.scenario_id
      || `Option ${index + 1}`;
  }
  return `Option ${index + 1}`;
}

function shortId(value) {
  const text = String(value || "");
  if (text.length <= 22) return text || "-";
  return `${text.slice(0, 10)}...${text.slice(-8)}`;
}

function formatFieldValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function lineRangeLabel(start, end) {
  if (start == null && end == null) return "-";
  if (start == null) return `L${end}`;
  if (end == null || start === end) return `L${start}`;
  return `L${start}-${end}`;
}

function fieldValidationRank(status) {
  if (status === "INVALID") return 0;
  if (status === "REVIEW") return 1;
  return 2;
}

function buildSetupDetailInfo(setup, symbolEvents, latestQuote, chartQuotes, setupEvents, intelligence) {
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

function renderSetupDetailJsonOutput(forceShow = false) {
  const output = document.getElementById("setup-detail-json-output");
  if (!output || !currentSetupDetailInfo) return;
  const visible = forceShow || !output.hidden;
  output.hidden = !visible;
  if (visible) {
    output.textContent = JSON.stringify(currentSetupDetailInfo, null, 2);
  }
}

function wireSetupDetailJsonButton() {
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

function showSetupIntelligenceMessage(text, kind = "") {
  const message = document.getElementById("setup-intelligence-message");
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || "";
  message.classList.remove("error", "success");
  if (kind) message.classList.add(kind);
}

function renderSetupChartLegend(setup, quotes = [], timeframe = setupChartTimeframe) {
  const legend = document.getElementById("setup-chart-legend");
  if (!legend) return;
  const colors = setupChartColors();
  const levels = setupTradeLevels(setup);
  const usesSnapshots = quotes.some((quote) => quote.synthetic);
  const items = [
    [`TF ${setupChartTimeframeLabel(timeframe)}`, colors.textSoft],
    [usesSnapshots ? "Snapshot +" : "Bougie +", colors.candleUp],
    [usesSnapshots ? "Snapshot -" : "Bougie -", colors.candleDown],
    levels.resistance !== null ? [`Resistance ${maybeMoney(levels.resistance)}`, colors.resistance] : null,
    levels.trigger !== null ? [`Trigger ${maybeMoney(levels.trigger)}`, colors.trigger] : null,
    levels.limit !== null ? [`Limite ${maybeMoney(levels.limit)}`, colors.limit] : null,
    levels.stop !== null ? [`Stop ${maybeMoney(levels.stop)}`, colors.stop] : null,
    [`Vol ratio ${numberText(levels.volumeMin, 2)}`, colors.volumeThreshold],
  ].filter(Boolean);
  legend.innerHTML = items.map(([label, color]) => `
    <span class="legend-item">
      <span class="legend-swatch" style="background:${escapeHtml(color)}"></span>
      ${escapeHtml(label)}
    </span>
  `).join("");
}

function renderSetupChart(
  setup,
  symbolEvents,
  chartQuotes = null,
  timeframe = setupChartTimeframe,
) {
  const quotes = (Array.isArray(chartQuotes) ? chartQuotes : extractQuoteEvents(symbolEvents))
    .slice(-SETUP_CHART_MAX_SOURCE_CANDLES);
  const previousState = setupChartState;
  const sameSetup = previousState
    && previousState.setup
    && previousState.setup.setup_id === setup.setup_id
    && previousState.timeframe === timeframe;
  const wasAtLatest = !sameSetup || chartViewportAtLatest(previousState);
  const visibleCount = normalizeChartVisibleCount(
    sameSetup ? previousState.visibleCount : defaultChartVisibleCount(quotes.length),
    quotes.length,
  );
  const visibleStart = wasAtLatest
    ? normalizeChartVisibleStart(quotes.length - visibleCount, quotes.length, visibleCount)
    : normalizeChartVisibleStart(previousState.visibleStart, quotes.length, visibleCount);
  setupChartState = {
    setup,
    quotes,
    visibleCount,
    visibleStart,
    hover: null,
    dragging: false,
    layout: null,
    timeframe,
    emptyMessage: setupChartDataMessage
      ? `Aucune bougie ${setupChartTimeframeLabel(timeframe)}: ${setupChartDataMessage}`
      : `Aucune bougie ${setupChartTimeframeLabel(timeframe)} disponible`,
  };
  renderSetupChartLegend(setup, quotes, timeframe);
  wireSetupChartInteractions();
  updateSetupChartRangeLabel();
  drawSetupChart(setup, quotes);
}

function setupChartStatusText(quotes = []) {
  const timeframe = setupChartState ? setupChartState.timeframe : setupChartTimeframe;
  const latest = quotes.length ? quotes[quotes.length - 1] : {};
  const barSize = latest.historical_bar_size || setupChartDataMeta.historical_bar_size || "";
  const parts = [`TF ${setupChartTimeframeLabel(timeframe)}`];
  if (barSize) parts.push(barSize);
  parts.push(`${quotes.length} bougies`);
  return parts.join(" - ");
}

function drawSetupChartTimeframeLabel(ctx, quotes, colors) {
  ctx.save();
  ctx.fillStyle = colors.textSoft;
  ctx.font = "800 12px Inter, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(setupChartStatusText(quotes), 26, 18);
  ctx.restore();
}

function wireSetupChartInteractions() {
  if (setupChartInteractionsWired) return;
  const canvas = document.getElementById("setup-chart");
  if (!canvas) return;
  canvas.addEventListener("wheel", handleSetupChartWheel, { passive: false });
  canvas.addEventListener("pointerdown", handleSetupChartPointerDown);
  canvas.addEventListener("pointermove", handleSetupChartPointerMove);
  canvas.addEventListener("pointerup", handleSetupChartPointerUp);
  canvas.addEventListener("pointercancel", handleSetupChartPointerUp);
  canvas.addEventListener("pointerleave", handleSetupChartPointerLeave);
  onClick("setup-chart-zoom-in", () => zoomSetupChart(0.72, 0.82));
  onClick("setup-chart-zoom-out", () => zoomSetupChart(1.32, 0.82));
  onClick("setup-chart-reset", resetSetupChartViewport);
  setupChartInteractionsWired = true;
}

function handleSetupChartWheel(event) {
  if (!setupChartState || !setupChartState.quotes.length) return;
  event.preventDefault();
  const rect = event.currentTarget.getBoundingClientRect();
  const centerRatio = chartPointerRatio(event.clientX, rect);
  zoomSetupChart(event.deltaY < 0 ? 0.82 : 1.22, centerRatio);
}

function handleSetupChartPointerDown(event) {
  if (!setupChartState || !setupChartState.layout || !setupChartState.quotes.length) return;
  if (!chartPointerInPlot(event.clientX, event.clientY, event.currentTarget)) return;
  setupChartState.dragging = true;
  setupChartState.dragStartX = event.clientX;
  setupChartState.dragStartVisibleStart = setupChartState.visibleStart;
  event.currentTarget.classList.add("dragging");
  if (event.currentTarget.setPointerCapture) event.currentTarget.setPointerCapture(event.pointerId);
  event.preventDefault();
}

function handleSetupChartPointerMove(event) {
  if (!setupChartState || !setupChartState.layout) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const layout = setupChartState.layout;
  setupChartState.hover = isPointInChartArea(x, y, layout) ? { x, y } : null;
  if (setupChartState.dragging) {
    const slotWidth = Math.max(layout.slotWidth, 1);
    const movedSlots = (event.clientX - setupChartState.dragStartX) / slotWidth;
    setupChartState.visibleStart = normalizeChartVisibleStart(
      Math.round(setupChartState.dragStartVisibleStart - movedSlots),
      setupChartState.quotes.length,
      setupChartState.visibleCount,
    );
  }
  drawSetupChart(setupChartState.setup, setupChartState.quotes);
}

function handleSetupChartPointerUp(event) {
  if (!setupChartState) return;
  setupChartState.dragging = false;
  event.currentTarget.classList.remove("dragging");
  if (event.currentTarget.releasePointerCapture) {
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Pointer capture may already be released by the browser.
    }
  }
}

function handleSetupChartPointerLeave(event) {
  if (!setupChartState) return;
  if (!setupChartState.dragging) {
    setupChartState.hover = null;
    drawSetupChart(setupChartState.setup, setupChartState.quotes);
  }
  event.currentTarget.classList.remove("dragging");
}

function zoomSetupChart(factor, centerRatio = 0.5) {
  if (!setupChartState || !setupChartState.quotes.length) return;
  const oldCount = setupChartState.visibleCount;
  const newCount = normalizeChartVisibleCount(Math.round(oldCount * factor), setupChartState.quotes.length);
  if (newCount === oldCount) return;
  const anchor = setupChartState.visibleStart + oldCount * Math.min(Math.max(centerRatio, 0), 1);
  setupChartState.visibleCount = newCount;
  setupChartState.visibleStart = normalizeChartVisibleStart(
    Math.round(anchor - newCount * centerRatio),
    setupChartState.quotes.length,
    newCount,
  );
  drawSetupChart(setupChartState.setup, setupChartState.quotes);
}

function resetSetupChartViewport() {
  if (!setupChartState) return;
  setupChartState.visibleCount = defaultChartVisibleCount(setupChartState.quotes.length);
  setupChartState.visibleStart = normalizeChartVisibleStart(
    setupChartState.quotes.length - setupChartState.visibleCount,
    setupChartState.quotes.length,
    setupChartState.visibleCount,
  );
  setupChartState.hover = null;
  drawSetupChart(setupChartState.setup, setupChartState.quotes);
}

function chartPointerRatio(clientX, rect) {
  const layout = setupChartState && setupChartState.layout;
  if (!layout) return 0.5;
  return Math.min(Math.max((clientX - rect.left - layout.margins.left) / layout.plotWidth, 0), 1);
}

function chartPointerInPlot(clientX, clientY, canvas) {
  const layout = setupChartState && setupChartState.layout;
  if (!layout) return false;
  const rect = canvas.getBoundingClientRect();
  return isPointInChartArea(clientX - rect.left, clientY - rect.top, layout);
}

function isPointInChartArea(x, y, layout) {
  return x >= layout.margins.left
    && x <= layout.plotRight
    && y >= layout.margins.top
    && y <= layout.volumeBottom;
}

function chartViewportAtLatest(state) {
  if (!state || !state.quotes || !state.quotes.length) return true;
  return state.visibleStart + state.visibleCount >= state.quotes.length;
}

function defaultChartVisibleCount(total) {
  if (!total) return 0;
  return normalizeChartVisibleCount(Math.min(total, SETUP_CHART_INITIAL_VISIBLE_CANDLES), total);
}

function normalizeChartVisibleCount(value, total) {
  if (!total) return 0;
  const minimum = Math.min(SETUP_CHART_MIN_VISIBLE_CANDLES, total);
  const count = Number.isFinite(Number(value)) ? Number(value) : defaultChartVisibleCount(total);
  return Math.min(Math.max(Math.round(count), minimum), total);
}

function normalizeChartVisibleStart(value, total, visibleCount) {
  if (!total || !visibleCount) return 0;
  const maxStart = Math.max(0, total - visibleCount);
  const start = Number.isFinite(Number(value)) ? Number(value) : maxStart;
  return Math.min(Math.max(Math.round(start), 0), maxStart);
}

function updateSetupChartRangeLabel() {
  const label = document.getElementById("setup-chart-range");
  if (!label || !setupChartState) return;
  const { quotes, visibleStart, visibleCount } = setupChartState;
  const total = quotes.length;
  if (!total) {
    label.textContent = "0 bougie";
  } else if (total < SETUP_CHART_MIN_VISIBLE_CANDLES) {
    label.textContent = `${total} / ${SETUP_CHART_MIN_VISIBLE_CANDLES} bougies dispo`;
  } else {
    const end = Math.min(total, visibleStart + visibleCount);
    label.textContent = `${visibleStart + 1}-${end} / ${total} bougies`;
  }

  const minVisible = Math.min(SETUP_CHART_MIN_VISIBLE_CANDLES, total);
  setButtonDisabled("setup-chart-zoom-in", total <= minVisible || visibleCount <= minVisible);
  setButtonDisabled("setup-chart-zoom-out", !total || visibleCount >= total);
  setButtonDisabled(
    "setup-chart-reset",
    !total
      || (visibleCount === defaultChartVisibleCount(total)
        && visibleStart === normalizeChartVisibleStart(total - visibleCount, total, visibleCount)),
  );
}

function setupChartColors() {
  return {
    axis: "#9aa4b2",
    bg: "#131722",
    candleDown: "#f23645",
    candleUp: "#00b386",
    current: "#f23645",
    grid: "#242a36",
    gridSoft: "#1d2330",
    limit: "#6c8cff",
    resistance: "#f59e0b",
    stop: "#ff4d5a",
    text: "#d1d4dc",
    textSoft: "#8a93a3",
    trigger: "#14b8a6",
    volumeDown: "rgba(242, 54, 69, 0.55)",
    volumeThreshold: "#f59e0b",
    volumeUp: "rgba(0, 179, 134, 0.55)",
  };
}

function drawSetupChart(setup, quotes) {
  const canvas = document.getElementById("setup-chart");
  const empty = document.getElementById("setup-chart-empty");
  if (!canvas) return;
  const parent = canvas.parentElement;
  const width = Math.max(320, Math.floor((parent && parent.clientWidth) || canvas.clientWidth || 760));
  const height = setupChartHeight();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  canvas.style.width = "100%";
  canvas.style.height = `${height}px`;

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const colors = setupChartColors();
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = colors.bg;
  ctx.fillRect(0, 0, width, height);
  drawSetupChartTimeframeLabel(ctx, quotes, colors);

  if (!quotes.length) {
    if (empty) {
      empty.hidden = false;
      empty.textContent = (setupChartState && setupChartState.emptyMessage)
        || "Aucune quote stock recente";
    }
    drawEmptyChartText(ctx, width, height, colors);
    return;
  }
  if (empty) empty.hidden = true;

  const margins = { top: 26, right: 108, bottom: 34, left: 26 };
  const volumeHeight = Math.max(86, Math.min(128, Math.round(height * 0.22)));
  const gap = 18;
  const priceBottom = height - margins.bottom - volumeHeight - gap;
  const volumeTop = priceBottom + gap;
  const volumeBottom = height - margins.bottom;
  const plotWidth = width - margins.left - margins.right;
  const plotRight = margins.left + plotWidth;
  const visibleCount = normalizeChartVisibleCount(
    setupChartState ? setupChartState.visibleCount : defaultChartVisibleCount(quotes.length),
    quotes.length,
  );
  const visibleStart = normalizeChartVisibleStart(
    setupChartState ? setupChartState.visibleStart : quotes.length - visibleCount,
    quotes.length,
    visibleCount,
  );
  if (setupChartState) {
    setupChartState.visibleCount = visibleCount;
    setupChartState.visibleStart = visibleStart;
  }
  const visibleQuotes = quotes.slice(visibleStart, visibleStart + visibleCount);
  const slotWidth = plotWidth / Math.max(visibleQuotes.length, 1);
  const xForIndex = (index) => margins.left + slotWidth * (index + 0.5);
  const layout = {
    height,
    margins,
    plotRight,
    plotWidth,
    priceBottom,
    slotWidth,
    visibleCount: visibleQuotes.length,
    visibleStart,
    volumeBottom,
    volumeHeight,
    volumeTop,
    width,
  };
  if (setupChartState) setupChartState.layout = layout;

  const levels = setupTradeLevels(setup);
  const latestQuote = quotes[quotes.length - 1];
  const latestPrice = quotePrice(latestQuote);
  const priceValues = [];
  visibleQuotes.forEach((quote) => {
    priceValues.push(quote.high, quote.low, quote.open, quote.close);
  });
  [levels.resistance, levels.trigger, levels.limit, levels.stop, latestPrice].forEach((value) => {
    if (value !== null) priceValues.push(value);
  });
  let minPrice = Math.min(...priceValues);
  let maxPrice = Math.max(...priceValues);
  if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) {
    minPrice = 0;
    maxPrice = 1;
  }
  if (minPrice === maxPrice) {
    minPrice -= 0.5;
    maxPrice += 0.5;
  }
  const padding = (maxPrice - minPrice) * 0.08;
  minPrice -= padding;
  maxPrice += padding;

  const yForPrice = (price) => (
    priceBottom - ((price - minPrice) / (maxPrice - minPrice)) * (priceBottom - margins.top)
  );

  drawPriceGrid(ctx, {
    colors,
    height,
    margins,
    maxPrice,
    minPrice,
    priceBottom,
    plotWidth,
    plotRight,
    visibleCount: visibleQuotes.length,
    volumeTop,
  });
  drawVolumeRatio(ctx, visibleQuotes, xForIndex, levels.volumeMin, {
    colors,
    height,
    margins,
    plotRight,
    plotWidth,
    slotWidth,
    volumeTop,
    volumeHeight,
  });
  ctx.save();
  ctx.beginPath();
  ctx.rect(margins.left, margins.top, plotWidth, priceBottom - margins.top);
  ctx.clip();
  drawCandles(ctx, visibleQuotes, xForIndex, yForPrice, { colors, slotWidth });
  ctx.restore();
  drawSetupPriceLevels(ctx, levels, latestPrice, yForPrice, {
    colors,
    margins,
    priceBottom,
    plotRight,
    plotWidth,
  });
  drawChartTimeAxis(ctx, visibleQuotes, {
    colors,
    height,
    margins,
    plotRight,
    plotWidth,
    slotWidth,
  });
  drawChartCrosshair(ctx, visibleQuotes, yForPrice, layout, colors);
  updateSetupChartRangeLabel();
}

function setupChartHeight() {
  return window.innerWidth < 720 ? 420 : 540;
}

function drawEmptyChartText(ctx, width, height, colors) {
  ctx.fillStyle = colors.textSoft;
  ctx.font = "700 13px Inter, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("Aucune donnee marche a tracer", width / 2, height / 2);
  ctx.textAlign = "left";
}

function drawPriceGrid(ctx, options) {
  const {
    colors,
    margins,
    maxPrice,
    minPrice,
    priceBottom,
    plotRight,
    plotWidth,
    visibleCount,
    volumeTop,
    height,
  } = options;
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = colors.axis;
  ctx.font = "11px Inter, sans-serif";
  ctx.textAlign = "left";
  for (let index = 0; index <= 6; index += 1) {
    const y = margins.top + ((priceBottom - margins.top) * index) / 6;
    const value = maxPrice - ((maxPrice - minPrice) * index) / 6;
    ctx.beginPath();
    ctx.moveTo(margins.left, y);
    ctx.lineTo(plotRight, y);
    ctx.stroke();
    ctx.fillText(value.toFixed(2), plotRight + 10, y + 4);
  }
  ctx.strokeStyle = colors.gridSoft;
  const verticalLines = Math.min(10, Math.max(4, visibleCount));
  for (let index = 0; index <= verticalLines; index += 1) {
    const x = margins.left + (plotWidth * index) / verticalLines;
    ctx.beginPath();
    ctx.moveTo(x, margins.top);
    ctx.lineTo(x, height - margins.bottom);
    ctx.stroke();
  }
  ctx.strokeStyle = colors.grid;
  ctx.beginPath();
  ctx.moveTo(margins.left, volumeTop);
  ctx.lineTo(plotRight, volumeTop);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(margins.left, height - margins.bottom);
  ctx.lineTo(plotRight, height - margins.bottom);
  ctx.stroke();
}

function drawCandles(ctx, quotes, xForIndex, yForPrice, options) {
  const { colors, slotWidth } = options;
  const candleWidth = Math.max(5, Math.min(18, slotWidth * 0.66));
  quotes.forEach((quote, index) => {
    const x = xForIndex(index);
    const isUp = quote.close >= quote.open;
    const color = isUp ? colors.candleUp : colors.candleDown;
    const highY = yForPrice(quote.high);
    const lowY = yForPrice(quote.low);
    const openY = yForPrice(quote.open);
    const closeY = yForPrice(quote.close);
    const top = Math.min(openY, closeY);
    const height = Math.max(Math.abs(openY - closeY), 2);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    ctx.fillRect(
      x - candleWidth / 2,
      top,
      candleWidth,
      height,
    );
  });
}

function drawSetupPriceLevels(ctx, levels, latestPrice, yForPrice, options) {
  const { colors, margins, priceBottom, plotRight, plotWidth } = options;
  const items = [
    { code: "RES", label: "Resistance", value: levels.resistance, color: colors.resistance, dash: [7, 5] },
    { code: "TRG", label: "Trigger", value: levels.trigger, color: colors.trigger, dash: [] },
    { code: "LMT", label: "Limite", value: levels.limit, color: colors.limit, dash: [3, 4] },
    { code: "STP", label: "Stop", value: levels.stop, color: colors.stop, dash: [] },
    { code: "PX", label: "Prix", value: latestPrice, color: colors.current, dash: [2, 4], subtle: true },
  ].filter((item) => item.value !== null);
  const labels = [];
  items.forEach((item) => {
    const y = yForPrice(item.value);
    if (y < margins.top || y > priceBottom) return;
    ctx.save();
    ctx.strokeStyle = item.color;
    ctx.globalAlpha = item.subtle ? 0.72 : 0.95;
    ctx.fillStyle = item.color;
    ctx.lineWidth = item.subtle ? 1 : 2;
    ctx.setLineDash(item.dash);
    ctx.beginPath();
    ctx.moveTo(margins.left, y);
    ctx.lineTo(plotRight, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    labels.push({
      ...item,
      y,
      text: `${item.code} ${maybeMoney(item.value)}`,
    });
  });
  drawLevelTags(ctx, labels, { colors, margins, priceBottom, plotRight });
}

function drawLevelTags(ctx, labels, options) {
  const { colors, margins, priceBottom, plotRight } = options;
  if (!labels.length) return;
  const tagHeight = 18;
  const tagGap = 4;
  const tagX = plotRight + 8;
  const tagWidth = 82;
  const topLimit = margins.top + tagHeight / 2;
  const bottomLimit = priceBottom - tagHeight / 2;
  const sorted = labels
    .slice()
    .sort((left, right) => left.y - right.y)
    .map((label) => ({ ...label, labelY: Math.min(Math.max(label.y, topLimit), bottomLimit) }));

  for (let index = 1; index < sorted.length; index += 1) {
    const previous = sorted[index - 1];
    if (sorted[index].labelY - previous.labelY < tagHeight + tagGap) {
      sorted[index].labelY = previous.labelY + tagHeight + tagGap;
    }
  }
  const overflow = sorted[sorted.length - 1].labelY - bottomLimit;
  if (overflow > 0) {
    sorted.forEach((label) => {
      label.labelY -= overflow;
    });
  }
  for (let index = sorted.length - 2; index >= 0; index -= 1) {
    const next = sorted[index + 1];
    if (next.labelY - sorted[index].labelY < tagHeight + tagGap) {
      sorted[index].labelY = next.labelY - tagHeight - tagGap;
    }
  }

  ctx.save();
  sorted.forEach((label) => {
    const tagY = label.labelY - tagHeight / 2;
    ctx.strokeStyle = label.color;
    ctx.fillStyle = label.color;
    ctx.globalAlpha = label.subtle ? 0.86 : 1;
    if (Math.abs(label.labelY - label.y) > 2) {
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotRight - 6, label.y);
      ctx.lineTo(tagX - 3, label.labelY);
      ctx.stroke();
    }
    roundRect(ctx, tagX, tagY, tagWidth, tagHeight, 4);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#ffffff";
    ctx.font = "800 10px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label.text, tagX + tagWidth / 2, label.labelY + 0.5);
  });
  ctx.textBaseline = "alphabetic";
  ctx.restore();
}

function drawVolumeRatio(ctx, quotes, xForIndex, volumeMin, options) {
  const { colors, height, margins, plotRight, slotWidth, volumeHeight, volumeTop } = options;
  const ratios = quotes.map((quote) => quoteVolumeRatio(quote)).filter((value) => value !== null);
  const maxRatio = Math.max(...ratios, volumeMin || 0, 1);
  const volumeBottom = height - margins.bottom;
  const yForVolume = (value) => (
    volumeBottom - (Math.max(value, 0) / maxRatio) * volumeHeight
  );
  const barWidth = Math.max(3, Math.min(14, slotWidth * 0.55));
  quotes.forEach((quote, index) => {
    const ratio = quoteVolumeRatio(quote);
    if (ratio === null) return;
    const x = xForIndex(index);
    const y = yForVolume(ratio);
    ctx.fillStyle = quote.close >= quote.open ? colors.volumeUp : colors.volumeDown;
    if (volumeMin !== null && ratio < volumeMin) ctx.globalAlpha = 0.48;
    ctx.fillRect(x - barWidth / 2, y, barWidth, volumeBottom - y);
    ctx.globalAlpha = 1;
  });
  if (volumeMin !== null) {
    const y = yForVolume(volumeMin);
    ctx.save();
    ctx.strokeStyle = colors.volumeThreshold;
    ctx.fillStyle = colors.volumeThreshold;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(margins.left, y);
    ctx.lineTo(plotRight, y);
    ctx.stroke();
    ctx.setLineDash([]);
    const label = `VOL ${numberText(volumeMin, 2)}`;
    ctx.font = "800 10px Inter, sans-serif";
    const labelWidth = Math.max(58, ctx.measureText(label).width + 12);
    const labelY = Math.min(Math.max(y, volumeTop + 12), volumeBottom - 10);
    roundRect(ctx, margins.left + 8, labelY - 9, labelWidth, 18, 4);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, margins.left + 8 + labelWidth / 2, labelY + 0.5);
    ctx.textBaseline = "alphabetic";
    ctx.restore();
  }
  ctx.fillStyle = colors.axis;
  ctx.font = "11px Inter, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("Vol ratio", margins.left + 8, volumeTop + 14);
  ctx.fillText(maxRatio.toFixed(2), plotRight + 10, volumeTop + 8);
  ctx.fillText("0", plotRight + 10, volumeBottom + 4);
}

function drawChartTimeAxis(ctx, quotes, options) {
  const { colors, height, margins, plotRight, slotWidth } = options;
  ctx.fillStyle = colors.axis;
  ctx.font = "11px Inter, sans-serif";
  const labelCount = Math.min(6, quotes.length);
  for (let index = 0; index < labelCount; index += 1) {
    const quoteIndex = labelCount === 1
      ? 0
      : Math.round((quotes.length - 1) * index / (labelCount - 1));
    const quote = quotes[quoteIndex];
    const x = margins.left + slotWidth * (quoteIndex + 0.5);
    ctx.textAlign = index === labelCount - 1 ? "right" : (index === 0 ? "left" : "center");
    ctx.fillText(formatChartTime(quote.timestamp), Math.min(Math.max(x, margins.left), plotRight), height - 8);
  }
  ctx.textAlign = "left";
}

function drawChartCrosshair(ctx, quotes, yForPrice, layout, colors) {
  const state = setupChartState;
  if (!state || !state.hover || !quotes.length) return;
  const { hover } = state;
  if (!isPointInChartArea(hover.x, hover.y, layout)) return;
  const quoteIndex = Math.min(
    Math.max(Math.floor((hover.x - layout.margins.left) / layout.slotWidth), 0),
    quotes.length - 1,
  );
  const quote = quotes[quoteIndex];
  const x = layout.margins.left + layout.slotWidth * (quoteIndex + 0.5);
  const price = quote.close;
  const y = yForPrice(price);

  ctx.save();
  ctx.strokeStyle = "rgba(154, 164, 178, 0.48)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x, layout.margins.top);
  ctx.lineTo(x, layout.volumeBottom);
  ctx.moveTo(layout.margins.left, y);
  ctx.lineTo(layout.plotRight, y);
  ctx.stroke();
  ctx.setLineDash([]);

  const priceText = maybeMoney(price);
  ctx.font = "800 10px Inter, sans-serif";
  const priceWidth = Math.max(54, ctx.measureText(priceText).width + 12);
  const priceY = Math.min(Math.max(y, layout.margins.top + 10), layout.priceBottom - 10);
  ctx.fillStyle = "#2a3141";
  roundRect(ctx, layout.plotRight + 8, priceY - 9, priceWidth, 18, 4);
  ctx.fill();
  ctx.fillStyle = colors.text;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(priceText, layout.plotRight + 8 + priceWidth / 2, priceY + 0.5);

  const timeText = formatChartTime(quote.timestamp);
  const timeWidth = Math.max(74, ctx.measureText(timeText).width + 12);
  const timeX = Math.min(
    Math.max(x - timeWidth / 2, layout.margins.left),
    layout.plotRight - timeWidth,
  );
  ctx.fillStyle = "#2a3141";
  roundRect(ctx, timeX, layout.height - layout.margins.bottom + 8, timeWidth, 18, 4);
  ctx.fill();
  ctx.fillStyle = colors.text;
  ctx.fillText(timeText, timeX + timeWidth / 2, layout.height - layout.margins.bottom + 17.5);
  ctx.textBaseline = "alphabetic";
  ctx.restore();
}

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function formatChartTime(value) {
  if (!value) return "-";
  if (/^\d{8}$/.test(String(value))) {
    const text = String(value);
    const date = new Date(`${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}T00:00:00`);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString(undefined, { month: "short", day: "2-digit" });
    }
  }
  const date = parseChartDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function renderSetupDetail() {
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

async function renderSetupCreationSnapshot(setupId) {
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

function parseSetupConfigEditor(editor) {
  if (!editor) return null;
  try {
    const parsed = JSON.parse(editor.value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("La configuration doit etre un objet JSON");
    }
    return parsed;
  } catch (error) {
    showSetupConfigMessage(error.message, "error");
    return null;
  }
}

function renderSetupConfigForm(config) {
  const form = document.getElementById("setup-config-form");
  if (!form) return;
  form.innerHTML = "";
  if (!config || Array.isArray(config) || typeof config !== "object") return;

  const rootFields = document.createElement("div");
  rootFields.className = "config-fields";
  orderedConfigEntries(config, CONFIG_ROOT_ORDER).forEach(([key, value]) => {
    if (isPlainObject(value) || Array.isArray(value)) return;
    rootFields.appendChild(createConfigField([key], value));
  });
  if (rootFields.children.length) form.appendChild(rootFields);

  orderedConfigEntries(config).forEach(([key, value]) => {
    if (!isPlainObject(value) && !Array.isArray(value)) return;
    form.appendChild(createConfigNode([key], value));
  });
}

function createConfigNode(path, value) {
  if (Array.isArray(value)) return createConfigList(path, value);
  const section = document.createElement("section");
  section.className = "config-section";
  const heading = document.createElement("h3");
  heading.textContent = formatConfigLabel(path[path.length - 1]);
  section.appendChild(heading);
  const fields = document.createElement("div");
  fields.className = "config-fields";
  orderedConfigEntries(value).forEach(([key, item]) => {
    const childPath = [...path, key];
    if (isPlainObject(item) || Array.isArray(item)) {
      section.appendChild(createConfigNode(childPath, item));
    } else {
      fields.appendChild(createConfigField(childPath, item));
    }
  });
  if (fields.children.length) section.appendChild(fields);
  return section;
}

function createConfigList(path, values) {
  const section = document.createElement("section");
  section.className = "config-section";
  const heading = document.createElement("h3");
  heading.textContent = formatConfigLabel(path[path.length - 1]);
  section.appendChild(heading);
  const list = document.createElement("div");
  list.className = "config-list";
  values.forEach((item, index) => {
    const itemPath = [...path, index];
    if (isPlainObject(item)) {
      const itemPanel = document.createElement("article");
      itemPanel.className = "config-list-item";
      const title = document.createElement("h4");
      title.textContent = `${formatConfigLabel(path[path.length - 1])} ${index + 1}`;
      itemPanel.appendChild(title);
      const fields = document.createElement("div");
      fields.className = "config-list-item-fields";
      orderedConfigEntries(item).forEach(([key, value]) => {
        const childPath = [...itemPath, key];
        if (isPlainObject(value) || Array.isArray(value)) {
          itemPanel.appendChild(createConfigNode(childPath, value));
        } else {
          fields.appendChild(createConfigField(childPath, value));
        }
      });
      if (fields.children.length) itemPanel.appendChild(fields);
      list.appendChild(itemPanel);
    } else if (Array.isArray(item)) {
      list.appendChild(createConfigList(itemPath, item));
    } else {
      list.appendChild(createConfigField(itemPath, item));
    }
  });
  section.appendChild(list);
  return section;
}

function createConfigField(path, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "config-field";
  if (typeof value === "boolean") wrapper.classList.add("boolean");

  const label = document.createElement("label");
  const labelText = document.createElement("span");
  labelText.className = "field-label";
  labelText.textContent = formatConfigLabel(path[path.length - 1]);

  if (typeof value === "boolean") {
    const input = createConfigInput(path, value);
    input.type = "checkbox";
    input.checked = value;
    label.appendChild(input);
    label.appendChild(labelText);
  } else {
    const options = configOptionsForPath(path);
    const input = options
      ? createConfigSelect(path, value, options)
      : createConfigInput(path, value);
    if (!options) {
      input.type = typeof value === "number" ? "number" : "text";
      if (typeof value === "number") input.step = "any";
      input.value = value ?? "";
    }
    label.appendChild(labelText);
    label.appendChild(input);
  }

  wrapper.appendChild(label);
  return wrapper;
}

function createConfigInput(path, value) {
  const input = document.createElement("input");
  input.dataset.configPath = JSON.stringify(path);
  input.dataset.configType = value === null ? "null" : typeof value;
  return input;
}

function createConfigSelect(path, value, options) {
  const select = document.createElement("select");
  select.dataset.configPath = JSON.stringify(path);
  select.dataset.configType = value === null ? "null" : typeof value;
  const values = options.includes(value) || value == null ? options : [value, ...options];
  values.forEach((optionValue) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue;
    option.selected = optionValue === value;
    select.appendChild(option);
  });
  return select;
}

function buildSetupConfigFromForm() {
  if (!currentSetupConfig) return null;
  const config = structuredCloneSafe(currentSetupConfig);
  const fields = document.querySelectorAll("[data-config-path]");
  fields.forEach((field) => {
    const path = JSON.parse(field.dataset.configPath || "[]");
    setDeepValue(config, path, parseConfigFieldValue(field));
  });
  return config;
}

function parseConfigFieldValue(field) {
  const type = field.dataset.configType;
  if (type === "boolean") return field.checked;
  if (type === "number") {
    if (field.value.trim() === "") return null;
    const value = Number(field.value);
    return Number.isFinite(value) ? value : null;
  }
  if (type === "null" && field.value === "") return null;
  return field.value;
}

function setDeepValue(target, path, value) {
  let cursor = target;
  for (let index = 0; index < path.length - 1; index += 1) {
    cursor = cursor[path[index]];
  }
  cursor[path[path.length - 1]] = value;
}

function formatConfigLabel(value) {
  return String(value).replaceAll("_", " ");
}

function configOptionsForPath(path) {
  const pathKey = path.join(".");
  const key = path[path.length - 1];
  return CONFIG_PATH_OPTIONS[pathKey] || CONFIG_FIELD_OPTIONS[key] || null;
}

function orderedConfigEntries(value, priority = []) {
  const entries = Object.entries(value);
  const priorities = priority.length ? priority : CONFIG_ROOT_ORDER;
  return entries.sort(([left], [right]) => {
    const leftIndex = priorities.indexOf(left);
    const rightIndex = priorities.indexOf(right);
    if (leftIndex !== -1 || rightIndex !== -1) {
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    }
    return left.localeCompare(right);
  });
}

function showSetupConfigMessage(text, kind = "") {
  const message = document.getElementById("setup-config-message");
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || "";
  message.classList.remove("error", "success");
  if (kind) message.classList.add(kind);
}

function syncSetupConfigActions() {
  const armButton = document.getElementById("setup-config-arm");
  const disarmButton = document.getElementById("setup-config-disarm");
  const dirty = setupConfigFormDirty || setupConfigEditorDirty;
  const disarmed = String((currentSetupDetailSetup || {}).status || "").toUpperCase() === "DISABLED";
  const armErrors = currentSetupArmStatus
    ? normalizeDetailMessages(currentSetupArmStatus.arm_validation && currentSetupArmStatus.arm_validation.errors)
    : [];
  const disarmErrors = currentSetupArmStatus
    ? normalizeDetailMessages(currentSetupArmStatus.disarm_validation && currentSetupArmStatus.disarm_validation.errors)
    : [];
  if (armButton) {
    armButton.disabled = dirty || (currentSetupArmStatus ? !currentSetupArmStatus.armable : false);
    armButton.title = dirty
      ? "Sauvegardez les modifications avant d'armer le setup"
      : (armErrors.length ? armErrors.join(" | ") : "Armer le setup sans sauvegarder la configuration");
  }
  if (disarmButton) {
    disarmButton.disabled = currentSetupArmStatus ? !currentSetupArmStatus.disarmable : disarmed;
    disarmButton.title = disarmErrors.length
      ? disarmErrors.join(" | ")
      : (disarmed
        ? "Le setup est deja desarme"
        : "Desarmer le setup sans modifier la configuration");
  }
}

async function renderLogsPage() {
  const container = document.getElementById("logs-events");
  const form = document.getElementById("logs-filter");
  const twsContainer = document.getElementById("logs-tws-events");

  async function loadLogs() {
    if (container) {
      const result = await api("/api/events?limit=200");
      renderEvents("logs-events", result.items || []);
    }
    if (twsContainer) {
      const tws = await api("/api/logs/tws?limit=200");
      renderTwsEvents("logs-tws-events", tws.items || []);
    }
  }

  if (!container && !twsContainer) return;
  await loadLogs();
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    const params = new URLSearchParams();
    Object.entries(data).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const query = params.toString();
    const filtered = await api(`/api/events?limit=200${query ? `&${query}` : ""}`);
    renderEvents("logs-events", filtered.items || []);
    const twsFiltered = await api(`/api/logs/tws?limit=200${query ? `&${query}` : ""}`);
    renderTwsEvents("logs-tws-events", twsFiltered.items || []);
  });
}

async function renderRadarHubPage() {
  await Promise.all([
    renderV2ScannerPage({ afterAction: renderRadarHubPage }),
    renderDetectionTechniquesPanel(),
    renderScanReliabilityPanel(),
    renderV2OpportunitiesPage({ afterScan: renderRadarHubPage }),
  ]);
}

async function fetchScanReliability() {
  if (fetchScanReliability._cache && Date.now() - fetchScanReliability._at < 15000) {
    return fetchScanReliability._cache;
  }
  const stats = await optionalApi("/api/techniques/stats").catch(() => null);
  fetchScanReliability._cache = stats;
  fetchScanReliability._at = Date.now();
  return stats;
}

function reliabilityLabel(technique, minSamples) {
  if (!technique) return "-";
  if (!technique.min_samples_reached) {
    return `<span class="status" title="${escapeHtml(`${technique.sample_size || 0} evaluations < seuil ${minSamples}`)}">ECHANTILLON INSUFFISANT</span>`;
  }
  const rate = technique.hit_rate == null ? "-" : `${(technique.hit_rate * 100).toFixed(0)}%`;
  return `<span title="correct ${technique.correct || 0} / faux ${technique.wrong || 0} (expectancy ${technique.expectancy_r == null ? "-" : technique.expectancy_r}R)">${escapeHtml(rate)} (${technique.correct || 0}/${(technique.correct || 0) + (technique.wrong || 0)})</span>`;
}

async function renderScanReliabilityPanel() {
  const tiles = document.getElementById("scan-reliability-tiles");
  const table = document.getElementById("scan-reliability-table");
  if (!tiles && !table) return;
  const stats = await fetchScanReliability();
  if (!stats || !stats.global) {
    if (tiles) tiles.innerHTML = "";
    return;
  }
  const globalStats = stats.global;
  if (tiles) {
    renderV2Kpis("scan-reliability-tiles", {
      corrects: globalStats.correct,
      faux: globalStats.wrong,
      indetermines: globalStats.indeterminate,
      en_attente: globalStats.pending,
      expires: globalStats.expired,
      hit_rate_global: globalStats.hit_rate == null ? "-" : `${(globalStats.hit_rate * 100).toFixed(0)}%`,
    });
  }
  if (table) {
    renderV2Table("scan-reliability-table", [
      ["technique_id", "Technique"],
      ["detections_total", "Detections"],
      ["pending", "En attente"],
      ["correct", "Corrects"],
      ["wrong", "Faux"],
      ["indeterminate", "Indet."],
      ["reliability", "Hit rate"],
      ["expectancy", "Expectancy"],
    ], (stats.techniques || []).map((technique) => ({
      ...technique,
      reliability: reliabilityLabel(technique, stats.min_samples),
      expectancy: technique.min_samples_reached && technique.expectancy_r != null
        ? `${technique.expectancy_r}R`
        : "-",
    })));
  }
}

function opportunityReliabilityCell(item, stats) {
  // Etape 13.5: the score says the theoretical quality of the setup, the
  // technique's historical hit rate says what this kind of signal actually
  // delivered. Both are shown side by side.
  if (!stats || !Array.isArray(stats.techniques)) return "-";
  const signal = (item.payload && item.payload.market_context_signal) || {};
  const ids = signal.detected_by_techniques || [];
  if (!ids.length) return "-";
  const byId = new Map(stats.techniques.map((technique) => [technique.technique_id, technique]));
  const technique = ids.map((id) => byId.get(id)).find(Boolean);
  return reliabilityLabel(technique, stats.min_samples);
}

async function renderDetectionTechniquesPanel() {
  const table = document.getElementById("detection-techniques-table");
  if (!table) return;
  const result = await api("/api/techniques");
  const items = result.items || [];
  setText("detection-techniques-count", `${items.length} techniques`);
  const stats = (item) => item.stats || {};
  renderV2Table("detection-techniques-table", [
    ["name", "Technique"],
    ["status", "Statut"],
    ["origin", "Origine"],
    ["hit_rate", "Hit rate"],
    ["samples", "Samples"],
    ["enabled", "Actif"],
  ], items.map((item) => ({
    ...item,
    status: statusBadge(item.status),
    hit_rate: stats(item).hit_rate == null ? "-" : `${(stats(item).hit_rate * 100).toFixed(0)}%`,
    samples: stats(item).sample_size || 0,
    enabled: techniqueToggleCell(item),
  })));
  wireDetectionTechniqueRows(table, items);
}

function techniqueToggleCell(item) {
  const label = item.enabled ? "ON" : "OFF";
  const tone = item.enabled ? "positive" : "muted";
  return `<button type="button" class="v2-inline-toggle ${tone}" data-technique-toggle="${escapeHtml(item.technique_id)}">${label}</button>`;
}

function wireDetectionTechniqueRows(table, items) {
  const byId = new Map(items.map((item) => [item.technique_id, item]));
  table.querySelectorAll("[data-technique-toggle]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const id = button.getAttribute("data-technique-toggle");
      const current = byId.get(id);
      if (!current) return;
      await api(`/api/techniques/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: { enabled: !current.enabled },
      });
      await renderDetectionTechniquesPanel();
    });
  });
  table.querySelectorAll("tbody tr").forEach((row, index) => {
    const item = items[index];
    if (!item) return;
    row.style.cursor = "pointer";
    row.addEventListener("click", () => showDetectionTechniqueDetail(item));
  });
}

const FEEDBACK_OPTIONS = ["good", "too_late", "false_signal", "bad_structure"];

async function showDetectionTechniqueDetail(item) {
  const box = document.getElementById("detection-technique-detail");
  if (!box) return;
  box.hidden = false;
  const stats = item.stats || {};
  const hitRate = stats.hit_rate == null ? "-" : `${(stats.hit_rate * 100).toFixed(0)}%`;
  box.innerHTML = `
    <strong>${escapeHtml(item.name || item.technique_id)}</strong>
    <div class="muted">${escapeHtml(item.description || "")}</div>
    <div>Regle : <code>${escapeHtml(item.rule_summary || "-")}</code></div>
    <div>Type d'opportunite : ${escapeHtml(item.opportunity_type || "-")}</div>
    <div>Origine : ${escapeHtml(item.origin || "-")} · Statut : ${escapeHtml(item.status || "-")} · Revision : ${escapeHtml(String(item.revision || 1))} (config v${escapeHtml(String(item.config_version || "1"))})</div>
    <div>Stats : ${escapeHtml(stats.status_label || "-")} (${escapeHtml(String(stats.sample_size || 0))} samples, hit rate ${escapeHtml(hitRate)})</div>
    <div id="detection-technique-outcomes" class="muted">Chargement des detections…</div>
  `;
  const outcomes = await api(`/api/techniques/${encodeURIComponent(item.technique_id)}/outcomes`)
    .then((res) => res.items || [])
    .catch(() => []);
  renderTechniqueOutcomes(outcomes);
}

function renderTechniqueOutcomes(outcomes) {
  const target = document.getElementById("detection-technique-outcomes");
  if (!target) return;
  if (!outcomes.length) {
    target.textContent = "Aucune detection enregistree pour le moment.";
    return;
  }
  target.classList.remove("muted");
  target.innerHTML = outcomes.slice(0, 20).map((outcome) => `
    <div class="detection-outcome-row" data-outcome-id="${escapeHtml(outcome.outcome_id)}">
      <span>${escapeHtml(outcome.symbol || "-")} · ${escapeHtml(outcome.horizon || "-")} · ${escapeHtml(outcome.status || "-")}${outcome.label_1r == null ? "" : ` · label ${escapeHtml(String(outcome.label_1r))}`}</span>
      <span class="detection-feedback">
        ${FEEDBACK_OPTIONS.map((value) => `<button type="button" class="v2-inline-toggle" data-feedback="${value}">${value}</button>`).join("")}
        <em class="detection-feedback-value">${escapeHtml(outcome.human_feedback || "")}</em>
      </span>
    </div>
  `).join("");
  target.querySelectorAll(".detection-outcome-row").forEach((row) => {
    const outcomeId = row.getAttribute("data-outcome-id");
    row.querySelectorAll("[data-feedback]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/api/techniques/outcomes/${encodeURIComponent(outcomeId)}/feedback`, {
          method: "PATCH",
          body: { feedback: button.getAttribute("data-feedback") },
        });
        const label = row.querySelector(".detection-feedback-value");
        if (label) label.textContent = button.getAttribute("data-feedback");
      });
    });
  });
}

async function renderObservabilityPage() {
  await Promise.all([
    renderV2DecisionTracePage(),
    renderV2SystemHealthPage(),
  ]);
}

async function renderResearchHubPage() {
  await Promise.all([
    renderV2ForecastingPage(),
    renderForecastStackPage(),
    renderForecastAccuracyPage(),
    renderV2ModelLabPage(),
    renderModelLabForecastStackPage(),
    renderV2BacktestsPage(),
  ]);
}

async function renderV2Page() {
  if (page === "opportunity-radar") return renderRadarHubPage();
  if (page === "observability") return renderObservabilityPage();
  if (page === "research") return renderResearchHubPage();
  if (page === "market-context") return renderV2MarketContextPage();
  return null;
}

async function renderV2OpportunitiesPage(options = {}) {
  const afterScan = options.afterScan || renderV2OpportunitiesPage;
  const scanButton = document.getElementById("v2-run-scan");
  if (scanButton) {
    scanButton.addEventListener("click", async () => {
      await api("/api/opportunities/scan", { method: "POST", body: {} });
      await afterScan();
    }, { once: true });
  }
  const [result, reliability] = await Promise.all([
    api("/api/opportunities/shortlist?limit=25"),
    fetchScanReliability(),
  ]);
  const rows = result.top_opportunities || result.items || [];
  const scenarios = result.generated_scenarios || [];
  setText("v2-opportunities-count", `${rows.length} items`);
  renderV2Table("v2-opportunities-table", [
    ["symbol", "Symbol"],
    ["opportunity_type", "Type"],
    ["detected_by", "Detecte par"],
    ["timeframe", "TF"],
    ["score", "Score"],
    ["quality", "Qualite"],
    ["reliability", "Fiabilite"],
    ["entry_level", "Entree"],
    ["stop_level", "SL"],
    ["r_per_share", "R/share"],
    ["status", "Status"],
    ["detected_at", "Detected"],
    ["reason", "Reason"],
  ], rows.map((item) => ({
    ...item,
    detected_by: item.detected_by || (item.payload && item.payload.detected_by) || "-",
    reason: (item.payload && item.payload.reason) || "",
    quality: opportunityQualityCell(item),
    reliability: opportunityReliabilityCell(item, reliability),
    entry_level: opportunityEntryCell(item),
    stop_level: opportunityStopCell(item),
    r_per_share: item.risk_per_share == null ? "-" : maybeMoney(item.risk_per_share),
    status: statusBadge(item.status),
  })));
  setText("v2-opportunities-scenarios-count", `${scenarios.length} items`);
  renderV2Table("v2-opportunities-scenarios-table", [
    ["scenario_id", "Scenario"],
    ["symbol", "Symbol"],
    ["setup_type", "Type"],
    ["status", "Status"],
    ["ambiguities", "Ambiguities"],
  ], scenarios.map((item) => ({
    ...item,
    status: statusBadge(item.status),
    ambiguities: (item.ambiguities || []).map((entry) => entry.field).join(", "),
  })));
}

function opportunityEntryCell(item) {
  // Consultative levels (etape 12): never an order button here, the
  // execution stays on the setup circuit or the manual order form.
  if (item.levels_status === "INCOMPLETE") {
    const ambiguities = (item.levels_ambiguities || [])
      .map((entry) => entry && (entry.reason || entry.field))
      .filter(Boolean)
      .join(" | ");
    return `<span class="status" title="${escapeHtml(ambiguities || "Niveaux non derivables")}">INCOMPLETE</span>`;
  }
  return maybeMoney(item.suggested_entry);
}

function opportunityStopCell(item) {
  if (item.suggested_stop == null) return "-";
  const value = maybeMoney(item.suggested_stop);
  if (item.stop_source === "ATR_FALLBACK") {
    return `<span title="Stop ATR de repli (pas un niveau structurel)">${escapeHtml(value)} (ATR)</span>`;
  }
  return value;
}

function opportunityQualityCell(item) {
  // Weighted quality score (skills.md 9.1): observational, next to the legacy
  // score; components frozen in F1 keep it low by construction.
  const scorePayload = item.payload && item.payload.score;
  if (!scorePayload || typeof scorePayload !== "object") return "-";
  const quality = numberOrNull(scorePayload.quality_score);
  const grade = scorePayload.score_grade;
  if (quality === null || !grade) return "-";
  return `${quality} · ${escapeHtml(String(grade))}`;
}

async function renderV2ScannerPage(options = {}) {
  const afterAction = options.afterAction || renderV2ScannerPage;
  wireV2Button("v2-scanner-run", "/api/scanner/run", afterAction);
  wireV2Button("v2-scanner-pause", "/api/scanner/pause", afterAction);
  wireV2Button("v2-scanner-resume", "/api/scanner/resume", afterAction);
  const [status, config] = await Promise.all([
    api("/api/scanner/status"),
    api("/api/scanner/config"),
  ]);
  renderV2Kpis("v2-scanner-status", {
    enabled: status.enabled,
    paused: status.paused,
    opportunities: status.opportunity_count,
    last_run: status.last_run && status.last_run.ran_at,
    candidates: status.last_run && status.last_run.candidates,
    shortlisted: status.last_run && status.last_run.shortlisted,
  });
  renderJson("v2-scanner-config", config);
}

async function renderV2MarketContextPage() {
  wireV2Button("v2-market-refresh", "/api/market-context/refresh", renderV2MarketContextPage);
  const [overview, sectors] = await Promise.all([
    api("/api/market-context/summary"),
    api("/api/market-context/sectors"),
  ]);
  renderV2Kpis("v2-market-overview", {
    symbols: overview.symbols,
    strong_context: overview.strong_context,
    weak_context: overview.weak_context,
    auto_allowed: overview.auto_allowed,
    watch_only: overview.watch_only,
    as_of: overview.as_of,
  });
  renderV2Table("v2-market-sectors", [
    ["sector", "Sector"],
    ["symbols", "Symbols"],
    ["average_context_score", "Score"],
    ["average_performance", "Perf"],
    ["status", "Status"],
  ], sectors.items || []);
}

async function renderV2ModelLabPage() {
  const result = await api("/api/model-lab/benchmarks");
  renderV2Table("v2-model-benchmarks", [
    ["model_name", "Model"],
    ["symbol", "Symbol"],
    ["timeframe", "TF"],
    ["horizon", "Horizon"],
    ["beats_baseline", "Beats baseline"],
    ["created_at", "Created"],
  ], result.items || []);
}

async function renderV2BacktestsPage() {
  const result = await api("/api/backtests");
  renderV2Table("v2-backtests", [
    ["backtest_id", "Backtest"],
    ["symbol", "Symbol"],
    ["timeframe", "TF"],
    ["status", "Status"],
    ["metrics", "Metrics"],
    ["created_at", "Created"],
  ], (result.items || []).map((item) => ({
    ...item,
    status: statusBadge(item.status),
    metrics: compactJson(item.metrics || {}),
  })));
}

async function renderV2ForecastingPage() {
  const result = await api("/api/forecasting/models");
  const modelRows = [
    ...(result.external_models || []),
    ...(result.baselines || []),
  ].map((item) => ({
    ...item,
    status: statusBadge(item.status || (item.available ? "AVAILABLE" : "MISSING_DEPENDENCY")),
  }));
  renderV2Table("v2-forecast-models", [
    ["model", "Model"],
    ["status", "Status"],
    ["baseline", "Baseline"],
    ["reason", "Reason"],
  ], modelRows);
  renderJson("v2-forecast-defaults", {
    default_models: result.default_models,
    decision_policy: result.decision_policy,
  });
}

async function renderForecastStackPage() {
  const result = await api("/api/forecasting/providers");
  renderV2Table("forecast-stack-providers", [
    ["model_name", "Model"],
    ["role", "Role"],
    ["priority", "Priority"],
    ["enabled", "Enabled"],
    ["worker_status", "Worker"],
    ["dependency_status", "Dependency"],
    ["input_data_status", "Input"],
    ["forecast_status", "Forecast"],
    ["direction", "Direction"],
    ["confidence", "Confidence"],
    ["expected_move_pct", "Expected move"],
    ["uncertainty_width_pct", "Interval %"],
    ["forecast_horizon", "Horizon"],
    ["reliability_status", "Reliability"],
    ["samples_display", "Samples"],
    ["eligible_for_display", "Display"],
    ["eligible_for_execution", "Execution"],
    ["execution_block_reason", "Reason"],
    ["last_run", "Last run"],
    ["last_error", "Last error"],
    ["status", "Legacy status"],
    ["use_for_scoring", "Scoring"],
    ["use_for_model_lab", "Model Lab"],
    ["action_required", "Action"],
  ], (result.providers || []).map((item) => ({
    ...item,
    status: statusBadge(item.status),
    worker_status: statusBadge(item.worker_status || item.status),
    dependency_status: statusBadge(item.dependency_status || "-"),
    input_data_status: statusBadge(item.input_data_status || "-"),
    forecast_status: statusBadge(item.forecast_status || "-"),
    reliability_status: statusBadge(item.reliability_status || item.reliability_grade || "-"),
    samples_display: `${item.accuracy_samples ?? item.sample_size ?? 0}/${item.min_accuracy_samples_required ?? 30}`,
    eligible_for_display: item.eligible_for_display ? "YES" : "NO",
    eligible_for_execution: item.eligible_for_execution ? "YES" : "NO",
    execution_block_reason: statusLabel(item.execution_block_reason || "-"),
    direction: item.direction || "-",
    confidence: item.confidence_display || item.confidence || "-",
    expected_move_pct: signedPercent(item.expected_move_pct),
    uncertainty_width_pct: maybePercent(item.uncertainty_width_pct),
    forecast_horizon: item.forecast_horizon || "-",
  })));
  renderJson("forecast-stack-safety", {
    execution_mode: result.execution_mode,
    primary_model: result.primary_model,
    safety: result.safety,
  });
}

async function renderForecastAccuracyPage() {
  const refreshButton = document.getElementById("forecast-accuracy-refresh");
  if (refreshButton && !refreshButton.dataset.wired) {
    refreshButton.dataset.wired = "true";
    refreshButton.addEventListener("click", () => renderForecastAccuracyPage());
  }
  const [modelsResult, setupsResult] = await Promise.all([
    api("/api/forecasting/models"),
    api("/api/setups"),
  ]);
  const modelSelect = document.getElementById("forecast-accuracy-model");
  const symbolSelect = document.getElementById("forecast-accuracy-symbol");
  const timeframeSelect = document.getElementById("forecast-accuracy-timeframe");
  const horizonSelect = document.getElementById("forecast-accuracy-horizon");
  const modelOptions = uniqueOptions([
    { value: "timesfm", label: "timesfm" },
    ...((modelsResult.default_models || []).map((item) => ({ value: String(item).toLowerCase(), label: String(item) }))),
    ...((modelsResult.external_models || []).map((item) => ({ value: String(item.model || "").toLowerCase(), label: String(item.model || "") }))),
    ...((modelsResult.baselines || []).map((item) => ({ value: String(item.model || "").toLowerCase(), label: String(item.model || "") }))),
    { value: "naive_baseline", label: "naive_baseline" },
    { value: "atr_baseline", label: "atr_baseline" },
  ]);
  const symbolOptions = uniqueOptions((setupsResult.items || [])
    .map((item) => String(item.symbol || "").toUpperCase())
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b))
    .map((value) => ({ value, label: value })));
  const timeframeOptions = ["15m", "1h", "1d"];
  const horizonOptions = ["4", "8", "16", "24"];
  populateSelect(modelSelect, modelOptions.length ? modelOptions : [{ value: "timesfm", label: "timesfm" }], "timesfm");
  populateSelect(symbolSelect, symbolOptions.length ? symbolOptions : [{ value: "", label: "All symbols" }], "");
  populateSelect(timeframeSelect, timeframeOptions.map((value) => ({ value, label: value })), "15m");
  populateSelect(horizonSelect, horizonOptions.map((value) => ({ value, label: value })), "4");
  const model = (modelSelect || {}).value || "";
  const symbol = (symbolSelect || {}).value || "";
  const timeframe = (timeframeSelect || {}).value || "";
  const horizon = (horizonSelect || {}).value || "";
  const normalizedModel = model.trim().toLowerCase() || "timesfm";
  const params = new URLSearchParams();
  if (symbol.trim()) params.set("symbol", symbol.trim().toUpperCase());
  if (timeframe.trim()) params.set("timeframe", timeframe.trim());
  if (horizon.trim()) params.set("horizon_bars", horizon.trim());
  const path = `/api/forecasting/accuracy/${encodeURIComponent(normalizedModel)}${params.toString() ? `?${params.toString()}` : ""}`;
  const result = await api(path);
  const scorecards = result.items || [];
  const outcomes = result.last_forecasts || [];
  renderV2Table("forecast-accuracy-scorecards", [
    ["model_name", "Model"],
    ["symbol", "Symbol"],
    ["timeframe", "TF"],
    ["horizon_bars", "Horizon"],
    ["sample_size", "Samples"],
    ["direction_accuracy", "Direction acc"],
    ["mae", "MAE"],
    ["rmse", "RMSE"],
    ["mape", "MAPE"],
    ["reliability_grade", "Grade"],
    ["updated_at", "Updated"],
  ], scorecards.map((item) => ({
    ...item,
    direction_accuracy: maybePercent(item.direction_accuracy * 100),
    mae: maybeMoney(item.mae),
    rmse: maybeMoney(item.rmse),
    mape: maybePercent(item.mape * 100),
    reliability_grade: statusBadge(item.reliability_grade || "-"),
  })));
  renderV2Table("forecast-accuracy-outcomes", [
    ["model_name", "Model"],
    ["symbol", "Symbol"],
    ["timeframe", "TF"],
    ["horizon_bars", "Horizon"],
    ["generated_at", "Generated"],
    ["forecast_direction", "Pred dir"],
    ["actual_direction", "Actual dir"],
    ["direction_correct", "Correct"],
    ["absolute_error", "Abs err"],
    ["percentage_error", "% err"],
    ["entry_touched_before_horizon", "Entry touch"],
    ["stop_touched_before_horizon", "Stop touch"],
    ["stop_touched_before_entry", "Stop first"],
    ["quality_bucket", "Bucket"],
  ], outcomes.map((item) => ({
    ...item,
    direction_correct: yesNo(item.direction_correct),
    entry_touched_before_horizon: yesNo(item.entry_touched_before_horizon),
    stop_touched_before_horizon: yesNo(item.stop_touched_before_horizon),
    stop_touched_before_entry: yesNo(item.stop_touched_before_entry),
    absolute_error: maybeMoney(item.absolute_error),
    percentage_error: maybePercent(item.percentage_error * 100),
    quality_bucket: statusBadge(item.quality_bucket || "-"),
  })));
}

function populateSelect(select, options, defaultValue = "") {
  if (!select) return;
  const current = select.value || defaultValue || "";
  select.innerHTML = "";
  if (defaultValue === "") {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "All";
    select.appendChild(option);
  }
  for (const optionData of options) {
    const option = document.createElement("option");
    option.value = String(optionData.value ?? "");
    option.textContent = String(optionData.label ?? optionData.value ?? "");
    select.appendChild(option);
  }
  if (current && [...select.options].some((option) => option.value === current)) {
    select.value = current;
  } else if ([...select.options].some((option) => option.value === defaultValue)) {
    select.value = defaultValue;
  }
}

function uniqueOptions(options) {
  const seen = new Set();
  const unique = [];
  for (const option of options) {
    const value = String(option && option.value ? option.value : "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    unique.push({ value, label: String(option.label ?? value) });
  }
  return unique;
}

async function renderModelLabForecastStackPage() {
  const result = await api("/api/model-lab/forecast-stack/results");
  renderV2Table("forecast-stack-experiments", [
    ["experiment_id", "Experiment"],
    ["name", "Name"],
    ["models", "Models"],
    ["status", "Status"],
    ["summary", "Summary"],
    ["started_at", "Started"],
  ], (result.items || []).map((item) => ({
    ...item,
    models: (item.models || []).join(", "),
    status: statusBadge(item.status),
    summary: compactJson(item.summary || {}),
  })));
  const rows = (result.items || []).flatMap((experiment) => (
    (experiment.results || []).map((item) => ({
      ...item,
      experiment_id: experiment.experiment_id,
      metrics: compactJson(item.metrics || {}),
      trading_metrics: compactJson(item.trading_metrics || {}),
      selected_for_symbol: item.selected_for_symbol ? "YES" : "NO",
    }))
  ));
  renderV2Table("forecast-stack-results", [
    ["model_name", "Model"],
    ["symbol", "Symbol"],
    ["timeframe", "Timeframe"],
    ["horizon_bars", "Horizon"],
    ["rank_overall", "Rank"],
    ["selected_for_symbol", "Selected"],
    ["metrics", "Forecast metrics"],
    ["trading_metrics", "Trading-aware metrics"],
  ], rows);
}

async function renderV2DecisionTracePage() {
  const form = document.getElementById("v2-decision-filter");
  if (form && !form.dataset.wired) {
    form.dataset.wired = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await renderV2DecisionTraceList(new FormData(form));
    });
  }
  await renderV2DecisionTraceList(form ? new FormData(form) : null);
}

async function renderV2DecisionTraceList(formDataPayload) {
  const params = new URLSearchParams();
  if (formDataPayload) {
    for (const [key, value] of formDataPayload.entries()) {
      if (value) params.set(key, value);
    }
  }
  const result = await api(`/api/decision-trace${params.toString() ? `?${params}` : ""}`);
  const rows = result.items || [];
  setText("v2-decision-count", `${rows.length} traces`);
  const container = document.getElementById("v2-decision-traces");
  if (!container) return;
  container.innerHTML = rows.map((item) => `
    <article class="v2-trace-card">
      <div class="v2-trace-head">
        <strong>${escapeHtml(item.decision_type || "-")}</strong>
        ${statusBadge(item.final_decision || "-")}
      </div>
      <dl class="detail-list compact-detail-list">
        <dt>Trace</dt><dd>${escapeHtml(item.trace_id || "-")}</dd>
        <dt>Symbol</dt><dd>${escapeHtml(item.symbol || "-")}</dd>
        <dt>Setup</dt><dd>${escapeHtml(item.setup_id || "-")}</dd>
        <dt>Opportunity</dt><dd>${escapeHtml(item.opportunity_id || "-")}</dd>
        <dt>Created</dt><dd>${escapeHtml(item.created_at || "-")}</dd>
      </dl>
      <pre class="config-view compact">${escapeHtml(JSON.stringify(item.trace || {}, null, 2))}</pre>
    </article>
  `).join("") || `<div class="market-context-empty">Aucune trace</div>`;
}

async function renderV2SystemHealthPage() {
  const [health, metrics, risk] = await Promise.all([
    api("/api/health"),
    api("/api/metrics"),
    api("/api/portfolio-risk/latest"),
  ]);
  renderV2Kpis("v2-health", health);
  renderV2Kpis("v2-metrics", metrics);
  renderJson("v2-portfolio-risk", risk);
}

function renderV2Table(id, columns, rows) {
  const table = document.getElementById(id);
  if (!table) return;
  table.innerHTML = `
    <thead>
      <tr>${columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr>
    </thead>
    <tbody>
      ${rows.map((row) => `
        <tr>
          ${columns.map(([key]) => `<td>${formatV2Cell(row[key])}</td>`).join("")}
        </tr>
      `).join("") || `<tr><td colspan="${columns.length}">No data</td></tr>`}
    </tbody>
  `;
}

function renderV2Kpis(id, payload) {
  const container = document.getElementById(id);
  if (!container) return;
  container.innerHTML = Object.entries(payload || {}).map(([key, value]) => `
    <article class="metric-card">
      <span>${escapeHtml(formatConfigLabel(key))}</span>
      <strong>${escapeHtml(formatV2Plain(value))}</strong>
    </article>
  `).join("");
}

function renderJson(id, payload) {
  const node = document.getElementById(id);
  if (node) node.textContent = JSON.stringify(payload || {}, null, 2);
}

function formatV2Cell(value) {
  if (typeof value === "string" && value.includes("<span")) return value;
  return escapeHtml(formatV2Plain(value));
}

function formatV2Plain(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "YES" : "NO";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") return compactJson(value);
  return String(value);
}

function compactJson(value) {
  return JSON.stringify(value || {});
}

function wireV2Button(id, path, after) {
  const button = document.getElementById(id);
  if (!button || button.dataset.wired) return;
  button.dataset.wired = "1";
  button.addEventListener("click", async () => {
    await api(path, { method: "POST", body: {} });
    if (after) await after();
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
const DASH_PALETTE = ["#5b8cff", "#34d399", "#f5b547", "#fb7185", "#a78bfa", "#38bdf8", "#94a3b8"];
const dashLastValues = new Map();
const dashCountUpTimers = new Map();
let dashEquityHistory = [];
let dashLiveEquity = null;
let dashLastUpdate = 0;
let dashEquityTimer = null;
let dashAgoTimer = null;
let dashCurveDrawn = false;

const dashCurrencyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function dashFormatCurrency(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return dashCurrencyFmt.format(number);
}

function dashFormatSigned(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const sign = number > 0 ? "+" : "";
  return `${sign}${dashCurrencyFmt.format(number)}`;
}

function initDashboardPremium() {
  fetchEquityHistory({ animate: true });
  if (dashEquityTimer) window.clearInterval(dashEquityTimer);
  dashEquityTimer = window.setInterval(() => fetchEquityHistory({ animate: false }), 60000);
  if (dashAgoTimer) window.clearInterval(dashAgoTimer);
  dashAgoTimer = window.setInterval(dashUpdatedAgoTick, 1000);
}

async function fetchEquityHistory({ animate }) {
  try {
    const data = await api("/api/equity/history?limit=500");
    dashEquityHistory = Array.isArray(data.points) ? data.points : [];
    dashRedrawEquity({ animate: animate && !dashCurveDrawn });
    updateEquityLegend(data);
  } catch (error) {
    /* keep last drawn curve on transient errors */
  }
}

function updateEquityLegend(data) {
  const changeEl = document.getElementById("equity-change");
  const rangeEl = document.getElementById("equity-range");
  if (changeEl) {
    if (data && data.change != null) {
      const pct = data.change_pct != null ? ` (${data.change_pct > 0 ? "+" : ""}${data.change_pct}%)` : "";
      changeEl.textContent = `${dashFormatSigned(data.change)}${pct}`;
      changeEl.classList.remove("money-positive", "money-negative", "money-flat");
      changeEl.classList.add(data.change > 0 ? "money-positive" : data.change < 0 ? "money-negative" : "money-flat");
    } else {
      changeEl.textContent = "--";
    }
  }
  if (rangeEl) {
    const n = data && data.count ? data.count : dashEquityHistory.length;
    rangeEl.textContent = n > 1 ? `${n} points enregistres` : "En attente de donnees";
  }
}

function dashSeries() {
  const series = dashEquityHistory
    .map((p) => Number(p.equity))
    .filter((v) => Number.isFinite(v));
  if (dashLiveEquity != null && Number.isFinite(Number(dashLiveEquity))) {
    const live = Number(dashLiveEquity);
    if (!series.length || series[series.length - 1] !== live) series.push(live);
  }
  return series;
}

function dashRedrawEquity({ animate }) {
  const host = document.getElementById("equity-chart");
  if (!host) return;
  const series = dashSeries();
  const empty = document.getElementById("equity-empty");
  if (series.length < 2) {
    if (empty) empty.style.display = "grid";
    const existing = host.querySelector("svg");
    if (existing) existing.remove();
    return;
  }
  if (empty) empty.style.display = "none";

  const W = 1000;
  const H = 240;
  const padX = 8;
  const padY = 18;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = max - min || 1;
  const stepX = (W - padX * 2) / (series.length - 1);
  const coords = series.map((v, i) => {
    const x = padX + i * stepX;
    const y = padY + (H - padY * 2) * (1 - (v - min) / span);
    return [x, y];
  });
  const linePath = coords
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
  const areaPath = `${linePath} L${coords[coords.length - 1][0].toFixed(1)},${H} L${coords[0][0].toFixed(1)},${H} Z`;
  const rising = series[series.length - 1] >= series[0];
  const stroke = rising ? "var(--dash-mint)" : "var(--dash-coral)";
  const fillId = "dashEquityFill";
  const stopColor = rising ? "rgba(52,211,153,0.28)" : "rgba(251,113,133,0.28)";

  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Courbe d'equite">
      <defs>
        <linearGradient id="${fillId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${stopColor}"></stop>
          <stop offset="100%" stop-color="rgba(10,14,26,0)"></stop>
        </linearGradient>
      </defs>
      <path d="${areaPath}" fill="url(#${fillId})" stroke="none"></path>
      <path class="equity-line ${rising ? "up" : "down"}" d="${linePath}"></path>
    </svg>`;

  const lineEl = host.querySelector(".equity-line");
  if (lineEl) {
    lineEl.style.stroke = stroke;
    if (animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const len = lineEl.getTotalLength();
      lineEl.style.strokeDasharray = String(len);
      lineEl.style.strokeDashoffset = String(len);
      lineEl.style.animation = "dashDraw 1s ease forwards";
      dashCurveDrawn = true;
    }
  }
}

function renderDashboardPremium(snapshot) {
  const metrics = snapshot.metrics || {};
  const account = metrics.account || {};
  const netLiq = account.net_liquidation;
  const dailyPnl = metrics.broker_pnl_fresh ? metrics.today_pnl : metrics.today_pnl;
  const unrealized = metrics.positions_pnl;

  dashSetMoney("hero-portfolio-value", netLiq, { countUp: true, currency: true });
  dashSetMoney("hero-daily-pnl", dailyPnl, { countUp: true, signed: true, tone: true });
  dashSetMoney("hero-unrealized-pnl", unrealized, { countUp: true, signed: true, tone: true });
  setText("hero-open-positions", metrics.open_positions ?? 0);

  const subEl = document.getElementById("hero-daily-pnl-sub");
  if (subEl) subEl.textContent = brokerPnlSourceLabel(metrics);
  const psub = document.getElementById("hero-portfolio-sub");
  if (psub) psub.textContent = `Net liquidation · ${account.currency || "USD"}`;

  dashLiveEquity = Number.isFinite(Number(netLiq)) ? Number(netLiq) : dashLiveEquity;
  dashRedrawEquity({ animate: false });

  drawAllocationDonut((snapshot.performance || {}).stock_pnl || []);

  dashLastUpdate = Date.now();
  dashUpdatedAgoTick();
}

function dashSetMoney(id, value, opts = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  const number = Number(value);
  const format = opts.currency
    ? dashFormatCurrency
    : opts.signed
      ? dashFormatSigned
      : dashFormatCurrency;
  if (!Number.isFinite(number)) {
    el.textContent = "--";
    return;
  }
  if (opts.tone) {
    el.classList.remove("money-positive", "money-negative", "money-flat");
    el.classList.add(number > 0 ? "money-positive" : number < 0 ? "money-negative" : "money-flat");
  }
  const prev = dashLastValues.get(id);
  if (prev != null && prev !== number) dashFlash(el, number >= prev);
  if (opts.countUp && prev != null && prev !== number
      && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    dashCountUp(el, prev, number, format);
  } else {
    el.textContent = format(number);
  }
  dashLastValues.set(id, number);
}

function dashCountUp(el, from, to, format) {
  const existing = dashCountUpTimers.get(el.id);
  if (existing) window.cancelAnimationFrame(existing);
  const duration = 600;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = format(from + (to - from) * eased);
    if (t < 1) {
      dashCountUpTimers.set(el.id, window.requestAnimationFrame(step));
    } else {
      el.textContent = format(to);
      dashCountUpTimers.delete(el.id);
    }
  };
  dashCountUpTimers.set(el.id, window.requestAnimationFrame(step));
}

function dashFlash(el, up) {
  el.classList.remove("flash-up", "flash-down");
  void el.offsetWidth;
  el.classList.add(up ? "flash-up" : "flash-down");
  window.setTimeout(() => el.classList.remove("flash-up", "flash-down"), 750);
}

function dashUpdatedAgoTick() {
  const ago = document.getElementById("dash-updated-ago");
  const live = document.getElementById("dash-live");
  if (!dashLastUpdate) return;
  const seconds = Math.max(0, Math.round((Date.now() - dashLastUpdate) / 1000));
  if (ago) ago.textContent = seconds < 2 ? "a l'instant" : `il y a ${seconds}s`;
  if (live) live.dataset.state = seconds > 20 ? "stale" : "idle";
}

function drawAllocationDonut(stockPnl) {
  const host = document.getElementById("allocation-donut");
  const legend = document.getElementById("allocation-legend");
  if (!host) return;
  const rows = (stockPnl || [])
    .map((r) => ({ symbol: r.symbol, value: Math.abs(Number(r.market_value) || 0) }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value);
  const empty = document.getElementById("allocation-empty");
  if (!rows.length) {
    host.querySelectorAll("svg").forEach((n) => n.remove());
    if (empty) empty.style.display = "grid";
    if (legend) legend.innerHTML = "";
    return;
  }
  if (empty) empty.style.display = "none";

  let items = rows;
  if (rows.length > 6) {
    const head = rows.slice(0, 5);
    const rest = rows.slice(5).reduce((sum, r) => sum + r.value, 0);
    items = [...head, { symbol: "Autres", value: rest }];
  }
  const total = items.reduce((sum, r) => sum + r.value, 0) || 1;

  const R = 46;
  const circ = 2 * Math.PI * R;
  let offset = 0;
  const segments = items.map((item, i) => {
    const frac = item.value / total;
    const len = frac * circ;
    const color = DASH_PALETTE[i % DASH_PALETTE.length];
    const seg = `<circle class="donut-seg" cx="50" cy="50" r="${R}" stroke="${color}"
      style="--circ:${circ.toFixed(2)}; --seg-len:${len.toFixed(2)}; --seg-off:${(-offset).toFixed(2)}"></circle>`;
    offset += len;
    return { seg, color, item, frac };
  });

  host.querySelectorAll("svg").forEach((n) => n.remove());
  const svg = `<svg viewBox="0 0 100 100" role="img" aria-label="Allocation par position">
      <circle cx="50" cy="50" r="${R}" fill="none" stroke="var(--dash-line)" stroke-width="18"></circle>
      ${segments.map((s) => s.seg).join("")}
    </svg>
    <div class="allocation-center">
      <strong>${items.length}</strong>
      <span>positions</span>
    </div>`;
  host.insertAdjacentHTML("afterbegin", svg);

  if (legend) {
    legend.innerHTML = segments
      .map((s) => `<li>
        <span class="swatch" style="background:${s.color}"></span>
        <span class="alloc-sym">${escapeHtml(s.item.symbol)}</span>
        <span class="alloc-pct">${(s.frac * 100).toFixed(1)}%</span>
      </li>`)
      .join("");
  }
}

init().catch((error) => toast(error.message));
