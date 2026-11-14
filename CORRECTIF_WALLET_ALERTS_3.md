# Correctif WebSocket 2026.11.14.3

La surveillance fonctionne : les achats de DECU sont reconnus. Les 48/102 ms affichées mesurent notification→décodage, et non le délai total depuis l’achat. Cette version affiche aussi blockTime→décodage, estimation grossière liée à l’horodatage du bloc à la seconde.

Le rattrapage passe de 10 pages de 100 à 10 pages de 1000 signatures par wallet. Les transactions échouées restent ignorées. Une seule écriture de journal par page évite de ralentir le WebSocket. Si plus de 10 000 signatures séparent deux contrôles, la couverture partielle reste signalée ; les anciens trous enregistrés ne sont pas réparés automatiquement. Le quota RPC reste partagé avec les autres programmes Helius.

## Installation

1. Sur le VPS, arrêter le lancement manuel avec Ctrl+C.
2. Extraire l’archive sur le PC et copier ses deux fichiers Python dans `C:\Users\Enzo\Downloads\wallet-alerts\scripts`, avec remplacement. Ne pas remplacer `wallet_alerts_config.json` ni supprimer `data`.
3. Dans PowerShell Windows, à l’invite `PS C:\Users\Enzo>`, transférer les deux fichiers :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" "C:\Users\Enzo\Downloads\wallet-alerts\scripts\configure_wallet_alerts.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

4. Dans la fenêtre SSH du VPS, relancer depuis `/home/bot/wallet-alerts` :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py
```

Le terminal doit afficher la version 2026.11.14.3. Helius et le journal existants sont conservés. Aucun envoi Telegram sans `--telegram`.

## Configurer Telegram quand le test est validé

Arrêter le test avec Ctrl+C, puis lancer :

```bash
.venv/bin/python scripts/configure_wallet_alerts.py --telegram
```

Entrer le token de ton bot d’alertes (BotFather) avec saisie masquée, puis son Chat ID. Ne pas utiliser la clé Helius ou une session de compte Telegram. Aucun secret ne doit être envoyé dans le chat. La configuration Helius est conservée et aucun message n’est envoyé à cette étape. Puis lancer explicitement :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --telegram
```

Les alertes concernent les transactions futures et récentes ; les anciens trades déjà décodés ne sont pas rejoués. Le service systemd fourni dans le paquet initial reste en console uniquement tant que `--telegram` n’est pas ajouté à son ExecStart.

Validation : 243 tests passent avec transactions et services externes simulés. Aucun achat ni message réel envoyé pendant les tests.
