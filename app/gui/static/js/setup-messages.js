export const SETUP_VALIDATION_MESSAGE_HINTS = {
  "Setup text is required": "Texte du setup manquant: colle le plan ou le JSON du setup.",
  "Ticker is required": "Ticker manquant: renseigne le symbole dans le champ Ticker.",
  "Add a stop loss in the setup text": "Stop loss manquant: ajoute un stop/SL dans le texte du setup.",
  "Not enough price levels detected. Add an entry/breakout level or a price zone.": "Niveaux de prix insuffisants: ajoute un niveau d'entree, de breakout ou une zone de prix.",
  "setup_id is required": "setup_id manquant: ajoute un identifiant unique pour ce setup.",
  "symbol is required": "symbol manquant: indique le ticker du setup.",
  "mode must be paper or live": "mode invalide: utilise paper ou live.",
  "setup_role must be ENTRY_AND_MANAGEMENT, ENTRY_ONLY or MANAGEMENT_ONLY": "setup_role invalide: utilise ENTRY_AND_MANAGEMENT, ENTRY_ONLY ou MANAGEMENT_ONLY.",
  "entry section must be a mapping": "section entry invalide: entry doit etre un objet JSON.",
  "risk section is required": "section risk manquante: ajoute les limites de risque et le stop.",
  "risk.max_position_amount_usd must be positive": "risk.max_position_amount_usd manquant ou invalide: mets un budget de position positif.",
  "risk.max_risk_usd must be positive": "risk.max_risk_usd manquant ou invalide: mets un risque maximal positif.",
  "trailing_stop_loss.initial_stop must be positive": "trailing_stop_loss.initial_stop manquant ou invalide: ajoute un stop trailing initial positif.",
  "trailing_stop_loss.initial_stop is required before arming": "trailing_stop_loss.initial_stop requis avant armement.",
  "trailing_stop_loss.broker_order.required_before_entry_transmission must be true before arming": "le trailing stop-loss doit etre pret avant transmission de l'ordre d'entree.",
  "estimated entry price is required": "prix d'entree estime manquant: ajoute entry.trigger_price, entry.entry_price ou les niveaux necessaires au type de setup.",
  "stop loss must be below estimated entry price for long setup": "stop loss incoherent: pour un setup long, le stop doit etre sous le prix d'entree estime.",
  "breakout.daily_close_above is required": "niveau breakout manquant: renseigne breakout.daily_close_above.",
  "retest.zone_min and retest.zone_max are required": "zone de retest incomplete: renseigne retest.zone_min et retest.zone_max.",
  "support_zone.min and support_zone.max are required": "zone de support incomplete: renseigne support_zone.min et support_zone.max.",
  "position_management setup_role must be MANAGEMENT_ONLY": "setup_role incoherent: un setup position_management doit etre MANAGEMENT_ONLY.",
  "position_source.mode must be adopt_existing_ibkr_position": "position_source.mode invalide: utilise adopt_existing_ibkr_position pour gerer une position existante.",
  "position_source.require_existing_position must be true": "position_source.require_existing_position doit etre true pour confirmer qu'une position IBKR existe.",
  "MANAGEMENT_ONLY setup cannot enable entry orders": "entry.enabled incoherent: un setup MANAGEMENT_ONLY ne peut pas activer les ordres d'entree.",
  "entry.enabled must be true when setup_role allows entries": "entry.enabled doit etre true quand le setup_role autorise les entrees.",
  "entry.maximum_limit_price must be greater than or equal to entry.trigger_price": "limite d'entree incoherente: entry.maximum_limit_price doit etre superieur ou egal a entry.trigger_price.",
  "retest.zone_min must be less than or equal to retest.zone_max": "zone de retest inversee: retest.zone_min doit etre inferieur ou egal a retest.zone_max.",
  "support_zone.min must be less than or equal to support_zone.max": "zone de support inversee: support_zone.min doit etre inferieur ou egal a support_zone.max.",
  "risk.max_risk_usd is above risk.max_position_amount_usd; verify the capital and risk budget.": "risque a verifier: risk.max_risk_usd est superieur au budget de position.",
};

export function formatSetupValidationDetail(detail) {
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return "";
  if (detail.detail && typeof detail.detail === "object") {
    const nested = formatSetupValidationDetail(detail.detail);
    if (nested) return nested;
  }

  const validation = plainObjectOrNull(detail.validation);
  const saveValidation = plainObjectOrNull(detail.save_validation);
  const rawErrors = uniqueMessages([
    ...normalizeDetailMessages(detail.errors),
    ...normalizeDetailMessages(validation && validation.errors),
    ...normalizeDetailMessages(saveValidation && saveValidation.errors),
    ...setupSemanticIssueMessages(detail.details, "error"),
    ...setupSemanticIssueMessages(validation && validation.details, "error"),
  ]);
  const rawWarnings = uniqueMessages([
    ...normalizeDetailMessages(detail.warnings),
    ...normalizeDetailMessages(validation && validation.warnings),
    ...normalizeDetailMessages(saveValidation && saveValidation.warnings),
    ...setupSemanticIssueMessages(detail.details, "warning"),
    ...setupSemanticIssueMessages(validation && validation.details, "warning"),
  ]);
  const hasSetupValidationShape = Boolean(
    detail.code === "SETUP_VALIDATION_FAILED"
      || detail.extracted
      || validation
      || saveValidation
      || (detail.details && (detail.details.semantic_validation || detail.details.canonical_mapped_fields))
      || rawErrors.some(isKnownSetupValidationMessage)
      || rawWarnings.some(isKnownSetupValidationMessage)
  );
  if (!hasSetupValidationShape) return "";

  const sections = [
    "Setup refuse: le programme a trouve des champs manquants ou incoherents.",
  ];
  const errors = uniqueMessages(rawErrors.map(humanizeSetupValidationMessage));
  const warnings = uniqueMessages(rawWarnings.map(humanizeSetupValidationMessage));
  const mappedFields = setupMappedFieldLines(detail);

  if (errors.length) sections.push(formatBulletSection("A corriger", errors));
  if (warnings.length) sections.push(formatBulletSection("A verifier", warnings));
  if (mappedFields.length) {
    sections.push(formatBulletSection("Champs reconnus automatiquement", mappedFields.slice(0, 8)));
  }
  if (!errors.length && !warnings.length && detail.message) {
    sections.push(humanizeSetupValidationMessage(detail.message));
  }
  return sections.filter(Boolean).join("\n");
}

export function normalizeDetailMessages(messages) {
  if (!messages) return [];
  const items = Array.isArray(messages) ? messages : [messages];
  return items
    .map((item) => {
      if (!item) return "";
      if (typeof item === "string") return item;
      if (typeof item === "object") {
        return item.message || item.detail || item.code || JSON.stringify(item);
      }
      return String(item);
    })
    .map((item) => item.trim())
    .filter(Boolean);
}

export function validationMessagesText(messages) {
  const normalized = uniqueMessages(normalizeDetailMessages(messages));
  return normalized.length ? normalized.join(" | ") : "OK";
}

export function uniqueMessages(messages) {
  const seen = new Set();
  const unique = [];
  messages.forEach((message) => {
    const text = String(message || "").trim();
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(text);
  });
  return unique;
}

export function setupSemanticIssueMessages(details, level) {
  const semantic = details && details.semantic_validation;
  const issues = semantic && Array.isArray(semantic.issues) ? semantic.issues : [];
  return issues
    .filter((issue) => !level || issue.level === level)
    .map((issue) => issue && (issue.message || issue.code))
    .filter(Boolean);
}

export function setupMappedFieldLines(detail) {
  const validation = plainObjectOrNull(detail.validation);
  const candidates = [
    ...(detail.details && Array.isArray(detail.details.canonical_mapped_fields)
      ? detail.details.canonical_mapped_fields
      : []),
    ...(validation && validation.details && Array.isArray(validation.details.canonical_mapped_fields)
      ? validation.details.canonical_mapped_fields
      : []),
    ...(detail.extracted && Array.isArray(detail.extracted.canonical_mapped_fields)
      ? detail.extracted.canonical_mapped_fields
      : []),
  ];
  return uniqueMessages(candidates.map((item) => {
    if (!item || typeof item !== "object") return "";
    const raw = item.raw_key || item.source || item.from || "";
    const canonical = item.canonical_path || item.target || item.to || "";
    if (!raw || !canonical || raw === canonical) return "";
    return `${raw} -> ${canonical}`;
  }));
}

export function isKnownSetupValidationMessage(message) {
  const text = String(message || "").trim();
  return Boolean(
    SETUP_VALIDATION_MESSAGE_HINTS[text]
      || text.startsWith("Unknown setup type:")
      || text.startsWith("setup_type must be ")
      || text.startsWith("Ticker field must match setup JSON symbol")
      || text.includes("zone_min above zone_max")
      || text.includes("must be less than or equal to")
      || text.includes("must be greater than or equal to")
  );
}

export function humanizeSetupValidationMessage(message) {
  const text = String(message || "").trim();
  if (!text) return "";
  if (SETUP_VALIDATION_MESSAGE_HINTS[text]) return SETUP_VALIDATION_MESSAGE_HINTS[text];
  if (text.startsWith("Unknown setup type:")) {
    return `${text}: utilise un setup_type supporte (ex: momentum_breakout, breakout_retest, aggressive_rebound, position_management).`;
  }
  if (text.startsWith("setup_type must be ")) {
    return `${text}: le setup_type du JSON ne correspond pas au type attendu par cette strategie.`;
  }
  if (text.startsWith("Ticker field must match setup JSON symbol")) {
    return `${text}: le ticker saisi doit correspondre au symbol present dans le JSON.`;
  }
  if (text.endsWith(" is required")) {
    return `${text.slice(0, -" is required".length)} manquant: ajoute ce champ dans la configuration.`;
  }
  if (text.includes(" must be one of ")) {
    return `${text}: valeur non supportee, choisis une valeur de la liste attendue.`;
  }
  if (text.includes(" must be ") && text.includes("got ")) {
    return `${text}: type de valeur invalide, verifie le format du champ.`;
  }
  if (text.includes(" must be > ") || text.includes(" must be >= ")) {
    return `${text}: la valeur numerique est trop basse.`;
  }
  if (text.includes(" is not declared in ")) {
    return `${text}: champ non reconnu par le schema, verifie le nom ou retire-le.`;
  }
  if (text.includes("zone_min above zone_max")) {
    return `${text}: la borne basse de zone doit etre inferieure ou egale a la borne haute.`;
  }
  return text;
}

export function formatBulletSection(title, items) {
  return `${title}:\n- ${items.join("\n- ")}`;
}

export function plainObjectOrNull(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}
