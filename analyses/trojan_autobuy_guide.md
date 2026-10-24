# Bot Trojan Autobuy : copie des premiers achats du wallet 7BNaxx6K (5 $)

Script : `scripts/trojan_autobuy.py`. Il tourne sur ton PC. Il ne touche jamais à ta clé privée : il se
contente d'envoyer l'adresse du token (le CA), en texte, à ton bot Trojan depuis ton compte Telegram, et
l'Autobuy de Trojan achète.

## Les règles appliquées

Un CA n'est envoyé que si **toutes** ces conditions sont remplies :

| Condition | Comment le bot la vérifie |
|---|---|
| C'est son **premier achat** du token (on ignore ses rachats) | Au démarrage, le bot lit ses 2 dernières heures de transactions. Un token qu'il a déjà touché ou qu'il détient déjà est ignoré. |
| Token **créé 2 h ou moins** avant son achat | La date de création vient de l'API Jupiter. Sur 743 tokens de l'échantillon, elle a donné 100 % d'accord avec mon critère du backtest. |
| Token lancé sur pump.fun ou stonkfun | C'est l'univers sur lequel les règles ont été testées. |
| **MC à son prix d'achat ≥ 37 000 $** (45 000 $ jusqu'au 24/10) | Son prix d'exécution (ce qu'il a payé, en SOL ou dans un autre token, converti en $) × 1 milliard. |
| Pas plus de 50 % sous le plus haut du token | Bougies pump.fun. Si elles ne répondent pas, ce contrôle est sauté. |
| Signal envoyé moins de 15 s après son achat | Sinon le signal est trop tardif : il est noté dans le journal mais pas envoyé. |
| CA jamais envoyé auparavant, et moins de 60 signaux dans la journée | Garde-fous contre les doublons ou un emballement du bot. |

Sorties de la règle :
- moitié vendue à **x1,5**, puis le stop remonte au **prix d'entrée** ;
- le reste vendu à **x2** ;
- tout vendu **5 s après x1,8** ;
- tout vendu si **−30 % dans la première minute** après son achat ;
- tout vendu **5 min après son achat** si le prix est encore **entre −20 % et +20 %** ;
- tout vendu **20 min après son achat** si x2 n'est pas atteint ;
- stop de départ à **−50 %**.

## Qui fait quoi pour les ventes

- **Trojan** gère les ventes à prix fixe : TP +50 % (vendre 50 %), TP +100 % (vendre le reste), SL −50 %
  (tout vendre). Règle-les dans Trojan pour les achats automatiques.
- **Le bot** gère les sorties que Trojan ne sait pas faire seul :
  - −30 % dans la première minute ;
  - 5 s après x1,8 ;
  - le retour au prix d'entrée après x1,5 ;
  - le contrôle à 5 min (entre −20 % et +20 %) ;
  - la vente à 20 min.

  Il suit le prix toutes les 2 s. Au bon moment :
  - **sans la vente auto** (réglage par défaut), il t'envoie « VENDS TOUT … » dans tes **Messages
    enregistrés** Telegram, et tu reçois la notification sur ton téléphone ;
  - **avec `"vente_auto": true`** dans `trojan_config.json`, il vend lui-même en cliquant sur les boutons de
    Trojan, comme toi :
    1. « Sell » sous le message d'achat de Trojan qui contient le CA ;
    2. dans le panneau de vente, « Swap » et « 100 % » cochés (✅) ;
    3. « SELL », puis il attend la réponse de Trojan.

    Il ne clique que sur ces boutons, et seulement sous un message qui contient le CA de ce token. Il ne
    recolle jamais le CA, car avec l'Autobuy activé Trojan rachèterait. Si une étape échoue, il t'envoie
    « VENDS TOUT … » pour que tu vendes à la main.

  Avant d'activer la vente auto, teste-la une fois sur une vraie position :
  `python scripts\trojan_autobuy.py --test-vente <CA>` vend 100 % de ce token par ce même parcours.

Ces sorties comptent. Sur les 88 tokens éligibles du 19 au 21/10, avec une entrée au prix de copie (bougies d'une
minute, donc la première minute et les « 5 s après x1,8 » sont approximés) :

| Sorties appliquées | Résultat par trade | 19/10 | 20/10 | 21/10 |
|---|---|---|---|---|
| Seulement le TP/SL de Trojan | −10,3 % | | | |
| Règle de départ (TP/SL + prix d'entrée après x1,5 + 20 min) | −5,4 % | −15,5 % | +0,3 % | −5,4 % |
| + −30 % dans la première minute | −2,2 % | −9,9 % | +2,9 % | −3,1 % |
| + contrôle à 5 min (entre −20 % et +20 %) | −5,0 % | −12,1 % | −1,2 % | −4,9 % |
| + tout vendre à x1,8 | −3,4 % à −4,4 % | | | |
| **Les 3 ajouts ensemble (règle actuelle du bot)** | **−0,6 % à +0,3 %** | −6,5 % à −5,5 % | +0,6 % à +1,2 % | +1,8 % à +2,9 % |

La fourchette va de « vendu à x1,8 » à « vendu au prix de clôture de la minute ». Les trois ajouts ont été
proposés par l'utilisateur, pas ajustés sur ces données ; ils améliorent chacun des trois jours.

## Ce qu'il faut attendre

- **Nombre de signaux :** environ 40 par jour avec le seuil de 37 k$ (29, 50 et 45 du 19 au 21/10). Les 36 tokens
  entre 37 et 45 k$ donnaient +0,4 % par trade avec les sorties actuelles, comme ceux au-dessus de 45 k$
  (+0,3 %) : plus de trades, même tendance.
- **Résultat attendu :** autour de 0 % par trade d'après le backtest, avant les frais fixes. C'est un test pour
  mesurer la tendance réelle, pas une stratégie gagnante prouvée.
- **Frais fixes :** à 5 $ la position, ils pèsent lourd. Les frais de priorité de Trojan et la location de
  0,002 SOL par nouveau compte de token font plusieurs % par trade. La location est récupérable en fermant
  les comptes vides.
- **Écarts avec le backtest :**
  - le délai réel (détection + Trojan) sera d'environ 3 à 5 s, contre 2 s dans le backtest ;
  - certains tokens pump.fun n'avaient pas son achat dans les données pump.fun (81 sur 3 jours) et n'ont pas
    été testés ; le bot les prend aussi ;
  - le filtre « 50 % sous le plus haut » n'a pas été testé sur le grand échantillon.

## Installation sur ton PC (Windows, PowerShell)

1. Mets le dépôt à jour, puis installe Telethon :
   ```
   cd <ton dossier lk>
   git pull
   pip install telethon
   ```
2. Crée tes identifiants d'application Telegram sur https://my.telegram.org (connecte-toi avec ton numéro,
   puis « API development tools »). Tu obtiens un **api_id** (un nombre) et un **api_hash**.
   **Ne les donne à personne, ni à moi dans le chat.** Avec le fichier de session, ils donnent accès à ton
   compte Telegram.
3. Copie `trojan_config.example.json` en `trojan_config.json`, à la racine du dossier, et remplis :
   - `api_id` et `api_hash` ;
   - `trojan_bot` : le nom exact de ton bot Trojan, sans le @, par exemple `solana_trojanbot`. Vérifie-le dans
     Telegram : il existe de faux bots Trojan.

   Ce fichier et le fichier `trojan_bot.session` créé à la première connexion ne sont jamais envoyés sur
   GitHub (ils sont dans `.gitignore`).
4. Dans Trojan :
   - **Autobuy activé**, montant = 5 $ en SOL (le bot affiche la conversion au démarrage, par exemple
     0,0485 SOL) ;
   - réglages de vente automatique si ton Trojan les propose : +50 % → vendre 50 %, +100 % → vendre 100 %,
     −50 % → vendre 100 % ;
   - n'y mets qu'un petit solde, par exemple 60 à 80 $ (une dizaine de positions ouvertes en même temps, plus
     les frais).
5. Test de connexion : `python scripts\trojan_autobuy.py --test-telegram`
   - La première fois, il demande ton numéro, puis le code reçu dans Telegram (et ton mot de passe 2FA si tu
     en as un).
   - Il envoie un message de test dans tes Messages enregistrés.
   - Il affiche les derniers messages de Trojan avec leurs boutons. **Rien n'est envoyé à Trojan.**
6. Vente automatique (facultatif) : teste-la sur une position que tu veux vendre de toute façon, avec
   `python scripts\trojan_autobuy.py --test-vente <CA>`. Si le script affiche « OK » et que Trojan confirme la
   vente, ajoute `"vente_auto": true` dans `trojan_config.json`.
7. Simulation, sans rien envoyer : `python scripts\trojan_autobuy.py`. Laisse tourner 1 h pour voir passer les
   signaux et les rejets.
8. En réel : `python scripts\trojan_autobuy.py --live`
   - Pour l'arrêter : Ctrl+C.
   - Le PC doit rester allumé et ne pas se mettre en veille.

## Journal et suivi

- `data\trojan\journal.jsonl` : chaque événement (signal envoyé, rejet avec sa raison, retard, sorties simulées,
  réponses de Trojan avec leurs boutons).
- `data\trojan\resume.json` : le bilan simulé de la règle (en % par trade, hors frais fixes), à comparer avec
  tes résultats réels.
- `data\trojan\envoyes.json` : les CA déjà envoyés (jamais deux fois le même).

Après une cinquantaine de trades, envoie-moi `resume.json`, et l'adresse **publique** de ton wallet Trojan si
tu veux que je lise tes vrais résultats on-chain. Ne m'envoie jamais ta clé privée ni ta phrase de récupération.
