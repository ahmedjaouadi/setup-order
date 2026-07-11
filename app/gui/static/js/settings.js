import { api } from "./api-client.js";
import { dlRows, formData, toast } from "./ui-helpers.js";

export const SETTINGS_RISK_LABELS = {
  max_open_positions: "Positions ouvertes max",
  max_position_amount_usd: "Montant max par position (USD)",
  max_risk_per_trade_usd: "Risque max par trade (USD)",
  max_daily_loss_usd: "Perte journaliere max (USD)",
  max_total_exposure_usd: "Exposition totale max (USD)",
  allow_short: "Short autorise",
};

export function renderSettings(snapshot) {
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

export function wireMarketForm() {
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
