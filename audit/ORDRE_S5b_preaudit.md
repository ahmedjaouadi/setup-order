MODE : AUDIT LECTURE SEULE STRICTE — PRÉ-S5b.

Écris d'abord cet ordre dans audit/ORDRE_S5b_preaudit.md (mot pour mot),
puis produis audit/35_pre_s5b.md.
Tu ne modifies AUCUN code. Tu ne commites que ces deux fichiers. Tu ne
pousses pas, tu ne supprimes rien, tu ne corriges rien.
Dans le chat : uniquement les verdicts Q1 à Q4, une ligne chacun.

CONTEXTE FIGÉ (ne pas re-diagnostiquer) :
- reconciliation.py, branche SUBMITTED de _update_setup_after_reconciled_order
  (~lignes 426-440 avant nos lots, décalées depuis) : elle réécrit le statut
  du setup avec le message "Open order restored from TWS", et n'écrit QUE si
  le statut courant est terminal ou MANUAL_REVIEW_REQUIRED (audit 23).
  Elle écrit STOP_ORDER_PLACED si side == SELL, sinon ENTRY_ORDER_PLACED.
- Le 2026-06-29, 4 setups sont passés de ERROR_REQUIRES_MANUAL_REVIEW à
  ENTRY_ORDER_PLACED ; l'audit 26 a établi 2 redémarrages moteur ce jour-là
  (setup_loaded ×2), et TradingEngine.start() enchaîne load_all() puis
  reconciliation.run().
- Notre lot 3b-2 écrit désormais MANUAL_REVIEW_REQUIRED dans deux cas :
  barreau 3 (prix/quantité introuvables) et fill SANS stop actif constaté
  (has_active_protection() == False) — ce dernier étant précisément le cas
  du 29 juin.
Objet : savoir si un mécanisme existant peut effacer l'alarme que nous
venons d'ajouter.

--- Q1 : CONDITIONS EXACTES DE L'EFFACEMENT ---
  - Cite intégralement la branche SUBMITTED dans sa version ACTUELLE
    (après nos lots), avec ses numéros de ligne à jour.
  - Liste exactement les statuts de setup depuis lesquels elle écrit
    (l'ensemble testé ligne ~432-434) : MANUAL_REVIEW_REQUIRED en fait-il
    partie ? ERROR_REQUIRES_MANUAL_REVIEW aussi ?
  - Que faut-il pour qu'elle se déclenche : un ordre local dans quel état,
    et un ordre broker dans quel état ? Sur quelle comparaison ?

--- Q2 : LE SCÉNARIO LE PLUS GRAVE (priorité de cet audit) ---
  Séquence à examiner : un fill réel se produit SANS stop actif → notre
  branche FILLED écrit MANUAL_REVIEW_REQUIRED ("filled without active
  protective stop"). Puis, dans la MÊME passe ou une passe ultérieure, la
  branche SUBMITTED traite l'ordre STOP (side == SELL) du même setup.
  - Ce scénario est-il atteignable ? Détaille les conditions nécessaires
    (l'ordre stop doit-il être encore SUBMITTED localement ET côté broker ?
    que se passe-t-il s'il a été REJECTED ?).
  - Si atteignable : la branche SUBMITTED écrirait-elle STOP_ORDER_PLACED
    par-dessus MANUAL_REVIEW_REQUIRED ? Autrement dit, un statut affirmant
    "stop protecteur soumis" remplacerait-il une alarme "rempli sans
    protection" ?
  - Rappel de l'ordre de parcours (audit 23) : list_orders() trie par
    created_at DESC, donc le stop est visité AVANT l'entrée. Que change cet
    ordre pour ce scénario, dans les deux sens possibles ?

--- Q3 : LÉGALITÉ ET AUTRES CHEMINS D'EFFACEMENT ---
  - MANUAL_REVIEW_REQUIRED → ENTRY_ORDER_PLACED et
    MANUAL_REVIEW_REQUIRED → STOP_ORDER_PLACED figurent-elles dans
    ALLOWED_TRANSITIONS ? Idem depuis ERROR_REQUIRES_MANUAL_REVIEW.
    (Les écritures étant directes, une transition illégale passerait
    silencieusement — c'est le point.)
  - Grep exhaustif : existe-t-il D'AUTRES endroits dans app/ qui écrivent
    un statut par-dessus MANUAL_REVIEW_REQUIRED ou
    ERROR_REQUIRES_MANUAL_REVIEW ? Liste-les avec fichier:ligne.
  - Un redémarrage du moteur suffit-il à déclencher l'effacement, ou faut-il
    en plus un ordre encore ouvert côté TWS ? Réponds par le code de
    TradingEngine.start().

--- Q4 : PORTÉE RÉELLE EN PRODUCTION ---
  - En base (mode=ro) : combien de fois un statut MANUAL_REVIEW_REQUIRED ou
    ERROR_REQUIRES_MANUAL_REVIEW a-t-il été suivi d'un retour vers
    ENTRY_ORDER_PLACED / STOP_ORDER_PLACED ? Donne les cas avec dates et
    setup_id (les 4 du 29 juin inclus).
  - Ces retours coïncident-ils avec des événements setup_loaded
    (redémarrages) ?
  - Le message "Open order restored from TWS" apparaît-il en base ? Combien
    de fois, sur quels setups ?

Termine par une section RISQUE QUALIFIÉ : le scénario Q2 est-il atteignable
aujourd'hui, et si oui, quelles options factuelles existent pour le fermer
(sans en recommander une).
