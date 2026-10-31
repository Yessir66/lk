#!/usr/bin/env python3
"""Run the existing alert_scout as a Discord reader only, without its Telegram cards."""
import importlib.util
import json
from pathlib import Path
import re
import time


def configure_reader(module):
    required = ('load_config', 'scout', 'journal', 'on_message', 'resume_journal', 'cfg', 'seen', 'main', 'JOURNAL')
    if any(not hasattr(module, key) for key in required):
        raise RuntimeError('Version alert_scout non compatible avec le mode lecteur seul')
    original_load, original_message = module.load_config, module.on_message

    def load_without_notifications(path):
        url = original_load(path)
        # In-memory only. The analysis process still reads the untouched file.
        module.cfg['telegram_bot_token'] = ''
        module.cfg['anthropic_api_key'] = ''
        module.cfg['narrative'] = False
        return url

    def record_signal(mint, t_alert, source, text='', replay=False):
        if source != 'discord' or replay:
            return
        module.journal(dict(type='alert', mint=mint, t_alert=t_alert, t0=t_alert-t_alert%60,
                            source='discord', text=text[:500]))
        print('SIGNAL DISCORD ENREGISTRÉ : ' + mint + ' ; analyse DECU / LE L dans le second module.', flush=True)

    def original_discord_time(message, source):
        if source != 'discord':
            return 'source ignorée'
        identity = str(message.get('id', ''))
        match = re.fullmatch(r'chat-messages-(\d{10,22})-(\d{10,22})', identity)
        if not match or str(message.get('channel')) != match[1]:
            return 'identifiant Discord absent ou incohérent ; signal ignoré'
        stamp_ms = (int(match[2]) >> 22) + 1420070400000
        if stamp_ms > time.time()*1000+10000:
            return 'horodatage futur ; signal ignoré'
        return original_message(dict(message, ts=stamp_ms), source)

    def resume_seen_only():
        path = Path(module.JOURNAL)
        if path.is_file():
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'alert' and time.time()-event['t_alert'] < 86400:
                            module.seen[event['mint']] = event['t_alert']
                    except (ValueError, KeyError, TypeError):
                        continue

    module.load_config = load_without_notifications
    module.scout = record_signal
    module.on_message = original_discord_time
    module.resume_journal = resume_seen_only
    module.tg = lambda *args, **kwargs: {}  # No Telegram API method from this process.
    return module


def main():
    path = Path(__file__).resolve().with_name('alert_scout.py')
    if not path.is_file():
        raise SystemExit('Conserver ton scripts/alert_scout.py existant : ce lanceur le charge en mode lecteur seul.')
    spec = importlib.util.spec_from_file_location('_alert_scout_reader_only', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        configure_reader(module)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    print('LECTEUR DISCORD SEUL : fiches, récits, verdicts et messages Telegram de l’ancien scout désactivés.', flush=True)
    module.main()


if __name__ == '__main__':
    main()
