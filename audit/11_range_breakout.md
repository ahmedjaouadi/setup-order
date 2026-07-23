# Audit en lecture seule — Lot 11 : `range_breakout` (cas réel AHMA_20260630_001)

Mode lecture seule. Aucun fichier de code n'a été modifié. Prérequis lus en
entier avant de commencer : `audit/09_normes_transverses.md` (passe
transverse sur les 8 `setup_types`, axes gate `current_status`, INVALIDATE
conditionnel, formation vs close, cohérence config), `audit/05_normalisation.md`
section B (comment chaque type lit sa config) et section A (A.1-A.3, incident
réel `range_breakout` du 2026-06-29), `audit/02_points_bloquants.md` (POINT 1
sémantique des cotations, POINT 2 chemin exact du prix vers le broker, POINT 3
state machine, POINT 4 `ENTRY_READY` jamais persisté via `transition_setup`).
Ce lot ne répète pas ces constats sauf pour les reconfirmer sur des données
réelles ; il va plus loin en cherchant des défauts **propres à la logique
range haut/bas** de `range_breakout`, absents des lots précédents.

Toutes les requêtes SQL ont été exécutées en lecture seule
(`sqlite3.connect('file:data/trading_state.sqlite?mode=ro', uri=True)`),
filtrées par `symbol=` et/ou `event_type=` (jamais de scan complet de la
table `events`, 2,4M lignes). Fil conducteur : **AHMA_20260630_001**
(`data/setups/AHMA_20260630_001.json`), 479 entrées `processed[]` retrouvées
pour ce `setup_id` sur les 676 événements `stock_analysis` du symbole AHMA
(2026-06-25 → 2026-07-17). Complété par une vérification arithmétique sur
les 11 `setup_type=range_breakout` réels présents dans `data/setups/`
(ACHR, AHMA, DXYZ, IONQ, METC, NOW, ON, QBTS, RGNT, SHOP, VRT).

Code lu en entier : `app/setups/range_breakout.py` (43 lignes),
`app/setups/base_setup.py`, `app/engine/signal_engine.py`,
`app/engine/state_machine.py`, `app/engine/action_executor.py`,
`app/engine/entry_order_executor.py`, `app/engine/order_manager.py:60-200`,
`app/engine/setup_lifecycle_service.py` (fonctions de revalidation),
`app/setups/setup_conditions.py` (section `range_breakout`),
`app/setups/setup_type_registry.py` (template `range_breakout`).

---

## 1. ENTRÉE — d'où vient le prix réellement transmis au broker

### Ce que fait le code

```python
19  def evaluate(self, snapshot: MarketSnapshot, current_status: SetupStatus) -> SetupSignal:
24      range_config = self.config.get("range", {})
25      high = float(range_config["high"])
26      low = float(range_config["low"])
...
34      if snapshot.price > high:
35          offset = float(self.config.get("entry", {}).get("trigger_offset", 0.02))
36          return SetupSignal(
37              action=SignalAction.ENTRY_READY,
38              reason="Range breakout confirmed",
39              target_status=SetupStatus.ENTRY_READY,
40              entry_price=round(high + offset, 2),
41              stop_loss=self.stop_loss,
42          )
```
(`app/setups/range_breakout.py:19-42`)

`entry_price` = `range.high` (config, **statique**, fixé à la création du
setup) + `entry.trigger_offset` (config, défaut `0.02`) — **jamais** une
fonction du prix courant au-delà du test `>` lui-même. C'est le seul des 5
types d'entrée où le niveau de référence est un champ de config obligatoire
(`float(range_config["high"])`, lève `KeyError` si absent — déjà noté audit
05 section B), mais le résultat produit (`entry_price`) ne s'ajuste jamais à
un marché qui s'est éloigné du niveau configuré. Ce chemin est ensuite celui
qui part réellement au broker : `signal.entry_price` →
`risk_decision.trigger_price` (`app/engine/risk_engine.py:154-163`, déjà
tracé en détail par audit 02 POINT 2) → `OrderManager._entry_order_prices`
(`app/engine/order_manager.py:206-237`) → `trigger_price` de l'`OrderRecord`
transmis à `broker.submit_order` (`order_manager.py:113-115`).

### AHMA_20260630_001 — le cas nominal "coïncide"

`range.high=1.8`, `entry.trigger_offset=0.02` → `entry_price` calculé =
`round(1.8+0.02, 2) = 1.82`, qui coïncide avec `entry.trigger_price=1.82`
déclaré dans le JSON (`data/setups/AHMA_20260630_001.json:18-19`). Ce n'est
**pas** parce que le code lit `entry.trigger_price` (il ne le lit jamais
dans `evaluate()`, seulement dans `estimated_entry_price()`, jamais appelée
en live — confirmé audit 05 section B) : c'est une coïncidence arithmétique
construite par l'auteur du setup qui a calculé `trigger_price` à la main
comme `high + offset` au moment de rédiger le JSON.

### Preuve que la coïncidence casse sur des setups réels

Vérification arithmétique sur les 11 `range_breakout` réels
(`range.high + entry.trigger_offset` calculé vs `entry.trigger_price`
déclaré) :

| setup_id | `range.high` | `trigger_offset` | `trigger_price` déclaré | calculé (`high+offset`) | écart |
|---|---|---|---|---|---|
| ACHR_20260709_001 | 5.10 | 0.02 | 5.12 | 5.12 | 0 |
| AHMA_20260630_001 | 1.80 | 0.02 | 1.82 | 1.82 | 0 |
| DXYZ_20260630_001 | 28.50 | 0.02 | 28.52 | 28.52 | 0 |
| IONQ_20260630_001 | 55.08 | 0.02 | 55.10 | 55.10 | 0 |
| **METC_20260713_001** | 13.50 | 0.02 | **13.55** | **13.52** | **0.03** |
| **NOW_20260712_001** | 112.00 | 0.02 | **112.50** | **112.02** | **0.48** |
| **ON_20260714_001** | 98.00 | 0.02 | **98.20** | **98.02** | **0.18** |
| QBTS_20260703_001 | 24.70 | 0.02 | 24.72 | 24.72 | 0 |
| RGNT_20260703_001 | 4.55 | 0.02 | 4.57 | 4.57 | 0 |
| SHOP_20260629_001 | 118.75 | 0.02 | 118.77 | 118.77 | 0 |
| **VRT_20260713_001** | 325.00 | **0.27** | **325.00** | **325.27** | **0.27** |

**4 des 11 setups `range_breakout` réels en base ont un `entry.trigger_price`
déclaré qui diverge de ce que `evaluate()` calcule et transmet réellement au
broker** — de $0.03 (METC) à $0.48 (NOW). Pour `VRT_20260713_001`, la note
du setup précise explicitement l'intention : *"Déclencheur : clôture 15m >
325.00$ (plus haut récent 324.73$)"* (`data/setups/VRT_20260713_001.json:258`)
— l'auteur a mis `trigger_price=325.00` **égal** à `range.high`, avec
`trigger_offset=0.27` visiblement destiné à un autre usage (peut-être la
distance au dernier plus haut réel 324.73). Le code ignore cette intention et
calcule `325.00 + 0.27 = 325.27` comme trigger réel envoyé au broker — un
niveau que l'auteur n'a jamais explicitement voulu voir transmis. Pour
`NOW_20260712_001`, l'écart de $0.48 sur un trigger déclaré à $112.50
représente ~0.4% du prix, un ordre de grandeur qui peut faire la différence
entre un remplissage et un rejet de limite selon la config `maximum_limit_price`.

### Incident réel AHMA : le calcul statique produit un prix absurde après un gap

`stock_analysis` du 2026-07-03, entre 14:00:40 et 17:22:59 UTC (36 ticks
consécutifs, requête `event_type='stock_analysis' AND symbol='AHMA'`,
filtrage `processed[i].setup_id='AHMA_20260630_001'`) :

```
2026-07-03T14:00:40  status=WAITING_ACTIVATION action=ENTRY_READY
  reason="Range breakout confirmed" entry_price=1.82 stop_loss=1.62
  snapshot: price=2.27 close=2.5 high=2.51 low=2.32 volume_ratio=7.5776
... (identique à chaque tick, 36 occurrences jusqu'à 17:22:59)
```

Le marché a gapé bien au-dessus du range configuré (`range.high=1.8`) —
`snapshot.price=2.27`, `snapshot.close=2.5`, soit **+26% à +39%** au-dessus
du niveau de référence — avec un volume 7,58x la normale
(`volume_ratio=7.5776`, champ présent dans le snapshot mais jamais lu par
`range_breakout.py`, voir section 7). `evaluate()` continue pourtant à
émettre `entry_price=1.82` (le calcul statique `high+offset`), **totalement
déconnecté** du marché réel. Si ce signal avait atteint
`OrderManager.place_entry_order` sans être bloqué, l'ordre `STP_LMT` transmis
aurait eu `trigger_price≈1.82` (déjà franchi, déclenchement immédiat) et
`limit_price=1.87` (`entry.maximum_limit_price`, config statique) — un `BUY
LMT` à 1.87$ dans un marché qui traite à 2.27$+ : un ordre qui ne peut
mathématiquement jamais se remplir, resterait pendu en l'attente indéfiniment
(`entry.cancel_if_not_filled_after_minutes=30` existe mais dans le JSON de
AHMA n'est utilisé qu'au niveau config déclaratif — l'ordre lui-même n'a
jamais été transmis dans ce cas, voir ci-dessous).

**Ce qui a réellement empêché l'envoi de cet ordre n'est pas une protection
de `range_breakout`** : c'est un garde générique et sans rapport,
`entry_order_executor.py:170-186`, qui bloque toute entrée si
`_trailing_stop_order_ready(config)` est faux
(`app/engine/entry_order_executor.py:406-420` — exige un champ
`trailing_stop_loss.trailing_stop_order_ready` ou
`trailing_stop_loss.broker_order.trailing_stop_order_ready` explicitement
`True`, **absent des deux endroits dans la config AHMA**, donc
structurellement toujours `False` pour ce setup). Confirmé par 136
événements `entry_blocked_trailing_stop_not_ready` pour AHMA sur la fenêtre
2026-07-03T14:00:38 → 17:22:59
(`SELECT COUNT(*) FROM events WHERE event_type='entry_blocked_trailing_stop_not_ready' AND symbol='AHMA'` → 136),
message `"Entry blocked because trailing stop-loss is not ready"`,
`data_json={"entry_decision":{"blocking_reasons":["TRAILING_STOP_LOSS_NOT_READY"],...}}`.
**C'est un accident de configuration qui a évité l'envoi d'un ordre
mathématiquement infillable, pas une protection délibérée de
`range_breakout` contre un prix devenu obsolète** — voir Problème 1.

---

## 2. CONFIRMATION — bougie en formation ou close, oscillation possible

`range_breakout.py` **n'appelle jamais** `bullish_confirmation()`
(`base_setup.py:169-174`, import absent du fichier — confirmé par grep,
0 occurrence) : c'est le seul des 5 types d'entrée à n'avoir **aucune**
fonction de confirmation (déjà établi audit 05 section B, tableau, ligne
`range_breakout`). Le test d'entrée est un pur test de prix instantané :
`if snapshot.price > high` (`range_breakout.py:34`), où `snapshot.price` est
le prix `live` (`ticker.marketPrice()`) le plus récent disponible au moment
du poll, rafraîchi bien plus souvent que la bougie de 15 min sous-jacente à
`snapshot.close` (établi `audit/02_points_bloquants.md` POINT 1, cache
`hybrid_signal` = 60s, `config.yaml:21`).

### Preuve empirique de la désynchronisation price/close

Extrait `stock_analysis` AHMA, 2026-06-30 07:40–10:00 (30 ticks) : `price`
varie 1.72 → 1.79 → 1.77 → 1.78, tandis que `close` reste **figé à 1.74**
pendant toute la fenêtre (45+ minutes, plusieurs bougies de 15 min). Les deux
champs proviennent de sources de fraîcheur différentes au sein d'un même
`MarketSnapshot` (voir `audit/02_points_bloquants.md` POINT 1) — le
test d'entrée (`price`) et le test d'invalidation (`close`, section 5)
regardent donc deux données différentes **au même instant d'évaluation**,
un défaut déjà signalé par l'audit 09 (axe 3) mais confirmé ici avec des
valeurs réelles concrètes.

### Cas réel d'oscillation intrabar/intraday

`stock_analysis` AHMA, 2026-06-30 :
```
08:12:14  price=1.72  close=1.74  action=HOLD  "Waiting for range breakout"
08:17:25  price=1.87  close=1.74  action=HOLD  "PREMARKET_TRIGGER_DETECTED:
             Le trigger a ete touche avant l'ouverture. Attente de
             confirmation en marche regulier."
08:19:47  price=1.79  close=1.74  action=HOLD  "Waiting for range breakout"
08:24:54  price=1.77  close=1.74  action=HOLD  "Waiting for range breakout"
```

À 08:17:25, `snapshot.price=1.87 > range.high=1.80` : `evaluate()` a bien
généré un `ENTRY_READY` en interne (confirmé par `reason` = message de
`session_policy.py:86-88`, qui **remplace** le signal d'origine par `HOLD`
— `apply_entry_session_policy`, `signal_engine.py:82`, appelé juste après
`strategy.evaluate()` ligne 81). Sept minutes plus tard, `price` est retombé
à 1.79, **sous** le niveau de cassure. **Le seul mécanisme qui a empêché un
armement sur ce simple accroc de mèche est la politique de session
(prémarché interdit) — un garde générique sans rapport avec la logique de
range** ; si ce même mouvement s'était produit en séance régulière (RTH),
`range_breakout.py:34` aurait émis `ENTRY_READY` sur un unique tick au-dessus
de 1.80, sans aucune confirmation de clôture, sans persistance sur plusieurs
barres, malgré `require_close_outside_range: true` explicitement déclaré
dans la config (`AHMA_20260630_001.json:58`) et jamais lu (section 7).

Le signal **peut donc osciller** : dans les données réelles, chaque
franchissement observé du haut du range (08:17 le 06-30, et le cas du
07-14 détaillé section 8) a été suivi d'un repli sous le niveau en quelques
minutes, sans qu'aucune logique interne à `range_breakout` n'ait jamais
empêché ni détecté ce repli — la seule chose qui a empêché un armement réel
à chaque fois est un garde système indépendant (session policy, lifecycle
anti-chase), jamais une protection du type lui-même.

---

## 3. STOP — quel stop part réellement au broker

`range_breakout.py:41` : `stop_loss=self.stop_loss`, où
`BaseSetup.stop_loss` (`base_setup.py:54-58`) lit
`trailing_stop_loss.initial_stop` — pour AHMA, `1.62`
(`AHMA_20260630_001.json:70`). C'est la même propriété statique utilisée par
4 des 5 types d'entrée (déjà établi audit 05 section B/C) : une seule
lecture à la construction du signal, jamais recalculée dynamiquement (à la
différence de `momentum_breakout`, qui dérive un stop structurel du marché
live).

**Ce stop n'est cependant pas celui qui part effectivement au broker** —
`OrderManager.place_entry_order` **relit indépendamment** la même clé de
config, sans jamais consommer `signal.stop_loss` / `risk_decision.stop_loss`
tel que produit par `evaluate()` :

```python
84  trailing_stop = _trailing_initial_stop(setup)
...
91  risk_decision.stop_loss = trailing_stop
```
(`app/engine/order_manager.py:84,91`, `_trailing_initial_stop` définie
`order_manager.py:602-609`, relit `config["trailing_stop_loss"]["initial_stop"]`)

Pour `range_breakout`, cette relecture indépendante **retombe aujourd'hui
sur la même valeur numérique** que celle produite par `evaluate()` (les deux
lisent le même champ `trailing_stop_loss.initial_stop`, jamais transformé
entre les deux lectures) — donc pas de divergence numérique observée sur
AHMA. Mais la valeur transmise au broker n'est structurellement **pas** celle
du signal évalué : c'est un second appel de lecture de configuration, sans
aucun lien de données avec `SetupSignal.stop_loss` (`risk_decision.stop_loss`
est **écrasé** ligne 91, la valeur venant du `RiskDecision` construit à
partir de `signal.stop_loss` — `entry_order_executor.py:194-201` — est donc
jetée avant d'atteindre le broker). Aucun stop dynamique n'existe pour
`range_breakout` (contrairement à `momentum_breakout`) : le champ riche
`trailing_stop_loss.calculation`/`ratchet_rules` du JSON AHMA (ATR, structure,
buffers — `AHMA_20260630_001.json:81-181`) décrit un calcul élaboré, mais
`range_breakout.evaluate()` n'en lit qu'un seul sous-champ,
`initial_stop` — tout le reste (`calculation.method`, `structure.reference`,
`ratchet_rules.*`) est consommé ailleurs (management de position post-entrée,
hors `evaluate()`), pas par la logique d'entrée elle-même.

---

## 4. MÉMOIRE / SÉQUENCE

Aucune. `evaluate()` ne reçoit que `(snapshot, current_status)` — pas de 3e
paramètre de mémoire (signature identique aux 4 autres types, confirmé audit
05 section C) — et son corps ne consulte aucun état persisté au-delà de
`self.config` (figé à la construction du setup) et du `snapshot` du tick
courant. Il n'existe **aucune notion de "le range a tenu pendant N barres
avant la cassure"** ni de "prix resté sous le haut du range pendant X temps" :
chaque tick est réévalué de zéro, indépendamment de l'historique de ses
propres décisions précédentes. Ceci est cohérent avec les 3 autres types sans
retest explicite (`aggressive_rebound`, `pullback_continuation`), mais
contraste avec l'intention affichée par `require_close_outside_range: true`
(section 7) et avec le champ `volume_confirmation.confirmed_hold_bars: 2`
déclaré dans **tous** les 11 setups réels (ex. AHMA :
`volume_confirmation.confirmed_hold_bars=2`,
`AHMA_20260630_001.json:49`) — un champ dont le nom suggère explicitement
une exigence de tenue sur plusieurs barres, jamais implémentée ni lue
(0 occurrence de `confirmed_hold_bars` dans `range_breakout.py`, confirmé
par grep).

---

## 5. INVALIDATION / SORTIE

### Mécanisme 1 — `evaluate()` lui-même

```python
27  close = snapshot.close if snapshot.close is not None else snapshot.price
28  if close < low:
29      return SetupSignal(action=SignalAction.INVALIDATE,
30          reason="Close below range low", target_status=SetupStatus.INVALIDATED)
```
(`range_breakout.py:27-33`)

Utilise correctement `range.low` (le champ config attendu) contrairement au
trigger d'entrée (section 1). Test en `close` (pas `price` brut), contraire
au trigger — incohérence de granularité déjà signalée par l'audit 09, sans
gate sur `current_status` (déjà signalé également).

### Cas limite réel AHMA : frontière exacte jamais franchie

`stock_analysis` AHMA, 2026-07-17T13:56:17 : `price=1.65`, `close=1.65`
— **exactement égal** à `range.low=1.65`. Le test `close < low` (strict) est
`False` : le setup reste `HOLD`, pas d'`INVALIDATE`. Comportement
mathématiquement cohérent avec le code tel qu'écrit (`<`, pas `<=`), mais
c'est la limite exacte de la plage la plus étroite jamais observée dans les
données AHMA : **aucune occurrence de `close < low` n'a été trouvée sur
l'ensemble des 479 entrées `processed` de AHMA** (`min(close)=1.65`,
requête Python sur toutes les entrées). Le setup AHMA n'a donc **jamais**
été invalidé sur toute la période disponible (25 jours), malgré une note
explicite ("Invalidation : clôture 15m sous 1,65") — silence de données, pas
preuve d'absence de défaut, voir INCERTITUDES.

### Mécanisme 2 — un second chemin d'invalidation, indépendant et cassé pour `range_breakout`

`SetupLifecycleService.revalidate_setup` (`app/engine/setup_lifecycle_service.py`,
appelé **avant** `evaluate()` à chaque tick pour tout setup en
`WAITING_ACTIVATION`, `signal_engine.py:73`) contient un **second** calcul
d'invalidation, indépendant de `evaluate()` :

```python
608  def _invalidation_reason(config, close, initial_stop, is_long, setup_type=""):
...
625          if invalidation_below is None:
626              invalidation_below = _entry_thesis_invalidation_level(config, setup_type, is_long=True)
...
628          if invalidation_below is not None and close < invalidation_below:
629              return "INVALIDATION_LEVEL_BROKEN"
```
(`app/engine/setup_lifecycle_service.py:608-629`)

```python
654  def _entry_thesis_invalidation_level(config, setup_type, is_long):
...
666      if setup_type == "range_breakout":
667          range_config = config.get("range", {})
...
670              return _number_or_none(range_config.get("invalidation_below"))
```
(`app/engine/setup_lifecycle_service.py:654-671`)

**Ce second mécanisme lit `range.invalidation_below`, pas `range.low`.**
Ce champ **n'existe ni dans le template canonique** (`SETUP_SPECIFIC_OPTIONS["range_breakout"]`,
`app/setups/setup_type_registry.py:141-147` — seulement `high`, `low`,
`breakout_side`, `require_close_outside_range`) **ni dans 10 des 11 setups
réels** (`grep -l invalidation_below data/setups/*.json` avec
`setup_type=range_breakout` → seul `ACHR_20260709_001.json:178` le
définit, `4.7`, dupliquant en fait `range.low=4.7` de la même ligne — un
ajout ad hoc, pas issu du template). Pour **AHMA et 9 autres setups réels**,
`range_config.get("invalidation_below")` retourne `None` →
`_entry_thesis_invalidation_level` retourne `None` → `invalidation_below`
reste `None` dans `_invalidation_reason` → ni `support_min`
(`support_zone` n'existe pas pour `range_breakout`) ni `initial_stop`
(explicitement neutralisé avant l'entrée en position, `:210-217`, commentaire
du code lui-même) ne prennent le relais → **ce second filet de sécurité
n'invalide jamais un `range_breakout` réel sur le franchissement du bas du
range**, quelle que soit la valeur atteinte par `close`. C'est exactement le
même patron de défaut que celui documenté par l'audit 09 pour
`aggressive_rebound` (`invalidation.close_below` jamais peuplé, seul
`support_zone.invalidation_below` l'est) — ici découvert indépendamment pour
`range_breakout`, sur un champ différent (`range.invalidation_below` au lieu
de `range.low`), avec la même conséquence : un mécanisme de protection
déclaré dans le code mais silencieusement inopérant pour la quasi-totalité
des setups réels du type. Sur `range_breakout`, ce n'est heureusement **pas
critique** aujourd'hui car le mécanisme 1 (`evaluate()`, section ci-dessus)
utilise bien `range.low` et fonctionne correctement — mais si `evaluate()`
ne tourne pas pour un tick donné (ex. `current_status` déjà passé à un
statut hors `EVALUABLE_STATUSES`, ou tout scénario où seule la revalidation
lifecycle s'exécute), la protection perdue est réelle.

### Cohérence avec les notes du setup

Note AHMA : *"Invalidation : clôture 15m sous 1,65, rejet au-dessus de 1,80
avec mèche haute forte, ou breakout sans volume."* — **3 conditions
d'invalidation/rejet déclarées** :
1. Clôture 15m sous 1.65 → **implémentée** (mécanisme 1, `close < low`).
2. Rejet au-dessus de 1.80 avec mèche haute forte → **jamais implémentée** —
   aucune lecture de mèche/wick dans `range_breakout.py` malgré
   `volume_confirmation.max_upper_wick_ratio=0.45` et
   `reject_detection_enabled=true` déclarés (`AHMA_20260630_001.json:51-52`,
   section 7).
3. Breakout sans volume → **jamais implémentée** — aucune lecture de
   `volume_ratio`/`volume_confirmation.*` (section 7), malgré
   `snapshot.volume_ratio=7.5776` effectivement présent et disponible au
   moment du signal du 2026-07-03 (section 1).

**1 condition sur 3 des invalidations/rejets déclarées par l'auteur du setup
est réellement vérifiée par le moteur.**

---

## 6. GATE `current_status` — vérification empirique sur AHMA

Confirmé, comme établi par audit 09 axe 1 : `range_breakout.py` ne teste
`current_status` nulle part dans son corps (le paramètre n'apparaît que dans
la signature, `:22`). Vérification empirique sur AHMA :

```sql
SELECT COUNT(*) FROM events WHERE setup_id='AHMA_20260630_001'
  AND event_type='setup_transition_rejected';
-- -> 0
```

**Zéro rejet de transition pour AHMA**, mais **pas parce que le gate
fonctionne** : c'est parce que `ENTRY_READY` (le seul signal "actionnable"
que `range_breakout` ait jamais émis pour ce setup — 36 fois, section 1)
**ne passe jamais par `ActionExecutor.transition_setup` / `state_machine`**
(`ActionExecutor.execute_simple_action`, `action_executor.py:25-39`, ne gère
que `HOLD`/`INVALIDATE`/`STATUS_CHANGE` ; `ENTRY_READY` tombe sur
`return False` ligne 39 et part vers `EntryOrderExecutor`, qui n'appelle
jamais `state_machine` non plus — confirmé `audit/02_points_bloquants.md`
POINT 4). Le statut de AHMA en base (`setups.status`) est resté
`WAITING_ACTIVATION` sur toute la période observée
(`SELECT status FROM setups WHERE setup_id='AHMA_20260630_001'` →
`WAITING_ACTIVATION`, confirmé aussi par les 479 lignes `processed`, qui
montrent `status='WAITING_ACTIVATION'` sur 100% des entrées, y compris les 36
`ENTRY_READY` du 2026-07-03).

**Nuance non documentée par l'audit 09** : même si un mécanisme externe
tentait un jour d'écrire `ENTRY_READY` via la state machine pour un
`range_breakout` en `WAITING_ACTIVATION`, ce serait **rejeté** — la
transition `WAITING_ACTIVATION -> ENTRY_READY` **n'existe pas** dans
`ALLOWED_TRANSITIONS[WAITING_ACTIVATION]`
(`app/engine/state_machine.py:24-38` : cibles autorisées =
`WAITING_BREAKOUT`, `MISSED_BREAKOUT`, `MISSED_BREAKOUT_WAIT_RETEST`,
`STALE_SETUP`, `BLOCKED`, `WAITING_RETEST`, `WAITING_REBOUND`,
`WAITING_CONFIRMATION`, `WAITING_ENTRY_SIGNAL`, `EXPIRED`, `INVALIDATED`,
`CANCELLED`, `ERROR` — pas `ENTRY_READY`). L'absence de gate dans
`evaluate()` n'a donc **jamais** produit de corruption de statut observable
pour AHMA — le filet de la state machine bloquerait cette transition précise
si jamais elle était tentée par ce chemin — mais uniquement parce que le
signal `ENTRY_READY` de `range_breakout` court-circuite structurellement
toute la state machine (audit 02 POINT 4), pas parce que le type a une
quelconque protection interne.

---

## 7. CHAMPS CONFIG IGNORÉS — vérification précise sur AHMA_20260630_001

`grep -n "self\.config\." app/setups/range_breakout.py` (exhaustif) montre
que **seuls 3 champs** sont lus dans tout le fichier :
`range.high` (`:25`), `range.low` (`:26`), `entry.trigger_offset` (`:35`)
— plus `trailing_stop_loss.initial_stop` via `self.stop_loss`
(`base_setup.py:54-58`). C'est le minimum des 5 types d'entrée (déjà établi
audit 05 section B). Champs présents dans `AHMA_20260630_001.json` et
**jamais lus par `evaluate()`**, avec leur valeur réelle :

| Champ | Valeur AHMA | Déclaré où | Lu par `evaluate()` ? |
|---|---|---|---|
| `range.breakout_side` | `"up"` | `:57`, template `registry:145` | **Non** — aucune branche symétrique "down" n'existe dans le fichier ; le code est structurellement long-only (`close < low` invalide, `price > high` entre), quelle que soit la valeur de ce champ |
| `range.require_close_outside_range` | `true` | `:58`, template `registry:146` | **Non** — le trigger utilise `snapshot.price` (tick brut), pas `snapshot.close` (section 1/2) ; ce champ, déclaré `true` dans 11/11 setups réels, n'a strictement aucun effet |
| `volume_confirmation.*` (7 sous-champs : `enabled`, `fast_volume_ratio_min=1.5`, `normal_volume_ratio_min=1`, `confirmed_volume_ratio_min=0.8`, `confirmed_hold_bars=2`, `close_above_level_required=true`, `reject_detection_enabled=true`, `max_upper_wick_ratio=0.45`) | bloc entier `:41-53` | `grep -i volume app/setups/range_breakout.py` → **0 occurrence** | **Non**, aucun — confirmé par le cas réel du 2026-07-03 où `snapshot.volume_ratio=7.5776` était disponible au moment du signal et n'a jamais été comparé à `fast_volume_ratio_min=1.5` |
| `direction` | `"long"` | `:8` | **Non** — jamais lu, le comportement long-only est câblé en dur, pas dérivé de ce champ |
| `trailing_stop_loss.calculation.*` / `.ratchet_rules.*` (buffers ATR, structure, break-even) | bloc de 100+ lignes `:81-181` | Seul `initial_stop` (`:70`) est lu ; tout le reste consommé ailleurs (management post-entrée), jamais par `evaluate()` | **Partiellement** (1 sous-champ sur ~30) |

**Écart le plus grave** : `volume_confirmation` est un bloc de configuration
aussi riche que celui de `momentum_breakout` (même vocabulaire :
`fast_volume_ratio_min`, `confirmed_hold_bars`, `reject_detection_enabled`,
`max_upper_wick_ratio`) — mais alors que `momentum_breakout` lit
effectivement ces champs (`_volume_confirmation()`,
`momentum_breakout.py:577-712`, établi audit 05), `range_breakout` ne les lit
**jamais**, malgré une note utilisateur qui présuppose explicitement que le
volume est vérifié ("cassure confirmée... avec volume", section 5). Le
panneau "Ce que cherche le setup" (`setup_conditions.py:423-441`) est
au moins **fidèle** à ce défaut plutôt que de le masquer : ses 2 seules
conditions (`range_holds` = `close >= low`, `resistance_break` = `price >
high`, `setup_conditions.py:265-283`) reproduisent exactement la logique de
`evaluate()`, sans jamais mentionner volume ou mèche — contrairement à
`momentum_breakout`, dont la condition `breakout_confirmed` mentionne
explicitement "Volume fort (fast), tenue 2 barres" (`setup_conditions.py:370-374`).
L'UI ne ment donc pas, mais elle ne signale pas non plus à l'utilisateur que
les champs `volume_confirmation.*`/`range.breakout_side`/`range.require_close_outside_range`
qu'il voit dans l'éditeur de config n'ont aucun effet moteur.

**Découverte connexe (pas dans le template range_breakout, mais présente
dans les JSON réels)** : `ACHR_20260709_001.json:200-205` et
`NOW_20260712_001.json:204-209` et `VRT_20260713_001.json:197-202`
déclarent un bloc `anti_chase` (`enabled`, `max_price_above_entry_percent`,
`action_if_too_far`, `block_entry_if_price_above_maximum_limit`) — **absent
de AHMA**. Ce bloc n'est lu nulle part dans `range_breakout.py`, mais **est**
lu par `setup_lifecycle_service.py:724-741`
(`_anti_chase_threshold`), un mécanisme complètement séparé de `evaluate()`
(voir section 1, l'incident AHMA du 2026-07-03 : le mécanisme lifecycle a
fini par classer AHMA `STALE_SETUP` pour `PRICE_TOO_FAR_ABOVE_ENTRY`, mais
seulement le lendemain, 2026-07-04T20:48 — pas immédiatement le 2026-07-03
pendant les 3h20 où le signal `ENTRY_READY` a été émis en boucle avec un prix
obsolète, question non résolue, voir INCERTITUDES). Pour AHMA, l'absence du
bloc `anti_chase` dans son propre JSON signifie que ce filet retombe sur le
défaut `DEFAULT_PRICE_TOO_FAR_PERCENT=1.5%`
(`setup_lifecycle_service.py:28`) — protection minimale par défaut, jamais
configurée explicitement pour ce setup contrairement à 3 des 10 autres
`range_breakout` réels.

---

## 8. SIMULATION — 2 scénarios réels

### Scénario A (nominal) — cassure "propre" bloquée par un garde système, pas par `range_breakout` lui-même

`stock_analysis` AHMA, 2026-07-14 :

```
17:01:33  price=1.8208 close=1.8208  action=HOLD
  "LUNCH_WINDOW_RESTRICTED: Pendant le lunch (11:30-14:00 New York) une
   entree exige un RVOL >= 1.5. RVOL observe: indisponible."
17:11:57  price=1.83   close=1.83    action=HOLD  (idem, RVOL observe: 0.05)
17:26:23  price=1.83   close=1.83    action=HOLD  (idem, RVOL observe: 0.45)
```

`price`/`close` sont **au-dessus** de `range.high=1.80` de façon soutenue
pendant ~25 minutes (contrairement au cas B ci-dessous, ici `close` confirme
aussi le franchissement, pas seulement `price`) — le cas le plus proche d'un
"vrai" breakout dans les données AHMA. `range_breakout.evaluate()` a
nécessairement émis `ENTRY_READY` à chaque tick (`price > high` vrai), mais
`apply_entry_session_policy` (`signal_engine.py:82`) l'a systématiquement
rabattu en `HOLD` pour un motif **sans aucun rapport avec la logique de
range** : une politique système de fenêtre de lunch exigeant un `RVOL >= 1.5`
(`session_policy.py`, garde générique appliqué à tous les types). Séquence
lifecycle associée (`setup_lifecycle_status_changed`) :
```
17:19:02  BLOCKED -> STALE_SETUP (PRICE_TOO_FAR_ABOVE_ENTRY)
17:21:22  STALE_SETUP -> WAITING_ACTIVATION (SETUP_VALID)
17:28:33  WAITING_ACTIVATION -> STALE_SETUP (PRICE_TOO_FAR_ABOVE_ENTRY)
```
Le setup oscille entre `STALE_SETUP` et `WAITING_ACTIVATION` en quelques
minutes — encore un mécanisme externe (lifecycle anti-chase, défaut 1.5%),
pas la logique propre du type. **Verdict : le verdict final (pas d'entrée)
est probablement correct pour cette fenêtre précise (lunch, RVOL faible),
mais `range_breakout` lui-même n'a strictement rien contribué à cette
décision — il aurait émis `ENTRY_READY` sans confirmation ni volume à
n'importe quel autre moment de la séance où `price > 1.80`, indépendamment
de la qualité réelle de la cassure.**

### Scénario B (cas limite) — cassure suivie d'un retour immédiat dans le range

`stock_analysis` AHMA, 2026-06-30 (déjà détaillé section 2) :
```
08:12:14  price=1.72  close=1.74  HOLD "Waiting for range breakout"
08:17:25  price=1.87  close=1.74  HOLD "PREMARKET_TRIGGER_DETECTED..."
08:19:47  price=1.79  close=1.74  HOLD "Waiting for range breakout"
08:24:54  price=1.77  close=1.74  HOLD "Waiting for range breakout"
```

Franchissement d'un seul tick (`price=1.87 > high=1.80`), suivi d'un retour
sous le niveau en **moins de 3 minutes** (`08:17:25` → `08:19:47`), avec
`close` qui n'a jamais bougé (`1.74` tout du long, la bougie 15 min sous-jacente
n'a pas confirmé le mouvement). **`range_breakout.py:34` aurait déclenché
`ENTRY_READY` sur ce seul tick si l'heure n'avait pas été prémarché** — le
seul obstacle réel a été `PREMARKET_TRIGGER_DETECTED`
(`session_policy.py:86-88`), une règle d'horaire, pas une règle de qualité
de cassure. **Verdict : dans ce cas précis, le hasard de l'horaire a protégé
l'utilisateur d'une entrée sur une fausse cassure (mèche d'une minute,
clôture jamais confirmée) — mais le même mouvement en séance régulière, hors
lunch, aurait produit un `ENTRY_READY` réel sans qu'aucune logique de
`range_breakout` ne l'empêche.** L'intention affichée par
`require_close_outside_range: true` (jamais lue, section 7) est précisément
de prévenir ce scénario, et elle échoue à le faire dans les deux cas observés
(A et B) — uniquement sauvée par des gardes système sans rapport avec le
type.

---

## PROBLÈMES PROPRES À `range_breakout`

**1. Prix d'entrée statique (`range.high + trigger_offset`), sans aucune
comparaison au prix de marché courant ni anti-chase interne — divergence
prouvée sur 4/11 setups réels et sur un cas réel extrême (+39%).**
Preuve : `range_breakout.py:34-41` calcule toujours `entry_price = high +
offset`, jamais borné par le prix actuel. Sur AHMA, 36 événements
`ENTRY_READY` consécutifs le 2026-07-03 (`14:00:40` → `17:22:59`) avec
`entry_price=1.82` alors que `snapshot.price=2.27`/`close=2.5` (+26% à +39%,
`volume_ratio=7.5776` disponible et ignoré). Sur 11 setups réels, 4
(`METC`, `NOW`, `ON`, `VRT`) ont un `entry.trigger_price` déclaré qui
diverge du calcul réel de $0.03 à $0.48. **Impact** : un ordre `STP_LMT`
transmis avec ces valeurs serait soit à un prix que l'auteur n'a jamais
voulu (les 4 cas arithmétiques), soit mathématiquement infillable après un
gap (le cas AHMA — `LMT 1.87` dans un marché à `2.27`+). Seul un garde
générique sans rapport (`trailing_stop_order_ready` toujours faux pour AHMA)
a empêché la transmission réelle dans ce lot de données ; ce n'est pas une
protection du type, c'est un accident de configuration constaté a
posteriori.

**2. Aucune confirmation de clôture ni de volume malgré 3 conditions
d'invalidation/entrée explicitement déclarées par l'auteur du setup, dont
2 n'ont aucune implémentation.**
Preuve : note AHMA — "entrée uniquement si cassure confirmée au-dessus de
1.80 avec clôture 15m **et volume**", "invalidation : ... rejet au-dessus de
1.80 avec **mèche haute forte**, ou **breakout sans volume**". Code réel :
`range_breakout.py:34` teste seulement `snapshot.price > high` (tick brut).
`grep -i volume app/setups/range_breakout.py` → 0 occurrence. Aucune lecture
de mèche/wick. Sur 3 conditions déclarées par l'auteur (clôture, volume,
mèche), **1 seule** (clôture, et seulement côté invalidation, pas côté
entrée) a un semblant d'implémentation, et encore avec `snapshot.close`
(section 2) dont la fraîcheur est ambiguë. **Impact** : démontré
empiriquement par les scénarios A et B (section 8) — chaque franchissement
réel du haut du range observé dans les données AHMA a été suivi d'un retour
sous le niveau en quelques minutes (B) ou n'a été bloqué que par un motif
d'horaire sans rapport (A), jamais par une vérification de volume ou de
clôture propre au type.

**3. Second mécanisme d'invalidation (lifecycle) cassé pour `range_breakout`
par un nom de champ absent du template — `range.invalidation_below` au lieu
de `range.low`.**
Preuve : `setup_lifecycle_service.py:666-671` lit
`range_config.get("invalidation_below")` pour la thèse d'entrée
`range_breakout`, un champ absent du template canonique
(`setup_type_registry.py:141-147`) et absent de 10 des 11 setups réels
(seul `ACHR_20260709_001.json:178` l'a, en doublon de `range.low`). Pour
AHMA (et 9 autres), ce filet de sécurité indépendant ne se déclenche jamais
sur un franchissement du bas du range. **Impact** : aujourd'hui non
critique car le mécanisme principal (`evaluate()`, `close < low`) fonctionne
correctement en parallèle — mais c'est une seconde ligne de défense
documentée dans le code, silencieusement inopérante pour la quasi-totalité
des setups réels du type, un défaut structurellement identique à celui déjà
connu pour `aggressive_rebound` (audit 09) mais découvert ici sur un champ
et un mécanisme différents.

**4. `range.breakout_side` suggère une capacité (cassure baissière / short)
que le code ne possède structurellement pas.**
Preuve : `range.breakout_side: "up"` est déclaré dans 11/11 setups réels
(y compris AHMA), présent dans le template (`registry:145`) et dans les
règles de validation affichées à l'utilisateur (`setup_conditions.py:277-280`,
*"breakout_side doit etre defini"*) — mais `range_breakout.py` n'a
**aucune** branche symétrique pour une cassure vers le bas : le code est
long-only câblé en dur (`close < low` = invalidation, `price > high` =
entrée), quelle que soit la valeur de `breakout_side` ou de `direction`.
**Impact** : un utilisateur qui configurerait `breakout_side: "down"` en
s'attendant à un short sur cassure du bas de range obtiendrait exactement le
même comportement long-only — un champ de configuration qui ne fait
strictement rien, contrairement à ce que sa présence dans les règles de
validation officielles laisse penser.

**5. Incohérence de source de données entre le test d'entrée (`price`,
rafraîchi en continu) et le test d'invalidation (`close`, figé pendant
plusieurs dizaines de minutes) au sein de la même méthode, avec un ordre
d'évaluation qui privilégierait silencieusement l'invalidation en cas de
conflit.**
Preuve : `range_breakout.py:27-34` teste `close < low` **avant**
`price > high`. Sur les données réelles AHMA, `close` reste identique
pendant que `price` varie sur des dizaines de ticks consécutifs (ex.
2026-06-30 07:40-08:45, `close=1.74` constant pendant 65+ minutes, `price`
1.72→1.87→1.77). **Impact** : ce n'est pas seulement une "granularité
différente" documentée par l'audit 09 — c'est un ordre de test qui, dans un
scénario extrême non observé mais possible (gap violent traversant tout le
range en un seul tick, `close` déjà sous `low` mais `price` du tick
au-dessus de `high` suite à un rebond immédiat), ferait gagner
silencieusement `INVALIDATE` sur `ENTRY_READY` sans que la logique métier
de cet arbitrage ne soit documentée ni testée nulle part dans le code.

---

## INCERTITUDES

1. **Cause du gap AHMA du 2026-07-03** (`price` 1.72→2.27 entre le
   2026-07-02 soir et le 2026-07-03 matin, `volume_ratio=7.5776`) — non
   déterminée (actualité, earnings, données de marché anormales). N'affecte
   pas le constat (le comportement du moteur est identique quelle que soit
   la cause), mais je n'ai pas de confirmation externe que ce chiffre
   reflète un vrai mouvement de marché plutôt qu'un artefact de flux de
   données.
2. **Mécanisme exact retardant la classification `STALE_SETUP` du
   2026-07-03 au 2026-07-04T20:48** — le calcul `_anti_chase_threshold`
   (section 1/7) aurait dû, sur lecture de code seule, classer AHMA
   `STALE_SETUP` dès le premier tick RTH à 2.27 (14:00:40, très au-dessus du
   seuil par défaut 1.5%), mais les événements `setup_lifecycle_status_changed`
   ne montrent la première transition liée au prix que 30h plus tard. Je n'ai
   pas retracé la boucle de planification exacte de
   `SetupLifecycleService.revalidate_and_apply` (fréquence, éventuel cache
   supplémentaire non lu dans ce lot) qui expliquerait ce délai — seul le
   résultat final (statut resté `WAITING_ACTIVATION` avec `ENTRY_READY`
   émis pendant ces 3h20) est confirmé par les données.
3. **Aucun `INVALIDATE` réel observé pour un `range_breakout` sur toute la
   période disponible** (AHMA n'a jamais franchi `close < low` ; aucune
   vérification par échantillon large sur les 10 autres setups
   `range_breakout` réels — silence de données, pas preuve que le mécanisme
   fonctionne correctement en conditions réelles au-delà de la lecture de
   code).
4. **Ordre `order_record_to_broker_request`/mapping IBKR final** — non
   retracé dans ce lot (même réserve que `audit/02_points_bloquants.md`
   INCERTITUDE 3) : je n'ai pas vérifié que `trigger_price`/`limit_price` de
   l'`OrderRecord` construit avec les valeurs erronées de la section 1
   seraient transmis sans transformation supplémentaire à l'objet `Order`
   IBKR final — seul le fait que cet ordre n'a **jamais été transmis** pour
   AHMA (bloqué par `trailing_stop_order_ready`) est confirmé.
5. **Comportement des 3 setups `range_breakout` non examinés en détail**
   (DXYZ, IONQ, QBTS, RGNT, SHOP — vérifiés seulement pour l'arithmétique
   trigger_price section 1, pas pour un déroulé complet d'événements
   `stock_analysis`) — AHMA reste le fil conducteur le plus riche
   (676 `stock_analysis` bruts, 479 `processed` pour ce setup précis), mais
   je n'ai pas confirmé que les problèmes 3 et 4 ci-dessus produisent des
   symptômes observables sur ces autres setups faute de temps de requêtage
   supplémentaire.
