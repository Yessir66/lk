# Algorithme « flux » — paramètres figés avant le test sur les jours mis de côté

Wallet déclencheur : 5dzH7gh5FjtrxUwtfBufJyTBA4fyCUGheZsdYQsE9vag. Mise : 50 $ par achat copié.

Jours de développement (mise au point) : 10/10 → 19/10 (heure française).
Jours mis de côté, jamais regardés pendant la mise au point : 01/10 → 09/10.

## F1 (algorithme principal)
1. Copier uniquement les achats du wallet faits sur la courbe de lancement pump.fun (avant migration).
2. Entrer tout de suite (copie rapide, ~2 s).
3. À +2 min : si moins de 50 nouveaux wallets ont acheté depuis l'entrée → tout vendre.
4. Vendre 50 % à ×1,5.
5. Vendre le reste quand le flux de nouveaux wallets (par minute) tombe sous 30 % de son pic (après 5 min).
6. À +5 min : si le prix est sous ×0,85 → tout vendre.
7. Tout vendre à 45 min au plus tard.

Variantes testées en parallèle : F2 = F1 si le wallet achète ≥ 2,5 SOL ; F3 = F1 si notre prix d'entrée est < +10 % du prix du wallet.

Code : `scripts/flow_backtest.py` (`flow_rule()` avec ses valeurs par défaut), évaluation : `scripts/flow_report.py`.
Coûts simulés : frais 1,25 % pump.fun + 1 % bot par côté, glissement 2 % + impact de notre ordre, 0,21 $ de gas par transaction, vente exécutée 2 s après la décision.
