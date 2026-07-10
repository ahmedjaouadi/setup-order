export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function money(value) {
  const number = Number(value || 0);
  return number.toFixed(2);
}

export function maybeMoney(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "-";
}

export function maybePercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)}%` : "-";
}

export function maybeProbability(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  const percent = number >= 0 && number <= 1 ? number * 100 : number;
  return `${percent.toFixed(2)}%`;
}

export function signedPercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

export const STATUS_BADGE_LABELS = {
  ACCURACY_HISTORY_WARMUP: "Accuracy warmup",
  BENCHMARK_FRAMEWORK_ONLY: "Model Lab only",
  BUILTIN_READY: "Built-in ready",
  DEPENDENCIES_OK: "Dependencies OK",
  DEPENDENCY_MISSING: "Dependency missing",
  DEPENDENCY_VERSION_ERROR: "Dependency version error",
  DISABLED_BY_CONFIG: "Disabled by config",
  EXTERNAL_WORKER_CONFIGURED: "External worker configured",
  EXTERNAL_WORKER_OK: "External worker OK",
  FORECAST_EMPTY_OUTPUT: "Empty forecast",
  FORECAST_FAILED: "Forecast failed",
  FORECAST_OK: "Forecast OK",
  FORECAST_STACK_ADVISORY_ONLY: "Advisory only",
  FORECAST_TIMEOUT: "Forecast timeout",
  INPUT_DATA_READY: "Input ready",
  INSUFFICIENT_ACCURACY_HISTORY: "Accuracy history short",
  INSUFFICIENT_HISTORY_FOR_MODEL: "History too short",
  MISSING_DEPENDENCY: "Missing dependency",
  MODEL_LOAD_FAILED: "Model load failed",
  MISMATCH: "Mismatch",
  LOCAL_FALLBACK: "Local fallback",
  LOCAL_HISTORY: "Local history",
  LOCAL_ONLY: "Local only",
  LOCAL_ORPHAN: "Local orphan",
  NO_CACHED_FORECAST: "No cached forecast",
  NO_BROKER_ORDER: "No broker order",
  NO_ENTRY_ORDER: "No entry order",
  NO_FORECAST_AVAILABLE: "No forecast available",
  NO_POSITION_NO_ENTRY_ORDER: "No position / no entry",
  NOT_APPLICABLE: "N/A",
  NOT_RUN: "Not run",
  NOT_SELECTED_FOR_CURRENT_RUN: "Not selected",
  OK_CALIBRATED: "Calibrated",
  OK_UNCALIBRATED: "Uncalibrated",
  PENDING_SUBMIT: "Pending submit",
  POSITION_OPEN_STOP_ACTIVE: "Position stop active",
  POSITION_OPEN_STOP_MISSING_CRITICAL: "Position stop missing",
  PREPARED_NOT_TRANSMITTED: "Prepared not transmitted",
  PROTECTED: "Protected",
  STALE: "Stale",
  NOT_RUNNING: "Not running",
  UNKNOWN_CRITICAL: "Unknown critical",
  BLOCKED_TRAILING_STOP_NOT_READY: "Trailing stop not ready",
  TRAILING_STOP_LOSS_REQUIRED: "Trailing stop required",
  RECONCILIATION_MISMATCH: "Reconciliation mismatch",
  STOP_MISSING: "Stop missing",
  STOP_PREPARED_NOT_TRANSMITTED: "Stop not transmitted",
  TRANSMITTED: "Transmitted",
  UNKNOWN_BROKER_STATE: "Unknown broker state",
  WORKER_ERROR: "Worker error",
  WORKER_NOT_CONFIGURED: "Worker not configured",
  WORKER_NOT_RUNNING: "Worker not running",
  WORKER_READY: "Worker ready",
  WORKER_UNREACHABLE: "Worker unreachable",
  MARKET_CLOSED: "Marche ferme",
  MISSING_MARKET_DATA: "Donnees marche manquantes",
  BROKER_DISCONNECTED: "Broker deconnecte",
  BROKER_TRACKER_STALE: "Broker tracker obsolete",
  RISK_UNKNOWN: "Risque inconnu",
  SPREAD_TOO_WIDE: "Spread trop large",
  MANAGEMENT_ONLY_POSITION_MISSING: "Position IBKR absente",
  TRAILING_STOP_NOT_READY: "Trailing stop pas pret",
};

export function statusBadge(value, detail = "") {
  const status = String(value || "");
  const label = statusLabel(status);
  const className = statusClassName(status);
  const title = detail ? ` title="${escapeHtml(detail)}"` : "";
  const style = statusBadgeStyle(status);
  return `<span class="status ${escapeHtml(className)}" style="${escapeHtml(style)}"${title}>${escapeHtml(label)}</span>`;
}

export function statusBadgeStyle(status) {
  const profile = statusBadgeProfile(status);
  return badgeStyleFromHue(profile.hue, profile);
}

export function statusLabel(value) {
  const status = String(value || "");
  const normalized = status.toUpperCase();
  return STATUS_BADGE_LABELS[normalized] || status;
}

export function statusClassName(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9_-]+/g, "_");
}

// Mirrors app/engine/setup_lifecycle_service.NON_ARMABLE_STATUSES so the GUI
// gates the same way the backend does.

export function formatStatusList(values, empty = "-") {
  const items = Array.isArray(values) ? values : [];
  const labels = items.map(statusLabel).filter(Boolean);
  return labels.length ? labels.join(" | ") : empty;
}

export function statusProfile(hue, saturation = 64, backgroundLightness = 93, textLightness = 28, borderSaturation = saturation - 18, borderLightness = 76) {
  return {
    hue,
    saturation,
    backgroundLightness,
    textLightness,
    borderSaturation,
    borderLightness,
  };
}

export const STATUS_BADGE_PROFILES = {
  AVAILABLE: statusProfile(145, 64, 92, 24, 42, 74),
  AFTER_HOURS_TRIGGER_DETECTED: statusProfile(26, 82, 93, 30, 55, 78),
  OK: statusProfile(145, 64, 92, 24, 42, 74),
  CREATED: statusProfile(220, 54, 93, 28, 36, 76),
  CANCELLED: statusProfile(344, 42, 94, 30, 28, 80),
  CONNECTED: statusProfile(145, 64, 92, 24, 42, 74),
  CONNECTING: statusProfile(205, 56, 93, 28, 36, 76),
  CLOSED: statusProfile(220, 22, 94, 30, 18, 80),
  DISABLED: statusProfile(225, 24, 94, 30, 20, 80),
  DISABLED_BY_CONFIG: statusProfile(220, 18, 94, 30, 18, 80),
  DRAFT: statusProfile(222, 54, 93, 28, 36, 76),
  EMERGENCY_STOP: statusProfile(358, 82, 92, 26, 62, 72),
  ERROR: statusProfile(0, 80, 92, 26, 56, 74),
  ERROR_REQUIRES_MANUAL_REVIEW: statusProfile(336, 78, 92, 26, 60, 72),
  ENTRY_FILLED: statusProfile(138, 68, 92, 24, 42, 74),
  ENTRY_LIMIT_EXCEEDED: statusProfile(18, 88, 92, 24, 64, 72),
  ENTRY_ORDER_PLACED: statusProfile(212, 62, 93, 28, 42, 76),
  ENTRY_PARTIALLY_FILLED: statusProfile(156, 66, 92, 24, 44, 74),
  ENTRY_READY: statusProfile(142, 68, 92, 24, 42, 74),
  EXTERNAL_WORKER_CONFIGURED: statusProfile(188, 58, 93, 28, 38, 76),
  EXTERNAL_WORKER_OK: statusProfile(158, 64, 92, 24, 42, 74),
  EXPIRED: statusProfile(12, 32, 94, 30, 24, 80),
  FILLED: statusProfile(138, 68, 92, 24, 42, 74),
  IN_POSITION: statusProfile(140, 68, 92, 24, 42, 74),
  INVALIDATED: statusProfile(356, 82, 92, 26, 62, 72),
  LOADED: statusProfile(204, 54, 93, 28, 36, 76),
  LOAD_ERROR: statusProfile(0, 82, 92, 26, 62, 72),
  LOCAL_FALLBACK: statusProfile(220, 18, 94, 30, 18, 80),
  LOCAL_HISTORY: statusProfile(220, 18, 94, 30, 18, 80),
  LOCAL_ONLY: statusProfile(220, 18, 94, 30, 18, 80),
  LOCAL_ORPHAN: statusProfile(0, 78, 93, 28, 58, 76),
  MANAGING_POSITION: statusProfile(170, 64, 92, 24, 40, 74),
  MANUAL_REVIEW_REQUIRED: statusProfile(284, 66, 93, 28, 48, 76),
  MISSING_DEPENDENCY: statusProfile(30, 88, 92, 24, 64, 72),
  MISMATCH: statusProfile(0, 82, 92, 26, 62, 72),
  MISSED_BREAKOUT: statusProfile(350, 88, 92, 24, 66, 70),
  MISSED_ENTRY_WAITING_RETEST: statusProfile(332, 72, 93, 27, 54, 76),
  NO_BROKER_ORDER: statusProfile(0, 78, 93, 28, 58, 76),
  NO_ENTRY_ORDER: statusProfile(220, 18, 94, 30, 18, 80),
  NO_POSITION_NO_ENTRY_ORDER: statusProfile(220, 18, 94, 30, 18, 80),
  PAUSED: statusProfile(42, 82, 93, 30, 55, 78),
  NOT_RUNNING: statusProfile(0, 78, 93, 28, 58, 76),
  PARTIAL_EXIT: statusProfile(292, 60, 93, 28, 38, 76),
  PENDING_SUBMIT: statusProfile(34, 82, 93, 30, 55, 78),
  POSITION_OPEN_STOP_ACTIVE: statusProfile(145, 64, 92, 24, 42, 74),
  POSITION_OPEN_STOP_MISSING_CRITICAL: statusProfile(0, 82, 92, 26, 62, 72),
  PRICE_TOO_FAR_ABOVE_TRIGGER: statusProfile(0, 84, 92, 25, 62, 72),
  PREMARKET_TRIGGER_DETECTED: statusProfile(22, 84, 93, 30, 56, 78),
  PREPARED_NOT_TRANSMITTED: statusProfile(30, 88, 92, 24, 64, 72),
  PROTECTED: statusProfile(145, 64, 92, 24, 42, 74),
  REARMED_ON_NEW_BASE: statusProfile(258, 64, 93, 28, 42, 76),
  REARM_REQUIRED: statusProfile(215, 34, 93, 28, 28, 76),
  RECONCILING_EXISTING_POSITION: statusProfile(188, 58, 93, 28, 38, 76),
  RECONCILIATION_MISMATCH: statusProfile(0, 82, 92, 26, 62, 72),
  REJECTED: statusProfile(350, 82, 92, 26, 62, 72),
  RTH_CONFIRMATION_REQUIRED: statusProfile(36, 82, 93, 30, 54, 78),
  RUNNING: statusProfile(148, 64, 92, 24, 42, 74),
  STOP_ORDER_PLACED: statusProfile(30, 82, 93, 30, 55, 78),
  STOP_MISSING: statusProfile(0, 82, 92, 26, 62, 72),
  STOP_PLACED: statusProfile(26, 82, 93, 30, 55, 78),
  STOP_PREPARED_NOT_TRANSMITTED: statusProfile(30, 88, 92, 24, 64, 72),
  STALE: statusProfile(30, 88, 92, 24, 64, 72),
  SUBMITTED: statusProfile(34, 82, 93, 30, 55, 78),
  TRANSMITTED: statusProfile(145, 64, 92, 24, 42, 74),
  TRIGGER_REACHED: statusProfile(198, 66, 93, 28, 44, 76),
  UNKNOWN_BROKER_STATE: statusProfile(0, 78, 93, 28, 58, 76),
  UNKNOWN_CRITICAL: statusProfile(0, 82, 92, 26, 62, 72),
  VALIDATED: statusProfile(145, 64, 93, 28, 42, 76),
  WAITING_ACTIVATION: statusProfile(42, 84, 93, 30, 55, 78),
  WAITING_AFTER_OPEN_BARS: statusProfile(34, 82, 93, 30, 55, 78),
  WAITING_BREAKOUT: statusProfile(28, 84, 93, 30, 55, 78),
  WAITING_CONFIRMATION: statusProfile(195, 62, 93, 28, 40, 76),
  WAITING_ENTRY_SIGNAL: statusProfile(210, 68, 93, 28, 46, 76),
  WAITING_REBOUND: statusProfile(302, 74, 93, 27, 54, 76),
  WAITING_RETEST: statusProfile(260, 76, 92, 26, 58, 72),
  WAITING_TRIGGER: statusProfile(36, 82, 93, 30, 54, 78),
  WATCH_ONLY_TRIGGERED: statusProfile(278, 66, 93, 28, 48, 76),
  WORKER_ERROR: statusProfile(0, 82, 92, 26, 62, 72),
  WORKER_NOT_CONFIGURED: statusProfile(30, 84, 93, 30, 56, 78),
  WORKER_UNREACHABLE: statusProfile(348, 82, 92, 26, 62, 72),
};

export function statusBadgeProfile(status) {
  const name = String(status || "").toUpperCase();
  if (STATUS_BADGE_PROFILES[name]) return STATUS_BADGE_PROFILES[name];
  const hash = hashString(name);
  return statusProfile((hash % 360 + 360) % 360, 58, 93, 28, 40, 76);
}

export function signalBadgeStyle(signal) {
  const score = signal && signal.percent !== null && signal.percent !== undefined
    ? Number(signal.percent) / 100
    : Number(signal && signal.score);
  const normalized = Number.isFinite(score) ? Math.min(1, Math.max(0, score)) : 0;
  const hue = Math.round(140 * normalized);
  const saturation = 72 + Math.round(normalized * 10);
  const backgroundLightness = 96 - Math.round(normalized * 16);
  const textLightness = normalized >= 0.82 ? 20 : normalized >= 0.5 ? 24 : 28;
  return badgeStyleFromHue(hue, {
    saturation,
    backgroundLightness,
    textLightness,
    borderSaturation: saturation - 18,
    borderLightness: 76 - Math.round(normalized * 6),
  });
}

export function badgeStyleFromHue(hue, options = {}) {
  const saturation = options.saturation ?? 64;
  const backgroundLightness = options.backgroundLightness ?? 93;
  const textLightness = options.textLightness ?? 28;
  const borderSaturation = options.borderSaturation ?? Math.max(30, saturation - 18);
  const borderLightness = options.borderLightness ?? 76;
  const h = ((Number(hue) || 0) % 360 + 360) % 360;
  return [
    `background: hsl(${h} ${saturation}% ${backgroundLightness}%);`,
    `color: hsl(${h} ${Math.min(90, saturation + 4)}% ${textLightness}%);`,
    `border-color: hsl(${h} ${borderSaturation}% ${borderLightness}%);`,
  ].join(" ");
}

export function hashString(value) {
  let hash = 0;
  for (let index = 0; index < String(value || "").length; index += 1) {
    hash = ((hash << 5) - hash) + String(value || "").charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function toast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.hidden = false;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    element.hidden = true;
  }, 3200);
}

export function compactToastMessage(message) {
  const firstLine = String(message || "")
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean) || "";
  if (firstLine.length <= 150) return firstLine;
  return `${firstLine.slice(0, 147)}...`;
}

export function timeWithAge(value, seconds) {
  if (!value) return "-";
  const age = formatAge(seconds);
  return age === "-" ? formatTime(value) : `${formatTime(value)} (${age})`;
}

export function formatAge(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${Math.max(value, 0)}s`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function secondsSince(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(Math.floor((Date.now() - date.getTime()) / 1000), 0);
}

export function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value ?? "";
}

export function setPnlTone(id, value) {
  const element = document.getElementById(id);
  if (!element) return;
  const number = Number(value);
  element.classList.remove("money-positive", "money-negative", "money-flat");
  if (!Number.isFinite(number)) return;
  if (number > 0) element.classList.add("money-positive");
  else if (number < 0) element.classList.add("money-negative");
  else element.classList.add("money-flat");
}

export function toneForAge(age) {
  const value = Number(age);
  if (!Number.isFinite(value)) return "warn";
  if (value <= 15) return "ok";
  if (value <= 45) return "info";
  if (value <= 90) return "warn";
  return "danger";
}

export function syncAgeChipLabel(age, staleAfter) {
  if (age === null || age === undefined || age === "") return "SYNC -";
  const value = Number(age);
  if (!Number.isFinite(value)) return `SYNC ${formatAge(age)}`;
  // Align with the server's broker stale_after_seconds so the SYNC chip and the
  // BROKER chip tell the same story: SYNC turns STALE exactly when the broker
  // tracker turns STALE (age > stale_after), and WARNs as it approaches. The
  // previous fixed 45/90s thresholds contradicted the 10s broker window.
  const stale = Number(staleAfter) > 0 ? Number(staleAfter) : 10;
  if (value > stale) return `SYNC STALE ${formatAge(value)}`;
  if (value > stale / 2) return `SYNC WARN ${formatAge(value)}`;
  return `SYNC ${formatAge(value)}`;
}

export function setToneData(id, tone) {
  const element = document.getElementById(id);
  if (!element) return;
  element.dataset.tone = tone || "neutral";
}

export function pnlClass(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "money-flat";
  if (number > 0) return "money-positive";
  if (number < 0) return "money-negative";
  return "money-flat";
}

export function cssSafeId(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function dlRows(values, labels = {}) {
  return Object.entries(values).map(([key, value]) => `
    <dt>${escapeHtml(labels[key] || key)}</dt>
    <dd>${escapeHtml(formatDetailValue(value))}</dd>
  `).join("");
}

export function formatDetailValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

export function emptyRow(span, text) {
  return `<tr><td colspan="${span}">${escapeHtml(text)}</td></tr>`;
}

export function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function onClick(id, handler) {
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

export function openModal(modal) {
  if (!modal) return;
  modal.hidden = false;
  document.body.classList.add("modal-open");
  const focusable = modal.querySelector(
    "input, textarea, select, button:not([data-modal-close])",
  );
  if (focusable) focusable.focus();
}

export function closeModal(modal) {
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  if (!document.querySelector(".modal-overlay:not([hidden])")) {
    document.body.classList.remove("modal-open");
  }
}

export function wireModals() {
  document.body.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-modal-open]");
    if (opener) {
      openModal(document.getElementById(opener.dataset.modalOpen));
      return;
    }
    const closer = event.target.closest("[data-modal-close]");
    if (closer) {
      closeModal(closer.closest(".modal-overlay"));
      return;
    }
    // Click on the backdrop itself (outside the .modal) closes it.
    if (event.target.classList.contains("modal-overlay")) {
      closeModal(event.target);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = document.querySelector(".modal-overlay:not([hidden])");
    if (open) closeModal(open);
  });
}

export function formData(form) {
  const data = {};
  for (const [key, value] of new FormData(form).entries()) {
    data[key] = value;
  }
  return data;
}

export function yesNo(value) {
  if (value === true) return "YES";
  if (value === false) return "NO";
  return "-";
}

export function setButtonDisabled(id, disabled) {
  const button = document.getElementById(id);
  if (button) button.disabled = Boolean(disabled);
}

export function removeUndefinedValues(values) {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== undefined),
  );
}

export function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function firstNumber(...values) {
  for (const value of values) {
    const number = numberOrNull(value);
    if (number !== null) return number;
  }
  return null;
}

export function numberText(value, digits = 2) {
  const number = numberOrNull(value);
  return number === null ? "-" : number.toFixed(digits);
}

export function isPlainObject(value) {
  return value !== null && !Array.isArray(value) && typeof value === "object";
}

export function structuredCloneSafe(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}
