import { api } from "./api-client.js";
import { escapeHtml, formatAge, setText } from "./ui-helpers.js";

export function brokerPnlSourceLabel(metrics) {
  const source = metrics.broker_pnl_source || metrics.pnl_display_source || "-";
  const status = metrics.broker_pnl_status || "-";
  const age = metrics.broker_pnl_age_seconds == null ? "-" : formatAge(metrics.broker_pnl_age_seconds);
  const reason = metrics.broker_pnl_reason ? ` ${metrics.broker_pnl_reason}` : "";
  return `${source} ${status} ${age}${reason}`.trim();
}

export const DASH_PALETTE = ["#5b8cff", "#34d399", "#f5b547", "#fb7185", "#a78bfa", "#38bdf8", "#94a3b8"];

export const dashLastValues = new Map();

export const dashCountUpTimers = new Map();

export let dashEquityHistory = [];

export let dashLiveEquity = null;

export let dashLastUpdate = 0;

export let dashEquityTimer = null;

export let dashAgoTimer = null;

export let dashCurveDrawn = false;

export const dashCurrencyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function dashFormatCurrency(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return dashCurrencyFmt.format(number);
}

export function dashFormatSigned(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const sign = number > 0 ? "+" : "";
  return `${sign}${dashCurrencyFmt.format(number)}`;
}

export function initDashboardPremium() {
  fetchEquityHistory({ animate: true });
  if (dashEquityTimer) window.clearInterval(dashEquityTimer);
  dashEquityTimer = window.setInterval(() => fetchEquityHistory({ animate: false }), 60000);
  if (dashAgoTimer) window.clearInterval(dashAgoTimer);
  dashAgoTimer = window.setInterval(dashUpdatedAgoTick, 1000);
}

export async function fetchEquityHistory({ animate }) {
  try {
    const data = await api("/api/equity/history?limit=500");
    dashEquityHistory = Array.isArray(data.points) ? data.points : [];
    dashRedrawEquity({ animate: animate && !dashCurveDrawn });
    updateEquityLegend(data);
  } catch (error) {
    /* keep last drawn curve on transient errors */
  }
}

export function updateEquityLegend(data) {
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

export function dashSeries() {
  const series = dashEquityHistory
    .map((p) => Number(p.equity))
    .filter((v) => Number.isFinite(v));
  if (dashLiveEquity != null && Number.isFinite(Number(dashLiveEquity))) {
    const live = Number(dashLiveEquity);
    if (!series.length || series[series.length - 1] !== live) series.push(live);
  }
  return series;
}

export function dashRedrawEquity({ animate }) {
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

export function renderDashboardPremium(snapshot) {
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

export function dashSetMoney(id, value, opts = {}) {
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

export function dashCountUp(el, from, to, format) {
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

export function dashFlash(el, up) {
  el.classList.remove("flash-up", "flash-down");
  void el.offsetWidth;
  el.classList.add(up ? "flash-up" : "flash-down");
  window.setTimeout(() => el.classList.remove("flash-up", "flash-down"), 750);
}

export function dashUpdatedAgoTick() {
  const ago = document.getElementById("dash-updated-ago");
  const live = document.getElementById("dash-live");
  if (!dashLastUpdate) return;
  const seconds = Math.max(0, Math.round((Date.now() - dashLastUpdate) / 1000));
  if (ago) ago.textContent = seconds < 2 ? "a l'instant" : `il y a ${seconds}s`;
  if (live) live.dataset.state = seconds > 20 ? "stale" : "idle";
}

export function drawAllocationDonut(stockPnl) {
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
