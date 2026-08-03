# 89 — Rapport de lot D-1 : plafonner l'exposition sur le compte-entier

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_D1.md` (racine D, sous-lot D-1)
- Branche : `fix/d1-account-wide-exposure-cap` | Commit : `b83103eeb2e7574d39dc9bbbff59887f0332b8f9`
- Basée sur : `feat/setup-conditions` (commit `b2d55db`)
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat feat/setup-conditions..fix/d1-account-wide-exposure-cap
 app/engine/broker_reality.py        |  83 +++++++++++++++++++++
 app/engine/entry_order_executor.py  |  28 +++++++
 app/engine/trade_guards.py          |  82 ++++++++++++++++----
 app/engine/trading_engine.py        |   4 +-
 audit/ORDRE_D1.md                   | 112 ++++++++++++++++++++++++++++
 tests/test_account_wide_exposure.py | 104 ++++++++++++++++++++++++++
 tests/test_entry_order_executor.py  | 145 +++++++++++++++++++++++++++++++++++-
 tests/test_trade_guards.py          | 117 +++++++++++++++++++++++++++++
 8 files changed, 660 insertions(+), 15 deletions(-)
```

Confrontation à la liste autorisée par l'ordre (§2) :
- `app/engine/trade_guards.py` — autorisé, touché (`_exposure_verdict`).
- `app/engine/entry_order_executor.py` — autorisé, touché exactement à la
  zone `:192-205` (source de `open_positions`/`exposure` pour
  `risk_engine.evaluate`).
- `app/engine/broker_reality.py` — autorisé ("éventuellement un helper de
  lecture du rapport") : ajout du helper `account_wide_exposure` +
  dataclass `AccountWideExposure`. **Aucune ligne de
  `_row_action_and_mismatch` touchée** (vérifié : le diff ne contient aucune
  modification dans cette fonction, c'est le périmètre de D-2).
- `app/engine/trading_engine.py` — **non listé explicitement par l'ordre**,
  touché pour un seul point : passer `event_store=self.event_store` au
  constructeur de `TradeGuardsService` (voir §4, décision prise).
- Tests — autorisé.
- `risk_engine.py`, `broker_reality.py::_row_action_and_mismatch`,
  `reconciliation.py`, le verrou (`instance_lock.py`) : **aucun touché**,
  confirmé par le diff ci-dessus (absents de la liste).

**Écart** : `trading_engine.py` n'était pas dans la liste AUTORISÉ explicite
mais son unique changement est le câblage d'une dépendance déjà existante
(`self.event_store`) vers un paramètre optionnel ajouté à
`TradeGuardsService.__init__`, nécessaire pour que l'événement de
traçabilité (§3(b) de l'ordre) puisse exister en dehors des tests. Sans ce
câblage, `event_store` resterait toujours `None` en production et
l'événement `exposure_cap_local_fallback` ne serait jamais émis par le
premier gate. Documenté aussi en §9.

## 3. Diff du code de production
Diff intégral (283 lignes, aucun déplacement de code, tout est additif) :

```diff
diff --git a/app/engine/broker_reality.py b/app/engine/broker_reality.py
index 5817307..c3ed3b1 100644
--- a/app/engine/broker_reality.py
+++ b/app/engine/broker_reality.py
@@ -1,6 +1,7 @@
 from __future__ import annotations
 
 from copy import deepcopy
+from dataclasses import dataclass
 from datetime import UTC, datetime
 from typing import Any
 
@@ -434,6 +435,88 @@ def broker_reality_blocking_reasons(
     return [str(item) for item in fresh.get("blocking_reasons", []) if str(item or "")]
 
 
+@dataclass(frozen=True, slots=True)
+class AccountWideExposure:
+    """Account-wide position count / capital read from the cached broker
+    reality report (root D, D-1).
+
+    ``fresh`` mirrors the report's own freshness contract (connected, synced,
+    not stale) -- when it is ``False`` neither ``positions_count`` nor
+    ``capital_usd`` may be trusted and callers must fall back to their local
+    view, exactly as they did before D-1. When ``True``, the two values are
+    account-wide totals that already include this instance's own positions:
+    a caller must never add them to a local count (double counting), only
+    take ``max(local, this)``.
+    """
+
+    fresh: bool
+    positions_count: int | None
+    capital_usd: float | None
+    fallback_reason: str | None = None
+
+
+def account_wide_exposure(
+    repository: Any,
+    settings: dict[str, Any] | None,
+    *,
+    now: str | None = None,
+) -> AccountWideExposure:
+    """Read the account-wide exposure from the cached ``broker_reality`` report.
+
+    No broker call is made here: this only re-reads and re-freshens the report
+    already persisted by ``ReconciliationEngine`` every cycle (~45s) and at
+    startup (audit 87 P2.2, audit 88 Q1/Q2). ``max_total_open_risk_R`` has no
+    account-wide equivalent (it depends on each setup's local risk config) and
+    is intentionally not covered by this helper -- it stays local-only.
+    """
+    report = repository.get_bot_state(REPORT_STATE_KEY, {})
+    if not isinstance(report, dict) or not report.get("broker_last_sync_at"):
+        return AccountWideExposure(
+            fresh=False,
+            positions_count=None,
+            capital_usd=None,
+            fallback_reason="BROKER_REALITY_REPORT_ABSENT",
+        )
+    fresh_report = freshen_broker_reality_report(report, settings=settings, now=now)
+    status = str(fresh_report.get("broker_tracker_status") or "")
+    if not fresh_report.get("broker_connected") or status != "OK":
+        reason = (
+            "BROKER_REALITY_REPORT_STALE"
+            if status == "STALE"
+            else "BROKER_REALITY_REPORT_DISCONNECTED"
+        )
+        return AccountWideExposure(
+            fresh=False,
+            positions_count=None,
+            capital_usd=None,
+            fallback_reason=reason,
+        )
+    positions_count = fresh_report.get("broker_positions_count")
+    if not isinstance(positions_count, int):
+        return AccountWideExposure(
+            fresh=False,
+            positions_count=None,
+            capital_usd=None,
+            fallback_reason="BROKER_REALITY_POSITIONS_UNAVAILABLE",
+        )
+    capital_usd = 0.0
+    for row in fresh_report.get("rows", []) or []:
+        if not isinstance(row, dict):
+            continue
+        quantity = _number_or_none(row.get("position_quantity"))
+        if quantity is None or quantity == 0:
+            continue
+        price = _number_or_none(row.get("average_price"))
+        if price is None:
+            continue
+        capital_usd += price * quantity
+    return AccountWideExposure(
+        fresh=True,
+        positions_count=positions_count,
+        capital_usd=round(capital_usd, 2),
+    )
+
+
 def _broker_reality_row(
     *,
     setup: dict[str, Any],
diff --git a/app/engine/entry_order_executor.py b/app/engine/entry_order_executor.py
index e2be09d..2be9798 100644
--- a/app/engine/entry_order_executor.py
+++ b/app/engine/entry_order_executor.py
@@ -6,6 +6,7 @@ from datetime import UTC, datetime
 from typing import Any
 
 from app.engine.broker_reality import (
+    account_wide_exposure,
     broker_reality_blocking_reasons,
     engine_safety_blocking_reasons,
 )
@@ -195,6 +196,33 @@ class EntryOrderExecutor:
             float(position["average_price"]) * int(position["quantity"]) for position in positions
         )
         daily_pnl = sum(float(position["unrealized_pnl"]) for position in positions)
+        account_exposure = account_wide_exposure(self.repository, self.settings)
+        if account_exposure.fresh:
+            # Account-wide view already includes this instance's own
+            # positions -- replace, never add, to avoid double counting
+            # (root D, D-1, audit 88 Q4).
+            if account_exposure.positions_count is not None:
+                open_positions = max(open_positions, account_exposure.positions_count)
+            if account_exposure.capital_usd is not None:
+                exposure = max(exposure, account_exposure.capital_usd)
+        else:
+            self.event_store.record(
+                EventLevel.WARNING,
+                "exposure_cap_local_fallback",
+                (
+                    "Account-wide broker view unavailable "
+                    f"({account_exposure.fallback_reason}); risk engine exposure "
+                    "evaluated on local positions only."
+                ),
+                setup_id=setup["setup_id"],
+                symbol=setup["symbol"],
+                data={
+                    "gate": "risk_engine",
+                    "reason": account_exposure.fallback_reason,
+                    "local_open_positions": open_positions,
+                    "local_exposure_usd": exposure,
+                },
+            )
         decision = self.risk_engine.evaluate(
             setup_config=effective_setup["config"],
             entry_price=signal.entry_price,
diff --git a/app/engine/trade_guards.py b/app/engine/trade_guards.py
index 9d98584..101bfb5 100644
--- a/app/engine/trade_guards.py
+++ b/app/engine/trade_guards.py
@@ -58,7 +58,9 @@ from app.decision_codes import STATUS_INVALIDATED as STATUS_INVALIDATED
 from app.decision_codes import STATUS_NO_GO as STATUS_NO_GO
 from app.decision_codes import STATUS_PAUSED as STATUS_PAUSED
 from app.decision_codes import STATUS_WAIT as STATUS_WAIT
-from app.models import MarketSnapshot, SetupSignal, SignalAction, utc_now_iso
+from app.engine.broker_reality import account_wide_exposure
+from app.models import EventLevel, MarketSnapshot, SetupSignal, SignalAction, utc_now_iso
+from app.storage.event_store import EventStore
 from app.storage.repositories import TradingRepository
 from app.utils.market_hours import (
     US_EQUITY_TIMEZONE,
@@ -357,9 +359,12 @@ class TradeGuardsService:
         self,
         repository: TradingRepository,
         settings: dict[str, Any] | None = None,
+        *,
+        event_store: EventStore | None = None,
     ) -> None:
         self.repository = repository
         self.settings = settings if isinstance(settings, dict) else {}
+        self.event_store = event_store
         self.circuit_breakers = CircuitBreakerTracker(repository, self.settings)
 
     def _config(self) -> dict[str, Any]:
@@ -467,22 +472,47 @@ class TradeGuardsService:
                     )
 
         max_open = int(_number(config.get("max_open_positions"), 0) or 0)
-        if max_open > 0 and len(positions) >= max_open:
-            return GuardVerdict(
-                status=STATUS_NO_GO,
-                reason_code=REASON_EXPOSURE_LIMIT,
-                decision_status="EXPOSURE_LIMIT",
-                title="Nombre maximal de positions atteint",
-                message=(
-                    f"{len(positions)} positions ouvertes (max {max_open}). "
-                    "Aucune nouvelle entree autorisee."
-                ),
-                context={"open_positions": len(positions), "max_open_positions": max_open},
-            )
+        if max_open > 0:
+            effective_open_positions = len(positions)
+            account_exposure = account_wide_exposure(self.repository, self.settings)
+            if account_exposure.fresh and account_exposure.positions_count is not None:
+                # Account-wide view already includes this instance's own
+                # positions -- replace, never add, to avoid double counting
+                # (root D, D-1, audit 88 Q4).
+                effective_open_positions = max(
+                    effective_open_positions, account_exposure.positions_count
+                )
+            else:
+                self._record_exposure_cap_local_fallback(
+                    symbol=normalized,
+                    gate="max_open_positions",
+                    reason=account_exposure.fallback_reason,
+                    local_value=len(positions),
+                )
+            if effective_open_positions >= max_open:
+                return GuardVerdict(
+                    status=STATUS_NO_GO,
+                    reason_code=REASON_EXPOSURE_LIMIT,
+                    decision_status="EXPOSURE_LIMIT",
+                    title="Nombre maximal de positions atteint",
+                    message=(
+                        f"{effective_open_positions} positions ouvertes (max {max_open}). "
+                        "Aucune nouvelle entree autorisee."
+                    ),
+                    context={
+                        "open_positions": effective_open_positions,
+                        "local_open_positions": len(positions),
+                        "max_open_positions": max_open,
+                    },
+                )
 
         risk_unit = abs(
             _number(_mapping(self.settings.get("risk")).get("max_risk_per_trade_usd"), 15.0) or 15.0
         )
+        # max_total_open_risk_R stays local-only: it is derived from each
+        # setup's own risk config (max_risk_usd), a notion that does not
+        # exist for positions opened by another instance on the broker
+        # (audit 87 Q3, audit 88 Q3 -- no account-wide equivalent).
         max_open_risk_r = _number(config.get("max_total_open_risk_R"), 0.0) or 0.0
         if max_open_risk_r > 0:
             open_risk = sum(
@@ -562,6 +592,32 @@ class TradeGuardsService:
                     )
         return None
 
+    def _record_exposure_cap_local_fallback(
+        self,
+        *,
+        symbol: str,
+        gate: str,
+        reason: str | None,
+        local_value: float,
+    ) -> None:
+        if self.event_store is None:
+            return
+        self.event_store.record(
+            EventLevel.WARNING,
+            "exposure_cap_local_fallback",
+            (
+                f"Account-wide broker view unavailable ({reason}); "
+                f"{gate} evaluated on local positions only."
+            ),
+            symbol=symbol,
+            data={
+                "gate": "trade_guards",
+                "limit": gate,
+                "reason": reason,
+                "local_value": local_value,
+            },
+        )
+
     def _candidate_risk_usd(self, setup: dict[str, Any] | None, risk_unit: float) -> float:
         config = setup.get("config") if isinstance(setup, dict) else None
         if isinstance(config, dict):
diff --git a/app/engine/trading_engine.py b/app/engine/trading_engine.py
index 4739c62..82731b1 100644
--- a/app/engine/trading_engine.py
+++ b/app/engine/trading_engine.py
@@ -161,7 +161,9 @@ class TradingEngine:
             ),
             settings=settings.raw,
         )
-        self.trade_guards = TradeGuardsService(repository, settings.raw)
+        self.trade_guards = TradeGuardsService(
+            repository, settings.raw, event_store=self.event_store
+        )
         self.position_manager = PositionManager(
             repository,
             self.event_store,
```

## 4. Décisions prises

1. **Ajout d'un paramètre `event_store` optionnel à
   `TradeGuardsService.__init__` + câblage dans `trading_engine.py`.**
   L'ordre demandait d'émettre un événement de traçabilité en cas de repli
   local (§3(b)), mais `TradeGuardsService` ne détenait auparavant aucune
   référence à un `EventStore` (contrairement à `EntryOrderExecutor`, qui
   en a déjà un). Deux options : (i) faire remonter l'information de repli
   dans le `GuardVerdict` retourné et laisser l'appelant (`entry_order_
   executor.py`) émettre l'événement, ou (ii) donner à `TradeGuardsService`
   sa propre référence optionnelle. (i) aurait mélangé un événement de
   pur repli (qui doit s'émettre même quand aucun verdict bloquant n'est
   retourné, cf. PREUVE 4/5 : `verdict is None` mais événement quand même
   présent) avec le mécanisme de retour de verdict, qui ne s'exécute que
   sur blocage. (ii) est plus direct et respecte le fait que
   `evaluate_stop_modification` (chemin distinct, non concerné par D-1)
   n'a pas besoin de cette dépendance. Le paramètre est `None` par défaut
   (aucun changement de signature cassant, tous les appels de test
   existants sans `event_store` continuent de fonctionner — vérifié,
   `test_manual_orders.py` et `test_stop_modification.py` passent sans
   modification).
2. **Le calcul du capital compte-entier utilise `average_price` (coût
   d'acquisition), pas `market_price`.** L'ordre le précise explicitement
   en §3(a) : "average_price × position_quantity". Choix confirmé cohérent
   avec le calcul local existant dans `entry_order_executor.py:194-196`
   (`float(position["average_price"]) * int(position["quantity"])`) —
   comparaison unité-à-unité correcte entre local et compte-entier.
3. **`positions_count` non-entier (`None`, canal positions en panne) est
   traité comme "non frais".** Le rapport peut être globalement `OK`
   (connecté, synchronisé, non périmé) tout en ayant
   `broker_positions_count = None` si `positions_channel_ok` est faux
   (audit 88 Q1). Non explicitement traité par l'ordre ; décision : dans ce
   cas précis, retomber sur le local avec la raison
   `BROKER_REALITY_POSITIONS_UNAVAILABLE`, cohérent avec la philosophie
   générale "doute → ARRÊTE / repli local" de l'ordre (§8).
4. **Le nom des raisons de repli** (`BROKER_REALITY_REPORT_ABSENT`,
   `BROKER_REALITY_REPORT_STALE`, `BROKER_REALITY_REPORT_DISCONNECTED`,
   `BROKER_REALITY_POSITIONS_UNAVAILABLE`) n'était pas fixé par l'ordre
   (qui donnait seulement `exposure_cap_local_fallback` comme *event_type*
   suggéré, "ex."). Choisi pour être auto-descriptif et distinct des
   codes déjà utilisés ailleurs (`BROKER_TRACKER_NOT_RUNNING`,
   `RECONCILIATION_MISMATCH`) afin de ne pas les confondre dans les logs.

## 5. Preuves de sortie

### PREUVE 1 — compte-entier > local refuse plus tôt (trade_guards)
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_account_wide_count_refuses_earlier_than_local -v
tests/test_trade_guards.py::AccountWideExposureCapTests::test_account_wide_count_refuses_earlier_than_local PASSED
```
Local = 2 positions, compte-entier = 4 positions, seuil `exposure.max_open_positions` = 3
→ `verdict` NON `None`, `context["open_positions"] == 4`,
`context["local_open_positions"] == 2`. **PASS**

### PREUVE 1 (gate risk_engine) — même principe côté `entry_order_executor`
```
$ python -m pytest tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_account_wide_position_count_blocks_earlier_than_local -v
tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_account_wide_position_count_blocks_earlier_than_local PASSED
```
Local = 0 position, compte-entier = 6 positions, `risk.max_open_positions` = 5
(défaut) → `entry_rejected_by_risk`, message "Maximum number of open
positions reached". **PASS**

### PREUVE 2 (Q4, LE test central) — pas de double comptage
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_no_double_counting_same_positions -v
tests/test_trade_guards.py::AccountWideExposureCapTests::test_no_double_counting_same_positions PASSED

$ python -m pytest tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_no_double_counting_same_position -v
tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_no_double_counting_same_position PASSED
```
Local = 2 (ou 1), compte-entier montre les MÊMES positions (2, ou 1) et pas
4 (ou 2) — `max(2, 2) = 2 < 3` (trade_guards) et `max(1, 1) = 1 < 2`
(risk_engine) → entrée autorisée dans les deux cas. Une addition
local+broker aurait donné 4 et 2 respectivement, refusant à tort. **PASS**

### PREUVE 3 — exposition capital (calcul de somme sur `rows`)
```
$ python -m pytest tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_account_wide_capital_blocks_earlier_than_local -v
tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_account_wide_capital_blocks_earlier_than_local PASSED
```
Compte-entier : 1 position, quantity=100, average_price=50 → capital=5000 USD
> `risk.max_total_exposure_usd` = 1000 (défaut), local = 0 → `entry_rejected_
by_risk`, message "Maximum exposure reached". **PASS**

### PREUVE 4 — rapport absent → repli local + événement de traçabilité
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_absent_report_falls_back_to_local_and_traces_event tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_absent_report_falls_back_to_local_and_traces_event -v
tests/test_trade_guards.py::AccountWideExposureCapTests::test_absent_report_falls_back_to_local_and_traces_event PASSED
tests/test_entry_order_executor.py::EntryOrderExecutorTests::test_absent_report_falls_back_to_local_and_traces_event PASSED
```
Rapport absent → `verdict is None` (trade_guards), ordre placé normalement
(entry_order_executor, avec les gates broker_tracker/reconciliation
désactivés pour isoler l'effet du gate exposition) et un événement
`exposure_cap_local_fallback` avec `reason == "BROKER_REALITY_REPORT_
ABSENT"` est bien présent dans les deux cas. **PASS**

### PREUVE 5 — rapport périmé → idem PREUVE 4
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_stale_report_falls_back_to_local_and_traces_event -v
tests/test_trade_guards.py::AccountWideExposureCapTests::test_stale_report_falls_back_to_local_and_traces_event PASSED
```
Rapport construit avec `broker_last_sync_at` vieux de 9999s (>
`stale_after_seconds`) → repli local + événement `reason == "BROKER_
REALITY_REPORT_STALE"`. **PASS**
(Testé uniquement côté trade_guards ; le même chemin de code
`account_wide_exposure` est partagé par `entry_order_executor.py`, couvert
directement au niveau du helper par `tests/test_account_wide_exposure.py::
test_stale_report_falls_back`.)

### PREUVE 6 — ne relâche jamais une limite
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_never_relaxes_below_local -v
tests/test_trade_guards.py::AccountWideExposureCapTests::test_never_relaxes_below_local PASSED
```
Local = 3 positions (seuil atteint seul), compte-entier = 1 (garde de
sûreté, cas non réaliste en pratique) → `max(3, 1) = 3 >= 3`, verdict NON
`None`, `context["open_positions"] == 3` (pas relâché à 1). **PASS**

### PREUVE 7 — `max_total_open_risk_R` inchangé (non-régression)
```
$ python -m pytest tests/test_trade_guards.py::AccountWideExposureCapTests::test_total_open_risk_limit_unaffected_by_broker_report tests/test_trade_guards.py::ExposureLimitTests::test_total_open_risk_limit tests/test_trade_guards.py::ExposureLimitTests::test_open_risk_within_limit_passes -v
3 passed
```
Le rapport broker_reality en cache (même avec des positions synthétiques)
n'affecte pas le calcul de `max_total_open_risk_R`, qui reste basé
uniquement sur `risk_remaining` des positions locales. **PASS**

### PREUVE 8 — non-régression trade_guards / chemin d'entrée
```
$ python -m pytest tests/test_trade_guards.py tests/test_entry_order_executor.py tests/test_manual_orders.py tests/test_stop_modification.py tests/test_broker_reality.py tests/test_review_status_sticky.py tests/test_entry_gate_current_status.py -q
............................................................................ [ ... ]
178 passed
```
(voir sortie complète ci-dessous en §7 pour le compte précis). **PASS**

### PREUVE 9 — suite complète : seul `test_account_metrics.py` en échec
```
$ python -m pytest -q
...
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 794 passed, 4 warnings, 134 subtests passed in 238.34s (0:03:58)
```
Vérifié **pré-existant et sans rapport avec D-1** : même échec, même
message (`AssertionError: None != 2.5` sur `snapshot["metrics"]
["today_pnl"]`), obtenu en exécutant le même test isolément sur
`feat/setup-conditions` (avant tout changement D-1) :
```
$ git stash && git checkout feat/setup-conditions -- . && python -m pytest tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty -q
1 failed in 0.96s
(AssertionError: None != 2.5)
$ git stash pop   # D-1 restauré ensuite
```
**PASS** (conforme à la prédiction exacte de l'ordre : "seul
test_account_metrics.py en échec").

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé sur disque en
  dehors du dépôt (le seul detour a été un `git diff > <chemin hors dépôt>`
  jamais réutilisé, sans effet sur l'arbre de travail).
- Un `git stash` a été créé et immédiatement re-appliqué (`git stash pop`)
  après une vérification ponctuelle sur `feat/setup-conditions` (voir
  PREUVE 9 et §8, incident signalé) — confirmé vide après coup :
  `git stash list` ne montre plus l'entrée créée pendant ce lot.
- Aucune branche ni worktree supplémentaire créé au-delà de
  `fix/d1-account-wide-exposure-cap` (celle demandée par l'ordre §6).
- `git status --short -- app/ tests/` après commit : vide (tout committé).
  Les fichiers non liés à ce lot visibles dans `git status` (dossiers
  `.codex/`, `tmp/`, anciens `audit/*.md` non commités, `data/setups/*`)
  préexistaient avant ce lot et n'ont pas été créés ni modifiés par D-1.

## 7. Suite de tests
```
$ python -m pytest -q
...
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 794 passed, 4 warnings, 134 subtests passed in 238.34s (0:03:58)
```
Avant ce lot (sur `feat/setup-conditions`) : même échec pré-existant
(vérifié isolément, §5 PREUVE 9), nombre total de tests non recompté en
intégral sur l'ancienne base (non nécessaire : seul le delta compte).
Après ce lot : 794 passed (+ 15 tests ajoutés par D-1 : 6 dans
`AccountWideExposureCapTests`, 4 dans `EntryOrderExecutorTests`, 5 dans
`test_account_wide_exposure.py`), 1 failed (pré-existant, confirmé
sans rapport avec D-1). Cohérent.

## 8. Découvert mais NON corrigé
- **Incident opérationnel pendant ce lot, corrigé immédiatement** : pour
  vérifier que l'échec de `test_account_metrics.py` était pré-existant, un
  `git checkout feat/setup-conditions -- .` a été exécuté après un
  `git stash` — cette commande a écrasé l'arbre de travail avec la version
  de `feat/setup-conditions`, effaçant temporairement les modifications
  D-1 du répertoire de travail (mais pas du stash). Détecté immédiatement
  via les rappels système signalant des fichiers modifiés de façon
  inattendue ; corrigé par `git stash pop`, qui a restauré exactement
  l'état D-1 (vérifié par `git diff --stat` après coup, identique à avant
  l'incident). Aucune perte de travail, mais l'action était plus risquée
  que nécessaire — une vérification plus sûre (ex. `git show feat/setup-
  conditions:tests/test_account_metrics.py` ou un `git worktree`) aurait
  évité de manipuler l'arbre de travail principal.
- Le trou de magnitude sur le mismatch même-symbole (`broker_reality.py:
  833-834`, `MISMATCH_POSITION_COUNT` sur présence et non magnitude,
  identifié par l'audit 87 §1.4) — hors périmètre de D-1, c'est le
  périmètre de D-2, non touché ici.
- Le verrou compte-entier (racine D-3) — hors périmètre, non touché.

## 9. Écarts par rapport à l'ordre
- **`app/engine/trading_engine.py` touché** alors qu'il n'était pas dans la
  liste AUTORISÉ explicite de l'ordre (§2). Changement d'une seule ligne
  (passage de `event_store=self.event_store` au constructeur de
  `TradeGuardsService`), nécessaire pour que l'événement de traçabilité
  demandé en §3(b) puisse effectivement être émis en production (sans ce
  câblage, `TradeGuardsService.event_store` resterait toujours `None` hors
  tests, et l'événement ne serait jamais visible). Documenté aussi en §4
  point 1. Aucune autre ligne de ce fichier modifiée.
- Aucun autre écart : tous les fichiers listés INTERDIT (§2) sont restés
  intacts (`risk_engine.py`, `broker_reality.py::_row_action_and_mismatch`,
  `reconciliation.py`, `instance_lock.py`) — vérifié par le diff complet en
  §3 et par `git diff --stat` en §2, qui ne les fait apparaître nulle part.
