ORDRE DE TRAVAIL — PRÉ-AUDIT S4 (LECTURE SEULE STRICTE).

Écris d'abord cet ordre dans audit/ORDRE_S4_preaudit.md (mot pour mot),
puis produis audit/32_pre_s4.md.
Tu ne modifies AUCUN code, tu ne commites rien d'autre que ces deux
fichiers, tu ne pousses pas, tu ne supprimes rien.
Dans le chat : uniquement les verdicts de Q1/Q2/Q3 en 3 lignes.

CONTEXTE FIGÉ (audit 26 Q1, ne pas re-diagnostiquer) :
ManualOrderService._submit_buy (manual_order_service.py:293) appelle
place_entry_order sans jamais lire un current_status. Mais assessment
["setup_id"] = new_id("man") (:121) génère un setup_id NEUF à chaque appel.
Conséquences déjà établies : (1) le mécanisme du 29 juin ne peut pas s'y
reproduire, faute de setup existant réutilisé ; (2) le garde-fou
protection_snapshot_for_setup / DuplicateOrderError, indexé par setup_id,
est neutralisé pour la même raison. Seul filet identifié :
risk_limits.max_total_exposure_usd (:423-432).
Le risque réel à qualifier n'est donc PAS le ré-envoi automatique, mais
l'accumulation manuelle sur un titre déjà détenu.

Q1 — EXISTE-T-IL UN GARDE-FOU AU NIVEAU DU SYMBOLE ?
  - Dans _assess_buy (:146-231) et _submit_buy (:262-310), une vérification
    porte-t-elle sur le SYMBOLE plutôt que sur le setup_id ? (position
    existante, exposition par titre, nombre max de positions, doublon de
    symbole)
  - trade_guards.evaluate_entry (appelé :203-212) : quelles règles
    applique-t-il exactement ? L'une d'elles regarde-t-elle si une position
    est déjà ouverte sur ce symbole ? Cite le code des guards.
  - repository.get_position(symbol) est-il appelé quelque part sur ce
    chemin ? Grep sur tout manual_order_service.py.

Q2 — QUE SE PASSE-T-IL CONCRÈTEMENT SUR UN DOUBLE ACHAT MANUEL ?
  Déroule, code à l'appui, le scénario : un titre est déjà en position
  (setup automatique rempli, position ouverte, stop actif chez IBKR), et
  l'utilisateur soumet un achat manuel sur CE MÊME symbole.
  - qu'est-ce qui est vérifié, dans l'ordre ?
  - l'ordre part-il chez le broker, oui ou non ?
  - si oui : que devient la position existante côté IBKR (moyenne du prix,
    quantité) et le stop protecteur déjà en place couvre-t-il la nouvelle
    quantité ?
  - un setup manuel est-il persisté en base, ou reste-t-il synthétique ?
    Existe-t-il une trace exploitable a posteriori ?

Q3 — Y A-T-IL DÉJÀ EU DES ORDRES MANUELS EN PRODUCTION ?
  - En base (mode=ro) : des ordres portant un setup_id préfixé "man"
    existent-ils ? Combien, sur quels symboles, à quelles dates ?
  - Parmi eux, y en a-t-il sur un symbole qui était déjà en position au
    même moment ? Donne les lignes brutes.
  - Si aucun ordre manuel n'a jamais été passé, dis-le : ça change
    l'urgence du sujet.

Termine par une section RISQUE QUALIFIÉ : le risque est-il réel et
atteignable aujourd'hui, ou théorique ? Et, factuellement, quelles options
existent pour le fermer (sans en recommander une).
