# Callout Desk — interface locale Windows

![Aperçu de l’interface au premier lancement](docs/dashboard.png)

Le tableau de bord reprend la présentation sombre des captures : paramètres à gauche, positions et journal au centre, callouts à droite. Il pilote le hub existant avec un lecteur partagé. Il est accessible uniquement sur l’ordinateur qui le lance.

## Installation et lancement

1. Télécharger le ZIP actualisé de la branche GitHub.
2. Mettre à jour les dossiers `scripts` et `web`, les documents et `LANCER_INTERFACE.bat` dans ton installation existante. **Conserver le dossier `data`** avec ses fichiers de wallet/session et ses journaux. Les dossiers `data`, `scripts` et `web` doivent être au même niveau.
3. Arrêter l’ancien hub dans le terminal avant de démarrer via l’interface.
4. Dans la fenêtre CMD où `JUP_API_KEY` est déjà configurée :

```bat
py -m pip install -r requirements-repost.txt
py scripts\callout_dashboard.py
```

Le navigateur s’ouvre automatiquement. Garder le terminal ouvert. `LANCER_INTERFACE.bat` peut aussi lancer l’interface ; il hérite des variables de la fenêtre depuis laquelle il est exécuté. Un double-clic depuis l’Explorateur n’hérite pas d’une clé définie uniquement dans une autre fenêtre CMD : dans ce cas, utiliser la commande ci-dessus ou une variable d’environnement Windows persistante.

Aucune installation Node.js/npm n’est nécessaire. Le serveur est en Python et l’interface utilise des fichiers HTML/CSS/JS locaux. Pour choisir un autre port : `py scripts\callout_dashboard.py --port 8766`.

## Fonctionnement

- **Simulation** : flux public réel ; décisions d’achat/republication simulées, sans transaction, publication ou envoi Telegram.
- **Réel** : mêmes achats, republications et alertes Telegram que le hub existant. Le bouton indique « Démarrer en réel » ; aucun démarrage automatique à l’ouverture de la page.
- **Arrêter** : arrêt coopératif du processus lancé par cette interface, après les opérations en cours. Cela suspend aussi le suivi TP. Les tokens restent dans le wallet et les états dans le journal ; le démarrage suivant reprend ce journal. Ce bouton ne vend pas les positions.

Les changements de mode, profil, montant, délai, TP, slippage, âge maximal et sélection des callers sont appliqués au prochain démarrage. Arrêter le hub pour les modifier. Les paramètres non secrets sont mémorisés dans `data/dashboard/settings.json`.

Le mode réel utilise les fichiers `data/pump_wallet.txt`, `data/pump_token.txt`, la session Telegram existante et les variables de la fenêtre terminal. Les valeurs de clés ne sont ni demandées ni affichées dans le navigateur. L’indicateur « Disponible » constate une présence locale ; il ne valide pas un accès auprès du fournisseur. Si Telegram n’a jamais été connecté sur cet ordinateur, effectuer la première connexion dans le terminal avec le hub comme décrit dans [CALLOUT_HUB.md](CALLOUT_HUB.md).

## Données affichées

Le flux montre les callouts Solana déjà reçus par le lecteur public du hub, y compris les callers non sélectionnés. Aucun second lecteur pump.fun n’est créé pour l’interface. « Mes callers » filtre l’affichage selon la sélection des callers d’achat/republication. Les alertes Telegram continuent de suivre les abonnements du profil ; cliquer « Copier CA » copie uniquement l’adresse du token.

La pause du flux suspend **l’affichage** ; la détection et l’exécution continuent. La recherche filtre les callers, noms, symboles, commentaires et adresses. Les données du flux sont bornées aux 240 derniers callouts dans la page, avec un compteur d’événements de session.

Le tableau des positions et les indicateurs lisent les journaux SQLite existants en lecture seule. Toutes les positions ouvertes et les 300 entrées historiques les plus récentes sont disponibles. Le statut `empty` est présenté comme « Solde nul », sans le transformer en vente TP confirmée. Les réponses anciennes sans nom/symbole utilisent une adresse abrégée.

Le PnL est **une estimation** basée sur la dernière cotation minimale et le coût de la quantité encore suivie. Ce n’est pas un gain réalisé ni un montant net de frais réseau. Une valeur absente reste « — ». L’âge de la cotation est indiqué par position ; les refus d’API peuvent rendre cette cotation ancienne. Une erreur TP en cours est marquée « TP différé ». Le PnL global reste « — » si une position est bloquée ou si sa dernière cotation remonte à plus d’une minute. Le tableau de bord ne crée aucune requête de prix supplémentaire.

« Vider l’affichage » masque les lignes de journal de l’écran, sans effacer les positions ni les journaux d’exécution. La fermeture d’un onglet navigateur ne ferme pas le serveur ni le bot : utiliser Arrêter, puis Ctrl+C dans le terminal pour fermer l’interface.

## Validation et limites

Le démarrage/arrêt du hub en simulation, le vrai flux public, la recherche, le presse-papiers et l’aide ont été vérifiés dans Chromium. L’affichage desktop et mobile a été inspecté sans débordement horizontal. Les tests Python couvrent les contrôles locaux, les modes, la sélection des callers, la lecture des positions, la confidentialité des réponses et la distribution du flux sans élargir les filtres de trading.

```bat
py -m unittest discover -s tests -v
```

Les contrôles réels de l’interface reprennent le moteur existant. Aucun achat réel n’a été déclenché pour les tests de cette interface. Le fonctionnement en réel depuis le navigateur et l’ouverture automatique sur Windows restent à vérifier sur le PC avec ses fichiers locaux. Un verrou de processus empêche le hub et le bot de republication de démarrer deux fois dans la même installation. Il se libère à l’arrêt ou si le processus est interrompu. Ne pas lancer une deuxième copie depuis un autre dossier : des installations distinctes ne partagent pas ce verrou.
