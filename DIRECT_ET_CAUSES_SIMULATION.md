# Mode direct et causes des decisions incompletes — 2026.11.16.1

Cette mise a jour traite deux points observes dans les captures de Wallet Radar. Aucun achat reel n'est active.

## Fin de l'affichage massif du rattrapage

Les lanceurs Windows utilisent maintenant **`--live-only`** : seules les nouvelles transactions du WebSocket sont traitees. Le moteur ne lance ni collecte historique ni worker de decodage des anciennes files. Les anciennes donnees SQLite restent conservees, sans etre supprimees ou rejouees dans la console.

La banniere affiche **`mode DIRECT UNIQUEMENT, rattrapage desactive`**. Cela libere les lectures consommees par les anciennes transactions. En contrepartie, les transactions manquees pendant une interruption ne sont pas recuperees par ces lanceurs. C'est adapte a l'essai en direct sur les fenetres de 1 et 3 secondes, qui ne peut pas exploiter les vieux signaux.

L'option est egalement utilisee par la surveillance normale du menu 1. Les menus 1 et 3 transferent les scripts avant le demarrage pour eviter un decalage de version. Un lancement manuel sans `--live-only` conserve l'ancien mode de reconciliation ; ce n'est pas le comportement conseille pour cet essai.

## Operations annexes et ventes apres la premiere seconde

L'ancien code marquait le volume comme incomplet lorsqu'une notification non decodee contenait n'importe quelle instruction. Cela incluait des creations/fermetures de comptes et pouvait donc produire un faux avertissement de donnees manquantes.

Le code distingue maintenant :

- **Initialisation, fermeture de compte ou simple transfert recu sur le token :** ces operations seules ne constituent pas un achat manque et n'invalident plus le compteur de volume.
- **Vente seule apres la premiere seconde :** elle n'invalide pas le volume d'achat et n'annule pas la decision, conformement a la regle retenue.
- **Vente ou mouvement de position de BBIG BOSS non decode pendant la premiere seconde :** la verification de position reste incomplete ; le programme ne presume pas qu'il a conserve sa position.
- **Achat, swap, route non decode ou logs tronques :** la possibilite d'un volume manque reste signalee.

Cette correction a ete reproduite par des tests controles. Les anciennes captures ne contiennent pas assez de traces pour prouver que leurs cas DONNEES_INSUFFISANTES venaient de ce defaut : les anciens bilans ne sont donc pas reclasses retroactivement.

## Causes visibles dans les prochains bilans

Les notifications et le journal ajoutent maintenant une liste `missing_reasons`. Le menu 4 affiche les raisons disponibles dans le message : cours SOL/USD absent ou perime, devise de cotation non supportee, position non validee, autre compte non nul, preuve tardive, achat/swap non decode ou coupure de flux.

Un resultat incomplet reste incomplet. La correction ne retire pas les controles pour fabriquer un feu vert. Les anciens evenements n'ayant pas enregistre leurs raisons detaillees restent affiches avec leur ancien message.

## Installer

1. Dans Wallet Radar, choisis **5** pour arreter proprement la surveillance du VPS. Attends sa confirmation, puis quitte les anciens menus avec **0**.
2. Extrais ce correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`, en acceptant les remplacements. Conserve la configuration, les donnees et l'extension Chrome.
3. Garde Chrome ouvert, puis lance **WALLET_RADAR.cmd** et choisis **3**.
4. Verifie la version **2026.11.16.1** et la mention **DIRECT UNIQUEMENT**.

Le redemarrage ouvre un nouvel essai de 30 tokens distincts. Les anciens essais restent dans `data/wallet_alerts_ws/paper_decisions.jsonl` ; le menu 4 montre le dernier essai. La regle de sortie totale dans la premiere seconde, le seuil 400 USD, la limite 3 secondes et le montant theorique 25 USD ne changent pas. Aucune cle API ni cle privee supplementaire n'est demandee.
