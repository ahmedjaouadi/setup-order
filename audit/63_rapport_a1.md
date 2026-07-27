# Rapport de lot — A-1

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_A1.md` — clôturer une position sur
  fill SELL (root cause A, T1) : la branche FILLED de
  `_update_setup_after_reconciled_order` ignorait tout fill SELL
  (`if side != "BUY": return`).
- Branche : `fix/a1-close-on-sell` | Commit :
  `42397b6dd997fd53eb1e8f41ac43c54f3c3eb477`.
- Basée sur : `feat/setup-conditions` @ `6f7eae98752254e3b5390c52c8199189b67e5b16`
  (docs(audit): fill in commit hash in A6-SEC lot report).
- Mergée : non | Poussée : non.

## 2. Fichiers touchés
```
 app/engine/reconciliation.py | 142 ++++++++++++++++++++++++++
 app/engine/trading_engine.py |   1 +
 audit/ORDRE_A1.md            | 110 ++++++++++++++++++++
 tests/test_reconciliation.py | 232 ++++++++++++++++++++++++++++++++++++++++++-
 4 files changed, 483 insertions(+), 2 deletions(-)
```
Confronté à la liste autorisée par l'ordre (section 2) : **conforme**.
- `app/engine/reconciliation.py` : injection du paramètre
  `position_manager` dans le constructeur + nouvelle branche SELL de
  `_update_setup_after_reconciled_order` déléguée à une méthode privée
  `_handle_sell_fill`. Aucune ligne de la branche BUY existante, des
  branches SUBMITTED/CANCELLED, ni de `_resolve_fill_details` n'a été
  modifiée.
- `app/engine/trading_engine.py` : un seul ajout, `position_manager=
  self.position_manager` au site d'instanciation (lignes ~170-175) —
  aucune autre ligne touchée.
- `tests/test_reconciliation.py` : tests de la branche SELL.
- `repositories.py`, `position_manager.py`, `order_manager.py`,
  `app/setups/*` : **non touchés**, conforme à l'interdiction.

Deux fichiers `data/setups/*.json` apparaissent supprimés dans l'arbre de
travail (`CODI_20260628_001.json`, `TXN_20260630_001.json`) : préexistants
à ce lot (déjà marqués supprimés dans le `git status` de tout début de
session, avant toute action de ce lot), non touchés ni commités par ce
lot. Idem pour les fichiers/dossiers non suivis listés en section 6.

## 3. Diff du code de production
```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index 6e9bc4f..ce5879b 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -7,6 +7,7 @@ from typing import Any, TypedDict
 from app.broker.ib_models import BrokerExecution, BrokerOrderRequest, BrokerPosition
 from app.broker.tws_connector import BrokerConnector
 from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
+from app.engine.position_manager import PositionManager
 from app.engine.post_fill_progression import PostFillProgression
 from app.models import ConnectionStatus, EventLevel, OrderStatus, PositionRecord, SetupStatus
 from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
@@ -50,11 +51,13 @@ class ReconciliationEngine:
         event_store: EventStore,
         broker: BrokerConnector,
         settings: dict[str, Any] | None = None,
+        position_manager: PositionManager | None = None,
     ) -> None:
         self.repository = repository
         self.event_store = event_store
         self.broker = broker
         self.settings = settings if isinstance(settings, dict) else {}
+        self.position_manager = position_manager
         self.progression = PostFillProgression(repository, event_store)
 
     async def run(self) -> ReconciliationResult:
@@ -502,6 +505,15 @@ class ReconciliationEngine:
                 )
             return
         if status == OrderStatus.FILLED.value:
+            if side == "SELL":
+                self._handle_sell_fill(
+                    order,
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    broker_positions=broker_positions or [],
+                    broker_executions=broker_executions or [],
+                )
+                return
             if side != "BUY":
                 return
             if setup_status not in {
@@ -620,6 +632,136 @@ class ReconciliationEngine:
             return None, None
         return int(order_quantity), broker_position.average_price
 
+    def _handle_sell_fill(
+        self,
+        order: dict[str, Any],
+        *,
+        setup_id: str,
+        symbol: str,
+        broker_positions: list[BrokerPosition],
+        broker_executions: list[BrokerExecution],
+    ) -> None:
+        order_id = str(order.get("id") or "")
+        # Root cause A / T1: only the matched-executions branch of
+        # _resolve_fill_details is trustworthy for a SELL — its broker-position
+        # fallback would hand back an entry cost, not a sale price.
+        match = _match_executions_to_order(broker_executions, order)
+        if match is None or not match["quantity_matches"]:
+            self.repository.update_setup_status(
+                setup_id,
+                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+                "Sell filled but fill price/quantity unavailable",
+            )
+            self.event_store.record(
+                EventLevel.CRITICAL,
+                "sell_filled_unknown_fill_details",
+                "Sell filled but fill price/quantity unavailable",
+                setup_id=setup_id,
+                symbol=symbol,
+                data={"order_id": order_id},
+            )
+            return
+        sold_quantity = round(match["quantity"])
+        sell_price = match["price"]
+
+        previous = self.repository.get_position(symbol)
+        if previous is None:
+            self.repository.update_setup_status(
+                setup_id,
+                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+                "Sell filled but no local position exists",
+            )
+            self.event_store.record(
+                EventLevel.CRITICAL,
+                "sell_filled_no_local_position",
+                "Sell filled but no local position exists",
+                setup_id=setup_id,
+                symbol=symbol,
+                data={"order_id": order_id, "sold_quantity": sold_quantity, "sell_price": sell_price},
+            )
+            return
+
+        remaining_quantity = int(previous["quantity"]) - sold_quantity
+
+        broker_position = next(
+            (position for position in broker_positions if position.symbol.upper() == symbol),
+            None,
+        )
+        if broker_position is not None and not math.isclose(
+            broker_position.quantity, remaining_quantity, rel_tol=1e-9, abs_tol=1e-6
+        ):
+            self.repository.update_setup_status(
+                setup_id,
+                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+                "Sell filled but broker position quantity disagrees",
+            )
+            self.event_store.record(
+                EventLevel.CRITICAL,
+                "sell_filled_broker_quantity_mismatch",
+                "Sell filled but broker-reported quantity disagrees with the local computation",
+                setup_id=setup_id,
+                symbol=symbol,
+                data={
+                    "order_id": order_id,
+                    "local_remaining_quantity": remaining_quantity,
+                    "broker_quantity": broker_position.quantity,
+                },
+            )
+            return
+
+        if self.position_manager is None:
+            self.repository.update_setup_status(
+                setup_id,
+                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+                "Sell filled but position manager is not configured",
+            )
+            self.event_store.record(
+                EventLevel.CRITICAL,
+                "sell_filled_no_position_manager",
+                "Sell filled but position manager is not configured",
+                setup_id=setup_id,
+                symbol=symbol,
+                data={"order_id": order_id},
+            )
+            return
+
+        average_price = float(previous["average_price"])
+        # current_price must be the real sell fill price (audit 62 Q3): it is
+        # what PositionManager._notify_if_closed uses as the exit price to
+        # feed the circuit breaker's realized PnL — never a generic quote.
+        self.position_manager.open_or_update_position(
+            setup_id,
+            symbol,
+            quantity=remaining_quantity,
+            average_price=average_price,
+            current_price=sell_price,
+            stop_loss=previous.get("current_stop"),
+        )
+
+        realized_pnl = round((sell_price - average_price) * sold_quantity, 2)
+        if remaining_quantity == 0:
+            new_status = SetupStatus.CLOSED.value
+            reason = "Position closed on sell fill"
+        else:
+            new_status = SetupStatus.PARTIAL_EXIT.value
+            reason = "Position partially exited on sell fill"
+        self.repository.update_setup_status(setup_id, new_status, reason)
+
+        self.event_store.record(
+            EventLevel.SYNC,
+            "position_closed_on_sell",
+            reason,
+            setup_id=setup_id,
+            symbol=symbol,
+            data={
+                "order_id": order_id,
+                "sold_quantity": sold_quantity,
+                "sell_price": sell_price,
+                "realized_pnl": realized_pnl,
+                "remaining_quantity": remaining_quantity,
+            },
+        )
+
 
 def _protective_stop(config: dict) -> float | None:
     trailing = config.get("trailing_stop_loss", {})
diff --git a/app/engine/trading_engine.py b/app/engine/trading_engine.py
index 6b8a90a..38350ac 100644
--- a/app/engine/trading_engine.py
+++ b/app/engine/trading_engine.py
@@ -172,6 +172,7 @@ class TradingEngine:
             self.event_store,
             self.broker,
             settings.raw,
+            position_manager=self.position_manager,
         )
         self.market_data = MarketDataService()
         self.state_machine = StateMachine()
```

## 4. Décisions prises
- **`position_manager` optionnel (`= None`) plutôt qu'un paramètre
  obligatoire.** L'ordre demande d'« ajouter le paramètre au constructeur »
  sans préciser s'il doit être obligatoire. Les tests existants de
  `tests/test_reconciliation.py` (branches BUY, SUBMITTED, CANCELLED)
  construisent `ReconciliationEngine(repository, event_store, broker)`
  sans 4e ni 5e argument. Rendre `position_manager` obligatoire aurait cassé
  ces constructions et forcé à toucher leur code de setUp — ce que
  l'invariant §4 de l'ordre interdit implicitement ("comportement
  strictement inchangé" pour BUY/SUBMITTED/CANCELLED). Décision : paramètre
  mot-clé avec défaut `None`, plus un garde-fou dans `_handle_sell_fill`
  (`sell_filled_no_position_manager` → `MANUAL_REVIEW_REQUIRED`) pour ne
  jamais planter ni écrire silencieusement si un appelant oublie de
  l'injecter. En production (`trading_engine.py`), il est toujours fourni.
- **Délégation à une méthode privée `_handle_sell_fill`** plutôt qu'un bloc
  `if/else` inline dans `_update_setup_after_reconciled_order`. Non imposé
  par l'ordre ; choisi pour garder la méthode existante lisible (elle gère
  déjà 3 statuts × 2 côtés) et pour que le diff de la branche BUY reste à
  une seule ligne modifiée (`if side != "BUY": return` déplacé après le
  nouvel aiguillage SELL, contenu inchangé).
- **Ordre des vérifications dans `_handle_sell_fill`** : résolution du fill
  (étape 1 de l'ordre) AVANT lecture de la position locale (étape 2).
  L'ordre liste ces étapes dans cet ordre numéroté ; conservé tel quel. Une
  conséquence testée : si les exécutions ne résolvent rien ET qu'aucune
  position locale n'existe, l'événement émis est
  `sell_filled_unknown_fill_details` (étape 1), pas
  `sell_filled_no_local_position` (étape 2) — voir
  `test_sell_filled_without_resolvable_fill_goes_to_manual_review`.
- **`stop_loss` passé à `open_or_update_position`** : `previous.get(
  "current_stop")`, c'est-à-dire le stop déjà connu localement. L'ordre ne
  précise pas explicitement cette valeur (point 4 de la section 3 le laisse
  ouvert avec `stop_loss=...`) ; c'est la seule source disponible sans
  toucher au broker (invariant §4 : « aucun appel broker ajouté »).
- **`realized_pnl` inclus dans l'événement `position_closed_on_sell`** :
  calculé comme `(sell_price - average_price) * sold_quantity` — c'est le
  PnL réalisé sur la quantité vendue, cohérent pour une clôture totale
  (où `sold_quantity == previous.quantity`, même valeur que celle envoyée
  au circuit-breaker) comme pour une sortie partielle (portion vendue
  uniquement). Cette valeur est calculée ici uniquement pour la
  traçabilité de l'événement ; le PnL réellement transmis au
  circuit-breaker est calculé indépendamment par
  `PositionManager._notify_if_closed` (code non touché, invariant §4).
- **Test `test_sell_filled_triggers_no_write` réécrit.** Ce test affirmait
  le comportement retiré par ce lot (`if side != "BUY": return` → aucune
  écriture). Il a été renommé
  `test_sell_filled_without_resolvable_fill_goes_to_manual_review` et son
  assertion changée pour affirmer le nouveau comportement
  (`MANUAL_REVIEW_REQUIRED` + événement `sell_filled_unknown_fill_details`).
  L'ordre exclut explicitement ce test de l'exigence de non-régression
  (§5.6 ne cite que « la branche BUY et les branches SUBMITTED/CANCELLED »)
  — c'est précisément le comportement que ce lot change.

## 5. Preuves de sortie

### Point 1 — Fill SELL total → CLOSED, quantity=0, event émis
Test : `SellFilledBranchTests::test_total_sell_closes_position` (entrée
qty=10 @ 100, vente qty=10 @ 90).
```
$ python -m pytest tests/test_reconciliation.py -q -k test_total_sell_closes_position
.                                                                        [100%]
1 passed, 29 deselected in 0.41s
```
Statut setup `CLOSED`, `position["quantity"] == 0`, événement
`position_closed_on_sell` présent — **PASS**.

### Point 2 — Fill SELL partiel → PARTIAL_EXIT, average_price inchangé
Test : `SellFilledBranchTests::test_partial_sell_sets_partial_exit_and_keeps_entry_cost`
(position 40 @ 100, vente 10 @ 110).
```
$ python -m pytest tests/test_reconciliation.py -q -k test_partial_sell_sets_partial_exit_and_keeps_entry_cost
.                                                                        [100%]
1 passed, 29 deselected in 0.39s
```
Statut `PARTIAL_EXIT`, `position["quantity"] == 30`,
`position["average_price"] == 100.0` (coût d'entrée, pas le prix de vente
110) — **PASS**.

### Point 3 — LE TEST CRITIQUE : PnL circuit-breaker sur le prix de vente réel
Test : `SellFilledBranchTests::test_realized_pnl_uses_real_sell_price_not_generic_quote`
(entrée 100, vente 90, qty 10 → attendu `realized_pnl == -100.0`).
```
$ python -m pytest tests/test_reconciliation.py -q -k test_realized_pnl_uses_real_sell_price_not_generic_quote
.                                                                        [100%]
1 passed, 29 deselected in 0.40s
```
Le `PositionManager` du test est construit avec un `on_position_closed`
qui capture `(symbol, pnl)` dans `self.closed_calls`. Assertion :
`self.closed_calls == [(self.symbol, -100.0)]` — exactement -100.0, pas
une valeur approchée dérivée d'un cours générique — **PASS**.

### Point 4 — Recoupement broker incohérent → MANUAL_REVIEW_REQUIRED, pas de clôture
Test : `SellFilledBranchTests::test_broker_position_mismatch_triggers_manual_review`
(position locale 40, vente 10 → 30 restant calculé localement ; broker
rapporte 20).
```
$ python -m pytest tests/test_reconciliation.py -q -k test_broker_position_mismatch_triggers_manual_review
.                                                                        [100%]
1 passed, 29 deselected in 0.39s
```
Statut `MANUAL_REVIEW_REQUIRED`, événement
`sell_filled_broker_quantity_mismatch`, position **inchangée**
(`quantity == 40`, aucune écriture), `on_position_closed` **jamais appelé**
— **PASS**.

### Point 5 — Exécutions non résolues → MANUAL_REVIEW_REQUIRED
Deux scénarios couverts :
- `test_unresolved_executions_trigger_manual_review` (position locale
  existe, aucune exécution ne correspond à l'ordre).
- `test_sell_resolved_but_no_local_position_triggers_manual_review`
  (exécutions résolvent bien, mais aucune position locale connue —
  étape 2 de l'ordre).
```
$ python -m pytest tests/test_reconciliation.py -q -k "test_unresolved_executions_trigger_manual_review or test_sell_resolved_but_no_local_position_triggers_manual_review"
..                                                                       [100%]
2 passed, 28 deselected in 0.61s
```
**PASS** pour les deux.

### Point 6 — Non-régression : branche BUY + branches SUBMITTED/CANCELLED
```
$ python -m pytest tests/test_reconciliation.py -k "FilledBranchTests or SubmittedBranchReviewLockTests" -q
...................                                                     [100%]
19 passed, 11 deselected in 5.40s
```
Les 9 tests de `FilledBranchTests` (barreaux 1/2/3 BUY, statut déjà
`IN_POSITION`, garde anti double `record_fill`) et les 4 tests de
`SubmittedBranchReviewLockTests` (S5b-1, A6-SEC) passent sans qu'aucune de
leurs assertions n'ait été modifiée — seule
`test_sell_filled_without_resolvable_fill_goes_to_manual_review` (ex-
`test_sell_filled_triggers_no_write`) a été réécrite, comme documenté et
justifié en section 4 — **PASS**.

### Point 7 — Suite complète : seul test_account_metrics.py en échec
```
$ python -m pytest -q
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 746 passed, 4 warnings, 134 subtests passed in 315.07s (0:05:15)
```
Seul échec : `test_account_metrics.py` (calcul de `today_pnl` à partir de
positions broker dans `TradingEngine.snapshot()` — code non touché par ce
lot, ni par le diff de `trading_engine.py`, ni par
`reconciliation.py`/`position_manager.py`). Conforme à l'attendu de
l'ordre — **PASS**.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot.
- `git stash list` affiche `stash@{0}` (« On feat/setup-conditions-ui:
  residuel feat setup-conditions... ») : préexistant à ce lot, créé sur une
  autre branche avant le début de cette session, non touché ni utilisé par
  ce lot.
- Aucune branche créée au-delà de `fix/a1-close-on-sell` (demandée par
  l'ordre, section 6). Aucun worktree créé.
- Confirmation :
```
$ git status --short
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/28_pre_s2.md
... (fichiers et suppressions préexistants au tout début de session,
non touchés par ce lot)
```
Rien d'ajouté par ce lot ne subsiste hors des 3 fichiers listés en
section 2 (`app/engine/reconciliation.py`, `app/engine/trading_engine.py`,
`tests/test_reconciliation.py`) plus `audit/ORDRE_A1.md` et ce rapport
(`audit/63_rapport_a1.md`), tous commités dans `42397b6`.

## 7. Suite de tests
`tests/test_reconciliation.py` : 24 → 30 méthodes `def test_` (+6 net) :
6 nouveaux tests dans `SellFilledBranchTests` (total, partiel, PnL
critique, recoupement broker, exécutions non résolues, position locale
absente) ; `test_sell_filled_triggers_no_write` renommé/réécrit en
`test_sell_filled_without_resolvable_fill_goes_to_manual_review` (même
test, nouvelle assertion — pas un ajout net) :
```
$ git show feat/setup-conditions:tests/test_reconciliation.py | grep -c "def test_"
24
$ grep -c "def test_" tests/test_reconciliation.py
30
```
Suite complète, après ce lot :
```
$ python -m pytest -q
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 746 passed, 4 warnings, 134 subtests passed in 315.07s (0:05:15)
```
746 passants, cohérent avec les +6 tests nets ajoutés à
`test_reconciliation.py` (aucun autre fichier de test n'a changé son
nombre de tests). L'unique échec (`test_account_metrics.py`) est le même
que celui déjà nommé par l'ordre comme seul échec attendu ; il porte sur
un calcul (`today_pnl`) dans `TradingEngine.snapshot()` totalement
indépendant du diff de ce lot (aucune ligne de `snapshot()`,
`account_metrics` ou du calcul `today_pnl` n'a été touchée) — pré-existant
et hors périmètre.

## 8. Découvert mais NON corrigé
- `CLOSED`/`PARTIAL_EXIT` ne sont pas protégés par la garde S5b-3a
  (`_ACTIVE_STATUSES`) : un fill SELL peut donc aujourd'hui écraser un
  `MANUAL_REVIEW_REQUIRED`/`ERROR_REQUIRES_MANUAL_REVIEW` existant avec
  `CLOSED` ou `PARTIAL_EXIT`. C'est le sous-lot **A-1b**, explicitement hors
  périmètre de A-1 (section 1 et 4 de l'ordre). Signalé, non corrigé ici.
- `_resolve_fill_details` n'est pas adaptée pour un SELL (branche 2, repli
  broker, rendrait un coût d'entrée) : `_handle_sell_fill` n'appelle donc
  jamais `_resolve_fill_details` et ré-implémente la résolution via
  `_match_executions_to_order` directement (branche 1 seule). Ce n'est pas
  un problème caché — c'est la conséquence directe de l'interdiction
  explicite de toucher `_resolve_fill_details` (section 2 de l'ordre) —
  mais je le signale car cela introduit une petite duplication logique
  (le calcul « branche 1 » existe maintenant à deux endroits légèrement
  différents dans le fichier). Non corrigé, hors périmètre (aurait exigé
  de toucher `_resolve_fill_details`, interdit).

## 9. Écarts par rapport à l'ordre
Aucun. Tous les points de la section 3 (a/b et sous-étapes 1-6), les
invariants de la section 4, le périmètre de la section 2 et les preuves de
sortie de la section 5 ont été respectés à la lettre. La seule
modification apportée à un test existant hors de la nouvelle classe
(`test_sell_filled_triggers_no_write`) est explicitement exclue de
l'exigence de non-régression par l'ordre lui-même (section 5, point 6 :
« les tests existants de la branche BUY et des branches
SUBMITTED/CANCELLED », pas la branche SELL de FILLED — précisément ce que
ce lot change).
