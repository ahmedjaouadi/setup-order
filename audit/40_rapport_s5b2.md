# Rapport de lot — S5b-2 (cliquet + preuve comportementale du collant des alarmes)

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_S5b2.md` (S5b-2)
- Branche : `fix/s5b2-review-sticky-ratchet` | Commit : `c945277530007ef7f72ea8aac385da8c4275cd09`
  (second commit ; premier commit `21d6bb70a9a3b4beab6fe9b0b1467e38d316b7f2`,
  voir §4)
- Basée sur : `feat/setup-conditions` @ `a38d00780696ea0f1623e663330d61b3678b75b0`
  (`git merge-base feat/setup-conditions HEAD` confirme ce même hash)
- Mergée : non | Poussée : non

## 2. Fichiers touchés

```
$ git diff --stat feat/setup-conditions..HEAD
 audit/ORDRE_S5b2.md                     |  92 ++++
 tests/test_active_status_write_sites.py | 142 ++++++
 tests/test_review_status_sticky.py      | 734 ++++++++++++++++++++++++++++++++
 3 files changed, 968 insertions(+)
```

Confrontation au périmètre (§2 de l'ordre — autorisé : deux nouveaux
fichiers de test uniquement ; interdit : tout fichier de `app/`,
modification de `tests/test_in_position_write_sites.py`) : **conforme**.
Zéro fichier de `app/` touché (confirmé aussi en §5.4 ci-dessous par
`git diff feat/setup-conditions..HEAD -- app/`, vide).
`tests/test_in_position_write_sites.py` n'apparaît nulle part dans le
diff. `audit/ORDRE_S5b2.md` est l'ordre lui-même, recopié mot pour mot
comme demandé au §0, conformément à la pratique déjà établie pour S5b-1
(audit 37 §2).

## 3. Diff du code de production

Aucun. Ce lot ne touche pas `app/` (§2 de l'ordre : "AUTORISÉ : deux
NOUVEAUX fichiers de test uniquement").

## 4. Décisions prises

1. **Deux commits au lieu d'un.** Le premier commit (`21d6bb7`) couvrait
   les deux fichiers de test tels que demandés, mais laissait
   `reconciliation.py:507` (ENTRY_FILLED, branche FILLED) documenté
   seulement en prose dans `ALLOWED_ACTIVE_WRITE_SITES`, sans test direct
   prouvant que la garde qui le protège (`setup_status in
   {ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED}`, reconciliation.py:
   488-491) tient réellement pour les deux statuts d'alarme — alors que
   §3(b) de l'ordre demande explicitement un test comportemental "pour
   CHAQUE site listé dans ALLOWED_ACTIVE_WRITE_SITES". Plutôt que
   d'amender le premier commit (proscrit par les règles générales de ce
   projet sauf demande explicite), un second commit
   (`ReconciliationFilledBranchUnreachableFromAlarmTests`, 2 tests) ferme
   cet écart. Décision prise pour respecter §3(b) à la lettre, pas
   demandée explicitement par l'ordre sous cette forme précise (l'ordre
   ne prescrit pas le nombre de commits).
2. **Ratchet textuel (a) : dict au niveau fichier, pas ligne.** Même
   granularité que S2 (`ALLOWED_WRITE_SITES: dict[str, str]`, clé =
   fichier). Un fichier peut apparaître avec plusieurs sites internes
   (ex. `order_manager.py` en a trois) ; la justification en prose liste
   chacun avec son numéro de ligne. Choix non explicitement tranché par
   l'ordre, mais imposé par "sur le modèle EXACT de
   test_in_position_write_sites.py" (§3(a)).
3. **Fixtures comportementales : composant direct plutôt qu'intégration
   complète quand l'intégration masque le site testé.** Tentative initiale
   de tester `post_fill_progression.py:64` et `order_manager.py:374` via
   `OrderManager.simulate_fill_order()` de bout en bout (comme pour
   `attach_missing_stop` et pour le cas de cascade complète) : les deux
   premiers tests échouaient parce que, dans le même appel, une seconde
   écriture (nouvel échec du stop → `ERROR_REQUIRES_MANUAL_REVIEW` via
   `place_stop_order`, ou succès → `IN_POSITION` via `mark_in_position`)
   masque systématiquement l'état intermédiaire. Documenté en détail dans
   les docstrings de `PostFillProgressionDirectWriteReviewStickyTests` et
   `PlaceStopOrderDirectWriteReviewStickyTests` (tests/
   test_review_status_sticky.py). Décision : tester ces deux sites
   directement sur le composant réel (`PostFillProgression`,
   `OrderManager.place_stop_order`), à l'identique du style déjà utilisé
   par `tests/test_post_fill_progression.py` et par
   `SubmittedBranchReviewLockTests`, et garder UN test de bout en bout
   séparé (`SimulatedFillFullCascadeReviewStickyTests`) pour montrer le
   résultat final réel de la chaîne complète (atterrit sur `IN_POSITION`).
   Ceci reste conforme à "instancie le vrai
   Database/TradingRepository/moteur concerné" (§3(b)) : `PostFillProgression`
   et `OrderManager` SONT le moteur concerné pour ces deux sites.
4. **Fixture `attach_missing_stop` : broker combiné plutôt qu'insertion
   manuelle en base.** Pour atteindre l'état réel "ordre d'entrée encore
   SUBMITTED en local + setup en ERROR_REQUIRES_MANUAL_REVIEW", plutôt que
   de construire un `OrderRecord` à la main (ce qui aurait été un
   raccourci artificiel), le test fait réellement passer
   `OrderManager.place_entry_order` par sa branche stop-rejeté-et-annulation-
   échouée (`_cancel_parent_for_failed_protection`, `cancelled=False`) via
   un broker de test qui rejette tous les ordres SELL et refuse toute
   annulation (`_StopAlwaysRejectedBroker`, tests/
   test_review_status_sticky.py). Ce choix a permis de découvrir que ce
   chemin (déjà présent dans `tests/test_order_manager.py` sous une forme
   voisine, `MissingCancelBrokerConnector`) reproduit exactement l'état que
   l'audit 35 qualifiait de "fragilité structurelle... pas un chemin actif
   aujourd'hui" pour `attach_missing_stop` — voir §8.

## 5. Preuves de sortie

### 5.1 — Cliquet textuel (a) passe, sites trouvés

```
$ python -m pytest tests/test_active_status_write_sites.py -v
tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status PASSED [100%]
1 passed in 0.06s
```

`ALLOWED_ACTIVE_WRITE_SITES` (3 fichiers, 7 sites littéraux au total) :

```python
ALLOWED_ACTIVE_WRITE_SITES: dict[str, str] = {
    "app/engine/order_manager.py": (
        "Three literal writes: place_entry_order() writes ENTRY_ORDER_PLACED "
        "after a bracket order is accepted (:180); place_stop_order() writes "
        "STOP_ORDER_PLACED when called with update_setup_status=True, which "
        "only happens from fill_executor's simulated-fill path (:374); "
        "attach_missing_stop() writes ENTRY_ORDER_PLACED after repairing a "
        "missing protective stop (:466). ..."
    ),
    "app/engine/post_fill_progression.py": (
        "record_fill() writes ENTRY_FILLED unconditionally once a trailing "
        "stop is found (:64); mark_in_position() writes IN_POSITION when "
        "the caller passes protection_verified=True (:92). ..."
    ),
    "app/engine/reconciliation.py": (
        "Two literal writes: the existing-IBKR-position adoption loop in "
        "run() writes IN_POSITION once all adoption checks pass (:263) ... "
        "The FILLED branch's 'fill price/quantity unavailable' fallback "
        "writes ENTRY_FILLED before immediately overwriting it with "
        "MANUAL_REVIEW_REQUIRED (:507) ..."
    ),
}
```
(Texte complet, avec verdict sûr/gap par site, dans le fichier lui-même —
raccourci ici pour lisibilité du rapport.)

### 5.2 — Preuve négative du cliquet (a)

```
$ git status --short -- app/ tests/
(vide avant injection)

$ cat > app/engine/_s5b2_decoy.py   # fichier leurre, jamais commité
from app.models import SetupStatus
from app.storage.repositories import TradingRepository

def decoy(repository: TradingRepository, setup_id: str) -> None:
    repository.update_setup_status(
        setup_id, SetupStatus.ENTRY_ORDER_PLACED.value,
        "decoy write for S5b-2 negative proof",
    )

$ python -m pytest tests/test_active_status_write_sites.py -v
FAILED tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status
E   AssertionError: {'app/engine/_s5b2_decoy.py': [6]} is not false : New direct write(s) of an ACTIF status found outside the audited sites: {'app/engine/_s5b2_decoy.py': [6]}. ...
1 failed in 0.12s

$ rm app/engine/_s5b2_decoy.py
$ git diff -- app/
(vide)
$ git status --short -- app/
(vide)
$ python -m pytest tests/test_active_status_write_sites.py -v
tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status PASSED [100%]
1 passed in 0.06s
```
**PASS** — le cliquet détecte bien le leurre, échoue comme attendu, puis
repasse au vert une fois le leurre retiré, sans résidu.

### 5.3 — Tests comportementaux (b)

```
$ python -m pytest tests/test_review_status_sticky.py -v
... (19 tests) ...
19 passed in 6.17s
```

Couverture par site (STICKY = l'alarme survit, confirmant une garde
existante ; GAP = l'alarme est écrasée, dette S5b-3 documentée au §8, non
corrigée) :

| Site | Classe de test | MANUAL_REVIEW_REQUIRED | ERROR_REQUIRES_MANUAL_REVIEW |
|---|---|---|---|
| `reconciliation.py:263` (adoption IN_POSITION) | `PositionAdoptionReviewStickyTests` | **GAP** | STICKY |
| `order_manager.py:466` (`attach_missing_stop`) | `AttachMissingStopReviewStickyTests` | **GAP** | **GAP** |
| `post_fill_progression.py:64` (`record_fill`) | `PostFillProgressionDirectWriteReviewStickyTests` | **GAP** | **GAP** |
| `post_fill_progression.py:92` (`mark_in_position`) | `PostFillProgressionDirectWriteReviewStickyTests` | **GAP** | **GAP** |
| `order_manager.py:374` (`place_stop_order`) | `PlaceStopOrderDirectWriteReviewStickyTests` | **GAP** | **GAP** |
| cascade complète `simulate_fill_order` | `SimulatedFillFullCascadeReviewStickyTests` | **GAP** (→IN_POSITION) | **GAP** (→IN_POSITION) |
| `order_manager.py:180` (`place_entry_order` bracket) | `PlaceEntryOrderUnreachableFromAlarmTests` | inatteignable (gardé en amont) | inatteignable (gardé en amont) |
| `reconciliation.py:507` + branche FILLED | `ReconciliationFilledBranchUnreachableFromAlarmTests` | STICKY (inatteignable) | STICKY (inatteignable) |
| branche SUBMITTED (S5b-1) | `SubmittedBranchStickyReferenceTest` (+ suite complète dans `test_reconciliation.py`) | STICKY | STICKY (non re-testé ici, référence) |
| `disarm_setup` | `DisarmSetupLegitimateExitTests` | sortie légitime (voulue) | sortie légitime (voulue) |

Au moins un test de ce nouveau fichier prouve le collant sur un chemin
réel (`SubmittedBranchStickyReferenceTest`,
`ReconciliationFilledBranchUnreachableFromAlarmTests`,
`PositionAdoptionReviewStickyTests::test_error_requires_manual_review_survives_...`)
— conforme à §5.3 de l'ordre. `disarm_setup` est documenté comme sortie
humaine légitime, pas un oubli — conforme à §3(b) dernier paragraphe.

### 5.4 — `git diff feat/setup-conditions..HEAD -- app/` (sortie brute)

```
$ git diff feat/setup-conditions..HEAD -- app/
(rien — sortie vide)
```
**PASS.**

### 5.5 — Suite complète

```
$ python -m pytest -q
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 732 passed, 4 warnings, 134 subtests passed in 196.85s (0:03:16)
```
Seul `test_account_metrics.py` en échec — pré-existant, sans rapport avec
ce lot (déjà documenté identique par les audits 26 et 37, avant tout
travail de ce lot ou de S5b-1). **PASS**, conforme au point 5.5/§5 de
l'ordre. Delta : avant ce lot (état `feat/setup-conditions`, après S5b-1)
`1 failed, 712 passed` (audit 37 §7) → après, `1 failed, 732 passed` —
**+20 tests**, cohérent avec les 20 tests ajoutés par ce lot (1 dans
`test_active_status_write_sites.py` + 19 dans
`test_review_status_sticky.py`, `grep -cE "^    (async )?def test_"`
confirmé sur les deux fichiers).

## 6. Nettoyage (obligatoire)

- Fichier leurre créé et supprimé pendant la preuve négative §5.2 :
  `app/engine/_s5b2_decoy.py` — jamais ajouté à l'index, jamais commité,
  `git diff`/`git status --short -- app/` vides après suppression
  (confirmé §5.2 et §5.4).
- Aucun stash créé.
- Une branche créée : `fix/s5b2-review-sticky-ratchet` (demandée par
  l'ordre §7, non supprimée — c'est la branche de livraison de ce lot).
- `git status --short` après le second commit :
```
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/28_pre_s2.md
?? audit/31_cloture_s3.md
?? audit/34_cloture_s4.md
?? audit/39_verif_cliquet.md
?? data/setups/*.json (setups du jour, sans rapport avec ce lot)
?? tmp/
```
Identique à l'état constaté par l'audit 37 §6 avant ce lot (mêmes entrées
`??`/`D` préexistantes : setups du jour, `.codex/`, `tmp/`, plus
`audit/39_verif_cliquet.md`, produit par la conversation précédente, hors
périmètre de ce lot). Aucun artefact de S5b-2 ne reste non commité.

## 7. Suite de tests

```
$ python -m pytest -q
1 failed, 732 passed, 4 warnings, 134 subtests passed in 196.85s (0:03:16)
```
Avant ce lot (état `feat/setup-conditions`, après S5b-1, audit 37 §7) :
`1 failed, 712 passed, ..., 134 subtests passed`. Après : `1 failed, 732
passed`. Delta **+20 tests**, entièrement expliqué par ce lot (§5.5).

## 8. Découvert mais NON corrigé

Conformément à l'ordre §6 : trois chemins automatiques distincts, non
gardés sur le statut du setup, capables d'écraser une alarme
(`MANUAL_REVIEW_REQUIRED` et/ou `ERROR_REQUIRES_MANUAL_REVIEW`) avec un
statut ACTIF. Aucun n'a été corrigé — signalés seulement, comme demandé.
Proposé comme dette **S5b-3**, lot séparé.

1. **`app/engine/reconciliation.py:157-267` (boucle d'adoption de position
   existante, dans `run()`) — `app/engine/reconciliation.py:263`
   (`IN_POSITION`).** La garde de la boucle (`:162`,
   `if str(setup.get("status")...) in _TERMINAL_SETUP_STATUSES: continue`)
   utilise `_TERMINAL_SETUP_STATUSES` (`:632-638`), qui contient
   `ERROR_REQUIRES_MANUAL_REVIEW` mais **pas** `MANUAL_REVIEW_REQUIRED`.
   Le commentaire ajouté par S5b-1 juste au-dessus de
   `_REVIEW_LOCKED_SETUP_STATUSES` (`:640-645`) signalait déjà cette boucle
   comme "hors périmètre" de son propre correctif. **Scénario concret** :
   un setup `MANAGEMENT_ONLY` en mode `adopt_existing_ibkr_position` est
   mis en `MANUAL_REVIEW_REQUIRED` par la boucle elle-même à une passe
   (ex. `"Existing IBKR position not found"`, `:175-180`, ou `"Market price
   is below protective stop"`, `:205-211`, ou `"Broker stop order not
   found"`, `:226-233`) ; à une passe ultérieure, une fois la condition
   bloquante résolue côté broker, la même boucle continue et écrit
   `IN_POSITION` (`:263-267`) sans jamais relire ni référencer l'alarme
   posée précédemment — aucun événement ne signale l'écrasement.
   `ERROR_REQUIRES_MANUAL_REVIEW` EST protégé (dans
   `_TERMINAL_SETUP_STATUSES`). Confirmé par
   `PositionAdoptionReviewStickyTests` (tests/test_review_status_sticky.py).

2. **`app/engine/order_manager.py:396-471` (`attach_missing_stop`) —
   `app/engine/order_manager.py:466-470` (`ENTRY_ORDER_PLACED`).** Aucune
   lecture du statut du setup nulle part dans la fonction ; ses seules
   gardes portent sur l'ORDRE (côté BUY, statut `CREATED`/`SUBMITTED`,
   absence de stop actif déjà attaché). Reformule et confirme
   concrètement ce que l'audit 35 qualifiait de fragilité "théorique" :
   **le scénario existe déjà en production potentielle**, via
   `order_manager.py:527-563`
   (`_cancel_parent_for_failed_protection`) : quand un stop protecteur est
   rejeté ET que l'annulation compensatoire de l'ordre d'entrée parent
   n'est PAS acceptée par le broker (`cancelled = bool(result.accepted)`
   reste `False`), l'ordre d'entrée reste `SUBMITTED` en local tandis que
   le setup passe en `ERROR_REQUIRES_MANUAL_REVIEW`. Un appel humain
   ultérieur à la route réelle `POST /api/orders/{order_id}/attach-stop`
   (`app/api/routes_orders.py:78-85`, destinée précisément à réparer un
   stop manquant) réussit alors à écrire `ENTRY_ORDER_PLACED`
   directement par-dessus l'alarme, sans aucun événement mentionnant
   qu'une alarme existait. Reproduit et confirmé pour **les deux**
   statuts d'alarme par `AttachMissingStopReviewStickyTests`.

3. **`app/engine/fill_executor.py:40-99` (`simulate_fill_order`),
   cascadant vers `app/engine/post_fill_progression.py:64`
   (`record_fill`, `ENTRY_FILLED`), `app/engine/order_manager.py:374`
   (`place_stop_order` avec `update_setup_status=True`,
   `STOP_ORDER_PLACED`) et `app/engine/post_fill_progression.py:92`
   (`mark_in_position`, `IN_POSITION`).** La seule garde de
   `simulate_fill_order` (`fill_executor.py:46`) porte sur
   `order["status"] == SUBMITTED` — jamais sur le statut du setup.
   Reachable via la route réelle, déclenchée par un humain,
   `POST /api/orders/{order_id}/simulate-fill`
   (`app/api/routes_orders.py:112-119`), un outil de test de paper trading
   qui ignore totalement qu'un setup peut être sous revue. Selon que la
   nouvelle tentative de pose de stop échoue ou réussit dans le même
   appel, le statut final observable atterrit soit sur
   `ERROR_REQUIRES_MANUAL_REVIEW` (mais en ayant réellement traversé
   `ENTRY_FILLED` entre-temps — écrasement transitoire réel, prouvé
   isolément par `PostFillProgressionDirectWriteReviewStickyTests` et
   `PlaceStopOrderDirectWriteReviewStickyTests`), soit sur `IN_POSITION`
   (écrasement complet et stable, prouvé de bout en bout par
   `SimulatedFillFullCascadeReviewStickyTests`) — dans les deux cas pour
   les deux statuts d'alarme de départ.

**Non-découverte notable, pour mémoire** : `app/engine/order_manager.py:
180` (`place_entry_order`, écriture `ENTRY_ORDER_PLACED` du bracket) et
la branche FILLED de `reconciliation.py` (dont `:507`) restent, eux,
réellement inatteignables depuis une alarme aujourd'hui — reconfirmé
positivement par `PlaceEntryOrderUnreachableFromAlarmTests` et
`ReconciliationFilledBranchUnreachableFromAlarmTests`, pas seulement cité
de mémoire de l'audit 35.

**Limite structurelle du cliquet (a) lui-même** (documentée dans le
fichier, pas un bug) : une écriture dont la cible est une *variable*
calculée (comme la branche SUBMITTED de `reconciliation.py`, celle
corrigée par S5b-1) est invisible au scan textuel par construction — voir
le docstring de module de `tests/test_active_status_write_sites.py` et
audit 39. Ce n'est pas une découverte nouvelle de ce lot, seulement
reconfirmé et documenté à l'endroit où ça compte (dans le cliquet
lui-même).

## 9. Écarts par rapport à l'ordre

Aucun.
