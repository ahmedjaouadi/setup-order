import {
  SETUP_ENTRY_BLOCKING_STATUSES,
  analysisItemForSetup,
  displaySetupStatus,
  latestAnalysisForSetup,
  setupAutoExecutionEnabled,
  setupPriceAtPlacement,
  setupStatusReason,
} from "./setup-analysis.js";
import { forecastWatchlistBySymbol, latestSnapshot } from "./state.js";
import {
  emptyRow,
  escapeHtml,
  firstNumber,
  formatTime,
  maybePercent,
  money,
  numberOrNull,
  pnlClass,
  signalBadgeStyle,
  signedPercent,
  statusBadge,
} from "./ui-helpers.js";

export const SETUPS_COLUMNS_STORAGE_KEY = "setup-order:setups-columns";

export const SETUPS_TABLE_COLUMNS = [
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

export const DEFAULT_SETUPS_COLUMN_ORDER = SETUPS_TABLE_COLUMNS.map((column) => column.id);

export let setupsColumnOrder = loadSetupsColumnOrder();

export let setupsSearchQuery = "";

export const SETUP_NON_ARMABLE_STATUSES = new Set([
  "INVALIDATED",
  "EXPIRED",
  "STALE_SETUP",
  "MISSED_BREAKOUT_WAIT_RETEST",
]);

export const SETUP_REVALIDATION_REASON_LABELS = {
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

export function setupLastRevalidatedAt(setup) {
  return (setup && setup.last_revalidated_at) || "";
}

export function revalidationReasonLabel(reason) {
  const key = String(reason || "").trim().toUpperCase();
  if (!key) return "";
  return SETUP_REVALIDATION_REASON_LABELS[key] || key;
}

export function setupIsArmable(setup) {
  const status = String((setup && setup.status) || "").toUpperCase();
  return !SETUP_NON_ARMABLE_STATUSES.has(status);
}

export function formatRevalidatedAt(value) {
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

export function renderSetupRevalidationCell(setup) {
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

export function setupDetailPath(setup) {
  return `/setups/${encodeURIComponent(setup.setup_id || "")}`;
}

export function loadSetupsColumnOrder() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SETUPS_COLUMNS_STORAGE_KEY) || "[]");
    return normalizeSetupsColumnOrder(saved);
  } catch {
    return [...DEFAULT_SETUPS_COLUMN_ORDER];
  }
}

export function normalizeSetupsColumnOrder(order) {
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

export function saveSetupsColumnOrder() {
  try {
    window.localStorage.setItem(
      SETUPS_COLUMNS_STORAGE_KEY,
      JSON.stringify(setupsColumnOrder),
    );
  } catch {
    // localStorage can be blocked by the browser; column order still works for the session.
  }
}

export function orderedSetupsColumns() {
  const columnsById = new Map(SETUPS_TABLE_COLUMNS.map((column) => [column.id, column]));
  return setupsColumnOrder
    .map((id) => columnsById.get(id))
    .filter(Boolean);
}

export function renderSetupsColumnControls() {
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

export function renderSetupsColumnHeader(columns = orderedSetupsColumns()) {
  const head = document.getElementById("setups-head");
  if (!head) return columns;
  head.innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>`;
  return columns;
}

export function moveSetupsColumn(columnId, direction) {
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

export function reorderSetupsColumn(sourceId, targetId, insertAfter) {
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

export function resetSetupsColumns() {
  setupsColumnOrder = [...DEFAULT_SETUPS_COLUMN_ORDER];
  saveSetupsColumnOrder();
  renderSetupsColumnControls();
  renderSetups((latestSnapshot || {}).setups || []);
}

export function filterSetups(setups) {
  const query = setupsSearchQuery.trim().toLowerCase();
  if (!query) return setups;
  return setups.filter((setup) => setupSearchText(setup).includes(query));
}

export function setupSearchText(setup) {
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

export function setupInitialTrailingStop(setup) {
  const trailing = ((setup.config || {}).trailing_stop_loss || {});
  return firstNumber(trailing.initial_stop);
}

export function renderSetupsCount(visibleCount, totalCount) {
  const count = document.getElementById("setups-count");
  if (!count) return;
  count.textContent = setupsSearchQuery.trim()
    ? `${visibleCount} / ${totalCount} setups`
    : `${totalCount} setups`;
}

export function renderSetups(setups) {
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

export function setupForecastForSetup(setup) {
  const symbol = String((setup && setup.symbol) || "").toUpperCase();
  return symbol ? forecastWatchlistBySymbol[symbol] || null : null;
}

export function renderTimesfmScoreCell(forecast) {
  if (!forecast) return `<span class="forecast-mini muted">-</span>`;
  const status = forecast.forecast_status || forecast.status || "-";
  const score = forecast.metric_score ?? "-";
  return `<span class="forecast-mini ${escapeHtml(forecastTone(status))}">
    <strong>${escapeHtml(score)}</strong>
    <em>${escapeHtml(status)}</em>
  </span>`;
}

export function renderTimesfmMoveCell(forecast) {
  if (!forecast) return "";
  return `<span class="${pnlClass(forecast.expected_return_pct)}">${escapeHtml(signedPercent(forecast.expected_return_pct))}</span>`;
}

export function setupRowClass(setup) {
  const signal = setupSignalState(setup);
  if (signal.action === "ENTRY_READY") return "setup-row-ready";
  if (signal.score >= signal.nearReadyThreshold) return "setup-row-nearly-ready";
  return "";
}

export function renderSetupSignalCell(setup) {
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

export function renderSetupPriceCell(setup) {
  const price = setupPriceAtPlacement(setup);
  return price === null ? "" : money(price);
}

export function setupSignalState(setup) {
  const events = (latestSnapshot && Array.isArray(latestSnapshot.events))
    ? latestSnapshot.events
    : [];
  const analysis = latestAnalysisForSetup(setup, events);
  const item = analysisItemForSetup(setup, analysis);
  const trace = item && item.trace ? item.trace : null;
  return setupOpportunityState(setup, item, trace);
}

export function setupOpportunityState(setup, item, trace) {
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

export function opportunityScorePayload(item) {
  if (!item || typeof item !== "object") return null;
  const score = item.opportunity_score;
  return score && typeof score === "object" ? score : null;
}

export function analysisTraceScore(trace) {
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

export function normalizeCheckState(value) {
  const state = String(value || "wait").toLowerCase();
  if (["ok", "info", "wait", "bad", "error"].includes(state)) return state;
  if (state === "warn" || state === "waiting") return "wait";
  if (state === "blocked") return "bad";
  return "wait";
}

export function fallbackSetupProgress(setup) {
  if (SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status)) return 0;
  if (setup.status === "ENTRY_READY") return 1;
  if (setup.status === "WAITING_ENTRY_SIGNAL") return 0.82;
  if (setup.status === "WAITING_ACTIVATION") return 0.45;
  return 0.25;
}

export function wireSetupsColumnControls() {
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

export function forecastTone(status) {
  if (status === "BULLISH" || status === "NEUTRAL_BULLISH") return "good";
  if (status === "NEUTRAL") return "neutral";
  if (status === "WEAK" || status === "BEARISH") return "bad";
  return "muted";
}
