ORDRE DE TRAVAIL — B-1b-2 : dégradation explicite quand le stop n'atteint pas le broker.
Écris d'abord audit/ORDRE_B1b2.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 83 Q3/Q4, rapport B-1b-1 §8) ═══
- StopModificationService.modify_stop retourne aujourd'hui ok=True avec
  broker_updated=False quand le bloc broker est sauté (stop_modification_service.py:105,
  `if broker_order_id and await self._broker_is_connected()`). Deux causes
  convergent sur cette même ligne : (a) broker_order_id=None (stop entré
  manuellement dans TWS, orderId=0, transmis par B-1a) ; (b) broker
  déconnecté.
- Conséquence : l'appelant voit ok=True et croit le stop modifié, alors qu'il
  n'a pas bougé chez IB. Sur le chemin RAISE_STOP (B-1b-1), la transition vers
  MANAGING_POSITION aurait lieu à tort. Le champ broker_updated=False existe
  mais est noyé dans data, au niveau RISK (même niveau qu'un succès).
- Cible : une alerte EXPLICITE et distincte quand stop_order existe mais
  broker_updated est resté False — pour TOUS les appelants de modify_stop
  (RAISE_STOP ET chemin manuel routes_positions.py), pas seulement RAISE_STOP.
- La garde B-2 (never_lower_stop) et sa dégradation asymétrique restent
  INCHANGÉES : ce lot ne touche que la traçabilité du cas "broker non atteint",
  pas la logique de décision de la garde.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/stop_modification_service.py + tests.
INTERDIT : position_action_executor.py (B-1b-1, déjà fait), le bloc de garde
never_lower_stop (:91-101) et _resolve_stop_guard_reference (B-2, inchangés),
routes_positions.py, tws_connector.py (le fallback permId est un correctif
compagnon FUTUR, PAS ce lot).

═══ 3. CHANGEMENT ═══
Dans modify_stop, après le bloc broker (~:114, là où broker_updated a sa
valeur finale), AVANT le retour :
  - si stop_order is not None ET broker_updated est False :
    émettre un événement DISTINCT (event_type ex. "stop_modification_local_only",
    niveau WARNING ou CRITICAL — choisis WARNING sauf si tu justifies CRITICAL),
    signalant que la modification n'a PAS été confirmée au broker et que le
    stop local peut diverger du stop réel. Inclure dans data : symbol,
    new_stop, la cause distinguable si possible (broker_order_id is None →
    "no_broker_order_id" ; sinon déconnexion → "broker_disconnected"), et
    broker_updated=False.
  - NE PAS changer la valeur de retour ok : un rejet de garde (never_lower_stop)
    reste ok=False ; un "modifié en local seulement" reste ok=True MAIS
    l'événement rend le cas visible et actionnable. (Le fait que la transition
    de statut doive ou non avoir lieu dans ce cas est une question B-1b-1 déjà
    tranchée : B-1b-1 transite sur ok=True. Ce lot NE modifie PAS ce contrat —
    il rend seulement le cas traçable. Si tu penses que la transition ne
    devrait pas avoir lieu quand broker_updated=False, SIGNALE-le en §8 comme
    dette, ne le corrige pas ici.)
  - distinguer proprement du cas "pas de stop_order du tout" (stop_order is
    None) qui n'est pas une dégradation broker mais une absence légitime de
    cible — ne PAS émettre l'alerte dans ce cas.

═══ 4. INVARIANTS ═══
- Un modify_stop qui atteint le broker (broker_updated=True) : aucun
  changement, aucun événement nouveau.
- Un modify_stop avec stop_order présent mais broker non atteint
  (orderId=None OU déconnecté) : événement stop_modification_local_only émis,
  pour TOUS les appelants.
- Un modify_stop sans stop_order (pas de cible) : comportement inchangé,
  pas d'alerte de dégradation.
- La garde B-2 et sa dégradation asymétrique : STRICTEMENT inchangées.
- La valeur de retour ok : inchangée par ce lot.

═══ 5. PREUVE DE SORTIE ═══
1. broker_order_id=None + stop_order présent → modify_stop émet
   stop_modification_local_only avec cause "no_broker_order_id",
   broker_updated=False dans data. C'est le cas orderId=0 de B-1a.
2. broker déconnecté + stop_order présent → même événement, cause
   "broker_disconnected".
3. broker atteint (broker_updated=True) → PAS d'événement de dégradation.
4. pas de stop_order (cible absente) → PAS d'événement de dégradation
   (distinct du cas dégradé).
5. La garde never_lower_stop fonctionne comme avant (non-régression B-2) :
   un new_stop sous la référence est toujours rejeté ok=False, indépendamment
   de ce lot.
6. Bout-en-bout avec B-1b-1 : un RAISE_STOP sur un stop orderId=None →
   modify_stop retourne ok=True MAIS l'événement stop_modification_local_only
   est émis ; documente que la transition a lieu (contrat B-1b-1) et que
   l'alerte est désormais présente pour la rendre visible.
7. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/b1b2-degrade-explicit-no-broker, EMPILÉE sur
fix/b1b1-route-raisestop-to-broker (PAS sur feat/setup-conditions — la paire
se merge ensemble). Commit avant rapport. Message :
"fix(stop-modification): alert explicitly when stop modification does not reach broker (root B, B-1b-2)"

═══ 7. RAPPORT ═══
audit/85_rapport_b1b2.md selon template + confrontation à ORDRE_B1b2.md.

═══ 8. INTERDICTIONS ═══
Ne pas modifier la garde B-2 ni sa dégradation. Ne pas changer la valeur de
retour ok. Ne pas toucher le fallback permId (correctif futur). Aucune
suppression. Doute → ARRÊTE.
