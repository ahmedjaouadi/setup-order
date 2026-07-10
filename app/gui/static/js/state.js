// État global partagé entre modules (extrait de app.js — aucun changement de comportement).
// Lecture : import du binding (live). Écriture : passer par le setter correspondant.

export const SETUP_CHART_DEFAULT_TIMEFRAME = "1d";

export let latestSnapshot = null;
export let currentSetupConfig = null;
export let currentSetupDetailInfo = null;
export let currentSetupIntelligence = null;
export let currentSetupIntelligenceSelectedId = null;
export let currentSetupIntelligenceComparison = null;
export let currentSetupArmStatus = null;
export let setupConfigFormDirty = false;
export let setupConfigEditorDirty = false;
export let currentSetupDetailSetup = null;
export let currentSetupSymbolEvents = [];
export let forecastWatchlistBySymbol = {};
export let setupChartTimeframe = SETUP_CHART_DEFAULT_TIMEFRAME;
export let setupChartDataMessage = "";
export let setupChartDataMeta = {};

export function setLatestSnapshot(value) {
  latestSnapshot = value;
}
export function setCurrentSetupConfig(value) {
  currentSetupConfig = value;
}
export function setCurrentSetupDetailInfo(value) {
  currentSetupDetailInfo = value;
}
export function setCurrentSetupIntelligence(value) {
  currentSetupIntelligence = value;
}
export function setCurrentSetupIntelligenceSelectedId(value) {
  currentSetupIntelligenceSelectedId = value;
}
export function setCurrentSetupIntelligenceComparison(value) {
  currentSetupIntelligenceComparison = value;
}
export function setCurrentSetupArmStatus(value) {
  currentSetupArmStatus = value;
}
export function setSetupConfigFormDirty(value) {
  setupConfigFormDirty = value;
}
export function setSetupConfigEditorDirty(value) {
  setupConfigEditorDirty = value;
}
export function setCurrentSetupDetailSetup(value) {
  currentSetupDetailSetup = value;
}
export function setCurrentSetupSymbolEvents(value) {
  currentSetupSymbolEvents = value;
}
export function setForecastWatchlistBySymbol(value) {
  forecastWatchlistBySymbol = value;
}
export function setSetupChartTimeframe(value) {
  setupChartTimeframe = value;
}
export function setSetupChartDataMessage(value) {
  setupChartDataMessage = value;
}
export function setSetupChartDataMeta(value) {
  setupChartDataMeta = value;
}
