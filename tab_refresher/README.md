# Auto Refresh — actualiser des onglets Chrome automatiquement

Extension Chrome qui recharge les onglets que tu choisis, chacun à son propre intervalle (de 1 seconde à plusieurs
heures). Elle tourne dans ton Chrome : les onglets restent connectés à tes comptes, et ils sont actualisés même en
arrière-plan ou fenêtre réduite.

## Installation (une seule fois)

1. Récupère le dossier `tab_refresher` sur ton ordi (`git clone` du dépôt, ou « Code → Download ZIP » sur GitHub puis
   décompresse).
2. Dans Chrome, ouvre `chrome://extensions`.
3. Active **Mode développeur** (interrupteur en haut à droite).
4. Clique **Charger l'extension non empaquetée** et choisis le dossier `tab_refresher`.
5. Clique l'icône puzzle 🧩 de la barre d'outils, puis l'épingle à côté de **Auto Refresh** pour garder son icône
   visible.

Chrome annonce que l'extension peut « lire l'historique de navigation » : c'est ce qui lui permet d'afficher le titre
des onglets dans sa liste. Elle n'envoie rien nulle part.

## Utilisation

- **Ajouter un onglet** : va sur l'onglet, clique l'icône Auto Refresh, choisis l'intervalle (s, min ou h) et clique
  **Démarrer**.
- **Retirer un onglet** : bouton **Arrêter** sur l'onglet concerné, ou la croix × dans la liste.
- **Raccourci clavier** : `Alt+Shift+U` ajoute ou retire l'onglet affiché, avec le dernier intervalle choisi. Pour le
  changer : lien « modifier » en bas de la fenêtre (ou `chrome://extensions/shortcuts`).
- **Changer un intervalle** : directement dans la liste, il s'applique tout de suite.
- **Aller sur un onglet** : clique son titre dans la liste.

L'icône affiche l'intervalle (`30s`, `5m`, `1h`) sur les onglets actualisés, et la fenêtre montre le temps restant
avant le prochain rechargement.

## À savoir

- Fermer un onglet le retire de la liste. Quand tu fermes Chrome, la liste est vidée : les onglets rouverts sont de
  nouveaux onglets pour Chrome.
- Si l'ordi se met en veille, l'actualisation reprend au réveil (un seul rechargement, pas un par intervalle manqué).
- Après une mise à jour des fichiers, clique la flèche ↻ de l'extension dans `chrome://extensions` (la liste est alors
  vidée).
