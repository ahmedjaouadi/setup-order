# Clôture B-1b (B-1b-1 + B-1b-2) — fusion de la paire et push vérifiés

Contexte : merge de `fix/b1b2-degrade-explicit-no-broker` (qui empile `fix/b1b1-route-raisestop-to-broker`) dans `feat/setup-conditions`, en une seule fusion ff-only couvrant les deux commits de la paire, puis push des trois branches vers `origin`. Aucun code modifié, aucune suppression effectuée.

## C1 — empilement correct

Commande :
```
git log --oneline fix/b1b2-degrade-explicit-no-broker -4
```

Sortie brute :
```
d26ea98 docs(audit): rapport de lot B-1b-2 — dégradation explicite stop non atteint au broker
b94e247 fix(stop-modification): alert explicitly when stop modification does not reach broker (root B, B-1b-2)
5921728 docs(audit): rapport de lot B-1b-1 — routage RAISE_STOP vers le broker vérifié
a3b2a93 feat(signals): route RAISE_STOP to broker via StopModificationService (root B, B-1b-1)
```

Constat : l'ordre attendait `b94e247` directement au-dessus de `a3b2a93` ; en réalité un commit de rapport de lot (`5921728`, rapport B-1b-1) s'intercale entre les deux, et un second rapport de lot (`d26ea98`, rapport B-1b-2) est en tête. Empilement néanmoins linéaire et dans le bon ordre : `a3b2a93` (B-1b-1) puis `b94e247` (B-1b-2) au-dessus, comme attendu. Écart signalé, non bloquant (rapports de lot = conséquence normale du process, cf. [[feedback_rapport_lot_template]]).

## C2 — fusion ff-only de la paire

Commande :
```
git checkout feat/setup-conditions
git merge --ff-only fix/b1b2-degrade-explicit-no-broker
```

Sortie brute :
```
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is ahead of 'origin/feat/setup-conditions' by 1 commit.
  (use "git push" to publish your local commits)
Switched to branch 'feat/setup-conditions'
Updating eff5a0a..d26ea98
Fast-forward
 app/engine/position_action_executor.py  |  21 ++-
 app/engine/stop_modification_service.py |  15 ++
 app/engine/trading_engine.py            |  13 +-
 audit/84_rapport_b1b1.md                | 322 ++++++++++++++++++++++++++++++++
 audit/85_rapport_b1b2.md                | 238 +++++++++++++++++++++++
 audit/ORDRE_B1b1.md                     | 105 +++++++++++
 audit/ORDRE_B1b2.md                     |  93 +++++++++
 tests/test_position_action_executor.py  | 137 ++++++++++++--
 tests/test_stop_modification.py         |  42 +++++
 9 files changed, 965 insertions(+), 21 deletions(-)
 create mode 100644 audit/84_rapport_b1b1.md
 create mode 100644 audit/85_rapport_b1b2.md
 create mode 100644 audit/ORDRE_B1b1.md
 create mode 100644 audit/ORDRE_B1b2.md
```

Constat : fast-forward pur, pas de conflit — l'empilement était bien linéaire malgré l'écart signalé au C1. La branche était en avance d'un commit sur `origin/feat/setup-conditions` avant la fusion (commit précédent `eff5a0a`, clôture B-1a, non encore poussé). Les deux lignes `D data/setups/...` sont un état de suppression préexistant dans l'arbre de travail, sans rapport avec le merge. Un seul `git merge --ff-only` a suffi pour intégrer les deux commits de la paire B-1b-1 + B-1b-2 d'un coup.

## C3 — les DEUX commits présents dans feat/setup-conditions

Commande :
```
git log --oneline feat/setup-conditions | grep -E "a3b2a93|b94e247"
```

Sortie brute :
```
b94e247 fix(stop-modification): alert explicitly when stop modification does not reach broker (root B, B-1b-2)
a3b2a93 feat(signals): route RAISE_STOP to broker via StopModificationService (root B, B-1b-1)
```

## C4 — push

Commandes :
```
git push origin feat/setup-conditions
git push -u origin fix/b1b1-route-raisestop-to-broker
git push -u origin fix/b1b2-degrade-explicit-no-broker
```

Sortie brute :
```
To https://github.com/ahmedjaouadi/setup-order.git
   d424bf4..d26ea98  feat/setup-conditions -> feat/setup-conditions

remote:
remote: Create a pull request for 'fix/b1b1-route-raisestop-to-broker' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/b1b1-route-raisestop-to-broker
remote:
branch 'fix/b1b1-route-raisestop-to-broker' set up to track 'origin/fix/b1b1-route-raisestop-to-broker'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/b1b1-route-raisestop-to-broker -> fix/b1b1-route-raisestop-to-broker

remote:
remote: Create a pull request for 'fix/b1b2-degrade-explicit-no-broker' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/b1b2-degrade-explicit-no-broker
remote:
branch 'fix/b1b2-degrade-explicit-no-broker' set up to track 'origin/fix/b1b2-degrade-explicit-no-broker'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/b1b2-degrade-explicit-no-broker -> fix/b1b2-degrade-explicit-no-broker
```

## C5 — les deux commits présents dans origin/feat/setup-conditions

Commande :
```
git fetch origin
git log --oneline origin/feat/setup-conditions | grep -E "a3b2a93|b94e247"
```

Sortie brute :
```
b94e247 fix(stop-modification): alert explicitly when stop modification does not reach broker (root B, B-1b-2)
a3b2a93 feat(signals): route RAISE_STOP to broker via StopModificationService (root B, B-1b-1)
```

## C6 — rien en attente

Commande :
```
git log --oneline origin/feat/setup-conditions..feat/setup-conditions
```

Sortie brute :
```
(vide)
```

## C7 — app/ et tests/ propres

Commande :
```
git status --short -- app/ tests/
```

Sortie brute :
```
(vide)
```

## C8 — note de sécurité : indissociabilité de la paire B-1b-1 + B-1b-2

`a3b2a93` (B-1b-1, seul) route `RAISE_STOP` vers le broker via `StopModificationService`, mais ne traite pas le cas où le broker ne confirme pas la modification (`broker_updated=False`) : ce cas reste silencieux, et le moteur transite vers `MANAGING_POSITION` en croyant le stop déplacé alors qu'il ne l'est pas côté broker. C'est `b94e247` (B-1b-2) qui introduit la dégradation explicite (alerte) quand `broker_updated=False`.

**`a3b2a93` ne doit jamais être déployé isolément sans `b94e247`.** La paire est indissociable, au même titre que :
- `42397b6` + `c7da0ab` (A-1 / A-1b)
- `5f31f85` + `fa4dfdd` (S5b-3)

Cette clôture les a fusionnés et poussés ensemble en une seule opération ff-only (C2), ce qui garantit qu'aucun déploiement intermédiaire ne peut isoler `a3b2a93` seul sur `feat/setup-conditions` ou `origin`.

## Verdicts

| Point | Vérification | Verdict |
|---|---|---|
| C1 | empilement b94e247 au-dessus de a3b2a93 au-dessus de eff5a0a | ÉCART (rapports de lot intercalés, ordre linéaire correct) |
| C2 | fusion ff-only de la paire d'un coup vers feat/setup-conditions | OK |
| C3 | a3b2a93 et b94e247 présents dans feat/setup-conditions | OK |
| C4 | push des trois branches vers origin | OK |
| C5 | a3b2a93 et b94e247 présents dans origin/feat/setup-conditions | OK |
| C6 | rien en attente (local == origin) | OK |
| C7 | app/ et tests/ propres | OK |
| C8 | note de sécurité : paire a3b2a93+b94e247 indissociable, consignée | OK |
