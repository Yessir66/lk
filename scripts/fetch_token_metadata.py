#!/usr/bin/env python3
"""Fetch pump.fun token metadata (creator, twitter/social link, creation
time, etc.) for every mint the wallet traded, to study *what* it selects
rather than *when*/*how much*. Cached + resumable, like the tx fetcher.
"""
import json
import os
import sys
import time
import http.cookiejar
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
os.makedirs(META_DIR, exist_ok=True)

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def fetch_coin(mint, retries=8):
    url = f"https://frontend-api-v3.pump.fun/coins/{mint}"
    for attempt in range(retries):
        try:
            with opener.open(url, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt, 15)
                time.sleep(wait + 0.3)
                continue
            if e.code == 404:
                return {"__error__": "not_found"}
            time.sleep(1.5)
        except Exception:
            time.sleep(1.5)
    return {"__error__": "failed_after_retries"}


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    mints = list(report["per_token"].keys())
    print(f"{len(mints)} mints à enrichir")
    done = 0
    for mint in mints:
        out_path = os.path.join(META_DIR, f"{mint}.json")
        done += 1
        if os.path.exists(out_path):
            continue
        data = fetch_coin(mint)
        with open(out_path, "w") as f:
            json.dump(data, f)
        if done % 10 == 0:
            print(f"  {done}/{len(mints)}", flush=True)
        time.sleep(1.1)
    print("done")


if __name__ == "__main__":
    main()
