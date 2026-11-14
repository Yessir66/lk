# GMGN : garder le graphique charge dans un seul onglet

Cette extension remplace l'ouverture d'un nouvel onglet a chaque achat. Elle reutilise un onglet GMGN et conserve son graphique quand le meme token est achete a nouveau. Elle ne change ni la detection Helius, ni les ordres, ni les mesures du VPS. Le script serveur **2026.11.14.7 deja installe suffit** : aucun transfert `scp` ni nouvelle cle API n'est necessaire.

## Installation sur ton PC Windows

1. Arrete l'ancien lanceur avec **Ctrl+C** dans sa fenetre. Si la fenetre attend une touche apres l'arret, tu peux la fermer. Une seule surveillance doit tourner avec le journal du VPS.
2. Extrais le correctif dans **`C:\Users\Enzo\Downloads\wallet-alerts`** et accepte la fusion des dossiers et le remplacement du script Windows. Tu dois retrouver `chrome-gmgn` a cote de `windows`. Ne supprime pas tes autres fichiers, la configuration ni les donnees.
3. Dans `wallet-alerts\windows`, double-clique sur **`INSTALLER_EXTENSION_GMGN.cmd`**. Il installe un petit pont local pour ton compte Windows. Aucun droit administrateur, cle API ou mot de passe VPS n'est demande a cette etape.
4. Dans ton **Chrome habituel**, ouvre **`chrome://extensions`**. Active **Mode developpeur**, clique **Charger l'extension non empaquetee**, puis selectionne exactement :

```text
C:\Users\Enzo\Downloads\wallet-alerts\chrome-gmgn
```

5. L'extension **Wallet Alerts - GMGN onglet unique** doit apparaitre. Son identifiant attendu est `hiiecbdabaichplolfmoelhjlojbffje`. Garde ce dossier a sa place. Dans Chrome, le bouton des extensions permet de l'epingler pour acceder a ses controles.
6. Garde un onglet GMGN ouvert sur un token. Clique sur l'icone de l'extension puis **Utiliser l'onglet GMGN actif** pour choisir cet onglet. Sans selection manuelle, elle reprend un onglet GMGN existant ou en cree un s'il n'y en a aucun. Tu peux fermer les anciens onglets GMGN devenus inutiles.
7. Dans `wallet-alerts\windows`, double-clique desormais sur **`LANCER_GMGN_ONGLET_UNIQUE.cmd`**. Saisis le mot de passe VPS si SSH le demande et garde sa fenetre ouverte. Ne relance pas `LANCER_CHARTS_GMGN.cmd` en parallele.

Laisse Chrome ouvert dans le profil ou l'extension est installee. Le pont ne pilote pas les fenetres de navigation privee. Si le site demande une verification ou une connexion, fais-la normalement dans cet onglet.

## Fonctionnement et verifications

- **Achat repete du token deja affiche :** l'onglet est selectionne, son URL et son graphique ne sont pas recharges. La fenetre de l'extension affiche `Graphique conserve, aucun rechargement`.
- **Achat d'un autre token :** l'extension essaie le routeur interne de GMGN s'il est expose, ou un lien deja present vers ce token. Elle affiche `Navigation interne demandee` ou `Lien GMGN utilise` quand l'URL change.
- **Si cette navigation n'est pas disponible ou ne fonctionne pas :** elle charge l'URL du nouveau token dans le meme onglet et affiche `Meme onglet, chargement classique`. Ce cas peut encore prendre les 5-6 secondes observees. La navigation interne reelle de la version actuelle de GMGN doit etre verifiee sur ton PC ; elle n'est pas garantie.
- L'onglet reserve est marque comme ne devant pas etre decharge automatiquement par Chrome. Cela aide a le garder pret, sans rendre sa disponibilite absolue en cas de fermeture, panne ou manque de memoire.
- Les ventes et les notifications provisoires ne pilotent jamais l'onglet. Les achats transmis il y a plus de 120 secondes sont ignores. Les signatures deja traitees sont memorisees pour eviter les reouvertures apres reconnexion.

Dans la console, **`CA TRANSMIS A L'EXTENSION`** confirme l'ecriture dans le pont local. Cela ne prouve pas que le graphique est affiche. Dans l'extension, **`Connexion locale OK`** confirme seulement le pont Chrome/Windows, pas la connexion du VPS. Regarde aussi `WEBSOCKET CONNECTE` dans le lanceur.

Le pont est interroge environ toutes les 100 ms lorsque la connexion native est active. Ce temps s'ajoute au traitement et a SSH ; ce n'est pas une garantie de latence. L'extension mesure la duree de sa **commande d'affichage**, pas la fin du chargement du graphique. Le rapport `--timing-report` reste une mesure du serveur, hors Chrome.

Pour comparer, observe d'abord un achat repete d'un token deja ouvert, puis un changement vers un autre token. Envoie une capture de la petite fenetre de l'extension : son mode indique si le graphique a ete conserve, si la navigation interne a ete utilisee ou si un chargement classique a ete necessaire.

## Garder le graphique pendant ta decision

Le bouton **Mettre en pause les changements** conserve le token affiche. Les nouvelles detections restent dans la console, mais elles ne changent plus l'onglet. **Reprendre les changements** reactivera l'affichage pour les prochains achats ; ceux recus pendant la pause ne sont pas rejoues.

## Ce qui est installe

L'extension accede uniquement aux pages `https://gmgn.ai/*`, au stockage de ses reglages et au pont natif autorise pour son identifiant. Elle n'a pas acces a Helius, a une cle privee, ni a ton mot de passe SSH. Son code ne clique sur aucun bouton d'achat/vente et ne remplit aucun formulaire de trading.

Le pont Windows est copie dans `%LOCALAPPDATA%\WalletAlertsGMGN\nativehost`, avec une declaration sous `HKCU\Software\Google\Chrome\NativeMessagingHosts\fr.walletalerts.gmgn`. Il lit uniquement les identifiants publics des achats ecrits par le lanceur. Aucun serveur HTTP, port reseau ou service payant n'est ajoute.

Si la fenetre de l'extension indique que le pont manque : relance `INSTALLER_EXTENSION_GMGN.cmd`, verifie l'identifiant de l'extension, puis recharge l'extension depuis `chrome://extensions`. Les permissions d'extension doivent etre autorisees sur GMGN dans ce profil Chrome.

Pour revenir a l'ancien fonctionnement : arrete le lanceur, desactive l'extension, puis relance **`LANCER_CHARTS_GMGN.cmd`**. Pour retirer aussi le pont, apres avoir ferme Chrome et le lanceur, supprime uniquement la cle de registre `fr.walletalerts.gmgn` mentionnee ci-dessus et le sous-dossier `nativehost`. Conserve le reste du dossier `WalletAlertsGMGN` si tu veux garder l'historique des achats deja ouverts.
