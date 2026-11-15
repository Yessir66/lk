# Suivre uniquement BBIG BOSS

Wallet public : `BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp`.

La configuration doit remplacer DECU et LE L par **BBIG BOSS**. La cle Helius, les URL RPC et les autres reglages sont conserves. Les anciens evenements restent dans l'historique, mais les evenements et alertes encore en attente pour les wallets retires sont desactives au prochain demarrage. Le programme d'analyse Discord est distinct : sa liste de wallets n'est pas modifiee.

## Mise a jour une seule fois

1. Arrete le lanceur actuel avec **Ctrl+C** et ferme sa fenetre.
2. Extrais le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`. Remplace les deux scripts sous `scripts`, sans effacer la configuration, `.venv`, `data`, `windows` ou `chrome-gmgn`.
3. Dans **PowerShell Windows**, a l'invite `PS C:\...>` :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" "C:\Users\Enzo\Downloads\wallet-alerts\scripts\configure_wallet_alerts.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

Puis connecte-toi au VPS :

```powershell
ssh bot@2.24.62.69
```

4. Dans ce terminal SSH, a l'invite `bot@...`, execute :

```bash
cd /home/bot/wallet-alerts
.venv/bin/python scripts/configure_wallet_alerts.py --wallet BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp --name "BBIG BOSS"
.venv/bin/python scripts/wallet_alerts_ws.py --check
```

Le helper ne demande pas de nouvelle cle et ne fait aucun appel reseau. La verification doit afficher `Wallets : BBIG BOSS`. Ce controle ne demarre pas la surveillance. Ferme la connexion SSH avec `exit`.

## Lancer le bot au quotidien

1. Ouvre ton Chrome habituel avec l'extension GMGN active et l'onglet de graphique reserve. Garde-le ouvert.
2. Dans `C:\Users\Enzo\Downloads\wallet-alerts\windows`, double-clique sur **LANCER_GMGN_ONGLET_UNIQUE.cmd**.
3. Saisis le mot de passe VPS si demande. Garde cette fenetre ouverte et ton PC connecte.

Tu dois voir **2026.11.15.1**, **Wallets : BBIG BOSS**, puis **WEBSOCKET CONNECTE : 1 wallets ; 2 abonnements** (les notifications processed et confirmed du meme wallet). Le lanceur demarre lui-meme le programme distant ; ne lance pas une autre instance.

Les nouveaux achats verifies recents commandent le graphique GMGN dans le meme onglet. Les achats repetes du token affiche ne rechargent pas le graphique. Les ventes sont detectees et affichees dans la console, sans ordre de vente automatique. Les detections provisoires ne pilotent pas GMGN. Telegram reste desactive. Le premier demarrage pour ce nouveau wallet initialise son suivi sans rejouer tout son historique.

Pour arreter : **Ctrl+C** dans la fenetre du lanceur. Pour reprendre plus tard, relance ce meme fichier Windows. Une interruption peut creer des lacunes d'historique : le rattrapage n'est pas garanti exhaustif.
