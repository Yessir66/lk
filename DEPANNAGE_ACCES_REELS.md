# Wallet Radar 2026.11.16.5 — controle des acces sans ordre

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
