import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('alerts', Path(__file__).resolve().parents[1] / 'scripts/callout_alerts.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def call(identity='one', created=2000):
    return dict(calloutId=identity, coinMint='So11111111111111111111111111111111111111112',
                createdAt=created, author_name='kreisi', name='Offline Runner', symbol='Dino',
                thesis='We all going offline soon.', marketCap=20800, mcap_now=20900)


class AlertsTest(unittest.TestCase):
    def test_format_and_escaping(self):
        c = call()
        c['thesis'] = '<b>fake</b> & test'
        text = a.format_alert(c)
        self.assertIn('kreisi</b> a callé <b>Offline Runner ($Dino)', text)
        self.assertIn('$20.8k</b> · maintenant $20.9k', text)
        self.assertIn('&lt;b&gt;fake&lt;/b&gt; &amp; test', text)
        self.assertIn('00:33:20 UTC', text)
        self.assertIn('https://dexscreener.com/solana/', text)
        self.assertEqual(a.timestamp(1720000000000), a.timestamp('2024-07-03T09:46:40Z'))

    def test_retry_restart_dedup_and_initial_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'state.sqlite')
            store = a.Store(path, 1000)
            store.enqueue(call('old', 900))
            store.enqueue(call())
            with self.assertRaises(a.RemoteError):
                store.deliver(lambda text: (_ for _ in ()).throw(a.RemoteError('failed')))
            store.db.close()
            store = a.Store(path, 3000)
            store.enqueue(call())
            store.enqueue(call('during-offline', 2500))
            sent = []
            store.deliver(sent.append)
            self.assertEqual(len(sent), 2)
            store.deliver(sent.append)
            self.assertEqual(len(sent), 2)
            store.db.close()

    def test_following_pagination_and_fail_closed(self):
        pump = a.Pump()
        batch = [{'address': str(i), 'username': str(i)} for i in range(100)]
        with patch.object(pump, 'get', side_effect=[batch, [{'address':'last'}]]):
            self.assertEqual(len(pump.following('owner')), 101)
        with patch.object(pump, 'get', return_value=batch):
            with self.assertRaises(a.RemoteError):
                pump.following('owner')
        with patch.object(pump, 'get', return_value={}):
            with self.assertRaises(a.RemoteError):
                pump.callouts('owner')

    def test_telegram_payload(self):
        with patch.object(a, 'request', return_value={'ok': True}) as request:
            a.send('test-token', '123', a.format_alert(call()))
            url, payload = request.call_args.args
            self.assertEqual(url, 'https://api.telegram.org/bottest-token/sendMessage')
            self.assertEqual(payload['chat_id'], '123')
            self.assertEqual(payload['parse_mode'], 'HTML')
            self.assertTrue(payload['disable_web_page_preview'])

    def test_telegram_refusal_does_not_count_as_success(self):
        with patch.object(a, 'request', return_value={'ok': False}):
            with self.assertRaises(a.RemoteError):
                a.send('secret', 'chat', 'text')

    def test_user_transport_login_send_and_cleanup(self):
        entity = SimpleNamespace(id=123)
        client = SimpleNamespace(start=AsyncMock(), get_entity=AsyncMock(return_value=entity),
                                 get_me=AsyncMock(return_value=SimpleNamespace(id=42)),
                                 send_message=AsyncMock(), disconnect=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp, patch('telethon.TelegramClient', return_value=client) as factory:
            sender = a.TelegramUserSender('@target_bot', str(Path(tmp) / 'session')).connect()
            try:
                sender('<b>Callout</b>')
                self.assertEqual(sender.identity, 42)
                client.get_entity.assert_awaited_once_with('@target_bot')
                client.send_message.assert_awaited_once_with(entity, '<b>Callout</b>', parse_mode='html', link_preview=False)
                self.assertEqual(factory.call_args.args[1], a.TG_API_ID)
            finally:
                sender.close()
            client.disconnect.assert_awaited_once()
            self.assertTrue(sender.loop.is_closed())

    def test_user_transport_failure_keeps_alert_pending(self):
        client = SimpleNamespace(start=AsyncMock(), get_entity=AsyncMock(return_value=SimpleNamespace(id=123)),
                                 get_me=AsyncMock(return_value=SimpleNamespace(id=42)),
                                 send_message=AsyncMock(side_effect=RuntimeError('network')),
                                 disconnect=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp, patch('telethon.TelegramClient', return_value=client):
            sender = a.TelegramUserSender('@target_bot', str(Path(tmp) / 'session')).connect()
            store = a.Store(str(Path(tmp) / 'state'), 1000)
            try:
                store.enqueue(call())
                with self.assertRaises(a.RemoteError):
                    store.deliver(sender)
                self.assertEqual(store.db.execute('SELECT sent FROM alerts').fetchone()[0], 0)
                client.send_message.side_effect = None
                store.deliver(sender)
                self.assertEqual(store.db.execute('SELECT sent FROM alerts').fetchone()[0], 1)
            finally:
                store.db.close()
                sender.close()

    def test_feed_normalizes_api_response(self):
        pump = a.Pump('urllib')
        payload = {'coins': [{'coinMint': call()['coinMint'], 'coinName': 'Offline Runner', 'symbol': 'Dino',
                              'marketCap': 20900, 'position': {'walletAddress': 'owner', 'callout': {
                                  'calloutId': 'one', 'calloutTimestamp': '2024-07-03T09:46:40Z',
                                  'calledOutAtMcap': 20800, 'thesis': 'hello'}}}, {'position': {}}]}
        with patch.object(pump, 'get', return_value=payload):
            rows = pump.feed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['author'], 'owner')
        self.assertEqual(rows[0]['marketCap'], 20800)
        self.assertEqual(rows[0]['mcap_now'], 20900)

    def test_feed_delivery_survives_profile_403(self):
        pump = MagicMock()
        pump.feed.return_value = [dict(call(), author='owner')]
        pump.callouts.side_effect = a.RemoteError('Forbidden', status=403)
        args = SimpleNamespace(mode='self', interval=5)
        with tempfile.TemporaryDirectory() as tmp:
            store = a.Store(str(Path(tmp) / 'state'), 1000)
            sent = []
            try:
                with patch.object(a.time, 'monotonic', return_value=10), patch.object(a.time, 'sleep', side_effect=[None, KeyboardInterrupt]):
                    with self.assertRaises(KeyboardInterrupt):
                        a.monitor(pump, 'owner', 'owner', store, sent.append, args)
                self.assertEqual(len(sent), 1)
                self.assertEqual(pump.feed.call_count, 2)
                self.assertEqual(pump.callouts.call_count, 1)
            finally:
                store.db.close()

    def test_browser_client_preserves_http_status(self):
        pump = a.Pump('urllib')
        pump.session = MagicMock()
        pump.session.get.return_value.status_code = 403
        with self.assertRaises(a.RemoteError) as caught:
            pump.get('/callout/list/owner')
        self.assertEqual(caught.exception.status, 403)
        self.assertIn('pump.fun /callout/list/owner', str(caught.exception))

    def test_invalid_link_rejected_and_metadata_optional(self):
        c = call()
        c['coinMint'] = '\" onclick=\"bad'
        with self.assertRaises(ValueError):
            a.format_alert(c)
        with patch.object(a.Pump, 'get', side_effect=a.RemoteError('403')):
            self.assertEqual(a.Pump().enrich(call()), call())


if __name__ == '__main__':
    unittest.main()
