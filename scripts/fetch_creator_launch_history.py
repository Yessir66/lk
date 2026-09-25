#!/usr/bin/env python3
"""
Fetch the COMPLETE launch history of the wallet's most-followed creators
over the wallet's observed window, using pump.fun's offset pagination
(70 per page). Gives exact bought/skipped denominators per creator.

Usage: fetch_creator_launch_history.py [min_buys_per_creator]
Output: data/creator_launches_full/<creator>.json (list of coins)
"""
import json
import os
import sys
import time
import http.cookiejar
import urllib.request
import urllib.error
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
OUT_DIR = os.path.join(DATA_DIR, "creator_launches_full")
os.makedirs(OUT_DIR, exist_ok=True)

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def get(url, retries=8):
    for attempt in range(retries):
        try:
            with opener.open(url, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 2)) + 0.3)
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


def main():
    min_buys = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    creator_buys = Counter()
    for m in report["per_token"]:
        p = os.path.join(META_DIR, f"{m}.json")
        if os.path.exists(p):
            d = json.load(open(p))
            if "__error__" not in d:
                creator_buys[d["creator"]] += 1

    sigs = json.load(open(os.path.join(DATA_DIR, "signatures.json")))
    window_start = min(s["blockTime"] for s in sigs if s.get("blockTime"))

    creators = [c for c, n in creator_buys.most_common() if n >= min_buys]
    print(f"{len(creators)} créateurs avec >= {min_buys} achats; fenêtre depuis {window_start}", flush=True)

    for i, c in enumerate(creators, 1):
        out = os.path.join(OUT_DIR, f"{c}.json")
        if os.path.exists(out):
            print(f"[{i}/{len(creators)}] {c}: déjà en cache", flush=True)
            continue
        coins, offset = [], 0
        while True:
            page = get(f"https://frontend-api-v3.pump.fun/coins?creator={c}&limit=70&offset={offset}"
                       f"&sort=created_timestamp&order=DESC")
            if not page:
                break
            coins.extend(page)
            oldest = min(x["created_timestamp"] for x in page) / 1000
            offset += len(page)
            if oldest < window_start or len(page) < 70:
                break
            time.sleep(1.1)
        json.dump(coins, open(out, "w"))
        in_window = sum(1 for x in coins if x["created_timestamp"] / 1000 >= window_start)
        print(f"[{i}/{len(creators)}] {c}: {len(coins)} lancements récupérés ({in_window} dans la fenêtre), "
              f"{creator_buys[c]} achetés par le wallet", flush=True)
        time.sleep(1.1)


if __name__ == "__main__":
    main()
