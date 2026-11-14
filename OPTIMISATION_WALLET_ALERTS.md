# Priorité aux transactions directes — 2026.11.14.6

Les mesures précédentes ont montré une attente importante dans la file et dans le régulateur local à 2 requêtes/s, alors que certaines lectures HTTP+JSON ne prenaient que 0,02 à 0,03 seconde. Cette version réduit ces attentes sans changer les critères de vérification des achats et ventes.

- Les requêtes issues du WebSocket sont prioritaires sur le rattrapage pendant l'attente du quota. Le rattrapage ne réserve plus le transport HTTP pendant cette attente.
- Deux workers distincts traitent le direct et le rattrapage. Une transaction historique en cours de traitement ne monopolise plus le worker du direct.
- Le plafond HTTPS partagé dans ce processus passe à **5 requêtes/s**. Toutes les lectures de rattrapage, listes de signatures et transactions comprises, ont en plus un plafond commun de **1 requête/s**.
- Une seule lecture HTTP s'exécute à la fois. Une lecture de rattrapage déjà envoyée ne peut pas être interrompue par un événement direct.
- Après un HTTP 429, la pause globale de **60 secondes** reste appliquée.

La [documentation officielle Helius](https://www.helius.dev/docs/billing/plans-and-rate-limits), consultée le 14 novembre 2026, indique **10 requêtes RPC/s et 1 million de crédits mensuels** pour le forfait Free. Le plafond local de 5/s laisse une marge, mais les autres programmes utilisant le même compte partagent les quotas du fournisseur. Cette modification ne change aucun abonnement. Le quota mensuel dépend de l'activité réelle et doit aussi être surveillé dans Helius. Un plafond de 5/s ne garantit pas un fonctionnement gratuit illimité.

Le rattrapage est volontairement secondaire : sous forte activité, il peut prendre du retard. Les avertissements `HISTORIQUE PARTIEL` et les fenêtres incomplètes restent enregistrés ; cette mise à jour ne répare pas les anciennes lacunes et ne garantit pas un historique exhaustif.

## Remplacer uniquement le script

1. Dans le terminal SSH du VPS, arrêter la surveillance avec **Ctrl+C**.
2. Extraire l'archive de correctif sur Windows et remplacer `scripts\wallet_alerts_ws.py` dans `C:\Users\Enzo\Downloads\wallet-alerts`. Conserver `wallet_alerts_config.json`, `.venv` et `data`.
3. Dans **PowerShell Windows**, à une invite `PS C:\...>` :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

4. Dans le **terminal SSH du VPS**, à une invite `bot@...` :

```bash
cd /home/bot/wallet-alerts
.venv/bin/python scripts/wallet_alerts_ws.py --early
```

Le démarrage doit afficher la version **2026.11.14.6**, le plafond local **5/s**, puis **2 wallets ; 4 abonnements**. Garder cette fenêtre SSH ouverte pendant le test. Ne pas lancer une deuxième instance en parallèle. Aucun changement de dépendance ni nouvelle saisie de clé n'est nécessaire.

Le programme reste en lecture seule. Avec cette commande, Telegram reste désactivé. Les lignes `TRANSACTION PROVISOIRE` indiquent seulement une transaction mentionnant le wallet ; elles ne prouvent pas un achat et ne déclenchent aucun ordre. Les achats et ventes vérifiés restent décodés après confirmation.

## Comparer les résultats

Après plusieurs nouvelles transactions vérifiées, arrêter avec **Ctrl+C**, puis exécuter :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --timing-report
```

Le rapport conserve les anciennes données. Les statistiques détaillées sont séparées par **version et plafond local** : comparer le groupe `.5 ; plafond 2/s` avec `.6 ; plafond 5/s`, en regardant le nombre de profils, la médiane et le P95 du total `confirmed → décodage`, de la file et du régulateur local. Les statistiques globales en tête de rapport peuvent mélanger les versions ; utiliser les groupes détaillés pour cette comparaison. Les médianes des étapes ne s'additionnent pas.

Les profils complets concernent les nouvelles transactions mesurées sur la même connexion. Ces intervalles locaux sont affichés au **centième de seconde**, sans prétendre donner l'heure exacte d'exécution sur Solana. La livraison Telegram n'est pas mesurée. Un petit échantillon ne suffit pas à promettre un délai futur.

Pour revenir au plafond précédent tout en gardant la priorité au direct :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --early --rpc-rps 2
```

`--rpc-rps` accepte une valeur entre 0,5 et 10 (point décimal dans la commande). Ce plafond s'applique aux lectures HTTPS ; le WebSocket reste ouvert en continu. Les clés, wallets et historiques ne changent pas.
