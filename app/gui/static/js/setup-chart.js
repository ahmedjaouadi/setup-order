import { api } from "./api-client.js";
import {
  SETUP_CHART_TIMEFRAMES,
  addVolumeRatios,
  compareQuotesByTime,
  extractQuoteEvents,
  normalizeSetupChartTimeframe,
  parseChartDate,
  quoteFromHistoricalBar,
  quotePrice,
  quoteVolumeRatio,
} from "./market-quotes.js";
import { setupTradeLevels } from "./setup-analysis.js";
import {
  setSetupChartDataMessage,
  setSetupChartDataMeta,
  setupChartDataMessage,
  setupChartDataMeta,
  setupChartTimeframe,
} from "./state.js";
import { escapeHtml, maybeMoney, numberText, onClick, setButtonDisabled } from "./ui-helpers.js";

export let setupChartState = null;

export let setupChartResizeTimer = null;

export let setupChartInteractionsWired = false;

export const SETUP_CHART_MIN_VISIBLE_CANDLES = 10;

export const SETUP_CHART_INITIAL_VISIBLE_CANDLES = 60;

export const SETUP_CHART_MAX_SOURCE_CANDLES = 180;

export async function fetchSetupSymbolEvents(symbol) {
  if (!symbol) return [];
  const params = new URLSearchParams({ limit: "600", symbol });
  const result = await api(`/api/events?${params.toString()}`);
  return result.items || [];
}

export async function fetchSetupChartQuotes(symbol, timeframe, fallbackEvents) {
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

export function historicalQuotesFromPayload(payload) {
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

export function setupChartTimeframeLabel(value) {
  const normalized = normalizeSetupChartTimeframe(value);
  const item = SETUP_CHART_TIMEFRAMES.find((option) => option.id === normalized);
  return item ? item.label : "1D";
}

export function renderSetupChartTimeframeControls() {
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

export function updateSetupChartTimeframeStatus(timeframe = setupChartTimeframe, quotes = null) {
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

export function renderSetupChartLegend(setup, quotes = [], timeframe = setupChartTimeframe) {
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

export function renderSetupChart(
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

export function setupChartStatusText(quotes = []) {
  const timeframe = setupChartState ? setupChartState.timeframe : setupChartTimeframe;
  const latest = quotes.length ? quotes[quotes.length - 1] : {};
  const barSize = latest.historical_bar_size || setupChartDataMeta.historical_bar_size || "";
  const parts = [`TF ${setupChartTimeframeLabel(timeframe)}`];
  if (barSize) parts.push(barSize);
  parts.push(`${quotes.length} bougies`);
  return parts.join(" - ");
}

export function drawSetupChartTimeframeLabel(ctx, quotes, colors) {
  ctx.save();
  ctx.fillStyle = colors.textSoft;
  ctx.font = "800 12px Inter, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(setupChartStatusText(quotes), 26, 18);
  ctx.restore();
}

export function wireSetupChartInteractions() {
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

export function handleSetupChartWheel(event) {
  if (!setupChartState || !setupChartState.quotes.length) return;
  event.preventDefault();
  const rect = event.currentTarget.getBoundingClientRect();
  const centerRatio = chartPointerRatio(event.clientX, rect);
  zoomSetupChart(event.deltaY < 0 ? 0.82 : 1.22, centerRatio);
}

export function handleSetupChartPointerDown(event) {
  if (!setupChartState || !setupChartState.layout || !setupChartState.quotes.length) return;
  if (!chartPointerInPlot(event.clientX, event.clientY, event.currentTarget)) return;
  setupChartState.dragging = true;
  setupChartState.dragStartX = event.clientX;
  setupChartState.dragStartVisibleStart = setupChartState.visibleStart;
  event.currentTarget.classList.add("dragging");
  if (event.currentTarget.setPointerCapture) event.currentTarget.setPointerCapture(event.pointerId);
  event.preventDefault();
}

export function handleSetupChartPointerMove(event) {
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

export function handleSetupChartPointerUp(event) {
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

export function handleSetupChartPointerLeave(event) {
  if (!setupChartState) return;
  if (!setupChartState.dragging) {
    setupChartState.hover = null;
    drawSetupChart(setupChartState.setup, setupChartState.quotes);
  }
  event.currentTarget.classList.remove("dragging");
}

export function zoomSetupChart(factor, centerRatio = 0.5) {
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

export function resetSetupChartViewport() {
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

export function chartPointerRatio(clientX, rect) {
  const layout = setupChartState && setupChartState.layout;
  if (!layout) return 0.5;
  return Math.min(Math.max((clientX - rect.left - layout.margins.left) / layout.plotWidth, 0), 1);
}

export function chartPointerInPlot(clientX, clientY, canvas) {
  const layout = setupChartState && setupChartState.layout;
  if (!layout) return false;
  const rect = canvas.getBoundingClientRect();
  return isPointInChartArea(clientX - rect.left, clientY - rect.top, layout);
}

export function isPointInChartArea(x, y, layout) {
  return x >= layout.margins.left
    && x <= layout.plotRight
    && y >= layout.margins.top
    && y <= layout.volumeBottom;
}

export function chartViewportAtLatest(state) {
  if (!state || !state.quotes || !state.quotes.length) return true;
  return state.visibleStart + state.visibleCount >= state.quotes.length;
}

export function defaultChartVisibleCount(total) {
  if (!total) return 0;
  return normalizeChartVisibleCount(Math.min(total, SETUP_CHART_INITIAL_VISIBLE_CANDLES), total);
}

export function normalizeChartVisibleCount(value, total) {
  if (!total) return 0;
  const minimum = Math.min(SETUP_CHART_MIN_VISIBLE_CANDLES, total);
  const count = Number.isFinite(Number(value)) ? Number(value) : defaultChartVisibleCount(total);
  return Math.min(Math.max(Math.round(count), minimum), total);
}

export function normalizeChartVisibleStart(value, total, visibleCount) {
  if (!total || !visibleCount) return 0;
  const maxStart = Math.max(0, total - visibleCount);
  const start = Number.isFinite(Number(value)) ? Number(value) : maxStart;
  return Math.min(Math.max(Math.round(start), 0), maxStart);
}

export function updateSetupChartRangeLabel() {
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

export function setupChartColors() {
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

export function drawSetupChart(setup, quotes) {
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

export function setupChartHeight() {
  return window.innerWidth < 720 ? 420 : 540;
}

export function drawEmptyChartText(ctx, width, height, colors) {
  ctx.fillStyle = colors.textSoft;
  ctx.font = "700 13px Inter, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("Aucune donnee marche a tracer", width / 2, height / 2);
  ctx.textAlign = "left";
}

export function drawPriceGrid(ctx, options) {
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

export function drawCandles(ctx, quotes, xForIndex, yForPrice, options) {
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

export function drawSetupPriceLevels(ctx, levels, latestPrice, yForPrice, options) {
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

export function drawLevelTags(ctx, labels, options) {
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

export function drawVolumeRatio(ctx, quotes, xForIndex, volumeMin, options) {
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

export function drawChartTimeAxis(ctx, quotes, options) {
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

export function drawChartCrosshair(ctx, quotes, yForPrice, layout, colors) {
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

export function roundRect(ctx, x, y, width, height, radius) {
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

export function formatChartTime(value) {
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

window.addEventListener("resize", () => {
  if (!setupChartState) return;
  window.clearTimeout(setupChartResizeTimer);
  setupChartResizeTimer = window.setTimeout(() => {
    drawSetupChart(setupChartState.setup, setupChartState.quotes);
  }, 120);
});
