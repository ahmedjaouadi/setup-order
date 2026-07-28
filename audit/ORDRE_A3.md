ORDRE DE TRAVAIL — A-3 : détecter et alerter un bracket orphelin au démarrage.
Écris d'abord audit/ORDRE_A3.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 58 S58.1, 66) ═══
- Un crash entre l'upsert de l'ordre d'entrée (order_manager.py:120) et la
  pose du stop (:157-165) laisse une entrée BUY active chez IB sans stop,
  jamais détectée. Le setup reste à son statut PRÉ-ENTRÉE
  (WAITING_ENTRY_SIGNAL ou ENTRY_READY) : l'écriture ENTRY_ORDER_PLACED
  (:180-184) n'a jamais été atteinte.
- protection_snapshot_for_setup (repositories.py:806-811) lit orders/positions
  LOCAL (aucun appel broker) et sait exposer "entrée active sans stop actif".
- La signature "entrée active, aucun stop" ne peut PAS exister en marche
  normale (place_entry_order ne rend pas la main entre les deux) : au
  démarrage seulement, c'est un orphelin certain. Donc détection réservée au
  premier cycle post-start.
- Comportement cible : DÉTECTER + ALERTER (MANUAL_REVIEW_REQUIRED + événement).
  NE PAS retransmettre l'entrée, NE PAS poser de stop, aucun appel broker
  d'écriture.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
  - app/engine/reconciliation.py (nouvelle méthode privée + paramètre startup)
  - app/engine/trading_engine.py (passer startup=True au seul appel de start())
  - tests
INTERDIT :
  - state_machine.py / ALLOWED_TRANSITIONS : NE PAS ajouter de transition.
    A-3 écrit MANUAL_REVIEW_REQUIRED en direct via update_setup_status, comme
    les autres branches de reconciliation.py (A-1, A6-SEC). C'est une écriture
    directe ASSUMÉE, cohérente avec le fichier ; on n'élargit pas la table
    pour un cas unique (décision : ne pas ouvrir d'états atteignables
    nouveaux ; la dette A6 se traite globalement, pas par exception).
  - repositories.py (protection_snapshot_for_setup utilisée telle quelle)
  - order_manager.py, la boucle d'adoption, les branches SELL/SUBMITTED/
    CANCELLED existantes.

═══ 3. CHANGEMENT ═══
(a) Ajouter un paramètre `startup: bool = False` à reconciliation.run().
    Le passer `startup=True` UNIQUEMENT depuis TradingEngine.start() (:324).
    `_reconcile_if_due` et tout autre appelant restent à False.
(b) Nouvelle méthode privée _detect_unprotected_entry_orphans(local_setups),
    appelée depuis run() UNIQUEMENT si startup est True, juste après
    _reconcile_local_orders et avant la boucle d'adoption (~:154-160).
(c) La méthode boucle sur local_setups, et pour chaque setup :
    - SAUTE si son statut ∈ _TERMINAL_SETUP_STATUSES (garde existante).
    - SAUTE si son statut ∈ _REVIEW_LOCKED_SETUP_STATUSES (garde existante,
      idempotence : ne pas ré-alerter un setup déjà en revue à chaque
      redémarrage).
    - appelle protection_snapshot_for_setup(setup_id).
    - Si le snapshot montre une ENTRÉE ACTIVE sans STOP ACTIF :
        * écrire MANUAL_REVIEW_REQUIRED (direct, update_setup_status),
        * émettre un événement, avec deux niveaux de sévérité selon le cas :
          - entrée déjà REMPLIE / position réelle sans stop → EventLevel.CRITICAL
            (message type "Filled entry without protective stop after restart")
          - entrée encore EN ATTENTE sans stop → niveau moindre
            (WARNING/RISK, message type "Pending entry without protective stop
            after restart")
        * distinguer les deux cas via le snapshot (position réelle présente ou
          non). Si le snapshot ne permet pas de distinguer proprement,
          ARRÊTE-TOI et demande plutôt que deviner.
(d) Aucun appel broker ajouté. Aucune retransmission, aucune pose de stop.

═══ 4. INVARIANTS ═══
- En marche normale (startup=False), _detect_unprotected_entry_orphans n'est
  JAMAIS appelée : zéro impact sur les cycles périodiques.
- Aucune transition ajoutée à ALLOWED_TRANSITIONS.
- Aucune écriture de statut ACTIF (donc pas d'interaction avec le garde
  S5b-3a/A-1b ; MANUAL_REVIEW_REQUIRED n'est pas un statut actif).
- Un setup déjà en alarme n'est pas ré-alerté (idempotence via
  _REVIEW_LOCKED_SETUP_STATUSES).
- Les branches existantes de run() et _update_setup_after_reconciled_order
  inchangées.

═══ 5. PREUVE DE SORTIE ═══
1. Orphelin "entrée en attente sans stop" au démarrage → MANUAL_REVIEW_REQUIRED
   + événement de niveau moindre. run(startup=True).
2. Orphelin "position réelle sans stop" au démarrage → MANUAL_REVIEW_REQUIRED
   + événement CRITICAL.
3. Idempotence : deux appels run(startup=True) consécutifs → l'événement
   n'est émis qu'UNE fois (le setup est en alarme au 2e passage, sauté).
4. PAS de faux positif en marche normale : un setup avec entrée active ET
   stop actif → aucune alerte. Un cycle périodique (startup=False) avec un
   orphelin présent → aucune alerte (la détection ne tourne qu'au démarrage).
5. Non-régression : les tests existants de reconciliation (A-1, BUY,
   SUBMITTED, CANCELLED) passent sans modification d'assertion.
6. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/a3-detect-orphan-entry, depuis feat/setup-conditions.
Commit avant rapport. Message :
"feat(reconciliation): detect and flag unprotected entry orphans at startup (root A, S58.1)"

═══ 7. RAPPORT ═══
audit/67_rapport_a3.md selon template + confrontation à ORDRE_A3.md.

═══ 8. INTERDICTIONS ═══
Aucun ajout à ALLOWED_TRANSITIONS. Aucun appel broker d'écriture. Aucun
refactoring, aucune suppression. Doute sur la distinction des deux cas de
sévérité → ARRÊTE-TOI et demande.
