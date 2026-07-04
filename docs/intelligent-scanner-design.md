# Module intelligent de détection d'opportunités — Document de conception

> Statut : **PROPOSITION — à valider avant implémentation**
> Périmètre : détection uniquement. Comme tout le forecasting existant, ce module
> est **strictement consultatif** : il ne place jamais d'ordre et n'influence pas
> l'exécution (`execution_allowed: false` partout).

---

## 1. Objectif

Aujourd'hui, le scanner détecte des opportunités avec **7 règles codées en dur**
(`app/opportunity_scanner/detectors.py`) : momentum ≥ 5 %, force relative vs SPY/secteur,
expansion de volume ≥ 1,5×, proximité de breakout, gap-and-hold, etc.

On veut transformer ça en **module apprenant** :

1. Les techniques de détection deviennent une **bibliothèque persistée**, visible et
   éditable dans l'interface (panneau « Techniques de détection » du Radar).
2. Chaque stock détecté est **rattaché à la technique** qui l'a repéré → *Opportunity shortlist*.
3. Le module **mesure les résultats réels** de chaque technique (le stock a-t-il monté
   après la détection ?) et calcule un taux de réussite.
4. Le module **apprend** : il désactive les techniques perdantes, teste des variantes
   de seuils, et **ajoute automatiquement** les variantes gagnantes à la bibliothèque.

## 2. Ce qui existe déjà (on construit dessus, on ne réinvente pas)

| Brique | Fichier | Rôle actuel |
|---|---|---|
| Market Context | `app/market_context/service.py` | % up/down vs séance précédente, RS vs secteur/SPY, secteurs |
| Scanner | `app/opportunities/scanner.py` | scan périodique (30 s) de la watchlist, filtres liquidité |
| Détecteurs | `app/opportunity_scanner/detectors.py` | ⚠️ règles en dur → **à remplacer par la bibliothèque** |
| Scoring | `app/opportunity_scanner/scoring.py` | score 0–100 par opportunité |
| Shortlist | table `opportunities` + `/api/opportunities/shortlist` | top 20 trié |
| Scénarios | table `scenario_drafts` | brouillons par stock (rubrique *Generated scenarios* — inchangée) |
| Config | `app/settings.py` → `opportunity_scanner` | seuils actuels (`context_thresholds`, `scanners`) |

## 3. Architecture cible

```
                       ┌────────────────────────────────┐
                       │  TECHNIQUE LIBRARY (nouveau)   │
                       │  table: detection_techniques   │
                       │  builtin + learned + manual    │
                       └───────────────┬────────────────┘
                                       │ techniques actives
Market Context ──snapshot──▶ Scanner ──┴─▶ TechniqueEvaluator (nouveau)
 (% 1d, RS, volume, gap)                      │ pour chaque stock × technique
                                              ▼
                                   match ? → opportunité taguée
                                   `detected_by: <technique_id>`
                                              │
                                              ▼
                                   Opportunity shortlist (existant)
                                              │ à la détection
                                              ▼
                       ┌────────────────────────────────┐
                       │  OUTCOME TRACKER (nouveau)     │
                       │  table: detection_outcomes     │
                       │  mesure fwd return +1j / +3j   │
                       └───────────────┬────────────────┘
                                       │ stats par technique
                                       ▼
                       ┌────────────────────────────────┐
                       │  LEARNING LOOP (nouveau)       │
                       │  - hit-rate / expectancy       │
                       │  - désactive les perdantes     │
                       │  - mute les seuils (variantes) │
                       │  - promeut les gagnantes       │
                       └────────────────────────────────┘
```

## 4. Schéma de données (SQLite, dans `app/storage/database.py`)

### 4.1 `detection_techniques` — la bibliothèque

```sql
CREATE TABLE IF NOT EXISTS detection_techniques (
    technique_id   TEXT PRIMARY KEY,          -- ex: "gap_and_hold_v1"
    name           TEXT NOT NULL,             -- lisible: "Gap and hold"
    description    TEXT NOT NULL DEFAULT '',
    rule_json      TEXT NOT NULL,             -- la règle, voir 4.3
    enabled        INTEGER NOT NULL DEFAULT 1,
    origin         TEXT NOT NULL,             -- 'builtin' | 'learned' | 'manual'
    parent_id      TEXT,                      -- technique d'origine si variante apprise
    status         TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | CANDIDATE | RETIRED
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
```

### 4.2 `detection_outcomes` — le suivi des résultats

```sql
CREATE TABLE IF NOT EXISTS detection_outcomes (
    outcome_id     TEXT PRIMARY KEY,
    technique_id   TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    detected_at    TEXT NOT NULL,
    price_at_detection REAL NOT NULL,
    horizon        TEXT NOT NULL,             -- '1d' | '3d'
    evaluation_due_at  TEXT NOT NULL,
    price_at_horizon   REAL,                  -- rempli à l'échéance
    forward_return_pct REAL,                  -- rempli à l'échéance
    status         TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | EVALUATED | EXPIRED
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_detection_outcomes_due ON detection_outcomes(status, evaluation_due_at);
CREATE INDEX idx_detection_outcomes_technique ON detection_outcomes(technique_id);
```

> Modèle identique à `forecast_outcomes` (déjà en prod dans l'app) : on réutilise
> le même pattern « détection → échéance → évaluation ».

### 4.3 Format de règle (`rule_json`) — déclaratif, pas de code exécutable

```json
{
  "all": [
    {"field": "gap_pct",       "op": ">=", "value": 3.0},
    {"field": "perf_stock_1d", "op": ">",  "value": 0}
  ],
  "opportunity_type": "GAP_AND_HOLD"
}
```

- Champs autorisés = ceux du snapshot Market Context existant :
  `perf_stock_1d`, `perf_sector_1d`, `perf_spy_1d`, `rs_spy`, `rs_sector`,
  `volume_ratio`, `gap_pct`, `breakout_proximity`, `new_intraday_high`, `spread_pct`.
- Opérateurs : `>=`, `>`, `<=`, `<`, `==`, `between`.
- Combinateurs : `all` (ET), `any` (OU).
- **Sécurité** : format déclaratif évalué par un interpréteur maison (~40 lignes),
  jamais d'`eval()`. Champs et opérateurs whitelistés.

### 4.4 Migration des 7 règles actuelles

Au premier démarrage, les règles de `detectors.py` sont insérées comme techniques
`origin='builtin'` (seed idempotent, `INSERT OR IGNORE`). `detect_opportunity_types()`
est remplacé par l'évaluation de la bibliothèque — comportement initial **identique**.

## 5. Boucle d'apprentissage (Learning Loop)

### 5.1 Mesure (à chaque scan, marché ouvert)

1. Technique X matche le stock S → opportunité créée + ligne `detection_outcomes`
   (PENDING, prix de détection, échéances +1 j et +3 j de bourse).
2. Un job périodique (même mécanique que `forecast_accuracy`) évalue les lignes dues :
   `forward_return_pct = (prix_échéance - prix_détection) / prix_détection × 100`.

### 5.2 Statistiques par technique

- `sample_size` : nombre de détections évaluées
- `hit_rate` : % de détections avec forward return > 0
- `avg_return` / `median_return` : rendement moyen/médian
- `expectancy` : hit_rate × gain_moyen − (1 − hit_rate) × perte_moyenne

### 5.3 Décisions automatiques (avec garde-fous)

| Condition | Action |
|---|---|
| `sample_size < 30` | Aucune décision (warmup) — affichage « WARMUP » |
| `sample_size ≥ 30` et `expectancy < 0` | Technique → `RETIRED` (désactivée, PAS supprimée) |
| `sample_size ≥ 30` et `expectancy > 0` | Génération de 2–3 **variantes** (seuils ±20 %) en statut `CANDIDATE` |
| Variante `CANDIDATE` avec `sample_size ≥ 30` et meilleure expectancy que son parent | Promue `ACTIVE`, `origin='learned'` → **c'est la « technique apprise »** |
| Variante `CANDIDATE` moins bonne que son parent | → `RETIRED` |

**Garde-fous** :
- Maximum **20 techniques ACTIVE** simultanées (pas d'explosion combinatoire).
- Les techniques `builtin` ne sont jamais supprimées, seulement désactivables.
- Chaque décision automatique est tracée dans `decision_traces` (auditables dans Observability).
- Un kill-switch global : `opportunity_scanner.learning.enabled: false` fige tout.

## 6. API (nouveau router `app/api/routes_techniques.py`)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/techniques` | liste + stats (hit rate, samples, statut) |
| POST | `/api/techniques` | créer une technique manuelle |
| PATCH | `/api/techniques/{id}` | activer/désactiver, éditer seuils |
| DELETE | `/api/techniques/{id}` | retirer (soft delete → RETIRED) |
| GET | `/api/techniques/{id}/outcomes` | historique des détections + résultats |
| POST | `/api/techniques/learning/run` | forcer un cycle d'apprentissage (debug) |

## 7. UI — panneau « Techniques de détection » (page Radar)

Placé entre *Scanner* et *Opportunity shortlist* :

```
┌─ Techniques de détection ────────────────────────────────────────────┐
│ Technique            Statut   Origine   Hit rate   Samples   Actif  │
│ Momentum anomaly     ACTIVE   builtin   62%        124       [ON]   │
│ Gap and hold         ACTIVE   builtin   55%        89        [ON]   │
│ Gap and hold v2      ACTIVE   learned   64%        41        [ON]   │ ← apprise
│ Volume expansion     WARMUP   builtin   —          12        [ON]   │
│ RS leader strict     RETIRED  learned   38%        52        [off]  │
└──────────────────────────────────────────────────────────────────────┘
```

- Clic sur une ligne → détail : règle lisible, historique des détections, courbe de hit rate.
- Dans la **shortlist**, nouvelle colonne « Détecté par » (nom de la technique).
- *Generated scenarios* **inchangé** (brouillons par stock, comme aujourd'hui).

## 8. Ce que le module ne fera JAMAIS

- Placer ou modifier un ordre (aucun chemin vers l'order manager).
- Armer un setup automatiquement.
- Évaluer du code arbitraire (règles déclaratives whitelistées uniquement).
- Apprendre pendant le marché fermé (les stats se figent hors RTH pour ne pas
  polluer les mesures avec des prix sans cotation).

## 9. Plan d'implémentation

| Étape | Contenu | Estimation |
|---|---|---|
| **P1-a** | Table `detection_techniques` + interpréteur de règles + seed des 7 builtins + remplacement de `detectors.py` | 1 session |
| **P1-b** | API `/api/techniques` + panneau Radar + colonne « Détecté par » dans la shortlist | 1 session |
| **P2-a** | Table `detection_outcomes` + job d'évaluation (+1 j/+3 j) + stats par technique | 1 session |
| **P2-b** | Learning loop (retrait/variantes/promotion) + garde-fous + traces | 1–2 sessions |

Chaque étape est livrable et utile seule. P1 ne change **aucun comportement**
(mêmes règles, mais visibles/éditables). P2 introduit la mesure puis l'apprentissage.

## 10. Plan de tests

- **Interpréteur de règles** : table de vérité par opérateur/combinateur, champs
  inconnus → règle non matchée (jamais d'exception), snapshot vide → aucun match.
- **Seed** : idempotent, les 7 builtins reproduisent exactement `detect_opportunity_types()`
  actuel (test de non-régression sur des snapshots synthétiques).
- **Outcomes** : détection → PENDING → évaluation à l'échéance → forward return correct ;
  week-end/jours sans cotation → échéance décalée au prochain jour de bourse.
- **Learning** : expectancy négative → RETIRED ; promotion uniquement si ≥ 30 échantillons
  ET meilleure que le parent ; plafond de 20 techniques actives respecté ;
  kill-switch `learning.enabled=false` → aucune mutation.
- **Sécurité** : `can_send_order`/`executable` toujours `false` sur les opportunités
  issues des techniques ; aucune route technique n'appelle l'order manager.

---

*Document généré le 2026-07-04. À valider avant implémentation (P1-a…P2-b).*
