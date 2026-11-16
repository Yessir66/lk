# Wallet Radar 2026.11.16.6 — controle des acces sans ordre

Derniere capture : RPC/getBalance **OK**, puis Jupiter/cotation **HTTP 400**, avant toute construction/signature/envoi. Le refus concerne la demande de cotation ; ce code ne prouve pas a lui seul une mauvaise cle API. La meme demande GET publique fonctionne depuis le cloud : le refus du VPS n'est pas encore reproduit ici.

La .6 precise `restrictIntermediateTokens=true` en plus des routes directes et du format legacy, dans un constructeur de requete commun au controle et aux cotations reelles. Les routes ne sont pas elargies et le glissement reste 20 %. Le montant de controle reste 0,001 SOL, sans ordre ; les achats reels restent 1 USD.

Les erreurs Jupiter affichent maintenant un **code metier connu** (`COULD_NOT_FIND_ANY_ROUTE`, `AMOUNT_TOO_SMALL`, etc.) avec un libelle fixe, ou la classification d'un message de parametre reconnu. Les textes bruts, codes inconnus, URL, cles et corps de reponse ne sont toujours pas affiches. Si le refus persiste au menu 10, transmettre cette ligne precise. Aucune ressaisie de cle, aucun nouvel approvisionnement ou changement de wallet n'est demande par ce correctif.

La capture du VPS confirme « Signature configuree pour E23vf... Aucun ordre envoye », puis un echec fournisseur generique. La cle wallet a donc passe le controle d'adresse, mais l'acces RPC/Jupiter n'est pas encore valide. Le message de la .4 ne permet pas de savoir quel appel echoue. Ne pas supposer que la cle wallet est incorrecte ou que le wallet doit etre recree.

La .5 ajoute le **menu 10**, qui conserve les cles existantes et execute seulement `wallet_radar_live.py --check` :

1. `CONTROLE 1/3 : RPC/getBalance` — lecture du solde SOL via le RPC configure.
2. `CONTROLE 2/3 : Jupiter/cotation` — cotation SOL/USDC de controle.
3. `CONTROLE 3/3 : Jupiter/construction non signee` — construction et verification des instructions, sans signature.

En cas d'echec, le diagnostic affiche le service et le statut HTTP/code RPC, ou un libelle reseau (DNS, TLS, delai). Aucun corps de reponse, URL privee, entete d'authentification ou cle n'est affiche. Un HTTP 401/403 indique un refus d'acces ; le code seul ne prouve pas si la cause est une cle, une permission, un filtrage ou une autre restriction. HTTP/RPC 429 declenche une pause de 60 s, sans changement automatique de fournisseur.

## Utilisation

- Quitter l'ancien menu avec **0**, extraire le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`, accepter les remplacements, relancer `WALLET_RADAR.cmd`.
- Choisir **10**. Les scripts sont transferes, puis le controle utilise les cles deja presentes sur le VPS. Seul le mot de passe SSH habituel peut etre demande.
- Envoyer seulement le texte de l'etape et du code d'erreur (ou la capture de ce controle). Ne pas ouvrir les fichiers prives pour les copier dans une conversation.
- Ce controle peut fonctionner pendant la collecte et ne modifie pas les journaux d'execution, les enveloppes reservees ni les positions. Il ne cree pas de nouvelle transaction signee.

Le menu **7** utilise maintenant `--setup-if-missing` : chaque cle existante valide est conservee, sans ressaisie et sans reecriture du fichier. Le mode manuel `--setup` reste disponible pour remplacer explicitement la configuration locale si necessaire ; le menu 10 n'utilise jamais ce mode.

Cette livraison ameliore le diagnostic. La cause du refus sur le VPS n'est pas encore connue et aucun succes de connexion authentifiee ou d'ordre reel n'est affirme. Les regles d'achat, le budget, le SL/TP et les limites de frais de la .4 restent les memes.
