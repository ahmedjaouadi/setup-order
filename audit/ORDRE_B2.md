ORDRE DE TRAVAIL — B-2 : fiabiliser never_lower_stop par lecture du stop broker.
Écris d'abord audit/ORDRE_B2.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 76 §2.3, 77) ═══
- StopModificationService.modify_stop (stop_modification_service.py) applique
  la garde never_lower_stop (:63-70) en comparant new_stop à _current_stop
  (:122-136), dérivé UNIQUEMENT de valeurs LOCALES (stop_order["stop_price"]
  puis position["current_stop"]), jamais relues au broker. Un stop monté
  côté broker (trailing IB, modif directe dans TWS) laisse le local périmé
  → la garde peut laisser passer une baisse réelle (audit 76 §1.3).
- broker.open_orders() renvoie des BrokerOrderRequest avec stop_price FRAIS,
  même référentiel de prix que le local (audit 77 Q3, aucune conversion).
- Décision produit tranchée : en cas d'indisponibilité broker (timeout /
  déconnexion / exception), la garde N'EST PAS bloquante mais devient
  ASYMÉTRIQUE — voir point 3.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/stop_modification_service.py + tests.
INTERDIT : reconciliation.py, position_manager.py, order_manager.py,
tws_connector.py (open_orders existe déjà, on l'appelle, on ne le modifie
pas), repositories.py. Le bloc de transmission :74 (écriture) NE change PAS —
B-2 ne touche que la LECTURE qui précède la garde.

═══ 3. CHANGEMENT ═══
Avant l'appel à _current_stop (:63), résoudre le stop de référence ainsi :
(a) Tenter de lire le stop réel au broker : await self.broker.open_orders(),
    matcher par symbole le stop SELL actif (même patron que la fonction
    module-level _matching_stop_order, reconciliation.py:946-954). Entourer
    d'un try/except ; traiter exception, timeout, ou absence de connexion
    comme "broker muet".
(b) SI un stop broker frais est trouvé → c'est LA référence de la garde :
    comparer new_stop à broker_stop. C'est le cas nominal, la vérité fraîche.
    (Si new_stop < broker_stop → refus, même REASON_STOP_LOWERING_FORBIDDEN.)
(c) SI le symbole est ABSENT de open_orders (réponse valide = aucun stop
    actif, pas une panne) → repli sur _current_stop local inchangé,
    comportement actuel. Ce n'est pas une dégradation, c'est l'absence
    légitime de stop.
(d) SI le broker est MUET (exception/timeout/déconnecté) → mode DÉGRADÉ
    ASYMÉTRIQUE :
      - référence = MAXIMUM des sources locales disponibles
        (stop_order["stop_price"], position["current_stop"]) — jamais une
        seule, le max, pour ne pas prendre un local périmé bas comme base.
      - AUTORISER la modification UNIQUEMENT si new_stop >= cette référence
        (une MONTÉE ou égalité — geste protecteur, toujours sûr).
      - REFUSER si new_stop < référence (une BAISSE sans vérité broker —
        le seul geste risqué), avec REASON_STOP_LOWERING_FORBIDDEN et un
        data indiquant le mode dégradé.
      - émettre un événement (mécanisme event_store/log déjà présent dans le
        service) signalant que la garde a statué en mode dégradé faute de
        vérité broker : niveau WARNING, avec symbol, new_stop, référence
        locale utilisée, décision (autorisé/refusé).
Ne PAS changer la signature publique de modify_stop. _current_stop peut être
réutilisé tel quel pour le calcul du max local (il renvoie déjà la première
source non nulle ; si nécessaire pour obtenir le MAX, calcule-le localement
sans casser _current_stop pour ses autres usages — ou ajoute un helper privé).

═══ 4. INVARIANTS ═══
- Cas nominal (broker répond, stop trouvé) : la garde compare au stop BROKER
  frais, plus jamais au local seul. C'est l'amélioration de sécurité.
- Broker muet : une MONTÉE de stop passe toujours (protection jamais bloquée
  par une panne réseau) ; une BAISSE est toujours refusée (jamais de perte de
  gain sur donnée périmée).
- Aucune modification n'est BLOQUÉE pour une montée légitime à cause d'une
  indisponibilité broker (on ne casse pas la protection).
- Le bloc de transmission :74 et sa dégradation existante (broker_updated:
  False si déconnecté) restent inchangés.
- Toute décision prise en mode dégradé est tracée par un événement.

═══ 5. PREUVE DE SORTIE ═══
1. LE TEST CENTRAL : broker montre un stop à 20.0 (via modify_stop_order
   direct sur le simulé), local à 18.0, appel modify_stop(19.0) → REFUSÉ
   (19 < 20 broker), alors que la garde AVANT B-2 l'aurait laissé passer
   (19 > 18 local). Prouve que la vérité broker prime.
2. Cas nominal montée : broker à 18, appel à 20 → accepté, transmis.
3. Broker muet + montée : broker.open_orders lève une exception, local max
   à 18, appel à 19 → ACCEPTÉ (montée sûre), événement mode dégradé émis.
4. Broker muet + baisse : même panne, local max à 18, appel à 17 → REFUSÉ,
   événement mode dégradé émis.
5. Broker muet + max local : stop_order local à 18, position à 15, broker
   muet, appel à 17 → REFUSÉ (17 < max(18,15)=18), prouve qu'on prend le max
   pas la première source.
6. Symbole absent d'open_orders (pas de panne) → repli local inchangé,
   comportement identique à aujourd'hui.
7. Non-régression : les tests existants de test_stop_modification.py passent
   (adapter uniquement ceux qui, par construction, supposaient l'ancienne
   comparaison locale — expliquer chaque adaptation en §9).
8. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/b2-broker-verified-stop-guard, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(stop-modification): verify never_lower_stop against live broker stop, degrade asymmetrically"

═══ 7. RAPPORT ═══
audit/78_rapport_b2.md selon template et confrontation à ORDRE_B2.md.

═══ 8. INTERDICTIONS ═══
Aucune modification du bloc de transmission :74, de open_orders, ou de la
signature publique. Aucun refactoring hors le calcul de référence. Doute →
ARRÊTE.
