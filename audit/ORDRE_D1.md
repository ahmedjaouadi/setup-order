ORDRE DE TRAVAIL — D-1 : plafonner l'exposition sur le compte-entier.
Écris d'abord audit/ORDRE_D1.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 87, 88 — ne pas re-diagnostiquer) ═══
- Deux gates d'exposition lisent aujourd'hui UNIQUEMENT les positions locales :
  * TradeGuardsService._exposure_verdict (trade_guards.py:438-563) —
    max_open_positions (exposure, défaut 3), max_total_open_risk_R, secteur,
    groupes corrélés.
  * risk_engine.evaluate via entry_order_executor.py:192-205 —
    max_open_positions (risk, défaut 5), max_total_exposure_usd.
- Deux instances sur le même compte TWS sous-estiment chacune l'exposition
  réelle (S58.6). Le rapport broker_reality en cache
  (repository.get_bot_state("broker_reality")) contient la vérité
  compte-entier, rafraîchie à chaque cycle (~45s) + au démarrage.
- broker_positions_count : nombre de positions compte-entier, lisible direct.
- L'exposition CAPITAL compte-entier n'a pas de clé : la sommer sur report["rows"]
  (average_price × position_quantity, position_quantity != 0). Calcul à écrire.
- max_total_open_risk_R : AUCUN équivalent compte-entier possible (notion de
  config locale par setup) → reste local-seul, documenter comme limite.
- PIÈGE Q4 tranché : le compte-entier INCLUT nos propres positions. NE JAMAIS
  additionner local + broker (double comptage). Utiliser max(local, broker) :
  jamais d'addition, toujours la vue la plus complète.
- DÉCISION (Q2) : rapport ABSENT ou PÉRIMÉ → RETOMBER SUR LE LOCAL
  (comportement actuel, pas de régression), en ÉMETTANT un événement traçant
  que la décision a été prise sans vue compte-entier.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
  - app/engine/trade_guards.py (_exposure_verdict : plafond compte-entier
    sur max_open_positions exposure + max_total_exposure si applicable)
  - app/engine/entry_order_executor.py:192-205 (la source de open_positions/
    exposure passée à risk_engine.evaluate)
  - éventuellement un helper de lecture du rapport (dans broker_reality.py ou
    un module utilitaire) pour extraire (count, capital) compte-entier
  - tests
INTERDIT :
  - risk_engine.py lui-même (on change la DONNÉE qu'on lui passe, pas sa
    logique de comparaison)
  - broker_reality.py au-delà d'un éventuel helper de lecture (NE PAS toucher
    _row_action_and_mismatch : c'est D-2)
  - reconciliation.py, la boucle d'adoption (compter ≠ adopter, audit 87 §3)
  - le verrou (c'est D-3)

═══ 3. CHANGEMENT ═══
(a) Un helper qui lit le rapport broker_reality en cache et retourne
    l'exposition compte-entier : (broker_positions_count, broker_capital_usd),
    avec un indicateur de fraîcheur (frais_et_connecté vs absent/périmé).
    - fraîcheur : le rapport a broker_last_sync_at ET broker_connected ET
      n'est pas stale (réutilise broker_sync_age_seconds/stale_after_seconds
      déjà dans le rapport). Si un critère manque → "non frais".
    - broker_capital_usd = somme sur rows de average_price × position_quantity
      (position_quantity != 0).
(b) Dans les DEUX gates, pour les plafonds qui ont un sens compte-entier
    (nombre de positions ; exposition capital USD) :
    - si le rapport est FRAIS : la base de comparaison = MAX(valeur locale,
      valeur compte-entier). Jamais local + broker.
    - si le rapport est ABSENT/PÉRIMÉ : base = valeur locale seule
      (comportement actuel), ET émettre UN événement
      (ex. exposure_cap_local_fallback, niveau WARNING) indiquant que le
      plafond a été évalué sans vue compte-entier, avec la raison
      (rapport absent / périmé / déconnecté).
    - max_total_open_risk_R : INCHANGÉ (local-seul, pas d'équivalent
      compte-entier). Documenter dans le code.
(c) Le plafonnement ne peut que REFUSER PLUS, jamais moins : si la vue
    compte-entier montre plus de positions/capital que le local, la limite
    est atteinte plus tôt. Il ne doit JAMAIS autoriser une entrée que le
    local seul aurait refusée.

═══ 4. INVARIANTS ═══
- Aucune addition local + broker nulle part. Toujours max(local, broker).
  Prouvé par un test qui ouvrirait un faux positif si une addition avait lieu.
- Rapport frais : un plafond compte-entier > local → entrée refusée plus tôt.
- Rapport absent/périmé : comportement IDENTIQUE à aujourd'hui + événement
  de traçabilité. Aucune entrée nouvellement bloquée par rapport à l'existant
  dans ce cas.
- Le plafond ne relâche JAMAIS une limite (ne peut que refuser plus).
- La boucle d'adoption n'est pas touchée (compter ≠ adopter).
- max_total_open_risk_R reste local-seul.

═══ 5. PREUVE DE SORTIE ═══
1. Rapport frais, compte-entier montre 4 positions, local en montre 2, seuil
   exposure=3 → l'entrée est REFUSÉE (4 > 3), alors que le local seul (2)
   l'aurait autorisée. Le cœur de D-1.
2. PAS de double comptage : local 2 positions, compte-entier 2 (les MÊMES,
   c'est notre seule instance), seuil 3 → entrée AUTORISÉE (max(2,2)=2 < 3),
   PAS refusée. Prouve qu'on ne fait pas 2+2=4. LE TEST CENTRAL de Q4.
3. Exposition capital : compte-entier montre un capital > max_total_exposure_usd,
   local en dessous → refusée. Le calcul de somme sur rows est correct.
4. Rapport absent → base = local seul, comportement identique à aujourd'hui,
   événement exposure_cap_local_fallback émis.
5. Rapport périmé (stale) → idem test 4.
6. Ne relâche jamais : compte-entier montre MOINS que le local (impossible en
   théorie mais garde de sûreté) → on ne prend pas une limite plus laxiste
   que le local (max garantit ça).
7. max_total_open_risk_R inchangé (test de non-régression du plafond R local).
8. Non-régression : les tests existants de trade_guards et du chemin d'entrée
   passent.
9. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/d1-account-wide-exposure-cap, depuis feat/setup-conditions.
Commit avant rapport. Message :
"feat(risk): cap exposure against account-wide broker positions, not just local (root D, D-1)"

═══ 7. RAPPORT ═══
audit/89_rapport_d1.md selon template + confrontation à ORDRE_D1.md.
Documenter la limite max_total_open_risk_R (reste local-seul) et la réserve
TWS (l'hypothèse "reqPositions est compte-entier" n'est pas re-testée en mock).

═══ 8. INTERDICTIONS ═══
Aucune addition local+broker. Ne pas toucher risk_engine.py, la boucle
d'adoption, le verrou. Le plafond ne relâche jamais une limite. Doute → ARRÊTE.
