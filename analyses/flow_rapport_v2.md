# Gros gagnants, flops et test final sur septembre — 20/10

Wallet 5dzH7…, achats sur la courbe pump.fun, 50 $ par achat, prix suivis 3 h (trades 45 min + bougies minute).

## Ce qui a été essayé sur octobre (01→19/10, 286 achats)
| Idée | Résultat octobre | Validation croisée (4 blocs de jours) | Retenue |
|---|---|---|---|
| Sortie précoce des flops (prix, ventes du wallet, achats/ventes à 20-90 s) | +529 $ (vs F1 +387 $) | −139 $ (vs F1 +387 $) | non |
| Trois modes (sortie / normal / course) | jusqu'à +921 $ | +276 $ (vs F1 +419 $) | non |
| Température du marché (lancements pump.fun / h) | heures chargées un peu moins bonnes | — | non (signal faible) |
| **F1R** : au-dessus de ×3, le reste court avec un stop suiveur −50 % jusqu'à 3 h | +615 $ (vs F1 +419 $) | meilleur dans 1 bloc sur 4 | figé |

Détection rapide : en octobre, les 8 tokens ≥ ×10 avaient tous ≥ 50 nouveaux wallets acheteurs à +90 s ;
avec ≥ 150 nouveaux wallets à +90 s, 31 % des tokens finissaient ≥ ×5 et aucun n'était un flop.

## Test final : septembre (15→30/09, 258 achats, jamais regardé avant de figer)
| | Octobre (mise au point) | **Septembre (test)** |
|---|---|---|
| F1 | +419 $ | **−580 $** |
| F1 + filtre « forme » | +977 $ | **−354 $** |
| F1R | +615 $ | **−609 $** |
| F1R + filtre « forme » | +901 $ | **−338 $** |

Le filtre « forme » réduit les pertes dans les deux mois ; aucune variante n'est rentable en septembre.

## Pourquoi septembre perd
* Plus de flops : 33 % des achats (23 % en octobre), −1 668 $.
* Les très gros gagnants démarrent lentement : seuls 3 des 8 tokens ≥ ×10 passaient le contrôle des 2 min
  (8 sur 8 en octobre). Un token monté à ×180 n'a rapporté que +20 $ car il avait été vendu au contrôle.
  La règle « on repère les gros gagnants en 2 minutes » n'est donc pas stable d'un mois à l'autre.

## Pistes
* **Rachat** : si un token vendu au contrôle repart plus tard (afflux de nouveaux acheteurs, prix au-dessus
  de l'entrée), y revenir — pour ne plus rater les gros gagnants lents.
* Plusieurs wallets déclencheurs pour diversifier.
* Test en direct (bot de simulation) pour juger sur des données vraiment nouvelles.
