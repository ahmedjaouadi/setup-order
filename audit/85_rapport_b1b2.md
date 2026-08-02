# Rapport de lot — B-1b-2

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_B1b2.md` — dégradation explicite
  quand le stop n'atteint pas le broker (root B, B-1b-2).
- Branche : `fix/b1b2-degrade-explicit-no-broker` | Commit :
  `b94e247af0d64b8d9414cfd56e9db7141e81e311`.
- Basée sur : `fix/b1b1-route-raisestop-to-broker`, `5921728` (docs(audit):
  rapport de lot B-1b-1 — routage RAISE_STOP vers le broker vérifié).
- Mergée : non | Poussée : non.

```
$ git log -1 --format="%H %s"
b94e247af0d64b8d9414cfd56e9db7141e81e311 fix(stop-modification): alert explicitly when stop modification does not reach broker (root B, B-1b-2)
```

## 2. Fichiers touchés
```
$ git diff --stat 5921728..b94e247
 app/engine/stop_modification_service.py | 15 ++++++
 audit/ORDRE_B1b2.md                     | 93 +++++++++++++++++++++++++++++++++
 tests/test_stop_modification.py         | 42 +++++++++++++++
 3 files changed, 150 insertions(+)
```
Confrontation au périmètre autorisé (§2 de l'ordre — `stop_modification_service.py`
+ tests) : **conforme**. `audit/ORDRE_B1b2.md` est le fichier d'ordre lui-même
(exigé par la première ligne de l'ordre), pas du code de production. Aucun
fichier interdit touché : `position_action_executor.py`, `routes_positions.py`,
`tws_connector.py` — tous absents du diff-stat ci-dessus, donc inchangés.

## 3. Diff du code de production
Diff intégral du seul fichier de `app/` touché :

```diff
diff --git a/app/engine/stop_modification_service.py b/app/engine/stop_modification_service.py
index 80f8083..df9b333 100644
--- a/app/engine/stop_modification_service.py
+++ b/app/engine/stop_modification_service.py
@@ -113,6 +113,21 @@ class StopModificationService:
                 )
             broker_updated = True
 
+        if stop_order is not None and not broker_updated:
+            cause = "no_broker_order_id" if not broker_order_id else "broker_disconnected"
+            self.event_store.record(
+                EventLevel.WARNING,
+                "stop_modification_local_only",
+                "Stop modification was not confirmed by the broker; local stop may diverge from the real stop",
+                symbol=normalized,
+                data={
+                    "symbol": normalized,
+                    "new_stop": new_stop,
+                    "cause": cause,
+                    "broker_updated": False,
+                },
+            )
+
         if stop_order is not None:
             self.repository.update_order_stop_price(str(stop_order["id"]), new_stop)
         if position is not None:
```

Point d'insertion exactement celui prescrit par l'ordre §3 : après le bloc
broker (`broker_updated` a sa valeur finale à cette ligne), avant les mises à
jour locales et le retour. La garde `never_lower_stop` (:91-101) et
`_resolve_stop_guard_reference` (B-2) sont en amont de ce point et n'ont reçu
aucune modification — vérifiable par le diff ci-dessus, qui ne touche que la
zone :113-129 (post-bloc broker).

## 4. Décisions prises
- **Niveau `WARNING`, pas `CRITICAL`** : l'ordre §3 laissait le choix "sauf
  si tu justifies CRITICAL". Choix `WARNING`, par cohérence avec
  `stop_guard_degraded_mode` (même fichier, même famille de dégradation
  broker-non-atteint, déjà en `WARNING`) — un stop qui a bougé en local sans
  confirmation broker est un cas à surveiller et corriger, pas une panne
  active du système ; `CRITICAL` est réservé ailleurs dans ce dépôt à des
  ruptures de sécurité plus dures (jamais utilisé pour un état "à réconcilier").
- **Nom de la cause `no_broker_order_id` / `broker_disconnected`** : repris
  mot pour mot du vocabulaire suggéré par l'ordre §3. Distinction calculée
  sur `broker_order_id` (déjà résolu à la ligne :104, `(stop_order or {}).get("broker_order_id")`) :
  falsy → `no_broker_order_id` (cas orderId=0/None de B-1a) ; sinon (un
  `broker_order_id` existe mais le bloc broker a quand même été sauté, donc
  `await self._broker_is_connected()` a renvoyé faux) → `broker_disconnected`.
  Aucun nouvel appel réseau ajouté pour distinguer les deux causes.
- **`data` inclut `symbol` en plus du paramètre `symbol=` de `event_store.record`** :
  redondant avec le champ `symbol` de la ligne d'événement elle-même, mais
  l'ordre §3 liste explicitement `symbol` comme clé de `data` attendue —
  respecté à la lettre plutôt que déduit comme redondant.
- **Condition `not broker_updated` plutôt que réévaluer `broker_order_id and connected`** :
  utilise directement la variable `broker_updated` déjà calculée par le bloc
  broker (source unique de vérité, aucune divergence possible avec la
  logique de :105), au lieu de dupliquer la condition d'entrée du bloc.

## 5. Preuves de sortie

### 5.1 — `broker_order_id=None` + `stop_order` présent → cause `no_broker_order_id`
Test : `test_stop_order_without_broker_order_id_emits_local_only_alert`
(nouveau). Simule le cas orderId=0 de B-1a : ordre local avec
`broker_order_id=None`. `modify_stop` renvoie `ok=True`, `broker_updated=False`,
et un événement `stop_modification_local_only` avec `cause="no_broker_order_id"`.
```
$ python -m pytest tests/test_stop_modification.py::StopModificationServiceTests::test_stop_order_without_broker_order_id_emits_local_only_alert -v
tests/test_stop_modification.py::StopModificationServiceTests::test_stop_order_without_broker_order_id_emits_local_only_alert PASSED
```
**PASS.**

### 5.2 — Broker déconnecté + `stop_order` présent → cause `broker_disconnected`
Test : `test_disconnected_broker_falls_back_to_local_update` (existant,
assertions étendues). Broker connecté puis `disconnect()` avant l'appel ;
un événement `stop_modification_local_only` avec `cause="broker_disconnected"`
est émis.
```
$ python -m pytest tests/test_stop_modification.py::StopModificationServiceTests::test_disconnected_broker_falls_back_to_local_update -v
tests/test_stop_modification.py::StopModificationServiceTests::test_disconnected_broker_falls_back_to_local_update PASSED
```
**PASS.**

### 5.3 — Broker atteint (`broker_updated=True`) → pas d'événement de dégradation
Test : `test_raise_stop_updates_broker_order_local_order_and_position`
(existant, assertion étendue : `list_events(event_type="stop_modification_local_only") == []`).
```
$ python -m pytest tests/test_stop_modification.py::StopModificationServiceTests::test_raise_stop_updates_broker_order_local_order_and_position -v
tests/test_stop_modification.py::StopModificationServiceTests::test_raise_stop_updates_broker_order_local_order_and_position PASSED
```
**PASS.**

### 5.4 — Pas de `stop_order` (cible absente) → pas d'événement de dégradation
Test : `test_position_without_stop_order_updates_locally` (existant,
assertion étendue). Position seule, aucun ordre stop actif : `ok=True`,
`broker_updated=False`, mais `stop_modification_local_only` reste vide —
distinct du cas dégradé (§3 dernier point de l'ordre).
```
$ python -m pytest tests/test_stop_modification.py::StopModificationServiceTests::test_position_without_stop_order_updates_locally -v
tests/test_stop_modification.py::StopModificationServiceTests::test_position_without_stop_order_updates_locally PASSED
```
**PASS.**

### 5.5 — Non-régression garde `never_lower_stop` (B-2)
Tests existants, inchangés, tous verts : `test_lowering_stop_is_rejected_before_touching_broker`,
`test_broker_truth_overrides_stale_local_reference`,
`test_degraded_mode_refuses_fall_when_broker_open_orders_fails`,
`test_degraded_mode_uses_max_of_local_sources_not_first`. Ces chemins
retournent (`self._rejected(...)`) avant le point d'insertion de ce lot
(:91-101, en amont de :116) ; le diff §3 ne touche aucune ligne de ce bloc.
```
$ python -m pytest tests/test_stop_modification.py -k "lowering_stop or broker_truth_overrides or degraded_mode_refuses or degraded_mode_uses_max" -v
tests/test_stop_modification.py::StopModificationServiceTests::test_broker_truth_overrides_stale_local_reference PASSED
tests/test_stop_modification.py::StopModificationServiceTests::test_degraded_mode_refuses_fall_when_broker_open_orders_fails PASSED
tests/test_stop_modification.py::StopModificationServiceTests::test_degraded_mode_uses_max_of_local_sources_not_first PASSED
tests/test_stop_modification.py::StopModificationServiceTests::test_lowering_stop_is_rejected_before_touching_broker PASSED
4 passed
```
**PASS.**

### 5.6 — Bout-en-bout avec B-1b-1
`PositionActionExecutor.execute_raise_stop_signal` (B-1b-1) appelle
`self.stop_modification_service.modify_stop(setup["symbol"], signal.new_stop)`
directement (`app/engine/position_action_executor.py:40`, non modifié par ce
lot). Un RAISE_STOP sur un stop `broker_order_id=None` traverse donc
exactement le chemin prouvé en 5.1 : `modify_stop` renvoie `ok=True` (contrat
B-1b-1 inchangé, la transition vers `MANAGING_POSITION` a lieu comme avant),
ET l'événement `stop_modification_local_only` est désormais émis — le
mensonge silencieux documenté en §8 du rapport B-1b-1 est maintenant
visible. Aucun nouveau test bout-en-bout ajouté : la composition est directe
(pas de logique intermédiaire dans `position_action_executor.py` à
re-tester), la preuve tient par lecture du point d'appel + 5.1.
**PASS** (par composition, périmètre `position_action_executor.py` interdit
à ce lot).

### 5.7 — Suite complète
```
$ python -m pytest -q
...
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 779 passed, 4 warnings, 134 subtests passed in 260.60s (0:04:20)
```
Conforme à l'attendu de l'ordre §5.7 : "seul test_account_metrics.py en
échec". Ce même échec était déjà présent et confirmé pré-existant/hors
périmètre dans le rapport B-1b-1 §5.7 (rejoué sur `eff5a0a` avant B-1b-1,
identique). Non rejoué isolément ici pour éviter la duplication d'une preuve
déjà établie deux lots plus tôt sur ce même test, inchangé par ce lot.
**PASS.**

## 6. Nettoyage (obligatoire)
- Fichiers temporaires / leurres / scripts jetables créés pendant le lot :
  aucun. Toutes les investigations (lecture de `stop_modification_service.py`,
  `position_action_executor.py`, `test_stop_modification.py`) se sont faites
  par lecture de fichiers existants.
- Stash créés/poppés : aucun.
- Branches ou worktrees créés/supprimés : branche
  `fix/b1b2-degrade-explicit-no-broker` créée depuis
  `fix/b1b1-route-raisestop-to-broker`, conservée (pas de suppression, règle
  générale de ne jamais supprimer sans accord explicite).
- Confirmation qu'aucun artefact du lot ne subsiste hors commit :
  ```
  $ git status --short -- app/ tests/ audit/ORDRE_B1b2.md audit/85_rapport_b1b2.md
  (vide après le commit b94e247 et l'écriture de ce rapport)
  ```
  Les fichiers non liés à ce lot déjà présents avant son démarrage
  (`data/setups/*`, `audit/*.md` d'autres sessions, `.codex/`, `tmp/`) ne
  sont ni touchés ni commités par ce lot.

## 7. Suite de tests
```
$ python -m pytest -q
...
1 failed, 779 passed, 4 warnings, 134 subtests passed in 260.60s (0:04:20)
```
Avant ce lot (sur `5921728`, base de la branche), `test_stop_modification.py`
comptait 20 tests. Après ce lot : 21 (+1 net — `test_stop_order_without_broker_order_id_emits_local_only_alert`
nouveau ; les 2 autres cas de preuve, 5.2 et 5.3/5.4, réutilisent des tests
existants avec assertions étendues plutôt que des méthodes séparées).
778 passés sur le rapport B-1b-1 (§7) → 779 ici, cohérent avec +1 test net
et le même échec pré-existant hors périmètre.

## 8. Découvert mais NON corrigé
- **Fallback `permId` (`tws_connector.py`)** : annoncé par l'ordre §2 comme
  correctif compagnon futur, non touché ici. Le cas `no_broker_order_id`
  reste donc permanent pour un stop entré manuellement dans TWS tant que ce
  correctif séparé n'est pas livré — ce lot le rend visible, ne le résout
  pas.
- **La transition B-1b-1 a lieu même quand `broker_updated=False`** :
  l'ordre §3 tranche explicitement que ce lot ne touche pas ce contrat
  (« Ce lot NE modifie PAS ce contrat »). Noté ici comme demandé par l'ordre :
  un RAISE_STOP consommé sur un stop `orderId=None` fait toujours transiter
  le setup vers `MANAGING_POSITION` alors que le broker n'a rien confirmé —
  l'événement `stop_modification_local_only` rend le cas traçable après
  coup, mais ne bloque ni ne retarde la transition. Si une divergence
  broker/local doit un jour empêcher la transition, c'est une décision
  produit à trancher dans un lot dédié (hors root B tel que cadré).
- **Pas de mécanisme de réconciliation automatique** : l'événement
  `stop_modification_local_only` est un signal, pas une action corrective —
  aucun rattrapage automatique (retry, alerte utilisateur active, blocage de
  nouvelles actions sur le symbole) n'existe pour ce cas. Cohérent avec le
  périmètre de l'ordre (traçabilité seule), signalé pour vigilance.

## 9. Écarts par rapport à l'ordre
Aucun.
