# Test processed / confirmed — version 2026.11.14.4

Le mode `--early` suit chaque wallet aux niveaux processed et confirmed en parallèle. Pour DECU et LE L, cela représente **4 abonnements sur une même connexion WebSocket**. Sans cette option, la surveillance confirmed habituelle reste disponible.

## Ce qui s’affiche

Une notification processed fait apparaître immédiatement dans la console : **TRANSACTION PROVISOIRE mentionnant DECU**, avec heure locale à la milliseconde et signature. Ce n’est pas encore la preuve d’un achat par ce wallet : les logs seuls ne suffisent pas à identifier tous les comptes, mouvements et contreparties. Aucun message Telegram ni ordre de trading n’est envoyé pour cette notification provisoire.

Après notification confirmed et décodage de la transaction, un achat ou une vente vérifié affiche les réceptions processed et confirmed, puis **l’écart local au centième de seconde**, par exemple `0,37 s`. Le temps confirmed→décodage est également affiché au centième.

- L’écart est calculé avec une horloge monotone à haute résolution. Le journal garde la valeur non arrondie en millisecondes ; seul l’affichage est arrondi à 0,01 seconde.
- Les deux premières réceptions doivent appartenir au même branchement WebSocket. Après une reconnexion ou un redémarrage, une paire incomplète n’est pas utilisée pour inventer un écart.
- Si confirmed arrive avant processed, un écart négatif est conservé et expliqué. On ne remplace pas cet écart par zéro.
- Si processed manque, la comparaison est indiquée comme indisponible ; la vérification confirmed fonctionne toujours.
- Une notification processed sans confirmation enregistrée ne déclenche jamais d’alerte d’achat vérifié. L’absence de confirmation dans le journal ne prouve pas, à elle seule, l’échec de la transaction.
- Il s’agit de l’écart **entre les arrivées chez nous**, pas du temps exact entre les étapes d’exécution sur Solana. La référence blockTime reste grossière à la seconde. Ni l’affichage au centième ni la notification précoce ne garantissent une détection 0,01 seconde après l’achat.

## Installer

1. Arrêter le lancement manuel sur le VPS avec Ctrl+C.
2. Extraire l’archive sur le PC et copier uniquement `scripts/wallet_alerts_ws.py` dans `C:\Users\Enzo\Downloads\wallet-alerts\scripts`, avec remplacement. Conserver le fichier de configuration et tout `data`.
3. Dans **PowerShell Windows** (`PS C:\Users\Enzo>`), transférer le fichier :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

4. Dans la **fenêtre SSH du VPS**, depuis `/home/bot/wallet-alerts`, lancer :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --early
```

Le démarrage doit afficher **2026.11.14.4**, puis `WEBSOCKET CONNECTÉ : 2 wallets ; 4 abonnements.` Aucun message Telegram tant que `--telegram` n’est pas ajouté. Ne pas démarrer une deuxième instance sur le même journal.

Le journal existant est migré automatiquement sans supprimer ni rejouer les trades précédents. Les anciennes lignes n’ont pas de mesures processed inventées. Les notifications et mesures ne sont disponibles que pour les événements réellement reçus pendant ce test.

## Lire le bilan après plusieurs transactions

Après quelques dizaines de transactions, arrêter le test avec Ctrl+C et lancer :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --timing-report
```

Le bilan affiche le nombre de transactions de swap vérifiées, les paires comparables, médiane et P95 des écarts processed→confirmed et confirmed→décodage, provisoires sans confirmation enregistrée et fenêtres de rattrapage incomplètes. Le P95 est le délai sous lequel se trouvent environ 95 % des observations ; avec peu de transactions, l’estimation est fragile. Les transactions non qualifiées comme achats/ventes ne sont pas mélangées à ces statistiques. Le bilan lit le journal sans connexion réseau, sans modifier les états d’envoi Telegram.

Pour poursuivre la collecte, relancer avec `--early`. La clé Helius et les réglages restent inchangés. Le fichier systemd déjà fourni ne reçoit pas automatiquement cette option : elle devra être ajoutée explicitement si ce mode est retenu pour le service permanent.

Validation : 250 tests passent, y compris migration de l’ancien journal, double abonnement pour chacun des deux wallets, signal provisoire sans alerte vérifiée, horloge système ajustée, reconnexion, duplications, ordre inversé des notifications et rapport sans modification du journal. Services externes simulés ; aucun achat ni message réel envoyé.
