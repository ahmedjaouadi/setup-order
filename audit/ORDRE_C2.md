ORDRE DE TRAVAIL — C-2 : lever l'alarme d'adoption quand le stop réapparaît.
Écris d'abord audit/ORDRE_C2.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audit 73, ne pas re-diagnostiquer) ═══
- Un setup alarmé MANUAL_REVIEW_REQUIRED par la boucle d'adoption pour la
  cause « stop broker introuvable » (reconciliation.py:234-238, last_event
  = "Broker stop order not found") reste bloqué À VIE même quand l'humain
  repose le stop dans TWS.
- La boucle d'adoption (reconciliation.py:163-286) tourne à CHAQUE cycle
  (pas seulement au démarrage), retrouve le stop via _matching_stop_order(
  broker_orders, symbol) (:230), tente d'écrire IN_POSITION (:269-273) —
  mais le garde central S5b-3a (repositories.py) BLOQUE ce write car il ne
  passe pas allow_from_review=True. L'alarme survit alors que sa cause a
  disparu (prouvé : test_manual_review_required_survives_existing_position_
  adoption, test_review_status_sticky.py:147-165).
- C'est le SEUL cas réel et atteignable. Les autres causes "stop manquant"
  (#4/#8/#9) sont inatteignables sans une action de repose de stop pour
  position ouverte, inexistante → HORS PÉRIMÈTRE, dette.
- La cause se distingue par last_event (PAS status_reason, faux ami prouvé
  audit 73 Q1 : status_reason sert à un autre sous-système).
- attach_missing_stop (order_manager.py:465-470) est le SEUL site qui passe
  allow_from_review=True aujourd'hui — c'est le patron éprouvé de levée
  d'alarme vérifiée.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/reconciliation.py (la boucle d'adoption, au point où
elle retrouve le stop et tente d'écrire IN_POSITION) + tests.
INTERDIT : repositories.py (le garde S5b-3a et allow_from_review NE changent
pas — on les UTILISE), order_manager.py, state_machine.py, post_fill_
progression.py, les branches d'alarme et de réparation (A-3, C-1).
Si tu penses devoir modifier le garde ou sa signature, ARRÊTE-TOI.

═══ 3. CHANGEMENT ═══
Dans la boucle d'adoption, au point où le stop est retrouvé
(_matching_stop_order renvoie un stop actif) et où le code écrit IN_POSITION :
  - AVANT d'écrire, déterminer si le setup est actuellement en alarme
    review ET si cette alarme est la cause #3 :
      current_status == MANUAL_REVIEW_REQUIRED
      ET last_event du setup == le message exact de la cause #3
         ("Broker stop order not found" — utilise la MÊME constante/chaîne
          que le site :234-238 ; si c'est une chaîne littérale, extrais-la
          en constante partagée pour éviter la divergence, sinon référence-la).
  - SI ces deux conditions sont vraies ET que le stop est bien actif
    (preuve fraîche du cycle courant, _matching_stop_order non nul) :
      écrire IN_POSITION en passant allow_from_review=True à
      update_setup_status. C'est la levée légitime : cause vérifiée disparue,
      preuve fraîche, dérogation tracée.
      Émettre un événement distinct (ex. "adoption_review_cleared_stop_restored",
      niveau SYNC) avec setup_id, symbol, l'id du stop retrouvé.
  - SINON (setup pas en alarme, ou alarme d'une AUTRE cause, ou pas de stop
    frais) : comportement STRICTEMENT INCHANGÉ — on n'écrit pas
    allow_from_review=True, le garde continue de protéger l'alarme comme
    aujourd'hui. Un setup en alarme pour une autre cause n'est JAMAIS levé
    par ce chemin.

═══ 4. INVARIANTS ═══
- Une alarme d'une cause AUTRE que #3 n'est JAMAIS levée par ce lot, même si
  le setup a par ailleurs une position ouverte et un stop actif (audit 73
  Q2 : la géométrie seule n'est pas univoque — le filtre last_event est
  obligatoire).
- allow_from_review=True n'est passé QUE dans ce nouveau cas précis. Le grep
  d'allow_from_review=True dans app/ doit maintenant montrer DEUX sites :
  attach_missing_stop (existant) et celui-ci. Documente-le.
- Le garde S5b-3a (repositories.py) n'est pas modifié : on l'utilise via son
  paramètre existant, on ne l'affaiblit pas.
- Un setup NON alarmé qui adopte une position : comportement inchangé (il
  n'était pas bloqué, il ne passe pas par la nouvelle condition d'alarme).
- Si le stop retrouvé n'est PAS actif (frais) ce cycle, aucune levée.

═══ 5. PREUVE DE SORTIE ═══
1. Le cas réel : setup en MANUAL_REVIEW_REQUIRED avec last_event="Broker stop
   order not found", stop reposé (présent dans broker_orders) → au cycle
   suivant, IN_POSITION écrit, alarme levée, événement émis. C'est
   l'inversion du test existant test_manual_review_required_survives_
   existing_position_adoption : écris le pendant qui prouve la levée.
2. LE TEST CRITIQUE — autre cause NON levée : setup en MANUAL_REVIEW_REQUIRED
   avec last_event d'une AUTRE cause (ex. "Sell filled but broker position
   quantity disagrees") MAIS avec une position ouverte et un stop actif →
   l'alarme SURVIT (pas de levée). Prouve que le filtre last_event protège.
3. Stop non frais : setup en alarme #3 mais aucun stop dans broker_orders ce
   cycle → alarme survit.
4. allow_from_review reste à exactement 2 sites (grep, sortie brute).
5. Non-régression : test_manual_review_required_survives_existing_position_
   adoption doit être ADAPTÉ (il testait la survie inconditionnelle ; il doit
   maintenant tester la survie SANS stop reposé) OU référencé par le nouveau
   test. Si tu modifies une assertion existante, explique-le en §9.
6. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/c2-clear-adoption-alarm, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(reconciliation): clear adoption review alarm when broker stop reappears (root C, S59.3a)"

═══ 7. RAPPORT ═══
audit/74_rapport_c2.md selon template + confrontation à ORDRE_C2.md.

═══ 8. INTERDICTIONS ═══
Aucune modification du garde S5b-3a. allow_from_review=True à ce seul
nouveau site. Aucune levée d'alarme d'une autre cause. Aucun refactoring
hors l'extraction éventuelle de la constante de message #3. Doute → ARRÊTE.
