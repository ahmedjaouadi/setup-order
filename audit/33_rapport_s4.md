# Rapport de lot — S4 (verrouiller la protection du chemin manuel)

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_S4.md`
- Branche : `fix/s4-manual-path-guard` | Commit : `bf2be9000c4fade8870754dd261dd05b858dd2fc`
- Basée sur : `feat/setup-conditions` @ `2d84f9e6c94b970361529bf05e3e0ae40bee77ba`
- Mergée : non | Poussée : non

## 2. Fichiers touchés

```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/manual_order_service.py           | 10 +++++
 tests/test_manual_order_guard_config_lock.py | 55 ++++++++++++++++++++++++++++
 tests/test_manual_orders.py                  | 38 +++++++++++++++++++
 3 files changed, 103 insertions(+)
```

Confrontation à la liste autorisée par l'ordre (§2) :
- `tests/test_manual_orders.py` — autorisé (test bout-en-bout). **Conforme.**
- `tests/test_manual_order_guard_config_lock.py` (nouveau fichier) — autorisé
  (« un fichier de test pour le cliquet de configuration »). **Conforme.**
- `app/engine/manual_order_service.py` — autorisé, commentaire uniquement.
  **Conforme** (voir §3, diff intégral, uniquement des lignes `#`).

Aucun autre fichier de `app/` touché. `trade_guards.py`, `settings.py`,
`config.yaml` : diff nul (vérifié explicitement, §5.4). **Aucun écart.**

## 3. Diff du code de production

Diff intégral (seul fichier `app/` modifié) :

```diff
diff --git a/app/engine/manual_order_service.py b/app/engine/manual_order_service.py
index 88385e9..f7b8c6f 100644
--- a/app/engine/manual_order_service.py
+++ b/app/engine/manual_order_service.py
@@ -200,6 +200,16 @@ class ManualOrderService:
             }
             return
 
+        # This call is the ONLY protection against stacking a manual BUY on a
+        # symbol already held: a fresh setup_id is minted per call (see
+        # new_id("man") above), so protection_snapshot_for_setup/
+        # DuplicateOrderError further down never sees a prior manual order on
+        # this symbol. The actual guard is trade_guards._exposure_verdict's
+        # block_if_position_on_same_symbol rule, keyed by symbol, not
+        # setup_id -- and it depends entirely on the
+        # trade_guards.exposure.block_if_position_on_same_symbol config
+        # switch staying True (audit 32/S4; locked by
+        # tests/test_manual_order_guard_config_lock.py).
         guard_verdict = self.trade_guards.evaluate_entry(symbol, now=now)
         if guard_verdict is not None:
             assessment["block"] = {
```

10 lignes ajoutées, toutes des commentaires `#`. Zéro ligne de code
exécutable ajoutée, modifiée ou supprimée. Zéro ligne retirée.

## 4. Décisions prises

- **Nom du fichier de test-cliquet** : non spécifié par l'ordre. Choisi
  `tests/test_manual_order_guard_config_lock.py`, cohérent avec les
  conventions de nommage du dépôt (`test_<sujet>.py`).
- **Emplacement du test bout-en-bout** : ajouté dans la classe
  `ManualBuyOrderTests` existante de `tests/test_manual_orders.py` (plutôt
  qu'une nouvelle classe), car c'est la classe qui regroupe déjà tous les
  scénarios de blocage du BUY manuel (`test_halted_symbol_is_refused_and_traced`,
  `test_outside_trading_window_is_refused`, `test_risk_above_limit_is_refused`)
  — le nouveau test suit le même patron.
- **Seeding de la position** : fait par appel direct à
  `repository.upsert_position(PositionRecord(...))` dans le corps du test
  (calqué sur `_seed_position` de `ManualSellOrderTests`, qui n'est pas
  partagé avec `ManualBuyOrderTests`), plutôt que d'extraire un helper
  commun aux deux classes — modification minimale, hors périmètre d'un
  refactor.
- **Interprétation de « réutiliser le harness de `test_guard_block_returns_422` »** :
  comprise comme réutiliser le patron d'assertion (bloqué → `reason_code`
  précis → aucun ordre transmis → trace) et le fixture `ManualOrderServiceTestCase`
  commun, pas comme dupliquer littéralement le test HALT.

## 5. Preuves de sortie

### 5.1 Le test bout-en-bout passe

```
$ python -m pytest tests/test_manual_orders.py::ManualBuyOrderTests::test_buy_blocked_when_position_already_open_on_symbol -v
tests/test_manual_orders.py::ManualBuyOrderTests::test_buy_blocked_when_position_already_open_on_symbol PASSED [100%]
1 passed in 1.00s
```
**PASS**

### 5.2 Preuve négative du test bout-en-bout

Assertion la plus spécifique mutée (`REASON_CONFLICT_WITH_OPEN_POSITION` →
`"MUTATED_WRONG_REASON_CODE"`), reste inchangée :

```
$ python -m pytest tests/test_manual_orders.py::ManualBuyOrderTests::test_buy_blocked_when_position_already_open_on_symbol -v
...
>       self.assertEqual(
            result["block"]["reason_code"],
            "MUTATED_WRONG_REASON_CODE",
        )
E       AssertionError: 'CONFLICT_WITH_OPEN_POSITION' != 'MUTATED_WRONG_REASON_CODE'
E       - CONFLICT_WITH_OPEN_POSITION
E       + MUTATED_WRONG_REASON_CODE
FAILED tests/test_manual_orders.py::ManualBuyOrderTests::test_buy_blocked_when_position_already_open_on_symbol
1 failed in 1.00s
```

Revert de la mutation, diff nul par rapport à l'état commité :

```
$ git diff --stat -- tests/test_manual_orders.py
 tests/test_manual_orders.py | 38 ++++++++++++++++++++++++++++++++++++++
 1 file changed, 38 insertions(+)
```
(38 insertions — identique à l'état post-ajout d'avant mutation ; la
mutation puis son retrait ne laissent aucune trace résiduelle. Re-run
post-revert : `1 passed in 0.91s`.)
**PASS**

### 5.3 Preuve négative du cliquet de configuration

Script jetable (`s4_ratchet_negative_proof.py`, écrit dans le répertoire
scratchpad de session, jamais dans le dépôt) monkeypatchant
`app.settings.DEFAULT_CONFIG` **en mémoire uniquement** pour forcer
`trade_guards.exposure.block_if_position_on_same_symbol = False`, puis
exécutant le test-cliquet :

```
$ PYTHONPATH=. python .../s4_ratchet_negative_proof.py
test_symbol_level_exposure_guard_is_enabled_in_loaded_config ... FAIL
AssertionError: False is not True : trade_guards.exposure.block_if_position_on_same_symbol
must be True: this is the only rule that blocks a manual BUY on a symbol
already in an open position (audit 32).
Ran 1 test in 0.064s
FAILED (failures=1)
```

`config.yaml` et `app/settings.py` **jamais écrits** — vérifié :

```
$ git diff --stat -- config.yaml app/settings.py
(vide)
```
**PASS**

### 5.4 `git diff` sur `app/` — commentaire seul

```
$ git diff feat/setup-conditions..HEAD -- app/
[diff intégral reproduit en §3 — 10 lignes, toutes des commentaires]
```
**PASS**

### 5.5 Suite complète

```
$ python -m pytest -q
1 failed, 709 passed, 4 warnings, 134 subtests passed in 253.11s (0:04:13)
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
```
Seul échec : `test_account_metrics.py`, préexistant, déjà rapporté à
l'identique par l'audit 26 avant tout travail S2/S3/S4, sans rapport avec
le chemin manuel ou les gates. **PASS.**

## 6. Nettoyage (obligatoire)

- Fichier jetable créé : `s4_ratchet_negative_proof.py` (scratchpad de
  session, jamais dans le dépôt). Supprimé après usage :
  ```
  $ rm ".../scratchpad/s4_ratchet_negative_proof.py"
  $ ls ".../scratchpad"
  (vide)
  ```
- Aucun stash créé.
- Aucune branche ni worktree créé au-delà de `fix/s4-manual-path-guard`
  (créée intentionnellement, conservée — c'est la branche du lot).
- `git status --short` après commit : ne montre, hors bruit préexistant
  (`data/setups/*.json` runtime, `.codex/`, `tmp/`, `audit/28_pre_s2.md`,
  `audit/31_cloture_s3.md` — non liés à ce lot), que `audit/ORDRE_S4.md`
  (voir §9) et `audit/33_rapport_s4.md` (ce document), tous deux destinés
  à être committés avec le rapport. Aucun artefact du lot ne subsiste par
  ailleurs.

## 7. Suite de tests

Avant le lot (calculé par soustraction des tests ajoutés, non mesuré par
un run séparé sur `feat/setup-conditions` — voir §9) : 707 passed (709 - 2).
Après le lot : **709 passed**, 1 échec préexistant inchangé (`test_account_metrics.py`),
134 subtests passed (inchangé, aucun subtest ajouté par ce lot).

2 tests ajoutés :
1. `ManualBuyOrderTests::test_buy_blocked_when_position_already_open_on_symbol`
   (`tests/test_manual_orders.py`)
2. `ManualBuyPositionGuardConfigLockTests::test_symbol_level_exposure_guard_is_enabled_in_loaded_config`
   (`tests/test_manual_order_guard_config_lock.py`, nouveau fichier)

707 + 2 = 709. Cohérent.

## 8. Découvert mais NON corrigé

Rien de nouveau au-delà de ce que l'audit 32 avait déjà documenté. Point
notable confirmé en écrivant le test bout-en-bout : le garde d'exposition
(`_exposure_verdict`) est un mécanisme de configuration pur — aucun code de
`manual_order_service.py` ne réaffirme indépendamment la vérification par
symbole (cf. commentaire ajouté §3). C'est exactement l'écart 3 déjà
qualifié par l'audit 32 (« la protection dépend d'un booléen de
configuration non verrouillé ») ; ce lot le **verrouille par un test**
(§5.3) mais ne le **corrige pas par du code redondant** — hors périmètre
explicite de cet ordre (§2, INTERDIT : « toute logique dans app/ »).
Signalé, non corrigé, conformément à la règle.

## 9. Écarts par rapport à l'ordre

- **§6 COMMIT** demande un commit unique avec le message
  `"test: lock symbol-level guard protecting the manual buy path"`. Ce
  commit (`bf2be90`) a été fait avant la rédaction de ce rapport, comme
  demandé, et ne contient que les 3 fichiers autorisés (§2). En revanche,
  par précédent direct du lot S3 (`audit/ORDRE_S3.md` committé dans le
  même commit que le code, `a4beb4c`), le fichier `audit/ORDRE_S4.md`
  aurait pu être inclus dans ce même commit `bf2be90` ; il ne l'a pas été
  — l'ordre S4 ne le demandait pas explicitement dans son §6, contrairement
  à ce qui avait été fait spontanément pour S3. `audit/ORDRE_S4.md` est
  committé séparément, avec ce rapport, dans un commit `docs(audit)`
  distinct — cohérent avec le traitement de `audit/ORDRE_S4_preaudit.md`
  lors du pré-audit 32 de cette même session. Écart de forme mineur, signalé
  sans correction rétroactive (pas de ré-commit/amend).
- **§7 Suite de tests, « avant »** : non mesuré par un run pytest séparé
  sur l'état pré-lot (`feat/setup-conditions`) — calculé par soustraction
  arithmétique des 2 tests ajoutés (707 = 709 - 2), confirmée cohérente
  mais pas une mesure indépendante. Signalé pour transparence plutôt que
  présenté comme une preuve mesurée au même titre que les autres points de
  §5.
- Aucun autre écart : périmètre respecté (aucune ligne de code dans `app/`,
  `trade_guards.py`/`settings.py`/`config.yaml` non touchés), toutes les
  preuves demandées en §5 produites, branche et message de commit conformes
  au §6.
