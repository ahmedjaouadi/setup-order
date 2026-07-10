# Plan de découpage de `app.js` — Phase 3

Source de vérité des regroupements : `docs/app.js-map.md` (validé). Chaque étape ci-dessous déplace
**exactement** les fonctions/constantes listées dans le groupe correspondant de la carte ; les exceptions
sont notées explicitement. Zéro réécriture, zéro renommage, zéro fusion.

## 1. Choix technique : ES modules (Option A recommandée)

### Constat sur le chargement actuel

- `app.js` est chargé par **un unique** `<script>` classique en **fin de `<body>`** de `base.html` (ligne 50), avec cache-busting `?v=...`.
- **Aucun handler inline** (`onclick=`…) dans les templates ni dans le HTML généré par JS : rien ne dépend de fonctions exposées en global.
- Aucun autre `<script>` dans le projet ; `init()` est appelé à la dernière ligne du fichier.
- L'app est servie par FastAPI en HTTP (jamais en `file://`) : les imports de modules fonctionnent.
- Pré-contrôle fait : `node --check` sur le fichier parsé **en mode module (strict) : OK** — aucune erreur de syntaxe liée au mode strict.

### Décision

**Option A — ES modules** : `<script type="module" src=".../js/app.js?v=...">`, `import`/`export` nommés.
`app.js` reste le **point d'entrée** (pas de changement de `src` dans le template) et se vide au fil des étapes
pour ne garder à la fin que le groupe `app-core` (init, refresh, wireActionButtons…).

Pourquoi pas l'option B (namespaces `window.App.*`) : elle obligerait à réécrire chaque site d'appel
(`toast(...)` → `App.ui.toast(...)`), soit des milliers de lignes touchées — contraire à la règle
« on déplace, on ne réécrit pas ». Avec les ES modules, les sites d'appel restent identiques ;
seules des lignes `import { ... } from "./x.js"` sont ajoutées en tête de fichier.

### Risques identifiés et mitigations

| Risque | Impact | Mitigation |
|---|---|---|
| Mode strict automatique des modules : une affectation à une variable non déclarée, qui passait silencieusement, devient `ReferenceError` à l'exécution | Erreur console à l'usage | Étape 0 dédiée : on passe le tag en `type="module"` **sans rien découper**, puis test manuel de toutes les pages. Diff minuscule, rollback en 1 commit |
| `this` top-level devient `undefined` dans un module | Théorique | Aucun usage de `this` top-level détecté |
| Cache navigateur : les `import "./x.js"` internes ne portent pas le `?v=` de cache-busting | Module périmé après déploiement | FastAPI StaticFiles répond avec ETag/Last-Modified (revalidation 304). En pratique : Ctrl+F5 pendant le dev ; on peut ajouter `?v=` aux specifiers d'import au commit final si besoin |
| Imports circulaires entre modules | Blocage à l'init | La carte ne montre aucune circularité d'appels ; les couplages passent par `state.js` (extrait en premier). De plus, les déclarations `function` hoistées tolèrent les cycles résiduels |
| `type="module"` diffère l'exécution | Aucun | Le script est déjà en fin de `<body>` : point d'exécution identique |

## 2. Gestion de l'état global — `state.js` (étape 1)

Principe retenu (variante la moins invasive des accesseurs) :

- `state.js` déclare chaque variable partagée en `export let x = ...` et **un setter par variable réassignée** (`export function setX(value) { x = value; }`).
- **Lecteurs** : `import { x } from "./state.js"` — les live bindings ES gardent la valeur à jour, les lignes de lecture existantes restent **inchangées**.
- **Écrivains** : seule la ligne d'affectation change : `x = valeur;` → `setX(valeur);` (une poignée de lignes par variable, écrivains identifiés dans la carte). Les **mutations de propriétés** (`state.view = ...`, `currentSetupDetailInfo.intelligence = ...`) fonctionnent sur un import et restent inchangées.

Variables **partagées** allant dans `state.js` (13) : `latestSnapshot`, `currentSetupConfig`, `currentSetupDetailInfo`,
`currentSetupIntelligence`, `currentSetupIntelligenceSelectedId`, `currentSetupIntelligenceComparison`,
`currentSetupArmStatus`, `setupConfigFormDirty`, `setupConfigEditorDirty`, `currentSetupDetailSetup`,
`currentSetupSymbolEvents`, `forecastWatchlistBySymbol`, `setupChartTimeframe`, `setupChartDataMessage`, `setupChartDataMeta`.

Variables **privées** restant dans leur module (elles ne sont lues/écrites que par lui) :
`setupChartState`, `setupChartResizeTimer`, `setupChartInteractionsWired` → `setup-chart.js` ;
`marketContextState`, `marketContextRefreshTimer` → `market-context.js` ;
`appAutoRefreshTimer`, `appAutoRefreshInFlight` → `app.js` (app-core) ;
`setupsColumnOrder`, `setupsSearchQuery` → `setups-list.js` ;
`dashEquityHistory`, `dashLiveEquity`, `dashLastUpdate`, `dashEquityTimer`, `dashAgoTimer`, `dashCurveDrawn` → `dashboard-premium.js`.

## 3. Ordre d'extraction et détail des étapes

Tous les modules sont créés dans `app/gui/static/js/`. Un commit par étape :
`refactor: extract <module> from app.js (no behavior change)`.
Après chaque étape : app relancée, page concernée ouverte, console sans `ReferenceError`/`is not defined`,
test fonctionnel du module (colonne « Test de non-régression »).

| # | Module créé | Contenu (groupe de la carte) | Test de non-régression |
|---|---|---|---|
| 0 | — (`base.html` : `type="module"` + bump `?v=`) | aucun déplacement | Toutes les pages : navigation complète, console vierge |
| 1 | `state.js` | groupe 1 (13 variables partagées + setters) | Détail setup : chargement, refresh auto, panneau intelligence |
| 2 | `ui-helpers.js` | groupe 2 (formatage, badges, toast, modales, DOM) | Toast visible (action quelconque), badges de statut sur la liste setups, ouverture/fermeture d'une modale |
| 3 | `api-client.js` | groupe 3 (`api`, `optionalApi`, `formatErrorDetail`, `connectWebSocket`) | Dashboard se charge, WS connecté (badge runtime), erreur API affichée proprement (URL bidon via bouton existant) |
| 4 | `setup-messages.js` | groupe 4 (validation/humanisation des messages setup) | Prévisualisation d'un setup invalide → messages humanisés |
| 5 | `clipboard.js` | groupe 5 (3 fonctions copie) | **Manuel obligatoire** : bouton « JSON détaillé » + copie template → coller le contenu quelque part |
| 6 | `market-quotes.js` | groupe 15 (normalisation quotes/candles, pur) | Graphique du détail setup affiche des bougies ; prix dans la liste setups |
| 7 | `setup-analysis.js` | groupe 16 (décision d'entrée, niveaux, statuts affichés) | Colonne signal/statut de la liste setups ; timeline d'analyse du détail |
| 8 | `market-context.js` | groupe 10 (+ `marketContextState`, timer privés) | Page contexte marché : heatmap, filtre, détail d'un symbole |
| 9 | `events-logs.js` | groupe 12 (`renderEvents`, `renderTwsEvents`, `renderLogsPage`) | Page logs : flux d'événements visible |
| 10 | `settings.js` | groupe 13 (page réglages + formulaires runtime) | Page settings : rendu + soumission du formulaire marché |
| 11 | `orders-positions.js` | groupe 11 (ordres, exécutions, positions, ordre manuel) | Pages orders/positions : tableaux rendus ; formulaire d'ordre manuel affiche le risque (sans transmettre) |
| 12 | `opportunity-radar.js` | groupe 9 | Page radar : cartes et résumé rendus |
| 13 | `dashboard-premium.js` | groupe 7 (+ 6 variables `dash*` privées) | Dashboard : courbe equity, donut, count-up |
| 14 | `hub-pages.js` | groupe 22 (pages V2/hub, forecast pages) | Pages scanner, opportunities, forecasting, decision trace : rendu sans erreur |
| 15 | `setups-list.js` | groupe 8 (+ `setupsColumnOrder`/`setupsSearchQuery` privées) | Liste setups : tri de colonnes, recherche, arm/disarm d'un setup DISABLED |
| 16 | `setup-form.js` | groupe 14 (création/import de setup) | Coller un JSON de setup → ticker synchronisé, préviz OK |
| 17 | `setup-chart.js` | groupe 17 (+ 3 variables chart privées, listener resize top-level) | Détail setup : bougies, zoom molette, crosshair, changement de timeframe |
| 18 | `setup-forecast.js` | groupe 18 | Détail setup : panneau forecast (cache) + « Recalculer » |
| 19 | `setup-config-editor.js` | groupe 20 | Détail setup : édition d'un champ config, dirty-flag, sauvegarde |
| 20 | `setup-intelligence.js` | groupe 21 | Détail setup : panneau intelligence, historique, comparaison |
| 21 | `setup-detail.js` | groupe 19 (orchestrateur, diagnostics marché, JSON détaillé) | Détail setup complet + bouton JSON détaillé (re-test clipboard) |
| 22 | `dashboard.js` | groupe 6 (snapshot, métriques, santé moteur, brief) | Dashboard : métriques, santé moteur, executive brief, broker reality |
| 23 | — (finalisation) | `app.js` résiduel = groupe 23 `app-core` (`page`, `activeNav`, `refresh`, `refreshActiveViews`, `scheduleAutoRefresh`, `refreshForecastWatchlist`*, `wireActionButtons`, `init`) ; mise à jour finale de `CLAUDE.md` et de la carte | Parcours complet de toutes les pages |

\* `refreshForecastWatchlist` est rangé en carte dans `setup-forecast` mais est appelé par le cycle de refresh ;
il partira dans `setup-forecast.js` (étape 18) et sera importé par `app.js` — noté ici pour éviter la surprise.

**Cas particulier `wireActionButtons` (241 lignes)** : reste dans `app.js` (app-core). Elle câble des boutons
de plusieurs pages et importera ce dont elle a besoin depuis les modules. Elle est déplaçable plus tard si souhaité,
hors mission.

Chaque étape suit mécaniquement : créer le fichier → coller les fonctions à l'identique → ajouter les `export` →
supprimer de `app.js` → ajouter les `import` nécessaires dans `app.js` et les modules déjà extraits → vérifier → 
mettre à jour le tableau « Où se trouve quoi » de `CLAUDE.md` → commit.

## 4. Méthode de vérification

Playwright n'est **pas installé** (ni venv ni npm) et la règle n°6 interdit d'ajouter une dépendance sans accord.
Deux options :

- **Option demandée** : installer Playwright (dev uniquement) pour automatiser le smoke-test par page
  (chargement + zéro erreur console) — le test presse-papiers restera manuel de toute façon.
- **Fallback sans dépendance** : je lance l'app et vérifie chaque page via requêtes HTTP (statut 200, HTML servi),
  et je vous demande le test navigateur (console + fonctionnalité) aux étapes sensibles : 0, 1, 2, 3, 5 (clipboard),
  17 (chart), 21 (détail), 23 (finale). Les étapes intermédiaires à faible risque sont regroupables dans vos vérifications.

## 5. Résultat attendu

- `app.js` : 8 744 → ~350 lignes (entrée + app-core).
- 22 modules de ~80 à ~900 lignes (les plus gros : `setup-chart.js` ~900, `setup-intelligence.js` ~700,
  `hub-pages.js` ~700 — cohérents par domaine, pas de fragmentation artificielle).
- Aucun changement de comportement, d'ID HTML, de route API ni de nom de fonction.
- `NOTES-BUGS.md` créé avec les 9 fonctions > 80 lignes signalées dans la carte.
