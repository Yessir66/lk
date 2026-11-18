# Envoi RPC et erreurs lisibles — 2026.11.18.2

Les options de transmission n'avaient pas change entre les derniers achats reussis et les deux nouvelles expirations. Leurs signatures ne sont trouvees ni par le RPC public officiel ni par PublicNode ; aucune transaction finalisee n'a ete retrouvee sur le RPC officiel. Cela concorde avec le bilan expired, mais ne permet pas d'en deduire la cause technique.

## Correctif

- Le blockhash est obtenu en `confirmed`. L'envoi precise desormais **preflightCommitment=confirmed**, au lieu de laisser le fournisseur choisir la valeur par defaut. La verification avant envoi reste active (`skipPreflight=false`).
- Les reponses utiles de l'envoi sont conservees dans le journal et affichees dans **9** : accusé RPC correspondant a la signature, refus RPC avec son code, simulation preflight refusee avec son erreur connue, erreur HTTP, ou transmission de resultat inconnu.
- Pour une erreur de simulation, seuls les libelles reconnus, l'index d'instruction et le code numerique du programme peuvent etre affiches. Par exemple `BlockhashNotFound` ou `instruction6 ; code programme6001`. Ces codes ne permettent pas, a eux seuls, d'identifier le programme responsable.
- Les messages fournisseur arbitraires, logs, comptes retournes, URL et cles restent exclus du diagnostic. Un timeout est affiche comme un resultat de transmission inconnu.

Un accuse RPC n'est pas une confirmation d'execution. Meme en cas de refus, le bot conserve la signature durable et verifie son statut : il ne cree pas de deuxieme achat, ne rejoue pas les signatures expirees et ne deduit pas une vente confirmee d'une erreur reseau. Une reponse tardive conserve aussi l'etat le plus recent si la confirmation est arrivee avant elle.

**Budget45USD, reservations precedentes, achats1USD, plafonds de frais et depots, glissement20%, filtre simule5%, SL/TP, sortie60s, limites1s/3s, quotas et maxRetries2 sont conserves.** Aucun appel reseau, nouvelle signature ou ecriture avant envoi n'est ajoute sur le parcours critique. Le diagnostic est enregistre avec l'ecriture de retour d'envoi existante.

Pour les anciens achats expires, le bilan precise que le retour RPC original n'a pas ete conserve. Le correctif ne peut pas reconstruire retroactivement leur erreur. Les prochains refus seront plus faciles a diagnostiquer ; il ne garantit pas l'inclusion de tous les futurs achats.

## Installation

1. Consulter9 et attendre la cloture des positions/transactions en attente. Choisir5 pour l'arret propre, puis0.
2. Extraire l'archive dans le dossier habituel wallet-alerts en remplacant les fichiers proposes. Conserver les configurations, cles et bases.
3. Relancer WALLET_RADAR.cmd, puis **8**. Verifier **2026.11.18.2** au demarrage.
4. Apres un prochain achat ou refus d'envoi, consulter9 et partager les lignes **Envoi buy** ou **Envoi sell**. Ces lignes excluent les secrets.

## Validation

**428 tests Python passent**, dont l'hote natif PowerShell ; menu Windows et lanceur testes avec actions externes simulees. Controles specifiques : engagement confirmed explicite, verification active, quota/priorite/echeance/reessais conserves, code preflight6001/BlockhashNotFound, erreurs inconnues/timeout sans secret, maintien de la signature et du budget, un seul envoi, confirmation rapide non ecrasee et bilan en lecture seule compatible avec l'historique.

Aucun ordre ou signature reelle depuis le cloud. Activation sur VPS et effet sur les prochains envois encore a verifier.
