ORDRE DE TRAVAIL — B-1a : persister le stop broker adopté en ligne orders locale.
Écris d'abord audit/ORDRE_B1a.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 76, 80 — ne pas re-diagnostiquer) ═══
- La boucle d'adoption (reconciliation.py:169-331, setups MANAGEMENT_ONLY en
  mode adopt_existing_ibkr_position) VOIT le stop broker (_matching_stop_order,
  :236) mais ne crée JAMAIS de ligne orders locale pour lui. Résultat :
  StopModificationService ne trouve pas de broker_order_id → broker_updated=
  False → B-1b (à venir) échouerait en silence. B-1a comble ce trou.
- Chaîne prouvée (audit 80 Q2) : OrderRecord.broker_order_id = stop_order.
  broker_order_id (IB orderId) → colonne orders.broker_order_id →
  active_stop_order_for_symbol → StopModificationService. Ce que B-1a écrit
  est ce que B-1b lira.
- upsert_order (repositories.py:681-725) est réutilisable tel quel (ON
  CONFLICT sur id).
- B-1a change DEUX comportements observables, tous deux BÉNÉFIQUES (corrige
  un faux positif A-3 dormant ; commence à fermer le trou de clôture sur stop
  déclenché pour un setup adopté). Ce n'est PAS un lot neutre.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/reconciliation.py (la boucle d'adoption, point
d'insertion :236-272) + tests.
INTERDIT : StopModificationService (le cas orderId=0 est une limite connue
transmise à B-1b, PAS corrigée ici), order_manager.py, repositories.py
(upsert_order et active_stop_order_for_symbol utilisées telles quelles),
_matching_stop_order (utilisée telle quelle).

═══ 3. CHANGEMENT ═══
Au point d'adoption, entre _matching_stop_order (:236) et upsert_position
(:272), ajouter, gardé par `if stop_order is not None:` :
(a) IDEMPOTENCE + ANTI-VOL (obligatoire) :
    existing = self.repository.active_stop_order_for_symbol(symbol)
    - si existing existe ET existing["setup_id"] == setup["setup_id"]
      → réutiliser existing["id"] (upsert en place, rafraîchit stop_price/
        status).
    - si existing existe MAIS appartient à un AUTRE setup_id
      → NE PAS le réutiliser (ne jamais voler l'ordre d'un autre setup) ;
        créer une ligne neuve avec new_id("adp"). Émettre un événement de
        traçabilité (deux stops actifs pour le symbole, situation anormale).
    - si existing n'existe pas → new_id("adp").
(b) Construire l'OrderRecord depuis stop_order (BrokerOrderRequest) et setup :
    - id = résolu en (a)
    - setup_id = setup["setup_id"]   (JAMAIS stop_order.setup_id, littéral
      "broker")
    - symbol, side="SELL", order_type=stop_order.order_type,
      quantity=stop_order.quantity
    - status = _normalize_order_status(stop_order.status) or
      OrderStatus.SUBMITTED.value
    - stop_price=stop_order.stop_price,
      broker_order_id=stop_order.broker_order_id,
      broker_perm_id=stop_order.broker_perm_id,
      parent_id=None, oca_group=stop_order.oca_group
    puis self.repository.upsert_order(record).
(c) Ne PAS émettre d'ordre broker (aucun submit/modify). Écriture locale
    uniquement.

═══ 4. INVARIANTS ═══
- Idempotence : N cycles d'adoption consécutifs → UNE seule ligne orders
  pour le stop (pas N). Prouvé par test.
- Anti-vol : une ligne orders active appartenant à un AUTRE setup n'est
  JAMAIS réassignée au setup adoptant. Prouvé par test.
- setup_id de la ligne = le setup adoptant, jamais "broker".
- Aucun ordre broker émis. Purement local.
- Aucune écriture de statut de setup ajoutée par ce lot (upsert_order
  n'écrit pas setups) → aucun impact cliquet.

═══ 5. PREUVE DE SORTIE ═══
1. Adoption avec stop broker actif → une ligne orders créée, broker_order_id
   = celui du stop broker, setup_id = setup adoptant. Vérifier qu'elle est
   ensuite trouvée par active_stop_order_for_symbol (la chaîne B-1b).
2. IDEMPOTENCE : deux run() consécutifs → toujours UNE seule ligne orders
   (compter les lignes). Le test central de ce lot.
3. ANTI-VOL : un autre setup possède déjà la ligne active du symbole →
   l'adoption ne la lui vole pas ; comportement défini (ligne neuve +
   événement). Le second test critique.
4. FAUX POSITIF A-3 CORRIGÉ : reproduire le scénario dormant (setup
   MANAGEMENT_ONLY adopté, ligne positions persistée SANS ligne orders, puis
   reconciliation.run(startup=True)) → AVANT B-1a : alarme CRITICAL
   "startup_filled_entry_without_stop" à tort. APRÈS B-1a : la ligne orders
   existe, plus d'alarme. Écris ce test — l'audit 80 note qu'AUCUN test ne
   couvre ce chemin aujourd'hui.
5. setup_id correct : la ligne créée porte le setup_id du setup adoptant,
   jamais "broker".
6. Non-régression : les tests existants de reconciliation passent.
7. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/b1a-persist-adopted-stop, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(reconciliation): persist adopted broker stop as local order (root B, B-1a prereq)"

═══ 7. RAPPORT ═══
audit/81_rapport_b1a.md selon template + confrontation à ORDRE_B1a.md.
Documenter explicitement (§4 ou §8) les DEUX effets bénéfiques et la limite
connue orderId=0 transmise à B-1b.

═══ 8. INTERDICTIONS ═══
Aucun ordre broker émis. Ne pas modifier StopModificationService (limite
orderId=0 = B-1b). Ne pas corriger _matching_stop_order. Aucune suppression.
Doute → ARRÊTE.
