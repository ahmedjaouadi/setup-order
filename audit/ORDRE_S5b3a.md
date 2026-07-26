ORDRE DE TRAVAIL — S5b-3a : garde centrale anti-effacement d'alarme.
Écris d'abord audit/ORDRE_S5b3a.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 42/43, ne pas re-diagnostiquer) ═══
- Trois chemins écrivent un statut ACTIF par-dessus une alarme sans la lire :
  cascade simulate_fill (post_fill_progression.py:64/:92, order_manager.py:374),
  boucle d'adoption (reconciliation.py:263, MANUAL_REVIEW_REQUIRED seulement).
- Cause commune : update_setup_status écrit sans lire le statut courant.
- upsert_setup est un 2e chemin d'écriture MAIS prouvablement inatteignable
  vers un statut actif depuis une alarme (audit 43 Q1) — à figer par cliquet,
  pas par garde.
- attach_missing_stop (order_manager.py:466) est une sortie potentiellement
  LÉGITIME → traité séparément en S5b-3b, PAS dans ce lot.
- repositories.py n'importe que app.models. NE PAS importer app.engine.*.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
  - app/storage/repositories.py (garde dans update_setup_status +
    ensembles locaux)
  - tests
INTERDIT :
  - app/engine/* (aucune modification ; les 3 chemins fautifs seront
    protégés PAR la garde centrale, pas modifiés un par un)
  - attach_missing_stop et order_manager.py:466 : NE PAS toucher (S5b-3b)
  - tout import de app.engine dans repositories.py
Si tu penses devoir modifier un fichier de app/engine, ARRÊTE-TOI : la
garde centrale doit suffire. Si elle ne suffit pas pour un chemin, signale-le
en dette, ne le corrige pas ici.

═══ 3. CHANGEMENT ═══
(a) Dans repositories.py, définir deux frozenset locaux à partir de
    app.models.SetupStatus (ajouter l'import de SetupStatus si absent) :
    - _REVIEW_ALARM_STATUSES = {MANUAL_REVIEW_REQUIRED,
      ERROR_REQUIRES_MANUAL_REVIEW}
    - _ACTIVE_STATUSES = {ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED,
      ENTRY_FILLED, STOP_ORDER_PLACED, STOP_PLACED, IN_POSITION,
      MANAGING_POSITION, PARTIAL_EXIT, RECONCILING_EXISTING_POSITION}
    (valeurs .value, cohérent avec le stockage)
(b) Ajouter à update_setup_status un paramètre mot-clé
    allow_from_review: bool = False.
(c) Garde, AVANT l'UPDATE : lire le statut courant du setup (une lecture
    SQL locale ; setup_id et self.database sont disponibles). Si le statut
    courant ∈ _REVIEW_ALARM_STATUSES ET le statut cible ∈ _ACTIVE_STATUSES
    ET allow_from_review est False :
      - NE PAS écrire,
      - émettre une trace (le mécanisme de log déjà présent dans
        repositories.py ; s'il n'y en a pas, un logger.warning — PAS un
        event_store, repositories ne doit pas dépendre de l'engine),
      - retourner sans lever d'exception (comportement identique à
        "écriture ignorée", cohérent avec la branche SUBMITTED de S5b-1 qui
        s'abstient sans lever).
    Sinon : comportement inchangé.
(d) Ne PAS passer allow_from_review=True depuis aucun appelant dans ce lot.
    (attach_missing_stop viendra en S5b-3b.)

═══ 4. INVARIANTS ═══
- non-alarme → actif : comportement STRICTEMENT inchangé.
- alarme → actif (sans flag) : bloqué, statut préservé, trace émise.
- alarme → non-actif (CANCELLED, ERROR, DISABLED, alarme→alarme) : inchangé
  (ces cibles ne sont pas dans _ACTIVE_STATUSES).
- Aucun fichier de app/engine touché. git diff app/engine == vide.
- repositories.py n'importe toujours pas app.engine.

═══ 5. PREUVE DE SORTIE ═══
1. INVERSION des tests GAP existants : les tests de
   test_review_status_sticky.py qui prouvaient AUJOURD'HUI l'effacement
   (cascade simulate_fill → IN_POSITION ; adoption → IN_POSITION pour MRR)
   doivent maintenant prouver la SURVIE de l'alarme. Inverse l'assertion
   (constante cible → statut d'alarme d'origine), NE touche PAS les fixtures.
   Montre le diff de ces assertions.
2. Test direct de la garde : update_setup_status(setup en
   MANUAL_REVIEW_REQUIRED, cible IN_POSITION, sans flag) → statut reste
   MANUAL_REVIEW_REQUIRED, trace émise. Idem depuis
   ERROR_REQUIRES_MANUAL_REVIEW.
3. Test de non-régression : update_setup_status(setup en
   ENTRY_ORDER_PLACED, cible IN_POSITION) → passe normalement (non-alarme).
4. Test du flag : update_setup_status(setup en alarme, cible active,
   allow_from_review=True) → écrit (prouve que l'échappatoire fonctionne,
   même si aucun appelant ne l'utilise encore).
5. CLIQUET upsert_setup (fige l'invariant de l'audit 43 Q1) : un test qui
   pose un setup en MANUAL_REVIEW_REQUIRED, appelle le chemin
   create_or_update_from_config / upsert_setup (édition de config), et
   vérifie que le statut reste MANUAL_REVIEW_REQUIRED. Documente qu'il
   protège contre une régression future de _status_after_config_save.
6. Les cliquets textuels S2 et S5b-2 passent toujours.
7. Suite complète : seul test_account_metrics.py en échec.

═══ 6. DÉCOUVERTE ATTENDUE ═══
Si un 4e chemin d'effacement apparaît (un site qui écrit alarme→actif que
la garde bloque et qui devrait légitimement passer), NE le corrige pas :
signale-le en §8 comme candidat au flag pour S5b-3b.

═══ 7. COMMIT ═══
Branche fix/s5b3a-central-review-guard, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(repository): block active-status writes over review alarms centrally"

═══ 8. RAPPORT ═══
audit/44_rapport_s5b3a.md selon template + confrontation à ORDRE_S5b3a.md.

═══ 9. INTERDICTIONS ═══
Aucun refactoring, aucune suppression, aucune modification de app/engine,
aucun import de app.engine dans repositories.py.
