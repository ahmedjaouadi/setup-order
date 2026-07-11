import { api, optionalApi } from "./api-client.js";
import { page } from "./state.js";
import {
  escapeHtml,
  formatConfigLabel,
  maybeMoney,
  maybePercent,
  numberOrNull,
  setText,
  signedPercent,
  statusBadge,
  statusLabel,
  yesNo,
} from "./ui-helpers.js";

export async function renderRadarHubPage() {
  await Promise.all([
    renderV2ScannerPage({ afterAction: renderRadarHubPage }),
    renderDetectionTechniquesPanel(),
    renderScanReliabilityPanel(),
    renderV2OpportunitiesPage({ afterScan: renderRadarHubPage }),
  ]);
}

export async function fetchScanReliability() {
  if (fetchScanReliability._cache && Date.now() - fetchScanReliability._at < 15000) {
    return fetchScanReliability._cache;
  }
  const stats = await optionalApi("/api/techniques/stats").catch(() => null);
  fetchScanReliability._cache = stats;
  fetchScanReliability._at = Date.now();
  return stats;
}

export function reliabilityLabel(technique, minSamples) {
  if (!technique) return "-";
  if (!technique.min_samples_reached) {
    return `<span class="status" title="${escapeHtml(`${technique.sample_size || 0} evaluations < seuil ${minSamples}`)}">ECHANTILLON INSUFFISANT</span>`;
  }
  const rate = technique.hit_rate == null ? "-" : `${(technique.hit_rate * 100).toFixed(0)}%`;
  return `<span title="correct ${technique.correct || 0} / faux ${technique.wrong || 0} (expectancy ${technique.expectancy_r == null ? "-" : technique.expectancy_r}R)">${escapeHtml(rate)} (${technique.correct || 0}/${(technique.correct || 0) + (technique.wrong || 0)})</span>`;
}

export async function renderScanReliabilityPanel() {
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

export function opportunityReliabilityCell(item, stats) {
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

export async function renderDetectionTechniquesPanel() {
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

export function techniqueToggleCell(item) {
  const label = item.enabled ? "ON" : "OFF";
  const tone = item.enabled ? "positive" : "muted";
  return `<button type="button" class="v2-inline-toggle ${tone}" data-technique-toggle="${escapeHtml(item.technique_id)}">${label}</button>`;
}

export function wireDetectionTechniqueRows(table, items) {
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

export const FEEDBACK_OPTIONS = ["good", "too_late", "false_signal", "bad_structure"];

export async function showDetectionTechniqueDetail(item) {
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

export function renderTechniqueOutcomes(outcomes) {
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

export async function renderObservabilityPage() {
  await Promise.all([
    renderV2DecisionTracePage(),
    renderV2SystemHealthPage(),
  ]);
}

export async function renderResearchHubPage() {
  await Promise.all([
    renderV2ForecastingPage(),
    renderForecastStackPage(),
    renderForecastAccuracyPage(),
    renderV2ModelLabPage(),
    renderModelLabForecastStackPage(),
    renderV2BacktestsPage(),
  ]);
}

export async function renderV2Page() {
  if (page === "opportunity-radar") return renderRadarHubPage();
  if (page === "observability") return renderObservabilityPage();
  if (page === "research") return renderResearchHubPage();
  if (page === "market-context") return renderV2MarketContextPage();
  return null;
}

export async function renderV2OpportunitiesPage(options = {}) {
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

export function opportunityEntryCell(item) {
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

export function opportunityStopCell(item) {
  if (item.suggested_stop == null) return "-";
  const value = maybeMoney(item.suggested_stop);
  if (item.stop_source === "ATR_FALLBACK") {
    return `<span title="Stop ATR de repli (pas un niveau structurel)">${escapeHtml(value)} (ATR)</span>`;
  }
  return value;
}

export function opportunityQualityCell(item) {
  // Weighted quality score (skills.md 9.1): observational, next to the legacy
  // score; components frozen in F1 keep it low by construction.
  const scorePayload = item.payload && item.payload.score;
  if (!scorePayload || typeof scorePayload !== "object") return "-";
  const quality = numberOrNull(scorePayload.quality_score);
  const grade = scorePayload.score_grade;
  if (quality === null || !grade) return "-";
  return `${quality} · ${escapeHtml(String(grade))}`;
}

export async function renderV2ScannerPage(options = {}) {
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

export async function renderV2MarketContextPage() {
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

export async function renderV2ModelLabPage() {
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

export async function renderV2BacktestsPage() {
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

export async function renderV2ForecastingPage() {
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

export async function renderForecastStackPage() {
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

export async function renderForecastAccuracyPage() {
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

export function populateSelect(select, options, defaultValue = "") {
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

export function uniqueOptions(options) {
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

export async function renderModelLabForecastStackPage() {
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

export async function renderV2DecisionTracePage() {
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

export async function renderV2DecisionTraceList(formDataPayload) {
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

export async function renderV2SystemHealthPage() {
  const [health, metrics, risk] = await Promise.all([
    api("/api/health"),
    api("/api/metrics"),
    api("/api/portfolio-risk/latest"),
  ]);
  renderV2Kpis("v2-health", health);
  renderV2Kpis("v2-metrics", metrics);
  renderJson("v2-portfolio-risk", risk);
}

export function renderV2Table(id, columns, rows) {
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

export function renderV2Kpis(id, payload) {
  const container = document.getElementById(id);
  if (!container) return;
  container.innerHTML = Object.entries(payload || {}).map(([key, value]) => `
    <article class="metric-card">
      <span>${escapeHtml(formatConfigLabel(key))}</span>
      <strong>${escapeHtml(formatV2Plain(value))}</strong>
    </article>
  `).join("");
}

export function renderJson(id, payload) {
  const node = document.getElementById(id);
  if (node) node.textContent = JSON.stringify(payload || {}, null, 2);
}

export function formatV2Cell(value) {
  if (typeof value === "string" && value.includes("<span")) return value;
  return escapeHtml(formatV2Plain(value));
}

export function formatV2Plain(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "YES" : "NO";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") return compactJson(value);
  return String(value);
}

export function compactJson(value) {
  return JSON.stringify(value || {});
}

export function wireV2Button(id, path, after) {
  const button = document.getElementById(id);
  if (!button || button.dataset.wired) return;
  button.dataset.wired = "1";
  button.addEventListener("click", async () => {
    await api(path, { method: "POST", body: {} });
    if (after) await after();
  });
}
