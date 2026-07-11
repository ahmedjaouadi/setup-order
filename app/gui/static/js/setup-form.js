import { formData } from "./ui-helpers.js";

export function setupTextPayload(form) {
  const data = formData(form);
  return {
    symbol: data.symbol,
    text: data.text,
    enabled: true,
  };
}

export function syncTickerFieldFromSetupText(form) {
  if (!form) return;
  const text = String(form.elements.text && form.elements.text.value || "").trim();
  if (!text.startsWith("{")) return;
  try {
    const parsed = JSON.parse(text);
    const skeleton = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed.skeleton && typeof parsed.skeleton === "object" ? parsed.skeleton : parsed)
      : null;
    const symbol = String((skeleton && skeleton.symbol) || "").trim().toUpperCase();
    if (!symbol) return;
    const symbolField = form.elements.symbol;
    if (symbolField && symbolField.value !== symbol) {
      symbolField.value = symbol;
    }
  } catch (error) {
    // Ignore invalid JSON here; the server-side conversion will report it.
  }
}

export function syncTickerFieldFromSetupResult(form, result) {
  if (!form) return;
  const config = result && (result.config || (result.setup && result.setup.config));
  const symbol = String((config && config.symbol) || "").trim().toUpperCase();
  if (!symbol) return;
  const symbolField = form.elements.symbol;
  if (symbolField && symbolField.value !== symbol) {
    symbolField.value = symbol;
  }
}

export function renderSetupToolsOutput(payload, messageText = "", options = {}) {
  const message = document.getElementById("setup-tools-message");
  const output = document.getElementById("setup-tools-output");
  if (message) {
    message.hidden = !messageText;
    message.textContent = messageText;
    message.classList.remove("error");
  }
  if (output) {
    output.hidden = Boolean(options.messageOnly);
    output.textContent = options.messageOnly ? "" : JSON.stringify(payload, null, 2);
  }
}

export function renderSetupToolsError(messageText) {
  const message = document.getElementById("setup-tools-message");
  const output = document.getElementById("setup-tools-output");
  if (message) {
    message.hidden = false;
    message.textContent = messageText;
    message.classList.add("error");
  }
  if (output) {
    output.hidden = true;
    output.textContent = "";
  }
}

export function renderSetupPreview(result) {
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

export function renderSetupPreviewError(messageText) {
  const message = document.getElementById("setup-conversion-result");
  const preview = document.getElementById("setup-preview");
  if (!message || !preview) return;
  message.hidden = false;
  message.classList.add("error");
  message.textContent = messageText;
  preview.hidden = true;
  preview.textContent = "";
}
