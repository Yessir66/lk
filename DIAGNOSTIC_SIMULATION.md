# Comprendre les cas incomplets sans interrompre l'essai

Version **2026.11.15.6**. Le dernier bilan utilisateur montrait 5/30 tokens : 2 donnees insuffisantes, 2 non supportes et 1 volume insuffisant observe. Aucun achat theorique valide. Ce n'est pas une preuve que les conditions etaient absentes du marche : une partie des donnees manque.

Le menu **6** effectue au maximum 8 lectures RPC (4 cas, comptes de position et transaction d'achat), a environ une requete par seconde. Il montre les codes HTTP/RPC lorsqu'ils sont disponibles, le format des comptes (JSON ou binaire), la presence de Pump.fun/PumpSwap, les evenements Pump reconnus, leur utilisateur et leur devise de cotation. Les messages prives et reponses brutes du fournisseur ne sont pas affiches.

## Installation et utilisation

1. **Garde ouverte la fenetre qui collecte les tokens. Ne l'arrete pas.**
2. Dans la seconde fenetre utilisee pour le bilan, tape **0** pour quitter ce menu.
3. Extrais le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts` en acceptant les remplacements.
4. Ouvre une nouvelle fenetre de **WALLET_RADAR.cmd** et choisis **6 - Diagnostiquer les cas incomplets**.
5. Saisis le mot de passe VPS si demande pour le transfert puis la connexion. Envoie une capture du resultat, ou copie son texte.

Le diagnostic transfere les scripts mais **ne redemarre pas le programme deja en memoire**. Il ne remet pas les 5/30 tokens a zero, ne modifie ni la configuration ni les journaux, ne prend aucun verrou de surveillance et n'envoie aucun ordre. La collecte continue avec sa version deja lancee. La nouvelle version sera utilisee lors du prochain demarrage volontaire.

## Interpreter les resultats

- **HTTP/RPC avec code** : refus ou erreur du fournisseur ; relever le code plutot que supposer un abonnement insuffisant.
- **Comptes binaires** : le serveur n'a pas fourni le JSON attendu. Il faut adapter/valider la lecture du compte, pas enlever le controle de position.
- **PumpSwap** sans evenement Pump.fun : la plateforme n'est pas couverte par ce premier decodeur. Ce n'est pas en soi un probleme de forfait Helius.
- **Pump.fun present, aucun evenement reconnu** : format ou logs a examiner.
- **Evenement reconnu mais utilisateur BBIG BOSS absent** : l'attribution via un autre acteur/routeur doit etre verifiee avant de modifier la detection.
- **Cotation autre que SOL/USDC** : la conversion USD de ce mode n'est pas disponible.
- **Lecture de position OK maintenant** : cela ne prouve pas que la lecture au moment du signal avait reussi ; un refus temporaire ancien peut ne pas etre reproductible.

Les comptes consultes representent l'etat actuel du wallet ; ce diagnostic ne reconstitue pas une position historique au dixieme de seconde. Le mode recupere la signature initiale enregistree ou, a defaut, l'achat retrouve le plus proche dans le journal, et ne doit pas etre utilise pour simuler retroactivement les fenetres de 1 et 3 secondes.

Les prochaines erreurs de position, apres redemarrage volontaire avec la .6, incluront leur classe et leur code dans les notifications, sans cle ni URL privee. Le diagnostic actuel ne peut pas recreer un code d'erreur ancien qui n'avait pas ete enregistre.
