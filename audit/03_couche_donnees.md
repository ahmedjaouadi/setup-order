# Audit en lecture seule — Lot 2 : la couche données

Suite de `audit/01_boucle_evaluation.md` et `audit/02_points_bloquants.md`. Mode
lecture seule : aucun fichier de code n'a été modifié. Chaque affirmation est
accompagnée d'une référence fichier:ligne. Pour Q2, des requêtes SQLite en
lecture seule (`mode=ro`) ont été exécutées sur `data/trading_state.sqlite`
(données réelles de production, pas de fixture).

## Q1 — `historical_bars`

### Contenu exact en mode hybrid

`historical_bars` est peuplé exclusivement par la branche `signal` de
`_hybrid_market_snapshot` (bougies 15 minutes historiques), jamais par `live`
ni, sauf cas de repli décrit plus bas, par `atr_1h` :

```python
2616  "historical_bars": rows[-180:],
```
(`app/broker/tws_connector.py:2616`, dans `_historical_market_snapshot`,
partagée par les trois appels `signal`/`atr_1h`/mode "historical" — voir plus
bas pour la distinction).

Pour la branche `signal` telle qu'appelée en production :
```python
1320  signal = await self._historical_market_snapshot(
1321      symbol, contract, timeout=historical_timeout,
1324      duration=self.hybrid_signal_duration,
1325      bar_size=self.hybrid_signal_bar_size,
1326      cache_profile="hybrid_signal",
1327  )
```
(`app/broker/tws_connector.py:1320-1327`)

- **Timeframe** : `hybrid_signal_bar_size` = `"15 mins"` (défaut
  `app/broker/tws_connector.py:590`, confirmé identique `config.yaml:28`).
- **Durée demandée à IBKR** : `hybrid_signal_duration` = `"5 D"` (défaut
  `app/broker/tws_connector.py:589`, `config.yaml:27`) — donc jusqu'à 5 jours
  de séance RTH en 15 min, soit au maximum ~130 bougies/jour × 5 ≈ 650 lignes
  brutes (`rows`), mais **tronqué aux 180 dernières** avant d'être exposé sous
  la clé `historical_bars` (`app/broker/tws_connector.py:2616`,
  `rows[-180:]`) — soit un peu moins de 2 jours de séance pleine.
- **RTH uniquement** : `historical_use_rth = True` (défaut
  `app/broker/tws_connector.py:594`, `config.yaml:32`), transmis tel quel à
  `reqHistoricalDataAsync` (`app/broker/tws_connector.py:1614-1625`,
  paramètre `useRTH`).
- **Ordre** : plus ancienne → plus récente. Preuve : `latest = rows[-1]`
  (`app/broker/tws_connector.py:2553`) et `previous = rows[-2]`
  (`:2554`) désignent respectivement la bougie la plus récente et
  l'avant-dernière — cohérent uniquement si `rows` (et donc
  `historical_bars`, sa troncature) est trié ordre croissant de temps. Confirmé
  aussi côté lecteur : `momentum_breakout.py` itère
  `reversed(snapshot.historical_bars)` pour partir de la plus récente
  (`app/setups/momentum_breakout.py:977`, voir plus bas).

### Structure d'une bougie

```python
3168  def _bar_to_ohlcv(bar: Any) -> dict[str, Any]:
3169      return {
3170          "date": _date_text(getattr(bar, "date", None)),
3171          "open": _number_or_none(getattr(bar, "open", None)),
3172          "high": _number_or_none(getattr(bar, "high", None)),
3173          "low": _number_or_none(getattr(bar, "low", None)),
3174          "close": _number_or_none(getattr(bar, "close", None)),
3175          "volume": _int_or_none(getattr(bar, "volume", None)),
3176      }
```
(`app/broker/tws_connector.py:3168-3176`)

Chaque bougie a donc bien un timestamp, champ `"date"`. Sa fabrication :
```python
3250  def _date_text(value: Any) -> str | None:
3251      if value in (None, ""):
3252          return None
3253      if isinstance(value, datetime):
3254          return value.isoformat()
3255      if isinstance(value, date):
3256          return value.isoformat()
3257      return str(value)
```
(`app/broker/tws_connector.py:3250-3257`) — pour des bougies intrajournalières
(`formatDate=1`, cinquième paramètre positionnel `1` de
`reqHistoricalDataAsync`, `app/broker/tws_connector.py:1623`), `ib_async`
retourne un `datetime` tz-aware par bougie, donc `"date"` est une chaîne
ISO 8601 avec heure et fuseau (confirmé empiriquement en Q2 :
`"2026-07-17T15:45:00-04:00"`).

### Rempli à chaque snapshot ? Vide en live/simulé ?

Non, pas systématiquement. Dans la fusion :
```python
2699  if signal.get("historical_bars"):
2700      base["historical_bars"] = signal["historical_bars"]
```
(`app/broker/tws_connector.py:2699-2700`) — cette ligne ne s'exécute que si
l'appel historique `signal` (15 min) a réussi et renvoyé des lignes non
vides. Si l'appel `signal` échoue (`signal.get("available")` faux, payload
d'erreur sans clé `"historical_bars"`, `app/broker/tws_connector.py:2534-2552`),
cette ligne ne fait rien.

**Cas de repli non trivial** : si `signal` échoue mais que `live` échoue
aussi et que `atr_1h` (bougies 1 HEURE) réussit, alors
`base = dict(atr_1h)` (`app/broker/tws_connector.py:2651`) — et comme la
fonction `_historical_market_snapshot` produit `"historical_bars": rows[-180:]`
**quel que soit le bar_size demandé** (même code partagé, ligne 2616), `base`
contient déjà, avant même la ligne 2699, un `historical_bars` composé de
bougies **1 HEURE**, pas 15 minutes. La ligne 2699 ne le remplace pas
(condition fausse) ni ne le vide. Donc **le contenu de `historical_bars`
n'est pas garanti être des bougies 15 min** : c'est vrai seulement quand
l'appel `signal` a réussi ; dans le cas de repli décrit ci-dessus, ce sont des
bougies 1h qui portent le même nom de clé, sans distinction possible pour un
lecteur aval (aucun champ n'indique le bar_size des lignes de
`historical_bars` lui-même — seul `hybrid_signal_bar_size`/`bars_15m_count`
existent au niveau du quote, mais rien n'accompagne le tableau lui-même).

**Mode live (`_ticker_market_snapshot_for_type`)** : ne construit jamais de
clé `"historical_bars"` (fonction entière lue,
`app/broker/tws_connector.py:1382-1461` et suite ; aucune occurrence).
Donc si `base = live` (signal et atr_1h tous deux indisponibles),
`historical_bars` est absent → `[]` côté `MarketSnapshot`
(`app/engine/stock_market_monitor.py:588-590`, valeur par défaut
`isinstance(..., list)` faux → `[]`).

**Mode simulé** : `SimulatedBrokerConnector.market_snapshot`
(`app/broker/tws_connector.py:342-360`, lu en entier) ne construit jamais de
clé `"historical_bars"` — toujours `[]` en mode simulé.

### Qui lit `historical_bars` aujourd'hui

Grep exhaustif (`historical_bars`, hors tests/JS) :
- **`app/setups/momentum_breakout.py:974,977`** — **seul lecteur côté
  évaluation live d'un setup** :
  ```python
  968  def _bars_above_resistance(
  969      snapshot: MarketSnapshot,
  970      resistance: float,
  971  ) -> int | None:
  972      if snapshot.bars_above_resistance is not None:
  973          return snapshot.bars_above_resistance
  974      if not snapshot.historical_bars:
  975          return None
  976      count = 0
  977      for bar in reversed(snapshot.historical_bars):
  978          if not isinstance(bar, dict):
  979              break
  980          close = _first_number(bar.get("close"))
  981          if close is None or close <= resistance:
  982              break
  983          count += 1
  984      return count
  ```
  (`app/setups/momentum_breakout.py:968-984`) — appelée
  `bars_above = _bars_above_resistance(snapshot, resistance)`
  (`app/setups/momentum_breakout.py:488`), elle-même dans
  `_entry_validation`, appelée depuis `_analyze_long` (chemin live de
  `MomentumBreakoutSetup.evaluate()`). `bars_above` détermine directement le
  chemin `CONFIRMED_BREAKOUT` :
  ```python
  491  confirmed = (
  492      bars_above is not None
  493      and bars_above >= hold_bars
  494      and close > resistance
  ...
  510  elif confirmed:
  511      path = "CONFIRMED_BREAKOUT"
  ```
  (`app/setups/momentum_breakout.py:491-511`) — `path` non vide rend
  `"valid": bool(path)` vrai (`app/setups/momentum_breakout.py:532`), ce qui
  alimente en aval la décision d'entrée. **Important : `snapshot.bars_above_resistance`
  n'est jamais peuplé par le broker** (grep exhaustif de la clé
  `"bars_above_resistance"` dans `app/broker/tws_connector.py` : aucune
  occurrence), donc `int_value(quote.get("bars_above_resistance"))` vaut
  toujours `None` en production (`app/engine/stock_market_monitor.py:536`) —
  la condition ligne 972 est donc toujours fausse en production, et
  `_bars_above_resistance` retombe **systématiquement** sur la lecture de
  `snapshot.historical_bars` (ligne 974-984). **`historical_bars` est donc
  bien lu, et est même déterminant, pour le chemin `CONFIRMED_BREAKOUT` de
  `momentum_breakout`** — setup_type réellement utilisé (17 fichiers dans
  `data/setups/*.json` déclarent `"setup_type": "momentum_breakout"`, contre
  1 seul `"breakout_retest"` — `AVGO_20260629_001.json` et
  `HON_20260703_001.json`). L'affirmation de la question ("n'est lu par aucun
  setup") **est donc fausse pour `momentum_breakout`** ; elle reste vraie pour
  `breakout_retest.py` (lu en entier — aucune occurrence de
  `historical_bars`), `aggressive_rebound.py`, `range_breakout.py`,
  `pullback_continuation.py` (aucune occurrence dans ces trois fichiers non
  plus, confirmé par le même grep exhaustif).
- `app/portfolio_risk/service.py:153` — `_returns_for_symbol`, calcul de
  corrélation inter-symboles à partir des `close` de `historical_bars` extrait
  d'événements `stock_quote` historiques en base ; module de risque
  portefeuille, pas l'évaluation d'un setup individuel.
- `app/opportunities/scanner.py:635` — calcul de VWAP de séance pour le
  scanner d'opportunités (module de découverte, pas la boucle de setups
  actifs).
- `app/features/store.py:211-214` — `FeatureStore._features_from_bars`,
  alimente des features dérivées (closes/volumes/highs/lows) pour le
  `feature_store` optionnel injecté dans `StockMarketMonitor`
  (`app/engine/stock_market_monitor.py:44,59`) — utilisé pour la couche
  ML/forecast, pas l'évaluation directe d'un setup.
- `app/background_jobs.py:310,354,380`, `app/forecasting/forecast_service.py:154-1038`,
  `app/model_lab/service.py:619` — sous-système forecasting/model lab, hors
  boucle d'évaluation de setups.
- `app/gui/static/js/app.js:4067,6044,6105` — affichage frontend uniquement.
- `app/market_data/snapshot_payload.py:121-122` — recopie la clé telle
  quelle dans un payload API générique (transport, pas lecture métier).

**Conclusion Q1** : `historical_bars` n'est pas mort. Il est lu et
load-bearing pour un setup_type massivement utilisé en production
(`momentum_breakout`, 17 instances), via une fonction qui compense un champ
jamais peuplé par le broker (`bars_above_resistance`). Il reste effectivement
inutilisé par `breakout_retest` (le setup_type d'AVGO, cité dans les audits
précédents) et par les trois autres setup_types d'entrée. Le champ est
rempli de façon conditionnelle (échec possible de la fusion, cas de repli
1h ambigu ci-dessus) et absent en mode live pur / simulé.

---

## Q2 — Bougie close vs bougie en formation : preuve empirique

### Les événements `stock_quote` contiennent-ils l'OHLC + un timestamp ?

Oui. `self.event_store.record(EventLevel.INFO, "stock_quote", message, symbol=symbol, data={**quote_data, "timing": timing})`
(`app/engine/stock_market_monitor.py:236-242`) — `quote_data` est le dict brut
renvoyé par `broker.market_snapshot(...)`, donc contient exactement les mêmes
clés `open`/`high`/`low`/`close`/`bar_date`/`market_data_source`/etc.
documentées en Q1/audit 2. `EventStore.record` persiste via
`self.repository.add_event(EventRecord(timestamp=utc_now_iso(), ...,
data=to_jsonable(data or {})))` (`app/storage/event_store.py:37-48`), lui-même :
```python
857  def add_event(self, record: EventRecord) -> None:
858      self.database.execute(
859          """
860          INSERT INTO events (
861              timestamp, level, event_type, setup_id, symbol, message, data_json
862          )
863          VALUES (?, ?, ?, ?, ?, ?, ?)
864          """,
```
(`app/storage/repositories.py:857-864`) — table `events`, colonnes
`timestamp, level, event_type, setup_id, symbol, message, data_json` (JSON
sérialisé du dict complet, donc `open`/`high`/`close`/`bar_date` y sont).
Base : `data/trading_state.sqlite` (`config.yaml:536`,
`database_file: "data/trading_state.sqlite"`). Table `events` : 2 418 019
lignes au moment de l'audit, dont 543 940 `event_type='stock_quote'` (requête
ci-dessous), plage `2026-06-01T20:02:50` → `2026-07-17T20:23:25`.

### Requêtes exécutées (lecture seule, `sqlite3.connect('file:data/trading_state.sqlite?mode=ro', uri=True)`)

```sql
SELECT COUNT(*) FROM events WHERE event_type='stock_quote';
-- 543940
SELECT symbol, COUNT(*) FROM events WHERE event_type='stock_quote'
  GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 20;
-- FLNC 17707, ONDS 17593, MRVL 17311, RDW 17227, SMCI 17144, ...
SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE event_type='stock_quote';
-- 2026-06-01T20:02:50.101090+00:00 | 2026-07-17T20:23:25.044168+00:00
```

Symbole choisi : `FLNC` (le plus d'échantillons). Requête :
```sql
SELECT timestamp, data_json FROM events
WHERE event_type='stock_quote' AND symbol='FLNC'
  AND timestamp >= '2026-07-17T19:46:00' AND timestamp <= '2026-07-17T20:05:00'
ORDER BY timestamp ASC;
```
Résultats bruts (champs extraits du JSON de `data_json`, colonnes :
timestamp UTC | bar_date | open | high | close | previous_high) :

```
19:46:18  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.97  previous_high=13.99
19:46:37  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.97  previous_high=13.99
19:47:12  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.97  previous_high=13.99
19:48:10  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.97  previous_high=13.99
19:48:40  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.96  previous_high=13.99
19:49:13  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.96  previous_high=13.99
19:50:01  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=13.96  previous_high=13.99
19:51:01  bar_date=15:30:00-04:00  open=13.97  high=14.05  close=14.01  previous_high=13.99
19:52:15  bar_date=15:30:00-04:00  open=13.97  high=14.06  close=14.06  previous_high=13.99
19:53:43  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.06  previous_high=13.99
19:54:49  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.02  previous_high=13.99
19:56:02  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.01  previous_high=13.99
19:57:05  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.04  previous_high=13.99
19:58:06  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.05  previous_high=13.99
19:59:33  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.04  previous_high=13.99
20:00:16  bar_date=15:30:00-04:00  open=13.97  high=14.08  close=14.04  previous_high=13.99
20:00:37  bar_date=15:45:00-04:00  open=14.01  high=14.04  close=14.03  previous_high=14.08
20:01:42  bar_date=15:45:00-04:00  open=14.01  high=14.06  close=13.99  previous_high=14.08
20:02:50  bar_date=15:45:00-04:00  open=14.01  high=14.06  close=14.00  previous_high=14.08
20:04:48  bar_date=15:45:00-04:00  open=14.01  high=14.06  close=14.02  previous_high=14.08
```
(liste complète des 45 lignes consultée dans le terminal ; toutes ont
`market_data_source="hybrid"`, `bars_15m_count=129` puis `130` après le
changement de `bar_date`.)

### Interprétation strictement limitée à ce que ces chiffres montrent

1. **Pendant tout le temps où `bar_date` reste `15:30:00-04:00`** (de 19:46:18
   à 20:00:16 UTC, soit 14 minutes d'observation continue) : `open` reste
   **strictement identique** (`13.97`) sur les 16 lignes, alors que `high`
   (`14.05` → `14.08`) et `close` (`13.97` → `14.06` → `14.01` → `14.04`, non
   monotone) **changent à plusieurs reprises**, y compris entre deux
   requêtes espacées de quelques secondes à quelques minutes — donc
   largement `< 15 min`. C'est la preuve empirique demandée : `open` fixe,
   `close`/`high` mobiles, pour un même `bar_date`.
2. **À 20:00:37**, `bar_date` passe à `15:45:00-04:00`, `open` change
   (`14.01`), et **`previous_high` bascule à `14.08`** — exactement la
   dernière valeur de `high` observée pour l'ancien `bar_date=15:30:00`
   (ligne 20:00:16 ci-dessus). Cette valeur `14.08` reste ensuite
   **strictement constante** sur toutes les lignes suivantes tant que
   `bar_date=15:45:00` reste courant (20:00:37 → 20:04:48 dans cet extrait,
   et vérifié jusqu'à 20:23:18 dans une requête plus large sur les 40
   derniers événements). Donc : dès qu'une bougie cesse d'être la plus
   récente, sa valeur haute se fige définitivement (au moins sur la fenêtre
   observée) et devient `previous_high` — cohérent avec une bougie
   effectivement CLOSE. Tant qu'elle est la plus récente, elle bouge.
3. Sur les 40 derniers événements de la base (requête séparée, 20:07:06 →
   20:23:18, `bar_date` figé à `15:45:00-04:00` tout du long), `close` varie
   encore entre `14.00` et `14.09`, soit **jusqu'à 20:23 UTC = 16:23 EDT**,
   c'est-à-dire au minimum 38 minutes après le début nominal de cette bougie
   (15:45 EDT) et 23 minutes après la clôture officielle du marché (16:00
   EDT ce jour-là, vendredi). Constat brut, non interprété au-delà : la
   bougie renvoyée comme "dernière" par `reqHistoricalDataAsync` continue de
   changer bien après son intervalle nominal de 15 minutes, y compris après
   la fermeture de la séance.

### Le timestamp de bougie suffit-il seul à savoir si elle est close ?

Non. Aucune ligne de code ne calcule `bar_date + 15min <= maintenant` ni
n'attache de booléen "closed" aux éléments de `historical_bars` ou du quote.
Recherche exhaustive des candidats :
```python
app/data_quality/service.py:82   "candle_closed": bool(closed),   # closed est un paramètre reçu, pas calculé depuis bar_date
app/features/store.py:207        "closed_candle": bool(quote.get("candle_closed", quote.get("bar_closed", False))),
```
`app/data_quality/service.py:70-94` (`record_candle(self, symbol, timeframe,
candle, *, closed: bool)`) est la seule fonction du dépôt qui accepte un
paramètre `closed` explicite — mais **elle n'a aucun appelant** (grep exhaustif
de `record_candle(` dans tout le dépôt : la seule occurrence est sa propre
définition). Les clés `"candle_closed"`/`"bar_closed"` ne sont **jamais
écrites** par `tws_connector.py` (grep exhaustif : aucune occurrence) — donc
`quote.get("candle_closed", quote.get("bar_closed", False))` vaut toujours
`False` par défaut en production dans `features/store.py:207`. **Réponse :
non, le timestamp seul ne permet pas de trancher, et aucun code du dépôt ne
tente ce calcul** ; le seul mécanisme qui aurait pu porter cette information
(`record_candle`) est mort.

### `previous = rows[-2]` — à quoi sert-il ?

```python
2554  previous = rows[-2] if len(rows) > 1 else {}
...
2599  "previous_high": previous.get("high"),
```
(`app/broker/tws_connector.py:2554,2599`) — grep exhaustif de `previous.` dans
tout `tws_connector.py` : **aucun autre usage**. `previous` ne sert qu'à
produire `quote["previous_high"]`, recopié dans
`MarketSnapshot.previous_high` (`app/engine/stock_market_monitor.py:515`).
Lecteurs réels de `snapshot.previous_high` sur le chemin d'exécution :
- `app/setups/aggressive_rebound.py:64-66` — `previous_high = snapshot.previous_high
  or snapshot.high or high`, utilisé pour calculer le prix d'entrée
  (`entry = previous_high + ...`) — **setup_type actif** (12 fichiers
  `data/setups/*.json` en `"aggressive_rebound"`).
- `app/engine/setup_diagnostics.py:398-422` — affichage/trace de diagnostic.
- `app/setups/position_management.py:171` — recopié dans un dict de contexte
  (`"previous_candle_high": snapshot.previous_high`) pour la gestion de
  position.

La preuve empirique ci-dessus (point 2) montre que `previous_high` se fige
correctement à la valeur finale de la bougie qui vient de cesser d'être
courante — c'est donc, à la différence de `close`/`high` du quote principal,
une valeur qui **se comporte comme une vraie clôture de bougie 15 min**
(gelée), simplement décalée d'une bougie par rapport au présent (la bougie
juste précédente, jamais la courante).

---

## Q3 — Peut-on obtenir une vraie clôture journalière ?

### Existe-t-il déjà un appel avec `bar_size` journalier ?

Oui, mais son code n'est jamais exécuté dans la configuration active. Grep
exhaustif de `"1 day"` (hors JS/tests) :
```
app/settings.py:39                   "historical_bar_size": "1 day",   (valeur par défaut applicative)
app/broker/tws_connector.py:588      self.historical_bar_size = str(config.get("historical_bar_size", "1 day"))
config.yaml:26                       historical_bar_size: "1 day"
config.yaml:25                       historical_duration: "30 D"
```
Ce couple `historical_duration`/`historical_bar_size` alimente
`_historical_market_snapshot(symbol, contract, timeout=...)` **sans
arguments `duration`/`bar_size` explicites** — c'est-à-dire uniquement les
deux branches suivantes de `market_snapshot()` :
```python
1289  if self.market_data_source in {"historical", "ohlcv"}:
1290      return await self._historical_market_snapshot(symbol, contract, timeout=...)
...
1297  snapshot = await self._ticker_market_snapshot(symbol, contract, timeout=timeout)
1298  if snapshot.get("available"):
1299      return snapshot
1300  historical = await self._historical_market_snapshot(symbol, contract, timeout=...)
```
(`app/broker/tws_connector.py:1289-1306`) — **aucune des deux n'est atteinte**
en configuration active : `self.market_data_source == "hybrid"`
(`config.yaml:14`, confirmé audit 2) déclenche la branche précédente,
ligne 1283-1288, qui retourne immédiatement. Donc : le code d'appel en
bougie journalière **existe et est câblé jusqu'à `reqHistoricalDataAsync`**,
mais il est **mort en pratique** tant que `market_data_source` reste
`"hybrid"` — aucun appel réel `bar_size="1 day"` n'est émis en production
actuellement (vérifiable uniquement par lecture de configuration, pas par
trace d'exécution réelle : voir INCERTITUDES).

### Mécanisme de `atr_1h` — second timeframe déjà en place

```python
1329  atr_1h = await self._historical_market_snapshot(
1330      symbol, contract, timeout=historical_timeout,
1333      duration=self.hybrid_atr_1h_duration,
1334      bar_size=self.hybrid_atr_1h_bar_size,
1335  )
```
(`app/broker/tws_connector.py:1329-1335`, appelé juste après `signal` et
juste avant la fusion, dans `_hybrid_market_snapshot`) :
- `hybrid_atr_1h_duration` = `"30 D"`, `hybrid_atr_1h_bar_size` = `"1 hour"`
  (défauts `app/broker/tws_connector.py:591-592`, identiques
  `config.yaml:29-30`).
- **Pas de `cache_profile` explicite** passé (contrairement à `signal` qui
  passe `cache_profile="hybrid_signal"`) — donc son TTL de cache est
  déterminé par le nom du `bar_size`, pas par un profil nommé :
  ```python
  1789  if cache_profile == "hybrid_signal":
  1790      return self.market_data_ttl["hybrid_signal_seconds"]
  1791  normalized = str(bar_size or "").strip().lower()
  ...
  1794  if ("1 hour" in normalized or "1h" in normalized or ("60" in normalized and "min" in normalized)):
  1799      return self.market_data_ttl["atr_1h_seconds"]
  ```
  (`app/broker/tws_connector.py:1789-1799`) — `atr_1h_seconds` = `5400`
  (90 min, `config.yaml:23`). Donc un nouvel appel `reqHistoricalDataAsync`
  pour les bougies 1h n'est émis qu'au plus toutes les 90 minutes (repli sur
  cache sinon, `_cached_historical_payload`,
  `app/broker/tws_connector.py:1591-1596`).
- **Fusion dans le dict final** — `_merge_hybrid_market_snapshot` copie
  sélectivement, jamais tout `atr_1h` en bloc (sauf s'il devient `base` par
  repli, voir Q1) :
  ```python
  2677  if atr_1h.get("atr_1h") not in (None, ""):
  2678      base["atr_1h"] = atr_1h["atr_1h"]
  2679  for key in (
  2680      "atr_1h_status", "atr_1h_bar_size", "atr_1h_duration", "atr_1h_use_rth",
  2684      "bars_required_for_atr", "historical_1h_available", "historical_1h_error",
  2687      "last_successful_atr_1h", "last_successful_atr_1h_at", "atr_1h_age_seconds",
  2690  ):
  2691      if atr_1h.get(key) not in (None, ""):
  2692          base[key] = atr_1h[key]
  ```
  (`app/broker/tws_connector.py:2677-2692`), plus dans le bloc `base.update`
  final :
  ```python
  2731  "hybrid_atr_1h_bar_size": atr_1h.get("historical_bar_size"),
  2733  "bars_1h_count": atr_1h.get("bar_count"),
  ```
  (`app/broker/tws_connector.py:2731,2733`).

### Champs du snapshot final issus de `atr_1h`

`atr_1h`, `atr_1h_status`, `atr_1h_bar_size`, `atr_1h_duration`,
`atr_1h_use_rth`, `bars_required_for_atr`, `historical_1h_available`,
`historical_1h_error`, `last_successful_atr_1h`, `last_successful_atr_1h_at`,
`atr_1h_age_seconds`, `hybrid_atr_1h_bar_size`, `bars_1h_count` — tous
recopiés tels quels dans `MarketSnapshot` par
`quote_to_market_snapshot` (`app/engine/stock_market_monitor.py:538-546,579,535`).
Note : `MarketSnapshot.close_1h` existe comme champ (`app/models.py`, lu par
`quote_to_market_snapshot` ligne `close_1h=float_value(quote.get("close_1h"))`,
`app/engine/stock_market_monitor.py:569`) mais **aucune ligne de
`tws_connector.py` n'écrit la clé `"close_1h"`** (grep exhaustif : aucune
occurrence) — champ toujours `None` en production, malgré son nom qui
suggérerait une clôture 1h exploitable.

### Mécanisme exact à réutiliser pour un troisième timeframe journalier (constat, pas conception)

Ce qui existe déjà et qu'il faudrait reproduire à l'identique pour un
troisième appel (constat factuel du mécanisme, aucune proposition) :
1. Un appel `await self._historical_market_snapshot(symbol, contract,
   timeout=historical_timeout, duration=<...>, bar_size=<...>)` supplémentaire
   dans `_hybrid_market_snapshot` (`app/broker/tws_connector.py:1313-1349`),
   à côté de `signal` et `atr_1h`.
2. Un cas supplémentaire dans `_historical_cache_ttl_seconds`
   (`app/broker/tws_connector.py:1783-1800`) pour donner un TTL au nouveau
   `bar_size` (sinon repli silencieux sur `historical_seconds` = 300s,
   `config.yaml:24`).
3. Passage du nouveau dict résultat en paramètre de
   `_merge_hybrid_market_snapshot` (signature actuelle,
   `app/broker/tws_connector.py:2635-2644` : `symbol, source, signal, live,
   atr_1h, ttl, policy, indicator_policy`) et ajout des clés à copier
   sélectivement dans `base`, sur le modèle des lignes 2677-2692.
4. Ajout des nouvelles clés dans `quote_to_market_snapshot`
   (`app/engine/stock_market_monitor.py:492-591`) et dans `MarketSnapshot`
   (`app/models.py`) pour qu'elles survivent jusqu'au dict `quote` exposé aux
   setups.
C'est la même chaîne mécanique déjà utilisée pour `atr_1h`, dupliquée un
niveau de plus — rien de plus n'existe aujourd'hui dans le dépôt pour cela.

---

## Q4 — Les champs de config morts

Méthode : grep exhaustif de chaque champ dans tout `app/` (hors `tests/`),
puis lecture du contexte pour distinguer chemin d'exécution réel (évaluation
d'un setup en cours de vie — `app/setups/*.py::evaluate()` et ce qu'il
appelle, plus `app/engine/*.py` de la boucle live) vs. modules exclus par la
question (opportunités, scoring) vs. modules non couverts par la question mais
également hors boucle live (création/template de setup, parsing de texte,
forecasting, semantic validation).

| Champ | Statut |
|---|---|
| `timeframes.signal` | **MORT sur le chemin d'exécution des setups.** Lu par `app/background_jobs.py:339-340`, `app/api/routes_forecasting.py:57-58`, `app/forecasting/forecast_operational_service.py:25`, `app/opportunities/scanner.py:378`, `app/model_lab/service.py:34,62` — tous hors boucle d'évaluation live (`app/setups/*.py`, `app/engine/signal_engine.py`, `app/engine/trading_engine.py` : zéro occurrence dans ces fichiers, grep confirmé). |
| `timeframes.confirmation` | **MORT partout, y compris hors boucle live.** Seule occurrence hors défauts/JS : `app/gui/static/js/app.js:72` (libellé de champ formulaire). Aucun lecteur Python. |
| `retest.confirmation_required` | **MORT.** Seules occurrences : `app/setups/setup_type_registry.py:131,158` (métadonnées de défauts pour l'UI de création de setup). Aucun `.get("confirmation_required")` dans `app/setups/breakout_retest.py` (lu en entier, Q1) ni ailleurs dans le chemin d'évaluation. |
| `retest.confirmation_timeframe` | **MORT**, même constat (`app/setups/setup_type_registry.py:132,170`, défauts uniquement). |
| `retest.max_retest_days` | **MORT.** `app/settings.py:569` (défaut applicatif), `app/setups/text_converter.py:284` (valeur de défaut lors du parsing texte→config, jamais relue par `evaluate()`). Aucune lecture dans `app/setups/breakout_retest.py`. |
| `breakout.volume_rule_mode` | **MORT sur le chemin d'exécution.** Lu par `app/opportunities/opportunity_to_scenario_mapper.py:80-83` (module opportunités, exclu par la question) et défini en défauts (`app/engine/setup_template_service.py:102`, `app/setups/setup_type_registry.py:68`). Aucune lecture dans `app/setups/momentum_breakout.py` ni `breakout_retest.py` (grep exhaustif, aucune occurrence). |
| `breakout.fast_breakout_volume_ratio_min` | **VIVANT.** `app/setups/momentum_breakout.py:587`, dans `_volume_confirmation` (appelée depuis `_entry_validation` ligne 469, elle-même depuis `_analyze_long`, chemin live de `MomentumBreakoutSetup.evaluate()`) : `fast_min = _first_number(config.get("fast_volume_ratio_min"), breakout.get("fast_breakout_volume_ratio_min"), 1.50)`. |
| `breakout.confirmed_breakout_hold_bars` | **VIVANT.** `app/setups/momentum_breakout.py:483`, dans `_entry_validation` : `hold_bars = int(_first_number(volume_config.get("confirmed_hold_bars"), breakout.get("confirmed_breakout_hold_bars"), 2) or 2)` — directement comparé à `bars_above` (Q1) pour déterminer le chemin `CONFIRMED_BREAKOUT`. |
| `breakout.close_above_resistance_required` | **MORT.** Seules occurrences : `app/engine/setup_template_service.py:113`, `app/setups/setup_type_registry.py:73` (défauts UI de création). Aucune lecture dans `app/setups/momentum_breakout.py` (qui applique sa propre condition câblée en dur `close > resistance`, ligne 494, indépendamment de ce champ de config) ni `breakout_retest.py`. |
| `breakout.resistance` | **VIVANT (pour `momentum_breakout` uniquement).** `app/setups/momentum_breakout.py:18,45` : `resistance = _first_number(breakout.get("resistance"), self.estimated_entry_price())`, dans `_analyze_long` (chemin live). Absent de `breakout_retest.py`, qui utilise `breakout.daily_close_above` à la place (`app/setups/breakout_retest.py:21,32,72`). |
| `volume_confirmation.*` (bloc entier) | **VIVANT pour `momentum_breakout`, MORT pour `breakout_retest` (donc pour AVGO).** Lu uniquement par `app/setups/momentum_breakout.py:473,584` (`_entry_validation`/`_volume_confirmation`, chemin live) : sous-clés effectivement lues — `fast_volume_ratio_min` (:586), `normal_volume_ratio_min` (:590), `confirmed_volume_ratio_min` (:592), `max_upper_wick_ratio` (:596), `reject_detection_enabled` (:597), `confirmed_hold_bars` (:482). **Aucune sous-clé `"enabled"` n'est jamais lue nulle part** (grep exhaustif de `volume_config.get("enabled")` / équivalent : aucune occurrence) — donc même pour `momentum_breakout`, un `volume_confirmation.enabled: false` éventuel serait sans effet : seules les valeurs numériques individuelles comptent, il n'y a pas de porte on/off. Pour AVGO (`breakout_retest`), le bloc entier est mort : `breakout_retest.py` ne référence jamais `self.config.get("volume_confirmation")` (grep confirmé, aucune occurrence dans le fichier). |
| `rearm.*` | **MORT partout.** Seules occurrences : `app/engine/setup_template_service.py:186,326,360` (construction de template de création), `app/setups/setup_type_registry.py:99` (défauts/métadonnées). Aucune lecture dans `app/setups/*.py::evaluate()` ni ailleurs dans `app/engine/` en dehors de la création de setup (grep exhaustif de `"rearm"` dans tout `app/`, aucune autre occurrence). |
| `trend_filter.*` | **MORT partout.** Occurrences : `app/engine/setup_template_service.py:189-190` (template création), `app/setups/setup_type_registry.py:150` (défauts), `app/setups/text_converter.py:319` (parsing texte→config, valeur par défaut jamais relue). Aucune lecture dans un `evaluate()` de setup. |
| `management.stop_management.*` | **VIVANT — chemin de gestion de position, pas d'entrée.** Lu par `app/setups/position_management.py:67-121` (`mode`, `rules`, `steps`, `raise_stop_only_if` — classe `PositionManagementSetup`, un des `setup_type` de `MANAGEMENT_ONLY_SETUP_TYPES = {"runner", "trailing_runner", "position_management"}`, `app/setups/setup_conditions.py:472`) et `app/setups/trailing_runner.py:21` (`steps`). Ces deux classes s'exécutent bien dans la boucle live (`evaluate()` de setups de gestion post-entrée), mais sur des `setup_type` distincts des setups d'entrée (`breakout_retest`, `momentum_breakout`, etc.). Également lu par `app/intelligence/semantic_validation_service.py:494-513` — module de validation sémantique du texte source, hors boucle live de trading. |

**Constat transversal** : les champs vraiment morts sur le chemin
d'exécution (`timeframes.*`, `retest.confirmation_required`,
`retest.confirmation_timeframe`, `retest.max_retest_days`,
`breakout.volume_rule_mode`, `breakout.close_above_resistance_required`,
`rearm.*`, `trend_filter.*`) ont tous le même profil : présents uniquement
dans les défauts de création de setup (`setup_type_registry.py`,
`setup_template_service.py`, `text_converter.py`) — c'est-à-dire qu'ils sont
proposés/pré-remplis à la création d'un setup, mais aucune classe
`*Setup.evaluate()` ne les relit jamais. À l'inverse, tous les champs vivants
identifiés ici appartiennent à `momentum_breakout.py` (setup_type
majoritaire, 17 instances) ou à la gestion de position post-entrée — jamais
à `breakout_retest.py`, qui reste, comme établi en Q1 et dans l'audit 2, le
setup le plus "minimal" du dépôt (5 champs de config lus au total dans
`evaluate()`).

---

## Q5 — Le volume dans le snapshot

### Champs de volume et origine

Tous produits par `_historical_market_snapshot` sur les lignes `rows` de la
branche `signal` (bougies 15 min, mêmes lignes que `historical_bars` en Q1,
avant troncature à 180) :
```python
2580  volume_stats = _historical_volume_stats(rows, sample_days=20)
2581  volume_ratio = volume_stats["ratio"]
2582  average_volume = volume_stats["average_volume"]
2583  payload = {
...
2596      "volume": latest.get("volume"),
2597      "bar_volume_15m": latest.get("volume"),
2598      "avg_volume_15m": average_volume,
2600      "volume_ratio": volume_ratio,
2601      "volume_ratio_15m": volume_ratio,
2602      "volume_ratio_closed_bar": volume_ratio,
2603      "volume_timeframe": ("15m" if ... else bar_size),
2604      "volume_comparison_mode": volume_stats["comparison_mode"],
2605      "volume_sample_days": volume_stats["sample_days"],
2606      "volume_sample_count": volume_stats["sample_count"],
```
(`app/broker/tws_connector.py:2580-2608`) — donc **`quote["volume"]` est le
volume de la dernière bougie 15 min de `historical_bars`** (`latest =
rows[-1]`, ligne 2553), la même bougie établie en Q2 comme étant en cours de
formation (mutable) : ce n'est ni un cumul du jour, ni un tick live. Recopiés
tels quels dans `MarketSnapshot` par
`app/engine/stock_market_monitor.py:510-530`.

Champs jamais peuplés par le broker en production (grep exhaustif des clés
littérales dans `tws_connector.py`, aucune occurrence pour chacune) :
`current_bar_volume` (retombe sur `quote.get("volume")` via le défaut de
`.get()`, `app/engine/stock_market_monitor.py:514`), `average_volume_ratio_last_2_bars`,
`volume_ratio_live`, `volume_status`, `elapsed_ratio`, `projected_volume` —
tous toujours `None`/`""` en production. Confirmé empiriquement : requête sur
les 5 derniers événements `stock_quote` de FLNC, tous les champs
correspondants sont absents du `data_json` (donc valeurs par défaut
appliquées côté `MarketSnapshot`).

`volume_status` mérite une précision : le champ `MarketSnapshot.volume_status`
lui-même est mort (jamais peuplé), mais **`momentum_breakout.py` calcule sa
propre notion de statut de volume localement**, en variable, dans
`_volume_confirmation` (`app/setups/momentum_breakout.py:577-620`, clé
`"status"` du dict retourné) — sans jamais lire ni écrire
`snapshot.volume_status`. Les deux notions portent le même nom mais ne sont
pas connectées.

### Volume moyen historique — calcul trouvé, et anomalie constatée

Oui, un calcul existe : `_historical_volume_stats`
(`app/broker/tws_connector.py:3183-3237`), qui compare le volume de la
dernière bougie au volume moyen de bougies précédentes, avec deux modes :
- `SAME_TIME_OF_DAY` : moyenne du volume des bougies précédentes au **même
  créneau horaire** (`_bar_time_slot`, comparaison sur les 5 caractères
  après un espace dans la chaîne de date), sur `sample_days` jours (20 par
  défaut, mais borné par les 5 jours réellement demandés via
  `hybrid_signal_duration="5 D"`).
- `RECENT_BARS` : repli, moyenne simple des bougies précédentes disponibles,
  si aucun créneau identique n'est trouvé.

**Anomalie constatée par la donnée réelle** : le mode `SAME_TIME_OF_DAY`
n'est **jamais atteint en production**. Requête :
```sql
SELECT DISTINCT json_extract(data_json,'$.volume_comparison_mode')
FROM events WHERE event_type='stock_quote' LIMIT 10;
-- [(None,), ('RECENT_BARS',)]
```
Exécutée sur les 543 940 événements `stock_quote` de la base (la clause
`DISTINCT ... LIMIT 10` a nécessité un balayage complet pour ne trouver que 2
valeurs distinctes) : **seules les valeurs `None` et `RECENT_BARS`
apparaissent, jamais `SAME_TIME_OF_DAY`**, sur l'ensemble de l'historique
disponible (2026-06-01 → 2026-07-17). Confirmé aussi sur l'échantillon
détaillé FLNC : `comparison_mode= RECENT_BARS`, `sample_days= None`,
`sample_count= 129`, systématiquement.

Explication trouvée par lecture du code : `_bar_time_slot` attend une chaîne
de date avec un espace séparant date et heure —
```python
3240  def _bar_time_slot(value: Any) -> str | None:
3241      if value in (None, ""):
3242          return None
3243      text = str(value).strip()
3244      if " " not in text:
3245          return None
3246      time_part = text.rsplit(" ", 1)[-1]
```
(`app/broker/tws_connector.py:3240-3246`) — mais `_date_text`
(`app/broker/tws_connector.py:3250-3257`) produit un format ISO 8601 avec
séparateur `"T"` (`value.isoformat()`), par exemple
`"2026-07-17T15:45:00-04:00"` (confirmé empiriquement en Q2) : **il n'y a
jamais d'espace dans cette chaîne**, donc `" " not in text` est toujours vrai
et `_bar_time_slot` retourne toujours `None`. `latest_slot` vaut donc
toujours `None` dans `_historical_volume_stats` (ligne 3196), la condition
`if latest_slot and ...` (ligne 3200) est donc toujours fausse,
`same_slot_volumes` est toujours vide, et le code tombe systématiquement sur
la branche `RECENT_BARS` (ligne 3207-3213). C'est un constat de lecture de
code corroboré par la donnée réelle, pas une supposition : le mécanisme
`SAME_TIME_OF_DAY` existe dans le code mais est inatteignable avec le format
de date produit par `_date_text` — tous les ratios de volume actuellement en
base sont donc calculés par simple moyenne des bougies précédentes
disponibles (jusqu'à 5 jours de séance en 15 min), jamais par comparaison au
même horaire les jours précédents malgré ce que le nom `volume_sample_days`
et le paramètre `sample_days=20` (`app/broker/tws_connector.py:2580`)
suggèrent.

### Qui lit les champs de volume

Uniquement `app/setups/momentum_breakout.py`, via `_market_context`
(`app/setups/momentum_breakout.py:349-368`, recopie
`current_bar_volume`/`average_bar_volume`/`volume_ratio_closed_bar`/
`volume_ratio_live`/`average_volume_ratio_last_2_bars`/`volume_status`/
`volume_timeframe`/`volume_comparison_mode`/`volume_sample_days`/
`volume_sample_count`/`elapsed_bar_percent`/`projected_bar_volume` depuis les
champs `MarketSnapshot` correspondants) puis `_volume_confirmation`
(`app/setups/momentum_breakout.py:577-620`) qui les consomme pour calculer un
statut de volume propre au setup et alimenter `_entry_validation` (donc le
chemin `evaluate()` live). Grep exhaustif confirmé : aucune occurrence de
`.volume` / `volume_ratio` dans `breakout_retest.py`, `aggressive_rebound.py`,
`range_breakout.py`, `pullback_continuation.py` — ces quatre setup_types
n'utilisent aucun champ de volume.

---

## INCERTITUDES RÉSIDUELLES

1. **Q1 — cas de repli `atr_1h` → `historical_bars` en bougies 1h.** Le
   mécanisme est établi par lecture directe (`app/broker/tws_connector.py:2651,2699-2700`),
   mais je n'ai pas observé empiriquement un événement `stock_quote` réel où
   ce cas se produit (il faudrait un échec simultané de `signal` et `live`
   avec succès de `atr_1h`, situation rare) — je n'ai donc pas de preuve en
   base que ce cas se soit produit en pratique dans les 543 940 événements
   disponibles, seulement que le code le permettrait.
2. **Q2 — comportement de `reqHistoricalDataAsync` après la clôture de
   séance.** La donnée montre que `close`/`high` de la dernière bougie
   continuent de changer jusqu'à 23 minutes après 16:00 EDT (clôture NYSE) le
   2026-07-17. Je n'ai pas d'explication définitive côté IBKR pour ce
   comportement précis (rapports de transactions tardifs consolidés,
   comportement propre à `reqHistoricalData` en fin de séance, ou autre) —
   seul le fait brut est établi par la requête, pas sa cause exacte côté API
   IBKR (pas d'accès à la documentation IBKR ni à une session TWS dans cet
   audit).
3. **Q2 — généralisation au-delà de FLNC.** La preuve empirique porte sur un
   seul symbole (FLNC, le plus échantillonné) et une fenêtre de ~40 minutes.
   Je n'ai pas répété la même vérification sur d'autres symboles ni sur
   d'autres jours ; le mécanisme de code étant commun à tous les symboles
   (même fonction `_historical_market_snapshot`), rien ne suggère que ce
   comportement serait spécifique à FLNC, mais ce n'est pas vérifié
   exhaustivement.
4. **Q3 — absence de trace d'exécution réelle pour la branche `"1 day"`.**
   J'ai établi par lecture de configuration (`market_data_source: "hybrid"`)
   que le code `bar_size="1 day"` n'est jamais atteint, mais je n'ai pas
   cherché dans les 2,4M lignes de la table `events` une trace positive
   (`reqHistoricalDataAsync` avec `barSize=1 day`) confirmant qu'aucun appel
   de ce type n'a jamais été émis historiquement (config différente dans le
   passé, tests manuels, etc.) — seule la configuration actuelle est
   vérifiée.
5. **Q4 — exhaustivité du grep pour les champs "MORT".** Pour chaque champ
   déclaré mort, l'absence de lecteur repose sur un grep textuel de la
   sous-clé (ex. `"confirmation_required"`) dans tout `app/` hors `tests/` ;
   un renommage dynamique de clé (construction de nom de champ par
   concaténation de chaînes) échapperait à cette méthode. Aucun cas de ce
   type n'a été rencontré dans les fichiers lus, mais je ne l'exclus pas à
   100% dans les fichiers non ouverts en entier (`opportunity_to_scenario_mapper.py`,
   `shortlist_service.py`, `semantic_validation_service.py` n'ont été lus que
   par extraits ciblés autour des correspondances de grep, pas intégralement).
6. **Q5 — portée de la vérification `DISTINCT volume_comparison_mode`.** La
   requête a balayé toute la table `events` (nécessaire pour épuiser
   `DISTINCT` avant `LIMIT 10`) mais uniquement pour `event_type='stock_quote'`
   implicitement via le `WHERE` — le résultat (`None`, `RECENT_BARS`
   uniquement) couvre donc bien tout l'historique enregistré, sous réserve
   que le format de date produit par `ib_async` pour les bougies 15 min soit
   resté un `datetime` tz-aware sur toute cette période (pas vérifié pour
   d'anciennes versions de la librairie si le connecteur a été mis à jour
   entre-temps).

Tout le reste affirmé dans ce document est appuyé par une lecture directe du
fichier et de la ligne citée, ou par une requête SQL en lecture seule sur
`data/trading_state.sqlite` reproduite intégralement ci-dessus, dans ce
dépôt, à l'état où il se trouvait au moment de l'audit (branche
`feat/setup-conditions`).
