#!/usr/bin/env python3
"""
Fetch every trade in the first WINDOW_S seconds of each sampled launch from
pump.fun's swap-api, seeking directly to creation time with a forged
keyset cursor ("<slotIndexId>-<timestamp_ms>", ordered by timestamp).
Most launches need a single call.

Sample (case-control, per creator): every launch the wallet bought or
attempted + up to NEG_PER_POS skipped launches per positive (min
MIN_NEG), drawn from periods when the bot was active (it bought
something within +/-30 min).

Output: data/early_trades/<mint>.json
"""
import bisect
import json
import os
import random
import time
import http.cookiejar
import urllib.request
import urllib.error
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_DIR = os.path.join(DATA_DIR, "early_trades")
os.makedirs(OUT_DIR, exist_ok=True)

WINDOW_S = 8
NEG_PER_POS = 3
MIN_NEG = 20
MAX_PAGES = 8
SEED = 42

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def get(url, retries=8):
    for _ in range(retries):
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


def build_sample():
    rows = json.load(open(os.path.join(DATA_DIR, "launch_labels.json")))
    ledger = json.load(open(os.path.join(DATA_DIR, "wallet_ledger.json")))
    buy_times = sorted(v["first_buy_bt"] for v in ledger.values())

    def active(t, win=1800):
        i = bisect.bisect_left(buy_times, t - win)
        return i < len(buy_times) and buy_times[i] <= t + win

    by_creator = defaultdict(lambda: {"pos": [], "neg": []})
    for r in rows:
        if r["status"] in ("bought", "attempted_failed"):
            by_creator[r["creator"]]["pos"].append(r)
        elif active(r["created_ts"]):
            by_creator[r["creator"]]["neg"].append(r)

    rng = random.Random(SEED)
    sample = []
    for c, g in by_creator.items():
        n_neg = min(len(g["neg"]), max(MIN_NEG, NEG_PER_POS * len(g["pos"])))
        sample += g["pos"] + rng.sample(g["neg"], n_neg)
    return sample


def fetch(mint, created_ts):
    cursor = f"{'9' * 22}-{int((created_ts + WINDOW_S) * 1000)}"
    trades, pages, complete = [], 0, False
    while pages < MAX_PAGES:
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        pages += 1
        if not d or "trades" not in d:
            break
        for t in d["trades"]:
            trades.append({
                "slot": int(t["slotIndexId"][:12]), "idx": int(t["slotIndexId"][12:]),
                "ts": t["timestamp"], "user": t["userAddress"], "type": t["type"],
                "sol": float(t["amountSol"]), "tokens": float(t["baseAmount"]),
                "price": float(t["fillPriceSol"]) if t.get("fillPriceSol") else None,
                "tx": t["tx"], "program": t.get("program"),
            })
        if not d["pagination"]["hasMore"]:
            complete = True
            break
        cursor = d["pagination"]["nextCursor"]
        time.sleep(0.4)
    trades.sort(key=lambda t: (t["slot"], t["idx"]))
    return {"mint": mint, "created_ts": created_ts, "complete": complete, "pages": pages, "trades": trades}


def main():
    sample = build_sample()
    n_pos = sum(1 for r in sample if r["status"] != "skipped")
    print(f"échantillon: {len(sample)} lancements ({n_pos} positifs, {len(sample) - n_pos} ignorés)", flush=True)
    json.dump(sample, open(os.path.join(DATA_DIR, "early_sample.json"), "w"))
    done = incomplete = 0
    for r in sample:
        out = os.path.join(OUT_DIR, f"{r['mint']}.json")
        done += 1
        if os.path.exists(out):
            continue
        res = fetch(r["mint"], r["created_ts"])
        json.dump(res, open(out, "w"))
        if not res["complete"]:
            incomplete += 1
        if done % 50 == 0:
            print(f"  {done}/{len(sample)} ({incomplete} incomplets)", flush=True)
        time.sleep(0.4)
    print(f"done ({incomplete} incomplets)", flush=True)


if __name__ == "__main__":
    main()
