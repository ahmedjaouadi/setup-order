# Audit en lecture seule — Lot 12 : `momentum_breakout` (cas reel GEV_20260628_001)

Mode lecture seule stricte. Aucun fichier de code n'a ete modifie — seul ce
fichier a ete cree. Fil conducteur impose : **GEV_20260628_001**
(`data/setups/GEV_20260628_001.json`), 4 361 evenements `stock_analysis` en
base pour le symbole GEV (le plus riche toutes categories confondues,
verifie ci-dessous). Toutes les requetes ont ete executees en lecture seule
(`sqlite3.connect('file:data/trading_state.sqlite?mode=ro', uri=True)`),
filtrees systematiquement par `symbol=` et/ou `event_type=`. Code lu en
entier : `app/setups/momentum_breakout.py` (985 lignes), `app/setups/
base_setup.py`, `app/engine/signal_engine.py`, `app/engine/state_machine.py`,
`app/engine/action_executor.py`, `app/engine/entry_order_executor.py`,
`app/engine/order_manager.py`, `app/engine/risk_engine.py`, `app/engine/
setup_lifecycle_service.py`, `app/storage/repositories.py`, `app/setups/
setup_conditions.py`, `app/setups/setup_type_registry.py`.

Ce lot part des faits deja etablis par `audit/09_normes_transverses.md`
(gate `current_status` absent pour la branche `ENTRY_READY` et pour
`STATUS_CHANGE -> MISSED_BREAKOUT`, 9 469 rejets `WAITING_RETEST ->
MISSED_BREAKOUT` en base, `_bars_above_resistance`/`current_bar_is_closed`
les plus explicites du lot, 3 champs de config ignores) et va plus loin :
disseque `momentum_breakout` seul, avec la meme rigueur que l'audit AVGO sur
`breakout_retest`, sans supposer que ses bugs sont les memes.

---

## 0. Verification prealable — GEV est bien le setup le plus riche

```sql
SELECT COUNT(*) FROM events WHERE event_type='stock_analysis' AND symbol='GEV';
-- -> 4361
SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE event_type='stock_analysis' AND symbol='GEV';
-- -> ('2026-06-04T17:02:37.853382+00:00', '2026-07-17T20:22:55.568582+00:00')
SELECT setup_id, symbol, setup_type, status, entry_zone, stop_loss, status_reason,
       last_revalidated_at, created_at, updated_at
FROM setups WHERE setup_id='GEV_20260628_001';
-- -> ('GEV_20260628_001','GEV','momentum_breakout',1,'paper','WAITING_RETEST','1085.00',
--     1032.0, 15.0, '', '', '', None, '2026-06-28T21:23:56...', '2026-07-17T14:02:29...')
```

**GEV_20260628_001 est actuellement en `WAITING_RETEST`**, statut inchange
depuis le 2026-06-29 (voir section 5) — c'est un fait central de ce lot, pas
une coincidence de calendrier.

---

## 1. ENTREE — prix de trigger reellement transmis au broker

### Ce que la config de GEV declare

```json
"entry": {
  "order_type": "STP_LMT", "trigger_offset": 1.0, "limit_offset": 5.0,
  "trigger_price": 1085.0, "entry_price": 1085.0, "limit_price": 1090.0,
  "maximum_limit_price": 1090.0, "cancel_if_not_filled_after_minutes": 30
},
"breakout": { "resistance": 1084.0, ... }
```

Les `notes` du setup sont explicites : *"Entree STP_LMT: trigger 1085.00,
entry_price 1085.00, limite maximale 1090.00. Ne pas poursuivre si le prix
depasse 1090.00 sans execution."*

### Ce que le code calcule reellement

`_analyze_long` (`momentum_breakout.py:38-302`) lit `entry_config =
_mapping(self.config.get("entry"))` (`:44`) mais **ne lit jamais**
`entry_config.get("trigger_price")`, `entry_config.get("entry_price")` ni
`entry_config.get("trigger_offset")` — la seule utilisation de
`entry_config` dans toute la methode est a la ligne 124-127, pour **plafonner**
(`min(...)`) le `maximum_limit_price` calcule :

```python
entry_trigger = _round_up_to_tick(
    resistance + offsets["trigger_offset"], market["minimum_tick"],
)                                                          # :116-119
maximum_limit_price = _round_up_to_tick(
    entry_trigger + offsets["limit_offset"], market["minimum_tick"],
)                                                          # :120-123
configured_limit = _first_number(
    entry_config.get("maximum_limit_price"), entry_config.get("limit_price"),
)
if configured_limit is not None:
    maximum_limit_price = min(maximum_limit_price, configured_limit)   # :124-129
```

`offsets["trigger_offset"]`/`offsets["limit_offset"]` viennent de
`_dynamic_offsets()` (`:409-434`), qui les calcule **entierement a partir de
donnees de marche live** (`minimum_tick`, `spread`, `atr_15m` du snapshot
courant) — `raw_trigger = max(2*tick, spread, 0.05*atr_15m)`,
`raw_limit = max(3*tick, 2*spread, 0.10*atr_15m)` — sans aucune reference a
`entry.trigger_offset` (1.0 dans le JSON) ni `entry.trigger_price` (1085.0).
`entry_trigger` devient ensuite `signal.entry_price` (`:299`), c'est-a-dire
le prix qui, via `risk_engine.RiskEngine.evaluate` (`risk_engine.py:71,
218-222`) puis `OrderManager._entry_order_prices` (`order_manager.py:
207-237`), devient `trigger_price` de l'ordre `STP_LMT` reellement transmis
au broker (`order_manager.py:100-115`).

### Preuve empirique sur GEV — le trigger derive tick a tick

```python
# extraction data_json de events id=1059189 (2026-06-29T13:57:18)
```

| champ | valeur declaree (JSON) | valeur calculee (moteur, id 1059189) |
|---|---|---|
| resistance | 1084.0 | 1084.0 (lu correctement, `breakout.resistance`) |
| trigger_offset | 1.0 | **1.4** (`raw_trigger_offset`, derive de `spread=1.40`) |
| trigger_price / entry_price | 1085.0 | **1085.4** (`entry_trigger`) |
| limit_offset | 5.0 | **2.8** (`raw_limit_offset`, derive de `2*spread=2.8`) |
| maximum_limit_price / limit_price | 1090.0 | **1088.2** (`min(1085.4+2.8, 1090.0)`) |

A un autre tick (id 1078257, 2026-06-29T14:56:14), le meme calcul donne
`trigger=1085.89`, `maximum_limit_price=1089.67` — une valeur **differente**
de celle du tick precedent, alors que `breakout.resistance` (1084.0) est
fixe : le trigger et la limite bougent a chaque evaluation avec le spread et
l'ATR 15m courants, jamais avec les valeurs figees du JSON. Le champ
`entry.trigger_offset: 1.0` de GEV n'apparait dans **aucune** sortie —
c'est un nombre uniquement decoratif dans le fichier de config.

**Verdict section 1** : le prix de declenchement transmis au broker n'est
**pas** celui exprime par la config (`entry.trigger_price`/`trigger_offset`),
mais un recalcul dynamique base sur `resistance + f(tick, spread, atr_15m)`
qui **derive a chaque tick**. `entry.maximum_limit_price`/`limit_price` ne
sont pas non plus le prix cible — ils ne servent qu'a **plafonner** un calcul
dynamique independant, jamais utilises comme valeur directe. C'est le meme
type de defaut de fond que celui documente sur `breakout_retest`/AVGO
(trigger calcule au lieu de trigger configure), mais avec un mecanisme
different (offsets dynamiques bases sur ATR/spread, pas simplement
`high + offset`) — et une consequence supplementaire propre a ce type : le
trigger n'est **pas stable** d'un tick a l'autre, contrairement a
`breakout_retest` ou le trigger calcule est au moins constant tant que
`resistance` ne change pas.

---

## 2. CONFIRMATION — bougie en formation ou close, oscillation intrabar

`_entry_validation` (`:461-548`) construit trois chemins de validation
(`FAST_BREAKOUT`, `CONFIRMED_BREAKOUT`, `BREAKOUT_RETEST`) a partir de
`_bars_above_resistance()` (`:968-984`) et `_volume_confirmation()`
(`:577-712`, `current_bar_is_closed` via `elapsed_bar_percent`, `:604-607`)
— le mecanisme le plus explicite des 8 types (confirme audit 09 Axe 3).

### `historical_bars` n'est jamais present dans les donnees persistees

```python
# scan des 4 361 stock_analysis GEV : 'historical_bars' in data['snapshot'] ?
# -> has_hist_bars=0, no_hist_bars=4361 (0/4361)
```

`_bars_above_resistance()` a deux branches (`:972-984`) : (a) retourner
`snapshot.bars_above_resistance` si ce scalaire precalcule est present, (b)
sinon iterer `snapshot.historical_bars` (tableau brut). Sur les 4 361
evenements GEV, **le tableau brut `historical_bars` n'apparait jamais** dans
le snapshot journalise — mais le scalaire `bars_above_resistance` **est**
present et varie reellement :

```sql
-- Counter sur analysis.validation.bars_above_resistance (GEV)
-- {0: 411, 3: 19, 2: 18, 1: 13, 4: 13, 5: 8}
```

Ceci confirme que c'est **toujours la branche (a)** (scalaire precalcule en
amont, hors de `app/setups/`, dans la couche broker/marche) qui est utilisee
en production — la branche (b) qui itere `historical_bars` localement est du
code mort pour GEV (et vraisemblablement pour tout setup en mode hybrid,
cf. audit 09 Axe 3 sur la provenance de `MarketSnapshot`). Consequence :
**le compte de barres au-dessus de la resistance n'est pas auditable** a
partir des donnees persistees — seul le resultat scalaire est visible, sa
methode de calcul reelle est hors du perimetre de ce fichier (signale en
INCERTITUDES).

### Oscillation reelle du scalaire — preuve empirique directe sur GEV

```sql
-- events id 1079715 et 1079842 (22 secondes d'ecart, meme session)
```

| id | timestamp | `bars_above_resistance` | `validation.path` | `validation.valid` | decision_status |
|---|---|---|---|---|---|
| 1079715 | 2026-06-29T15:00:58.010 | **2** | `CONFIRMED_BREAKOUT` | **True** | PAUSED_MISSING_MARKET_DATA (bloque par le stop, voir section 3) |
| 1079842 | 2026-06-29T15:01:20.367 | **0** | `""` (vide) | **False** | WAITING_CONFIRMATION |

En 22 secondes (un seul cycle d'evaluation, pas 15 minutes), le compteur
passe de 2 barres consecutives au-dessus de la resistance a **0** — une
remise a zero complete, pas une decroissance progressive. Que cela vienne
d'un revirement de prix reel ou d'une instabilite de la donnee amont
(non verifiable, cf. INCERTITUDES), le resultat mesure est le meme : le
verdict `CONFIRMED_BREAKOUT` de `momentum_breakout` **oscille reellement
intrabar** sur donnees de production — malgre le mecanisme `hold_bars`
(`confirmed_breakout_hold_bars: 2` dans la config GEV) explicitement concu
pour filtrer ce bruit.

**Verdict section 2** : confirmation basee sur une donnee nominalement
"close" (le nommage et le filtre `current_bar_is_closed` visent une bougie
cloturee), mais (a) le tableau source n'est jamais audite dans les donnees
reelles disponibles, (b) le scalaire qui en tient lieu **n'est pas stable**
tick a tick sur un exemple reel documente, ce qui contredit l'intention
"tenue 2 barres" du texte `notes` de GEV.

---

## 3. STOP — quel stop part reellement au broker

### Deux stops distincts dans la config, un seul reellement transmis

GEV declare **deux stops statiques** : `trailing_stop_loss.initial_stop:
1032.0` (le stop "officiel", lu par `BaseSetup.stop_loss`,
`base_setup.py:54-58`, utilise pour `validate()` et l'affichage) et un
sous-bloc `management.stop_management.steps` avec 2 regles de remontee
(`two_15m_closes_above -> 1095.0`, `new_higher_low_confirmed ->
raise_stop_below_confirmed_higher_low` avec `buffer: 4.0`).

`_initial_stop()` (`:800-841`) **ignore les deux** : il calcule un stop a
partir de champs de snapshot vivants (`last_confirmed_higher_low`,
`support_level`, `successful_retest_low`, `structural_support`, ou leurs
equivalents dans `risk.*`), filtre `< entry_trigger`, prend le max
(`structural_support`), puis soustrait un buffer :

```python
stop_buffer = max(
    2 * market["minimum_tick"], 2 * market["spread"], 0.20 * market["atr_1h"],
)                                                              # :826-830
```

Le `0.20` est **code en dur** — la config declare pourtant
`trailing_stop_loss.calculation.structure.atr_fraction_buffer: 0.1` (JSON
`:139`), jamais lu par ce fichier (0 occurrence de `atr_fraction_buffer`
dans `momentum_breakout.py`) : le buffer reellement applique est **2x** ce
que la config exprime.

### Le stop calcule ecrase le stop configure avant transmission

`metadata["trailing_stop_overrides"] = {"initial_stop": stop["initial_stop"]}`
(`:294`) est consomme par `EntryOrderExecutor._setup_with_signal_overrides`
(`entry_order_executor.py:352-388`), qui **remplace**
`config["trailing_stop_loss"]["initial_stop"]` par la valeur calculee avant
que `_trailing_initial_stop()` (`entry_order_executor.py:397-403`,
`order_manager.py:84`) ne le relise pour construire l'ordre stop reel — donc
c'est bien le stop **calcule dynamiquement**, pas le `1032.0` du JSON, qui
finirait au broker.

### Preuve empirique — entree valide silencieusement bloquee faute de stop

```python
# 7 evenements GEV ou analysis.missing_conditions contient
# 'structural_support_below_entry' (2026-06-29, 14:25 - 15:00)
```

| id | timestamp | `validation.path` | `validation.valid` | volume ratio | decision_status final |
|---|---|---|---|---|---|
| 1067966 | 14:27:31 | CONFIRMED_BREAKOUT | **True** | 1.17 (VOLUME_CONFIRMED) | PAUSED_MISSING_MARKET_DATA |
| 1068217 | 14:28:15 | CONFIRMED_BREAKOUT | **True** | 1.17 | PAUSED_MISSING_MARKET_DATA |
| 1078049 | 14:55:38 | CONFIRMED_BREAKOUT | **True** | 0.94 | PAUSED_MISSING_MARKET_DATA |
| 1078257 | 14:56:14 | CONFIRMED_BREAKOUT | **True** | 1.01 | PAUSED_MISSING_MARKET_DATA |
| 1078499 | 14:56:49 | CONFIRMED_BREAKOUT | **True** | 1.01 | PAUSED_MISSING_MARKET_DATA |
| 1079715 | 15:00:58 | CONFIRMED_BREAKOUT | **True** | 1.17 | PAUSED_MISSING_MARKET_DATA |

Sur ces 6 evaluations (`WAITING_RETEST`, apres la premiere a 14:25:48 encore
en `WAITING_ACTIVATION`), le breakout est **valide** au sens du moteur
(`bars_above_resistance` 2-3, volume confirme jusqu'a 1.17x, close au-dessus
de la resistance) — mais l'entree est bloquee car `_initial_stop()` ne
trouve **aucun support structurel** (`last_confirmed_higher_low`,
`support_level`, etc.) sous le trigger courant (~1085-1090) :
`eligible_supports = []` -> `missing=["structural_support_below_entry"]`
(`:822-824`). Le stop statique **1032.0** declare dans le JSON — largement
sous ce trigger, donc parfaitement eligible en theorie — n'est jamais
utilise comme filet de secours : le code ne connait que les champs de
snapshot live, jamais `trailing_stop_loss.initial_stop`. **Un breakout
confirme avec volume a 1.17x a ete silencieusement abandonne pendant au
moins 35 minutes** faute d'un fallback vers le stop configure.

### Pas de gestion de stop apres entree (RAISE_STOP)

`momentum_breakout.py` ne contient **aucune** occurrence de `RAISE_STOP`,
`IN_POSITION` ni `MANAGING_POSITION` (grep exhaustif sur le fichier, 0
resultat). Le dispatcher `_handle_signal` (`trading_engine.py:2463-2470`)
essaie dans l'ordre `execute_simple_action` (HOLD/INVALIDATE/STATUS_CHANGE),
`position_action_executor.execute_raise_stop_signal` (`SignalAction.
RAISE_STOP`), puis `entry_order_executor.execute_entry_ready` — mais comme
`evaluate()` de ce type n'emet jamais `RAISE_STOP`, la branche
`PositionActionExecutor` n'est **jamais** activee pour un setup
`momentum_breakout`, quel que soit son statut. Consequence directe : toute
la mecanique `management.stop_management.steps` /
`trailing_stop_loss.ratchet_rules` de GEV (2 regles de remontee de stop,
`break_even_policy`, `min_improvement_atr_fraction`) **ne sera jamais
executee par ce setup une fois en position** — le seul stop que ce type
produit est le stop initial, une fois, au moment de l'entree
(`stop_loss=stop["initial_stop"]`, `:300`).

**Verdict section 3** : le stop transmis au broker est calcule
independamment du signal *et* de la config, avec un buffer ATR code en dur
qui diverge de la valeur declaree (0.20 vs 0.1) ; il n'a aucun filet de
secours vers le stop statique configure quand la donnee structurelle live
manque (preuve empirique : entree valide perdue) ; et une fois en position,
`momentum_breakout` n'a **aucun** mecanisme pour faire remonter ce stop
malgre une configuration tres detaillee prevue a cet effet.

---

## 4. MEMOIRE / SEQUENCE

### Sequence reellement emise par le code (exhaustif)

```
grep -n "target_status=" app/setups/momentum_breakout.py
```

Trois cibles seulement, sur tout le fichier :
`SetupStatus.MISSED_BREAKOUT` (`:173`), `SetupStatus.WAITING_RETEST`
(`:190`), `SetupStatus.ENTRY_READY` (`:298`). Aucune trace de
`WAITING_CONFIRMATION`, `REARMED_ON_NEW_BASE` ni `WAITING_ENTRY_SIGNAL`.

### `bars_above_resistance` : pas de memoire propre au setup

Comme etabli section 2, ce compteur n'est **pas** un etat persiste par le
setup lui-meme (pas de champ stocke en base entre deux ticks) — c'est un
scalaire recalcule a chaque evaluation par la couche marche en amont, sur
une fenetre non auditable depuis les donnees disponibles, et
empiriquement instable (2 -> 0 en 22 secondes sur GEV). Il n'y a donc
**aucune memoire de sequence** pour la progression du breakout — chaque
tick repart d'une fenetre recalculee.

### Le champ `rearm` — declare, jamais lu, et c'est la cle du blocage

```
grep -c "rearm" app/setups/momentum_breakout.py
-> 0
```

GEV declare pourtant un bloc `rearm` complet et exploitable :

```json
"rearm": { "new_local_resistance": 1084.0, "new_trigger": 1085.0, "new_limit": 1090.0 }
```

Ce bloc est un champ **standard** du template
(`setup_type_registry.py:99-103`, `SETUP_SPECIFIC_OPTIONS["momentum_
breakout"]["rearm"]`), pas ad hoc. C'est aussi le seul mecanisme de config
qui correspondrait semantiquement a `SetupStatus.REARMED_ON_NEW_BASE`, une
cible explicitement autorisee par la state machine depuis `WAITING_RETEST`
(`state_machine.py:89-97`, ensemble `{WAITING_CONFIRMATION,
REARMED_ON_NEW_BASE, WAITING_ENTRY_SIGNAL, EXPIRED, INVALIDATED, CANCELLED,
ERROR}`). Or `momentum_breakout.py` **n'emet jamais**
`target_status=SetupStatus.REARMED_ON_NEW_BASE`
(0 occurrence de la chaine `REARMED` dans le fichier, confirme par lecture
complete) : la seule sortie non-terminale que la state machine autorise
depuis `WAITING_RETEST` et que ce type pourrait theoriquement utiliser pour
sortir de l'impasse **n'est jamais produite par le code**. Voir section 5
pour la consequence complete.

### `management.stop_management.steps` — jamais lu non plus

`grep -c "stop_management" app/setups/momentum_breakout.py` -> 0. Le bloc
`steps` de GEV (`condition: "two_15m_closes_above"`,
`"new_higher_low_confirmed"`) n'est lu par aucun code de ce fichier (il
n'est meme pas cense l'etre puisque ce type n'emet jamais `RAISE_STOP`,
section 3).

**Verdict section 4** : la sequence
`WAITING_ACTIVATION -> MISSED_BREAKOUT -> WAITING_RETEST -> [...]` n'a
**aucune memoire d'etat interne au setup au-dela du statut persiste
lui-meme** — chaque tick recalcule tout depuis la resistance fixe et un
snapshot marche instantane. Le seul etat "memorise" est le `current_status`
en base, mais ce statut ne peut mener nulle part une fois `WAITING_RETEST`
atteint (section 5) car le code ne sait pas produire les transitions que la
state machine autoriserait, notamment `REARMED_ON_NEW_BASE` malgre un champ
de config (`rearm.*`) explicitement prevu pour ca.

---

## 5. INVALIDATION / SORTIE — l'impasse `WAITING_RETEST`

Confirme par audit 09 (Axe 2) : `momentum_breakout` n'emet jamais
`SignalAction.INVALIDATE`. Ce lot etablit **comment il sort reellement** (ou
plutot, n'en sort pas).

### La state machine n'autorise `WAITING_RETEST` a aller que vers des cibles jamais emises

```
state_machine.py:89-97
SetupStatus.WAITING_RETEST: {
    WAITING_CONFIRMATION, REARMED_ON_NEW_BASE, WAITING_ENTRY_SIGNAL,
    EXPIRED, INVALIDATED, CANCELLED, ERROR,
}
```

`momentum_breakout.py` ne cible jamais aucune de ces 7 valeurs depuis
`WAITING_RETEST` — il ne fait que re-emettre `STATUS_CHANGE ->
MISSED_BREAKOUT` (rejete a 100%, deja documente audit 09) ou, si la
validation d'entree redevient positive, `ENTRY_READY`.

### `ENTRY_READY` ne passe jamais par la state machine — decouverte cle de ce lot

`ActionExecutor.execute_simple_action` (`action_executor.py:25-39`) ne
traite que `HOLD`, `INVALIDATE`, `STATUS_CHANGE` — pas `ENTRY_READY`. Le
dispatcher (`trading_engine.py:2463-2470`) route donc `ENTRY_READY` vers
`EntryOrderExecutor.execute_entry_ready`, qui **ne verifie jamais
`current_status`** et, en cas de succes, ecrit directement
`SetupStatus.ENTRY_ORDER_PLACED` via
`self.repository.update_setup_status(...)` (`order_manager.py:180-184`) —
un **appel direct au repository, jamais `state_machine.transition()`**.
Consequence : la transition `WAITING_RETEST -> ENTRY_READY`, que la state
machine rejetterait si elle etait testee (`ENTRY_READY` n'est pas dans
l'ensemble autorise depuis `WAITING_RETEST` ci-dessus), **n'est jamais
testee du tout** — le pipeline d'entree contourne entierement le graphe
`ALLOWED_TRANSITIONS` pour ce saut precis. Cela signifie concretement que si
`_entry_validation` redevient valide pendant que le setup est en
`WAITING_RETEST` (ce qui arrive, section 3 : 6 des 7 occurrences documentees
sont exactement dans ce cas), **rien dans le code n'empeche** l'ordre d'etre
soumis — le statut `WAITING_RETEST` n'est pas une porte fermee pour l'entree,
seulement pour les transitions de statut explicites.

### Aucune gouvernance de cycle de vie sur `WAITING_RETEST`/`MISSED_BREAKOUT`

`setup_lifecycle_service.LIFECYCLE_MANAGED_STATUSES`
(`setup_lifecycle_service.py:33-41`) ne contient que
`WAITING_ACTIVATION`, `BLOCKED`, `STALE_SETUP`,
`MISSED_BREAKOUT_WAIT_RETEST`, `RECONCILING_EXISTING_POSITION`. Cette liste
contient **`MISSED_BREAKOUT_WAIT_RETEST`** — un statut de l'enum
(`models.py`) distinct de `MISSED_BREAKOUT` et de `WAITING_RETEST`, les deux
seuls que `momentum_breakout.py` produit reellement. Verification :

```
grep -n "MISSED_BREAKOUT_WAIT_RETEST" app/setups/momentum_breakout.py
-> 0 occurrence
```

`momentum_breakout.py` ne produit **jamais** le statut que le service de
cycle de vie est cense gouverner pour ce cas d'usage — il produit un statut
au nom presque identique mais different (`MISSED_BREAKOUT` sans suffixe,
puis `WAITING_RETEST`). Consequence : `SignalEngine._revalidate_lifecycle`
(`signal_engine.py:117-135`) **ignore** tout setup `momentum_breakout` en
`WAITING_RETEST` ou `MISSED_BREAKOUT` (`str(setup.get("status")) not in
LIFECYCLE_MANAGED_STATUSES` -> retour inchange, `:124-125`) : ni expiration
par age, ni detection de staleness, ni retour automatique en
`WAITING_ACTIVATION` ne s'appliquent jamais a ces deux statuts pour ce type.

### Preuve empirique complete sur GEV — l'impasse en chiffres

```sql
SELECT COUNT(*) FROM events
WHERE event_type='setup_transition_rejected' AND setup_id='GEV_20260628_001';
-- -> 697
SELECT message, COUNT(*) FROM events
WHERE event_type='setup_transition_rejected' AND setup_id='GEV_20260628_001'
GROUP BY message;
-- -> ('Invalid setup transition: WAITING_RETEST -> MISSED_BREAKOUT', 697)
SELECT MIN(timestamp), MAX(timestamp) FROM events
WHERE event_type='setup_transition_rejected' AND setup_id='GEV_20260628_001';
-- -> ('2026-06-29T14:49:04...', '2026-07-10T17:53:57...')
```

Chronologie GEV reconstituee :

1. `2026-06-28T21:23:56` — setup cree, statut initial `WAITING_ACTIVATION`.
2. `2026-06-29T13:57:18` — premier `STATUS_CHANGE -> MISSED_BREAKOUT`
   (ask 1094.16, largement au-dessus de `maximum_limit_price` 1088.20 +
   buffer) : GEV a ouvert en gap tres au-dessus de la resistance, le
   breakout est manque avant meme la premiere chance d'entree.
3. `13:57:18` -> `14:19:18` — 19 confirmations `MISSED_BREAKOUT` supplementaires
   pendant que le prix reste au-dessus de la limite (transitions triviales
   `MISSED_BREAKOUT -> MISSED_BREAKOUT`, autorisees car statut identique).
4. `14:26:24` — `"Missed breakout retest zone reached"` : transition **legitime**
   `MISSED_BREAKOUT -> WAITING_RETEST` (autorisee par la state machine).
5. `14:25:48` -> `15:00:58` — pendant que le statut est `WAITING_RETEST`, le
   breakout se **reconfirme** 6 fois (`CONFIRMED_BREAKOUT`, volume jusqu'a
   1.17x) mais reste bloque par l'absence de support structurel pour le stop
   (section 3) — pas par un gate de statut, puisqu'il n'y en a pas.
6. `15:01:20` — le compteur de barres retombe a 0 (section 2), la fenetre de
   confirmation se referme.
7. `14:49:04` (2026-06-29) -> `17:53:57` (2026-07-10) — **697 tentatives
   rejetees** `WAITING_RETEST -> MISSED_BREAKOUT`, une fois par tick pendant
   que l'ask reste hors zone, sur **11 jours**.
8. `2026-07-17T14:02:29` (dernier `updated_at` en base) — le setup est
   **toujours** `WAITING_RETEST`, **trois semaines** apres son entree dans
   cet etat, sans aucune gouvernance de cycle de vie pour l'en sortir
   automatiquement.

**GEV a genere a lui seul 697 des 9 469 rejets `WAITING_RETEST ->
MISSED_BREAKOUT` documentes par l'audit 09 (7,4 % du total sur toute la
plateforme, tous types confondus, sur un seul setup)** :

```sql
SELECT status, COUNT(*) FROM setups WHERE setup_type='momentum_breakout' GROUP BY status;
-- ('INVALIDATED', 13) ('MISSED_BREAKOUT_WAIT_RETEST', 2) ('STALE_SETUP', 1)
-- ('WAITING_ACTIVATION', 4) ('WAITING_RETEST', 9)
```

**9 des 16 setups `momentum_breakout` non termines actuellement en base
(56 %) sont dans ce meme piege `WAITING_RETEST`** — GEV n'est pas un cas
isole, c'est le mode de defaillance dominant du type le plus actif de la
plateforme. (Note : les 2 `MISSED_BREAKOUT_WAIT_RETEST` visibles ici sont un
statut different, gere par le lifecycle service — pas la meme impasse,
possiblement le meme setup ayant transite differemment ou un artefact d'un
autre mecanisme ; non investigue plus avant, cf. INCERTITUDES.)

**Verdict section 5** : `momentum_breakout` ne "sort" de `WAITING_RETEST`
que par (a) une nouvelle entree valide qui **contourne** completement la
state machine (section ci-dessus), ou (b) une intervention manuelle — jamais
par une transition de statut normale, jamais par expiration automatique. Ce
n'est pas juste "pas de gate" (deja documente audit 09) : c'est une **fuite
de gouvernance a deux niveaux** — le graphe `ALLOWED_TRANSITIONS` est
correctement restrictif pour `WAITING_RETEST`, mais (1) le code ne produit
jamais les cibles qu'il autoriserait (`REARMED_ON_NEW_BASE` notamment,
malgre `rearm.*` en config) et (2) le service de cycle de vie qui pourrait
compenser en expirant le setup ne reconnait pas les statuts que ce type
produit reellement.

---

## 6. GATE `current_status` — confirmation chiffree sur GEV

Audit 09 avait etabli : pas de gate pour `ENTRY_READY` (`:279-302`), pas de
gate pour `STATUS_CHANGE -> MISSED_BREAKOUT` (`:155-175`, 9 469 rejets
plateforme), gate present seulement pour `STATUS_CHANGE -> WAITING_RETEST`
(`:179`, teste `current_status == MISSED_BREAKOUT`).

**GEV fait bien partie des setups touches**, et de maniere disproportionnee :

| Metrique | Plateforme (audit 09) | GEV seul | Part de GEV |
|---|---|---|---|
| Rejets `WAITING_RETEST -> MISSED_BREAKOUT` | 9 469 | 697 | **7,4 %** |
| Fenetre des rejets | 2026-05-31 -> 2026-07-17 (globale) | 2026-06-29T14:49 -> 2026-07-10T17:53 | ~11 jours continus |
| `setup_transition_rejected` total pour GEV | - | 697 (100 % de meme cause) | - |

Le gate manquant pour `ENTRY_READY` (`:279-302`) est confirme sur GEV avec
une preuve nouvelle et concrete par rapport a l'audit 09 : les 6 evaluations
`CONFIRMED_BREAKOUT` valides pendant `WAITING_RETEST` (section 3) auraient
**pu** produire un ordre reel sans jamais tester `current_status ==
WAITING_RETEST` — seul un manque de donnee de stop (section 3), sans rapport
avec le gate de statut, les a empechees. Le gate manquant n'est donc pas
seulement une source de gaspillage de cycles (comme le montre le compteur de
697 rejets) : sur GEV, il a laisse la porte ouverte a une entree en position
depuis un statut que l'intention du setup (le texte `notes` : *"Retest
valide si breakout manque puis retour... avec reprise haussiere"*) ne
prevoyait manifestement pas comme un point d'entree "normal" — sans jamais
que cela se produise reellement, faute de stop calculable ce jour-la.

---

## 7. CHAMPS CONFIG IGNORES — valeurs concretes de GEV

| Champ (valeur GEV) | Lu par `evaluate()` ? | Ce qui est utilise a la place |
|---|---|---|
| `breakout.volume_rule_mode: "FLEXIBLE_CONFIRMATION"` | Non (`registry:68`, 0 occurrence dans le fichier) | Toujours les 3 chemins `FAST_BREAKOUT`/`CONFIRMED_BREAKOUT`/`BREAKOUT_RETEST` en dur, aucun mode alternatif possible |
| `breakout.close_above_resistance_required: true` | Non (`registry:73`, 0 occurrence) | `close_above_resistance = close > resistance` toujours calcule et toujours exige (`:644`) — le booleen ne peut donc jamais rien desactiver, meme s'il valait `false` |
| `breakout.broken_resistance: null` | Non (0 occurrence) | Aucun equivalent — champ vide dans GEV, jamais consomme de toute facon |
| `management.stop_management.steps` (2 regles concretes : `1095.0`/`raise_stop`, `new_higher_low_confirmed`/`buffer 4.0`) | Non (0 occurrence de `stop_management`) | Rien — `momentum_breakout` n'emet jamais `RAISE_STOP` (section 3) |
| `rearm.new_local_resistance/new_trigger/new_limit` (1084.0/1085.0/1090.0) | Non (0 occurrence de `rearm`) | Rien — c'est la cause directe de l'impasse `WAITING_RETEST` (section 4-5) |
| `trailing_stop_loss.calculation.structure.atr_fraction_buffer: 0.1` | Non | `0.20` code en dur (`:829`) — buffer reel 2x plus large que declare |
| `trailing_stop_loss.initial_stop: 1032.0` | Seulement par `BaseSetup.stop_loss` (`validate()`), **pas** par `_initial_stop()` | Stop calcule depuis les supports structurels live, sans fallback vers 1032.0 (section 3) |
| `entry.trigger_price/trigger_offset: 1085.0/1.0` | Non (section 1) | Recalcul dynamique `resistance + f(spread, atr_15m)` |
| `entry.cancel_if_not_filled_after_minutes: 30` | Non (grep sur `app/`, uniquement present dans le template UI/texte, jamais dans le moteur d'execution) | Aucune expiration automatique de l'ordre a 30 min — la seule "expiration" est le buffer de staleness (section 1), un mecanisme different |

**Verdict section 7** : sur les 8 sections de config specifiques a
`momentum_breakout` dans le JSON de GEV
(`entry`, `risk`, `management`, `breakout`, `volume_confirmation`,
`missed_breakout`, `rearm`, `trend_filter`), **seules `risk.*` et
`missed_breakout.retest_zone_min/max` sont integralement lues et utilisees
telles quelles** ; `trend_filter.required_trend: "uptrend"` n'est egalement
jamais teste (0 occurrence de `trend_filter` dans le fichier). C'est un
ecart plus large que celui deja documente par l'audit 09 (qui ne citait que
3 champs) — `rearm` en particulier n'avait pas ete releve et s'avere etre la
piece manquante la plus consequente (section 4-5).

---

## 8. SIMULATION — deux scenarios reels sur GEV

### Scenario nominal — breakout confirme proprement, verdict silencieusement perdu

Deroule complet, donnees reelles (`id` 1067966 a 1079715, 2026-06-29
14:27 -> 15:00) :

- Contexte : setup en `WAITING_RETEST` depuis 14:26:24 (juste apres le
  premier `MISSED_BREAKOUT` du jour).
- 14:27:31 : `close=` au-dessus de resistance 1084, `bars_above_resistance=3`,
  volume ratio 1.17 (`VOLUME_CONFIRMED`), `validation.path=CONFIRMED_BREAKOUT`,
  `validation.valid=True`. C'est exactement le scenario que le setup
  cherche selon ses `notes` (*"casse 1084.00 avec cloture 15m au-dessus"*)
  et sa config (`confirmed_breakout_hold_bars: 2`, `confirmed_breakout_
  volume_ratio_min: 0.8` — 3 barres et 1.17x satisfont largement ces deux
  seuils).
- Verdict moteur : **`HOLD`**, `decision_status=PAUSED_MISSING_MARKET_DATA`,
  a cause de `structural_support_below_entry` manquant pour `_initial_stop()`
  (section 3) — un blocage **totalement independant** de la qualite du
  signal de breakout lui-meme.
- Ce meme pattern se repete 5 fois de plus jusqu'a 15:00:58 (volume 0.94 a
  1.17x, bars_above 2-3), puis disparait (15:01:20, bars_above retombe a 0).

**Ecart avec l'intention du setup** : le verdict du moteur (`HOLD`) ne
correspond pas a l'intention affichee — la config et les `notes` decrivent
precisement ce signal comme le declencheur d'entree, et le moteur l'a
correctement identifie comme tel (`valid=True`, chemin nomme
`CONFIRMED_BREAKOUT`) avant de l'annuler pour une raison sans rapport
(absence de donnee de support structurel, jamais compensee par le stop
statique `1032.0` pourtant declare dans le meme fichier).

### Scenario limite — missed breakout puis retest, oscillation, impasse permanente

Deroule complet (2026-06-29 13:57 -> aujourd'hui) :

- 13:57:18 : gap d'ouverture, ask 1094.16 tres au-dessus de la limite
  maximale calculee (1088.20) -> `MISSED_BREAKOUT` immediat, avant toute
  chance d'entree.
- 14:26:24 : prix revenu dans la zone de retest (1073.00-1084.00 configuree)
  -> transition legitime vers `WAITING_RETEST`.
- 14:27-15:00 : reconfirmation reelle du breakout (scenario nominal
  ci-dessus), perdue pour une raison sans rapport avec le retest.
- 15:01 et apres : oscillation du compteur de barres, retour a un etat non
  valide.
- A partir de 14:49:04 et jusqu'au 2026-07-10T17:53:57 (11 jours) : 697
  tentatives `WAITING_RETEST -> MISSED_BREAKOUT` rejetees en boucle, un
  cycle complet de calcul + ecriture d'evenement gaspille a chaque tick.
- Depuis, jusqu'a la derniere mise a jour connue (2026-07-17T14:02:29,
  soit ~3 semaines apres le debut de l'impasse) : le setup est toujours
  `WAITING_RETEST`, sans mecanisme de sortie automatique (section 5).

**Ecart avec l'intention du setup** : les `notes` de GEV decrivent un cycle
missed-breakout/retest borne dans le temps ("*retour dans 1073.00-1084.00
avec reprise haussiere*"), implicitement une fenetre de quelques heures a
quelques jours de marche. Le comportement reel observe est une **impasse
permanente** : le setup n'a ni expire, ni ete invalide, ni retente son
entree avec succes depuis trois semaines — un ecart total entre
l'intention (retest borne) et le resultat (parking indefini).

---

## LISTE DES PROBLEMES PROPRES A `momentum_breakout`

### P1 — Le trigger transmis au broker n'est jamais celui de la config, et il derive tick a tick

**Preuve** : `momentum_breakout.py:44,116-129` — `entry_config.get(
"trigger_price"/"trigger_offset")` jamais lu ; `entry_trigger = resistance +
offsets["trigger_offset"]` avec `offsets` recalcule a chaque tick depuis
`spread`/`atr_15m`/`tick` courants (`_dynamic_offsets`, `:409-434`). GEV :
config `trigger_offset=1.0` -> trigger declare 1085.0 ; calcule reellement
1085.40 a 13:57:18 (id 1059189, `raw_trigger_offset=1.4`), 1085.89 a
14:56:14, 1085.72 a 14:56:49 (memes 5 minutes, valeurs differentes). **Impact** :
le prix d'entree reellement soumis au broker est imprevisible depuis le
JSON du setup, et non reproductible d'un tick a l'autre meme a resistance
fixe.

### P2 — Le stop transmis n'a aucun fallback vers le stop configure, et un buffer code en dur diverge de la config

**Preuve** : `_initial_stop()` (`:800-841`) ignore
`trailing_stop_loss.initial_stop` (1032.0 chez GEV) et n'utilise que des
champs de snapshot live filtres `< entry_trigger` ; buffer
`0.20 * atr_1h` code en dur (`:829`) contre `atr_fraction_buffer: 0.1`
declare (JSON `:139`). GEV, 6 occurrences reelles (id 1067966-1079715,
2026-06-29 14:27-15:00) : `validation.valid=True`, `path=CONFIRMED_BREAKOUT`,
volume jusqu'a 1.17x, mais entree bloquee ("`structural_support_below_
entry`" manquant) alors que le stop statique 1032.0 declare aurait ete
parfaitement utilisable. **Impact** : un breakout correctement confirme
par le moteur lui-meme est silencieusement perdu pour une raison purement
mecanique, sans lien avec la qualite du signal.

### P3 — Le chemin `BREAKOUT_RETEST` est structurellement mort (jamais atteignable)

**Preuve** : `retest_valid` (`:499-506`) exige
`snapshot.breakout_already_detected` et `snapshot.new_higher_low_confirmed`
tous deux `True` ; grep exhaustif sur tout `app/` : ces deux champs ne sont
**jamais assignes** ailleurs qu'a leur defaut `False`
(`models.py:207-208`, `stock_market_monitor.py:567-568` lit
`quote.get(key, False)` sans qu'aucun code n'ecrive `quote[key]=True`
nulle part). Empirique GEV : 0/483 occurrences a `True` sur toute la
periode disponible. **Impact** : malgre son nom, le mecanisme de "retest
apres breakout manque" (`missed_breakout.*`, `rearm.*`) ne peut jamais
aboutir via ce chemin de validation specifique — seuls `FAST_BREAKOUT` et
`CONFIRMED_BREAKOUT` (qui ne verifient jamais le statut) sont vivants.

### P4 — `rearm.*` (config standard) n'est jamais lu, et c'est la cause directe de l'impasse `WAITING_RETEST`

**Preuve** : 0 occurrence de `rearm` dans `momentum_breakout.py` ; c'est un
champ standard du template (`setup_type_registry.py:99-103`), present et
concret chez GEV (`new_local_resistance/new_trigger/new_limit`) ;
`SetupStatus.REARMED_ON_NEW_BASE` est la seule cible non-terminale que la
state machine autorise depuis `WAITING_RETEST`
(`state_machine.py:89-97`) que ce type pourrait produire, et il ne la
produit jamais (0 occurrence de `REARMED` dans le fichier). **Impact** :
sans ce mecanisme, `WAITING_RETEST` n'a structurellement aucune sortie
normale — confirme empiriquement par GEV, bloque dans cet etat depuis trois
semaines.

### P5 — `WAITING_RETEST`/`MISSED_BREAKOUT` echappent totalement a la gouvernance de cycle de vie

**Preuve** : `setup_lifecycle_service.LIFECYCLE_MANAGED_STATUSES`
(`:33-41`) gouverne `MISSED_BREAKOUT_WAIT_RETEST` — un statut de l'enum
different de ceux que `momentum_breakout.py` produit reellement
(`MISSED_BREAKOUT`, `WAITING_RETEST`, 0 occurrence de
`MISSED_BREAKOUT_WAIT_RETEST` dans le fichier). Consequence directe :
`SignalEngine._revalidate_lifecycle` (`:117-135`) ignore ces deux statuts
pour ce type — aucune expiration par age, aucune detection de staleness.
**Impact empirique** : GEV en `WAITING_RETEST` sans interruption depuis le
2026-06-29, toujours actif au 2026-07-17 (dernier `updated_at`) ; 9 des 16
setups `momentum_breakout` non termines actuellement en base (56 %) sont
dans ce meme etat.

### P6 — `ENTRY_READY` contourne entierement la state machine, y compris pour les statuts qu'elle interdirait explicitement

**Preuve** : `ActionExecutor.execute_simple_action` ne traite pas
`ENTRY_READY` (`action_executor.py:25-39`) ; `OrderManager.
place_entry_order` ecrit `SetupStatus.ENTRY_ORDER_PLACED` par
`repository.update_setup_status(...)` directement
(`order_manager.py:180-184`), sans jamais appeler
`state_machine.transition()`. Consequence verifiee : `WAITING_RETEST ->
ENTRY_READY`, qui n'est **pas** dans `ALLOWED_TRANSITIONS[WAITING_RETEST]`
(`state_machine.py:89-97`) et serait donc rejetee si elle etait testee,
**n'est jamais testee** — le filet de securite de la state machine, qui
protege effectivement les statuts post-`ENTRY_ORDER_PLACED` (etabli par
audit 09 Axe 2), ne protege **pas** ce saut precis pour ce type.
**Impact** : combine a l'absence de gate `current_status` (Axe 1, audit 09),
c'est un manque de defense a deux niveaux (ni gate applicatif, ni filet
state machine) pour la transition la plus consequente du systeme (soumettre
un ordre reel) — confirme concretement sur GEV, ou 6 evaluations
`CONFIRMED_BREAKOUT` valides en `WAITING_RETEST` n'ont ete stoppees que par
un manque de donnee de stop (P2), pas par une protection structurelle.

### P7 — Aucun mecanisme de gestion de stop apres entree, et risque theorique de double entree en position

**Preuve** : 0 occurrence de `RAISE_STOP`/`IN_POSITION`/`MANAGING_POSITION`
dans `momentum_breakout.py` (grep exhaustif). `SignalEngine.
TERMINAL_SIGNAL_STATUSES` (`signal_engine.py:24-37`) n'inclut ni
`IN_POSITION` ni `MANAGING_POSITION` : `evaluate()` continuerait donc a
tourner et a re-valider la meme resistance fixe apres une entree reelle,
sans jamais emettre `RAISE_STOP` (donc `management.stop_management.steps` /
`trailing_stop_loss.ratchet_rules` de GEV ne seraient jamais actionnes), et
theoriquement capable de re-emettre `ENTRY_READY`. Verification des gardes
anti-doublon (`repositories.py:302-339 _protection_snapshot`,
`signal_engine.py:210-261 _apply_runtime_entry_guards`,
`order_manager.py:73-83`) : elles bloquent seulement `position_open sans
stop actif` et `active_entry_order_id present` — le cas `position ouverte +
stop actif + aucun ordre d'entree actif` (`protection_status=
"POSITION_OPEN_STOP_ACTIVE"`, `repositories.py:338-339`) ne declenche
**aucune** des deux gardes. **Impact** : non observe empiriquement (0/29
setups `momentum_breakout` actuels n'a jamais atteint `IN_POSITION` dans les
donnees disponibles, cf. INCERTITUDES), mais le chemin de code est sans
ambiguite et propre a ce type — les 3 autres types capables d'`ENTRY_READY`
sans gate (audit 09) sont proteges par construction car leur validation
d'entree depend d'un statut qui change definitivement apres le fill,
contrairement a `momentum_breakout` dont le declencheur (`price >
resistance fixe`) reste vrai indefiniment une fois franchi.

---

## INCERTITUDES

1. **Provenance reelle de `snapshot.bars_above_resistance`** : confirme
   n'etre jamais calcule localement depuis `historical_bars` pour GEV (le
   tableau n'apparait dans aucun des 4 361 evenements), mais son mode de
   calcul en amont (broker/`tws_connector.py`/`stock_market_monitor.py`)
   n'a pas ete trace jusqu'au bout dans ce lot — l'instabilite mesuree
   (2 -> 0 en 22 secondes) pourrait venir d'un revirement de prix reel ou
   d'une instabilite du calcul amont ; les deux sont compatibles avec les
   donnees disponibles, aucune ne peut etre exclue depuis `app/setups/`.
2. **Discrepance de comptage `setup_status_changed` vs `STATUS_CHANGE`
   dans `stock_analysis`** : le nombre de transitions `MISSED_BREAKOUT`
   ecrites (`setup_status_changed`, ~19-20 lignes) ne correspond pas
   exactement au cross-tab `(status, target_status)` reconstruit depuis
   `processed[]` (11 attendues) — possiblement du au fait que
   `_revalidate_lifecycle` reecrit `setup["status"]` avant que `evaluate()`
   le voie, decalant le `status` journalise dans `stock_analysis` par
   rapport au statut reellement transitionne. Non resolu dans ce lot, sans
   impact sur les conclusions principales (les chiffres cles — 697 rejets,
   9/16 setups en `WAITING_RETEST` — viennent de requetes directes, pas de
   ce cross-tab).
3. **Risque de double entree en position (P7)** : etabli par lecture de
   code sur 4 fichiers distincts, mais **zero verification empirique
   possible** — aucun setup `momentum_breakout` (parmi les 29 actuels ni
   parmi les `setup_id` retrouvables via `entry_order_submitted`) n'a
   atteint `IN_POSITION` dans les donnees disponibles. Le chemin de code
   est demontre, pas observe en conditions reelles.
4. **`MISSED_BREAKOUT_WAIT_RETEST` (2 setups en base)** : statut distinct
   de `WAITING_RETEST`/`MISSED_BREAKOUT`, gere par le lifecycle service.
   Comment ces 2 setups y sont arrives (puisque `momentum_breakout.py` ne
   produit jamais ce statut lui-meme) n'a pas ete investigue — possiblement
   via `setup_lifecycle_service.py` directement depuis `WAITING_ACTIVATION`
   sur un chemin qui n'a pas ete trace dans ce lot.
5. **Setups `momentum_breakout` autres que GEV (RDW, SOFI, ANET, RKLB,
   etc.)** : non scannes systematiquement dans ce lot (temps limite) au-dela
   de la verification transverse (audit 09) et de la requete `setups`
   agregee (section 5) — GEV a servi de fil conducteur exclusif pour toutes
   les preuves empiriques detaillees (sections 1-3, 6-8) conformement au
   mandat de ce lot ; il est possible que d'autres setups du meme type
   revelent des variantes non couvertes ici (ex. un setup ayant reellement
   atteint `ENTRY_ORDER_PLACED`, ce qui n'a pas ete trouve pour GEV).
