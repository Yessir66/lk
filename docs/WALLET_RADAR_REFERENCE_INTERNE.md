# Wallet Radar — fiche interne de reprise

Mise à jour : **16 novembre 2026, Europe/Paris**. À lire au début d'une reprise concernant ce bot, y compris après compression de la conversation. Le fichier `AGENTS.md` à la racine pointe vers cette fiche.

Cette fiche décrit le dernier état connu ; le code et la configuration du déploiement priment si l'utilisateur les a modifiés depuis. Elle ne contient aucun secret.

## Identité et périmètre

- **Nom de référence choisi avec l'utilisateur : Wallet Radar.**
- Fonction : suivre des wallets Solana via Helius, reconnaître leurs achats et ventes, puis afficher le token acheté dans un onglet GMGN sur le PC Windows de l'utilisateur.
- À distinguer du bot de republication de callouts pump.fun/Trojan et du bot d'analyse Discord, présents dans le même dépôt.
- Aucun achat ni vente automatique dans le fonctionnement utilisé. L'utilisateur consulte le graphique et décide manuellement de vendre.
- Telegram est implémenté en option explicite, mais **reste désactivé dans le lanceur utilisé**. Ne pas l'activer par défaut.
- Les anciennes idées de momentum sur 100–300 ms n'ont pas été activées. Un mode expérimental de simulation 1 s / 3 s a ensuite été ajouté : voir la section « Simulation BBIG BOSS ». Aucun trading réel ni flux exhaustif de tous les DEX n'est activé.

## État connu et wallet demandé

- Version serveur du dépôt : **2026.11.16.1**, constante `VERSION` dans `scripts/wallet_alerts_ws.py`.
- Extension Chrome : version **1.0.0**, dossier `chrome-gmgn`.
- Dernière liste demandée : **BBIG BOSS uniquement**, adresse publique :
  `BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp`.
- DECU et LE L ont été retirés du suivi Wallet Radar à la demande de l'utilisateur. Leur présence dans l'historique ou dans les constantes du module partagé d'analyse Discord ne signifie pas qu'ils sont encore suivis ici.
- Les listes de wallets sont désormais modifiables par l'utilisateur. **Vérifier la liste actuelle avant toute future modification** ; ne pas réimposer cette liste historique.
- La configuration locale `wallet_alerts_config.json` prime sur `DEFAULT_WALLETS` et le fichier d'exemple. Modifier un défaut du code ne suffit pas à migrer une configuration existante.
- Dernieres captures du 16 novembre : diagnostic sur un token Pump.fun avec 1 evenement BBIG BOSS, cotation SOL, zero octet inconnu, 1 compte JSON parse et abonnement a 17 ms. Bilan du meme essai ensuite a **5/30 : 2 DONNEES_INSUFFISANTES, 1 SORTIE_TOTALE_1S a 0,82 s, 2 VOLUME_INSUFFISANT** (environ 232,34 USD et 14,10 USD observes). Un cas incomplet affiche environ 146,92 USD, l'autre non mesure. Aucun achat theorique positif. La sortie a 0,82 s teste bien le premier filtre observe ; les causes exactes des anciens cas incomplets ne sont pas prouvees par ces captures.
- L'utilisateur se plaint d'un rattrapage massif (anciennes transactions de dizaines de minutes). Correctif 2026.11.16.1 prepare : `--live-only` par defaut dans les lanceurs Windows, sans aucune tache de collecte/decodage historique, ni suppression des donnees. Les interruptions ne sont plus rattrapees dans ce mode. Tests du mode direct verifient que les anciennes files restent conservees mais ne consomment aucun RPC.
- Defaut identifie dans le code : toute instruction non decodee invalidait le volume, meme creation/fermeture de compte ou vente apres 1 s. Classification corrigee, vraie transaction d'achat/swap non decodee et mouvement de position inconnu avant 1 s restent incomplets. Causes detaillees `missing_reasons` ajoutees aux futurs evenements. Le defaut peut expliquer des cas mais ne constitue pas une preuve de la cause des anciennes captures. Guide `DIRECT_ET_CAUSES_SIMULATION.md`.

## Dépôt et lieux de déploiement

| Élément | Chemin / référence |
|---|---|
| Dépôt cloud | `/workspace/lk` ; GitHub `Yessir66/lk` |
| Branche de travail | `codex/callout-alerts-telegram` |
| PC Windows | `C:\Users\Enzo\Downloads\wallet-alerts` |
| VPS | `bot@2.24.62.69`, nom observé `srv2140782`, Ubuntu 24.04.5 |
| Dossier VPS | `/home/bot/wallet-alerts` |
| Python VPS | `/home/bot/wallet-alerts/.venv/bin/python` |
| Configuration VPS | `/home/bot/wallet-alerts/wallet_alerts_config.json` — privée, ignorée par Git |
| Journal serveur | `data/wallet_alerts_ws/state.sqlite3`, relatif au dossier serveur |
| État local Windows | `%LOCALAPPDATA%\WalletAlertsGMGN` |

Le VPS n'est pas automatiquement accessible depuis l'environnement cloud. Ne pas prétendre avoir déployé ou lu sa configuration sans action réelle. L'utilisateur installe les correctifs ZIP sur son PC et le menu transfère les scripts.

## Utilisation quotidienne et menus

Point d'entrée conseillé : **`WALLET_RADAR.cmd` à la racine du dossier Windows**.

Menu principal Windows :

- **1** : démarrer la surveillance et GMGN en onglet unique.
- **2** : gérer les wallets. Transfert automatique via SCP de `configure_wallet_alerts.py`, `wallet_alerts_ws.py`, `wallet_radar_paper.py` et `wallet_radar_diagnostics.py`, puis connexion SSH interactive à leur gestion. Mot de passe VPS éventuellement demandé deux fois.
- **3** : lancer l'essai de simulation BBIG BOSS, après transfert automatique des scripts.
- **4** : afficher le bilan hors réseau du dernier essai via SSH.
- **5** : transférer les scripts, puis demander un arrêt propre des instances du même script sur le VPS (`--stop`). Pas de suppression du journal/verrou, pas de kill forcé ni arrêt des autres bots.
- **6** : diagnostic en lecture seule pendant la collecte, sans arrêt ni réinitialisation : maximum 4 cas, 8 lectures RPC à 1/s. Codes HTTP/RPC sans messages privés, formats de comptes, plateformes, événements et cotations. Comptes interrogés actuellement, pas reconstruction historique ; un ancien refus temporaire peut ne plus être reproductible. Guide `DIAGNOSTIC_SIMULATION.md`.
- **0** : quitter le menu.

Dans la gestion distante des wallets :

- **1** : afficher la liste.
- **2** : ajouter ; demander nom puis adresse publique Solana.
- **3** : retirer par numéro dans la liste affichée.
- **4** : renommer par numéro.
- **0** : revenir au menu Windows.

Les changements valides sont enregistrés immédiatement et atomiquement, sans toucher aux clés ni aux autres réglages, mais **les abonnements changent au prochain démarrage**, pas à chaud. Arrêter avec Ctrl+C, modifier, puis relancer. Limite 1–20 wallets ; doublons et adresses invalides refusés. Pour remplacer le dernier wallet, ajouter le nouveau avant de retirer l'ancien.

Au démarrage : Chrome ouvert dans le bon profil, extension active, onglet GMGN réservé, PC connecté, fenêtre du lanceur maintenue ouverte. L'ancien `windows/LANCER_GMGN_ONGLET_UNIQUE.cmd` fonctionne encore mais n'est plus le point d'entrée privilégié. `windows/GERER_WALLETS.cmd` ouvre directement la gestion.

Le lanceur démarre lui-même le Python distant via SSH avec **`--early --browser-events`**, sans `--telegram`. Une seule instance par journal. Avec BBIG BOSS seul : **1 wallet et 2 abonnements** (processed + confirmed), pas deux wallets.

Depuis 2026.11.16.1, il ajoute aussi **`--live-only`** dans tous les lancements Windows (normal et simulation). Pas de tache de rattrapage ni de worker historique ; aucune ancienne file n'est effacee. Les menus 1 et 3 synchronisent les scripts avant lancement pour que ce nouvel argument soit accepte par le serveur. Le mode manuel sans l'argument garde la reconciliation historique pour compatibilite.

L'utilisateur confond parfois les fenêtres : toujours préciser **PowerShell Windows : `PS C:\…>`** ou **VPS SSH : `bot@…`** avant une commande. Ne pas demander d'exécuter `scp C:\…` à l'invite Linux. Préférer le menu aux commandes manuelles quand il suffit.

## Chaîne de détection

1. Helius `logsSubscribe` fournit les notifications. `--early` ajoute `processed` à `confirmed` pour mesurer l'écart.
2. Une notification processed affiche « TRANSACTION PROVISOIRE » : elle ne prouve pas encore un achat, peut seulement mentionner le wallet, et ne commande jamais GMGN ni Telegram.
3. Après confirmed, lecture `getTransaction` en `jsonParsed`, `maxSupportedTransactionVersion: 1`.
4. Analyse stricte partagée avec `discord_token_analysis.py` : signature du wallet, soldes, contrepartie et preuve de swap. Transfert seul ou swap ambigu/inconnu ne devient pas un achat vérifié.
5. Achat vérifié récent : émission sur stdout d'une ligne **`GMGN_BUY ` suivie d'un JSON** public contenant version, type, mint, wallet, signature et instant de détection.
6. La console Windows reçoit le flux SSH et transmet ces événements au pont local. Les ventes sont affichées, sans commande de graphique.

L'achat doit dater d'au plus 120 s pour être transmis au navigateur. Le rattrapage peut fournir un achat récent, mais l'historique plus ancien ne doit pas ouvrir de graphiques. Aucun système de relivraison garanti des ouvertures manquées pendant une coupure n'a été ajouté.

## Ordonnancement, état et limites

- Plafond HTTPS local global **5 requêtes/s**, configurable par `--rpc-rps` entre 0.5 et 10. Ce n'est pas une réservation de quota chez Helius.
- Priorité au direct pendant l'attente de quota ; workers distincts direct/rattrapage ; une seule requête HTTP en vol. Une requête déjà envoyée ne peut pas être préemptée.
- Rattrapage et ses lectures de transactions limités ensemble à **1 requête/s**, à l'intérieur du plafond global.
- HTTP 429 : pause globale de **60 s**. Ne pas accélérer les réessais pour contourner un quota.
- Rattrapage périodique et à la reconnexion, pages de 1000 signatures, maximum 10 pages par fenêtre. « HISTORIQUE PARTIEL » indique des lacunes persistées, pas un historique exhaustif. Ne pas présenter sa durée comme la latence du direct.
- Premier suivi d'un nouveau wallet : initialise le curseur, sans rejouer tout son historique.
- SQLite WAL conserve événements, curseurs, lacunes, profils et éventuelles alertes. Les états pending/provisional des wallets retirés sont ignorés au redémarrage, leurs alertes pending expirées ; l'historique n'est pas effacé.
- Verrou `StateLock` partagé : empêche deux instances du même journal. Ne pas supprimer le fichier de verrou pour contourner un processus réellement actif.
- Helius Free avait été vérifié à 10 RPC/s et 1 million de crédits mensuels ; conditions susceptibles d'évoluer. D'autres processus partageant le compte consomment aussi des quotas. Aucune promesse de fonctionnement gratuit illimité.

## Chrome / GMGN : conserver le gain acquis

L'utilisateur observait 5–6 s de chargement avec un nouvel onglet par achat. L'extension en onglet unique a été installée ; il a **confirmé que le problème de latence était réglé**.

- Réutilise un onglet GMGN existant/réservé. Même mint déjà affiché ou en navigation vers ce mint : activation sans rechargement.
- Autre mint : essaie `window.next.router.push` si disponible, ou un lien correspondant déjà présent. Si le changement d'URL n'aboutit pas, fallback vers une navigation classique **dans le même onglet**.
- Ne pas promettre la disponibilité du routeur interne sur toutes les versions GMGN, ni un délai précis pour un nouveau token.
- Onglet réservé `autoDiscardable: false` pour aider à conserver la page chargée.
- Bouton « Utiliser l'onglet GMGN actif » pour choisir l'onglet ; bouton pause pour conserver le graphique pendant la décision. Les achats reçus pendant la pause ne sont pas rejoués lors de la reprise.
- Permissions limitées à GMGN, stockage et native messaging. Aucun clic sur un bouton d'achat/vente ni remplissage de formulaire de trading.
- Pas de clé API GMGN ni de solde minimum nécessaire à l'ouverture des graphiques.

Pont local :

- Identifiant stable de l'extension : **`hiiecbdabaichplolfmoelhjlojbffje`**. Conserver la clé publique `key` du manifest ; la changer casserait l'autorisation du pont installé.
- Hôte natif : **`fr.walletalerts.gmgn`** ; installation par `windows/INSTALLER_EXTENSION_GMGN.cmd` sous le compte Windows, sans administrateur.
- Fichiers hôte : `%LOCALAPPDATA%\WalletAlertsGMGN\nativehost` ; déclaration HKCU `Software\Google\Chrome\NativeMessagingHosts\fr.walletalerts.gmgn`.
- Le lanceur écrit `bridge-session.txt` et `gmgn-events-<session>.jsonl`. Hôte PowerShell en protocole binaire native messaging ; **stdout réservé au protocole**, ne pas y ajouter de logs.
- Extension : interroge le pont environ toutes les 100 ms, filtre achats récents et déduplique les signatures/mints. `opened-buys.txt` garde les achats transmis côté Windows ; stockage extension garde aussi les événements traités.
- Aucun serveur HTTP ni port réseau local ouvert.
- « CA TRANSMIS À L'EXTENSION » = écriture du pont, pas preuve que la chart est chargée. « Connexion locale OK » = pont Chrome/Windows, pas état du VPS.

## Mesures déjà obtenues : interprétation

- Avant optimisation, version .5 à 2 RPC/s : 24 profils complets ; médiane confirmed reçu → décodage **0,72 s**, P95 **1,66 s**.
- Après optimisation, version .6 à 5 RPC/s : 18 profils complets ; médiane **0,04 s**, P95 **0,25 s**. Échantillons différents ; pas une garantie future.
- Captures ultérieures : souvent 0,03–0,12 s après confirmation reçue, et quelques dixièmes depuis la première notification processed. Réseau/RPC souvent 0,02–0,05 s.
- Toutes ces durées sont des mesures locales, pas l'heure exacte d'exécution on-chain. Les paires processed/confirmed comparent leurs premières réceptions sur la même connexion, avec horloge monotone.
- `blockTime` Solana n'a qu'une précision à la seconde. Ne pas déduire un horodatage d'exécution au dixième à partir du slot ou de la réception réseau.
- Les durées locales sont affichées au centième ; la livraison SSH, le pilotage de Chrome et le chargement réel de la chart ne sont pas inclus dans le rapport serveur.
- Rapport hors réseau : `.venv/bin/python scripts/wallet_alerts_ws.py --timing-report`. Profils détaillés regroupés par version/plafond ; ne pas confondre statistiques globales mélangeant les versions avec ces groupes. Ne pas additionner les médianes des étapes.
- L'utilisateur s'intéresse aussi aux achats qui s'intercalent entre BBIG BOSS et DECU. Une capture montrait 11 achats intermédiaires, environ 2303 $, dans la même seconde affichée ; cela ne donnait ni leurs intervalles exacts, ni la preuve d'une entrée possible avant DECU. Suivre un wallet seul ne fournit pas un flux exhaustif des achats de tout le token.

## Simulation BBIG BOSS : décisions et limites nouvelles

L'utilisateur veut préparer une couche d'achat automatique, **mais l'implémentation livrée reste exclusivement en simulation**. Guide complet : `SIMULATION_BBIG_BOSS.md`, moteur : `scripts/wallet_radar_paper.py`. Choix actés :

- Signal fondé uniquement sur BBIG BOSS. À partir de son achat, attendre une seconde ; s'il liquide toute sa position pendant cette première seconde observée, annuler. Une vente partielle n'annule pas.
- Refuser le créateur déclaré **bwam**, adresse `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`. Le champ de créateur de l'événement natif est utilisé ; l'identité humaine ou les anciens créateurs ne sont pas reconstruits.
- Après la première seconde, achat théorique dès que les achats observés depuis T0 atteignent 400 USD, **hors transaction initiale de BBIG BOSS**, avant T0 + 3 s. Les achats suivants de BBIG BOSS peuvent compter. Une vente totale après la première seconde n'annule plus.
- Montant voulu pour une future exécution : **25 USD de tokens**, frais en supplément. Plafond des frais toujours à définir. Exécution directe depuis un VPS avec wallet dédié retenue comme orientation, mais aucun système de signature/envoi n'est installé dans ce mode.
- Point de départ retenu pour l'essai : première réception processed associée à l'achat ; le décodage/confirmation ne redémarre pas les compteurs. L'heure exacte d'exécution on-chain n'est pas connue.
- Helius Developer à 49 USD/mois, flux complet `transactionSubscribe` processed : budget acceptable pour l'utilisateur, **mais il a explicitement demandé d'essayer d'abord avec le gratuit**. Aucun abonnement changé.
- L'utilisateur a expressément fixé le premier essai à **30 tokens distincts, pas 50**. Les cas exclus, non supportés et inexploitables comptent dans ces 30. Zéro token réel testé depuis le cloud à ce stade ; les tests sont contrôlés/mocks. L'essai réel devra tourner sur son VPS.

Le mode 3 ajoute `--paper-buys` au lanceur normal. Deuxième WSS standard : logs processed de BBIG BOSS, puis abonnement temporaire au mint pour décoder les TradeEvent Pump.fun, sans requête RPC par acheteur. Pas de PumpSwap ni autres DEX dans cette première couverture. Forme ABI inconnue : pas de feu vert présumé. Devise de cotation non convertible : volume inconnu. SOL/USD public CoinGecko horodaté, âge maximal 90 s ; USDC nominal 1 USD.

Depuis la .5, le schema TradeEvent inclut `quote_transfer_fee` du SDK officiel `@pump-fun/pump-sdk` **4.3.1**, absent de `pump-public-docs` au moment de la consultation. Ce champ u64 final explique les 8 octets non lus de la fixture archivee. Ne pas supprimer globalement le controle d'octets restants : seules les extensions identifiees doivent etre ajoutees. Guide `CORRECTIF_FORMAT_PUMP.md`. Les anciens evenements sans ce champ sont compatibles. Le rapport distingue les volumes non mesures des zeros.

La position initiale vient du getTransaction confirmé déjà lu par le suivi principal, complété par un getTokenAccountsByOwner sous le plafond RPC partagé. Un autre compte non nul hors de ceux de la transaction empêche de prétendre connaître la position totale initiale : résultat inexploitables. Les ventes/achats natifs suivants reconstruisent la fenêtre initiale. Une preuve arrivant trop tard, un changement incompris ou une coupure empêchent une conclusion positive. Le volume reste observé à réception, avec trou possible avant l'abonnement au mint ; l'ordre exact dans un même slot n'est pas reconstitué et processed reste provisoire. C'est un essai de faisabilité/couverture, pas une validation d'exécution réelle ou de rentabilité.

Notifications console `[SIMULATION]` et bulles Windows selon réglages OS : suivi, développeur exclu, sortie totale pendant 1 s, attente de volume, achat théorique, volume non observé, données insuffisantes ou non supportées. Aucun Telegram ajouté. Le marqueur transport est `RADAR_DECISION ` + JSON public, distinct de `GMGN_BUY`.

Journal par session : `data/wallet_alerts_ws/paper_decisions.jsonl`. À 30 résultats, la collecte expérimentale s'arrête ; la surveillance habituelle/GMGN continue. Menu 4 ou `--paper-report` lit le dernier essai sans réseau. Un redémarrage crée un nouvel essai et conserve les anciens journaux. Les règles et les montants restent théoriques : aucune signature, aucun ordre ni estimation de prix exécutable.

## Dépannage connu

**`Démarrage impossible (RemoteError)` immédiatement après la bannière** : vérifier en premier une autre instance encore active. Le verrou partagé peut émettre cette classe d'erreur. La classe seule n'est pas une preuve absolue de sa cause.

- Arrêter les autres fenêtres de surveillance avec Ctrl+C.
- Méthode conseillée depuis la .4 : **menu 5**, attendre l'arrêt confirmé ou l'absence d'instance, puis menu 3. Le contrôleur cherche les processus Python du même UID qui exécutent le chemin exact du script (absolu ou relatif à leur cwd), puis SIGINT et attente bornée à 30 s. Le message de verrou connu est désormais explicite. Guide `ARRET_WALLET_RADAR.md`.
- Si l'instance est orpheline, après connexion SSH, arrêt ciblé et propre déjà conseillé :
  `pkill -INT -u bot -f '[p]ython.*wallet_alerts_ws[.]py'`.
- Ne pas utiliser `killall python` ni détruire le journal.
- Si une ancienne version apparaît encore, le menu 1 ne transfère pas les scripts : utiliser le **menu 2** une fois, puis 0 dans la gestion et 1 dans le menu principal.

Autres cas :

- `401` HTTPS et WSS : clé Helius erronée possible ; ancien incident résolu en recopiant la bonne clé. Ne pas demander la clé dans le chat. Le helper accepte une saisie locale masquée. Une saisie visible avait été autorisée localement à la demande de l'utilisateur, jamais nécessaire pour la gestion des wallets.
- Clé enregistrée dans le JSON : variables d'environnement absentes ne prouvent pas qu'il manque une clé.
- Config offline `--check` valide seulement le format ; `--diagnose` teste HTTPS/WSS, sans transaction ni Telegram.
- Pont absent dans l'extension : vérifier installation de l'hôte, identifiant stable, bon profil Chrome et extension active.
- Anciens caractères accentués illisibles : problème d'encodage Windows, pas preuve de CA incorrect. Les nouveaux lanceurs règlent UTF-8.
- Le VPS n'a pas besoin de Chrome ni de RDP pour Wallet Radar. XFCE/xrdp et Chrome VPS existaient pour un autre travail : ne pas les confondre avec le Chrome Windows utilisé ici.

## Fichiers à consulter pour intervenir

| Fonction | Fichiers |
|---|---|
| Guide utilisateur/menu | `WALLET_RADAR.md`, `WALLET_RADAR.cmd`, `windows/WALLET_RADAR.ps1` |
| Gestion de configuration | `scripts/configure_wallet_alerts.py`, `windows/GERER_WALLETS.cmd` |
| Moteur de suivi | `scripts/wallet_alerts_ws.py` |
| Transport/décodage partagé | `scripts/discord_token_analysis.py` |
| Verrou partagé | `scripts/discord_solana_watch.py` |
| Pont SSH vers Windows | `windows/OUVRIR_CHARTS_GMGN.ps1` |
| Hôte natif/installation | `windows/GMGN_NATIVE_HOST.*`, `windows/INSTALLER_EXTENSION_GMGN.*` |
| Extension | `chrome-gmgn/manifest.json`, `core.js`, `background.js`, `popup.*` |
| Guides complémentaires | `GMGN_ONGLET_UNIQUE.md`, `WALLET_ALERTS_WS.md`, `BBIG_BOSS.md`, `OPTIMISATION_WALLET_ALERTS.md` |
| Tests principaux | `tests/test_wallet_alerts_ws.py`, `test_gmgn_native_host.py`, `test_gmgn_extension.mjs`, `test_gmgn_launcher.ps1`, `test_wallet_radar_menu.ps1` |

Les scripts `discord_*` sont partagés avec d'autres bots : ne pas changer leurs listes globales pour remplacer seulement la liste de Wallet Radar.

## Vérification et livraison

Dernière validation de la version .3 : **301 tests Python**, tests de lancement/gestion Windows avec SSH/SCP simulés et protocole de notifications ; hôte natif testé via PowerShell portable. Les 8 tests JS de navigation avaient également passé pour la version précédente ; l'extension n'a pas changé pour ce mode. Pas de transaction ni message Telegram envoyé par ces tests. Les 30 tokens réels de l'essai ne sont pas encore collectés depuis le cloud. L'exécution complète Chrome/Windows est confirmée par l'utilisateur pour les étapes antérieures, pas par un accès direct à son PC.

Correctif .4 : **305 tests Python passent**, dont un arrêt SIGINT d'un processus de test isolé avec conservation d'un autre processus Python ; menu 5 validé avec SSH/SCP simulés. L'arrêt réel sur le VPS reste à effectuer par l'utilisateur.

Correctif .5 : **307 tests Python passent**. Tous les noms et types du schema TradeEvent sont compares au SDK officiel 4.3.1 ; fixture archivee de 405 octets entierement lue, nouveau champ et compatibilite des anciens evenements verifies. Test de progression du nouveau format jusqu'a une decision theorique, sans ordre. Aucun resultat reel des prochains tokens corrige n'est encore acquis.

Correctif .6 : **312 tests Python passent**, dont codes de fournisseur sans secrets, comptes binaires/PumpSwap identifies, recherche d'un achat plutot qu'une vente dans l'historique, preservation du journal et maintien du mode --check hors reseau. Menu 6 teste avec SCP/SSH simules. Le diagnostic reel reste a executer par l'utilisateur ; aucune cause precise de l'echec de position n'est encore confirmee.

Version 2026.11.16.1 : **319 tests Python passent**, plus tests des lanceurs et du menu Windows avec mocks. Cas de non-rattrapage sans perte de donnees, operations annexes, vente apres 1 s, vrai swap non decode, mouvement de position avant 1 s et logs tronques couverts. Aucun ordre reel. Correctif encore a appliquer et observer sur le VPS.

Commandes usuelles dans le dépôt :

```bash
/workspace/lk-venv/bin/python -m unittest discover -s tests
node --test tests/test_gmgn_extension.mjs
```

Les tests Python de l'hôte natif sont optionnels sans PowerShell. Pour les inclure, définir `GMGN_PWSH` vers un exécutable `pwsh` disponible. Tests Windows : `pwsh -NoProfile -File tests/test_wallet_radar_menu.ps1` et `tests/test_gmgn_launcher.ps1` avec mocks. Les chemins sous `/tmp` d'une session antérieure peuvent disparaître.

Derniers correctifs remis à l'utilisateur, sous `/workspace/artifacts` si disponibles :

- `gmgn_onglet_unique_1.0.0.zip` : extension + pont natif.
- `wallet_unique_BBIG_BOSS_2026.11.15.1.zip` : configuration BBIG BOSS et désactivation des anciennes attentes.
- `wallet_radar_menu_2026.11.15.2.zip` : menu Windows + gestion interactive.
- `wallet_radar_simulation_30_tokens_2026.11.15.3.zip` : mode 3 de simulation gratuite BBIG BOSS et mode 4 de bilan.

Les archives doivent exclure les configurations réelles, clés, bases, `.venv` et données personnelles. Préserver ces fichiers au déploiement ; ne jamais demander de remplacer tout `scripts` en effaçant des fichiers des autres bots.

## Mise à jour de cette fiche

Après un changement durable : actualiser date, version, wallet(s) demandés si connus, procédures, mesures et limites. Distinguer systématiquement ce qui est implémenté dans le dépôt, préparé en archive, installé par l'utilisateur et directement vérifié. Les futures instructions de l'utilisateur restent prioritaires sur cet instantané.
