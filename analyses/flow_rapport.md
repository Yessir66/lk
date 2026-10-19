# Copier les achats du wallet 5dzH7… avec ses propres sorties — rapport de la nuit du 19 au 20/10

Mise : **50 $ par achat copié**. Données : tous les achats du wallet du 01/10 au 19/10 (467 exploitables) et,
pour chacun, tous les trades du token de 5 min avant à 45 min après (swap-api.pump.fun). Coûts simulés : frais
1,25 % pump.fun + 1 % bot par côté, glissement 2 % + impact de notre ordre, 0,21 $ de gas par transaction,
vente exécutée 2 s après la décision. Méthode : mise au point sur le 10→19/10, test sur le 01→09/10 jamais vu.

## 1. La méthode « climax + retest » sur les 6 autres achats du 19/10
+255 $ sur 5 achats exploitables (le 6e, 3aT7me, est appairé à un token BTC et n'a pas de données). Mais
+236 $ viennent d'un seul token (8NfX91, vendu à ×6 par la limite de temps), et sur 289 achats « courbe »
la méthode perd **−1 881 $**. Abandonnée.

## 2. Ce que les données montrent
* Le profit vient **presque entièrement de quelques tokens qui explosent** (×5 à ×25). Sans les 3 meilleurs
  trades, toutes les familles de règles testées (3 000+) étaient négatives sur l'échantillon de départ.
* Chaque aller-retour coûte ~7 % (≈ 3,7 $ sur 50 $) : il faut éviter les trades inutiles.
* **Seuls les achats du wallet sur la courbe de lancement pump.fun** (avant migration) sont exploitables ;
  ses achats sur PumpSwap, Raydium, Meteora perdent.
* Les gagnants se reconnaissent **dans les 2 minutes** : beaucoup de nouveaux wallets acheteurs.
* Les tokens du wallet ont été bien moins bons du 02 au 07/10 (≥ ×3 atteint ~10 % du temps contre 15-27 %
  du 14 au 19/10) : aucune règle de sortie ne compense une mauvaise passe du wallet.

## 3. L'algorithme F1 (figé avant le test, voir `flow_preregistration.md`)
1. Copier seulement les achats du wallet sur la courbe pump.fun, tout de suite.
2. À +2 min : moins de 50 nouveaux wallets acheteurs depuis l'entrée → tout vendre.
3. Vendre 50 % à ×1,5.
4. Le reste quand le flux de nouveaux wallets tombe sous 30 % de son pic (après 5 min).
5. À +5 min : prix sous ×0,85 → tout vendre. 45 min maximum.

| | Trades | Résultat | Jours positifs |
|---|---|---|---|
| Mise au point 10→19/10 | 82 | **+606 $** | 8/10 |
| **Test 01→09/10 (jamais vu)** | 207 | **−218 $** | 3/9 |
| Total 19 jours | 289 | +387 $ (pire baisse −876 $) | 11/19 |

Le test échoue : F1 seul n'est pas rentable de façon fiable.

## 4. Ajout après le test : le filtre « forme »
Simuler tous les signaux, mais ne prendre un trade que si **les 15 derniers trades simulés sont gagnants au
total**. Le filtre s'est coupé seul du 04 au 07/10 (mauvaise passe) et rallumé le 08/10.

| | Trades | Résultat | Jours positifs / perdants | Pire baisse |
|---|---|---|---|---|
| F1 + filtre, 01→09/10 | 63 | +471 $ | 3 / 3 (3 sans trade) | |
| F1 + filtre, 10→19/10 | 75 | +474 $ | 8 / 2 | |
| **F1 + filtre, 19 jours** | 138 | **+945 $** | 11 / 5 | **−170 $** |

Positif pour toutes les valeurs testées du filtre à partir de 10 trades (+696 à +945 $). **Limite** : idée venue
après avoir vu le test, et encore dépendante de quelques gros gagnants (sans les 5 meilleurs trades : ~0 $).
Seul un test en direct peut la valider.

## 5. En cours
`scripts/flow_bot.py` tourne en **simulation (aucun argent)** sur le wallet : il applique F1 et le filtre
« forme » aux achats en direct et tient un journal (`data/paper/journal.jsonl`, `data/paper/summary.json`).

## Fichiers
* `scripts/flow_backtest.py` — données, indicateurs, simulateur ; `flow_rule()` = F1
* `scripts/flow_report.py` — le rapport ci-dessus (`flow_report.py <wallet> <fenêtres> --split 2026-10-10`)
* `scripts/flow_bot.py` — le bot de simulation en direct
* `analyses/flow_F1_par_jour.png` — résultat jour par jour
