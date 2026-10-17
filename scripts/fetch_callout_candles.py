#!/usr/bin/env python3
"""
Price paths after the callouts of the followed callers, for exit-rule backtests (callout_exit_search.py).

  fetch_callout_candles.py [--per-caller 40] [--days 14] [--callers <wallet>,...]

For each caller (default: callout_follow.CALLERS), its last --per-caller callouts on Solana coins posted within
--days days and at least 4 h ago (so the window is complete). For each callout: the coin's creation time
(pump.fun coins-v2) and USD price bars from pump.fun's candles API (GET /candles/{chain}/{mint}, at most 300 bars
a request): 1 s bars up to the call + 5 min, 15 s bars up to + 1 h, 1 min bars up to + 4 h. Bars only exist for
seconds with trades. About 1 request/s; resumes where it stopped.
Output: data/callout_candles.json
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from callout_follow import CALLERS, is_solana  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "callout_candles.json")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
API = "https://frontend-api-v3.pump.fun"
CHAIN = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PER_CALLER, DAYS = int(arg("--per-caller", "40")), float(arg("--days", "14"))
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
WINDOWS = [("1s", 300), ("15s", 3600), ("1m", 4 * 3600)]   # bar width, seconds after the call it reaches


def get(path):
    for i in range(6):
        time.sleep(1.1)
        try:
            req = urllib.request.Request(API + path, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=20, context=SSL))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return None
            time.sleep(5 * (i + 1))
        except Exception:
            time.sleep(3)
    return None


def callouts(wallet):
    out, token, now = [], "", time.time()
    while len(out) < PER_CALLER:
        d = get(f"/callout/list/{wallet}?limit=50&sortBy=TIMESTAMP&sortOrder=DESC" + (f"&pageToken={token}" if token else ""))
        if not d:
            break
        for c in d["callouts"]:
            age_s = now - c["createdAt"] / 1000
            if age_s > DAYS * 86400:
                return out[:PER_CALLER]
            if age_s > 4 * 3600 and is_solana(c["coinMint"]) and not c.get("chain") and not c.get("perp"):
                out.append(c)
        token = d.get("nextPageToken")
        if not token:
            break
    return out[:PER_CALLER]


def bars(mint, t0_ms):
    out = {}
    for res, horizon in WINDOWS:
        d = get(f"/candles/{CHAIN}/{mint}?res={res}&count=300&to={int(t0_ms + horizon * 1000)}")
        out[res] = [[b["openTimeMs"], float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])]
                    for b in (d or {}).get("candles") or []]
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    callers = arg("--callers", None)
    for wallet in (callers.split(",") if callers else CALLERS):
        cs = callouts(wallet)
        print(f"{wallet[:8]} : {len(cs)} callouts", flush=True)
        for c in cs:
            if c["calloutId"] in done:
                continue
            coin = get(f"/coins-v2/{c['coinMint']}") or {}
            done[c["calloutId"]] = {"caller": wallet, "mint": c["coinMint"], "t0": c["createdAt"],
                                    "mcap_call": c.get("marketCap"), "price_call": c.get("calloutPriceUsd"),
                                    "coin_created": coin.get("created_timestamp"), "bars": bars(c["coinMint"], c["createdAt"])}
            json.dump(done, open(OUT, "w"))
        print(f"  total {len(done)} callouts enregistrés", flush=True)


if __name__ == "__main__":
    main()
