# Audit en lecture seule — boucle de décision "breakout retest"

Mode : audit en lecture seule. Aucun fichier de code n'a été modifié. Chaque
affirmation est accompagnée d'une référence fichier:ligne.

## 1. Point d'entrée

Le point d'entrée réel de la boucle vivante (celle qui tourne en continu
pendant que le bot est démarré) est une boucle `while True` avec
`asyncio.sleep` :

- `app/engine/trading_engine.py:649-650`
  ```
  while True:
      await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
  ```
  `HEARTBEAT_INTERVAL_SECONDS = 5` — `app/engine/trading_engine.py:75`.

Cette boucle est lancée comme tâche asyncio par :
- `TradingEngine._start_monitor(self) -> None` — `app/engine/trading_engine.py:635-638`
  ```
  def _start_monitor(self) -> None:
      if self._monitor_task and not self._monitor_task.done():
          return
      self._monitor_task = asyncio.create_task(self._monitor_loop())
  ```
- `_start_monitor` est appelé une seule fois depuis `TradingEngine.async def start(self) -> None` —
  `app/engine/trading_engine.py:304-335`, ligne d'appel `app/engine/trading_engine.py:334`.
  `start()` est la méthode qui démarre le moteur (connexion broker, chargement des
  setups, reconciliation, puis lancement de la boucle).

À chaque tick de 5s, `_heartbeat()` est appelée — `app/engine/trading_engine.py:661`
(`async def _heartbeat(self, poll_stocks: bool = True) -> None`). Elle appelle, entre
autres :
- `await self._poll_active_stock_quotes_with_timeout(current_status, broker_status)` —
  `app/engine/trading_engine.py:701`, qui elle-même appelle
  `self.stock_market_monitor.poll_active_stock_quotes(...)` —
  `app/engine/trading_engine.py:1679` (wrapper défini `app/engine/trading_engine.py:1674-1692`).

Le vrai point d'entrée de l'évaluation des setups est donc :

**`StockMarketMonitor.poll_active_stock_quotes(self, runtime_status: str, broker_status: ConnectionStatus) -> None`**
— `app/engine/stock_market_monitor.py:63-70`.

Fréquence : cette méthode est appelée toutes les 5s (rythme du heartbeat), mais
elle s'auto-limite avec un throttle interne :
- `interval = int(market_config.get("tws_stock_poll_interval_seconds", 15) or 15)` —
  `app/engine/stock_market_monitor.py:70`. Valeur par défaut confirmée dans
  `app/settings.py:172` (`"tws_stock_poll_interval_seconds": 15`), non surchargée dans
  `config.yaml` (aucune occurrence trouvée par grep).
- `last_poll_age = age_seconds(self.health.get("last_stock_poll_at"))` puis
  `if last_poll_age is not None and last_poll_age < interval: return` —
  `app/engine/stock_market_monitor.py:80-82`.

Donc : le heartbeat tourne toutes les 5s, mais l'évaluation réelle des setups
(poll des cotations + analyse) n'a lieu qu'environ toutes les 15s par symbole,
et seulement si `runtime_status == BotStatus.RUNNING.value` (`should_analyze`,
`app/engine/stock_market_monitor.py:79`) et si le broker est `CONNECTED`
(`app/engine/stock_market_monitor.py:76-78`).

Il existe un second point d'entrée, non cyclique : `TradingEngine.process_market_snapshot`
— `app/engine/trading_engine.py:2399-2414` — qui reçoit un snapshot poussé de
l'extérieur (probablement une route API — non vérifié, voir INCERTITUDES) et
appelle la même chaîne via `self._analyze_market_snapshot(snapshot)` —
`app/engine/trading_engine.py:2412`, `2416-2417`. Il rejoint le même code que
la boucle cyclique dès `analyze_market_snapshot`.

## 2. Chaîne d'appels

Arbre d'appel complet depuis le point d'entrée cyclique jusqu'à la décision
(signal `ENTRY_READY` puis transmission d'ordre) :

```
StockMarketMonitor.poll_active_stock_quotes(runtime_status, broker_status)
  (app/engine/stock_market_monitor.py:63)
  -> retourne None ; boucle sur les symboles actifs
     symbols = active_market_symbols(self.repository.list_setups())
       (app/engine/stock_market_monitor.py:84, def ligne 477)
       -> list[str] (symboles dont le setup n'est pas dans un statut terminal)

  └─ StockMarketMonitor.poll_stock_symbol(symbol, broker, timeout, should_analyze, runtime_status)
       (app/engine/stock_market_monitor.py:172-179)
       -> dict[str, Any] {"symbol", "quote_ok", "analysis_count", "timing", ...}
       appelé via poll_one() dans asyncio.gather (stock_market_monitor.py:119-129)

       └─ quote = await broker.market_snapshot(symbol, timeout=timeout)  (ligne 193)
          snapshot = quote_to_market_snapshot(symbol, quote_data)
            (app/engine/stock_market_monitor.py:492, def ligne 492-591)
            -> MarketSnapshot | None

          └─ StockMarketMonitor.analyze_market_snapshot(snapshot, timing)
               (app/engine/stock_market_monitor.py:273-317)
               -> list[dict[str, Any]] (liste "processed", un dict par setup évalué)

               ├─ evaluations = self.signal_engine.evaluate_snapshot(snapshot, build_setup_analysis_trace)
               │    (app/engine/stock_market_monitor.py:282-285)
               │
               │  SignalEngine.evaluate_snapshot(self, snapshot: MarketSnapshot, trace_builder: TraceBuilder) -> list[SignalEvaluation]
               │    (app/engine/signal_engine.py:63-115)
               │    -> list[SignalEvaluation] (dataclass ligne 42-47 : setup, current_status, signal, processed)
               │
               │    pour chaque setup du symbole (app/engine/signal_engine.py:70-72) :
               │
               │    ├─ setup = self._revalidate_lifecycle(setup, snapshot)  (ligne 73, def ligne 117-135)
               │    │    -> appelle SetupLifecycleService.revalidate_and_apply si statut dans
               │    │       LIFECYCLE_MANAGED_STATUSES (app/engine/setup_lifecycle_service.py:359-437)
               │    │       -> peut réécrire setup["status"] AVANT l'appel à evaluate() ci-dessous
               │    │       (ex : bascule vers BLOCKED / STALE_SETUP / MISSED_BREAKOUT_WAIT_RETEST)
               │    │
               │    ├─ current_status = SetupStatus(setup["status"])  (ligne 74)
               │    ├─ strategy = SetupFactory.create(setup["config"])  (ligne 76)
               │    │    SetupFactory.create(cls, setup_config: dict[str, Any]) -> BaseSetup
               │    │    (app/setups/setup_factory.py:31-37) -> instancie BreakoutRetestSetup si
               │    │    setup_config["setup_type"] == "breakout_retest" (registre ligne 22)
               │    │
               │    ├─ if current_status in TERMINAL_SIGNAL_STATUSES: continue  (ligne 79, set ligne 24-37)
               │    │
               │    └─ signal = strategy.evaluate(snapshot, current_status)  (ligne 81)  <-- DÉCISION ICI
               │         BreakoutRetestSetup.evaluate(self, snapshot: MarketSnapshot, current_status: SetupStatus) -> SetupSignal
               │         (app/setups/breakout_retest.py:45-94)
               │         -> SetupSignal (action=HOLD | INVALIDATE | STATUS_CHANGE | ENTRY_READY, ...)
               │
               │    puis, sur le signal retourné (toujours dans evaluate_snapshot) :
               │    signal = apply_entry_session_policy(signal, snapshot, self.settings)  (ligne 82)
               │    signal = self._apply_trade_guard_gates(setup, signal, snapshot)  (ligne 83, def 137-154)
               │    metadata = attach_entry_decision(...)  (ligne 84-89)
               │    self._apply_runtime_entry_guards(setup, signal)  (ligne 91, def 210-261)
               │    -> ces étapes peuvent DÉGRADER un signal ENTRY_READY (garde-fous système :
               │       session, trade_guards, coûts de transaction, ordre/position déjà en cours)
               │       mais ne peuvent pas EN CRÉER un ; la décision d'achat elle-même est prise
               │       uniquement dans BreakoutRetestSetup.evaluate() ci-dessus.
               │
               ├─ processed = [evaluation.processed for evaluation in evaluations]  (stock_market_monitor.py:287)
               │
               └─ pour chaque evaluation (stock_market_monitor.py:291-297) :
                    self.track_setup_conditions(evaluation, snapshot)  (ligne 292, def 319-334)
                      -> SetupConditionTracker.update_from_evaluation (persistance checklist UI, voir section 4)
                    await self.signal_handler(evaluation.setup, evaluation.current_status, evaluation.signal)  (ligne 293-297)

                    signal_handler == TradingEngine._handle_signal
                      (app/engine/trading_engine.py:2463-2470, injecté à la construction de
                      StockMarketMonitor via signal_handler=self._handle_signal, app/engine/trading_engine.py:293)

                    async def _handle_signal(self, setup, current_status, signal) -> None
                      (app/engine/trading_engine.py:2463-2470)

                    ├─ if self.action_executor.execute_simple_action(setup, current_status, signal): return
                    │    ActionExecutor.execute_simple_action(...) -> bool
                    │    (app/engine/action_executor.py:25-39)
                    │    -> gère HOLD (no-op), INVALIDATE et STATUS_CHANGE : appelle
                    │       transition_setup() -> self.repository.update_setup_status(...)
                    │       (app/engine/action_executor.py:41-60, persistance ligne 60)
                    │    -> retourne False si signal.action == ENTRY_READY (non géré ici)
                    │
                    ├─ if self.position_action_executor.execute_raise_stop_signal(...): return
                    │    (non pertinent pour breakout_retest en phase d'entrée, non exploré en détail)
                    │
                    └─ await self.entry_order_executor.execute_entry_ready(setup, signal)
                         EntryOrderExecutor.execute_entry_ready(self, setup, signal) -> bool
                         (app/engine/entry_order_executor.py:50-305)
                         -> si signal.action != ENTRY_READY : return False (ligne 55-56)
                         -> sinon, chaîne de garde-fous (session, trade_guards, rôle management-only,
                            auto-exécution, lifecycle, trailing stop prêt, risk_engine.evaluate,
                            cost_gate, broker_reality) puis :
                         await self.order_manager.place_entry_order(effective_setup, decision)  (ligne 263)
                         -> c'est ici que l'ordre est effectivement transmis au broker.
```

Retour de chaque fonction (résumé) :
- `poll_active_stock_quotes` -> `None`
- `poll_stock_symbol` -> `dict[str, Any]`
- `analyze_market_snapshot` -> `list[dict[str, Any]]`
- `SignalEngine.evaluate_snapshot` -> `list[SignalEvaluation]`
- `BreakoutRetestSetup.evaluate` -> `SetupSignal` (dataclass, `app/models.py:231-238`)
- `ActionExecutor.execute_simple_action` -> `bool`
- `EntryOrderExecutor.execute_entry_ready` -> `bool`
- `OrderManager.place_entry_order` -> non lu en détail dans cet audit (voir INCERTITUDES)

## 3. Les conditions

Code exact qui évalue les conditions d'un breakout retest —
`BreakoutRetestSetup.evaluate` — `app/setups/breakout_retest.py:45-94` :

```python
45  def evaluate(
46      self,
47      snapshot: MarketSnapshot,
48      current_status: SetupStatus,
49  ) -> SetupSignal:
50      breakout = self.config.get("breakout", {})
51      retest = self.config.get("retest", {})
52      entry = self.config.get("entry", {})
53      close = snapshot.close if snapshot.close is not None else snapshot.price
54      no_close_below = float(retest.get("no_close_below", retest["zone_min"]))
55
56      if (
57          current_status
58          in {
59              SetupStatus.WAITING_ACTIVATION,
60              SetupStatus.WAITING_ENTRY_SIGNAL,
61          }
62          and close < no_close_below
63      ):
64          return SetupSignal(
65              action=SignalAction.INVALIDATE,
66              reason="Close below retest invalidation",
67              target_status=SetupStatus.INVALIDATED,
68          )
69
70      daily_close = snapshot.daily_close if snapshot.daily_close is not None else close
71      if current_status == SetupStatus.WAITING_ACTIVATION:
72          if daily_close > float(breakout["daily_close_above"]):
73              return SetupSignal(
74                  action=SignalAction.STATUS_CHANGE,
75                  reason="Daily breakout confirmed",
76                  target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
77              )
78          return SetupSignal.hold("Waiting for daily breakout")
79
80      if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
81          in_retest = float(retest["zone_min"]) <= snapshot.price <= float(retest["zone_max"])
82          if in_retest and bullish_confirmation(snapshot):
83              reference_high = snapshot.high or snapshot.price
84              trigger_offset = float(entry.get("trigger_offset", 0.02))
85              return SetupSignal(
86                  action=SignalAction.ENTRY_READY,
87                  reason="Retest confirmed by bullish candle",
88                  target_status=SetupStatus.ENTRY_READY,
89                  entry_price=round(reference_high + trigger_offset, 2),
90                  stop_loss=self.stop_loss,
91              )
92          return SetupSignal.hold("Waiting for retest confirmation")
93
94      return SetupSignal.hold("No breakout action")
```

Liste condition par condition :

1. **Invalidation par clôture sous la zone** — `app/setups/breakout_retest.py:56-68`.
   - Donnée : `close` = `snapshot.close`, ou à défaut `snapshot.price` si `close` est `None`
     (`app/setups/breakout_retest.py:53`). C'est le prix "de clôture" tel que fourni par le
     snapshot au moment T — pas une clôture de bougie historique relue en base ; c'est la
     valeur du champ `close` du `MarketSnapshot` courant (voir INCERTITUDES sur l'origine
     exacte de ce champ côté broker).
   - Horizon temporel : instant T uniquement (le tick courant). Aucune bougie passée n'est
     relue.
   - Seuil : `no_close_below = float(retest.get("no_close_below", retest["zone_min"]))` —
     `app/setups/breakout_retest.py:54`. Vient de la config du setup (`retest.no_close_below`
     si présent, sinon repli sur `retest.zone_min`) — jamais hardcodé, jamais calculé.
   - Ne s'applique que si `current_status` est `WAITING_ACTIVATION` ou
     `WAITING_ENTRY_SIGNAL` (`app/setups/breakout_retest.py:57-61`).

2. **Confirmation du breakout journalier** — `app/setups/breakout_retest.py:70-78`.
   - Donnée : `daily_close = snapshot.daily_close if snapshot.daily_close is not None else close`
     (ligne 70). Champ dédié `daily_close` du `MarketSnapshot` (`app/models.py:173`), rempli
     par `quote_to_market_snapshot` à partir de `quote.get("close")` ou du prix courant
     (`app/engine/stock_market_monitor.py:505` et `516` : `close=float_value(quote.get("close")) or price`,
     `daily_close=float_value(quote.get("close")) or price`). Donc en pratique `close` et
     `daily_close` proviennent de la MÊME valeur brute `quote.get("close")` — voir
     INCERTITUDES sur ce que "close" signifie réellement côté broker (clôture de la veille ?
     dernier prix ? bougie journalière en cours ?).
   - Horizon : instant T (pas d'historique de bougies journalières consulté ici).
   - Seuil : `breakout["daily_close_above"]` — `app/setups/breakout_retest.py:72` — vient
     entièrement de la config du setup (champ obligatoire, voir section 5).
   - Ne s'exécute que si `current_status == WAITING_ACTIVATION` (ligne 71). Sur succès,
     transition vers `WAITING_ENTRY_SIGNAL` (ligne 76). Sur échec, `HOLD`
     ("Waiting for daily breakout", ligne 78).

3. **Retour dans la zone de retest + confirmation haussière** —
   `app/setups/breakout_retest.py:80-92`.
   - Sous-condition A : `in_retest = float(retest["zone_min"]) <= snapshot.price <= float(retest["zone_max"])`
     (ligne 81). Donnée : `snapshot.price`, le prix INSTANTANÉ du tick courant (pas une
     clôture, pas une moyenne). Horizon : instant T uniquement.
   - Seuils : `retest["zone_min"]` et `retest["zone_max"]` — config du setup, obligatoires
     (section 5).
   - Sous-condition B : `bullish_confirmation(snapshot)` —
     `app/setups/base_setup.py:169-174` :
     ```python
     def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
         if snapshot.bullish_candle:
             return True
         if snapshot.close is not None and snapshot.open is not None:
             return snapshot.close > snapshot.open
         return False
     ```
     Donnée : soit le champ booléen `snapshot.bullish_candle` (`app/models.py:227`, défaut
     `False`), soit à défaut la comparaison `close > open` du snapshot courant. Horizon :
     un seul point de données (le snapshot actuel), pas une bougie 15m explicitement close
     ni un historique. Note : dans la construction réelle du snapshot depuis TWS,
     `quote_to_market_snapshot` (`app/engine/stock_market_monitor.py:492-591`) NE renseigne
     JAMAIS `bullish_candle` (absent de la liste des champs assignés) — donc en production
     ce champ vaut toujours `False` par défaut et c'est systématiquement la branche
     `close > open` qui est utilisée (voir INCERTITUDES sur la sémantique de `open`/`close`
     alors renvoyés par le broker).
   - Ne s'exécute que si `current_status == WAITING_ENTRY_SIGNAL` (ligne 80). Sur succès des
     deux sous-conditions : `ENTRY_READY`, `entry_price = round(reference_high + trigger_offset, 2)`
     où `reference_high = snapshot.high or snapshot.price` (ligne 83) et
     `trigger_offset = float(entry.get("trigger_offset", 0.02))` (ligne 84, config, défaut
     hardcodé `0.02` si absent). Sur échec : `HOLD` ("Waiting for retest confirmation",
     ligne 92).

Complément — checklist d'affichage (non décisionnelle, voir section 4) qui documente les
mêmes conditions sous une autre forme, dans `app/setups/setup_conditions.py:238-259` et
`396-422` :
- `_retest_breakout_confirmed` (`app/setups/setup_conditions.py:238-247`) : relit
  `snapshot.close`/`snapshot.daily_close` contre `breakout.daily_close_above`.
- `_retest_zone` (`app/setups/setup_conditions.py:250-259`) : relit `snapshot.price` contre
  `retest.zone_min`/`zone_max`.
- Définition de la séquence à 3 conditions pour `breakout_retest` :
  `app/setups/setup_conditions.py:396-422` (`breakout_confirmed`, `retest_of_level`,
  `bullish_confirmation`), avec `status_floors={SetupStatus.WAITING_ENTRY_SIGNAL.value: 1}`
  (ligne 420).

## 4. La question centrale — la mémoire

**Il n'existe aucune mémoire fine ("le prix EST DÉJÀ PASSÉ dans la zone de
retest") entre deux cycles d'évaluation.** Vérifié explicitement :

- `BreakoutRetestSetup.evaluate` (`app/setups/breakout_retest.py:45-94`) est une fonction
  pure : elle ne lit et n'écrit aucun état interne à l'objet `BreakoutRetestSetup` autre que
  `self.config` (figé à la construction, `app/setups/base_setup.py:27-28`). Elle recalcule
  `in_retest` et `bullish_confirmation` UNIQUEMENT à partir du `snapshot` passé en paramètre
  à CET appel précis (`app/setups/breakout_retest.py:81-82`). Il n'y a ni variable de classe,
  ni cache, ni compteur de bougies consécutives, ni relecture d'un historique de prix dans
  cette fonction.
  - `snapshot.historical_bars` existe comme champ du modèle (`app/models.py`, voir aussi
    `app/engine/stock_market_monitor.py:588-590`) mais n'est JAMAIS lu par
    `BreakoutRetestSetup.evaluate` (aucune occurrence de `historical_bars` dans
    `app/setups/breakout_retest.py`, vérifié par lecture complète du fichier).

- La SEULE chose conservée entre deux cycles est le **statut grossier du setup**
  (`SetupStatus`), persisté en base SQLite :
  - Colonne `status` de la table `setups`, lue par
    `TradingRepository.list_setups(self) -> list[dict[str, Any]]` —
    `app/storage/repositories.py:443-445` (`SELECT * FROM setups ...`).
  - Écrite par `TradingRepository.update_setup_status(...)` —
    `app/storage/repositories.py:454-488`.
  - Le point d'écriture appelé depuis la boucle de décision est
    `ActionExecutor.transition_setup` — `app/engine/action_executor.py:41-60` — qui appelle
    `self.repository.update_setup_status(setup["setup_id"], new_status.value, reason)`
    (`app/engine/action_executor.py:60`), déclenché quand `signal.action` vaut
    `STATUS_CHANGE` ou `INVALIDATE` (`app/engine/action_executor.py:33-38`).
  - Au cycle suivant, `SignalEngine.evaluate_snapshot` relit ce statut :
    `current_status = SetupStatus(setup["status"])` — `app/engine/signal_engine.py:74` — et
    le passe tel quel à `evaluate()`.

  Concrètement, la mémoire se limite à trois valeurs possibles pour un
  `breakout_retest` en phase pré-entrée : `WAITING_ACTIVATION` -> `WAITING_ENTRY_SIGNAL` ->
  `ENTRY_READY` (`SetupStatus`, `app/models.py:36-68`). Le passage de
  `WAITING_ACTIVATION` à `WAITING_ENTRY_SIGNAL` mémorise UNIQUEMENT le fait que le breakout
  journalier a été confirmé un jour donné — pas la trajectoire du prix depuis. Une fois en
  `WAITING_ENTRY_SIGNAL`, chaque nouveau tick réévalue `in_retest` et
  `bullish_confirmation` à partir de zéro (`app/setups/breakout_retest.py:81-82`) : si le
  prix est sorti de la zone de retest entre-temps, la condition redevient fausse, sans
  laisser de trace du passage antérieur dans la zone.

- **Cas particulier `ENTRY_READY` non transmis** : si le signal `ENTRY_READY` est produit
  mais bloqué par un garde-fou (session, trade_guards, coûts...), le `target_status`
  `ENTRY_READY` est quand même persisté via `transition_setup`
  (`app/engine/action_executor.py:36-38`, le signal reste `STATUS_CHANGE`-like car
  `signal.action == SignalAction.ENTRY_READY` n'est PAS géré par `execute_simple_action` —
  seuls `HOLD`, `INVALIDATE`, `STATUS_CHANGE` le sont, `app/engine/action_executor.py:31-38`).
  Au cycle suivant, `current_status == SetupStatus.ENTRY_READY` : dans
  `BreakoutRetestSetup.evaluate`, aucune branche ne correspond à `ENTRY_READY`
  (seules `WAITING_ACTIVATION` et `WAITING_ENTRY_SIGNAL` sont testées, lignes 71 et 80), donc
  le code tombe sur `return SetupSignal.hold("No breakout action")` (ligne 94). Le prix
  d'entrée déjà calculé (`signal.entry_price`) n'est PAS reconservé par `evaluate()` lui-même
  au tour suivant — c'est `EntryOrderExecutor.execute_entry_ready` qui, à CHAQUE appel avec
  action `ENTRY_READY`, retente la transmission d'ordre (`app/engine/entry_order_executor.py:50-56`).
  Voir INCERTITUDES : ce audit n'a pas vérifié qui recalcule `signal.entry_price` pour les
  cycles suivants une fois le statut `ENTRY_READY` mémorisé (l'`evaluate()` renvoie `HOLD`
  sans prix ; le comportement exact du re-déclenchement de `execute_entry_ready` sur les
  cycles suivants n'a pas été tracé en détail).

- **Checklist UI persistée (`setup_condition_states`) — mémoire d'affichage, PAS de
  mémoire de décision.** Table SQLite dédiée :
  - Lecture : `TradingRepository.get_setup_condition_state` —
    `app/storage/repositories.py:523-531`.
  - Écriture : `TradingRepository.save_setup_condition_state` —
    `app/storage/repositories.py:533-543`.
  - Alimentée par `SetupConditionTracker.update_from_evaluation` —
    `app/engine/setup_condition_tracker.py:63-99` — appelée depuis
    `StockMarketMonitor.track_setup_conditions` — `app/engine/stock_market_monitor.py:319-334`
    — elle-même appelée à chaque cycle dans `analyze_market_snapshot`
    (`app/engine/stock_market_monitor.py:292`), AVANT l'appel à `signal_handler`
    (ligne 293) mais indépendamment de lui.
  - Cette table conserve des timestamps `validated_at` par condition
    (`app/engine/setup_condition_tracker.py:220-239`), mais l'ANALYSE elle-même
    (`evaluate_setup_conditions`, `app/setups/setup_conditions.py`) est recalculée à
    CHAQUE cycle à partir du snapshot courant (`app/engine/setup_condition_tracker.py:84`).
    Le nombre de conditions "verrouillées" (`validated_count`) part d'un `floor` dérivé du
    `SetupStatus` courant/cible (`app/engine/setup_condition_tracker.py:80-83`,
    `build_conditions_payload` lignes 206-211 dans le même fichier) puis avance tant que
    `checks[validated_count].met is True` POUR CE TICK
    (`app/engine/setup_condition_tracker.py:207-209` — boucle `while` dans
    `build_conditions_payload`). Si la condition "retest_of_level" redevient fausse au tick
    suivant (prix sorti de la zone) sans que le statut du setup n'ait changé entre-temps, ce
    calcul redescend en pratique la condition à `in_progress`/`pending` — confirmé par
    lecture du code, PAS testé à l'exécution (voir INCERTITUDES).
  - Le docstring de la classe le dit explicitement :
    `app/engine/setup_condition_tracker.py:52-58`, et le commentaire au point d'appel :
    `"""Met a jour la checklist persistee du setup; jamais bloquant pour le trading."""`
    — `app/engine/stock_market_monitor.py:320`. Cette persistance ne réintervient JAMAIS
    dans `SignalEngine.evaluate_snapshot` ni dans `BreakoutRetestSetup.evaluate` : elle n'est
    lue que côté API (`SetupConditionTracker.conditions_payload`,
    `app/engine/setup_condition_tracker.py:101-122`) pour l'affichage.

- **Le moteur a-t-il une notion d'état par setup ?** Oui : `SetupStatus`
  (`app/models.py:36-68`), 30 valeurs listées) est LE seul état persistant utilisé par la
  logique de décision. Pour `breakout_retest`, les états pré-entrée traversés dans l'ordre
  sont : `WAITING_ACTIVATION` -> `WAITING_ENTRY_SIGNAL` -> `ENTRY_READY` -> (ensuite, hors
  périmètre de `BreakoutRetestSetup.evaluate`) `ENTRY_ORDER_PLACED` etc. Il existe aussi
  `INVALIDATED` (clôture sous la zone) et, via un mécanisme SÉPARÉ
  (`SetupLifecycleService.revalidate_setup`, `app/engine/setup_lifecycle_service.py:87-276`,
  appliqué AVANT `evaluate()` par `SignalEngine._revalidate_lifecycle`,
  `app/engine/signal_engine.py:117-135`), les états `BLOCKED`, `STALE_SETUP`,
  `MISSED_BREAKOUT_WAIT_RETEST`, `EXPIRED`. Ce mécanisme de lifecycle a SA PROPRE notion de
  "zone de retest" (`_retest_zone`, `app/engine/setup_lifecycle_service.py:713-721`, lisant
  `config.missed_breakout.retest_zone_min/max`) — un champ de config DIFFÉRENT de
  `retest.zone_min/zone_max` utilisé par `BreakoutRetestSetup.evaluate`. Pour le setup AVGO
  réel (section 5), `missed_breakout` n'est pas présent dans le JSON de config, donc
  `_retest_zone` renverrait `None` pour ce setup précis (`app/engine/setup_lifecycle_service.py:713-721`
  retourne `None` si `zone_min`/`zone_max` absents) — non exécuté, déduit par lecture du
  code et du fichier de config.

## 5. Configuration

Un setup est configuré par un fichier JSON, un par setup, dans le dossier
`setups_folder` (défaut `data/setups/`, chargé au démarrage par
`self.setup_engine.load_all()` — `app/engine/trading_engine.py:320` — non exploré en détail
dans cet audit ; voir INCERTITUDES). Le format est un objet JSON plat avec des sections
(`breakout`, `retest`, `entry`, `risk`, `trailing_stop_loss`, etc.), validé contre un schéma
JSON Schema dédié : `config/schemas/setup.breakout_retest.schema.json`, complet ci-dessous :

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "setup.breakout_retest.schema.json",
  "title": "Breakout Retest Setup",
  "type": "object",
  "required": ["breakout", "retest"],
  "properties": {
    "breakout": {
      "type": "object",
      "required": ["daily_close_above"],
      "properties": {
        "daily_close_above": { "type": "number", "exclusiveMinimum": 0 }
      },
      "additionalProperties": true
    },
    "retest": {
      "type": "object",
      "required": ["zone_min", "zone_max"],
      "properties": {
        "zone_min": { "type": "number", "exclusiveMinimum": 0 },
        "zone_max": { "type": "number", "exclusiveMinimum": 0 },
        "no_close_below": { "type": "number", "exclusiveMinimum": 0 },
        "max_retest_days": { "type": "integer", "minimum": 1 }
      },
      "additionalProperties": true
    }
  },
  "additionalProperties": true
}
```

Champs obligatoires selon ce schéma : `breakout.daily_close_above`, `retest.zone_min`,
`retest.zone_max`. Confirmé indépendamment côté code par la validation applicative :
`BreakoutRetestSetup.validate` — `app/setups/breakout_retest.py:16-25` :
```python
16  def validate(self) -> ValidationResult:
17      result = super().validate()
18      errors = list(result.errors)
19      breakout = self.config.get("breakout", {})
20      retest = self.config.get("retest", {})
21      if breakout.get("daily_close_above") is None:
22          errors.append("breakout.daily_close_above is required")
23      if retest.get("zone_min") is None or retest.get("zone_max") is None:
24          errors.append("retest.zone_min and retest.zone_max are required")
25      return ValidationResult(valid=not errors, errors=errors)
```
Plus les règles génériques héritées de `BaseSetup.validate` —
`app/setups/base_setup.py:65-106` — qui exigent notamment `setup_id`, `symbol`,
`setup_type == "breakout_retest"`, `mode in {"paper","live"}`, `risk.max_position_amount_usd > 0`
et `risk.max_risk_usd > 0` (si le rôle permet une entrée), `trailing_stop_loss.enabled == True`,
et `trailing_stop_loss.initial_stop` positif et strictement inférieur au prix d'entrée estimé
pour un long.

Défauts déclarés (utilisés seulement si le champ correspondant est absent de la config, jamais
en remplacement d'un champ obligatoire manquant) :
- `retest.no_close_below` -> repli sur `retest.zone_min` — `app/setups/breakout_retest.py:54`.
- `entry.trigger_offset` -> `0.02` (hardcodé dans le code, pas dans un fichier de config) —
  `app/setups/breakout_retest.py:84` et aussi `app/setups/breakout_retest.py:33`.
- Modèle de valeurs par défaut par setup_type (utilisé lors de la CRÉATION d'un nouveau setup
  via l'outillage, pas lors de l'évaluation) : `SETUP_SPECIFIC_OPTIONS["breakout_retest"]` —
  `app/setups/setup_type_registry.py:123-140` (tous les champs y valent `None` ou une valeur
  par défaut générique, à remplir par l'utilisateur).

Configuration RÉELLE et COMPLÈTE du setup AVGO — fichier
`data/setups/AVGO_20260629_001.json`, contenu intégral :

```json
{
  "setup_id": "AVGO_20260629_001",
  "symbol": "AVGO",
  "enabled": true,
  "mode": "paper",
  "setup_type": "breakout_retest",
  "setup_role": "ENTRY_AND_MANAGEMENT",
  "direction": "long",
  "timeframes": { "signal": "15m", "confirmation": "1d" },
  "entry": {
    "enabled": true,
    "order_type": "STP_LMT",
    "trigger_offset": 2.5,
    "limit_offset": 2.5,
    "trigger_price": 368.5,
    "entry_price": 368.5,
    "limit_price": 371,
    "maximum_limit_price": 371,
    "cancel_if_not_filled_after_minutes": 30
  },
  "risk": {
    "max_position_amount_usd": 250,
    "max_risk_usd": 15,
    "emergency_exit_if_stop_fails": true
  },
  "management": {
    "take_profit_mode": "none",
    "never_lower_stop": true,
    "stop_management": {
      "mode": "structure",
      "never_lower_stop": true,
      "trail_type": "ATR_OR_STRUCTURE",
      "atr_timeframe": "1h",
      "atr_multiplier": 1.5,
      "steps": [
        { "condition": "close_15m_above", "level": 380, "new_stop": 360 },
        { "condition": "close_15m_above", "level": 395, "new_stop": 368.5 },
        { "condition": "close_15m_above", "level": 410, "new_stop": 390 }
      ]
    }
  },
  "breakout": {
    "resistance": 360,
    "broken_resistance": 360,
    "daily_close_above": 360,
    "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
    "fast_breakout_volume_ratio_min": 1.5,
    "confirmed_breakout_volume_ratio_min": 0.8,
    "confirmed_breakout_hold_bars": 2,
    "confirmed_breakout_timeframe": "15m",
    "close_above_resistance_required": true
  },
  "volume_confirmation": {
    "enabled": false,
    "signal_timeframe": "15m",
    "comparison_mode": "SAME_TIME_OF_DAY",
    "average_sample_days": 20,
    "fast_volume_ratio_min": 1.5,
    "normal_volume_ratio_min": 1,
    "confirmed_volume_ratio_min": 0.8,
    "confirmed_hold_bars": 2,
    "close_above_level_required": true,
    "reject_detection_enabled": true,
    "max_upper_wick_ratio": 0.5
  },
  "retest": {
    "zone_min": 358,
    "zone_max": 366,
    "confirmation_required": true,
    "confirmation_timeframe": "15m"
  },
  "rearm": { "new_local_resistance": 371, "new_trigger": 371.5, "new_limit": 374 },
  "trend_filter": { "enabled": true, "required_trend": "uptrend" },
  "targets": [],
  "notes": "Setup choisi: breakout_retest prudent. AVGO est revenu sur la zone de retest 358-366 proche de la moyenne 200j, mais reste sous la moyenne 50j; pas d'achat direct tant que le prix ne reprend pas 368.50 en 15m. Invalidation si perte de 354.80. Aucun take profit fixe; gestion uniquement par stop structurel, sans jamais baisser le stop.",
  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "initial_stop": 354.8,
    "current_stop": 354.8,
    "never_lower_stop": true,
    "stop_source": "MIGRATED_FROM_LEGACY_STOP",
    "applies_to": "ENTRY_AND_POSITION_MANAGEMENT",
    "migration_status": "MIGRATED_TO_TRAILING_STOP",
    "activation": {
      "mode": "ON_ENTRY_FILL",
      "activate_before_entry_transmission": true,
      "entry_order_requires_attached_trailing_stop": true
    },
    "calculation": { "...": "voir fichier source pour le détail complet (ATR/structure/risk_constraints), non reproduit ici pour la lisibilité — section identique à celle lue dans data/setups/AVGO_20260629_001.json:112-173" },
    "ratchet_rules": { "...": "voir data/setups/AVGO_20260629_001.json:174-192" },
    "broker_order": {
      "order_type": "TRAIL_OR_MANAGED_STOP",
      "attach_to_entry_order": true,
      "required_before_entry_transmission": true,
      "use_native_ibkr_trailing_order_if_available": true,
      "fallback_to_managed_stop_updates": true,
      "parent_child_bracket_required": true,
      "entry_parent_transmit": false,
      "trailing_stop_child_transmit": true,
      "block_if_broker_stop_not_confirmed": true
    },
    "audit": { "...": "voir data/setups/AVGO_20260629_001.json:204-211" }
  }
}
```
(Le contenu intégral et exact, non abrégé, est dans le fichier source
`data/setups/AVGO_20260629_001.json`, 213 lignes ; les sections `calculation`, `ratchet_rules`
et `audit` de `trailing_stop_loss` sont abrégées ci-dessus par souci de lisibilité — elles ne
sont PAS lues par `BreakoutRetestSetup.evaluate`.)

Valeurs pertinentes pour `evaluate()` :
- `breakout.daily_close_above = 360` — `data/setups/AVGO_20260629_001.json:60`
- `retest.zone_min = 358`, `retest.zone_max = 366` — `data/setups/AVGO_20260629_001.json:82-83`
- `retest.no_close_below` : ABSENT du JSON -> repli sur `zone_min = 358`
  (`app/setups/breakout_retest.py:54`)
- `entry.trigger_offset = 2.5` — `data/setups/AVGO_20260629_001.json:16` (remplace le défaut
  hardcodé `0.02`)
- `trailing_stop_loss.initial_stop = 354.8` — `data/setups/AVGO_20260629_001.json:101`, utilisé
  comme `stop_loss` dans le signal `ENTRY_READY` via `BaseSetup.stop_loss`
  (`app/setups/base_setup.py:54-58`).

Note : ce fichier JSON ne contient PAS de champ `status` — le statut runtime
(`WAITING_ACTIVATION`, etc.) n'existe qu'en base SQLite (table `setups`, colonne `status`),
initialisé au chargement via `BaseSetup.initial_status()` (`app/setups/base_setup.py:60-63`).

## 6. Le cas AVGO — simulation manuelle

Hypothèses tirées de la config réelle ci-dessus : `zone_min=358`, `zone_max=366`,
`no_close_below=358` (repli), `daily_close_above=360`, `trigger_offset=2.5`,
`initial_stop=354.8`.

### Situation 1 : le prix est actuellement DANS la zone de retest (ex. `price=362`)

Pré-requis implicite pour être en zone de retest post-breakout : `current_status` doit déjà
être `WAITING_ENTRY_SIGNAL` (sinon on serait encore en train d'attendre le breakout
journalier). On se place donc avec `current_status = SetupStatus.WAITING_ENTRY_SIGNAL`.

Déroulé ligne par ligne de `app/setups/breakout_retest.py:45-94` :
- Ligne 53 : `close = snapshot.close` (supposons `close=362`, cohérent avec `price=362`).
- Ligne 54 : `no_close_below = 358`.
- Lignes 56-63 : `current_status in {WAITING_ACTIVATION, WAITING_ENTRY_SIGNAL}` -> VRAI
  (on est en `WAITING_ENTRY_SIGNAL`) ET `close < no_close_below` -> `362 < 358` -> **FAUX**.
  -> le bloc `if` ne s'exécute pas, pas d'`INVALIDATE`.
- Ligne 70 : `daily_close = snapshot.daily_close` (supposons `daily_close=362` aussi).
- Ligne 71 : `current_status == WAITING_ACTIVATION` -> **FAUX** (on est en
  `WAITING_ENTRY_SIGNAL`) -> ce bloc entier (lignes 71-78) est ignoré.
- Ligne 80 : `current_status == WAITING_ENTRY_SIGNAL` -> **VRAI** -> on entre dans le bloc.
- Ligne 81 : `in_retest = 358 <= 362 <= 366` -> **VRAI**.
- Ligne 82 : `bullish_confirmation(snapshot)` — dépend du snapshot :
  - **Si `snapshot.close > snapshot.open`** (ex. `open=360, close=362`) -> **VRAI**.
    - Ligne 82 entière : `in_retest and bullish_confirmation(snapshot)` -> **VRAI**.
    - Lignes 83-91 : `reference_high = snapshot.high or snapshot.price` (ex. `high=363`),
      `trigger_offset=2.5`, `entry_price = round(363 + 2.5, 2) = 365.5`,
      `stop_loss = self.stop_loss = 354.8` (`app/setups/base_setup.py:54-58`).
      **Verdict : `SignalAction.ENTRY_READY`**, `target_status=ENTRY_READY`,
      `entry_price=365.5`, `stop_loss=354.8` (`app/setups/breakout_retest.py:85-91`).
  - **Si `snapshot.close <= snapshot.open`** (ex. bougie neutre/baissière dans la zone) ->
    **FAUX**.
    - Ligne 82 entière -> **FAUX**.
    - Ligne 92 : **Verdict : `SetupSignal.hold("Waiting for retest confirmation")`**
      (`app/setups/breakout_retest.py:92`, `SetupSignal.hold` défini `app/models.py:240-242`).

Condition vraie / fausse (cas bougie haussière) : `in_retest` VRAI, `close < no_close_below`
FAUX, `bullish_confirmation` VRAI -> **verdict ENTRY_READY**.
Condition vraie / fausse (cas bougie non haussière) : `in_retest` VRAI, `close < no_close_below`
FAUX, `bullish_confirmation` FAUX -> **verdict HOLD** ("Waiting for retest confirmation").

### Situation 2 : le prix est actuellement AU-DESSUS du trigger, après être passé dans la zone il y a 2 heures

Exemple : `price=369` maintenant (au-dessus de `trigger_price=368.5` /
`entry_price=365.5+trigger_offset`), le prix étant passé par `362` (dans la zone) il y a 2
heures avec, disons, une bougie haussière à ce moment-là.

Deux sous-cas selon ce qui s'est passé il y a 2 heures :

**Sous-cas 2a : le cycle d'évaluation d'il y a 2 heures a bien tourné et a produit
`ENTRY_READY` à ce moment-là.**
- À ce cycle passé, `transition_setup` (`app/engine/action_executor.py:41-60`) n'a PAS été
  appelé pour un `ENTRY_READY` — car `ActionExecutor.execute_simple_action`
  (`app/engine/action_executor.py:25-39`) ne gère que `HOLD`, `INVALIDATE`, `STATUS_CHANGE` ;
  pour `ENTRY_READY` c'est `EntryOrderExecutor.execute_entry_ready`
  (`app/engine/entry_order_executor.py:50-305`) qui a été appelé, et qui (sauf blocage par un
  garde-fou) a persisté un nouveau statut via la chaîne de `order_manager.place_entry_order`
  (non explorée en détail, voir INCERTITUDES) — vraisemblablement `ENTRY_ORDER_PLACED` ou
  équivalent. Si c'est le cas, au tick ACTUEL (2h plus tard), `current_status` n'est déjà plus
  `WAITING_ENTRY_SIGNAL`. Dans `SignalEngine.evaluate_snapshot`
  (`app/engine/signal_engine.py:79`), si ce statut fait partie de `TERMINAL_SIGNAL_STATUSES`
  (`app/engine/signal_engine.py:24-37` — ne contient PAS `ENTRY_ORDER_PLACED`), l'évaluation
  continue quand même, mais `BreakoutRetestSetup.evaluate` ne reconnaît que
  `WAITING_ACTIVATION` et `WAITING_ENTRY_SIGNAL` (lignes 71 et 80) : pour tout autre statut la
  ligne 94 s'applique -> **Verdict : `HOLD` ("No breakout action")**. Le prix à `369`
  aujourd'hui n'est alors même plus examiné par cette fonction.

**Sous-cas 2b : le passage dans la zone il y a 2 heures n'a PAS déclenché `ENTRY_READY`**
(ex. bougie non haussière à ce moment-là, ou le cycle a été manqué — pas de tick capté
exactement à ce moment, cf. throttle 15s / poll asynchrone). `current_status` est resté
`WAITING_ENTRY_SIGNAL` jusqu'à maintenant.
- Déroulé au tick ACTUEL (`price=369`, en dehors de la zone `358-366`) :
  - Ligne 53-54 : `close` (supposons `369`), `no_close_below=358`.
  - Lignes 56-63 : `close < no_close_below` -> `369 < 358` -> **FAUX** -> pas d'invalidation.
  - Ligne 71 : `current_status == WAITING_ACTIVATION` -> **FAUX**.
  - Ligne 80 : `current_status == WAITING_ENTRY_SIGNAL` -> **VRAI**.
  - Ligne 81 : `in_retest = 358 <= 369 <= 366` -> **FAUX** (369 > 366).
  - Ligne 82 : `in_retest and bullish_confirmation(...)` -> **FAUX** (court-circuité par
    `in_retest` déjà faux ; `bullish_confirmation` n'est même pas nécessairement évaluée en
    Python grâce au court-circuit du `and`, mais cela ne change rien au résultat).
  - Ligne 92 : **Verdict : `SetupSignal.hold("Waiting for retest confirmation")`**.

**Conclusion de la simulation** : dans le sous-cas 2b (le plus probable si aucun ordre n'a
été transmis), le passage antérieur du prix dans la zone, 2 heures plus tôt, avec ou sans
bougie haussière à ce moment précis, N'A AUCUNE INFLUENCE sur le verdict actuel : seul l'état
du `snapshot` au tick présent compte, et comme `in_retest` est maintenant faux (prix sorti de
la zone par le haut), le verdict est `HOLD`, quel que soit ce qui s'est passé il y a 2 heures
— ce qui recoupe exactement la conclusion de la section 4 sur l'absence de mémoire fine.

## 7. Logs et observabilité

Logs émis pendant le cycle d'évaluation (par ordre d'apparition dans la chaîne d'appel) :

- `logger.info("TWS stock poll started: %d symbols (%s)", ...)` —
  `app/engine/stock_market_monitor.py:106-110`, avant le poll de tous les symboles.
- `logger.warning("TWS stock poll skipped: no monitored setup symbols")` —
  `app/engine/stock_market_monitor.py:87`, si aucun setup actif.
- `logger.info(message)` où `message = stock_quote_message(symbol, {...})` —
  `app/engine/stock_market_monitor.py:234-235` — log détaillé de la cotation reçue (prix,
  bid/ask, volume, ATR, etc., via `stock_quote_fields_text`,
  `app/engine/stock_market_monitor.py:601-671`), + persistance de l'événement
  `stock_quote` (`app/engine/stock_market_monitor.py:236-242`, via `self.event_store.record`).
- `logger.warning(message)` / événement `stock_quote_missing` si la cotation broker est
  absente ou sans prix exploitable — `app/engine/stock_market_monitor.py:198-211`,
  `221-231`, `254-267`.
- `logger.info("TWS stock poll finished: %d symbols, %d quotes OK, %d errors, %d analyses in %.1f ms", ...)` —
  `app/engine/stock_market_monitor.py:160-170`, résumé de fin de cycle.
- Événement `stock_analysis` (niveau `INFO`) — `record_stock_analysis`,
  `app/engine/stock_market_monitor.py:336-362` — contient `snapshot`, la liste `processed`
  (un item par setup, avec `action`, `reason`, `status`, `entry_price`, `stop_loss`, `trace`)
  et le `timing`. C'est le log le plus riche pour comprendre pourquoi un setup a ou n'a pas
  déclenché.
  - Ce log est dédupliqué si le contenu est identique au précédent dans la fenêtre de cooldown
    (`should_suppress_repeated_event`, `app/engine/stock_market_monitor.py:425-461`,
    cooldown par défaut `300s`, config `market.event_deduplication.repeated_hold_cooldown_seconds`
    — ligne 434). Donc en régime stable (HOLD répété), le log n'est PAS ré-émis à chaque tick.
  - Alternative si aucun setup actif pour le symbole : événement `stock_analysis_skipped`,
    `record_stock_analysis_skipped`, `app/engine/stock_market_monitor.py:364-383`.

**Oui, le code explique explicitement pourquoi un setup ne se déclenche pas**, à deux
niveaux :

1. **Le `reason` texte du `SetupSignal` lui-même**, directement dans
   `app/setups/breakout_retest.py` :
   - `"Waiting for daily breakout"` — ligne 78 (breakout journalier pas encore confirmé).
   - `"Waiting for retest confirmation"` — ligne 92 (soit hors zone, soit bougie non
     haussière — le message ne distingue PAS laquelle des deux sous-conditions a échoué).
   - `"Close below retest invalidation"` — ligne 66 (invalidation).
   - `"No breakout action"` — ligne 94 (statut hors des cas gérés, ex. `ENTRY_READY` déjà
     atteint).
   Ce `reason` est propagé dans `evaluation.processed["reason"]`
   (`app/engine/signal_engine.py:103`) et donc dans le log `stock_analysis` ci-dessus.

2. **La trace condition-par-condition** — `build_setup_analysis_trace`
   (`app/engine/setup_diagnostics.py:33-...`), appelée comme `trace_builder` à chaque
   évaluation (`app/engine/signal_engine.py:92`, callback injecté depuis
   `app/engine/stock_market_monitor.py:284` = `build_setup_analysis_trace`), et stockée dans
   `evaluation.processed["trace"]` (`app/engine/signal_engine.py:111`). Pour
   `setup_type == "breakout_retest"` spécifiquement, le détail est construit
   `app/engine/setup_diagnostics.py:347-383` :
   - `"Invalidation retest"` : état `threshold_state(close, no_close_below, ">=")`,
     valeur actuelle = `close`, attendu = `>= {no_close_below}` (lignes 360-365).
   - `"Breakout journalier"` : état `threshold_state(daily_close, daily_level, ">")`, valeur
     actuelle = `daily_close`, attendu = `> {daily_level}` (lignes 366-371).
   - `"Prix dans zone retest"` : état `range_state(price, zone_min, zone_max)`, valeur
     actuelle = `price`, attendu = l'intervalle `zone_min-zone_max` (lignes 372-377).
   - `"Bougie de confirmation"` : `"ok"` si `snapshot_bullish_confirmation(snapshot)` sinon
     `"wait"`, détail `"haussiere"` / `"non confirmee"`, attendu =
     `"close > open ou bullish_candle"` (lignes 378-383).
   Ce format donne, condition par condition, l'état (`ok`/`wait`/`bad`), la valeur observée et
   la valeur attendue — ce qui permet de savoir PRÉCISÉMENT laquelle des 4 conditions bloque,
   contrairement au simple `reason` textuel de `evaluate()` qui regroupe zone + bougie sous un
   même message.

   Cette trace est calculée à chaque cycle par `SignalEngine.evaluate_snapshot` via le
   paramètre `trace_builder` (`app/engine/signal_engine.py:66`, appel ligne 92) — donc
   disponible même quand le log `stock_analysis` est supprimé par déduplication, TANT QUE ce
   `processed` est conservé quelque part (ex. exposé par une route API de lecture des
   derniers résultats — non vérifié dans cet audit, voir INCERTITUDES).

Recherche complémentaire (non détaillée ici) : le tracker de checklist persistant
(`SetupConditionTracker`, section 4) expose aussi un `summary_message` lisible
(`_summary_message`, `app/engine/setup_condition_tracker.py:310-332`), par exemple
`"2/3 conditions validees - etape actuelle: Confirmation haussiere"` — mais ce message est
une vue dérivée pour l'API/UI, pas un log technique, et n'est pas garanti refléter exactement
l'état interne d'`evaluate()` à l'instant T (voir la logique de floor/verrouillage décrite en
section 4).

## INCERTITUDES

Liste honnête de ce qui n'a pas été vérifié ou pas pu être déterminé avec certitude dans le
temps imparti à cet audit :

1. **Sémantique exacte de `quote.get("open")` / `quote.get("close")` côté broker IBKR.**
   `quote_to_market_snapshot` (`app/engine/stock_market_monitor.py:492-591`) assigne
   `close=float_value(quote.get("close")) or price` et
   `daily_close=float_value(quote.get("close")) or price` à partir de la MÊME clé brute
   `"close"` du dict `quote` retourné par `broker.market_snapshot(...)`
   (`app/engine/stock_market_monitor.py:193`). Je n'ai PAS remonté jusqu'au code du connecteur
   broker (`ib_insync` ou équivalent) pour vérifier si ce `"close"` est la clôture de la veille,
   le dernier prix (`last`), ou une clôture de bougie 15m en cours. Cela affecte directement le
   sens réel de la condition n°2 (section 3) et du calcul de `bullish_confirmation` (`close >
   open` où `open` a la même incertitude).
   **Vérifié après coup** : `docs/Lecture_des_donnees_TWS_IBKR.md` a été lu en entier — ce
   document NE clarifie PAS ce point. Il couvre uniquement la distinction entre positions,
   ordres ouverts, exécutions et valeurs de compte (`ib.positions()`, `ib.openTrades()`,
   `ib.fills()`, `ib.accountValues()`), sans jamais mentionner `reqMktData`, un ticker/snapshot
   de marché, ni les champs `open`/`high`/`low`/`close` d'une cotation. L'incertitude reste
   donc entière : le code du connecteur broker qui construit le dict `quote` consommé par
   `broker.market_snapshot(...)` (`app/engine/stock_market_monitor.py:193`) et remplit ses clés
   `"open"`/`"close"`/`"high"` n'a pas été localisé ni lu dans cet audit.

2. **`OrderManager.place_entry_order`** (`app/engine/entry_order_executor.py:263`) — je n'ai
   pas lu le corps de cette méthode (fichier `app/engine/order_manager.py`, non ouvert dans cet
   audit). Je ne sais donc pas avec certitude : (a) quel `SetupStatus` est écrit après
   transmission réussie d'un ordre (supposé `ENTRY_ORDER_PLACED` par déduction du nom de
   l'enum, non vérifié dans le code), (b) si un `ENTRY_READY` répété (garde-fou bloquant à
   chaque cycle) retente réellement `place_entry_order` à chaque tick ou s'il existe une
   protection anti-doublon en amont dans `order_manager` lui-même (au-delà de
   `DuplicateOrderError` catché ligne 285-292 de `entry_order_executor.py`, dont je n'ai pas
   vérifié les conditions de levée exactes).

3. **`setup_engine.load_all()`** (`app/engine/trading_engine.py:320`) — je n'ai pas ouvert
   `app/engine/setup_template_service.py` ni le module de chargement réel des fichiers JSON
   (`SetupEngine`, mentionné dans `tests/test_setup_status_reporter.py` mais son fichier source
   exact n'a pas été relu ligne à ligne). Je suppose, sans l'avoir vérifié précisément, que
   c'est ce mécanisme qui lit `data/setups/*.json`, appelle `SetupFactory.create` /
   `.validate()` / `.to_record()` et fait le premier `upsert_setup`. Non confirmé par lecture
   directe dans cet audit.

4. **`StateMachine.transition` et `StateMachine.explain_transition`**
   (`app/engine/state_machine.py`, référencé `app/engine/action_executor.py:49` et
   `app/engine/setup_lifecycle_service.py:14`, `410`) — fichier non ouvert. Je ne connais donc
   pas la table exacte des transitions autorisées entre `SetupStatus` ; je n'ai pas vérifié que
   `WAITING_ENTRY_SIGNAL -> ENTRY_READY` (utilisé par `BreakoutRetestSetup.evaluate`) est
   effectivement une transition acceptée par cette state machine (probable, non confirmé).

5. **Comportement exact de `should_suppress_repeated_event`** en présence de changements très
   fins (ex. `percent_bucket` arrondi, `app/engine/stock_market_monitor.py:696`) — je n'ai pas
   vérifié empiriquement si un simple changement de prix DANS la zone de retest (sans
   changement d'action/reason/status) déclenche ou non un nouveau log `stock_analysis`, ni la
   fréquence réelle observée en production.

6. **Point d'entrée de `TradingEngine.process_market_snapshot`** —
   `app/engine/trading_engine.py:2399-2414`. Je n'ai pas identifié la route API (ou autre
   déclencheur) qui appelle cette méthode ; je ne sais pas si elle est utilisée en production
   (flux temps réel alternatif) ou seulement dans des tests / un endpoint de debug.

7. **`snapshot.high` réellement fourni par le broker en continu** — la ligne
   `reference_high = snapshot.high or snapshot.price` (`app/setups/breakout_retest.py:83`)
   suppose que `snapshot.high` est le plus haut du jour ou de la bougie ; je n'ai pas vérifié
   quelle grandeur exacte `quote.get("high")` représente côté IBKR
   (`app/engine/stock_market_monitor.py:503`).

8. **Persistance / recalcul de `signal.entry_price` sur les cycles suivant l'atteinte de
   `ENTRY_READY`** — voir section 4 et point 2 ci-dessus : le chaînon exact entre "statut
   `ENTRY_READY` mémorisé" et "nouvelle tentative de transmission au tick suivant avec quel
   prix" n'a pas été tracé jusqu'au bout (dépend du point 2, `order_manager.py` non lu).

9. **Contenu exact de `_row_to_dict`** (`app/storage/repositories.py`, référencé mais pas
   entièrement relu) pour la table `setups` — je n'ai pas vérifié explicitement TOUTES les
   colonnes de la table (schéma SQL de création non ouvert dans cet audit), seulement les
   requêtes UPDATE/SELECT pertinentes citées en section 4.

Tout le reste affirmé dans ce rapport est appuyé par une lecture directe du fichier et de la
ligne citée, dans ce dépôt, à l'état où il se trouvait au moment de l'audit (branche
`feat/setup-conditions`).
