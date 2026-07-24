# Audit en lecture seule — Lot 4 (final) : normalisation transverse

Suite de `audit/01_boucle_evaluation.md` a `audit/04_pre_spec.md`. Mode lecture
seule : aucun fichier de code n'a ete modifie. Objectif de ce lot : determiner
si les corrections envisagees peuvent devenir des regles valables pour les 5
setup_types d'entree (`breakout_retest`, `momentum_breakout`,
`aggressive_rebound`, `range_breakout`, `pullback_continuation`), pas des
rustines par stock. Toutes les requetes SQL ont ete executees en lecture
seule (`mode=ro`) sur `data/trading_state.sqlite` (~154 Go, base reelle de
production, 2026-06-01 -> 2026-07-17).

---

## SECTION A — Etats intermediaires vs ecritures directes de statut

Rappel des 3 points d'ecriture directe (audit 2 POINT 3, confirmes inchanges
a cette lecture) :
1. `app/engine/order_manager.py:126-130` (`ERROR_REQUIRES_MANUAL_REVIEW` sur
   rejet de l'ordre parent) et `:180-184` (`ENTRY_ORDER_PLACED` sur succes du
   bracket) — plus deux autres sites du meme fichier suivant le meme motif
   (jamais notes explicitement dans l'audit 2) : `:352-356`
   (`protective_stop_rejected` -> `ERROR_REQUIRES_MANUAL_REVIEW`) et
   `:543-547` (`_cancel_parent_for_failed_protection` ->
   `ERROR_REQUIRES_MANUAL_REVIEW`, appele depuis 3 sites internes :
   `:149-154`, `:167-172`, `:178`).
2. `app/engine/entry_order_executor.py:121-125` (blocage `MANAGEMENT_ONLY` ->
   `ERROR_REQUIRES_MANUAL_REVIEW`).
3. `app/engine/setup_lifecycle_service.py:415-421` (transitions de lifecycle,
   cible variable).

Aucune des 3 requetes ci-dessus ne consulte `state_machine` **sauf** le
point 3, qui appelle `explain_transition` en garde a la ligne 410 (mais pas
`.transition()`, donc n'utilise jamais l'exception qui protegerait). Les
points 1 et 2 n'appellent `state_machine` nulle part (grep confirme, deja
etabli audit 2).

### A.1 — `order_manager.py` : statuts de depart reellement atteints en production

Aucune table d'historique de statut n'existe (`setups` ne porte que la valeur
courante — confirme par `PRAGMA table_info(setups)`, colonnes : `setup_id,
symbol, setup_type, enabled, mode, status, entry_zone, stop_loss,
risk_amount, order_status, position_status, last_event, config_json,
created_at, updated_at, status_reason, last_revalidated_at` — pas de colonne
d'historique). Reconstruction empirique via l'evenement `stock_analysis`
(qui porte `processed[i]["status"]` = `current_status` **avant** l'appel
`evaluate()` de ce tick, `app/engine/signal_engine.py:98-113`), en notant un
piege : l'evenement `stock_analysis` du meme tick est enregistre **apres**
la boucle de soumission d'ordre (`app/engine/stock_market_monitor.py:282-303`
— `evaluate_snapshot` ligne 282, boucle `signal_handler` lignes 291-293,
`record_stock_analysis` ligne 303, en dehors de la boucle). Il faut donc
chercher le `stock_analysis` **suivant** l'evenement d'ordre, pas precedent.
Requete utilisee (cf. `tmp/audit_query7.py`, execution reelle) :

```sql
SELECT id, timestamp, setup_id, symbol, data_json FROM events
WHERE event_type='entry_order_submitted' ORDER BY timestamp;
-- puis pour chaque ligne :
SELECT id, timestamp, data_json FROM events
WHERE event_type='stock_analysis' AND symbol=? AND timestamp>=?
ORDER BY timestamp ASC LIMIT 5;
-- -> extraction de processed[i] ou setup_id correspond
```

Resultat brut agrege (44 evenements `entry_order_submitted` +
`entry_order_rejected` sur toute la periode disponible) :

| event_type | setup_type | status AVANT le tick | action | n |
|---|---|---|---|---|
| entry_order_submitted | momentum_breakout | WAITING_ACTIVATION | ENTRY_READY | 23 |
| entry_order_submitted | aggressive_rebound | WAITING_ENTRY_SIGNAL | ENTRY_READY | 7 |
| entry_order_submitted | range_breakout | WAITING_ACTIVATION | ENTRY_READY | 5 |
| entry_order_submitted | range_breakout | **ENTRY_ORDER_PLACED** | ENTRY_READY | 4 |
| entry_order_submitted | breakout_retest | WAITING_ENTRY_SIGNAL | ENTRY_READY | 1 |
| entry_order_rejected | momentum_breakout | WAITING_ACTIVATION | ENTRY_READY | 3 |
| entry_order_rejected | range_breakout | WAITING_ACTIVATION | ENTRY_READY | 2 |
| entry_order_rejected | aggressive_rebound | WAITING_ENTRY_SIGNAL | ENTRY_READY | 1 |

**Constat direct** : `momentum_breakout` et `range_breakout` atteignent
`order_manager.py:180-184`/`126-130` depuis `WAITING_ACTIVATION`
**directement** (34/41 cas geolocalises), jamais depuis
`WAITING_ENTRY_SIGNAL` — coherent avec la lecture de code (section B :
`MomentumBreakoutSetup.evaluate()` et `RangeBreakoutSetup.evaluate()` ne
testent jamais `current_status == WAITING_ENTRY_SIGNAL`, ce statut n'est
jamais emis par ces deux types). `aggressive_rebound` et `breakout_retest`
n'atteignent ce point que depuis `WAITING_ENTRY_SIGNAL`, jamais
`WAITING_ACTIVATION` directement (coherent : leur `evaluate()` exige le
palier intermediaire). `pullback_continuation` n'a produit aucun
`entry_order_submitted`/`entry_order_rejected` sur toute la periode
disponible (0 evenement) — aucune donnee empirique pour ce type sur ce point
precis.

**La ligne `range_breakout | ENTRY_ORDER_PLACED | ENTRY_READY | 4` est une
transition non prevue et confirmee par un incident reel documente
ci-dessous (A.3).**

### A.2 — Incoherence confirmee avec `ALLOWED_TRANSITIONS` (independante de WAITING_RETEST/WAITING_CONFIRMATION)

`ALLOWED_TRANSITIONS` (`app/engine/state_machine.py:8-201`) : `SetupStatus.
ERROR_REQUIRES_MANUAL_REVIEW` n'apparait **comme cible autorisee que depuis
`RECONCILING_EXISTING_POSITION`** (ligne 166) et depuis
`MANUAL_REVIEW_REQUIRED` (ligne 194 — `MANUAL_REVIEW_REQUIRED ->
{CANCELLED, ERROR, ERROR_REQUIRES_MANUAL_REVIEW}`). Il **n'apparait dans
aucun autre ensemble cible** — ni `WAITING_ACTIVATION` (lignes 24-38),
ni `WAITING_ENTRY_SIGNAL` (lignes 120-126), ni `ENTRY_ORDER_PLACED`
(lignes 133-138) (grep exhaustif de `ERROR_REQUIRES_MANUAL_REVIEW` dans
`state_machine.py` : 4 occurrences totales, lignes 166, 194, 196, 214 — la
196 et 214 sont respectivement la cle du dict et l'appartenance a
`MANUAL_REVIEW_STATUSES`, pas des cibles supplementaires).

Or A.1 montre empiriquement que `order_manager.py:126-130` ecrit
`ERROR_REQUIRES_MANUAL_REVIEW` depuis `WAITING_ACTIVATION`
(`momentum_breakout`, `range_breakout`) et depuis `WAITING_ENTRY_SIGNAL`
(`aggressive_rebound`) — et A.3 montre que `:352-356`/`:543-547` l'ecrit
depuis `ENTRY_ORDER_PLACED` (les 4 cas `protective_stop_rejected`, voir
A.3). **Ces trois couples — `(WAITING_ACTIVATION, ERROR_REQUIRES_MANUAL_
REVIEW)`, `(WAITING_ENTRY_SIGNAL, ERROR_REQUIRES_MANUAL_REVIEW)`,
`(ENTRY_ORDER_PLACED, ERROR_REQUIRES_MANUAL_REVIEW)` — sont deja, aujourd'hui,
absents de `ALLOWED_TRANSITIONS`, et se produisent neanmoins en production a
chaque tick ou un ordre ou un stop protecteur est rejete par le broker.**
Aucune exception n'est levee car ces 3 sites n'appellent jamais
`state_machine` (confirme audit 2, reconfirme ici par lecture complete des
2 fichiers). Ceci corrige/precise l'audit 2, qui concluait prudemment que
les cibles ecrites "figurent dans les ensembles autorises dans la plupart
des cas observes" : pour `ERROR_REQUIRES_MANUAL_REVIEW` specifiquement, ce
n'est vrai dans **aucun** cas observe sur le chemin d'entree normal.

**Reponse directe a la question posee** : inserer `WAITING_RETEST` et
`WAITING_CONFIRMATION` **n'introduit pas une incoherence nouvelle** sur ce
point precis — l'incoherence existe deja, independamment de ces 2 statuts,
pour tout statut d'entree qui mene a un rejet broker. Elle ajouterait
seulement 2 couples de plus a une liste de couples deja non couverts
(`(WAITING_RETEST, ERROR_REQUIRES_MANUAL_REVIEW)`,
`(WAITING_CONFIRMATION, ERROR_REQUIRES_MANUAL_REVIEW)`), de meme nature que
ceux qui existent deja pour `WAITING_ACTIVATION`/`WAITING_ENTRY_SIGNAL`/
`ENTRY_ORDER_PLACED`.

### A.3 — Incident reel confirme : double soumission d'ordre le 2026-06-29 (`range_breakout`)

Recherche des ordres BUY/SELL en base pour les 4 setup_id impliques
(`GILT_20260628_001`, `LUNR_20260628_001`, `QBTS_20260628_001`,
`STM_20260628_001`) : **0 ligne dans la table `orders`** pour ces 4
`setup_id` (la table `orders` ne conserve donc pas non plus d'historique
complet au-dela d'une purge — `order_history_deleted` est un event_type
observe, 58 occurrences totales). La reconstruction se fait donc uniquement
via `events` (requete `tmp/audit_query8.py`, timeline complete
`GILT_20260628_001` entre 15:00 et 18:00 le 2026-06-29) :

```
15:38:57  protective_stop_rejected     "Accepted by TWS: Cancelled"
15:41:15  order_history_deleted        "Order removed from local history"
15:55:52  active_entry_order_unprotected "An active entry order exists without an attached protective stop order"
15:55:54  opportunity_ready             100.0% READY AUTO
16:18:57  setup_loaded                  "Setup loaded and validated"
16:18:57  order_status_reconciled       "Order marked FILLED after broker reconciliation"
17:26:31  setup_loaded                  "Setup loaded and validated"
17:26:47  protective_stop_submitted     "Protective stop submitted"
17:26:47  entry_order_submitted         "Accepted by TWS: PendingSubmit"   <- 2e ordre reel envoye au broker
17:28:03  duplicate_order_blocked       (puis repete ~150 fois jusqu'a 17:59:51)
```

Meme sequence (`protective_stop_rejected` ~15:38-15:40 puis un second
`entry_order_submitted` ~90 minutes plus tard alors que
`processed["status"]` valait encore `ENTRY_ORDER_PLACED`, puis
`duplicate_order_blocked` en boucle) confirmee pour `LUNR_20260628_001`
(`protective_stop_rejected` 15:39:55 -> `entry_order_submitted` 17:27:00),
`QBTS_20260628_001` (15:39:52 -> 17:27:32) et `STM_20260628_001`
(15:39:59 -> 17:28:25) — **4/4 setups `range_breakout` touches ce jour-la par
un rejet de stop protecteur ont ensuite recu un 2e ordre d'entree reel**,
seulement arrete par le garde-fou `DuplicateOrderError` (qui, une fois le 2e
ordre lui-meme actif, bloque les tentatives suivantes — ce qui explique
l'arret des `duplicate_order_blocked` a repetition apres 17:28).

**Enchainement demontre par le code, coherent avec cette timeline** :
1. `protective_stop_rejected` (`order_manager.py:347-356`) declenche
   `_cancel_parent_for_failed_protection` -> ecrit
   `ERROR_REQUIRES_MANUAL_REVIEW` (`:543-547`) — un statut normalement
   **terminal** pour la boucle de signal
   (`TERMINAL_SIGNAL_STATUSES`, `app/engine/signal_engine.py:24-37`, qui
   inclut bien `ERROR_REQUIRES_MANUAL_REVIEW` ligne 31 — `evaluate()` ne
   serait donc plus jamais appele pour ce setup tant qu'il reste dans ce
   statut, `signal_engine.py:79-80`).
2. Entre 15:41 et 16:18, le statut est revenu a une valeur non-terminale
   (`ENTRY_ORDER_PLACED`, confirme par le `status` lu juste avant le tick de
   17:26:47) — **le mecanisme exact de ce retour n'est pas trace par les
   evenements disponibles** (aucun `setup_status_changed` — qui aurait
   prouve un passage par `ActionExecutor`/`state_machine` — n'apparait dans
   cette fenetre pour ce `setup_id` ; le `setup_loaded` a 16:18:57 preserve
   normalement le statut existant via `_status_after_config_save`,
   `app/engine/setup_engine.py:293-304`, ligne 300-302 : il **relit et
   renvoie le statut deja present en base**, il ne le reinitialise pas a
   `WAITING_ACTIVATION` — donc le retour a `ENTRY_ORDER_PLACED` a du se
   produire **avant** ce `setup_loaded`, par une action non identifiee dans
   les evenements disponibles, cf. INCERTITUDES).
3. `ENTRY_ORDER_PLACED` **n'est pas** dans `TERMINAL_SIGNAL_STATUSES` — donc
   `evaluate()` continue d'etre appele a chaque tick pour ce setup.
   `RangeBreakoutSetup.evaluate()` (`app/setups/range_breakout.py:19-43`)
   **ne teste jamais `current_status`** (confirme par grep : le parametre
   n'apparait qu'une fois, dans la signature, ligne 22 — jamais dans le
   corps de la methode) : il reemet `ENTRY_READY` a chaque tick des que
   `snapshot.price > high`, **quel que soit le statut courant**, y compris
   `ENTRY_ORDER_PLACED`.
4. `OrderManager.place_entry_order` (`:73-83`) ne bloque cette 2e tentative
   que si `protection_snapshot_for_setup` trouve un ordre BUY dans
   `ACTIVE_ORDER_STATUSES = {"CREATED", "SUBMITTED"}`
   (`app/storage/repositories.py:260-261, 281-282`) — un ordre deja
   `FILLED` (comme celui reconcilie a 16:18:57) **ne compte plus comme
   actif** (`_is_active_order`, ligne 281-282), donc
   `protection.get("active_entry_order_id")` redevient `None` et **aucune
   des deux gardes (`UnprotectedActiveOrderError`,
   `DuplicateOrderError`, lignes 74-83) ne se declenche** : le 2e ordre
   passe.

**Conclusion factuelle** : la combinaison (a) statut `ENTRY_ORDER_PLACED`
revenu a une valeur non-terminale par un mecanisme hors de la boucle
normale, et (b) l'absence totale de verification de `current_status` dans
`RangeBreakoutSetup.evaluate()` (et, par lecture de code identique,
`MomentumBreakoutSetup.evaluate()`, section B) a produit un **second ordre
d'entree reellement transmis au broker sur un setup dont l'ordre precedent
etait deja reconcilie `FILLED`**, le 2026-06-29, pour 4 setups `range_
breakout` distincts. Ce n'est pas un scenario theorique : c'est un
evenement de production documente par les logs.

### A.4 — `setup_lifecycle_service.py:415-421` — transitions empiriques

Requete (`tmp/audit_query1.py`/`audit_query2.py`, sur les 16 471 evenements
`setup_lifecycle_status_changed`, qui portent `data.from`/`data.to` en clair
— `app/engine/setup_lifecycle_service.py:429-435`) :

```sql
SELECT json_extract(data_json,'$.from'), json_extract(data_json,'$.to'), COUNT(*)
FROM events WHERE event_type='setup_lifecycle_status_changed' GROUP BY 1,2;
```

| from | to | n |
|---|---|---|
| WAITING_ACTIVATION | BLOCKED | 5699 |
| BLOCKED | WAITING_ACTIVATION | 5679 |
| BLOCKED | MISSED_BREAKOUT_WAIT_RETEST | 1341 |
| MISSED_BREAKOUT_WAIT_RETEST | BLOCKED | 1309 |
| BLOCKED | STALE_SETUP | 850 |
| STALE_SETUP | BLOCKED | 828 |
| WAITING_ACTIVATION | MISSED_BREAKOUT_WAIT_RETEST | 204 |
| MISSED_BREAKOUT_WAIT_RETEST | WAITING_ACTIVATION | 200 |
| STALE_SETUP | WAITING_ACTIVATION | 167 |
| WAITING_ACTIVATION | STALE_SETUP | 163 |
| WAITING_ACTIVATION | INVALIDATED | 20 |
| BLOCKED | INVALIDATED | 11 |

Repartition par `setup_type` (jointure sur `setups.setup_id`, incomplete car
les setups termines/supprimes ne sont plus dans `setups` — voir
INCERTITUDES) : les 4 types `aggressive_rebound`, `momentum_breakout`,
`pullback_continuation`, `range_breakout` presentent tous les memes 6 paires
(`WAITING_ACTIVATION<->BLOCKED`, `BLOCKED<->STALE_SETUP`,
`WAITING_ACTIVATION<->MISSED_BREAKOUT_WAIT_RETEST` etc. — le detail complet
est dans `tmp/audit_query2.py`, sortie deja capturee dans cette session).
`breakout_retest` n'apparait que sur `WAITING_ACTIVATION<->BLOCKED` (4+4,
echantillon plus petit).

**Toutes les 12 paires observees figurent dans `ALLOWED_TRANSITIONS`**
(verifie une a une contre `state_machine.py:24-71` : chaque paire est un
sous-ensemble exact de l'ensemble autorise pour son `from`). Ce point
d'ecriture est donc, empiriquement, **coherent a 100 % avec la table** sur
la periode disponible — attendu, puisque c'est le seul des 3 sites qui
consulte `explain_transition` avant d'ecrire (`:410-414`), meme s'il
n'appelle pas `.transition()` et ne beneficie donc pas de l'exception qui
ferait planter un appel invalide.

**Consequence directe pour l'insertion de `WAITING_RETEST`/
`WAITING_CONFIRMATION`** : ces 2 statuts ne figurent pas dans
`LIFECYCLE_MANAGED_STATUSES` (`app/engine/setup_lifecycle_service.py:33-41`)
ni dans `EVALUABLE_STATUSES` au sens de la revalidation active (ils sont
bien dans `EVALUABLE_STATUSES`, ligne 56-69, mais PAS dans
`LIFECYCLE_MANAGED_STATUSES`). Consequence exacte du code
(`revalidate_and_apply`, ligne 392 : `if not setup_id or current_status not
in LIFECYCLE_MANAGED_STATUSES: return result`) : **un setup dans
`WAITING_RETEST` ou `WAITING_CONFIRMATION` ne recevra jamais de transition
ecrite par `setup_lifecycle_service`** — ni bonne ni mauvaise, il est
simplement hors de portee de ce mecanisme. Aucune incoherence de transition
n'est donc creee ici (le point 3 ne touchera jamais ces 2 statuts), mais un
**gap de couverture** est cree : ces 2 statuts ne beneficieraient plus du
filet de secours lifecycle (detection de staleness, prix trop loin,
invalidation de these, broker-reality) que `WAITING_ACTIVATION` recoit
aujourd'hui — a moins de les ajouter explicitement a
`LIFECYCLE_MANAGED_STATUSES` (changement de perimetre, pas seulement
d'ajout de valeurs a une table).

### A.5 — Existe-t-il un endroit unique pour canaliser toute ecriture de statut ?

Non, structurellement disperse a ce jour : **4 mecanismes distincts et
independants** ecrivent `setups.status` (grep exhaustif de
`update_setup_status` dans `app/engine/` reconfirme sur cette lecture) :
1. `ActionExecutor.transition_setup` / `PositionActionExecutor` — passent
   par `state_machine.transition()`, exception levee si invalide,
   evenements `setup_status_changed`/`setup_transition_rejected` (existent
   deja et sont peuples : 7296 et 10425 occurrences respectivement — c'est
   le SEUL mecanisme qui produit une trace directement exploitable de
   `(from, to)` a 100 %, et le seul qui refuse reellement une transition
   invalide).
2. `SetupLifecycleService.revalidate_and_apply` — consulte
   `explain_transition` en garde (pas d'exception, juste un skip logge en
   warning si refuse) avant d'ecrire.
3. `OrderManager` (4 sites) et `EntryOrderExecutor` (1 site) — n'appellent
   jamais `state_machine`.
4. `FillExecutor` (non lu dans ce lot — `app/engine/fill_executor.py`,
   instancie par `OrderManager.__init__` ligne 56-62) — chemin de
   remplissage d'ordre, tres probablement un 5e site d'ecriture directe non
   audite ici (ENTRY_FILLED, STOP_ORDER_PLACED, IN_POSITION) ; a verifier
   dans un lot ulterieur, voir INCERTITUDES.

Un point de canalisation unique **n'existe pas aujourd'hui**. Le seul
endroit par lequel passe la DECISION (le `SetupSignal` retourne par
`evaluate()`) est unique (`signal_engine.py:81`, voir Section C), mais la
DECISION n'est pas la meme chose que l'ECRITURE du statut : entre les deux,
le routage se fait par type de signal
(`SignalAction.STATUS_CHANGE`/`INVALIDATE` -> passe par la state machine ;
`SignalAction.ENTRY_READY` -> ne passe jamais par elle, part directement
vers `EntryOrderExecutor`/`OrderManager`). Rendre ce routage unique
demanderait de faire transiter `ENTRY_READY` par
`ActionExecutor.transition_setup` (ou equivalent) avant tout appel a
`OrderManager`, ce qui n'est pas le cas aujourd'hui pour aucun des 5 types.

---

## SECTION B — Comment chaque setup_type lit sa config

Tableau rempli par lecture directe de `evaluate()` et de ce qu'il appelle,
pour chacun des 5 types. "CALCULE" = valeur derivee au tick courant depuis le
snapshot marche ; "CONFIG" = lu tel quel (ou avec offset simple) depuis
`self.config`.

| setup_type | Prix d'ENTREE / trigger | STOP | CONFIRMATION | # champs config lus dans evaluate() |
|---|---|---|---|---|
| breakout_retest | **CALCULE** : `round(reference_high + trigger_offset, 2)` ou `reference_high = snapshot.high or snapshot.price` — `app/setups/breakout_retest.py:83-89`. `entry.trigger_price`/`breakout.daily_close_above` (config) ne sont lus que par `estimated_entry_price()` (`:27-37`), **jamais appele par `evaluate()`** (confirme par grep, aucun `self.estimated_entry_price()` dans `evaluate()`) | `self.stop_loss` (`app/setups/base_setup.py:54-58`, lit `trailing_stop_loss.initial_stop`) -> `SetupSignal.stop_loss` ligne 90. Relie a `signal.stop_loss` mais **deconnecte** du stop reellement envoye au broker (`order_manager.py` relit `trailing_stop_loss.initial_stop` independamment, audit 04 Q3) | `bullish_confirmation(snapshot)` (`base_setup.py:169-174`) — compare `close > open` de la bougie **en cours de formation** (audit 02 POINT 1), appelee ligne 82 | 5 : `breakout.daily_close_above`, `retest.no_close_below`, `retest.zone_min`, `retest.zone_max`, `entry.trigger_offset` (+ `trailing_stop_loss.initial_stop` via `self.stop_loss` = 6) |
| momentum_breakout | **CALCULE, dynamique** : `entry_trigger = round_up_to_tick(resistance + offsets["trigger_offset"], minimum_tick)` (`app/setups/momentum_breakout.py:116-119`) ou `offsets["trigger_offset"]` est **lui-meme calcule** depuis ATR/spread/tick live (`_dynamic_offsets`, `:409-434`), **pas depuis un champ de config statique** (`entry.trigger_offset` n'est jamais lu par ce type — absent de tout le fichier). `resistance` = `breakout.resistance` (config) ou repli sur `estimated_entry_price()` (`:45`) | **CALCULE, dynamique, jamais `self.stop_loss`** : `_initial_stop()` (`:800-841`) derive le stop de niveaux de support **live** (`snapshot.last_confirmed_higher_low`, `snapshot.support_level`, `snapshot.successful_retest_low`, `snapshot.structural_support`, ou leurs equivalents `risk.*` en repli) moins un buffer dynamique (ATR/spread/tick). Le resultat est injecte via `metadata["trailing_stop_overrides"]` (`:294`) qui **ecrase** `trailing_stop_loss.initial_stop` de la config au moment de l'entree (`entry_order_executor.py:379-387`) — mecanisme absent des 4 autres types | **Bougie CLOSE deja utilisee** : `_bars_above_resistance()` (`:968-984`) lit `snapshot.historical_bars` (bougies closes) pour compter les bougies consecutives au-dessus de la resistance ; `_volume_confirmation()` (`:577-712`) calcule explicitement `current_bar_is_closed` via `elapsed_bar_percent` (`:605-607`) et **n'utilise le ratio de volume "closed" que si la bougie est effectivement close**, sinon bascule sur un ratio projete (`:634-636`) | 20+ : `breakout.resistance/retest_volume_ratio_min/confirmed_breakout_volume_ratio_min/confirmed_breakout_hold_bars/fast_breakout_volume_ratio_min`, `entry.maximum_limit_price/limit_price`, `liquidity.cap_tier/max_spread_bps/max_spread_atr_fraction/max_position_vs_dollar_volume_pct`, `volume_confirmation.*` (6 sous-champs), `missed_breakout.retest_zone_min/max`, `retest.zone_min/max`, `risk.max_position_amount_usd/max_risk_usd/last_confirmed_higher_low/support_level/structural_support` |
| aggressive_rebound | **CALCULE** : `entry = previous_high + trigger_offset` (`app/setups/aggressive_rebound.py:65-68`) ou `previous_high = snapshot.previous_high or snapshot.high or high` (repli config `support_zone.max` en dernier recours seulement) | `self.stop_loss` (identique a breakout_retest) -> ligne 74 | `bullish_confirmation(snapshot)` — bougie en formation, ligne 65, meme fonction que breakout_retest | 4 : `support_zone.min`, `support_zone.max`, `invalidation.close_below`, `entry.trigger_offset` (+ stop = 5) |
| range_breakout | **PARTIELLEMENT CONFIG** : `entry_price = high + offset` (`app/setups/range_breakout.py:35-40`) ou `high = range.high` est un champ de config **directement requis** (`float(range_config["high"])`, ligne 25, leve `KeyError` si absent — seul type ou le niveau de reference est *lui-meme* un champ config obligatoire, pas une valeur snapshot) | `self.stop_loss` — ligne 41 | **AUCUNE** — `if snapshot.price > high:` (ligne 34) est la seule condition d'entree, sans `bullish_confirmation`, sans lecture de bougie close, sans volume. Le declenchement est un pur test de prix instantane | 3 : `range.high`, `range.low`, `entry.trigger_offset` (+ stop = 4) — le minimum des 5 types |
| pullback_continuation | **CALCULE** : `reference_high + offset` (`app/setups/pullback_continuation.py:38-44`) ou `reference_high = snapshot.high or snapshot.price` (aucune donnee de config, uniquement le snapshot) | `self.stop_loss` — ligne 45 | `bullish_confirmation(snapshot)` (bougie en formation, ligne 37) **combine a** `snapshot.price <= snapshot.ema_20` (indicateur technique live, pas config) | 2 seulement dans `evaluate()` : `entry.trigger_offset` (+ `trailing_stop_loss.initial_stop` = 2 total) — `pullback.entry_reference` n'est lu que par `estimated_entry_price()` (`:10-15`), jamais par `evaluate()` |

### Divergences explicites entre types

1. **Trigger depuis config vs calcule** : aucun des 5 types ne lit
   `entry.trigger_price` dans `evaluate()` (seulement dans
   `estimated_entry_price()`, non appele en live pour aucun type — confirme
   par grep sur les 5 fichiers). Seul `range_breakout` a un niveau de
   reference (`range.high`) qui est **lui-meme** un champ config obligatoire
   ; les 4 autres derivent leur reference d'une valeur **snapshot** (`high`,
   `price`, `previous_high`) et n'utilisent la config que pour un **offset**
   (`trigger_offset`), sauf `momentum_breakout` dont l'offset lui-meme est
   calcule dynamiquement (aucun `entry.trigger_offset` lu). **"Trigger
   depuis config" n'est donc la realite d'AUCUN des 5 types aujourd'hui** :
   c'est une regle a instaurer, pas une regle deja majoritaire a etendre.
2. **Stop relie au signal** : 4/5 types (`breakout_retest`,
   `aggressive_rebound`, `range_breakout`, `pullback_continuation`) utilisent
   la meme propriete statique `self.stop_loss`
   (`trailing_stop_loss.initial_stop`, fixee a la creation du setup).
   `momentum_breakout` fait l'inverse : il **calcule** un stop dynamique a
   chaque tick et **l'ecrit** dans la config via `trailing_stop_overrides`
   avant l'entree. Une regle transverse "le stop du signal doit etre la
   config" **casserait `momentum_breakout`**, qui depend precisement de
   pouvoir le recalculer/l'ecraser ; une regle transverse "le stop peut etre
   calcule mais doit etre ecrit dans la meme config qui alimentera
   l'ordre" est en revanche deja ce que fait `momentum_breakout`, et
   pourrait s'appliquer aux 4 autres en supprimant leur relecture
   independante (audit 04 Q3) plutot que l'inverse.
3. **Confirmation sur bougie close** : deja partiellement en place pour
   `momentum_breakout` (`historical_bars` + detection explicite de l'etat
   "close" via `elapsed_bar_percent`), **absente totalement** de
   `range_breakout` (aucune confirmation, juste `price > high`), et fondee
   sur la bougie **en formation** (pas close) pour les 3 autres
   (`bullish_confirmation`). Les 5 types divergent donc en 3 groupes
   distincts sur ce point precis, pas 2.
4. **Nombre de champs de config lus** : de 2 (`pullback_continuation`) a
   20+ (`momentum_breakout`) — facteur 10, pas une variation mineure.
   `momentum_breakout` n'est pas un cas particulier isole : c'est un type
   structurellement d'une autre complexite (marche, liquidite, volume,
   staleness, tout recalcule en interne) que les 4 autres, qui partagent un
   squelette bien plus proche entre eux.
5. **Gate sur `current_status`** (releve en Section A, reconfirme ici par
   grep) : `breakout_retest`, `aggressive_rebound`, `pullback_continuation`
   **testent explicitement** `current_status` (`WAITING_ACTIVATION`,
   `WAITING_ENTRY_SIGNAL`) avant d'emettre `ENTRY_READY`. `range_breakout`
   ne teste **jamais** `current_status` dans le corps de `evaluate()`
   (confirme : le parametre n'apparait que dans la signature,
   `app/setups/range_breakout.py:22`). `momentum_breakout` ne teste
   `current_status` que pour une seule branche (`MISSED_BREAKOUT`, ligne
   179) ; sa branche `ENTRY_READY` (lignes 279-302) ne le teste pas du
   tout. **Ce n'est pas documente dans les audits precedents et c'est la
   cause racine directe de l'incident de production decrit en A.3.**

---

## SECTION C — Point d'application d'une regle transverse

### Methodes `BaseSetup` partagees et reellement appelees dans `evaluate()`

| Methode `BaseSetup` | breakout_retest | momentum_breakout | aggressive_rebound | range_breakout | pullback_continuation |
|---|---|---|---|---|---|
| `self.stop_loss` (`base_setup.py:54-58`) | Appelee (`:90`) | **Non appelee** (stop recalcule en interne, `_initial_stop`) | Appelee (`:74`) | Appelee (`:41`) | Appelee (`:45`) |
| `bullish_confirmation()` (fonction module, `base_setup.py:169-174`) | Appelee (`:82`) | **Non appelee** (import absent du fichier) | Appelee (`:65`) | **Non appelee** (import absent) | Appelee (`:37`) |
| `estimated_entry_price()` | Surchargee (`:27-37`), **jamais appelee par `evaluate()`** | Surchargee (`:13-20`), idem | Surchargee (`:24-31`), idem | Surchargee (`:10-17`), idem | Surchargee (`:10-15`), idem |
| `worst_case_entry_price()`/`maximum_limit_price()` (`base_setup.py:118-132`) | Non surchargee, non appelee dans `evaluate()` (utilisee seulement par `validate()`) | **Reimplementee en parallele**, pas reutilisee (`:120-129`, logique differente : arrondi au tick + `min()` avec la config) | Non surchargee, non appelee dans `evaluate()` | Non surchargee, non appelee dans `evaluate()` | Non surchargee, non appelee dans `evaluate()` |
| `entry_zone_label()` | Surchargee (`:39-43`) | Non surchargee | Surchargee (`:33-37`) | Non surchargee | Non surchargee |
| `validate()` | Surchargee, ajoute des champs requis (`:16-25`) | Non surchargee | Surchargee, ajoute des champs requis (`:16-22`) | Non surchargee | Non surchargee |
| `initial_status()` | Non surchargee (aucun des 5 types) | | | | |

**Constat** : aucune methode de `BaseSetup` n'est appelee par les 5 types a
la fois a l'interieur de `evaluate()`. `self.stop_loss` est le plus proche
(4/5, tout sauf `momentum_breakout`) ; `bullish_confirmation` est a 3/5.
`momentum_breakout` est systematiquement l'exception : il ne reutilise ni le
stop, ni la confirmation, ni le calcul de prix limite de `BaseSetup` — il
reimplemente sa propre version de chacun.

### Point de passage unique

`SignalEngine.evaluate_snapshot` (`app/engine/signal_engine.py:63-115`),
ligne 81 : `signal = strategy.evaluate(snapshot, current_status)`. C'est le
**seul** endroit du depot ou l'un des 5 `evaluate()` est invoque sur le
chemin live (`strategy` vient de `SetupFactory.create(setup["config"])`,
ligne 76, qui dispatche vers l'une des 5 classes selon
`config["setup_type"]` — `app/setups/setup_factory.py:32`). Aucun autre
appelant de `.evaluate()` sur une instance `BaseSetup` n'existe dans
`app/engine/` (grep confirme).

Consequence pratique : une regle transverse **posee immediatement apres la
ligne 81** (sur le `SetupSignal` retourne, avant qu'il ne soit transmis a
`apply_entry_session_policy`/`_apply_trade_guard_gates` lignes 82-83) pourrait
etre appliquee **une seule fois**, pour les 5 types, sans toucher aux 5
fichiers individuels — mais seulement pour des regles **verifiables sur la
sortie** (ex. "si `action == ENTRY_READY` et `current_status` n'est pas dans
un ensemble autorise pour ce `setup_type`, retomber sur `HOLD`" — c'est
exactement le patch qui aurait empeche l'incident A.3, applicable en un
seul point sans modifier `range_breakout.py` ni `momentum_breakout.py`).
Une regle transverse posee **avant** la ligne 81 (ex. "ne pas appeler
`evaluate()` du tout si `current_status` est un statut de position") est
egalement possible au meme endroit, en s'inspirant de
`TERMINAL_SIGNAL_STATUSES` (lignes 24-37) qui existe deja comme mecanisme de
filtre global mais qui, aujourd'hui, **ne couvre pas** les statuts post-entree
non terminaux (`ENTRY_ORDER_PLACED`, `ENTRY_FILLED`, `STOP_ORDER_PLACED`,
`IN_POSITION`, etc. — absents de l'ensemble, confirme par lecture complete
des lignes 24-37).

En revanche, une regle transverse portant sur le **calcul** (ex. "le trigger
doit toujours venir de tel champ de config", "la confirmation doit toujours
lire `historical_bars[-2]`") ne peut **pas** etre posee a ce point unique :
elle porte sur la maniere dont chaque `evaluate()` **construit** son
`SetupSignal`, ce qui exige de modifier les 5 corps de methode
individuellement (ou de les faire deleguer a un helper commun place dans
`BaseSetup`, ce qui n'existe pas aujourd'hui pour le trigger/la confirmation
— seul `self.stop_loss` et `bullish_confirmation` jouent ce role partiel,
et seulement pour 4/5 et 3/5 types respectivement, cf. tableau ci-dessus).

### Signature de `evaluate()`

Identique pour les 5 types et pour la methode abstraite de `BaseSetup` :
`evaluate(self, snapshot: MarketSnapshot, current_status: SetupStatus) ->
SetupSignal` (`base_setup.py:161-165`,
`breakout_retest.py:45-48`, `momentum_breakout.py:22-25`,
`aggressive_rebound.py:39-42`, `range_breakout.py:19-22`,
`pullback_continuation.py:17-20` — verifie ligne par ligne, aucune
divergence de signature). Un changement de contrat (ex. ajouter un 3e
parametre de memoire persistee, cf. audit 04 Q2/`retest_touched`) devrait
donc etre fait dans les 5 fichiers simultanement, mais ne se heurte a
**aucune divergence prealable** de signature — c'est une modification
mecanique identique 5 fois, pas une reconciliation de contrats differents.

### Helper transverse de lecture de config

`_first_number()` (module-prive, `momentum_breakout.py:946-953`) est **la
seule** fonction de ce type dans tout `app/setups/` (grep confirme : le seul
autre `_first_number` du depot est dans `app/setups/creation_snapshot_service.py`,
une copie separee non partagee, module different). `breakout_retest`,
`aggressive_rebound`, `range_breakout`, `pullback_continuation` n'ont
**aucun helper de lecture de config** : chacun fait ses propres appels
`float(x.get(...))`/`float(x["..."])` inline, sans fonction commune, y
compris entre eux (pas seulement vs `momentum_breakout`). **Il n'existe donc
aujourd'hui aucun socle reutilisable de lecture de config** sur lequel
batir une norme sans le creer d'abord dans `BaseSetup`.

---

## INCERTITUDES RESIDUELLES

1. **Mecanisme exact du retour de statut `ERROR_REQUIRES_MANUAL_REVIEW` ->
   `ENTRY_ORDER_PLACED` entre 15:41 et 16:18 le 2026-06-29** (A.3). Aucun
   evenement `setup_status_changed` (qui aurait prouve un passage par
   `ActionExecutor`) n'apparait dans cette fenetre pour `GILT_20260628_001`.
   `setup_loaded` (`setup_engine.py:293-304`) preserve le statut existant,
   il ne le reinitialise pas — donc le retour a `ENTRY_ORDER_PLACED` a du se
   produire par une action non capturee dans les `event_type` inspectes
   (possible action manuelle via une route API de reconciliation/re-armement
   non tracee par un evenement dedie, ou un mecanisme de `FillExecutor`/
   reconciliation broker non lu dans ce lot). Sans lire
   `app/engine/fill_executor.py` et les routes API de reconciliation
   (`app/api/routes_*.py`), l'origine exacte du retour n'est pas prouvee —
   seul le resultat (statut non-terminal atteint, `evaluate()` relance,
   2e ordre transmis) est confirme par les logs.
2. **`app/engine/fill_executor.py` n'a pas ete lu dans ce lot.** C'est tres
   probablement un 5e site d'ecriture directe de `setups.status`
   (`ENTRY_FILLED`, `STOP_ORDER_PLACED`/`STOP_PLACED`, `IN_POSITION`),
   instancie par `OrderManager.__init__` (`order_manager.py:56-62`) et donc
   hors served par `state_machine` selon toute probabilite (a verifier). Son
   comportement face a `WAITING_RETEST`/`WAITING_CONFIRMATION` n'a pas ete
   audite.
3. **Couverture partielle de la jointure `setup_type` en Section A** : une
   grande partie des `setup_id` cites dans les evenements de 2026-06 ont ete
   supprimes de la table `setups` (setups clotures/purges) — la
   repartition par `setup_type` en A.1/A.4 s'appuie sur le champ
   `processed[i]["setup_type"]` porte par l'evenement `stock_analysis`
   lui-meme (fiable, independant de l'etat actuel de `setups`), mais
   `pullback_continuation` n'a produit aucun evenement `entry_order_submitted`/
   `rejected` observable sur toute la periode — silence de donnees, pas
   preuve d'absence de probleme pour ce type.
4. **`historical_bars` degrade (~4,5 % des cotations, audit 04 Q1)
   n'a pas ete recroise avec les evenements A.3** : rien n'indique que la
   degradation `atr_1h` melange sous la cle `historical_bars` ait joue un
   role dans l'incident du 2026-06-29 (le mecanisme identifie — statut
   non-terminal + absence de gate `current_status` — suffit a l'expliquer
   entierement sans invoquer cette autre defaillance), mais la coincidence
   n'a pas ete formellement exclue faute de temps.
5. **Table `orders` vide pour les 4 `setup_id` de l'incident A.3** —
   confirme qu'un mecanisme de purge (`order_history_deleted`, 58
   occurrences totales en base) retire les lignes d'ordres au-dela d'une
   retention non identifiee dans ce lot ; impossible donc de confirmer
   directement, au niveau de la table `orders`, qu'un 2e `OrderRecord`
   BUY distinct a bien ete cree pour `GILT_20260628_001` (la preuve
   repose uniquement sur la sequence d'`events`, qui est neanmoins sans
   ambiguite : `entry_order_submitted` = un appel reussi et distinct a
   `place_entry_order`, confirme par le message broker different
   "PendingSubmit" a 17:26:47 vs le rejet "Cancelled" a 15:38:57).

---

## CANDIDATS AU STATUT DE NORME

### 1. Trigger depuis un champ de config unique (au lieu de calcule au tick)

**Ne peut PAS devenir une regle unique sans exceptions.** Preuve section B :
`range_breakout` a deja une reference obligatoire en config (`range.high`)
mais **calcule** quand meme le trigger final (`high + offset`) ; les 4
autres derivent leur reference d'une donnee **snapshot** (`high`, `price`,
`previous_high`), pas de config. Imposer "le trigger est un champ de
config" casserait le principe meme de `breakout_retest`/`aggressive_
rebound`/`pullback_continuation` (dont le prix de reference n'est
precisement connu qu'au moment du breakout/rebond/pullback reel, pas a la
creation du setup) et serait incompatible avec les offsets **dynamiques**
de `momentum_breakout` (qui ne lit meme pas `entry.trigger_offset`).
Norme atteignable en revanche : "le trigger CALCULE doit etre trace/
justifiable par un champ de config nomme (`trigger_offset`, `resistance`,
etc.), jamais une constante magique" — deja vrai pour 4/5 types ; seul
`momentum_breakout` derive entierement ses offsets de marche.

### 2. Confirmation sur bougie CLOSE (`historical_bars[-2]`)

**Se heurte a une divergence structurelle, pas seulement a un detail
d'implementation.** 3 groupes distincts existent deja (section B, point 3) :
bougie en formation (`breakout_retest`, `aggressive_rebound`, `pullback_
continuation`, via `bullish_confirmation`), bougie close explicite
(`momentum_breakout`, via `historical_bars` + `elapsed_bar_percent`), et
aucune confirmation du tout (`range_breakout`). Une norme "toujours
`historical_bars[-2]` gardee" est **appliquable sans exception** aux 3 du
premier groupe (remplacement direct de `bullish_confirmation`) et **deja
en place, a durcir seulement**, pour `momentum_breakout` (ajouter la garde
`len(historical_bars) >= 2` + detection de fraicheur identifiee en audit 04
comme prerequis manquant). Pour `range_breakout`, ce n'est pas une
correction mais un **ajout de fonctionnalite** (il n'y a rien a remplacer,
il faudrait introduire une confirmation qui n'existe pas). Verdict : norme
**atteignable pour 4/5 types** (remplacement ou durcissement), avec un
5e (`range_breakout`) qui exige une decision produit prealable ("range_
breakout doit-il desormais exiger une confirmation ?") avant d'etre norme,
pas seulement une extension mecanique.

### 3. Memoire de progression (persistee, du type `retest_touched`)

**Peut devenir une regle unique au niveau du contrat, pas au niveau du
contenu.** Section C confirme : la signature `evaluate()` est **identique**
pour les 5 types et ne presente aucune divergence prealable — ajouter un 3e
parametre est une modification mecanique identique 5 fois (audit 04 avait
deja etabli qu'une colonne sur `setups`, deja chargee par `list_setups()`
avant l'appel a `evaluate()`, est le vehicule le plus direct). Le contenu de
la memoire, en revanche, diverge necessairement par type (`retest_touched`
n'a de sens que pour les types a re-test explicite —
`breakout_retest`/`momentum_breakout` — pas pour `range_breakout` qui n'a
pas de notion de retest dans son `evaluate()` actuel). Norme atteignable :
"le contrat `evaluate(snapshot, current_status, state)` est le meme pour
les 5", pas "le contenu de `state` est le meme pour les 5".

### 4. Stop relie au signal (`signal.stop_loss` = le stop reellement transmis)

**Se heurte a une divergence structurelle inverse de ce qu'on attendrait.**
4/5 types partagent deja `self.stop_loss` (statique, une seule lecture) —
mais c'est prcisement ce groupe des 4 qui est **deconnecte** du stop reel
(audit 04 Q3 : `order_manager.py` relit `trailing_stop_loss.initial_stop`
independamment, 3 fois, jamais reliees a `signal.stop_loss`).
`momentum_breakout`, l'exception structurelle, est le seul type qui **relie
deja** son calcul de stop a ce qui sera reellement transmis, via le
mecanisme `trailing_stop_overrides` consomme explicitement par
`entry_order_executor.py:379-387` avant l'appel a `OrderManager`. Norme
atteignable : **generaliser le mecanisme de `momentum_breakout`** (calcul +
override explicite consomme en aval) aux 4 autres, plutot que d'imposer aux
5 types une simple lecture passive de `self.stop_loss` qui n'a jamais
garanti la coherence avec l'ordre reel pour aucun d'entre eux.

### 5. Gate `current_status` dans `evaluate()` avant tout `ENTRY_READY`

**Non demande explicitement dans les lots precedents, mais impose par cet
audit comme prerequis de securite avant toute autre norme.** Section A.3
demontre par un incident de production reel qu'un `setup_type` sans cette
garde (`range_breakout`, et par lecture de code identique
`momentum_breakout`) peut retransmettre un ordre d'entree reel sur une
position deja remplie des lors que `setups.status` revient, par n'importe
quel mecanisme, a une valeur non-terminale. **Applicable aux 5 types sans
aucune exception** : les 3 types qui le font deja
(`breakout_retest`/`aggressive_rebound`/`pullback_continuation`) n'ont rien
a changer ; les 2 qui ne le font pas n'ont aucune raison metier de ne pas
le faire (rien dans leur logique n'exige de continuer a evaluer une fois
`ENTRY_ORDER_PLACED`/`ENTRY_FILLED`/`IN_POSITION` atteint). C'est, parmi les
5 candidats, le **seul qui soit a la fois generalisable sans exception ET
deja prouve necessaire par une donnee de production**, applicable en un
point unique (Section C : juste apres `signal_engine.py:81`, ou dans
`TERMINAL_SIGNAL_STATUSES` en y ajoutant les statuts post-entree non
terminaux) sans toucher aux 5 fichiers `evaluate()` individuellement.
