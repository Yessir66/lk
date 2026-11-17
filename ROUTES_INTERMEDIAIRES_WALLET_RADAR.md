# Wallet Radar 2026.11.17.1 — routes Jupiter avec intermediaires

Le dernier signal utilisateur avait valide les conditions de strategie, puis l'achat avait ete ignore avec `NO_ROUTES_FOUND`. La recherche imposait auparavant une route directe. Cette version autorise Jupiter a proposer aussi un trajet avec intermediaire, par exemple SOL → USDC → token, pour les achats et les sorties SL/TP.

## Ce qui change

- `onlyDirectRoutes=false`, `restrictIntermediateTokens=true` conserve, instructions Jupiter v1 explicitement demandees. Les intermediaires restent ceux admis par la restriction Jupiter ; aucune liste de tokens douteux n'est ajoutee manuellement.
- Un plan d'au plus huit segments doit relier la source a la destination, sans cycle ni branche isolee/sans issue. Une route directe reste acceptee.
- Les instructions de creation de comptes associes peuvent concerner un intermediaire du plan. Le proprietaire doit rester le wallet d'essai et l'adresse derivee doit correspondre au mint/programme token. Le cout de ces comptes entre dans le meme plafond de depot de 0,50 USD par cote.
- Les messages v0 peuvent utiliser jusqu'a quatre tables de compression d'adresses, lues en une requete RPC. Verification du proprietaire Solana Address Lookup Table, des donnees base64, de la taille, de l'absence de desactivation et de l'extension anterieure au slot lu. Aucun message fournisseur n'est signe aveuglement : le bot compile lui-meme les instructions controlees et les tables verifiees.
- Taille finale maximale toujours 1232 octets. Nouvelle variante de route/instruction non reconnue : refus. Le suivi des signatures persistantes ne change pas.

Les 1 USD par achat, budget cumule15 USD, enveloppe reservee2,40 USD, glissement20 %, plafonds reseau0,10 USD et depot0,50 USD restent identiques. Aucune entree apres trois secondes pour rattraper une preparation lente. SL -80 % et TP +75 % toujours d'apres notre entree reelle. Aucune nouvelle cle a saisir. Les signatures/positions precedentes et les enveloppes deja reservees sont conservees.

Une route avec intermediaire peut demander plus de comptes, de frais ou de temps. Elle sera ignoree si elle depasse les limites. `NO_ROUTES_FOUND` reste possible : un token recent peut ne pas encore avoir de route Jupiter exploitable. Ce correctif n'ajoute pas d'execution native directe Pump.fun et ne garantit pas l'achat du token de la capture precedente.

## Installation

1. Consulter **9** dans un second menu. Si une position est ouverte ou qu'une transaction attend sa confirmation, laisser le suivi actif et effectuer la mise a jour apres sa cloture ; arreter le processus suspend le SL/TP.
2. Quand aucune position n'attend de suivi : **5**, attendre l'arret, puis **0** dans les anciens menus.
3. Extraire le ZIP dans `C:\Users\Enzo\Downloads\wallet-alerts`, accepter les remplacements. Ne pas supprimer les configurations, fichiers prives ou journaux.
4. Relancer `WALLET_RADAR.cmd`, choisir **10** pour le controle sans ordre. Les cles existantes sont reutilisees.
5. Apres reussite du controle, choisir **8**. Banniere **2026.11.17.1**. Ce choix reprend le mode reel autorise.

Le redemarrage ouvre une nouvelle collecte de conditions ; il ne remet pas le budget de l'essai a zero. Chrome, l'extension et la fenetre du bot restent ouverts pour cet essai.

## Validation

Tests controles : comptes intermediaires propres acceptes, proprietaire externe ou mint absent du plan refuses ; cycles/branches deconnectees refuses ; tables d'adresses compressant bien les cles attendues ; mauvais proprietaire, desactivation ou table trop recente refuses ; chargement borne en lecture seule. Les tests existants couvrent toujours budget persistant, glissement, delai d'envoi et SL/TP.

Suite Python : 371 tests, 4 tests facultatifs de l'hote Windows ignores car PowerShell absent dans cet environnement ; les 367 autres passent. Aucune modification du lanceur Windows ou de l'extension dans ce correctif. Deux cotations publiques SOL→PUMP et SOL→DOGE ont retourne des plans valides de deux segments avec les nouveaux parametres ; ce sont des cotations, pas des transactions executees. Aucun ordre reel envoye depuis le cloud ; verification authentifiee sur VPS apres installation encore necessaire.
