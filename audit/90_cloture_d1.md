# 90 — Clôture D-1 (git uniquement, aucun code, aucune suppression)

Mode : opérations git strictement de fusion/publication, aucun fichier de
`app/` ou `tests/` touché, aucune suppression. Chaque point ci-dessous :
commande exacte + sortie brute.

## C1 — commit D-1 en tête de sa branche

```
$ git log --oneline fix/d1-account-wide-exposure-cap -2
2e0dbf8 docs(audit): rapport de lot D-1 — plafond d'exposition compte-entier
b83103e feat(risk): cap exposure against account-wide broker positions, not just local (root D, D-1)
```

`b83103e` juste en dessous du commit de rapport (`2e0dbf8`), conforme à
l'attendu. **PASS**

## C2 — merger dans feat/setup-conditions (fast-forward strict)

```
$ git checkout feat/setup-conditions
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Switched to branch 'feat/setup-conditions'

$ git merge --ff-only fix/d1-account-wide-exposure-cap
Updating b2d55db..2e0dbf8
Fast-forward
 app/engine/broker_reality.py        |  83 ++++++
 app/engine/entry_order_executor.py  |  28 ++
 app/engine/trade_guards.py          |  82 +++++-
 app/engine/trading_engine.py        |   4 +-
 audit/89_rapport_d1.md              | 557 ++++++++++++++++++++++++++++++++++++
 audit/ORDRE_D1.md                   | 112 ++++++++
 tests/test_account_wide_exposure.py | 104 +++++++
 tests/test_entry_order_executor.py  | 145 +++++++++-
 tests/test_trade_guards.py          | 117 ++++++++
 9 files changed, 1217 insertions(+), 15 deletions(-)
 create mode 100644 audit/89_rapport_d1.md
 create mode 100644 audit/ORDRE_D1.md
 create mode 100644 tests/test_account_wide_exposure.py
```

`--ff-only` a réussi (fast-forward pur, aucun commit de merge créé). Les
lignes `D data/setups/...` proviennent de suppressions non indexées
préexistantes dans l'arbre de travail (hors périmètre D-1, présentes avant
ce lot), non affectées par le `checkout`/`merge`. **PASS**

## C3 — présent dans feat/setup-conditions

```
$ git log --oneline feat/setup-conditions | grep b83103e
b83103e feat(risk): cap exposure against account-wide broker positions, not just local (root D, D-1)
```

**PASS**

## C4 — pousser

```
$ git push origin feat/setup-conditions
To https://github.com/ahmedjaouadi/setup-order.git
   b2d55db..2e0dbf8  feat/setup-conditions -> feat/setup-conditions

$ git push -u origin fix/d1-account-wide-exposure-cap
remote:
remote: Create a pull request for 'fix/d1-account-wide-exposure-cap' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/d1-account-wide-exposure-cap
remote:
branch 'fix/d1-account-wide-exposure-cap' set up to track 'origin/fix/d1-account-wide-exposure-cap'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/d1-account-wide-exposure-cap -> fix/d1-account-wide-exposure-cap
```

Les deux push ont réussi (fast-forward pour `feat/setup-conditions`,
nouvelle branche distante pour `fix/d1-account-wide-exposure-cap`).
**PASS**

## C5 — présent dans origin

```
$ git log --oneline origin/feat/setup-conditions | grep b83103e
b83103e feat(risk): cap exposure against account-wide broker positions, not just local (root D, D-1)
```

**PASS**

## C6 — rien en attente

```
$ git log --oneline origin/feat/setup-conditions..feat/setup-conditions
(sortie vide)
```

**PASS**

## C7 — app/ et tests/ propres

```
$ git status --short -- app/ tests/
(sortie vide)
```

**PASS**

## Verdict global

| Point | Vérifié | Verdict |
|---|---|---|
| C1 | `b83103e` en tête (sous le rapport `2e0dbf8`) | PASS |
| C2 | fast-forward strict, aucun merge commit | PASS |
| C3 | `b83103e` présent dans `feat/setup-conditions` | PASS |
| C4 | push des deux branches réussi | PASS |
| C5 | `b83103e` présent dans `origin/feat/setup-conditions` | PASS |
| C6 | aucun commit local en attente de push | PASS |
| C7 | `app/`/`tests/` propres | PASS |

Aucune suppression effectuée, aucun fichier `app/` ou `tests/` modifié
pendant cette clôture. D-1 est mergé (fast-forward) et poussé sur les deux
branches.
