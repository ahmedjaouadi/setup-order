# Audit en lecture seule — résolution des 4 points bloquants

Suite de `audit/01_boucle_evaluation.md`. Mode lecture seule : aucun fichier de
code n'a été modifié. Chaque affirmation est accompagnée d'une référence
fichier:ligne.

## POINT 1 — Sémantique des cotations (`quote["close"]`, `quote["open"]`, `quote["high"]`, `quote["price"]`)

### Le connecteur broker trouvé

Le dict `quote` consommé par `broker.market_snapshot(symbol, timeout=timeout)`
(`app/engine/stock_market_monitor.py:193`) est construit par
`IbAsyncTwsConnector.market_snapshot` — `app/broker/tws_connector.py:1268-1311`
(classe déclarée `app/broker/tws_connector.py:553`, implémentation réelle IBKR
via `ib_async`, à ne pas confondre avec `SimulatedBrokerConnector.market_snapshot`
— `app/broker/tws_connector.py:342-360` — utilisé seulement en mode simulé, ni
avec `BrokerConnector.market_snapshot` — `app/broker/tws_connector.py:241-247`
— l'implémentation abstraite par défaut qui renvoie `available: False`).

Le mode réellement actif est déterminé par `broker.market_data_source` —
configuré `"hybrid"` de manière identique par le défaut applicatif
(`app/broker/tws_connector.py:26`, dans `_default_market_data_policy` ou
équivalent — valeur par défaut du connecteur) et par `config.yaml:14`
(`market_data_source: "hybrid"`), donc c'est
`IbAsyncTwsConnector._hybrid_market_snapshot` —
`app/broker/tws_connector.py:1313-1349` — qui construit le dict effectivement
reçu par `poll_stock_symbol` en production (confirmé, pas déduit : c'est la
seule branche prise quand `self.market_data_source == "hybrid"`,
`app/broker/tws_connector.py:1283-1288`).

### Ce que contient chaque champ

`_hybrid_market_snapshot` combine 3 sources — `signal` (bougies 15 min
historiques), `live` (ticker temps réel `reqMktData`), `atr_1h` (bougies 1h
historiques) — via `_merge_hybrid_market_snapshot`
(`app/broker/tws_connector.py:2635-2752`). Le point clé :

```python
2651  base = dict(signal if signal_available else live if live_available else atr_1h)
...
2660  for key in ("bid", "ask", "last"):
2661      if live.get(key) not in (None, ""):
2662          base[key] = live[key]
...
2673  if live.get("price") not in (None, ""):
2674      base["price"] = live["price"]
```
(`app/broker/tws_connector.py:2651-2674`)

**`open`, `high`, `low`, `close` ne sont JAMAIS écrasés par `live`** dans
`_merge_hybrid_market_snapshot` (aucune clé `"open"`/`"high"`/`"low"`/`"close"`
dans la boucle de fusion lignes 2660-2704, ni dans le `base.update(...)` final
lignes 2716-2737). Ils restent donc ceux de `signal`, c.-à-d. de la dernière
bougie historique.

`signal` vient de :
```python
1320  signal = await self._historical_market_snapshot(
1321      symbol,
1322      contract,
1323      timeout=historical_timeout,
1324      duration=self.hybrid_signal_duration,
1325      bar_size=self.hybrid_signal_bar_size,
1326      cache_profile="hybrid_signal",
1327  )
```
(`app/broker/tws_connector.py:1320-1327`)

`hybrid_signal_duration` = `"5 D"`, `hybrid_signal_bar_size` = `"15 mins"` —
défauts (`app/broker/tws_connector.py:589-590`) **confirmés identiques** dans
`config.yaml:27-28`. `historical_use_rth` = `true`
(`app/broker/tws_connector.py:594`, `config.yaml:32`) — donc uniquement des
bougies de la séance normale (RTH), pas de pré/post-marché.

`_historical_market_snapshot` appelle
`self._ib.reqHistoricalDataAsync(contract, "", duration, bar_size, whatToShow, useRTH, 1, False)`
— `app/engine/tws_connector.py` réf. exacte `app/broker/tws_connector.py:1616-1625`
— `endDateTime=""` (maintenant), `keepUpToDate=False`. Le résultat est passé à
`_historical_quote_from_bars` :
```python
2553  latest = rows[-1]
2554  previous = rows[-2] if len(rows) > 1 else {}
...
2592  "open": latest.get("open"),
2593  "high": latest.get("high"),
2594  "low": latest.get("low"),
2595  "close": latest.get("close"),
```
(`app/broker/tws_connector.py:2553-2595`)

`rows[-1]` est **la dernière bougie de 15 minutes renvoyée par
`reqHistoricalDataAsync`**. Il ne s'agit pas d'un appel à `reqMktData`/tick
type direct : c'est un appel de données historiques en bougies (bars), donc la
notion de "tick type IBKR" ne s'applique pas ici — elle s'applique seulement à
la branche `live` (voir plus bas). Aucune ligne du code ne filtre ou n'exclut
explicitement la bougie en cours de formation (pas de test du type
`if bar.date == today and not bar.closed: drop`). Avec `endDateTime=""`
pendant les heures de marché, le comportement documenté de l'API IBKR pour
`reqHistoricalData` sur un `barSizeSetting` intrajournalier est que la
DERNIÈRE bougie renvoyée est la bougie EN COURS DE FORMATION (incomplète),
mise à jour à chaque nouvel appel — ceci n'est PAS vérifié par exécution dans
cet audit (pas d'accès à TWS/Gateway), c'est une déduction basée sur le
comportement documenté de l'API `reqHistoricalData` d'Interactive Brokers pour
ce pattern d'appel précis ; voir INCERTITUDES.

**Réponses précises :**
- `quote["close"]` = `close` de la dernière bougie de 15 minutes (RTH), très
  probablement la bougie EN COURS si l'appel a lieu pendant la séance
  (`app/broker/tws_connector.py:2595`, `2553`). **Ce n'est PAS la clôture de
  la veille**, ni une clôture "journalière" au sens d'une bougie 1 jour — bien
  que ce même champ soit ensuite recopié tel quel dans `MarketSnapshot.daily_close`
  côté `stock_market_monitor.py:516` (voir plus bas, incohérence de nommage).
- `quote["open"]` = `open` de cette même bougie 15 min (`app/broker/tws_connector.py:2592`).
- `quote["high"]` = `high` de cette même bougie 15 min (`app/broker/tws_connector.py:2593`).
- `quote["price"]` = le prix `live` (ticker `reqMktData`) s'il est disponible
  (`app/broker/tws_connector.py:2673-2674`), sinon replié sur `price` de la
  bougie historique = `close` de la même bougie 15 min
  (`app/broker/tws_connector.py:2588`, `_historical_quote_from_bars`).
  Le calcul du prix `live` lui-même : `_ticker_price`
  (`app/broker/tws_connector.py:2266-2286`) utilise `ticker.marketPrice()`
  (méthode `ib_async`, dérivée en interne du dernier tick LAST, ou à défaut du
  midpoint bid/ask), puis à défaut `last`/`close` du ticker, puis le midpoint
  bid/ask brut. Ce `close` de ticker (`_ticker_fields`,
  `app/broker/tws_connector.py:2261`, `getattr(ticker, "close", None)`) est
  alimenté par IBKR tick type **9 (CLOSE_PRICE)** — la clôture de la séance
  PRÉCÉDENTE — mais ce champ `close` du ticker n'est utilisé qu'en dernier
  recours pour `price`, **jamais** pour `quote["close"]`/`quote["open"]`/`quote["high"]`
  en mode hybrid (ces derniers viennent exclusivement de `signal`, voir
  ci-dessus). `quote["open"]` du ticker (tick type 14, OPEN_TICK = ouverture
  du jour) n'est également jamais utilisé en mode hybrid pour la même raison.

### Ces valeurs changent-elles pendant la séance ?

Oui. Le cache de la requête historique `signal` a une durée de vie
(`_historical_cache_ttl_seconds`, `cache_profile="hybrid_signal"`) de
`self.market_data_ttl["hybrid_signal_seconds"]` — défaut `20`
(`app/broker/tws_connector.py:2153`), **surchargé à `60` dans
`config.yaml:21`**. Donc au minimum toutes les 60 secondes, une nouvelle
requête `reqHistoricalDataAsync` est faite et `open`/`high`/`low`/`close`
sont recalculés à partir de la bougie 15 min la plus récente — qui, tant
qu'elle n'est pas terminée, voit son `close` bouger à chaque rafraîchissement,
et bascule sur une toute nouvelle bougie (nouveau `open`) toutes les 15
minutes. Ces valeurs ne sont donc **pas figées pour la journée** — ni une
clôture de veille immuable, ni une valeur calculée une seule fois au matin.

### Conséquence sur `bullish_confirmation`

```python
169  def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
170      if snapshot.bullish_candle:
171          return True
172      if snapshot.close is not None and snapshot.open is not None:
173          return snapshot.close > snapshot.open
174      return False
```
(`app/setups/base_setup.py:169-174`)

`snapshot.bullish_candle` n'est jamais renseigné par `quote_to_market_snapshot`
(`app/engine/stock_market_monitor.py:492-591` — champ absent de la liste des
assignations, confirmé par audit 1 section 3), donc toujours `False` par
défaut (`app/models.py:227`). En production, `bullish_confirmation` compare
donc systématiquement `close > open` **de la même bougie de 15 minutes en
cours de formation** (pas une bougie déjà close, pas une comparaison
jour-vs-veille).

Son résultat **peut changer plusieurs fois dans la même séance** :
- à chaque rafraîchissement du cache `hybrid_signal` (au moins toutes les 60s
  pendant que la bougie 15 min courante se remplit, le `close` de la bougie en
  formation évolue tick par tick au niveau IBKR même si le connecteur ne le
  relit qu'à cette fréquence-là) ;
- à chaque nouvelle bougie de 15 minutes (`open` change, `close` repart de
  l'`open`, la comparaison peut basculer de vrai à faux ou l'inverse) ;
- il n'est donc **pas constant sur la journée** — un même tick de 15h peut
  être haussier, puis le prochain relevé (15-60s plus tard ou 15 min plus
  tard) redevenir non-haussier si le prix recule sous l'open de la bougie
  courante.

### Note annexe — incohérence de nommage confirmée

`app/engine/stock_market_monitor.py:505` et `:516` :
```python
505  close=float_value(quote.get("close")) or price,
...
516  daily_close=float_value(quote.get("close")) or price,
```
`close` et `daily_close` du `MarketSnapshot` sont bien la **même valeur brute**
issue de la **même bougie de 15 minutes**, jamais d'une bougie journalière
réelle — ce que l'audit 1 avait repéré sans pouvoir le confirmer côté broker
(section 3, "Confirmation du breakout journalier"). C'est maintenant confirmé
par lecture directe du connecteur : le nom `daily_close` est trompeur, la
condition "Confirmation du breakout journalier"
(`app/setups/breakout_retest.py:70-78`) est en réalité évaluée contre la
clôture de la dernière bougie 15 minutes, pas contre une clôture journalière.

---

## POINT 2 — Quel prix part chez IBKR

### Chemin de code lu

`app/engine/order_manager.py`, méthode `OrderManager.place_entry_order`
(`app/engine/order_manager.py:64-204`), appelée depuis
`app/engine/entry_order_executor.py:263` :
```python
263  await self.order_manager.place_entry_order(effective_setup, decision)
```
où `decision` est le `RiskDecision` produit juste avant par
`self.risk_engine.evaluate(...)` (`app/engine/entry_order_executor.py:194-201`) :
```python
194  decision = self.risk_engine.evaluate(
195      setup_config=effective_setup["config"],
196      entry_price=signal.entry_price,
197      stop_loss=trailing_stop,
198      open_positions=open_positions,
199      current_exposure_usd=exposure,
200      daily_pnl_usd=daily_pnl,
201  )
```
`signal.entry_price` est bien celui calculé par `BreakoutRetestSetup.evaluate()`
= `round(reference_high + trigger_offset, 2)` (`app/setups/breakout_retest.py:89`,
`reference_high = snapshot.high or snapshot.price` ligne 83) — **pas** un champ
de config lu directement à cet endroit.

### `RiskEngine.evaluate` — transformation du prix

`app/engine/risk_engine.py:68-163`. Étapes pertinentes :
```python
100  worst_case_entry_price = self.worst_case_entry_price(setup_config, entry_price)
```
```python
52  @staticmethod
53  def worst_case_entry_price(
54      setup_config: dict[str, Any],
55      trigger_price: float,
56  ) -> float:
57      entry = setup_config.get("entry", {})
58      if not isinstance(entry, dict):
59          return trigger_price
60      if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
61          return trigger_price
62      if entry.get("maximum_limit_price") is not None:
63          return float(entry["maximum_limit_price"])
64      if entry.get("limit_price") is not None:
65          return float(entry["limit_price"])
66      return trigger_price + float(entry.get("limit_offset", 0.0) or 0.0)
```
(`app/engine/risk_engine.py:52-66`)

Et à la fin :
```python
154  return RiskDecision(
155      approved=True,
156      reason="Risk approved",
157      quantity=quantity,
158      entry_price=round(worst_case_entry_price, 4),
159      stop_loss=stop_loss,
...
162      trigger_price=entry_price,
163  )
```
(`app/engine/risk_engine.py:154-163` — `entry_price` ici est le paramètre
d'entrée de la fonction = `signal.entry_price` dynamique, pas la config.)

Donc :
- `risk_decision.trigger_price` = `signal.entry_price` (calculé par
  `evaluate()`), **toujours**, quelle que soit la config.
- `risk_decision.entry_price` = `worst_case_entry_price` = **la config**
  `entry.maximum_limit_price` si présent, sinon `entry.limit_price` si
  présent, sinon `signal.entry_price + entry.limit_offset`.

### `OrderManager._entry_order_prices` — ce qui part réellement au broker

```python
206  @staticmethod
207  def _entry_order_prices(
208      order_type: str,
209      risk_decision: RiskDecision,
210      limit_offset: float,
211  ) -> tuple[float | None, float | None, float | None]:
218      trigger_price = (
219          risk_decision.trigger_price
220          if risk_decision.trigger_price is not None
221          else risk_decision.entry_price
222      )
...
227      if order_type == OrderType.STP_LMT.value:
228          limit_price = round(
229              (
230                  risk_decision.entry_price
231                  if risk_decision.trigger_price is not None
232                  else trigger_price + limit_offset
233              ),
234              2,
235          )
236          return trigger_price, limit_price, None
```
(`app/engine/order_manager.py:206-237`)

Pour un `order_type == "STP_LMT"` (cas AVGO) et puisque
`risk_decision.trigger_price` n'est jamais `None` (toujours = `signal.entry_price`) :
- **`trigger_price` transmis au broker = `risk_decision.trigger_price` =
  `signal.entry_price`** = `round(reference_high + trigger_offset, 2)`, calculé
  à chaque tick par `BreakoutRetestSetup.evaluate()`.
- **`limit_price` transmis au broker = `risk_decision.entry_price`** =
  `worst_case_entry_price(setup_config, ...)`.

Ces deux prix sont ensuite encapsulés dans un `OrderRecord`
(`app/engine/order_manager.py:100-112`, `trigger_price=trigger_price`,
`limit_price=limit_price`) puis transmis au broker via
`self.broker.submit_order(order_record_to_broker_request(order, transmit=False))`
(`app/engine/order_manager.py:113-115`) — je n'ai pas retracé
`order_record_to_broker_request` ni le mapping IBKR final (`app/broker/order_mapper.py`),
mais l'`OrderRecord` transmis à cette fonction porte déjà les valeurs numériques
ci-dessus ; voir INCERTITUDES.

### Réponse directe sur AVGO (`trigger_price=368.5`, `limit_price=371`)

Grep exhaustif de `entry.get("trigger_price")` / `entry["trigger_price"]` dans
tout le dépôt (hors tests) :
```
app/setups/breakout_retest.py:29    if entry.get("trigger_price") is not None:
app/setups/breakout_retest.py:30        return float(entry["trigger_price"])
app/setups/momentum_breakout.py:15-16   (même pattern, autre setup_type)
app/setups/aggressive_rebound.py:26-27  (idem)
app/setups/range_breakout.py:12-13      (idem)
app/setups/pullback_continuation.py:12-13 (idem)
app/setups/base_setup.py:110            explicit = entry.get("entry_price") or entry.get("trigger_price")
app/opportunities/opportunity_to_scenario_mapper.py:142
app/opportunities/shortlist_service.py:144
app/setups/creation_snapshot_service.py:40
app/intelligence/semantic_validation_service.py:260
```
Pour `breakout_retest`, le seul lecteur de `entry.trigger_price` sur le chemin
d'exécution des setups est `BreakoutRetestSetup.estimated_entry_price`
(`app/setups/breakout_retest.py:27-37`) :
```python
27  def estimated_entry_price(self) -> float | None:
28      entry = self.config.get("entry", {})
29      if entry.get("trigger_price") is not None:
30          return float(entry["trigger_price"])
```
Cette méthode **n'est appelée ni par `evaluate()` ni par `place_entry_order`**.
Ses seuls appelants trouvés par grep (`estimated_entry_price`) sont
`BaseSetup.validate()` (`app/setups/base_setup.py:100-105`, via
`worst_case_entry_price()` ligne 131-132) et `BaseSetup.maximum_limit_price()`
(`app/setups/base_setup.py:118-129`) — tous deux utilisés **au chargement /
validation du setup** (contrôle que le stop est bien sous le prix d'entrée
estimé), **pas** dans la boucle de décision live ni dans
`OrderManager.place_entry_order`. Les autres lecteurs (`opportunity_to_scenario_mapper.py`,
`shortlist_service.py`, `creation_snapshot_service.py`,
`semantic_validation_service.py`) appartiennent aux modules d'opportunités /
scoring / création de setup, pas au chemin d'ordre réel.

**Conclusion factuelle** : pour AVGO,
- `entry.trigger_price = 368.5` (config) **n'est lu par aucun code du chemin
  d'exécution réel de l'ordre** — il sert uniquement de garde-fou de validation
  au chargement (`estimated_entry_price()` doit être non-`None` et > stop) et
  d'estimation d'affichage/scoring ailleurs. Le trigger réellement envoyé au
  broker est `signal.entry_price` recalculé à chaque tick par `evaluate()`
  (`round(reference_high + trigger_offset, 2)` — donc pour l'exemple chiffré de
  l'audit 1, `365.5`, pas `368.5`).
- `entry.limit_price = 371` (config) **est lu**, mais seulement comme repli
  dans `RiskEngine.worst_case_entry_price` (`app/engine/risk_engine.py:64-65`)
  et `BaseSetup.maximum_limit_price` (`app/setups/base_setup.py:127-128`) —
  et ce repli n'est **jamais atteint pour AVGO** car `entry.maximum_limit_price
  = 371` est également défini dans la config AVGO et a priorité (ligne 62-63 /
  125-126). Donc c'est `maximum_limit_price` (valeur identique ici, `371`) qui
  fixe réellement `limit_price` de l'ordre transmis, `limit_price` n'intervenant
  que comme valeur de repli théorique si `maximum_limit_price` était absent.

### `SetupStatus` écrit après transmission réussie

```python
180  self.repository.update_setup_status(
181      setup["setup_id"],
182      SetupStatus.ENTRY_ORDER_PLACED.value,
183      "Bracket order submitted",
184  )
```
(`app/engine/order_manager.py:180-184`, atteint seulement après soumission
acceptée de l'ordre parent — ligne 121-124 — et soumission réussie du stop
protecteur — ligne 174-179.) Confirmé par lecture directe, pas déduit du nom
de l'enum.

Notes : cet appel **contourne** `ActionExecutor.transition_setup` /
`StateMachine.transition` — voir POINT 3 — il écrit directement via
`self.repository.update_setup_status`.

En cas de rejet broker de l'ordre parent : `SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW`
(`app/engine/order_manager.py:126-130`), également en écriture directe.

### Protection anti-doublon

```python
73  protection = self.repository.protection_snapshot_for_setup(setup["setup_id"])
74  if protection.get("position_open") and not protection.get("has_active_stop_order"):
75      raise UnprotectedActiveOrderError(
76          "An open position exists without an active protective stop order"
77      )
78  if protection.get("active_entry_order_id"):
79      if not protection.get("has_active_stop_order"):
80          raise UnprotectedActiveOrderError(
81              "An active entry order exists without an attached protective stop order"
82          )
83      raise DuplicateOrderError("An active protected order already exists for this setup")
```
(`app/engine/order_manager.py:73-83`, tout début de `place_entry_order`, avant
toute soumission au broker.)

`DuplicateOrderError` est levée précisément quand
`protection.get("active_entry_order_id")` est vrai **et**
`protection.get("has_active_stop_order")` est vrai — c.-à-d. quand un ordre
d'entrée déjà actif ET protégé par un stop existe pour ce `setup_id`. Elle est
catchée dans l'appelant `EntryOrderExecutor.execute_entry_ready`
(`app/engine/entry_order_executor.py:285-292`), qui enregistre un événement
`duplicate_order_blocked` et retourne `True` sans lever plus loin.

Effet pratique sur un `ENTRY_READY` répété à chaque tick : d'après le POINT 4
ci-dessous, le statut du setup en base **reste `WAITING_ENTRY_SIGNAL`** tant
qu'aucun ordre n'a été transmis avec succès — `evaluate()` continue donc à
réémettre `ENTRY_READY` à chaque tick où `in_retest and bullish_confirmation`
restent vrais. Chaque tentative repasse par `place_entry_order`, qui relit
`protection_snapshot_for_setup` à chaque appel : tant qu'aucun ordre broker
n'a été effectivement accepté (pas de `active_entry_order_id`), aucune
`DuplicateOrderError` n'est levée et une nouvelle tentative de soumission a
lieu à CHAQUE tick où la condition reste vraie (pas de fenêtre de
déduplication au niveau `order_manager`). Une fois qu'un ordre a été soumis
avec succès (bracket complet, ligne 180-184), le statut passe à
`ENTRY_ORDER_PLACED` — à ce moment-là, `evaluate()` ne réémet plus `ENTRY_READY`
(seules les branches `WAITING_ACTIVATION`/`WAITING_ENTRY_SIGNAL` sont testées,
`app/setups/breakout_retest.py:71,80` ; `ENTRY_ORDER_PLACED` tombe sur la ligne
94, `HOLD`) — donc la protection anti-doublon de `order_manager.py:78-83` n'est
en pratique un filet de sécurité que pour la fenêtre étroite entre soumission
du parent et retour du callback (ou en cas d'échec partiel de la pose du stop,
scénario `UnprotectedActiveOrderError`).

---

## POINT 3 — La machine à états existante

Fichier lu en entier : `app/engine/state_machine.py` (350 lignes).

### Table complète des transitions autorisées

`ALLOWED_TRANSITIONS: dict[SetupStatus, set[SetupStatus]]`
(`app/engine/state_machine.py:8-201`) :

| Depuis | Vers (autorisé) |
|---|---|
| `DRAFT` | `LOADED`, `CANCELLED` |
| `LOADED` | `VALIDATED`, `ERROR` |
| `VALIDATED` | `WAITING_ACTIVATION`, `RECONCILING_EXISTING_POSITION`, `WAITING_ENTRY_SIGNAL`, `DISABLED`, `CANCELLED`, `ERROR` |
| `DISABLED` | `WAITING_ACTIVATION`, `RECONCILING_EXISTING_POSITION`, `CANCELLED` |
| `WAITING_ACTIVATION` | `WAITING_BREAKOUT`, `MISSED_BREAKOUT`, `MISSED_BREAKOUT_WAIT_RETEST`, `STALE_SETUP`, `BLOCKED`, `WAITING_RETEST`, `WAITING_REBOUND`, `WAITING_CONFIRMATION`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `BLOCKED` | `WAITING_ACTIVATION`, `RECONCILING_EXISTING_POSITION`, `MISSED_BREAKOUT_WAIT_RETEST`, `STALE_SETUP`, `DISABLED`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `STALE_SETUP` | `WAITING_ACTIVATION`, `MISSED_BREAKOUT_WAIT_RETEST`, `BLOCKED`, `DISABLED`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `MISSED_BREAKOUT_WAIT_RETEST` | `WAITING_ACTIVATION`, `WAITING_RETEST`, `REARMED_ON_NEW_BASE`, `STALE_SETUP`, `BLOCKED`, `DISABLED`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `WAITING_BREAKOUT` | `MISSED_BREAKOUT`, `WAITING_RETEST`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `MISSED_BREAKOUT` | `WAITING_RETEST`, `REARMED_ON_NEW_BASE`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `WAITING_RETEST` | `WAITING_CONFIRMATION`, `REARMED_ON_NEW_BASE`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `REARMED_ON_NEW_BASE` | `ENTRY_READY`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `WAITING_REBOUND` | `WAITING_CONFIRMATION`, `WAITING_ENTRY_SIGNAL`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `WAITING_CONFIRMATION` | `ENTRY_READY`, `WAITING_ENTRY_SIGNAL`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `WAITING_ENTRY_SIGNAL` | `ENTRY_READY`, `ENTRY_ORDER_PLACED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `ENTRY_READY` | `ENTRY_ORDER_PLACED`, `INVALIDATED`, `CANCELLED`, `ERROR` |
| `ENTRY_ORDER_PLACED` | `ENTRY_PARTIALLY_FILLED`, `ENTRY_FILLED`, `CANCELLED`, `ERROR` |
| `ENTRY_PARTIALLY_FILLED` | `ENTRY_FILLED`, `CANCELLED`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `ENTRY_FILLED` | `STOP_ORDER_PLACED`, `STOP_PLACED`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `STOP_ORDER_PLACED` | `IN_POSITION`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `STOP_PLACED` | `IN_POSITION`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `RECONCILING_EXISTING_POSITION` | `IN_POSITION`, `BLOCKED`, `INVALIDATED`, `MANUAL_REVIEW_REQUIRED`, `ERROR_REQUIRES_MANUAL_REVIEW`, `CANCELLED` |
| `IN_POSITION` | `MANAGING_POSITION`, `PARTIAL_EXIT`, `CLOSED`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `MANAGING_POSITION` | `PARTIAL_EXIT`, `CLOSED`, `ERROR`, `MANUAL_REVIEW_REQUIRED` |
| `PARTIAL_EXIT` | `MANAGING_POSITION`, `CLOSED`, `ERROR` |
| `CLOSED` | *(aucune, ensemble vide)* |
| `EXPIRED` | *(aucune)* |
| `INVALIDATED` | *(aucune)* |
| `CANCELLED` | *(aucune)* |
| `MANUAL_REVIEW_REQUIRED` | `CANCELLED`, `ERROR`, `ERROR_REQUIRES_MANUAL_REVIEW` |
| `ERROR_REQUIRES_MANUAL_REVIEW` | `CANCELLED`, `MANUAL_REVIEW_REQUIRED` |
| `ERROR` | `CANCELLED`, `MANUAL_REVIEW_REQUIRED` |

Règle transversale supplémentaire, appliquée AVANT la table ci-dessus dans
`explain_transition` : si `setup_role == MANAGEMENT_ONLY` et que la cible fait
partie de `ENTRY_FLOW_STATUSES` (liste `app/engine/state_machine.py:217-236`,
qui inclut `ENTRY_READY`), la transition est refusée quel que soit le contenu
de la table (`app/engine/state_machine.py:276-282`). `current == target` est
toujours autorisé (ligne 283-289, no-op explicite).

### Qui appelle `transition()` / `explain_transition()` — table réellement appliquée ou seulement par endroits ?

Grep exhaustif de `state_machine.transition(`, `.explain_transition(`,
`.can_transition(` dans `app/` :
```
app/engine/action_executor.py:49       self.state_machine.transition(current_status, target_status)
app/engine/position_action_executor.py:58   self.state_machine.transition(current_status, target_status)
app/engine/setup_lifecycle_service.py:410   self.state_machine.explain_transition(current_enum, target_enum, role)
```
Trois points d'appel seulement :
1. `ActionExecutor.transition_setup` (`app/engine/action_executor.py:41-60`) —
   appelle `.transition()` puis, si acceptée, écrit via
   `self.repository.update_setup_status(...)` (ligne 60). C'est le SEUL appelant
   de `ActionExecutor.transition_setup`, lui-même appelé uniquement par
   `execute_simple_action` pour `STATUS_CHANGE` et `INVALIDATE`
   (`app/engine/action_executor.py:33-38`).
2. `PositionActionExecutor` (`app/engine/position_action_executor.py:58`) —
   chemin de gestion de position (raise stop etc.), hors périmètre entrée.
3. `SetupLifecycleService.revalidate_and_apply`
   (`app/engine/setup_lifecycle_service.py:390-421`) — appelle seulement
   `.explain_transition()` (pas `.transition()`) comme garde avant d'écrire
   directement via `self.repository.update_setup_status(...)` (ligne 415-421)
   si `decision.allowed` est vrai (ligne 411-414 : sinon, log + pas d'écriture
   de statut, seulement `update_setup_revalidation`).

**La table N'EST PAS appliquée de façon universelle.** Deux écritures directes
de statut CONTOURNENT complètement la state machine (aucun appel à
`transition`/`explain_transition` avant elles) :
- `OrderManager.place_entry_order` — `self.repository.update_setup_status(...)`
  aux lignes `app/engine/order_manager.py:126-130` (`ERROR_REQUIRES_MANUAL_REVIEW`
  sur rejet broker) et `180-184` (`ENTRY_ORDER_PLACED` sur succès du bracket) —
  aucun appel à `self.state_machine` dans tout le fichier `order_manager.py`
  (confirmé par grep, aucune occurrence de `state_machine` dans ce fichier).
- `EntryOrderExecutor.execute_entry_ready` —
  `self.repository.update_setup_status(...)` à
  `app/engine/entry_order_executor.py:121-125` (blocage `MANAGEMENT_ONLY`,
  cible `ERROR_REQUIRES_MANUAL_REVIEW`) — même constat, aucune référence à
  `state_machine` dans `entry_order_executor.py`.

Ces deux écritures directes restent **cohérentes** avec la table (leurs
cibles — `ENTRY_ORDER_PLACED` depuis `WAITING_ENTRY_SIGNAL`,
`ERROR_REQUIRES_MANUAL_REVIEW` depuis `RECONCILING_EXISTING_POSITION` ou via
`MANAGEMENT_ONLY`... — figurent dans les ensembles autorisés dans la plupart
des cas observés), mais ce n'est pas VÉRIFIÉ par le code à l'exécution : rien
n'empêcherait une future modification de ces deux fichiers d'écrire une
transition que `ALLOWED_TRANSITIONS` interdirait, sans qu'aucune exception ne
soit levée.

`transition_setup`, `app/engine/action_executor.py:41-60` — la question posée
("passe-t-il par elle ?") : **oui pour `STATUS_CHANGE`/`INVALIDATE`, non pour
`ENTRY_READY`.** `execute_simple_action` (`app/engine/action_executor.py:25-39`)
ne gère explicitement que trois branches (`HOLD`, `INVALIDATE`,
`STATUS_CHANGE`, lignes 31-38) et retourne `False` pour tout le reste, y
compris `ENTRY_READY` (ligne 39, chemin par défaut). `ENTRY_READY` est routé
ailleurs, vers `EntryOrderExecutor.execute_entry_ready`
(`app/engine/trading_engine.py:2463-2470`, confirmé en audit 1 section 2), qui
lui-même n'appelle jamais `transition_setup` ni `state_machine` (grep déjà
cité : aucune occurrence). Donc l'entrée de table
`WAITING_ENTRY_SIGNAL -> ENTRY_READY` (ligne 120-126) n'est, en pratique,
**jamais exercée par un appel réel à `.transition()`** sur le chemin de
production de `breakout_retest` : rien n'écrit jamais littéralement le statut
`ENTRY_READY` en base (voir POINT 4).

### `WAITING_ENTRY_SIGNAL -> ENTRY_READY` est-il autorisé par la table ?

Oui, explicitement listé : `app/engine/state_machine.py:120-121`
(`SetupStatus.WAITING_ENTRY_SIGNAL: {SetupStatus.ENTRY_READY, ...}`). Autorisé
en théorie, mais jamais invoqué en pratique (voir ci-dessus et POINT 4).

### Contexte/payload par setup ?

Non. `StateMachine` (`app/engine/state_machine.py:260-340`) ne définit aucun
`__init__` et ne stocke aucun état d'instance : c'est une classe sans attributs
d'instance, dont toutes les méthodes (`can_transition`, `explain_transition`,
`transition`, `next_statuses`, `is_terminal`, `requires_manual_review`,
`is_entry_flow_status`, `is_position_status`) sont des fonctions pures
paramétrées par `current`, `target`, `setup_role` — aucun de ces paramètres
n'est conservé entre deux appels. Toutes les données réelles par setup (statut
courant, prix, config...) vivent exclusivement dans la table SQLite `setups`
via `TradingRepository`, jamais dans l'objet `StateMachine` lui-même (une
seule instance `self.state_machine = StateMachine()` est créée au démarrage du
moteur, `app/engine/trading_engine.py:175`, et réutilisée pour tous les
setups). La machine à états ne porte donc qu'une fonction de validation
`(depuis, vers) -> autorisé/refusé`, pas un état ni un payload.

---

## POINT 4 — Contradiction dans l'audit précédent

### Tranchage par le code

`ActionExecutor.execute_simple_action` — `app/engine/action_executor.py:25-39` :
```python
25  def execute_simple_action(
26      self,
27      setup: dict[str, Any],
28      current_status: SetupStatus,
29      signal: Any,
30  ) -> bool:
31      if signal.action == SignalAction.HOLD:
32          return True
33      if signal.action == SignalAction.INVALIDATE and signal.target_status:
34          self.transition_setup(setup, current_status, signal.target_status, signal.reason)
35          return True
36      if signal.action == SignalAction.STATUS_CHANGE and signal.target_status:
37          self.transition_setup(setup, current_status, signal.target_status, signal.reason)
38          return True
39      return False
```
Pour `signal.action == SignalAction.ENTRY_READY` : aucune des trois conditions
(lignes 31, 33, 36) n'est vraie -> la fonction tombe sur `return False` (ligne
39) **sans jamais appeler `self.transition_setup`**. `transition_setup`
(`app/engine/action_executor.py:41-60`) — et donc
`self.repository.update_setup_status(...)` en son sein (ligne 60) — **n'est
appelé pour aucun signal `ENTRY_READY`**, point final.

**Réponse à la question 1** : Quand `evaluate()` renvoie `action=ENTRY_READY`
et qu'un garde-fou bloque ensuite la transmission, **le statut en base n'est
PAS modifié**. Preuve complète, ligne par ligne :
1. `execute_simple_action` retourne `False` pour `ENTRY_READY`
   (`app/engine/action_executor.py:39`) — aucune écriture ici.
2. `TradingEngine._handle_signal` route alors vers
   `self.entry_order_executor.execute_entry_ready(setup, signal)`
   (comportement documenté en audit 1 section 2, ligne d'appel
   `app/engine/trading_engine.py:2469` d'après le arbre d'appel déjà tracé).
3. Dans `EntryOrderExecutor.execute_entry_ready`
   (`app/engine/entry_order_executor.py:50-305`), les garde-fous "système"
   (session policy ligne 58-78, fenêtre d'exécution ligne 80-93, trade_guards
   ligne 95-110, auto-exécution désactivée ligne 128-146, entrée manquant
   prix/trailing stop ligne 153-186), risque (`risk_engine.evaluate` rejeté
   ligne 202-210), coûts (`cost_gate` NO_GO ligne 218-231), broker reality
   (ligne 242-260) **enregistrent tous un événement via `self.event_store.record(...)`
   puis font `return True` immédiatement — aucun n'appelle
   `self.repository.update_setup_status(...)`.** Seules DEUX branches de ce
   fichier écrivent un statut : le blocage `MANAGEMENT_ONLY`
   (ligne 121-125, cible `ERROR_REQUIRES_MANUAL_REVIEW`) et le blocage
   lifecycle (`_lifecycle_allows_transmission`, ligne 307-350, qui appelle
   `self.lifecycle_service.revalidate_and_apply(setup)` ligne 330 — un chemin
   séparé, indépendant du signal `ENTRY_READY` lui-même, déclenché seulement
   si le statut de lifecycle recalculé tombe dans
   `{INVALIDATED, EXPIRED, STALE_SETUP, MISSED_BREAKOUT_WAIT_RETEST, BLOCKED}`,
   ligne 319-325).
4. Pour un garde-fou "ordinaire" (session, trade_guards, risque, coûts, broker
   reality, trailing stop pas prêt) : aucune de ces lignes ne touche
   `setups.status`. Le statut reste donc exactement celui lu en tout début de
   cycle par `SignalEngine.evaluate_snapshot` — `current_status = SetupStatus(setup["status"])`
   (`app/engine/signal_engine.py:74`) — c.-à-d. `WAITING_ENTRY_SIGNAL` dans le
   cas qui nous intéresse (seul statut, avec `WAITING_ACTIVATION`, sous lequel
   `evaluate()` peut produire `ENTRY_READY`).

**La section 4 de l'audit 1 était donc FAUSSE** sur ce point précis :
`"le target_status ENTRY_READY est quand même persisté via transition_setup"`
ne correspond à aucune ligne de code — `transition_setup` n'est jamais appelé
avec `ENTRY_READY`. La section 6 (sous-cas 2a), qui supposait implicitement
que le statut passait à autre chose que `WAITING_ENTRY_SIGNAL` après un
`ENTRY_READY` bloqué, était la version correcte des deux, mais pour la
mauvaise raison (elle supposait une écriture de statut via
`order_manager.place_entry_order` même en cas de blocage par garde-fou
"ordinaire" — ce qui n'est pas le cas : `place_entry_order` n'est même pas
appelé si un garde-fou de `entry_order_executor.py` bloque avant, cf. ligne
262-263, dans le bloc `try` qui ne s'exécute qu'après TOUS les `return True`
précédents).

**Précision additionnelle** : le champ `signal.target_status = SetupStatus.ENTRY_READY`
fixé par `BreakoutRetestSetup.evaluate()` (`app/setups/breakout_retest.py:88`)
n'est donc utilisé QUE pour l'affichage / les logs — il est recopié tel quel
dans `processed["target_status"]` (`app/engine/signal_engine.py:104-106`,
exposé dans l'événement `stock_analysis` et potentiellement une route API de
lecture), mais **jamais transformé en écriture SQL**. La valeur littérale
`SetupStatus.ENTRY_READY` n'apparaît nulle part comme statut effectivement
écrit dans `setups.status` sur le chemin de `breakout_retest` (grep de
`update_setup_status` dans tout `app/engine/` : seuls
`ActionExecutor.transition_setup`, `OrderManager.place_entry_order`
[`ENTRY_ORDER_PLACED`/`ERROR_REQUIRES_MANUAL_REVIEW`],
`EntryOrderExecutor.execute_entry_ready` [`ERROR_REQUIRES_MANUAL_REVIEW`] et
`SetupLifecycleService.revalidate_and_apply` [statuts de lifecycle] écrivent
ce champ — aucun n'écrit `ENTRY_READY`).

### Que se passe-t-il au tick suivant ?

Le setup **reste en `WAITING_ENTRY_SIGNAL` et réévalue normalement** — il
n'est PAS figé. Preuve :
- `SignalEngine.evaluate_snapshot` relit `setup["status"]` depuis le
  repository à chaque appel (`app/engine/signal_engine.py:70-74`), donc relit
  `WAITING_ENTRY_SIGNAL` puisque rien ne l'a changé (démontré ci-dessus).
- `BreakoutRetestSetup.evaluate()` reprend alors la branche
  `current_status == SetupStatus.WAITING_ENTRY_SIGNAL`
  (`app/setups/breakout_retest.py:80-92`), recalcule `in_retest` et
  `bullish_confirmation` à partir du NOUVEAU snapshot du tick courant (aucune
  mémoire de la tentative précédente, cf. audit 1 section 4) :
  - si les conditions restent vraies : `ENTRY_READY` est réémis avec un
    `entry_price` recalculé (potentiellement différent, car `reference_high`
    et `trigger_offset` dépendent du nouveau `snapshot.high`/`snapshot.price`)
    — une nouvelle tentative de transmission a lieu via
    `EntryOrderExecutor.execute_entry_ready`, qui repasse par tous les
    garde-fous depuis le début ;
  - si `in_retest` devient faux (prix sorti de la zone) : `HOLD` ("Waiting for
    retest confirmation", ligne 92) ;
  - si `close < no_close_below` : `INVALIDATE` (ligne 64-68), qui CETTE FOIS
    passe bien par `execute_simple_action` -> `transition_setup` -> écriture
    réelle du statut `INVALIDATED`.

Donc : **le setup n'est jamais figé** par un `ENTRY_READY` bloqué — c'est au
contraire un garde-fou strictement sans mémoire (aucune trace de la tentative
précédente n'est conservée par ce mécanisme précis), qui retente
inconditionnellement à chaque tick tant que `in_retest and bullish_confirmation`
restent vrais, indépendamment du fait que la tentative précédente ait été
bloquée par un garde-fou "soft" (session, coûts, risque...). Seule la
protection `DuplicateOrderError`/`UnprotectedActiveOrderError` de
`OrderManager.place_entry_order` (POINT 2) empêche une double soumission
RÉELLE au broker, et seulement une fois qu'un premier ordre a déjà été accepté
(`active_entry_order_id` non vide) — ce qui, par construction, n'arrive
justement pas dans le scénario "bloqué par un garde-fou avant
`place_entry_order`", puisque `place_entry_order` n'est alors même pas appelé.

---

## INCERTITUDES RÉSIDUELLES

1. **Comportement exact de `reqHistoricalDataAsync` avec `endDateTime=""`
   pendant les heures de marché** (POINT 1) : je n'ai pas d'accès à une
   session TWS/Gateway vivante pour observer empiriquement si la dernière
   bougie renvoyée est bien la bougie 15 min EN COURS DE FORMATION (comme le
   comportement documenté de l'API IBKR le suggère fortement pour ce pattern
   d'appel) ou déjà une bougie 15 min CLOSE avec un léger décalage. Le code ne
   contient aucun filtre explicite qui trancherait la question par lecture
   seule. Cela ne change pas la conclusion qualitative (les valeurs viennent
   d'une bougie 15 min, pas d'une clôture de veille ni d'une bougie
   journalière), mais affecte la précision temporelle exacte ("bougie en
   cours" vs "dernière bougie close, à quelques secondes près").

2. **`ticker.marketPrice()` — implémentation exacte côté `ib_async`.** Je n'ai
   pas ouvert le code source de la librairie `ib_async` elle-même (hors du
   dépôt applicatif) pour confirmer sa logique interne de repli
   (bid/ask/last/close) ; je me suis appuyé sur le comportement documenté de
   cette méthode dans l'écosystème `ib_insync`/`ib_async`. Ceci n'affecte que
   le champ `price` en mode `live` de la fusion hybride, pas `open`/`high`/`close`
   qui viennent exclusivement de la branche historique (point établi par
   lecture directe du dépôt, indépendant de ce point).

3. **`order_record_to_broker_request` et le mapping IBKR final**
   (`app/broker/order_mapper.py`) — non ouvert dans cet audit (POINT 2). Je
   n'ai donc pas vérifié que `trigger_price`/`limit_price` de l'`OrderRecord`
   sont bien transmis sans transformation supplémentaire à l'objet `Order`
   IBKR final (`auxPrice`, `lmtPrice`, etc.) — seule la construction de
   l'`OrderRecord` en amont a été vérifiée avec certitude.

4. **Cohérence de la table `ALLOWED_TRANSITIONS` avec les écritures directes
   qui la contournent** (POINT 3) — j'ai vérifié que les cibles utilisées par
   `OrderManager.place_entry_order` et `EntryOrderExecutor.execute_entry_ready`
   figurent dans les ensembles autorisés pour les couples `(current, target)`
   les plus probables en production, mais je n'ai pas énuméré tous les
   `current_status` possibles au moment de ces appels pour garantir qu'aucun
   cas limite ne produirait une transition que la table interdirait
   silencieusement (puisqu'aucune exception ne serait de toute façon levée,
   ces deux chemins ne consultant jamais `state_machine`).

5. **Fraîcheur effective de `hybrid_signal_seconds=60` vs la fréquence réelle
   de poll (15s, cf. audit 1)** — le cache empêche un nouvel appel
   `reqHistoricalDataAsync` avant 60s, mais je n'ai pas vérifié empiriquement
   la cadence réelle observée en production (logs), seulement la logique de
   code (`_historical_cache_ttl_seconds`, `app/broker/tws_connector.py:1783-1800`).

Tout le reste affirmé dans ce document est appuyé par une lecture directe du
fichier et de la ligne citée, dans ce dépôt, à l'état où il se trouvait au
moment de l'audit (branche `feat/setup-conditions`).
