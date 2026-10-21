# Alertes du bot de volume Discord — règle figée avant le test (21/10/2026)

## Données de mise au point
* 173 alertes « nouveau token » du 20/10 13:16 au 21/10 12:50, et toutes les notifications de performance
  (x2, x3…) de la même période.
* **Mise au point** : les 90 premières alertes (20/10 13:16 → 23:33), trades de 6 min avant à 20 min après
  chaque alerte (`alerts_test.py`, `alerts_features.py --minutes 20`).
* **Test** : les alertes à partir du 20/10 23:36 (83 alertes), jamais regardées pour choisir la règle.

## Ce que la mise au point a montré
* Acheter chaque alerte perd : F1R −660 $ sur 90 alertes (achat 59 s après), −235 $ en achetant 2 min après.
* Un tiers des tokens ont leur plus haut dans la minute de l'alerte : l'alerte arrive souvent au sommet.
* Les chiffres du bot lui-même trient mal (KOL, smart money, avertissements : écarts faibles ou nuls).
* Acheter à la notification « x2 » du bot perd avec toutes les règles testées (−250 à −946 $ sur 41 tokens).
* Seul filtre positif sur les deux moitiés de la mise au point : **attendre 1 minute et n'acheter que si au
  moins 131 nouveaux wallets ont acheté pendant cette minute** (+62 $ sur la 1re moitié, +140 $ sur la 2e,
  18 alertes gardées sur 45 dans la 2e moitié).

## Règle figée
1. À l'alerte, ne rien faire.
2. 60 s après : compter les wallets dont le premier trade sur le token est un achat pendant cette minute.
3. Si ce nombre est ≥ 131 : acheter 50 $ 2 minutes après l'alerte (t + 119 s), sinon ignorer l'alerte.
4. Sortie F1R (`flow_backtest.runner_rule`), horizon 20 min.

Test : `python scripts/alerts_rule.py alerts.csv --after "20/10/2026 23:34" --minutes 20`.
Critère de succès : total positif sur les alertes de test, et meilleur que « acheter toutes les alertes à
+2 min ». Avec ~80 alertes, un résultat positif reste à confirmer sur d'autres jours avant tout argent réel.
