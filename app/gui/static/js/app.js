const page = document.body.dataset.page;
let latestSnapshot = null;
let currentSetupConfig = null;
let setupConfigFormDirty = false;
let setupConfigEditorDirty = false;
let setupChartState = null;
let setupChartResizeTimer = null;
let setupChartInteractionsWired = false;

const SETUP_CHART_MIN_VISIBLE_CANDLES = 10;
const SETUP_CHART_INITIAL_VISIBLE_CANDLES = 60;
const SETUP_CHART_MAX_SOURCE_CANDLES = 180;
const SETUP_CHART_DEFAULT_TIMEFRAME = "1d";
const SETUP_CHART_TIMEFRAMES = [
  { id: "3m", label: "3mn" },
  { id: "10m", label: "10mn" },
  { id: "15m", label: "15mn" },
  { id: "30m", label: "30mn" },
  { id: "1h", label: "1h" },
  { id: "4h", label: "4h" },
  { id: "1d", label: "1D" },
];
let setupChartTimeframe = SETUP_CHART_DEFAULT_TIMEFRAME;
let setupChartDataMessage = "";
let setupChartDataMeta = {};

const CONFIG_FIELD_OPTIONS = {
  direction: ["long", "short"],
  mode: ["simulation", "paper", "live"],
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
    render: (setup) => statusBadge(setup.status),
  },
  {
    id: "entry_trigger",
    label: "Trigger",
    render: (setup) => setup.entry_trigger == null ? "" : money(setup.entry_trigger),
  },
  {
    id: "maximum_limit_price",
    label: "Limite max",
    render: (setup) => setup.maximum_limit_price == null ? "" : money(setup.maximum_limit_price),
  },
  {
    id: "protective_stop",
    label: "Stop protecteur",
    render: (setup) => setup.protective_stop == null ? "" : money(setup.protective_stop),
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
    id: "actions",
    label: "Actions",
    render: (setup) => {
      const enabled = Boolean(setup.enabled);
      const action = enabled ? "disable" : "enable";
      const state = enabled ? "ON" : "OFF";
      const title = enabled ? "Desactiver ce setup" : "Activer ce setup";
      return `
        <div class="row-actions">
          <a href="${setupDetailPath(setup)}">Voir</a>
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  const number = Number(value || 0);
  return number.toFixed(2);
}

function maybeMoney(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "-";
}

function maybePercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)}%` : "-";
}

function statusBadge(value) {
  const text = escapeHtml(value || "");
  return `<span class="status ${text}">${text}</span>`;
}

async function api(path, options = {}) {
  const request = { ...options };
  request.headers = { ...(request.headers || {}) };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = formatErrorDetail(data.detail);
    throw new Error(detail || response.statusText);
  }
  return data;
}

function formatErrorDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.join(", ");
  if (detail.errors) return detail.errors.join(", ");
  if (detail.detail) return formatErrorDetail(detail.detail);
  return JSON.stringify(detail);
}

function toast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.hidden = false;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    element.hidden = true;
  }, 3200);
}

function renderSnapshot(snapshot) {
  latestSnapshot = snapshot;
  renderRuntime(snapshot.runtime || {});
  renderEngineHealth(snapshot.health || {});
  renderMetrics(snapshot.metrics || {});
  renderDashboard(snapshot);
  renderSetups(snapshot.setups || []);
  renderOrders(snapshot.orders || []);
  renderPositions(snapshot.positions || []);
  renderEvents("dashboard-events", snapshot.events || []);
  renderSettings(snapshot);
}

function renderRuntime(runtime) {
  const modeLabel = document.getElementById("mode-label");
  if (modeLabel) modeLabel.textContent = runtime.mode_label || runtime.mode || "simulation";
  setStatus("connection-status", runtime.connection_label || runtime.connection || "DISCONNECTED");
  setStatus("bot-status", runtime.status_label || runtime.status || "PAUSED");
}

function setStatus(id, value) {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = value;
  element.className = "pill";
  if (["CONNECTED", "RUNNING", "SIMULATED", "SIM RUNNING"].includes(value)) {
    element.classList.add("ok");
  }
  if (["PAUSED", "WAITING"].includes(value)) element.classList.add("warn");
  if (["DISCONNECTED", "ERROR", "EMERGENCY_STOP"].includes(value)) {
    element.classList.add("danger");
  }
}

function renderEngineHealth(health) {
  const heartbeatAge = secondsSince(health.last_heartbeat_at) ?? health.heartbeat_age_seconds;
  const tickAge = secondsSince(health.last_market_tick_at) ?? health.market_tick_age_seconds;
  const analysisAge = secondsSince(health.last_market_analysis_at)
    ?? health.market_analysis_age_seconds;
  const stockPollAge = secondsSince(health.last_stock_poll_at) ?? health.stock_poll_age_seconds;
  const staleAfter = Number(health.heartbeat_stale_seconds || 45);
  let status = health.status || "STARTING";
  let label = health.label || "CHECKING";
  const brokerStatus = health.broker_status || "";
  if (brokerStatus === "DISCONNECTED" || brokerStatus === "ERROR") {
    status = "BROKER_DOWN";
    label = brokerStatus === "ERROR" ? "BROKER ERROR" : "TWS OFFLINE";
  } else if (health.last_error) {
    status = "ERROR";
    label = "HEARTBEAT ERROR";
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
  }

  const detail = document.getElementById("dashboard-engine-health");
  if (!detail) return;
  detail.innerHTML = dlRows({
    Etat: status,
    "Etat broker": brokerStatus || "-",
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

function timeWithAge(value, seconds) {
  if (!value) return "-";
  const age = formatAge(seconds);
  return age === "-" ? formatTime(value) : `${formatTime(value)} (${age})`;
}

function formatAge(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${Math.max(value, 0)}s`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function secondsSince(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(Math.floor((Date.now() - date.getTime()) / 1000), 0);
}

function renderMetrics(metrics) {
  const account = metrics.account || {};
  setText("metric-account-value", maybeMoney(account.net_liquidation));
  setText("metric-cash", maybeMoney(account.cash ?? account.available_funds));
  setText("metric-pnl-yesterday", maybeMoney(metrics.pnl_until_yesterday));
  setText("metric-pnl-today", maybeMoney(metrics.today_pnl));
  setText("metric-active-setups", metrics.active_setups);
  setText("metric-open-positions", metrics.open_positions);
  setText("metric-open-orders", metrics.open_orders);
  setText("metric-positions-pnl", maybeMoney(metrics.positions_pnl));
  setText("metric-loss-remaining", money(metrics.daily_loss_remaining));
  setPnlTone("metric-pnl-yesterday", metrics.pnl_until_yesterday);
  setPnlTone("metric-pnl-today", metrics.today_pnl);
  setPnlTone("metric-positions-pnl", metrics.positions_pnl);
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value ?? "";
}

function setPnlTone(id, value) {
  const element = document.getElementById(id);
  if (!element) return;
  const number = Number(value);
  element.classList.remove("money-positive", "money-negative", "money-flat");
  if (!Number.isFinite(number)) return;
  if (number > 0) element.classList.add("money-positive");
  else if (number < 0) element.classList.add("money-negative");
  else element.classList.add("money-flat");
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
    setup.protective_stop,
    setup.maximum_quantity,
    setup.maximum_risk,
    setup.reconciliation_status,
    setup.enabled ? "enabled on actif" : "disabled off inactif",
  ].map((value) => String(value ?? "").toLowerCase()).join(" ");
}

function renderSetupsCount(visibleCount, totalCount) {
  const count = document.getElementById("setups-count");
  if (!count) return;
  count.textContent = setupsSearchQuery.trim()
    ? `${visibleCount} / ${totalCount} setups`
    : `${totalCount} setups`;
}

function renderDashboard(snapshot) {
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

function pnlClass(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "money-flat";
  if (number > 0) return "money-positive";
  if (number < 0) return "money-negative";
  return "money-flat";
}

function renderSetups(setups) {
  const tbody = document.getElementById("setups-table");
  if (!tbody) return;
  const columns = renderSetupsColumnHeader();
  const filteredSetups = filterSetups(setups);
  renderSetupsCount(filteredSetups.length, setups.length);
  tbody.innerHTML = filteredSetups.map((setup) => `
    <tr>
      ${columns.map((column) => `<td>${column.render(setup)}</td>`).join("")}
    </tr>
  `).join("") || emptyRow(
    columns.length,
    setupsSearchQuery.trim() ? "Aucun setup ne correspond a la recherche" : "Aucun setup",
  );
}

function renderOrders(orders) {
  const tbody = document.getElementById("orders-table");
  if (!tbody) return;
  tbody.innerHTML = orders.map((order) => `
    <tr>
      <td>${escapeHtml(order.id)}</td>
      <td>${escapeHtml(order.symbol)}</td>
      <td>${escapeHtml(order.setup_id)}</td>
      <td>${escapeHtml(order.side)}</td>
      <td>${escapeHtml(order.order_type)}</td>
      <td>${escapeHtml(order.quantity)}</td>
      <td>${statusBadge(order.status)}</td>
      <td>${escapeHtml(order.broker_order_id || "")}</td>
      <td>
        <div class="row-actions">
          ${order.status === "SUBMITTED" ? `<button type="button" data-action="fill" data-order="${escapeHtml(order.id)}">Fill</button>` : ""}
          ${order.status === "SUBMITTED" ? `<button class="danger-small" type="button" data-action="cancel-order" data-order="${escapeHtml(order.id)}">Cancel</button>` : ""}
          ${canDeleteOrder(order) ? `<button class="danger-small" type="button" data-action="delete-order" data-order="${escapeHtml(order.id)}" data-symbol="${escapeHtml(order.symbol)}">Suppr</button>` : ""}
        </div>
      </td>
    </tr>
  `).join("") || emptyRow(9, "Aucun ordre");
}

function canDeleteOrder(order) {
  return ["REJECTED", "CANCELLED", "FILLED", "ERROR"].includes(order.status);
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
      || "simulated";
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

function dlRows(values, labels = {}) {
  return Object.entries(values).map(([key, value]) => `
    <dt>${escapeHtml(labels[key] || key)}</dt>
    <dd>${escapeHtml(formatDetailValue(value))}</dd>
  `).join("");
}

function formatDetailValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

function emptyRow(span, text) {
  return `<tr><td colspan="${span}">${escapeHtml(text)}</td></tr>`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

async function refresh() {
  const snapshot = await api("/api/dashboard");
  renderSnapshot(snapshot);
}

function connectWebSocket() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "snapshot") renderSnapshot(event.payload);
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

function onClick(id, handler) {
  const element = document.getElementById(id);
  if (!element) return;
  element.addEventListener("click", async () => {
    try {
      await handler();
    } catch (error) {
      toast(error.message);
    }
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
  const previewButton = document.getElementById("setup-preview-button");
  if (previewButton) {
    previewButton.addEventListener("click", async () => {
      try {
        const result = await api("/api/setups/convert-text", {
          method: "POST",
          body: setupTextPayload(form),
        });
        renderSetupPreview(result);
      } catch (error) {
        renderSetupPreviewError(error.message);
      }
    });
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await api("/api/setups/from-text", {
        method: "POST",
        body: setupTextPayload(form),
      });
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

function formData(form) {
  const data = {};
  for (const [key, value] of new FormData(form).entries()) {
    data[key] = value;
  }
  return data;
}

function wireActionButtons() {
  document.body.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    try {
      if (action === "enable" || action === "disable") {
        await api(`/api/setups/${encodeURIComponent(button.dataset.setup)}/${action}`, {
          method: "POST",
        });
        await refresh();
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
      if (action === "cancel-order") {
        await api(`/api/orders/${encodeURIComponent(button.dataset.order)}/cancel`, {
          method: "POST",
        });
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
        const price = window.prompt("Fill price");
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
    } catch (error) {
      toast(error.message);
    }
  });
}

const SETUP_ENTRY_BLOCKING_STATUSES = new Set([
  "CANCELLED",
  "COMPLETED",
  "DELETED",
  "DISABLED",
  "EMERGENCY_STOP",
  "EXPIRED",
  "FILLED",
  "IN_POSITION",
  "INVALIDATED",
  "MANAGING_POSITION",
  "REJECTED",
]);

async function fetchSetupSymbolEvents(symbol) {
  if (!symbol) return [];
  const params = new URLSearchParams({ limit: "600", symbol });
  const result = await api(`/api/events?${params.toString()}`);
  return result.items || [];
}

async function fetchSetupChartQuotes(symbol, timeframe, fallbackEvents) {
  setupChartDataMessage = "";
  setupChartDataMeta = {};
  const fallbackQuotes = extractQuoteEvents(fallbackEvents);
  const normalized = normalizeSetupChartTimeframe(timeframe);
  setupChartDataMeta = {
    timeframe: normalized,
    timeframe_label: setupChartTimeframeLabel(normalized),
  };
  if (!symbol) return fallbackQuotes;
  const params = new URLSearchParams({ timeframe: normalized });
  try {
    const result = await api(`/api/market/history/${encodeURIComponent(symbol)}?${params.toString()}`);
    setupChartDataMeta = result || setupChartDataMeta;
    const quotes = historicalQuotesFromPayload(result);
    if (quotes.length) return quotes.slice(-SETUP_CHART_MAX_SOURCE_CANDLES);
    setupChartDataMessage = result.message
      || `Aucune bougie ${setupChartTimeframeLabel(normalized)} disponible`;
  } catch (error) {
    setupChartDataMessage = error.message;
  }
  if (normalized === SETUP_CHART_DEFAULT_TIMEFRAME && fallbackQuotes.length) {
    const latest = fallbackQuotes[fallbackQuotes.length - 1];
    setupChartDataMeta = {
      ...setupChartDataMeta,
      historical_bar_size: latest.historical_bar_size,
      historical_duration: latest.historical_duration,
      source: latest.source || "events",
    };
    return fallbackQuotes;
  }
  return [];
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

function normalizeSetupChartTimeframe(value) {
  const normalized = String(value || SETUP_CHART_DEFAULT_TIMEFRAME).trim().toLowerCase();
  const aliases = {
    "3mn": "3m",
    "3min": "3m",
    "10mn": "10m",
    "10min": "10m",
    "15mn": "15m",
    "15min": "15m",
    "30mn": "30m",
    "30min": "30m",
    "60m": "1h",
    "60mn": "1h",
    "1 hour": "1h",
    "4 hours": "4h",
    "1 day": "1d",
    "1D": "1d",
  };
  const id = aliases[normalized] || normalized;
  return SETUP_CHART_TIMEFRAMES.some((item) => item.id === id)
    ? id
    : SETUP_CHART_DEFAULT_TIMEFRAME;
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

function wireSetupChartTimeframeControls() {
  const container = document.getElementById("setup-chart-timeframes");
  if (!container) return;
  container.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-chart-timeframe]");
    if (!button) return;
    const nextTimeframe = normalizeSetupChartTimeframe(button.dataset.chartTimeframe);
    if (nextTimeframe === setupChartTimeframe) return;
    setupChartTimeframe = nextTimeframe;
    renderSetupChartTimeframeControls();
    try {
      await renderSetupDetail();
    } catch (error) {
      toast(error.message);
    }
  });
}

function renderSetupDetailSummary(setup) {
  const summary = document.getElementById("setup-detail-summary");
  if (!summary) return;
  const config = setup.config || {};
  const entry = config.entry || {};
  const breakout = config.breakout || {};
  const risk = config.risk || {};
  const management = config.management || {};
  const stopManagement = management.stop_management || config.stop_management || {};
  const positionSource = config.position_source || {};
  const targets = Array.isArray(management.targets)
    ? management.targets
    : (Array.isArray(config.targets) ? config.targets : []);
  summary.innerHTML = dlRows(removeUndefinedValues({
    symbol: setup.symbol,
    setup_id: setup.setup_id,
    setup_type: setup.setup_type,
    setup_role: setup.setup_role,
    direction: config.direction,
    mode: config.mode,
    enabled_db: setup.enabled,
    enabled_config: config.enabled,
    status: setup.status,
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
    protective_stop: setup.protective_stop,
    initial_stop_loss: risk.initial_stop_loss,
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
    created_at: setup.created_at,
    updated_at: setup.updated_at,
  }), {
    enabled_db: "enabled runtime",
    enabled_config: "enabled config",
    entry_enabled: "entry enabled",
    maximum_limit_price: "maximum limit price",
    worst_case_entry_price: "worst case entry",
    protective_stop: "protective stop",
    initial_stop_loss: "initial stop loss",
    maximum_quantity: "maximum quantity",
    maximum_risk: "maximum risk",
    max_risk_usd: "max risk usd",
    max_position_amount_usd: "max position amount usd",
    relative_strength_required: "relative strength required",
    volume_required: "volume above average",
  });
}

function renderSetupEntryPlan(setup, latestQuote) {
  const container = document.getElementById("setup-entry-plan");
  if (!container) return;
  const levels = setupTradeLevels(setup);
  const readiness = entryReadiness(setup, latestQuote);
  const price = quotePrice(latestQuote);
  const volumeRatio = numberOrNull(latestQuote && latestQuote.volume_ratio);
  const currentDistanceToStop = price !== null && levels.stop !== null
    ? Math.max(price - levels.stop, 0)
    : null;
  const worstCaseRiskPerShare = levels.limit !== null && levels.stop !== null
    ? Math.max(levels.limit - levels.stop, 0)
    : null;
  const entry = ((setup.config || {}).entry || {});
  container.innerHTML = dlRows(removeUndefinedValues({
    decision: readiness.label,
    missing: readiness.missing.length ? readiness.missing.join(" | ") : "OK",
    current_price: maybeMoney(price),
    resistance: maybeMoney(levels.resistance),
    trigger_price: maybeMoney(levels.trigger),
    trigger_offset: numberText(levels.triggerOffset, 3),
    limit_price: maybeMoney(levels.limit),
    limit_offset: numberText(levels.limitOffset, 3),
    stop: maybeMoney(levels.stop),
    current_distance_to_stop: maybeMoney(currentDistanceToStop),
    worst_case_risk_per_share: maybeMoney(worstCaseRiskPerShare),
    volume_ratio: numberText(volumeRatio, 3),
    volume_required: numberText(levels.volumeMin, 3),
    cancel_after_minutes: entry.cancel_if_not_filled_after_minutes,
    last_quote: latestQuote ? formatTime(latestQuote.timestamp) : "-",
  }), {
    current_price: "current price",
    trigger_price: "trigger price",
    limit_price: "limit price",
    current_distance_to_stop: "current distance to stop",
    worst_case_risk_per_share: "worst case risk/share",
    volume_required: "volume required",
    cancel_after_minutes: "cancel after minutes",
    last_quote: "last quote",
  });
}

function renderSetupConditionGrid(setup, latestQuote) {
  const container = document.getElementById("setup-condition-grid");
  if (!container) return;
  const config = setup.config || {};
  const entry = config.entry || {};
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = numberOrNull(latestQuote && latestQuote.volume_ratio);
  const setupEnabled = Boolean(setup.enabled) && config.enabled !== false;
  const entryEnabled = entry.enabled !== false;
  const priceReady = price !== null && levels.resistance !== null && price >= levels.resistance;
  const volumeReady = volumeRatio !== null && levels.volumeMin !== null && volumeRatio >= levels.volumeMin;
  const status = setup.status || "-";
  const blockedStatus = SETUP_ENTRY_BLOCKING_STATUSES.has(status);
  const conditions = [
    {
      label: "Setup",
      value: setupEnabled ? "ON" : "OFF",
      state: setupEnabled ? "ok" : "bad",
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
      value: `${numberText(volumeRatio, 2)} / ${numberText(levels.volumeMin, 2)}`,
      state: volumeReady ? "ok" : "warn",
    },
    {
      label: "Statut",
      value: status,
      state: blockedStatus ? "bad" : setupStatusTone(status),
    },
  ];
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
    ["Volume ratio", numberText(latestQuote && latestQuote.volume_ratio, 3)],
    ["Bar size", latestQuote && latestQuote.historical_bar_size
      ? latestQuote.historical_bar_size
      : (setupChartDataMeta.historical_bar_size || "-")],
    ["Bar date", latestQuote && latestQuote.bar_date ? latestQuote.bar_date : "-"],
    ["Source", latestQuote && latestQuote.source ? latestQuote.source : "-"],
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

function renderSetupAnalysisPanel(setup, symbolEvents, latestQuote) {
  const overview = document.getElementById("setup-analysis-overview");
  const checks = document.getElementById("setup-analysis-checks");
  const timeline = document.getElementById("setup-analysis-timeline");
  if (!overview && !checks && !timeline) return;

  const analysis = latestAnalysisForSetup(setup, symbolEvents);
  const item = analysisItemForSetup(setup, analysis);
  const trace = item && item.trace
    ? item.trace
    : fallbackAnalysisTrace(setup, latestQuote, item);
  const snapshot = analysisSnapshot(analysis) || latestQuote || {};
  const decision = item || setupAnalysisDecision(setup, analysis);
  const action = decision.action || "-";
  const reason = decision.reason || trace.summary || "Aucune analyse recente";
  const nextStep = trace.next_step || nextStepFromAction(action, reason);
  const bid = numberOrNull(snapshot.bid);
  const ask = numberOrNull(snapshot.ask);
  const spread = bid !== null && ask !== null ? Math.max(ask - bid, 0) : null;
  const midPrice = bid !== null && ask !== null ? (bid + ask) / 2 : null;
  const spreadBps = spread !== null && midPrice !== null && midPrice > 0
    ? (spread / midPrice) * 10000
    : null;

  if (overview) {
    overview.innerHTML = [
      ["Phase", trace.phase || setup.status || "-"],
      ["Decision moteur", action],
      ["Raison", reason],
      ["Prochaine action", nextStep],
      ["Derniere analyse", analysis ? formatTime(analysis.timestamp) : "-"],
      ["Dernier prix analyse", maybeMoney(firstNumber(snapshot.price, snapshot.close))],
      ["Bid", maybeMoney(snapshot.bid)],
      ["Ask", maybeMoney(snapshot.ask)],
      ["Bougie", setupAnalysisCandleText(snapshot)],
      ["Volume ferme", numberText(firstNumber(snapshot.volume_ratio_closed_bar, snapshot.volume_ratio), 3)],
      ["Volume live", numberText(snapshot.volume_ratio_live, 3)],
      ["Session", snapshot.session || "-"],
      ["Spread", maybeMoney(spread)],
      ["Spread bps", numberText(spreadBps, 2)],
      ["ATR 15m", maybeMoney(snapshot.atr_15m)],
      ["ATR 1h", maybeMoney(snapshot.atr_1h)],
      ["Bougies au-dessus seuil", snapshot.bars_above_resistance ?? "-"],
    ].map(([label, value]) => `
      <div class="analysis-cell">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(formatDetailValue(value))}</strong>
      </div>
    `).join("");
  }

  if (checks) {
    const checkItems = Array.isArray(trace.checks) ? trace.checks : [];
    checks.innerHTML = checkItems.map((check) => renderAnalysisCheck(check)).join("")
      || `<article class="analysis-check info">
        <span class="analysis-check-state">INFO</span>
        <div><strong>Aucune trace detaillee</strong></div>
        <p>Le prochain scan stock remplira cette section.</p>
      </article>`;
  }

  if (timeline) {
    const events = analysisTimelineEvents(setup, symbolEvents).slice(0, 8);
    timeline.innerHTML = events.map((event) => {
      const eventItem = analysisItemForSetup(setup, event);
      const eventAction = eventItem ? eventItem.action : event.event_type;
      const eventReason = eventItem ? eventItem.reason : event.message;
      return `
        <article class="analysis-event">
          <time class="analysis-event-meta">${escapeHtml(formatTime(event.timestamp))}</time>
          <span class="analysis-event-meta">${escapeHtml(eventAction || event.level || "-")}</span>
          <div>
            <strong>${escapeHtml(event.event_type)}</strong>
            <div>${escapeHtml(eventReason || event.message || "-")}</div>
          </div>
        </article>
      `;
    }).join("") || `<article class="analysis-event">
      <span class="analysis-event-meta">-</span>
      <span class="analysis-event-meta">INFO</span>
      <div><strong>Aucune analyse</strong><div>Aucun evenement d'analyse pour ce setup.</div></div>
    </article>`;
  }
}

function renderAnalysisCheck(check) {
  const state = normalizeAnalysisState(check.state);
  const stateLabel = {
    ok: "OK",
    wait: "Attente",
    bad: "Bloque",
    info: "Info",
  }[state] || "Info";
  const detailParts = [];
  if (check.actual !== undefined) detailParts.push(`Actuel: ${formatDetailValue(check.actual)}`);
  if (check.expected !== undefined) detailParts.push(`Attendu: ${formatDetailValue(check.expected)}`);
  if (check.detail) detailParts.push(check.detail);
  return `
    <article class="analysis-check ${escapeHtml(state)}">
      <span class="analysis-check-state">${escapeHtml(stateLabel)}</span>
      <div>
        <span>${escapeHtml(check.label || "Condition")}</span>
        <strong>${escapeHtml(check.summary || check.label || "Condition")}</strong>
      </div>
      <p>${escapeHtml(detailParts.join(" | ") || "-")}</p>
    </article>
  `;
}

function normalizeAnalysisState(value) {
  const state = String(value || "info").toLowerCase();
  if (["ok", "wait", "bad", "info"].includes(state)) return state;
  if (state === "warn" || state === "waiting") return "wait";
  if (state === "error" || state === "blocked") return "bad";
  return "info";
}

function fallbackAnalysisTrace(setup, latestQuote, item) {
  const readiness = entryReadiness(setup, latestQuote);
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = numberOrNull(latestQuote && latestQuote.volume_ratio);
  const checks = [
    {
      label: "Setup actif",
      state: setup.enabled && (setup.config || {}).enabled !== false ? "ok" : "bad",
      actual: setup.enabled ? "ON" : "OFF",
      expected: "ON",
    },
    {
      label: "Statut suivi",
      state: SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status) ? "bad" : "wait",
      actual: setup.status,
      expected: "statut non terminal",
    },
    {
      label: "Prix vs resistance",
      state: levels.resistance === null ? "info" : (price !== null && price >= levels.resistance ? "ok" : "wait"),
      actual: maybeMoney(price),
      expected: levels.resistance === null ? "non renseigne" : maybeMoney(levels.resistance),
    },
    {
      label: "Volume relatif",
      state: levels.volumeMin === null ? "info" : (volumeRatio !== null && volumeRatio >= levels.volumeMin ? "ok" : "wait"),
      actual: numberText(volumeRatio, 3),
      expected: numberText(levels.volumeMin, 3),
    },
    {
      label: "Signal entree",
      state: readiness.missing.length ? "wait" : "ok",
      actual: readiness.missing.length ? readiness.missing.join(" | ") : "conditions locales OK",
      expected: "ENTRY_READY",
    },
  ];
  return {
    phase: setup.status || "Surveillance",
    summary: item && item.reason ? item.reason : readiness.label,
    next_step: item ? nextStepFromAction(item.action, item.reason) : "Attendre le prochain scan stock.",
    checks,
  };
}

function setupAnalysisCandleText(snapshot) {
  if (!snapshot) return "-";
  const open = maybeMoney(snapshot.open);
  const high = maybeMoney(snapshot.high);
  const low = maybeMoney(snapshot.low);
  const close = maybeMoney(snapshot.close);
  return `O ${open} H ${high} L ${low} C ${close}`;
}

function nextStepFromAction(action, reason) {
  if (action === "ENTRY_READY") return "Verifier le risque puis envoyer l'ordre d'entree.";
  if (action === "STATUS_CHANGE") return "Changer de phase et continuer la surveillance.";
  if (action === "INVALIDATE") return "Invalider le setup.";
  if (action === "RAISE_STOP") return "Monter le stop de protection.";
  return reason ? `Continuer a surveiller: ${reason}` : "Attendre le prochain scan stock.";
}

function analysisTimelineEvents(setup, events) {
  const interestingTypes = new Set([
    "stock_analysis",
    "stock_analysis_skipped",
    "stock_quote_missing",
    "entry_rejected_by_risk",
    "entry_signal_rejected",
    "entry_order_submitted",
    "entry_order_rejected",
    "duplicate_order_blocked",
    "broker_mode_mismatch",
    "setup_status_changed",
  ]);
  return (events || []).filter((event) => {
    if (!interestingTypes.has(event.event_type)) return false;
    if (event.setup_id && event.setup_id === setup.setup_id) return true;
    if (event.symbol && event.symbol === setup.symbol) {
      if (event.event_type !== "stock_analysis") return true;
      return Boolean(analysisItemForSetup(setup, event));
    }
    return false;
  });
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

function setButtonDisabled(id, disabled) {
  const button = document.getElementById(id);
  if (button) button.disabled = Boolean(disabled);
}

function extractQuoteEvents(events) {
  const quoteEvents = (events || []).filter((event) => event.event_type === "stock_quote");
  const eventWithBars = quoteEvents.find((event) => {
    const bars = event.data && event.data.historical_bars;
    return Array.isArray(bars) && bars.length;
  });
  if (eventWithBars) {
    return addVolumeRatios(
      historicalBarsFromEvent(eventWithBars)
        .filter(Boolean)
        .sort(compareQuotesByTime),
    );
  }
  const rawQuotes = quoteEvents
    .map((event) => quoteFromEvent(event))
    .filter(Boolean)
    .sort(compareQuotesByTime);
  const uniqueQuotes = dedupeQuotes(rawQuotes);
  if (shouldUseSnapshotCandles(rawQuotes, uniqueQuotes)) {
    return quoteSnapshotsToCandles(rawQuotes);
  }
  return uniqueQuotes;
}

function historicalBarsFromEvent(event) {
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const bars = Array.isArray(data.historical_bars) ? data.historical_bars : [];
  return bars.map((bar) => quoteFromHistoricalBar(event, bar, data));
}

function quoteFromHistoricalBar(event, bar, eventData) {
  if (!bar || typeof bar !== "object") return null;
  const close = firstNumber(bar.close);
  const open = firstNumber(bar.open, close);
  const high = firstNumber(bar.high, Math.max(open ?? 0, close ?? 0));
  const low = firstNumber(bar.low, Math.min(open ?? 0, close ?? 0));
  if ([open, high, low, close].some((value) => value === null)) return null;
  return {
    timestamp: bar.date || event.timestamp,
    open,
    high,
    low,
    close,
    price: close,
    bid: numberOrNull(eventData.bid),
    ask: numberOrNull(eventData.ask),
    volume: numberOrNull(bar.volume),
    volume_ratio: numberOrNull(bar.volume_ratio),
    volume_ratio_closed_bar: numberOrNull(bar.volume_ratio_closed_bar),
    volume_ratio_live: numberOrNull(bar.volume_ratio_live),
    average_volume_ratio_last_2_bars: numberOrNull(eventData.average_volume_ratio_last_2_bars),
    bars_above_resistance: numberOrNull(eventData.bars_above_resistance),
    minimum_tick: numberOrNull(eventData.minimum_tick),
    atr_15m: numberOrNull(eventData.atr_15m),
    atr_1h: numberOrNull(eventData.atr_1h),
    session: eventData.session || "",
    source: eventData.market_data_source || eventData.source || "",
    bar_date: bar.date || "",
    timeframe: eventData.timeframe || "",
    timeframe_label: eventData.timeframe_label || "",
    historical_bar_size: eventData.historical_bar_size || "",
    historical_duration: eventData.historical_duration || "",
  };
}

function quoteFromEvent(event) {
  const data = event.data && typeof event.data === "object" ? event.data : {};
  if (data.available === false) return null;
  const close = firstNumber(data.close, data.price, data.last);
  const open = firstNumber(data.open, close);
  const high = firstNumber(data.high, Math.max(open ?? 0, close ?? 0));
  const low = firstNumber(data.low, Math.min(open ?? 0, close ?? 0));
  if ([open, high, low, close].some((value) => value === null)) return null;
  return {
    timestamp: event.timestamp,
    open,
    high,
    low,
    close,
    price: firstNumber(data.price, data.last, close),
    bid: numberOrNull(data.bid),
    ask: numberOrNull(data.ask),
    volume: numberOrNull(data.volume),
    volume_ratio: numberOrNull(data.volume_ratio),
    volume_ratio_closed_bar: numberOrNull(data.volume_ratio_closed_bar),
    volume_ratio_live: numberOrNull(data.volume_ratio_live),
    average_volume_ratio_last_2_bars: numberOrNull(data.average_volume_ratio_last_2_bars),
    bars_above_resistance: numberOrNull(data.bars_above_resistance),
    minimum_tick: numberOrNull(data.minimum_tick),
    atr_15m: numberOrNull(data.atr_15m),
    atr_1h: numberOrNull(data.atr_1h),
    session: data.session || "",
    source: data.market_data_source || data.source || "",
    bar_date: data.bar_date || data.date || "",
    timeframe: data.timeframe || "",
    timeframe_label: data.timeframe_label || "",
    historical_bar_size: data.historical_bar_size || "",
    historical_duration: data.historical_duration || "",
  };
}

function latestQuoteFromEvents(events) {
  const event = (events || []).find((item) => item.event_type === "stock_quote");
  return event ? quoteFromEvent(event) : null;
}

function shouldUseSnapshotCandles(rawQuotes, uniqueQuotes) {
  if (rawQuotes.length < 6) return false;
  const hasHistoricalSnapshots = rawQuotes.some((quote) => quote.source === "historical");
  if (!hasHistoricalSnapshots) return false;
  const uniqueBarDates = new Set(rawQuotes.map((quote) => quote.bar_date).filter(Boolean));
  return uniqueQuotes.length <= 2 || uniqueBarDates.size <= 1;
}

function quoteSnapshotsToCandles(rawQuotes) {
  const seen = new Set();
  const ordered = rawQuotes.filter((quote) => {
    const key = quote.timestamp || `${quote.bar_date}:${quote.price}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return quotePrice(quote) !== null;
  });
  return addVolumeRatios(ordered.map((quote, index) => {
    const close = quotePrice(quote);
    const previousClose = index > 0 ? quotePrice(ordered[index - 1]) : close;
    const open = previousClose ?? close;
    return {
      ...quote,
      open,
      high: Math.max(open, close),
      low: Math.min(open, close),
      close,
      price: close,
      synthetic: true,
    };
  }));
}

function dedupeQuotes(quotes) {
  const byKey = new Map();
  quotes.forEach((quote) => {
    byKey.set(quoteCandleKey(quote), quote);
  });
  return addVolumeRatios(Array.from(byKey.values()).sort(compareQuotesByTime));
}

function quoteCandleKey(quote) {
  if (quote.bar_date) return `bar:${quote.bar_date}`;
  if (quote.source === "historical") return `historical:${quote.timestamp}`;
  return `tick:${quote.timestamp}`;
}

function addVolumeRatios(quotes) {
  return quotes.map((quote, index) => {
    if (quote.volume_ratio !== null && quote.volume_ratio !== undefined) return quote;
    const volume = numberOrNull(quote.volume);
    if (volume === null || volume <= 0 || index === 0) return quote;
    const previousVolumes = quotes
      .slice(Math.max(0, index - 20), index)
      .map((item) => numberOrNull(item.volume))
      .filter((value) => value !== null && value > 0);
    if (!previousVolumes.length) return quote;
    const average = previousVolumes.reduce((sum, value) => sum + value, 0) / previousVolumes.length;
    if (average <= 0) return quote;
    return {
      ...quote,
      volume_ratio: volume / average,
    };
  });
}

function compareQuotesByTime(left, right) {
  return quoteSortTime(left) - quoteSortTime(right);
}

function quoteSortTime(quote) {
  if (!quote) return 0;
  const time = parseChartDate(quote.bar_date || quote.timestamp || 0).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function latestAnalysisForSetup(setup, events) {
  return (events || []).find((event) => {
    if (event.event_type !== "stock_analysis") return false;
    if (event.setup_id === setup.setup_id) return true;
    const data = event.data && typeof event.data === "object" ? event.data : {};
    const processed = Array.isArray(data.processed) ? data.processed : [];
    if (!processed.length) return event.symbol === setup.symbol;
    return processed.some((item) => item.setup_id === setup.setup_id || item.symbol === setup.symbol);
  }) || null;
}

function analysisItemForSetup(setup, event) {
  if (!event) return null;
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const processed = Array.isArray(data.processed) ? data.processed : [];
  if (!processed.length) return null;
  return processed.find((candidate) => (
    candidate.setup_id === setup.setup_id || candidate.symbol === setup.symbol
  )) || null;
}

function analysisSnapshot(event) {
  const data = event && event.data && typeof event.data === "object" ? event.data : {};
  return data.snapshot && typeof data.snapshot === "object" ? data.snapshot : null;
}

function setupAnalysisDecision(setup, event) {
  if (!event) return {};
  const data = event.data && typeof event.data === "object" ? event.data : {};
  const processed = Array.isArray(data.processed) ? data.processed : [];
  const item = analysisItemForSetup(setup, event) || processed[0] || {};
  return {
    action: item.action || item.signal || data.action || data.signal || "",
    reason: item.reason || data.reason || event.message || "",
  };
}

function entryReadiness(setup, latestQuote) {
  const config = setup.config || {};
  const entry = config.entry || {};
  const levels = setupTradeLevels(setup);
  const price = quotePrice(latestQuote);
  const volumeRatio = numberOrNull(latestQuote && latestQuote.volume_ratio);
  const missing = [];
  if (!setup.enabled || config.enabled === false) missing.push("setup OFF");
  if (entry.enabled === false) missing.push("entree OFF");
  if (SETUP_ENTRY_BLOCKING_STATUSES.has(setup.status)) missing.push("statut bloque");
  if (levels.resistance !== null) {
    if (price === null) missing.push("prix manquant");
    else if (price < levels.resistance) missing.push("prix sous resistance");
  }
  if ((setup.setup_type || config.setup_type) === "momentum_breakout") {
    if (volumeRatio === null) missing.push("volume manquant");
    else if (levels.volumeMin !== null && volumeRatio < levels.volumeMin) {
      missing.push("volume insuffisant");
    }
  }
  return {
    label: missing.length ? "Attente" : "Entree possible",
    missing,
  };
}

function setupTradeLevels(setup) {
  const config = setup.config || {};
  const breakout = config.breakout || {};
  const entry = config.entry || {};
  const risk = config.risk || {};
  const resistance = firstNumber(breakout.resistance, config.resistance);
  const triggerOffset = firstNumber(entry.trigger_offset, config.trigger_offset, 0);
  const limitOffset = firstNumber(entry.limit_offset, config.limit_offset, 0);
  const trigger = firstNumber(
    setup.entry_trigger,
    resistance === null ? null : resistance + triggerOffset,
  );
  const limit = firstNumber(
    setup.maximum_limit_price,
    trigger === null ? null : trigger + limitOffset,
  );
  return {
    resistance,
    trigger,
    limit,
    stop: firstNumber(setup.protective_stop, risk.initial_stop_loss, config.protective_stop),
    volumeMin: firstNumber(breakout.volume_above_average, config.volume_above_average, 1),
    triggerOffset,
    limitOffset,
  };
}

function quotePrice(quote) {
  if (!quote) return null;
  return firstNumber(quote.price, quote.close);
}

function setupStatusTone(status) {
  if (String(status || "").startsWith("WAITING")) return "warn";
  if (["PAUSED", "SUBMITTED"].includes(status)) return "warn";
  if (["ERROR"].includes(status)) return "bad";
  return "ok";
}

function removeUndefinedValues(values) {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== undefined),
  );
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function firstNumber(...values) {
  for (const value of values) {
    const number = numberOrNull(value);
    if (number !== null) return number;
  }
  return null;
}

function numberText(value, digits = 2) {
  const number = numberOrNull(value);
  return number === null ? "-" : number.toFixed(digits);
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
  const ratios = quotes.map((quote) => numberOrNull(quote.volume_ratio)).filter((value) => value !== null);
  const maxRatio = Math.max(...ratios, volumeMin || 0, 1);
  const volumeBottom = height - margins.bottom;
  const yForVolume = (value) => (
    volumeBottom - (Math.max(value, 0) / maxRatio) * volumeHeight
  );
  const barWidth = Math.max(3, Math.min(14, slotWidth * 0.55));
  quotes.forEach((quote, index) => {
    const ratio = numberOrNull(quote.volume_ratio);
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

function parseChartDate(value) {
  const text = String(value || "").trim();
  const intraday = text.match(/^(\d{4})(\d{2})(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (intraday) {
    const [, year, month, day, hour, minute, second = "00"] = intraday;
    return new Date(`${year}-${month}-${day}T${hour}:${minute}:${second}`);
  }
  const compact = text.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (compact) {
    const [, year, month, day] = compact;
    return new Date(`${year}-${month}-${day}T00:00:00`);
  }
  return new Date(value);
}

async function renderSetupDetail() {
  const setupId = document.body.dataset.setupId;
  if (!setupId) return;
  const result = await api(`/api/setups/${encodeURIComponent(setupId)}`);
  const setup = result.setup;
  let symbolEvents = [];
  try {
    symbolEvents = await fetchSetupSymbolEvents(setup.symbol);
  } catch (error) {
    toast(`Events symbole indisponibles: ${error.message}`);
  }
  renderSetupChartTimeframeControls();
  const chartQuotes = await fetchSetupChartQuotes(
    setup.symbol,
    setupChartTimeframe,
    symbolEvents,
  );
  updateSetupChartTimeframeStatus(setupChartTimeframe, chartQuotes);
  setText("detail-title", setup.setup_id);
  setText("detail-subtitle", `${setup.symbol} - ${setup.setup_type}`);
  const latestQuote = (chartQuotes.length ? chartQuotes[chartQuotes.length - 1] : null)
    || latestQuoteFromEvents(symbolEvents);
  renderSetupDetailSummary(setup);
  renderSetupConditionGrid(setup, latestQuote);
  renderSetupEntryPlan(setup, latestQuote);
  renderSetupAnalysisPanel(setup, symbolEvents, latestQuote);
  renderSetupMarketSummary(setup, symbolEvents, latestQuote, setupChartTimeframe);
  renderSetupChart(setup, symbolEvents, chartQuotes, setupChartTimeframe);
  const config = document.getElementById("setup-config");
  currentSetupConfig = structuredCloneSafe(setup.config);
  setupConfigFormDirty = false;
  setupConfigEditorDirty = false;
  renderSetupConfigForm(currentSetupConfig);
  if (config) config.value = JSON.stringify(setup.config, null, 2);
  showSetupConfigMessage("");
  renderEvents("setup-events", result.events || []);
}

function wireSetupConfigEditor() {
  const editor = document.getElementById("setup-config");
  const form = document.getElementById("setup-config-form");
  if (!editor && !form) return;
  if (editor) {
    editor.addEventListener("input", () => {
      setupConfigEditorDirty = true;
    });
  }
  if (form) {
    form.addEventListener("input", () => {
      setupConfigFormDirty = true;
      const preview = buildSetupConfigFromForm();
      if (preview && editor && !setupConfigEditorDirty) {
        editor.value = JSON.stringify(preview, null, 2);
      }
    });
  }
  onClick("setup-config-format", () => {
    const parsed = parseSetupConfigEditor(editor);
    if (!parsed) return;
    currentSetupConfig = structuredCloneSafe(parsed);
    setupConfigFormDirty = false;
    setupConfigEditorDirty = false;
    renderSetupConfigForm(parsed);
    editor.value = JSON.stringify(parsed, null, 2);
    showSetupConfigMessage("JSON formate", "success");
  });
  onClick("setup-config-reset", async () => {
    await renderSetupDetail();
    showSetupConfigMessage("Configuration rechargee", "success");
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
      currentSetupConfig = structuredCloneSafe(result.setup.config);
      setupConfigFormDirty = false;
      setupConfigEditorDirty = false;
      renderSetupConfigForm(currentSetupConfig);
      if (editor) editor.value = JSON.stringify(result.setup.config, null, 2);
      toast("Setup sauvegarde");
      await refresh();
      await renderSetupDetail();
      showSetupConfigMessage("Configuration sauvegardee", "success");
    } catch (error) {
      showSetupConfigMessage(error.message, "error");
      toast(error.message);
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

function isPlainObject(value) {
  return value !== null && !Array.isArray(value) && typeof value === "object";
}

function structuredCloneSafe(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function showSetupConfigMessage(text, kind = "") {
  const message = document.getElementById("setup-config-message");
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || "";
  message.classList.remove("error", "success");
  if (kind) message.classList.add(kind);
}

async function renderLogsPage() {
  const container = document.getElementById("logs-events");
  if (!container) return;
  const result = await api("/api/events?limit=200");
  renderEvents("logs-events", result.items || []);
  const form = document.getElementById("logs-filter");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    const params = new URLSearchParams();
    Object.entries(data).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const filtered = await api(`/api/events?limit=200&${params.toString()}`);
    renderEvents("logs-events", filtered.items || []);
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
  wireActionButtons();
  wireSetupConfigEditor();
  await refresh();
  await renderSetupDetail();
  await renderLogsPage();
  window.setInterval(() => {
    if (latestSnapshot) renderEngineHealth(latestSnapshot.health || {});
  }, 1000);
  connectWebSocket();
}

init().catch((error) => toast(error.message));
