# Audit en lecture seule — Lot 13 : `aggressive_rebound` (cas réel JOBY)

Mode lecture seule strict. Aucun fichier de code n'a été modifié — seul ce
fichier a été créé. Prérequis lu en entier avant de commencer :
`audit/09_normes_transverses.md` (axes 1-5, tableau des normes) et
`audit/05_normalisation.md` (référencé par 09). Toutes les requêtes SQL
ci-dessous ont été exécutées en lecture seule
(`sqlite3.connect('file:data/trading_state.sqlite?mode=ro', uri=True)`),
filtrées systématiquement par `symbol='JOBY'` et/ou `event_type=` (jamais de
scan complet de `events`).

**Correction factuelle préalable, importante** : la consigne de ce lot
affirme que `JOBY_20260703_001` est « le setup déjà identifié dans l'audit 09
avec 807 rejets de transition ». C'est **inexact** — vérifié empiriquement
ci-dessous (§6). Les 807 rejets `ENTRY_ORDER_PLACED -> INVALIDATED`
documentés par l'audit 09 appartiennent à `JOBY_20260628_001`, un setup
`aggressive_rebound` antérieur et distinct sur le même symbole JOBY, supprimé
le 2026-06-30 (`setup_deleted`). `JOBY_20260703_001` (le fichier réellement
fourni en cas de test, `data/setups/JOBY_20260703_001.json`) est un setup
ultérieur, créé le 2026-07-03, qui n'a **aucun** événement
`setup_transition_rejected` sur toute sa durée de vie :

```sql
SELECT setup_id, COUNT(*), MIN(timestamp), MAX(timestamp)
FROM events WHERE event_type='setup_transition_rejected' AND symbol='JOBY'
GROUP BY setup_id;
-- -> ('JOBY_20260628_001', 807, '2026-06-29T14:22:44...', '2026-06-30T09:46:25...')
-- (aucune ligne pour JOBY_20260703_001)
```

Ce lot suit donc la consigne (JOBY_20260703_001 comme fil conducteur pour les
§1-5, 7-8) et restitue le vrai historique des 807 rejets sur
`JOBY_20260628_001` au §6, en le signalant explicitement comme un setup
différent. Les deux sont exploités : `JOBY_20260703_001` a produit une
richesse de données bien supérieure pour ce lot (2 993 `stock_analysis` pour
le symbole JOBY sur toute la période, dont ~155 concernent directement ce
setup_id en `WAITING_ENTRY_SIGNAL`, et une invalidation réelle observée en
base — voir §5).

---

## 1. ENTRÉE — d'où vient le prix transmis au broker ?

`app/setups/aggressive_rebound.py:63-75` :

```python
if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
    previous_high = snapshot.previous_high or snapshot.high or high
    if bullish_confirmation(snapshot) and close > previous_high:
        entry = previous_high + float(
            self.config.get("entry", {}).get("trigger_offset", 0.02)
        )
        return SetupSignal(
            action=SignalAction.ENTRY_READY,
            ...
            entry_price=round(entry, 2),
            stop_loss=self.stop_loss,
        )
```

Le prix d'entrée transmis n'est **jamais** `entry.trigger_price` ni
`entry.entry_price` (les deux valent `8.72` dans le JSON de
JOBY_20260703_001, avec la note explicite *« JOBY doit défendre la zone
8.30-8.50 puis reprendre 8.72 en clôture 15m »*). Le champ `trigger_price`
n'est lu nulle part dans `aggressive_rebound.py` (confirmé par grep — seul
`estimated_entry_price()` le lit, `:24-27`, une méthode jamais appelée par
`evaluate()`, exactement comme `pullback.entry_reference` documenté en
audit 09 pour `pullback_continuation`). Le prix réellement calculé est
`snapshot.previous_high + entry.trigger_offset` (`trigger_offset=0.02` dans
le JSON), où `previous_high` est une valeur de marché courante qui **dérive
librement à chaque tick**, sans aucun ancrage au niveau `8.72` déclaré par le
setup.

Preuve directe sur JOBY_20260703_001, en filtrant les événements
`stock_analysis` où le signal émis vaut `ENTRY_READY` pour ce setup_id le
2026-07-06 :

```sql
-- stock_analysis, symbol='JOBY', timestamp entre 2026-07-06T13:59 et 15:16:30
```

| horodatage | `close` | `previous_high` (snapshot) | `entry_price` calculé (signal) |
|---|---|---|---|
| 14:00:29 | 8.86 | 8.49 | **8.51** |
| 14:03:02 | 8.93 | 8.92 | 8.94 |
| 14:17:02 | 9.07 | 9.04 | 9.06 |
| 14:28:51 | 9.13 | 9.04 | 9.06 |
| 14:48:34 | 9.22 | 9.16 | 9.18 |
| 15:15:18 | 9.25 | 9.23 | **9.25** |

Le prix « d'entrée » proposé par le moteur dérive de **8.51 à 9.25** en
75 minutes sur le même setup_id, sans jamais toucher `8.72` — l'écart va de
-2.4% à +6.1% par rapport au niveau que le JSON et les `notes` déclarent
vouloir. Le cas 14:00:29 est le plus grave : `entry_price=8.51` est calculé
à partir d'un `previous_high` **déjà obsolète** (8.49, capturé avant la
pause `WAITING_AFTER_OPEN_BARS` visible dans les événements précédents,
14:00:29 étant la première évaluation après la levée de ce gate) alors que
`close` valait déjà 8.86 au même instant — soit un prix de déclenchement
calculé **sous le marché courant**, ce qui pour un ordre `STP_LMT` (mode
réellement configuré, `entry.order_type: "STP_LMT"`) revient à envoyer un
stop déjà franchi, donc un ordre quasi-marché plutôt qu'un déclenchement de
cassure contrôlé.

Confirmation que cette valeur calculée est bien celle qui alimente la
construction d'ordre côté broker (pas juste un artefact de log) :
`app/engine/entry_order_executor.py:136,153,196` (`signal.entry_price` passé
à `risk_engine.evaluate(entry_price=signal.entry_price, ...)`) puis
`app/engine/order_manager.py:219-232` (`risk_decision.entry_price` devient
`trigger_price`/`limit_price` de l'ordre `STP`/`STP_LMT` réel). Il n'existe
aucun point du pipeline où `entry.trigger_price` (8.72) est réintroduit pour
borner ou valider ce calcul.

**Verdict §1** : le trigger transmis ne correspond pas à la config déclarée
— même défaut de nature que celui trouvé sur `breakout_retest` (high+offset
au lieu du niveau configuré), mais ici la dérive est plus grave car
`previous_high` n'est pas recalculé une fois puis figé : il est relu à
neuf à **chaque tick** tant que `current_status == WAITING_ENTRY_SIGNAL`,
donc le niveau de déclenchement « chasse » le marché indéfiniment (voir §4).

---

## 2. CONFIRMATION — bougie en formation ou close ? Le volume compte-t-il ?

`bullish_confirmation()` (`app/setups/base_setup.py:169-174`), appelée par
`aggressive_rebound.py:65` :

```python
def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
    if snapshot.bullish_candle:
        return True
    if snapshot.close is not None and snapshot.open is not None:
        return snapshot.close > snapshot.open
    return False
```

`snapshot.bullish_candle` n'est jamais assigné par
`quote_to_market_snapshot` en flux réel (confirmé audit 09 §Axe 3) — vérifié
directement sur les événements JOBY : **tous** les snapshots inspectés
portent `"bullish_candle": false`, y compris ceux où le signal `ENTRY_READY`
est émis. La confirmation « haussière » se réduit donc systématiquement au
fallback `close > open` de la même barre `close` dont le caractère
réellement clos n'est pas garanti (audit 09, même réserve).

Sur le champ `rebound_confirmation.require_volume_confirmation: true` du
JSON JOBY (également porté par le bloc `volume_confirmation` top-niveau,
`fast_volume_ratio_min: 1.2`, `normal_volume_ratio_min: 1.0`,
`confirmed_volume_ratio_min: 0.8`) : `grep -in volume
app/setups/aggressive_rebound.py` → **0 occurrence**, confirmé.

Preuve empirique sur JOBY_20260703_001 — événement `stock_analysis` du
2026-07-06T14:03:02 :

```
snapshot: close=8.93, open=8.85, previous_high=8.92, volume_ratio=0.1236
processed[JOBY_20260703_001]: action=ENTRY_READY, reason="Bullish rebound confirmed",
                               entry_price=8.94, stop_loss=8.18, readiness_percent=100.0,
                               opportunity_score.blocking_checks=[]
```

`volume_ratio=0.1236` signifie que le volume de la barre 15m représentait
**12,36 %** du volume moyen de référence — très en-dessous même du seuil le
plus permissif déclaré (`confirmed_volume_ratio_min: 0.8`, soit 80 %). Le
signal est pourtant émis avec un score de préparation de **100 %** et
`blocking_checks: []` (aucun frein). L'événement précédent (14:01:57,
`volume_ratio=0.0637`, 6,37 % du volume moyen) donne `action=HOLD` — mais
uniquement parce que `close (8.91) > previous_high (8.92)` était faux à cet
instant précis, pas parce que le volume était insuffisant : le panneau
`opportunity_score.waiting_checks` de cet événement ne mentionne aucune
condition de volume manquante non plus.

**Verdict §2** : confirme et illustre concrètement, sur données réelles
JOBY, que `rebound_confirmation.require_volume_confirmation: true` et le
bloc `volume_confirmation` entier « ne font littéralement rien » (formule de
l'audit 09) — un rebond a été validé et un ordre `ENTRY_READY` généré à
100 % de score sur un volume représentant un huitième de la normale.

---

## 3. STOP — quel stop part réellement au broker ?

`self.stop_loss` (`app/setups/base_setup.py:54-58`) :

```python
@property
def stop_loss(self) -> float | None:
    trailing = self.config.get("trailing_stop_loss", {})
    stop = trailing.get("initial_stop") if isinstance(trailing, dict) else None
    return float(stop) if stop is not None else None
```

`aggressive_rebound.py:74` (`stop_loss=self.stop_loss`) — le stop transmis
avec le signal `ENTRY_READY` est **toujours**
`trailing_stop_loss.initial_stop`, une valeur statique du JSON
(`8.18` pour JOBY), jamais recalculée dynamiquement dans `evaluate()`
lui-même (aucune fonction de calcul ATR/structure n'est appelée côté
`aggressive_rebound.py` — le bloc `trailing_stop_loss.calculation` très
détaillé du JSON, méthode `HYBRID_ATR_STRUCTURE`, buffers, etc., n'est pas lu
par ce fichier). Confirmé sur les 11 événements `ENTRY_READY` de JOBY
relevés au §1/§2 : `stop_loss` vaut **8.18 dans les 11 cas**, y compris
lorsque `entry_price` calculé grimpe jusqu'à 9.25 — soit un risque par
action affiché qui passe de `8.51-8.18=0.33` à `9.25-8.18=1.07`, plus de
**3x plus large**, sans qu'aucun recalcul de position sizing visible dans ce
fichier n'en tienne compte (le sizing se fait en aval, dans le moteur de
risque, hors périmètre de ce fichier).

Fait notable : dans ce cas précis, `trailing_stop_loss.initial_stop` (8.18)
est **numériquement identique** à `support_zone.invalidation_below` (8.18)
— alors que le seuil réellement utilisé par la branche INVALIDATE de
`evaluate()` est `support_zone.min` (8.30, voir §5). Autrement dit le stop
envoyé au broker (8.18) est cohérent avec l'intention déclarée du setup, mais
**pas avec le seuil que le moteur utilise lui-même pour décider
d'invalider** (8.30) — deux branches de la même méthode `evaluate()` lisent
des niveaux différents pour, en théorie, protéger la même position contre le
même risque de rupture de support.

**Verdict §3** : le stop transmis est stable et correctement lié à
`trailing_stop_loss.initial_stop` (pas de bug de lecture ici, contrairement
à l'INVALIDATE), mais il est **totalement découplé** du prix d'entrée
dynamique calculé en §1 — un stop fixe de 8.18 combiné à un entry qui dérive
jusqu'à 9.25 fait exploser le risque réel par rapport au risque affiché dans
`entry_decision.planned_vs_current_risk` du JSON statique (`risk_per_share:
0.59`, calculé sur l'hypothèse `entry=8.77`).

---

## 4. MÉMOIRE / SÉQUENCE

`aggressive_rebound.py` a une notion de séquence **uniquement via
`current_status`** (la state machine) : `WAITING_ACTIVATION` doit d'abord
transitionner vers `WAITING_ENTRY_SIGNAL` (`:57-62`, « le prix a touché la
zone de support ») avant que la branche de confirmation (`:63-75`) ne soit
évaluée. Il n'y a en revanche **aucune mémoire du niveau ou de l'instant où
le support a été touché** : une fois le statut `WAITING_ENTRY_SIGNAL`
atteint, la seule chose que le moteur retient est *que* le support a été
touché — jamais *où* (quel `previous_high` était valide à ce moment-là) ni
*quand*. `previous_high` est relu à neuf depuis `snapshot.previous_high` à
chaque tick (`:64`), donc le niveau de reprise recherché **flotte** avec le
marché tant que `WAITING_ENTRY_SIGNAL` dure — démontré au §1 (8.51 → 9.25 en
75 minutes sur le même setup_id).

Cela a une conséquence directement vérifiable : le JSON déclare
`anti_chase.enabled: true`,
`anti_chase.max_price_above_entry_percent: 1.5`,
`anti_chase.action_if_too_far: "MISSED_REBOUND_WAIT_RETEST"`,
`anti_chase.block_entry_if_price_above_maximum_limit: true` — un
garde-fou explicitement conçu pour empêcher exactement ce qui a été observé
(un entry qui s'éloigne du support). `grep -in anti_chase
app/setups/aggressive_rebound.py` → **0 occurrence**. Le signal
`ENTRY_READY` à 9.25 (2026-07-06T15:15:18) représente **+8.8%** par rapport
au haut de la zone de support (`support_zone.max=8.5`) et **+6.1%** par
rapport à `entry.trigger_price` (8.72) — largement au-delà du seuil de 1.5%
que le setup déclare vouloir respecter, sans qu'aucun frein ne se déclenche
(`opportunity_score.blocking_checks=[]` à cet instant, vérifié dans les
données ci-dessus).

**Verdict §4** : aucune mémoire de séquence au sens strict (aucun état
persisté au-delà de `current_status`), et le garde-fou de config censé
compenser cette absence de mémoire (`anti_chase`) est totalement inerte —
c'est la cause directe de la dérive du trigger documentée au §1.

---

## 5. INVALIDATION / SORTIE — creusé sur JOBY_20260703_001

`aggressive_rebound.py:44-56` :

```python
support = self.config.get("support_zone", {})
low = float(support["min"])                                    # 8.30
high = float(support["max"])                                   # 8.50
invalidation = self.config.get("invalidation", {})              # {} — clé absente
close_below = float(invalidation.get("close_below", low))       # fallback -> 8.30
close = snapshot.close if snapshot.close is not None else snapshot.price

if close < close_below:
    return SetupSignal(action=SignalAction.INVALIDATE,
                        reason="Close below support invalidation",
                        target_status=SetupStatus.INVALIDATED)
```

Sur JOBY_20260703_001 :

| seuil | valeur | source |
|---|---|---|
| `support_zone.min` (fallback réellement utilisé) | **8.30** | `data/setups/JOBY_20260703_001.json` (`support_zone.min`) |
| `support_zone.invalidation_below` (déclaré, jamais lu) | **8.18** | idem (`support_zone.invalidation_below`), identique à `trailing_stop_loss.initial_stop` |
| écart | 0.12 (confirme le tableau de l'audit 09, ligne JOBY_20260703_001) | |

Invalidation réelle en base — reconstruction complète depuis `setups` et
`events` :

```sql
SELECT status FROM setups WHERE setup_id='JOBY_20260703_001';
-- -> INVALIDATED

SELECT timestamp, message, data_json FROM events
WHERE setup_id='JOBY_20260703_001' AND event_type='setup_status_changed'
ORDER BY timestamp;
-- 2026-07-03T11:20:57 | Price touched support zone   | {"from":"WAITING_ACTIVATION","to":"WAITING_ENTRY_SIGNAL"}
-- 2026-07-07T14:31:29 | Close below support invalidation | {"from":"WAITING_ENTRY_SIGNAL","to":"INVALIDATED"}
```

Le `stock_analysis` correspondant à l'instant exact de l'invalidation
(2026-07-07T14:31:29.772478+00:00) donne :
`snapshot.close = 8.17`, `snapshot.price = 8.20`, `snapshot.open = 8.17`.
`opportunity_score.waiting_checks` affiche littéralement
`{"label": "Invalidation support", "actual": 8.17, "expected": ">= 8.3"}`
— le seuil **effectivement utilisé et affiché est bien 8.30 (le fallback
buggé), jamais 8.18**.

Reconstruction de la trajectoire des 15 dernières barres avant
l'invalidation (`stock_analysis` du 2026-07-07, `close` par tick) :
`8.92 → 8.72 → 8.70 → 8.68 → 8.38 → 8.32 → 8.30 → 8.17 (INVALIDATE)`.
**Point clé, nouveau par rapport à l'audit 09** : la dernière clôture
au-dessus du seuil (8.30, exactement `support_zone.min`, à 14:16:33 —
`close < close_below` est un test strict donc `8.30` ne déclenche pas) est
suivie d'un unique saut de barre 15 minutes plus tard directement à
**8.17**, c'est-à-dire que le marché a traversé toute la zone tampon
`[8.18, 8.30]` (0.12 de large) **en une seule barre**, sans qu'aucune
clôture intermédiaire n'y soit observée. Conséquence : sur ce cas réel
précis, le bug (seuil 8.30 au lieu de 8.18) **n'a pas produit d'écart de
timing mesurable** — la clôture 8.17 est de toute façon inférieure aux deux
seuils, donc l'INVALIDATE se serait déclenché au même tick avec le seuil
correct. C'est une nuance importante : le défaut est réel et structurel
(prouvé sur 14/14 setups en config), mais sur JOBY_20260703_001
spécifiquement, la granularité 15m du flux a « masqué » sa manifestation
concrète — contrairement à d'autres setups du même type où l'écart
(jusqu'à 4.00 pour AAOI) rend la probabilité d'un franchissement
intermédiaire bien plus élevée.

Une fois `INVALIDATED` atteint, `signal_engine.py:79-80`
(`current_status in TERMINAL_SIGNAL_STATUSES: continue`, avec
`SetupStatus.INVALIDATED` dans `TERMINAL_SIGNAL_STATUSES`,
`signal_engine.py:28`) arrête toute réévaluation de ce setup_id — cohérent
avec `ALLOWED_TRANSITIONS[INVALIDATED] = set()`
(`state_machine.py:189`, statut terminal absorbant).

**Verdict §5** : le bug déjà identifié par l'audit 09 (lecture de
`invalidation.close_below` au lieu de `support_zone.invalidation_below`) est
confirmé au niveau événementiel exact sur JOBY_20260703_001 — le panneau
`opportunity_score` stocké en base affiche noir sur blanc le seuil buggé
(`>= 8.3`) comme critère d'invalidation officiel. La conséquence *observable*
sur ce setup précis est nulle (voir nuance ci-dessus), mais le mécanisme est
bien celui décrit par l'audit 09, pas une simple hypothèse théorique.

---

## 6. GATE `current_status` — chronologie réelle des 807 rejets (setup distinct : JOBY_20260628_001)

Comme signalé en préambule, les 807 rejets ne concernent **pas**
JOBY_20260703_001. Reconstruction complète sur le vrai setup concerné,
`JOBY_20260628_001` (`aggressive_rebound`, même symbole, setup antérieur et
indépendant) :

```sql
SELECT timestamp, event_type, message FROM events
WHERE setup_id='JOBY_20260628_001' ORDER BY timestamp;
```

| horodatage | événement | détail |
|---|---|---|
| 2026-06-28T21:36:12 | `setup_status_changed` | `WAITING_ACTIVATION -> WAITING_ENTRY_SIGNAL`, « Price touched support zone » (seul `setup_status_changed` de toute la vie du setup) |
| 2026-06-29T13:45:41 | `entry_order_submitted` | `trigger_price=8.89`, `limit_price=9.15`, `quantity=21` — accepté par TWS (le setup est donc passé à `ENTRY_ORDER_PLACED`, sans qu'un `setup_status_changed` distinct ne soit journalisé pour cette transition précise) |
| 2026-06-29T13:45:46 | `opportunity_ready` | 100% READY AUTO |
| **2026-06-29T14:22:44** | **1er `setup_transition_rejected`** | `Invalid setup transition: ENTRY_ORDER_PLACED -> INVALIDATED` |
| 2026-06-29T15:39:26 | `protective_stop_submitted` | `stop_loss=8.45` (rejets déjà en cours depuis 1h17) |
| 2026-06-29T15:41:28 | `order_cancelled` | stop annulé |
| 2026-06-29T16:18:57 | `order_status_reconciled` | ordre d'entrée marqué `FILLED` par réconciliation broker |
| **2026-06-30T09:46:25** | **807e / dernier `setup_transition_rejected`** | même message, ~19h04 après le premier |
| 2026-06-30T09:46:34 | `setup_deleted` | le setup est supprimé ~9 secondes après le dernier rejet |

Le message rejeté est **unique** sur les 807 occurrences :
`Invalid setup transition: ENTRY_ORDER_PLACED -> INVALIDATED` (vérifié par
`SELECT DISTINCT message ...` → une seule ligne). Le statut du setup est
resté `ENTRY_ORDER_PLACED` (ou un statut de position, l'ordre ayant été
réconcilié `FILLED` à 16:18:57 sans qu'un nouveau `setup_status_changed` ne
soit journalisé) pendant toute la fenêtre de 19h — cohérent avec l'analyse
de l'audit 09 : `aggressive_rebound.py:51-56` teste `close < close_below`
et émet `INVALIDATE` **avant** tout test de `current_status`
(`:57` est le premier test de statut, après), donc à chaque tick où le
prix restait sous 8.30 (le seuil buggé, ou 8.18 le seuil déclaré — les deux
étaient probablement franchis simultanément vu l'ampleur du repli sur ce
setup), le moteur retentait la transition, systématiquement rejetée par
`ALLOWED_TRANSITIONS[ENTRY_ORDER_PLACED]` qui ne contient pas `INVALIDATED`
(`state_machine.py:133-138`). `ActionExecutor.transition_setup`
(`action_executor.py:48-59`) avale l'exception et se contente de logger —
aucune corruption d'état, mais 807 cycles de calcul et d'écriture gaspillés
sur ~19h continues, jusqu'à suppression manuelle du setup.

**Vérification que JOBY_20260703_001 n'est structurellement pas exposé à ce
schéma sur sa propre durée de vie** : il n'a jamais atteint
`ENTRY_ORDER_PLACED` (aucun `entry_order_submitted` en base pour ce
setup_id — bloqué en permanence par `TRAILING_STOP_LOSS_NOT_READY` /
`entry_blocked_by_lifecycle_revalidation`, voir §7), donc son invalidation
(§5) s'est produite depuis `WAITING_ENTRY_SIGNAL`, un statut où
`INVALIDATED` **est** une cible autorisée
(`state_machine.py:120-126`) — d'où l'absence de rejet et une transition qui
réussit du premier coup.

**Verdict §6** : le mécanisme (gate absent avant INVALIDATE) est bien celui
identifié par l'audit 09, et son incident réel le plus sévère (807 rejets)
est confirmé chronologie à l'appui — mais sur un setup_id différent de celui
imposé par la consigne. Sur JOBY_20260703_001 lui-même, ce défaut précis ne
s'est jamais manifesté car le setup n'a jamais dépassé `WAITING_ENTRY_SIGNAL`.

---

## 7. CHAMPS CONFIG IGNORÉS (au-delà de `invalidation.close_below`)

Vérification exhaustive par grep de chaque section du JSON
`JOBY_20260703_001.json` contre `app/setups/aggressive_rebound.py` :

| section JSON | lue par `evaluate()` ? | preuve |
|---|---|---|
| `entry.trigger_price` / `entry.entry_price` (8.72) | **Non** | voir §1 — seul `entry.trigger_offset` est lu (`:67`) |
| `entry.order_type`, `limit_offset`, `maximum_limit_price`, `cancel_if_not_filled_after_minutes` | **Non** dans ce fichier (utilisés en aval par `order_manager.py`/`entry_order_executor.py`, hors `evaluate()`) | grep négatif dans `aggressive_rebound.py` |
| `trailing_stop_loss.calculation.*` (méthode `HYBRID_ATR_STRUCTURE`, ATR 1h période 14, buffers structure, `stock_specific_adjustment`...) | **Non** — seul `trailing_stop_loss.initial_stop` est lu, via `BaseSetup.stop_loss` (`base_setup.py:54-58`) | grep `atr\|structure\|calculation` dans `aggressive_rebound.py` → 0 |
| `trailing_stop_loss.ratchet_rules.*` (min_improvement, break_even_policy...) | **Non** | idem |
| **`management.stop_management.steps`** (4 paliers : break-even à 9.10, trail sous higher-low à 9.50, resserrement ATR à 10.25, higher-lows à 11.00) | **Non — mort structurellement, pas seulement non lu ici** | ce bloc n'est consommé que par `position_management.py` et `trailing_runner.py` (`grep -rn stop_management.steps app/` → ces 2 fichiers uniquement, plus `semantic_validation_service.py` qui ne le valide que pour `setup_type in {"runner","trailing_runner","position_management"}`, `:493-506`). `SetupFactory._registry` (audit 09 §0) dispatche `aggressive_rebound` exclusivement vers `AggressiveReboundSetup` — jamais vers ces classes. **Les 4 paliers de gestion de stop détaillés dans le JSON de JOBY ne seront donc jamais exécutés par aucun code, quelle que soit l'évolution du prix.** |
| `volume_confirmation.*` (bloc top-niveau complet, 9 champs) | **Non** | voir §2 |
| `rebound_confirmation.require_bullish_candle`, `require_volume_confirmation`, `confirmation_timeframe` | **Non** | voir §2 |
| `trend_filter` | **Non** (mais `enabled: false` sur JOBY donc sans impact ici) | grep négatif |
| `position_source.*` | **Non** | grep négatif |
| `anti_chase.*` (4 champs) | **Non** | voir §4 |
| `session_policy.*` (10+ champs) | **Non** dans `aggressive_rebound.py` (consommé par `app/engine/session_policy.py`, un module séparé appliqué après `evaluate()` dans `signal_engine.py:82` — donc pas « ignoré » globalement, mais absent de la logique propre au setup) | — |
| `broker_safety.*` | **Non** dans ce fichier (gates génériques appliqués ailleurs dans le pipeline, `entry_order_executor.py`) | — |
| `entry_decision.*` (bloc entier, snapshot statique pré-calculé dans le JSON : `status`, `decision`, `blocking_reasons`, `warnings`...) | **Non** — c'est un artefact figé au moment de la création du fichier, régénéré dynamiquement à chaque tick par `attach_entry_decision` (`signal_engine.py:84-90`), jamais relu comme entrée | confirmé : le JSON déclare `entry_decision.status: "WAITING_ACTIVATION"` alors que le statut réel en base est `INVALIDATED` (§5) — le bloc est une photo obsolète, pas une donnée vivante |

**Constat le plus significatif** : `management.stop_management.steps`
n'est pas simplement « non lu par ce fichier » comme les autres champs — il
appartient à un vocabulaire de configuration (`steps`, `trigger_type:
CANDLE_CLOSE_ABOVE`, etc.) qui **n'est interprété par aucun code pour ce
setup_type**, alors que le JSON de production de JOBY en contient une
définition complète et à première vue opérationnelle (4 paliers avec prix
précis 9.10/9.50/10.25/11.00). Un opérateur lisant ce fichier croirait
disposer d'une gestion de trailing stop échelonnée après l'entrée ; il n'en
existe aucune pour `aggressive_rebound`.

---

## 8. SIMULATION — 2 scénarios réels

### Scénario nominal : 2026-07-03T11:20:57 — entrée en zone de support

```
avant: WAITING_ACTIVATION, support_zone=[8.30,8.50]
tick réel: price touche la zone -> STATUS_CHANGE -> WAITING_ENTRY_SIGNAL
("Price touched support zone", aggressive_rebound.py:57-62)
```
Intention du setup (JSON `entry_decision.display_message`) : *« Attendre
défense de la zone 8.30-8.50 puis reprise 8.72 en 15m avec volume »*. Le
moteur exécute fidèlement la première moitié (détection du toucher de zone)
mais, comme démontré au §1/§2/§4, la seconde moitié (« reprise 8.72 avec
volume ») **n'est jamais vérifiée telle que déclarée** : le niveau 8.72 n'est
lu nulle part, et le volume n'entre dans aucun calcul. Le verdict du moteur
(transition réussie) est correct sur la forme (le prix a bien touché la
zone), mais la porte qu'il ouvre ensuite (`WAITING_ENTRY_SIGNAL`) autorise
un déclenchement sur un critère (`close > previous_high` flottant, sans
volume) qui diverge largement de l'intention texte du setup.

### Scénario limite : 2026-07-07T14:31:29 — invalidation réelle

```
avant: WAITING_ENTRY_SIGNAL (depuis 4 jours, 3h10)
tick réel: close=8.17 (open=8.17, low=8.16, high=8.18, price=8.20)
signal: INVALIDATE, reason="Close below support invalidation"
transition: WAITING_ENTRY_SIGNAL -> INVALIDATED (acceptée, WAITING_ENTRY_SIGNAL
autorise INVALIDATED, state_machine.py:120-126)
```
Intention du setup (`notes`) : *« Invalidation si clôture 15m sous 8.18 avant
activation »*. Le moteur a comparé `close (8.17) < close_below`, avec
`close_below` valant en réalité `support_zone.min` (8.30, le fallback
buggé) et non `support_zone.invalidation_below` (8.18, le seuil que les
`notes` citent mot pour mot). Le verdict final (INVALIDATED) **coïncide**
avec l'intention (8.17 < 8.18 aussi), mais **par coïncidence de la
granularité de barre** (§5) — le mécanisme qui a produit ce verdict n'est
pas celui que le setup déclare, et sur un autre tirage de marché (repli plus
lent, une clôture s'arrêtant par exemple à 8.25) le moteur aurait invalidé
prématurément un setup qui, selon ses propres `notes`, méritait encore
confiance jusqu'à 8.18.

**Bilan des 2 scénarios** : dans les deux cas, le verdict *formel* (statut
atteint) coïncide avec l'intention déclarée du setup — mais dans aucun des
deux cas le *mécanisme* interne ne correspond à ce que le JSON/les `notes`
décrivent. C'est une divergence config/moteur qui n'a pas encore produit de
conséquence financière négative visible sur ce setup précis (aucun ordre
rempli), mais uniquement parce qu'un autre gate indépendant
(`TRAILING_STOP_LOSS_NOT_READY`, voir ci-dessous) a bloqué toute
transmission d'ordre pendant toute la durée de vie du setup.

### Complément notable, hors périmètre `aggressive_rebound.py` mais déterminant sur ce cas réel

```sql
SELECT COUNT(*) FROM events WHERE setup_id='JOBY_20260703_001'
AND event_type='entry_blocked_trailing_stop_not_ready';
-- -> 135
SELECT COUNT(*) FROM events WHERE setup_id='JOBY_20260703_001'
AND event_type='entry_order_submitted';
-- -> 0
```

Malgré au moins 11 émissions `ENTRY_READY` à score 100% entre le
2026-07-06T14:00 et 15:16 (prix passant de 8.83 à 9.25, soit jusqu'à +6.1%
au-dessus du niveau déclaré 8.72), **aucun ordre n'a jamais été transmis** —
bloqué à chaque tentative par `TRAILING_STOP_LOSS_NOT_READY`
(`trailing_stop_loss.broker_order.trailing_stop_order_ready: false` dans le
JSON, jamais passé à `true`). Le setup a donc raté l'intégralité du
mouvement (8.30 → 9.25, +11.4%) puis a été invalidé au retour sur 8.17, sans
qu'aucune position n'ait jamais été ouverte. Ce gate est extérieur à
`aggressive_rebound.py` (logique dans `entry_order_executor.py`) donc hors
périmètre strict de ce lot, mais il explique pourquoi les bugs de calcul de
prix documentés en §1 n'ont eu, sur ce setup précis, aucune conséquence de
capital réel — une simple coïncidence opérationnelle, pas une protection
délibérée contre ces bugs.

---

## PROBLÈMES PROPRES À `aggressive_rebound`

**P1 — Le niveau d'entrée déclaré (`entry.trigger_price`/`entry.entry_price`)
n'est jamais lu ; le trigger réel dérive sans borne avec le marché.**
Preuve : `aggressive_rebound.py:64-68` calcule
`entry = snapshot.previous_high + trigger_offset`, recalculé à neuf à chaque
tick tant que `current_status == WAITING_ENTRY_SIGNAL`, sans jamais
référencer `entry.trigger_price` (8.72 pour JOBY). Sur JOBY_20260703_001, la
valeur observée dérive de **8.51 à 9.25** en 75 minutes le 2026-07-06
(§1). Impact concret : le trigger transmis au broker (via
`entry_order_executor.py:136,196` → `order_manager.py:219-232`) peut être
significativement plus haut (ici jusqu'à +6.1%) que ce que
l'utilisateur a configuré et voit affiché comme intention (`entry_decision.
display_message`), sans aucun garde-fou de config actif pour l'en empêcher
(voir P2).

**P2 — `anti_chase` (le garde-fou de config censé limiter précisément la
dérive de P1) est totalement inerte.**
Preuve : `grep -in anti_chase app/setups/aggressive_rebound.py` → 0
occurrence, alors que `anti_chase.enabled: true`,
`anti_chase.max_price_above_entry_percent: 1.5` sont déclarés sur
JOBY_20260703_001 (et sur tous les autres `aggressive_rebound` échantillonnés
— TROX, RKLB). Impact concret : le signal `ENTRY_READY` à 9.25
(2026-07-06T15:15:18, +8.8% au-dessus de `support_zone.max`) est émis avec
`blocking_checks: []` — aucun frein, alors que le champ de config existe
précisément pour ce cas et porte un seuil (1.5%) six fois plus strict que
l'écart réellement observé.

**P3 — Le stop transmis (`trailing_stop_loss.initial_stop`, fixe) n'est
jamais recalculé en fonction du prix d'entrée réellement dérivant (P1),
faisant varier le risque réel par action d'un facteur >3x sans alerte.**
Preuve : sur les 11 signaux `ENTRY_READY` de JOBY relevés, `stop_loss=8.18`
dans 100% des cas, alors que `entry_price` calculé varie de 8.51 à 9.25 —
risque par action affiché passant de 0.33 à 1.07 (§3). `evaluate()` ne
recalcule jamais le stop pour tenir compte de ce prix d'entrée mouvant
malgré le bloc `trailing_stop_loss.calculation` très détaillé du JSON
(ATR 1h, structure, buffers), qui n'est lu par aucune ligne de ce fichier.

**P4 — `management.stop_management.steps` (4 paliers de gestion de stop
post-entrée déclarés dans le JSON de JOBY) est un vocabulaire de
configuration entièrement mort pour ce setup_type, pas seulement « non lu
par ce fichier ».**
Preuve : `grep -rn "stop_management" app/` ne retourne que
`position_management.py`, `trailing_runner.py` et
`semantic_validation_service.py:493-506` (validé uniquement pour
`setup_type in {"runner","trailing_runner","position_management"}`).
`SetupFactory._registry` dispatche `aggressive_rebound` exclusivement vers
`AggressiveReboundSetup`, dont `evaluate()` ne retourne jamais
`SignalAction.RAISE_STOP` et ne traite aucun statut de position
(`IN_POSITION`/`MANAGING_POSITION` tombent dans le `return
SetupSignal.hold(...)` final, `:76`). Impact concret : si JOBY était
entrée en position, les 4 paliers détaillés (break-even à 9.10, trail sous
higher-low à 9.50, resserrement ATR à 10.25, activation higher-lows à
11.00) ne se seraient **jamais** déclenchés — aucune logique applicative ne
les interprète.

**P5 — Le seuil d'invalidation buggé (`support_zone.min` au lieu de
`support_zone.invalidation_below`, déjà identifié en audit 09) est confirmé
au niveau de l'événement réel sur JOBY_20260703_001, avec le seuil erroné
visible dans le payload stocké en base.**
Preuve nouvelle par rapport à l'audit 09 : l'événement `stock_analysis` de
l'invalidation réelle (2026-07-07T14:31:29) contient littéralement
`{"label": "Invalidation support", "actual": 8.17, "expected": ">= 8.3"}`
dans `opportunity_score.waiting_checks` — le seuil buggé (8.30) est ce que
le système affiche et persiste comme critère officiel, pas seulement un
artefact de calcul interne. Nuance nouvelle également : sur ce cas précis,
la conséquence *observable* est nulle car le marché a traversé toute la
zone tampon `[8.18, 8.30]` en une seule barre 15m (§5) — le bug est réel et
structurel, mais sa manifestation dépend de la vitesse du repli, ce qui
n'était pas mesurable sans dérouler un cas réel complet.

**P6 — Le panneau UI (`setup_conditions.py`) est fidèle au moteur bugué, pas
à la config déclarée — vérifié précisément sur JOBY.**
Preuve : `_rebound_support`/`_rebound_reclaim`
(`setup_conditions.py:289-316`) et `invalidation_map=(("support
invalidation", "price_at_support"),)` (`:468`) ne référencent que
`support_zone.min`/`max` et `previous_high` — jamais
`support_zone.invalidation_below` ni un seuil de volume. Le panneau
« Ce que cherche le setup » d'un utilisateur consultant JOBY_20260703_001
n'aurait donc jamais montré ni le vrai seuil d'invalidation (8.18) ni
l'absence de vérification du volume — cohérent avec le moteur, mais
silencieusement infidèle à ce que le JSON et les `notes` déclarent.

---

## INCERTITUDES

1. **Le lien exact entre `entry_order_submitted` (13:45:41) et le passage à
   `ENTRY_ORDER_PLACED` pour `JOBY_20260628_001`** (§6) n'a pas pu être
   confirmé par un événement `setup_status_changed` dédié — un seul
   `setup_status_changed` existe sur toute la vie de ce setup (le passage
   `WAITING_ACTIVATION -> WAITING_ENTRY_SIGNAL`). Le code exact qui écrit le
   statut `ENTRY_ORDER_PLACED` (probablement `entry_order_executor.py`, hors
   périmètre strict de ce lot) n'a pas été audité ligne à ligne pour
   confirmer qu'il journalise ailleurs ou pas du tout.
2. **La config complète de `JOBY_20260628_001`** (le setup des 807 rejets)
   n'a pas pu être récupérée — le fichier a été supprimé du disque
   (`setup_deleted`, `file_deleted: true`) et l'événement `setup_saved`
   correspondant ne porte pas de `data_json` exploitable. Impossible de
   confirmer si ses valeurs `support_zone.min`/`invalidation_below`
   suivaient le même écart que JOBY_20260703_001 (8.30 vs 8.18) ou un écart
   différent qui aurait pu, cette fois, avoir un impact observable pendant
   les 19h de rejets.
3. **Caractère réellement clos de la barre 15m au moment précis de
   l'invalidation** (close=8.17 à 14:31:29) — même réserve que l'audit 09
   Axe 3 (dépend du comportement serveur IBKR en mode hybrid, non
   vérifiable statiquement).
4. **Le calcul exact de la quantité/du sizing en aval** (risk_engine,
   `order_manager.py`) quand `entry_price` dérive comme documenté en P1/P3
   n'a pas été audité ligne à ligne — seul le fait que `entry_price` et
   `stop_loss` de `signal_engine` alimentent bien la construction d'ordre a
   été vérifié (citations §1/§3), pas le détail du dimensionnement qui en
   résulte.
5. **`TRAILING_STOP_LOSS_NOT_READY` / `trailing_stop_order_ready: false`** —
   le mécanisme exact qui devrait faire passer ce flag à `true` (et
   pourquoi il ne l'a jamais fait sur toute la durée de vie de
   JOBY_20260703_001, empêchant tout ordre réel) n'a pas été investigué :
   c'est un gate situé dans `entry_order_executor.py`/le pipeline broker,
   hors du périmètre `aggressive_rebound.py` demandé par ce lot, mais c'est
   la seule raison pour laquelle P1/P2/P3 n'ont eu aucune conséquence
   financière observable sur ce setup précis.
