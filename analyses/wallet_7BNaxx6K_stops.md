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
