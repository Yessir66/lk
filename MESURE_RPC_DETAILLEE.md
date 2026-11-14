# Détail des délais RPC — 2026.11.14.5

Cette mise à jour ajoute uniquement des mesures. Le RPC reste en lecture seule, limité localement à **2 requêtes par seconde**, partagé entre lecture des transactions et rattrapage. Les mêmes swaps sont vérifiés après confirmation. Aucun changement au bot d’achats, aucune activation automatique de Telegram.

Le mode de collecte reste :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --early
```

Après chaque achat ou vente vérifié pour lequel toutes les étapes sont mesurables, une ligne **Détail** affiche le temps en file, l’accès au RPC, le quota local, HTTP+JSON, l’attente entre essais, le décodage et le nombre de lectures. Les valeurs affichées sont arrondies au centième de seconde ; les mesures plus fines sont conservées.

## Ce qui est mesuré

| Étape | Mesure |
|---|---|
| File avant première lecture | Réception confirmed → première prise en charge par le worker |
| Attente accès RPC partagé | Attente de la fin d’une autre lecture RPC, par exemple un rattrapage |
| Régulateur local 2 requêtes/s | Pause volontaire du programme pour respecter son plafond local ; ce n’est pas une lenteur du réseau |
| Pause après HTTP 429 | Attente imposée par le programme après un refus de quota du fournisseur |
| Ordonnancement des lectures | Mise en route du thread HTTP et retour de son résultat au programme |
| RPC HTTP + JSON | Réseau, fournisseur, lecture et validation de la réponse JSON, cumulés sur tous les essais |
| Attente entre essais | Temps entre un essai sans résultat/avec erreur et le suivant ; inclut délai de réessai et attente de reprise par le worker |
| Décodage des swaps | Analyse locale des comptes, quantités, contreparties et preuves de swap |
| Autres opérations locales | Petit solde des opérations locales entre les étapes instrumentées |
| Total confirmed → décodage | Mesure complète au moyen de l’horloge monotone |

HTTP+JSON ne sépare pas le traitement chez Helius du temps de transport réseau. Les étapes HTTP sont additionnées lorsqu’il faut réessayer une transaction pas encore disponible. La file initiale n’est pas recomptée à chaque réessai.

Les profils incomplets après redémarrage, reconnexion ou collecte partielle ne servent pas aux statistiques détaillées. Les anciennes mesures restent dans le rapport général, sans phases inventées. Le journal stocke les profils par wallet/signature, ainsi que les essais sans résultat. Aucune URL ou clé n’est stockée dans ces profils.

## Installer

1. Sur le VPS, arrêter le test manuel avec Ctrl+C.
2. Extraire l’archive sur le PC et remplacer uniquement `wallet_alerts_ws.py` dans `C:\Users\Enzo\Downloads\wallet-alerts\scripts`.
3. Dans **PowerShell Windows**, à l’invite `PS C:\Users\Enzo>`, transférer :

```powershell
scp "C:\Users\Enzo\Downloads\wallet-alerts\scripts\wallet_alerts_ws.py" bot@2.24.62.69:/home/bot/wallet-alerts/scripts/
```

4. Dans la **fenêtre SSH du VPS**, depuis `/home/bot/wallet-alerts`, relancer :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --early
```

Vérifier **2026.11.14.5** et **4 abonnements**. Conserver `wallet_alerts_config.json` et `data` : les mesures existantes sont préservées et le journal est migré automatiquement. Les dépendances ne changent pas.

## Envoyer le bilan

Après plusieurs nouvelles transactions, arrêter avec Ctrl+C, puis lancer :

```bash
.venv/bin/python scripts/wallet_alerts_ws.py --timing-report
```

Copier ici le résultat ou envoyer une capture. Il faut de **nouvelles transactions après installation** pour remplir la section « Profils détaillés complets ». Elle affiche médiane et P95 de chaque étape sur les profils complets d’achats/ventes vérifiés. Les médianes des étapes ne s’additionnent pas ; comparer le total mesuré séparément. Aucun chiffre ne comprend la livraison Telegram, qui reste désactivée sans `--telegram`.

Validation : 256 tests passent, dont séparation quota local/HTTP+JSON, attente du RPC partagé, profil complet du décodeur, plusieurs tentatives sans double comptage, reconnexion qui invalide une mesure complète et rapport qui exclut les profils partiels. Aucun achat ou message réel envoyé.
