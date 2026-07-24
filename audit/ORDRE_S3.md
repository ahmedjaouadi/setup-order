CONTEXTE FIGÉ (audit 26, Q2) : le gate current_status est prouvé BLOQUANT
uniquement pour range_breakout et breakout_retest. Aucun test de blocage
n'existe pour momentum_breakout, pullback_continuation, aggressive_rebound.
Le mécanisme est agnostique au type par lecture de code, mais ce n'est pas
une preuve.

PÉRIMÈTRE :
  AUTORISÉ : tests/test_entry_gate_current_status.py uniquement.
  INTERDIT : tout fichier de app/. Aucun code de production ne doit changer.
  Si tu penses qu'une modification de app/ est nécessaire, ARRÊTE-TOI.

CHANGEMENT : paramétrer le test de blocage par setup_type, sur le modèle du
test nominal déjà paramétré (test_nominal_entry_transmitted_for_each_setup_type).
Couverture cible : 5 setup_types × les 9 statuts post-entrée
(ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, STOP_ORDER_PLACED,
STOP_PLACED, IN_POSITION, MANAGING_POSITION, PARTIAL_EXIT,
RECONCILING_EXISTING_POSITION).
Réutilise le harness et le patron de paramétrage existants ; n'en crée pas
un second.

INVARIANTS :
  - aucune assertion existante modifiée
  - aucun fichier de app/ touché
  - pour chaque combinaison : aucun ordre transmis + événement
    entry_gate_blocked émis

PREUVE DE SORTIE :
  1. Les 45 combinaisons (5 × 9) passent. Donne la sortie brute du fichier.
  2. Preuve négative : retire temporairement UN type de la liste blanche
     ENTRY_ELIGIBLE_STATUSES... NON — ne touche pas app/. À la place :
     montre que le test échoue si on retire une assertion clé, ou explique
     comment tu prouves que le test discrimine réellement.
  3. Suite complète : seul test_account_metrics.py en échec.

COMMIT : branche fix/s3-gate-all-types, à partir de feat/setup-conditions
(après l'étape 0). Commit AVANT de rapporter.
Message : "test: prove entry gate blocks all setup types on post-entry status"

RAPPORT : audit/30_rapport_s3.md selon audit/TEMPLATE_rapport_lot.md, avec
en plus une section confrontant chaque point de audit/ORDRE_S3.md.

INTERDICTIONS : aucun refactoring, aucune suppression de branche/stash/
fichier, aucune correction hors périmètre.
