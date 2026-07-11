import { latestSnapshot } from "./state.js";
import {
  cssSafeId,
  emptyRow,
  escapeHtml,
  formatTime,
  maybeMoney,
  maybePercent,
  money,
  numberOrNull,
  setText,
  statusBadge,
} from "./ui-helpers.js";

export function renderOrders(orders) {
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

export function renderOrderHistory(orders) {
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

export function renderOrderRows(orders, options = {}) {
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

export function renderLocalOrderOrphans(orders) {
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

export function describeOrderPrice(order) {
  const parts = [];
  if (order.trigger_price != null) parts.push(`T ${maybeMoney(order.trigger_price)}`);
  if (order.limit_price != null) parts.push(`L ${maybeMoney(order.limit_price)}`);
  if (!parts.length) return order.order_type === "MKT" ? "MKT" : "-";
  return parts.join(" / ");
}

export function orderSourceBadge(order) {
  const brokerStatus = order.broker_order_status || order.broker_live_status || "";
  if (brokerStatus === "NO_BROKER_ORDER") {
    return statusBadge("LOCAL_ONLY");
  }
  return statusBadge(brokerStatus || order.status || "UNKNOWN");
}

export function renderExecutions(executions) {
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

export function manualOrderPayload() {
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

export function renderManualOrderRisk(result) {
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

export function orderIsBrokerActive(order) {
  const brokerStatus = order.broker_order_status || order.broker_live_status || "";
  if (["PENDING_SUBMIT", "TRANSMITTED", "SUBMITTED", "PARTIALLY_FILLED"].includes(brokerStatus)) {
    return true;
  }
  if (brokerStatus === "PREPARED_NOT_TRANSMITTED") return false;
  return Boolean(order.is_active);
}

export function describeOrderStop(order) {
  if (order.stop_price != null) {
    return maybeMoney(order.stop_price);
  }
  if (order.stop_order_status) {
    return order.stop_order_status;
  }
  return "MISSING";
}

export function describeProtectionStatus(status) {
  return String(status || "NO_ENTRY_ORDER").replaceAll("_", " ");
}

export function describeOrderDiagnostic(order) {
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

export function canDeleteOrder(order) {
  return ["REJECTED", "CANCELLED", "FILLED", "ERROR"].includes(order.status);
}

export function canAttachMissingStop(order) {
  return String(order.side || "").toUpperCase() === "BUY"
    && ["CREATED", "SUBMITTED"].includes(String(order.status || ""))
    && !order.stop_order_id
    && order.stop_price == null;
}

export function renderPositions(positions) {
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
