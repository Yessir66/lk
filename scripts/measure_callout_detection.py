#!/usr/bin/env python3
"""
How soon after its creation can a bot see a callout? Polls, for DURATION_S seconds:
  - GET /home-feed/new (every callout passing the feed's floors, all users) every FEED_EVERY_S;
  - GET /callout/list/<address>?limit=1 for the most active followed accounts, each every LIST_EVERY_S;
and records, for each callout seen, first-seen time minus its creation time (server clock; ours is
NTP-synced, the gap is checked against the HTTP Date header).

Usage: measure_callout_detection.py [--minutes N]
Output: data/callout_detection.json
"""
import datetime as dt
import email.utils
import json
import os
import sys
import threading
import time
import urllib.request

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
API = "https://frontend-api-v3.pump.fun"
FEED_EVERY_S, LIST_EVERY_S, N_AUTHORS = 1.0, 2.0, 5
seen, lock = {}, threading.Lock()
clock_skew = []


def fetch(url):
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]
    t0 = time.time()
    with op.open(url, timeout=10) as r:
        body = json.load(r)
        date = r.headers.get("Date")
    t1 = time.time()
    if date:
        clock_skew.append(email.utils.parsedate_to_datetime(date).timestamp() - (t0 + t1) / 2)
    return body, t1


def record(cid, created, t_seen, source, author):
    with lock:
        key = (cid, source)
        if key not in seen:
            seen[key] = {"calloutId": cid, "source": source, "author": author, "created": created,
                         "seen": t_seen, "delay_s": t_seen - created}


def poll_feed(stop):
    first = True
    while not stop.is_set():
        try:
            d, t = fetch(f"{API}/home-feed/new")
            for c in d.get("coins", []):
                co = (c.get("position") or {}).get("callout") or {}
                if co.get("calloutTimestamp") and not first:
                    created = dt.datetime.fromisoformat(co["calloutTimestamp"].replace("Z", "+00:00")).timestamp()
                    record(co["calloutId"], created, t, "home-feed/new", c["position"].get("userName"))
                elif co.get("calloutId"):
                    seen.setdefault((co["calloutId"], "home-feed/new"), None)  # already there at start
            first = False
        except Exception:
            pass
        stop.wait(FEED_EVERY_S)


def poll_author(stop, a):
    known = None
    while not stop.is_set():
        try:
            d, t = fetch(f"{API}/callout/list/{a['address']}?limit=1&sortBy=TIMESTAMP&sortOrder=DESC")
            cs = d.get("callouts") or []
            if cs:
                if known is not None and cs[0]["calloutId"] != known:
                    record(cs[0]["calloutId"], cs[0]["createdAt"] / 1000, t, "callout/list", a["username"])
                known = cs[0]["calloutId"]
        except Exception:
            pass
        stop.wait(LIST_EVERY_S)


def main():
    minutes = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 60
    rows = json.load(open(os.path.join(DATA, "callouts", "callouts.json")))
    following = {u["username"]: u for u in json.load(open(os.path.join(DATA, "callouts", "following.json")))}
    recent = [r for r in rows if r["createdAt"] / 1000 > time.time() - 7 * 86400]
    counts = {}
    for r in recent:
        counts[r["author"]] = counts.get(r["author"], 0) + 1
    authors = [following[a] for a, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:N_AUTHORS]]
    print("auteurs suivis:", ", ".join(a["username"] for a in authors), flush=True)
    stop = threading.Event()
    ths = [threading.Thread(target=poll_feed, args=(stop,))] + \
          [threading.Thread(target=poll_author, args=(stop, a)) for a in authors]
    for th in ths:
        th.start()
    end = time.time() + 60 * minutes
    while time.time() < end:
        time.sleep(30)
        got = [v for v in seen.values() if v]
        print(f"  {len(got)} callouts nouveaux vus", flush=True)
    stop.set()
    for th in ths:
        th.join()
    got = sorted((v for v in seen.values() if v), key=lambda v: v["seen"])
    skew = sorted(clock_skew)[len(clock_skew) // 2] if clock_skew else None
    for src in ("home-feed/new", "callout/list"):
        ds = sorted(v["delay_s"] for v in got if v["source"] == src)
        if ds:
            print(f"{src}: {len(ds)} callouts, délai création -> vu: min {ds[0]:.1f} s, médiane {ds[len(ds) // 2]:.1f} s, "
                  f"max {ds[-1]:.1f} s", flush=True)
    print(f"écart d'horloge serveur - local (en-tête Date, résolution 1 s): {skew:+.2f} s" if skew is not None else "", flush=True)
    json.dump({"authors": [a["username"] for a in authors], "clock_skew_s": skew, "rows": got},
              open(os.path.join(DATA, "callout_detection.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
