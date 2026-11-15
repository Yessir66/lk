# Corriger une ancienne instance encore active

Version **2026.11.15.4**. Si la simulation s'arrete immediatement avec `RemoteError`, une autre surveillance peut encore detenir le verrou sur le VPS, meme si sa fenetre Windows a ete fermee.

1. Quitte l'ancien menu avec **0**.
2. Extrais ce correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`, en acceptant les remplacements.
3. Lance **WALLET_RADAR.cmd** et choisis **5 - Arreter proprement Wallet Radar sur le VPS**.
4. Saisis le mot de passe VPS si demande. Le menu transfere les scripts puis demande leur arret. Attends le message indiquant que Wallet Radar est arrete (jusqu'a 30 secondes), ou qu'aucune instance n'est active.
5. Choisis ensuite **3** pour la simulation sur 30 tokens, avec Chrome ouvert et l'extension active.

Le menu 5 identifie uniquement les processus Python du compte Linux courant qui executent le chemin exact du script Wallet Radar. Il demande un arret normal par SIGINT et attend sa fin. Il ne ferme pas les autres bots et ne supprime ni configuration, ni base, ni fichier de verrou. Si l'arret depasse le delai, il affiche une erreur et n'utilise pas de destruction forcee.

Si le meme probleme persiste apres un arret confirme, envoie la nouvelle capture : il faudra verifier une autre cause. Le message de verrou connu indique desormais directement le menu 5, au lieu du seul terme `RemoteError`.

Commande equivalente, uniquement dans un terminal SSH du VPS :

```bash
cd /home/bot/wallet-alerts
.venv/bin/python scripts/wallet_alerts_ws.py --stop
```

Cette commande ne demande ni cle API ni acces Helius et ne transmet aucun ordre de trading. Pour le bilan de simulation, le menu 4 reste disponible.
