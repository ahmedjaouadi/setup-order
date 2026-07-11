import { api } from "./api-client.js";
import { escapeHtml, formatTime, maybeMoney, maybePercent, toast } from "./ui-helpers.js";

export let marketContextState = { view: "WATCHLIST", heatmap: null, selectedSymbol: "" };

export let marketContextRefreshTimer = null;

export async function renderMarketContextPage() {
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

export function scheduleMarketContextRefresh() {
  if (!document.getElementById("market-context-heatmap")) return;
  window.clearTimeout(marketContextRefreshTimer);
  marketContextRefreshTimer = window.setTimeout(() => {
    renderMarketContextPage().catch((error) => toast(error.message));
  }, 500);
}

export function renderMarketContextHeatmap(data) {
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

export function renderMarketContextMarketMap(data) {
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

export function renderMarketContextMarketTile(node) {
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

export function marketContextSectorGroups(nodes) {
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

export function marketContextAverage(values) {
  const numbers = values
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter((value) => Number.isFinite(value));
  if (!numbers.length) return null;
  return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
}

export function marketContextIndustryWeight(industry) {
  return industry.nodes.reduce((sum, node) => sum + marketContextTileWeight(node), 0);
}

export function marketContextTileWeight(node) {
  const marketCap = Number(node.market_cap);
  if (Number.isFinite(marketCap) && marketCap > 0) {
    return Math.max(1, Math.min(9, Math.log10(marketCap) - 7));
  }
  const value = Number(node.value || 1);
  return Math.max(1, Math.min(6, value));
}

export function marketContextPerformanceTone(value) {
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

export function marketContextMapBadge(node) {
  if ((node.badges || []).includes("AUTO_ALLOWED")) return "AUTO";
  if ((node.badges || []).includes("WATCH_ONLY")) return "WATCH";
  if (node.metadata_status && node.metadata_status !== "SECTOR_OK") {
    return marketContextMetadataLabel(node.metadata_status);
  }
  return "SETUP";
}

export function marketContextNodeTitle(node) {
  const name = node.company_name ? `${node.label || node.id} - ${node.company_name}` : (node.label || node.id);
  const metadata = node.metadata_status ? ` | Metadata: ${marketContextMetadataLabel(node.metadata_status)}` : "";
  return `${name} | Perf stock 1D: ${maybePercent(node.performance)} | Score contexte: ${node.context_score ?? 0}${metadata}`;
}

export function renderMarketContextSectors(nodes) {
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

export async function renderMarketContextDetail(symbol) {
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

export function marketContextDisplaySector(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Non classé";
  const normalized = raw.toLowerCase();
  if (normalized === "unknown" || normalized === "non classe") return "Non classé";
  return raw;
}

export function displaySectorLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Secteur inconnu";
  const normalized = raw.toLowerCase();
  if (normalized === "unknown" || normalized === "non classe" || normalized === "non classã©") {
    return "Secteur inconnu";
  }
  return raw;
}

export function marketContextMetadataLabel(value) {
  const normalized = String(value || "SECTOR_UNKNOWN").trim().toUpperCase();
  if (normalized === "SECTOR_OK") return "sector ok";
  if (normalized === "SECTOR_MANUAL_OVERRIDE") return "manual override";
  if (normalized === "SECTOR_PROVIDER_MISSING") return "provider missing";
  if (normalized === "SECTOR_ETF_MISSING") return "sector ETF missing";
  if (normalized === "SECTOR_UNKNOWN") return "sector unknown";
  return normalized.replaceAll("_", " ").toLowerCase();
}

export function renderMarketContextEmptyDetail() {
  const container = document.getElementById("market-context-detail");
  if (!container) return;
  container.innerHTML = `<div class="market-context-empty">Aucun detail disponible.</div>`;
}

export function filterMarketContextNodes(nodes) {
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

export function wireMarketContextControls() {
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

export function marketContextTone(status) {
  if (status === "STRONG_CONTEXT") return "strong";
  if (status === "POSITIVE_CONTEXT") return "positive";
  if (status === "WEAK_CONTEXT") return "weak";
  if (status === "BLOCKED_OR_RISKY_CONTEXT") return "blocked";
  return "neutral";
}

export function marketContextToneFromScore(score) {
  if (score >= 60) return "strong";
  if (score >= 20) return "positive";
  if (score <= -60) return "blocked";
  if (score <= -20) return "weak";
  return "neutral";
}

export function marketContextBadgeLabel(badge) {
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
