from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import callout_hub as h

AUTHOR = next(iter(h.CALLERS))
MINT = 'So11111111111111111111111111111111111111112'


def call(identity='one', author=AUTHOR, created=1000):
    return dict(calloutId=identity, author=author, coinMint=MINT, createdAt=created,
                name='Token', symbol='T', marketCap=2000, mcap_now=2100, thesis='source')


class HubTest(unittest.TestCase):
    def reader(self, pump=None, repost=True):
        pump = pump or MagicMock()
        pump.following.return_value = {AUTHOR: 'caller', 'other': 'other-user'}
        pump.feed.return_value = [call()]
        pump.callouts.return_value = [call()]
        return h.SharedReader(pump, 'owner', 900, 900, queue.Queue(), queue.Queue(), repost=repost)

    def test_one_feed_one_profile_read_fans_out_once_to_both_features(self):
        reader = self.reader()
        with patch.object(h.time, 'monotonic', return_value=10):
            reader.step()
        reader.pump.feed.assert_called_once()
        reader.pump.callouts.assert_called_once_with(AUTHOR)
        self.assertEqual(reader.alerts_queue.qsize(), 1)
        self.assertEqual(reader.repost_queue.qsize(), 1)
        self.assertEqual(reader.alerts_queue.get()['author_name'], 'caller')
        reader.pump.enrich.assert_not_called()

    def test_profile_429_respects_server_delay_without_stopping_feed(self):
        reader = self.reader()
        reader.pump.callouts.side_effect = h.RemoteError('429', status=429, retry_after=900)
        with patch.object(h.time, 'monotonic', return_value=10):
            reader.step()
        self.assertEqual(reader.next_backup, 910)
        with patch.object(h.time, 'monotonic', return_value=13):
            reader.step()
        reader.pump.callouts.assert_called_once()
        self.assertEqual(reader.pump.feed.call_count, 2)
        self.assertEqual(reader.repost_queue.qsize(), 1)

    def test_repeated_profile_429_extends_pause_and_recovers_on_success(self):
        reader = self.reader()
        reader.pump.callouts.side_effect = h.RemoteError('429', status=429)
        with patch.object(h.time, 'monotonic', return_value=10): reader.step()
        self.assertEqual(reader.next_backup, 130)
        with patch.object(h.time, 'monotonic', return_value=130): reader.step()
        self.assertEqual(reader.next_backup, 370)
        reader.pump.callouts.side_effect = None
        with patch.object(h.time, 'monotonic', return_value=370): reader.step()
        self.assertEqual(reader.backup_backoff.failures, 0)
        self.assertEqual(reader.next_backup, 385)

    def test_subscription_refresh_keeps_existing_profile_pause(self):
        reader = self.reader()
        reader.pump.callouts.side_effect = h.RemoteError('429', status=429, retry_after=1800)
        with patch.object(h.time, 'monotonic', return_value=10): reader.step()
        with patch.object(h.time, 'monotonic', return_value=700): reader.step()
        self.assertEqual(reader.pump.following.call_count, 2)
        reader.pump.callouts.assert_called_once()
        self.assertEqual(reader.next_backup, 1810)

    def test_filters_keep_alert_following_and_ten_traders_independent(self):
        reader = self.reader()
        reader.follows = {'other': 'other-user'}
        reader.targets = dict(reader.follows, **h.CALLERS)
        reader.route([call(), call('two', 'other'), call('ignored', 'unwatched')])
        self.assertEqual(reader.repost_queue.qsize(), 1)
        self.assertEqual(reader.alerts_queue.qsize(), 1)
        self.assertEqual(reader.alerts_queue.get()['calloutId'], 'two')

    def test_metadata_cannot_delay_trade_routing_and_is_cached(self):
        reader = self.reader()
        reader.follows = reader.targets = {AUTHOR: 'caller'}
        partial = call()
        partial.pop('name')
        partial.pop('mcap_now')
        def enrich(c):
            self.assertFalse(reader.repost_queue.empty())
            return dict(c, name='Token', symbol='T', mcap_now=2200)
        reader.pump.enrich.side_effect = enrich
        reader.route([partial])
        reader.route([dict(partial, calloutId='another-call')])
        reader.pump.enrich.assert_called_once()
        self.assertEqual(reader.alerts_queue.qsize(), 2)

    def test_restarts_do_not_enrich_or_requeue_durable_ids(self):
        reader = self.reader()
        reader.follows = reader.targets = {AUTHOR: 'caller'}
        reader.seen_alerts.add('one')
        reader.seen_reposts.add('one')
        reader.route([dict(call(), name=None)])
        self.assertTrue(reader.alerts_queue.empty())
        self.assertTrue(reader.repost_queue.empty())
        reader.pump.enrich.assert_not_called()

    def test_feed_failure_keeps_profile_fallback_and_backs_off_feed(self):
        reader = self.reader()
        reader.pump.feed.side_effect = h.RemoteError('403', status=403)
        with patch.object(h.time, 'monotonic', return_value=10):
            reader.step()
        with patch.object(h.time, 'monotonic', return_value=13):
            reader.step()
        reader.pump.feed.assert_called_once()
        self.assertEqual(reader.pump.callouts.call_count, 2)
        self.assertFalse(reader.repost_queue.empty())

    def test_unavailable_following_does_not_drop_fixed_trading_targets(self):
        reader = self.reader()
        reader.pump.following.side_effect = h.RemoteError('503')
        with patch.object(h.time, 'monotonic', return_value=10):
            reader.step()
        self.assertEqual(set(reader.targets), set(h.CALLERS))
        self.assertEqual(reader.repost_queue.qsize(), 1)
        self.assertTrue(reader.alerts_queue.empty())

    def test_alerts_only_does_not_route_any_trades(self):
        reader = self.reader(repost=False)
        with patch.object(h.time, 'monotonic', return_value=10):
            reader.step()
        self.assertTrue(reader.repost_queue.empty())
        self.assertEqual(reader.alerts_queue.qsize(), 1)

    def test_slow_trade_worker_does_not_block_alerts_or_public_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop, blocked, release, delivered = threading.Event(), threading.Event(), threading.Event(), threading.Event()
            trade_events, alert_events = queue.Queue(), queue.Queue()
            execution = SimpleNamespace(owner='owner')
            args = SimpleNamespace(max_age=60, delay=15, text=None)
            trade = h.RepostWorker(trade_events, str(Path(tmp)/'trade'), execution, args, stop)
            alerts = h.AlertWorker(alert_events, str(Path(tmp)/'alerts'), lambda text: delivered.set(), stop)
            fake_engine = MagicMock()
            fake_engine.step.side_effect = lambda now: (blocked.set(), release.wait(3))
            threads = [threading.Thread(target=w.run) for w in (trade, alerts)]
            with patch.object(h, 'Engine', return_value=fake_engine):
                try:
                    for t in threads: t.start()
                    self.assertTrue(trade.ready.wait(2))
                    self.assertTrue(alerts.ready.wait(2))
                    self.assertTrue(blocked.wait(2))
                    reader = self.reader()
                    reader.alerts_queue, reader.repost_queue = alert_events, trade_events
                    reader.alert_since, reader.repost_since = alerts.since, trade.since
                    reader.pump.feed.return_value = [call(created=time.time()+.01)]
                    reader.pump.callouts.return_value = []
                    reader.step()
                    self.assertTrue(delivered.wait(1), 'Telegram should run while the trade engine is waiting')
                    self.assertFalse(release.is_set())
                    reader.next_feed = 0
                    reader.step()
                    self.assertEqual(reader.pump.feed.call_count, 2)
                finally:
                    stop.set()
                    release.set()
                    for t in threads: t.join(3)
            self.assertTrue(all(not t.is_alive() for t in threads))
            self.assertIsNone(trade.error)
            self.assertIsNone(alerts.error)

    def test_status_command_never_starts_telegram_wallet_or_poller(self):
        with patch.object(h, 'parse_args', return_value=SimpleNamespace(status=True, state='journal.sqlite3')), \
             patch.object(h, 'show_status') as status, patch.object(h, 'Pump') as pump, \
             patch.object(h, 'TelegramUserSender') as telegram, patch.object(h, 'Execution') as execution:
            h.main()
        status.assert_called_once_with('journal.sqlite3')
        pump.assert_not_called()
        telegram.assert_not_called()
        execution.assert_not_called()

    def test_finds_retained_telegram_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, old = Path(tmp)/'new', Path(tmp)/'old'
            old.mkdir()
            session = old/'callout_alerts_telegram.session'
            session.touch()
            with patch.object(h, 'data_roots', return_value=[first, old]):
                self.assertEqual(h.retained_path(session.name), str(session))


if __name__ == '__main__':
    unittest.main()
