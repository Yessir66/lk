# Algorithmes figés avant le test sur septembre (15/09 → 30/09), jamais regardé

Mise au point sur le 01/10 → 19/10 (wallet 5dzH7…, achats sur la courbe pump.fun, 50 $ par achat).

* **F1** (inchangé, `flow_rule()`) : contrôle à +2 min (≥ 50 nouveaux wallets), 50 % à ×1,5, reste sur
  essoufflement du flux (< 30 % du pic après 5 min), stop temps à +5 min sous ×0,85, tout vendu à 45 min.
* **F1R** (`runner_rule()`) : F1, mais dès que le prix atteint ×3, l'essoufflement ne s'applique plus et le reste
  suit un stop suiveur à −50 % sous le plus haut, jusqu'à 3 h.
* Chacun avec et sans le **filtre « forme »** : ne prendre un signal que si les 15 derniers trades simulés
  (clôturés) font une somme positive.

Résultats de mise au point (01→19/10, 286 achats, prix sur 3 h) : F1 +419 $, F1R +615 $ ; F1R ne bat F1 que
dans 1 bloc de jours sur 4 (le gain vient des tokens à ×17-×25).
Rejetés après validation croisée : sorties précoces des flops (−139 $ en validation croisée), algorithme à
trois modes (+276 $ en validation croisée contre +419 $ pour F1), filtre de température du marché (signal faible).
