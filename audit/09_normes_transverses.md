# Audit en lecture seule — Lot 9 : normes transverses (complement audit 05/08)

Mode lecture seule. Aucun fichier de code n'a ete modifie. Complete
`audit/05_normalisation.md` section B (trigger/stop/confirmation) avec les
axes manquants (gate de statut, INVALIDATE conditionnel, formation vs close,
coherence config, point d'application), etendu a **tous** les setup_types
retournes par `SetupFactory.create`, pas seulement les 5 types d'entree deja
couverts en 05. Toutes les requetes SQL ont ete executees en lecture seule
(`mode=ro`) sur `data/trading_state.sqlite` (~155 Go). Fenetre de donnees
disponible : `events` du 2026-05-31T08:59 au 2026-07-17T20:23,
`stock_analysis` du 2026-06-02T12:14 au 2026-07-17T20:23 (96 344 evenements
`stock_analysis` scannes en integralite pour ce lot).

---

## 0. Liste exhaustive des setup_types (verification prealable)

`app/setups/setup_factory.py:20-29` — `SetupFactory._registry` contient
**8 entrees**, pas 5 :

```python
_registry: dict[str, type[BaseSetup]] = {
    AggressiveReboundSetup.setup_type: AggressiveReboundSetup,      # aggressive_rebound
    BreakoutRetestSetup.setup_type: BreakoutRetestSetup,            # breakout_retest
    PullbackContinuationSetup.setup_type: PullbackContinuationSetup, # pullback_continuation
    MomentumBreakoutSetup.setup_type: MomentumBreakoutSetup,        # momentum_breakout
    PositionManagementSetup.setup_type: PositionManagementSetup,    # position_management
    RangeBreakoutSetup.setup_type: RangeBreakoutSetup,              # range_breakout
    RunnerSetup.setup_type: RunnerSetup,                            # runner
    TrailingRunnerSetup.setup_type: TrailingRunnerSetup,            # trailing_runner
}
```

`setup_factory.py:32` (`cls._registry.get(str(setup_type))`) confirme que
c'est la liste complete et unique de dispatch — aucun autre registre parallele
n'existe (`app/setups/setup_type_registry.py:6-15`,
`SUPPORTED_SETUP_TYPES`, liste exactement les 8 memes valeurs, ordre
alphabetique, confirmant qu'il n'en manque aucun des deux cotes).

`runner` (`RunnerSetup`) et `trailing_runner` (`TrailingRunnerSetup`)
partagent **la meme implementation** (`app/setups/trailing_runner.py:7-42`,
`RunnerBaseSetup`, les deux classes filles ne font que redefinir
`setup_type` sans surcharger `evaluate()`) — traites comme un seul bloc de
code ci-dessous, mais comme deux entrees distinctes dans les tableaux (le
`setup_type` en base differe).

**Verification empirique en base — quels types ont reellement tourne en
production ?**

```sql
SELECT setup_type, COUNT(*) FROM setups GROUP BY setup_type ORDER BY 2 DESC;
```

| setup_type | n (table `setups`, snapshot courant) |
|---|---|
| momentum_breakout | 29 |
| aggressive_rebound | 14 |
| pullback_continuation | 13 |
| range_breakout | 11 |
| breakout_retest | 5 |

Reconstruction sur l'historique complet via `stock_analysis.processed[i].
setup_type` (identique a la methode audit 05 A.1, insensible aux setups
purges de `setups`) :

```python
# scan des 96 344 events stock_analysis, Counter sur processed[i]["setup_type"]
```

| setup_type | occurrences sur toute la periode |
|---|---|
| momentum_breakout | 89 892 |
| aggressive_rebound | 8 982 |
| pullback_continuation | 4 377 |
| range_breakout | 3 903 |
| breakout_retest | 3 608 |
| **position_management** | **0** |
| **runner** | **0** |
| **trailing_runner** | **0** |

**Constat prealable, valable pour tous les axes ci-dessous** :
`position_management`, `runner` et `trailing_runner` n'ont **jamais** ete
executes en production sur toute la fenetre disponible (0 occurrence sur
96 344 evenements). Leur analyse dans ce lot est donc **une lecture de code
pure, sans aucune verification empirique possible** — a la difference des 5
types d'entree, ou chaque affirmation de code a pu etre confrontee a des
donnees reelles. C'est signale explicitement dans chaque tableau ci-dessous
(colonne "empirique").

---

## AXE 1 — Gate `current_status` avant emission de signal d'action

Rappel audit 05 (section B, point 5) : `breakout_retest`,
`aggressive_rebound`, `pullback_continuation` testent `current_status` avant
`ENTRY_READY` ; `range_breakout` jamais ; `momentum_breakout` partiellement.
Verification ligne par ligne + extension a `position_management`/`runner`/
`trailing_runner`.

| setup_type | Gate avant `ENTRY_READY`/`RAISE_STOP` | Gate avant signal "descendant" (STATUS_CHANGE de recul) | Citation | Empirique |
|---|---|---|---|---|
| breakout_retest | Oui — `current_status == WAITING_ENTRY_SIGNAL` (`breakout_retest.py:80`), `WAITING_ACTIVATION` pour l'etape intermediaire (`:71`) | N/A (pas de signal de recul dans ce type) | `breakout_retest.py:71,80` | Oui (3 608 stock_analysis) |
| aggressive_rebound | Oui — `WAITING_ACTIVATION` (`:57`), `WAITING_ENTRY_SIGNAL` (`:63`) | N/A | `aggressive_rebound.py:57,63` | Oui (8 982) |
| pullback_continuation | Oui pour les 2 branches "positives" — `WAITING_ACTIVATION` (`:30`), `WAITING_ENTRY_SIGNAL` (`:36`) | N/A (voir Axe 2 : son seul signal "negatif" est INVALIDATE, non gate) | `pullback_continuation.py:30,36` | Oui (4 377) |
| range_breakout | **Non** — aucun test de `current_status` dans tout le corps de `evaluate()` ; le parametre n'apparait que dans la signature (`:22`), confirme par grep exhaustif | N/A (pas de signal de recul) | `range_breakout.py:19-43` | Oui (3 903, incident reel documente audit 05 A.3) |
| momentum_breakout | **Non** pour la branche `ENTRY_READY` (`:279-302`, aucun test de `current_status`) | **Non** pour `STATUS_CHANGE -> MISSED_BREAKOUT` (`:155-175`, aucun test) ; **Oui** seulement pour `STATUS_CHANGE -> WAITING_RETEST` (`:179`, teste `current_status == MISSED_BREAKOUT` avant d'emettre) | `momentum_breakout.py:155-175` (non gate), `:179` (gate), `:279-302` (non gate) | Oui (89 892) — **confirme par un 2e incident distinct de A.3, voir ci-dessous** |
| position_management | Oui — `RECONCILING_EXISTING_POSITION` -> hold (`:46-47`), `current_status not in {IN_POSITION, MANAGING_POSITION}` -> hold (`:48-49`) avant tout `RAISE_STOP` | N/A (pas de signal de recul, seul `RAISE_STOP` est emis) | `position_management.py:46-49` | **Non — 0 occurrence** |
| runner / trailing_runner | Oui — `current_status not in {IN_POSITION, MANAGING_POSITION}` -> hold (`:19`) avant tout `RAISE_STOP` | N/A | `trailing_runner.py:19` (classe partagee `RunnerBaseSetup`) | **Non — 0 occurrence** |

### Incident empirique supplementaire (non documente en 05/08) : `momentum_breakout` regresse `WAITING_RETEST -> MISSED_BREAKOUT` en boucle

```sql
SELECT message, COUNT(*) FROM events WHERE event_type='setup_transition_rejected'
GROUP BY message ORDER BY 2 DESC;
```

| message | n |
|---|---|
| `Invalid setup transition: WAITING_RETEST -> MISSED_BREAKOUT` | **9 469** |
| `Invalid setup transition: ENTRY_ORDER_PLACED -> INVALIDATED` | 956 |

`ALLOWED_TRANSITIONS[WAITING_RETEST]` (`state_machine.py:89-97`) ne contient
pas `MISSED_BREAKOUT` (seulement `WAITING_CONFIRMATION`,
`REARMED_ON_NEW_BASE`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`,
`CANCELLED`, `ERROR`) — cette transition est **rejetee a 100 %** par la
state machine, mais 9 469 fois sur la periode disponible, c'est-a-dire que
la branche `momentum_breakout.py:155-175` (detection `MISSED_BREAKOUT`, qui
ne teste jamais `current_status`) est reevaluee et retente cette
transition **chaque tick** pour tout setup deja `WAITING_RETEST` dont l'ask
reste au-dela du buffer de staleness — c'est un volume presque 10x plus
grand que l'incident INVALIDATE documente ci-dessous (Axe 2), sur le meme
type de defaut structurel (signal emis sans lire `current_status`). Cette
transition precise est **toujours rejetee sans consequence visible** (le
statut `WAITING_RETEST` n'est jamais corrompu, `ActionExecutor.
transition_setup` avale l'exception, `action_executor.py:48-59`), mais c'est
9 469 cycles d'evaluation, d'ecriture d'evenement et de log gaspilles, pour
le seul type le plus actif en production.

**Verdict Axe 1** : **norme avec durcissement, applicable a 8/8 types sans
exception structurelle.** 5/8 types respectent deja le principe (au moins
partiellement) ; 3/8 (`range_breakout` totalement, `momentum_breakout`
partiellement sur 2 branches distinctes) l'enfreignent, avec des volumes de
rejet en production qui prouvent que ce n'est pas theorique (956 + 9 469 =
10 425 rejets, soit **100 % des `setup_transition_rejected` de toute la base
de production**, `SELECT COUNT(*) FROM events WHERE event_type=
'setup_transition_rejected'` -> 10 425, deux causes identiques, deux types).

---

## AXE 2 — Emission d'INVALIDATE (ou signal destructeur) conditionnelle au statut

### Quels types peuvent emettre INVALIDATE ?

```
grep -rn "SignalAction.INVALIDATE" app/setups/
```

| fichier | ligne |
|---|---|
| `aggressive_rebound.py` | 53 |
| `breakout_retest.py` | 65 |
| `pullback_continuation.py` | 26 |
| `range_breakout.py` | 30 |

**Seuls 4 des 8 types peuvent emettre INVALIDATE** — `momentum_breakout`,
`position_management`, `runner`, `trailing_runner` n'appellent jamais
`SignalAction.INVALIDATE` (grep exhaustif, 0 occurrence dans ces 4 fichiers) ;
`momentum_breakout` utilise `STATUS_CHANGE -> MISSED_BREAKOUT` comme
equivalent fonctionnel "signal destructeur" (voir Axe 1, meme defaut mais
statut different) ; `position_management`/`runner`/`trailing_runner`
n'ont **aucun** signal destructeur dans leur `evaluate()` (seuls `HOLD` et
`RAISE_STOP`, qui ne peut que faire monter un stop — `max(candidates, ...)`
`position_management.py:114`, `RaiseStop` n'a pas de branche qui puisse
baisser un stop ni fermer une position).

### Le gate est-il present pour les 4 types concernes ?

| setup_type | INVALIDATE gate sur `current_status` ? | Citation |
|---|---|---|
| breakout_retest | **Oui** — `current_status in {WAITING_ACTIVATION, WAITING_ENTRY_SIGNAL}` est une condition explicite de la branche INVALIDATE elle-meme (`:56-63`, pas juste une garde en amont) | `breakout_retest.py:56-68` |
| aggressive_rebound | **Non** — le test `if close < close_below: return INVALIDATE` (`:51-56`) precede tout test de `current_status` dans la methode (le premier test de statut n'arrive qu'a la ligne 57, apres) | `aggressive_rebound.py:51-56` |
| pullback_continuation | **Non** — `if snapshot.price < snapshot.ema_50: return INVALIDATE` (`:24-29`) precede le premier test de `current_status` (ligne 30) | `pullback_continuation.py:22-29` |
| range_breakout | **Non** — aucun test de `current_status` n'existe nulle part dans le fichier (confirme Axe 1) | `range_breakout.py:28-33` |

**3 des 4 types capables d'INVALIDATE le font sans regarder le statut du
setup — pas seulement `aggressive_rebound`** (deja releve par D4) : le meme
defaut existe, non documente avant ce lot, sur `pullback_continuation` et
`range_breakout`.

### Consequence reelle : que se passe-t-il quand INVALIDATE est emis en position/post-entree ?

`ActionExecutor.execute_simple_action` (`action_executor.py:33-35`) route
INVALIDATE vers `transition_setup`, qui appelle `state_machine.transition()`
(`:49`) dans un `try/except` qui **avale l'exception** et se contente de
logger `setup_transition_rejected` (`:48-59`) — **aucune ecriture n'a lieu**
si la transition est invalide selon `ALLOWED_TRANSITIONS`. Verification des
cibles autorisees pour les statuts post-entree (`state_machine.py:120-190`) :

| statut de depart | `INVALIDATED` dans les cibles autorisees ? |
|---|---|
| `WAITING_ENTRY_SIGNAL` (`:120-126`) | Oui |
| `ENTRY_READY` (`:127-132`) | Oui |
| `ENTRY_ORDER_PLACED` (`:133-138`) | **Non** |
| `ENTRY_PARTIALLY_FILLED` (`:139-144`) | **Non** |
| `ENTRY_FILLED` (`:145-150`) | **Non** |
| `STOP_ORDER_PLACED`/`STOP_PLACED` (`:151-160`) | **Non** |
| `IN_POSITION` (`:169-175`) | **Non** |
| `MANAGING_POSITION` (`:176-181`) | **Non** |
| `RECONCILING_EXISTING_POSITION` (`:161-168`) | **Oui** (seul statut de position ou la cible est autorisee) |

Donc l'absence de gate dans le code de `evaluate()` **n'a pas produit de
corruption de statut en production** pour les statuts `ENTRY_ORDER_PLACED`
et au-dela (le filet de la state machine bloque effectivement l'ecriture) —
a l'exception theorique de `RECONCILING_EXISTING_POSITION`, ou la
transition **est** autorisee (voir INCERTITUDES). C'est une nuance
importante par rapport a l'incident A.3 (audit 05), qui portait sur
`ENTRY_READY` (gate absent -> **ecriture reelle** -> 2e ordre broker) : ici
le gate est absent aussi, mais la consequence observee est un **rejet
silencieux repete**, pas une corruption.

### Preuve empirique du rejet repete

```sql
SELECT message, COUNT(*) FROM events
WHERE event_type='setup_transition_rejected' AND message LIKE '%INVALIDATED%';
-- -> 'Invalid setup transition: ENTRY_ORDER_PLACED -> INVALIDATED' : 956
```

Resolution du `setup_type` des 4 `setup_id` distincts impliques (jointure
via `stock_analysis.processed[i].setup_id/setup_type`, meme methode que
Axe 1/audit 05 A.1) :

| setup_id | setup_type | n rejets | fenetre |
|---|---|---|---|
| JOBY_20260628_001 | aggressive_rebound | 807 | 2026-06-29T14:22 -> 2026-06-30T09:46 (~19h, en continu) |
| HIMX_20260628_001 | aggressive_rebound | 73 | 2026-06-29T14:24 -> 15:06 |
| QCOM_20260628_001 | aggressive_rebound | 33 | 2026-06-29T14:28 -> 15:04 |
| STM_20260628_001 | range_breakout | 43 | 2026-06-29T14:22 -> 14:40 |

**`JOBY_20260628_001` a retente la transition `ENTRY_ORDER_PLACED ->
INVALIDATED` a 807 reprises sur ~19 heures continues** — c'est-a-dire que
`evaluate()` a recalcule et retente ce signal destructeur a **chaque tick**
pendant toute cette fenetre, sans jamais reussir a l'ecrire (bloque par la
state machine), gaspillant un cycle complet (calcul + event store +
warning log) a chaque fois. `pullback_continuation`, bien que
structurellement expose au meme defaut (INVALIDATE non gate, confirme par
lecture de code), **n'apparait dans aucun des 4 `setup_id`** de cet
echantillon — silence de donnees, pas preuve d'absence de risque (meme
reserve que audit 05 pour ce type, note dans INCERTITUDES 3 la-bas).

**Verdict Axe 2** : **norme avec durcissement, applicable a 4/4 types
concernes sans exception** (`breakout_retest` conforme deja ;
`aggressive_rebound`/`pullback_continuation`/`range_breakout` a corriger).
Le risque reel n'est pas une corruption de statut (le filet
`ALLOWED_TRANSITIONS` protege deja tous les statuts post-`ENTRY_ORDER_
PLACED`), mais (a) une charge de calcul et de logs gaspillee de facon
prouvee massive (807 essais consecutifs sur un seul setup), et (b) un trou
de couverture theorique non trivial sur `RECONCILING_EXISTING_POSITION`
(voir INCERTITUDES).

---

## AXE 3 — Lecture de bougie : formation vs close

### Provenance de `MarketSnapshot.close`/`open`/`high`/`low` (prealable necessaire, absent de l'audit 05)

Trace complete : `stock_market_monitor.py:499` construit `MarketSnapshot`
depuis un dict `quote` obtenu par `broker.market_snapshot(...)`
(`stock_market_monitor.py:193`) -> `TWSBrokerConnector.market_snapshot`
(`tws_connector.py:1268-1311`) -> mode par defaut **hybrid**
(`app/settings.py`, `market_data_source="hybrid"`, bar size `"15 mins"`).

Dans `_merge_hybrid_market_snapshot` (`tws_connector.py:2635-2752`),
`open`/`high`/`low`/`close` proviennent **exclusivement** de la branche
historique (`base = signal`, ligne 2651) — jamais ecrases par le flux live
(seuls `bid`/`ask`/`last`/`price` le sont, lignes 2660-2674). La branche
historique (`_historical_quote_from_bars`, `tws_connector.py:2522-2632`) fixe
`open/high/low/close = rows[-1]...` (ligne 2553) ou `rows` vient de
`reqHistoricalDataAsync(..., bar_size="15 mins", keepUpToDate=False)`
(`:1614-1627`). **Le caractere reellement clos ou non de cette derniere
barre au moment de la requete est un comportement cote serveur IBKR non
verifiable statiquement** (signale INCERTAIN) — mais le nommage interne du
code (`"last_closed_bar.close <= resistance"`,
`momentum_breakout.py:521`) montre que les auteurs le supposent clos.
`daily_close` (`stock_market_monitor.py:516`) recoit **la meme valeur** que
`close` (`daily_close=float_value(quote.get("close")) or price`) — ce n'est
donc **pas** un agregat journalier distinct malgre son nom, consomme comme
tel par `breakout.daily_close_above` (`breakout_retest.py:70-72`).
`bullish_candle` (`models.py:227`) n'est **jamais assigne** dans
`quote_to_market_snapshot` — son defaut dataclass `False` s'applique a
**toute** donnee live reelle (seuls les fixtures de tests et un champ GUI
manuel l'assignent).

### Par type

| setup_type | Lecture bougie | Fonction | Citation |
|---|---|---|---|
| breakout_retest | Formation implicite via `bullish_confirmation()` (`close > open` du snapshot courant, ou `bullish_candle` — toujours `False` en live, voir ci-dessus) | `bullish_confirmation`, `base_setup.py:169-174`, appelee `breakout_retest.py:82` | idem |
| aggressive_rebound | Idem `bullish_confirmation()` | `aggressive_rebound.py:65` | idem |
| pullback_continuation | Idem `bullish_confirmation()`, combine a `snapshot.price <= snapshot.ema_20` (indicateur, pas bougie) | `pullback_continuation.py:37` | idem |
| range_breakout | **Incoherent en interne** : `close` (meme provenance ambigue) pour l'INVALIDATE (`close < low`, `:28`), mais `snapshot.price` (tick brut, jamais `close`) pour le trigger `ENTRY_READY` (`price > high`, `:34`) — deux granularites differentes dans la meme methode, aucune fonction de confirmation | `range_breakout.py:27-34` |
| momentum_breakout | Le plus explicite : `_bars_above_resistance()` compte les barres consecutives dans `snapshot.historical_bars` (le meme tableau brut que celui dont `close` est extrait, pas une source plus fiable en soi) en iterant `reversed(...)` et en s'arretant au premier `bar["close"] <= resistance` ; `_volume_confirmation()` calcule `current_bar_is_closed` via `elapsed_bar_percent` (`0 < elapsed < 0.999` => pas close) et bascule sur un ratio projete sinon | `momentum_breakout.py:968-983` (`_bars_above_resistance`), `:604-636` (`current_bar_is_closed`) |
| position_management | `_metric_value()` lit `candle_open/high/low/close` directement depuis le snapshot (meme provenance ambigue, aucune distinction formation/close dans le code de ce fichier) | `position_management.py:164-174` | (0 usage production) |
| runner / trailing_runner | Aucune lecture de champ bougie — seul `snapshot.price` est compare aux seuils de `step["when_price_above"]` | `trailing_runner.py:22-26` | N/A |

**Verdict Axe 3** : **impossible — norme non uniformement atteignable sans
decision produit prealable**, memes conclusions que l'audit 05 (3 groupes
distincts : formation via `bullish_confirmation` pour 3 types, close
explicite pour `momentum_breakout`, aucune confirmation pour
`range_breakout`) — avec une nuance nouvelle et plus grave : **la donnee
source (`snapshot.close`) que les 3 types "formation" et `range_breakout`
lisent n'a elle-meme pas de garantie d'etre une barre reellement close**
(provenance hybrid = derniere ligne d'un appel historique dont le caractere
clos depend du comportement serveur IBKR, non du code applicatif). Durcir
"confirmation sur bougie CLOSE" pour les 4 types qui n'ont pas la logique de
`momentum_breakout` exige donc **deux correctifs empiles** : (1) generaliser
`historical_bars`/`elapsed_bar_percent` aux 4 autres types (modification
individuelle de chaque fichier, deja etabli en 05), et (2) fiabiliser en
amont, dans `tws_connector.py`, la garantie que la derniere ligne
`historical_bars` est effectivement close (hors perimetre setups, cote
broker — a verifier separement, cf. INCERTITUDES).

---

## AXE 4 — Coherence config lue vs config declaree

Verification croisee : (a) `evaluate()` de chaque type (ce qui est
reellement lu), (b) `setup_type_registry.py:64-210`
(`SETUP_SPECIFIC_OPTIONS`, le template canonique genere pour chaque type),
(c) les fichiers JSON reels dans `data/setups/` (echantillon +
verification exhaustive pour `aggressive_rebound`). Seuls les champs
**notables ignores** sont signales (pas d'inventaire champ par champ).

| setup_type | Champs config standards presents mais IGNORES par `evaluate()` | Preuve |
|---|---|---|
| range_breakout | `range.breakout_side` (`registry:145`), `range.require_close_outside_range` (`registry:146`, **`true` dans le template et dans les 11/11 setups reels en base**) | `grep -rn "breakout_side\|require_close_outside_range" app/` -> 0 occurrence hors `setup_type_registry.py`. Consequence directe : le champ declare `require_close_outside_range: true` sur 100% des setups `range_breakout` n'est verifie nulle part — le trigger utilise `snapshot.price`, pas `close` (voir Axe 3) |
| momentum_breakout | `breakout.volume_rule_mode` (`registry:68`), `breakout.close_above_resistance_required` (`registry:73`), `breakout.broken_resistance` | `grep` : 0 occurrence hors template/echantillons JSON |
| breakout_retest | `breakout.broken_resistance` (`registry:125`), `retest.confirmation_required`/`retest.confirmation_timeframe` (`registry:131-132`, `true`/`"15m"` dans le template) | 0 occurrence de ces cles hors template — la confirmation est **toujours** appliquee inconditionnellement via `bullish_confirmation()` (`:82`), le booleen `confirmation_required` ne peut donc jamais desactiver ce comportement meme si mis a `false` |
| pullback_continuation | **Le bloc `pullback{}` entier au-dela de `entry_reference`** : `pullback.zone_min`/`zone_max` (`registry:156-157`), `pullback.confirmation_required` (`:158`) — et **`support_zone{}` entier**, qui n'est meme pas declare dans le template pour ce type mais est ajoute de facon ad hoc dans les JSON reels (ex. `CBRL_20260714_001.json`, `support_zone.min/max/invalidation_below`) | `evaluate()` (`:17-47`) n'utilise **que** `snapshot.ema_20`/`snapshot.ema_50`/`snapshot.price` — aucune reference a `pullback.*` ni `support_zone.*`. `pullback.entry_reference` n'est lu que par `estimated_entry_price()` (`:10-15`), jamais appelee par `evaluate()` (confirme audit 05) |
| aggressive_rebound | **`invalidation.close_below` n'existe dans AUCUN template ni AUCUN des 14 setups reels** — le champ reellement declare est `support_zone.invalidation_below` (`registry:165`, present dans 14/14 fichiers reels scannes), jamais lu par le code. `rebound_confirmation.require_bullish_candle`/`require_volume_confirmation`/`confirmation_timeframe` (`registry:167-171`) egalement jamais lus | Voir detail ci-dessous — **la constatation la plus severe de ce lot** |
| position_management / runner / trailing_runner | N/A structurel (moteur de regles generique, champs lus dynamiquement par `metric`/`operator`/`value`) — mais 0 donnee production pour verifier que le format reel des regles correspond au format attendu par `_metric_value`/`_compare` | (0 usage production) |

### Detail — `aggressive_rebound` : le champ d'invalidation configure n'est jamais lu

`aggressive_rebound.py:47-48` :
```python
invalidation = self.config.get("invalidation", {})
close_below = float(invalidation.get("close_below", low))
```
`self.config.get("invalidation", {})` — verification exhaustive sur les 14
fichiers reels (`data/setups/*.json` avec `"setup_type": "aggressive_
rebound"`) :

```python
for f in files: assert "invalidation" not in json.load(open(f))  # vrai pour les 14/14
```

| setup_id | `support_zone.min` (fallback code) | `support_zone.invalidation_below` (config declaree, jamais lue) | ecart |
|---|---|---|---|
| TROX_20260714_001 | 5.95 | 5.80 | 0.15 |
| AAOI_20260708_001 | 112.00 | 108.00 | 4.00 |
| AEHR_20260709_001 | 62.50 | 62.00 | 0.50 |
| AMPX_20260628_001 | 12.10 | 11.80 | 0.30 |
| ARM_20260703_001 | 303.67 | 303.40 | 0.27 |
| EFOR_20260712_001 | 17.00 | 16.80 | 0.20 |
| HIMX_20260703_001 | 12.90 | 12.72 | 0.18 |
| INOD_20260703_001 | 67.10 | 66.80 | 0.30 |
| IREN_20260703_001 | 37.70 | 37.40 | 0.30 |
| JOBY_20260703_001 | 8.30 | 8.18 | 0.12 |
| NVTS_20260703_001 | 14.00 | 13.78 | 0.22 |
| PLAB_20260703_001 | 28.25 | 28.20 | 0.05 |
| QCOM_20260703_001 | 172.20 | 171.20 | 1.00 |
| RKLB_20260713_001 | 76.00 | 75.50 | 0.50 |

**Sur les 14/14 setups `aggressive_rebound` existants, l'ecart est
systematique et toujours dans le meme sens** (`support_zone.min` >
`support_zone.invalidation_below`) : le code invalide **plus tot / plus
haut** que ce que le setup declare vouloir. Le fallback `low`
(=`support_zone.min`) n'est pas un choix delibere documente ailleurs dans le
code — c'est un defaut de parametre Python (`invalidation.get("close_below",
low)`) qui masque silencieusement l'absence totale de la cle
`invalidation`. Le texte de `notes` de plusieurs setups (ex. TROX : "sortir
si cloture 15m sous 5,80") confirme que **5.80 est le niveau reellement
voulu**, pas 5.95. `rebound_confirmation.require_volume_confirmation: true`
est egalement present dans le template et systematiquement absent de toute
lecture de volume dans le fichier (`grep -i volume aggressive_rebound.py` ->
0 occurrence) : le champ ne fait **litteralement rien**.

Le panneau UI "Ce que cherche le setup" (`setup_conditions.py:443-469`,
feature deja en place, cf. memoire projet) n'expose pas non plus le seuil
numerique d'invalidation pour `aggressive_rebound` — `invalidation_map`
(`:468`) ne fait que mapper la raison textuelle `"support invalidation"` a
la condition `price_at_support`, sans jamais afficher la valeur `close_
below`/`invalidation_below` utilisee. **Ce defaut est donc invisible de
bout en bout**, y compris pour un utilisateur qui consulterait le detail du
setup avant armement.

**Verdict Axe 4** : **impossible — norme structurelle par nature specifique
a chaque type**, mais la constatation factuelle est generalisable : **tous
les 5 types actifs en production ont au moins un champ de configuration
"standard" (present dans le template ET dans les JSON reels) qui n'est
jamais lu par `evaluate()`**, avec un cas de gravite nettement superieure
aux autres (`aggressive_rebound`, ou le champ ignore est precisement celui
qui determine le seuil de perte, avec un ecart mesurable et systematique sur
14/14 setups reels).

---

## AXE 5 — Point d'application d'une norme

Rappel audit 05 section C / audit 08 : `signal_engine.evaluate_snapshot`
(`signal_engine.py:63-115`), ligne 81 (`signal = strategy.evaluate(snapshot,
current_status)`), est le **seul** point de dispatch de `.evaluate()` sur le
chemin live, pour les 8 types desormais confirmes (`SetupFactory.create`
ligne 76, dispatch identique quel que soit le type — aucune branche
specifique par type dans `signal_engine.py`).

| Norme candidate | Portable en un point unique (post-ligne 81, sur `SetupSignal` en sortie) ? | Raison |
|---|---|---|
| Axe 1 — gate avant `ENTRY_READY`/`RAISE_STOP`/`STATUS_CHANGE` regressif | **Oui** | Verifiable entierement sur `(current_status, signal.action, signal.target_status)`, sans connaitre la logique interne du type — ex. "si `signal.action in {ENTRY_READY, RAISE_STOP}` et `current_status` hors d'un ensemble autorise pour ce statut+role, retomber sur `HOLD`". Deja la conclusion d'audit 05 pour `ENTRY_READY` seul ; ce lot confirme qu'elle s'etend a `RAISE_STOP` et aux `STATUS_CHANGE` regressifs (`MISSED_BREAKOUT` depuis `WAITING_RETEST`) par le meme motif |
| Axe 2 — INVALIDATE conditionnel au statut | **Oui** | Meme raisonnement : "si `signal.action == INVALIDATE` et `current_status` est un statut post-`ENTRY_ORDER_PLACED`, retomber sur `HOLD` plutot que de tenter une transition qu'on sait deja rejetee" — s'appuie sur `TERMINAL_SIGNAL_STATUSES`/`ALLOWED_TRANSITIONS` deja disponibles, aucune modification des 4 fichiers `evaluate()` concernes n'est necessaire |
| Axe 3 — lecture bougie close uniforme | **Non** | Porte sur la **construction** du `SetupSignal` (quelle donnee `evaluate()` lit pour decider), pas sur sa forme de sortie — exige de modifier le corps de `range_breakout.py`, `aggressive_rebound.py`, `breakout_retest.py`, `pullback_continuation.py` individuellement (remplacement de `bullish_confirmation`/`snapshot.price` par une lecture `historical_bars`), plus un correctif en amont dans `tws_connector.py` pour la garantie de barre close (hors perimetre `app/setups/`) |
| Axe 4 — coherence config lue/declaree | **Non** | Par construction specifique a chaque type (les champs ignores different d'un fichier a l'autre) — correctif obligatoirement fichier par fichier, ex. `aggressive_rebound.py:47-48` doit lire `support_zone.invalidation_below` au lieu de `invalidation.close_below` ; aucun point commun ne peut reconcilier des chemins de config differents entre types |

---

## INCERTITUDES RESIDUELLES

1. **`RECONCILING_EXISTING_POSITION -> INVALIDATED` est une transition
   autorisee** (`state_machine.py:161-168`) — contrairement a tous les
   autres statuts de position. Aucun setup `entry_and_management` (donc
   potentiellement `range_breakout`/`aggressive_rebound`/`pullback_
   continuation`) n'a ete observe en base avec `setup_role=MANAGEMENT_ONLY`
   et `current_status=RECONCILING_EXISTING_POSITION` simultanement sur la
   fenetre disponible (aucune ligne `setup_transition_rejected` ne le
   mentionne, et aucun de ces 3 types n'apparait avec ce statut dans la
   table `setups` actuelle) — mais rien dans `validate()` de ces 3 fichiers
   n'empeche cette combinaison de configuration. Risque theorique non
   exclu, non confirme empiriquement.
2. **Caractere reellement clos de la derniere barre `historical_bars`/
   `quote["close"]` en mode hybrid** (Axe 3) : depend du comportement du
   serveur IBKR au moment de `reqHistoricalDataAsync(keepUpToDate=False)`,
   non verifiable par lecture statique du code applicatif. Le nommage
   interne (`"last_closed_bar.close"`) suggere une hypothese des auteurs,
   pas une garantie prouvee.
3. **`position_management`/`runner`/`trailing_runner`** : analyse
   entierement fondee sur la lecture de code, **zero verification
   empirique possible** (0 occurrence sur 96 344 `stock_analysis`, 0 ligne
   dans `setups`). Si ces types sont utilises a l'avenir, les verdicts
   "conforme" des Axes 1-2 pour ces 3 types n'ont jamais ete eprouves en
   conditions reelles.
4. **`pullback_continuation` et INVALIDATE** : structurellement expose au
   meme defaut que `aggressive_rebound`/`range_breakout` (Axe 2), mais
   n'apparait dans aucun des 4 `setup_id` de l'echantillon empirique
   disponible (956 rejets). Silence de donnees, pas preuve d'absence de
   risque — meme reserve que l'audit 05 pour ce type.

---

## TABLEAU DES NORMES

| # | Norme candidate | Verdict | Types conformes | Types a corriger | Point d'application unique |
|---|---|---|---|---|---|
| 1 | Gate `current_status` avant `ENTRY_READY`/`RAISE_STOP` | Avec durcissement | breakout_retest, aggressive_rebound, pullback_continuation, position_management, runner, trailing_runner (6/8 — 2 derniers non eprouves en prod) | range_breakout (total), momentum_breakout (branche `ENTRY_READY`) | **Oui** — post `signal_engine.py:81` |
| 2 | Gate `current_status` avant `STATUS_CHANGE` regressif (ex. `MISSED_BREAKOUT`) | Avec durcissement | Toutes les branches "progressives" des 8 types | momentum_breakout (branche `MISSED_BREAKOUT`, 9 469 rejets prod) | **Oui** — meme point |
| 3 | INVALIDATE conditionnel au statut (pas post-`ENTRY_ORDER_PLACED`) | Avec durcissement | breakout_retest | aggressive_rebound, pullback_continuation, range_breakout (956 rejets prod, dont 807 sur un seul setup) | **Oui** — meme point |
| 4 | Confirmation sur bougie CLOSE uniforme | Impossible — exception structurelle (3 groupes distincts) + fiabilite de la source elle-meme non garantie | momentum_breakout (le plus proche, a durcir) | breakout_retest, aggressive_rebound, pullback_continuation (bougie en formation), range_breakout (aucune confirmation) | Non — 4 fichiers + 1 correctif broker en amont |
| 5 | Coherence champ config lu = champ config declare | Impossible — par nature specifique a chaque type | Aucun type n'est 100% coherent | Les 5 types actifs (au moins 1 champ notable ignore chacun) ; `aggressive_rebound` en urgence (seuil d'invalidation, 14/14 setups, ecarts 0.05 a 4.00 en valeur absolue) | Non — 1 fichier par type, ex. `aggressive_rebound.py:47-48` |
