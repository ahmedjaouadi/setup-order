# Audit en lecture seule — Lot 10 : `pullback_continuation` (cas reel TXN)

Mode lecture seule. Aucun fichier de code n'a ete modifie. Suite de
`audit/09_normes_transverses.md` (axes transverses) et
`audit/05_normalisation.md` section B (trigger/stop/confirmation), avec la
meme rigueur que l'audit AVGO/`breakout_retest` mais appliquee
specifiquement a `pullback_continuation`. Fil conducteur impose :
**TXN_20260713_001** (`data/setups/TXN_20260713_001.json`). Toutes les
requetes SQL ont ete executees en lecture seule
(`sqlite3.connect('file:data/trading_state.sqlite?mode=ro', uri=True)`) via
`python3 -c "..."`, systematiquement filtrees par `symbol=`/`event_type=`/
`setup_id=`. Aucun scan complet de `events` (2,4M lignes) n'a ete effectue.

**Avertissement liminaire, valable pour tout le reste du document** : la
decouverte la plus importante de ce lot (section 0) change la nature de
l'analyse : `pullback_continuation` n'a **jamais**, sur toute la fenetre de
donnees disponible, execute autre chose que sa toute premiere ligne de
`evaluate()`. Les sections 2 a 6 (CONFIRMATION, STOP, MEMOIRE, INVALIDATION
via `evaluate()`, GATE) portent donc necessairement sur du **code jamais
exerce en production** — chaque section le precise explicitement plutot que
de le laisser implicite.

---

## 0. Decouverte prealable : `pullback_continuation` n'a jamais depasse sa premiere ligne

`app/setups/pullback_continuation.py:22-23` :
```python
if snapshot.ema_20 is None or snapshot.ema_50 is None:
    return SetupSignal.hold("Waiting for EMA data")
```

Verification empirique, **sur les 96 344 evenements `stock_analysis`
disponibles, sans exception** :

```python
# scan filtre event_type='stock_analysis', Counter sur processed[i]["reason"]
# pour tout processed[i] ou setup_type == "pullback_continuation"
```

```
examined events 96344   pullback matches 4377
[('Waiting for EMA data', 4377)]
[(('WAITING_ACTIVATION', 'HOLD'), 4377)]
```

**4 377/4 377 evaluations de `pullback_continuation` sur toute la periode
disponible (2026-06 -> 2026-07-17) ont retourne exactement `HOLD /
"Waiting for EMA data"`, statut `WAITING_ACTIVATION` inchange.** Aucune
n'a jamais atteint la ligne 24 (`INVALIDATE`), 30 (`STATUS_CHANGE` vers
`WAITING_ENTRY_SIGNAL`) ni 37-46 (`ENTRY_READY`). Confirme specifiquement
pour TXN_20260713_001 (6/6 evenements `stock_analysis` disponibles pour ce
setup, 2026-07-13T17:45 -> 18:03) :

```sql
SELECT timestamp, ... FROM events WHERE symbol='TXN' AND event_type='stock_analysis'
  AND timestamp >= '2026-07-13' -- puis extraction processed[] pour setup_id='TXN_20260713_001'
```

| timestamp | status | action | reason | snapshot price/high |
|---|---|---|---|---|
| 17:45:42 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 300.87 / 301.10 |
| 17:46:12 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 300.98 / 301.10 |
| 17:46:54 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 301.00 / 301.17 |
| 17:50:37 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 300.81 / 301.24 |
| 17:56:14 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 300.43 / 301.24 |
| 18:03:13 | WAITING_ACTIVATION | HOLD | Waiting for EMA data | 300.21 / 300.55 |

### Cause racine : `snapshot.ema_20`/`snapshot.ema_50` ne sont jamais peuples sur le chemin live

Trace complete de construction du `MarketSnapshot` reellement passe a
`evaluate()` : `stock_market_monitor.py:193` (`broker.market_snapshot(...)`)
-> `quote_data` (dict brut TWS) -> `quote_to_market_snapshot(symbol,
quote_data)` (`stock_market_monitor.py:217`, fonction definie
`:492-591`). Lecture **complete** du corps de cette fonction (99 lignes,
tous les champs du dataclass `MarketSnapshot` y sont assignes un par un
depuis `quote.get(...)`) : **`ema_20` et `ema_50` n'y apparaissent nulle
part** — confirme par grep exhaustif (`grep -n "ema_20|ema_50"
app/engine/stock_market_monitor.py` -> 0 occurrence) et par lecture ligne a
ligne des 99 lignes du constructeur. `app/models.py:225-226`
(`ema_20: float | None = None`, `ema_50: float | None = None`) : la valeur
par defaut du dataclass s'applique donc a **toute** cotation live, sans
exception.

Verification qu'aucun autre chemin ne peuple ce champ avant `evaluate()` :
`grep -rn "ema_20\|ema_50" app/` (hors tests) donne 8 fichiers, **tous en
lecture** de la cle, jamais en ecriture depuis un calcul reel :
- `app/features/store.py:202-203` : `"ema_20": _number(quote.get("ema_20"))`
  — relit la meme cle deja absente, ne la calcule pas. Ce module calcule en
  realite `historical_ema_20`/`historical_ema_50` (`:237-238`, via
  `_ema(closes, 20)` sur des cloture **journalieres**) — une paire de cles
  **differente**, utilisee uniquement par `app/opportunities/scanner.py:649`
  pour le scanner d'opportunites, jamais reliee a `MarketSnapshot.ema_20`.
- `app/opportunities/scanner.py:668-669` : meme lecture `quote.get("ema_20")`
  dans `_selection_context()`, pour le scoring du type d'opportunite
  `"pullback_continuation"` du scanner lui-meme (`:721-729`,
  `ema_20 >= ema_50 and price >= ema_20 and price <= ema_20*1.05`) — **ce
  sous-systeme independant est structurellement expose au meme defaut**,
  pour la meme raison racine (la cle `ema_20` sur le `quote` scanner n'est,
  elle non plus, jamais calculee).
- `app/market_data/snapshot_payload.py:39-40` : `ema_20`/`ema_50` figurent
  dans `FLOAT_FIELDS`, l'ensemble des champs connus pour la serialisation
  **sortante** d'un `MarketSnapshot` deja construit — confirme que le champ
  existe dans le modele, pas qu'il est peuple en amont.
- `app/setups/setup_conditions.py:103,111` (panneau "Ce que cherche le
  setup") et `app/engine/setup_diagnostics.py:431-432,670-671` : lecture du
  meme `snapshot.ema_20`/`ema_50` pour l'affichage UI — voir section 6bis
  ci-dessous, ce panneau reflete donc honnetement l'absence de donnees
  plutot que de la masquer.

**Aucune des 8 occurrences n'est un site de calcul reel d'EMA a partir de
cloture intrajournalieres alimentant `MarketSnapshot`.** `TWSBrokerConnector`
(`app/broker/tws_connector.py`, fichier source de `quote_data`) ne contient
**aucune** occurrence de `ema_20`/`ema_50` (grep confirme, 0 match). Le champ
est donc mort de bout en bout sur le chemin live : ni la couche broker, ni
`quote_to_market_snapshot`, ni aucun service intermediaire ne le calcule.

**Consequence directe et generalisable (pas seulement TXN)** :
`PullbackContinuationSetup.evaluate()` est **structurellement inatteignable
au-dela de sa ligne 23** dans l'etat actuel du code — ce n'est pas un
defaut de configuration de TXN (aucun champ de config ne peut compenser
`snapshot.ema_20 is None`), c'est un defaut de la chaine de donnees en
amont de tous les setups de ce type. Les 13 setups `pullback_continuation`
actuellement dans `data/setups/` (dont TXN) sont, sans exception,
condamnes au meme sort tant que ce champ n'est pas alimente.

---

## 1. ENTREE — prix reellement transmis au broker

Puisque `evaluate()` n'a jamais atteint la branche `ENTRY_READY` en
production, cette section est une lecture de code pure (aucune
verification empirique du `entry_price` reel n'est possible — aucun
`entry_order_submitted` n'existe pour un `setup_type='pullback_continuation'`
sur toute la periode, confirme par requete ci-dessous), mais le calcul est
sans ambiguite et se laisse retracer completement jusqu'au broker.

```sql
SELECT COUNT(*) FROM events
WHERE event_type IN ('entry_order_submitted','entry_order_rejected')
  AND setup_id IN (SELECT setup_id FROM setups WHERE setup_type='pullback_continuation');
-- -> 0 (coherent avec la section 0 : jamais d'ENTRY_READY emis)
```

### Calcul dans `evaluate()`

`pullback_continuation.py:37-46` :
```python
if snapshot.price <= snapshot.ema_20 and bullish_confirmation(snapshot):
    reference_high = snapshot.high or snapshot.price
    offset = float(self.config.get("entry", {}).get("trigger_offset", 0.02))
    return SetupSignal(
        action=SignalAction.ENTRY_READY,
        ...
        entry_price=round(reference_high + offset, 2),
        stop_loss=self.stop_loss,
    )
```

`entry_price` = **`snapshot.high` (ou `snapshot.price` en repli) du tick de
confirmation, plus `entry.trigger_offset`** — une valeur purement dynamique,
recalculee a chaque tick a partir du marche, **jamais** derivee de
`entry.trigger_price`/`entry.entry_price`/`entry.limit_price` (config).
`estimated_entry_price()` (`:10-15`), qui lit bien
`pullback.entry_reference`/`entry.trigger_price`, existe mais **n'est
jamais appelee par `evaluate()`** (confirme par grep : aucun
`self.estimated_entry_price()` dans le corps de la methode).

### Config declaree pour TXN vs valeur qui serait calculee

`data/setups/TXN_20260713_001.json` declare :
- `entry.trigger_price = 302.0`, `entry.entry_price = 302.0`,
  `entry.limit_price = 302.5`, `entry.maximum_limit_price = 304.0`
- `pullback.entry_reference = 302.0`
- `entry_decision.planned_vs_current_risk.planned_entry = 302.0` (texte
  d'authoring, affiche a l'utilisateur)

Aucun de ces 5 champs n'entre dans le calcul de `entry_price` d'`evaluate()`.
En reprenant les 6 `high` reels de TXN captures le 2026-07-13 (section 0) et
`entry.trigger_offset = 0.02`, si le garde EMA n'avait pas bloque
l'evaluation, le `entry_price` **effectivement calcule** aurait ete :

| tick | `snapshot.high` | `entry_price` calcule (`high + 0.02`) | vs `entry.trigger_price` config (302.0) |
|---|---|---|---|
| 17:45:42 | 301.10 | 301.12 | -0.88 |
| 17:46:54 | 301.17 | 301.19 | -0.81 |
| 17:50:37 / 17:56:14 | 301.24 | 301.26 | -0.74 |
| 18:03:13 | 300.55 | 300.57 | -1.43 |

Ces valeurs restent, par coincidence, a l'interieur de la zone declaree
(`pullback.zone_min=300.0` / `zone_max=303.5`) pour ces 6 ticks precis —
mais rien dans le calcul ne le garantit structurellement : `reference_high`
n'est jamais borne par `pullback.zone_min`/`zone_max`, ni par
`support_zone.min`/`max`. Si le prix repartait en tendance et que
`snapshot.high` du tick de confirmation se trouvait au-dessus de
`support_zone.max` (303.5) — un `high` a 306, par exemple, tout a fait
plausible puisque TXN cotait ~305 le jour de creation du setup — le
`entry_price` calcule (306.02) serait transmis **au-dela de la zone de
pullback declaree**, sans qu'aucun garde-fou dans `evaluate()` ne l'empeche
(le seul filtre amont est `snapshot.price <= snapshot.ema_20`, qui ne
contraint pas `snapshot.high`).

### Chemin complet jusqu'au broker (lecture de code, `order_type=LMT` pour TXN)

1. `entry_order_executor.py:196` : `risk_decision = self.risk_engine.evaluate(..., entry_price=signal.entry_price, ...)` — **c'est bien `signal.entry_price`** (la valeur dynamique ci-dessus) qui entre dans le moteur de risque, pas un champ de config relu independamment.
2. `risk_engine.py:100` : `worst_case_entry_price = self.worst_case_entry_price(setup_config, entry_price)`. `risk_engine.py:60` : `if str(entry.get("order_type", "STP_LMT")) != "STP_LMT": return trigger_price` — **pour TXN (`order_type="LMT"`), cette fonction retourne `trigger_price` inchange** : la garde `maximum_limit_price=304.0`/`limit_price=302.5` declaree en config **n'est jamais appliquee comme plafond**, elle ne joue ce role que pour un `order_type=STP_LMT`. `risk_engine.py:158` : `entry_price=round(worst_case_entry_price, 4)` = `signal.entry_price` arrondi, sans plafonnement.
3. `order_manager.py:207-224` (`_entry_order_prices`), branche `LMT` : `return None, round(risk_decision.entry_price, 2), None` — le **prix LMT reellement transmis au broker est directement `signal.entry_price`** (le calcul dynamique de l'etape 1), arrondi a 2 decimales.

**Conclusion section 1** : pour TXN (et tout `pullback_continuation` en
`order_type=LMT`), le prix LMT envoye au broker serait `round(snapshot.high
+ 0.02, 2)` au moment ou `bullish_confirmation()` et `price <= ema_20`
sont simultanement vrais — un nombre qui n'a **aucun lien mecanique** avec
`entry.trigger_price=302.0` ni avec `pullback.zone_min/zone_max`
(300.0/303.5), uniquement une coincidence de fenetre de prix. Le champ
`entry.maximum_limit_price=304.0`, present et documente dans le JSON comme
garde-fou, est **inerte** pour ce setup precis a cause du choix
`order_type=LMT` (la meme garde, dans `base_setup.py:118-129`
`maximum_limit_price()`, est elle aussi conditionnee `order_type ==
STP_LMT` a la ligne 123).

---

## 2. CONFIRMATION — formation vs close, oscillation intrabar

`bullish_confirmation()` (`base_setup.py:169-174`) :
```python
def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
    if snapshot.bullish_candle:
        return True
    if snapshot.close is not None and snapshot.open is not None:
        return snapshot.close > snapshot.open
    return False
```
`snapshot.bullish_candle` n'est jamais assigne par `quote_to_market_snapshot`
(confirme audit 09 Axe 3, reconfirme ici par grep sur
`stock_market_monitor.py` — la fonction ne l'assigne pas et le defaut
dataclass `False` s'applique). La confirmation retombe donc **toujours**
sur `close > open` du `MarketSnapshot` courant. Comme etabli audit 09 Axe 3,
`close`/`open` proviennent de la derniere ligne de `historical_bars` en
mode hybrid — une bougie dont le caractere reellement clos n'est pas
garanti cote code applicatif (INCERTAIN, meme reserve que 09).

### Preuve empirique d'oscillation intrabar sur TXN (donnee reelle, pas hypothetique)

Les 5 premiers evenements `stock_analysis` de TXN_20260713_001
(17:45:42 -> 17:56:14) partagent tous **le meme `snapshot.open = 300.94`**
— c'est-a-dire qu'ils lisent la meme bougie 15m encore en formation
(confirme par le changement d'`open` a 300.44 sur l'evenement suivant,
18:03:13, qui marque le debut d'une nouvelle bougie). Sur cette fenetre,
`snapshot.close` (donc `bullish_confirmation()`) oscille :

| timestamp | open (fixe) | close | `close > open` (bullish_confirmation) |
|---|---|---|---|
| 17:45:42 | 300.94 | 300.85 | **False** |
| 17:46:12 | 300.94 | 300.85 | **False** |
| 17:46:54 | 300.94 | 301.01 | **True** |
| 17:50:37 | 300.94 | 300.93 | **False** |
| 17:56:14 | 300.94 | 300.53 | **False** |

**Le verdict de `bullish_confirmation()` bascule False -> True -> False en
moins de 5 minutes, a l'interieur de la meme bougie 15m non close**, avec
des donnees TXN reelles. Combine a la condition `snapshot.price <=
snapshot.ema_20` (elle-meme jamais evaluable ici faute d'EMA, mais tout
aussi instantanee/sans lissage), un `ENTRY_READY` — si le garde EMA
n'existait pas — pourrait etre emis a 17:46:54 puis retombe a `HOLD` 4
minutes plus tard sur le meme repli de marche, sans qu'aucune des deux
lectures ne soit plus "vraie" que l'autre : aucune des deux n'est une
bougie close. C'est une preuve directe, sur donnees reelles, que la
condition d'entree de `pullback_continuation` peut techniquement flapper
plusieurs fois par bougie — le seul frein reel etant, empiriquement, que le
garde EMA (section 0) bloque tout avant que ce flapping n'ait de
consequence.

---

## 3. STOP — quel niveau part au broker, dynamique ou statique ?

`pullback_continuation.py:45` : `stop_loss=self.stop_loss` ->
`base_setup.py:54-58` : `trailing.get("initial_stop")` = valeur STATIQUE
lue telle quelle dans `trailing_stop_loss.initial_stop` (297.0 pour TXN).

### Coherence avec le stop reellement transmis au broker

`entry_order_executor.py:151` : `trailing_stop =
_trailing_initial_stop(effective_setup.get("config", {}))` — relit
**independamment** `trailing_stop_loss.initial_stop` depuis la config (pas
`signal.stop_loss`), puis `order_manager.py:91` : `risk_decision.stop_loss =
trailing_stop` **ecrase** toute valeur portee par `risk_decision`
auparavant. Contrairement a `momentum_breakout` (qui recalcule et **ecrase**
`trailing_stop_loss.initial_stop` via `metadata["trailing_stop_overrides"]`,
audit 04/05), `pullback_continuation.evaluate()` ne renseigne **jamais**
`signal.metadata` — `_setup_with_signal_overrides`
(`entry_order_executor.py:352-388`) ne trouve donc aucun override et
retourne le `setup` tel quel. **Pour ce type precis, `self.stop_loss` (lu
par `evaluate()`) et `_trailing_initial_stop` (relu independamment par
`order_manager`) pointent vers le meme champ de config
(`trailing_stop_loss.initial_stop`) et produisent donc la meme valeur
numerique** (297.0 pour TXN) — contrairement a `breakout_retest`/
`aggressive_rebound` ou l'audit 04 documentait deja cette double lecture
comme "deconnectee" (le champ etant identique, la deconnexion n'a pas
d'effet numerique observable pour ce type, sauf si la config change entre
les deux lectures dans le meme tick, cas non observe).

### "Stop dynamique" declare mais jamais calcule dynamiquement

Le JSON de TXN declare un stop sophistique :
`trailing_stop_loss.mode="AUTO_INTELLIGENT"`,
`calculation.method="HYBRID_ATR_STRUCTURE"`,
`calculation.atr.multiplier_initial="AUTO"`,
`calculation.structure.reference="INTRADAY_SUPPORT"`. Grep exhaustif de ces
identifiants (`HYBRID_ATR_STRUCTURE`, `AUTO_INTELLIGENT`) dans `app/` :
present uniquement dans des fichiers d'**authoring/validation a la creation
du setup** (`setup_template_service.py`, `semantic_validation_service.py`,
`opportunity_to_scenario_mapper.py`, `canonical_model_builder.py`,
`text_converter.py`) — **aucune occurrence dans le chemin d'evaluation live
ou dans `order_manager.py`/`entry_order_executor.py`** au-dela de la simple
lecture de `initial_stop`. Contrairement a `momentum_breakout`, dont
`_initial_stop()` (`momentum_breakout.py:800-841`) recalcule reellement un
stop a partir de niveaux de support **live** a chaque tick,
`pullback_continuation` **ne recalcule jamais** ce stop au runtime : 297.0
est une valeur figee a la creation du setup (vraisemblablement par le
module d'authoring/intelligence, hors perimetre de ce lot), relue telle
quelle par `evaluate()` comme par `order_manager`. Le libelle
`"AUTO_INTELLIGENT"`/`"HYBRID_ATR_STRUCTURE"` decrit donc une **intention de
conception**, pas un comportement du moteur de signal en production.

**Verdict section 3** : le stop transmis est coherent en valeur avec celui
lu par `evaluate()` (pas de bug de desynchronisation numerique pour ce
type precis), mais il est **statique** malgre son etiquetage
"AUTO_INTELLIGENT" — aucune donnee empirique disponible pour verifier ce
point en conditions reelles puisque, section 0, aucun stop n'a jamais ete
reellement transmis au broker pour ce type.

---

## 4. MEMOIRE / SEQUENCE

`evaluate()` ne recoit que `(snapshot, current_status)` — signature
identique aux 7 autres types (confirme audit 05 section C, reconfirme ici).
`current_status` **est** la seule memoire disponible, et `evaluate()`
l'exploite reellement pour structurer une sequence en 2 etapes :

1. `WAITING_ACTIVATION` + `ema_20 > ema_50` -> `STATUS_CHANGE` vers
   `WAITING_ENTRY_SIGNAL` (`:30-35`) — "j'ai detecte la tendance haussiere".
2. `WAITING_ENTRY_SIGNAL` + `price <= ema_20` + bougie haussiere ->
   `ENTRY_READY` (`:36-46`) — "le repli + confirmation ont eu lieu".

C'est donc une **sequence a memoire faible mais reelle**, portee
exclusivement par le statut persiste en base (pas par un champ dedie type
`retest_touched` que d'autres types pourraient vouloir, cf. audit 05
candidat 3) : le setup doit **avoir ete** en `WAITING_ENTRY_SIGNAL` (donc
avoir deja confirme `ema_20 > ema_50` a un tick anterieur) avant que la
branche d'entree ne puisse s'activer. Ce n'est **pas** une reevaluation
totale a chaque tick sans memoire — sur ce point precis,
`pullback_continuation` est mieux construit que `range_breakout` (aucune
notion de sequence, audit 09).

**Mais cette memoire ne porte que sur le statut, pas sur la zone de
pullback elle-meme** : rien dans `evaluate()` ne verifie que le prix a
**effectivement touche** `pullback.zone_min`/`zone_max` ou
`support_zone.min`/`max` avant de confirmer une entree — la seule
condition est `price <= ema_20`, un niveau **mobile**, sans rapport
garanti avec la zone 300.00-303.50 declaree (l'EMA20 peut se trouver a
n'importe quel niveau selon l'historique de prix, y compris au-dessus de
`zone_max` ou en dessous de `zone_min`). Aucune trace n'est conservee du
plus bas atteint pendant le repli, aucun compteur de bougies dans la zone,
aucune verification que la "continuation" suit reellement un test de la
zone annoncee. **Zero verification empirique possible sur ce point**
(section 0).

---

## 5. INVALIDATION / SORTIE

C'est ici que l'audit va significativement au-dela de ce qu'`evaluate()`
seul peut reveler : **le vrai mecanisme d'invalidation observe en
production pour `pullback_continuation` ne passe pas par
`PullbackContinuationSetup.evaluate()`** (mort au-dela de la ligne 23,
section 0) **mais par un service completement independant,
`SetupLifecycleService`**, qui possede sa propre logique de lecture de
`support_zone{}` — et c'est ce service, pas le moteur de signal, qui a
reellement invalide TXN_20260713_001 en production.

### Preuve empirique complete sur TXN (timeline reelle)

```sql
SELECT timestamp, event_type, message FROM events
WHERE setup_id='TXN_20260713_001' ORDER BY timestamp ASC;
```

Extrait pertinent (2026-07-13, apres re-armement a 17:54:22) :
```
17:54:51  setup_lifecycle_status_changed  WAITING_ACTIVATION -> BLOCKED (BROKER_TRACKER_STALE)
17:56:14  setup_lifecycle_status_changed  BLOCKED -> WAITING_ACTIVATION (SETUP_VALID)
...       (cycle BLOCKED<->WAITING_ACTIVATION repete 4x, BROKER_TRACKER_STALE / SETUP_VALID)
18:03:49  setup_lifecycle_status_changed  WAITING_ACTIVATION -> BLOCKED (BROKER_TRACKER_STALE)
18:05:21  setup_lifecycle_status_changed  BLOCKED -> INVALIDATED (SUPPORT_BROKEN)
```

Prix reel au moment de l'invalidation (`stock_quote`, meme fenetre) :

```sql
SELECT timestamp, event_type, data_json FROM events
WHERE symbol='TXN' AND timestamp BETWEEN '2026-07-13T18:00:00' AND '2026-07-13T18:10:00';
```

| timestamp | evenement | prix |
|---|---|---|
| 18:03:13 | stock_analysis | close 300.22 |
| 18:03:49 | stock_quote | 300.12 |
| 18:04:18 | stock_quote | 300.07 |
| 18:04:53 | stock_quote | **299.80** |
| 18:05:21 | stock_quote | 299.74 |
| 18:05:21 | setup_lifecycle_status_changed | **BLOCKED -> INVALIDATED (SUPPORT_BROKEN)** |

### Le niveau reellement applique n'est pas le niveau declare comme "invalidation"

`setup_lifecycle_service.py:608-634` (`_invalidation_reason`, branche
`is_long`) :
```python
zone = config.get("support_zone")           # {"min":300.0,"max":303.5,"invalidation_below":299.0}
invalidation_below = _number_or_none(zone.get("invalidation_below"))   # 299.0
...
support_min = _number_or_none(zone.get("min"))                          # 300.0
if invalidation_below is not None and close < invalidation_below:
    return "INVALIDATION_LEVEL_BROKEN"     # close < 299.0
if support_min is not None and close < support_min:
    return "SUPPORT_BROKEN"                # close < 300.0  <-- teste APRES, mais atteint AVANT en pratique
```
Le champ que l'utilisateur a explicitement configure comme niveau
d'invalidation (`support_zone.invalidation_below = 299.0`, echo exact du
texte `notes` du JSON : *"Invalidation these si cloture sous 299.00"*)
n'est **pas** celui qui a declenche la sortie. Comme le prix descend en
traversant `support_zone.min` (300.0, le plancher de la **zone d'entree**,
pas un niveau d'invalidation au sens ou l'utilisateur l'entend) avant
d'atteindre 299.0, c'est la branche `SUPPORT_BROKEN` qui se declenche en
premier — **empiriquement confirme : invalidation a 18:05:21 avec un prix
observe de 299.74-299.80, soit 0.20 a 0.26 USD au-dessus du niveau que le
setup declare vouloir (299.00), et deja franchi une premiere fois a
18:04:53 (299.80) avant meme l'ecriture du statut.**

Ce n'est pas isole a TXN : verification sur les 13 setups
`pullback_continuation` presents dans `data/setups/` — les 8 qui portent
`support_zone{}` presentent **7 fois sur 8** la meme relation
(`support_zone.min == pullback.zone_min` et `invalidation_below <
support_zone.min`, donc `SUPPORT_BROKEN` preempte structurellement
`INVALIDATION_LEVEL_BROKEN`) :

| setup_id | `pullback.zone_min` | `support_zone.min` | `support_zone.invalidation_below` | ecart (`min - invalidation_below`) |
|---|---|---|---|---|
| CAST_20260628_001 | 7.12 | 7.12 | 6.40 | 0.72 |
| CBRL_20260714_001 | 49.20 | 49.20 | 48.85 | 0.35 |
| IRDM_20260630_001 | 52.20 | 52.20 | 49.75 | 2.45 |
| LPTH_20260628_001 | 12.80 | 12.80 | 12.35 | 0.45 |
| POWI_20260628_001 | 78.00 | 78.00 | 77.90 | 0.10 |
| SNEX_20260712_001 | 114.00 | 114.00 | 108.00 | 6.00 |
| TXN_20260713_001 | 300.00 | 300.00 | 299.00 | 1.00 |
| MRVL_20260628_001 (cas inverse) | 263.00 | 260.00 | 260.20 | **-0.20** |

Sur ces 8 setups, **7 invalideraient plus tot que leur seuil declare**
(l'ecart va de 0.10 a 6.00 USD selon le titre) ; `MRVL_20260628_001` est le
seul cas ou `invalidation_below` (260.20) est **au-dessus** de
`support_zone.min` (260.0) — la branche `INVALIDATION_LEVEL_BROKEN`
gagnerait alors la course, produisant le comportement attendu par
accident de configuration, pas par construction du code. Les 5 autres
setups (ALAB, ALGM, BGC, CRDO, ZETA) n'ont pas de `support_zone{}` du tout
— pour ceux-la, `_invalidation_reason` retombe sur
`_entry_thesis_invalidation_level` (`:695-702`), qui lit
`pullback.invalidation_below` (absent de tous ces JSON) puis
`pullback.zone_min` — meme mecanisme, pas de conflit puisqu'il n'y a
qu'une seule source.

### Gate sur `current_status`

Contrairement a l'INVALIDATE de `evaluate()` (Axe 2, audit 09 — non gate,
mais de toute facon inatteignable, section 0), cette invalidation par
`SetupLifecycleService` **est** correctement gatee : `revalidate_and_apply`
(`signal_engine.py:117-135`) n'est appele que si
`current_status in LIFECYCLE_MANAGED_STATUSES`
(`setup_lifecycle_service.py:33-41`, qui contient `WAITING_ACTIVATION` —
le seul statut que `pullback_continuation` atteint jamais en pratique,
section 0) — et la transition ecrite (`WAITING_ACTIVATION -> INVALIDATED`)
**est** autorisee par `ALLOWED_TRANSITIONS` (`state_machine.py:24-38`).
Sur ce point precis, aucun defaut de gate n'est demontre.

**Verdict section 5** : le vrai canal de sortie de `pullback_continuation`
en production n'est pas celui documente dans son propre `evaluate()`
(mort), c'est un service parallele qui lit reellement `support_zone{}` —
mais avec une priorite de champs (`min` avant `invalidation_below`) qui
contredit le texte `notes` et l'intention numerique explicite de
l'utilisateur, avec un ecart mesurable et reproductible.

---

## 6. GATE `current_status` avant emission de signal

`pullback_continuation.py:30,36` teste bien `current_status ==
WAITING_ACTIVATION`/`WAITING_ENTRY_SIGNAL` avant d'emettre
`STATUS_CHANGE`/`ENTRY_READY` — confirme, coherent avec audit 09 Axe 1.
**Mais ce test n'a jamais ete exerce** : le garde EMA (lignes 22-23)
s'execute **avant** tout test de `current_status`, pour tout tick, quel
que soit le statut — la portion "gatee" du code n'a donc, empiriquement,
jamais recu la moindre execution reelle sur les 4 377 evaluations
disponibles (section 0). L'axe 1 de l'audit 09 ("pullback_continuation
gate bien avant ENTRY_READY") reste vrai en lecture de code, mais ce lot
etablit que cette portion du fichier est **du code entierement mort en
production** — une nuance plus severe que "non confirme empiriquement",
c'est "structurellement inatteignable avec le pipeline de donnees actuel".

L'INVALIDATE propre a `evaluate()` (`:24-29`, ema_50) n'est, pour la meme
raison, **jamais atteint non plus** — l'observation de l'audit 09 ("le
gate est absent, mais aucun `setup_id` pullback_continuation n'apparait
dans l'echantillon de rejets") est ici expliquee : ce n'est pas un silence
statistique, c'est une impossibilite structurelle de declenchement.

---

## 7. CHAMPS CONFIG IGNORES PAR `evaluate()` — valeurs concretes sur TXN

| Champ JSON (TXN, valeur reelle) | Lu par `evaluate()` ? | Lu ailleurs dans le moteur ? |
|---|---|---|
| `pullback.entry_reference = 302.0` | Non (seulement `estimated_entry_price()`, jamais appelee) | Non |
| `pullback.zone_min = 300.0` / `zone_max = 303.5` | Non | **Oui** — `SetupLifecycleService._invalidation_reason`/`_entry_thesis_invalidation_level` (fallback), et `support_zone.min` (valeur identique, 300.0) directement en priorite haute pour l'invalidation (section 5) |
| `pullback.confirmation_required = true` | Non | Non (grep exhaustif `confirmation_required` hors `setup_type_registry.py` : 0 occurrence) |
| `support_zone.min/max/invalidation_below` | Non | **Oui** — meme service, c'est la source reelle de l'invalidation (section 5) |
| `entry.trigger_price = 302.0` / `entry_price = 302.0` | Non (evaluate() calcule dynamiquement, section 1) | **Oui** — `SetupLifecycleService._entry_reference_price` (`:496-504`) l'utilise pour l'anti-chase (`anti_chase.max_price_above_entry_percent=1.5%` -> seuil 306.53) et pour le garde `STOP_ABOVE_ENTRY_FOR_LONG` (`:178-180`) |
| `entry.maximum_limit_price = 304.0` / `limit_price = 302.5` | Non (`order_type=LMT`, la garde ne s'applique qu'a `STP_LMT`, section 1) | **Oui** — `SetupLifecycleService._maximum_limit_price` (`:507-521`) les lit pour le calcul anti-chase, meme si le pipeline d'ordre lui-meme ne les applique jamais comme plafond pour ce `order_type` |
| `volume_confirmation.*` (8 sous-champs : `fast_volume_ratio_min`, `normal_volume_ratio_min`, `confirmed_volume_ratio_min`, `confirmed_hold_bars`, `close_above_level_required`, `reject_detection_enabled`, `max_upper_wick_ratio`, `enabled=true`) | Non — grep `volume` dans `pullback_continuation.py` : 0 occurrence | Non trouve ailleurs sur le chemin live |
| `trend_filter.enabled = true`, `trend_filter.required_trend = "uptrend"` | Non — `evaluate()` teste `ema_20 > ema_50` en dur, sans lire ce flag (meme desactive, le test resterait actif) | Non |
| `anti_chase.enabled/action_if_too_far/block_entry_if_price_above_maximum_limit` | Non | Partiellement — `anti_chase.max_price_above_entry_percent` seul est lu (ci-dessus), les 3 autres cles ne le sont pas (grep confirme) |
| `session_policy.*` (10 sous-champs) | Non — ce n'est pas le role d'`evaluate()`, c'est `apply_entry_session_policy` (`signal_engine.py:82`) qui s'en charge en aval, hors perimetre de ce fichier | Oui, ailleurs |
| `risk.*`, `broker_safety.*`, `management.*`, `targets` | Non lus par `evaluate()` (comme pour les autres types) | Oui, ailleurs dans le pipeline d'entree/gestion |

**Constat additionnel par rapport a l'audit 09 (qui portait uniquement sur
`evaluate()`)** : `support_zone{}` et `entry.trigger_price`/
`maximum_limit_price` ne sont **pas** des champs totalement morts dans le
moteur au sens large — ils sont reellement consommes par
`SetupLifecycleService`. L'affirmation "jamais lu" doit donc etre precisee
: **jamais lu par le composant qui decide de l'entree** (`evaluate()`),
mais bien lu par le composant qui decide de la sortie/du blocage
(`SetupLifecycleService`) — deux moteurs paralleles qui n'utilisent pas la
meme reference de "prix d'entree prevu" (302.0 statique cote lifecycle vs
`high + 0.02` dynamique cote `evaluate()`, section 1), ce qui est en soi
une source d'incoherence non documentee avant ce lot.

---

## 8. SIMULATION — 2 scenarios reels

### Scenario 1 (nominal attendu, donnees reelles TXN_20260713_001, 2026-07-13)

Le contexte est, sur le papier, exactement celui vise par le setup : TXN
cotait ~305 a la creation (`current_price_approx: 305.13` dans le JSON),
en repli de -2% ce jour-la (`notes`), avec une zone de pullback
300.00-303.50 attendant une "bougie 15m haussiere de confirmation". Les 6
ticks reels captures montrent le prix **entrant effectivement dans la
zone** (300.87 -> 300.21, a l'interieur de 300.00-303.50 des le premier
tick disponible) et une bougie 15m qui devient meme haussiere a 17:46:54
(section 2). **C'est un scenario textuellement ideal pour ce setup.**
Verdict reel du moteur : `HOLD / "Waiting for EMA data"` sur les 6/6
ticks, aucune transition, aucun signal — parce que `snapshot.ema_20`/
`ema_50` sont `None` (section 0). **Le verdict du moteur ne correspond pas
du tout a l'intention du setup** : un setup dont les conditions de marche
declarees (repli dans la zone, bougie haussiere) sont reunies produit
neanmoins un `HOLD` inconditionnel, pour une raison sans rapport avec la
logique metier du type (absence de donnee technique, pas absence de
signal de marche).

### Scenario 2 (limite/ambigu — sortie reelle 4 jours plus tard, meme setup)

Le 2026-07-13 en fin de journee, le prix continue de baisser sous la zone.
`SetupLifecycleService` (pas `evaluate()`) invalide reellement le setup a
18:05:21 (`SUPPORT_BROKEN`), avec un prix observe de 299.74-299.80 (section
5) — **avant** que le niveau explicitement declare par l'utilisateur comme
"invalidation" (299.00, `support_zone.invalidation_below`, echo du texte
`notes`) ne soit atteint. Verdict reel de la base : `setups.status =
'INVALIDATED'`, `status_reason = 'SUPPORT_BROKEN'` (confirme par requete
directe sur la table `setups`, ci-dessous) — **le setup a ete tue ~0.20 a
0.26 USD trop tot par rapport a sa propre these ecrite**, par un champ de
configuration (`support_zone.min`, le plancher de la zone d'ENTREE) que
l'utilisateur n'a jamais pense comme un niveau de sortie.

```sql
SELECT setup_id, status, status_reason, updated_at FROM setups WHERE setup_id='TXN_20260713_001';
-- ('TXN_20260713_001', 'INVALIDATED', 'SUPPORT_BROKEN', '2026-07-17T14:02:30.225443+00:00')
```

Point notable additionnel : le fichier `data/setups/TXN_20260713_001.json`
lu au debut de cet audit (celui livre comme cas de test) **ne reflete pas**
ce statut — son `entry_decision.status` affiche `"EVALUATED"` /
`"WAIT_FOR_PULLBACK"` / `can_send_order: false`, sans aucune mention
d'invalidation. Le statut d'execution vit dans la table `setups`
(`status`/`status_reason`), pas dans le fichier de config JSON : consulter
uniquement le JSON, comme le fait l'utilisateur via son panneau de creation
de setup, donne une image perimee de l'etat reel du setup depuis le
2026-07-13T18:05.

---

## LISTE NUMEROTEE DES PROBLEMES PROPRES A `pullback_continuation`

### P1 — Le filtre EMA20/EMA50 rend `evaluate()` inatteignable au-dela de sa 2e ligne, 100% du temps, sur toute la periode disponible

**Preuve** : `pullback_continuation.py:22-23` ; requete exhaustive sur les
96 344 `stock_analysis` -> 4 377/4 377 occurrences `pullback_continuation`
= `HOLD "Waiting for EMA data"`, `status=WAITING_ACTIVATION` (section 0).
Cause : `quote_to_market_snapshot()` (`stock_market_monitor.py:492-591`)
n'assigne jamais `ema_20`/`ema_50` — champ absent de bout en bout de la
chaine TWS -> snapshot (grep exhaustif de `tws_connector.py` : 0
occurrence). **Impact** : `pullback_continuation` n'a jamais, en
production, execute son test de tendance, sa detection de repli, sa
confirmation de bougie ni son INVALIDATE propre — c'est un type de setup
entierement non fonctionnel sur le chemin live, quelle que soit la qualite
de la config utilisateur. Le meme defaut touche independamment le scoring
`"pullback_continuation"` du scanner d'opportunites
(`opportunities/scanner.py:721-729`, meme cle `ema_20`/`ema_50` jamais
peuplee sur son `quote`).

### P2 — L'invalidation reelle vient d'un service parallele qui invalide plus tot que le niveau declare par l'utilisateur

**Preuve** : `setup_lifecycle_service.py:608-634`
(`_invalidation_reason`) teste `support_zone.min` (300.0 pour TXN, le
plancher de la ZONE D'ENTREE) avant, dans l'ordre d'evaluation reel,
d'atteindre `support_zone.invalidation_below` (299.0, le niveau que
`notes` et l'utilisateur declarent explicitement vouloir) puisque le prix
descendant croise 300.0 avant 299.0. Confirme empiriquement sur TXN :
invalidation a 18:05:21 avec prix reel 299.74-299.80 (section 5), et
generalise sur 7/8 setups `pullback_continuation` ayant un `support_zone{}`
(section 5, table). **Impact** : le setup sort de these 0.10 a 6.00 USD
(selon le titre) plus tot que ce que l'utilisateur a explicitement
configure/ecrit dans `notes`, sur la quasi-totalite des setups reels de ce
type — silencieusement, sans avertissement, puisque l'evenement produit
(`SUPPORT_BROKEN`) ne mentionne pas l'ecart avec
`invalidation_below`.

### P3 — Le trigger d'entree calcule par `evaluate()` n'a aucun lien avec la reference d'entree utilisee par le reste du moteur

**Preuve** : `pullback_continuation.py:38-44`
(`entry_price = round(snapshot.high_or_price + trigger_offset, 2)`,
dynamique) vs `setup_lifecycle_service.py:496-504`
(`_entry_reference_price`, qui lit `entry.trigger_price`=302.0, statique)
— **deux fonctions du meme moteur repondent differemment a "quel est le
prix d'entree prevu de ce setup ?"**, l'une pour decider si emettre
`ENTRY_READY` (jamais atteinte, P1), l'autre pour calculer le seuil
anti-chase et le garde stop-vs-entree (`:178-180`), activement utilisee
tant que le setup est `WAITING_ACTIVATION`. **Impact** : si P1 etait
corrige (EMA peuplees), le prix reellement transmis au broker
(`round(high+0.02,2)`, section 1) pourrait diverger significativement de
302.0/303.5 (zone declaree) sans qu'aucun garde-fou de `evaluate()` ne
l'empeche, alors meme que le service de lifecycle, lui, continuerait de
raisonner sur 302.0 comme reference stable pour ses propres verifications
(anti-chase, coherence stop/entree) — un cas ou le "prix prevu" affiche a
l'utilisateur (`entry_decision.planned_entry=302.0`) ne serait pas celui
transmis au broker.

### P4 — `volume_confirmation{}` (8 champs, actif dans 100% des JSON pullback_continuation echantillonnes) et `trend_filter.enabled` sont entierement decoratifs

**Preuve** : `grep -i volume app/setups/pullback_continuation.py` -> 0
occurrence ; `evaluate()` (`:22-46`) ne lit ni `trend_filter.enabled` ni
`trend_filter.required_trend`, le test de tendance (`ema_20 > ema_50`) est
code en dur et s'executerait meme si l'utilisateur mettait
`trend_filter.enabled=false`. **Impact** : TXN declare un filtre volume
complet (`fast_volume_ratio_min=1.5`, `confirmed_hold_bars=2`,
`reject_detection_enabled=true`, etc., valeurs reelles du JSON,
`enabled: true`) qui ne joue absolument aucun role dans la decision
d'entree — meme si P1 etait corrige, un breakout sur volume nul
declencherait une entree identique a un breakout sur 5x le volume moyen.

### P5 — Le panneau UI ("Ce que cherche le setup") reflete honnetement l'absence de donnees, mais aucun signal ne remonte a l'utilisateur que le type est structurellement bloque

**Preuve** : `setup_conditions.py:103-119` (`_pullback_uptrend`,
`_pullback_to_ema20`) retournent `ConditionCheck(met=None,
observed_value="Donnees EMA indisponibles")` quand `snapshot.ema_20` est
`None` — cette partie ne ment pas. Mais rien dans le panneau, dans
`entry_decision` (JSON) ni dans un evenement dedie n'indique a
l'utilisateur que cette situation est **permanente** (P1) plutot que
temporaire ("en attente de la prochaine cotation"). Le `display_message`
du JSON TXN ("Attente d'un repli en zone... avant transmission") laisse
entendre une attente normale, pas un blocage structurel. **Impact** :
un utilisateur consultant le setup ne peut pas distinguer "en attente
legitime" de "ne demarrera jamais", ce qui est pourtant le cas reel pour
ce type depuis le debut de la periode observee (2026-06-02).

---

## INCERTITUDES RESIDUELLES

1. **Comportement du moteur au-dela de la ligne 23 d'`evaluate()`
   (confirmation, memoire de zone, INVALIDATE via `ema_50`, calcul
   d'`entry_price` reel transmis)** : entierement fonde sur une lecture de
   code, **zero verification empirique possible** — confirme par le scan
   exhaustif des 96 344 `stock_analysis` (section 0) : aucune occurrence de
   `pullback_continuation` n'a jamais depasse `HOLD "Waiting for EMA
   data"`. Toute conclusion des sections 1, 3, 4, 6 sur le comportement
   "si les conditions etaient reunies" est donc une simulation de code, pas
   un fait observe.
2. **Mecanisme exact de peuplement (ou non) de `ema_20`/`ema_50` avant la
   periode disponible (avant 2026-06-02)** : non verifiable, la base ne
   remonte pas plus loin. Il n'est pas exclu que ce champ ait ete alimente
   par un autre chemin dans une version anterieure du code, puis regresse
   silencieusement — aucune trace de changelog n'a ete consultee dans ce
   lot (hors perimetre : lecture de donnees uniquement, pas d'historique
   git du fichier `stock_market_monitor.py`).
3. **Le "gap" entre `support_zone.min` et `support_zone.invalidation_below`
   (P2) est-il un choix de conception assume (le plancher de la zone
   d'entree est intentionnellement le vrai niveau d'invalidation, et
   `invalidation_below` un niveau plus bas encore, "de derniere chance") ou
   un bug de configuration/lecture ?** Rien dans le code ni dans les
   commentaires de `setup_lifecycle_service.py` ne documente explicitement
   la priorite `support_zone.min` avant `invalidation_below` comme
   deliberee (a la difference d'autres commentaires du meme fichier, ex.
   `:210-215`, qui documentent explicitement le choix de ne pas utiliser
   `initial_stop` avant position ouverte). L'ecart avec le texte `notes` de
   TXN ("cloture sous 299.00") suggere fortement une divergence non
   voulue, mais ceci reste une inference, pas une confirmation par les
   auteurs du code.
4. **`app/features/store.py` et `app/opportunities/scanner.py`** : lus
   uniquement pour tracer la provenance (ou l'absence) de `ema_20`/`ema_50`
   ; leur role complet dans le pipeline (frequence d'appel, si un daily
   feed alimente un jour `historical_ema_20`/`historical_ema_50` vers le
   scanner) n'a pas ete audite en profondeur — hors perimetre de ce lot
   (le scanner ne nourrit pas `evaluate()`).
5. **Table `orders`** : non consultee dans ce lot (aucun ordre
   `pullback_continuation` n'existe de toute facon, P1 rend la question
   sans objet pour la periode disponible).
