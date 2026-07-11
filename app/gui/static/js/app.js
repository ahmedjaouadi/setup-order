import { renderEngineHealth, renderSnapshot } from "./dashboard.js";
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
