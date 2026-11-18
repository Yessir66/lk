# Ventes manuelles automatiques et retrait du filtre5% — 2026.11.18.3

## Filtre5% retire

Le bot ne refuse plus un achat parce que la simulation prevoit plus de5% de tokens en moins que la cotation. Le refus `Prix degrade : simulation sous95%` a ete supprime, ainsi que son controle avant signature.

La simulation reste obligatoire et la transaction conserve son glissement maximal **20%**. Le cours SOL, les frais, les depots, les comptes, la fraicheur des cotations/simulations et la limite3s restent controles. Une simulation peut donc encore refuser une transaction invalide ou un glissement superieur a la limite du programme. Les anciens bilans peuvent toujours montrer le filtre utilise lors d'anciens achats ; les nouveaux enregistrements indiquent simplement les quantites cotees et simulees.

## Quand tu vends toi-meme

Si tu vends100% du token depuis ton wallet, le bot detecte le changement de solde et verifie la vente automatiquement. Apres preuve d'une vente totale finalisee et nouvelle verification du solde nul, il passe le suivi a **closed** et libere le blocage de cette position. Tu n'as normalement plus besoin de faire12puis13 pour ces ventes.

Le bot reessaie au plus toutes les15s apres la fin du controle precedent. Il faut aussi attendre la finalisation de la vente et le temps des lectures RPC ; la reprise n'est pas instantanee. Le processus doit rester actif.

La preuve est la meme que celle deja utilisee au menu13 : achat initial et quantite verifies, vente reussie signee par le wallet apres l'achat, total exact de tokens vendu contreSOL, puis solde nul relu. Recherche bornee20signatures recentes et8transactions candidates. Une vente partielle, un transfert, une vente non finalisee, une autre contrepartie ou une preuve hors de cette recherche ne cloture pas automatiquement la position. Le menu13 reste disponible pour afficher le diagnostic si le rapprochement ne peut pas aboutir.

Les signatures et le budget reserve restent comptabilises. Une cloture ne remet pas le compteur a zero et ne permet pas de racheter le meme token deja traite. Les prochains signaux restent soumis au budget et aux autres regles. Le rapprochement ne prepare, ne signe et n'envoie aucun ordre. Une verification concurrente ne peut pas ecraser une cloture ou un changement plus recent du journal.

Notifications : `VENTE_MANUELLE_EN_VERIFICATION`, puis **VENTE_CONFIRMEE : Vente manuelle finalisee verifiee automatiquement ; suivi cloture** lorsque la preuve est obtenue. Une ancienne position deja en etatmanual est aussi examinee apres le redemarrage.

## Installation

1. Consulter9 et attendre la cloture des positions/transactions en attente. Choisir5 pour l'arret propre, puis0.
2. Extraire cette archive dans le dossier habituel wallet-alerts, en remplacant les fichiers proposes. Garder configurations, cles et bases.
3. Relancer WALLET_RADAR.cmd puis **8**. Verifier **2026.11.18.3** et les lignes `Filtre de reception5% retire` / `Ventes manuelles : rapprochement automatique`.

Budget45USD, achats1USD, SL-80%, TP+75%, sortie60s et enveloppes anterieures sont conserves. Le compteur des conditions recommence ; le budget ne se reinitialise pas.

## Validation

**433 tests Python passent**, y compris les tests PowerShell natifs ; menu/lanceur Windows valides avec actions externes simulees. Cas verifies : rapprochement depuisopen/manual sans menu, budget et signatures conserves, admission d'un nouveau mint sans rachat du precedent, attente de finalisation et cadence, transfert/partiel/erreur reseau maintenus bloques, mise a jour concurrente non ecrasee, zero signature/envoi pendant rapprochement, refus historique du menu13 conserves, filtre5% retire jusque dans la signature, glissement20% et simulation obligatoire preserves. Aucune transaction reelle depuis le cloud ; activation et premiere detection automatique sur VPS encore a verifier.
