# Module Roadmap

## Prochaine priorite

V2.4 Architecture Stabilization. Ne pas ajouter de nouvelle feature avant reprise des contrats de securite.

- Auditer le modele canonique de setup et aligner les champs de stop vers `trailing_stop_loss.initial_stop`.
- Corriger le generateur de template pour inclure `trailing_stop_loss` racine et supprimer `risk.initial_stop_loss` / `risk.protective_stop` comme champs principaux.
- Ajouter les golden tests du contrat `docs/18-tests-golden-contract.md`.
- Verifier que le validateur, l'armement, le risk engine et l'order manager lisent le stop canonique.
- Renforcer broker reality: positions TWS, ordres TWS, stops actifs, P&L, sync age et blocage si stale.
- Renforcer reconciliation: position sans stop, stop incoherent, ordre inconnu, modification manuelle TWS.
- Aligner la GUI dashboard sur broker reality et `entry_decision`.

## Apres stabilisation V2.4
- Calibrer le Opportunity Scanner sur une campagne paper-trading: valider les seuils `detected/watchlist/weak`, l'impact `DO_NOT_CHASE_EXTENDED_PRICE`, les warnings metadata et les actions recommandees.
- Completer les pages detail `/opportunities/{id}` et `/scenarios/{id}` avec raisons, warnings, snapshot source, scenario draft et decision trace.
- Ajouter les boutons GUI Run scanner, Create setup candidate, Generate scenario, Ignore, Archive et Review depuis la vue Market Context/Radar.
- Implementer le bootstrap Forecast Stack automatique par symbole: creer les fenetres historiques, executer les baselines/modeles disponibles et remplir l'historique d'accuracy en tache de fond.
- Ajouter un classifieur de session complet pour distinguer `RTH_PREVIOUS_DAY`, `PRE_MARKET_CURRENT_DAY`, `RTH_CURRENT_DAY` et les autres sessions dans `entry_decision`.
- Executer une campagne paper-trading suffisamment longue pour calibrer le poids de `forecast_accuracy_scorecards.reliability_grade`.
- Surveiller le drift entre fenetres recentes et historiques avant toute promotion d'un modele.
- Ajouter les boutons GUI Run forecast et Re-score.
- Afficher la derniere decision trace dans les details setup/opportunite/scenario.

## Ensuite
- Executer une matrice de smoke tests avec les environnements optionnels P1/P2/P3 installes et figer les versions validees.
- Etendre le replay MVP avec plusieurs trades et variantes de setup.
- Brancher les scorecards Model Lab dans le poids `forecast_alignment_score`.
- Ajouter des tests API pour `/api/reports/daily/*`.
