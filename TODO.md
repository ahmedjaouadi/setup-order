# TODO — Module intelligent de détection d'opportunités

Plan d'implémentation détaillé, dérivé de `docs/etude-module-intelligent-trading.md`
(qui prime) et de `docs/intelligent-scanner-design.md` (référence pour les détails).
Établi le 2026-07-04.

**Périmètre : détection uniquement, strictement consultatif.** Aucun chemin vers
l'order manager, `execution_allowed: false` partout, jamais d'`eval()`.

---

## Règles d'ingénierie (applicables à CHAQUE étape)

Ces règles ne sont pas optionnelles ; une étape n'est pas « done » si l'une d'elles est violée.

- [ ] **Typage strict dès l'écriture** : tout nouveau module passe `mypy` sans erreur et
  n'entre **jamais** dans la liste `[[tool.mypy.overrides]]` de `pyproject.toml`
  (pas de nouvelle dette de typage).
- [ ] **Lint/format** : `ruff check` et `black --check` verts avant chaque commit
  (le job CI `lint` les vérifie déjà).
- [ ] **Tests d'abord ou avec le code, jamais après coup** : chaque étape a son plan de
  tests (voir chaque section) ; ne pas commencer l'étape N+1 tant que les tests de
  l'étape N ne sont pas verts en CI.
- [ ] **Chaque étape livrable seule** : un commit (ou une petite série) par étape,
  l'app reste fonctionnelle entre chaque commit.
- [ ] **Réutiliser les patterns existants** : schéma SQL dans `app/storage/database.py`
  (`CREATE TABLE IF NOT EXISTS`), job périodique sur le modèle de `forecast_accuracy`
  (`app/background_jobs.py` + `app/forecasting/forecast_accuracy_service.py`),
  traces via `decision_traces` (`app/observability/`), repository/service/schemas
  comme dans `app/opportunity_scanner/`.
- [ ] **Séparation des couches** : SQL uniquement dans les repositories, logique métier
  dans les services, validation Pydantic dans les schemas, routers minces.
- [ ] **Pas de suppression physique** : soft delete uniquement (statut `RETIRED`).
- [ ] **Migrations idempotentes** : `CREATE TABLE IF NOT EXISTS`, seed `INSERT OR IGNORE` —
  redémarrer l'app N fois ne doit rien dupliquer.
- [ ] **Docstrings + docs** : mettre à jour la doc du module à la fin de chaque phase.

---

## Étape 1 (P1-a) — Bibliothèque de techniques + interpréteur de règles

**Objectif : zéro changement de comportement.** Les 7 règles en dur de
`app/opportunity_scanner/detectors.py` deviennent des lignes en DB, évaluées par un
interpréteur déclaratif. La sortie du scanner reste strictement identique.

### 1.1 Schéma de données
- [ ] Ajouter la table `detection_techniques` dans `app/storage/database.py`
  (schéma du design doc §4.1 : `technique_id`, `name`, `description`, `rule_json`,
  `enabled`, `origin` = `builtin|learned|manual`, `parent_id`, `status` =
  `ACTIVE|CANDIDATE|RETIRED`, `created_at`, `updated_at`).
- [ ] Vérifier l'idempotence : double appel d'init DB → aucune erreur, aucune duplication.

### 1.2 Interpréteur de règles déclaratives (`app/opportunity_scanner/rule_interpreter.py`)
- [ ] Whitelist stricte des champs (constante module) : `perf_stock_1d`, `perf_sector_1d`,
  `perf_spy_1d`, `rs_spy`, `rs_sector`, `volume_ratio`, `gap_pct`,
  `breakout_proximity`, `new_intraday_high`, `spread_pct` — plus les alias déjà
  gérés par `detectors.py` (`stock_perf_1d`, `relative_strength_vs_sector`,
  `relative_strength_vs_spy`, `relative_volume`, `volume_ratio_15m`) pour la
  non-régression.
- [ ] Opérateurs whitelistés : `>=`, `>`, `<=`, `<`, `==`, `between`.
- [ ] Combinateurs `all` / `any`, **récursifs** (imbrication autorisée).
- [ ] Robustesse absolue : champ inconnu, valeur `None`/absente, type invalide,
  `between` à bornes inversées, JSON malformé → **la condition ne matche pas,
  jamais d'exception**. L'interpréteur ne lève rien vers le scanner.
- [ ] Validation de `rule_json` à l'écriture (schema Pydantic) : champ hors whitelist ou
  opérateur inconnu → rejet à la création/édition (400), pas à l'évaluation.
- [ ] Typage complet (TypedDict/Pydantic pour la structure de règle), aucun `Any` évitable.

### 1.3 Seed des 7 builtins
- [ ] Module `app/opportunity_scanner/technique_seed.py` : traduction fidèle des 7 règles
  de `detectors.py` en `rule_json` (`INTRADAY_MOMENTUM_ANOMALY`,
  `RELATIVE_STRENGTH_LEADER`, `VOLUME_EXPANSION`, `BREAKOUT_CANDIDATE`,
  `GAP_AND_HOLD`, `WATCHLIST_ANOMALY`, `SECTOR_LEADER`).
  ⚠️ Attention aux deux cas non triviaux :
  `RELATIVE_STRENGTH_LEADER` = `any` (rs_spy ≥ 3 OU rs_sector ≥ 2) ;
  `SECTOR_LEADER` dépend de `RELATIVE_STRENGTH_LEADER` **et** rs_sector ≥ 2 →
  l'exprimer comme règle autonome équivalente (`any(rs_spy≥3, rs_sector≥2)` ET rs_sector≥2,
  ce qui se simplifie en `rs_sector ≥ 2` … à vérifier par le test de non-régression).
- [ ] Seed idempotent (`INSERT OR IGNORE`), `origin='builtin'`, appelé au démarrage de l'app.

### 1.4 Branchement dans le scanner
- [ ] Repository `app/opportunity_scanner/technique_repository.py` (CRUD + liste des
  techniques actives).
- [ ] Dans `app/opportunity_scanner/service.py` : remplacer l'appel à
  `detect_opportunity_types()` par l'évaluation des techniques actives ;
  conserver `primary_opportunity_type()` (ordre de priorité inchangé).
- [ ] Tagger chaque opportunité avec `detected_by: <technique_id>`.
- [ ] Garder `detectors.py` intact pendant P1-a : il sert d'oracle au test de
  non-régression. Le supprimer seulement une fois l'étape validée (ou le conserver
  comme fixture de test).

### 1.5 Tests (bloquants pour passer à l'étape 2)
- [ ] `tests/test_rule_interpreter.py` : table de vérité par opérateur ; combinateurs
  `all`/`any` y compris imbriqués ; champ inconnu → non-match sans exception ;
  snapshot vide → aucun match ; `between` bornes inversées → non-match ;
  JSON malformé → non-match.
- [ ] `tests/test_technique_seed.py` : idempotence (double seed → 7 lignes) ;
  **non-régression** : sur un jeu de snapshots synthétiques couvrant chaque règle
  (match / non-match / cas limites / alias de champs), sortie bibliothèque ==
  sortie `detect_opportunity_types()`, à l'identique.
- [ ] Adapter `tests/test_opportunity_detection.py` si besoin sans en perdre la couverture.

**Critère de done :** non-régression verte, comportement scanner identique, mypy/ruff/black verts.

---

## Étape 2 (P1-b) — API + UI

### 2.1 API (`app/api/routes_techniques.py`)
- [ ] Schemas Pydantic dédiés (création, patch, réponse avec stats) dans
  `app/opportunity_scanner/schemas.py`.
- [ ] `GET /api/techniques` — liste + stats (hit rate, samples, statut ; stats vides avant P2-a).
- [ ] `POST /api/techniques` — création manuelle (`origin='manual'`), validation stricte
  du `rule_json` (whitelist), 400 si invalide.
- [ ] `PATCH /api/techniques/{id}` — activer/désactiver, éditer nom/description/seuils ;
  revalidation du `rule_json`.
- [ ] `DELETE /api/techniques/{id}` — **soft delete** (statut `RETIRED`), jamais de
  suppression physique ; les builtins passent seulement à `enabled=0`, jamais `RETIRED` supprimée.
- [ ] `GET /api/techniques/{id}/outcomes` — historique (vide avant P2-a, mais la route existe).
- [ ] `POST /api/techniques/learning/run` — 501/no-op propre avant P2-b (ou absente jusqu'à P2-b — trancher au moment venu).
- [ ] Enregistrer le router dans `app/main.py`.
- [ ] Codes d'erreur cohérents avec les autres routers (404 inconnu, 400 règle invalide).

### 2.2 UI (page Radar)
- [ ] Panneau « Techniques de détection » entre *Scanner* et *Opportunity shortlist* :
  colonnes Technique / Statut / Origine / Hit rate / Samples / toggle Actif.
- [ ] Statut affiché `WARMUP` si `sample_size < 30` (dès P2-a ; avant, afficher « — »).
- [ ] Clic sur une ligne → détail : règle lisible (rendu humain du JSON), historique
  des détections.
- [ ] Colonne « Détecté par » dans la shortlist (nom de la technique, via `detected_by`).
- [ ] *Generated scenarios* : **ne pas toucher**.

### 2.3 Tests
- [ ] `tests/test_techniques_api.py` : CRUD complet ; POST avec champ hors whitelist → 400 ;
  DELETE → statut RETIRED (la ligne existe toujours) ; PATCH toggle enabled ;
  technique inconnue → 404.
- [ ] Test page Radar (pattern de `tests/test_opportunity_radar_page.py`) : le panneau
  se rend, les techniques du seed sont visibles.

**Critère de done :** techniques visibles/activables/désactivables depuis l'UI, tests verts.

---

## Étape 3 (P2-a) — Outcome tracking (version AMENDÉE : triple barrier, pas forward return naïf)

### 3.1 Schéma `detection_outcomes` (version enrichie de l'étude §2, PAS celle du design doc)
- [ ] Table dans `app/storage/database.py` avec **tous** les champs de l'étude :
  `outcome_id`, `technique_id`, `symbol`, `detected_at`, `price_at_detection`,
  `features_snapshot` (JSON du snapshot Market Context complet — dataset ML futur),
  `r_unit_pct`, `horizon` (`1d|3d`), `evaluation_due_at`, `price_at_horizon`,
  `forward_return_pct`, `mfe_pct`, `mae_pct`, `label_1r`, `human_feedback`,
  `status` (`PENDING|EVALUATED|EXPIRED`), `created_at`.
- [ ] Index `(status, evaluation_due_at)` et `(technique_id)`.

### 3.2 Enregistrement à la détection
- [ ] À chaque match technique × stock (marché ouvert) : une ligne PENDING par horizon
  (+1 j et +3 j de bourse), avec `features_snapshot` = snapshot complet sérialisé.
- [ ] `r_unit_pct` = 1×ATR% du symbole au moment de la détection ; si ATR indisponible →
  fallback 2 % **et le noter dans le JSON de la ligne**.
- [ ] Échéances : week-ends/jours fériés → décalées au prochain jour de bourse
  (réutiliser la logique calendrier de `forecast_accuracy`).
- [ ] Dédoublonnage : ne pas créer une nouvelle ligne si une détection identique
  (technique, symbole, horizon) est déjà PENDING sur la même fenêtre.

### 3.3 Job d'évaluation
- [ ] Service `app/opportunity_scanner/outcome_tracker.py` + job périodique dans
  `app/background_jobs.py` (même mécanique que `forecast_accuracy`).
- [ ] À l'échéance : `price_at_horizon`, `forward_return_pct`, **MFE/MAE** sur la fenêtre
  (barres intraday si disponibles, sinon extrêmes high/low daily — **préciser la
  granularité utilisée dans le JSON de la ligne**), et `label_1r` :
  `1` = +1R atteint avant −1R ; `0` = −1R d'abord ; `NULL` = ni l'un ni l'autre (expiré).
- [ ] Données introuvables à l'échéance → statut `EXPIRED`, jamais de crash du job.
- [ ] Stats par technique : `sample_size`, `hit_rate_1r`, `avg/median forward return`,
  `avg mfe`, `avg |mae|`, `expectancy_r` (formule étape 4). Stats **figées hors RTH**.
- [ ] Brancher les stats dans `GET /api/techniques` et le panneau Radar.

### 3.4 Tests
- [ ] `tests/test_detection_outcomes.py` : cycle PENDING → EVALUATED ; forward return
  correct ; décalage week-end ; **MFE/MAE corrects sur des séries synthétiques** ;
  `label_1r` : cas « +1R d'abord », « −1R d'abord », « ni l'un ni l'autre » (NULL) ;
  fallback ATR absent (r_unit=2 % + note JSON) ; données manquantes → EXPIRED.

**Critère de done :** outcomes évalués avec fwd return, MFE/MAE, label_1r ; stats affichées dans l'UI.

---

## Étape 4 (P2-b) — Learning loop

### 4.1 Métrique de décision
- [ ] Les décisions automatiques se prennent sur **`expectancy_r`** (label R-based),
  jamais sur le forward return brut :
  `expectancy_r = hit_rate_1r × avg(mfe_pct | label_1r=1) − (1 − hit_rate_1r) × avg(|mae_pct| | label_1r=0)`
- [ ] Le forward return brut reste affiché dans l'UI (lisibilité), sans rôle décisionnel.

### 4.2 Cycle d'apprentissage (`app/opportunity_scanner/learning_loop.py`)
- [ ] `sample_size < 30` → WARMUP, **aucune décision**.
- [ ] `sample_size ≥ 30` et `expectancy_r < 0` → technique `RETIRED` (désactivée, pas supprimée).
- [ ] `sample_size ≥ 30` et `expectancy_r > 0` → génération de 2–3 **variantes**
  (seuils ±20 %), statut `CANDIDATE`, `parent_id` renseigné.
- [ ] Variante CANDIDATE avec `sample_size ≥ 30` **ET** `expectancy_r` > parent →
  promue `ACTIVE`, `origin='learned'`. Sinon (≥ 30 et moins bonne) → `RETIRED`.
- [ ] Le cycle tourne uniquement marché ouvert (RTH) ; hors RTH tout est figé.

### 4.3 Garde-fous (tous obligatoires, chacun testé)
- [ ] Plafond **20 techniques ACTIVE** simultanées.
- [ ] Builtins jamais supprimées ni RETIRED-définitives (désactivables seulement).
- [ ] Kill-switch `opportunity_scanner.learning.enabled: false` dans `app/settings.py`
  (défaut : **false**) → zéro mutation de la bibliothèque.
- [ ] Chaque décision automatique (retrait, variante, promotion) tracée dans
  `decision_traces` avec la justification chiffrée (visible dans Observability).

### 4.4 API / déclenchement
- [ ] `POST /api/techniques/learning/run` : force un cycle (debug) ; respecte le
  kill-switch et les garde-fous ; retourne le résumé des décisions prises.
- [ ] Job périodique (cadence à définir, ex. 1×/jour en RTH) dans `app/background_jobs.py`.

### 4.5 Tests
- [ ] `tests/test_learning_loop.py` sur données synthétiques : expectancy_r négative →
  RETIRED ; promotion uniquement ≥ 30 échantillons ET > parent ; plafond 20 respecté ;
  kill-switch → aucune mutation (test dédié) ; builtins indélébiles ; warmup → aucune
  décision ; chaque décision produit une trace.
- [ ] Cycle complet simulable de bout en bout via `POST /learning/run`.

**Critère de done :** cycle complet simulable sur données synthétiques, tous les garde-fous testés.

---

## Étape 5 (P2-b bis) — Feedback humain

- [ ] Bouton feedback sur chaque ligne de la shortlist → valeurs `good` | `too_late` |
  `false_signal` | `bad_structure` | texte libre.
- [ ] Endpoint (ex. `PATCH /api/techniques/outcomes/{outcome_id}/feedback`) qui remplit
  `human_feedback` sur la ligne `detection_outcomes` correspondante.
- [ ] Feedback visible dans le détail de la technique (avec l'historique des détections).
- [ ] Le feedback est **stocké seulement** à ce stade : aucune influence sur le learning
  loop (ce sera un input du ML en P3).
- [ ] Tests : persistance, valeurs libres acceptées, outcome inconnu → 404.

**Critère de done :** feedback persisté et visible dans le détail technique.

---

## Invariants de sécurité (à vérifier par des tests dédiés, `tests/test_techniques_security.py`)

1. [ ] `execution_allowed: false` / `can_send_order: false` sur **toute** opportunité
   issue des techniques.
2. [ ] Aucun import, aucune route, aucun chemin d'appel entre le module techniques et
   l'order manager — **test statique** : grep des imports de
   `app/engine/order_manager` dans les nouveaux modules → zéro occurrence.
3. [ ] Règles déclaratives uniquement : aucun `eval()`/`exec()`/`compile()` dans les
   nouveaux modules (test statique), aucune exécution de code venant de la DB ou de l'API.
4. [ ] Kill-switch `learning.enabled=false` → zéro mutation (test dédié, cf. 4.5).
5. [ ] Apprentissage et stats figés hors RTH.
6. [ ] Toute décision automatique auditable dans `decision_traces`.

---

## P3 — Pont vers le ML (**NE PAS IMPLÉMENTER MAINTENANT**)

Déclencheur : **≥ 300 outcomes évalués sur au moins 3 techniques distinctes.**
Grâce à `features_snapshot` + `label_1r` + MFE/MAE, le dataset se construira en SQL pur.

- [ ] Champs de régime de marché dans le snapshot (SPY vs EMA50, tendance VIX, breadth) —
  d'abord comme simples champs de règles déclaratives.
- [ ] Meta-labeling : modèle tabulaire (LightGBM / régression logistique calibrée) qui
  apprend `P(label_1r=1)` — il **filtre**, il ne détecte pas. Walk-forward strict,
  purged CV, jamais d'optimisation sur tout l'historique.
- [ ] Migration d'infra (DuckDB/Parquet, MLflow) **seulement si** SQLite devient le goulot.

### À ne PAS faire (accord des deux documents source)
- Pas de ML avant le seuil de déclenchement ci-dessus.
- Pas de deep learning / RL / LLM décisionnaire.
- Pas de nouvelle infra tant que SQLite suffit (< ~100k lignes).
- Pas d'optimisation massive de paramètres sur tout l'historique (overfitting garanti).

---

## Ordre d'exécution et discipline

| Étape | Livrable | Critère de done |
|---|---|---|
| 1 (P1-a) | Table + interpréteur + seed + branchement scanner | Non-régression verte, comportement identique |
| 2 (P1-b) | API `/api/techniques` + panneau Radar + « Détecté par » | Techniques gérables depuis l'UI |
| 3 (P2-a) | `detection_outcomes` enrichie + job d'évaluation + stats | Fwd return, MFE/MAE, label_1r évalués et affichés |
| 4 (P2-b) | Learning loop + garde-fous + traces + kill-switch | Cycle simulable via POST `/learning/run` |
| 5 (P2-b bis) | Feedback humain sur la shortlist | Feedback persisté et visible |

**Chaque étape est livrable seule. Ne pas commencer une étape avant que les tests de
la précédente soient verts (localement ET en CI).**
