# Wallet Radar 2026.11.17.3 — diagnostiquer une sortie indisponible

La notification `SORTIE_INDISPONIBLE` indique un echec de verification/preparation/suivi de sortie. Elle ne prouve ni que le seuil SL/TP a ete franchi, ni qu'un ordre a ete envoye, ni que la vente est confirmee. Le message17.2 etait trop general pour identifier la cause. Ne pas supposer que le SL/TP peut executer correctement tant que cet echec persiste.

## Sans arreter la position suivie

1. **Garder la fenetre du mode8 ouverte.** Aucun menu5 ni redemarrage n'est demande pour ce diagnostic.
2. Extraire ce ZIP dans `C:\Users\Enzo\Downloads\wallet-alerts`, en acceptant les remplacements. Garder les journaux et fichiers prives.
3. Ouvrir **un second** `WALLET_RADAR.cmd` apres extraction. Choisir **12 — Diagnostiquer la sortie de la position ouverte**.
4. Partager les lignes `ETAPE`, `BLOCAGE`, les statuts et quantites affiches. Aucune cle n'est demandee, hors mot de passe SSH habituel masque.

La copie des scripts ne recharge pas le processus deja actif. Celui-ci continue avec son code en memoire. Le diagnostic est un autre processus, limite a la lecture. Les notifications plus precises ajoutees en17.3 seront disponibles lors d'un prochain demarrage du bot ; aucun redemarrage n'est necessaire pour utiliser12.

## Ce que lit le diagnostic

- Une position ouverte/manuelle ou transaction pending du journal SQLite, via connexion **mode=ro**, puis fermeture de cette connexion avant les appels reseau. Il ne cree pas de journal et ne reserve pas de budget.
- Transaction pending : lecture du statut de sa signature ; pas de nouvelle preparation d'ordre.
- Position ouverte : quantite du journal comparee au solde actuel du wallet, puis cotation de liquidation complete en SOL.
- Cours SOL/USD frais pour comparer la valeur de liquidation a l'entree et au SL/TP. Une decision de sortie deja enregistree est conservee dans l'interpretation.
- Si aucun seuil ni decision de sortie n'est actif : annonce que l'evaluation est possible actuellement, sans tester de vente.
- Si une sortie est demandee : estimation de priorite, construction d'instructions, controle des tables, des frais/depots et simulation **non signee**. RPC limite1/s pour le diagnostic, pas de retries automatiques.

Le diagnostic ne lit pas `wallet.json`, n'instancie pas de signataire et n'appelle pas la methode de signature. Son transport interdit `sendTransaction` et les endpoints autres que lecture/cotation/construction. Aucun ordre, aucune modification de position ou d'enveloppe ; les valeurs lues decrivent l'instant du controle, pas l'etat historique exact a la notification.

Un echec affiche son etape et une cause sure : code HTTP/RPC, refus de route, cours perime, plafond frais/depot, simulation. Pour les erreurs de simulation reconnues, seuls l'index d'instruction et le code numerique du programme ou un libelle connu sont affiches ; pas les logs bruts, URL ou corps prives du fournisseur. Des codes identiques peuvent avoir des significations differentes selon le programme : ne pas les interpreter sans contexte.

## Autre token NON_SUPPORTE

La nouvelle notification de l'utilisateur `NON_SUPPORTE` pour AFk... concerne une entree distincte. Elle signifie que les conditions ne sont pas verifiables depuis le flux Pump.fun actuel ; aucun achat n'est valide pour ce signal. Elle ne confirme pas la sortie de la position DSrh... et ne change pas sa gestion. L'extension et Chrome ne sont pas la cause indiquee par ce message.

## Verification

381 tests Python passent, y compris tests facultatifs de l'hote natif avec PowerShell portable ; menu Windows12 valide avec SCP/SSH simules. Tests specifiques : diagnostic complet jusqu'a simulation sans lecteur de cle wallet ni signature, journal byte/mtime conserve, RPC mutateurs/envoi refuses, erreur de cotation situee avant les etapes suivantes, notification de sortie avec etape et code, code de simulation filtre. Aucun ordre reel envoye depuis le cloud. Cause reelle de la notification VPS encore a obtenir via12.
