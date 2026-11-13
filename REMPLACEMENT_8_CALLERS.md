# Huit nouveaux callers — version 2026.11.13.1

Les cinq anciens profils sont retirés des sources actives. Les huit profils ci-dessous ont tous le même fonctionnement : **achat, publication du callout initial après confirmation de l’achat, puis republication des updates de l’auteur en réponse au callout publié**.

| Nom affiché | Profil pump.fun |
|---|---|
| Dzv3gkwm | https://pump.fun/profile/Dzv3gkwmkMkPoJwSaRCPyQZFhc8hNUL3FwaFgp3tgKUr |
| FniZYxox | https://pump.fun/profile/FniZYxox48b6FPfJC9XZVAdVT3fQ6dUiXzQ5ZBjUCyz6 |
| DJ78WYDT | https://pump.fun/profile/DJ78WYDTkU5Fo5oJDMyqD7KmXJKQUQMiqsuxvokUdxmh |
| 4JBkkjZh | https://pump.fun/profile/4JBkkjZhReTMCtriRoL7epbTMCoxNoX1Sab5vUvqDfMy |
| 8Fxc1gYm | https://pump.fun/profile/8Fxc1gYmuR1fvQsnCXHnFCmimNpUZ4r5gopuVoM1pjvg |
| 3saciJi6 | https://pump.fun/profile/3saciJi6vUnWx1GgWPEwtgSQCscXstacqZkPZCpuxffW |
| GAVq4j28 | https://pump.fun/profile/GAVq4j28TVdfG138wMtgLcPQGiP2rEkzUmWmhZwUrRa8 |
| 2oHsGWdP | https://pump.fun/profile/2oHsGWdPkHoCfesoUfJHQfjnaethvUuYvEANQByghpXd |

Les noms affichés sont les huit premiers caractères du wallet, et non des pseudos vérifiés. Les adresses complètes identifient les profils surveillés.

## Réglages et historique

- Tous utilisent le **délai général avant achat**, 12 secondes par défaut. L’exception YourMistyy de 5 secondes est retirée. Le délai des updates reste celui de l’interface.
- Les paramètres existants sont conservés : profil personnel, mode, exécuteur, montant, délais, âge maximal et sorties. Si l’ancienne sélection ne contient plus aucun profil admissible, les huit nouveaux sont sélectionnés au chargement. Aucun démarrage automatique.
- En mode Trojan, montant et TP/SL restent réglés dans Trojan.
- Les anciens callers ne créent plus de nouveaux jobs. Leurs achats encore à l’état « new » sont annulés au démarrage du worker ; leurs publications et updates sont désactivées. Les transactions déjà signées ou envoyées restent suivies sans créer une nouvelle commande d’achat.
- Les journaux, positions existantes et suivi des sorties sont conservés. Les anciennes lignes peuvent rester visibles dans l’historique.
- La déduplication par token reste en place : un token déjà traité n’est pas racheté simplement parce qu’un nouveau caller le signale.

## Installation sur le PC

1. Cliquer sur **Arrêter** dans l’interface des achats, puis fermer son terminal avec Ctrl+C.
2. Extraire l’archive. Copier les trois fichiers de `scripts` et les deux fichiers de `web` dans les dossiers correspondants du projet utilisé. Accepter les remplacements. Conserver les autres fichiers, notamment `data`, les sessions et les configurations.
3. Copier si nécessaire `LANCER_NOUVELLE_INTERFACE.bat` à la racine du même projet, puis le lancer. Il utilise le port local 8770 pour laisser le port du lecteur Discord disponible.
4. Recharger http://127.0.0.1:8770 avec Ctrl+F5. Vérifier la version **2026.11.13.1**, les huit callers, l’exécuteur et les délais avant de démarrer dans le mode souhaité.

Diagnostic facultatif depuis la racine du projet, sans démarrer le hub :

```bat
py scripts\callout_dashboard.py --diagnose
```

La mise à jour ne change pas le mode d’accès à pump.fun et ne corrige pas le challenge Cloudflare rencontré sur le VPS Hostinger.

Validation : 221 tests passent. Les huit profils sont testés avec Jupiter et Trojan simulés : délai, confirmation avant publication, update au délai configuré, réponse au bon callout et déduplication. Les cinq anciens profils sont rejetés et leurs positions conservées. La migration des réglages préserve les paramètres personnalisés. Aucun achat réel ni message réel n’a été envoyé pendant les tests.
