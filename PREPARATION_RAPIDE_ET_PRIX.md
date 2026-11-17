# Preparation rapide et controle du prix — 2026.11.17.5

La comparaison des deux achats du17novembre montre deux secondes d'ecart entre les heures de bloc Solscan, et un prix moyen SOL/token du bot26,47% plus eleve. Le bot a recu15,69% de tokens en moins que le montant cote dans son instruction Jupiter. Les tailles d'achat et leur impact sur le prix interviennent aussi dans la comparaison ; le retard seul n'explique pas toute la difference.

## Ce qui change

- La construction Jupiter commence des que la cotation arrive, sans attendre les autres lectures RPC.
- Le solde SOL et les comptes token utiles sont lus ensemble par `getMultipleAccounts`, pendant la construction Jupiter. Les comptes possibles sont derives des mints de la route, avec les deux programmes de tokens. La liste reste bornee a19adresses maximum. Une requete RPC d'achat est retiree, et les comptes sont lus avant la fin de construction.
- Les frais du message et la simulation restent verifies par RPC avant signature. La simulation demande un contexte au moins aussi recent que le snapshot des comptes. Les quotas RPC3/s et Jupiter1/s, les plafonds, la priorite d'envoi et l'echeance de3s restent appliques.
- La simulation retourne aussi le compte token de destination. Le moteur verifie son programme SPL/Token2022, son mint, son proprietaire, son format et son etat initialise, puis calcule la reception prevue.
- **Si cette reception est inferieure a95% des tokens cotes, aucun achat n'est signe.** Par exemple, pour100000tokens cotes, la simulation doit en prevoir au moins95000. Le minimum est arrondi vers le haut. Une position deja apparue dans les comptes bloque aussi l'achat.
- Avant signature, la cotation doit avoir au plus2s et le controle de simulation au plus0,5s. Le bilan9 conserve les quantites brutes cotees/simulees, le minimum, la limite et l'age de la cotation a la signature.

Ce filtre concerne l'achat. Le **glissement sur la transaction reste20%**, en achat et vente. Une execution peut encore etre moins favorable si le prix bouge apres la simulation. Le filtre peut refuser davantage d'achats ; il ne garantit pas une reception finale a95% de la cotation. Aucune cotation reseau supplementaire n'est ajoutee juste avant signature.

Achats1USD, budget cumule15USD, reserve permanente2,40USD par achat signe, SL-80%, TP+75%, une seule position, protection contre les doublons et signatures durables avant envoi sont conserves. Un achat refuse avant signature ne reserve pas d'enveloppe.

## Installation

1. Consulter **9**. Attendre que toutes les positions soient `closed` ou que les transactions en attente aient un resultat confirme. Si une position est encore ouverte, garder son suivi actif ; utiliser12 pour le diagnostic et13 uniquement apres une vente manuelle verifiable.
2. Lorsqu'il n'y a plus de position a suivre, choisir **5** pour arreter proprement le bot, puis **0** pour quitter le menu.
3. Extraire l'archive dans le dossier habituel `wallet-alerts`, en remplacant les fichiers proposes. Garder les configurations, cles et bases existantes.
4. Ouvrir `WALLET_RADAR.cmd`, choisir **8**, puis verifier la version **2026.11.17.5** dans la console et la ligne `Filtre achat`.
5. Apres le prochain achat ou refus, consulter9. Comparer `decision_envoi_HTTP_ms`, `preparation_restante_ms` et les phases `comptes_anticipes`, `construction_Jupiter`, `simulation`. Le budget et les signatures persistent ; l'echantillon des conditions repart au demarrage.

Copier les fichiers ne recharge pas le processus deja actif : le redemarrage8 est necessaire pour activer ce correctif.

## Validation et limites de la mesure

**393 tests Python passent**, y compris les tests de l'hote natif avec PowerShell portable. Menu Windows et lanceur valides avec SSH/SCP simules. Cas ajoutes : parallellisme sans attente du solde de tokens, reduction des appels, refus d'une reception simulee trop basse, seuil95% et arrondi, comptes incomplets, apparition de position, controle perime/absent, formats/proprietaires/mints invalides et maintien des plafonds frais/depot/debit et de la simulation obligatoire.

Banc comparatif hors reseau, bloqueur RPC3/s et Jupiter1/s, latence HTTP simulee50ms, blockhash deja precharge :

| Mesure de ce banc | 17.4 | 17.5 |
|---|---:|---:|
| Preparation non signee | 1780ms | 1439ms |
| Premier creneau d'envoi simule depuis le debut | 2066ms | 1722ms |
| Lectures RPC d'achat | 5 | 4 |

Le gain d'environ340ms appartient a ce scenario controle, pas a une mesure sur le VPS. Aucune signature ou transaction reelle n'est creee/envoyee par ce banc. Les quotas Jupiter, reseau et charge fournisseur restent presents ; les resultats du prochain essai reel permettront de mesurer le gain effectif.
