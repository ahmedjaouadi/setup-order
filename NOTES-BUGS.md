# NOTES-BUGS — anomalies repérées pendant le refactoring (à traiter séparément, hors mission)

Règle de la mission : aucun bug corrigé pendant le découpage ; tout est consigné ici.

## Bugs

1. **`formatNumber` n'est pas défini** — `renderSetupCreationSnapshot` (app.js) l'appelle 4 fois
   (`creation_price`, `current_price`, `entry_trigger`, `initial_trailing_stop`). Présent sur `main`
   depuis le commit V2 `fa9b1f2` ; la fonction n'a jamais existé. Effet : le panneau « snapshot de création »
   du détail setup lève `ReferenceError` (avalée par le try/catch appelant → toast « formatNumber is not defined »),
   et le rendu du snapshot n'aboutit pas. Corriger en définissant/remplaçant par un formateur existant
   (`numberText` ? `money` ?) — à décider hors refactoring.

## Fonctions > 80 lignes (signalées, non réécrites — seuil du guide de mission)

Voir `docs/app.js-map.md` (marqueurs ⚠) : `renderSetupIntelligencePanel` (280 l.), `wireActionButtons` (241 l.),
`renderEngineHealth` (148 l.), `drawSetupChart` (147 l.), `setupAnalysisPanelValues` (122 l.),
`drawTimesfmForecastChart` (108 l.), `buildExecutiveBrief` (95 l.), `renderForecastAccuracyPage` (93 l.),
`wireSetupConfigEditor` (90 l.).
