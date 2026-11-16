# Wallet Radar 2026.11.16.9 — controle des acces sans ordre

A la demande explicite de l'utilisateur, **la cle API Jupiter est maintenant visible pendant sa saisie locale**, au menu11 et lors de la premiere configuration au menu7. La cle privee du wallet reste saisie avec getpass (masquee). Les cles deja enregistrees ne sont pas affichees et ne sont pas ajoutees aux journaux. Le remplacement API conserve le wallet. Eviter de partager une capture ou un enregistrement du terminal contenant cette saisie visible ; partager seulement le resultat du controle.

Apres un nouveau HTTP400 non JSON depuis le VPS, le menu10 ajoute automatiquement une comparaison **en lecture seule** : meme cotation fixe SOL/USDC sans cle API, puis avec une cle volontairement factice. Deux GET uniquement, sans acces au wallet de signature, sans construction, sans envoi, sans changement automatique du mode d'authentification des ordres. Les cotations reelles gardent la cle enregistree ; aucun contournement d'authentification n'est active.

Le rapport signale aussi, sans aucune valeur : cle contenant URL/guillemets/caracteres non ASCII, et presence ou absence d'un proxy declare dans l'environnement. Le proxy n'est pas desactive ou modifie. Il affiche seulement les statuts des requetes et confirme si une reponse200 est bien une cotation coherente.

- Sans cle200 valide et cle factice401 : le service repond et rejette normalement une cle fausse. Le refus observe avec la cle enregistree depend de cette requete authentifiee ; verifier le format/type/permissions de la cle. Cela ne prouve pas a lui seul qu'elle doit etre regeneree.
- Sans cle400 egalement : la cle n'est pas necessaire pour reproduire le refus. Une nouvelle cle n'est pas justifiee par ce seul resultat.
- Sans cle200 et cle factice400 : l'en-tete d'authentification influence le refus meme avec une fausse cle ; verifier l'acces ou l'intermediaire avant de conclure.
- Autres statuts : resultat non concluant, conserver les cles.

Le **menu11** remplace uniquement la cle API Jupiter par saisie masquee puis refait le controle. Il ne lit ni ne reecrit `wallet.json`. Il peut servir a recopier correctement la cle actuelle, sans forcement en creer une nouvelle. Une cle contenant URL, guillemets, espaces, caracteres non ASCII ou format excessif est refusee avant enregistrement. Ne pas utiliser ce menu pour la cle privee du wallet.

Installer comme les correctifs precedents, puis choisir **10**, pas 7 ni8. Envoyer uniquement les lignes de comparaison. Le budget, les positions et toutes les cles existantes restent conserves au transfert des scripts.

La capture suivante conserve HTTP400 sans motif reconnu. La .7 utilise pour Jupiter **curl_cffi, deja installe et utilise par la surveillance**, a la place de urllib. Elle retire Content-Type des GET sans corps, garde Accept/application/json, la meme cle, le meme hote, la verification TLS, les quotas et les parametres de cotation. Les redirections sont refusees. Aucun nouvel ordre ou essai de signature dans le menu10.

Le controle affiche explicitement « client curl_cffi ». Si le refus persiste, il distingue reponse HTML, non JSON et motif JSON non reconnu ; il classe aussi les messages sous `message` (pas seulement `error`). Les erreurs de connexion curl ne sont pas confondues avec des statuts HTTP (par exemple errno6 = DNS, pas HTTP6). Aucun corps fournisseur n'est affiche.

Un essai public depuis le cloud avec cle deliberement factice a donne **401**, et sans cle **200**, pour la meme cotation. Cela ne prouve pas la validite de la cle de l'utilisateur ni le fonctionnement du VPS ; il n'y a toutefois pas encore de preuve qu'une regeneration de cle soit necessaire. **Conserver la cle actuelle et tester menu10 apres installation.** Un refus d'authentification explicite pourra ensuite etre traite sans modifier la cle wallet. Les tests du client ne lancent jamais d'ordre.

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
