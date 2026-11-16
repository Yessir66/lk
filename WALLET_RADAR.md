# Wallet Radar

**2026.11.16.6 : menu 10** pour verifier RPC/Jupiter sans ordre et sans ressaisir les cles. Les erreurs precisent le service, le statut et un motif Jupiter reconnu sans exposer les secrets. Voir [DEPANNAGE_ACCES_REELS.md](DEPANNAGE_ACCES_REELS.md).

**2026.11.16.4 : essais reels explicites.** Les menus **7 / 8 / 9** configurent localement la signature, lancent l'essai reel de 1 USD avec budget reserve cumule de 15 USD, puis affichent les transactions/delais. Voir [ESSAIS_REELS_WALLET_RADAR.md](ESSAIS_REELS_WALLET_RADAR.md). Le menu 3 reste en simulation. Le SL/TP necessite que le processus reste actif.

**Wallet Radar** est le nom de reference de notre bot de surveillance Solana et d'affichage GMGN. Il detecte les achats et ventes des wallets choisis via Helius sur le VPS, puis transmet les achats verifies a Chrome sur ton PC. L'extension reutilise un onglet GMGN et conserve le graphique lorsqu'il affiche deja le meme token. Il est distinct du bot de republication de callouts et de l'analyse Discord.

Il n'effectue aucun achat ni vente automatique. Le lanceur n'active pas Telegram. La decision de vente reste manuelle.

Depuis la version **2026.11.15.3**, le menu propose aussi **3 : simulation des conditions BBIG BOSS sur 30 tokens**, et **4 : bilan du dernier essai**. Voir [SIMULATION_BBIG_BOSS.md](SIMULATION_BBIG_BOSS.md). Aucun ordre reel n'est active.

Depuis **2026.11.15.4**, **5 : arreter proprement Wallet Radar sur le VPS** permet de fermer une ancienne instance encore active avant de relancer. Voir [ARRET_WALLET_RADAR.md](ARRET_WALLET_RADAR.md). Les donnees et les autres bots sont conserves.

Depuis **2026.11.15.6**, **6 : diagnostiquer les cas incomplets**, en lecture seule et sans redemarrer la collecte. Voir [DIAGNOSTIC_SIMULATION.md](DIAGNOSTIC_SIMULATION.md).

Depuis **2026.11.16.1**, les lanceurs suivent **uniquement le direct** et n'affichent plus les anciennes transactions par rattrapage. Les operations annexes ne sont plus assimilees a des achats manquants ; les causes des donnees insuffisantes sont detaillees. Voir [DIRECT_ET_CAUSES_SIMULATION.md](DIRECT_ET_CAUSES_SIMULATION.md).

Depuis **2026.11.16.3**, la simulation convertit aussi les achats cotes en **PUMP, TTWO et DOGE** pour les adresses verifiees. Voir [COTATIONS_DOGE_TTWO.md](COTATIONS_DOGE_TTWO.md) et [COTATION_PUMP_USD.md](COTATION_PUMP_USD.md). Le prix DOGE est indicatif, issu d'une bougie minute ; toute cotation vieille de plus de 90 s est refusee. Les autres cotations inconnues restent exclues de la valorisation et aucun achat reel n'est active.

## Installer le menu une seule fois

1. Arrete l'ancien lanceur avec **Ctrl+C**.
2. Extrais le correctif dans **`C:\Users\Enzo\Downloads\wallet-alerts`** en acceptant les remplacements. Conserve les autres dossiers et fichiers. L'extension GMGN deja installee peut rester telle quelle.
3. A la racine de ce dossier, double-clique sur **`WALLET_RADAR.cmd`**.
4. Choisis **2 - Ajouter, retirer ou renommer les wallets**. Le menu transfere automatiquement les scripts necessaires vers ton VPS, puis ouvre leur gestion. Le mot de passe habituel du VPS peut etre demande **deux fois** : une pour le transfert, une pour la connexion. Ne saisis jamais de cle API ou de cle privee dans ces champs.

Ta liste actuelle est reprise depuis le VPS : **BBIG BOSS reste surveille** si tu ne modifies pas la liste. Rien n'est remplace automatiquement par les anciens wallets. La cle Helius et les reglages GMGN sont conserves.

## Gerer les wallets

Dans la gestion, les wallets apparaissent avec un numero, leur nom et leur adresse publique.

- **1 : afficher la liste.**
- **2 : ajouter.** Entre un nom, puis l'adresse publique Solana du wallet. Le wallet s'ajoute a la liste existante. Ne colle pas le CA d'un token.
- **3 : retirer.** Entre le numero du wallet affiche. Les autres wallets sont conserves.
- **4 : renommer.** Entre le numero puis le nouveau nom.
- **0 : quitter la gestion et revenir au menu Windows.**

Chaque modification valide est enregistree immediatement dans la configuration du VPS, sans modifier la cle Helius. Elle est prise en compte **au prochain lancement de la surveillance**. Si le bot tournait dans une autre fenetre, arrete-le avec Ctrl+C puis relance-le. Ce menu ne change pas a chaud ses abonnements WebSocket.

Les doublons d'adresse, noms deja utilises et adresses invalides sont refuses. La liste accepte jusqu'a 20 wallets. Au moins un wallet doit rester : pour remplacer le dernier, ajoute d'abord le nouveau puis retire l'ancien. Les evenements anciens restent dans l'historique, mais les evenements encore en attente des wallets retires sont desactives lors du redemarrage.

Le raccourci `windows\GERER_WALLETS.cmd` permet aussi d'acceder directement a cette gestion.

## Demarrer au quotidien

1. Garde Chrome ouvert dans ton profil habituel, avec l'extension GMGN active et son onglet reserve.
2. Double-clique sur **`WALLET_RADAR.cmd`**, puis choisis **1 - Demarrer la surveillance et GMGN en onglet unique**.
3. Le menu transfere d'abord les scripts puis lance la connexion : le mot de passe VPS peut etre demande deux fois. Garde le PC connecte et la fenetre du lanceur ouverte.

La console affiche **Wallet Radar**, puis les noms reellement suivis. Avec BBIG BOSS seul et le mode `--early`, il y a **1 wallet et 2 abonnements** : processed et confirmed. Il n'y a pas deux wallets.

N'ouvre pas plusieurs surveillances en parallele. L'ancien `LANCER_GMGN_ONGLET_UNIQUE.cmd` fonctionne encore, mais `WALLET_RADAR.cmd` devient le point d'entree conseille pour demarrer et gerer la liste.

Pour arreter la surveillance : **Ctrl+C** dans sa fenetre. Si Windows demande de terminer le programme de commandes, valide l'arret. Le rattrapage apres interruption n'est pas garanti exhaustif.

## Reutiliser ce nom dans nos discussions

Tu peux dire par exemple : « ajoute ce wallet a Wallet Radar », « retire ce wallet de Wallet Radar », ou « les graphiques de Wallet Radar chargent lentement ». Ce nom designe le suivi des wallets et GMGN, pas les autres bots du projet.
