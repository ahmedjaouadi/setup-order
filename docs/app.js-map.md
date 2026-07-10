# Cartographie de `app/gui/static/js/app.js`

Phase 2b de la mission refactoring (2026-07-10). Fichier : **8 744 lignes**, **444 définitions top-level**
(~385 fonctions, ~29 constantes, 30 variables d'état `let`). Chargé en `<script>` classique par `base.html`
(pas de `type="module"`). Les tailles sont approximatives (distance jusqu'à la définition suivante).

Toutes les fonctions sont listées ci-dessous, directement regroupées par **domaine fonctionnel proposé**
(ce qui couvre à la fois l'inventaire et le regroupement). Format : `ligne · taille · nom — rôle`.

## 1. État global partagé (30 variables, lignes 2–39, 268–269, 8459–8464)

| Variable | Écrite par | Lue par |
|---|---|---|
| `latestSnapshot` | `renderSnapshot` | dashboard, setups-list, radar, ordres, `init`, `displaySetupStatus`… (12 fn) |
| `currentSetupConfig` | `renderSetupDetail`, `wireSetupConfigEditor` | config-editor, `wireSetupIntelligencePanel` |
| `currentSetupDetailInfo` | `renderSetupDetail` (+ mutation par `syncCurrentSetupDetailIntelligence`) | JSON detail, clipboard, chart refresh |
| `currentSetupIntelligence` / `...SelectedId` / `...Comparison` | intelligence + `renderSetupDetail` + `wireActionButtons` | intelligence, `wireActionButtons` |
| `currentSetupArmStatus` | `renderSetupDetail` | `setupDetailSummaryValues`, `syncSetupConfigActions` |
| `setupConfigFormDirty` / `setupConfigEditorDirty` | detail + config-editor | config-editor, `refreshActiveViews` |
| `setupChartState` | `renderSetupChart` | tout le module chart (15 fn) |
| `setupChartResizeTimer` / `setupChartInteractionsWired` | top-level resize / `wireSetupChartInteractions` | idem |
| `setupChartTimeframe` / `setupChartDataMessage` / `setupChartDataMeta` | timeframe controls / `fetchSetupChartQuotes` | chart, detail, market summary |
| `marketContextState` | mutations de propriétés (page + controls) | market-context (6 fn) |
| `marketContextRefreshTimer` | `scheduleMarketContextRefresh` | idem |
| `appAutoRefreshTimer` / `appAutoRefreshInFlight` | `scheduleAutoRefresh` / `refreshActiveViews` | idem |
| `currentSetupDetailSetup` / `currentSetupSymbolEvents` | `renderSetupDetail` | detail, chart, forecast, snapshot |
| `forecastWatchlistBySymbol` | `refreshForecastWatchlist` | `setupForecastForSetup` (setups-list) |
| `setupsColumnOrder` / `setupsSearchQuery` | colonnes / recherche | setups-list |
| `dashEquityHistory`, `dashLiveEquity`, `dashLastUpdate`, `dashEquityTimer`, `dashAgoTimer`, `dashCurveDrawn` | dashboard-premium | dashboard-premium uniquement |

→ Destination : module `state.js` avec accesseurs get/set (extrait en premier).

## 2. `ui-helpers` — formatage, badges, DOM, modales (sans dépendance)

282·9 `escapeHtml` — échappe le HTML · 291·5 `money` · 296·6 `maybeMoney` · 302·6 `maybePercent` · 308·8 `maybeProbability` · 316·7 `signedPercent` — formatage monétaire/% · 323 `STATUS_BADGE_LABELS` · 388·9 `statusBadge` · 397·5 `statusBadgeStyle` · 402·6 `statusLabel` · 408·8 `statusClassName` · 492·6 `formatStatusList` · 498·11 `statusProfile` · 509 `STATUS_BADGE_PROFILES` · 592·7 `statusBadgeProfile` · 599·18 `signalBadgeStyle` · 617·14 `badgeStyleFromHue` · 631·9 `hashString` — badges de statut · 867·11 `toast` · 878·9 `compactToastMessage` — notifications · 1115·6 `timeWithAge` · 1121·11 `formatAge` · 1132·7 `secondsSince` · 2992·7 `formatTime` — temps · 1209·5 `setText` · 1214·11 `setPnlTone` · 1523·9 `toneForAge` · 1532·14 `syncAgeChipLabel` · 1546·6 `setToneData` · 1570·8 `pnlClass` — helpers DOM/tonalité · 2678·4 `cssSafeId` · 2973·7 `dlRows` · 2980·8 `formatDetailValue` · 2988·4 `emptyRow` — rendus tabulaires · 3133·12 `onClick` — wiring click générique · 3211·10 `openModal` · 3221·8 `closeModal` · 3229·24 `wireModals` — modales · 3394·8 `formData` · 4052·6 `yesNo` · 6035·5 `setButtonDisabled` · 6665·6 `removeUndefinedValues` · 6671·6 `numberOrNull` · 6677·8 `firstNumber` · 6685·5 `numberText` · 7621·4 `isPlainObject` · 7625·5 `structuredCloneSafe` — utilitaires purs.

## 3. `api-client` — accès backend

640·16 `api` — fetch JSON avec erreurs détaillées · 656·17 `optionalApi` — fetch tolérant (404→null) · 673·21 `formatErrorDetail` — formate le corps d'erreur · 3048·15 `connectWebSocket` — WS de rafraîchissement.

## 4. `setup-messages` — humanisation des messages de validation de setup

85 `SETUP_VALIDATION_MESSAGE_HINTS` · 694·52 `formatSetupValidationDetail` · 746·16 `normalizeDetailMessages` · 762·5 `validationMessagesText` · 767·14 `uniqueMessages` · 781·9 `setupSemanticIssueMessages` · 790·22 `setupMappedFieldLines` · 812·13 `isKnownSetupValidationMessage` · 825·34 `humanizeSetupValidationMessage` · 859·4 `formatBulletSection` · 863·4 `plainObjectOrNull` — utilisés par setup-form et wireActionButtons.

## 5. `clipboard`

887·13 `copySetupTemplateToClipboard` — copie template (piège : pas d'await réseau avant) · 900·20 `fallbackCopyTextToClipboard` — fallback execCommand · 5605·15 `copySetupDetailInfoToClipboard` — copie via ClipboardItem+promesse de Blob.

## 6. `dashboard` — page d'accueil, runtime, santé moteur

920·19 `renderSnapshot` — écrit `latestSnapshot`, dispatch des rendus · 939·11 `renderRuntime` · 950·17 `setStatus` · 967·148 `renderEngineHealth` ⚠ >80 l. · 1139·49 `renderMetrics` · 1188·8 `renderPnlMetricText` · 1196·8 `brokerPnlSourceLabel` · 1204·5 `renderRemainingRiskMetricText` · 1369·18 `renderDashboard` · 1387·21 `renderExecutiveBrief` · 1408·95 `buildExecutiveBrief` ⚠ >80 l. · 1503·10 `compactReasonList` · 1513·10 `compactSymbols` · 1552·18 `renderStockPnl` · 2431·48 `renderBrokerReality` · 2479 `SAFETY_GATE_CONDITION_LABELS` · 2489·33 `renderSafetyGate` · 2522·38 `renderRiskProtection` · 2560·6 `brokerSyncLabel`.

## 7. `dashboard-premium` — widgets equity/donut (lignes 8456–8744, bloc autonome)

8456 `DASH_PALETTE` · 8457 `dashLastValues` · 8458 `dashCountUpTimers` · 8466 `dashCurrencyFmt` · 8473·6 `dashFormatCurrency` · 8479·7 `dashFormatSigned` · 8486·8 `initDashboardPremium` · 8494·11 `fetchEquityHistory` · 8505·19 `updateEquityLegend` · 8524·11 `dashSeries` · 8535·60 `dashRedrawEquity` · 8595·26 `renderDashboardPremium` · 8621·28 `dashSetMoney` · 8649·19 `dashCountUp` · 8668·7 `dashFlash` · 8675·9 `dashUpdatedAgoTick` · 8684·61 `drawAllocationDonut`.

## 8. `setups-list` — table des setups, colonnes, arm/disarm

126 `SETUPS_COLUMNS_STORAGE_KEY` · 128·139 `SETUPS_TABLE_COLUMNS` — définitions de colonnes · 267 `DEFAULT_SETUPS_COLUMN_ORDER` · 416 `SETUP_NON_ARMABLE_STATUSES` · 423 `SETUP_REVALIDATION_REASON_LABELS` · 447·4 `setupStatusReason` · 451·4 `setupLastRevalidatedAt` · 455·6 `revalidationReasonLabel` · 461·5 `setupIsArmable` · 466·12 `formatRevalidatedAt` · 478·14 `renderSetupRevalidationCell` · 1225·4 `setupDetailPath` · 1229·9 `loadSetupsColumnOrder` · 1238·14 `normalizeSetupsColumnOrder` · 1252·11 `saveSetupsColumnOrder` · 1263·7 `orderedSetupsColumns` · 1270·25 `renderSetupsColumnControls` · 1295·7 `renderSetupsColumnHeader` · 1302·12 `moveSetupsColumn` · 1314·12 `reorderSetupsColumn` · 1326·7 `resetSetupsColumns` · 1333·6 `filterSetups` · 1339·17 `setupSearchText` · 1356·5 `setupInitialTrailingStop` · 1361·8 `renderSetupsCount` · 1954·16 `renderSetups` · 1970·5 `setupForecastForSetup` · 1975·10 `renderTimesfmScoreCell` · 1985·5 `renderTimesfmMoveCell` · 2304·7 `setupRowClass` · 2311·28 `renderSetupSignalCell` · 2339·5 `renderSetupPriceCell` · 2344·10 `setupSignalState` · 2354·28 `setupOpportunityState` · 2382·6 `opportunityScorePayload` · 2388·22 `analysisTraceScore` · 2410·8 `normalizeCheckState` · 2418·8 `fallbackSetupProgress` · 2426·5 `setupAutoExecutionEnabled` · 3063·50 `wireSetupsColumnControls` · 3402·10 `armSetupById` · 3412·10 `disarmSetupById`.

## 9. `opportunity-radar` — page radar

1578 `OPPORTUNITY_RADAR_LIMIT` · 1579 `OPPORTUNITY_RADAR_TERMINAL_STATUSES` · 1990·48 `renderOpportunityRadar` · 2038·26 `opportunityRadarItem` · 2064·41 `renderOpportunityRadarSummary` · 2105·10 `opportunityRadarSummaryCell` · 2115·5 `opportunityRadarFocusItems` · 2120·4 `opportunityRadarItemKey` · 2124·4 `opportunityRadarCountText` · 2128·50 `renderOpportunityRadarFocusCard` · 2178·47 `renderOpportunityRadarCard` · 2225·15 `opportunityRadarState` · 2240·16 `opportunityRadarRemainingChecks` · 2256·9 `opportunityRadarIgnoredCheck` · 2265·18 `opportunityRadarRemainingText` · 2283·9 `opportunityRadarNextStep` · 2292·8 `compareOpportunityRadarItems` · 2300·4 `setupRadarTerminal`.

## 10. `market-context` — page contexte marché (lignes 1595–1953, bloc contigu)

1595·24 `renderMarketContextPage` · 1619·8 `scheduleMarketContextRefresh` · 1627·28 `renderMarketContextHeatmap` · 1655·36 `renderMarketContextMarketMap` · 1691·19 `renderMarketContextMarketTile` · 1710·33 `marketContextSectorGroups` · 1743·9 `marketContextAverage` · 1752·4 `marketContextIndustryWeight` · 1756·9 `marketContextTileWeight` · 1765·12 `marketContextPerformanceTone` · 1777·9 `marketContextMapBadge` · 1786·6 `marketContextNodeTitle` · 1792·23 `renderMarketContextSectors` · 1815·38 `renderMarketContextDetail` · 1853·8 `marketContextDisplaySector` · 1861·10 `displaySectorLabel` · 1871·10 `marketContextMetadataLabel` · 1881·6 `renderMarketContextEmptyDetail` · 1887·15 `filterMarketContextNodes` · 1902·20 `wireMarketContextControls` · 1922·8 `marketContextTone` · 1930·8 `marketContextToneFromScore` · 1938·16 `marketContextBadgeLabel`.

## 11. `orders-positions` — ordres, exécutions, positions, ordre manuel

2566·26 `renderOrders` · 2592·13 `renderOrderHistory` · 2605·54 `renderOrderRows` · 2659·19 `renderLocalOrderOrphans` · 2682·8 `describeOrderPrice` · 2690·8 `orderSourceBadge` · 2698·16 `renderExecutions` · 2714·18 `manualOrderPayload` · 2732·23 `renderManualOrderRisk` · 2755·43 `wireManualOrderForm` · 2798·9 `orderIsBrokerActive` · 2807·10 `describeOrderStop` · 2817·4 `describeProtectionStatus` · 2821·18 `describeOrderDiagnostic` · 2839·4 `canDeleteOrder` · 2843·7 `canAttachMissingStop` · 2850·22 `renderPositions`.

## 12. `events-logs` — flux d'événements

2872·21 `renderEvents` · 2893·34 `renderTwsEvents` · 7666·34 `renderLogsPage`.

## 13. `settings` — page réglages et formulaires runtime

117 `SETTINGS_RISK_LABELS` · 2927·46 `renderSettings` · 3113·20 `wireRuntimeButtons` · 3145·16 `wireMarketForm` · 3161·32 `wireBrokerAccountForm` · 3193·18 `wireTwsAuditForm`.

## 14. `setup-form` — création/import de setup (page setups)

3253·44 `wireSetupForm` · 3297·9 `setupTextPayload` · 3306·20 `syncTickerFieldFromSetupText` · 3326·11 `syncTickerFieldFromSetupResult` · 3337·14 `renderSetupToolsOutput` · 3351·14 `renderSetupToolsError` · 3365·18 `renderSetupPreview` · 3383·11 `renderSetupPreviewError`.

## 15. `market-quotes` — normalisation des données de marché (pur, sans DOM)

6040·27 `extractQuoteEvents` · 6067·12 `quoteEventMatchesTimeframe` · 6079·24 `normalizeChartTimeframeCandidate` · 6103·6 `historicalBarsFromEvent` · 6109·67 `quoteFromHistoricalBar` · 6176·69 `quoteFromEvent` · 6245·5 `latestQuoteFromEvents` · 6250·9 `latestQuoteForSymbol` · 6259·14 `mergeMarketSnapshots` · 6273·4 `isMissingMarketValue` · 6277·8 `shouldUseSnapshotCandles` · 6285·24 `quoteSnapshotsToCandles` · 6309·8 `dedupeQuotes` · 6317·6 `quoteCandleKey` · 6323·19 `addVolumeRatios` · 6342·4 `compareQuotesByTime` · 6346·6 `quoteSortTime` · 6501·8 `quoteVolumeRatio` · 6541·5 `quotePrice`.

## 16. `setup-analysis` — décision d'entrée, niveaux, statut affiché (partagé liste + détail)

3663 `SETUP_ENTRY_BLOCKING_STATUSES` · 5654·24 `renderAnalysisCheck` · 5678·8 `normalizeAnalysisState` · 5686·51 `fallbackAnalysisTrace` · 5737·9 `setupAnalysisCandleText` · 5746·8 `nextStepFromAction` · 5754·24 `analysisTimelineEvents` · 6352·11 `latestAnalysisForSetup` · 6363·10 `analysisItemForSetup` · 6373·5 `analysisSnapshot` · 6378·15 `setupAnalysisDecision` · 6393·13 `entryDecisionFromAnalysisItem` · 6406·6 `entryDecisionForSetup` · 6412·37 `entryReadiness` · 6449·30 `setupTradeLevels` · 6479·22 `setupVolumeThresholds` · 6509·8 `volumeThresholdText` · 6517·11 `volumeConditionText` · 6528·13 `setupPriceAtPlacement` · 6546·7 `setupStatusTone` · 6553·79 `displaySetupStatus` · 6632–6654 `buildTriggerStatusDetail`/`buildTriggerReachedDetail`/`buildWatchOnlyDetail`/`buildEntryReadyDetail`/`buildEntryLimitExceededDetail`/`buildPriceTooFarDetail` (4–11 l. chacune).

## 17. `setup-chart` — canvas graphique du détail setup (timeframes, dessin, interactions)

23–28 `SETUP_CHART_*` (constantes+`SETUP_CHART_TIMEFRAMES`) · 3676·7 `fetchSetupSymbolEvents` · 3683·36 `fetchSetupChartQuotes` · 4065·15 `historicalQuotesFromPayload` · 4080·24 `normalizeSetupChartTimeframe` · 4104·6 `setupChartTimeframeLabel` · 4110·15 `renderSetupChartTimeframeControls` · 4125·17 `updateSetupChartTimeframeStatus` · 4142·29 `refreshSetupChartOnly` · 4171·18 `wireSetupChartTimeframeControls` · 5778·24 `renderSetupChartLegend` · 5802·40 `renderSetupChart` · 5842·10 `setupChartStatusText` · 5852·10 `drawSetupChartTimeframeLabel` · 5862·16 `wireSetupChartInteractions` · 5878–5930 `handleSetupChartWheel`/`PointerDown`/`PointerMove`/`PointerUp`/`PointerLeave` · 5939·15 `zoomSetupChart` · 5954·12 `resetSetupChartViewport` · 5966·6 `chartPointerRatio` · 5972·7 `chartPointerInPlot` · 5979·7 `isPointInChartArea` · 5986·5 `chartViewportAtLatest` · 5991·5 `defaultChartVisibleCount` · 5996·7 `normalizeChartVisibleCount` · 6003·7 `normalizeChartVisibleStart` · 6010·25 `updateSetupChartRangeLabel` · 6690·21 `setupChartColors` · 6711·147 `drawSetupChart` ⚠ >80 l. · 6858·4 `setupChartHeight` · 6862·8 `drawEmptyChartText` · 6870·47 `drawPriceGrid` · 6917·29 `drawCandles` · 6946·34 `drawSetupPriceLevels` · 6980·59 `drawLevelTags` · 7039·51 `drawVolumeRatio` · 7090·17 `drawChartTimeAxis` · 7107·53 `drawChartCrosshair` · 7160·15 `roundRect` · 7175·19 `formatChartTime` · 7194·15 `parseChartDate`.

## 18. `setup-forecast` — panneau forecast du détail setup

3719·28 `fetchSetupForecast` · 3747·42 `renderSetupForecastPanel` · 3789·49 `renderSetupForecastStackSummary` · 3838·45 `renderSetupForecastSummary` · 3883·6 `wireSetupForecastPanel` · 3889·108 `drawTimesfmForecastChart` ⚠ >80 l. · 3997·13 `drawForecastLine` · 4010·16 `drawForecastReference` · 4026·12 `drawForecastEndpoint` · 4038·14 `drawForecastLegend` · 4058·7 `forecastTone` · 3034·14 `refreshForecastWatchlist` (écrit `forecastWatchlistBySymbol` pour la liste).

## 19. `setup-detail` — page détail setup (rendu principal)

4471 `SETUP_ANALYSIS_OVERVIEW_LABELS` · 4189·69 `setupDetailSummaryValues` · 4258·25 `renderSetupDetailSummary` · 4283·47 `setupEntryPlanValues` · 4330·25 `renderSetupEntryPlan` · 4355·66 `setupEntryConditions` · 4421·12 `renderSetupConditionGrid` · 4433·38 `renderSetupMarketSummary` · 4520·122 `setupAnalysisPanelValues` ⚠ >80 l. · 4642·44 `renderSetupAnalysisPanel` · 5353·29 `buildSetupDetailInfo` · 5382·61 `setupMarketDataDiagnostic` · 5443·18 `setupMarketDiagnosticMissing` · 5461·10 `diagnosticFieldBlocks` · 5471·10 `marketDiagnosticFieldsList` · 5481·11 `readinessFieldStatus` · 5492·46 `setupMarketReadinessIssue` · 5538·25 `marketDataDiagnosticField` · 5563·10 `marketReadinessStatusFromMissing` · 5573·10 `renderSetupDetailJsonOutput` · 5583·22 `wireSetupDetailJsonButton` · 7209·75 `renderSetupDetail` — orchestrateur de la page · 7284·42 `renderSetupCreationSnapshot`.

## 20. `setup-config-editor` — éditeur de configuration du setup (détail)

41 `CONFIG_FIELD_OPTIONS` · 67 `CONFIG_PATH_OPTIONS` · 75 `CONFIG_ROOT_ORDER` · 7326·90 `wireSetupConfigEditor` ⚠ >80 l. · 7416·14 `parseSetupConfigEditor` · 7430·20 `renderSetupConfigForm` · 7450·21 `createConfigNode` · 7471·38 `createConfigList` · 7509·34 `createConfigField` · 7543·7 `createConfigInput` · 7550·15 `createConfigSelect` · 7565·11 `buildSetupConfigFromForm` · 7576·12 `parseConfigFieldValue` · 7588·8 `setDeepValue` · 7596·4 `formatConfigLabel` · 7600·6 `configOptionsForPath` · 7606·15 `orderedConfigEntries` · 7630·9 `showSetupConfigMessage` · 7639·27 `syncSetupConfigActions`.

## 21. `setup-intelligence` — panneau analyse LLM (détail)

27 `SETUP_INTELLIGENCE_HISTORY_PAGE_SIZE` · 4686·38 `fetchSetupIntelligence` · 4724·14 `emptySetupIntelligencePage` · 4738·18 `renderSetupIntelligence` · 4756·7 `setupIntelligenceHistoryItems` · 4763·9 `upsertIntelligenceAnalyses` · 4772·22 `buildSetupIntelligenceState` · 4794·25 `loadSetupIntelligenceHistoryPage` · 4819·26 `ensureSetupIntelligenceAnalysisLoaded` · 4845·12 `upsertIntelligenceAnalysis` · 4857·280 `renderSetupIntelligencePanel` ⚠ >80 l. (la plus grosse fonction du fichier) · 5137·21 `selectedIntelligenceAnalysis` · 5158·11 `syncCurrentSetupDetailIntelligence` · 5169·9 `intelligenceCell` · 5178·69 `renderSetupIntelligenceComparison` · 5247·10 `formatComparisonValue` · 5257·7 `formatComparisonDelta` · 5264·7 `comparisonTone` · 5271·10 `renderConfidencePill` · 5281·7 `confidenceTone` · 5288·7 `ambiguityTone` · 5295·5 `validationStateText` · 5300·5 `intelligenceIssueCount` · 5305·6 `analysisScenarioCount` · 5311·7 `analysisOpenAmbiguityCount` · 5318·10 `intelligenceOptionLabel` · 5328·6 `shortId` · 5334·6 `formatFieldValue` · 5340·7 `lineRangeLabel` · 5347·6 `fieldValidationRank` · 5620·25 `wireSetupIntelligencePanel` · 5645·9 `showSetupIntelligenceMessage`.

## 22. `hub-pages` — pages hub/V2 (logs, radar hub, scanner, observabilité, forecast pages…)

7700·9 `renderRadarHubPage` · 7709·10 `fetchScanReliability` · 7719·9 `reliabilityLabel` · 7728·40 `renderScanReliabilityPanel` · 7768·13 `opportunityReliabilityCell` · 7781·24 `renderDetectionTechniquesPanel` · 7805·6 `techniqueToggleCell` · 7811·23 `wireDetectionTechniqueRows` · 7834 `FEEDBACK_OPTIONS` · 7836·21 `showDetectionTechniqueDetail` · 7857·32 `renderTechniqueOutcomes` · 7889·7 `renderObservabilityPage` · 7896·11 `renderResearchHubPage` · 7907·8 `renderV2Page` · 7915·55 `renderV2OpportunitiesPage` · 7970·13 `opportunityEntryCell` · 7983·9 `opportunityStopCell` · 7992·11 `opportunityQualityCell` · 8003·20 `renderV2ScannerPage` · 8023·23 `renderV2MarketContextPage` · 8046·12 `renderV2ModelLabPage` · 8058·16 `renderV2BacktestsPage` · 8074·21 `renderV2ForecastingPage` · 8095·52 `renderForecastStackPage` · 8147·93 `renderForecastAccuracyPage` ⚠ >80 l. · 8240·23 `populateSelect` · 8263·12 `uniqueOptions` · 8275·36 `renderModelLabForecastStackPage` · 8311·12 `renderV2DecisionTracePage` · 8323·30 `renderV2DecisionTraceList` · 8353·11 `renderV2SystemHealthPage` · 8364·17 `renderV2Table` · 8381·11 `renderV2Kpis` · 8392·5 `renderJson` · 8397·5 `formatV2Cell` · 8402·8 `formatV2Plain` · 8410·4 `compactJson` · 8414·10 `wireV2Button`.

## 23. `app-core` — bootstrap, refresh périodique, wiring global

1·21 `const page` — détection de la page courante par IDs DOM · 22 `APP_AUTO_REFRESH_INTERVAL_MS` · 271·11 `const activeNav` (IIFE nav active) · 2999·7 `refresh` · 3006·21 `refreshActiveViews` · 3027·7 `scheduleAutoRefresh` · 3422·241 `wireActionButtons` ⚠ >80 l. — méga-fonction de wiring des boutons de TOUTES les pages (touche setups, détail, intelligence, ordres) ; candidate à rester en dernier ou à déplacer telle quelle · 8424·32 `init` — point d'entrée (DOMContentLoaded).

## Matrice de dépendances entre groupes

Colonnes = dépend de. (✱ = dépendance via variables globales partagées, à router par `state.js`.)

| Groupe | ui-helpers | api-client | state | setup-messages | clipboard | market-quotes | setup-analysis | setup-chart | setup-forecast | setup-intelligence | autres |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ui-helpers | — | | | | | | | | | | |
| api-client | ✓ (toast) | — | | | | | | | | | |
| setup-messages | ✓ | | | — | | | | | | | |
| clipboard | ✓ | | ✱ | | — | | | | | | |
| market-quotes | | | | | | — | | | | | |
| setup-analysis | ✓ | | ✱ | | | ✓ | — | | | | |
| dashboard | ✓ | ✓ | ✱ | | | ✓ | ✓ | | | | |
| dashboard-premium | ✓ | ✓ | ✱ | | | | | | | | |
| setups-list | ✓ | ✓ | ✱ | | | ✓ | ✓ | | | | |
| opportunity-radar | ✓ | | ✱ | | | | ✓ | | | | setups-list (cellules) |
| market-context | ✓ | ✓ | ✱ | | | | | | | | |
| orders-positions | ✓ | ✓ | ✱ | | | ✓ | | | | | |
| events-logs | ✓ | ✓ | | | | | | | | | |
| settings | ✓ | ✓ | | | | | | | | | |
| setup-form | ✓ | ✓ | | ✓ | | | | | | | |
| setup-chart | ✓ | ✓ | ✱ | | | ✓ | ✓ (niveaux) | — | | | |
| setup-forecast | ✓ | ✓ | ✱ | | | ✓ | | | — | | |
| setup-config-editor | ✓ | ✓ | ✱ | | | | | | | | |
| setup-intelligence | ✓ | ✓ | ✱ | | | | | | | — | |
| setup-detail | ✓ | ✓ | ✱ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | config-editor |
| hub-pages | ✓ | ✓ | | | | | | | ✓ (accuracy) | | |
| app-core (init) | ✓ | ✓ | ✱ | ✓ | ✓ | | | ✓ | ✓ | ✓ | tous les groupes de pages |

**Dépendances circulaires** : aucune circularité d'appel de fonctions détectée. Les couplages bidirectionnels
passent par les variables globales (ex. `setup-intelligence` mute `currentSetupDetailInfo` écrit par `setup-detail` ;
`refreshActiveViews` lit les dirty-flags du config-editor). Un module `state.js` extrait en premier neutralise ce risque.

**Points d'attention** :
- `wireActionButtons` (241 l.) câble des boutons de plusieurs pages → à déplacer en dernier, tel quel, dans app-core (ou noter comme non-déplaçable).
- 9 fonctions > 80 lignes signalées (⚠) → à consigner dans `NOTES-BUGS.md`, aucune réécriture pendant la mission.
- `renderSnapshot`/`refreshActiveViews` orchestrent les rendus de toutes les pages → app-core.

## Ordre d'extraction suggéré (pour la Phase 3)

1. `state.js` (variables globales + accesseurs) · 2. `ui-helpers` · 3. `api-client` · 4. `setup-messages` · 5. `clipboard` · 6. `market-quotes` · 7. `setup-analysis` · 8. groupes de pages indépendants (`market-context`, `events-logs`, `settings`, `orders-positions`, `opportunity-radar`, `dashboard-premium`, `hub-pages`) · 9. `setups-list` · 10. `setup-form` · 11. `setup-chart` · 12. `setup-forecast` · 13. `setup-config-editor` · 14. `setup-intelligence` · 15. `setup-detail` · 16. `dashboard` · 17. `app-core` (init + wireActionButtons, reste dans app.js ou dernier module).
