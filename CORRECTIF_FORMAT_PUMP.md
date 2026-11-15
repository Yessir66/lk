# Corriger le champ Pump.fun manquant — 2026.11.15.5

Le premier bilan observe sur le VPS comptait 2 tokens, tous deux arretes immediatement avec « Extension du format Pump.fun non reconnue ». Les 400 USD n'avaient pas ete mesures : le zero affiche n'etait pas une preuve d'absence d'achats.

Le schema initial provenait du depot public de documentation Pump.fun. Le SDK officiel **@pump-fun/pump-sdk 4.3.1**, dans `src/idl/pump.json`, ajoute le champ final `quote_transfer_fee: u64`. Il est maintenant decode explicitement. La transaction archivee qui reproduisait un excedent de 8 octets est desormais lue sans octet restant ; un test verifie aussi le passage d'un evenement de ce format a une decision theorique. Les anciens evenements sans ce champ restent acceptes ; toute autre extension inconnue reste refusee pour les decisions.

Sources verifiees en lecture seule :

- [SDK officiel sur npm, version 4.3.1](https://www.npmjs.com/package/@pump-fun/pump-sdk/v/4.3.1)
- [Depot du SDK officiel](https://github.com/pump-fun/pump-sdk)

Il n'est pas necessaire d'installer npm ou le SDK sur le VPS : le correctif utilise directement la definition du champ. Les frais de transfert du quote ne sont pas ajoutes au volume de pool ; cela ne change pas les frais d'un achat reel, puisqu'aucun ordre n'est envoye.

## Appliquer le correctif

1. Dans le menu Wallet Radar, choisis **5** pour arreter proprement la surveillance du VPS. Attends l'arret confirme, puis quitte les anciens menus avec **0**.
2. Extrais le correctif dans `C:\Users\Enzo\Downloads\wallet-alerts` et accepte les remplacements. Conserve la configuration et les donnees.
3. Garde Chrome et l'extension ouverts, puis lance **WALLET_RADAR.cmd**.
4. Choisis **3**. Le menu transfere automatiquement le moteur et son decodeur corriges, puis lance un **nouvel essai de 30 tokens distincts**.
5. La version serveur affichee doit etre **2026.11.15.5**. La configuration Helius, BBIG BOSS et GMGN restent identiques.

Les deux premiers cas bloques restent dans l'ancien journal. Ils ne sont pas transformes retroactivement en resultats valides. Le nouvel essai commence avec les prochains signaux, sans acheter et sans rejouer les anciens tokens.

Le menu **4** affiche desormais « volume USD observe non mesure » quand les donnees sont absentes ou le format bloque. Les autres limites de `SIMULATION_BBIG_BOSS.md` restent applicables : premiere couverture Pump.fun, devise de cotation convertible, preuves de position disponibles a temps et fenetres mesurees a reception.

Si un nouveau signal affiche encore une extension inconnue, relever le bilan : le format devra etre verifie sur ses donnees reelles plutot que d'ignorer arbitrairement les octets. Cette mise a jour corrige le champ identifie dans le SDK et le cas archive reproduit ; son fonctionnement sur les prochains tokens du VPS reste a observer.
