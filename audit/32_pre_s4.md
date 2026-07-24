# Pré-audit S4 — Garde-fou symbole sur le chemin manuel, lecture seule

Mode : audit lecture seule strict. Aucune modification de code, aucun
commit hors `audit/ORDRE_S4_preaudit.md` et ce fichier, aucun push, aucune
suppression. Commandes exécutées en dehors de lectures de fichiers et de
`git log`/`git status`/`git merge-base` : `sqlite3` via `sqlite3.connect(...,
uri=True)` en `mode=ro` sur `data/trading_state.sqlite`, `grep`. Aucune
mutation de base, aucune exécution de test. Date d'audit : 2026-07-24,
branche `feat/setup-conditions` (HEAD `8b3a1c5`).

Rappel du contexte figé (audit 26 Q1, non re-diagnostiqué ici) : le chemin
manuel (`manual_order_service.py:293`) n'a pas de `current_status` sur
lequel s'appuyer, faute de `setup_id` réel réutilisé — le garde-fou
`protection_snapshot_for_setup`/`DuplicateOrderError`, indexé par
`setup_id`, est neutralisé pour cette raison. L'ordre demande de vérifier
s'il existe malgré tout un filet **au niveau du symbole**, indépendant du
`setup_id`.

---

## Q1 — Existe-t-il un garde-fou au niveau du symbole ?

### `_assess_buy`/`_submit_buy` : aucune vérification directe par symbole, une délégation

Lecture intégrale de `_assess_buy` (`manual_order_service.py:146-231`) et
`_submit_buy` (`:262-310`) : aucune des deux méthodes n'appelle
`repository.get_position(...)` ni `repository.list_positions()`
directement. Mais `_assess_buy` délègue à un service externe :

```python
# manual_order_service.py:203-212
guard_verdict = self.trade_guards.evaluate_entry(symbol, now=now)
if guard_verdict is not None:
    assessment["block"] = {
        "status": guard_verdict.status,
        "reason_code": guard_verdict.reason_code,
        "message": guard_verdict.message,
        "source": "trade_guards",
        "context": guard_verdict.as_payload(),
    }
    return
```

### `trade_guards.evaluate_entry` — chaîne de vérifications, code cité

```python
# trade_guards.py:598-619
def evaluate_entry(self, symbol, *, setup=None, now=None):
    if not self.enabled():
        return None
    verdict = self._halt_verdict(symbol, now)
    if verdict is not None:
        return verdict
    verdict = self.circuit_breakers.breaker_verdict(now)
    if verdict is not None:
        return verdict
    verdict = self.circuit_breakers.pdt_verdict(now)
    if verdict is not None:
        return verdict
    verdict = self.circuit_breakers.cooldown_verdict(symbol, now)
    if verdict is not None:
        return verdict
    return self._exposure_verdict(symbol, setup)
```

Le dernier maillon, `_exposure_verdict`, contient bien une vérification par
symbole — **avant** toute autre règle d'exposition (max positions, risque
ouvert, secteur, groupes corrélés) :

```python
# trade_guards.py:438-467 (extrait)
def _exposure_verdict(self, symbol, setup):
    config = _mapping(self._config().get("exposure"))
    if config.get("enabled", True) is False:
        return None
    positions = [
        position
        for position in self.repository.list_positions()
        if int(_number(position.get("quantity"), 0) or 0) > 0
        and str(position.get("status") or "OPEN").upper() != "CLOSED"
    ]
    normalized = symbol.upper()

    if config.get("block_if_position_on_same_symbol", True) is not False:
        for position in positions:
            if str(position.get("symbol") or "").upper() == normalized:
                return GuardVerdict(
                    status=STATUS_NO_GO,
                    reason_code=REASON_CONFLICT_WITH_OPEN_POSITION,
                    decision_status="CONFLICT_WITH_OPEN_POSITION",
                    title="Position deja ouverte sur ce titre",
                    message=(
                        f"Une position est deja ouverte sur {normalized}. "
                        "Pas d'empilement sur le meme titre."
                    ),
                    context={"symbol": normalized},
                )
    ...
```

**Réponse directe** : oui, une règle regarde explicitement si une position
est déjà ouverte sur le symbole (`block_if_position_on_same_symbol`), et
cette règle est la **première** condition évaluée dans `_exposure_verdict`,
avant `max_open_positions`, `max_total_open_risk_R`,
`max_positions_same_sector` et `correlated_groups`. Elle opère par
**symbole**, pas par `setup_id` — donc structurellement immunisée contre le
renouvellement de `setup_id` à chaque appel manuel décrit dans le contexte
figé.

### Configuration réellement active

`app/settings.py:76-83` (`DEFAULT_CONFIG["trade_guards"]["exposure"]`) :

```python
"exposure": {
    "enabled": True,
    "block_if_position_on_same_symbol": True,
    "max_open_positions": 3,
    ...
}
```

`config.yaml` (racine du dépôt, chargé par `load_settings`) ne contient
**aucune clé `trade_guards`** — vérifié par lecture intégrale du fichier
(558 lignes). `deep_merge(DEFAULT_CONFIG, overrides)`
(`app/settings.py:716-723`, `742-751`) laisse donc la section
`trade_guards` telle quelle : `enabled: True`,
`exposure.enabled: True`, `block_if_position_on_same_symbol: True`. Le
garde-fou est actif dans la configuration réellement chargée par
l'application, pas seulement dans un défaut de code jamais atteint.

### `repository.get_position(symbol)` — grep exhaustif sur `manual_order_service.py`

```
$ grep -n "get_position" app/engine/manual_order_service.py
236:        position = self.repository.get_position(symbol)
```

Un seul appel, dans `_assess_sell` (ligne 236) — le chemin **SELL**, pas
BUY. Le chemin BUY n'appelle jamais `get_position` directement ; sa seule
lecture de position passe par `trade_guards._exposure_verdict` via
`repository.list_positions()`, comme montré ci-dessus.

### Confirmation indirecte : `evaluate_entry` n'est pas un chemin mort

```
$ grep -rn "evaluate_entry(" app/ --include="*.py" | grep -v test
app/engine/entry_order_executor.py:100   (chemin automatique)
app/engine/manual_order_service.py:203   (chemin manuel)
app/engine/signal_engine.py:148          (génération de signal ENTRY_READY)
```

Trois appelants réels. Ce n'est pas un mécanisme écrit pour le chemin
manuel seul et jamais exercé ailleurs : le chemin automatique et la
génération de signal l'utilisent aussi, ce qui donne un historique
d'exécution indirect au code du garde lui-même (mais pas à la combinaison
« ordre manuel + position déjà ouverte », voir Q2).

**Verdict Q1** : le garde-fou par symbole existe, est câblé sur le chemin
manuel BUY, est actif par défaut, et n'est pas contourné par le
renouvellement du `setup_id`.

---

## Q2 — Que se passe-t-il concrètement sur un double achat manuel ?

Scénario : `AAPL` (exemple) a un setup automatique déjà `IN_POSITION`
(fill réel réconcilié, position ouverte, stop actif chez IBKR — chemin
confirmé écrit par `post_fill_progression.record_fill` +
`upsert_position`, voir plus bas). L'utilisateur soumet un BUY manuel sur
`AAPL`.

### Ordre des vérifications dans `_assess_buy`

```
1. stop_loss requis                         (:155-163)
2. prix de référence marché disponible       (:165-176)
3. stop_loss < prix de référence             (:177-185)
4. calcul du risque (_risk_summary)          (:187-188)
5. fenêtre de session (execution_window_block) (:190-201)
6. trade_guards.evaluate_entry               (:203-212)  <-- ICI
7. limites de risque (risk_limits_block)     (:214-217)
8. cost gate                                 (:219-231)
```

En supposant que 1-5 passent (stop valide, marché ouvert, session
correcte), l'étape 6 atteint `_exposure_verdict`. `AAPL` figure dans
`repository.list_positions()` (quantité > 0, statut `OPEN` — la position
laissée par le setup automatique). Le premier test de la boucle
(`block_if_position_on_same_symbol`) matche : un `GuardVerdict` `NO_GO` /
`CONFLICT_WITH_OPEN_POSITION` est retourné.

### L'ordre part-il chez le broker ?

**Non, dans la configuration par défaut actuellement chargée.**
`assessment["block"]` est renseigné à `_assess_buy:205-211`, la méthode
`return`. De retour dans `submit()` :

```python
# manual_order_service.py:82-97
async def submit(self, payload):
    assessment = await self._assess(payload)
    if assessment.get("validation_error") or assessment.get("block"):
        self._trace(assessment, orders=None)
        self.event_store.record(
            EventLevel.RISK, "manual_order_rejected", ...,
            data={"reason_code": self._refusal_reason_code(assessment), ...},
        )
        return self._result_payload(assessment)
    ...
```

`assessment["block"]` est non nul → retour immédiat, **aucun appel à
`_submit_buy`, donc aucun appel à `order_manager.place_entry_order`, donc
aucune soumission au broker.** Un événement `manual_order_rejected` est
journalisé, et une trace `decision_traces` (`decision_type="MANUAL_ORDER"`,
`final_decision="NO_GO:CONFLICT_WITH_OPEN_POSITION"`) est écrite via
`_trace()` (`:482-503`), que la commande soit acceptée ou refusée.
`routes_orders.py:64-65` traduit ce refus en `HTTPException(422)` côté API.

### Si le garde-fou était désactivé ou mal configuré : que deviendrait la position ?

Question factuelle utile pour qualifier le risque résiduel (pas le
scénario nominal actuel). Si `block_if_position_on_same_symbol` ou
`trade_guards.enabled` passait à `False` (erreur de configuration), le
flux continuerait vers `_submit_buy` (`:262-310`) puis
`order_manager.place_entry_order(setup, decision)` avec :

```python
# manual_order_service.py:282-293
setup = self._synthetic_setup(assessment)   # setup_id = new_id("man"), neuf
decision = RiskDecision(..., quantity=assessment["quantity"], ...)
order = await self.order_manager.place_entry_order(setup, decision)
```

`place_entry_order` (`order_manager.py:73-83`) vérifie
`protection_snapshot_for_setup(setup["setup_id"])` — indexé par le
`setup_id` **neuf**, donc toujours vide pour un premier appel manuel :
aucune levée de `UnprotectedActiveOrderError`/`DuplicateOrderError` (déjà
établi par le contexte figé). L'ordre serait alors bien transmis : un
**nouveau** bracket order (entrée + stop), avec son propre
`oca_group=f"bracket:{setup['setup_id']}"` distinct du bracket du setup
automatique existant, dimensionné uniquement sur `assessment["quantity"]`
(la quantité de l'ordre manuel, pas la quantité déjà détenue).

Conséquence architecturale, si ce cas se produisait : côté IBKR, les deux
achats sur le même symbole se **nettent** en une seule position agrégée
(comportement standard du broker pour un compte donné — non vérifiable
statiquement depuis ce dépôt, au même titre que les points serveur IB déjà
signalés non tranchables par l'audit 26 Q3). Côté application, en
revanche, ce qui EST vérifiable par lecture de code : **deux stops
distincts existeraient**, chacun dimensionné sur sa propre quantité — le
stop de l'ordre manuel ne couvre que les actions achetées manuellement, pas
celles du setup automatique préexistant, et réciproquement. Rien dans le
code lu ne fusionne les deux stops ni ne recalcule une quantité totale
protégée.

### Un setup manuel est-il persisté en base ?

```
$ grep -n "upsert_setup" app/engine/order_manager.py app/engine/manual_order_service.py
(vide)
```

Aucun appel à `repository.upsert_setup`. `_synthetic_setup` (`:435-462`)
construit un dictionnaire Python passé par valeur à `place_entry_order`,
jamais écrit dans la table `setups`. Confirmé empiriquement (Q3) : 0 ligne
`setups` avec `setup_id LIKE 'man_%'`.

Ce qui EST persisté, à chaque appel de `submit()` (accepté ou refusé) :
une ligne `decision_traces` (`decision_type="MANUAL_ORDER"`,
`manual_order_service.py:490-503`), portant le payload complet, le calcul
de risque, le motif de blocage éventuel et les ordres résultants. C'est la
seule trace exploitable a posteriori pour un ordre manuel — les tables
`setups` et (si bloqué en amont) `orders` n'en contiennent aucune.

**Verdict Q2** : dans la configuration actuellement chargée, l'ordre est
bloqué avant tout envoi au broker par le garde d'exposition symbole. Le
scénario "deux stops distincts, quantités non fusionnées" décrit ci-dessus
est réel dans le code mais n'est atteignable qu'en cas de contournement ou
de désactivation du garde — ce n'est pas le chemin nominal aujourd'hui.

---

## Q3 — Y a-t-il déjà eu des ordres manuels en production ?

Requêtes `mode=ro` sur `data/trading_state.sqlite` :

```
$ SELECT COUNT(*) FROM orders WHERE setup_id LIKE 'man_%';
0

$ SELECT COUNT(*) FROM setups WHERE setup_id LIKE 'man_%';
0

$ SELECT COUNT(*), decision_type FROM decision_traces GROUP BY decision_type;
(SETUP_QUALITY_SCORE, OPPORTUNITY_REJECTED, TECHNIQUE_REVISION, SCANNER_GATE)
-- "MANUAL_ORDER" n'apparaît dans AUCUNE ligne
$ SELECT COUNT(*) FROM decision_traces WHERE setup_id LIKE 'man_%';
0

$ SELECT COUNT(*) FROM events WHERE event_type='manual_order_rejected';
0
```

`new_id("man")` (`manual_order_service.py:121`,
`app/utils/id_generator.py:6-7`) produit des identifiants `man_<12 hex>` —
le préfixe recherché est donc exact, pas une supposition.

### Contrôle de robustesse : la table `orders` n'a-t-elle pas simplement été purgée ?

Oui, en grande partie — `orders` ne contient que **7 lignes**, toutes du
2026-06-30, alors que `events`/`decision_traces` couvrent jusqu'au
2026-07-17. 58 événements `order_history_deleted` existent
(`app/api/routes_orders.py:100-107`, purge manuelle via
`DELETE /api/orders/{id}`). Mais chaque suppression journalise le
`setup_id` de l'ordre supprimé :

```
$ SELECT DISTINCT substr(setup_id,1,4) FROM events WHERE event_type='order_history_deleted';
FLNC, STM_, LUNR, QBTS, GILT, IRDM, HIMX, IONQ, JOBY, QCOM, RKLB, SHOP, DXYZ
```

Aucun préfixe `man_` parmi les 58 ordres purgés. La purge de la table
`orders` n'explique donc pas l'absence de trace : les ordres supprimés
étaient tous des setups automatiques (préfixe ticker), jamais des ordres
manuels. Et `decision_traces` (1 239 074 lignes, 2026-06-23 → 2026-07-17)
n'a **aucun** mécanisme de purge identifié dans ce dépôt (aucune requête
`DELETE FROM decision_traces` trouvée) — son zéro absolu pour
`decision_type="MANUAL_ORDER"` n'est donc pas un artefact de nettoyage.

### Historique du fichier

```
$ git log --diff-filter=A --format="%h %ad %s" --date=short -- app/engine/manual_order_service.py
9774eec 2026-07-06  Etape 11: ordre manuel depuis l UI via le pipeline de securite complet
$ git log --oneline -1 -- app/engine/manual_order_service.py
9774eec  (aucune modification depuis la création)
```

**Réponse directe : aucun ordre manuel n'a jamais été soumis en
production.** La fonctionnalité existe depuis 18 jours (2026-07-06 →
2026-07-24) et n'a jamais été invoquée une seule fois — ni avec succès, ni
en échec (le `decision_traces` de `submit()` est écrit
inconditionnellement dans les deux cas, `:82-97`, `:482-503`, donc même une
tentative refusée aurait laissé une trace).

Conséquence sur l'urgence : il n'existe **aucun** ordre manuel, a fortiori
aucun ordre manuel passé sur un symbole déjà en position, à examiner
rétroactivement — la question "le double achat a-t-il déjà eu lieu" a une
réponse négative factuelle, pas une absence de recherche.

**Verdict Q3** : zéro ordre manuel en production, sur toute trace
disponible (ordres, setups, decision_traces, events). La fonctionnalité
est déployée mais inexercée depuis 18 jours.

---

## RISQUE QUALIFIÉ

**Le risque décrit par le contexte figé (accumulation manuelle non gardée
sur un titre déjà détenu) ne correspond pas exactement à l'état actuel du
code : un garde-fou par symbole existe, est câblé sur le chemin manuel BUY,
est activé par défaut dans la configuration réellement chargée
(`config.yaml` ne surcharge pas `trade_guards`), et bloquerait ce scénario
avant tout envoi au broker (Q1, Q2).**

Ce qui reste néanmoins ouvert, factuellement :

1. **Aucune preuve testée de la combinaison exacte.** `tests/test_trade_guards.py`
   couvre `block_if_position_on_same_symbol` en isolation
   (`test_same_symbol_conflict`) ; `tests/test_manual_orders.py` couvre le
   blocage manuel par HALT (`test_guard_block_returns_422`) mais **aucun
   test ne seed une position ouverte puis ne soumet un BUY manuel sur le
   même symbole** pour vérifier bout-en-bout le blocage et l'absence
   d'ordre transmis. C'est un écart de preuve, pas une lacune de code
   identifiée.
2. **Le garde dépend d'une donnée (`positions`) elle-même dépendante d'un
   correctif récent.** L'écriture fiable de `positions` sur un fill réel
   vient du lot 3b-2 (`c3a44df`, confirmé ancêtre de HEAD sur cette
   branche). Avant ce lot, l'audit 26 documentait un cas réel
   (`LUNR_20260630_001`) où un fill réel n'avait jamais produit de ligne
   `positions`. Le garde-fou par symbole ne peut protéger que ce qu'il
   voit : si `positions` était de nouveau incomplète pour une raison non
   encore identifiée, le garde ne le détecterait pas non plus.
3. **Le garde est un paramètre de configuration, pas une invariante de
   code.** `block_if_position_on_same_symbol` et `trade_guards.enabled`
   sont des booléens de `config.yaml`/`DEFAULT_CONFIG`, modifiables sans
   revue de code dédiée à ce risque précis ; rien dans `manual_order_service.py`
   ne réaffirme la vérification indépendamment de ce paramètre (pas de
   second garde structurel comme il en existe pour d'autres cas, ex. le
   double gate `current_status` du chemin automatique, audit 26 Q1).
4. **Zéro historique d'exécution réelle** (Q3) : ni le succès ni l'échec du
   garde n'a jamais été observé en production sur ce chemin précis — la
   confiance repose sur la lecture de code et un test unitaire du
   mécanisme générique, pas sur un cas vécu.

**Verdict global** : le risque est aujourd'hui **atténué par construction
et par configuration par défaut, mais non prouvé bout-en-bout et non
observé en production**. Ni "réel et imminent" (un garde actif s'interpose
avant l'envoi broker dans la configuration actuelle), ni "purement
théorique" (la protection repose sur un paramètre de configuration non
verrouillé, sur une donnée dont la fiabilité a eu un précédent documenté de
défaillance, et sur zéro test de la combinaison exacte).

### Options factuelles pour fermer le sujet (aucune recommandée)

- **A.** Ajouter un test bout-en-bout dans `tests/test_manual_orders.py` :
  seed d'une position ouverte sur un symbole, soumission d'un BUY manuel
  sur ce même symbole, assertion du blocage `CONFLICT_WITH_OPEN_POSITION`
  et de l'absence d'ordre transmis (`repository.list_orders` vide pour le
  nouveau `setup_id`).
- **B.** Ajouter une vérification redondante et structurelle dans
  `_assess_buy`/`_submit_buy` elle-même (ex. `repository.get_position(symbol)`,
  comme déjà fait côté SELL à `:236`), indépendante du paramètre
  `trade_guards.exposure.block_if_position_on_same_symbol`, pour ne pas
  dépendre d'un seul booléen de configuration.
- **C.** Documenter explicitement (commentaire de code ou doc externe) que
  la protection par symbole du chemin manuel repose entièrement sur
  `trade_guards.exposure.block_if_position_on_same_symbol`, avec un
  avertissement contre toute désactivation sans garde de remplacement.
- **D.** Ne rien changer et accepter le risque résiduel tel que qualifié
  ci-dessus, sur la base que le garde est actif par défaut aujourd'hui.
