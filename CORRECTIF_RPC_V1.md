# Correctif 2026.10.31.4 : achat DECU non détecté

Ce correctif s’applique à la version 2026.10.31.3 déjà installée. Il contient les deux scripts à remplacer ; aucun changement de clé, de dépendance, d’extension ou de configuration n’est nécessaire.

Le RPC refusait les transactions Solana v1 car le client déclarait ne prendre en charge que v0 (erreur -32015). Le client accepte maintenant v1. Le cas public DECU fourni par l’utilisateur a été reproduit et relu avec le client corrigé :

https://solscan.io/tx/5oiP9vYXXGZFdE9NAMQWPA5mig3EFSdBmTzQAaxJVbBcgdWpvkYBLXrHWs28Ker8KpKRh6PVYz1vEbX1pWUN3eGi

Le filtre strict de preuve d’achat est inchangé. Les diagnostics distinguent désormais une erreur de lecture, une analyse partielle et une absence de preuve retrouvée. Une transaction future non prise en charge n’efface plus les achats déjà vérifiés sur d’autres transactions.

## Installation Windows

1. Arrêter uniquement le terminal du module d’analyse avec Ctrl+C. Le lecteur Discord peut rester ouvert.
2. Extraire le ZIP et copier ses deux fichiers `scripts/discord_token_analysis.py` et `scripts/discord_solana_watch.py` dans le dossier `scripts` existant, en acceptant leur remplacement. Conserver intégralement `data`, `scout_config.json`, l’extension et les autres scripts.
3. Dans un terminal :

```bat
cd /d "C:\Users\Enzo\Downloads\lk-claude-quirky-archimedes-6j2dkq"
py scripts\discord_solana_watch.py --retry-mint YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump
```

Vérifier la version **2026.10.31.4** au démarrage. La réanalyse conserve l’heure originale du signal Discord. Le programme poursuit ensuite la surveillance normale : il n’est pas nécessaire de lancer un deuxième module d’analyse.

Si la lecture reste incomplète, les lignes DECU / LE L du terminal indiquent maintenant le code RPC, le nombre de signatures parcourues, de transactions lues/indisponibles et de mouvements non attribués. Ce correctif n’augmente pas le plafond d’historique et ne transforme pas un transfert en achat.

Validation : 200 tests automatisés, dont une régression sur la transaction publique réelle, et lecture effective de cette transaction avec le client corrigé. Aucun message Telegram ni ordre de trading n’a été envoyé depuis l’environnement de développement.
