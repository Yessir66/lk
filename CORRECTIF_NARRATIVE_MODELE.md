# Correctif narrative — 2026.10.31.6

L’achat DECU est maintenant détecté et l’alerte Telegram est confirmée. L’erreur `NotFoundError` concerne seulement l’IA : le serveur renvoie HTTP 404. Le modèle demandé ou la route API peuvent en être la cause ; le code ne permettait pas de les distinguer.

Le correctif reprend automatiquement la constante `MODEL` de ton ancien `scripts/alert_scout.py` (dans le document fourni : `claude-opus-5-5`) sans exécuter ce fichier. Un modèle explicitement défini dans `ANTHROPIC_MODEL` ou `callout_narrative_model` garde la priorité. Le terminal affiche le modèle choisi. Si l’erreur persiste, son diagnostic indique le statut HTTP sans afficher de clé ni le contenu de la réponse API.

1. Dans le terminal d’analyse, faire Ctrl+C. Garder le lecteur Discord et Chrome ouverts.
2. Extraire cette archive et copier ses deux fichiers Python dans le dossier `scripts` de `C:\Users\Enzo\Downloads\lk-claude-quirky-archimedes-6j2dkq`, en remplaçant les deux fichiers correspondants. Conserver tous les autres fichiers, notamment `alert_scout.py`, la configuration et `data`.
3. Dans le terminal ouvert à la racine du projet, lancer :

```bat
py scripts\discord_solana_watch.py --retry-narrative
```

Le terminal doit afficher `2026.10.31.6` et le modèle sélectionné. Cette reprise conserve l’alerte initiale et prépare une réponse de narrative ; elle peut aussi reprendre les autres narratives échouées. Ne pas ajouter `--retry-mint`.

Si un ancien réglage explicite force encore Sonnet, modifier seulement `callout_narrative_model` dans la configuration existante pour utiliser le modèle accessible à ton compte. Ne jamais partager les clés.

Validation : 208 tests passent, dont la lecture du modèle sans exécution du Scout, la priorité des réglages et l’absence de secrets dans le diagnostic HTTP. Aucun appel Anthropic réel n’a été effectué ici : l’accès au modèle reste à vérifier sur ton PC.

Le délai affiché de 12 872 890 ms mesure l’écart depuis le signal Discord conservé jusqu’à l’envoi de cette reprise, soit environ 3 h 35 ; ce n’est pas le temps de calcul de cette analyse seule.
