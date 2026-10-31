# Correctif 2026.10.31.5 — recherche des achats dans l’historique du token

Le correctif v1 précédent reste actif. Ce deuxième correctif évite que les 300 signatures récentes, y compris échecs et opérations postérieures au signal, empêchent d’atteindre l’achat recherché.

La recherche cible les comptes du token : comptes actuels + comptes associés SPL/Token-2022, même fermés. Les transactions échouées et postérieures au signal ne consomment plus le budget d’analyse des transactions. La pagination de l’historique dispose d’une limite distincte (30 000 lignes par défaut).

## Installation

1. Arrêter uniquement le terminal du module d’analyse avec Ctrl+C. Le lecteur Discord peut rester ouvert.
2. Copier le contenu du ZIP dans le projet existant : remplacer les trois fichiers du dossier `scripts` et `requirements-discord.txt`. Conserver tout `data`, `scout_config.json`, l’extension et les autres scripts.
3. Dans le terminal :

```bat
cd /d "C:\Users\Enzo\Downloads\lk-claude-quirky-archimedes-6j2dkq"
py -m pip install -r requirements-discord.txt
py scripts\discord_solana_watch.py --retry-mint YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump
```

Vérifier la version **2026.10.31.5**. La réanalyse reprend l’heure originale du signal et la surveillance continue ensuite normalement. Ne pas démarrer un deuxième module d’analyse en parallèle.

## Vérification et limites

Le test réel sur le compte associé du token a retrouvé la transaction d’achat DECU fournie par l’utilisateur. Il a parcouru 1 847 signatures, dont 1 813 transactions échouées. Le RPC public a ensuite limité certaines lectures : le résultat partiel est conservé et signalé, sans inventer de transactions manquantes.

Le filtre exige toujours un swap réussi, signé par le wallet, relié au token et antérieur au signal. Une adresse dérivée ou un solde courant ne constitue pas en soi une preuve d’achat.

D’anciens comptes non associés fermés peuvent manquer : les totaux sont ceux des comptes observés. Un pourcentage sur le premier achat retrouvé est ainsi libellé, et non présenté comme un historique absolu garanti. Une limite encore atteinte est explicitement affichée ; le silence reste préférable à une fausse preuve.

Solders sert uniquement à calculer des adresses publiques ; aucune clé privée n’est utilisée. Aucun message Telegram ni ordre de trading n’a été envoyé depuis l’environnement de développement.
