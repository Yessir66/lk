# PumpSwap et budget45USD — 2026.11.18.1

Le plafond cumule passe a **45USD au total** (30USD puis15USD de plus). Les14,40USD deja reserves restent comptabilises, avec les anciennes signatures et les achats expires. Il reste30,60USD, soit **jusqu'a12reservations supplementaires de2,40USD**. Les achats restent a1USD ; SL-80%, TP+75%, sortie totale60s, glissement20% et filtre de reception simulee5% sont conserves. Les ventes ne recyclent pas le budget. Maximum18reservations au total, soit43,20USD.

## PumpSwap

Les evenements BuyEvent/SellEvent, y compris BuyV2/SellV2, sont decodes depuis les logs du programme PumpSwap reellement execute. Schemas compares au SDK officiel **@pump-fun/pump-swap-sdk2.2.0**, avec le champ quote_transfer_fee. Le pool est lu en lecture seule pour verifier son programme, discriminateur, mints et coin_creator. Le createur de la monnaie sert a l'exclusion bwam, et non le createur technique du pool.

Une nouvelle lecture de pool utilise le quota RPC partage du watcher. Cache60s/64pools, maximum8resolutions en attente et64evenements par pool. Un pool absent peut etre relu une seule fois ; les erreurs et429 ne sont pas reessayes ainsi. Pas de lecture de transaction par acheteur supplementaire. La reception WebSocket continue pendant la resolution.

Les heures de reception initiales sont conservees, et la decision utilise l'heure d'evaluation reelle : **aucune prolongation des fenetres1s/3s**. Un mouvement BBIG non encore identifie pendant la premiere seconde bloque la decision. Une vente totale pendant cette seconde annule le signal, meme si des achats sont aussi en attente. L'achat initial reste exclu du volume400USD, et les preuves de position initiale restent obligatoires.

Couverture : token base des pools verifiables et devises de cotation deja prises en charge. Les autres DEX, orientations inverses, formats inconnus ou pools incoherents peuvent rester inexploitables. Volume observe avec lacunes possibles avant l'abonnement ; aucun ancien signal rejoue. L'execution reelle passe toujours par les routes Jupiter controlees.

Diagnostic6 : affichage EVENEMENTS PUMPSWAP ; maximum2lectures de pool par transaction, soit16lectures au total pour quatre cas, au quota1/s.

## Installation

1. Consulter9 et attendre la cloture des positions/transactions en attente, puis choisir5 pour l'arret propre et0.
2. Extraire l'archive dans le dossier habituel wallet-alerts en remplacant les fichiers proposes. Conserver les configurations, cles et bases. Le nouveau fichier **scripts/wallet_radar_pumpswap.py** est necessaire ; le menu le transfere automatiquement.
3. Ouvrir WALLET_RADAR.cmd et choisir **8**.
4. Verifier **2026.11.18.1**, budget45USD et **CONDITIONS REELLES : flux Pump.fun et PumpSwap**.
5. Consulter9 : les14,40USD precedents doivent rester comptabilises sous le nouveau plafond. Le compteur des30tokens de conditions recommence ; le budget ne repart pas a zero.

## Verification

**424 tests Python passent**, y compris PowerShell natif. Menu et lanceur Windows testes avec SSH/SCP simules. Tests : schemas officiels, logs authentifies, formats/troncatures, pool et createur, cache/bornes, regroupement, horodatages, fenetres3s, vente premiere seconde, ordre des evenements en attente, preuve de position initiale, diagnostic sans mutation et extension du budget sans reset.

Un achat et une vente publics de BBIG BOSS sur le token du diagnostic **3Yq3ucTYekHXz9KS6qWsfygBh18sTjDaGMvMv5pkpump** ont ete verifies : evenements500/449octets integralement lus et quantites concordant avec les soldes du wallet. Fixtures publiques conservees dans les tests. Aucun ordre reel envoye depuis le cloud ; activation et nouveaux resultats PumpSwap sur le VPS encore a confirmer.

Sources : [IDL officiel](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json), [SDK officiel2.2.0](https://www.npmjs.com/package/@pump-fun/pump-swap-sdk/v/2.2.0).
