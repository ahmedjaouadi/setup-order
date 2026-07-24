# 25 — Audit lecture seule : vérification de conformité du lot 3b-2

Mode lecture seule strict. Aucune modification de code. Commandes exécutées
en dehors de lectures de fichiers et de `git diff`/`git log`/`git status` :
`python -m pytest tests/test_reconciliation.py -q` (20 passed, déjà vert
avant cet audit, revérifié pour confirmer l'état) et `python -m pytest
tests/test_reconciliation.py --collect-only -q` (énumération des noms de
test). Aucun autre test n'a été relancé — la suite complète (704 passed /
1 failed pré-existant) avait déjà été exécutée et rapportée à la fin du
lot lui-même ; je ne l'ai pas rejouée ici, seul le fichier concerné par ce
lot a été revérifié.

---

## 0 — Anomalie préalable : rien n'est commité sur `fix/03b2-filled-branch`

```
$ git branch --show-current
fix/03b2-filled-branch
$ git status --short --branch | head -3
## fix/03b2-filled-branch
 M app/engine/reconciliation.py
 D data/setups/CODI_20260628_001.json
$ git log --oneline feat/setup-conditions..fix/03b2-filled-branch -- app/engine/reconciliation.py
30e2385 fix(reconciliation): plumb broker_executions and add pure execution matcher
$ git diff feat/setup-conditions..fix/03b2-filled-branch --stat -- app/engine/reconciliation.py
 app/engine/reconciliation.py | 72 ++++++++++++++++++++++++++++++++++++++++++--
 1 file changed, 69 insertions(+), 3 deletions(-)
```

Contrairement au lot 3a (`audit/21`, écart #1 : la branche n'existait même
pas), ici la branche `fix/03b2-filled-branch` **existe** et pointe bien sur
le tip de `fix/03b1-executions-plumbing`. Mais **la totalité du travail du
lot 3b-2** (câblage de `broker_positions`, branche FILLED, 9 tests) **n'a
jamais été commitée** — elle n'existe que comme modifications non commitées
de l'arbre de travail. La seule différence entre `feat/setup-conditions` et
le tip commité de `fix/03b2-filled-branch` est le commit `30e2385`, qui est
le lot **3b-1**, pas le 3b-2.

**Conséquence directe pour cet audit** : la commande demandée en Q5
(`git diff feat/setup-conditions..fix/03b2-filled-branch --
app/engine/reconciliation.py`) ne montre **aucune ligne du lot 3b-2** — ni
la branche FILLED, ni le câblage de `broker_positions`. Substitution
effectuée : comparaison du working tree (`git diff` sans argument de
branche, contre `HEAD` = tip de `fix/03b1-executions-plumbing`) pour
répondre aux questions de fond. C'est un écart de forme, signalé en
conclusion (écart #1), au même titre que celui déjà relevé pour le lot 3a.

---

## Q1 — L'écart de compte (695 → 704, 9 ou 10 tests ?)

```
$ python -m pytest tests/test_reconciliation.py --collect-only -q | grep FilledBranchTests
tests/test_reconciliation.py::FilledBranchTests::test_barreau1_nominal_weighted_price_reaches_in_position
tests/test_reconciliation.py::FilledBranchTests::test_barreau1_quantity_mismatch_falls_through_to_barreau3
tests/test_reconciliation.py::FilledBranchTests::test_barreau1_without_active_stop_requires_manual_review
tests/test_reconciliation.py::FilledBranchTests::test_barreau2_excluded_when_local_position_preexists
tests/test_reconciliation.py::FilledBranchTests::test_barreau2_used_when_position_newly_born
tests/test_reconciliation.py::FilledBranchTests::test_barreau3_no_reliable_source_marks_manual_review_without_position
tests/test_reconciliation.py::FilledBranchTests::test_idempotent_record_fill_called_once_across_two_passes
tests/test_reconciliation.py::FilledBranchTests::test_sell_filled_triggers_no_write
tests/test_reconciliation.py::FilledBranchTests::test_setup_already_in_position_receives_no_write
```

**9 méthodes**, pas 10. La classe `FilledBranchTests` (`tests/test_reconciliation.py:277-453`)
contient exactement neuf `def test_...`. Le compte `695 → 704` (delta = **+9**)
est donc **cohérent avec le code réel** ; c'est l'annonce finale du lot
("10 new `FilledBranchTests`") qui est fausse — une erreur de dénombrement
dans le message de synthèse, pas un défaut du travail livré.

Vérification qu'aucun test préexistant n'a été renommé/supprimé/fusionné :

```
$ git diff -- tests/test_reconciliation.py | grep -c '^-[^-]'
3
$ git diff -- tests/test_reconciliation.py | grep '^-[^-]'
--- a/tests/test_reconciliation.py
-from app.broker.ib_models import BrokerExecution
-from app.models import OrderRecord, OrderStatus
```

Seules deux lignes sont retirées, et ce sont deux lignes d'import élargies
(`BrokerExecution` → `BrokerExecution, BrokerPosition` ;
`OrderRecord, OrderStatus` → `OrderRecord, OrderStatus, OrderType,
PositionRecord, SetupStatus`), pas des corps de test. Les deux hunks du
diff (`@@ -3,15 +3,18 @@` et `@@ -203,5 +206,254 @@`) confirment : le
premier ne touche que le bloc d'imports, le second est une pure addition
après la dernière méthode de `MatchExecutionsToOrderTests`. **Aucun test
préexistant n'a été renommé, supprimé, fusionné ou remplacé.**

**Réponse Q1** : écart de forme (erreur de compte dans l'annonce, 10 au
lieu de 9), aucun écart de fond sur les tests eux-mêmes.

---

## Q2 — `protection_verified` est-il vraiment calculé ? (point critique)

Lignes exactes, `app/engine/reconciliation.py` :

```
518:            protection_verified = self.progression.has_active_protection(setup_id)
519:            if protection_verified:
520:                self.progression.mark_in_position(setup_id, protection_verified=True)
```

**Constat direct : ligne 520 passe le littéral `True`, pas la variable
`protection_verified` calculée à la ligne 518.** C'est exactement la
formulation que l'ordre interdisait ("Passer un littéral True est
formellement INTERDIT"). Grep exhaustif de `mark_in_position` dans `app/` :

```
$ grep -rn "mark_in_position" app/
app\engine\fill_executor.py:96:        self.progression.mark_in_position(
app\engine\post_fill_progression.py:83:    def mark_in_position(self, setup_id: str, *, protection_verified: bool) -> None:
app\engine\reconciliation.py:520:                self.progression.mark_in_position(setup_id, protection_verified=True)
```

Un seul site d'appel dans le diff de ce lot : `reconciliation.py:520`, et
c'est celui qui utilise le littéral. Pour comparaison, le site d'appel
préexistant et hors périmètre de ce lot, `fill_executor.py:96-99`, passe la
variable :
```python
96:        self.progression.mark_in_position(
97:            setup["setup_id"],
98:            protection_verified=protection_verified,
99:        )
```

**Nuance nécessaire, pas une excuse** : le littéral de la ligne 520 est
syntaxiquement à l'intérieur du bloc `if protection_verified:` (ligne 519)
— il ne peut donc être atteint que lorsque `protection_verified` vient
d'être évalué à `True` par le vrai appel à `has_active_protection` une
ligne au-dessus. Il n'existe **aucun chemin où ce littéral est atteint avec
une protection non vérifiée** : le seul appelant de `mark_in_position`
depuis ce fichier est cette unique ligne, gardée par ce unique `if`. Donc,
au sens strict de "un IN_POSITION mensonger" (l'incident du 29 juin), ce
n'est **pas** reproduit ici — la valeur *effective* de la protection a bien
été vérifiée avant l'écriture.

Mais l'ordre ne demandait pas seulement un résultat correct, il interdisait
explicitement la forme ("passer un littéral True est formellement
interdit"), précisément parce que cette forme est fragile aux
modifications futures : si demain quelqu'un déplace ce `mark_in_position`
hors du `if`, ou ajoute une branche supplémentaire avant lui sans y
reproduire la garde, le littéral `True` ne le révèle pas — alors que passer
la variable `protection_verified` l'aurait fait échouer immédiatement.
**Écart de forme réel, sur le point que l'ordre qualifiait lui-même de
"le plus important du lot".**

### Test du 29 juin — citation intégrale (`tests/test_reconciliation.py:303-317`)

```python
def test_barreau1_without_active_stop_requires_manual_review(self) -> None:
    # The 2026-06-29 incident: entry filled while its stop was rejected.
    executions = [
        _execution(order_id="9001", broker_perm_id="555", quantity=40, price=100.0)
    ]

    self.reconciliation._update_setup_after_reconciled_order(
        self._order_dict(),
        OrderStatus.FILLED.value,
        broker_positions=[],
        broker_executions=executions,
    )

    self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
    self.assertIn("entry_filled_without_protection", self._event_types())
```

- **Assertion positive du statut final** : `assertEqual(...,
  MANUAL_REVIEW_REQUIRED.value)` — oui, c'est une comparaison d'égalité
  stricte, pas une simple absence d'exception.
- **IN_POSITION jamais écrit** : la colonne `status` est un champ unique en
  base (`UPDATE setups SET status = ...`, `app/storage/repositories.py:454-471`
  — pas de table d'historique). Une assertion d'égalité à
  `MANUAL_REVIEW_REQUIRED` est donc *mathématiquement* incompatible avec un
  état final `IN_POSITION` sur le même champ. Mais le test ne va pas plus
  loin : il ne pose pas d'espion sur `mark_in_position` pour prouver que
  cette méthode n'a jamais été appelée (contrairement à
  `test_sell_filled_triggers_no_write` et
  `test_idempotent_record_fill_called_once_across_two_passes`, qui, eux,
  utilisent `mock.patch.object(..., wraps=...)` sur `record_fill`). Une
  preuve par espionnage de `mark_in_position` aurait été strictement plus
  forte ; l'absence d'un tel espion n'est pas un défaut fonctionnel ici (la
  lecture du code en Q3/§code confirme qu'aucun chemin entre les deux
  n'appelle `mark_in_position` avec `True` dans ce scénario), mais c'est
  une preuve un cran plus faible que ce que l'ordre semblait réclamer
  ("pas seulement 'pas d'exception levée'").
- **`has_active_protection` renvoie-t-il bien `False` dans ce test ?**
  **Il n'y a pas de mock.** `self.setUp()` (`tests/test_reconciliation.py:284-311`)
  crée le setup et l'ordre BUY mais **n'appelle pas**
  `self._add_active_stop_order()` dans ce test précis (contrairement à
  `test_barreau1_nominal_weighted_price_reaches_in_position`, qui l'appelle
  explicitement en premier). `has_active_protection` exécute donc la vraie
  implémentation (`app/engine/post_fill_progression.py:79-81`, elle-même
  basée sur `repository.protection_snapshot_for_setup`), qui retourne
  `False` parce qu'aucun ordre SELL actif n'existe en base pour ce
  `setup_id` — pas parce qu'une valeur est simulée. C'est une vérification
  de bout en bout (repository réel), pas un test unitaire isolé par mock —
  plus fort qu'un mock, pas plus faible.

---

## Q3 — Le barreau 3 ne crée-t-il vraiment aucune position ?

Code du barreau 3, `app/engine/reconciliation.py:492-511` :

```python
            if quantity is None or fill_price is None:
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.ENTRY_FILLED.value,
                    "Entry order filled",
                )
                self.repository.update_setup_status(
                    setup_id,
                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
                    "Filled but fill price/quantity unavailable",
                )
                self.event_store.record(
                    EventLevel.CRITICAL,
                    "entry_filled_unknown_fill_details",
                    "Entry filled but fill price/quantity unavailable",
                    setup_id=setup_id,
                    symbol=symbol,
                    data={"order_id": str(order.get("id") or "")},
                )
                return
```

Le `return` de la ligne 511 est **inconditionnel et syntaxiquement le
dernier statement du bloc** — les lignes 513-514 (`record_fill`) et
au-delà ne sont physiquement pas atteignables depuis ce bloc. Recherche
active d'un contre-exemple : le seul autre appelant de `record_fill` dans
tout `app/` est `fill_executor.py:96` (hors périmètre, chemin
`simulate_fill_order`, jamais invoqué depuis `_update_setup_after_reconciled_order`).
Le seul appel à `upsert_position` visible depuis ce chemin passe *par*
`record_fill` (`post_fill_progression.py:63`) — aucun `upsert_position`
direct n'existe dans la branche FILLED de `reconciliation.py`. **Aucun
chemin du barreau 3 n'atteint `record_fill` ni `upsert_position`,
confirmé.**

Test correspondant, `tests/test_reconciliation.py:380-390` :

```python
def test_barreau3_no_reliable_source_marks_manual_review_without_position(self) -> None:
    self.reconciliation._update_setup_after_reconciled_order(
        self._order_dict(),
        OrderStatus.FILLED.value,
        broker_positions=[],
        broker_executions=[],
    )

    self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
    self.assertIsNone(self.repository.get_position(self.symbol))
    self.assertIn("entry_filled_unknown_fill_details", self._event_types())
```

Ligne 389 : `self.assertIsNone(self.repository.get_position(self.symbol))`
— **assertion positive de l'absence de `PositionRecord`**, pas seulement du
statut. Conforme à ce que demandait l'ordre.

---

## Q4 — Qualité des tests (reportée du lot 3b-1)

### Prix pondéré

`tests/test_reconciliation.py:280-287` : exécutions `E1`
(quantity=10, price=100.0) et `E2` (quantity=30, price=110.0), ordre de
quantity=40.

- Moyenne pondérée : `(10×100.0 + 30×110.0) / 40 = (1000 + 3300) / 40 = 4300 / 40 = 107.5`
- Moyenne simple : `(100.0 + 110.0) / 2 = 105.0`

**107.5 ≠ 105.0** — les deux moyennes diffèrent réellement. Le test
(ligne 298-300) calcule les deux, affirme explicitement leur différence
(`assertNotEqual`), puis vérifie que le prix retenu par la position est la
moyenne **pondérée** (107.5), pas la moyenne simple. Ce test prouve
effectivement ce qu'il prétend prouver — verdict confirmé, ce n'est pas un
test qui passerait tout aussi bien avec un calcul naïf faux.

### Test d'idempotence

`tests/test_reconciliation.py:427-457` (citation complète du corps) :

```python
def test_idempotent_record_fill_called_once_across_two_passes(self) -> None:
    self._add_active_stop_order()
    executions = [
        _execution(order_id="9001", broker_perm_id="555", quantity=40, price=100.0)
    ]

    with mock.patch.object(
        self.reconciliation.progression,
        "record_fill",
        wraps=self.reconciliation.progression.record_fill,
    ) as record_fill:
        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )
        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

        # Second reconciliation pass for the same order: the setup is no
        # longer ENTRY_ORDER_PLACED/ENTRY_PARTIALLY_FILLED, so the status
        # guard blocks re-entry into the FILLED branch.
        self.reconciliation._update_setup_after_reconciled_order(
            self._order_dict(),
            OrderStatus.FILLED.value,
            broker_positions=[],
            broker_executions=executions,
        )
        record_fill.assert_called_once()
```

**Ce test n'exerce PAS deux passes de réconciliation successives au sens
du pipeline réel.** Il appelle directement la méthode privée
`_update_setup_after_reconciled_order` deux fois — il ne passe ni par
`ReconciliationEngine.run()`, ni par `_reconcile_local_orders`, ni par
`_mark_local_order_status`. Le "contexte figé" de l'ordre affirmait que
l'idempotence de production est assurée par la garde
`_ACTIVE_ORDER_STATUSES` (`reconciliation.py:372`, sur le **statut de
l'ordre local**, une fois celui-ci marqué `FILLED` en base) appliquée dans
`_reconcile_local_orders` — **ce mécanisme-là n'est jamais exercé par ce
test**. `_order_dict()` (`tests/test_reconciliation.py:244-255`) ne contient
même pas de clé `"status"` ; la fonction testée ne lit d'ailleurs jamais le
statut de l'ordre passé en paramètre, seulement celui du **setup**
(`setup_status`, ligne 453 du fichier source).

Ce que le test démontre réellement, c'est un garde-fou **différent** :
celui du point 2 de l'ordre lui-même ("GARDE DE STATUT" sur le setup,
ajoutée par ce lot), qui bloque une deuxième exécution du corps de la
branche FILLED parce que le statut du *setup* est passé de
`ENTRY_ORDER_PLACED` à `IN_POSITION` après le premier appel. C'est une
propriété réelle et testée du nouveau code, mais ce n'est pas la preuve
d'idempotence au niveau du pipeline de réconciliation complet (deux
appels à `.run()`, avec l'ordre local relu depuis le repository entre les
deux) que le libellé du test ("across two passes") et le contexte de
l'ordre suggèrent. **Le nom du test survend sa couverture.**

### Test d'appariement identifiants None des deux côtés

Existe : `tests/test_reconciliation.py:140-152`
(`test_no_match_when_identifiers_empty_on_one_or_both_sides`, préexistant
du lot 3b-1, non modifié par ce lot — confirmé en Q1/Q5, aucune ligne de
`MatchExecutionsToOrderTests` n'apparaît dans le diff). Dernier cas du
test :
```python
self.assertIsNone(
    _match_executions_to_order([execution_without_ids], order_without_ids)
)
```
`execution_without_ids` (`order_id=None, broker_perm_id=None`) contre
`order_without_ids` (`broker_order_id=None, broker_perm_id=None`) — deux
identifiants `None` des deux côtés — assertion positive `assertIsNone`,
donc **aucun appariement retenu**. Confirmé conforme.

---

## Q5 — Les branches existantes sont-elles intactes ?

Diff complet des lignes retirées (`-`) dans `app/engine/reconciliation.py` :

```
$ git diff -- app/engine/reconciliation.py | grep -c '^-[^-]'
0
```

**Zéro ligne supprimée ou modifiée dans tout le fichier** — chaque hunk du
diff n'ajoute que des lignes (`+`), y compris les deux ajouts de paramètre
`broker_positions: list[BrokerPosition] | None = None` dans des signatures
existantes (ajouts de paramètres keyword-only avec valeur par défaut, pas
de modification de ligne existante). La branche SUBMITTED (le bloc
`if status == OrderStatus.SUBMITTED.value: ... return`,
`reconciliation.py:456-470`) et la branche CANCELLED (à partir de
`if status != OrderStatus.CANCELLED.value: return`, ligne 536) sont
physiquement inchangées, seulement décalées vers le bas par l'insertion du
bloc FILLED entre les deux — confirmé par absence totale de `-` dans le
diff sur ces zones.

Note sur les numéros de ligne cités par l'ordre lui-même (":426-440" pour
SUBMITTED, ":441-465" pour CANCELLED) : ces numéros ne correspondent pas
exactement à l'état réel du fichier au moment où ce lot a démarré (le
`return` de SUBMITTED était en réalité à la ligne 460-461, le garde
CANCELLED à 462, d'après la première lecture faite en début de lot) — un
écart d'une vingtaine de lignes, probablement parce que l'ordre a été
rédigé sur un instantané antérieur du fichier (avant l'ajout du
`ExecutionMatch TypedDict` et du câblage `broker_executions` par le lot
3b-1). Ce n'est pas une erreur du lot 3b-2 lui-même, seulement une
observation sur la fraîcheur des références de lignes dans l'ordre — le
contenu qualitatif visé ("entre le retour de SUBMITTED et le garde de
CANCELLED") a bien été respecté, seuls les numéros absolus étaient déjà
obsolètes avant même que ce lot ne commence.

Diff de `tests/test_reconciliation.py` : déjà montré en Q1 — deux lignes
d'import élargies, zéro caractère modifié dans un corps de test
préexistant.

Fichiers touchés par le diff total (tracked, hors `data/setups/*.json` qui
sont des fichiers de données runtime pré-existants et sans rapport,
présents dans le `git status` dès le début de la conversation) :

```
$ git diff --name-only
app/engine/reconciliation.py
data/setups/CODI_20260628_001.json
data/setups/TXN_20260630_001.json
tests/test_reconciliation.py
```

```
$ git diff --stat -- app/engine/post_fill_progression.py app/engine/fill_executor.py app/engine/order_manager.py app/engine/state_machine.py
(vide)
```

**`post_fill_progression.py`, `fill_executor.py`, `order_manager.py` et
`state_machine.py` ne sont touchés par aucune modification.** Conforme à
l'interdiction explicite de l'ordre (périmètre §2 et §7).

---

## Q6 — L'ordre des opérations au barreau 2

Séquence réelle, avec numéros de ligne :

```
486:            quantity, fill_price = self._resolve_fill_details(     <- appelé D'ABORD
...
514:            position = self.progression.record_fill(order_id, setup_id, quantity, fill_price, symbol)  <- appelé ENSUITE, seulement si (quantity, fill_price) résolus
...
562:    def _resolve_fill_details(
...
574:        if self.repository.get_position(symbol) is not None:   <- la vérification d'absence
575:            return None, None
```

`_resolve_fill_details` (appelé ligne 486, définition ligne 562) contient
la vérification `self.repository.get_position(symbol) is not None` à la
ligne 574, **avant** toute recherche de `BrokerPosition` correspondante
(boucle sur `broker_positions` juste après, lignes 576-580) et avant tout
retour de valeurs exploitables. La fonction retourne obligatoirement
(directement ou après le calcul du barreau 1/2) avant que l'appelant ne
puisse atteindre la ligne 514. **Le code est synchrone et mono-threadé** :
`_update_setup_after_reconciled_order` n'est pas une coroutine (`def`, pas
`async def`), il n'y a pas de point de suspension entre la lecture de
`get_position` et l'appel à `record_fill` — aucune écriture concurrente ne
peut s'intercaler.

**Aucun chemin ne fait exécuter `record_fill` avant la vérification
d'absence de position préexistante.** Le seul moyen théorique de casser
cet ordre serait un appelant qui n'utiliserait pas
`_resolve_fill_details` — recherche exhaustive : c'est le seul point
d'appel de `record_fill` dans la branche FILLED de `reconciliation.py`
(ligne 514), il n'y en a pas d'autre.

---

## CONCLUSION : ÉCARTS CONSTATÉS

1. **Rien n'est commité sur `fix/03b2-filled-branch`.** La branche existe
   (contrairement au lot 3a) mais ne contient, en commit, que le lot 3b-1
   (`30e2385`) — tout le travail de ce lot (câblage `broker_positions`,
   branche FILLED, 9 tests) reste non commité dans l'arbre de travail.
   `git diff feat/setup-conditions..fix/03b2-filled-branch` ne montre donc
   rien du lot 3b-2 ; l'audit a dû comparer le working tree.
2. **`mark_in_position(setup_id, protection_verified=True)` passe un
   littéral, pas la variable `protection_verified` calculée juste
   au-dessus** (`reconciliation.py:518-520`) — exactement la forme que
   l'ordre qualifiait de "formellement interdite" sur "le point le plus
   important du lot". Le littéral est syntaxiquement inatteignable sans
   que `protection_verified` ait été `True` (il est dans le corps du
   `if protection_verified:`), donc **aucun chemin d'exécution actuel ne
   produit un IN_POSITION mensonger** — mais la forme demandée
   explicitement (repasser la variable) n'a pas été respectée, ce qui rend
   la garantie fragile à toute évolution future du code qui ne
   reproduirait pas exactement ce `if`.
3. **L'annonce finale du lot comptait "10 nouveaux tests"** ; il y en a
   réellement **9** dans `FilledBranchTests`. Le delta 695→704 (+9) est
   cohérent avec le code réel — c'est une erreur de dénombrement dans le
   message de synthèse, sans conséquence sur le contenu livré.
4. **Le test d'idempotence n'exerce pas deux passes de réconciliation au
   sens du pipeline réel** (`.run()` / `_reconcile_local_orders` /
   `_ACTIVE_ORDER_STATUSES` sur le statut de l'ordre local) : il appelle
   directement `_update_setup_after_reconciled_order` deux fois et
   démontre en réalité la garde de statut du **setup** (nouvelle dans ce
   lot), pas le mécanisme d'idempotence décrit dans le "contexte figé" de
   l'ordre. Le nom du test ("across two passes") suggère une couverture
   plus large que ce qu'il vérifie réellement.
5. **Le test du 29 juin n'espionne pas `mark_in_position`** pour prouver
   positivement qu'il n'a jamais été invoqué avec `True` — il s'appuie sur
   l'unicité de la colonne `status` en base (une assertion d'égalité à
   `MANUAL_REVIEW_REQUIRED` exclut mécaniquement `IN_POSITION` comme état
   final, mais pas comme état transitoire hypothétique). Dans ce cas
   précis, la lecture du code confirme qu'aucun chemin transitoire de ce
   type n'existe, donc ce n'est pas un défaut fonctionnel, seulement une
   preuve un cran plus faible que ce qu'un espionnage explicite aurait
   offert.
6. **Les numéros de ligne cités par l'ordre lui-même** (SUBMITTED
   ":426-440", CANCELLED ":441-465") **ne correspondaient déjà plus à
   l'état réel du fichier** au début de ce lot (décalage d'environ 20
   lignes, probablement dû à des ajouts du lot 3b-1 postérieurs à la
   rédaction de l'ordre). Sans conséquence sur le contenu livré (le point
   d'insertion qualitatif — entre le retour SUBMITTED et le garde
   CANCELLED — a bien été respecté), mais à signaler pour la fraîcheur des
   futurs ordres.

Aucun écart trouvé sur : le périmètre de fichiers touchés (Q5, confirmé
strictement `reconciliation.py` + `test_reconciliation.py`), l'intégrité
des branches SUBMITTED/CANCELLED (zéro ligne modifiée), l'absence de
`PositionRecord` au barreau 3 (Q3, assertion positive présente), l'ordre
des opérations au barreau 2 (Q6, `get_position` s'exécute toujours avant
tout `record_fill` possible), la validité mathématique du test de prix
pondéré (Q4), et la couverture du cas d'appariement à identifiants `None`
des deux côtés (Q4).
