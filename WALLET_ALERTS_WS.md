# Alertes de wallets par WebSocket — 2026.11.14.4

Programme indépendant du bot de republication. Il suit **DECU** (`4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`) et **LE L** (`CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD`) par défaut. Aucun achat, ordre Trojan, appel pump.fun, GMGN ou IA. Aucune clé privée de wallet n’est nécessaire.

Le programme reçoit des notifications Solana `logsSubscribe` en **confirmed**, puis lit chaque transaction avec `getTransaction`, version maximale 1, au même niveau de confirmation. Une alerte d’achat ou de vente demande un swap signé par le wallet, un changement de solde et une contrepartie économique avec attribution du transfert au swap. Un transfert seul ne devient pas un achat. Les programmes de swap inconnus et certains swaps complexes peuvent rester non reconnus ; les données ambiguës restent locales.

Il s’agit d’une détection **après exécution de la transaction**, pas d’une prédiction ni d’un accès aux transactions avant leur diffusion. Aucune latence garantie avant mesure sur le fournisseur réel.

## Installer sur le VPS Hostinger

Extraire l’archive sur le PC dans un dossier simple, par exemple `C:\Users\Enzo\Downloads\wallet-alerts`. À la racine doivent se trouver `scripts`, `deploy`, le fichier d’exemple et `requirements-wallet-alerts.txt`.

Dans **PowerShell Windows**, copier le dossier vers le serveur (ne pas exécuter cette commande à l’invite Linux `bot@…`) :

```powershell
scp -r "C:\Users\Enzo\Downloads\wallet-alerts" bot@2.24.62.69:/home/bot/
```

Dans la **fenêtre SSH du VPS**, exécuter les commandes suivantes **une à la fois**, en attendant le retour de l’invite entre chaque commande :

```bash
cd /home/bot/wallet-alerts
```

```bash
chmod 700 /home/bot/wallet-alerts
```

```bash
python3 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements-wallet-alerts.txt
```

```bash
.venv/bin/python scripts/configure_wallet_alerts.py
```

Cette dernière commande demande **localement** la clé API Helius, sans affichage. La coller dans le terminal, puis valider avec Entrée. Ce n’est pas une clé privée de wallet. Ne pas la coller dans le chat. Elle est enregistrée dans `wallet_alerts_config.json`, fichier ignoré par Git et créé avec accès réservé au propriétaire sur Linux. Les URL Helius sont déduites de la clé ; ne pas ajouter de clé dans une commande de lancement.

Validation sans connexion :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --check
```

Premier lancement, **console uniquement** :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py
```

Attendre `WEBSOCKET CONNECTÉ : 2 wallets ; 2 abonnements.` Les achats et ventes vérifiés s’affichent dans ce terminal quand ils surviennent. Ctrl+C arrête le test. Ces événements sont indépendants des callouts : une transaction peut exister sans aucun callout et inversement.

## Mesures et conservation

- Journal séparé : `data/wallet_alerts_ws/state.sqlite3`. Ne pas le supprimer entre les relances : il conserve déduplication, curseurs et transactions décodées.
- Un même couple wallet/signature n’est décodé qu’une fois avec succès. Les mouvements de plusieurs tokens restent distingués. Le même token peut générer plusieurs alertes pour plusieurs véritables achats/ventes distincts.
- Chaque trade sauvegarde réception initiale, réception confirmée, fin de décodage, délai notification→décodage et délai approximatif blockTime→décodage. Le blockTime Solana est une référence grossière à la seconde ; il ne permet pas de mesurer précisément des écarts de quelques millisecondes. Ces chiffres n’incluent pas la réception sur le téléphone Telegram.
- `--early` ajoute des abonnements `processed` pour mesurer l’arrivée précoce, mais aucun trade n’est alerté avant lecture confirmée. Il double le nombre d’abonnements ; vérifier les limites du forfait. Les événements provisoires jamais confirmés n’envoient rien.
- Rattrapage au branchement et toutes les 60 secondes ; premier démarrage initialise une référence sans rejouer l’historique. Après une coupure, le rattrapage remonte jusqu’au curseur sauvegardé, avec au maximum 10 000 signatures par wallet. Une fenêtre trop grande est signalée et inscrite dans la table `gaps` : l’exhaustivité ne peut pas être garantie.
- Le rattrapage, les mouvements ambigus et les vieux trades sont conservés localement. Les événements retrouvés par rattrapage sont étiquetés comme tels. Les nouvelles notifications WebSocket ont priorité sur le décodage de l’historique.
- Les transactions pas encore lisibles sont réessayées jusqu’à 12 fois, avec attente croissante ; ensuite elles restent `unavailable`. Le réseau se reconnecte automatiquement. HTTP 429 déclenche une pause RPC. Les URL, clés et textes des exceptions réseau ne sont jamais imprimés.
- Le transport RPC utilise exclusivement `getSignaturesForAddress` et `getTransaction`. Les quotas Helius sont partagés avec les autres programmes qui utilisent la même clé. Un ancien bot qui reçoit 403 sur pump.fun ne gêne pas ce service indépendant.

## Alertes Telegram, après le test console

La commande `.venv/bin/python scripts/configure_wallet_alerts.py --telegram` demande le token du bot avec saisie masquée puis le Chat ID, en conservant la clé Helius. Elle n’envoie aucun message. Autre possibilité : ajouter au fichier **local** `wallet_alerts_config.json` les champs `telegram_bot_token` et `telegram_chat_id` existants, ou fournir `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans l’environnement du service. Ne pas utiliser la destination Trojan : ce programme utilise la Bot API de ton bot d’alertes, sans session Telegram utilisateur et sans ordre de trading.

Lancer explicitement :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --telegram
```

Les alertes sont limitées aux transactions confirmées de moins de 120 secondes au moment de l’envoi. Les trades historiques ne sont pas rejoués lors de l’activation de Telegram. L’envoi utilise une file indépendante pour ne pas ralentir le décodage.

Les alertes sont persistées avant envoi. Après une coupure ou une erreur d’envoi ambiguë, elles restent `uncertain` et **ne sont pas renvoyées automatiquement**, pour éviter les doublons. Cela peut sacrifier une alerte lors d’un incident ; vérifier Telegram avant toute reprise manuelle. Une alerte encore en attente qui vieillit au-delà de 120 secondes expire. Le journal est lié à la destination Telegram : changer de destination demande un autre `--state`.

## Fonctionnement continu avec systemd

Le fichier fourni démarre en **console uniquement** et ne lance pas le bot de republication. Après validation du test, arrêter le lancement manuel avant de démarrer le service pour éviter un conflit de verrou.

```bash
sudo cp deploy/wallet-alerts.service /etc/systemd/system/wallet-alerts.service
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now wallet-alerts
```

```bash
journalctl -u wallet-alerts -n 30 --no-pager
```

Pour Telegram, modifier ensuite `ExecStart` dans le fichier du service pour ajouter `--telegram`, puis faire `daemon-reload` et `restart wallet-alerts`, uniquement après configuration des identifiants locaux. Sans cette option, aucun message n’est envoyé.

Le service continue après déconnexion SSH et redémarre avec le VPS. Aucun port entrant supplémentaire, Chrome ou bureau distant n’est nécessaire. Il ne remplace pas encore la recherche d’historique du bot Discord ; ce rapprochement sera une modification séparée après mesure de fiabilité.

## Validation

250 tests passent dans le dépôt, dont 29 tests WebSocket : protocole d’abonnement simulé, transaction DECU publique connue en version 1, vente, transferts non qualifiés, processed sans confirmation, doublons, rattrapage et couverture partielle, conservation des erreurs sans secrets, file Telegram indépendante et envois incertains. Aucun achat ni message Telegram réel envoyé. La connexion Helius et la latence réelle doivent encore être vérifiées sur le VPS avec la clé de l’utilisateur.

## Diagnostic de connexion

Après Ctrl+C, lancer `.venv/bin/python scripts/wallet_alerts_ws.py --diagnose`. Cette option teste une lecture HTTPS et un abonnement WSS une seule fois, sans surveillance permanente ni Telegram. Elle affiche les codes HTTP sans URL, clé ou contenu des exceptions. `--check` valide uniquement le format local, pas l’accès au fournisseur.

## Comparaison au centième de seconde

Voir [MESURE_PROCESSED_CONFIRMED.md](MESURE_PROCESSED_CONFIRMED.md). Lancer avec `--early` pour afficher les notifications provisoires en console et comparer leurs réceptions aux notifications confirmed. Les alertes Telegram restent réservées aux achats/ventes vérifiés après confirmation. Le rapport hors ligne se lance avec `--timing-report`. Les anciennes mesures ne sont ni effacées ni reconstituées artificiellement.
