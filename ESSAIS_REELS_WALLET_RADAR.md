# Wallet Radar 2026.11.16.8 — premier essai reel

En cas d'echec de verification, le **menu 10** controle les acces sans ressaisie ni ordre. Voir [DEPANNAGE_ACCES_REELS.md](DEPANNAGE_ACCES_REELS.md). Le menu 7 conserve desormais les cles valides deja enregistrees.

Ce mode ajoute des achats et ventes reels **uniquement via le menu 8**. Le menu 3 reste une simulation. La configuration de la signature (menu 7) et son controle ne signent ni n'envoient de transaction.

## Parametres de cet essai

- Wallet surveille : BBIG BOSS, `BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp`.
- Wallet d'execution dedie : `E23vfW9oA9CnANP5wzimpo3DM3WQtMuxzUP4oxFp97Jq`.
- Achat : equivalent indicatif de **1 USD de SOL engage dans le swap**, hors frais reseau et depots de comptes. La quantite de tokens effectivement recue depend du cours, des frais du DEX et du glissement.
- Budget cumule **15 USD**, sans recycler les ventes/gains. Les conversions USD utilisent les cours SOL/USD recents observes ; ce n'est pas un compte bancaire en dollars.
- SL **-80 %**, TP **+75 %**, vente totale au premier seuil observe. Comparaison du produit SOL estime pour toute la position, converti en USD, avec le montant USD de notre entree. Frais reseau exclus du calcul du rendement. Le prix BBIG BOSS n'est pas notre prix d'entree.
- Glissement maximum **20 %**, achat et vente. Le SL est un seuil de declenchement, pas un prix garanti : la vente peut executer plus bas avec le glissement.
- Plafond reseau/priorite **0,10 USD par transaction**, sans Jito. Estimation de priorite en arriere-plan toutes les 10 s, perimee apres 30 s, puis frais verifies sur le message avant signature. Pas de depense automatique de tout le plafond.
- Depot de comptes : maximum estime **0,50 USD par cote**, dans le budget. Ce depot peut etre recuperable ; aucun remboursement n'est recycle pour augmenter le nombre de tests.
- Une seule position ouverte ou incertaine a la fois. Trois tentatives de vente maximum si les precedentes ont echoue ou expire ; jamais plusieurs signatures de vente simultanees.

L'enveloppe est volontairement prudente : **2,40 USD reserves definitivement par achat signe**, couvrant le dollar achete, les plafonds d'achat et de sortie et jusqu'a trois frais de vente. Cela permet **au maximum six achats signes**, plutot que quinze achats qui laisseraient les frais hors budget. Une transaction echouee ou expiree garde son enveloppe ; la somme reservee n'est pas une mesure des frais reellement payes. Le journal ne se reinitialise pas au redemarrage. Ne pas le supprimer pour remettre le compteur a zero pendant cet essai.

## Chemin d'execution et delais

Les conditions BBIG BOSS restent celles de la simulation : exclure bwam ; pas de sortie totale pendant la premiere seconde observee ; au moins 400 USD d'achats observes hors transaction initiale, avant trois secondes. Vente totale de BBIG BOSS apres une seconde : ne change pas la decision convenue.

La cotation et la construction **non signee** peuvent commencer au signal initial. La signature n'arrive qu'apres validation des conditions. Si le dossier n'est pas pret ou que l'appel d'envoi ne peut pas commencer avant T0 + 3 s, achat ignore. La confirmation peut naturellement arriver apres cette limite. Pas de rattrapage d'ordres abandonnes ou de replay d'anciens signaux.

Cette premiere version utilise les **routes directes Jupiter exact-in v1** seulement. Certaines courbes recentes, certains tokens cotes en une autre devise ou certaines nouvelles variantes Jupiter peuvent ne pas avoir de route exploitable : ils seront ignores, pas forces. Les simulations de volume sont plus larges que la couverture d'execution. La construction et les verifications RPC peuvent consommer toute la fenetre ; la faible latence d'execution devra etre mesuree sur le VPS, elle n'est pas garantie par les tests hors reseau.

Le programme construit son propre message a partir d'instructions controlees : route Jupiter connue, quantites/20 % lies a la cotation, comptes associes du wallet, wrapping SOL, budget de calcul fixe. Autres programmes/signataires/destinations refuses. Validation des frais, des depots et simulation avant signature. Il ne signe pas aveuglement une transaction encodee retournee par une API. Les extensions de routes inconnues restent refusees.

Une transaction signee est enregistree avant tout envoi. Une reponse reseau inconnue conserve la meme signature et bloque un second achat. Au redemarrage, suivi du statut et reprise de la position ; aucun nouvel achat du meme token. Une quantite modifiee manuellement ou un resultat incoherent bloque les nouveaux achats et demande une intervention. Les depenses d'autres applications utilisant ce wallet ne sont pas controlees par ce programme : conserver le wallet dedie aux tests.

Le suivi SL/TP interroge les cotations, avec attente de deux secondes entre tours plus le temps des requetes. Il reste actif apres les 30 decisions de conditions. **Ce ne sont pas des ordres limites deposes sur la blockchain : une coupure/fermeture du bot, une indisponibilite de cotation ou une absence de liquidite peut empecher la sortie.** Une notification precise une sortie indisponible. Garder le processus et la connexion SSH actifs pendant l'essai.

## Installation et configuration locale

1. Depuis l'ancien menu, **5**, attendre l'arret, puis **0**. Extraire le ZIP dans `C:\Users\Enzo\Downloads\wallet-alerts`, en acceptant les remplacements. Garder les configurations, journaux, extension et autres scripts existants.
2. Obtenir une cle API Jupiter gratuite sur [portal.jup.ag](https://portal.jup.ag/). Elle sert a la cotation/construction ; ce n'est ni une cle de wallet ni une cle GMGN. Si les conditions de l'offre changent, ne pas souscrire un forfait payant automatiquement.
3. Lancer `WALLET_RADAR.cmd`, choisir **7**. Les dependances sont installees sur le VPS, puis les saisies masquees se font dans la session SSH : cle privee du wallet dedie (base58 ou tableau JSON), puis cle Jupiter. **Ne jamais les envoyer dans une conversation, capture, argument de commande ou fichier a televerser.** L'adresse derivee de la cle doit correspondre exactement au wallet ci-dessus.
4. Le controle verifie le wallet, le RPC, une cotation et la construction d'instructions SOL/USDC **sans signature ni envoi**. Ne pas lancer 8 si cette verification echoue. Une reussite ne prouve pas qu'un futur token aura une route.
5. Alimenter le wallet dedie en SOL pour l'essai, dans l'enveloppe souhaitee de 15 USD. Les retraits/depots manuels ne remettent pas le compteur du bot a zero. Une preparation d'achat requiert assez de SOL pour toute son enveloppe reservee.
6. Chrome et l'extension ouverts, choisir **8**. La banniere doit afficher **2026.11.16.8 — ESSAI REEL**. Ce choix peut effectivement acheter/vendre avec les fonds du wallet.
7. **9** dans un second menu affiche les transactions et les delais. **5** arrete le processus, donc suspend aussi les sorties : verifier la position avant de fermer. Apres un arret, le menu 8 reprend le suivi des transactions/positions persistantes.

Fichiers prives sur le VPS : `/home/bot/wallet-alerts/private_wallet_radar/` (0700), `wallet.json` et `jupiter_api_key.txt` (0600). Exclus de Git et des archives. Ils ne sont jamais transmis au PC par le menu. La cle du wallet reste accessible au processus local qui doit signer ; l'acces au compte VPS doit rester prive.

Journal d'execution : `data/wallet_alerts_ws/live_trades.sqlite3`, permissions 0600, distinct du journal de simulation. Contient etat, signatures, transactions signees, enveloppes et notifications ; aucun secret de signature. Il ne faut pas le partager publiquement ni le supprimer pendant l'essai.

## Mesures et verification

Le bilan 9 distingue detection → decision, preparation (qui peut chevaucher l'attente des conditions), decision → debut de la requete HTTP d'envoi, puis envoi → confirmation observee. Cette derniere inclut le polling et la lecture des metadonnees : elle n'est pas l'horodatage exact d'execution on-chain. Les durees ne s'additionnent pas toutes. Une reponse reseau perdue ou un arret peut laisser certaines mesures inconnues ; elles ne sont pas inventees.

Tests controles : budget persistant, signataire de test, routes/comptes/montants, simulation et frais, SL/TP, sorties echouees, delai limite, reponse reseau inconnue, confirmation plus rapide que la reponse d'envoi, prix maintenus apres la fin de la collecte. Les actions reseau des tests Windows sont simulees. Aucune transaction reelle n'a ete envoyee par ces tests ou depuis l'environnement cloud. L'installation, l'authentification et le premier ordre sur le VPS restent a verifier avec l'utilisateur.
