#!/usr/bin/env python3
"""Enter a Helius API key locally; never print or place it in shell arguments."""
import getpass
import argparse
import json
import os
from pathlib import Path
import tempfile
import re


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telegram',action='store_true',help='Configurer le bot d’alertes Telegram sans requête réseau')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];path=root/'wallet_alerts_config.json'
    template=root/'wallet_alerts_config.example.json'
    data=json.loads((path if path.is_file() else template).read_text(encoding='utf-8-sig'))
    if args.telegram:
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
    descriptor,name=tempfile.mkstemp(prefix='.wallet-alerts-',dir=root)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as handle:
            json.dump(data,handle,ensure_ascii=False,indent=2);handle.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
    print('Configuration enregistrée localement. Aucun appel réseau, aucune clé affichée.')


if __name__=='__main__':main()
