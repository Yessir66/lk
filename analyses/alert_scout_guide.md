# alert_scout : une fiche et un verdict sur chaque alerte du bot de volume

Quand le bot de volume poste un nouveau token sur Discord, `scripts/alert_scout.py` t'envoie sur Telegram :

| Quand | Quoi |
|---|---|
| ~2 s après l'alerte | **La fiche** : âge, launchpad, paire, MC, liquidité, holders, top 10, part du dev, tokens créés par le dev, score organique, achats/ventes 5 min, Dex payé, réseaux, mint/freeze authority. |
| ~10-20 s | **La note du récit** ajoutée à la fiche : accroche, timing, crédibilité, durabilité (sur 10), points forts et drapeaux rouges, par Claude, à partir de la description, du post ou profil X, du site et du Telegram. |
| fin de la minute après l'alerte + ~5-15 s | **Le verdict** : ✅ ENTRER, ❌ PASSER ou ⚖️ À LA LIMITE, la note sur 100 et les chiffres de la minute. |
| ensuite, 30 min | **Le suivi** (pour ENTRER et À LA LIMITE) : un message mis à jour chaque minute, et un nouveau message à chaque signal (sécuriser la moitié, sortir). |

L'outil n'achète et ne vend rien.

## D'où vient le verdict

L'étude des 173 alertes des 20 et 21/10 (`analyses/alerts_preregistration.md`, branche `claude/busy-lovelace-efrui4`) :

* acheter toutes les alertes perd de l'argent, et un tiers des tokens font leur plus haut dans la minute de l'alerte ;
* le seul filtre gagnant sur les deux moitiés des données : **au moins 131 wallets qui achètent le token pour la première fois pendant la minute qui suit l'alerte** (la minute qui commence à l'heure de l'alerte, à la minute près), entrée 2 min après le début de la minute de l'alerte.

C'est le verdict. Le script lit sur la blockchain toutes les transactions de cette minute sur les pools du token. Quand il ne peut pas tout lire à temps (tokens à plusieurs milliers de transactions par minute), il estime le compte et donne une fourchette. Si la fourchette chevauche 131, le verdict est **À LA LIMITE**.

La **note sur 100** est indicative :

| Partie | Points | Critères |
|---|---|---|
| Flux | 40 | les nouveaux acheteurs |
| Structure | 30 | top 10, part du dev, acheteurs de 50 $ ou plus, part des micro-achats, poids du plus gros acheteur, liquidité / MC |
| Récit | 30 | la note du récit |

Seul le seuil de 131 a été testé. Le reste est un point de départ, à recalibrer avec ton journal (plus bas).

Le **suivi** reprend les sorties F1R (`flow_bot.py`), avec le nombre de holders à la place des nouveaux wallets :

* x1,5 : sécuriser la moitié ;
* sortir si moins de 50 holders gagnés 2 min après l'entrée ;
* sortir sous x0,85 après 5 min ;
* sortir si le flux de holders tombe sous 30 % de son pic après 5 min (sous x3) ;
* sortir le reste à -50 % sous un plus haut au-dessus de x3.

## Installation (Windows)

0. **Le code.** Sur https://github.com/Yessir66/lk, choisis la branche `claude/quirky-archimedes-6j2dkq`, puis *Code* → *Download ZIP*, et décompresse (par exemple dans `C:\lk`). Avec git : `git clone -b claude/quirky-archimedes-6j2dkq https://github.com/Yessir66/lk.git`.

1. **Python 3.10 ou plus** (python.org, coche « Add Python to PATH » à l'installation), puis dans un terminal :

   ```
   pip install -U anthropic
   ```

   C'est seulement pour la note du récit. Sans ce paquet, tout le reste marche.

2. **Les clés.** Copie `scout_config.example.json` en `scout_config.json` à la racine du dossier, puis remplis :

   | Champ | Où le trouver |
   |---|---|
   | `helius_api_key` | Compte sur helius.dev, tableau de bord, *API Keys*. Avec l'offre gratuite, garde `"rps": 10`. Avec une offre payante, monte `rps` (50 pour Developer) : la minute sera lue entièrement plus souvent. |
   | `telegram_bot_token` | Sur Telegram, écris à **@BotFather**, `/newbot`, et copie le token. |
   | `telegram_chat_id` | Envoie un message à ton nouveau bot, ouvre `https://api.telegram.org/bot<TOKEN>/getUpdates` dans le navigateur et copie le nombre après `"chat":{"id":`. |
   | `anthropic_api_key` | Sur console.anthropic.com, *API Keys*. Le récit est noté par Claude Opus 5.5, en effort bas pour aller vite. Compte environ 3 à 6 centimes par alerte, soit 5 à 10 $ par jour à ~170 alertes par jour (estimation ; le coût réel est sur la page *Usage* de la console). 10 $ de crédit suffisent pour tester. Si le modèle refuse d'analyser un token, l'API passe automatiquement sur un autre modèle (option `fallbacks`). |

   `scout_config.json` contient tes clés : il n'est jamais envoyé sur GitHub (il est dans `.gitignore`).

3. **L'extension Chrome** (dossier `extension`).
   1. Ouvre `extension\config.js` et mets l'identifiant du salon d'alertes dans `SCOUT_CHANNELS`, par exemple `["1234567890123456789"]`. C'est le dernier nombre de l'adresse quand le salon est ouvert : `discord.com/channels/<serveur>/<salon>`.
   2. Dans Chrome, va sur `chrome://extensions`, active le **Mode développeur**, clique sur **Charger l'extension non empaquetée** et choisis le dossier `extension`.
   3. Si tu modifies `config.js` plus tard, clique sur la flèche ↻ de l'extension.

   Garde un onglet Chrome ouvert sur le salon d'alertes, dans Discord web et non dans l'application. L'onglet peut rester en arrière-plan.

   L'extension lit seulement ce que la page affiche. Elle ne clique et n'écrit rien sur Discord. Les règles de Discord interdisent en principe de modifier le client, c'est à toi de juger.

## Lancer

```
python scripts\alert_scout.py --test-telegram      (vérifie que le bot Telegram t'écrit)
python scripts\alert_scout.py --test-claude        (vérifie la clé et la connexion à l'API Claude)
python scripts\alert_scout.py                      (démarre ; Ctrl+C pour arrêter)
```

Avec le script lancé, `http://127.0.0.1:8765/` dans le navigateur doit afficher « alert_scout actif ».

* **Fiche à la demande** : envoie une adresse de contrat à ton bot Telegram. L'heure de ton message fait office d'heure d'alerte.
* **Arrêter les suivis en cours** : envoie `stop` au bot.
* **Un token tout de suite**, sans serveur : `python scripts\alert_scout.py --mint <CA> --console`.
* **Rejouer une alerte passée** : `python scripts\alert_scout.py --mint <CA> --at 23:02 --console` (ou `--at "25/10 23:02"`).
  Le verdict est exact (la minute est relue sur la blockchain), la fiche montre le token tel qu'il est maintenant,
  et le suivi est rejoué sur l'historique du prix (sans les signaux du flux de holders).

Chaque token n'est traité qu'une fois. Les messages suivants du bot sur ce même token (notifications x2, x3…) sont seulement notés dans le journal.

## Calibrer

Tout est enregistré dans `data\scout\journal.jsonl` : la fiche, le récit, les chiffres de la minute, le suivi. Le plus haut et le plus bas atteints depuis l'entrée, 1 h et 6 h après chaque alerte, y sont ajoutés automatiquement (rattrapés au prochain lancement si le script était arrêté).

```
python scripts\alert_scout.py --report
```

Cette commande affiche chaque alerte avec son verdict, sa note, sa note de récit et son résultat, puis, pour ENTRER, À LA LIMITE et PASSER, le plus haut médian et la part des tokens qui ont fait x2. Après quelques jours d'alertes, on pourra voir quels critères séparent vraiment les tokens qui font x2 des autres, et ajuster la note.

## Limites

* Discord peut changer sa page. Si l'extension ne transmet plus rien alors que le script tourne, il faudra adapter `content.js`.
* Sans clé Helius, le script utilise le RPC public de Solana : la minute après l'alerte n'est lue qu'en partie et l'estimation est grossière.
* Les tokens très actifs (des milliers de transactions par minute, souvent des bots de volume) sont estimés sur un échantillon. La fiche le signale (« estimé sur X/Y transactions »).
* La note du récit est l'avis d'un modèle sur ce que les liens du token montrent à l'instant T. Elle ne vérifie rien au-delà.
