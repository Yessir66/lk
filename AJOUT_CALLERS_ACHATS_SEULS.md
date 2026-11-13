# Deux callers supplémentaires pour les achats — 2026.11.01.1

> Ancienne configuration, remplacée par [les huit nouveaux callers du 13 novembre 2026](REMPLACEMENT_8_CALLERS.md). Les deux callers de ce document ne font plus partie des sources actives.

| Profil pump.fun | Nom affiché | Fonctionnement |
|---|---|---|
| https://pump.fun/profile/ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1 | ApRnQN2H | Achats uniquement |
| https://pump.fun/profile/CwUHN4zTn5wiEYoZjsP4FrDvAT9heDWewCTQjhgwhJqS | CwUHN4zT | Achats uniquement |

Les noms affichés utilisent le début du wallet pour conserver une identité stable. Ces deux sources peuvent déclencher un achat si elles sont cochées. Elles n’entraînent ni republication du callout, ni republication des updates, ni alerte source Telegram. En mode Trojan, l’envoi du CA au bot Trojan reste nécessaire pour déclencher l’achat.

lynk0x_, YourMistyy et narc conservent leurs achats, alertes et republications. L’exception de délai de 5 secondes reste propre à YourMistyy. Les deux nouveaux callers utilisent le délai général « Délai avant achat », 12 secondes par défaut. Les autres réglages restent ceux de l’interface ; en mode Trojan, le montant acheté et les sorties TP/SL se règlent dans Trojan.

Les achats restent dédupliqués par token dans le journal de l’exécuteur : deux callers qui signalent le même token ne déclenchent pas deux achats. Le profil qui a créé le job détermine sa politique de publication. Un premier signal issu d’un caller « achats seuls » ne devient pas une republication si un autre caller signale ensuite le même token.

## Installation

1. Dans le tableau de bord des achats, cliquer sur **Arrêter**, puis fermer son terminal avec Ctrl+C. Le lecteur Discord et son analyse peuvent rester ouverts : ils sont indépendants.
2. Extraire l’archive. Copier les quatre fichiers contenus dans `scripts` vers le dossier `scripts` du projet qui lance cette interface. Copier les deux fichiers de `web` dans le dossier `web` du même projet. Accepter le remplacement des fichiers correspondants. Conserver les autres fichiers, les configurations et tout le dossier `data`.
3. Relancer `LANCER_NOUVELLE_INTERFACE.bat`, puis recharger la page avec Ctrl+F5. La version serveur affichée doit être **2026.11.01.1**.
4. Les sélections déjà enregistrées sont conservées. Cocher **ApRnQN2H · achats seuls** et **CwUHN4zT · achats seuls**. Vérifier le mode et les réglages avant de cliquer sur **Démarrer**, qui enregistre aussi les paramètres. La mise à jour ne lance aucun achat par elle-même.

Le patch fonctionne avec l’exécuteur Jupiter et avec le relais Trojan. Les positions existantes conservent leur gestion des sorties. Aucun changement n’est apporté aux scripts de détection Discord ni aux narratives DECU / LE L.

Validation : tests simulés des deux exécuteurs, déduplication, délai général, filtrage des alertes, refus des publications et updates, absence de préparation IA/images pour ces deux profils, restauration des jobs et conservation de la sélection existante. Aucun achat réel ni message Telegram réel n’a été envoyé pendant les tests.
