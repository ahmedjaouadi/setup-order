# Audit en lecture seule — Lot 18 : pre-implementation du rang 2 (gate INVALIDATE + STATUS_CHANGE regressif)

Mode audit lecture seule. Aucun fichier de code n'a ete modifie, aucune
branche creee. Ce lot verifie les faits exacts avant conception du rang 2,
en s'appuyant sur `audit/09_normes_transverses.md` (deja produit, non
re-diagnostique ici) et sur des requetes SQL fraiches (`mode=ro`) sur
`data/trading_state.sqlite` (155 Go, meme base que l'audit 09).

---

## Q1 — Routage exact des signaux INVALIDATE et STATUS_CHANGE

### Point d'entree : `_handle_signal`

`trading_engine.py:2465-2486` :

```python
2465	    async def _handle_signal(
2466	        self, setup: dict[str, Any], current_status: SetupStatus, signal: Any
2467	    ) -> None:
2468	        if self.action_executor.execute_simple_action(setup, current_status, signal):
2469	            return
2470	        if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
2471	            return
2472	        if signal.action == SignalAction.ENTRY_READY and current_status not in ENTRY_ELIGIBLE_STATUSES:
2473	            ...
2486	        await self.entry_order_executor.execute_entry_ready(setup, signal, current_status)
```

Trois branches en cascade : `execute_simple_action` (HOLD/INVALIDATE/
STATUS_CHANGE), `execute_raise_stop_signal` (RAISE_STOP uniquement),
sinon ENTRY_READY. `SignalAction` a exactement 5 valeurs
(`app/models.py:104-108` : `HOLD`, `STATUS_CHANGE`, `ENTRY_READY`,
`INVALIDATE`, `RAISE_STOP`) — INVALIDATE et STATUS_CHANGE ne peuvent donc
matcher **que** la premiere branche.

### Methode qui traite INVALIDATE et STATUS_CHANGE : `execute_simple_action`

`app/engine/action_executor.py:25-39` :

```python
25	    def execute_simple_action(
26	        self,
27	        setup: dict[str, Any],
28	        current_status: SetupStatus,
29	        signal: Any,
30	    ) -> bool:
31	        if signal.action == SignalAction.HOLD:
32	            return True
33	        if signal.action == SignalAction.INVALIDATE and signal.target_status:
34	            self.transition_setup(setup, current_status, signal.target_status, signal.reason)
35	            return True
36	        if signal.action == SignalAction.STATUS_CHANGE and signal.target_status:
37	            self.transition_setup(setup, current_status, signal.target_status, signal.reason)
38	            return True
39	        return False
```

Confirmation que `position_action_executor.py` ne traite jamais ces deux
actions : `execute_raise_stop_signal` (`position_action_executor.py:28-40`)
retourne `False` immediatement si `signal.action != SignalAction.RAISE_STOP`
(`:34`) — INVALIDATE et STATUS_CHANGE sont donc deja consommes par
`execute_simple_action` (qui retourne `True`) avant meme d'atteindre cette
deuxieme branche dans `_handle_signal`.

Note : `execute_simple_action` exige `signal.target_status` (verite non
nulle) pour router vers `transition_setup` (`:33` et `:36`) — un signal
INVALIDATE ou STATUS_CHANGE sans `target_status` renverrait `False` dans son
ensemble (aucune des 4 conditions ne matche) et retomberait dans les
branches suivantes de `_handle_signal`. En pratique, les 5 emissions
STATUS_CHANGE et les 4 emissions INVALIDATE trouvees dans `app/setups/`
(voir Q2) fixent toutes `target_status` explicitement — ce cas n'a pas ete
observe.

### Determination de `target_status`

`target_status` n'est pas calcule dans `action_executor.py` : c'est un champ
du `SetupSignal` construit directement par chaque `evaluate()` de
`app/setups/*.py` (ex. `momentum_breakout.py:173`,
`target_status=SetupStatus.MISSED_BREAKOUT`). `action_executor.py` le
transmet tel quel a `state_machine.transition(current_status, target_status)`
(`action_executor.py:49`, voir bloc complet ci-dessous). Aucune logique de
derivation ou de validation intermediaire n'existe entre l'emission du
signal et l'appel a `transition()`.

### Le bloc `try/except` de `transition_setup`

`app/engine/action_executor.py:41-68` :

```python
41	    def transition_setup(
42	        self,
43	        setup: dict[str, Any],
44	        current_status: SetupStatus,
45	        target_status: SetupStatus,
46	        reason: str,
47	    ) -> None:
48	        try:
49	            new_status = self.state_machine.transition(current_status, target_status)
50	        except Exception as exc:
51	            logger.warning("Rejected transition for %s: %s", setup["setup_id"], exc)
52	            self.event_store.record(
53	                EventLevel.ERROR,
54	                "setup_transition_rejected",
55	                str(exc),
56	                setup_id=setup["setup_id"],
57	                symbol=setup["symbol"],
58	            )
59	            return
60	        self.repository.update_setup_status(setup["setup_id"], new_status.value, reason)
61	        self.event_store.record(
62	            EventLevel.INFO,
63	            "setup_status_changed",
64	            reason,
65	            setup_id=setup["setup_id"],
66	            symbol=setup["symbol"],
67	            data={"from": current_status.value, "to": new_status.value},
68	        )
```

Comportement exact en cas de transition invalide (`state_machine.transition`
leve `InvalidTransitionError`, sous-classe de `ValueError`, elle-meme une
`Exception` — capturee par le `except Exception` generique ligne 50) :
1. **Avale l'exception** — `except Exception as exc` ligne 50, aucun
   `raise` ni propagation.
2. **Journalise un warning applicatif** (`logger.warning`, ligne 51) —
   niveau logging Python, pas la table `events`.
3. **Ecrit un evenement `setup_transition_rejected`** dans la table
   `events` (niveau `ERROR`, ligne 52-58) — c'est cet evenement qui a permis
   de compter les 10 425 rejets (voir Q3).
4. **`return` immediat ligne 59** — la fonction s'arrete la. La ligne 60
   (`self.repository.update_setup_status(...)`) n'est **jamais atteinte**
   dans le cas rejete. Aucun statut n'est ecrit en base.

Le meme motif exact (try/except identique, meme sequence
warning-log/event/`return`) existe dans
`app/engine/position_action_executor.py:50-68` pour `RAISE_STOP`, mais ce
n'est pas le chemin emprunte par INVALIDATE/STATUS_CHANGE (voir ci-dessus).

---

## Q2 — La forme exacte d'un STATUS_CHANGE regressif

### Emission du STATUS_CHANGE vers MISSED_BREAKOUT (`momentum_breakout`)

`app/setups/momentum_breakout.py:155-175` :

```python
155	        stale = self._stale_state(market, maximum_limit_price)
156	        metadata["analysis"]["stale"] = stale
157	        if market["ask"] > maximum_limit_price + stale["buffer"]:
158	            metadata["analysis"].update(
159	                {
160	                    "decision_status": "MISSED_BREAKOUT",
161	                    "decision": "NO_ENTRY",
162	                    "next_action": "WAITING_RETEST",
163	                    "blocking_conditions": [
164	                        "PRICE_TOO_FAR_ABOVE_ENTRY",
165	                        "ASK_ABOVE_MAXIMUM_LIMIT_PLUS_STALE_BUFFER",
166	                        "ask above maximum_limit_price + stale_buffer",
167	                    ],
168	                }
169	            )
170	            return SetupSignal(
171	                action=SignalAction.STATUS_CHANGE,
172	                reason="MISSED_BREAKOUT: ask above maximum limit plus stale buffer",
173	                target_status=SetupStatus.MISSED_BREAKOUT,
174	                metadata=metadata,
175	                )
```

`SetupSignal` exact produit : `action=SignalAction.STATUS_CHANGE`,
`reason="MISSED_BREAKOUT: ask above maximum limit plus stale buffer"`,
`target_status=SetupStatus.MISSED_BREAKOUT`, `metadata=` (dict d'analyse,
sans effet sur le routage). Pas de `entry_price`/`stop_loss`/`new_stop`
(champs par defaut de `SetupSignal`, non consommes par `execute_simple_
action`).

### Depuis quels `current_status` ce signal est-il emis en pratique ?

Le test ligne 157 (`market["ask"] > maximum_limit_price + stale["buffer"]`)
ne lit **jamais** `current_status` — c'est le seul parametre d'entree de
`_analyze_long` (signature `momentum_breakout.py:38-42`) qui conditionne
cette branche, et `current_status` n'y apparait pas avant la ligne 179
(branche suivante). `current_status` est fige par
`signal_engine.evaluate_snapshot` avant l'appel (`signal_engine.py:74-81`,
voir dispatch complet en Q3) et n'est filtre que par
`TERMINAL_SIGNAL_STATUSES` (`signal_engine.py:24-37`) — qui **exclut**
`WAITING_RETEST` (absent de cette liste). Donc `evaluate()` est bien invoque
avec `current_status=WAITING_RETEST`, et la branche ligne 157-175 s'execute
identiquement quel que soit le statut de depart parmi tous les statuts non
terminaux. Preuve empirique directe : `events.message =
'Invalid setup transition: WAITING_RETEST -> MISSED_BREAKOUT'`, 9 469
occurrences (requete reproduite Q3) — confirmee a l'identique par
`audit/09_normes_transverses.md:110-137` (memes chiffres).

### Liste complete des STATUS_CHANGE emis dans `app/setups/`

Verification exhaustive (`grep -rn "SignalAction.STATUS_CHANGE" app/setups/`
— 5 occurrences, aucune autre que celles ci-dessous) :

| # | Fichier:ligne | `current_status` de depart attendu (condition explicite) | `target_status` | Nature |
|---|---|---|---|---|
| 1 | `aggressive_rebound.py:57-62` | `current_status == SetupStatus.WAITING_ACTIVATION` (`:57`, condition explicite de la branche) | `WAITING_ENTRY_SIGNAL` | Progressive, gatee |
| 2 | `breakout_retest.py:71-77` | `current_status == SetupStatus.WAITING_ACTIVATION` (`:71`, condition explicite de la branche `if`) | `WAITING_ENTRY_SIGNAL` | Progressive, gatee |
| 3 | `momentum_breakout.py:170-175` | **Aucune** — s'execute pour tout `current_status` non terminal (voir ci-dessus) | `MISSED_BREAKOUT` | Regressive/laterale, **non gatee** — c'est le defaut cible du rang 2 |
| 4 | `momentum_breakout.py:187-192` | `current_status == SetupStatus.MISSED_BREAKOUT` (`:179`, condition explicite de la branche) | `WAITING_RETEST` | Progressive, gatee |
| 5 | `pullback_continuation.py:30-35` | `current_status == SetupStatus.WAITING_ACTIVATION` (`:30`, condition explicite de la branche) | `WAITING_ENTRY_SIGNAL` | Progressive, gatee |

`range_breakout.py` n'emet **aucun** `STATUS_CHANGE` (grep exhaustif sur ce
fichier : 0 occurrence ; ses seuls signaux sont `INVALIDATE` (`:29-33`) et
`ENTRY_READY` (`:36-42`), confirme par lecture complete du fichier,
94 lignes).

**Conclusion factuelle Q2** : sur les 5 STATUS_CHANGE emis dans
`app/setups/`, **4 sont deja gates par une condition explicite sur
`current_status` dans le corps meme de la branche** (doivent continuer de
passer sans modification) ; **1 seul (`momentum_breakout.py:170-175`, vers
`MISSED_BREAKOUT`) ne teste jamais `current_status`** — c'est exactement le
signal qui produit les 9 469 rejets. Ce constat est identique a celui deja
etabli par `audit/09_normes_transverses.md` Axe 1 (lignes 106, 122-137) ;
aucun ecart trouve entre les deux lots.

---

## Q3 — Le filet state machine est-il deja en place ?

### Confirmation sur `ALLOWED_TRANSITIONS`

`app/engine/state_machine.py:89-97` (bloc complet pour `WAITING_RETEST`) :

```python
89	    SetupStatus.WAITING_RETEST: {
90	        SetupStatus.WAITING_CONFIRMATION,
91	        SetupStatus.REARMED_ON_NEW_BASE,
92	        SetupStatus.WAITING_ENTRY_SIGNAL,
93	        SetupStatus.EXPIRED,
94	        SetupStatus.INVALIDATED,
95	        SetupStatus.CANCELLED,
96	        SetupStatus.ERROR,
97	    },
```

`SetupStatus.MISSED_BREAKOUT` **absent** de cet ensemble — confirme.

`app/engine/state_machine.py:133-138` (bloc complet pour
`ENTRY_ORDER_PLACED`) :

```python
133	    SetupStatus.ENTRY_ORDER_PLACED: {
134	        SetupStatus.ENTRY_PARTIALLY_FILLED,
135	        SetupStatus.ENTRY_FILLED,
136	        SetupStatus.CANCELLED,
137	        SetupStatus.ERROR,
138	    },
```

`SetupStatus.INVALIDATED` **absent** de cet ensemble — confirme.

### Consequence : rejet sans corruption, ou ecriture reelle ?

`StateMachine.transition` (`state_machine.py:304-313`) :

```python
304	    def transition(
305	        self,
306	        current: SetupStatus,
307	        target: SetupStatus,
308	        setup_role: SetupRole | str | Any | None = None,
309	    ) -> SetupStatus:
310	        decision = self.explain_transition(current, target, setup_role)
311	        if not decision.allowed:
312	            raise InvalidTransitionError(decision.reason)
313	        return target
```

Si `target not in ALLOWED_TRANSITIONS[current]`, `explain_transition`
(`:269-302`) retourne `allowed=False` (branche finale `:297-302`), et
`transition()` **leve** `InvalidTransitionError` avant tout `return`
(ligne 312) — aucune valeur n'est jamais retournee dans ce cas. Cette
exception est capturee par `action_executor.py:48-59` (bloc cite en Q1) :
le `except` intercepte avant la ligne `self.repository.update_setup_status(
...)` (`:60`), qui n'est **jamais executee**. Aucune ecriture SQL n'a lieu
pour une transition rejetee — verifie par lecture de code (pas de chemin
alternatif d'ecriture) et confirme empiriquement ci-dessous.

### Preuve empirique — requete executee sur `data/trading_state.sqlite` (mode=ro)

```sql
SELECT message, COUNT(*) FROM events
WHERE event_type='setup_transition_rejected'
GROUP BY message ORDER BY 2 DESC;
```

Resultat brut (execution du 2026-07-19, via `sqlite3.connect(
"file:...trading_state.sqlite?mode=ro", uri=True)`) :

```
('Invalid setup transition: WAITING_RETEST -> MISSED_BREAKOUT', 9469)
('Invalid setup transition: ENTRY_ORDER_PLACED -> INVALIDATED', 956)
```

```sql
SELECT COUNT(*) FROM events WHERE event_type='setup_transition_rejected';
-- -> 10425
SELECT COUNT(*) FROM events WHERE event_type='setup_status_changed';
-- -> 7296
```

Ces deux seuls messages epuisent 100% des 10 425 lignes
`setup_transition_rejected` de toute la base (identique a
`audit/09_normes_transverses.md:117-121,144-146`, reconfirme sur la base
actuelle a l'identique). Croise avec la table `setups` (snapshot courant,
23 lignes reparties par `setup_type`/`status`, requete
`SELECT setup_type, status, COUNT(*) FROM setups GROUP BY 1,2`) : **aucune
ligne `status='MISSED_BREAKOUT'` ni `status='INVALIDATED'` issue d'une
transition depuis `WAITING_RETEST`/`ENTRY_ORDER_PLACED` non autorisee
n'existe** — les seules lignes `INVALIDATED` presentes (12 `aggressive_
rebound`, 2 `breakout_retest`, 13 `momentum_breakout`, 10 `pullback_
continuation`, 7 `range_breakout`) proviennent necessairement de
transitions **autorisees** (le filet empechant structurellement toute autre
voie d'ecriture).

### Verdict — securite ou hygiene ?

Tranche par le code + la base : pour les deux cas mesures
(`WAITING_RETEST -> MISSED_BREAKOUT`, `ENTRY_ORDER_PLACED -> INVALIDATED`),
**le filet `ALLOWED_TRANSITIONS` fonctionne a 100%** — 10 425 tentatives,
10 425 rejets, 0 ecriture corrompue prouvee. Le rang 2 n'est donc **pas un
correctif de securite** pour ces deux transitions precises : c'est un
correctif d'**hygiene** (calcul + `event_store.record` + `logger.warning`
gaspilles a chaque tick pour tout setup dans cet etat, cf. le cas
`JOBY_20260628_001` deja documente : 807 tentatives consecutives sur ~19h,
`audit/09_normes_transverses.md:235,240-244`). La question de savoir si un
**autre** couple (statut, type) que ces deux-la pourrait, lui, produire une
ecriture reelle est traitee separement en Q4 — c'est le seul angle ou le
verdict "hygiene" pourrait basculer vers "securite".

---

## Q4 — Le seul trou reel potentiel : `RECONCILING_EXISTING_POSITION`

### Confirmation sur la table

`app/engine/state_machine.py:161-168` :

```python
161	    SetupStatus.RECONCILING_EXISTING_POSITION: {
162	        SetupStatus.IN_POSITION,
163	        SetupStatus.BLOCKED,
164	        SetupStatus.INVALIDATED,
165	        SetupStatus.MANUAL_REVIEW_REQUIRED,
166	        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
167	        SetupStatus.CANCELLED,
168	    },
```

`INVALIDATED` **present** (ligne 164) — confirme, seul statut de position
(`RECONCILING_EXISTING_POSITION`, `IN_POSITION`, `MANAGING_POSITION`,
`PARTIAL_EXIT`, cf. `POSITION_STATUSES` `state_machine.py:237-245`) ou
cette cible est autorisee.

### Un setup peut-il atteindre cette combinaison (statut + type INVALIDATE-capable) ?

**1. Comment `RECONCILING_EXISTING_POSITION` est assigne** —
`app/setups/base_setup.py:60-63` :

```python
60	    def initial_status(self) -> SetupStatus:
61	        if setup_is_management_only(self.setup_role):
62	            return SetupStatus.RECONCILING_EXISTING_POSITION
63	        return SetupStatus.WAITING_ACTIVATION
64	    (suite: def validate...)
```

`self.setup_role` (`base_setup.py:46-48`) delegue a
`setup_role_from_config(self.config)` (`app/setups/setup_roles.py:17-30`) —
lit `config.get("setup_role")`, **independant de `setup_type`**. Rien dans
`initial_status()` ne restreint cette branche a `setup_type ==
"position_management"`.

**2. Rien dans `validate()` des 4 types INVALIDATE-capables n'exclut
`setup_role=MANAGEMENT_ONLY`** :
- `aggressive_rebound.py:16-22` (`validate()`) : ajoute uniquement une
  verification `support_zone.min/max`, aucune contrainte de role.
- `breakout_retest.py:16-25` (`validate()`) : ajoute uniquement
  `breakout.daily_close_above`/`retest.zone_min/max`, aucune contrainte de
  role.
- `pullback_continuation.py` : **aucune surcharge de `validate()`** (grep
  confirme — seule `base_setup.validate()` s'applique).
- `range_breakout.py` : **aucune surcharge de `validate()`**.

La seule contrainte de role vient de `base_setup.validate()` ->
`entry_policy_errors` (`app/setups/setup_roles.py:56-63`) : si
`setup_role=MANAGEMENT_ONLY`, exige seulement `entry.enabled=false`
(sinon erreur `"MANAGEMENT_ONLY setup cannot enable entry orders"`,
ligne 60) — satisfaisable trivialement par n'importe lequel des 4 types.

**3. Confirmation a la validation d'armement (generique, independante du
type)** — `app/engine/setup_engine.py:94-103` :

```python
 94	        if setup_is_management_only(role):
 95	            position_source = config.get("position_source", {})
 96	            if not isinstance(position_source, dict):
 97	                position_source = {}
 98	            if position_source.get("mode") != "adopt_existing_ibkr_position":
 99	                errors.append(
100	                    "MANAGEMENT_ONLY setup must use position_source.mode=adopt_existing_ibkr_position"
101	                )
102	            if entry_enabled:
103	                errors.append("MANAGEMENT_ONLY setup cannot arm an entry order")
```

`role = setup_role_from_config(config, infer_position_management=True)`
(`setup_engine.py:75`) est calcule **a partir de la config**, pas du
`setup_type` — cette verification passerait pour un `range_breakout` (ou
les 3 autres) configure avec `setup_role=MANAGEMENT_ONLY`,
`position_source.mode=adopt_existing_ibkr_position`,
`entry.enabled=false`.

**Nuance** : il existe un **autre** ensemble, distinct et non synchronise,
`app/setups/setup_conditions.py:472` :
`MANAGEMENT_ONLY_SETUP_TYPES = {"runner", "trailing_runner",
"position_management"}` — une liste figee par `setup_type`, utilisee
ailleurs (ex. `setup_condition_tracker.py:348`) pour des besoins d'affichage
UI. Cette liste **ne bloque rien structurellement** : elle ne participe ni a
`initial_status()`, ni a `validate()`, ni a `state_machine.transition()`.
Elle est un signal que le produit "pense" MANAGEMENT_ONLY = ces 3 types
seulement, mais rien dans le code d'execution ne fait respecter cette
hypothese pour les 4 types INVALIDATE-capables.

**Conclusion structurelle** : **rien n'empeche** un setup `aggressive_
rebound`, `pullback_continuation`, `range_breakout` ou `breakout_retest`
configure avec `setup_role=MANAGEMENT_ONLY` d'atteindre
`RECONCILING_EXISTING_POSITION`, tout en conservant sa logique `evaluate()`
d'origine (qui emet `INVALIDATE` **sans jamais tester `current_status`**
pour 3 des 4 types, cf. Q2/audit 09 Axe 2).

### Verification empirique en base (mode=ro)

**Table `setups` (snapshot courant)** :

```sql
SELECT setup_id, setup_type, status, config_json FROM setups
WHERE status='RECONCILING_EXISTING_POSITION';
-- -> 0 ligne
SELECT setup_type, status, COUNT(*) FROM setups GROUP BY setup_type, status ORDER BY 1,2;
```

Resultat brut (23 lignes, aucune `RECONCILING_EXISTING_POSITION`) :

```
('aggressive_rebound', 'INVALIDATED', 12)
('aggressive_rebound', 'STALE_SETUP', 1)
('aggressive_rebound', 'WAITING_ENTRY_SIGNAL', 1)
('breakout_retest', 'ENTRY_ORDER_PLACED', 1)
('breakout_retest', 'INVALIDATED', 2)
('breakout_retest', 'WAITING_ENTRY_SIGNAL', 2)
('momentum_breakout', 'INVALIDATED', 13)
('momentum_breakout', 'MISSED_BREAKOUT_WAIT_RETEST', 2)
('momentum_breakout', 'STALE_SETUP', 1)
('momentum_breakout', 'WAITING_ACTIVATION', 4)
('momentum_breakout', 'WAITING_RETEST', 9)
('pullback_continuation', 'DISABLED', 1)
('pullback_continuation', 'INVALIDATED', 10)
('pullback_continuation', 'STALE_SETUP', 1)
('pullback_continuation', 'WAITING_ACTIVATION', 1)
('range_breakout', 'INVALIDATED', 7)
('range_breakout', 'MISSED_BREAKOUT_WAIT_RETEST', 1)
('range_breakout', 'WAITING_ACTIVATION', 3)
```

**Table `events`, historique complet (2 418 019 lignes)** :

```sql
SELECT COUNT(*) FROM events
WHERE event_type='setup_status_changed' AND data_json LIKE '%RECONCILING_EXISTING_POSITION%';
-- -> 0
SELECT COUNT(*) FROM events
WHERE event_type='setup_transition_rejected' AND message LIKE '%RECONCILING_EXISTING_POSITION%';
-- -> 0
SELECT message, COUNT(*) FROM events WHERE event_type='setup_transition_rejected' GROUP BY message;
-- -> seulement les 2 messages deja cites en Q3 (9469 + 956), aucune mention de RECONCILING_EXISTING_POSITION
```

**Table `events`, sous-ensemble `event_type='stock_analysis'` (96 344
lignes, fenetre 2026-05-31 -> 2026-07-19, meme source que la reconstruction
`processed[i]` d'`audit/09_normes_transverses.md:62-80`)** :

```sql
SELECT COUNT(*) FROM events
WHERE event_type='stock_analysis' AND data_json LIKE '%RECONCILING_EXISTING_POSITION%';
-- -> 0 (execution effective, ~41s, scan complet des 96 344 lignes)
```

**Resultat** : sur l'integralite de la fenetre de donnees disponible (base
de 155 Go), la chaine `RECONCILING_EXISTING_POSITION` n'apparait **dans
aucun** evenement `stock_analysis`, `setup_status_changed` ni
`setup_transition_rejected`, et aucune ligne `setups` actuelle n'a ce
statut. Ceci va au-dela de la verification d'`audit/09_normes_transverses.md`
(INCERTITUDE 1, qui se limitait au snapshot `setups` courant et a un
sous-ensemble des evenements) : ce lot confirme l'absence sur la table
`stock_analysis` complete egalement.

### Reponse a la question posee

Si cette combinaison etait atteinte, un `INVALIDATE` emis dans cet etat
**ecrirait reellement `INVALIDATED`** — la transition est autorisee
(`state_machine.py:164`), donc `action_executor.py:60`
(`self.repository.update_setup_status(...)`) **serait executee**, contrairement
aux deux cas de Q3 ou l'ecriture est bloquee. **Le risque est reel au sens
structurel** (rien dans le code ne l'empeche, confirme ci-dessus par lecture
exhaustive de `validate()`/`initial_status()`/`setup_engine.py`), mais
**purement theorique au sens empirique** : 0 occurrence sur l'integralite
des donnees de production disponibles (`setups` courant, `stock_analysis`
complet, `setup_status_changed`, `setup_transition_rejected`). C'est le seul
cas identifie ou l'absence de gate en amont de `evaluate()` pourrait, en
theorie, produire une ecriture au lieu d'un simple gaspillage — mais aucune
preuve qu'il se soit jamais materialise.

---

## Q5 — Ce que le gate ne doit surtout pas casser

Question factuelle : quels couples `(action, current_status, target_status)`
sont legitimes aujourd'hui (doivent continuer a passer), lesquels sont
illegitimes (rejetes par la state machine, candidats a un court-circuit
amont), et cette distinction coincide-t-elle avec `ALLOWED_TRANSITIONS` ?

### Couples LEGITIMES (passent aujourd'hui, a preserver)

**STATUS_CHANGE** — les 4 emissions gatees identifiees en Q2, verifiees
contre `ALLOWED_TRANSITIONS` :

| Fichier:ligne | `(current_status -> target_status)` | Dans `ALLOWED_TRANSITIONS` ? | Citation table |
|---|---|---|---|
| `aggressive_rebound.py:57-62` | `WAITING_ACTIVATION -> WAITING_ENTRY_SIGNAL` | Oui | `state_machine.py:24-38` (cible ligne 33) |
| `breakout_retest.py:71-77` | `WAITING_ACTIVATION -> WAITING_ENTRY_SIGNAL` | Oui | idem |
| `momentum_breakout.py:187-192` | `MISSED_BREAKOUT -> WAITING_RETEST` | Oui | `state_machine.py:81-88` (cible ligne 82) |
| `pullback_continuation.py:30-35` | `WAITING_ACTIVATION -> WAITING_ENTRY_SIGNAL` | Oui | `state_machine.py:24-38` (cible ligne 33) |

**INVALIDATE** — cible toujours `INVALIDATED` ; statuts de depart pour
lesquels `INVALIDATED in ALLOWED_TRANSITIONS[statut]` (extraction complete
de la table, `state_machine.py:8-201`) :

| Statut de depart | `INVALIDATED` autorise ? | Ligne table |
|---|---|---|
| `WAITING_ACTIVATION` | Oui | `:35` |
| `BLOCKED` | Oui | `:46` |
| `STALE_SETUP` | Oui | `:56` |
| `MISSED_BREAKOUT_WAIT_RETEST` | Oui | `:68` |
| `WAITING_BREAKOUT` | Oui | `:77` |
| `MISSED_BREAKOUT` | Oui | `:85` |
| `WAITING_RETEST` | Oui | `:94` |
| `REARMED_ON_NEW_BASE` | Oui | `:102` |
| `WAITING_REBOUND` | Oui | `:109` |
| `WAITING_CONFIRMATION` | Oui | `:116` |
| `WAITING_ENTRY_SIGNAL` | Oui | `:123` |
| `ENTRY_READY` | Oui | `:129` |
| `RECONCILING_EXISTING_POSITION` | Oui (voir Q4) | `:164` |

Parmi ceux-ci, seuls les statuts reellement atteignables par les 4 types
INVALIDATE-capables **avant filtrage `TERMINAL_SIGNAL_STATUSES`**
(`signal_engine.py:24-37`, qui exclut `BLOCKED`, `STALE_SETUP`,
`MISSED_BREAKOUT_WAIT_RETEST` de l'evaluation — `evaluate()` n'y est jamais
appele, donc ces 3 lignes ne se materialisent jamais en pratique pour ces
4 types) sont pertinents empiriquement : `WAITING_ACTIVATION`,
`WAITING_ENTRY_SIGNAL` (flux normal de ces 4 types), plus
`RECONCILING_EXISTING_POSITION` (Q4, theorique).

### Couples ILLEGITIMES (rejetes aujourd'hui par la state machine)

| Fichier:ligne | `(current_status -> target_status)` | Preuve empirique |
|---|---|---|
| `momentum_breakout.py:170-175` | `WAITING_RETEST -> MISSED_BREAKOUT` (et tout autre statut non terminal `-> MISSED_BREAKOUT` hors ceux ou c'est le statut initial) | 9 469 rejets (`events`, Q3) |
| `aggressive_rebound.py:51-56`, `pullback_continuation.py:24-29`, `range_breakout.py:28-33` (INVALIDATE non gate) | `ENTRY_ORDER_PLACED -> INVALIDATED`, `ENTRY_PARTIALLY_FILLED -> INVALIDATED`, `ENTRY_FILLED -> INVALIDATED`, `STOP_ORDER_PLACED/STOP_PLACED -> INVALIDATED`, `IN_POSITION/MANAGING_POSITION/PARTIAL_EXIT -> INVALIDATED` (tous absents de `ALLOWED_TRANSITIONS`, cf. lignes `:133-190` de la table) | 956 rejets confirmes pour `ENTRY_ORDER_PLACED -> INVALIDATED` (`events`, Q3), dont 807 sur le seul `JOBY_20260628_001` (`aggressive_rebound`, `audit/09_normes_transverses.md:235,240-244`) ; les autres statuts post-entree n'ont produit aucun rejet observe (silence de donnees, pas preuve d'absence — meme reserve qu'audit 09 INCERTITUDE 4) |

### La distinction legitime/illegitime coincide-t-elle exactement avec `ALLOWED_TRANSITIONS` ?

**Oui, par construction** : aujourd'hui, "legitime" et "illegitime" sont
definis **exclusivement** par `ALLOWED_TRANSITIONS`
(`state_machine.py:8-201`) via `explain_transition`/`transition()`
(`:269-313`) — c'est le seul arbitre existant, il n'y a pas de deuxieme
notion de legitimite ailleurs dans le code (aucun autre garde-fou
n'intercepte INVALIDATE/STATUS_CHANGE avant `action_executor.py:49`, cf.
Q1). Un futur gate qui consulterait la meme table produirait donc
exactement les memes decisions accept/reject que le comportement actuel,
sans changement de perimetre.

**Nuance factuelle unique** (deja signalee Q4) : la table `ALLOWED_
TRANSITIONS` autorise `RECONCILING_EXISTING_POSITION -> INVALIDATED`
(`:164`) sans distinguer *quel type* de setup emet le signal — cette entree
a ete ajoutee (lecture de code, pas d'historique git consultee ici) en
pensant vraisemblablement aux seuls types `MANAGEMENT_ONLY` structurels
(`position_management`/`runner`/`trailing_runner`, aucun desquels n'emet
INVALIDATE, cf. audit 09 Axe 2 lignes 165-174), mais elle reste
techniquement ouverte a n'importe quel `setup_type` configure en
`MANAGEMENT_ONLY` (Q4). Ce n'est pas un ecart entre "legitime" et "ce que
dit la table" — la table dit bien `autorise` — c'est un ecart potentiel
entre "ce que la table autorise" et "ce que les auteurs du systeme
voulaient probablement autoriser", visible uniquement en croisant la table
avec `setup_role`/`setup_type`, une dimension que `ALLOWED_TRANSITIONS`
n'encode pas.

---

## INCERTITUDES RESIDUELLES

1. **Absence totale de trace historique pour `RECONCILING_EXISTING_
   POSITION` combine a un type INVALIDATE-capable** (Q4) : confirmee sur
   3 sources independantes (`setups` snapshot, `stock_analysis` complet
   96 344 lignes, `setup_transition_rejected`/`setup_status_changed`
   integraux 2 418 019 lignes) — mais une absence de preuve n'est pas une
   preuve d'impossibilite future ; rien dans le code n'empeche la creation
   d'un tel setup a l'avenir (demontre structurellement Q4).
2. **Provenance de la ligne `RECONCILING_EXISTING_POSITION -> INVALIDATED`
   dans `ALLOWED_TRANSITIONS`** (Q5) : aucune recherche d'historique git
   n'a ete effectuee dans ce lot (hors perimetre "lecture seule du code
   actuel") — l'intention originale (limiter aux 3 types structurellement
   MANAGEMENT_ONLY, ou autoriser deliberement tout type) n'est pas
   verifiable par la seule lecture de `state_machine.py`.
3. **`position_management`/`runner`/`trailing_runner`** : comme deja note
   par `audit/09_normes_transverses.md` INCERTITUDES 3, ces 3 types ont
   0 occurrence sur toute la fenetre de donnees disponible — leurs
   verdicts ("conformes" pour Axe 1/2, "hors de portee du trou Q4" car ils
   n'emettent jamais INVALIDATE) reposent entierement sur la lecture de
   code, jamais eprouves en production.
4. **`pullback_continuation` et INVALIDATE non gate** : structurellement
   expose au meme defaut qu'`aggressive_rebound`/`range_breakout` (Q2, Q5),
   mais n'apparait dans aucun `setup_id` du jeu de 956 rejets empiriques
   (`audit/09_normes_transverses.md:245-249`) — silence de donnees, pas
   preuve d'absence de risque, reporte a l'identique de l'audit 09.
5. **Caractere exhaustif de la recherche `LIKE '%RECONCILING_EXISTING_
   POSITION%'`** (Q4) : cette methode textuelle suppose que la chaine
   litterale apparait dans `data_json`/`message` chaque fois que ce statut
   est implique ; elle ne couvrirait pas un encodage different (ex. valeur
   numerique d'enum) — non identifie dans le schema actuel
   (`SetupStatus` serialise en toutes lettres partout observe), mais non
   verifie formellement au-dela des colonnes `events` interrogees.
