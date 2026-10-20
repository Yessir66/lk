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
* Plusieurs wallets déclencheurs pour diversifier.
* Test en direct (bot de simulation) pour juger sur des données vraiment nouvelles.

## Test du rachat des gros gagnants (20/10) — rejeté
Idée : quand F1R a tout vendu et que le token repart, racheter 50 $ pour ne plus rater les gros gagnants.
Script : `scripts/reentry_test.py` (sortie : `analyses/rachat_sortie.txt`).

* Le potentiel existe : après la vente F1R, 22 tokens d'octobre et 21 de septembre montent encore à ≥ ×5
  (vs notre 1re entrée), 8 par mois à ≥ ×10.
* 2 442 réglages testés : déclencheur prix (×1,2 à ×3 de l'entrée), nouveau record, afflux de nouveaux
  wallets (40 à 150 en 1 min) ; gestion par stop −25/−35/−50 %, prise de profit à ×2, stop suiveur, ou F1R.
* **Octobre (mise au point) : aucun réglage positif** (médiane −795 $, validation croisée −407 $).
* Septembre : positif seulement grâce au token ×180 ; sans lui, aucun réglage positif (médiane −469 $).
* Pourquoi : les vrais gros gagnants rechutent de 50 à 77 % avant leur plus haut. Un stop les coupe ; sans
  stop, les nombreux faux départs coûtent plus que les rares gagnants ne rapportent (tenir 3 h :
  −2 481 $ en octobre). Racheter plus haut, c'est payer plus cher le même pari.
