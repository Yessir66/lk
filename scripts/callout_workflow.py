"""Background content preparation and author-only callout update observation."""
import re
import queue
import threading
import time

from callout_alerts import Pump, RemoteError, RateBackoff
from callout_content import ContentService
from callout_repost import CALLERS, ROOT


class UpdateWatcher:
    def __init__(self, events, stop):
        self.events, self.stop = events, stop
        self.lock = threading.Lock()
        self.targets, self.seen = [], set()
        self.thread = threading.Thread(target=self.run, daemon=True, name='callout-updates')

    def set_jobs(self, jobs):
        targets = [dict(j) for j in jobs if j.get('workflow_version') == 2
                   and j.get('source_author') in CALLERS and not j.get('publication_disabled')
                   and j['state'] in ('posted', 'simulated') and re.fullmatch(r'[0-9a-fA-F-]{36}', j['call_id'])]
        with self.lock:
            self.targets = targets

    def run(self):
        pump, backoff, index = Pump('browser'), RateBackoff(), 0
        while not self.stop.is_set():
            with self.lock:
                jobs = list(self.targets)
            if not jobs:
                self.stop.wait(.25)
                continue
            job = jobs[index % len(jobs)]
            index += 1
            try:
                rows = pump.get(f'/callout/{job["call_id"]}/replies?filter=AUTHOR_ONLY&sort=RECENCY&limit=100').get('replies', [])
                # The endpoint's AUTHOR_ONLY gate is authoritative; additionally
                # reject any resolved wallet that doesn't match our source.
                for row in reversed(rows):
                    if row.get('tombstone') or row.get('isQuote') or not row.get('createdAt') or not row.get('id'):
                        continue
                    if row.get('walletAddress') and row['walletAddress'] != job['source_author']:
                        continue
                    key = job['call_id'] + ':' + row['id']
                    if key not in self.seen:
                        self.events.put({'_kind': 'update', 'mint': job['mint'], 'source_call_id': job['call_id'], 'update': row})
                        self.seen.add(key)
                if len(rows) == 100:
                    print('Updates : fenêtre de 100 réponses atteinte, historique éventuellement incomplet.', flush=True)
                if len(self.seen) > 20000:
                    self.seen.clear()  # Persistent update IDs still prevent duplicate posts.
                backoff.success()
                self.stop.wait(2)
            except RemoteError as exc:
                wait = backoff.delay(exc)
                print(f'Lecture des updates différée ({wait:g}s) : {exc}', flush=True)
                self.stop.wait(wait)
            except Exception:
                print('Lecture des updates différée : réponse inexploitable.', flush=True)
                self.stop.wait(30)


class Workflow:
    def __init__(self, journal, args, events, stop):
        self.journal, self.args = journal, args
        self.content = ContentService(ROOT / 'data' / 'content', getattr(args, 'rewrite_mode', 'local'))
        self.update_events = queue.Queue()
        self.watcher = UpdateWatcher(self.update_events, stop)
        self.watcher.thread.start()

    def tick(self, now):
        # Social updates have their own queue; a burst of old replies must never
        # look like pending buy signals or starve the exit checks.
        for _ in range(32):
            try:
                self.journal.add_update(self.update_events.get_nowait(), now, getattr(self.args, 'update_delay', 10))
            except queue.Empty:
                break
            except (KeyError, ValueError, TypeError):
                print('Update incomplète ignorée.', flush=True)
        jobs = self.journal.jobs()
        self.watcher.set_jobs(jobs)
        for job in jobs:
            if job.get('workflow_version') != 2 or job.get('source_author') not in CALLERS or job.get('content_ready') or job['state'] in ('failed', 'review'):
                if job['state'] in ('failed', 'review') or job.get('publication_disabled'):
                    self.content.forget('initial:' + job['call_id'])
                continue
            source = {'thesis': job.get('source_thesis', ''), 'mediaUrl': job.get('source_media')}
            key = 'initial:' + job['call_id']
            result = self.content.take(key)
            if result is None:
                self.content.submit(key, source)
                continue
            if job['state'] in ('new', 'signed', 'confirmed', 'simulated') and not job.get('post_started'):
                if getattr(self.args, 'text', None) is None:
                    job['text'] = result['text']
                job.update({k: v for k, v in result.items() if k != 'text'})
            elif job['state'] == 'posted' and result.get('image_path') and not job.get('image_path'):
                self.late_image(job, result, 'initial', now)
            job['content_ready'] = True
            self.journal.save(job)
            print(f'Contenu préparé : {job["caller"]}, mode {result["rewrite_method"]}.', flush=True)
        indexed = {j['mint']: j for j in self.journal.jobs()}
        for update in self.journal.updates():
            if update.get('content_ready') or update['state'] in ('failed', 'review'):
                if update['state'] in ('failed', 'review'):
                    self.content.forget('update:' + update['key'])
                continue
            job = indexed.get(update['mint'])
            if not job:
                continue
            key = 'update:' + update['key']
            result = self.content.take(key)
            if result is None:
                closed = job.get('exit_state') in ('sold', 'empty')
                update.setdefault('prepared_closed', closed)
                self.content.submit(key, update['source'], 'update', update['prepared_closed'])
                self.journal.save_update(update)
                continue
            if update['state'] == 'new':
                update.update(result)
            elif update['state'] == 'posted' and result.get('image_path') and not update.get('image_path'):
                self.late_image(job, result, update['source']['id'], now)
            update['content_ready'] = True
            self.journal.save_update(update)

    def late_image(self, job, result, source_id, now):
        event = {'mint': job['mint'], 'source_call_id': job['call_id'],
                 'update': {'id': 'image-' + source_id, 'createdAt': now,
                            'content': 'Image accompanying the project context.'}}
        if self.journal.add_update(event, now, 0):
            key = job['call_id'] + ':image-' + source_id
            row = next(u for u in self.journal.updates() if u['key'] == key)
            row.update({k: v for k, v in result.items() if k != 'text'})
            row['content_ready'] = True
            self.journal.save_update(row)

    def close(self):
        self.content.close()
