ORDRE DE TRAVAIL — B-1b-1 : router RAISE_STOP vers le broker.
Écris d'abord audit/ORDRE_B1b1.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 76, 83 — ne pas re-diagnostiquer) ═══
- RAISE_STOP est dispatché par execute_raise_stop_signal
  (position_action_executor.py:28-40), qui appelle self.move_stop (:37) →
  PositionActionExecutor.move_stop (:42-48) → PositionManager.raise_stop
  (LOCAL, aucun appel broker).
- La cible : router vers StopModificationService.modify_stop(symbol, new_stop)
  (le chemin broker, déjà alimenté par la ligne orders de B-1a). Signature
  attendue = (setup["symbol"], signal.new_stop), exactement les valeurs déjà
  extraites à :37. AUCUN écart de données.
- Trois adaptations MÉCANIQUES (audit 83 Q2) : (a) execute_raise_stop_signal
  devient async, son appel trading_engine.py:2471 devient await ; (b)
  PositionActionExecutor gagne une dépendance stop_modification_service
  (constructeur :209-214, l'objet existe déjà trading_engine.py:215-221) ;
  (c) le retour passe de bool à dict — lire result["ok"] avant de décider
  transition_setup.
- La garde B-2 (never_lower_stop) vit DANS modify_stop, avant l'appel broker :
  B-1b-1 en hérite automatiquement (positif, ne rien ajouter).
- RAISE_STOP est le SEUL appelant de production de PositionActionExecutor.
  move_stop : elle devient code mort après reroutage. NE PAS la supprimer dans
  ce lot (signaler en dette).

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
  - app/engine/position_action_executor.py (execute_raise_stop_signal :
    async + reroutage + lecture result["ok"] ; move_stop laissée en place)
  - app/engine/trading_engine.py (constructeur PositionActionExecutor :
    injecter stop_modification_service ; await à :2471)
  - tests
INTERDIT :
  - app/engine/stop_modification_service.py (c'est B-1b-2 ; la dégradation
    explicite orderId=None/déconnexion N'EST PAS dans ce lot)
  - PositionManager.raise_stop (la primitive locale reste inchangée ; elle a
    un autre appelant légitime dans modify_stop:119)
  - les evaluate() des setups (RunnerBaseSetup, PositionManagementSetup) :
    ils émettent le signal, ils ne connaissent pas le service — INCHANGÉS
  - routes_positions.py (chemin manuel, hors périmètre)

═══ 3. CHANGEMENT ═══
(a) execute_raise_stop_signal (position_action_executor.py:28-40) :
    - devient async def.
    - filtre signal.action != RAISE_STOP → retourne False (inchangé).
    - au lieu de self.move_stop(...), appeler :
      result = await self.stop_modification_service.modify_stop(
          setup["symbol"], signal.new_stop)
    - lire result["ok"] : si True → transition_setup vers signal.target_status
      (comme aujourd'hui) ; si False → NE PAS transiter, retourner True
      (signal traité, mais pas de transition — le stop n'a pas bougé).
      Émettre un événement si le service a rejeté (ex. raise_stop_rejected,
      avec la raison de result) pour tracer pourquoi la transition n'a pas eu
      lieu.
    - retourner True (signal RAISE_STOP consommé dans tous les cas où
      action == RAISE_STOP).
(b) PositionActionExecutor.__init__ (:209-214) : ajouter le paramètre
    stop_modification_service. Le passer depuis trading_engine.py (l'objet
    est construit :215-221 ; s'il est construit APRÈS PositionActionExecutor,
    réordonner la construction — sans casser d'autres dépendances ; si
    réordonner est risqué, ARRÊTE-TOI et signale).
(c) trading_engine.py:2471 : await execute_raise_stop_signal(...).

═══ 4. INVARIANTS ═══
- Un RAISE_STOP atteint désormais modify_stop (broker), plus jamais
  seulement le local. Prouvé par test (spy sur le service).
- La transition de statut (→ MANAGING_POSITION) n'a lieu QUE si
  result["ok"] est True. Un rejet du service (never_lower_stop via B-2,
  marché fermé, etc.) → pas de transition. Prouvé par test.
- Les evaluate() des setups sont strictement inchangés.
- PositionManager.raise_stop inchangée ; son appel dans modify_stop:119
  n'est pas affecté.
- Aucune écriture de statut nouvelle hors la transition existante.
- move_stop local laissée en place (code mort documenté), pas supprimée.

═══ 5. PREUVE DE SORTIE ═══
1. LE TEST CENTRAL : un RAISE_STOP émis → modify_stop du service est appelé
   avec (symbol, new_stop) corrects (spy/mock), PAS PositionManager.raise_stop
   en direct via move_stop. Prouve le reroutage.
2. result ok=True → transition vers MANAGING_POSITION a lieu.
3. result ok=False (le service rejette, ex. never_lower_stop) → PAS de
   transition, événement de rejet émis, le signal est quand même consommé
   (retourne True).
4. La garde B-2 héritée : un RAISE_STOP avec new_stop < stop broker →
   modify_stop le rejette (B-2), donc pas de transition (recoupe test 3).
   Prouve que B-1b-1 bénéficie de B-2 sans code ajouté.
5. Non-régression : les tests de position_action_executor adaptés au async
   (les 3 appels synchrones signalés par l'audit 83 Q2) — expliquer chaque
   adaptation en §9. Les tests des evaluate() inchangés.
6. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/b1b1-route-raisestop-to-broker, depuis feat/setup-conditions.
Commit avant rapport. Message :
"feat(signals): route RAISE_STOP to broker via StopModificationService (root B, B-1b-1)"

═══ 7. RAPPORT ═══
audit/84_rapport_b1b1.md selon template + confrontation à ORDRE_B1b1.md.
Documenter : move_stop local devenue code mort (dette) ; réserve TWS
(throttle au tick) ; le fait que B-1b-2 suit et que la paire ne doit pas
être déployée séparément (B-1b-1 seul expose le mensonge broker_updated=False).

═══ 8. INTERDICTIONS ═══
Ne pas toucher StopModificationService (B-1b-2). Ne pas supprimer move_stop.
Ne pas modifier les evaluate(). Aucune suppression. Doute sur l'ordre de
construction des dépendances → ARRÊTE.
