# Alertes Discord filtrées : DECU, LE L — version 2026.10.31.5

Ce module complète **alert_scout**, décrit dans le document fourni par l’utilisateur (`alert_scout_pour_ia.md`, instantané c50ebba de la branche claude/quirky-archimedes-6j2dkq). Il ne remplace ni son extension Chrome ni son serveur. Il ne fait aucun achat, vente ni publication Discord/pump.fun. Une alerte est envoyée au bot Telegram configuré uniquement si un achat antérieur confirmé de DECU ou LE L est vérifié pour ce token. Les erreurs et les cas sans preuve restent locaux.

## Installation sur le PC où alert_scout fonctionne déjà

Copier les cinq fichiers du module dans le dossier `scripts` existant :

- `scripts/discord_solana_watch.py`
- `scripts/discord_token_analysis.py`
- `scripts/discord_token_telegram.py`
- `scripts/alert_scout_lecture_discord.py`
- `scripts/discord_token_narrative.py`

Copier aussi `requirements-discord.txt`, `LANCER_ANALYSE_DISCORD.bat`, `LANCER_LECTURE_DISCORD.bat` et ce guide à la racine du projet. **Ne pas remplacer ni supprimer le dossier `scripts` existant : il contient `alert_scout.py`, absent de cette branche.** Conserver l’extension, `scout_config.json` et tout `data`.

Dans un terminal ouvert dans ce dossier :

```bat
py -m pip install -r requirements-discord.txt
```

`tzdata` permet d’afficher les heures de Paris sous Windows. Aucune clé Discord, Telegram ou OpenAI supplémentaire n’est nécessaire.

1. **Arrêter l’ancien `alert_scout.py` et l’ancien module d’analyse.** Garder l’onglet Discord et l’extension configurée uniquement pour le salon **solana**. Ne plus lancer directement `alert_scout.py` en parallèle : ses anciennes fiches Telegram ne sont pas filtrées.
2. Lancer d’abord le module d’analyse dans un terminal du projet :

```bat
py scripts\discord_solana_watch.py
```

Le lanceur `LANCER_ANALYSE_DISCORD.bat` exécute la même commande. Le terminal doit afficher **Version analyse Discord : 2026.10.31.5** et **Filtre Telegram : achat antérieur vérifié de DECU ou LE L uniquement**.

3. Dans un deuxième terminal du projet, lancer le lecteur seul :

```bat
py scripts\alert_scout_lecture_discord.py
```

Ou ouvrir `LANCER_LECTURE_DISCORD.bat`. Ce lanceur charge ton `alert_scout.py` existant, conserve son serveur et l’extension, mais remplace son analyse par l’enregistrement du signal. Les fiches, récits, verdicts et notifications Telegram de l’ancien scout sont désactivés **en mémoire**, sans modifier son fichier ni les clés dans `scout_config.json`. L’ancien token Telegram reste disponible pour le module d’alertes filtrées. Le lecteur retrouve la véritable heure Discord depuis le snowflake du message ; il ne se fie pas à un éventuel remplacement par l’heure de lecture.

4. Ouvrir le rapport local :

```bat
start "" "data\discord_solana\reports\index.html"
```

La page se recharge toutes les dix secondes. Elle affiche les CA en attente, les bilans et les erreurs. Un fichier JSON par CA contient le détail des transactions et des callouts retrouvés. Les rapports locaux sont conservés pour tous les tokens ; seuls les tokens admissibles produisent une alerte Telegram. Les anciennes notifications non filtrées sont arrêtées par le lanceur lecteur seul.

Si scout est dans un autre dossier :

```bat
py scripts\discord_solana_watch.py --scout-journal "C:\CHEMIN\VERS\PROJET\data\scout\journal.jsonl" --scout-config "C:\CHEMIN\VERS\PROJET\scout_config.json"
```

## Bilans sur Telegram

Le module réutilise **`telegram_bot_token`** et **`telegram_chat_id`** de `scout_config.json`. Les variables `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, si définies, les remplacent pour cette exécution. Aucune clé n’est affichée ou copiée dans le journal. Ne pas partager ce fichier de configuration.

À la fin de l’analyse, **si et seulement si une preuve positive d’achat antérieur est disponible**, un message récapitule le CA, les heures de création et de signal, les bilans DECU et LE L (montants, quantités, ventes, pourcentages), le compteur de callouts et les limites constatées. Les pourcentages portent sur les quantités de tokens, pas sur le rendement. Une analyse en erreur, un transfert, un ancien bilan sans preuve de swap ou une absence de données ne déclenche aucun message Telegram. Les erreurs restent dans le terminal et le rapport local.

Les messages sont envoyés par le bot existant au destinataire existant : il ne s’agit pas d’un envoi à Trojan. Le texte du bilan est envoyé en texte brut, sans interprétation HTML.

- **Au premier lancement de cette version, les bilans déjà terminés ne sont pas tous renvoyés.** Les prochains tokens qui passent le filtre d’achat vérifié sont notifiés automatiquement. Pour envoyer également les anciens rapports une fois, ajouter `--telegram-history`.
- Un identifiant de message est mémorisé pour chaque token. Au redémarrage, les messages confirmés ne sont pas renvoyés. Si `--retry-errors` produit ensuite un bilan corrigé, le message existant est édité.
- Les notifications utilisent une file séparée : un quota Telegram ne bloque pas la réception Discord ou l’analyse.
- Un HTTP 429 explicitement refusé est retenté après le délai demandé. Un timeout ou un arrêt pendant un nouvel envoi laisse la livraison **incertaine** ; aucun renvoi automatique n’est tenté pour éviter un doublon. Vérifier Telegram et le terminal dans ce cas.
- Après correction d’un envoi explicitement refusé (par exemple un bot bloqué), `--retry-telegram` permet de réessayer. Les livraisons incertaines ne sont pas relancées par cette option.
- La destination est liée à l’historique local. Pour changer de bot/destinataire, utiliser un autre `--state` afin de ne pas confondre les identifiants des messages.
- Si la configuration Telegram est absente, le terminal indique **rapports locaux uniquement**. Si elle est partielle, il indique les champs à compléter. Le bot doit avoir été démarré dans Telegram et être autorisé à écrire au destinataire.

Pour garder seulement les rapports locaux : `py scripts\discord_solana_watch.py --no-telegram`.

## Narrative IA après l’alerte — version 2026.10.31.5

L’alerte d’achat vérifié conserve son propre worker et sa propre session HTTP. **Aucun appel IA ni téléchargement d’image n’est effectué avant la confirmation d’envoi de cette alerte.** Un worker distinct prépare ensuite la narrative et l’envoie en réponse au bon message Telegram. L’image éventuelle est envoyée en réponse à cette narrative, seulement après confirmation du message texte.

Le module utilise **Anthropic**, avec la clé `anthropic_api_key` de `scout_config.json` ou `ANTHROPIC_API_KEY` ; `ANTHROPIC_AUTH_TOKEN` reste aussi utilisable si aucune clé API n’est définie. Le texte de la clé et les secrets Telegram/Helius ne sont pas transmis dans le prompt ni stockés dans les rapports.

Mettre à jour les dépendances après copie des cinq scripts :

```bat
py -m pip install -r requirements-discord.txt
```

Avec la clé déjà utilisée par ton ancien Scout, la narrative est activée automatiquement pour **les prochaines alertes admissibles**. Le terminal affiche **Narrative IA : après confirmation de l’alerte initiale, en réponse Telegram.** Sans clé, sans SDK ou en cas d’échec du modèle, les alertes d’achat continuent ; le problème de narrative reste indiqué localement.

Réglages facultatifs à ajouter dans la configuration existante (conserver les autres champs et les virgules JSON) :

```json
{
  "callout_narrative": true,
  "callout_narrative_count": 20,
  "callout_narrative_model": "claude-sonnet-4-5"
}
```

Ne pas remplacer le fichier entier par cet extrait : il ne contient pas tes clés. `callout_narrative_count` accepte 10 à 20. `ANTHROPIC_MODEL` peut remplacer le nom du modèle. Le modèle par défaut est Sonnet 4.5 ; il doit être accessible à ton compte Anthropic. L’ancien modèle Opus peut être sélectionné explicitement si souhaité.

### Sources et sélection

Les textes et images des callouts sont conservés lors du comptage existant : aucune nouvelle collecte de callouts n’est ajoutée sur le chemin de l’alerte initiale. Seuls des callouts **datés entre la création du token et l’heure du signal Discord** sont admissibles. Réponses et updates ne sont pas utilisées comme s’il s’agissait de callouts initiaux.

La sélection classe les textes les plus longs (jusqu’à 2 000 caractères chacun), retire les copies et quasi-copies, privilégie des auteurs distincts et retient jusqu’à 20 textes informatifs d’au moins 60 caractères. La diversité des auteurs et les filtres de répétition restent des heuristiques, pas une garantie de véracité. S’il y a moins de dix textes exploitables, la synthèse précise cette limite ; s’il n’y en a aucun, aucun appel IA ni réponse inventée n’est produit.

La réponse explique le thème/meme, l’origine et le déclencheur **évoqués par les auteurs**, puis les points à vérifier. Elle cite 1 à 3 callouts via les liens réels `pump.fun/coin/<CA>?highlight_callout=<ID>`. Les affirmations des auteurs ne sont pas une confirmation d’affiliation, de partenariat ou de soutien officiel. Le modèle ne reçoit ni outil de navigation ni accès à tes données privées. Les textes et images sont explicitement traités comme des données non fiables, jamais comme des instructions.

Des liens HTTPS présents dans les sources peuvent être repris comme références **non vérifiées**. Le programme ne consulte pas automatiquement les sites, posts X ou liens Telegram externes pour les valider. Les IDs de sources, liens et images renvoyés par le modèle sont contrôlés ; les références inconnues entraînent un échec local, pas une publication.

### Images

Jusqu’à **deux images réellement attachées aux callouts sélectionnés** peuvent être téléchargées pour être montrées au modèle. Le modèle peut en choisir **une seule**, avec une explication de son utilité. Aucun logo n’est ajouté par défaut et aucune image n’est générée ou présentée comme officielle.

Les téléchargements sont limités aux CDN publics autorisés, en HTTPS sans identifiants, sans redirection vers un domaine privé. PNG, JPEG, GIF et WebP sont reconnus par signature ; HTML, SVG et vidéo sont exclus. Chaque image est limitée à 1,5 Mio et six secondes de téléchargement. Une image refusée ou indisponible est omise : le texte reste analysable. Une image trop grande en pixels ou illisible peut toutefois être refusée par Anthropic et faire échouer cette génération.

### Délais, coût et reprises

La narrative arrive **après** l’alerte, avec le temps d’attente dans sa propre file, les téléchargements et le temps de génération. Prévoir généralement des dizaines de secondes supplémentaires ; ce n’est pas un délai garanti. Les requêtes initiales Solana et Telegram restent indépendantes, mais les limites générales du bot Telegram peuvent encore retarder ses messages.

Un appel Anthropic est effectué par token admissible ayant des sources exploitables, pas par nouveau token Discord. L’appel est facturé selon ton compte, le modèle, les textes et les images ; le programme ne promet pas un coût fixe. Le détail `usage` est conservé dans le rapport quand l’API le fournit. Aucun appel IA réel facturé ni message Telegram réel n’a été effectué depuis l’environnement de développement.

Les sources sélectionnées, le modèle, la réponse et les références sont conservés dans `data/discord_solana/reports/<CA>.narrative.json`. Les images sont mises en cache dans le sous-dossier `narrative_media`. L’index HTML propose un lien vers ce relevé.

La génération n’est pas relancée automatiquement après une interruption ou un échec : cela évite de multiplier les appels facturés. Les envois Telegram confirmés ne sont pas renvoyés au redémarrage. Un timeout pendant l’envoi laisse une livraison incertaine, sans renvoi automatique ; l’image n’est pas envoyée si la réception du texte n’est pas confirmée.

Options explicites :

- `--no-narrative` : conserver les alertes et désactiver l’IA.
- `--retry-narrative` : retenter une génération ou un envoi explicitement refusé. Une génération retentée peut être facturée ; les livraisons incertaines ne sont pas renvoyées automatiquement.
- `--narrative-history` : inclure les anciennes alertes déjà envoyées. Les anciens rapports qui ne contiennent pas les textes doivent d’abord être réanalysés avec `--retry-errors` ; cela ne crée pas une preuve absente.

La synthèse ne modifie ni le filtre d’achat DECU/LE L ni les ordres de Trojan. Elle fournit une lecture des propos collectés, pas une vérification indépendante du projet ou une recommandation de trading.

## Critère strict d’alerte et délais

L’identité est vérifiée par **l’adresse du wallet**, pas seulement son alias. Un achat doit être une transaction réussie et confirmée par le RPC, signée par ce wallet, avec une contrepartie payée et un transfert du token relié à un programme de swap reconnu. Le transfert sous ce programme doit expliquer intégralement la variation du solde du token. Un swap sans rapport dans la même transaction ne suffit pas.

La liste de programmes actuellement reconnue couvre pump.fun, PumpSwap, Jupiter v4/v6, Raydium AMM/CPMM, Meteora DLMM/pools et Orca Whirlpool. Un autre routeur peut être reconnu s’il invoque un de ces programmes dans une hiérarchie CPI exploitable. Si les instructions manquent, si l’attribution est ambiguë ou si le protocole n’est pas pris en charge, aucune alerte n’est envoyée sur cette base. Les transferts taxés dont les montants ne peuvent pas être réconciliés peuvent également être écartés.

Pour l’antériorité, les heures de bloc sont à la seconde : **la borne de fin de la seconde du bloc doit être antérieure ou égale au signal** (`blockTime + 1 <= t_alert`). Un achat dans la même seconde que le signal est donc écarté en l’absence d’un autre achat antérieur vérifié. Cette prudence peut manquer de vrais achats ; aucune promesse de zéro erreur ou de détection exhaustive n’est faite.

Un achat confirmé suffit même si le wallet a ensuite vendu tout ou partie avant le signal : le bilan indique les ventes retrouvées. Si l’utilisateur souhaite exiger une position encore ouverte, il s’agit d’un filtre supplémentaire, non activé ici. Un achat d’un wallet ne prouve pas la qualité ou la rentabilité du token.

Sans achat positif vérifié, la collecte des callouts est évitée pour ne pas retarder les signaux suivants. Le rapport local reste disponible. Les anciens résultats sans la nouvelle preuve ne sont pas utilisés pour émettre une alerte ; `--retry-errors` permet explicitement de les réanalyser.

À 2 requêtes RPC/s, pour le **message complet** et sans file d’attente, ordres de grandeur théoriques :

| Cas | Budget estimatif signal → Telegram |
|---|---|
| Cache chaud, peu de nouvelles transactions et peu de pages | Environ 10–30 s |
| 20–100 transactions non cachées et quelques pages de callouts | Environ 30–90 s |
| Plusieurs centaines de transactions et beaucoup de pages | Plusieurs minutes ; autour de 5–7 min au plafond actuel si les services répondent vite, davantage avec incidents |

Ces fourchettes ne sont pas des mesures sur le PC utilisateur. Une file d’attente peut prolonger chaque délai. Le scan maximal représente jusqu’à environ 600 lectures de transactions, plus les pages de signatures et jusqu’à 81 requêtes de callouts ; les délais réseau s’ajoutent aux attentes de quota.

Les fichiers JSON enregistrent les étapes dans `timings_ms` : lecture après signal, attente en file, métadonnées, chaque wallet, callouts et analyse totale. Après le premier envoi réussi, la table SQLite `telegram_reports` conserve `sent_at` et `signal_to_sent_ms`. Le terminal affiche **signal→confirmation Telegram** : c’est la réponse du serveur Telegram observée par le programme, pas l’heure de lecture sur le téléphone. Les éditions ultérieures ne remplacent pas la mesure du premier envoi.

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

Le RPC est limité à **2 requêtes/s par défaut**. Ce quota est propre au processus et s’ajoute aux requêtes de l’ancien scout. Avec un abonnement Helius à 10/s, régler par exemple **l’ancien scout à `rps: 7`** et ce module à **2/s**, pour garder une marge. Il faut compter aussi les autres programmes utilisant la même clé. Les deux processus ne partagent pas automatiquement leur limiteur. En mode lecteur seul, les analyses et lectures RPC de l’ancien scout ne sont plus exécutées ; tenir compte surtout des autres programmes actifs avec la même clé.

Par défaut, le scan analyse jusqu’à **300 transactions candidates réussies par wallet**, parcourt jusqu’à **30 000 lignes de signatures** et lit **40 pages par catégorie de callouts**. Les transactions après le signal et les échecs sont exclus avant le décompte des transactions candidates. Le budget d’historique est réglable avec `--max-history-signatures`. Augmenter uniquement si le rapport indique un historique tronqué et si le quota le permet :

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

## Correctif de lecture Solana v1 — 2026.10.31.4

La lecture `getTransaction` accepte maintenant les transactions legacy, v0 et v1. La version précédente limitait la requête à v0 ; une transaction v1 était refusée par le RPC avec le code **-32015**, puis l’analyse du wallet devenait indisponible.

Le cas public signalé par l’utilisateur a été reproduit :
https://solscan.io/tx/5oiP9vYXXGZFdE9NAMQWPA5mig3EFSdBmTzQAaxJVbBcgdWpvkYBLXrHWs28Ker8KpKRh6PVYz1vEbX1pWUN3eGi

Achat confirmé par DECU le **31 octobre 2026 à 19:16:19, heure de Paris**, de **22 741 914,376767 tokens** du mint `YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump`. Le débit SOL corrigé des loyers et frais réseau est de **5,01 SOL** ; il peut inclure pourboires et frais groupés. Une fois la transaction récupérée, elle satisfait les règles strictes existantes de preuve de swap, sans ajout du routeur FLASH à la liste de confiance ni assouplissement du filtre.

Les codes RPC sont désormais visibles sans recopier les messages privés du fournisseur. Une transaction d’une version future non prise en charge est comptée comme indisponible ; elle n’efface plus un achat déjà vérifié dans les autres transactions. Une panne de transport interrompt la poursuite des lectures et signale le caractère partiel du résultat.

Pour réanalyser uniquement le signal déjà enregistré de ce token, arrêter l’ancien module d’analyse, copier les scripts corrigés et lancer dans le dossier du Scout :

```bat
py scripts\discord_solana_watch.py --retry-mint YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump
```

Le CA et l’heure originale du signal sont conservés : l’heure courante ne remplace pas l’heure Discord. La surveillance des prochains signaux continue ensuite normalement. Si une alerte initiale existe déjà, son identifiant reste utilisé pour l’édition ; les critères d’achat antérieur et les protections contre les doublons sont inchangés.

Pour les autres cas en erreur/incomplets, `--retry-errors` reste disponible. La correction v1 n’augmente pas automatiquement la limite de 300 signatures par wallet ; le terminal affiche maintenant les signatures parcourues, transactions lues/indisponibles et mouvements non attribués afin de distinguer une couverture limitée d’un achat non prouvé.

## Recherche ciblée des comptes du token — 2026.10.31.5

Le diagnostic « 300 signatures parcourues, 0 transactions lues » ne suffisait pas à atteindre l’heure du signal : le budget était consommé par les transactions échouées et postérieures au signal. Le correctif sépare les budgets de pagination d’historique et de transactions candidates. Les échecs et les transactions trop récentes ne consomment plus les 300 lectures candidates ; la pagination reste bornée par `--max-history-signatures` (30 000 par défaut).

L’analyse utilise désormais les **comptes du token**, plutôt que toutes les opérations récentes du wallet. `getTokenAccountsByOwner` retrouve ses comptes actuels, et **Solders**, utilisé uniquement pour dériver des adresses publiques, calcule ses comptes associés standards SPL / Token-2022. Ces adresses sont consultées même quand le compte a été fermé après une vente. Aucune clé privée ni opération de signature n’est utilisée. Sans Solders et sans compte actuel, le module se replie sur le scan du wallet et signale cette limite.

Le test public sur le token DECU signalé a retrouvé l’achat réel `5oiP9vY...` via son compte associé `6eEWQuE5LszMFLFCewUfa8ahoBEy4stWuv6TtmT5uy33`, après un parcours de 1 847 signatures dont 1 813 échecs. Le cutoff de ce test était une minute après l’achat, **pas une heure Discord devinée** ; la commande `--retry-mint` reprend l’heure réellement enregistrée sur le PC utilisateur. Un quota du RPC public a rendu une partie du test partielle, sans effacer l’achat confirmé déjà récupéré. Cela ne garantit pas un même délai sur le PC utilisateur.

### Périmètre des pourcentages

La recherche ciblée couvre les comptes associés et les comptes token actuels. **D’anciens comptes non associés déjà fermés peuvent manquer.** Le rapport précise donc ce périmètre et ne présente pas le premier achat observé comme un premier achat absolu de tout le wallet. Si la lecture des comptes observés est complète, il peut afficher « Vendu / 1er achat retrouvé (comptes observés) ». Le cumul reste celui des transactions retrouvées. Si les lectures sont incomplètes ou ambiguës, le pourcentage de référence reste indisponible.

Cette réserve n’empêche pas une alerte lorsque l’achat positif est réellement prouvé : le filtre strict, le contrôle du propriétaire, la réussite de la transaction et l’antériorité au signal sont inchangés. L’absence de résultat sur une lecture partielle n’est jamais une preuve d’absence d’achat.

### Installation du correctif

Arrêter le module d’analyse, remplacer les trois scripts fournis (`discord_token_analysis.py`, `discord_solana_watch.py`, `discord_token_telegram.py`) et `requirements-discord.txt`, conserver `data` et la configuration, puis lancer :

```bat
py -m pip install -r requirements-discord.txt
py scripts\discord_solana_watch.py --retry-mint YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump
```

Le lecteur Discord existant peut rester ouvert. Vérifier **2026.10.31.5**. Les diagnostics précisent le mode ciblé, les lignes après le signal, les échecs ignorés et une éventuelle « FENÊTRE DU SIGNAL NON ATTEINTE ». Pour approfondir explicitement une recherche encore tronquée : `--max-history-signatures 100000`. Cela ajoute des requêtes et ne garantit pas que le fournisseur serve tout l’historique.
