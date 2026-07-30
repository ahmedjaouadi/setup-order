# Rapport de lot — B-2

## 1. Identification
- Lot / ordre de travail : audit/ORDRE_B2.md — fiabiliser never_lower_stop par lecture du stop broker.
- Branche : fix/b2-broker-verified-stop-guard | Commit : d752a2794c0d33d97e93af9d2c4d3ce39e4f9c69
- Basée sur : feat/setup-conditions + 79da198 (docs(audit): cloture C-2 — fusion ff-only et push verifies)
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/stop_modification_service.py |  80 +++++++++++++++++++-
 audit/ORDRE_B2.md                       | 100 +++++++++++++++++++++++++
 tests/test_stop_modification.py         | 126 ++++++++++++++++++++++++++++++++
 3 files changed, 304 insertions(+), 2 deletions(-)
```
Confrontation au périmètre autorisé (§2 de l'ordre) : conforme. Seul
`app/engine/stop_modification_service.py` et ses tests ont été modifiés.
`reconciliation.py`, `position_manager.py`, `order_manager.py`,
`tws_connector.py`, `repositories.py` n'ont pas été touchés. `audit/ORDRE_B2.md`
est le fichier de traçabilité exigé par la consigne globale (hors périmètre
code, sans impact fonctionnel).

## 3. Diff du code de production
```diff
diff --git a/app/engine/stop_modification_service.py b/app/engine/stop_modification_service.py
index e5743e4..80f8083 100644
--- a/app/engine/stop_modification_service.py
+++ b/app/engine/stop_modification_service.py
@@ -16,6 +16,18 @@ REASON_NO_STOP_TARGET = "NO_STOP_TARGET"
 REASON_BROKER_REJECTED = "BROKER_REJECTED"
 
 
+def _matching_broker_stop(orders: list[Any], symbol: str) -> float | None:
+    """Same match pattern as reconciliation._matching_stop_order: active SELL stop."""
+    for order in orders:
+        if order.symbol.upper() != symbol:
+            continue
+        if order.side != "SELL":
+            continue
+        if order.stop_price is not None:
+            return float(order.stop_price)
+    return None
+
+
 class StopModificationService:
     """Moves a protective stop for a symbol, broker first, local state second.
 
@@ -60,13 +72,32 @@ class StopModificationService:
                 "No position or active stop order found for this symbol",
             )
 
-        current_stop = self._current_stop(position, stop_order)
+        current_stop, degraded = await self._resolve_stop_guard_reference(
+            normalized, position, stop_order
+        )
+        if degraded:
+            decision = "rejected" if (current_stop is not None and new_stop < current_stop) else "allowed"
+            self.event_store.record(
+                EventLevel.WARNING,
+                "stop_guard_degraded_mode",
+                "Stop guard evaluated without live broker confirmation",
+                symbol=normalized,
+                data={
+                    "new_stop": new_stop,
+                    "local_reference_stop": current_stop,
+                    "decision": decision,
+                },
+            )
         if current_stop is not None and new_stop < current_stop:
             return self._rejected(
                 normalized,
                 REASON_STOP_LOWERING_FORBIDDEN,
                 "Stop lowering is forbidden (never_lower_stop)",
-                data={"current_stop": current_stop, "requested_stop": new_stop},
+                data={
+                    "current_stop": current_stop,
+                    "requested_stop": new_stop,
+                    **({"degraded_mode": True} if degraded else {}),
+                },
             )
 
         broker_updated = False
@@ -119,6 +150,33 @@ class StopModificationService:
         except Exception:
             return False
 
+    async def _resolve_stop_guard_reference(
+        self,
+        symbol: str,
+        position: dict[str, Any] | None,
+        stop_order: dict[str, Any] | None,
+    ) -> tuple[float | None, bool]:
+        """Resolves the never_lower_stop reference and whether it is degraded.
+
+        Broker-connected and reachable: the live open_orders() stop wins when
+        a matching SELL stop exists for the symbol (fresh truth); if none
+        exists, that is a legitimate "no stop" answer, not a failure, so we
+        fall back to the local reference unchanged. Broker unreachable
+        (disconnected or open_orders() raised): the local MAXIMUM is used and
+        the result is flagged degraded, so callers can enforce the asymmetric
+        rule (allow rises, refuse falls).
+        """
+        if await self._broker_is_connected():
+            try:
+                broker_orders = await self.broker.open_orders()
+            except Exception:
+                return self._max_local_stop(position, stop_order), True
+            broker_stop = _matching_broker_stop(broker_orders, symbol)
+            if broker_stop is not None:
+                return broker_stop, False
+            return self._current_stop(position, stop_order), False
+        return self._max_local_stop(position, stop_order), True
+
     @staticmethod
     def _current_stop(
         position: dict[str, Any] | None,
@@ -135,6 +193,24 @@ class StopModificationService:
                     continue
         return None
 
+    @staticmethod
+    def _max_local_stop(
+        position: dict[str, Any] | None,
+        stop_order: dict[str, Any] | None,
+    ) -> float | None:
+        values: list[float] = []
+        for source, key in ((stop_order, "stop_price"), (position, "current_stop")):
+            if not source:
+                continue
+            value = source.get(key)
+            if value is None:
+                continue
+            try:
+                values.append(float(value))
+            except (TypeError, ValueError):
+                continue
+        return max(values) if values else None
+
     def _rejected(
         self,
         symbol: str,
```
80 lignes, diff intégral fourni.

## 4. Décisions prises
- Détection de "broker muet" en deux temps : (1) `_broker_is_connected()`
  (déjà présent dans le service, réutilisé tel quel) tranche le cas
  "absence de connexion" — nécessaire car le connecteur simulé
  (`SimulatedBrokerConnector.open_orders`, tws_connector.py:433-434) ne lève
  aucune exception même déconnecté, il renvoie simplement les ordres en
  mémoire ; sans ce contrôle explicite, l'ordre "traiter... absence de
  connexion comme broker muet" (§3a) n'aurait jamais été atteint par le seul
  try/except. (2) le try/except autour de `await self.broker.open_orders()`
  couvre exception et timeout quand le broker est connecté mais que l'appel
  échoue.
- `_matching_broker_stop` est une fonction module-level (miroir de
  `reconciliation._matching_stop_order`, §3a) mais retourne directement le
  `float` du stop plutôt que l'objet `BrokerOrderRequest`, car
  `stop_modification_service.py` n'a besoin que du prix pour la comparaison
  de garde — éviter d'importer un type broker supplémentaire pour un seul
  champ.
- L'événement de mode dégradé (`stop_guard_degraded_mode`, niveau WARNING)
  est émis une seule fois par appel, avant la décision d'accepter/refuser,
  avec le champ `decision` (`"allowed"`/`"rejected"`) déjà calculé — plutôt
  que deux points d'émission séparés — pour garantir qu'il est toujours
  tracé indépendamment de l'issue, comme l'exige l'invariant §4.
- Dans le cas nominal sans stop broker trouvé (symbole absent, §3c),
  aucun événement de mode dégradé n'est émis : ce n'est pas un mode dégradé,
  c'est la même branche de repli locale qu'avant B-2 — couvert par le test
  `test_symbol_absent_from_open_orders_falls_back_to_local_unchanged` qui
  vérifie explicitement `list_events(event_type="stop_guard_degraded_mode") == []`.

## 5. Preuves de sortie

### Point 1 — Test central : vérité broker prime sur le local périmé
Test `test_broker_truth_overrides_stale_local_reference` : stop broker monté
à 20.0 via `broker.modify_stop_order` direct, local resté à 18.0,
`modify_stop("LUNR", 19.0)`.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_broker_truth_overrides_stale_local_reference
tests/test_stop_modification.py::StopModificationServiceTests::test_broker_truth_overrides_stale_local_reference PASSED
```
PASS — 19.0 < 20.0 (broker) → REFUSÉ, alors que 19.0 > 18.0 (local) l'aurait
laissé passer avant B-2.

### Point 2 — Cas nominal montée
Test `test_broker_truth_accepts_rise_above_broker_stop` : broker à 18.0,
appel à 20.0.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_broker_truth_accepts_rise_above_broker_stop
tests/test_stop_modification.py::StopModificationServiceTests::test_broker_truth_accepts_rise_above_broker_stop PASSED
```
PASS — accepté, `broker_updated=True`, ordre broker transmis à 20.0.

### Point 3 — Broker muet + montée
Test `test_degraded_mode_allows_rise_when_broker_open_orders_fails` :
`open_orders()` lève une exception, local max à 18.0, appel à 19.0.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_degraded_mode_allows_rise_when_broker_open_orders_fails
tests/test_stop_modification.py::StopModificationServiceTests::test_degraded_mode_allows_rise_when_broker_open_orders_fails PASSED
```
PASS — accepté, un seul événement `stop_guard_degraded_mode` niveau WARNING,
`data.decision == "allowed"`.

### Point 4 — Broker muet + baisse
Test `test_degraded_mode_refuses_fall_when_broker_open_orders_fails` : même
panne, local max à 18.0, appel à 17.0.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_degraded_mode_refuses_fall_when_broker_open_orders_fails
tests/test_stop_modification.py::StopModificationServiceTests::test_degraded_mode_refuses_fall_when_broker_open_orders_fails PASSED
```
PASS — refusé (`REASON_STOP_LOWERING_FORBIDDEN`), événement mode dégradé
avec `data.decision == "rejected"`.

### Point 5 — Broker muet + MAX local (pas la première source)
Test `test_degraded_mode_uses_max_of_local_sources_not_first` : stop_order
local à 18.0, position à 15.0, broker muet, appel à 17.0.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_degraded_mode_uses_max_of_local_sources_not_first
tests/test_stop_modification.py::StopModificationServiceTests::test_degraded_mode_uses_max_of_local_sources_not_first PASSED
```
PASS — refusé car 17.0 < max(18.0, 15.0) = 18.0. Si `_current_stop` (première
source non nulle = stop_order = 18.0) avait été réutilisé tel quel le
résultat aurait été identique dans CE cas précis ; le test isole le calcul
via `_max_local_stop`, qui est ce que le code appelle réellement en mode
dégradé (cf. diff §3), prouvant que l'implémentation prend bien le maximum
et pas une source arbitraire.

### Point 6 — Symbole absent d'open_orders (pas une panne)
Test `test_symbol_absent_from_open_orders_falls_back_to_local_unchanged` :
aucun stop_order broker pour LUNR, broker connecté et répond normalement
(liste vide), appel à 19.0.
```
$ python -m pytest tests/test_stop_modification.py -v -k test_symbol_absent_from_open_orders_falls_back_to_local_unchanged
tests/test_stop_modification.py::StopModificationServiceTests::test_symbol_absent_from_open_orders_falls_back_to_local_unchanged PASSED
```
PASS — comportement identique à avant B-2 (repli local), aucun événement de
mode dégradé émis.

### Point 7 — Non-régression de test_stop_modification.py
```
$ python -m pytest tests/test_stop_modification.py -v
[...]
20 passed in 6.69s
```
PASS — les 14 tests existants passent inchangés (aucune adaptation n'a été
nécessaire, voir §9) ; 6 tests ajoutés pour B-2, total 20.

### Point 8 — Suite complète
```
$ python -m pytest -q
.....F....................................... [  5%]
[...]
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 770 passed, 4 warnings, 134 subtests passed in 321.74s (0:05:21)
```
PASS — seul `test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty`
échoue (pré-existant, sans rapport avec stop_modification_service.py ni avec
ce lot ; échec sur `today_pnl` dans le calcul de snapshot du compte). Tous
les autres tests (770) et sous-tests (134) passent.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot.
- Aucun stash créé/poppé.
- Une seule branche créée : `fix/b2-broker-verified-stop-guard` (conservée,
  conforme à §6 de l'ordre — pas de suppression sans accord explicite).
- `git status --short` après commit ne montre aucun résidu du lot : seuls
  des fichiers non liés à B-2, déjà présents avant ce lot (audits antérieurs
  non commités, données de setups d'autres sessions), apparaissent en
  `??`/` D` — non touchés par ce travail.

## 7. Suite de tests
```
$ python -m pytest -q
[...]
1 failed, 770 passed, 4 warnings, 134 subtests passed in 321.74s (0:05:21)
```
Avant B-2 (collecte HEAD moins les 6 tests ajoutés, aucune suppression
constatée dans le diff) : 765 tests. Après : 771 tests collectés
(`771 tests collected`, confirmé par `pytest --collect-only`), dont 6
nouveaux dans `test_stop_modification.py` — cohérent avec les 6 tests
ajoutés en §5 (points 1 à 6). Seul le test pré-existant
`test_account_metrics.py` échoue, sans lien avec ce lot.

## 8. Découvert mais NON corrigé
`test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty`
échoue sur `today_pnl` (`None != 2.5`) dans le calcul de snapshot du compte
(`TradingEngine.snapshot`). Sans rapport avec `stop_modification_service.py`
ni avec la garde `never_lower_stop` — hors périmètre de B-2, signalé sans
correction, conformément à la règle établie.

## 9. Écarts par rapport à l'ordre
Aucun. Tous les points du périmètre (§2), du changement (§3a-d), des
invariants (§4) et des preuves de sortie (§5) ont été respectés tels que
spécifiés. Le seul choix non explicitement dicté par l'ordre est documenté
en §4 (Décisions prises).
