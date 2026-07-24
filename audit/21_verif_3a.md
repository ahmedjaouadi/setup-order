# 21 — Audit lecture seule : vérification de conformité du lot 3a

Mode lecture seule strict. Aucune modification de code. Ce lot vérifie le
travail d'extraction de la progression post-fill (commandé sur la base de
`audit/20_factorisation_postfill.md`) contre son ordre. Aucun test n'a été
modifié ; `python -m pytest tests/test_fill_executor.py
tests/test_post_fill_progression.py tests/test_order_manager.py
tests/test_reconciliation.py` a été exécuté en lecture seule pour vérifier
l'état (18 passed) — c'est la seule commande exécutée en dehors de
lectures de fichiers et de `git diff`.

---

## 0 — Anomalie préalable : la branche `fix/03a-extract-postfill` n'existe pas

`git rev-parse fix/03a-extract-postfill` échoue (`unknown revision`),
aucune trace dans `git branch -a`, `git for-each-ref`, ni `git reflog`.
Le travail n'a **jamais été commité sur une branche dédiée**. Il existe
uniquement comme modifications non commitées dans l'arbre de travail de
la branche courante `fix/01-gate-current-status`, elle-même identique en
commit à `feat/setup-conditions` (`git merge-base` des deux = `9544cab`,
`git diff feat/setup-conditions..HEAD --stat` = vide).

**Conséquence** : la commande demandée à l'étape 1 (`git diff
feat/setup-conditions..fix/03a-extract-postfill`) est irréalisable telle
quelle. Substitution effectuée : `git diff` (working tree, non commité)
contre `HEAD`=`feat/setup-conditions`. C'est un écart de forme en soi —
voir écart #1 en conclusion.

---

## 1 — Fichiers touchés vs liste autorisée

Diff complet (working tree) :

```
 app/engine/action_executor.py      |   9 ++          <- HORS PÉRIMÈTRE
 app/engine/entry_order_executor.py |   6 +-          <- HORS PÉRIMÈTRE
 app/engine/fill_executor.py        |  71 ++++---------  AUTORISÉ
 app/engine/trading_engine.py       |  18 +++-          <- HORS PÉRIMÈTRE
 app/models.py                      |  15 +++            <- HORS PÉRIMÈTRE
 data/setups/CODI_20260628_001.json | supprimé            <- HORS PÉRIMÈTRE
 data/setups/TXN_20260630_001.json  | supprimé            <- HORS PÉRIMÈTRE
 tests/test_action_executor.py      |  89 ++++            <- HORS PÉRIMÈTRE
 tests/test_entry_order_executor.py |  34 +++-           <- HORS PÉRIMÈTRE
 app/engine/post_fill_progression.py (nouveau, non tracké)  AUTORISÉ
 tests/test_post_fill_progression.py (nouveau, non tracké)  AUTORISÉ
 + tests/test_entry_gate_current_status.py (nouveau)     <- HORS PÉRIMÈTRE
 + ~13 fichiers data/setups/*.json non trackés            <- HORS PÉRIMÈTRE
```

Vérification de contenu : `action_executor.py`, `entry_order_executor.py`,
`trading_engine.py`, `models.py` (ajout de `ENTRY_ELIGIBLE_STATUSES`) et
leurs tests forment un ensemble cohérent et distinct — un gate d'entrée
sur le statut courant (`ENTRY_READY blocked: setup already past entry`),
qui correspond au nom de la branche courante `fix/01-gate-current-status`
et au fichier `tests/test_entry_gate_current_status.py`. **Ce n'est pas
une extension du périmètre du lot 3a** : aucune de ces lignes ne touche
`simulate_fill_order`, `PostFillProgression`, ni la progression
post-fill. C'est un autre chantier, non commité, qui cohabite dans le
même arbre de travail non commité que le lot 3a.

Les fichiers **effectivement liés au lot 3a** sont exactement :
`app/engine/fill_executor.py` (modifié) et
`app/engine/post_fill_progression.py` (nouveau). C'est conforme à la
liste autorisée (nouveau module + `fill_executor.py`). `order_manager.py`
n'a pas été touché (constat, pas d'écart : l'ordre le rendait
conditionnel — "si nécessaire" — et il n'était effectivement pas
nécessaire ici puisque la signature de `FillExecutor.__init__` n'a pas
changé).

**Écart de fond** : rien dans le lot 3a lui-même ne déborde de la liste
autorisée. **Écart de forme** : le lot n'a jamais été isolé sur sa propre
branche/commit, il partage l'arbre de travail avec un chantier sans
rapport — impossible de vérifier par un simple `git diff` de branche que
le lot 3a est bien tout ce qui a été livré, il a fallu trier fichier par
fichier.

---

## 2 — `tests/test_fill_executor.py` et `tests/test_order_manager.py`

```
$ git diff -- tests/test_fill_executor.py
(vide)
$ git diff -- tests/test_order_manager.py
(vide)
```

Zéro octet modifié dans les deux fichiers. **Conforme.**

---

## 3 — Comparaison ligne à ligne des 8 blocs (b) de `audit/20`

### Bloc (b)1 — `:68` `update_order_status(..., FILLED)`

Ancien (`fill_executor.py:68`, HEAD) :
```python
self.repository.update_order_status(order_id, OrderStatus.FILLED.value)
```
Nouveau (`post_fill_progression.py:29`) :
```python
self.repository.update_order_status(order_id, OrderStatus.FILLED.value)
```
**Identique, octet pour octet. DÉPLACÉ.**

### Bloc (b)2 — `:69-71` `get_setup` + garde

Ancien :
```python
setup = self.repository.get_setup(order["setup_id"])
if not setup:
    return None
```
Nouveau (`:30-32`) :
```python
setup = self.repository.get_setup(setup_id)
if not setup:
    return None
```
Seule différence : `order["setup_id"]` → `setup_id` (paramètre nommé).
Adaptation de signature strictement nécessaire à l'extraction (la
fonction n'a plus accès à `order`), aucune logique changée. **DÉPLACÉ
avec adaptation de signature minimale.**

### Bloc (b)3 — `:73-89` lecture `trailing_stop_loss.initial_stop` + branche d'échec

Ancien (`:73-89`) et nouveau (`post_fill_progression.py:34-50`) : comparés
caractère pour caractère — **identiques**, y compris les clés `setup_id=`,
`symbol=`, le message d'event, le message de statut. **DÉPLACÉ à
l'identique.**

### Bloc (b)4 — `:91-102` construction `PositionRecord` + `upsert_position`

Ancien :
```python
position = PositionRecord(
    symbol=broker_position.symbol,
    setup_id=setup["setup_id"],
    quantity=broker_position.quantity,
    ...
    risk_remaining=round(max(fill_price - stop_loss, 0) * broker_position.quantity, 2),
    status="OPEN",
)
self.repository.upsert_position(position)
```
Nouveau (`:52-63`) :
```python
position = PositionRecord(
    symbol=symbol,
    setup_id=setup["setup_id"],
    quantity=quantity,
    ...
    risk_remaining=round(max(fill_price - stop_loss, 0) * quantity, 2),
    status="OPEN",
)
self.repository.upsert_position(position)
```
Différence : `broker_position.symbol`/`broker_position.quantity` (objet
spécifique au broker simulé, classé (a)) → `symbol`/`quantity` (scalaires
passés en paramètres). C'est la décision de conception que `audit/20`
Q1(c) et INCERTITUDE #3 laissaient explicitement ouverte ("le format exact
que prendrait la donnée d'entrée côté réel ... n'est pas tranché par ce
lot"). L'implémentation a tranché : primitives plutôt que
`BrokerPosition`. Décision de conception raisonnable et cohérente avec
Q3, mais c'est une **reformulation**, pas un déplacement — signalée pour
mémoire, pas comme anomalie.

### Bloc (b)5 — `:103-107` `update_setup_status(ENTRY_FILLED)`

Identique caractère pour caractère (`:64-68` du nouveau fichier).
**DÉPLACÉ à l'identique.**

### Bloc (b)6 — `:108-115` `event_store.record("entry_filled", ...)`

Identique sauf `data={"fill_price": fill_price, "quantity":
broker_position.quantity}` → `data={"fill_price": fill_price,
"quantity": quantity}`. Même remarque que bloc 4 (paramétrage, pas
reformulation de logique). **DÉPLACÉ avec adaptation de signature.**

### Bloc (b)7 — `:117-118` vérification `protection_snapshot_for_setup`

Ancien :
```python
protection = self.repository.protection_snapshot_for_setup(setup["setup_id"])
if not protection.get("has_active_stop_order"):
```
Nouveau — **pas déplacé tel quel**, transformé en méthode dédiée
(`post_fill_progression.py:79-81`) :
```python
def has_active_protection(self, setup_id: str) -> bool:
    protection = self.repository.protection_snapshot_for_setup(setup_id)
    return bool(protection.get("has_active_stop_order"))
```
et le site d'appel dans `fill_executor.py:84` devient
`protection_verified = self.progression.has_active_protection(setup["setup_id"])`
suivi de `if not protection_verified:` — la négation qui était inline est
maintenant portée par le nom de la variable. C'est cohérent avec
`audit/20` (":117-118, la vérification elle-même est commune ; c'est la
branche qui suit qui diverge (a)") : la vérification est bien isolée dans
le module partagé, la branche de pose du stop reste dans
`fill_executor.py`. **Correctement séparé, mais REFORMULÉ** : une méthode
et une abstraction booléenne ont été créées, ce n'est pas une simple
relocation de 2 lignes.

### Bloc (b)8 — `:128-132` `update_setup_status(IN_POSITION)`

Ancien : écriture **inconditionnelle** à ce point du flot (le contrôle de
flux garantissait déjà la protection avant d'arriver ici : soit le
snapshot montrait un stop actif, soit un stop venait d'être posé sans
rejet — sinon `return position` sortait plus tôt).

Nouveau (`post_fill_progression.py:83-94`) :
```python
def mark_in_position(self, setup_id: str, *, protection_verified: bool) -> None:
    """... Refuses to write unless the caller has proven the position is
    protected (audit 19, obstacle 3: an unconditional write here would
    let a caller mark a naked position as protected).
    """
    if not protection_verified:
        return
    self.repository.update_setup_status(
        setup_id,
        SetupStatus.IN_POSITION.value,
        "Position protected and open",
    )
```
**REFORMULÉ, pas déplacé** : une garde d'exécution (`if not
protection_verified: return`) a été ajoutée, absente du bloc (b) original
classé par `audit/20`. C'est une addition de logique délibérée (le
docstring cite explicitement l'obstacle 3 de `audit/19`), pas une
relocation — voir §6 et §7 ci-dessous pour ce que cette garde protège
réellement en pratique.

**Bilan §3** : sur 8 blocs, 4 sont déplacés à l'identique (1, 3, 5, et
2 avec adaptation triviale de signature), 2 ont un paramétrage de
signature sans changement de logique (4, 6), et 2 ont été reformulés en
nouvelles méthodes avec ajout de logique (7, 8). Aucune reformulation
cosmétique gratuite (pas de renommage arbitraire de variable, pas de
changement de style) — les reformulations trouvées sont toutes
fonctionnelles et localisées aux deux points que `audit/20` avait
lui-même signalés comme ambigus ((c), obstacle 3).

---

## 4 — `mark_in_position` : `protection_verified` littéral ou calculé ?

Site d'appel, `app/engine/fill_executor.py:84-98` (working tree) :

```python
84: protection_verified = self.progression.has_active_protection(setup["setup_id"])
85: if not protection_verified:
86:     stop_order = await self.stop_order_placer.place_stop_order(
...
93:     if stop_order.status in {OrderStatus.REJECTED.value, OrderStatus.ERROR.value}:
        return position
94:     protection_verified = True
...
98:     protection_verified=protection_verified,
```

Réponse : **les deux**, selon la branche.
- Si le snapshot montre déjà un stop actif : `protection_verified` est la
  valeur **calculée** par `has_active_protection` (ligne 84).
- Si un stop vient d'être posé et n'est ni `REJECTED` ni `ERROR` :
  `protection_verified` est réécrit en un **littéral `True` codé en dur**
  (ligne 94), sans re-vérification que le stop posé est effectivement
  actif (pas de nouvel appel à `has_active_protection`, pas de test sur
  `stop_order.status` autre que l'exclusion de rejet/erreur).

C'est exactement le même critère que le code original (qui ne testait
que `status in {REJECTED, ERROR}` avant d'écrire `IN_POSITION`) — le
littéral `True` de la ligne 94 ne fait que renommer/formaliser ce critère
préexistant sous l'apparence d'une valeur "vérifiée". Voir §6.

---

## 5 — Imports du nouveau module

`app/engine/post_fill_progression.py:1-5` (cité intégralement) :
```python
from __future__ import annotations

from app.models import EventLevel, OrderStatus, PositionRecord, SetupStatus
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
```

Aucun import de `app.engine.*`. **Conforme** à la conclusion Q3 de
`audit/20` (seul un module n'important ni `state_machine` ni un autre
fichier de `app/engine/*` évite tout cycle avec `reconciliation.py`).
Confirmé aussi : ni `order_manager.py` ni `reconciliation.py` ne
référencent `PostFillProgression` ou `post_fill_progression` — le
câblage côté réel n'a pas été fait dans ce lot (cohérent avec une liste
autorisée qui ne mentionne pas `reconciliation.py`).

---

## 6 — Existe-t-il un chemin où `IN_POSITION` est écrit sans protection constatée ?

Dans `mark_in_position`, la garde `if not protection_verified: return`
est réelle et testée (`test_mark_in_position_without_verified_protection_
does_not_write`, `tests/test_post_fill_progression.py:123-139` — passe
`protection_verified=False` explicitement et vérifie qu'aucune écriture
n'a lieu). **Aucun chemin ne contourne cette garde** avec un booléen
`False` explicite : je n'ai pas trouvé de contre-exemple direct.

Mais la garde ne protège que contre un `False` explicite — elle ne
protège pas contre un `True` **non fondé**. Or le seul appelant actuel
(`fill_executor.py:94`, §4 ci-dessus) fabrique ce `True` par litéral dès
que la pose du stop n'a pas été rejetée, sans revérifier que le stop est
réellement actif (un stop `SUBMITTED`/`PENDING`/tout statut hors
`{REJECTED, ERROR}` suffit). **Ce n'est pas une régression introduite par
l'extraction** — le code original avait exactement la même faiblesse,
non corrigée, seulement non nommée. Mais le docstring de
`mark_in_position` ("Refuses to write unless the caller has proven the
position is protected") **surdéclare** ce que la garde garantit
réellement : elle garantit seulement que l'appelant a *dit* `True`, pas
que la preuve existe. Avec le seul appelant actuel, la garde est donc une
tautologie (elle ne peut jamais bloquer un chemin d'exécution réel, sauf
le test qui la force explicitement à `False`). Ce n'est pas un
contre-exemple à "IN_POSITION écrit sans protection constatée" au sens
strict de l'ordre (aucun test/chemin actuel ne le fait), mais c'est un
écart entre ce que le module *prétend* garantir et ce qu'il garantit
*effectivement* aujourd'hui.

---

## 7 — Modifications ne servant pas l'extraction

Deux constats, en plus de la contamination de fichiers hors périmètre
déjà notée en §1 (qui ne fait pas partie du diff du lot 3a lui-même) :

1. **Double appel à `get_setup`.** `fill_executor.py` appelle désormais
   `self.repository.get_setup(order["setup_id"])` une seconde fois
   après `record_fill` (ligne 80, working tree), alors que
   `record_fill` avait déjà récupéré et validé ce même setup en interne
   (`post_fill_progression.py:30`). L'original ne faisait l'appel
   qu'une fois. Cet appel redondant existe uniquement parce que
   `record_fill` retourne un `PositionRecord | None` et ne redonne pas
   le dict `setup` à l'appelant, qui en a pourtant besoin plus loin pour
   `place_stop_order(setup, ...)`. Ce n'est pas un bug fonctionnel
   (`get_setup` est réputé stable entre les deux appels dans ce flot
   synchrone), mais c'est un coût (une requête en plus) et une
   incohérence introduits par le découpage de l'interface, pas une pure
   extraction de code.
2. **Renommage/négation implicite en bloc (b)7** (`protection` +
   `if not protection.get(...)` → `has_active_protection` retournant un
   booléen positif) et **garde ajoutée en bloc (b)8** (§3, §6) : ce sont
   des ajouts de logique/abstraction au-delà du simple déplacement,
   déjà détaillés ci-dessus — je ne les répète pas ici mais ils comptent
   comme "modification ne servant pas strictement le déplacement".

Aucun renommage cosmétique, aucun reformatage, aucune correction
opportuniste sans rapport (style, imports inutiles supprimés à tort,
etc.) n'a été trouvé dans `fill_executor.py`/`post_fill_progression.py`
eux-mêmes.

---

## CONCLUSION : ÉCARTS CONSTATÉS

Le contenu du lot 3a (`fill_executor.py` + `post_fill_progression.py` +
`test_post_fill_progression.py`) respecte la liste de fichiers autorisée
et ne touche pas `test_fill_executor.py`/`test_order_manager.py`. Mais :

1. **La branche `fix/03a-extract-postfill` n'a jamais été créée** — le
   travail est resté non commité dans l'arbre de la branche
   `fix/01-gate-current-status`, rendant la vérification par
   `git diff <branche>..<branche>` demandée à l'étape 1 impossible telle
   quelle (substitution par diff de working tree effectuée).
2. **Le même arbre de travail contient un chantier sans rapport**
   (`action_executor.py`, `entry_order_executor.py`, `trading_engine.py`,
   `models.py` et leurs tests, plus des fichiers `data/setups/*.json`) —
   non commité non plus, donc aucune frontière ne sépare le lot 3a de ce
   second chantier. Ce n'est pas une extension du périmètre du lot 3a
   lui-même, mais c'est un manquement à l'isolation attendue par l'ordre.
3. **Deux des 8 blocs (b) ont été reformulés, pas simplement déplacés**
   (bloc 7 : extraction en méthode `has_active_protection` avec inversion
   de la négation ; bloc 8 : ajout d'une garde `if not
   protection_verified: return` absente de l'original) — logique
   nouvelle, pas seulement relocation. Justifiée par les incertitudes
   déjà notées dans `audit/20` ((c), obstacle 3), mais ce sont bien des
   reformulations, pas des déplacements à l'identique.
4. **Le docstring de `mark_in_position` surdéclare la garantie apportée** :
   la garde ne peut aujourd'hui jamais s'activer sur un chemin réel car
   son unique appelant lui passe soit une valeur calculée soit un
   littéral `True` non re-vérifié (`fill_executor.py:94`) — la faiblesse
   préexistante (statut du stop non re-contrôlé au-delà de
   `REJECTED`/`ERROR`) n'a été ni corrigée ni aggravée, seulement
   déplacée derrière un nom qui suggère une vérification plus forte
   qu'elle ne l'est.
5. Deux blocs (4, 6) ont un paramétrage de signature (scalaires au lieu
   de `broker_position.symbol`/`.quantity`) qui tranche une question que
   `audit/20` avait explicitement laissée ouverte (INCERTITUDE #3) — pas
   un écart au sens strict, mais une décision de conception prise sans
   retour à l'audit préalable.
6. Un appel redondant à `get_setup` a été introduit côté
   `fill_executor.py` (§7.1), conséquence de l'interface choisie pour
   `record_fill`.
