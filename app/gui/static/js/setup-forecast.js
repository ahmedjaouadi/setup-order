import { api } from "./api-client.js";
import { renderV2Table } from "./hub-pages.js";
import { renderSetups } from "./setups-list.js";
import { currentSetupDetailSetup, latestSnapshot, setForecastWatchlistBySymbol } from "./state.js";
import {
  dlRows,
  formatStatusList,
  formatTime,
  maybeMoney,
  maybePercent,
  maybeProbability,
  numberOrNull,
  onClick,
  signedPercent,
  statusBadge,
  statusLabel,
  yesNo,
} from "./ui-helpers.js";

export async function refreshForecastWatchlist() {
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

export async function fetchSetupForecast(setup, options = {}) {
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

export async function renderSetupForecastPanel(setup = currentSetupDetailSetup, options = {}) {
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

export async function renderSetupForecastStackSummary(setup) {
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

export function renderSetupForecastSummary(forecast) {
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

export function wireSetupForecastPanel() {
  onClick("setup-forecast-refresh", async () => {
    await renderSetupForecastPanel(currentSetupDetailSetup, { forceRefresh: true });
  });
}

export function drawTimesfmForecastChart(forecast, message = "") {
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

export function drawForecastLine(ctx, values, xForIndex, yForPrice, color, width) {
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

export function drawForecastReference(ctx, value, label, xForIndex, yForPrice, count, color) {
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

export function drawForecastEndpoint(ctx, value, x, yForPrice, label, color) {
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

export function drawForecastLegend(ctx, x, y, forecast) {
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
