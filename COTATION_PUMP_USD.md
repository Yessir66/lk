# Conversion des achats cotes en PUMP — 2026.11.16.2

Le diagnostic utilisateur a montre des evenements Pump.fun bien lus, sans octets inconnus, mais plusieurs transactions cotees dans un jeton autre que SOL ou USDC. Deux des quatre cas diagnostiques utilisaient **PUMP** :

`pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn`

Le programme lisait la quantite PUMP mais ne savait pas la valoriser en dollars ; ces achats pouvaient donc finir en DONNEES_INSUFFISANTES. La plateforme Pump.fun et sa devise de cotation PUMP sont deux notions distinctes.

## Correction

- **PUMP utilise 6 decimales**, verifiees dans les metadonnees publiques CoinGecko du contrat Solana exact.
- SOL/USD et PUMP/USD sont recuperes dans **la meme requete publique**, actualisee toutes les 15 secondes. Aucun abonnement supplementaire ni cle privee ne sont ajoutes.
- Prix positif, fini, horodate et age de 90 secondes maximum obligatoire. Pas de supposition « PUMP = 1 USD » ou « PUMP = SOL ».
- Les achats suivants sont convertis a partir de `quote_amount / 10^6 * PUMP_USD`. L'achat initial de BBIG BOSS reste exclu.
- Si le cours manque au moment d'une notification, l'achat est garde en attente de valorisation. Un cours frais recu **avant la limite de trois secondes** peut servir a le convertir. Les receptions initiales, le filtre d'une seconde et T0 sont conserves.
- Les achats valorises une fois ne sont pas recomptes et leur valeur n'est pas modifiee ensuite. La conversion tardive utilise le cours disponible lors de cette valorisation : c'est un volume USD indicatif, pas une reconstitution exacte du prix de chaque trade.
- Un prix arrive apres l'echeance ne produit jamais un achat theorique retroactif.
- Les autres devises inconnues restent non converties ; leur raison est explicite. Ajouter PUMP n'explique pas a lui seul le cas deja cote en SOL.

Sources publiques verifiees :

- [Metadonnees du contrat PUMP](https://api.coingecko.com/api/v3/coins/solana/contract/pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn)
- [Cours publics SOL et PUMP](https://api.coingecko.com/api/v3/simple/price?ids=solana,pump-fun&vs_currencies=usd&include_last_updated_at=true)

## Installation au prochain essai

Si tu veux terminer l'echantillon en cours a 30 tokens, laisse-le tourner avant d'installer. Les anciens resultats ne seront pas reclasses retroactivement : les achats non valorises n'avaient pas ete enregistres comme un historique complet permettant de reconstruire cette fenetre.

1. Dans Wallet Radar, choisis **5** et attends l'arret confirme. Quitte les anciens menus avec **0**.
2. Extrais le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts` et accepte les remplacements.
3. Garde Chrome ouvert avec l'extension, relance **WALLET_RADAR.cmd**, puis choisis **3**.
4. Le menu transfere les scripts et lance **2026.11.16.2**, en **DIRECT UNIQUEMENT**.

Ce redemarrage ouvre un nouvel essai de 30 tokens distincts. Les anciens journaux restent conserves ; le menu 4 affiche le dernier essai. Le menu 6 nomme maintenant la cotation **PUMP** lorsqu'elle correspond au contrat connu.

Le seuil 400 USD, le delai maximal de trois secondes, l'exclusion de bwam et le montant theorique 25 USD ne changent pas. Aucun ordre reel, frais de trading ou wallet de signature n'est active. Les tests controles valident le calcul et ses limites ; l'effet sur les prochains tokens du VPS reste a observer.
