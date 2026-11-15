#!/usr/bin/env python3
"""Enter a Helius API key locally; never print or place it in shell arguments."""
import getpass
import argparse
import json
import os
from pathlib import Path
import tempfile
import re


def save_config(path,data):
    descriptor,name=tempfile.mkstemp(prefix='.wallet-alerts-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as handle:
            json.dump(data,handle,ensure_ascii=False,indent=2);handle.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def manage_wallets(path,data):
    from discord_token_analysis import valid_address
    wallets=data.get('wallets')
    if not isinstance(wallets,dict):
        raise SystemExit('Liste absente ou invalide. Configurer un premier wallet avec --wallet.')
    while True:
        print('\nWALLET RADAR - Wallets surveilles ('+str(len(wallets))+'/20)')
        for index,(name,address) in enumerate(wallets.items(),1):print(f'{index}. {name} : {address}')
        print('\n1 - Afficher la liste\n2 - Ajouter un wallet\n3 - Retirer un wallet\n4 - Renommer un wallet\n0 - Quitter')
        print('Les changements seront actifs au prochain lancement de Wallet Radar.')
        choice=input('Ton choix : ').strip()
        if choice=='0':return
        if choice=='1':continue
        updated=dict(wallets)
        if choice=='2':
            if len(wallets)>=20:
                print('Maximum 20 wallets. Retire un wallet avant de continuer.');continue
            name=input('Nom du wallet : ').strip()
            address=input('Adresse publique Solana du wallet : ').strip()
            if not 1<=len(name)<=40 or any(ord(c)<32 or ord(c)==127 for c in name):
                print('Nom invalide : 1 a 40 caracteres. Aucun changement.');continue
            if any(name.casefold()==existing.casefold() for existing in wallets):
                print('Ce nom existe deja. Choisis un autre nom.');continue
            if not valid_address(address):
                print('Adresse Solana invalide. Aucun changement.');continue
            if address in wallets.values():
                print('Cette adresse est deja surveillee. Aucun doublon ajoute.');continue
            updated[name]=address
        elif choice in ('3','4'):
            value=input('Numero du wallet dans la liste ci-dessus (Entree pour annuler) : ').strip()
            if not value:continue
            if not value.isdecimal() or not 1<=int(value)<=len(wallets):
                print('Numero invalide. Aucun changement.');continue
            selected=list(wallets)[int(value)-1]
            if choice=='3':
                if len(wallets)==1:
                    print('Garde au moins un wallet. Ajoute le nouveau avant de retirer le dernier.');continue
                del updated[selected]
            else:
                name=input('Nouveau nom : ').strip()
                if (not 1<=len(name)<=40 or any(ord(c)<32 or ord(c)==127 for c in name)
                    or any(name.casefold()==existing.casefold() for existing in wallets if existing!=selected)):
                    print('Nom invalide ou deja utilise. Aucun changement.');continue
                updated={name if old==selected else old:address for old,address in wallets.items()}
        else:
            print('Choisis 0, 1, 2, 3 ou 4.');continue
        # Write only the public-wallet list; preserve every credential and other setting.
        replacement=dict(data,wallets=updated)
        save_config(path,replacement)
        data=replacement;wallets=updated
        print('Liste enregistree. Arrete puis relance Wallet Radar pour appliquer le changement.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--telegram',action='store_true',help='Configurer le bot d’alertes Telegram sans requête réseau')
    mode.add_argument('--wallet',help='Remplacer toute la liste par cette adresse publique Solana')
    mode.add_argument('--manage-wallets',action='store_true',help='Menu Wallet Radar : ajouter, retirer et renommer les wallets')
    parser.add_argument('--name',help='Nom du wallet avec --wallet')
    args=parser.parse_args()
    if args.name and not args.wallet:parser.error('--name nécessite --wallet')
    root=Path(__file__).resolve().parents[1];path=root/'wallet_alerts_config.json'
    template=root/'wallet_alerts_config.example.json'
    data=json.loads((path if path.is_file() else template).read_text(encoding='utf-8-sig'))
    if args.manage_wallets:
        if not data.get('helius_api_key') or data['helius_api_key']=='REMPLACER_LOCALEMENT':
            raise SystemExit('Configure d’abord la cle Helius. Aucune modification effectuee.')
        try:manage_wallets(path,data)
        except (KeyboardInterrupt,EOFError):print('\nGestion terminee. Les changements deja enregistres sont conserves.')
        return
    if args.wallet:
        from discord_token_analysis import valid_address
        wallet_name=(args.name or args.wallet[:8]).strip()
        if not valid_address(args.wallet) or not 1<=len(wallet_name)<=40:
            raise SystemExit('Adresse publique Solana ou nom invalide ; aucun fichier modifié.')
        data['wallets']={wallet_name:args.wallet}
    elif args.telegram:
        token=getpass.getpass('Token du bot d’alertes Telegram (BotFather ; saisie masquée) : ').strip()
        chat=input('Chat ID Telegram (laisser vide pour conserver la valeur existante) : ').strip()
        if token:data['telegram_bot_token']=token
        if chat:data['telegram_chat_id']=chat
        if not re.fullmatch(r'[0-9]{5,16}:[A-Za-z0-9_-]{15,}',str(data.get('telegram_bot_token',''))):
            raise SystemExit('Token Bot API invalide ; aucun fichier modifié.')
        if not re.fullmatch(r'-?[0-9]+|@[A-Za-z0-9_]{5,}',str(data.get('telegram_chat_id',''))):
            raise SystemExit('Chat ID invalide ; aucun fichier modifié.')
    else:
        value=getpass.getpass('Clé API Helius (saisie masquée ; vide pour conserver la clé existante) : ').strip()
        if value:data['helius_api_key']=value
    if not data.get('helius_api_key') or data['helius_api_key']=='REMPLACER_LOCALEMENT':
        raise SystemExit('Aucune clé enregistrée. Relancer avec une clé Helius valide.')
    # Keep this helper restricted to API keys. It never requests a wallet private key.
    save_config(path,data)
    print('Configuration enregistrée localement. Aucun appel réseau, aucune clé affichée.')
    if args.wallet:print('Wallet suivi uniquement : '+wallet_name+' ; '+args.wallet)


if __name__=='__main__':main()
