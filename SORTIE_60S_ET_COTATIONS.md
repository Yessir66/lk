# Sortie apres une minute et nouvelles devises — 2026.11.17.6

## Vente automatique apres 60 secondes

En mode reel8, une position encore ouverte fait l'objet d'une demande de **vente totale apres60secondes**, meme si le SL n'est pas atteint. Le SL-80% et le TP+75% restent actifs : le premier declenchement enregistre commande la sortie.

Le minuteur commence a la **confirmation d'achat observee par le bot**. L'echeance absolue est conservee dans le journal avec la position. Un redemarrage ou une tentative de vente echouee ne donne pas une nouvelle minute. Pour une ancienne position ouverte, le bot utilise sa confirmation deja enregistree : si celle-ci date de plus de60s, la sortie devient exigible des la reprise.

Le suivi est verifie toutes les2s, en plus du temps des appels. A60s, le bot declenche la preparation puis l'envoi de la vente ; l'execution exacte a60s n'est pas garantie. Le prix, les frais, le solde, la simulation et la disponibilite de la route restent controles. Une erreur conserve la demande `TIMEOUT_60S` pour la prochaine verification. Une signature en attente est rapprochee avant toute nouvelle tentative. Trois tentatives maximum selon les regles existantes ; un solde modifie hors bot exige toujours une intervention manuelle.

Le bilan9 affiche le temps restant des positions ouvertes et le motif de sortie enregistre. Une vente confirmee indique `TIMEOUT_60S` quand le minuteur est le declencheur. Garder le processus actif pour le suivi des sorties.

## Devises ajoutees pour le volume observe

| Devise | Contrat Solana exact | Decimales | Source USD |
|---|---|---:|---|
| Microsoft xStock (MSFTx) | XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX | 8 | CoinGecko `microsoft-xstock` |
| Costco Backpack (COST) | CZEB3WNZuF2Yz1z2H81RcCk8T7fsw82KB33zqamASVsg | 6 | CoinGecko `costco-wholesale-backpack-securities` |
| GTAVI | ERiM8NiCanarGEnWMnURAoxTuTvUXZbtJsPTirnHpump | 6 | Bougie minute USD GeckoTerminal du pool9YbrZx4ytWsuhsbfM75r4RfPW25MGaos4CVcuXM6qBNY |

Contrats/decimales recoupes le17novembre2026 avec les API publiques JupiterTokens et GeckoTerminal. Les metadonnees de contrat CoinGecko confirment aussi les deux titres tokenises :

- [Microsoft xStock, contrat Solana](https://api.coingecko.com/api/v3/coins/solana/contract/XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX)
- [Costco Backpack, contrat Solana](https://api.coingecko.com/api/v3/coins/solana/contract/CZEB3WNZuF2Yz1z2H81RcCk8T7fsw82KB33zqamASVsg)
- [GTAVI, metadonnees du token](https://api.geckoterminal.com/api/v2/networks/solana/tokens/ERiM8NiCanarGEnWMnURAoxTuTvUXZbtJsPTirnHpump)

MSFTx et COST rejoignent la requete CoinGecko existante : pas d'appel CoinGecko supplementaire. GTAVI ajoute une lecture de bougie en arriere-plan dans la boucle existante DOGE. Les requetes de prix ne sont pas ajoutees a la preparation d'ordre. Les prix doivent etre positifs, finis, dates de moins de90s et sans date future. Pour GTAVI, le token exact de la reponse est verifie et l'age part du debut de la minute : c'est une estimation pour valoriser le volume, pas un cours executable ni le prix exact de chaque trade.

Si un prix manque ou perime, le bot affiche `COURS_MSFTX_USD_ABSENT_OU_PERIME`, `COURS_COST_USD_ABSENT_OU_PERIME` ou `COURS_GTAVI_USD_ABSENT_OU_PERIME`. Il ne prolonge pas la fenetre3s, ne recompte pas un achat et ne relance pas une ancienne decision. Les autres devises inconnues restent ignorees. Le cas distinct `PumpSwap / NON_SUPPORTE` n'est pas corrige par cet ajout de prix.

## Installation et verification

1. Consulter9 et attendre la cloture des positions ou la resolution des transactions en attente. Garder le suivi actif en attendant ; diagnostic12 et rapprochement13 restent disponibles.
2. Choisir5 pour l'arret propre, puis0. Extraire l'archive dans le dossier habituel `wallet-alerts`, en remplacant les fichiers proposes. Conserver les configurations, cles et bases.
3. Relancer `WALLET_RADAR.cmd`, puis **8**. Verifier **2026.11.17.6** et la ligne `Sortie totale : SL, TP ou 60 s`.
4. Apres un achat confirme, consulter9 pour le temps restant. Le prochain diagnostic6 nommera MSFTX/COST/GTAVI pour ces contrats.

Achats1USD, budget15USD sans recyclage, enveloppes deja reservees, glissement20%, filtre de reception simulee5%, historique et signatures sont conserves. Le compteur des conditions recommence au demarrage ; le budget ne se reinitialise pas.

## Validation

**402 tests Python passent**, dont les tests de l'hote natif avec PowerShell. Menu Windows et lanceur verifies avec SSH/SCP simules. Nouveaux cas : minuterie sans sortie prematuree, echeance franchie pendant une cotation, vente totale, redemarrage, erreur de cotation, vente echouee et absence de deuxieme ordre pending ; conversions des trois devises, seuil400USD, decimales, prix perimes/futurs/non finis, adresse de bougie incorrecte, revalorisation sans double compte et respect des3s.

Sources publiques de prix repondant pendant la preparation ; aucun ordre ou signature reelle depuis le cloud. L'installation, la disponibilite des prix et la premiere sortie chronometree sur le VPS restent a verifier apres deploiement.
