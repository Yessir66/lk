# Wallet Radar : essai gratuit sur 30 tokens, sans achat reel

Version **2026.11.15.3**. Ce mode prepare et teste la logique de decision. Il ne contient aucun chemin de signature ou d'envoi d'ordre. Aucun wallet de trading ni nouvelle cle privee n'est necessaire. Les 25 USD sont un montant theorique, pas une depense.

## Demarrer l'essai

1. Arrete la surveillance actuelle avec **Ctrl+C** et ferme sa fenetre.
2. Extrais le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts`, en acceptant les remplacements. Conserve la configuration, les donnees et l'extension GMGN.
3. Ouvre Chrome avec l'extension et l'onglet GMGN habituel.
4. Double-clique sur **WALLET_RADAR.cmd**, puis choisis **3 - Tester les conditions BBIG BOSS en SIMULATION**.
5. Le menu transfere automatiquement les trois scripts necessaires au VPS et lance le mode experimental. Le mot de passe VPS peut etre demande deux fois. BBIG BOSS doit figurer dans les wallets surveilles ; le programme le verifie.

Garde le PC et le lanceur ouverts. Le mode 1 reste la surveillance habituelle, sans cet essai. Ne lance pas deux surveillances avec le meme journal.

## Les regles simulees

- T0 est la reception processed de l'achat de BBIG BOSS, identifie par un evenement natif Pump.fun. Si la surveillance principale a recu la notification correspondante plus tot, ce premier instant est conserve lors de sa verification : la confirmation ne redemarre pas le chronometre.
- Aucun achat theorique avant **T0 + 1 seconde**.
- Si BBIG BOSS liquide toute sa position pendant cette premiere seconde observee, le signal est annule. Le calcul utilise le solde total apres sa transaction initiale confirmee, pas seulement la quantite de cette transaction. Une vente partielle n'annule pas.
- Le createur declare par Pump.fun est compare a **bwam** : `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`. Egalite : token exclu. Cela identifie une adresse declaree par le protocole, pas les personnes ou autres adresses derriere elle ; un historique de changements de createur n'est pas reconstruit.
- Achat initial de BBIG BOSS exclu du volume (toute sa transaction initiale est exclue). Ses achats suivants peuvent contribuer aux 400 USD, puisque seule l'exclusion de l'achat initial a ete demandee.
- Apres la premiere seconde, un achat **THEORIQUE de 25 USD de tokens** est note des que 400 USD d'achats suivants sont observes, dans la limite de **T0 + 3 secondes**.
- Une liquidation totale recue apres la premiere seconde n'annule plus le signal, conformement a la demande de l'utilisateur.
- Sans seuil observe dans la fenetre, abandon. Si les preuves, le cours ou les formats manquent, resultat **DONNEES_INSUFFISANTES**, jamais un feu vert presume.

La preparation d'un achat reel, les frais maximaux et le service d'execution ne sont pas implementes. Les frais seraient en supplement des 25 USD ; leur plafond reste a decider.

## Comment le volume est obtenu gratuitement

Un deuxieme WebSocket standard Helius suit BBIG BOSS en processed. Le programme decode les `TradeEvent` contenus dans les logs emis par le vrai programme Pump.fun, en verifiant la pile d'execution. Il souscrit ensuite temporairement au mint du token et lit les achats natifs recus, sans `getTransaction` pour chaque acheteur.

L'abonnement temporaire est retire apres la decision ; maximum huit signaux actifs simultanement. La surveillance normale conserve son plafond RPC 5/s et fournit la verification de l'achat initial. Une lecture supplementaire des comptes de tokens du wallet controle qu'aucune position non nulle n'est omise par les comptes de cette transaction ; en cas de compte non couvert, la decision reste inexploitables plutot que de deviner sa position initiale. Ces lectures doivent aboutir avant l'echeance de trois secondes. Il n'y a pas de lecture RPC par achat des autres wallets.

Le schema provient de l'[IDL officiel Pump.fun](https://github.com/pump-fun/pump-public-docs/blob/639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22/idl/pump.json). Les donnees d'un autre programme ne sont pas interpretees comme des evenements Pump.fun simplement parce que leur contenu ressemble a un evenement.

## Limites a mesurer pendant cet essai

- **Premiere couverture : Pump.fun**, pas tous les DEX. PumpSwap et les autres formats non reconnus donnent NON_SUPPORTE ou DONNEES_INSUFFISANTES. Une extension inconnue du format Pump.fun est refusee pour les decisions, meme si son prefixe peut etre lu.
- Le nouvel abonnement au mint arrive apres T0. L'intervalle jusqu'a son accuse de reception est enregistre sous `subscription_gap_ms`. Les achats manques dans cet intervalle ne sont pas inventes ni rattrapes dans la fenetre courte.
- Le volume est celui des achats **observes a reception**, une borne observee avec des omissions possibles. Des messages peuvent arriver dans un autre ordre que l'execution ; l'ordre exact des transactions partageant un slot n'est pas reconstruit. Le flux processed est encore provisoire. L'essai ne prouve donc ni un volume exhaustif on-chain, ni une execution possible avant un autre wallet.
- Conversion SOL/PUMP/TTWO USD : cours indicatifs publics CoinGecko horodates, age maximum 90 secondes, rafraichis ensemble toutes les 15 secondes. PUMP et TTWO ont 6 decimales. Depuis 2026.11.16.3, le DOGE Solana identifie (8 decimales) utilise une bougie minute USD GeckoTerminal, age conservateur depuis le debut de la minute, maximum 90 s, interrogee toutes les 30 s. Un achat en attente de cours peut etre valorise dans la fenetre restante, jamais apres T0 + 3 secondes et sans recommencer le chronometre. Pas de cours frais exploitable : decision potentiellement impossible. USDC reste nominalement 1 USD ; les autres devises ne sont pas converties. Voir `COTATIONS_DOGE_TTWO.md`.
- Les temps sont locaux et monotones, pas un horodatage exact de Solana au dixieme de seconde. Boucle de controle d'environ 20 ms, sans garantie absolue de latence.
- Une seule decision par token pendant cet essai. Un nouveau renforcement de BBIG BOSS ne remet pas les compteurs a zero pour un token deja en cours ou termine.
- Une coupure de flux abandonne les decisions actives. Les signaux trouves uniquement par rattrapage ne sont pas simules retroactivement.

Le but est de mesurer la **couverture et les delais reels** du forfait gratuit avant de choisir une infrastructure payante. Ce n'est pas encore une validation de rentabilite. Les limites et credits du fournisseur restent ceux du compte existant.

## Notifications

Dans la console : lignes jaunes `[SIMULATION]`, avec CA et raison. Sous Windows, le lanceur demande aussi des notifications via l'icone de notification ; leur affichage depend des reglages Windows et de l'assistant de concentration. La console et le journal restent disponibles si les bulles sont masquees. Aucun Telegram n'est active.

Codes : `SUIVI`, `DEV_EXCLU`, `SORTIE_TOTALE_1S`, `ATTENTE_VOLUME`, `ACHAT_SIMULE`, `VOLUME_INSUFFISANT`, `DONNEES_INSUFFISANTES`, `NON_SUPPORTE`. ACHAT_SIMULE signifie uniquement que les conditions observees de cet essai sont satisfaites : aucun ordre envoye, aucun prix d'execution ni remplissage simule.

## Exactement 30 tokens pour le premier bilan

L'essai echantillonne les **30 premiers tokens distincts detectes** : les exclusions, formats non supportes et donnees manquantes comptent aussi. Ce sont des resultats de couverture, pas 30 tokens necessairement exploitables.

Apres les decisions du trentieme token, la collecte experimentale s'arrete ; **la surveillance habituelle et GMGN continuent**. Un message l'indique dans la console. Redemarrer le mode 3 commence un nouvel essai, sans effacer les anciens journaux.

Les evenements publics sont dans `data/wallet_alerts_ws/paper_decisions.jsonl` sur le VPS, avec identifiant d'essai. Aucune cle n'y est ecrite.

Pour afficher le bilan, arrete le lanceur avec Ctrl+C, reouvre **WALLET_RADAR.cmd** puis choisis **4 - Afficher le bilan du dernier essai de 30 tokens**. Envoie une capture ou le texte de ce bilan. Il indique combien auraient satisfait les regles observees et combien sont annules, exclus ou inexploitables.

Equivalent manuel, en **SSH VPS** :

```bash
cd /home/bot/wallet-alerts
.venv/bin/python scripts/wallet_alerts_ws.py --paper-report
```

Le bilan lit seulement le journal et ne fait aucun appel reseau.
