# TODO — Ordres & Positions broker-truth, ordre manuel, shortlist actionnable, fiabilité du scan

Remplace l'ancien TODO (étapes 6 → 8 du module de détection, toutes livrées :
commits `f002951` → `8ac711a`). Restent de l'ancien plan : l'étape 9 (collecte,
discipline) et le gate P3 — repris en fin de fichier.

Audit du code du 2026-07-06 (base de ce plan) :

- La page « Ordres & Positions » mélange trois sources : tableau **Positions**
  (fusion locale+broker qui garde des lignes locales orphelines), tableau **Ordres**
  (historique local + overlay broker, 16 colonnes, redondant), tableau
  **Broker Reality** (vérité TWS, lecture seule). Les actions (Cancel, Attach SL,
  Move stop, Suppr) sont éparpillées entre les deux premiers.
- Le fix « broker fait autorité » sur `_merge_position_snapshots`
  (`trading_engine.py:1198`) est **déjà appliqué dans le working tree, non committé** —
  à finaliser en 10.1.
- `opportunity_to_scenario_mapper.py` calcule déjà `entry.trigger_price`,
  `entry.limit_price`, `trailing_stop_loss.initial_stop` (+ `ambiguities` quand un
  niveau manque) — la shortlist ne les expose pas.
- `outcome_tracker.py` fait déjà l'évaluation triple-barrier des détections
  (`label_1r` 1R-avant--1R, `hit_rate`, `expectancy_r`, `technique_stats`) — il
  manque l'exposition UI/API « combien de signaux corrects / faux » et la
  vérification que la collecte tourne réellement (constat étape 9 : 0 outcome).

**Périmètre sécurité inchangé** : tout ordre (auto, manuel, modification de stop)
passe par le pipeline `trade_guards` / order manager existant. La détection reste
strictement consultative (`execution_allowed: false`). Jamais d'`eval()`.

---

## Règles d'ingénierie (applicables à CHAQUE étape)

Une étape n'est pas « done » si l'une d'elles est violée.

- [ ] **Typage strict** : tout nouveau module passe `mypy` sans erreur, jamais dans
  les overrides de `pyproject.toml`.
- [ ] **Lint/format** : `ruff check` et `black --check` verts avant chaque commit.
- [ ] **Tests d'abord ou avec le code** ; ne pas commencer l'étape N+1 tant que les
  tests de l'étape N ne sont pas verts.
- [ ] **Chaque étape livrable seule** : un commit (ou petite série) par étape, l'app
  reste fonctionnelle entre chaque commit.
- [ ] **Séparation des couches** : SQL dans les repositories, métier dans les
  services, validation Pydantic dans les schemas, routers minces.
- [ ] **Réutiliser les patterns existants** : traces `decision_traces`, migrations
  idempotentes `PRAGMA table_info` + `ALTER TABLE`, statuts broker normalisés
  (`normalize_broker_order_status`).
- [ ] **Docs** : mettre à jour `docs/implementation-status.md` à la fin de chaque étape.

---

## Étape 10 — « Ordres & Positions » = miroir temps réel de TWS

Objectif produit : quand un setup est confirmé, l'ordre part vers TWS et apparaît
sous cet onglet ; la page montre **exactement** ce que TWS connaît (ordres actifs,
exécutions, positions), et toutes les actions sur un titre donné (modifier le stop,
annuler un ordre, …) se font depuis cette interface.

### 10.1 Positions = reflet exact du broker (décision prise : broker fait autorité)

- [ ] Finaliser le fix déjà présent dans le working tree
  (`trading_engine.py` : `_merge_position_snapshots(local, broker, broker_connected)`) :
  broker connecté → seules les positions confirmées par TWS (qty ≠ 0) s'affichent,
  enrichies des champs locaux (stop courant, setup_id) ; broker déconnecté →
  fallback sur les positions locales (comportement actuel conservé).
- [ ] Tests `tests/test_orders_positions_broker_truth.py` (compléter) :
  position locale orpheline (fermée côté TWS) + broker connecté → absente du
  snapshot ; broker déconnecté → présente (fallback) ; position broker sans ligne
  locale → présente avec `setup_id = broker:<symbol>` ; enrichissement
  `current_stop` local conservé.
- [ ] Vérifier la cohérence des compteurs du bandeau (« Positions ouvertes »,
  « PnL latent ») : ils doivent compter la même liste que le tableau (aujourd'hui
  `open_positions_count` préfère `broker_reality.broker_positions_count` — OK, mais
  ajouter un test de cohérence tableau ↔ compteur).
- [ ] Commit dédié (inclut la revue du `run.py` modifié non committé — à trier :
  committer si lié, sinon stash).

### 10.2 Refonte de la section « Ordres » : vue broker temps réel unique + actions

Décision UX (remplace la réponse « Supprimer » donnée avant la reformulation du
besoin) : **une seule vue ordres, alimentée par la vérité TWS**, avec les actions
intégrées. Le tableau « Ordres » local (16 colonnes) et le tableau « Broker
Reality » actuel fusionnent.

- [ ] Backend — snapshot ordres unifié : partir de `_orders_with_broker_overlay`
  (trading_engine.py:1217) mais inverser l'autorité comme en 10.1 : broker connecté
  → la liste = ordres ouverts TWS + exécutions du jour (fills), chaque ligne
  enrichie des métadonnées locales (setup_id, bracket, protection) ; les intentions
  locales sans ordre broker correspondant restent visibles mais **explicitement
  marquées** `NO_BROKER_ORDER` (elles ne doivent plus se confondre avec des ordres
  réels).
- [ ] Exécutions temps réel : exposer les fills TWS du jour (via
  `tws_connector` — vérifier ce que `reqExecutions`/le cache de session fournit
  déjà) dans le snapshot : symbole, side, qty exécutée, prix moyen d'exécution,
  heure, ordre parent.
- [ ] Rafraîchissement : la page doit refléter TWS « en temps réel » — vérifier le
  TTL du cache snapshot (`SNAPSHOT_CACHE_TTL_SECONDS`,
  `BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS`) et le polling front ; cible ≤ 5 s de
  latence perçue sur la page Ordres & Positions. Afficher l'âge de la dernière
  synchro sur la page (le champ `broker_sync_age_seconds` existe).
- [ ] UI (`orders.html` + `app.js`) : remplacer les deux tableaux par une vue
  unique par titre : colonnes essentielles seulement (Symbole, Side, Type, Qty,
  Prix/Trigger, Stop lié, Statut broker, Setup, Âge synchro, Actions). Le détail
  complet (parent id, perm id, diagnostic…) passe en ligne dépliable, pas en
  colonnes.
- [ ] Actions par ligne / par titre (toutes via les endpoints existants ou à
  compléter, toujours à travers le pipeline de sécurité) :
  - **Modifier le stop** : étendre l'action `move-stop` existante
    (`/api/positions/{symbol}/move-stop`) pour couvrir aussi la modification du
    **step/trailing** du SL (nouveau paramètre ou endpoint
    `/api/positions/{symbol}/stop` PATCH : `stop_price` | `trail_amount`) ;
    l'invariant `never_lower_stop` du scénario doit être respecté côté serveur.
  - **Annuler un ordre** : `cancel-order` existant, à brancher sur l'ID broker réel
    quand la ligne vient de TWS (aujourd'hui il prend l'ID local).
  - **Attach SL** sur position/ordre non protégé : action existante, conserver.
  - Supprimer l'action « Test fill » de cette page (réservée au broker simulé —
    la garder derrière le flag `connector === "simulated"` comme aujourd'hui, ou
    la déplacer vers une page debug).
- [ ] Traçabilité du flux setup confirmé → TWS : vérifier qu'un événement clair
  relie setup → ordre transmis → apparition sur la page (les `decision_traces` /
  events existent ; ajouter ce qui manque pour reconstituer la chaîne depuis l'UI —
  au minimum le `setup_id` doit suivre l'ordre jusqu'à la ligne affichée).
- [ ] Tests : snapshot unifié (ordre TWS matché ↔ métadonnées locales ; intention
  locale orpheline marquée ; fill du jour présent) ; endpoint modification de stop
  (refus si baisse du stop avec `never_lower_stop` ; refus si marché fermé selon
  les guards existants) ; cancel sur ID broker.

**Critère de done étape 10 :** la page affiche uniquement ce que TWS confirme
(positions ET ordres), les intentions locales sont visuellement distinctes, chaque
action (modifier stop/step, annuler, attacher SL) fonctionne depuis la page sur un
titre donné, latence de synchro affichée et ≤ 5 s. mypy/ruff/black verts.

---

## Étape 11 — Passage d'ordre manuel depuis l'UI

Objectif : pouvoir passer un ordre manuellement (hors setup automatique), avec le
même niveau de protection que les ordres issus de setups.

- [ ] Backend `POST /api/orders/manual` : payload validé Pydantic — symbole, side
  (BUY/SELL), quantité, type (MKT/LMT/STP/STP_LMT), limit/trigger selon type,
  **stop de protection obligatoire pour un BUY** (cohérent avec la philosophie
  bracket du projet : refuser un ordre d'entrée sans SL, sauf flag explicite
  `allow_unprotected` réservé au mode simulé).
- [ ] L'ordre manuel passe par le **même pipeline** que les ordres de setup :
  `trade_guards` (circuit breakers, exposition max, horaires, coûts, halt/PDT),
  order manager, enregistrement local, transmission TWS. Aucun chemin de
  contournement.
- [ ] `setup_id = "manual"` (ou id dédié `man_…`) pour que la ligne soit
  identifiable sur la page Ordres & Positions et dans les stats.
- [ ] Trace `decision_traces` : `decision_type="MANUAL_ORDER"`, payload complet +
  verdict des guards (accepté/refusé + reason_code).
- [ ] UI : formulaire « Nouvel ordre » sur la page Ordres & Positions (panneau ou
  modale) : symbole avec validation, side, qty, type, prix, stop ; affichage du
  risque calculé ($ et % du compte) AVANT confirmation ; double confirmation si
  compte réel (le mode est dans `runtime.account_mode`).
- [ ] Tests : ordre BUY sans stop → 400 ; guard qui bloque (ex. hors horaires) →
  refus tracé ; ordre valide en mode simulé → créé + visible dans le snapshot ;
  le risque affiché correspond au calcul serveur.

**Critère de done étape 11 :** un ordre manuel protégé peut être passé depuis
l'UI, il subit exactement les mêmes guards qu'un ordre de setup, il est visible et
gérable sur la page Ordres & Positions, tout est tracé.

---

## Étape 12 — Shortlist actionnable : prix d'entrée + SL proposés

Constat : la « Opportunity shortlist » liste les titres détectés mais ne dit ni où
entrer ni où placer le stop. Or `OpportunityToScenarioMapper._entry_levels` calcule
déjà trigger/limit/résistance et `trailing_stop_loss.initial_stop` — il faut
brancher, pas réinventer.

- [ ] `shortlist_service._enrich` : joindre le scénario draft correspondant
  (`source_opportunity_id`) quand il existe et exposer :
  `suggested_entry` (= `entry.trigger_price`), `suggested_limit`,
  `suggested_stop` (= `trailing_stop_loss.initial_stop`),
  `risk_per_share` (= entry − stop, si les deux présents),
  `levels_status` : `READY` si entry+stop présents, sinon `INCOMPLETE` avec la
  liste `ambiguities` du mapper (déjà calculée : « stop non dérivable », « trigger
  non dérivable »).
- [ ] Si aucun scénario draft n'existe pour l'opportunité, appeler le mapper à la
  volée (fonction pure, pas de persistance) pour produire les niveaux — ou générer
  le draft systématiquement au moment de la détection (trancher à l'implémentation ;
  préférer la 2ᵉ option si le coût est nul, le draft existe déjà pour certaines
  opportunités via `list_scenario_drafts`).
- [ ] Fallback de stop quand le mapper ne peut pas dériver (`initial_stop` None) :
  proposer un stop ATR (`price − k × atr_15m`, k configurable, défaut 1.5 —
  cohérent avec `r_unit_pct` du outcome tracker) **marqué comme
  `stop_source: "ATR_FALLBACK"`** pour que l'utilisateur sache que ce n'est pas un
  niveau structurel.
- [ ] UI shortlist (`opportunity_radar.html` / `app.js`) : colonnes « Entrée »,
  « SL », « R/share » ; badge `INCOMPLETE` avec tooltip des ambiguïtés ; les
  niveaux sont **consultatifs** (aucun bouton d'envoi d'ordre depuis la shortlist
  dans cette étape — l'exécution reste le circuit setup existant ou l'ordre manuel
  de l'étape 11).
- [ ] Tests : opportunité avec draft complet → niveaux exposés et cohérents avec le
  draft ; sans draft → mapper à la volée ; stop non dérivable → fallback ATR marqué ;
  ni ATR ni niveau → `levels_status: INCOMPLETE`, pas de crash, pas de valeur
  inventée.

**Critère de done étape 12 :** chaque ligne de la shortlist affiche entrée + SL (ou
un statut INCOMPLETE explicite), la provenance du stop est visible, tout reste
consultatif.

---

## Étape 13 — Fiabilité du scan : suivi correct/faux et boucle d'apprentissage visible

Constat : le mécanisme demandé existe déjà côté backend — `outcome_tracker.py`
enregistre chaque détection (`record_detection`, statut PENDING) puis l'évalue en
triple-barrier (`evaluate_window` : `label_1r = 1` si +1R atteint avant −1R,
0 sinon ; `hit_rate`, `expectancy_r`, `mfe/mae` ; agrégation `technique_stats`).
Le learning loop mute déjà les techniques sur ces stats (étape 6.4). Ce qui manque :
**la visibilité** (métriques « combien correct / combien faux » dans l'UI) et la
**preuve que la collecte tourne** (0 outcome au dernier constat).

- [ ] **13.1 Vérifier la chaîne de collecte de bout en bout** (préalable à tout
  affichage) : un scan RTH avec match de technique crée bien des outcomes PENDING
  (gate qualité 6.2 passée) ; le job d'évaluation (`evaluate_due`) tourne bien en
  background (`background_jobs.py`) et passe les outcomes à EVALUATED après leur
  fenêtre ; corriger ce qui bloque le cas échéant. Ajouter un event/trace si le job
  n'a rien à évaluer depuis N jours alors que des détections existent (détection de
  panne silencieuse).
- [ ] **13.2 API stats globales** `GET /api/techniques/stats` (ou enrichir
  l'existant) : par technique ET global — `detections_total`, `pending`,
  `evaluated`, `correct` (label_1r=1), `wrong` (label_1r=0), `indeterminate`
  (label null : fenêtre sans barrière touchée), `hit_rate`, `expectancy_r`,
  `min_samples` atteint ou non (seuil existant du learning). Réutiliser
  `aggregate_stats` — ne pas recalculer en SQL ce que le module fait déjà.
- [ ] **13.3 UI Radar — panneau « Fiabilité du scan »** : tuiles globales
  (X corrects / Y faux / Z en attente, hit rate global) + tableau par technique
  (hit_rate, expectancy, échantillon, tendance) ; sous le seuil `min_samples`,
  afficher « échantillon insuffisant » plutôt qu'un pourcentage trompeur.
- [ ] **13.4 Lien opportunité → verdict** : depuis la shortlist ou l'historique des
  opportunités, pouvoir voir le verdict a posteriori d'une détection (PENDING /
  correct / faux + mfe/mae). Jointure par `technique_id` + symbole + fenêtre de
  détection (vérifier que `detection_outcomes` porte de quoi joindre — sinon
  ajouter `opportunity_id` à `record_detection`, migration idempotente).
- [ ] **13.5 Le score seul ne suffit pas — l'assumer dans l'UI** : afficher à côté
  du `quality_score` de chaque opportunité le hit_rate historique de la technique
  qui l'a détectée (« ce signal a été correct N fois sur M ») quand l'échantillon
  le permet. C'est la réponse au « comment être confiant du score » : le score dit
  la qualité théorique du setup, le hit_rate dit ce que ce type de signal a
  réellement donné.
- [ ] Tests : stats API sur un jeu d'outcomes synthétiques (corrects/faux/pending
  mélangés) ; `opportunity_id` propagé jusqu'à l'outcome ; panneau sous
  min_samples ; job d'évaluation → transition PENDING→EVALUATED testée (existe
  peut-être déjà dans `test_detection_outcomes.py` — compléter).

**Critère de done étape 13 :** on peut répondre depuis l'UI à « combien de signaux
corrects / combien de faux, par technique et au global », chaque opportunité
affichée porte la fiabilité historique de sa technique, et la collecte est prouvée
vivante (outcomes qui avancent PENDING → EVALUATED chaque semaine de RTH).

---

## Étape 9 (reprise) — Collecte : laisser tourner et surveiller

- [ ] Scanner actif chaque jour de RTH (chemin critique vers les 300 outcomes).
- [ ] Vérification hebdomadaire : outcomes PENDING/EVALUATED, répartition par
  technique, présence des tags (le panneau 13.3 rendra ce contrôle trivial).
- [ ] Ne PAS ajuster les seuils à la main pendant la collecte (un paramètre à la
  fois — c'est le rôle du learning loop).

## P3 — Lots F2/F3 et ML (**NE PAS IMPLÉMENTER MAINTENANT**)

Déclencheur inchangé : **≥ 300 outcomes évalués sur au moins 3 techniques
distinctes.** Contenu (niveaux en zones, higher_lows, compression, état séquentiel,
contexte marché SPY/QQQ/VIX, meta-labeling ML walk-forward) et interdits (pas de ML
avant seuil, pas d'opérateurs temporels dans les règles, pas de deep learning
décisionnaire, pas de nouvelle infra tant que SQLite suffit) : voir
`docs/mapping-skills-vers-module-detection.md`.

---

## Invariants de sécurité (tests dédiés, à re-vérifier après chaque étape)

1. [ ] `execution_allowed: false` sur toute opportunité issue des techniques — la
   shortlist enrichie (étape 12) reste consultative.
2. [ ] Aucun import de `app/engine/order_manager` dans les modules de détection.
3. [ ] Aucun `eval()`/`exec()`/`compile()` dans les nouveaux modules.
4. [ ] Tout ordre (setup, manuel, modification de stop) passe par `trade_guards` —
   aucun endpoint de l'étape 10/11 ne contourne les guards.
5. [ ] `never_lower_stop` respecté côté serveur pour toute modification de stop.
6. [ ] Toute décision automatique ou action manuelle sensible auditable dans
   `decision_traces` / events.

---

## Ordre d'exécution

| Étape | Livrable | Pourquoi cet ordre |
|---|---|---|
| 10.1 | Positions = vérité broker | Fix déjà en working tree, à finaliser + tester en premier |
| 10.2 | Vue ordres TWS unifiée + actions (stop/step, cancel) | Cœur de la demande produit |
| 11 | Ordre manuel via le pipeline de sécurité | Dépend de la page assainie (10) |
| 12 | Shortlist : entrée + SL | Indépendant, branché sur le mapper existant |
| 13 | Fiabilité du scan (correct/faux) visible | Backend déjà là ; débloque la confiance dans le score |
| 9 | Collecte RTH continue | Toujours le chemin critique vers P3 |

**Chaque étape est livrable seule. Ne pas commencer une étape avant que les tests
de la précédente soient verts.**
