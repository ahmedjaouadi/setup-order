import { latestQuoteForSymbol, quotePrice } from "./market-quotes.js";
import { analysisItemForSetup, fallbackAnalysisTrace, latestAnalysisForSetup, nextStepFromAction } from "./setup-analysis.js";
import { normalizeCheckState, opportunityScorePayload, setupDetailPath, setupOpportunityState } from "./setups-list.js";
import { latestSnapshot } from "./state.js";
import { escapeHtml, formatAge, maybeMoney, maybePercent, secondsSince, signalBadgeStyle } from "./ui-helpers.js";

export const OPPORTUNITY_RADAR_LIMIT = 6;

export const OPPORTUNITY_RADAR_TERMINAL_STATUSES = new Set([
  "CANCELLED",
  "CLOSED",
  "COMPLETED",
  "DELETED",
  "EMERGENCY_STOP",
  "ERROR",
  "ERROR_REQUIRES_MANUAL_REVIEW",
  "EXPIRED",
  "FILLED",
  "IN_POSITION",
  "INVALIDATED",
  "MANAGING_POSITION",
  "REJECTED",
]);

export function renderOpportunityRadar(setups) {
  const list = document.getElementById("opportunity-radar-list");
  const launchpad = document.getElementById("opportunity-radar-launchpad");
  const summary = document.getElementById("opportunity-radar-summary");
  const count = document.getElementById("opportunity-radar-count");
  const secondaryCount = document.getElementById("opportunity-radar-secondary-count");
  if (!list && !launchpad && !summary && !count && !secondaryCount) return;
  const events = (latestSnapshot && Array.isArray(latestSnapshot.events))
    ? latestSnapshot.events
    : [];
  const items = (setups || [])
    .filter((setup) => !setupRadarTerminal(setup))
    .map((setup) => opportunityRadarItem(setup, events))
    .sort(compareOpportunityRadarItems);
  const focusItems = opportunityRadarFocusItems(items);
  const focusKeys = new Set(focusItems.map(opportunityRadarItemKey));
  const detailItems = items
    .filter((item) => !focusKeys.has(opportunityRadarItemKey(item)))
    .slice(0, OPPORTUNITY_RADAR_LIMIT);

  if (count) {
    count.textContent = opportunityRadarCountText(focusItems.length, "dans la hot zone");
  }
  if (summary) {
    summary.innerHTML = renderOpportunityRadarSummary(items, focusItems);
  }
  if (secondaryCount) {
    secondaryCount.textContent = detailItems.length
      ? opportunityRadarCountText(detailItems.length, "en surveillance")
      : "";
  }
  if (launchpad) {
    launchpad.innerHTML = focusItems.map(renderOpportunityRadarFocusCard).join("")
      || `<article class="opportunity-radar-empty opportunity-radar-empty-focus">
        ${items.length
          ? "Aucun setup en hot zone pour le moment. Le radar continue la surveillance."
          : "Aucun setup surveille. Charge un setup pour alimenter le radar."}
      </article>`;
  }
  if (!list) return;
  list.innerHTML = detailItems.map(renderOpportunityRadarCard).join("")
    || `<article class="opportunity-radar-empty">
      ${items.length
        ? "Tous les setups suivis sont deja visibles dans la hot zone."
        : "Aucun setup surveille. Charge un setup pour alimenter le radar."}
    </article>`;
}

export function opportunityRadarItem(setup, events) {
  const analysis = latestAnalysisForSetup(setup, events);
  const item = analysisItemForSetup(setup, analysis);
  const latestQuote = latestQuoteForSymbol(events, setup.symbol);
  const trace = item && item.trace
    ? item.trace
    : fallbackAnalysisTrace(setup, latestQuote, item);
  const signal = setupOpportunityState(setup, item, trace);
  const scorePayload = opportunityScorePayload(item);
  const remaining = opportunityRadarRemainingChecks(scorePayload, trace);
  const state = opportunityRadarState(signal);
  const analysisAge = analysis ? secondsSince(analysis.timestamp) : null;
  return {
    setup,
    analysis,
    item,
    trace,
    signal,
    scorePayload,
    remaining,
    state,
    analysisAge,
    latestQuote,
  };
}

export function renderOpportunityRadarSummary(items, focusItems) {
  const readyAuto = items.filter((item) => item.state.key === "ready-auto").length;
  const readyWatch = items.filter((item) => item.state.key === "ready-watch").length;
  const nearReady = items.filter((item) => item.state.key === "near").length;
  const watchOnly = items.filter((item) => !item.signal.autoExecution).length;
  const leader = items[0] || null;
  const topScore = leader ? leader.signal.percent : null;
  return [
    opportunityRadarSummaryCell(
      "Hot zone",
      focusItems.length,
      focusItems.length ? "ready" : "idle",
      focusItems.length ? "setup(s) a regarder maintenant" : "zone calme pour le moment",
    ),
    opportunityRadarSummaryCell(
      "Ready Auto",
      readyAuto,
      readyAuto ? "ready" : "idle",
      readyAuto ? "peuvent declencher via TWS" : "aucune execution auto prete",
    ),
    opportunityRadarSummaryCell(
      "Ready Watch",
      readyWatch + nearReady,
      readyWatch + nearReady ? "near" : "idle",
      "surveillance proche du depart",
    ),
    opportunityRadarSummaryCell(
      "Suivi seul",
      watchOnly,
      watchOnly ? "watch" : "idle",
      "observes sans ordre automatique",
    ),
    opportunityRadarSummaryCell(
      "Leader",
      leader ? leader.setup.symbol || "-" : "-",
      leader ? leader.state.tone : "idle",
      leader && topScore !== null ? `${maybePercent(topScore)} de proximite` : "aucun setup charge",
    ),
  ].join("");
}

export function opportunityRadarSummaryCell(label, value, tone, note) {
  return `
    <article class="opportunity-radar-stat ${escapeHtml(tone)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(note || "")}</small>
    </article>
  `;
}

export function opportunityRadarFocusItems(items) {
  const hotItems = items.filter((item) => ["ready-auto", "ready-watch", "near"].includes(item.state.key));
  return (hotItems.length ? hotItems : items).slice(0, 4);
}

export function opportunityRadarItemKey(item) {
  return `${item.setup.setup_id || ""}::${item.setup.symbol || ""}`;
}

export function opportunityRadarCountText(count, suffix) {
  return `${count} setup${count > 1 ? "s" : ""} ${suffix}`;
}

export function renderOpportunityRadarFocusCard(item) {
  const setup = item.setup;
  const signal = item.signal;
  const state = item.state;
  const remaining = opportunityRadarRemainingText(item);
  const age = item.analysisAge === null ? "Aucune analyse" : `Analyse ${formatAge(item.analysisAge)}`;
  const nextStep = opportunityRadarNextStep(item);
  const priceText = maybeMoney(quotePrice(item.latestQuote));
  const width = Math.max(0, Math.min(signal.percent || 0, 100));
  return `
    <article class="opportunity-radar-focus-card ${escapeHtml(state.tone)}">
      <div class="opportunity-radar-focus-head">
        <span class="setup-signal-pill ${escapeHtml(state.tone)}" style="${escapeHtml(signalBadgeStyle(signal))}">${escapeHtml(state.label)}</span>
        <span class="opportunity-radar-focus-mode">${escapeHtml(signal.autoExecution ? "Auto TWS ON" : "Watch only")}</span>
      </div>
      <div class="opportunity-radar-card-head">
        <div>
          <a class="opportunity-radar-symbol" href="${setupDetailPath(setup)}">${escapeHtml(setup.symbol || "-")}</a>
          <span class="opportunity-radar-meta">${escapeHtml(setup.setup_type || "-")} · ${escapeHtml(setup.status || "-")}</span>
        </div>
        <div class="opportunity-radar-score">
          <strong>${escapeHtml(maybePercent(signal.percent))}</strong>
          <span>proximite</span>
        </div>
      </div>
      <div class="opportunity-radar-bar" aria-label="Proximite ${escapeHtml(maybePercent(signal.percent))}">
        <span style="width:${escapeHtml(String(width))}%"></span>
      </div>
      <div class="opportunity-radar-focus-metrics">
        <article>
          <span>Dernier prix</span>
          <strong>${escapeHtml(priceText)}</strong>
        </article>
        <article>
          <span>Blocage principal</span>
          <strong>${escapeHtml(remaining)}</strong>
        </article>
        <article>
          <span>Prochaine etape</span>
          <strong>${escapeHtml(nextStep)}</strong>
        </article>
        <article>
          <span>Derniere analyse</span>
          <strong>${escapeHtml(age)}</strong>
        </article>
      </div>
    </article>
  `;
}

export function renderOpportunityRadarCard(item) {
  const setup = item.setup;
  const signal = item.signal;
  const state = item.state;
  const remaining = opportunityRadarRemainingText(item);
  const age = item.analysisAge === null ? "Aucune analyse" : `Analyse ${formatAge(item.analysisAge)}`;
  const nextStep = opportunityRadarNextStep(item);
  const priceText = maybeMoney(quotePrice(item.latestQuote));
  const width = Math.max(0, Math.min(signal.percent || 0, 100));
  return `
    <article class="opportunity-radar-card ${escapeHtml(state.tone)}">
      <div class="opportunity-radar-card-head">
        <div>
          <a class="opportunity-radar-symbol" href="${setupDetailPath(setup)}">${escapeHtml(setup.symbol || "-")}</a>
          <span class="opportunity-radar-meta">${escapeHtml(setup.setup_type || "-")} · ${escapeHtml(setup.status || "-")}</span>
        </div>
        <span class="setup-signal-pill ${escapeHtml(state.tone)}" style="${escapeHtml(signalBadgeStyle(signal))}">${escapeHtml(state.label)}</span>
      </div>
      <div class="opportunity-radar-score">
        <strong>${escapeHtml(maybePercent(signal.percent))}</strong>
        <span>${escapeHtml(signal.autoExecution ? "Auto TWS ON" : "Suivi seul - aucun ordre auto")}</span>
      </div>
      <div class="opportunity-radar-bar" aria-label="Proximite ${escapeHtml(maybePercent(signal.percent))}">
        <span style="width:${escapeHtml(String(width))}%"></span>
      </div>
      <dl class="opportunity-radar-detail">
        <div>
          <dt>Dernier prix</dt>
          <dd>${escapeHtml(priceText)}</dd>
        </div>
        <div>
          <dt>Blocage restant</dt>
          <dd>${escapeHtml(remaining)}</dd>
        </div>
        <div>
          <dt>Derniere analyse</dt>
          <dd>${escapeHtml(age)}</dd>
        </div>
        <div>
          <dt>Prochaine action</dt>
          <dd>${escapeHtml(nextStep)}</dd>
        </div>
      </dl>
    </article>
  `;
}

export function opportunityRadarState(signal) {
  if (signal.action === "ENTRY_READY") {
    return signal.autoExecution
      ? { key: "ready-auto", label: "READY AUTO", tone: "ready" }
      : { key: "ready-watch", label: "READY WATCH", tone: "near" };
  }
  if (signal.score >= signal.nearReadyThreshold) {
    return { key: "near", label: "NEAR READY", tone: "near" };
  }
  if (signal.score >= 0.70) {
    return { key: "watching", label: "WATCHING", tone: "watch" };
  }
  return { key: "waiting", label: "WAITING", tone: "idle" };
}

export function opportunityRadarRemainingChecks(scorePayload, trace) {
  const fromScore = [];
  if (scorePayload && typeof scorePayload === "object") {
    ["blocking_checks", "waiting_checks"].forEach((key) => {
      if (Array.isArray(scorePayload[key])) fromScore.push(...scorePayload[key]);
    });
  }
  if (fromScore.length) return fromScore;
  const checks = Array.isArray(trace && trace.checks) ? trace.checks : [];
  return checks.filter((check) => {
    const state = normalizeCheckState(check && check.state);
    const label = String((check && check.label) || "");
    return ["wait", "bad", "error"].includes(state) && !opportunityRadarIgnoredCheck(label);
  });
}

export function opportunityRadarIgnoredCheck(label) {
  return [
    "Suivi setup",
    "Setup actif",
    "Execution auto TWS",
    "Controle risque",
  ].includes(String(label || ""));
}

export function opportunityRadarRemainingText(item) {
  if (item.signal.action === "ENTRY_READY") {
    return item.signal.autoExecution
      ? "Pret: execution automatique autorisee."
      : "Pret: Auto TWS OFF, aucune execution automatique.";
  }
  const check = item.remaining[0];
  if (check) {
    const label = check.label || "Condition";
    const actual = check.actual === undefined || check.actual === null ? "" : String(check.actual);
    const expected = check.expected === undefined || check.expected === null ? "" : String(check.expected);
    if (actual && expected) return `${label}: ${actual} / attendu ${expected}`;
    if (actual) return `${label}: ${actual}`;
    return String(label);
  }
  return item.signal.reason || "Aucun blocage detaille, attendre le prochain scan.";
}

export function opportunityRadarNextStep(item) {
  const scoreNextStep = item.scorePayload && item.scorePayload.next_step
    ? String(item.scorePayload.next_step)
    : "";
  return scoreNextStep
    || (item.trace && item.trace.next_step)
    || nextStepFromAction(item.signal.action, item.signal.reason);
}

export function compareOpportunityRadarItems(left, right) {
  if (right.signal.score !== left.signal.score) return right.signal.score - left.signal.score;
  if (right.signal.autoExecution !== left.signal.autoExecution) {
    return right.signal.autoExecution ? 1 : -1;
  }
  return String(left.setup.symbol || "").localeCompare(String(right.setup.symbol || ""));
}

export function setupRadarTerminal(setup) {
  return OPPORTUNITY_RADAR_TERMINAL_STATUSES.has(setup.status);
}
