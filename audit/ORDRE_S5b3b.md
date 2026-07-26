ORDRE DE TRAVAIL — S5b-3b : réparation légitime de stop, protection prouvée.
Écris d'abord audit/ORDRE_S5b3b.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 44/45, ne pas re-diagnostiquer) ═══
- S5b-3a a posé une garde centrale : update_setup_status refuse d'écrire un
  statut ACTIF par-dessus une alarme sauf allow_from_review=True. Effet de
  bord : attach_missing_stop (order_manager.py:466) est désormais bloqué
  quand le setup est en alarme — c'est le chemin de RÉPARATION d'un stop
  manquant, il doit redevenir possible mais seulement si le stop est
  réellement posé.
- attach_missing_stop dispose DÉJÀ de stop_order.status (vérité broker
  directe, retour de place_stop_order). L'écriture ENTRY_ORDER_PLACED
  (:465-470, branche else) n'a lieu que si stop_order.status ∉ {REJECTED,
  ERROR}. Les deux chemins d'échec passent par
  _cancel_parent_for_failed_protection qui écrit ERROR_REQUIRES_MANUAL_REVIEW
  (non-actif, non affecté par la garde).
- ANGLE MORT préexistant (audit 45) : "∉ {REJECTED, ERROR}" inclut aussi
  FILLED et CANCELLED, qui ne sont PAS des stops actifs. On le resserre dans
  ce lot car il conditionne directement l'autorisation qu'on introduit.
- Un seul appelant : POST /api/orders/{id}/attach-stop.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/order_manager.py (attach_missing_stop uniquement) + tests.
INTERDIT : repositories.py (la garde de 3a reste inchangée), tout autre
fichier de app/, les autres méthodes d'order_manager (dont place_stop_order,
_cancel_parent_for_failed_protection : ne pas toucher).
Si tu penses devoir modifier autre chose, ARRÊTE-TOI.

═══ 3. CHANGEMENT ═══
Dans attach_missing_stop, remplacer le branchement actuel par une condition
POSITIVE de protection active :
  - définir "stop actif" = stop_order.status in {CREATED, SUBMITTED}
    (les deux valeurs qu'ailleurs le code — ACTIVE_ORDER_STATUSES,
    repositories.py:285, et le filtre active_stop de cette fonction même —
    traite déjà comme "actif chez le broker").
  - SI stop actif : écrire ENTRY_ORDER_PLACED en passant explicitement
    allow_from_review=True à update_setup_status. C'est la réparation
    légitime : le stop est prouvé posé, on autorise la sortie d'alarme.
  - SINON (REJECTED, ERROR, FILLED, CANCELLED, ou tout autre) : NE PAS
    écrire de statut actif. Router vers _cancel_parent_for_failed_protection
    comme le fait déjà la branche d'échec actuelle (réutiliser le mécanisme
    existant, ne pas en inventer un). FILLED et CANCELLED, aujourd'hui
    traités à tort comme succès, rejoignent ainsi le traitement d'échec —
    c'est le resserrement voulu.
Le commentaire doit expliquer : allow_from_review=True n'est passé QUE parce
que stop_order.status prouve un stop actif ; ce n'est pas un contournement
de la garde mais son exception légitime documentée.

═══ 4. INVARIANTS ═══
- ENTRY_ORDER_PLACED n'est écrit QUE si stop_order.status ∈ {CREATED,
  SUBMITTED}. Jamais sur FILLED/CANCELLED/REJECTED/ERROR.
- allow_from_review=True n'apparaît QU'À ce seul point de tout le code
  (grep : une occurrence dans app/, hors repositories.py qui en définit le
  paramètre). C'est l'unique exception à la garde centrale.
- La garde de 3a (repositories.py) n'est pas modifiée.
- Les chemins REJECTED/ERROR conservent leur comportement (alarme via
  _cancel_parent_for_failed_protection).

═══ 5. PREUVE DE SORTIE ═══
1. Inverser les 2 tests AttachMissingStopReviewStickyTests que 3a avait mis
   à "survit" (le stop y était actif) : ils doivent maintenant prouver que,
   stop ACTIF + setup en alarme, attach_missing_stop écrit bien
   ENTRY_ORDER_PLACED (réparation légitime réussie). Montre le diff des
   assertions.
2. Nouveau test : stop_order.status = REJECTED → alarme préservée, pas
   d'écriture active (comportement inchangé, confirme non-régression).
3. Nouveau test — le resserrement : stop_order.status = CANCELLED (ou
   FILLED) + setup en alarme → PAS de ENTRY_ORDER_PLACED, l'alarme est
   préservée. Prouve que l'angle mort est fermé.
4. Grep prouvant que allow_from_review=True n'apparaît qu'à ce seul endroit
   dans app/. Sortie brute.
5. Les cliquets textuels S2 + S5b-2 passent (order_manager.py:466 reste un
   site connu écrivant un statut actif — vérifie que sa justification dans
   ALLOWED_ACTIVE_WRITE_SITES est à jour, sinon mets-la à jour).
6. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/s5b3b-attach-stop-repair, à partir de fix/s5b3a-central-review-guard
(EMPILÉE sur 3a, PAS sur feat/setup-conditions — les deux seront mergées
ensemble). Commit avant rapport.
Message : "fix(order-manager): allow stop-repair to clear review alarm only when stop is active"

═══ 7. RAPPORT ═══
audit/46_rapport_s5b3b.md selon template + confrontation à ORDRE_S5b3b.md.

═══ 8. INTERDICTIONS ═══
Aucun refactoring hors le branchement décrit, aucune modification de la
garde 3a, aucune suppression. Le resserrement FILLED/CANCELLED est la SEULE
extension de portée autorisée, car il conditionne l'autorisation introduite.
