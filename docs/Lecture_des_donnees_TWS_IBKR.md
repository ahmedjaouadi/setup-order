# Lecture des données TWS / IBKR — Guide de référence

> Fichier de contexte pour Claude Code.
> But : lire correctement les données de Trader Workstation (TWS) et **ne jamais
> confondre** Positions, Ordres, Exécutions et Valeurs de compte.

---

## 1. Le modèle mental à retenir

Dans TWS et dans l'API IBKR, ce sont **4 objets de données distincts**, avec des
appels différents. Ils se ressemblent visuellement mais ne signifient pas la même chose.

| Objet | Ce que c'est | Onglet TWS | Appel API |
|---|---|---|---|
| **POSITION** | Titre déjà détenu (ordre exécuté, en cours de détention) | « Portefeuille » | `ib.positions()` / `ib.portfolio()` |
| **ORDRE OUVERT** | Instruction transmise mais **pas encore remplie** | « Ordres » | `ib.openTrades()` / `ib.openOrders()` |
| **EXÉCUTION** | Un ordre qui a été rempli (historique des fills) | « Transactions » | `ib.executions()` / `ib.fills()` |
| **VALEUR DE COMPTE** | Cash, marge, liquidité nette, P&L | Bandeau P&L / marge | `ib.accountValues()` / `ib.accountSummary()` |

**Règle d'or :**
> Une **position** est le *résultat* d'un ordre déjà exécuté.
> Un **ordre ouvert** n'a *pas encore* été exécuté.
> Compter les positions pour répondre à « combien d'ordres ouverts ? » est une **ERREUR**.

Exemple concret : sur un écran montrant 8 lignes dans le Portefeuille et un onglet
« Ordres » vide → la réponse à « combien d'ordres ouverts ? » est **0**, pas 8.

---

## 2. Connexion (setup)

Bibliothèque à utiliser : **`ib_async`** (remplace `ib_insync`, qui n'est plus maintenue).

```bash
pip install ib_async
```

Pré-requis côté TWS / IB Gateway :
- API activée : *Configuration → API → Settings → « Enable ActiveX and Socket Clients »*
- Cocher **« Download open orders on connection »** (sinon les ordres existants ne remontent pas au démarrage)
- Ajouter `127.0.0.1` aux *Trusted IPs* si connexion locale
- Ports par défaut : **7497** (TWS papier), **7496** (TWS live), **4002/4001** (Gateway papier/live)

```python
from ib_async import IB

ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)   # clientId unique par connexion
```

> Note compte simulé : un compte « DUMxxxxxx » et le bandeau « CECI N'EST PAS UN COMPTE
> DE COURTAGE… » indiquent un **compte de démonstration/simulé** avec données de
> marché **différées**. Les prix ne sont pas temps réel.

---

## 3. Lire chaque type de donnée

### 3.1 Positions (ce qui est détenu)

```python
for p in ib.positions():
    print(p.contract.symbol, p.position, p.avgCost)
    # position > 0 = long ; position < 0 = short
```

`ib.portfolio()` donne en plus la valeur de marché et le P&L non réalisé :

```python
for item in ib.portfolio():
    print(item.contract.symbol,
          item.position,          # quantité détenue
          item.marketPrice,       # dernier prix
          item.marketValue,       # valeur de la position
          item.averageCost,       # coût moyen d'entrée
          item.unrealizedPNL,     # P&L latent
          item.realizedPNL)
```

Champs clés : `position` (quantité, négatif = short), `averageCost`, `marketValue`,
`unrealizedPNL`. **Une position N'A PAS** de champ « type d'ordre » ni « prix limite ».

### 3.2 Ordres ouverts (en attente d'exécution)

```python
for t in ib.openTrades():
    o = t.order
    print(o.orderId, o.action, o.orderType, o.totalQuantity,
          o.lmtPrice, t.orderStatus.status)
    # status typiques : Submitted, PreSubmitted, PendingSubmit
```

Un ordre ouvert **a** un `action` (BUY/SELL), un `orderType` (LMT/MKT/STP…),
une `totalQuantity`, un `lmtPrice`/`auxPrice`, et un `status`.
S'il n'y a aucune ligne → **0 ordre ouvert**. Ne jamais substituer les positions.

### 3.3 Exécutions / transactions (historique des fills)

```python
for f in ib.fills():
    print(f.execution.time, f.contract.symbol,
          f.execution.side,        # BOT / SLD
          f.execution.shares,
          f.execution.price)
```

### 3.4 Valeurs du compte (cash, marge, P&L global)

```python
for v in ib.accountValues():
    if v.tag in ('NetLiquidation', 'TotalCashValue',
                 'MaintMarginReq', 'AvailableFunds', 'UnrealizedPnL'):
        print(v.tag, v.value, v.currency)
```

---

## 4. Glossaire TWS (FR) ↔ API

L'interface TWS peut être en français. Voici la correspondance des libellés vus à l'écran :

| Libellé TWS (FR) | Signification | Source API |
|---|---|---|
| Portefeuille | Liste des positions | `ib.portfolio()` |
| Ordres | Ordres ouverts / en attente | `ib.openTrades()` |
| Transactions | Exécutions passées | `ib.fills()` |
| POS | Quantité de la position | `position` |
| DERNIER | Dernier prix négocié | `marketPrice` |
| PRX MYN / Prix moyen | Coût moyen d'entrée | `averageCost` |
| VARI. | Variation du jour | (calcul / tick) |
| Non réalisé | P&L latent | `unrealizedPNL` |
| Réalisé | P&L réalisé | `realizedPNL` |
| JOURNALIER (P&L) | P&L du jour | `accountValue` DailyPnL |
| Liquidité nette | Valeur nette du compte | `NetLiquidation` |
| Maintien | Marge de maintien | `MaintMarginReq` |
| Cours acheteur / vendeur (Bid/Ask) | Meilleur achat / vente | tick `bid` / `ask` |
| MID | Milieu bid-ask | (bid+ask)/2 |
| Cours ach/vend « 101 x 1 » | Tailles bid × ask | `bidSize` / `askSize` |
| QTÉ | Quantité de l'ordre à saisir | `totalQuantity` |
| LMT / MKT / STP | Type d'ordre | `orderType` |
| DAY / GTC | Durée de validité | `tif` (time in force) |

---

## 5. Pièges à éviter (checklist de désambiguïsation)

- [ ] « Combien d'ordres ouverts ? » → **compter uniquement** `ib.openTrades()`,
      jamais le portefeuille.
- [ ] Le panneau « Portefeuille » liste des **positions**, pas des ordres.
- [ ] Une quantité négative en position = **short**, ce n'est pas un ordre de vente en attente.
- [ ] Le bandeau « Saisie d'ordres » en haut est un **formulaire** de saisie
      (un ordre en préparation, non transmis) — ce n'est ni une position ni un ordre ouvert.
- [ ] P&L « Journalier / Non réalisé / Réalisé » = valeurs de **compte**, pas des ordres.
- [ ] Données différées + compte DUM = **simulation**, prix non temps réel.
- [ ] Un ordre en statut `PreSubmitted`/`Submitted` est ouvert ; un ordre `Filled`
      est devenu une exécution (et a modifié une position).

---

## 6. Lecture par capture d'écran (si pas d'accès API)

Si Claude Code doit lire une **capture d'écran** de TWS au lieu de l'API, appliquer
la même logique mais avec prudence (l'OCR est fragile) :

1. **Identifier l'onglet actif** en bas à gauche : « Activité / Ordres / Transactions / Récapitulatif ».
2. Pour les **ordres ouverts** : regarder l'onglet « Ordres ». S'il affiche le texte
   d'aide par défaut (« Dans cet espace, consultez, suivez… vos ordres envoyés »)
   et aucune ligne sous les colonnes Action/Type/Détails → **0 ordre ouvert**.
3. Pour les **positions** : lire le panneau « Portefeuille » (colonnes INSTR FIN, POS,
   DERNIER, etc.). Chaque ligne = une position.
4. Ne jamais déduire le nombre d'ordres à partir du portefeuille.

> ⚠️ La lecture par capture est un dernier recours. Dès que possible, privilégier
> l'API (`ib_async`) : positions, ordres et exécutions y sont des méthodes **séparées**,
> ce qui élimine toute confusion.

---

## 7. Résumé en une phrase

> Positions = ce que je détiens (`ib.portfolio()`).
> Ordres ouverts = ce que j'attends de faire exécuter (`ib.openTrades()`).
> Exécutions = ce qui a déjà été fait (`ib.fills()`).
> Valeurs de compte = mon cash / ma marge / mon P&L (`ib.accountValues()`).
> Ne jamais mélanger les quatre.
