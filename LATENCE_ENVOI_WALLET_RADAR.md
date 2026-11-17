# Wallet Radar 2026.11.17.2 — validation et envoi

Premier achat reel montre par l'utilisateur : decision a1,011 s, puis envoi HTTP1,025 s apres decision, confirmation observee1,110 s apres envoi, preparation totale1,734 s. Position `open`, enveloppe2,40 USD reservee. La preparation chevauche l'attente des conditions ; les nombres ne prouvent pas quelle requete a consomme la seconde restante.

## Optimisations

- **Envoi prioritaire** dans le transport RPC reel : il passe devant les lectures encore en attente, y compris pendant une attente du quota. Une requete deja en vol finit normalement. Intervalle minimum1/3 s conserve, ainsi que pause60 s apres429. Pas d'augmentation de plafond ni de frais.
- **Blockhash precharge** en arriere-plan toutes les5 s. Un cache lu depuis au plus10 s peut remplacer l'appel de preparation. Cache absent/perime : lecture normale, avec verrou pour fusionner les demandes simultanees. Hash et hauteur valides obligatoires ; avant signature, age maximal15 s. Les verifications de frais du message et la simulation continuent a interroger le RPC.
- **Une ecriture synchrone dupliquee retiree avant HTTP**. La signature exacte et son enveloppe doivent deja etre enregistrees durablement ; sans cette preuve, envoi refuse. Seuls le timestamp de file et les nouvelles mesures sont ajoutes apres la reponse. Une reponse inconnue conserve le meme ordre pending et ne declenche aucun doublon.

Les parametres d'achat, la fenetre1/3 s, le seuil400 USD, le budget cumule15 USD, les1 USD par achat, le glissement20 %, les plafonds de frais/depots, la route controlee, la simulation et SL/TP ne changent pas. Le cache ne contient pas de transaction signee ni d'autorisation d'acheter ; la signature attend toujours la validation des conditions.

## Nouvelles mesures, option9

Pour chaque nouvel achat signe :

- File de decision, attente de preparation restante, controles/signature, journal avant envoi.
- File RPC et quota avant l'envoi HTTP ; temps de reponse HTTP affiche separement, **apres le debut d'envoi**.
- Phases de preparation : position, cotation, blockhash/cache, soldeSOL, constructionJupiter, tables d'adresses, frais, comptes, simulation.
- Cumuls de file/quota/reponse par fournisseur pour identifier l'attente dominante. Certaines phases sont paralleles : ne pas les additionner pour calculer une latence globale.

Le bilan conserve les mesures des anciennes lignes et ne les invente pas retroactivement. Il indique aussi le nombre d'achats ignores enregistres par la version17.2 et les cinq derniers motifs/details. Une preparation depassant l'echeance est abandonnee ; le delai de trois secondes n'est jamais prolonge pour obtenir une mesure ou forcer un achat.

Les profils contiennent uniquement des durees, categories de requetes et indicateurs de cache. Aucun corps fournisseur, URL privee ou cle n'est ajoute au journal. Le temps confirmation est toujours une observation locale incluant le polling, pas l'instant exact d'execution on-chain.

## Installation

1. Consulter **9** dans un second menu. Si une position est ouverte ou une signature en attente, conserver le suivi et faire la mise a jour apres sa cloture : arreter le bot suspend le SL/TP.
2. Sans position a suivre : **5**, attendre l'arret, puis **0**.
3. Extraire le ZIP dans `C:\Users\Enzo\Downloads\wallet-alerts`, accepter les remplacements, sans effacer fichiers prives ou journaux.
4. Relancer `WALLET_RADAR.cmd`, faire **10** (sans ordre), puis **8** apres reussite. La banniere doit afficher **2026.11.17.2**.
5. Pour comparer avec l'ancien achat, afficher **9** apres les prochains essais. Les anciennes signatures et le budget deja reserve restent conserves ; aucune nouvelle cle a saisir. La collecte des conditions recommence au redemarrage, pas le budget.

Validation : 377 tests Python, dont4 facultatifs Windows ignores faute de PowerShell, les373 autres passent. Nouveaux tests : priorite d'envoi sans contourner le quota, nettoyage d'un waiter expire, cache et expiration, fusion de lectures concurrentes, signature durable obligatoire sans ecriture dupliquee avant HTTP, bilan compatible/lecture seule. Fraicheur du blockhash avant signature verifiee. Aucun achat reel envoye depuis le cloud ; gain sur le VPS encore a mesurer, aucune promesse de passer sous un delai particulier.
