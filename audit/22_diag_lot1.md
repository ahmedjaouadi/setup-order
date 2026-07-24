# Diagnostic lot 1 — pourquoi `tests/test_entry_gate_current_status.py` échoue

Investigation en lecture seule. Aucune modification de code, aucune correction,
aucun pop de stash. Réalisée via un worktree Git détaché séparé
(`../setup-order-diag-worktree`, HEAD détaché sur `d18d7ec`) pour ne toucher ni
`fix/01-gate-current-status` ni le stash en cours.

État Git au moment de l'investigation :

```
$ git status --short | head -5
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/
?? data/setups/ALAB_20260713_001.json

$ git stash list
stash@{0}: On fix/01-gate-current-status: lot2-lot3a-setaside-for-isolation-test
stash@{1}: On feat/setup-conditions-ui: residuel feat setup-conditions (deja commite sur feat/setup-conditions) + donnees runtime

$ git branch --show-current
fix/01-gate-current-status

$ git log --oneline -3
0aaf12a fix(safety): gate ENTRY_READY on current_status before broker submission
9544cab fix(setups): traduit les raisons d'invalidation en messages lisibles
c485f5b docs: catalogue setup conditions (doc 21) + mises a jour associees
```

---

## Q1 — Ces tests ont-ils déjà passé ?

**Réponse : NON.** Le fichier échoue à l'identique sur le commit de sauvegarde
`d18d7ec` (état exact d'avant toute manipulation Git, testé dans un worktree
détaché isolé) :

```
$ git worktree add --detach ../setup-order-diag-worktree d18d7ec9b10d364ac5527de076f393edbe94376b
Preparing worktree (detached HEAD d18d7ec)
HEAD is now at d18d7ec WIP backup: état complet avant séparation des lots

$ cd ../setup-order-diag-worktree
$ python -m pytest tests/test_entry_gate_current_status.py -q
...
SUBFAILED(setup_type='aggressive_rebound') tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_nominal_entry_transmitted_for_each_setup_type
SUBFAILED(setup_type='breakout_retest') tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_nominal_entry_transmitted_for_each_setup_type
SUBFAILED(setup_type='pullback_continuation') tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_nominal_entry_transmitted_for_each_setup_type
SUBFAILED(setup_type='momentum_breakout') tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_nominal_entry_transmitted_for_each_setup_type
SUBFAILED(setup_type='range_breakout') tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_nominal_entry_transmitted_for_each_setup_type
FAILED tests/test_entry_gate_current_status.py::EntryGateTradingEngineTests::test_replay_2026_06_29_incident_no_second_order
6 failed, 4 passed, 9 subtests passed in 6.82s
```

Mêmes 6 échecs, mêmes assertions (`WAITING_ENTRY_SIGNAL != ENTRY_ORDER_PLACED`,
`0 != 2`), qu'après le commit `0aaf12a`. La branche puisque `d18d7ec` est
antérieur à tout commit de lot, cela signifie que **ces tests n'ont jamais
passé depuis leur écriture**. Le rapport initial qui affirmait le rang 1
validé était erroné — il n'y a pas eu de vérification indépendante avant
mon rapport.

Puisque la réponse est NON, la comparaison octet-par-octet demandée en cas de
OUI (lot 1 entre `d18d7ec` et `0aaf12a`) ne s'applique pas : le contenu des 5
fichiers du lot 1 est de toute façon identique entre les deux commits (aucune
modification n'a eu lieu pendant l'étape de commit — seul un `git add`
sélectif suivi d'un `git commit`, aucune édition).

---

## Q2 — Pourquoi le gate bloque-t-il tout ?

**Ce n'est PAS `ENTRY_ELIGIBLE_STATUSES` qui bloque.** Vérification directe :

```
$ python -c "
from app.models import SetupStatus, ENTRY_ELIGIBLE_STATUSES
print('type of member:', type(list(ENTRY_ELIGIBLE_STATUSES)[0]))
print('SetupStatus.WAITING_ENTRY_SIGNAL in set:', SetupStatus.WAITING_ENTRY_SIGNAL in ENTRY_ELIGIBLE_STATUSES)
print('str value in set:', 'WAITING_ENTRY_SIGNAL' in ENTRY_ELIGIBLE_STATUSES)
print(repr(SetupStatus.WAITING_ENTRY_SIGNAL))
print('is StrEnum:', isinstance(SetupStatus.WAITING_ENTRY_SIGNAL, str))
"
type of member: <enum 'SetupStatus'>
SetupStatus.WAITING_ENTRY_SIGNAL in set: True
str value in set: True
<SetupStatus.WAITING_ENTRY_SIGNAL: 'WAITING_ENTRY_SIGNAL'>
is StrEnum: True
```

`SetupStatus` est un `StrEnum` (`app/models.py:13,36`), donc `hash()`/`==`
se comportent comme sur `str`. La comparaison `frozenset[SetupStatus]` contre
membre `SetupStatus` OU contre `str` fonctionne dans les deux sens — pas de
bug de type ici. Dans les deux appels réels (`trading_engine.py:2472`,
`entry_order_executor.py:56`), `current_status` est bien passé comme membre
`SetupStatus` (voir les appels dans le test, `tests/test_entry_gate_current_status.py:169-171` :
`await harness.engine._handle_signal(setup, SetupStatus.WAITING_ENTRY_SIGNAL, ...)`).
Donc `current_status not in ENTRY_ELIGIBLE_STATUSES` vaut `False` pour
`WAITING_ENTRY_SIGNAL` — le gate du lot 1 laisse bien passer ce cas.

**Cause réelle : un gate PRÉEXISTANT et non lié au lot 1**, la revalidation de
cycle de vie (`entry_order_executor.py:311` `_lifecycle_allows_transmission`,
appelée sans condition depuis `execute_entry_ready` à la ligne 153). Script
jetable non commité (`../setup-order-diag-worktree/_diag_repro.py`, supprimé
après investigation) reproduisant exactement le scénario nominal du test et
imprimant les événements :

```
current_status passed: WAITING_ENTRY_SIGNAL <enum 'SetupStatus'>
status after: WAITING_ENTRY_SIGNAL
orders: []
--- events (most recent first) ---
entry_blocked_by_lifecycle_revalidation | Entry transmission blocked by setup revalidation: BLOCKED (MISSING_MARKET_DATA) | {'entry_decision': {'blocking_reasons': ['MISSING_MARKET_DATA'], 'can_send_order': False, 'decision': 'NO_ENTRY', 'status': 'BLOCKED'}, 'lifecycle': {'blocking_reasons': ['MISSING_MARKET_DATA'], 'can_be_armed': True, 'can_send_order': False, ...}}
```

Chaîne exacte, fichier:ligne :

- `entry_order_executor.py:153` — `if not self._lifecycle_allows_transmission(setup, effective_setup): return True`
  (appelé pour **toute** transmission, indépendamment du statut).
- `entry_order_executor.py:319` — `result = self.lifecycle_service.revalidate(effective_setup)`.
- `setup_lifecycle_service.py:117-118` — `session = classify_us_equity_session(now_dt); market_in_session = session == "RTH"`.
- `setup_lifecycle_service.py:198-206` :
  ```python
  prices = _snapshot_prices(market_snapshot, symbol=str(setup.get("symbol") or ""))
  if prices is None:
      if not market_in_session:
          return result(healthy_status, "MARKET_CLOSED")   # inoffensif
      blocking.append("MISSING_MARKET_DATA")
      return result(SetupStatus.BLOCKED.value, "MISSING_MARKET_DATA")  # bloque
  ```
- `trading_engine.py:178-183` — `self.setup_lifecycle = SetupLifecycleService(..., market_snapshot_provider=lambda symbol: self.market_data.latest(symbol))`,
  avec `self.market_data = MarketDataService()` (ligne 176) **instancié à vide**, sans aucune donnée injectée par le harness de test.

Le harness du test (`tests/test_entry_gate_current_status.py:93-134`,
`_EngineHarness`) construit un `TradingEngine` réel complet, mais ne nourrit
jamais `self.market_data` (aucun appel à `record_market_tick` ou équivalent
dans tout le fichier de test). Donc `_snapshot_prices(...)` renvoie toujours
`None` pour n'importe quel symbole. Comme le test tourne pendant les heures de
marché US réelles (`classify_us_equity_session(now_dt) == "RTH"` au moment de
l'exécution), `market_in_session` est `True`, et le gate préexistant tombe
systématiquement sur `MISSING_MARKET_DATA` — bloquant tout, y compris les cas
nominaux censés passer.

**Ce n'est donc pas un bug du gate introduit par le lot 1.** C'est un gate
préexistant (non touché par le diff du lot 1 — voir `git diff 9544cab 0aaf12a`
ci-dessous) que la fixture des nouveaux tests ne satisfait jamais.

```
$ git diff 9544cab 0aaf12a -- app/engine/entry_order_executor.py app/engine/trading_engine.py app/models.py
```
Confirme que le diff du lot 1 se limite exactement à : import + garde
`ENTRY_ELIGIBLE_STATUSES` dans `entry_order_executor.py:56` (nouveau paramètre
`current_status`), garde équivalente dans `trading_engine.py:2472-2485`, et la
définition de la constante dans `models.py:71-83`. Aucune touche à
`setup_lifecycle_service.py` ni à `_lifecycle_allows_transmission`.

---

## Q3 — Les fixtures dépendent-elles de `data/setups/` ?

**Non, aucun test de ce fichier (ni du reste de la suite) ne lit
`data/setups/` réel.**

`_EngineHarness.__init__` (`tests/test_entry_gate_current_status.py:97-112`) :
```python
raw_config["storage"]["setups_folder"] = str(root / "setups")
```
où `root = Path(self.tmp.name)` est un `tempfile.TemporaryDirectory()` frais —
jamais le dossier réel du dépôt. Les setups des tests sont construits en
mémoire via `_setup_record(...)` → `SetupRecord(...)` puis
`self.repository.upsert_setup(record)` (base SQLite temporaire), sans jamais
toucher au disque `data/setups/`.

Vérification élargie sur toute la suite :
```
$ grep -rn "data/setups\|setups_folder" tests/
tests/test_account_metrics.py:60:        config["storage"]["setups_folder"] = str(root / "setups")
tests/test_core_module_contracts.py:27:            setups_folder=root / "setups",
tests/test_engine_heartbeat.py:49:        config["storage"]["setups_folder"] = str(root / "setups")
tests/test_entry_gate_current_status.py:102:        raw_config["storage"]["setups_folder"] = str(root / "setups")
tests/test_intelligence_service.py:648:        config["storage"]["setups_folder"] = str(root / "setups")
tests/test_opportunity_detection.py:192,239: ... str(root / "setups")
tests/test_orders_positions_broker_truth.py:360,403: ... str(root / "setups")
tests/test_semantic_validation_service.py:100: setups_folder=root / "setups"
tests/test_setup_arm_api.py:29: ... str(root / "setups")
tests/test_setup_lifecycle_browser.py:42: ... str(root / "setups")
tests/test_setup_status_reporter.py:26,36: ... str(root / "setups")
tests/test_setup_template_api.py:56: ... str(root / "setups")
tests/test_setup_tools.py:29: ... str(root / "setups")
tests/test_signal_engine.py:25: ... str(root / "setups")
tests/test_stock_market_monitor.py:156: ... str(root / "setups")
tests/test_tws_logging.py:84,1249: ... str(root / "setups")
```
Chaque occurrence pointe systématiquement vers un `root` temporaire (`tmp`),
jamais vers `data/setups`. **Aucun test de la suite ne dépend du contenu réel
de `data/setups/`.** Les 5 sous-échecs nominaux et l'échec du replay ne
viennent donc pas de là — cause confirmée en Q2.

**`data/setups/*.json` : artefacts runtime, pas du source figé.**
Le code qui les crée/supprime, `app/engine/setup_engine.py` :
```python
# ligne 306-326 : écriture
def _save_setup_file(self, config: dict[str, Any]) -> None:
    self.setups_folder.mkdir(parents=True, exist_ok=True)
    path = self._matching_setup_file(str(config["setup_id"]))
    if path is None:
        path = self.setups_folder / f"{_safe_filename(str(config['setup_id']))}.json"
    ...
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)

# ligne 340-345 : suppression
def delete_setup_file(self, setup_id: str) -> bool:
    path = self._matching_setup_file(setup_id)
    if path is None:
        return False
    path.unlink()
    return True
```
`self.setups_folder` est réglé en production par défaut sur `data/setups`
(`app/settings.py:660`, `"setups_folder": "data/setups"`), et
`TradingEngine` transmet ce chemin à `SetupEngine` via
`trading_engine.py:143` (`setups_folder=settings.setups_folder`). Ces fichiers
sont donc écrits/supprimés par l'application elle-même à chaque création,
modification ou suppression de setup via l'API/GUI pendant les sessions de
travail — exactement l'origine plausible des 2 suppressions et ~13 créations
observées dans le diff. Ce sont des artefacts d'utilisation réelle de
l'application, pas des fichiers de configuration figés à la main comme du
code source — même s'ils sont explicitement versionnés (voir `.gitignore`
ci-dessous).

**`.gitignore` actuel :**
```
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/

# Virtualenvs (including the dedicated forecasting venvs .venv-p2/.venv-p3)
.venv/
.venv-*/
venv/

# Secrets / local config
.env

# Temp
.codex_tmp_pdf/
lightning_logs/

# Runtime data (never commit — generated by the app: sqlite db, exports, logs)
data/*
!data/setups/
!data/watchlists/

# Runtime logs (root)
logs/
```
`data/*` est ignoré globalement, mais `data/setups/` et `data/watchlists/`
sont explicitement dé-ignorés (`!data/setups/`) — un choix déjà pris
antérieurement de verser ces fichiers dans Git malgré leur nature
runtime-générée. Rien dans ce diagnostic ne remet en cause ce choix ; je le
signale seulement tel qu'il est.

---

## Q4 — Le gate en production : le code committé dans `0aaf12a` bloque-t-il tout ?

**Non — d'après le code seul, rien n'empêche la transmission en production.**

Le chemin de blocage identifié en Q2 (`_lifecycle_allows_transmission` →
`MISSING_MARKET_DATA`) dépend de `market_snapshot_provider` :
```python
# trading_engine.py:176-184
self.market_data = MarketDataService()
...
self.setup_lifecycle = SetupLifecycleService(
    ...,
    market_snapshot_provider=lambda symbol: self.market_data.latest(symbol),
)
```
En production, `self.market_data` est alimenté en continu par les ticks du
broker réel pendant que le moteur tourne connecté (mécanisme hors-scope de ce
diagnostic, non inspecté ici en détail, mais c'est la même instance que
`_record_market_tick` alimente à `trading_engine.py:2462-2463`). Donc
`MarketDataService.latest(symbol)` renvoie un snapshot non-`None` dès qu'un
tick a été reçu pour ce symbole — condition normale d'un moteur connecté et
actif, pas un blocage permanent. Le blocage `MISSING_MARKET_DATA` ne se
déclenche, par construction du code (`setup_lifecycle_service.py:198-206`),
que si **aucune donnée n'a jamais été reçue** pour ce symbole alors que le
marché est en séance (`RTH`) — un vrai gate de sécurité (pas de trading à
l'aveugle), pas un bug.

Concernant le gate ajouté par le lot 1 lui-même
(`ENTRY_ELIGIBLE_STATUSES`, `models.py:71-83`) : il autorise explicitement 9
statuts pré-entrée (`WAITING_ACTIVATION`, `WAITING_ENTRY_SIGNAL`,
`ENTRY_READY`, `WAITING_RETEST`, `WAITING_CONFIRMATION`, `WAITING_REBOUND`,
`REARMED_ON_NEW_BASE`, `VALIDATED`, `MISSED_BREAKOUT`). N'importe quel setup
dans l'un de ces statuts passe la garde de `trading_engine.py:2472` et
`entry_order_executor.py:56` sans blocage — démontré empiriquement en Q2 par
le test Python direct de la comparaison d'appartenance.

**Conclusion Q4 : le lot 1, tel que committé, ne bloque pas la transmission
d'ordres en production.** Le mode de défaillance observé est un artefact de
test (fixture sans données de marché), pas une régression de sécurité en
conditions réelles.

---

## INCERTITUDES

1. **Dépendance au temps réel (flakiness potentielle)** : le test échoue avec
   `MISSING_MARKET_DATA` parce que `classify_us_equity_session(now_dt)` a
   renvoyé `"RTH"` au moment de l'exécution (heures de marché US réelles). Je
   n'ai pas vérifié ce qui se passerait si la suite tournait hors séance
   (`market_in_session=False` → `MARKET_CLOSED`, chemin inoffensif qui ne
   bloque pas). Si c'est le cas, ces tests pourraient passer ou échouer selon
   l'heure du jour où ils sont lancés — un problème de fixture indépendant de
   celui déjà identifié, à vérifier explicitement (ex. figer l'heure via
   `current_time_provider` comme le fait déjà
   `EntryOrderExecutorDefenseInDepthTests.asyncSetUp` ligne 278).
2. **Portée de l'investigation Q4** : je n'ai pas tracé en détail le chemin
   complet par lequel `MarketDataService` est alimenté en production (ticks
   broker réels, fréquence, symboles couverts) — seulement confirmé que le
   mécanisme d'alimentation existe et est distinct du test. Une confirmation
   plus poussée demanderait de suivre `_record_market_tick` et son appelant
   amont (hors du périmètre "lecture seule sur ce diagnostic précis").
3. **Le script `_diag_repro.py`** utilisé pour Q2 a été écrit et exécuté dans
   le worktree jetable `../setup-order-diag-worktree` (jamais commité, jamais
   copié dans le dépôt principal), puis supprimé après usage. Le worktree
   lui-même existe toujours sur disque (checkout détaché sur `d18d7ec`) — je
   ne l'ai pas supprimé en attendant votre feu vert, puisque `git worktree
   remove` n'était pas explicitement autorisé par les interdictions de la
   tâche.
4. **Le contenu exact des 5 fichiers du lot 1 n'a pas été re-diffé
   octet-par-octet entre `d18d7ec` et `0aaf12a`** puisque Q1 a répondu NON
   (jamais passé) — cette comparaison n'était utile que dans la branche OUI de
   la question. Si vous voulez tout de même cette comparaison (par exemple
   pour confirmer qu'aucune frappe accidentelle n'a eu lieu pendant le staging
   sélectif), je peux la fournir sur demande.
