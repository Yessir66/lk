# Alertes de callouts pump.fun vers Telegram

`scripts/callout_alerts.py` surveille les callouts des comptes suivis par `Lelouch_0x` et transmet les alertes au bot Telegram destinataire depuis ton compte Telegram.

## Configuration avec ton compte Telegram

Les identifiants d’application fournis sont intégrés. Le mode par défaut est `--transport user` ; il utilise Telethon (déjà installé dans cet environnement). Pour une autre machine :

```bash
python -m pip install Telethon==1.45.0
```

Le destinataire par défaut est `@callout44_bot`. Depuis `/workspace/lk` :

```bash
/workspace/lk-venv/bin/python -u scripts/callout_alerts.py
```

Au premier lancement, Telethon demande le numéro du compte, le code de connexion et le mot de passe de double authentification si activé. Effectuer cette connexion dans le terminal. La session est ensuite conservée dans `data/callout_alerts_telegram.session` pour les redémarrages. `--session CHEMIN` permet d’utiliser une autre session ; `TG_API_ID` et `TG_API_HASH` peuvent remplacer les identifiants intégrés.

La boucle Telegram reste active en arrière-plan pendant la lecture pump.fun. Les erreurs d’envoi laissent le message en attente. Le script ferme sa connexion à l’arrêt.

## Alternative : publier au nom d’un bot

Le mode Bot API reste disponible : définir `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`, puis lancer :

```bash
/workspace/lk-venv/bin/python -u scripts/callout_alerts.py --transport bot
```

Ce mode publie au nom du bot vers une conversation, un groupe ou un canal ; il ne nécessite pas de connexion au compte utilisateur.

L’option `--profile` accepte un nom, une adresse de wallet ou une URL de profil. Utiliser l’adresse du wallet si la recherche par nom échoue. Pour surveiller uniquement les callouts publiés par ce profil, utiliser `--mode self`.

Pour vérifier la lecture sans envoyer de message :

```bash
/workspace/lk-venv/bin/python -u scripts/callout_alerts.py --profile Lelouch_0x --dry-run
```

Arrêt avec Ctrl+C. Le processus doit être relancé après un redémarrage de la machine.

## Alertes et état

Chaque alerte contient l’auteur, le nom/symbole lorsque disponibles, le commentaire, la capitalisation au call, l’adresse copiable, les liens pump.fun et DexScreener et l’heure UTC. La capitalisation actuelle est ajoutée quand `/coins-v2/` fournit `usd_market_cap`. Les métadonnées indisponibles ne bloquent pas l’alerte ; une capitalisation manquante est affichée `?`.

Au premier lancement, seuls les callouts créés après le démarrage sont retenus. La base SQLite dans `data/` conserve le point de départ et les envois réussis entre les redémarrages. Les messages en attente sont réessayés après un échec. Une réponse Telegram perdue ou un arrêt entre l’envoi et sa mémorisation peut provoquer un doublon : l’API ne permet pas une garantie « exactement une fois ».

Le fichier d’état par défaut est distinct pour chaque profil, mode et chat ; le mode de simulation a son propre fichier. Pour deux bots différents publiant vers le même chat, préciser des `--state` différents. Ne pas lancer deux instances sur la même base. Ne pas supprimer cette base si l’on veut conserver la mémoire des envois.

Les abonnements sont relus toutes les dix minutes. Les profils sont interrogés successivement, à raison d’au maximum une requête pump.fun toutes les 1,1 seconde, puis une pause de cinq secondes (`--interval`). Le délai augmente avec le nombre d’abonnements et les requêtes de métadonnées. La lecture porte sur les 100 derniers callouts de chaque profil : une longue interruption ou une forte activité peut laisser des callouts hors de cette fenêtre. Un avertissement est affiché si la fenêtre est pleine.

## Réseau et validation

Accès HTTPS requis pour pump.fun : `frontend-api-v3.pump.fun`. Le mode Bot API utilise aussi `api.telegram.org`. Le mode utilisateur exige une connexion MTProto aux centres de données Telegram ; une autorisation HTTPS vers api.telegram.org seule ne suffit pas. Le contrôle depuis cette machine a échoué avec ConnectionRefusedError vers le centre de données Telegram (port 443). L’envoi par compte utilisateur ne peut donc pas encore démarrer ici ; exécuter le script sur une machine autorisant MTProto ou adapter la connectivité de cet environnement. Les routes pump.fun utilisées proviennent du script existant ; leur disponibilité et leurs formats peuvent changer. Une erreur 403 ou un challenge Cloudflare empêche la détection ; le script signale l’erreur sans remplacer les callouts par des achats on-chain.

La lecture réelle de l’API a été validée depuis cette machine : recherche du profil kreisi, lecture de ses 100 derniers callouts, récupération des métadonnées du token, production du message HTML et lecture de ses 7 abonnements. Le profil demandé `Lelouch_0x` a également été résolu et ses 25 abonnements ont été récupérés. Aucun envoi Telegram réel n’a été validé : la première connexion du compte reste nécessaire. Le destinataire configuré est `@callout44_bot`. Le fonctionnement complet reste à vérifier avec ton profil et un nouveau callout réel.

Tests locaux :

```bash
/workspace/lk-venv/bin/python -m unittest discover -s tests -v
```

Ils couvrent le format et l’échappement HTML, la pagination, le refus Telegram, la connexion/envoi/fermeture Telethon simulés, les métadonnées indisponibles, la reprise après échec et le dédoublonnage persistant.
