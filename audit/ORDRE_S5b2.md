ORDRE DE TRAVAIL — S5b-2 : cliquet + preuve comportementale du collant des alarmes.
Écris d'abord audit/ORDRE_S5b2.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 35, 36, 39) ═══
- Un statut d'alarme (MANUAL_REVIEW_REQUIRED, ERROR_REQUIRES_MANUAL_REVIEW)
  signifie "un humain doit voir". Il ne doit jamais être écrasé par un
  statut ACTIF (ENTRY_ORDER_PLACED, STOP_ORDER_PLACED, IN_POSITION,
  MANAGING_POSITION, ...) via un mécanisme automatique.
- S5b-1 a bouché le seul chemin automatique connu (branche SUBMITTED de
  reconciliation) et l'a prouvé par SubmittedBranchReviewLockTests.
- disarm_setup (setup_engine.py:277) PEUT quitter une alarme, mais c'est
  une action HUMAINE légitime (audit 36) → sortie autorisée.
- Le cliquet S2 (test_in_position_write_sites.py) est un SCAN TEXTUEL : il
  liste "quel statut cible, quel fichier", il ne peut PAS raisonner sur
  "depuis quel statut" (audit 39). Ne pas l'étendre à du comportemental.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : deux NOUVEAUX fichiers de test uniquement.
INTERDIT : tout fichier de app/. tests/test_in_position_write_sites.py
(ne pas le modifier). Aucune logique de production.
Si tu penses qu'un correctif de app/ est nécessaire, ARRÊTE-TOI et
signale-le : ce lot ne fait que verrouiller l'existant. Un chemin non sûr
découvert est une DETTE à signaler, pas à corriger ici.

═══ 3. CHANGEMENT ═══

(a) CLIQUET TEXTUEL — tests/test_active_status_write_sites.py
    Sur le modèle EXACT de test_in_position_write_sites.py (réutilise son
    approche _find_update_setup_status_calls ; tu peux copier la fonction,
    ce sont deux cliquets indépendants par conception, audit 39).
    Inventorie tous les sites de app/ qui écrivent un statut ACTIF via
    update_setup_status. Définis "statut actif" par un ensemble explicite en
    dur : ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED, ENTRY_FILLED,
    STOP_ORDER_PLACED, STOP_PLACED, IN_POSITION, MANAGING_POSITION,
    PARTIAL_EXIT, RECONCILING_EXISTING_POSITION.
    ALLOWED_ACTIVE_WRITE_SITES : dict fichier -> justification, rempli avec
    les sites RÉELS trouvés aujourd'hui. Le test échoue si un site non listé
    apparaît. Objectif : forcer la revue de tout NOUVEAU site d'écriture de
    statut actif.

(b) PREUVE COMPORTEMENTALE — tests/test_review_status_sticky.py
    Pour CHAQUE site listé dans ALLOWED_ACTIVE_WRITE_SITES qui est un
    chemin AUTOMATIQUE (pas disarm_setup), un test comportemental qui :
      - instancie le vrai Database/TradingRepository/moteur concerné
        (modèle SubmittedBranchReviewLockTests, test_reconciliation.py)
      - pose le setup en MANUAL_REVIEW_REQUIRED, puis en
        ERROR_REQUIRES_MANUAL_REVIEW
      - déclenche le chemin réel
      - vérifie que le statut d'alarme SURVIT
    Si un chemin ne PEUT PAS être atteint depuis un statut d'alarme (garde
    en amont), documente-le dans le test avec la raison, plutôt que de
    forcer un scénario impossible.
    Ajoute AUSSI un test explicite que disarm_setup, LUI, quitte bien
    l'alarme (sortie humaine légitime) — pour documenter que c'est voulu,
    pas un oubli.

═══ 4. INVARIANTS ═══
- Aucun fichier de app/ touché. git diff app/ == vide.
- test_in_position_write_sites.py inchangé.
- Les deux nouveaux cliquets passent sur le code actuel.

═══ 5. PREUVE DE SORTIE ═══
1. Le cliquet textuel passe, et liste les sites trouvés (colle son
   ALLOWED_ACTIVE_WRITE_SITES).
2. Preuve négative du cliquet : injecte temporairement un update_setup_status
   écrivant ENTRY_ORDER_PLACED dans un fichier leurre de app/engine/, montre
   que le cliquet échoue, retire le leurre, montre le diff nul et le
   git status propre.
3. Les tests comportementaux passent. Pour le chemin déjà couvert par S5b-1
   (branche SUBMITTED), tu peux référencer l'existant plutôt que le dupliquer,
   mais assure-toi qu'AU MOINS un test de ce nouveau fichier prouve le
   collant sur un chemin, et que disarm_setup est documenté comme sortie.
4. git diff feat/setup-conditions..HEAD -- app/ : vide. Sortie brute.
5. Suite complète : seul test_account_metrics.py en échec.

═══ 6. DÉCOUVERTE ATTENDUE ═══
En listant les sites (a) et en écrivant les tests (b), tu vas peut-être
trouver un chemin automatique qui PEUT écraser une alarme et n'est pas
gardé (autre que la branche SUBMITTED déjà corrigée). Si c'est le cas :
NE LE CORRIGE PAS. Documente-le en section 8 du rapport comme dette S5b-3,
avec fichier:ligne et le scénario. On en fera un lot séparé.

═══ 7. COMMIT ═══
Branche fix/s5b2-review-sticky-ratchet, depuis feat/setup-conditions.
Commit avant rapport. Message :
"test: ratchet + behavioural proof that review statuses are sticky"

═══ 8. RAPPORT ═══
audit/40_rapport_s5b2.md selon template + confrontation à ORDRE_S5b2.md.

═══ 9. INTERDICTIONS ═══
Aucun refactoring, aucune suppression, aucune correction de app/.
