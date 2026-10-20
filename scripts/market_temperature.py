#!/usr/bin/env python3
"""
Market "temperature" series for pump.fun, hourly: number of token launches (every pump.fun create references
the mint authority TSLvdd1p..., so its signature list is the list of launches) and the SOL price (GeckoTerminal
SOL/USDC pool, hourly candles). Resumable: launch counts are cached per hour as pages are read.

Usage: market_temperature.py --since YYYY-MM-DD
Output: data/market/launches_hourly.json {hour unix: count}, data/market/sol_hourly.json {hour unix: close USD}
"""
import argparse
import calendar
import collections
import json
import os
import time
import urllib.request

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "market")
MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
RPCS = ["https://api.mainnet-beta.solana.com", "https://solana-mainnet.gateway.tatum.io"]
SOL_POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"   # Raydium SOL/USDC


def rpc(method, params):
    for k in range(10):
        try:
            r = urllib.request.urlopen(urllib.request.Request(RPCS[k % 2], data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "curl/8.5.0"}), timeout=30)
            b = json.loads(r.read())
            if "result" in b:
                return b["result"]
        except Exception:
            pass
        time.sleep(min(2 ** k, 20))
    raise SystemExit("RPC en échec")


def launches(since):
    path = os.path.join(OUT, "launches_hourly.json")
    counts = collections.Counter({int(k): v for k, v in json.load(open(path)).items()}) if os.path.exists(path) else collections.Counter()
    state_p = os.path.join(OUT, "launches_state.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {"before": None, "oldest": None}
    before, n = state["before"], 0
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [MINT_AUTHORITY, p])
        if not page:
            break
        for s in page:
            if s.get("blockTime") and s["err"] is None:
                counts[s["blockTime"] // 3600 * 3600] += 1
        before = page[-1]["signature"]
        n += 1
        oldest = page[-1].get("blockTime") or 0
        if n % 20 == 0:
            json.dump(counts, open(path, "w"))
            json.dump({"before": before, "oldest": oldest}, open(state_p, "w"))
            print(f"  {n} pages, {time.strftime('%d/%m %H:%M', time.gmtime(oldest))}", flush=True)
        if oldest < since:
            break
        time.sleep(0.5)
    json.dump(counts, open(path, "w"))
    json.dump({"before": before, "oldest": oldest}, open(state_p, "w"))


def sol(since):
    out, before = {}, int(time.time())
    for _ in range(3):
        for k in range(10):
            try:
                d = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{SOL_POOL}/ohlcv/hour?aggregate=1&limit=1000&before_timestamp={before}",
                    headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
                break
            except Exception:
                time.sleep(30)
        else:
            raise SystemExit("GeckoTerminal en échec")
        rows = d["data"]["attributes"]["ohlcv_list"]
        for r in rows:
            out[int(r[0])] = r[4]
        if not rows or rows[-1][0] < since:
            break
        before = rows[-1][0]
        time.sleep(2.5)
    json.dump(out, open(os.path.join(OUT, "sol_hourly.json"), "w"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    since = calendar.timegm(time.strptime(a.since, "%Y-%m-%d"))
    launches(since)
    print("lancements ok", flush=True)
    sol(since)
    print("fini", flush=True)
