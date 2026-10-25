# Un seul bot pour les alertes et la republication

`callout_hub.py` remplace le lancement simultané de `callout_alerts.py` et `callout_repost.py`.

Un lecteur commun interroge le flux pump.fun, puis transmet chaque nouveau callout aux deux fonctions :

- Telegram reçoit les callouts des comptes suivis par Lelouch_0x, au format existant, vers @callout44_bot.
- Pour les 10 callers configurés, le second worker achète 2 USD et publie une des 10 phrases aléatoires, une fois l’achat confirmé et au plus tôt 15 secondes après l’original. Le TP à +50 % vend ensuite la totalité de la position.

Le worker d’achat, celui de Telegram et la lecture publique tournent séparément. Un envoi Telegram lent ne bloque pas un achat. Une attente de confirmation Solana ne bloque pas la détection ou les alertes.

## Windows : passer à la version commune

1. Arrêter les deux anciens scripts avec Ctrl+C.
2. Télécharger le ZIP actualisé de la branche GitHub. Remplacer les fichiers du projet existant en conservant son dossier `data`.
3. Ouvrir un terminal dans le dossier qui contient `requirements-repost.txt` et `scripts`.
4. Installer les dépendances :

```bat
py -m pip install -r requirements-repost.txt
```

Pour les alertes Telegram seules :

```bat
py scripts\callout_hub.py --alerts-only
```

Pour les alertes réelles et une simulation des décisions de republication :

```bat
py scripts\callout_hub.py
```

Pour activer aussi les achats et publications réels, après la configuration de `data/pump_wallet.txt` et `data/pump_token.txt` décrite dans [CALLOUT_REPOST.md](CALLOUT_REPOST.md) :

```bat
py scripts\callout_hub.py --live
```

Pour tout tester sans envoi ni achat :

```bat
py scripts\callout_hub.py --dry-run
```

Utiliser une seule fenêtre pour le hub. Les anciens scripts restent disponibles, mais les faire tourner avec le hub rétablirait les lectures redondantes.

## TP à +50 %

Le mode réel active automatiquement le TP du moteur de republication. Pour une entrée de 2 USD, une valeur minimale de vente cotée à 3 USD déclenche la vente complète de la quantité achetée par le bot, hors frais réseau. Le contrôle est prévu toutes les 5 secondes ; il continue après redémarrage grâce au journal, sans diffusion d’une nouvelle transaction tant que le résultat de la précédente reste inconnu.

```bat
py scripts\callout_hub.py --live --take-profit 50
```

L’option est déjà à 50 par défaut. Conserver `data`, puis remplacer les fichiers de code à la mise à jour. Les positions de l’ancien journal dépourvues de coût USD sont signalées et restent hors suivi TP. Voir [CALLOUT_REPOST.md](CALLOUT_REPOST.md) pour le calcul, les ventes précédant éventuellement la publication, et la reprise après erreur.

## Session et historique existants

Le hub recherche automatiquement la session Telegram dans le dossier `data` du projet et dans les emplacements de l’ancien téléchargement, notamment `%USERPROFILE%\data`. Il reprend le fichier d’alertes correspondant au même profil et au même destinataire. Les journaux de republication conservent leurs noms `repost_live.sqlite3` et `repost_simulation.sqlite3` dans le projet.

Si l’ancien dossier a été déplacé ou plusieurs copies existent, préciser les chemins :

```bat
py scripts\callout_hub.py --session "C:\Users\Enzo\data\callout_alerts_telegram.session" --alerts-state "CHEMIN_DU_JOURNAL_ALERTES.sqlite3" --state "CHEMIN_DU_JOURNAL_REPUBLICATION.sqlite3"
```

Le hub recharge les identifiants déjà enregistrés afin d’éviter les enrichissements et traitements redondants après un redémarrage. Ne pas supprimer les journaux pour changer de lanceur.

## Requêtes et délais

Le flux `/home-feed/new` est lu par une seule fonction, pour les deux usages. Son intervalle cible est de 2 secondes. Le minimum de 1,1 seconde entre les lectures publiques est conservé. Une lecture de profil de secours est prévue toutes les 15 secondes : priorité aux 10 callers, puis un autre abonnement toutes les six lectures de secours. Chaque réponse est partagée entre les deux usages, y compris pour un caller qui figure déjà dans les abonnements.

Quand le flux global est refusé ou indisponible, les profils sont interrogés plus souvent (intervalle de 2 secondes), et la source en erreur attend avant de réessayer. Les 429 provoquent une pause progressive de 120, 240, 480, 960 puis 1800 secondes ; un délai `Retry-After` plus long est respecté. Un succès remet la progression à zéro. Les pauses sur les listes ne sont pas annulées par le rafraîchissement des abonnements. La durée réelle dépend des réponses de l’API et du nombre de profils ; le flux contient une fenêtre limitée et ne garantit pas d’inclure tous les callouts. Les lectures de secours peuvent détecter certains callouts plus tard, et la limite d’âge de 60 secondes pour les achats reste appliquée.

Les métadonnées d’un même mint sont réutilisées pendant 30 secondes, tout en conservant celles présentes dans le nouveau callout lorsqu’elles sont disponibles. Les données du flux suffisent généralement pour produire l’alerte sans requête supplémentaire. Un signal d’achat est transmis avant un éventuel enrichissement de l’alerte.

Les requêtes nécessaires à une opération réelle subsistent : prix/cotation, transaction, confirmation RPC, contrôle du solde et publication authentifiée. En cas de réponse de publication perdue, le contrôle du profil personnel reste nécessaire pour retrouver le callout et éviter une publication répétée. Le partage concerne les lectures des callers, pas ces étapes d’exécution. Une limitation des lectures de profils ne suspend donc pas le suivi TP ni les confirmations, qui utilisent leurs propres API.

Réglages : `--feed-interval 2` et `--backup-interval 15`. La nouvelle architecture évite les lectures faites en double et utilise un seul programme pour répartir les requêtes. Aucun gain de latence mesuré sur le PC Windows n’est encore établi.

## Vérification

```bat
py -m unittest discover -s tests -v
```

Les tests vérifient la lecture unique et sa distribution, les filtres des abonnements et des 10 callers, le cache des métadonnées, le secours sur HTTP 403 et la poursuite de Telegram et du lecteur quand le moteur d’achat est bloqué. Les tests du moteur de transactions et des alertes restent exécutés.

Le mode `--dry-run` permet de vérifier le démarrage et les API publiques dans le cloud. La session Telegram et le wallet étant sur le PC, les envois réels et la republication du hub doivent encore y être validés.

## Messages HTTP 429

`HTTP 429` signifie que pump.fun limite les requêtes sur la route indiquée. Le hub affiche désormais la durée de pause des profils, tandis que le flux global continue tant qu’il répond. La bibliothèque conserve les délais `Retry-After` en secondes ou en date HTTP et les applique à toute la famille de routes concernée, même pour un autre wallet. Elle ne refait pas de requête sur cette famille avant l’expiration de ce délai.

La lecture des profils reste un secours : son ralentissement peut retarder la détection des callouts absents du flux. Les callouts découverts au-delà de la limite d’âge restent ignorés pour les achats. La disparition des 429 dépend de la limite appliquée par pump.fun ; le correctif réduit les sollicitations et respecte les attentes indiquées par le serveur.

## Achat confirmé mais « TP différé : HTTP 429 »

Cette erreur concerne les services d’exécution, distincts de la lecture pump.fun. La mise à jour affiche le nom précis du RPC ou de Jupiter, régule les requêtes de chaque fournisseur et partage les pauses entre positions. Les alertes Telegram et le lecteur public continuent indépendamment, mais un TP en attente de réponses de ces services ne peut pas être considéré comme exécuté.

Le journal d’achat/vente doit être conservé à la mise à jour. Voir la section « Limites du RPC et de Jupiter » de [CALLOUT_REPOST.md](CALLOUT_REPOST.md) pour les nouveaux messages et la configuration d’un accès adapté.

## Un achat n’a pas eu lieu après une erreur 500

Le délai annoncé est un minimum avant une nouvelle tentative, et non une garantie d’exécution. Tant que Jupiter ne fournit pas de transaction, il n’existe rien à diffuser. La limite d’âge de 60 secondes peut aussi conduire à abandonner le callout. Le hub affiche désormais `PRÉPARATION ACHAT`, `ACHAT SIGNÉ` et `ACHAT ABANDONNÉ` pour distinguer ces situations ; le nombre de préparations et la dernière erreur sont enregistrés.

Pour lire les 20 dernières entrées du journal réel, dans une seconde fenêtre terminal :

```bat
py scripts\callout_hub.py --status
```

Cette commande est en lecture seule. Elle ne se connecte ni à Telegram, ni au wallet, ni aux API, et n’affiche pas le contenu des transactions signées. Elle indique notamment si une signature d’achat est enregistrée, si la confirmation est connue et si la limite d’âge a provoqué l’abandon. Pour un ancien journal sans cause enregistrée, seul l’état disponible est affiché ; la cause historique n’est pas inventée.

Si le journal se trouve ailleurs, utiliser `--state CHEMIN`. Le mode `--status` ne crée pas de nouveau journal. Les entrées abandonnées restent dédoublonnées : ce diagnostic ne relance pas un ancien achat.

## Tester la construction pour un token sans achat

Après une erreur `/swap/v1/swap`, il est possible de comparer le comportement des API depuis le PC sans relancer le callout :

```bat
py scripts\callout_hub.py --check-swap ADRESSE_DU_TOKEN
```

Ce test lit le prix SOL, calcule le montant configuré (2 USD par défaut), demande une cotation puis une transaction non signée à Jupiter. Il utilise le même profil public et la variable `JUP_API_KEY` de la fenêtre terminal. Aucune clé privée n’est chargée, aucune signature n’est produite et aucune transaction n’est envoyée. Le journal n’est pas modifié. Le test de construction ne valide pas à lui seul une exécution ni un résultat financier.

Les réponses de construction contenant un `simulationError`, même en HTTP 200, sont désormais refusées avant signature. Un succès du test sur un ancien mint n’entraîne pas son achat et ne réactive pas une entrée `failed`.
