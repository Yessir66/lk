# Analyse des signaux Discord : DECU, LE L et callouts pump.fun

Ce module complète **alert_scout**, décrit dans le document fourni par l’utilisateur (`alert_scout_pour_ia.md`, instantané c50ebba de la branche claude/quirky-archimedes-6j2dkq). Il ne remplace ni son extension Chrome ni son serveur. Il ne fait aucun achat, vente ni publication Discord/pump.fun. Les bilans sont envoyés au bot Telegram déjà configuré dans `scout_config.json`.

## Installation sur le PC où alert_scout fonctionne déjà

Copier les trois fichiers du module dans le dossier `scripts` existant :

- `scripts/discord_solana_watch.py`
- `scripts/discord_token_analysis.py`
- `scripts/discord_token_telegram.py`

Copier aussi `requirements-discord.txt`, `LANCER_ANALYSE_DISCORD.bat` et ce guide à la racine du projet. **Ne pas remplacer ni supprimer le dossier `scripts` existant : il contient `alert_scout.py`, absent de cette branche.** Conserver l’extension, `scout_config.json` et tout `data`.

Dans un terminal ouvert dans ce dossier :

```bat
py -m pip install -r requirements-discord.txt
```

`tzdata` permet d’afficher les heures de Paris sous Windows. Aucune clé Discord, Telegram ou OpenAI supplémentaire n’est nécessaire.

1. Laisser tourner `alert_scout.py` comme d’habitude, avec l’onglet Discord ouvert sur le salon **solana** et l’extension déjà configurée pour ce salon.
2. Ouvrir un deuxième terminal dans le même dossier et lancer :

```bat
py scripts\discord_solana_watch.py
```

Le lanceur `LANCER_ANALYSE_DISCORD.bat` exécute la même commande.

3. Ouvrir le rapport local :

```bat
start "" "data\discord_solana\reports\index.html"
```

La page se recharge toutes les dix secondes. Elle affiche les CA en attente, les bilans et les erreurs. Un fichier JSON par CA contient le détail des transactions et des callouts retrouvés. Les rapports locaux restent disponibles ; un bilan récapitulatif par token est aussi envoyé sur ton bot Telegram. Les notifications de l’ancien scout continuent indépendamment.

Si scout est dans un autre dossier :

```bat
py scripts\discord_solana_watch.py --scout-journal "C:\CHEMIN\VERS\PROJET\data\scout\journal.jsonl" --scout-config "C:\CHEMIN\VERS\PROJET\scout_config.json"
```

## Bilans sur Telegram

Le module réutilise **`telegram_bot_token`** et **`telegram_chat_id`** de `scout_config.json`. Les variables `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, si définies, les remplacent pour cette exécution. Aucune clé n’est affichée ou copiée dans le journal. Ne pas partager ce fichier de configuration.

À la fin de l’analyse, un message récapitule le CA, les heures de création et de signal, les bilans DECU et LE L (montants, quantités, ventes, pourcentages), le compteur de callouts et les limites constatées. Les pourcentages portent sur les quantités de tokens, pas sur le rendement. Si une analyse échoue, un message d’indisponibilité est envoyé plutôt qu’un faux résultat à zéro.

Les messages sont envoyés par le bot existant au destinataire existant : il ne s’agit pas d’un envoi à Trojan. Les réponses HTML de Telegram ne sont pas utilisées pour interpréter les données du token : le texte du bilan est envoyé en texte brut.

- **Au premier lancement de cette version, les bilans déjà terminés ne sont pas tous renvoyés.** Les prochains tokens sont notifiés automatiquement. Pour envoyer également les anciens rapports une fois, ajouter `--telegram-history`.
- Un identifiant de message est mémorisé pour chaque token. Au redémarrage, les messages confirmés ne sont pas renvoyés. Si `--retry-errors` produit ensuite un bilan corrigé, le message existant est édité.
- Les notifications utilisent une file séparée : un quota Telegram ne bloque pas la réception Discord ou l’analyse.
- Un HTTP 429 explicitement refusé est retenté après le délai demandé. Un timeout ou un arrêt pendant un nouvel envoi laisse la livraison **incertaine** ; aucun renvoi automatique n’est tenté pour éviter un doublon. Vérifier Telegram et le terminal dans ce cas.
- Après correction d’un envoi explicitement refusé (par exemple un bot bloqué), `--retry-telegram` permet de réessayer. Les livraisons incertaines ne sont pas relancées par cette option.
- La destination est liée à l’historique local. Pour changer de bot/destinataire, utiliser un autre `--state` afin de ne pas confondre les identifiants des messages.
- Si la configuration Telegram est absente, le terminal indique **rapports locaux uniquement**. Si elle est partielle, il indique les champs à compléter. Le bot doit avoir été démarré dans Telegram et être autorisé à écrire au destinataire.

Pour garder seulement les rapports locaux : `py scripts\discord_solana_watch.py --no-telegram`.

## Comment le signal est récupéré

Le lecteur suit **`data/scout/journal.jsonl`**, qui reçoit déjà un événement `type: alert`, `source: discord`, `mint`, `t_alert` au début de `scout()`. L’heure retenue est **`t_alert`**, pas `t0` (début de minute), ni l’heure d’analyse. Le CA a donc déjà été résolu par le lecteur existant, sans analyser de nouveau tous les liens du message. Le journal ne conserve pas l’identifiant du salon : vérifier que `SCOUT_CHANNELS` dans l’extension contient uniquement le salon **solana** (identifiant entre guillemets). Une liste vide surveille tous les salons ouverts et ne permettrait pas à ce module de les distinguer.

Au premier lancement, le module démarre à la fin du journal existant : il attend les prochaines alertes, sans traiter l’historique. Si le fichier n’existe pas encore, il attend sa création. Un curseur est ensuite enregistré : après redémarrage, les nouveaux événements ajoutés pendant son absence sont repris. Les lignes en cours d’écriture sont attendues et les CA sont dédupliqués de façon persistante.

Les événements `later_message`, `replay`, les mises à jour, résultats et messages venant de Telegram sont ignorés. Un CA déjà analysé ne déclenche pas un deuxième bilan. L’ancien scout peut avoir sa propre déduplication sur 24 h ; ce module conserve sa déduplication tant que son journal SQLite est conservé.

Pour rejouer le journal antérieur **au tout premier lancement d’un nouvel état**, ajouter `--from-start`. Cela peut lancer beaucoup d’analyses historiques et atteindre les limites du RPC. Ce n’est pas nécessaire pour le direct.

## Bilan des wallets

| Nom | Adresse publique |
|---|---|
| DECU | 4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9 |
| LE L | CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD |

Les deux wallets sont analysés **depuis la création du token jusqu’à l’heure du signal Discord**. Un achat après le signal n’est pas inclus.

Pour chacun : premier achat retrouvé, quantité de tokens reçue, montant payé dans chaque monnaie identifiable (SOL, USDC, USDT ou autre token de cotation), total des achats, total des ventes et signatures. Les pourcentages sont :

- **Tokens vendus / tokens du premier achat × 100** : pourcentage par rapport à la quantité initiale.
- **Tokens vendus / tous les tokens achetés × 100** : utile s’il y a eu des achats supplémentaires.

Exemple : 100 tokens achetés, puis 100 supplémentaires, puis 50 vendus → **50 % du premier achat**, **25 % du cumul acheté**. Un pourcentage sur le premier achat peut dépasser 100 % s’il y a eu plusieurs achats ; ce n’est pas automatiquement une erreur. Il ne s’agit pas du pourcentage du capital récupéré en SOL/USD ni d’un rendement.

Un achat/une vente doit avoir une transaction réussie, signée par le wallet, un mouvement du token et une contrepartie économique dans une transaction de swap identifiée. Les transferts, cadeaux ou mouvements non attribuables sont signalés séparément. Les montants SOL retirent les frais réseau et corrigent les loyers de comptes ; ils peuvent encore inclure des pourboires ou d’autres mouvements groupés. Les monnaies de cotation différentes ne sont pas additionnées entre elles. Aucune conversion USD arbitraire n’est faite.

Si l’historique est incomplet ou si plusieurs premiers achats sont dans le même slot sans ordre établi, le pourcentage sur la mise initiale est indisponible. Les autres totaux restent ceux des transactions retrouvées et sont marqués comme partiels.

## Callouts

Le module compte les callouts dont l’heure est comprise entre la création du token et `t_alert`, bornes incluses. Il croise la liste des meilleurs callouts avec les positions ouvertes (`LATEST`) et fermées (`CLOSED_PNL`), parcourt les pages et déduplique les identifiants.

Le résultat est toujours **un minimum public retrouvé**. Les publications supprimées, masquées, filtrées ou absentes de l’API ne peuvent pas être garanties. Les totaux annoncés par le serveur ne sont pas utilisés comme nombre de callouts. Les réponses, updates et simples reposts ne sont pas comptés.

Si l’API pump.fun répond 403/429 ou échoue, le rapport indique une indisponibilité ou un résultat partiel ; **il ne remplace pas une erreur par zéro**. Les wallets restent analysés indépendamment sur Solana. Si l’heure de création ne peut pas être obtenue, elle reste indisponible : le scan borné du wallet porte alors sur l’historique accessible avant le signal, sans prétendre avoir vérifié cette heure. Un compteur exact et exhaustif ne peut pas être promis avec cette API publique.

## Accès RPC, délai et limites

Le module réutilise seulement l’URL RPC / la clé Helius de ton `scout_config.json`, sans l’afficher ni la recopier. Les variables `SOLANA_RPC_URL`, `HELIUS_API_KEY`, ou `--rpc` permettent aussi de sélectionner un accès. Aucune clé privée de wallet n’est lue.

Le RPC est limité à **2 requêtes/s par défaut**. Ce quota est propre au processus et s’ajoute aux requêtes de l’ancien scout. Avec un abonnement Helius à 10/s, régler par exemple **l’ancien scout à `rps: 7`** et ce module à **2/s**, pour garder une marge. Il faut compter aussi les autres programmes utilisant la même clé. Les deux processus ne partagent pas automatiquement leur limiteur.

Par défaut, le scan est limité à **300 signatures par wallet** et **40 pages par catégorie de callouts**. Augmenter uniquement si le rapport indique un historique tronqué et si le quota le permet :

```bat
py scripts\discord_solana_watch.py --max-signatures 1000 --max-callout-pages 80 --retry-errors
```

`--retry-errors` retente les analyses en erreur ou incomplètes. Sans cette option, elles restent enregistrées et ne sont pas relancées à chaque message. Les transactions déjà récupérées sont mises en cache.

La lecture Discord et l’analyse tournent séparément : une analyse longue n’arrête pas la réception des nouveaux signaux, qui sont mis en file. Un rapport n’est **pas garanti en quelques secondes** : il dépend du nombre de transactions à lire, des quotas et de la disponibilité de pump.fun. Si chaque analyse prend trop longtemps par rapport à l’arrivée des signaux, une file d’attente se forme.

Les heures de blocs Solana sont à la seconde. Une transaction dans la seconde du signal est signalée comme ambiguë : son ordre exact par rapport au message Discord n’est pas établi.

Les limitations de l’extension existante restent valables : onglet ouvert, messages effectivement rendus, pas de PC en veille. Le journal scout ne conserve pas l’identifiant Discord d’origine ; ce module conserve l’offset de l’événement comme référence et utilise l’heure transmise par scout.

Les sources alternatives `--source bot`, `--source browser` et `--source jsonl` existent pour une autre installation ; **elles ne sont pas nécessaires avec ton alert_scout**. Le mode bot utilise uniquement un jeton de bot officiel (`DISCORD_BOT_TOKEN`), le mode browser nécessite Playwright/CDP localhost, et le mode JSONL importe explicitement un fichier de messages Discord. Aucun jeton de compte utilisateur n’est utilisé.

## Vérification

Tests automatisés : déduplication persistante, reconnexion au journal, lignes partielles, respect de l’heure du signal, calculs de pourcentages, loyers SOL, transferts, cotations non-SOL, erreurs fournisseurs et absence de RPC d’écriture. Le parseur a aussi été confronté aux deux transactions réelles MACBOOK déjà étudiées. Cela ne constitue pas une validation sur ton salon Discord réel : celle-ci se fait à la prochaine alerte captée sur ton PC.
