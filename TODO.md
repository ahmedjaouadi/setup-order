# TODO — Module intelligent de détection : conventions, features F1, scoring

Plan d'implémentation détaillé, dérivé de `docs/mapping-skills-vers-module-detection.md`
(qui prime sur le périmètre), `docs/skills.md` v2.0 (référence métier — citer la section
dans chaque description de technique) et de l'audit du code du 2026-07-05.
Remplace l'ancien TODO (étapes P1-a → P2-b bis, toutes livrées : commits
`a205ad9` → `a8fcef3`).

**Constat qui ordonne tout le plan : `detection_outcomes` contient 0 ligne.**
La collecte d'apprentissage n'a pas commencé. Les étapes 6.x (tags, gate qualité,
mutation mono-paramètre) conditionnent la **valeur** de tout ce qui sera collecté :
un outcome enregistré sans tags ou sur données douteuses est perdu ou pollué,
**irréversiblement**. Elles passent donc avant tout le reste, y compris F1.

**Périmètre inchangé : détection uniquement, strictement consultatif.** Aucun chemin
vers l'order manager, `execution_allowed: false` partout, jamais d'`eval()`.
Les circuit breakers, coûts, anti-chase d'exécution vivent dans
`app/engine/trade_guards.py` / `transaction_costs.py` — ne rien y dupliquer côté détection.

---

## Règles d'ingénierie (applicables à CHAQUE étape)

Ces règles ne sont pas optionnelles ; une étape n'est pas « done » si l'une d'elles est violée.

- [ ] **Typage strict dès l'écriture** : tout nouveau module passe `mypy` sans erreur et
  n'entre **jamais** dans la liste `[[tool.mypy.overrides]]` de `pyproject.toml`.
- [ ] **Lint/format** : `ruff check` et `black --check` verts avant chaque commit.
- [ ] **Tests d'abord ou avec le code, jamais après coup** ; ne pas commencer l'étape
  N+1 tant que les tests de l'étape N ne sont pas verts.
- [ ] **Chaque étape livrable seule** : un commit (ou une petite série) par étape,
  l'app reste fonctionnelle entre chaque commit.
- [ ] **Réutiliser les patterns existants** : migrations idempotentes via
  `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` (helper existant dans
  `app/storage/database.py`), traces via `decision_traces` (`app/observability/`),
  repository/service/schemas comme dans `app/opportunity_scanner/`.
- [ ] **Séparation des couches** : SQL dans les repositories, logique métier dans les
  services, validation Pydantic dans les schemas, routers minces.
- [ ] **Pas de suppression physique** : soft delete uniquement (statut `RETIRED`).
- [ ] **Aucun nouveau champ de règle sans whitelist + tests** : toute extension du
  snapshot passe par `ALIAS_GROUPS` de `rule_interpreter.py` + table de vérité.
- [ ] **Docstrings + docs** : mettre à jour `docs/14-opportunity-scanner-contract.md`
  et `docs/implementation-status.md` à la fin de chaque étape.

---

## Étape 6 (P2-c) — Conventions skills.md §4 : à faire AVANT toute collecte sérieuse

Source : mapping §4 (« coût quasi nul, gain élevé ») + audit (4 des 5 conventions
absentes, la 5ᵉ — mutation — violée).

### 6.1 Tags de contexte dans `features_snapshot` (skills.md §32.2bis) — LE PLUS URGENT

Objectif : chaque outcome doit pouvoir répondre plus tard à « les détections pendant
le lunch avec rvol < 1.2 sont-elles rentables ? » sans re-collecte.

- [x] Nouveau module `app/opportunity_scanner/context_tags.py` avec une fonction pure
  `build_context_tags(snapshot: dict, now: datetime | None = None) -> dict` retournant :
  - `time_bucket` : `OPEN` (09:30–10:00) / `MORNING` (10:00–11:30) / `LUNCH`
    (11:30–14:00) / `AFTERNOON` (14:00–15:00) / `POWER_HOUR` (15:00–16:00),
    heure de New York — réutiliser `app/utils/market_hours.py`
    (`US_EQUITY_TIMEZONE`, `coerce_datetime`). Hors RTH → `OFF_HOURS`
    (ne devrait pas arriver : l'enregistrement est déjà RTH-only, mais ne jamais crasher).
  - `rvol_bucket` : `<0.8` / `0.8-1.2` / `1.2-2.0` / `>2.0` (buckets exacts de
    skills.md §32.2bis), calculé sur le meilleur champ disponible dans l'ordre
    `rvol` → `relative_volume` → `volume_ratio` ; absent → `UNKNOWN`.
  - `spread_bucket` : `tight` (≤ 0.1 %) / `normal` (≤ 0.3 %) / `wide` (> 0.3 %) sur
    `spread_pct` ; absent → `UNKNOWN`.
  - `day_of_week` : `MON`…`FRI` (jour NY, pas UTC — un scan à 20h NY vendredi ne doit
    pas être taggé samedi).
  - `market_regime` : `UNKNOWN` tant que F3 n'existe pas (le champ est réservé dès
    maintenant pour que le dataset ait une colonne stable).
  - `had_catalyst` : `None` tant que les sources news/earnings ne sont pas branchées
    (colonne réservée, cf. « Données externes » plus bas).
- [x] Dans `OutcomeTracker.record_detection` (`outcome_tracker.py`) : stocker
  `features_snapshot = {**snapshot, "context_tags": build_context_tags(snapshot, now)}`.
  Les tags vivent DANS le snapshot JSON (pas de migration de schéma nécessaire),
  sous une clé dédiée pour rester requêtables en SQL (`json_extract`).
- [x] Exposer les tags dans `GET /api/techniques/{id}/outcomes` (déjà sérialisés via
  `features_snapshot`, vérifier seulement qu'ils sortent bien).
- [x] Tests `tests/test_context_tags.py` : chaque bucket de chaque tag (bornes
  incluses/excluses exactes : 11:30 → LUNCH, 14:00 → AFTERNOON, rvol = 0.8 → `0.8-1.2`,
  = 2.0 → `1.2-2.0` — trancher les bornes et les figer par test) ; champs absents →
  `UNKNOWN`/`None` sans exception ; timezone (un timestamp UTC 18:00 en juillet =
  14:00 NY = AFTERNOON) ; `test_detection_outcomes.py` : la ligne créée contient
  `context_tags` complet.

### 6.2 Data quality gate avant évaluation + avant enregistrement (skills.md §28bis)

Deux défauts à corriger : (a) aucune validation qualité avant l'évaluation des
techniques ; (b) `_record_detection_outcomes` est appelé (scanner.py ~l.375) AVANT le
filtre liquidité/données (~l.316) — des outcomes naissent sur des candidats ensuite rejetés.

- [x] Fonction `snapshot_quality_issues(snapshot: dict, *, now, staleness_max_seconds) ->
  list[str]` (dans `app/opportunity_scanner/` ou réutiliser/adapter
  `app/data_quality/service.py` — préférer une fonction pure locale, le
  DataQualityService actuel persiste des events et prend un repository ; le scanner
  n'a besoin que du verdict). Vérifications minimales (§28bis) :
  - staleness : `timestamp` du snapshot plus vieux que `staleness_max` (défaut :
    2× le timeframe, soit 30 min pour 15m ; configurable) → `STALE_DATA` ;
  - cohérence OHLC : `high >= low`, `high >= open/close`, `low <= open/close`
    (sur les champs présents seulement) → `MISSING_MARKET_DATA` ;
  - `bid < ask` quand les deux sont présents, spread positif ;
  - prix présent et > 0.
- [x] Dans `opportunities/scanner.py` : évaluer la gate sur `context_snapshot` AVANT
  `self.context_scanner.evaluate(...)`. Échec → pas d'évaluation de techniques, pas
  d'outcome, opportunité marquée `REJECTED` avec
  `payload["data_quality"] = {"status": "PAUSED", "reason_code": "STALE_DATA" | "MISSING_MARKET_DATA", "issues": [...]}`.
- [x] Déplacer l'appel `_record_detection_outcomes` APRÈS le filtre liquidité :
  n'enregistrer les outcomes que si `filters["blocked"] is False` **et** gate qualité
  passée. (Refactor : remonter le résultat du filtre dans `_opportunity_from_market`
  ou déplacer l'enregistrement dans `_opportunity_from_candidate`.)
- [x] Tests `tests/test_scanner_data_quality_gate.py` : snapshot stale → aucune
  technique évaluée, aucun outcome, reason_code `STALE_DATA` ; OHLC incohérent →
  idem `MISSING_MARKET_DATA` ; bid > ask → bloqué ; candidat bloqué liquidité →
  **aucun outcome créé** (test de non-régression du bug actuel) ; snapshot sain →
  comportement identique à avant (non-régression sur `test_opportunity_detection.py`).

### 6.3 `status` + `reason_code` dans les traces du scanner (skills.md §2.5)

- [x] **Importer le référentiel existant** depuis `app/engine/trade_guards.py`
  (`STATUS_PAUSED`, `STATUS_NO_GO`, `REASON_STALE_DATA`, `REASON_MISSING_MARKET_DATA`,
  `REASON_SPREAD_TOO_WIDE`, …). Ne PAS dupliquer les constantes. Si le couplage
  détection→engine gêne, extraire les constantes dans un module partagé
  `app/decision_codes.py` et faire pointer les deux (petit refactor acceptable).
- [x] Chaque refus **qualifié** du scanner (gate qualité, filtre liquidité) est tracé
  via `decision_traces` (`event_store.record_decision_trace`, pattern du learning
  loop) : `decision_type="SCANNER_GATE"`, `final_decision=f"{status}:{reason_code}"`,
  trace = symbole + issues + snapshot minimal. Un non-match de règle reste
  **silencieux** (pas de trace par symbole scanné ×30 s — volumétrie).
- [x] Test : un scan sur snapshot stale produit exactement une trace `SCANNER_GATE`
  avec le bon reason_code ; un non-match n'en produit pas.

### 6.4 Mutation mono-paramètre du learning loop (skills.md §32.2ter) — VIOLATION à corriger

Aujourd'hui `learning_loop.scale_rule` multiplie TOUS les seuils numériques d'une
règle par ±20 % : impossible d'attribuer l'effet d'une variante. À corriger AVANT que
le loop ne promeuve quoi que ce soit (aucune variante n'existe encore — fenêtre ouverte).

- [x] Remplacer la génération de variantes dans `_spawn_variants` :
  - énumérer les **conditions feuilles numériques** de la règle parent
    (parcours récursif de `all`/`any`, ignorer les booléens et `==` sur chaînes) ;
  - pour chaque feuille, générer une variante par facteur (±20 % par défaut) qui ne
    modifie QUE cette feuille (`mutated_field`, `factor`) ;
  - plafonner le nombre de variantes créées par cycle et par parent
    (`learning.max_variants_per_parent`, défaut 4 — p. ex. 2 feuilles × 2 facteurs ;
    au-delà, prioriser les feuilles dans l'ordre de la règle) ; le plafond global de
    20 techniques ACTIVE existant reste inchangé ;
  - `technique_id` : `{parent}_{field}_{p20|m20}` (unicité via `_unique_id` existant) ;
  - `description` : mentionner explicitement « mutation de {field} ×{factor},
    skills.md §32.2ter — un paramètre à la fois » ;
  - stocker `{"mutated_field": ..., "factor": ...}` dans la trace `VARIANT_SPAWNED`.
- [x] Conserver `scale_rule` uniquement si encore utilisé par des tests ; sinon le
  supprimer (il ne doit plus avoir d'appelant en production).
- [x] Tests `tests/test_learning_loop.py` (compléter) : une variante ne diffère de son
  parent que par UN seuil (comparaison structurelle des deux règles) ; règle à 3
  conditions numériques + plafond 4 → les variantes créées respectent le plafond et
  l'ordre ; règle sans seuil numérique → aucune variante, pas de crash ; les traces
  contiennent `mutated_field`.

### 6.5 Versioning des techniques (skills.md §30bis)

- [x] Migration idempotente dans `app/storage/database.py` (helper
  `PRAGMA table_info` + `ALTER TABLE` existant) : colonnes
  `config_version TEXT NOT NULL DEFAULT '1'` et `revision INTEGER NOT NULL DEFAULT 1`
  sur `detection_techniques`.
- [x] `technique_repository.py` : `insert_if_absent` renseigne les deux champs ;
  nouvelle méthode `bump_revision(technique_id, updated_at)` ;
- [x] `technique_service.py` / `PATCH /api/techniques/{id}` : toute modification de
  `rule_json` (et seulement de `rule_json` — un toggle `enabled` ne version pas)
  incrémente `revision` ET journalise dans `decision_traces` :
  `decision_type="TECHNIQUE_REVISION"`, trace = `{technique_id, revision_from,
  revision_to, rule_before, rule_after}`. Le learning doit pouvoir rejouer une
  décision avec la règle exacte de l'époque.
- [x] Les variantes du learning loop naissent en `revision=1` avec leur propre id
  (la lignée est déjà portée par `parent_id`).
- [x] Exposer `revision`/`config_version` dans `GET /api/techniques` et le panneau Radar
  (colonne discrète ou détail).
- [x] Tests `tests/test_techniques_api.py` (compléter) : PATCH de rule_json →
  revision +1 + trace avec before/after ; PATCH de nom seul → revision inchangée ;
  double init DB → pas d'erreur de migration (idempotence).

**Critère de done étape 6 :** un outcome enregistré porte ses tags ; aucun outcome ne
peut naître d'un snapshot douteux ou d'un candidat illiquide ; toute variante ne mute
qu'un seuil ; toute édition de règle est versionnée et rejouable ; refus qualifiés
tracés en (status, reason_code). mypy/ruff/black verts.

---

## Étape 7 (F1) — Lot de features : rvol, atr_pct, dist_vwap_pct, time_bucket, EMA/SMA

Source : mapping §2 lot F1 (« peu coûteux, débloque beaucoup ») + §6.3.
Chaque feature = calcul + injection dans le snapshot du scanner + entrée whitelist
(`ALIAS_GROUPS`) + tests. L'interpréteur traite déjà « champ absent → non-match » :
une feature indisponible ne casse jamais une règle, elle l'empêche de matcher.

### 7.1 `rvol` — canonicaliser l'existant (skills.md §6.2bis)

Le connecteur TWS calcule déjà le RVOL en mode `SAME_TIME_OF_DAY`
(`tws_connector.py`, `comparison_mode = "SAME_TIME_OF_DAY"`, exposé comme
`relative_volume`/`volume_ratio`). Il manque un champ canonique garanti.

- [x] Dans `_context_snapshot_from_candidate` (`opportunities/scanner.py`) : ajouter
  `"rvol": _first_value(quote.get("relative_volume"), quote.get("volume_ratio"),
  quote.get("volume_ratio_15m"))`.
- [x] `rule_interpreter.ALIAS_GROUPS` : `"rvol": ("rvol", "relative_volume",
  "volume_ratio", "volume_ratio_15m")`. Garder l'entrée `volume_ratio` existante
  (non-régression des builtins) mais faire des nouvelles techniques des
  consommatrices de `rvol`.
- [x] Documenter dans la docstring d'`ALIAS_GROUPS` que `rvol` est la mesure de
  référence (courbe en U intraday, skills.md §6.2bis) et `volume_ratio` l'héritage.

### 7.2 `atr_pct` (skills.md §7.1)

- [x] Calcul : `atr_pct = atr_15m / price × 100` (fallback `atr_1h / price × 100` si
  atr_15m absent ; les deux absents → None). À calculer dans
  `_context_snapshot_from_candidate` à partir des champs du quote, et dans
  `FeatureStore._features_from_quote` (le tracker d'outcomes utilise déjà l'ATR pour
  `r_unit_pct` — mutualiser : une fonction `atr_pct(quote)` dans un module utilitaire
  du scanner, appelée des deux côtés, source unique de vérité).
- [x] Whitelist : `"atr_pct": ("atr_pct",)`.

### 7.3 `vwap` + `dist_vwap_pct` — le manque le plus structurant (skills.md §5.1, §19)

Aucun VWAP n'existe dans le pipeline. Prérequis : barres intraday de la session.

- [x] Calcul du VWAP session dans le connecteur ou le FeatureStore (trancher à
  l'implémentation ; recommandation : là où les barres 15m de la session sont déjà
  disponibles — le connecteur produit déjà `bar_volume_15m`/`avg_volume_15m`, suivre
  le même chemin de données) :
  `vwap = Σ(typical_price × volume) / Σ(volume)` sur les barres RTH du jour,
  `typical_price = (high + low + close) / 3`. Recalculé à chaque scan (pas d'état
  incrémental à persister — le scanner tourne toutes les 30 s, N barres 15m max = 26).
- [x] Barres indisponibles / volume cumulé nul / hors RTH → `vwap = None` (jamais 0,
  jamais d'exception).
- [x] `dist_vwap_pct = (price − vwap) / vwap × 100` dans le snapshot du scanner.
- [x] Whitelist : `"dist_vwap_pct": ("dist_vwap_pct",)` (et `"vwap"` si utile aux
  règles ; `dist_vwap_pct` suffit a priori).
- [x] Tests : VWAP exact sur une série synthétique de 3 barres (valeur calculée à la
  main dans le test) ; volume nul → None ; `dist_vwap_pct` signé correctement
  (prix sous VWAP → négatif).

### 7.4 `time_bucket` dans le snapshot (skills.md §25bis)

- [x] Réutiliser `build_context_tags` (étape 6.1) : injecter `time_bucket` directement
  dans le snapshot du scanner (pas seulement dans les tags d'outcome), pour que les
  règles puissent l'utiliser : `"time_bucket": ("time_bucket",)` en whitelist,
  opérateur `==` sur chaîne (déjà supporté).
- [x] Ceci permet la **pénalité lunch déclarative** (mapping §2 F1) : les nouvelles
  techniques sensibles au volume incluent
  `any(time_bucket != LUNCH, rvol >= 1.5)`… — l'interpréteur n'a pas de `!=` :
  l'exprimer en `any(time_bucket == OPEN, …)` est verbeux ; **ajouter l'opérateur
  `in`** (valeur = liste de chaînes) à l'interpréteur : whitelist
  `ALLOWED_OPERATORS + "in"`, validation Pydantic, table de vérité. C'est la seule
  extension du langage de règles de tout ce plan — la garder minimale.

### 7.5 `price_above_ema20`, `price_above_sma50` (daily) (skills.md §4.1)

- [x] `FeatureStore._features_from_bars` calcule déjà `historical_ema_20` ; ajouter
  `historical_sma_50` (moyenne simple des 50 derniers closes daily ; < 50 barres →
  None).
- [x] Booléens dans le snapshot du scanner : `price_above_ema20 = price >
  historical_ema_20` (None si l'un des deux manque), idem `price_above_sma50`.
  ⚠️ S'assurer que le chemin scanner reçoit bien des features daily (le FeatureStore
  est par timeframe ; le quote du scanner doit porter l'enrichissement daily — sinon
  brancher `enrich_historical` sur les barres daily déjà récupérées par ailleurs).
- [x] Whitelist : les deux booléens, opérateur `==` (l'interpréteur gère déjà
  l'égalité booléenne via `_evaluate_equals`).

### 7.6 Extension whitelist + nouvelles techniques seed

- [x] `ALIAS_GROUPS` : + `rvol`, `atr_pct`, `dist_vwap_pct`, `time_bucket`,
  `price_above_ema20`, `price_above_sma50`. La validation Pydantic à l'écriture suit
  automatiquement (`ALLOWED_FIELDS = frozenset(ALIAS_GROUPS)`).
- [x] Nouvelles techniques `origin='builtin'`, `status='ACTIVE'`, seed
  `INSERT OR IGNORE` (idempotent), chacune citant sa section skills.md dans
  `description` (exigence du mapping §6.5) :
  - **`GAP_AND_GO_FULL`** (skills.md §16) : `all(gap_pct >= 3, perf_stock_1d > 0,
    dist_vwap_pct >= 0, rvol >= 1.5, spread_pct <= 0.5)` — la version complète
    « tient au-dessus de VWAP » du builtin gap-and-hold.
  - **`MOMENTUM_RVOL_CONFIRMED`** (skills.md §6.2bis + §10) : version rvol du
    momentum : `all(perf_stock_1d >= 5, rs_spy > 0, rvol >= 1.5, spread_pct <= 0.5)`.
  - **Pénalité lunch** (skills.md §25bis) : intégrée aux deux techniques ci-dessus via
    `any(time_bucket in [OPEN, MORNING, AFTERNOON, POWER_HOUR], rvol >= 1.5)` —
    seuils exacts à figer dans le seed, pas de duplication de la logique en Python.
- [x] **Ne PAS modifier les 7 builtins existants dans cette étape** (leur ajout du
  filtre spread est l'étape 7.7, séparée, car elle touche des lignes existantes en DB).

### 7.7 Filtre liquidité dans les règles existantes (mapping §1)

Les 7 seeds ne contiennent pas `spread_pct <= 0.5` (prescrit « en condition `all` de
toute technique »). Le seed est `INSERT OR IGNORE` : modifier le code du seed ne
changera PAS les lignes déjà en base.

- [x] Migration applicative explicite (au démarrage, idempotente) : pour chaque builtin
  en base dont la règle ne contient pas de condition `spread_pct`, réécrire le
  `rule_json` (wrap : `all(<règle actuelle>, spread_pct <= 0.5)`), **incrémenter
  `revision`** (étape 6.5) et tracer `TECHNIQUE_REVISION` avec before/after.
  Marquer la migration comme appliquée (bot_state) pour ne pas la rejouer.
- [x] Non-régression assouplie : les tests de non-régression P1 comparaient à
  `detectors.py` sans filtre spread — les mettre à jour en conséquence (le
  changement de comportement est voulu et documenté ici).
- [x] Test : un snapshot avec spread 0.8 % ne matche plus aucune technique ; spread
  absent → non-match (comportement voulu : pas de données de spread = pas de
  détection, cohérent avec la gate qualité).

### 7.8 Tests transverses étape 7

- [x] `tests/test_rule_interpreter.py` : table de vérité pour chaque nouveau champ +
  opérateur `in` (liste vide → non-match, valeur non-liste → non-match, casse).
- [x] `tests/test_technique_seed.py` : idempotence avec les nouvelles techniques
  (double seed → toujours N lignes) ; chaque nouvelle description cite skills.md.
- [x] Tests features : `atr_pct`/`dist_vwap_pct`/booléens sur snapshots synthétiques,
  y compris tous les cas « ingrédient manquant → None ».

**Critère de done étape 7 :** les 6 features sont dans le snapshot et la whitelist,
GAP_AND_GO_FULL et MOMENTUM_RVOL_CONFIRMED détectent sur snapshots synthétiques, les
builtins portent le filtre spread avec revision incrémentée et trace, non-régression
mise à jour, mypy/ruff/black verts.

---

## Étape 8 — Scoring pondéré skills.md §9.1 (chantier séparé, APRÈS F1)

Cible : `app/opportunity_scanner/scoring.py` (actuellement bonus/malus ad hoc).
Le mapping §5 le classe explicitement hors bibliothèque de techniques.

- [x] Nouveau calcul à 7 composants pondérés (skills.md §9.1), chaque composant somme
  ses sous-critères **calculables avec les features disponibles** ; un sous-critère
  non calculable rapporte 0 ET est listé dans `score_breakdown.unavailable`
  (transparence — le score reste comparable entre titres) :
  - `trend_quality` /20 : `price_above_ema20 && price_above_sma50` (+2), daily bullish
    via `return_20_bar_pct > 0` (+8), intraday aligné via `perf_stock_1d > 0` (+6) ;
    `higher_lows_count` indisponible avant F2 → 0 (+ unavailable).
  - `structure_quality` /20 : indisponible avant F2 (niveaux/consolidation) →
    documenter que ce composant vaut 0 en F1.
  - `volume_quality` /15 : `rvol >= 1.5` (+7) ; les 2 autres sous-critères attendent F2.
  - `risk_quality` /20 : proxies F1 : `atr_pct` dans une plage saine (+4) ;
    le reste attend l'exécution (stop structurel).
  - `market_context` /10 : attend F3 (`spy_above_vwap`…) → 0 en F1.
  - `fundamental_context` /10 : attend les données externes → 0 en F1.
  - `execution_quality` /5 : `spread_pct <= 0.3` (+3), liquidité (+2 si volume moyen
    suffisant — seuil configurable).
- [x] Barème d'interprétation de skills.md §9.1 (≥80 excellent / 65–79 acceptable /
  50–64 faible / <50 no-go) exposé dans la réponse API (`score_grade`).
- [x] **Le score ne remplace jamais les refus automatiques** (skills.md §9.1) : la gate
  qualité et le filtre liquidité priment quel que soit le score.
- [x] Compatibilité : conserver `discovery_score`/`risk_adjusted_score` actuels pendant
  une phase de recouvrement (les seuils `detected` de `_status` en dépendent) ;
  brancher le nouveau score comme champ additionnel `quality_score` + breakdown — fait.
  **Reste ouvert (volontairement)** : basculer `_status` sur `quality_score` dans un
  second commit, une fois le score observé pendant la collecte (étape 9).
- [x] Tests `tests/test_scoring_v2.py` : chaque sous-critère isolément (snapshot
  minimal qui ne déclenche que lui) ; somme et bornes ; sous-critères indisponibles
  listés ; grades aux frontières (79/80…).

**Critère de done étape 8 :** `quality_score` + breakdown exposés dans l'API et le
panneau Radar, composition testée sous-critère par sous-critère, l'ancien score
toujours présent.

---

## Étape 9 — Collecte : laisser tourner et surveiller (pas de code, de la discipline)

- [ ] Scanner actif chaque jour de RTH (l'app doit tourner : c'est le chemin critique
  vers les 300 outcomes, aucune étape de code ne le remplace).
- [ ] Vérification hebdomadaire (requête SQL ou panneau Radar) : nombre d'outcomes
  PENDING/EVALUATED, répartition par technique, présence des tags.
- [ ] Ne PAS ajuster les seuils à la main pendant la collecte (skills.md §32.2ter :
  un paramètre à la fois, et c'est le rôle du learning loop).

---

## P3 — Lots F2/F3 et ML (**NE PAS IMPLÉMENTER MAINTENANT**)

Déclencheur inchangé : **≥ 300 outcomes évalués sur au moins 3 techniques distinctes.**

- Lot F2 (mapping §2) : niveaux en zones (`resistance_zone_min/max`, `level_touches`),
  `higher_lows_count`, compression (`consolidation_bars`, `range_compression_pct`),
  opening range, **features d'état séquentiel** (`breakout_confirmed_bars`,
  `retest_held_bars`, `reclaim_confirmed`, `failed_breakdown_detected`) — machine à
  états par symbole dans le moteur de features, jamais dans le langage de règles
  (constat d'architecture du mapping §0). Débloque les setups 2, 3, 5, 8, 10, 11.
- Lot F3 : `spy_above_vwap`, `qqq_above_vwap`, `vix_trend`, `market_regime` —
  d'abord champs de règles déclaratives ; alimente aussi le tag `market_regime`
  réservé en 6.1 et les composants scoring gelés en étape 8.
- Meta-labeling ML (LightGBM / rég. logistique calibrée sur `P(label_1r=1)`),
  walk-forward strict — il filtre, il ne détecte pas.

### Données externes — reportées (dépendances hors app)
Short interest, float, catalyseurs/news, earnings (skills.md §3.3, §21, §26) :
débloqueraient le setup 12 (short squeeze), le filtre `EARNINGS_IMMINENT`, le tag
`had_catalyst` (réservé en 6.1) et `fundamental_context` du scoring. Ne bloquer
aucune étape dessus.

### À ne PAS faire (accord des documents source)
- Pas de ML avant le seuil de déclenchement.
- Pas d'opérateurs temporels dans le langage de règles (l'état séquentiel = features).
- Pas de deep learning / RL / LLM décisionnaire.
- Pas de nouvelle infra tant que SQLite suffit (< ~100k lignes).
- Pas d'optimisation massive de paramètres sur tout l'historique.

---

## Invariants de sécurité (tests dédiés, `tests/test_techniques_security.py` à compléter)

1. [x] `execution_allowed: false` / `can_send_order: false` sur **toute** opportunité
   issue des techniques — inchangé, re-vérifier après chaque étape.
2. [x] Aucun import de `app/engine/order_manager` dans les modules de détection
   (test statique existant — y ajouter les nouveaux modules `context_tags.py`, etc.).
   L'import des **constantes** de `trade_guards` (6.3) est autorisé : constantes
   pures, aucun chemin d'exécution (le test statique doit le distinguer, ou passer
   par `app/decision_codes.py`).
3. [x] Aucun `eval()`/`exec()`/`compile()` dans les nouveaux modules (test statique).
4. [x] Kill-switch `learning.enabled=false` → zéro mutation, y compris les nouvelles
   variantes mono-paramètre et la migration 7.7 (qui n'est PAS du learning : elle
   s'exécute indépendamment du kill-switch, mais est tracée et one-shot).
5. [x] Apprentissage et stats figés hors RTH ; l'enregistrement d'outcomes reste
   RTH-only après le refactor 6.2.
6. [x] Toute décision automatique (gate, revision, variante, promotion) auditable dans
   `decision_traces`.

---

## Ordre d'exécution et discipline

| Étape | Livrable | Pourquoi cet ordre |
|---|---|---|
| 6.1–6.3 | Tags de contexte + gate qualité + reason codes tracés | Conditionne la valeur de CHAQUE outcome futur ; irréversible si raté |
| 6.4–6.5 | Mutation mono-paramètre + versioning | À corriger avant la première promotion de variante |
| 7 | Features F1 + nouvelles techniques + filtre spread | Débloque les détections de qualité et la pénalité lunch |
| 8 | Scoring §9.1 | Dépend des features F1 |
| 9 | Collecte RTH continue | Chemin critique vers les 300 outcomes |
| P3 | F2/F3/ML | Gated : ≥ 300 outcomes évalués, ≥ 3 techniques |

**Chaque étape est livrable seule. Ne pas commencer une étape avant que les tests de
la précédente soient verts. Les étapes 6.1 et 6.2 priment sur tout : chaque jour de
RTH scanné sans elles produit de la donnée dégradée.**
