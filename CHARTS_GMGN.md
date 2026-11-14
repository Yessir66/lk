# Graphiques GMGN sur ton PC — version 2026.11.14.7

Le VPS reconnait deja le CA de chaque achat verifie. Le lanceur Windows recoit ces achats sur une connexion SSH et ouvre directement `https://gmgn.ai/sol/token/CA` dans ton Chrome Windows. Il ouvre GMGN au demarrage pour preparer le navigateur. Il n'utilise ni API GMGN ni recherche par copier-coller.

Un nouvel onglet est demande pour chaque nouvel achat verifie recent, meme si le token a deja ete achete auparavant. Un meme achat ne doit pas rouvrir un onglet apres redemarrage : le VPS garde son journal et Windows conserve les signatures deja ouvertes dans `%LOCALAPPDATA%\WalletAlertsGMGN\opened-buys.txt`. Une transaction avec plusieurs wallets suivis achetant le meme token n'ouvre qu'un onglet pour ce token et cette signature.

Les ventes et notifications provisoires n'ouvrent aucun onglet. Les achats retrouves par rattrapage peuvent ouvrir leur graphique uniquement s'ils datent de moins de 120 secondes. L'historique plus ancien ne doit pas ouvrir une serie d'onglets. L'ouverture n'est pas garantie si la liaison SSH est coupee ; seuls les nouveaux achats traites pendant son fonctionnement arrivent au lanceur. Aucun systeme de relivraison des ouvertures manquees n'est ajoute.

## Installation

1. Dans le terminal SSH du VPS, arreter l'ancienne surveillance avec **Ctrl+C**. Ne pas lancer deux surveillances en parallele avec le meme journal.
2. Extraire le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`, en fusionnant les dossiers. Il ajoute un dossier `windows` et remplace seulement le script de surveillance. Conserver la configuration Helius, `.venv` et `data`.
3. Dans **PowerShell Windows**, a l'invite `PS C:\...>` :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

4. Sur Windows, ouvrir le dossier `wallet-alerts\windows` et double-cliquer **LANCER_CHARTS_GMGN.cmd**. Saisir le mot de passe SSH du compte `bot` si demande. A la premiere connexion, verifier l'identite du VPS avant d'accepter son empreinte SSH.

Le lanceur ouvre GMGN puis demarre la surveillance distante avec `--early --browser-events`. La fenetre doit afficher **2026.11.14.7**, **5/s**, puis **2 wallets ; 4 abonnements**. Ne pas relancer la commande habituelle dans une autre fenetre : le lanceur s'en charge. Si un service systemd utilise deja ce journal, il faut l'arreter avant ce mode interactif.

Garder **le PC allume, connecte et la fenetre du lanceur ouverte**. Chrome utilise son profil habituel par defaut. Une session GMGN dans un autre profil Chrome peut necessiter de se reconnecter dans celui ouvert par le lanceur. Aucun mot de passe ni cle Helius n'est envoye a GMGN par ce programme.

Pour arreter : **Ctrl+C** dans le lanceur. Si une connexion est interrompue, verifier que l'ancienne surveillance est terminee avant de relancer ; un verrou empeche de lancer deux instances avec le meme journal. Le lanceur ne se reconnecte pas automatiquement. Il affiche les erreurs SSH et les messages du serveur.

## Delai et limites

Le plafond et la priorite RPC de la version .6 sont conserves. Le lien du graphique part apres verification et enregistrement de l'achat, sans attendre une reponse GMGN. La liaison SSH, le demarrage de l'onglet et le chargement du graphique ajoutent un delai qui n'est pas inclus dans `--timing-report`. Les 0,04 seconde precedemment mesures ne representent pas le temps d'affichage du graphique.

Le chargement du graphique depend de GMGN et de ta connexion. Un message `CHART GMGN OUVERT` signifie que l'ouverture a ete demandee a Chrome, pas que le graphique a fini de charger. Une erreur ou verification GMGN peut toujours apparaitre.

**Aucun achat, ordre de vente, collage dans un formulaire de trading ou message Telegram n'est declenche.** Tu consultes le graphique et decides manuellement. Une cle API GMGN et un solde minimum GMGN ne sont pas necessaires a cette simple ouverture de lien.

Test local du protocole, sans SSH ni Chrome :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Enzo\Downloads\wallet-alerts\windows\OUVRIR_CHARTS_GMGN.ps1" -Test
```
