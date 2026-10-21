# Wallet 7BNaxx6K — stop-loss avec prise de profit à x2 (21/10/2026)

Règles de l'utilisateur : copier seulement le premier achat du wallet sur un token créé depuis moins de 2 h,
vendre à x2. 7 tokens « win » et 5 « loose » fournis ; 10 analysables (96AsCWA8 et BeRERNw6 : pas de données
pump.fun). Prix trade par trade pendant 30 min après son achat, en multiples de son prix d'achat
(`scripts/extra/w7b_trades.py`, `scripts/extra/w7b_rules.py`).

| | x2 atteint après | pire baisse avant x2 | plus haut en 30 min | plus bas en 30 min |
|---|---|---|---|---|
| WIN Ety3BtYe | 2,4 min | −5 % | x4,92 | x0,77 |
| WIN Cbkg5w7V | 1,7 min | −12 % | x3,72 | x0,88 |
| WIN HuShD8S8 | 12,6 min | −14 % | x2,03 | x0,86 |
| WIN AyCogdFp | 6,1 min | −54 % | x4,33 | x0,46 |
| WIN 4QXsjfa8 | 16,2 min | −28 % | x2,31 | x0,69 |
| LOOSE AYW3zXYA | jamais | | x1,88 | x0,77 |
| LOOSE BqsbFtfb | jamais | | x1,02 | x0,84 |
| LOOSE 419RpM6K | jamais | | x1,02 | x0,34 |
| LOOSE Hz9oYp8r | jamais | | x1,20 | x0,13 |
| LOOSE 7YPcRjnM | jamais | | x1,24 | x0,43 |

Avec 50 $ par token (frais compris) : sans stop +107 $ (perdants vendus au prix à 30 min) ; stop −30 % +91 $
(coupe AyCogdFp) ; stop −60 % +108 $ (ne protège presque rien) ; **sortie si pas de x2 en 20 min : +162 $, les
5 wins gardés, pertes des perdants divisées par deux** ; 15 min : +159 $ (4 wins sur 5).
À confirmer sur davantage de tokens : 10 tokens d'une seule journée, et le seuil de 20 min est ajusté sur eux.

## Test sur un échantillon plus large (nuit du 21 au 22/10) — règle figée avant (`wallet_7BNaxx6K_preregistration.md`)
Premiers achats du wallet le 21/10 (04:00 → 24:00), sélection automatique : 242 tokens, dont 145 rejetés car
créés plus de 2 h avant son premier achat, 27 sans données pump.fun, 2 sans bougies ; **63 testés** (hors
tokens de mise au point). Bougies minute pump.fun (vérifiées contre les trades sur les tokens de départ : mêmes
plus hauts). NB : 3 tokens de la liste de départ ne respectent pas le critère des 2 h (HuShD8S8, 419RpM6K,
BqsbFtfb avaient des trades plus de 2 h avant son achat).

* x2 atteint en 30 min : **12 tokens sur 63 (19 %)** — la liste de départ (5 win / 5 loose) surestimait la
  réussite ; x1,5 : 31/63 ; x1,3 : 42/63.
* Avec 50 $ par token, depuis son prix d'achat :

| Règle | Total | Par token |
|---|---|---|
| **Règle figée : x2, sinon vente à 20 min, stop −60 %** | **−512 $** | −8,1 $ |
| Sans stop (x2 ou prix à 30 min) | −687 $ | −10,9 $ |
| Stop −30 % | −543 $ | −8,6 $ |
| x2, sinon vente à 10 min | −302 $ | −4,8 $ |
| Prix de copie (2 s après lui) avec la règle figée | −592 $ | −9,7 $ |

* Le stop au temps réduit les pertes mais ne rend pas la stratégie rentable.
* 144 combinaisons (TP x1,3 / x1,5 / x2, stop, sortie à 5-30 min, contrôle à 1-5 min) choisies sur la 1re
  moitié : **aucune n'est positive sur les deux moitiés**. Acheter seulement après confirmation (attendre 1 à
  5 min et acheter si le prix est au-dessus du sien) : aucune variante positive non plus.
* Seul indice : le prix 5 min après son achat sépare bien les tokens qui feront x2 (AUC 0,83), mais c'est
  presque mécanique (ceux qui font x2 le font vite) et ça ne donne pas de règle d'achat rentable.
