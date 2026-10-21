# Wallet 7BNaxx6K — règle figée avant le test sur un plus grand échantillon (nuit du 21 au 22/10/2026)

## Choisie sur
Les 10 tokens fournis par l'utilisateur le 21/10 (5 win, 5 loose ; `wallet_7BNaxx6K_stops.md`). Ils sont
exclus du test.

## Échantillon de test (sélection automatique, sans regarder les résultats)
* Toutes les transactions du wallet du 18/10 au 22/10 (getSignaturesForAddress + getTransaction).
* Pour chaque token : **seulement le premier achat du wallet** (les rachats sont ignorés).
* **Critère de l'utilisateur : le token a été créé 2 h ou moins avant ce premier achat.** Vérifié avec les
  trades pump.fun : aucun trade du token plus de 2 h avant le premier achat. Les tokens dont la création est
  antérieure au début de l'historique récupéré sont rejetés.
* Tokens sans données de trades pump.fun (autres plateformes) : exclus, et comptés.

## Règle figée
* Référence : le prix d'achat du wallet (son prix moyen d'exécution).
* Vente totale à **x2**.
* Sinon, vente **20 min après son achat** si x2 n'a pas été atteint.
* Stop de sécurité à −60 % (presque jamais déclenché sur les gagnants de l'échantillon de départ).
* 50 $ par token, frais 2,25 % par côté, glissement 2 % à la vente, 0,21 $ de gas par transaction.

## Comparaisons (pour information, non figées)
Sans stop (x2 ou prix à 30 min), stops −30 / −40 / −50 %, sorties à 10 / 15 / 30 min, et la même règle avec
une entrée de copie (premier trade 2 s après celui du wallet) au lieu de son prix.

## Critère de succès
Total positif sur l'échantillon de test et meilleur que « sans stop » et que « stop −30 % ».
