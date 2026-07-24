# Rapport de lot — <nom>

## 1. Identification
- Lot / ordre de travail : <référence>
- Branche : <nom>  | Commit : <hash complet>
- Basée sur : <branche parente + hash>
- Mergée : oui/non  | Poussée : oui/non

## 2. Fichiers touchés
Sortie brute de `git diff --stat <parent>..<hash>`.
Confronter à la liste autorisée par l'ordre : conforme / écart (lequel).

## 3. Diff du code de production
Diff INTÉGRAL des fichiers de app/ (pas les tests). Si > 150 lignes,
diff intégral des parties nouvelles + résumé des déplacements.

## 4. Décisions prises
Tout choix non spécifié par l'ordre : ce qui a été décidé et pourquoi.
Si aucune : « aucune ».

## 5. Preuves de sortie
Une sous-section par point de preuve exigé par l'ordre, avec la COMMANDE
et sa SORTIE BRUTE. Verdict PASS/FAIL par point.

## 6. Nettoyage (obligatoire)
- fichiers temporaires / leurres / scripts jetables créés pendant le lot :
  liste + preuve de suppression (`git status --short`, `ls`)
- stash créés/poppés
- branches ou worktrees créés/supprimés
- confirmation qu'aucun artefact du lot ne subsiste sur disque ni en commit

## 7. Suite de tests
Sortie brute des 5 dernières lignes de `python -m pytest -q`.
Compte avant → après, et cohérence avec le nombre de tests ajoutés.

## 8. Découvert mais NON corrigé
Tout problème rencontré et volontairement laissé (règle : on signale, on ne
corrige pas hors périmètre). Si aucun : « aucun ».

## 9. Écarts par rapport à l'ordre
Tout point de l'ordre non respecté, ou respecté différemment, avec la
raison. Si aucun : « aucun ».
