#!/usr/bin/env python3
"""Enter a Helius API key locally; never print or place it in shell arguments."""
import getpass
import json
import os
from pathlib import Path
import tempfile


def main():
    root=Path(__file__).resolve().parents[1];path=root/'wallet_alerts_config.json'
    template=root/'wallet_alerts_config.example.json'
    data=json.loads((path if path.is_file() else template).read_text(encoding='utf-8-sig'))
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
