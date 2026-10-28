# Fonctionnement actuel : trois callers, achat après 12 secondes et updates

Cette version remplace la liste de trading précédente par ces trois profils, identifiés par leurs wallets :

| Caller | Profil |
|---|---|
| lynk0x_ | https://pump.fun/profile/CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD |
| YourMistyy | https://pump.fun/profile/8NFh9y6qmMigMvRtT2KhrnCDo4BE2jMYcondAzmXZ26E |
| narc | https://pump.fun/profile/CxgPWvH2GoEDENELne2XKAR2z2Fr4shG2uaeyqZceGve |

Le hub envoie aussi ses alertes Telegram uniquement pour les profils configurés. Les anciens outils indépendants du dépôt gardent leurs propres usages ; lancer uniquement le hub ou son interface pour ce fonctionnement.

## Cycle d’un nouveau callout

1. À la détection, le journal conserve le caller, le texte source, son image éventuelle et l’heure de publication pump.fun. Une préparation de contenu démarre sur un worker séparé.
2. L’achat de 2 USD par défaut ne commence pas avant **publication du caller + 12 secondes**. C’est le début de préparation de la transaction : cotation, construction, signature, diffusion et confirmation prennent encore du temps. Une détection après cette échéance est traitée immédiatement si elle respecte encore l’âge maximal configuré, 60 secondes par défaut.
3. Dès que l’achat est confirmé et la détention vérifiée, la publication est tentée dans le même passage du moteur, sans attente supplémentaire de 15 secondes. L’authentification et la réponse de pump.fun peuvent encore retarder l’affichage.
4. Le texte et l’image déjà prêts sont utilisés. Si la préparation tarde, le texte local sauvegardé sert de secours. Une image prête plus tard peut faire l’objet d’une réponse distincte sur le bon callout, sans nouvel achat.
5. Les réponses du caller d’origine sont surveillées via le filtre API **AUTHOR_ONLY**. Une réponse d’un autre wallet identifié est rejetée. Les tombstones et quotes sont ignorées. Chaque update est programmée au plus tôt **heure de son auteur + 10 secondes**, puis publiée sous le callout de notre compte. Une détection tardive ou un refus API prolonge ce délai ; ce n’est pas une garantie de publication à la seconde exacte.

Le suivi des updates utilise une lecture de callout toutes les deux secondes environ, en rotation entre les callouts suivis. Plus il y en a, plus l’attente avant détection peut augmenter. La fenêtre de lecture est de 100 réponses récentes par callout ; une fenêtre pleine est signalée. Ce suivi n’utilise pas encore le prototype WebSocket étudié précédemment.

Les nouvelles réponses publiées par le bot sont espacées d’au moins 6,1 secondes pour rester sous la limite de 10/min de cette route. Des updates simultanées peuvent donc être publiées après leur échéance minimale de 10 secondes.

## Reformulation et images

Dans Configuration, choisir **Locale** ou **IA OpenAI**. Aucun nom de caller ni « following … » n’est ajouté au texte par le modèle. Le nom reste visible dans le tableau pour identifier la source.

- **Locale** : gratuite, sans clé supplémentaire. Elle reprend prudemment la thèse entre guillemets avec une introduction et une conclusion. Elle préserve le contexte mais reste moins naturelle qu’une vraie reformulation. Les mentions personnelles de holding dans les updates sont écartées. Ce mode est le défaut.
- **IA OpenAI** : reformulation en anglais, cible de 40–60 mots au premier callout et de 25–60 mots pour les updates, à partir du seul texte source. Le modèle doit conserver les hypothèses et ne pas inventer d’analyse, d’approbation ou de trades personnels. Il ne vérifie pas les affirmations sur le Web et n’analyse pas les pixels des images. En cas de clé manquante, erreur ou délai dépassé, le texte local prend le relais. Les informations de la source restent des données non fiables, pas des instructions destinées au modèle.

Pour activer l’IA, dans le terminal qui lance l’interface :

```bat
set /p OPENAI_API_KEY=Colle ta cle API OpenAI puis Entree :
py scripts\callout_dashboard.py --port 8766
```

Sélectionner ensuite **IA OpenAI** avant Démarrer. L’API est facturée séparément de ChatGPT. Le modèle par défaut est `gpt-4.1-mini`, modifiable par `OPENAI_MODEL`. Le texte public de la source et l’état ouvert/fermé sont envoyés à OpenAI ; aucune clé de wallet, session pump.fun ou clé Jupiter n’est incluse dans cette requête. Aucun appel OpenAI n’est effectué en mode Local, même si une clé est présente.

Les images jointes au callout ou à l’update sont préparées sans bloquer le moteur : HTTPS sur les domaines CDN pris en charge, formats PNG/JPEG/GIF/WebP, limite locale de 8 Mio, délai borné. Les vidéos, SVG et pages HTML ne sont pas repris comme images. Les fichiers sont conservés dans `data/content`. Une image refusée explicitement par pump.fun permet une nouvelle tentative du texte seul. Un téléchargement impossible ne bloque pas un achat.

Si notre position est déjà vendue ou son solde nul, une update ne doit pas dire que nous détenons encore le token : le texte de secours précise la clôture. En cas de vente ambiguë « À vérifier », les nouvelles updates sont différées.

## Sorties

Le **TP +50 % est conservé**, avec vente de 100 % de la quantité acquise par le bot. Les nouvelles entrées enregistrent aussi un **SL −50 %** : pour un coût de 2 USD, une cotation attendue de sortie à 1 USD ou moins déclenche la préparation d’une vente totale. Le TP compare le minimum de sortie coté au seuil de gain ; le SL utilise la sortie attendue pour éviter qu’une simple tolérance de slippage large ne déclenche artificiellement le stop.

Les contrôles utilisent les cotations Jupiter, les quantités détenues et le RPC. Ce sont des sorties surveillées par le programme, pas des ordres déjà déposés sur Solana. Le slippage, les frais et les délais réseau peuvent produire une exécution différente du seuil. Le terminal doit rester ouvert. Arrêter le hub suspend les contrôles.

Les ventes signées sont sauvegardées avant envoi et reprises avec la même signature. Une réponse inconnue n’entraîne pas une nouvelle vente aveugle. Le même principe s’applique aux achats et aux publications : une update dont la réponse est perdue est recherchée avant tout nouvel envoi, puis placée à vérifier si l’ambiguïté persiste.

## Mise à jour Windows et migration

Si l’ancienne liste persiste, utiliser **LANCER_NOUVELLE_INTERFACE.bat**, situé à la racine du ZIP, après extraction. Ce lanceur se place dans son propre dossier et ouvre le port **8770** afin de distinguer la nouvelle interface d’un serveur encore actif sur 8766. Il ne ferme aucun autre processus. Arrêter l’ancien bot avant de démarrer le nouveau.

La version **2026.10.29.1** affiche le dossier absolu, le PID et la version du serveur sous le titre. `/api/version` expose aussi ces informations localement. Le navigateur bloque Démarrer s’il reçoit encore une ancienne API. Un serveur récent refuse une installation dont le moteur garde les dix anciens callers ou dont les nouveaux modules manquent. Pour un diagnostic sans démarrer le serveur ni le bot : `py scripts\callout_dashboard.py --diagnose`.

Arrêter le bot, puis l’interface avec Ctrl+C. Télécharger la nouvelle version, remplacer `scripts` et `web` en conservant **tout `data`**, puis relancer :

```bat
py scripts\callout_dashboard.py --port 8766
```

Les anciens réglages migrent vers les trois profils et les délais 12/10 secondes. Le montant, le quota Jupiter et le TP sont conservés. Vérifier les paramètres avant Démarrer. Le champ « Délai avant achat » remplace l’ancien délai de republication. La configuration IA reste locale par défaut, sans activation payante automatique.

Les achats non encore signés des anciens callers sont abandonnés ; leurs nouvelles publications sont désactivées. Les transactions déjà signées restent réconciliées et les anciennes positions continuent leur suivi de sortie existant. Le nouveau SL n’est pas appliqué rétroactivement à des achats anciens qui ne l’avaient pas enregistré. Aucun ancien achat abandonné n’est rejoué. Ne pas supprimer les journaux pour changer la liste.

Les données de publication et les identifiants d’updates sont conservés dans SQLite. Une mise à jour ne peut être envoyée que sur le callout personnel retrouvé pour le même token et le même texte. La fiche Détails affiche le texte, le mode de rédaction, l’image préparée et le nombre d’updates publiées/en attente/à vérifier. Le statut « SL confirmé » distingue une sortie au stop d’un TP.

## Validation

Les tests couvrent les échéances d’achat et d’updates, les trois wallets, la migration, la limite exacte du SL, les quantités vendues, les images multipart, l’isolation du contenu lent, la déduplication et la reprise après timeout. Les tests de publication/signature utilisent des services simulés. Aucun achat, post ou réponse réel n’est envoyé pour valider cette version dans le cloud.
