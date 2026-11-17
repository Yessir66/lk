# Cloturer le suivi apres une vente manuelle — 2026.11.17.4

Le dernier diagnostic montre une position `manual` avec une quantite actuelle de **0**. Le bot bloque les nouveaux achats parce que son journal n'a pas encore rapproche la vente externe.

1. Extraire cette archive dans le dossier habituel `wallet-alerts`, en remplacant les fichiers proposes. Conserver les fichiers de configuration, cles et bases existants.
2. Garder la fenetre du bot actif ouverte. Ouvrir un second `WALLET_RADAR.cmd` depuis le dossier mis a jour.
3. Choisir **13 — Rapprocher une vente manuelle verifiee et debloquer les achats**. Saisir uniquement le mot de passe SSH habituel si demande. Aucune cle wallet ou Jupiter n'est necessaire pour ce controle.
4. Attendre `POSITION CLOTUREE`, puis consulter **9** : la ligne doit passer a `closed` et afficher une signature de vente externe verifiee.

**Si le mode reel 8 est encore actif, la cloture du suivi permet de nouveaux achats sur les prochains signaux**, dans le budget initial. Le rapprochement ne redemarre pas le bot. Si celui-ci est arrete, il reste arrete.

Le programme verifie le solde nul, retrouve l'achat initial dans le wallet de test, puis cherche une vente totale apres cet achat : transactions finalisees, reussies, signees par le wallet, avec un swap reconnu et une contrepartie SOL. La quantite vendue doit correspondre exactement a celle du journal et le solde apres vente doit etre nul. Un simple transfert ou solde nul ne suffit pas. Le solde est relu avant la modification du journal.

La recherche est limitee aux 20 signatures recentes et a 8 transactions candidates, avec un quota RPC de 1/s. Une vente trop recente pour etre finalisee peut necessiter de relancer13 plus tard. Une vente partielle, contre une autre devise ou hors de cette recherche reste bloquee : transmettre alors le **lien public de la vente**, jamais une cle privee.

Seule la ligne manuelle controlee passe a `closed`, avec sa preuve externe. Les anciennes signatures et la reserve de **2,40 USD** sont conservees. Le budget ne revient pas a zero et ce token ne devient pas eligible a un nouvel achat. Une modification concurrente du journal annule le rapprochement.

Cette commande ne signe et n'envoie aucune transaction. Ne pas effacer la base ou les verrous pour debloquer le bot.
