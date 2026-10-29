# Relais Trojan — version 2026.10.29.3

Le mode par défaut de l’interface est maintenant **Trojan · Telegram**, destination **@solana_trojanbot**, à la demande de l’utilisateur qui a configuré et importé son wallet dans ce bot. Aucun achat de validation ni message réel n’a été envoyé depuis l’environnement de développement.

## Installation Windows

1. Dans l’ancienne interface, arrêter le hub. Fermer ensuite son serveur avec Ctrl+C dans son terminal.
2. Télécharger la version mise à jour et remplacer **tout `scripts` et `web`**. Conserver intégralement `data` : session Telegram, journaux et session pump.fun.
3. Depuis le dossier du projet, lancer `py scripts\callout_dashboard.py --port 8770` puis ouvrir http://127.0.0.1:8770 et actualiser avec Ctrl+F5.
4. Vérifier la version **2026.10.29.3**, l’exécuteur **Trojan · Telegram**, le destinataire **@solana_trojanbot** et les trois callers. Aucun ordre n’est envoyé tant que le hub n’est pas démarré en réel.
5. Vérifier dans Trojan lui-même : Auto Buy effectif à réception d’un CA, wallet pump.fun sélectionné, montant, frais, slippage, TP +50 % / vente 100 %, SL −50 % / vente 100 %. L’application ne lit ni ne modifie ces réglages Telegram. Le simple envoi d’un CA peut uniquement ouvrir une fiche si Auto Buy n’est pas actif.
6. Cliquer sur Démarrer en réel. Garder le terminal ouvert. La session Telegram existante est utilisée ; aucune clé privée du wallet n’est chargée en mode Trojan. Le token de session pump.fun reste nécessaire pour publier.

Le montant de 2 USD, le TP/SL, le slippage et le quota Jupiter de l’interface sont grisés en mode Trojan : ils ne configurent pas le bot Telegram. La clé Jupiter n’est pas nécessaire dans ce mode. Les messages d’alerte habituels gardent leur destinataire distinct ; le démarrage refuse d’utiliser Trojan également comme destination des alertes, pour éviter deux commandes involontaires.

## Cycle

- Signal admissible de **YourMistyy** : envoi du CA seul à partir de publication source + **5 secondes**.
- Signal de **lynk0x_ ou narc** : délai de l’interface, **12 secondes par défaut**.
- Le texte et l’image sont préparés en parallèle. Les délais sont minimaux : détection, lectures RPC, connexion Telegram et services peuvent ajouter du temps.
- L’intention d’envoi et un identifiant MTProto `random_id` sont sauvegardés avant la requête Telegram. Un envoi ambigu, un redémarrage ou un timeout ne provoque **aucun renvoi automatique du CA**. Cela privilégie l’absence de doubles achats ; un crash juste avant l’envoi peut donc laisser un achat non effectué à vérifier manuellement.
- Une réponse de Telegram n’est pas une confirmation d’achat. Le programme relève un point de départ sur Solana puis cherche une transaction confirmée, réussie, signée par le wallet du profil pump.fun et augmentant son solde du token. Un solde déjà présent n’est jamais une preuve suffisante. Les signatures déjà vues avant envoi sont exclues, y compris dans le même slot.
- Après confirmation et contrôle de détention, le callout est publié sur pump.fun. À défaut de confirmation pendant 180 secondes, la position passe « À vérifier », sans publication ni nouvel achat automatique.
- Les updates restent programmées à **source +10 secondes** puis publiées sous notre propre callout.
- Trojan gère seul les ventes. Le programme ne demande aucune cotation Jupiter, ne signe et ne diffuse aucune transaction Solana. Il consulte le solde périodiquement ; un solde nul est affiché comme tel, sans inventer un TP/SL confirmé.

## Limites à connaître

- Le relais ne clique pas sur les boutons Telegram et ne vérifie pas les réglages Auto Buy/TP/SL de Trojan. Un refus de Trojan ou une fiche attendant un clic finit donc en achat à vérifier.
- L’observation Solana ne prouve pas que Trojan est à l’origine de la transaction : **ne pas acheter manuellement ou depuis un autre bot le même token pendant l’attente**. Le wallet configuré dans Trojan doit être celui du profil pump.fun. Un achat sur un autre wallet ne sera pas reconnu.
- La recherche est limitée aux 30 dernières transactions du wallet ; au plus trois transactions sont examinées à chaque passage. Un wallet très actif ou un RPC indisponible peut empêcher la reconnaissance. Une indisponibilité n’entraîne aucun nouvel ordre.
- Les montants USD exécutés, les MC d’entrée/sortie et le motif de vente Trojan ne sont pas reconstruits dans cette première intégration. Ils restent indisponibles dans l’interface ; consulter les transactions et l’historique Trojan. La signature d’achat confirmée reste liée dans Détails.
- Les ordres locaux non signés en attente au basculement sont annulés dans le journal. Les anciennes transactions déjà signées passent à vérifier et ne sont pas rediffusées ; elles peuvent néanmoins avoir déjà été transmises au réseau.
- **Les sorties locales des anciennes positions sont également suspendues en mode Trojan.** Importer le wallet n’assure pas que Trojan ait créé des TP/SL sur ces positions : les vérifier/configurer directement dans Trojan avant de s’y fier. Rien n’est liquidé par le basculement.
- Fermer notre programme arrête les nouveaux envois et publications. Cela n’annule pas les ordres déjà reçus par Trojan ; ses sorties dépendent de son propre fonctionnement.
- Le mode Jupiter reste sélectionnable pour revenir explicitement à l’ancien fonctionnement ; il requiert alors la clé wallet locale. Ne pas faire exécuter simultanément les mêmes entrées/sorties par les deux systèmes.

## Terminal

`py scripts\callout_hub.py --live --executor trojan --trojan-bot @solana_trojanbot`

Simulation sans messages ni transactions :

`py scripts\callout_hub.py --dry-run --executor trojan`

Le relais nécessite le hub ou l’interface. Le script indépendant `callout_repost.py` ne gère pas la connexion Telegram du relais.
