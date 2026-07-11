import { normalizeDetailMessages } from "./setup-messages.js";
import {
  currentSetupArmStatus,
  currentSetupConfig,
  currentSetupDetailSetup,
  setupConfigEditorDirty,
  setupConfigFormDirty,
} from "./state.js";
import { formatConfigLabel, isPlainObject, structuredCloneSafe } from "./ui-helpers.js";

export const CONFIG_FIELD_OPTIONS = {
  direction: ["long", "short"],
  mode: ["paper", "live"],
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

export const CONFIG_PATH_OPTIONS = {
  "position_source.mode": ["adopt_existing_ibkr_position", "manual", "bot"],
  "management.stop_management.mode": ["step_based", "trailing", "none"],
  "stop_management.mode": ["step_based", "trailing", "none"],
  "timeframes.signal": CONFIG_FIELD_OPTIONS.timeframe,
  "timeframes.confirmation": CONFIG_FIELD_OPTIONS.timeframe,
};

export const CONFIG_ROOT_ORDER = [
  "setup_id",
  "symbol",
  "enabled",
  "mode",
  "setup_type",
  "setup_role",
  "direction",
];

export function parseSetupConfigEditor(editor) {
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

export function renderSetupConfigForm(config) {
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

export function createConfigNode(path, value) {
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

export function createConfigList(path, values) {
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

export function createConfigField(path, value) {
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

export function createConfigInput(path, value) {
  const input = document.createElement("input");
  input.dataset.configPath = JSON.stringify(path);
  input.dataset.configType = value === null ? "null" : typeof value;
  return input;
}

export function createConfigSelect(path, value, options) {
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

export function buildSetupConfigFromForm() {
  if (!currentSetupConfig) return null;
  const config = structuredCloneSafe(currentSetupConfig);
  const fields = document.querySelectorAll("[data-config-path]");
  fields.forEach((field) => {
    const path = JSON.parse(field.dataset.configPath || "[]");
    setDeepValue(config, path, parseConfigFieldValue(field));
  });
  return config;
}

export function parseConfigFieldValue(field) {
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

export function setDeepValue(target, path, value) {
  let cursor = target;
  for (let index = 0; index < path.length - 1; index += 1) {
    cursor = cursor[path[index]];
  }
  cursor[path[path.length - 1]] = value;
}

export function configOptionsForPath(path) {
  const pathKey = path.join(".");
  const key = path[path.length - 1];
  return CONFIG_PATH_OPTIONS[pathKey] || CONFIG_FIELD_OPTIONS[key] || null;
}

export function orderedConfigEntries(value, priority = []) {
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

export function showSetupConfigMessage(text, kind = "") {
  const message = document.getElementById("setup-config-message");
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || "";
  message.classList.remove("error", "success");
  if (kind) message.classList.add(kind);
}

export function syncSetupConfigActions() {
  const armButton = document.getElementById("setup-config-arm");
  const disarmButton = document.getElementById("setup-config-disarm");
  const dirty = setupConfigFormDirty || setupConfigEditorDirty;
  const disarmed = String((currentSetupDetailSetup || {}).status || "").toUpperCase() === "DISABLED";
  const armErrors = currentSetupArmStatus
    ? normalizeDetailMessages(currentSetupArmStatus.arm_validation && currentSetupArmStatus.arm_validation.errors)
    : [];
  const disarmErrors = currentSetupArmStatus
    ? normalizeDetailMessages(currentSetupArmStatus.disarm_validation && currentSetupArmStatus.disarm_validation.errors)
    : [];
  if (armButton) {
    armButton.disabled = dirty || (currentSetupArmStatus ? !currentSetupArmStatus.armable : false);
    armButton.title = dirty
      ? "Sauvegardez les modifications avant d'armer le setup"
      : (armErrors.length ? armErrors.join(" | ") : "Armer le setup sans sauvegarder la configuration");
  }
  if (disarmButton) {
    disarmButton.disabled = currentSetupArmStatus ? !currentSetupArmStatus.disarmable : disarmed;
    disarmButton.title = disarmErrors.length
      ? disarmErrors.join(" | ")
      : (disarmed
        ? "Le setup est deja desarme"
        : "Desarmer le setup sans modifier la configuration");
  }
}
