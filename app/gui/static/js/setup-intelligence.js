import { api, optionalApi } from "./api-client.js";
import { renderSetupDetailJsonOutput } from "./setup-detail.js";
import {
  currentSetupDetailInfo,
  currentSetupIntelligence,
  currentSetupIntelligenceComparison,
  currentSetupIntelligenceSelectedId,
  page,
  setCurrentSetupIntelligence,
  setCurrentSetupIntelligenceComparison,
  setCurrentSetupIntelligenceSelectedId,
} from "./state.js";
import { escapeHtml, formatTime, numberOrNull, numberText } from "./ui-helpers.js";

export const SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE = 8;

export async function fetchSetupIntelligence(setupId, options = {}) {
  const limitValue = numberOrNull(options.limit);
  const offsetValue = numberOrNull(options.offset);
  const limit = limitValue === null ? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE : limitValue;
  const offset = offsetValue === null ? 0 : offsetValue;
  const latestPath = `/api/intelligence/setups/${encodeURIComponent(setupId)}/latest`;
  const params = new URLSearchParams({
    summary: "true",
    limit: String(limit),
    offset: String(offset),
  });
  const listPath = `/api/intelligence/setups/${encodeURIComponent(setupId)}/analyses?${params.toString()}`;
  const [latest, history] = await Promise.all([
    optionalApi(latestPath),
    api(listPath).catch(() => ({
      items: [],
      limit,
      offset,
      has_more: false,
      total_count: 0,
    })),
  ]);
  const analyses = ((history && history.items) || []).map((analysis) => (
    latest && analysis.analysis_id === latest.analysis_id ? latest : analysis
  ));
  return {
    latest,
    analyses,
    history: {
      items: analyses,
      limit: numberOrNull(history && history.limit) ?? limit,
      offset: numberOrNull(history && history.offset) ?? offset,
      has_more: Boolean(history && history.has_more),
      total_count: numberOrNull(history && history.total_count),
    },
  };
}

export function emptySetupIntelligencePage() {
  return {
    latest: null,
    analyses: [],
    history: {
      items: [],
      limit: SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE,
      offset: 0,
      has_more: false,
      total_count: 0,
    },
  };
}

export async function renderSetupIntelligence(setupId) {
  const previousState = currentSetupIntelligence && currentSetupIntelligence.setup_id === setupId
    ? currentSetupIntelligence
    : null;
  const page = await fetchSetupIntelligence(setupId, {
    limit: SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE,
    offset: 0,
  });
  setCurrentSetupIntelligence(buildSetupIntelligenceState(previousState, setupId, page));
  setCurrentSetupIntelligenceComparison(null);
  setCurrentSetupIntelligenceSelectedId(null);
  setCurrentSetupIntelligenceSelectedId(selectedIntelligenceAnalysis(currentSetupIntelligence)?.analysis_id || null);
  renderSetupIntelligencePanel(currentSetupIntelligence);
  syncCurrentSetupDetailIntelligence();
  renderSetupDetailJsonOutput();
  return currentSetupIntelligence;
}

export function setupIntelligenceHistoryItems(data) {
  if (data && data.history && Array.isArray(data.history.items)) {
    return data.history.items;
  }
  return Array.isArray(data && data.analyses) ? data.analyses : [];
}

export function upsertIntelligenceAnalyses(analyses, nextAnalyses) {
  let updated = Array.isArray(analyses) ? analyses : [];
  if (!Array.isArray(nextAnalyses)) return updated;
  for (let index = nextAnalyses.length - 1; index >= 0; index -= 1) {
    updated = upsertIntelligenceAnalysis(updated, nextAnalyses[index]);
  }
  return updated;
}

export function buildSetupIntelligenceState(previousState, setupId, page) {
  const sameSetup = previousState && previousState.setup_id === setupId;
  const previousCache = sameSetup && Array.isArray(previousState.analyses)
    ? previousState.analyses
    : [];
  const pageAnalyses = Array.isArray(page && page.analyses) ? page.analyses : [];
  let analyses = upsertIntelligenceAnalyses(previousCache, pageAnalyses);
  if (page && page.latest) {
    analyses = upsertIntelligenceAnalysis(analyses, page.latest);
  }
  return {
    ...(sameSetup ? previousState : {}),
    setup_id: setupId,
    latest: page ? page.latest : null,
    analyses,
    history: {
      ...(page && page.history ? page.history : {}),
      items: pageAnalyses,
    },
  };
}

export async function loadSetupIntelligenceHistoryPage(setupId, offset) {
  if (!setupId) return currentSetupIntelligence;
  const previousState = currentSetupIntelligence && currentSetupIntelligence.setup_id === setupId
    ? currentSetupIntelligence
    : null;
  const currentLimit = numberOrNull(previousState && previousState.history && previousState.history.limit);
  const limit = currentLimit === null ? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE : currentLimit;
  const requestedOffset = numberOrNull(offset);
  const page = await fetchSetupIntelligence(setupId, {
    limit,
    offset: requestedOffset === null ? 0 : Math.max(0, requestedOffset),
  });
  const previousSelectedId = currentSetupIntelligenceSelectedId;
  setCurrentSetupIntelligence(buildSetupIntelligenceState(previousState, setupId, page));
  const selected = selectedIntelligenceAnalysis(currentSetupIntelligence);
  setCurrentSetupIntelligenceSelectedId(selected ? selected.analysis_id : null);
  if (previousSelectedId !== currentSetupIntelligenceSelectedId) {
    setCurrentSetupIntelligenceComparison(null);
  }
  renderSetupIntelligencePanel(currentSetupIntelligence);
  syncCurrentSetupDetailIntelligence();
  renderSetupDetailJsonOutput();
  return currentSetupIntelligence;
}

export async function ensureSetupIntelligenceAnalysisLoaded(analysisId) {
  if (!currentSetupIntelligence || !analysisId) return null;
  if (
    currentSetupIntelligence.latest
    && currentSetupIntelligence.latest.analysis_id === analysisId
  ) {
    return currentSetupIntelligence.latest;
  }
  const analyses = Array.isArray(currentSetupIntelligence.analyses)
    ? currentSetupIntelligence.analyses
    : [];
  const existing = analyses.find((analysis) => analysis.analysis_id === analysisId);
  if (existing && existing.detail_loaded !== false && Array.isArray(existing.scenarios)) {
    return existing;
  }
  const full = await api(`/api/intelligence/analyses/${encodeURIComponent(analysisId)}`);
  currentSetupIntelligence.analyses = upsertIntelligenceAnalysis(analyses, full);
  if (currentSetupIntelligence.history && Array.isArray(currentSetupIntelligence.history.items)) {
    currentSetupIntelligence.history.items = upsertIntelligenceAnalysis(
      currentSetupIntelligence.history.items,
      full,
    );
  }
  return full;
}

export function upsertIntelligenceAnalysis(analyses, analysis) {
  if (!analysis || !analysis.analysis_id) return analyses;
  let replaced = false;
  const updated = analyses.map((item) => {
    if (item.analysis_id !== analysis.analysis_id) return item;
    replaced = true;
    return analysis;
  });
  if (!replaced) updated.unshift(analysis);
  return updated;
}

export function renderSetupIntelligencePanel(data) {
  const overview = document.getElementById("setup-intelligence-overview");
  const compare = document.getElementById("setup-intelligence-compare");
  const scenarios = document.getElementById("setup-intelligence-scenarios");
  const ambiguities = document.getElementById("setup-intelligence-ambiguities");
  const fields = document.getElementById("setup-intelligence-fields");
  const history = document.getElementById("setup-intelligence-history");
  if (!overview && !compare && !scenarios && !ambiguities && !fields && !history) return;

  const latest = data && data.latest ? data.latest : null;
  const analysisHistory = setupIntelligenceHistoryItems(data);
  const historyMeta = data && data.history ? data.history : null;

  if (!latest) {
    const empty = `<div class="intelligence-empty">Aucune analyse intelligence enregistree pour ce setup.</div>`;
    if (overview) overview.innerHTML = empty;
    if (compare) compare.innerHTML = "";
    if (scenarios) scenarios.innerHTML = "";
    if (ambiguities) ambiguities.innerHTML = "";
    if (fields) fields.innerHTML = "";
    if (history) history.innerHTML = "";
    return;
  }

  const selectedAnalysis = selectedIntelligenceAnalysis(data);
  const selectedConfidence = selectedAnalysis.confidence || {};
  const selectedScenarios = Array.isArray(selectedAnalysis.scenarios) ? selectedAnalysis.scenarios : [];
  const selectedAmbiguities = Array.isArray(selectedAnalysis.ambiguities) ? selectedAnalysis.ambiguities : [];
  const selectedFields = Array.isArray(selectedAnalysis.extracted_fields) ? selectedAnalysis.extracted_fields : [];
  const openAmbiguities = selectedAmbiguities.filter((item) => item.status === "OPEN");
  const errorCount = intelligenceIssueCount(selectedAnalysis.issues, "ERROR");
  const warningCount = intelligenceIssueCount(selectedAnalysis.issues, "WARNING");
  const isLatestView = selectedAnalysis.analysis_id === latest.analysis_id;

  if (overview) {
    overview.innerHTML = [
      intelligenceCell("Confiance", renderConfidencePill(selectedConfidence), true),
      intelligenceCell("Affichage", escapeHtml(isLatestView ? "Latest" : shortId(selectedAnalysis.analysis_id)), true),
      intelligenceCell("Derniere analyse", escapeHtml(formatTime(selectedAnalysis.created_at) || "-"), true),
      intelligenceCell("Scenarios", escapeHtml(String(selectedScenarios.length)), true),
      intelligenceCell("Ambiguites ouvertes", escapeHtml(String(openAmbiguities.length)), true),
      intelligenceCell("Save validation", escapeHtml(validationStateText(selectedAnalysis.save_validation)), true),
      intelligenceCell("Arm validation", escapeHtml(validationStateText(selectedAnalysis.arm_validation)), true),
      intelligenceCell("Parser", escapeHtml(selectedAnalysis.parser_version || "-"), true),
      intelligenceCell("Schema", escapeHtml(selectedAnalysis.schema_version || "-"), true),
      intelligenceCell("Provider", escapeHtml(selectedAnalysis.provider_name || "-"), true),
      intelligenceCell("Issues", escapeHtml(`${errorCount} erreur(s), ${warningCount} warning(s)`), true),
      intelligenceCell("Resume", escapeHtml(selectedConfidence.summary || "-"), true),
      intelligenceCell("Analyse ID", `<code>${escapeHtml(shortId(selectedAnalysis.analysis_id))}</code>`, true),
    ].join("");
  }

  renderSetupIntelligenceComparison(compare, currentSetupIntelligenceComparison);

  if (scenarios) {
    scenarios.innerHTML = selectedScenarios.map((scenario) => {
      const confidence = scenario.confidence || {};
      const config = scenario.canonical_config || {};
      return `
        <article class="intelligence-scenario">
          <div class="intelligence-scenario-head">
            <div>
              <h3>${escapeHtml(scenario.scenario_name || scenario.scenario_id)}</h3>
              <span class="intelligence-scenario-meta">${escapeHtml(scenario.scenario_role || "-")} · ${escapeHtml(scenario.setup_type || "-")}</span>
            </div>
            ${renderConfidencePill(confidence)}
          </div>
          <div class="intelligence-scenario-grid">
            <div>
              <span class="intelligence-scenario-meta">Statut</span>
              <strong>${escapeHtml(scenario.status || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Setup ID</span>
              <strong>${escapeHtml(config.setup_id || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Save</span>
              <strong>${escapeHtml(validationStateText(scenario.save_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Arm</span>
              <strong>${escapeHtml(validationStateText(scenario.arm_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Score</span>
              <strong>${escapeHtml(numberText(confidence.score, 3))}</strong>
            </div>
            <div>
              <span class="intelligence-scenario-meta">Resume</span>
              <strong>${escapeHtml(confidence.summary || "-")}</strong>
            </div>
          </div>
        </article>
      `;
    }).join("") || `<div class="intelligence-empty">Aucun scenario extrait.</div>`;
  }

  if (ambiguities) {
    ambiguities.innerHTML = selectedAmbiguities.map((ambiguity) => {
      const metadata = ambiguity.metadata || {};
      const evidence = metadata.evidence || {};
      const severity = String(ambiguity.severity || metadata.severity || "REVIEW").toUpperCase();
      const kind = String(ambiguity.kind || metadata.kind || "USER_PROVIDED").toUpperCase();
      const impact = numberOrNull(ambiguity.confidence_impact ?? metadata.confidence_impact);
      const action = ambiguity.suggested_action || metadata.suggested_action || "-";
      const sourceLine = evidence.source_line ? `L${evidence.source_line}` : "-";
      return `
      <article class="intelligence-ambiguity">
        <div class="intelligence-ambiguity-head">
          <div>
            <h3>${escapeHtml(ambiguity.message || "Ambiguite")}</h3>
            <span class="intelligence-ambiguity-meta">${escapeHtml(ambiguity.field_path || "-")} · ${escapeHtml(ambiguity.status || "-")}</span>
          </div>
          ${ambiguity.status === "RESOLVED" ? `<span class="confidence-pill high">RESOLVED</span>` : `<span class="confidence-pill ${escapeHtml(ambiguityTone(ambiguity.status, severity))}">${escapeHtml(severity)}</span>`}
        </div>
        <div class="intelligence-ambiguity-grid">
          <div>
            <span class="intelligence-ambiguity-meta">Type</span>
            <strong>${escapeHtml(kind)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Impact confiance</span>
            <strong>${escapeHtml(impact === null ? "-" : `-${Math.round(impact * 100)}%`)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Source</span>
            <strong>${escapeHtml(sourceLine)}</strong>
          </div>
          <div>
            <span class="intelligence-ambiguity-meta">Action</span>
            <strong>${escapeHtml(action)}</strong>
          </div>
        </div>
        ${ambiguity.status === "OPEN" && Array.isArray(ambiguity.options) && ambiguity.options.length ? `
          <div class="intelligence-ambiguity-options">
            ${ambiguity.options.map((option, index) => `
              <button
                type="button"
                data-action="resolve-intelligence-ambiguity"
                data-analysis="${escapeHtml(selectedAnalysis.analysis_id || "")}"
                data-ambiguity="${escapeHtml(ambiguity.ambiguity_id || "")}"
                data-resolution="${escapeHtml(encodeURIComponent(JSON.stringify({ selected_option: option })))}"
              >${escapeHtml(intelligenceOptionLabel(option, index))}</button>
            `).join("")}
          </div>
        ` : ""}
      </article>
    `;
    }).join("") || `<div class="intelligence-empty">Aucune ambiguite ouverte pour cette analyse.</div>`;
  }

  if (fields) {
    const sortedFields = [...selectedFields].sort((left, right) => {
      const leftRank = fieldValidationRank(left.validation_status);
      const rightRank = fieldValidationRank(right.validation_status);
      if (leftRank !== rightRank) return leftRank - rightRank;
      return String(left.canonical_path || "").localeCompare(String(right.canonical_path || ""));
    }).slice(0, 12);
    fields.innerHTML = sortedFields.map((field) => `
      <article class="intelligence-field">
        <span class="intelligence-field-meta">${escapeHtml(field.validation_status || "-")} · ${escapeHtml(field.extraction_method || "-")}</span>
        <strong>${escapeHtml(field.canonical_path || "-")}</strong>
        <code>${escapeHtml(formatFieldValue(field.parsed_value))}</code>
        <div class="intelligence-field-grid">
          <div>
            <span class="intelligence-field-meta">Raw key</span>
            <strong>${escapeHtml(field.raw_key || "-")}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Confiance</span>
            <strong>${escapeHtml(numberText(field.confidence, 3))}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Source</span>
            <strong>${escapeHtml(lineRangeLabel(field.source_line_start, field.source_line_end))}</strong>
          </div>
          <div>
            <span class="intelligence-field-meta">Texte</span>
            <strong>${escapeHtml(field.source_text || "-")}</strong>
          </div>
        </div>
      </article>
    `).join("") || `<div class="intelligence-empty">Aucune provenance exploitable.</div>`;
  }

  if (history) {
    const historyLimit = numberOrNull(historyMeta && historyMeta.limit) ?? SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE;
    const historyOffset = numberOrNull(historyMeta && historyMeta.offset) ?? 0;
    const historyTotal = numberOrNull(historyMeta && historyMeta.total_count);
    const historyHasMore = Boolean(historyMeta && historyMeta.has_more);
    const historyStart = analysisHistory.length ? historyOffset + 1 : 0;
    const historyEnd = analysisHistory.length ? historyOffset + analysisHistory.length : 0;
    const historyRange = analysisHistory.length
      ? `Revisions ${historyStart}-${historyEnd}${historyTotal !== null ? ` / ${historyTotal}` : ""}`
      : "Aucun historique d'analyse pour ce setup.";
    history.innerHTML = analysisHistory.length ? `
      <div class="intelligence-history-toolbar">
        <span class="intelligence-history-range">${escapeHtml(historyRange)}</span>
        <div class="intelligence-history-pagination">
          <button
            type="button"
            class="secondary-button"
            data-action="intelligence-history-page"
            data-offset="${escapeHtml(String(Math.max(0, historyOffset - historyLimit)))}"
            ${historyOffset <= 0 ? "disabled" : ""}
          >Plus recents</button>
          <button
            type="button"
            class="secondary-button"
            data-action="intelligence-history-page"
            data-offset="${escapeHtml(String(historyOffset + historyLimit))}"
            ${historyHasMore ? "" : "disabled"}
          >Plus anciens</button>
        </div>
      </div>
      ${analysisHistory.map((analysis) => `
        <article class="intelligence-history-item ${analysis.analysis_id === selectedAnalysis.analysis_id ? "active" : ""}">
          <div class="intelligence-history-head">
            <div>
              <h3>${escapeHtml(shortId(analysis.analysis_id))}</h3>
              <span class="intelligence-history-meta">${escapeHtml(formatTime(analysis.created_at) || "-")}</span>
            </div>
            ${renderConfidencePill(analysis.confidence || {})}
          </div>
          <div class="intelligence-history-grid">
            <div>
              <span class="intelligence-history-meta">Scenarios</span>
              <strong>${escapeHtml(String(analysisScenarioCount(analysis)))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Ambiguites ouvertes</span>
              <strong>${escapeHtml(String(analysisOpenAmbiguityCount(analysis)))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Save</span>
              <strong>${escapeHtml(validationStateText(analysis.save_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Arm</span>
              <strong>${escapeHtml(validationStateText(analysis.arm_validation))}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Parser</span>
              <strong>${escapeHtml(analysis.parser_version || "-")}</strong>
            </div>
            <div>
              <span class="intelligence-history-meta">Schema</span>
              <strong>${escapeHtml(analysis.schema_version || "-")}</strong>
            </div>
          </div>
          <div class="intelligence-history-actions">
            <button
              type="button"
              data-action="view-intelligence-analysis"
              data-analysis="${escapeHtml(analysis.analysis_id || "")}"
            >${analysis.analysis_id === selectedAnalysis.analysis_id ? "Analyse affichee" : "Afficher"}</button>
            ${analysis.analysis_id !== selectedAnalysis.analysis_id ? `
              <button
                type="button"
                class="secondary-button"
                data-action="compare-intelligence-analysis"
                data-analysis="${escapeHtml(analysis.analysis_id || "")}"
              >Comparer</button>
            ` : ""}
            ${analysis.analysis_id !== latest.analysis_id ? `
              <button
                type="button"
                class="secondary-button"
                data-action="rollback-intelligence-analysis"
                data-analysis="${escapeHtml(analysis.analysis_id || "")}"
              >Restaurer</button>
            ` : ""}
          </div>
        </article>
      `).join("")}
    ` : `<div class="intelligence-empty">${escapeHtml(historyRange)}</div>`;
  }
}

export function selectedIntelligenceAnalysis(data) {
  const latest = data && data.latest ? data.latest : null;
  const analysisCache = data && Array.isArray(data.analyses) ? data.analyses : [];
  const analysisHistory = setupIntelligenceHistoryItems(data);
  if (!analysisHistory.length) {
    setCurrentSetupIntelligenceSelectedId(latest ? latest.analysis_id : null);
    return latest;
  }
  if (latest && currentSetupIntelligenceSelectedId === latest.analysis_id) {
    return latest;
  }
  const selected = analysisCache.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId)
    || analysisHistory.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId);
  if (selected) return selected;
  setCurrentSetupIntelligenceSelectedId(latest ? latest.analysis_id : analysisHistory[0].analysis_id);
  if (latest && currentSetupIntelligenceSelectedId === latest.analysis_id) {
    return latest;
  }
  return analysisHistory.find((analysis) => analysis.analysis_id === currentSetupIntelligenceSelectedId) || latest || analysisHistory[0];
}

export function syncCurrentSetupDetailIntelligence() {
  if (!currentSetupDetailInfo) return;
  currentSetupDetailInfo.intelligence = currentSetupIntelligence
    ? {
      ...currentSetupIntelligence,
      selected_analysis_id: currentSetupIntelligenceSelectedId,
      comparison: currentSetupIntelligenceComparison,
    }
    : null;
}

export function intelligenceCell(label, value, allowHtml = false) {
  return `
    <div class="intelligence-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${allowHtml ? value : escapeHtml(value)}</strong>
    </div>
  `;
}

export function renderSetupIntelligenceComparison(container, comparison) {
  if (!container) return;
  if (!comparison) {
    container.innerHTML = "";
    return;
  }
  const summary = comparison.summary || {};
  const fieldChanges = Array.isArray(comparison.field_changes) ? comparison.field_changes : [];
  container.innerHTML = `
    <article class="intelligence-compare-card">
      <div class="intelligence-compare-head">
        <div>
          <h3>Comparaison de revisions</h3>
          <span class="intelligence-history-meta">${escapeHtml(shortId(comparison.left?.analysis_id || ""))} -> ${escapeHtml(shortId(comparison.right?.analysis_id || ""))}</span>
        </div>
        <div class="intelligence-history-actions">
          <button type="button" class="secondary-button" data-action="clear-intelligence-comparison">Masquer</button>
        </div>
      </div>
      <div class="intelligence-overview">
        ${[
          intelligenceCell("Champs modifies", String(summary.field_change_count || 0)),
          intelligenceCell("Valeurs changees", String(summary.changed_count || 0)),
          intelligenceCell("Ajouts", String(summary.added_count || 0)),
          intelligenceCell("Suppressions", String(summary.removed_count || 0)),
          intelligenceCell("Delta confiance", formatComparisonDelta(summary.confidence_delta), true),
          intelligenceCell("Delta erreurs", formatComparisonDelta(summary.error_delta), true),
          intelligenceCell("Delta warnings", formatComparisonDelta(summary.warning_delta), true),
          intelligenceCell("Delta ambiguites", formatComparisonDelta(summary.open_ambiguity_delta), true),
        ].join("")}
      </div>
      <div class="intelligence-compare-columns">
        <div class="intelligence-compare-side">
          <span class="intelligence-history-meta">Revision affichee</span>
          <h4>${escapeHtml(comparison.left?.scenario_name || "-")}</h4>
          <div>${renderConfidencePill(comparison.left?.confidence || {})}</div>
          <p><strong>${escapeHtml(comparison.left?.status || "-")}</strong></p>
        </div>
        <div class="intelligence-compare-side">
          <span class="intelligence-history-meta">Revision comparee</span>
          <h4>${escapeHtml(comparison.right?.scenario_name || "-")}</h4>
          <div>${renderConfidencePill(comparison.right?.confidence || {})}</div>
          <p><strong>${escapeHtml(comparison.right?.status || "-")}</strong></p>
        </div>
      </div>
      <div class="intelligence-compare-fields">
        ${fieldChanges.map((change) => `
          <article class="intelligence-compare-field">
            <div class="intelligence-compare-field-head">
              <code>${escapeHtml(change.field_path || "-")}</code>
              <span class="confidence-pill ${comparisonTone(change.change_type)}">${escapeHtml(change.change_type || "-")}</span>
            </div>
            <div class="intelligence-compare-field-grid">
              <div>
                <span class="intelligence-field-meta">Avant</span>
                <strong>${formatComparisonValue(change.left_value)}</strong>
              </div>
              <div>
                <span class="intelligence-field-meta">Apres</span>
                <strong>${formatComparisonValue(change.right_value)}</strong>
              </div>
            </div>
          </article>
        `).join("") || `<div class="intelligence-empty">Aucune difference detectee entre ces deux revisions.</div>`}
      </div>
    </article>
  `;
}

export function formatComparisonValue(value) {
  if (value === null || typeof value === "undefined") {
    return `<span class="intelligence-compare-empty">absent</span>`;
  }
  if (typeof value === "string") {
    return `<code>${escapeHtml(value)}</code>`;
  }
  return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
}

export function formatComparisonDelta(value) {
  const numeric = numberOrNull(value);
  if (numeric === null) return escapeHtml("-");
  const prefix = numeric > 0 ? "+" : "";
  return escapeHtml(`${prefix}${numeric}`);
}

export function comparisonTone(changeType) {
  const normalized = String(changeType || "").toUpperCase();
  if (normalized === "ADDED") return "high";
  if (normalized === "REMOVED") return "review";
  return "medium";
}

export function renderConfidencePill(confidence) {
  const score = numberOrNull(confidence && confidence.score);
  const label = String((confidence && confidence.label) || "REVIEW").toUpperCase();
  const tone = confidenceTone(label);
  const text = score === null
    ? label
    : `${label} · ${Math.round(score * 100)}%`;
  return `<span class="confidence-pill ${escapeHtml(tone)}">${escapeHtml(text)}</span>`;
}

export function confidenceTone(label) {
  if (label === "HIGH") return "high";
  if (label === "MEDIUM") return "medium";
  if (label === "INVALID") return "invalid";
  return "review";
}

export function ambiguityTone(status, severity) {
  if (status === "RESOLVED") return "high";
  if (severity === "BLOCKER") return "invalid";
  if (severity === "INFO") return "medium";
  return "review";
}

export function validationStateText(validation) {
  if (!validation) return "-";
  return validation.allowed ? "ALLOWED" : "REVIEW";
}

export function intelligenceIssueCount(issues, severity) {
  if (!Array.isArray(issues)) return 0;
  return issues.filter((item) => item && item.severity === severity).length;
}

export function analysisScenarioCount(analysis) {
  const summaryCount = numberOrNull(analysis && analysis.scenario_count);
  if (summaryCount !== null) return summaryCount;
  return Array.isArray(analysis && analysis.scenarios) ? analysis.scenarios.length : 0;
}

export function analysisOpenAmbiguityCount(analysis) {
  const summaryCount = numberOrNull(analysis && analysis.open_ambiguity_count);
  if (summaryCount !== null) return summaryCount;
  const ambiguities = Array.isArray(analysis && analysis.ambiguities) ? analysis.ambiguities : [];
  return ambiguities.filter((item) => item.status === "OPEN").length;
}

export function intelligenceOptionLabel(option, index) {
  if (option && typeof option === "object") {
    return option.scenario_name
      || option.label
      || option.scenario_id
      || `Option ${index + 1}`;
  }
  return `Option ${index + 1}`;
}

export function shortId(value) {
  const text = String(value || "");
  if (text.length <= 22) return text || "-";
  return `${text.slice(0, 10)}...${text.slice(-8)}`;
}

export function formatFieldValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function lineRangeLabel(start, end) {
  if (start == null && end == null) return "-";
  if (start == null) return `L${end}`;
  if (end == null || start === end) return `L${start}`;
  return `L${start}-${end}`;
}

export function fieldValidationRank(status) {
  if (status === "INVALID") return 0;
  if (status === "REVIEW") return 1;
  return 2;
}

export function showSetupIntelligenceMessage(text, kind = "") {
  const message = document.getElementById("setup-intelligence-message");
  if (!message) return;
  message.hidden = !text;
  message.textContent = text || "";
  message.classList.remove("error", "success");
  if (kind) message.classList.add(kind);
}
