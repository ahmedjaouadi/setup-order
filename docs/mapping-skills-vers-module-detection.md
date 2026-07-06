# Mapping skills.md (v2.0) → Module intelligent de détection

> **Complément de `etude-module-intelligent-trading.md`, à donner ensemble à Claude Code.**
> Objet : traduire la base de connaissances `skills.md` en éléments exploitables par le
> module de détection. Le module ne lit JAMAIS skills.md au runtime — chaque concept est
> compilé soit en **feature** (champ calculé du snapshot), soit en **règle** (`rule_json`
> dans `detection_techniques`), soit écarté vers le module d'exécution (hors périmètre).

---

## 0. Constat d'architecture — le point le plus important

`skills.md` couvre **trois systèmes**, pas un :

| Système | Sections skills.md | Statut dans notre plan |
|---|---|---|
| **Détection** (notre module) | 2, 4, 5, 6, 7, 9.1, 10–21, 25, 25bis, 28bis, 32 | Mappé ci-dessous |
| **Exécution / risk management** | 2.2, 8, 22–24bis, 27bis, 31, 34 | **Hors périmètre** — module d'exécution séparé, comme l'exige le design doc (`execution_allowed: false`) |
| **Conventions transverses** | 2.5 (status+reason_code), 30bis (versioning), 34.5 (logs) | **À adopter dès P1** (voir §4) |

Second constat structurel : le `rule_json` du design doc est **stateless** (évalué sur un
snapshot instantané), alors que la moitié des setups de skills.md sont des **séquences**
(cassure PUIS retest PUIS rebond tenu). La bonne réponse n'est PAS de complexifier le
langage de règles avec des opérateurs temporels. C'est de **précalculer l'état temporel
comme features** : le moteur de features maintient `retest_held_bars`, `reclaim_confirmed`,
etc., et les règles restent des comparaisons simples sur ces champs. La complexité vit
dans les features (code testé), jamais dans les règles (données éditables).

---

## 1. Concepts → features existantes (encodables en techniques dès P1)

Champs déjà présents dans le snapshot Market Context : `perf_stock_1d`, `perf_sector_1d`,
`perf_spy_1d`, `rs_spy`, `rs_sector`, `volume_ratio`, `gap_pct`, `breakout_proximity`,
`new_intraday_high`, `spread_pct`.

| Concept skills.md | Section | Encodage rule_json |
|---|---|---|
| Momentum + force relative | 2.4, 10 | `perf_stock_1d >= 5` + `rs_spy > 0` (déjà builtin) |
| Expansion de volume | 6.2 | `volume_ratio >= 1.5` (déjà builtin) |
| Gap haussier tenu (Gap and Go simplifié) | 16 | `gap_pct >= 3` + `perf_stock_1d > 0` (déjà builtin gap-and-hold) |
| Proximité de breakout | 10 | `breakout_proximity` + `new_intraday_high` (déjà builtin) |
| Filtre liquidité | 24 | `spread_pct <= 0.5` en condition `all` de toute technique |
| Leader sectoriel | 25.1 | `rs_sector > 0` + `perf_sector_1d > perf_spy_1d` |

**Conclusion P1 : rien à ajouter.** Les 7 builtins couvrent déjà ce sous-ensemble. Le seed
du design doc est suffisant.

## 2. Concepts → features à développer (extension du snapshot, prérequis avant nouvelles techniques)

Par ordre de rentabilité (impact / effort). Chaque feature ajoutée = ajout à la whitelist
de l'interpréteur + tests.

### Lot F1 — peu coûteux, débloque beaucoup (recommandé dès P2)

| Feature | Source skills.md | Définition | Débloque |
|---|---|---|---|
| `rvol` | **6.2bis** | Volume cumulé du jour à l'instant t / moyenne au même instant (20 j). Mode `SAME_TIME_OF_DAY` — le `volume_ratio` brut actuel est explicitement identifié comme trompeur (courbe en U intraday) | Toutes les techniques volume, en mieux |
| `atr_pct` | 7.1 | ATR / prix. **Déjà requis par l'étude** pour `r_unit_pct` des outcomes (P2-a) — donc coût marginal nul | Anti-extension, qualité de stop théorique |
| `dist_vwap_pct` | 5.1, 19 | (prix − VWAP) / VWAP | Gap-and-go complet (§16 : « tient au-dessus de VWAP »), extension (§6.3), VWAP reclaim |
| `time_bucket` | **25bis** | OPEN / MORNING / LUNCH / AFTERNOON / POWER_HOUR (heure NY) | Pénalité lunch (`rvol >= 1.5` exigé entre 11:30–14:00), fenêtres horaires par technique |
| `price_above_ema20`, `price_above_sma50` | 4.1 | Booléens daily | Filtre de tendance sur toute technique long |

### Lot F2 — structurel, plus coûteux (P2 tardif / P3)

| Feature | Source | Définition | Débloque |
|---|---|---|---|
| `resistance_zone_min/max`, `support_zone_min/max`, `level_touches` | 5.1–5.3 | Détection de niveaux **en zones** (jamais prix exacts, §5.3), clusters de highs/lows locaux 15m + daily | Setups 1, 2, 9, 11 en version complète |
| `higher_lows_count` | 4.1 | Nombre de creux ascendants consécutifs (15m et daily) | Qualité de tendance, pullback |
| `consolidation_bars`, `range_compression_pct` | 4.3, 15, 18 | Durée et resserrement du range | High tight flag, base breakout |
| `opening_range_high/low`, `or_breakout` | 14 | Range des 30 premières minutes | ORB (avec `wait_after_open_minutes` via `time_bucket`) |
| **Features d'état séquentiel** : `breakout_confirmed_bars`, `retest_zone_active`, `retest_held_bars`, `reclaim_confirmed`, `failed_breakdown_detected` | 11, 12, 17, 23 | Machine à états par symbole maintenue par le moteur de features (le scanner tourne toutes les 30 s : l'état persiste entre scans) | Breakout-retest, reclaim, failed breakdown, VWAP reclaim — les setups « prudents », les plus précieux de skills.md |

### Lot F3 — régime de marché (P3, conforme à l'étude §6 niveau 3)

| Feature | Source | Définition |
|---|---|---|
| `spy_above_vwap`, `qqq_above_vwap` | 25.1 | Booléens |
| `vix_trend` | 25.2 | rising / stable / falling |
| `market_regime` | 25 | FAVORABLE / NEUTRAL / DEFAVORABLE (règle composée des trois précédents) |

D'abord simples champs de règles déclaratives (`market_regime == FAVORABLE` en condition),
ML seulement bien plus tard.

### Données externes — reporter (dépendances hors app)

Short interest, float, catalyseurs/news, earnings calendar, fondamentaux (§3.3, §21) :
nécessitent des sources de données non branchées. Le setup 12 (short squeeze) et le
filtre `EARNINGS_IMMINENT` attendent ces sources. Ne pas bloquer P1–P2 dessus.

## 3. Les 12 setups de skills.md — statut de faisabilité

| # | Setup | Faisable avec | Phase |
|---|---|---|---|
| 1 | Momentum breakout | Builtins actuels (version simplifiée) ; complet avec F2 niveaux | **P1** / P3 |
| 7 | Gap and Go | Builtin gap-and-hold ; complet avec `dist_vwap_pct` + `rvol` | **P1** / P2 |
| 5 | Opening Range Breakout | F1 (`time_bucket`) + F2 (`opening_range_*`) | P2–P3 |
| 4 | Pullback tendance | F1 (EMA/VWAP) + F2 (`higher_lows_count`) | P3 |
| 11 | Support bounce | F2 (zones de support) | P3 |
| 6 | High tight flag | F2 (compression) + `atr_pct` | P3 |
| 9 | Base breakout | F2 (compression multi-jours) | P3 |
| 2 | Breakout retest | F2 état séquentiel | P3 |
| 3 | Reclaim | F2 état séquentiel | P3 |
| 8 | Failed breakdown | F2 état séquentiel | P3 |
| 10 | VWAP reclaim | F1 VWAP + F2 état séquentiel | P3 |
| 12 | Short squeeze | Données externes (short interest, float) | Reporté |

## 4. Conventions de skills.md à adopter immédiatement (coût quasi nul, gain élevé)

1. **`status` + `reason_code` (§2.5)** — le référentiel canonique de skills.md est
   meilleur que des statuts ad hoc. À utiliser pour les décisions du scanner et du
   learning loop (dans `decision_traces`) : un non-match reste silencieux, mais tout
   refus qualifié (données stale, spread) est loggé `(status, reason_code)`. Rend les
   logs exploitables statistiquement — exactement l'argument de skills.md.
2. **Data quality gate (§28bis)** — avant toute évaluation de techniques : staleness,
   cohérence OHLC, bid < ask. Échec → `(PAUSED, STALE_DATA)`, scan sauté, jamais de
   détection sur données douteuses. Protège aussi les outcomes (pas de forward return
   calculé sur un prix aberrant).
3. **Tags de contexte (§32.2bis)** — `time_bucket`, `rvol_bucket`, `spread_bucket`,
   `day_of_week`, `market_regime` : à inclure dans le `features_snapshot` de
   `detection_outcomes` dès P2-a. C'est ce qui permettra de répondre à « les détections
   pendant le lunch avec rvol < 1.2 sont-elles rentables ? » sans re-collecter.
4. **Un paramètre à la fois (§32.2ter)** — déjà l'esprit du learning loop (variantes
   ±20 % testées individuellement contre leur parent). À rendre explicite : une variante
   CANDIDATE ne mute qu'**un seul seuil** de son parent, jamais plusieurs — sinon
   impossible d'attribuer l'effet.
5. **Versioning (§30bis)** — ajouter `config_version` et `revision` à
   `detection_techniques` ; toute édition de `rule_json` incrémente `revision` et
   journalise avant/après dans `decision_traces`. Le learning doit pouvoir rejouer une
   décision avec la règle exacte de l'époque.

## 5. Ce que le module de détection n'utilise PAS de skills.md (et pourquoi)

Réservé au futur module d'exécution — aucun de ces concepts n'entre dans
`detection_techniques` :

- Position sizing, `max_risk_usd`, `never_lower_stop`, breakeven, trailing (§2.2, 8) ;
- Anti-chase en tant que blocage d'ordre (§22) — la notion de « prix trop étendu » peut
  en revanche devenir un simple filtre de détection via `atr_pct`/`dist_vwap_pct` ;
- Ordres, bracket/OCA, TIF, fills partiels, ordres orphelins (§27bis, 31) ;
- Circuit breakers journaliers, limites d'exposition/corrélation (§34.3, 34.4) ;
- Coûts/slippage (§24bis) — pertinent uniquement au moment du backtest event-driven (P3).

Le score pondéré du §9.1 (100 points avec sous-critères) est quant à lui un **candidat
d'évolution pour `app/opportunity_scanner/scoring.py`** — pas pour la bibliothèque de
techniques. À traiter comme un chantier séparé, après F1 (la plupart des sous-critères
exigent rvol, EMA, VWAP, niveaux).

## 6. Instruction de synthèse pour Claude Code

1. Exécuter P1-a/P1-b tels que définis dans `etude-module-intelligent-trading.md` —
   skills.md n'y change rien (les builtins couvrent déjà les concepts encodables).
2. En P2-a, intégrer les conventions du §4 ci-dessus : `status+reason_code`, data quality
   gate, tags de contexte dans `features_snapshot`, versioning des techniques, mutation
   mono-paramètre.
3. Implémenter le **lot F1** de features (rvol, atr_pct, dist_vwap_pct, time_bucket,
   EMA/SMA) comme extension du snapshot Market Context + whitelist de l'interpréteur,
   puis seeder les techniques nouvelles correspondantes (Gap and Go complet, pénalité
   lunch) en `origin='builtin'`, `status='ACTIVE'`.
4. Les lots F2 (niveaux, séquences) et F3 (régime) sont des chantiers P3, à ne commencer
   qu'après ≥ 300 outcomes évalués — critère inchangé de l'étude.
5. skills.md reste le document de référence métier : à chaque nouvelle feature ou
   technique, vérifier la définition dans skills.md et citer la section dans la
   description de la technique (`description` de `detection_techniques`), pour que le
   panneau UI affiche la source du concept.

---

*Mapping produit le 2026-07-05 à partir de skills.md v2.0. En cas de conflit entre
skills.md et le design doc / l'étude consolidée sur le périmètre (détection vs exécution),
le design doc prime : le module de détection reste strictement consultatif.*
