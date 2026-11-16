# Wallet Radar 2026.11.16.3 — cotations DOGE et TTWO

La simulation sait maintenant convertir les achats libelles dans les deux devises identifiees par le diagnostic :

| Devise | Adresse Solana exacte | Decimales | Source USD |
|---|---|---|---|
| TTWO, Take-Two Interactive Software (Backpack Securities) | `TTWofwAge91oFhZs7kpQdyrVRkmevgM88xijGvQFbKo` | 6 | CoinGecko, identifiant `take-two-interactive-software-backpack-securities` |
| DOGE, jeton Solana nomme Dogecoin | `DoGEV7LASBkQbibMc5k5vKnTZoMg423GpJ5QtJEGfm7R` | 8 | GeckoTerminal, bougie minute USD du pool `4n9rkpKkPUGFquBh6UXqNYbaCNyBcFq9VwNbutwGX5d6` |

Metadonnees des deux contrats recoupees sur Jupiter et GeckoTerminal ; TTWO egalement sur CoinGecko. Le nom DOGE ne suffit pas a prouver une parite avec le Dogecoin natif : seul le prix du contrat Solana exact est utilise.

TTWO rejoint la requete CoinGecko SOL/PUMP existante, toutes les 15 secondes. DOGE a une requete publique distincte toutes les 30 secondes, hors chemin de decision, sans cle ni appel par acheteur. Adresse du token explicite dans la requete OHLCV ; le token de base retourne doit correspondre exactement a DOGE. Si le pool devient indisponible ou ne correspond plus, le cours ne se renouvelle pas.

Les prix doivent etre positifs et finis. CoinGecko fournit `last_updated_at`. Pour DOGE, l'age est calcule depuis le **debut de la bougie minute**, pas depuis la reception HTTP : une bougie vieille de plus de 90 secondes est refusee. Le cours de cloture renvoye est une approximation de marche, pas un prix exact a la milliseconde ni une offre executable. Une bougie peut encore evoluer pendant sa minute. L'heure de reception HTTP n'est jamais utilisee pour rajeunir une vieille cotation.

Sans cours frais, les cas deviennent `COURS_DOGE_USD_ABSENT_OU_PERIME` ou `COURS_TTWO_USD_ABSENT_OU_PERIME`, plutot que `DEVISE_COTATION_NON_SUPPORTEE`. Les autres devises restent refusees. Aucun elargissement du delai : premiere seconde, seuil 400 USD hors transaction initiale et limite T0 + 3 s conserves. Les anciennes decisions ne sont pas recalculees.

## Installation

Si l'essai actuel doit aller a 30/30, le laisser terminer avant la mise a jour. Puis menu **5**, attendre l'arret, **0**, extraire le ZIP dans `C:\Users\Enzo\Downloads\wallet-alerts` en remplacant les fichiers, relancer `WALLET_RADAR.cmd`, menu **3**. La version affichee doit etre **2026.11.16.3**. Chrome et son extension restent ouverts.

Le redemarrage cree un nouvel essai, sans effacer les journaux anterieurs. Le menu 6 affiche les noms DOGE et TTWO pour ces adresses. La disponibilite et la fraicheur reelles des API depuis le VPS restent a observer.

Cette livraison reste en **simulation** : aucun achat reel, wallet de signature, envoi d'ordre ni revente automatique. Les essais reels demandes ensuite a 1 USD de tokens par achat font l'objet d'un travail distinct, avec frais et revente encore a definir.
