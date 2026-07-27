ORDRE DE TRAVAIL — A-1 : clôturer une position sur fill SELL.
Écris d'abord audit/ORDRE_A1.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 61/62, ne pas re-diagnostiquer) ═══
- reconciliation.py, branche FILLED, ligne 505 : `if side != "BUY": return`
  ignore tout fill SELL (T1). On ajoute une branche SELL symétrique.
- open_or_update_position (position_manager.py:21-48) sait écrire OPEN/CLOSED
  selon la quantité restante, et notifie le circuit-breaker via
  _notify_if_closed. Elle n'est appelée nulle part aujourd'hui.
- ReconciliationEngine.__init__ ne reçoit PAS position_manager : il faut
  l'injecter (constructeur + site trading_engine.py:170-175).
- _resolve_fill_details branche 1 (exécutions appariées) fonctionne pour un
  SELL sans adaptation. Sa branche 2 (repli broker) NE doit PAS servir pour
  un SELL (rendrait un coût d'entrée, pas un prix de vente).
- La quantité RESTANTE = quantité de la position locale précédente moins
  quantité vendue (calcul local, pas broker_positions).
- Décision actée : l'ALARME PRIME. CLOSED doit être ajouté à _ACTIVE_STATUSES
  pour que la garde S5b-3a protège une alarme contre l'écrasement par une
  clôture (c'est le sous-lot A-1b, séparé — PAS dans A-1).

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
  - app/engine/reconciliation.py (branche SELL + injection position_manager)
  - app/engine/trading_engine.py (site d'instanciation, passage du paramètre
    UNIQUEMENT)
  - tests
INTERDIT :
  - repositories.py (S5b-3a et _ACTIVE_STATUSES : c'est A-1b, pas A-1)
  - position_manager.py (open_or_update_position est utilisée telle quelle,
    pas modifiée)
  - la branche BUY existante, les branches SUBMITTED/CANCELLED
  - _resolve_fill_details (utilisée telle quelle ; si tu penses devoir la
    modifier, ARRÊTE-TOI)
  - order_manager.py, tout app/setups/

═══ 3. CHANGEMENT ═══
(a) Injecter position_manager dans ReconciliationEngine :
    - ajouter le paramètre au constructeur (reconciliation.py:47-58)
    - le passer au site trading_engine.py:170-175
    (position_manager est déjà construit trading_engine.py:165-169 avec son
    callback on_position_closed câblé au circuit-breaker)
(b) Dans la branche FILLED, remplacer `if side != "BUY": return` par un
    aiguillage : BUY → comportement actuel inchangé ; SELL → nouvelle logique :
    1. résoudre (quantité vendue, prix de vente) via _resolve_fill_details.
       Si (None, None) → MANUAL_REVIEW_REQUIRED avec event, comme le fait
       déjà le cas BUY à :525-544 (réutilise ce patron, ne l'invente pas).
       Utiliser UNIQUEMENT le résultat de la branche 1 (exécutions
       appariées) ; ne pas dépendre du repli broker pour un SELL.
    2. lire la position locale précédente (repository.get_position(symbol)).
       Si absente → MANUAL_REVIEW_REQUIRED (incohérence : vente sans position
       connue).
    3. quantité restante = previous.quantity − quantité vendue.
       RECOUPEMENT : si broker_positions contient ce symbole avec une
       quantité, et qu'elle diffère de la quantité restante calculée →
       MANUAL_REVIEW_REQUIRED (ne pas écrire une clôture douteuse).
    4. appeler open_or_update_position(setup_id, symbol,
       quantity=quantité_restante, average_price=previous.average_price,
       current_price=PRIX_DE_VENTE_RÉEL, stop_loss=...).
       POINT CRITIQUE (audit 62 Q3) : current_price DOIT être le prix de
       vente réel résolu en 1, PAS un cours de marché générique — c'est lui
       qui alimente le PnL réalisé du circuit-breaker. Commente cette ligne
       pour l'expliquer.
    5. faire évoluer le statut du setup : quantité restante == 0 → CLOSED ;
       > 0 → PARTIAL_EXIT. Via update_setup_status (écriture directe, comme
       le reste du fichier ; NE PAS introduire state_machine ici).
    6. émettre un événement de clôture (position_closed_on_sell) avec
       setup_id, symbol, quantité vendue, prix de vente, PnL, quantité
       restante — mécanisme event_store déjà présent dans le fichier.

═══ 4. INVARIANTS ═══
- La branche BUY et les branches SUBMITTED/CANCELLED : comportement
  strictement inchangé.
- Aucun appel broker ajouté (A-1 réagit à un fill déjà survenu, il n'émet
  pas d'ordre).
- current_price passé à open_or_update_position == prix de vente réel du
  fill (jamais un cours générique). Prouvé par test.
- Pour un PARTIAL_EXIT, average_price reste le coût d'entrée d'origine
  (previous.average_price), jamais le prix de vente (audit 62 Q3 second
  point).
- CLOSED n'est PAS encore ajouté à _ACTIVE_STATUSES (c'est A-1b) : dans A-1,
  la garde ne protège pas encore l'alarme contre l'écrasement par CLOSED —
  documenter que A-1b suit immédiatement et que A-1 ne doit pas être déployé
  seul sur un système où des alarmes coexistent avec des clôtures.

═══ 5. PREUVE DE SORTIE ═══
1. Fill SELL total (quantité vendue == position) → statut CLOSED, position
   à quantity=0, event émis.
2. Fill SELL partiel → statut PARTIAL_EXIT, position à quantité restante > 0,
   average_price INCHANGÉ (coût d'entrée).
3. LE TEST CRITIQUE : vérifier que le PnL transmis au circuit-breaker
   (on_position_closed) est calculé sur le PRIX DE VENTE RÉEL. Monter un cas
   entrée à 100, vente à 90, quantité 10 → realized_pnl == -100.0 exactement.
   Si le code passait un cours générique, ce test échouerait.
4. Recoupement broker incohérent → MANUAL_REVIEW_REQUIRED, pas de clôture.
5. Exécutions non résolues (None, None) → MANUAL_REVIEW_REQUIRED.
6. Non-régression : les tests existants de la branche BUY et des branches
   SUBMITTED/CANCELLED passent sans modification d'assertion.
7. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/a1-close-on-sell, depuis feat/setup-conditions.
Commit AVANT rapport. Message :
"feat(reconciliation): close position on real SELL fill (root cause A, T1)"

═══ 7. RAPPORT ═══
audit/63_rapport_a1.md selon template et confrontation à audit/ORDRE_A1.md.

═══ 8. INTERDICTIONS ═══
Aucun refactoring hors la branche SELL et l'injection. Ne touche pas à
_ACTIVE_STATUSES (A-1b). Aucune suppression. Doute → tu t'ARRÊTES.
