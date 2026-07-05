# Étude consolidée — Module intelligent de détection d'opportunités

> **Destinataire : Claude Code.** Ce document synthétise deux sources : (1) le design doc
> `intelligent-scanner-design.md` (proposition scoped, construite sur l'app existante) et
> (2) une étude d'architecture cible « Opportunity Intelligence Engine » (vision long terme).
> Il tranche les divergences, corrige les faiblesses identifiées, et fournit un plan
> d'implémentation phasé directement exécutable.

---

## 1. Verdict de l'analyse

### 1.1 Les deux documents sont complémentaires, pas concurrents

| Aspect | Design doc (doc 1) | Étude cible (doc 2) | Décision |
|---|---|---|---|
| Périmètre | Détection uniquement, consultatif | Détection → setup → apprentissage complet | **Doc 1 pour l'implémentation immédiate** |
| Horizon | 4-5 sessions | 6 phases, plusieurs mois | Doc 2 = roadmap phases 3+ |
| Stack | SQLite existant, zéro nouvelle infra | PostgreSQL, MLflow, DuckDB, Redis, vectorbt | Rester sur SQLite tant que < ~100k lignes |
| Apprentissage | Mutation de seuils sur règles déclaratives | Meta-labeling ML (LightGBM) au-dessus de règles | Doc 1 d'abord ; le ML exige un dataset qui n'existe pas encore |
| Labels | Forward return brut +1j/+3j | Triple barrier R-based, MFE/MAE | **⚠️ Corriger doc 1 dès P2-a** (voir §2) |
| Sécurité | `execution_allowed: false` partout, règles whitelistées | Risk gate + human approval | Identiques sur le principe — conserver les deux garde-fous |

### 1.2 Ce que le design doc fait bien (à conserver tel quel)

- **Construit sur l'existant** : Market Context, scanner 30 s, shortlist, pattern `forecast_outcomes` déjà en prod. Aucune réinvention.
- **Règles déclaratives JSON, jamais d'`eval()`** : champs et opérateurs whitelistés, interpréteur maison ~40 lignes. C'est exactement le « Niveau 1 — règles déterministes » que recommande l'étude.
- **P1 sans changement de comportement** : migration des 7 règles en dur vers la bibliothèque avec test de non-régression. Livraison sans risque.
- **Garde-fous du learning loop** : warmup 30 échantillons, plafond 20 techniques actives, builtins jamais supprimées, kill-switch, traces auditables, stats figées hors RTH.

### 1.3 Les 4 faiblesses du design doc (corrigées par l'étude)

1. **Label naïf.** `forward_return_pct > 0` à +1j/+3j est un label à horizon fixe : il ignore le risque. Un stock qui fait +0,2 % après avoir drawdowné -4 % compte comme un « hit ». L'étude a raison : le bon label est *triple barrier* — « +1R atteint avant -1R ? » — même si aucun trade réel n'est pris.
2. **Pas de snapshot de features à la détection.** Sans `features_snapshot` stocké au moment du match, impossible de construire un dataset ML plus tard. Coût : une colonne JSON. Bénéfice : la phase 4 de l'étude devient gratuite.
3. **Pas de MFE/MAE.** Maximum Favorable/Adverse Excursion sur la fenêtre d'évaluation : deux colonnes de plus, indispensables pour distinguer « la technique détecte des mouvements exploitables » de « le prix finit vaguement plus haut ».
4. **Pas de feedback humain.** L'étude insiste : « ce setup est faux », « trop tard », « stop illogique » sont des données d'apprentissage. Une colonne `human_feedback` + un bouton dans l'UI suffisent pour commencer à les capturer.

### 1.4 Ce qu'il ne faut PAS faire maintenant (accord des deux documents)

- Pas de ML avant d'avoir ≥ quelques centaines d'outcomes évalués par type de setup.
- Pas de deep learning / RL / LLM décisionnaire.
- Pas de nouvelle infra (PostgreSQL, MLflow, Redis, Feast) avant que SQLite soit le goulot.
- Aucun chemin vers l'order manager. `execution_allowed: false` non négociable.
- Pas d'optimisation massive de paramètres sur tout l'historique (overfitting garanti). La mutation ±20 % avec validation forward sur données réelles du design doc est justement une forme de walk-forward implicite — c'est la bonne approche.

---

## 2. Spécification d'implémentation (à exécuter par Claude Code)

Le plan reprend P1-a → P2-b du design doc **avec les amendements ci-dessous**, puis ajoute P3 (pont vers le ML) issu de l'étude.

### Phase P1-a — Bibliothèque de techniques + interpréteur de règles

Conforme au design doc §4.1, §4.3, §4.4. Points d'implémentation :

- Table `detection_techniques` (schéma du design doc, inchangé).
- Interpréteur déclaratif : combinateurs `all`/`any` (récursifs, imbrication autorisée), opérateurs `>=`, `>`, `<=`, `<`, `==`, `between`. Champ inconnu ou valeur `None` dans le snapshot → la condition ne matche pas, **jamais d'exception**. Whitelist stricte des champs (ceux du snapshot Market Context listés au §4.3 du design doc).
- Seed idempotent (`INSERT OR IGNORE`) des 7 règles de `app/opportunity_scanner/detectors.py` avec `origin='builtin'`.
- `detect_opportunity_types()` remplacé par l'évaluation de la bibliothèque.
- **Test de non-régression obligatoire** : sur un jeu de snapshots synthétiques couvrant chaque règle (match / non-match / cas limites), la sortie bibliothèque == sortie `detectors.py` actuel, à l'identique.

### Phase P1-b — API + UI

Conforme au design doc §6 et §7 :

- Router `app/api/routes_techniques.py` : GET/POST/PATCH/DELETE `/api/techniques`, GET `/api/techniques/{id}/outcomes`, POST `/api/techniques/learning/run`.
- DELETE = soft delete (statut `RETIRED`), jamais de suppression physique.
- Panneau « Techniques de détection » sur la page Radar, entre *Scanner* et *Opportunity shortlist*.
- Colonne « Détecté par » dans la shortlist (`detected_by: <technique_id>`).
- *Generated scenarios* inchangé.

### Phase P2-a — Outcome tracking **(AMENDÉE — c'est ici que l'étude corrige le design doc)**

Table `detection_outcomes` du design doc, **enrichie** :

```sql
CREATE TABLE IF NOT EXISTS detection_outcomes (
    outcome_id          TEXT PRIMARY KEY,
    technique_id        TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    detected_at         TEXT NOT NULL,
    price_at_detection  REAL NOT NULL,

    -- Snapshot des features au moment de la détection (dataset ML futur, phase P3+)
    features_snapshot   TEXT NOT NULL DEFAULT '{}',   -- JSON : le snapshot Market Context complet

    -- Barrières théoriques (aucun trade réel — mesure pure)
    r_unit_pct          REAL,       -- taille du "R" théorique, ex: 1×ATR% ou stop structurel si dispo
    horizon             TEXT NOT NULL,                 -- '1d' | '3d'
    evaluation_due_at   TEXT NOT NULL,

    -- Résultats (remplis à l'échéance)
    price_at_horizon    REAL,
    forward_return_pct  REAL,       -- conservé pour continuité avec forecast_outcomes
    mfe_pct             REAL,       -- Maximum Favorable Excursion sur la fenêtre
    mae_pct             REAL,       -- Maximum Adverse Excursion sur la fenêtre
    label_1r            INTEGER,    -- 1 = +1R atteint avant -1R ; 0 = -1R d'abord ; NULL = ni l'un ni l'autre (expiré)

    -- Feedback humain (optionnel, capturé via l'UI)
    human_feedback      TEXT,       -- 'good' | 'too_late' | 'false_signal' | 'bad_structure' | libre

    status              TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | EVALUATED | EXPIRED
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_detection_outcomes_due ON detection_outcomes(status, evaluation_due_at);
CREATE INDEX idx_detection_outcomes_technique ON detection_outcomes(technique_id);
```

Règles d'implémentation :

- `r_unit_pct` : par défaut 1×ATR% du symbole au moment de la détection (l'ATR est calculable depuis les données déjà disponibles ; si absent, fallback = 2 % et le noter dans le JSON). Aucun ordre, aucune position : ce sont des **barrières théoriques de mesure** (triple barrier de l'étude, §5).
- MFE/MAE et `label_1r` calculés sur les barres intrajournalières de la fenêtre d'évaluation si disponibles, sinon sur les extrêmes high/low daily (préciser la granularité utilisée dans le JSON de la ligne).
- Job d'évaluation : même mécanique que `forecast_accuracy` (design doc §5.1). Week-ends/jours fériés → échéance décalée au prochain jour de bourse.
- Stats figées hors RTH.

### Phase P2-b — Learning loop

Conforme au design doc §5.3, avec **un amendement** : l'`expectancy` utilisée pour retirer/promouvoir se calcule sur le **label R-based** (`label_1r`, MFE/MAE), pas sur le forward return brut :

```
expectancy_r = hit_rate_1r × avg(mfe_pct | label_1r=1) − (1 − hit_rate_1r) × avg(|mae_pct| | label_1r=0)
```

Le forward return brut reste affiché dans l'UI (lisible), mais les décisions automatiques se prennent sur `expectancy_r`.

Garde-fous inchangés (tous obligatoires) :
- `sample_size < 30` → WARMUP, aucune décision.
- Plafond 20 techniques ACTIVE.
- Builtins jamais supprimées.
- Variantes : ±20 % sur les seuils, statut CANDIDATE, promotion uniquement si ≥ 30 échantillons ET meilleure `expectancy_r` que le parent.
- Toute décision automatique tracée dans `decision_traces`.
- Kill-switch `opportunity_scanner.learning.enabled: false` fige toute mutation.

### Phase P3 (futur, ne pas implémenter maintenant) — pont vers le ML de l'étude

Grâce à `features_snapshot` + `label_1r` + MFE/MAE collectés en P2, le dataset ML se construira par simple requête SQL. À ce moment-là seulement :

1. **Régime de marché** (étude §6 niveau 3) : ajouter au snapshot 3-4 champs de régime (SPY vs EMA50, tendance VIX, breadth) — utilisables d'abord comme simples champs de règles déclaratives, avant tout ML.
2. **Meta-labeling** (étude §3) : un modèle tabulaire (LightGBM / régression logistique calibrée) qui apprend `P(label_1r=1)` par-dessus les techniques — il filtre, il ne détecte pas. Validation walk-forward stricte, purged CV, jamais d'optimisation sur tout l'historique.
3. **Migration d'infra seulement si nécessaire** (DuckDB/Parquet pour l'historique, MLflow pour le registry) — pas avant.

Le critère de déclenchement de P3 : **≥ 300 outcomes évalués** sur au moins 3 techniques distinctes.

---

## 3. Invariants de sécurité (non négociables, à vérifier par les tests)

1. `execution_allowed: false` / `can_send_order: false` sur toute opportunité issue des techniques.
2. Aucun import, aucune route, aucun chemin d'appel entre le module techniques et l'order manager (test statique possible : grep des imports).
3. Règles déclaratives uniquement : champs et opérateurs whitelistés, aucun `eval()`, aucune exécution de code venant de la DB ou de l'API.
4. Kill-switch : `learning.enabled=false` → zéro mutation de la bibliothèque (test dédié).
5. Apprentissage figé hors RTH.
6. Toute décision automatique auditable dans `decision_traces`.

## 4. Plan de tests (design doc §10 + amendements)

- **Interpréteur** : table de vérité par opérateur et combinateur (y compris imbriqués), champ inconnu → non-match sans exception, snapshot vide → aucun match, `between` avec bornes inversées → non-match.
- **Seed** : idempotence, non-régression exacte vs `detect_opportunity_types()`.
- **Outcomes** : cycle PENDING → EVALUATED, forward return correct, décalage week-end, **MFE/MAE corrects sur séries synthétiques**, `label_1r` : cas « +1R d'abord », « -1R d'abord », « ni l'un ni l'autre » (NULL), fallback ATR absent.
- **Learning** : expectancy_r négative → RETIRED ; promotion uniquement ≥ 30 échantillons ET > parent ; plafond 20 respecté ; kill-switch → aucune mutation ; builtins indélébiles.
- **Sécurité** : invariants du §3.

## 5. Ordre d'exécution recommandé pour Claude Code

| Étape | Livrable | Critère de done |
|---|---|---|
| 1 (P1-a) | Table + interpréteur + seed + remplacement `detectors.py` | Test de non-régression vert, comportement scanner identique |
| 2 (P1-b) | API `/api/techniques` + panneau Radar + colonne « Détecté par » | Techniques visibles, activables/désactivables depuis l'UI |
| 3 (P2-a) | Table `detection_outcomes` enrichie + job d'évaluation + stats | Outcomes évalués avec fwd return, MFE/MAE, label_1r ; stats affichées |
| 4 (P2-b) | Learning loop + garde-fous + traces + kill-switch | Cycle complet simulable via POST `/learning/run` sur données synthétiques |
| 5 (P2-b bis) | Bouton feedback humain sur la shortlist → `human_feedback` | Feedback persisté et visible dans le détail technique |

Chaque étape est livrable seule. Ne pas commencer une étape avant que les tests de la précédente soient verts.

---

*Synthèse produite le 2026-07-04 à partir de `intelligent-scanner-design.md` et de l'étude « Opportunity Intelligence Engine ». Le design doc reste la référence pour tout détail non contredit ici ; en cas de conflit, ce document prime (il intègre les corrections de labels).*
