ORDRE DE TRAVAIL — S5b-1 : rendre MANUAL_REVIEW_REQUIRED et
ERROR_REQUIRES_MANUAL_REVIEW collants face à la réconciliation automatique.

Écris d'abord cet ordre dans audit/ORDRE_S5b1.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 35 et 36, ne pas re-diagnostiquer) ═══
- reconciliation.py, branche SUBMITTED (lignes 456-470) : écrit
  ENTRY_ORDER_PLACED (side BUY) ou STOP_ORDER_PLACED (side SELL) par-dessus
  le statut du setup, si ce statut est dans un ensemble de 7 valeurs
  incluant MANUAL_REVIEW_REQUIRED (ligne 462-464) et
  ERROR_REQUIRES_MANUAL_REVIEW (via _TERMINAL_SETUP_STATUSES, ligne 624).
- Ce mécanisme a effacé une alarme 4 fois en production le 29/06.
- Le lot 3b-2 écrit MANUAL_REVIEW_REQUIRED comme alarme "filled without
  active protective stop" : un statut disant "position peut-être nue, un
  humain doit voir". La branche SUBMITTED peut l'écraser par
  "stop protecteur soumis" — l'inverse de la vérité.
- Cette branche SUBMITTED est la SEULE branche de
  _update_setup_after_reconciled_order qui n'émet AUCUN event_store.record
  (audit 35) — d'où l'invisibilité de l'effacement du 29/06.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/reconciliation.py + tests.
INTERDIT : state_machine.py, setup_engine.py (disarm_setup traité
séparément), toute autre logique de app/. Les branches FILLED et CANCELLED
de la même fonction : NE PAS les toucher.

═══ 3. CHANGEMENT ═══
(a) Retirer MANUAL_REVIEW_REQUIRED et ERROR_REQUIRES_MANUAL_REVIEW de
    l'ensemble des statuts depuis lesquels la branche SUBMITTED écrit.
    Concrètement : la condition ligne 462-464 ne doit plus être vraie quand
    le setup est dans l'un de ces deux statuts d'alarme. Les 5 autres
    statuts de _TERMINAL_SETUP_STATUSES (CLOSED, CANCELLED, EXPIRED,
    INVALIDATED, ERROR) conservent EXACTEMENT le comportement actuel :
    la "restauration depuis TWS" reste possible pour eux.
    Attention : _TERMINAL_SETUP_STATUSES est peut-être utilisé ailleurs.
    Vérifie ses autres usages (grep) AVANT de le modifier. Si ce set sert
    à autre chose, NE le modifie PAS : construis plutôt l'ensemble effaçable
    localement dans la branche SUBMITTED, sans ERROR_REQUIRES_MANUAL_REVIEW
    ni MANUAL_REVIEW_REQUIRED. Le choix entre les deux approches dépend de
    ce grep — si tu hésites, ARRÊTE-TOI et demande.
(b) Rendre cette branche traçable : quand la branche SUBMITTED s'abstient
    d'écrire PARCE QUE le setup est en alarme (le nouveau cas), émettre un
    event_store.record de niveau INFO (ex. "reconciliation_skipped_review_locked")
    avec setup_id, symbol, le statut d'alarme préservé, et l'ordre concerné.
    Réutilise le patron event_store.record déjà présent dans les autres
    branches de ce fichier. NE crée pas un nouveau système de log.

═══ 4. INVARIANTS ═══
- Pour les 5 statuts terminaux non-alarme : comportement IDENTIQUE à avant.
- Pour MANUAL_REVIEW_REQUIRED et ERROR_REQUIRES_MANUAL_REVIEW : la branche
  SUBMITTED ne les écrase PLUS JAMAIS, quel que soit le side.
- Aucune autre branche modifiée. Aucune écriture via state_machine ajoutée
  (S5b ne migre pas vers state_machine ; c'est le sujet global A6/S6).
- Les tests existants de test_reconciliation.py passent sans modification
  d'assertion.

═══ 5. PREUVE DE SORTIE ═══
1. Test : setup en MANUAL_REVIEW_REQUIRED + ordre SELL rapporté SUBMITTED
   par le broker → le statut RESTE MANUAL_REVIEW_REQUIRED (pas
   STOP_ORDER_PLACED), et l'event reconciliation_skipped_review_locked est
   émis. C'est le cœur du correctif : le scénario du 29/06 appliqué à
   l'alarme 3b-2.
2. Test : idem depuis ERROR_REQUIRES_MANUAL_REVIEW + ordre BUY → statut
   préservé.
3. Test de non-régression : setup en CLOSED (ou autre terminal non-alarme)
   + ordre SUBMITTED → comportement inchangé (restauration toujours
   possible, event de restauration comme avant).
4. Preuve négative : mute l'assertion la plus spécifique du test 1 (le
   statut final == MANUAL_REVIEW_REQUIRED) et montre l'échec, puis reverte,
   diff nul.
5. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/s5b1-sticky-review-status, depuis feat/setup-conditions.
Commit AVANT rapport. Message :
"fix(reconciliation): never overwrite manual-review status on order restore"

═══ 7. RAPPORT ═══
audit/37_rapport_s5b1.md selon le template, confrontation littérale à
audit/ORDRE_S5b1.md.

═══ 8. INTERDICTIONS ═══
Aucun refactoring, aucune suppression de branche/stash/fichier, aucune
correction hors périmètre. Le point 3(a) contient un embranchement décidé
par grep : si le résultat est ambigu, tu t'ARRÊTES et tu demandes.
