#!/usr/bin/env python3
"""
Trade-by-trade path of the called-out token around each callout (data/callouts/callouts.json, from
fetch_kol_callouts.py): every trade from BEFORE_S before the callout to AFTER_S after it.

Source: pump.fun's frontend trade tape, GET /trades/<chain>/<mint>?after=<OrdinalKey>, which pages
forwards in time (oldest first) — unlike swap-api it is not limited to ~60 requests/min, and a token with
too many trades still has a complete start: `complete_to` is the time (s) up to which the path is
complete. priceQuote is the spot price after the trade (checked: fill = sqrt(spot before * spot after),
k = 30 * 1.073e9 on the curve, ~1.73e10 on the PumpSwap pool), quoteAmount the SOL entering or leaving
the pool.

Usage: fetch_callout_paths.py [--since YYYY-MM-DD] [--workers N]
Output: data/callout_paths/<calloutId>.json {calloutId, mint, t_call (s), complete_to, venues, trades}
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "callout_paths")
API = "https://frontend-api-v3.pump.fun/trades/solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BEFORE_S, AFTER_S, MAX_PAGES = 60, 900, 150
STATS = {"requests": 0, "429": 0}
_lock = threading.Lock()


def get(url, retries=8):
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]
    for _ in range(retries):
        with _lock:
            STATS["requests"] += 1
        try:
            with opener.open(url, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                with _lock:
                    STATS["429"] += 1
                time.sleep(float(e.headers.get("Retry-After", 2)) + 0.3)
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return None


def parse(t):
    b, q = t["baseAmount"], t["quoteAmount"]
    return {"slot": int(t["blockId"]), "idx": t["txIndex"] * 10000 + t["eventIndex"], "leg": t["legIndex"],
            "ts": t["blockTimeMs"] / 1000, "user": (t.get("trader") or {}).get("address"), "type": t["side"],
            "venue": t["venue"], "quote": (t.get("quote") or {}).get("id"),
            "sol": int(q["raw"]) / 10 ** q["decimals"], "tokens": int(b["raw"]) / 10 ** b["decimals"],
            "price_after": float(t["priceQuote"]) if t.get("priceQuote") else None}


def fetch_path(mint, t_call):
    t0, t_end = t_call - BEFORE_S, t_call + AFTER_S
    url = f"{API}/{mint}?limit=100&after=0-0-0-{int(t0 * 1000) - 1}"
    trades, complete_to = [], t0
    for _ in range(MAX_PAGES):
        d = get(url)
        if not d or "trades" not in d:
            break
        trades += [parse(t) for t in d["trades"] if t.get("kind") == "swap"]
        last = d["trades"][-1]["blockTimeMs"] / 1000 if d["trades"] else t0
        if not d.get("cursor") or len(d["trades"]) < 100 or last > t_end:
            complete_to = t_end
            break
        complete_to = last - 1  # trades of the last block second may continue on the next page
        url = f"{API}/{mint}?limit=100&cursor={d['cursor']}"
    trades = [t for t in trades if t["ts"] <= t_end]
    trades = sorted({(t["slot"], t["idx"], t["leg"]): t for t in trades}.values(),
                    key=lambda t: (t["slot"], t["idx"], t["leg"]))
    return trades, min(complete_to, t_end)


def work(r):
    t_call = r["createdAt"] / 1000
    try:
        tr, complete_to = fetch_path(r["coinMint"], t_call)
        out = os.path.join(OUT, f"{r['calloutId']}.json")
        json.dump({"calloutId": r["calloutId"], "mint": r["coinMint"], "t_call": t_call,
                   "complete_to": complete_to, "venues": sorted({t["venue"] for t in tr}), "trades": tr},
                  open(out + ".tmp", "w"))
        os.replace(out + ".tmp", out)
        return True
    except Exception as e:
        print(f"  ERREUR {r['calloutId']}: {e}", flush=True)
        return False


def main():
    os.makedirs(OUT, exist_ok=True)
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2000-01-01"
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
    since_ms = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc).timestamp() * 1000
    rows = json.load(open(os.path.join(DATA, "callouts", "callouts.json")))
    now_ms = time.time() * 1000
    todo = [r for r in rows if since_ms <= r["createdAt"] < now_ms - (AFTER_S + 60) * 1000
            and not r.get("perp") and r.get("chain") in (None, "solana")
            and not os.path.exists(os.path.join(OUT, f"{r['calloutId']}.json"))]
    todo.sort(key=lambda r: r["calloutId"])  # unrelated to time or author: a partial fetch is a random sample
    print(f"{len(todo)} callouts à récupérer", flush=True)
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(work, todo), 1):
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}  {STATS['requests']} requêtes, {STATS['429']} x 429", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
