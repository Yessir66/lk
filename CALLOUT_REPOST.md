# Achat et republication des callouts

> **Nouvelle stratégie :** voir [NOUVEAU_FONCTIONNEMENT.md](NOUVEAU_FONCTIONNEMENT.md) pour les trois callers, les délais achat/update 12/10 s, les images et le SL −50 %. Les descriptions de l’ancienne stratégie ci-dessous restent historiques.

Pour utiliser les alertes et la republication ensemble avec un lecteur commun, voir [CALLOUT_HUB.md](CALLOUT_HUB.md).


`callout_repost.py` surveille 0xEthan, chairman, schoen, Dior, trunoest, ely, HarryPaulDavies, sadcrissy, SossaDotKek et _logjam. Il utilise les adresses déjà présentes dans le dépôt, pour suivre les mêmes comptes même après un changement de pseudo.

Pour un nouveau token signalé par l’un de ces callers :

1. Détecter le callout via le flux public, complété par les listes de profils.
2. Calculer l’équivalent de **2 USD en SOL** avec le prix Jupiter courant, puis obtenir une cotation de swap.
3. Signer localement, mémoriser la signature, envoyer la transaction et attendre sa confirmation.
4. Vérifier la détention du token et la correspondance du compte pump.fun avec le wallet.
5. Publier une phrase choisie aléatoirement parmi les 10 textes ci-dessous sur **Lelouch_0x**, au plus tôt **15 secondes après le callout source**.

Le délai de 15 secondes est un minimum, pas une heure d’exécution garantie : détection, cotation, confirmation et indexation pump.fun peuvent retarder la publication. Les 2 USD représentent le montant converti à l’entrée du swap, hors frais réseau, frais de swap et création éventuelle du compte token. Les positions sont ensuite surveillées avec un TP par défaut à +50 %, qui vend 100 % de la quantité achetée par le bot.

Le script traite une seule fois un même mint : plusieurs callers signalant ce token déclenchent un achat et une publication uniques. Les callouts antérieurs au premier lancement ou découverts après 60 secondes sont ignorés par défaut. Les anciens callouts d’un premier lancement de simulation ne sont pas repris en mode réel : chaque mode a son état.

## Textes aléatoires

À chaque nouveau token retenu, une phrase est tirée au hasard, puis mémorisée pour les nouvelles tentatives et les redémarrages. Deux callouts successifs peuvent tirer la même phrase.

1. let's run it, seems like a good narra
2. narra looks clean on this one, sending it
3. like this narrative a lot, let's see where it goes
4. good narra, early enough, let's run it
5. this one has a real narrative, i'm in
6. grabbed a small bag early on this one
7. the timing on this one feels right, i'm in
8. chart looks healthy and the community is waking up
9. could see this ticker everywhere soon, taking a position
10. still early here, holding this one

## Windows

Télécharger le ZIP de la branche GitHub, l’extraire puis ouvrir `cmd` dans le dossier contenant `requirements-repost.txt` et le sous-dossier `scripts`.

```bat
py -m pip install -r requirements-repost.txt
py scripts\callout_repost.py
```

Cette commande démarre la **simulation de décisions** : elle lit les vrais callouts et affiche ce qu’elle ferait. Elle n’exécute ni transaction ni publication et ne simule pas une transaction sur la blockchain. Le bot d’alertes Telegram existant peut continuer à fonctionner dans une autre fenêtre.

## Configuration du mode réel

Dans ce même dossier, créer un dossier `data` avec :

- `pump_wallet.txt` : la clé privée du wallet correspondant au profil `Lelouch_0x`, exportée en base58, ou le tableau JSON de 64 octets au format Solana. Le script compare son adresse publique à celle du profil.
- `pump_token.txt` : le jeton de session du compte pump.fun. Dans le navigateur connecté à ce profil, ouvrir F12 → Application/Stockage → Cookies → pump.fun, puis copier la valeur de `auth_token` dans ce fichier.

Ces fichiers restent sur le PC, dans `data/` ignoré par Git. Ne pas envoyer leurs valeurs dans le chat. Le token est relu à chaque requête, ce qui permet son remplacement s’il expire.

Lancer ensuite :

```bat
py scripts\callout_repost.py --live
```

La clé doit ouvrir le wallet du profil et la session pump.fun doit correspondre à cette même adresse. Le wallet doit contenir les SOL nécessaires au montant et aux frais ; le contrôle conserve une réserve minimale de 0,005 SOL au moment de préparer chaque achat.

Le RPC public Solana est utilisé par défaut. Un endpoint personnel peut être configuré avec `--rpc URL` ou la variable locale `SOLANA_RPC_URL`. Les services Jupiter utilisent `https://api.jup.ag` ; si le service exige une clé, configurer localement `JUP_API_KEY`. Aucun token Telegram n’est nécessaire pour ce script.

Texte personnalisé au lieu du tirage aléatoire (le paramètre `{text}` reprend le texte source) :

```bat
py scripts\callout_repost.py --live --text "Call de {caller} : {text}"
```

Autres réglages : `--usd 2`, `--delay 15`, `--max-age 60`, `--slippage-bps 100` (1 %), `--wallet CHEMIN`, `--token CHEMIN`, `--profile NOM` et `--state CHEMIN`. Utiliser les deux fichiers Python `callout_repost.py` et `callout_alerts.py` du même dossier `scripts`.

## TP +50 % : vente de 100 %

Le TP est actif par défaut en mode réel, dans le script autonome comme dans le hub. À partir de la confirmation d’achat, le bot enregistre la quantité réellement reçue grâce aux soldes avant/après de cette transaction. Il suit cette quantité, sans ajouter les tokens du même mint déjà présents avant cet achat.

Pour un achat d’environ 2 USD, il prépare la vente de toute la position lorsque la valeur minimale de la cotation Jupiter atteint 3 USD. La cotation porte sur la quantité entière à vendre, inclut le minimum après slippage, et son résultat en SOL est converti au prix SOL/USD courant. Les frais réseau de la vente restent en plus : le déclenchement n’est pas une garantie de 50 % de bénéfice net ni d’un prix de règlement exact.

Le contrôle est prévu toutes les 5 secondes par position (`--tp-interval 5`). Le prix SOL/USD est partagé et mis en cache 5 secondes. Les soldes de tokens sont réutilisés pendant 15 secondes pour réduire les lectures RPC ; avant la signature d’une vente, une nouvelle lecture du solde est obligatoire. Aucun nouvel achat n’est effectué pour financer ou compléter une sortie. Si une partie de la position a été retirée du wallet, la quantité disponible est plafonnée à la quantité acquise et son coût de référence est ajusté proportionnellement.

La transaction de vente est signée puis enregistrée avant son premier envoi. Après une coupure ou un redémarrage, la même signature est contrôlée et les mêmes octets sont réutilisés. Seule une erreur de transaction confirmée permet une nouvelle préparation automatique. Une transaction expirée/non retrouvée est marquée à vérifier, pour éviter une vente répétée dont le résultat serait incertain.

Si le TP se déclenche avant la publication du callout, la vente prend la priorité ; la republication n’est pas poursuivie sur une position vendue. Le TP reste actif même si la publication pump.fun échoue après un achat confirmé.

Les achats créés par cette version mémorisent leur coût USD et reprennent leur TP au redémarrage. Les anciennes entrées sans coût USD enregistré sont signalées comme non suivies par le TP ; les positions extérieures au journal ne sont pas adoptées automatiquement. En mode simulation, le TP prévu est affiché mais aucune position ni vente réelle n’est créée.

Réglage explicite :

```bat
py scripts\callout_repost.py --live --take-profit 50 --tp-interval 5
```

## Reprise et erreurs

Le journal SQLite `data/repost_live.sqlite3` conserve les mints, la transaction signée et chaque étape. Il est distinct du journal de simulation. Une transaction signée est enregistrée avant diffusion ; les retries réutilisent ses octets et sa signature. Une perte de connexion ou un redémarrage ne déclenche pas un second achat.

En cas de réponse de publication perdue, le bot cherche le callout sur ton profil et ne reposte pas à l’aveugle. Après deux minutes sans résolution, il marque l’entrée `review` pour vérification manuelle. Une transaction expirée/non retrouvée est aussi laissée à vérifier, sans nouvel achat. Ne pas supprimer le journal ni lancer deux instances de republication simultanément.

Une session expirée ou un refus explicite 403/429 de publication entraîne une nouvelle tentative sans racheter. Une erreur d’achat empêche la publication. Une publication refusée après achat conserve le suivi TP de la position.

## Validation

Les tests vérifient le calcul USD/SOL, le délai minimal, le dédoublonnage, la signature avec solders, la reprise sans rachat, l’absence de publication après un achat échoué et la réconciliation d’une publication dont la réponse est perdue, le seuil exact du TP, la vente de la quantité acquise et la reprise des ventes signées.

```bat
py -m unittest discover -s tests -v
```

La lecture des callouts et les cotations publiques sont vérifiables sans fonds. L’achat réel et `/callout/create` restent à valider avec les fichiers de wallet/session sur le PC ; leur présence n’a pas été constatée dans l’environnement de développement. La disponibilité de ces API depuis le cloud ne garantit pas leur disponibilité depuis le PC.

## Limites du RPC et de Jupiter

Les erreurs d’exécution identifient le service et l’opération : par exemple `RPC Solana/getTokenAccountsByOwner : HTTP 429` ou `Jupiter/swap/v1/quote : HTTP 429`. Les URL RPC, clés API, en-têtes et réponses arbitraires ne sont pas affichés. Les erreurs JSON-RPC exposent seulement le code numérique et le nom de la méthode.

Le client espace les requêtes d’exécution : plafond local par défaut de 2 requêtes RPC/s et 0,5 requête Jupiter/s (une toutes les deux secondes, pour le forfait gratuit affiché en octobre 2026). Chaque fournisseur possède une pause partagée en cas de 429, même lorsque plusieurs positions sont suivies. Le délai augmente de 15, 30, 60, 120, 240 puis 300 secondes ; un `Retry-After` plus long est toujours respecté. Les jobs attendent ce délai au lieu de relancer leur étape toutes les 5 ou 10 secondes. Un refus JSON-RPC signalant une limitation est traité comme un 429.

Les paramètres `--rpc-rps` et `--jupiter-rps` doivent correspondre aux quotas de ton accès. Ce plafond local n’accorde pas de quota supplémentaire au serveur. Le délai de contrôle TP peut donc dépasser 5 secondes, notamment avec plusieurs positions ou pendant une pause fournisseur.

Si les limites persistent après la mise à jour :

- Pour un message `RPC Solana/...`, configurer un endpoint RPC personnel dans `SOLANA_RPC_URL` ou avec `--rpc`.
- Pour un message `Jupiter/...`, vérifier le quota du service et configurer `JUP_API_KEY` dans les variables locales Windows si le fournisseur le demande.

Renseigner les accès localement ; ne pas transmettre leurs valeurs dans le chat. Tant que les cotations ou les requêtes RPC nécessaires sont refusées, le bot ne peut pas confirmer le seuil ni garantir l’exécution du TP. Les transactions déjà signées et les positions restent dans le journal, pour reprendre sans achat ou vente supplémentaires créés à l’aveugle.

## État des achats et erreurs de préparation

`py scripts\callout_repost.py --status` lit le journal réel sans lancer de processus de trading. Le hub accepte la même option. La préparation, la transaction signée, la confirmation et l’abandon pour dépassement de la limite d’âge sont distingués. Lorsqu’un prochain réessai tomberait après cette limite, l’entrée est marquée abandonnée explicitement. La limite reste inchangée par défaut.

Les réponses d’erreur HTTP sont classées en catégories connues (simulation, route, fonds, frais, blockhash, délai). Le corps brut de réponse n’est pas affiché ; les secrets et en-têtes restent exclus des messages. Ces catégories aident à diagnostiquer un nouveau refus mais ne reconstituent pas le contenu d’une ancienne réponse déjà perdue.

`--check-swap ADRESSE_DU_TOKEN` permet aussi un test de construction non signée depuis le script autonome. Il ne charge pas de clé privée et ne diffuse pas de transaction, même si `--live` figure par erreur dans la commande. Les réponses HTTP 200 ayant une erreur de simulation Jupiter ne sont pas signées.
