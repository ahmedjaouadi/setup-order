# Audit en lecture seule — Lot 3 : pré-spécification (dernier lot avant spec)

Suite de `audit/01_boucle_evaluation.md`, `audit/02_points_bloquants.md` et
`audit/03_couche_donnees.md`. Mode lecture seule : aucun fichier de code n'a
été modifié. Chaque affirmation est accompagnée d'une référence fichier:ligne.
Pour les vérifications en base, requêtes SQL/Python exactes + résultat brut
(base réelle de production `data/trading_state.sqlite`, ~154 Go au moment de
l'audit, ouverte en `mode=ro`).

Décisions déjà prises, rappelées ici sans être rediscutées : confirmation
AVGO sur bougie 15m CLOSE (`historical_bars[-2]`) ; AVGO reste
`breakout_retest` ; cible `WAITING_ACTIVATION -> WAITING_RETEST ->
WAITING_CONFIRMATION -> ENTRY_READY` avec mémoire `retest_touched`.

---

## Q1 — Fiabilité de `historical_bars[-2]` comme bougie CLOSE

### Construction du tableau

```python
2529  rows = [
2530      row
2531      for row in (_bar_to_ohlcv(bar) for bar in list(bars or []))
2532      if row.get("close") is not None
2533  ]
2534  if not rows:
2535      payload = {... "available": False, ...}   # pas de clé historical_bars du tout
...
2553  latest = rows[-1]
2554  previous = rows[-2] if len(rows) > 1 else {}
...
2599  "previous_high": previous.get("high"),
...
2616  "historical_bars": rows[-180:],
```
(`app/broker/tws_connector.py:2529-2616`, fonction `_historical_quote_from_bars`)

Point critique non documenté ailleurs : **si `len(rows) == 1`** (un seul point
OHLCV utilisable renvoyé par `reqHistoricalDataAsync`), `previous = {}`
(ligne 2554) mais **`historical_bars` vaut quand même `rows[-180:]` = une
liste à 1 élément** (ligne 2616) — rien dans `_historical_quote_from_bars` ne
raccourcit ou ne vide `historical_bars` dans ce cas. Un lecteur qui ferait
`historical_bars[-2]` sur cette liste lèverait une `IndexError` (accès hors
borne sur une liste de longueur 1). Si `rows` est vide, la fonction retourne
avant (ligne 2534-2552) et **aucune clé `historical_bars` n'existe du tout**
dans le payload — un lecteur non défensif planterait avec un `KeyError` (ou,
via `MarketSnapshot.historical_bars` qui vaut `[]` par défaut côté
`app/engine/stock_market_monitor.py:588-590`, `historical_bars[-2]`
planterait alors en `IndexError` sur liste vide).

### Preuve empirique en base — `bars_15m_count`

`bars_15m_count` est peuplé uniquement par
`"bars_15m_count": signal.get("bar_count")` — `app/broker/tws_connector.py:2732`
— c'est-à-dire le `bar_count` (`len(rows)`) de la branche `signal` (15 min)
spécifiquement, avant fusion. Requête (lecture seule, table `events`,
543 940 lignes `event_type='stock_quote'`, scan complet nécessaire) :

```sql
SELECT json_extract(data_json,'$.bars_15m_count') AS c, COUNT(*)
FROM events WHERE event_type='stock_quote' GROUP BY c ORDER BY CAST(c AS INTEGER) ASC;
```
Résultat brut (valeurs les plus basses) :
```
(None, 24337)
(104, 13612)
(105, 13028)
(106, 14606)
(107, 14682)
(108, 13736)
(109, 11556)
(110, 10096)
(111, 11676)
...
```
```sql
SELECT COUNT(*) FROM events WHERE event_type='stock_quote'
  AND CAST(json_extract(data_json,'$.bars_15m_count') AS INTEGER) < 2;
-- (0,)
```

**Réponse directe** : sur l'intégralité des 543 940 événements `stock_quote`
disponibles (2026-06-01 → 2026-07-17), **aucun** n'a `bars_15m_count < 2`
quand cette clé est renseignée (0 ligne). La valeur minimale non nulle
observée est `104`. Explication : `hybrid_signal_duration = "5 D"`
(`app/broker/tws_connector.py:589`, `config.yaml:27`) — l'appel demande 5
jours de bougies RTH à chaque fois, pas seulement "depuis l'ouverture de
séance" ; donc même au tout premier tick d'une journée, la fenêtre contient
déjà plusieurs jours précédents. Le cas "moins de 2 bougies en tout début de
séance" évoqué dans la question **ne se produit pas en pratique avec cette
configuration**, sauf pour un symbole dont l'historique IBKR réel serait
inférieur à 5 jours de cotation (introduction récente) — cas non rencontré
dans les données disponibles.

### Mais `bars_15m_count` est `None` dans 24 337 cas (4,5 %) — et ce n'est PAS anodin

Inspection directe de 3 événements récents à `bars_15m_count IS NULL` :

```python
# id=2111816  QCOM  2026-07-12T10:59:40Z  market_data_source=hybrid
# bar_count=210  len(historical_bars)=180
# first_bar_date=2026-06-03T11:00:00-04:00  last_bar_date=2026-07-10T15:00:00-04:00

# id=2111812  ONDS  2026-07-12T10:59:35Z  market_data_source=hybrid
# bar_count=210  len(historical_bars)=180
# first_bar_date=2026-06-03T11:00:00-04:00  last_bar_date=2026-07-10T15:00:00-04:00

# id=2045532  AAOI  2026-07-08T15:51:17Z  market_data_source=hybrid
# bar_count=None  len(historical_bars)=0
```

Pour QCOM/ONDS : le quote est daté du 2026-07-12 mais la **dernière** bougie
de `historical_bars` est du **2026-07-10 15:00** — vieille de 2 jours au
moment du tick — et 180 bougies couvrent une plage du **2026-06-03 au
2026-07-10** (37 jours calendaires), ce qui est incompatible avec des bougies
15 minutes (180 bougies de 15 min ne couvrent que ~1,9 jour de séance RTH,
cf. audit 01) mais cohérent avec des bougies **1 HEURE**. C'est exactement le
mécanisme de repli théorisé dans `audit/03_couche_donnees.md` (Q1,
`app/broker/tws_connector.py:2651`, `2699-2700`) : quand la branche `signal`
(15 min) échoue, `base = dict(atr_1h)` récupère un `historical_bars` en
bougies 1h, sous la MÊME clé, **sans aucun champ qui le signale** —
**confirmé ici empiriquement pour la première fois**, pas seulement déduit du
code. `market_data_source` reste `"hybrid"` dans ce cas : ce champ ne permet
pas de détecter la dégradation.

Pour AAOI : `historical_bars` est une liste **vide** (`len == 0`),
`bar_count=None` — cas où même `atr_1h` a échoué (repli sur `live`, qui ne
construit jamais `historical_bars`, `app/broker/tws_connector.py:1382-1461`).

**Conséquence directe pour la cible** : dans ~4,5 % des cotations
enregistrées sur 6 semaines de production, `historical_bars[-2]` serait soit
(a) une bougie 1h vieille de plusieurs jours faussement prise pour "la
15 min précédente", soit (b) inexistante (`IndexError`/`KeyError` selon le
point d'accès). Rien dans le snapshot ne permet de distinguer ces cas d'un
vrai `historical_bars` 15 min frais — ni `bar_size`, ni un flag, ni
`bars_15m_count` lui-même n'étant fiable comme simple détecteur suffisant
puisqu'il vaut `None` aussi bien pour le cas "1h mélangé" que pour le cas
"vide" (les deux se confondent sous `bars_15m_count IS NULL`, il faudrait
inspecter `len(historical_bars)` ET la plage de dates pour les distinguer,
ce qu'aucun code actuel ne fait — cf. audit 03, aucune fonction ne calcule
"closed" à partir de `bar_date`).

### Continuité intra-séance — recherche de trous

Inspection directe du `historical_bars` complet (130 bougies) du dernier
événement `stock_quote` disponible (`id=2418012`, ZETA, 2026-07-17T20:23:25Z,
`bars_15m_count` renseigné dans ce cas) :

```python
gap distribution (minutes): Counter({15.0: 125, 1065.0: 4})
first date 2026-07-13T09:30:00-04:00
last date  2026-07-17T15:45:00-04:00
```

125 écarts consécutifs sur 129 valent exactement 15,0 minutes ; les 4 écarts
restants (1065 min ≈ 17h45) correspondent aux 4 transitions
clôture-veille → ouverture-lendemain entre les 5 séances couvertes (2026-07-13
au 2026-07-17) — cohérent avec `useRTH=True`
(`app/broker/tws_connector.py:594`), pas un trou intra-séance silencieux.
**Sur cet échantillon réel, aucune bougie intra-séance manquante n'est
observée** : `historical_bars[-2]` est bien, dans ce cas nominal (branche
`signal` disponible), la bougie exactement 15 minutes avant `historical_bars[-1]`.
Limite de cette vérification : un seul symbole, une seule fenêtre — pas
généralisé à tous les symboles/jours (voir INCERTITUDES).

### Fuseau horaire de la clé `"date"`

`_date_text` (`app/broker/tws_connector.py:3250-3257`) produit soit
`value.isoformat()` d'un `datetime` tz-aware (chaîne avec heure ET offset,
ex. `"2026-07-17T15:45:00-04:00"`), soit — si `ib_async` renvoie un objet
`date` pur (pas `datetime`) — une chaîne **sans heure ni offset** (ex.
`"2026-06-01"`, ligne 3255-3256 : `if isinstance(value, date): return value.isoformat()`,
qui pour un objet `date` ne produit qu'`AAAA-MM-JJ`).

Échantillonnage large (1 ligne sur 5000, toute la table `events
event_type='stock_quote'`, 118 lignes retenues) :
```python
distinct offsets: {'-04:00', 'NO_OFFSET:2026-06-03', 'NO_OFFSET:2026-06-01', 'NO_OFFSET:2026-06-02'}
```
Vérifié séparément sur les événements les plus récents (5 derniers du
2026-07-17) : offset `-04:00` systématique, jamais de format sans heure.
**Constat** : le format `date`-seul (sans heure/offset) n'apparaît que sur
les tout premiers jours de la base (2026-06-01 à 03) ; depuis, format ISO
avec heure et `-04:00` constant. Cause exacte non déterminée (branche de
code différente à l'époque ? config différente ? — voir INCERTITUDES) :
**le format n'est donc pas garanti par le code lui-même** (`_date_text`
gère explicitement les deux cas), seule l'observation récente est stable. Un
parseur qui supposerait toujours "T" + offset présents planterait sur ce
format historique s'il devait se reproduire.
`-04:00` = EDT (heure d'été New York) — aucune bascule EST (`-05:00`)
observée dans les données disponibles (toutes en juin-juillet, en heure
d'été ; non vérifié sur une bascule DST réelle, voir INCERTITUDES).

### Champs `previous_*` existants

Grep exhaustif de `previous_` dans `app/broker/tws_connector.py`,
`app/engine/stock_market_monitor.py`, `app/models.py` : **seul
`previous_high` existe** —
`"previous_high": previous.get("high")` (`app/broker/tws_connector.py:2599`),
recopié dans `MarketSnapshot.previous_high`
(`app/engine/stock_market_monitor.py:515`, champ déclaré `app/models.py:172`).
**Aucun `previous_close`, `previous_open`, `previous_low` nulle part dans le
dépôt** (grep confirmé, zéro occurrence). Pour obtenir la clôture de la
bougie précédente, il faut donc lire `historical_bars[-2]["close"]`
directement — aucun raccourci équivalent à `previous_high` n'existe déjà
pour `close`/`open`/`low`.

---

## Q2 — Le point de câblage de la mémoire `retest_touched`

### Table et schéma exacts

```sql
570  CREATE TABLE IF NOT EXISTS setup_condition_states (
571      setup_id TEXT PRIMARY KEY,
572      payload_json TEXT NOT NULL DEFAULT '{}',
573      updated_at TEXT NOT NULL
574  );
```
(`app/storage/database.py:570-574`)

**Une seule ligne par `setup_id`** (clé primaire = `setup_id`, pas
`(setup_id, condition)`) — `payload_json` est un blob JSON contenant une
liste `conditions: [...]`, chaque élément de la liste portant son propre
`validated_at` (`app/engine/setup_condition_tracker.py:245-255`). Ce n'est
donc PAS une table normalisée par condition ; toute lecture/écriture
individuelle d'une condition passe par lire tout le JSON, le modifier, tout
réécrire.

Écriture :
```python
533  def save_setup_condition_state(self, setup_id: str, payload: dict[str, Any]) -> None:
534      self.database.execute(
535          """
536          INSERT INTO setup_condition_states (setup_id, payload_json, updated_at)
537          VALUES (?, ?, ?)
538          ON CONFLICT(setup_id) DO UPDATE SET
539              payload_json = excluded.payload_json, ...
```
(`app/storage/repositories.py:533-543`) — appelée uniquement depuis
`SetupConditionTracker.update_from_evaluation`
(`app/engine/setup_condition_tracker.py:63-99`, écriture ligne 97-98, mais
seulement `if not _same_payload(payload, previous)` — ligne 97).

Lecture :
```python
523  def get_setup_condition_state(self, setup_id: str) -> dict[str, Any] | None:
524      row = self.database.execute(
525          "SELECT payload_json FROM setup_condition_states WHERE setup_id = ?",
```
(`app/storage/repositories.py:523-531`).

### `validated_at` est-il JAMAIS remis à zéro ?

Le docstring de la classe l'affirme (`"les timestamps de validation sont
persistes en base et jamais recalcules"`, `app/engine/setup_condition_tracker.py:56-57`)
mais **le code contredit partiellement ce docstring** :
```python
286  def _previous_conditions_by_id(
287      previous: dict[str, Any] | None,
288      overall: str,
289  ) -> dict[str, dict[str, Any]]:
290      if not isinstance(previous, dict):
291          return {}
292      # Sequence terminee puis relancee (rearm lifecycle): on repart de zero.
293      # ready_to_enter -> watching n'est PAS un rearm: le signal d'entree peut
294      # simplement etre retenu par un garde-fou systeme, l'historique est garde.
295      if overall == OVERALL_WATCHING and previous.get("overall_status") in {
296          OVERALL_ENTERED,
297          OVERALL_INVALIDATED,
298      }:
299          return {}
```
(`app/engine/setup_condition_tracker.py:286-299`) — quand l'`overall_status`
courant redevient `OVERALL_WATCHING` alors que le précédent payload était
`OVERALL_ENTERED` ou `OVERALL_INVALIDATED`, la fonction retourne `{}` : dans
`build_conditions_payload` (`app/engine/setup_condition_tracker.py:194-283`),
`previous_conditions = _previous_conditions_by_id(previous, overall)` (ligne
205) devient vide, donc **tous les `validated_at` précédents sont perdus** —
chaque condition repart avec `validated_at=None` sauf si `index <
validated_count` au nouveau tick (ligne 225-227, où `validated_at = ... if
prev_validated else now`, mais `prev` vaut `{}` donc `prev_validated=False`,
donc `validated_at = now` si validée à nouveau ce tick, sinon `None`).
**Donc** : `validated_at`, une fois posé, n'est PAS remis à zéro tant que le
statut recule seulement jusqu'à `OVERALL_WATCHING` **sans être passé par**
`ENTERED`/`INVALIDATED` juste avant (ex. `READY -> WATCHING` par garde-fou
bloquant, explicitement exempté ligne 294 : "n'est PAS un rearm"). Il EST
remis à zéro si le setup a été réarmé après être passé par `ENTERED` ou
`INVALIDATED` (rearm lifecycle). Ce n'est donc "jamais recalculé" que dans
le cas où aucun rearm n'a eu lieu depuis — le docstring est imprécis sur ce
point.

### Peut-on lire cet état AVANT `evaluate()` aujourd'hui ?

Grep exhaustif de `condition_tracker`, `get_setup_condition_state`,
`conditions_payload`, `SetupConditionTracker` dans `app/engine/signal_engine.py`
et tout `app/setups/` : **zéro occurrence**. Les seuls appelants de
`conditions_payload`/`get_setup_condition_state` sont :
- `SetupConditionTracker.update_from_evaluation` elle-même (ligne 85, pour
  lire le `previous` payload — mais cet appel a lieu APRÈS `evaluate()`,
  voir plus bas) ;
- `TradingEngine.setup_conditions(setup_id)` — `app/engine/trading_engine.py:2209-2213`,
  route API de LECTURE pour l'UI (`app/api/routes_setups.py:64`), totalement
  hors du cycle d'évaluation live.

Ordre exact d'exécution dans le cycle (`app/engine/stock_market_monitor.py:273-334`) :
```python
282  evaluations = self.signal_engine.evaluate_snapshot(snapshot, build_setup_analysis_trace)
                    # <- evaluate() de chaque setup tourne ICI (signal_engine.py:81)
...
291  for evaluation in evaluations:
292      self.track_setup_conditions(evaluation, snapshot)
                    # <- SetupConditionTracker.update_from_evaluation ICI, APRES
293      await self.signal_handler(...)
```
`track_setup_conditions` (ligne 319-334) appelle
`self.condition_tracker.update_from_evaluation(evaluation.setup,
evaluation.current_status, evaluation.signal, snapshot)` (ligne 324-329) —
qui prend en paramètre `evaluation.signal`, **le signal déjà produit par
`evaluate()`**. Structurellement, `update_from_evaluation` ne PEUT être
appelée qu'après `evaluate()`, jamais avant, dans le même tick. Et rien dans
`signal_engine.py` ne relit `setup_condition_states` avant ou pendant
l'appel à `evaluate()` (ligne 81) du tick courant NI d'un tick précédent :
**`setup_condition_states` est structurellement coupé de la décision** —
écrit après le signal, jamais relu avant `evaluate()`, à aucun tick.

### Ce qui EST déjà lu avant `evaluate()` dans le cycle

```python
70  for setup in self.repository.list_setups():
71      if setup["symbol"] != symbol:
72          continue
73      setup = self._revalidate_lifecycle(setup, snapshot)
74      current_status = SetupStatus(setup["status"])
75
76      strategy = SetupFactory.create(setup["config"])
...
81      signal = strategy.evaluate(snapshot, current_status)
```
(`app/engine/signal_engine.py:70-81`) — `self.repository.list_setups()`
exécute `SELECT * FROM setups ORDER BY symbol, setup_id`
(`app/storage/repositories.py:444`) : **TOUTES les colonnes de la table
`setups`** sont donc déjà chargées dans le dict `setup`, EN MÉMOIRE, dans
CETTE MÊME fonction, ligne 70, avant l'appel à `evaluate()` ligne 81. Seule
`current_status` (colonne `status`, ligne 74) est effectivement PASSÉE en
paramètre à `evaluate()` aujourd'hui — mais n'importe quelle autre colonne de
`setups` serait, elle aussi, déjà présente dans ce même dict `setup` à ce
point précis du code, sans requête SQL supplémentaire à ajouter.

### Réponse directe à la question posée

Ce qui existe aujourd'hui, sans rien concevoir de nouveau :
- **(a) `setup_condition_states`** — une ligne par `setup_id`, blob JSON,
  lue exclusivement par `SetupConditionTracker` (après `evaluate()`, pour le
  diff d'affichage) et par une route API de lecture UI. **Jamais lue dans
  `signal_engine.py` ni dans aucun `app/setups/*.py`.** Non disponible dans
  la fonction/le scope où `evaluate()` est appelé.
- **(b) une colonne sur `setups`** — `list_setups()` (`app/storage/repositories.py:443-445`)
  fait déjà `SELECT *` : toute colonne ajoutée à cette table serait déjà
  chargée dans le dict `setup`, dans la même fonction `evaluate_snapshot`
  (`app/engine/signal_engine.py:70-81`), AVANT la ligne d'appel à
  `evaluate()` (ligne 81) — au même titre que `current_status` (ligne 74)
  l'est déjà.
- **(c) autre mécanisme déjà présent lu avant `evaluate()`** : recherche
  faite, il n'y en a pas d'autre. Le seul autre état relu avant `evaluate()`
  dans ce même passage est `setup["config"]` (ligne 76, JSON statique de
  configuration, non mutable par le moteur en cours de vie) et
  `current_status` lui-même (ligne 74). Aucune troisième source d'état
  mutable par-setup n'est lue à cet endroit du code.

**Seul critère demandé** : parmi ce qui existe, seule l'option **(b)** place
la donnée dans un dict déjà chargé, dans la même fonction, avant la ligne
d'appel à `evaluate()` — (a) ne l'est dans aucun cas, à aucun tick.

---

## Q3 — L'écart entre `evaluate()` et le prix réellement envoyé

### Point d'injection exact de `signal.entry_price`

```python
80  if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
81      in_retest = float(retest["zone_min"]) <= snapshot.price <= float(retest["zone_max"])
82      if in_retest and bullish_confirmation(snapshot):
83          reference_high = snapshot.high or snapshot.price
84          trigger_offset = float(entry.get("trigger_offset", 0.02))
85          return SetupSignal(
86              action=SignalAction.ENTRY_READY,
87              reason="Retest confirmed by bullish candle",
88              target_status=SetupStatus.ENTRY_READY,
89              entry_price=round(reference_high + trigger_offset, 2),
90              stop_loss=self.stop_loss,
91          )
```
(`app/setups/breakout_retest.py:80-91`, lignes confirmées inchangées depuis
`audit/01`) — **seul et unique** endroit du dépôt qui construit un
`SetupSignal.entry_price` pour `breakout_retest` (grep de
`entry_price=` dans `app/setups/breakout_retest.py` : une seule occurrence,
ligne 89).

### `signal.entry_price` est-il recalculé/écrasé en aval ?

Grep exhaustif de `.entry_price =` (assignation) dans `app/engine/*.py` :
**aucune occurrence** — `signal.entry_price` n'est jamais réassigné après sa
création. Tous les usages en aval sont des LECTURES qui en dérivent une
NOUVELLE valeur, sans toucher `signal.entry_price` lui-même :
- `entry_price=signal.entry_price` passé à `RiskEngine.evaluate`
  (`app/engine/entry_order_executor.py:196`) — lecture seule.
- `RiskEngine.worst_case_entry_price(setup_config, entry_price)`
  (`app/engine/risk_engine.py:52-66`) calcule une valeur DÉRIVÉE,
  `risk_decision.entry_price = round(worst_case_entry_price, 4)`
  (`app/engine/risk_engine.py:158`), tandis que
  `risk_decision.trigger_price = entry_price` (paramètre d'entrée, ligne 162)
  **reste égal à `signal.entry_price` sans transformation**.
- `OrderManager._entry_order_prices` (`app/engine/order_manager.py:206-237`) :
  pour `order_type == "STP_LMT"`, `trigger_price = risk_decision.trigger_price`
  (ligne 218-221, jamais `None` donc toujours pris) = **toujours
  `signal.entry_price` sans modification**. `limit_price =
  risk_decision.entry_price` (ligne 227-235) = la valeur dérivée
  `worst_case_entry_price` (config `maximum_limit_price`/`limit_price`, ou
  repli `entry_price + limit_offset`).

**Réponse directe** : `breakout_retest.py:83-89` (numérotation identique en
lignes 80-91 dans la lecture actuelle) est bien le SEUL endroit qui calcule
`entry_price` pour ce setup — il n'est recalculé nulle part, seulement
DÉRIVÉ (une seconde valeur, `limit_price`, en est tirée séparément, sans
toucher au trigger). C'est donc bien ce point précis (ligne 89) qu'il
faudrait modifier pour que le trigger transmis vaille
`entry.trigger_price` (368.5 pour AVGO) au lieu de
`round(reference_high + trigger_offset, 2)`.

### Le stop : que lit exactement `self.stop_loss` ?

```python
54  @property
55  def stop_loss(self) -> float | None:
56      trailing = self.config.get("trailing_stop_loss", {})
57      stop = trailing.get("initial_stop") if isinstance(trailing, dict) else None
58      return float(stop) if stop is not None else None
```
(`app/setups/base_setup.py:54-58`) — lit `trailing_stop_loss.initial_stop`.
Pour AVGO : `"initial_stop": 354.8` (`data/setups/AVGO_20260629_001.json`,
section `trailing_stop_loss`) — **confirmé identique** à ce qu'attend la
config (`trailing_stop_loss.initial_stop=354.8`, pas d'autre champ). Aucun
écart ici pour AVGO spécifiquement.

**Mais** : `signal.stop_loss` ainsi calculé n'est PAS le stop réellement
transmis au broker. Grep de tous les usages de `signal.stop_loss` /
`.stop_loss` dans le chemin d'ordre :
```
app/engine/entry_order_executor.py:137   "stop_loss": signal.stop_loss,   # payload d'événement (affichage)
app/engine/signal_engine.py:108          "stop_loss": signal.stop_loss,   # processed dict (affichage/log)
app/engine/signal_engine.py:164          stop_loss = _number_or_none(signal.stop_loss)  # cost gate uniquement
```
`signal.stop_loss` sert donc à l'affichage/logs et au calcul du
`_cost_gate_verdict` (`app/engine/signal_engine.py:156-176`, `risk_per_share
= abs(entry_price - stop_loss)`), **mais jamais transmis à
`RiskEngine.evaluate` ni à `OrderManager.place_entry_order`.** Le stop
réellement utilisé pour le risque et pour l'ordre stop protecteur est
recalculé INDÉPENDAMMENT, à deux reprises distinctes :
```python
151  trailing_stop = _trailing_initial_stop(effective_setup.get("config", {}))
...
197      stop_loss=trailing_stop,   # passé a RiskEngine.evaluate
```
(`app/engine/entry_order_executor.py:151,197`) puis, une seconde fois, dans
`OrderManager.place_entry_order` :
```python
84  trailing_stop = _trailing_initial_stop(setup)
...
91  risk_decision.stop_loss = trailing_stop   # ECRASE toute valeur precedente
```
(`app/engine/order_manager.py:84-91`) — `_trailing_initial_stop`
(`app/engine/order_manager.py:602-603`) délègue à
`_protective_stop_from_setup` (`app/engine/order_manager.py:590-599`), qui
relit `trailing_stop_loss.initial_stop` **directement depuis
`setup["config"]`**, indépendamment de tout ce qui a été calculé par
`evaluate()`. Trois fonctions distinctes
(`app/engine/risk_engine.py:417-423`,
`app/engine/entry_order_executor.py:397-403`,
`app/engine/order_manager.py:590-603`) relisent chacune
`trailing_stop_loss.initial_stop` séparément — même champ de config, mais
trois lectures indépendantes à trois moments différents du cycle, jamais
reliées à `signal.stop_loss`. **Pour AVGO aujourd'hui, la valeur numérique
finale est identique (354.8) car le champ de config n'a pas changé entre
temps** — mais un écart serait possible en toute généralité si
`trailing_stop_loss.initial_stop` était modifié en base entre le calcul de
`evaluate()` et la transmission de l'ordre (aucun verrou ni snapshot commun
entre ces relectures).

### `order_type` : lu ou câblé en dur ?

```python
93  order_type = str(entry.get("order_type", self.default_entry_order_type))
```
(`app/engine/order_manager.py:93`, `self.default_entry_order_type` valant
`"STP_LMT"` par défaut seulement si `entry.order_type` est absent —
`app/engine/order_manager.py:45`) — **lu depuis la config**, pas hardcodé.
Pour AVGO, `entry.order_type = "STP_LMT"`
(`data/setups/AVGO_20260629_001.json`) est donc bien respecté : la branche
`if order_type == OrderType.STP_LMT.value:` (`app/engine/order_manager.py:227`)
est prise, produisant `trigger_price` + `limit_price` (pas de `stop_price`
simple), cohérent avec un ordre stop-limite bracket. Aucun écart constaté ici
pour AVGO.

---

## INCERTITUDES RÉSIDUELLES

1. **Q1 — origine du format `bar_date` sans heure/offset** observé
   uniquement sur 2026-06-01 à 03 (les tout premiers jours de la base). Le
   code (`_date_text`, `app/broker/tws_connector.py:3250-3257`) explique
   MÉCANIQUEMENT comment ce format peut apparaître (objet `date` pur au lieu
   de `datetime`), mais je n'ai pas déterminé la cause précise (config
   différente à l'époque, version différente d'`ib_async`, branche de code
   `historical`/`ohlcv` active un temps avant bascule vers `hybrid` ?) ni
   confirmé que ce cas est définitivement clos aujourd'hui — seule
   l'absence d'occurrence récente (5 derniers événements du 2026-07-17) a
   été vérifiée, pas une garantie de non-récurrence.
2. **Q1 — généralisation de la continuité 15 min sans trou.** Vérifiée sur
   UN symbole (ZETA), UNE fenêtre de 130 bougies (5 séances). Le mécanisme
   de code étant commun à tous les symboles, rien ne suggère un
   comportement différent ailleurs, mais ce n'est pas vérifié
   exhaustivement — un symbole illiquide pourrait présenter des bougies
   creuses ou des trous que cet échantillon ne montre pas.
3. **Q1 — fréquence du cas `historical_bars` en bougies 1h mélangées**
   confirmée à 2 occurrences précises (QCOM, ONDS, même minute) sur les 3
   événements `bars_15m_count IS NULL` inspectés en détail ; les 24 337
   occurrences totales de `bars_15m_count IS NULL` n'ont pas été
   individuellement classées entre "repli 1h mélangé" (comme QCOM/ONDS) et
   "échec total, `historical_bars` vide" (comme AAOI) — la proportion
   exacte de chaque sous-cas sur l'ensemble des 24 337 n'est pas établie,
   seulement leur coexistence démontrée.
4. **Q2 — absence de vérification empirique du rearm.** Le comportement de
   remise à zéro de `validated_at` lors d'un rearm après `ENTERED`/`INVALIDATED`
   (`app/engine/setup_condition_tracker.py:295-299`) est établi par lecture
   de code, pas observé sur un setup réel ayant traversé ce cycle complet en
   base.
5. **Q3 — absence de verrou entre les trois lectures indépendantes de
   `trailing_stop_loss.initial_stop`.** J'ai établi que ces trois lectures
   (risk_engine, entry_order_executor, order_manager) portent sur le même
   champ de config et donnent la même valeur pour AVGO aujourd'hui, mais je
   n'ai pas vérifié s'il existe un mécanisme (ailleurs dans le dépôt, non
   couvert par cet audit) qui modifierait `trailing_stop_loss.initial_stop`
   en cours de vie AVANT l'entrée (par opposition à son évolution
   post-entrée via le ratchet, hors périmètre de cette question) — si un tel
   mécanisme existait, ces trois lectures pourraient diverger entre elles.
6. **Q3 — mapping final vers l'objet `Order` IBKR**
   (`order_record_to_broker_request`, `app/broker/order_mapper.py`, déjà
   noté non ouvert dans `audit/02`) reste non vérifié dans ce lot non plus :
   je confirme seulement que `trigger_price`/`limit_price` de l'`OrderRecord`
   correspondent bien à `signal.entry_price` (via `risk_decision.trigger_price`)
   et à `worst_case_entry_price` respectivement, pas leur transformation
   finale en paramètres `Order` IBKR (`auxPrice`, `lmtPrice`).

---

## PRÉREQUIS NON SATISFAITS POUR LA CIBLE

Cible rappelée : `WAITING_ACTIVATION -> WAITING_RETEST -> WAITING_CONFIRMATION
-> ENTRY_READY`, confirmation sur bougie 15m CLOSE (`historical_bars[-2]`),
mémoire `retest_touched`.

1. **`historical_bars` n'est pas fiable à 100 % comme source de "la bougie
   15m close précédente".** Dans ~4,5 % des cotations observées en
   production (24 337/543 940), soit `historical_bars` contient des bougies
   **1 HEURE** silencieusement mélangées sous la même clé (repli `atr_1h`,
   confirmé empiriquement ici, pas seulement déduit), soit il est **vide**
   (échec total, cas AAOI). Aucun champ du snapshot ne permet de détecter
   ces deux états dégradés avant de lire `historical_bars[-2]` — une
   confirmation basée sur `historical_bars[-2]` sans garde explicite
   lirait, dans ce cas, soit une bougie vieille de plusieurs jours comme si
   elle venait de clôturer, soit provoquerait une exception. Un garde-fou
   (vérifier `len(historical_bars) >= 2` ET la fraîcheur/l'écart temporel
   entre bougies) est un prérequis, pas une option.
2. **Aucun accesseur `previous_close`/`previous_open`/`previous_low`
   n'existe** (seul `previous_high` existe, calqué sur `rows[-2]`) — lire la
   clôture de la bougie précédente exige de parcourir `historical_bars`
   directement, il n'y a pas de raccourci déjà câblé au niveau du quote pour
   ça.
3. **`setup_condition_states` (la mémoire persistée existante la plus
   proche d'un `retest_touched`) est structurellement invisible depuis
   `evaluate()`** : elle est écrite après le signal, dans un module séparé
   (`stock_market_monitor.py`), jamais relue dans `signal_engine.py` ni dans
   `app/setups/*.py`. La câbler dans la décision demanderait de modifier la
   signature de `evaluate()` (aujourd'hui `(snapshot, current_status) ->
   SetupSignal`, aucun troisième paramètre d'état persistant) — ce n'est pas
   un simple ajout de lecture, c'est un changement de contrat d'interface
   pour TOUS les `setup_type` (5 classes héritent de `BaseSetup.evaluate`).
4. **`signal.stop_loss` calculé par `evaluate()` est déjà, aujourd'hui,
   déconnecté du stop réellement transmis au broker** (trois relectures
   indépendantes de `trailing_stop_loss.initial_stop` en aval, jamais reliées
   à `signal.stop_loss`). Ajouter de la logique dans `evaluate()` qui
   suppose que `self.stop_loss` est LA valeur qui protégera la position
   reposerait sur une hypothèse déjà fausse dans le code actuel — ce
   découplage préexistant devrait être résolu (ou à tout le moins documenté
   comme accepté) avant/pendant la spec, indépendamment du sujet
   `retest_touched`.
5. **La state machine (`app/engine/state_machine.py`, table
   `ALLOWED_TRANSITIONS`) autorise déjà `WAITING_ACTIVATION ->
   WAITING_RETEST`, `WAITING_RETEST -> WAITING_CONFIRMATION`,
   `WAITING_CONFIRMATION -> ENTRY_READY`** (audit 02, POINT 3) — mais aucune
   des trois écritures directes de statut qui contournent la state machine
   (`OrderManager.place_entry_order`, `EntryOrderExecutor.execute_entry_ready`,
   `SetupLifecycleService.revalidate_and_apply`) ni `ActionExecutor` ne
   passent aujourd'hui par ces statuts intermédiaires pour `breakout_retest` :
   introduire 2 statuts intermédiaires supplémentaires dans le flux réel
   nécessite de vérifier, un par un, que chacun des points d'écriture directe
   de `setups.status` (qui ignorent `state_machine.transition()`, cf. audit
   02 POINT 3) reste cohérent — la table dit "autorisé", mais son
   application n'est pas universelle dans le code existant.
