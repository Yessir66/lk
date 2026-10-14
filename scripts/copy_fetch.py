#!/usr/bin/env python3
"""
Data for a copy-trading study of one wallet (any Solana wallet trading memecoins).

Steps (each one resumable, cached under data/wallet_<first 8 chars>/):
  sigs     getSignaturesForAddress back to --since
  txs      getTransaction for every successful signature (public RPCs, both rate-limited: ~1 tx/s)
  swaps    the wallet's token and SOL deltas per transaction -> swaps.json
  entry    trade-by-trade data from 5 s before to ~2 min after each buy (swap-api.pump.fun): the price a copy
           would get a few blocks later
  candles  minute candles in USD: pump.fun's own (last 1000 traded minutes), GeckoTerminal for tokens where
           that does not reach back to the wallet's first buy (~30 calls/min)
  all      every step in order

Usage: copy_fetch.py <wallet> <step> [--since YYYY-MM-DD]
"""
import argparse
import calendar
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

RPCS = [("https://solana-mainnet.gateway.tatum.io", 0.30, 2), ("https://api.mainnet-beta.solana.com", 0.9, 1)]
WSOL = "So11111111111111111111111111111111111111112"
NOT_MEME = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
            "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"}
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def data_dir(wallet):
    d = os.path.join(os.path.dirname(__file__), "..", "data", f"wallet_{wallet[:8]}")
    os.makedirs(d, exist_ok=True)
    return d


def http_json(url, body=None, headers=UA, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={**headers, "Content-Type": "application/json"} if body else headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def step_sigs(w, D, since):
    out, before = [], None
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        for a in range(8):
            try:
                page = http_json(RPCS[1][0], {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [w, p]})["result"]
                break
            except Exception:
                time.sleep(min(2 ** a, 20))
        else:
            raise SystemExit("getSignaturesForAddress en échec")
        if not page:
            break
        out += page
        before = page[-1]["signature"]
        print(f"  {len(out)} signatures, {time.strftime('%Y-%m-%d %H:%M', time.gmtime(page[-1]['blockTime']))}", flush=True)
        if len(page) < 1000 or page[-1]["blockTime"] < since:
            break
        time.sleep(0.5)
    json.dump([s for s in out if s["blockTime"] >= since], open(os.path.join(D, "sigs.json"), "w"))


def step_txs(w, D, since):
    os.makedirs(os.path.join(D, "tx"), exist_ok=True)
    todo = [s["signature"] for s in json.load(open(os.path.join(D, "sigs.json")))
            if s["err"] is None and s["blockTime"] >= since and not os.path.exists(os.path.join(D, "tx", s["signature"] + ".json"))]
    print(f"{len(todo)} transactions à récupérer", flush=True)
    q = queue.Queue()
    for s in todo:
        q.put((s, 0))
    done, lock = [0], threading.Lock()

    def worker(url, gap):
        while True:
            try:
                sig, tries = q.get(timeout=5)
            except queue.Empty:
                return
            ok = False
            try:
                r = http_json(url, {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}]},
                              headers={"User-Agent": "curl/8.5.0"})
                if r.get("result"):
                    json.dump(r["result"], open(os.path.join(D, "tx", sig + ".json"), "w"))
                    ok = True
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(2)
            except Exception:
                pass
            with lock:
                if ok:
                    done[0] += 1
                    if done[0] % 250 == 0:
                        print(f"  {done[0]}/{len(todo)}", flush=True)
                elif tries < 6:
                    q.put((sig, tries + 1))
            time.sleep(gap)

    th = [threading.Thread(target=worker, args=(u, g)) for u, g, n in RPCS for _ in range(n)]
    [t.start() for t in th]
    [t.join() for t in th]
    print(f"{done[0]} transactions récupérées", flush=True)


def parse_tx(w, tx):
    m = tx["meta"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    if w not in keys:
        return None
    i = keys.index(w)
    sol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9
    d = {}
    for side, sgn in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            if b.get("owner") == w:
                d[b["mint"]] = d.get(b["mint"], 0.0) + sgn * float(b["uiTokenAmount"]["uiAmount"] or 0)
    sol += d.pop(WSOL, 0.0)
    return {"sig": tx["transaction"]["signatures"][0], "slot": tx["slot"], "bt": tx["blockTime"], "sol": sol,
            "toks": {k: v for k, v in d.items() if abs(v) > 0}}


def step_swaps(w, D, since):
    out = []
    for f in os.listdir(os.path.join(D, "tx")):
        p = parse_tx(w, json.load(open(os.path.join(D, "tx", f))))
        if p:
            out.append(p)
    out.sort(key=lambda x: x["slot"])
    json.dump(out, open(os.path.join(D, "swaps.json"), "w"))
    print(f"{len(out)} transactions, {sum(1 for p in out if p['toks'])} avec un mouvement de token", flush=True)


def buys(D):
    out = []
    for p in json.load(open(os.path.join(D, "swaps.json"))):
        if len(p["toks"]) == 1 and p["sol"] < -0.1:
            (m, q), = p["toks"].items()
            if q > 0 and m not in NOT_MEME:
                out.append((m, p["bt"], p["sig"]))
    return out


class Paced:
    def __init__(self, gap):
        self.gap, self.last = gap, 0.0

    def get(self, url):
        for a in range(6):
            wait = self.gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                return http_json(url)
            except urllib.error.HTTPError as e:
                if e.code in (400, 404):
                    return None
                time.sleep(20 if e.code == 429 else 4)
            except Exception:
                time.sleep(4)
        return None


def step_entry(w, D, since):
    os.makedirs(os.path.join(D, "entry"), exist_ok=True)
    api = Paced(0.35)
    for m, bt, sig in sorted(buys(D), key=lambda b: -b[1]):
        f = os.path.join(D, "entry", f"{m}_{bt}.json")
        if os.path.exists(f):
            continue
        t_to = (bt + 90) // 60 * 60 + 60
        cursor, trades, complete = f"{'9' * 22}-{t_to * 1000}", [], False
        for _ in range(15):
            d = api.get(f"https://swap-api.pump.fun/v2/coins/{m}/trades?limit=100&cursor={cursor}")
            if not d or "trades" not in d:
                break
            trades += [{"slot": int(t["slotIndexId"][:12]), "idx": int(t["slotIndexId"][12:]), "user": t["userAddress"],
                        "type": t["type"], "prog": t.get("program"), "ts": t["timestamp"],
                        "usd": float(t["priceUsd"]) if t.get("priceUsd") else None,
                        "sol": float(t.get("amountSol") or 0), "tx": t["tx"]} for t in d["trades"]]
            if not d["pagination"]["hasMore"] or not d["trades"] or int(d["pagination"]["nextCursor"].split("-")[1]) / 1000 < bt - 5:
                complete = True
                break
            cursor = d["pagination"]["nextCursor"]
        trades.sort(key=lambda t: (t["slot"], t["idx"]))
        json.dump({"mint": m, "bt": bt, "sig": sig, "complete": complete, "trades": trades}, open(f, "w"))
    print("trades d'entrée à jour", flush=True)


def step_candles(w, D, since):
    by_mint = {}
    for m, bt, _ in buys(D):
        by_mint.setdefault(m, []).append(bt)
    os.makedirs(os.path.join(D, "pcandles"), exist_ok=True)
    pump = Paced(0.3)
    for m, bts in by_mint.items():
        f = os.path.join(D, "pcandles", m + ".json")
        if os.path.exists(f) and json.load(open(f))["fetched"] > max(bts) + 24 * 3600:
            continue
        d = pump.get(f"https://swap-api.pump.fun/v1/coins/{m}/candles?interval=1m&limit=1000")
        c = {str(int(x["timestamp"]) // 1000): [float(x[k]) for k in ("open", "high", "low", "close", "volume")]
             for x in (d or []) if isinstance(x, dict)}
        json.dump({"mint": m, "fetched": time.time(), "candles": c}, open(f, "w"))

    def covered(m):
        ks = sorted(int(k) for k in json.load(open(os.path.join(D, "pcandles", m + ".json")))["candles"])
        return bool(ks) and (len(ks) < 1000 or ks[0] <= min(by_mint[m]) - 120)

    os.makedirs(os.path.join(D, "ohlcv"), exist_ok=True)
    gecko, now = Paced(2.1), time.time()
    todo = [m for m in by_mint if not covered(m)]
    print(f"{len(by_mint)} tokens, {len(todo)} à compléter via GeckoTerminal", flush=True)
    for i, m in enumerate(todo, 1):
        f = os.path.join(D, "ohlcv", m + ".json")
        d = json.load(open(f)) if os.path.exists(f) else {"mint": m, "pools": None, "windows": [], "candles": {}}
        if d["pools"] is None:
            r = gecko.get(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{m}/pools?page=1")
            d["pools"] = [{"id": p["attributes"]["address"], "dex": p["relationships"]["dex"]["data"]["id"],
                           "reserve": float(p["attributes"].get("reserve_in_usd") or 0)} for p in (r or {}).get("data", [])]
        pools = [p for p in d["pools"] if p["dex"] in ("pump-fun", "pumpswap")][:2] or \
            sorted(d["pools"], key=lambda p: -p["reserve"])[:2]
        spans = []
        for t in sorted(by_mint[m]):
            if spans and t - spans[-1][1] <= 24 * 3600:
                spans[-1][1] = t
            else:
                spans.append([t, t])
        for a, b in spans:
            win = [a - 120, min(b + 24 * 3600, now)]
            if any(x[0] <= win[0] and x[1] >= win[1] - 60 for x in d["windows"]):
                continue
            for p in pools:
                c, before = d["candles"].setdefault(p["id"], {}), int(win[1])
                for _ in range(4):
                    r = gecko.get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{p['id']}/ohlcv/minute"
                                  f"?aggregate=1&before_timestamp={before}&limit=1000&currency=usd&token={m}")
                    rows = (r or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                    for k in rows:
                        c[str(k[0])] = k[1:]
                    if len(rows) < 1000 or rows[-1][0] <= win[0]:
                        break
                    before = rows[-1][0]
            d["windows"].append(win)
        json.dump(d, open(f, "w"))
        if i % 10 == 0:
            print(f"  GeckoTerminal {i}/{len(todo)}", flush=True)
    print("bougies à jour", flush=True)


STEPS = {"sigs": step_sigs, "txs": step_txs, "swaps": step_swaps, "entry": step_entry, "candles": step_candles}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("step", choices=list(STEPS) + ["all"])
    ap.add_argument("--since", default=time.strftime("%Y-%m-%d", time.gmtime(time.time() - 14 * 86400)))
    a = ap.parse_args()
    since = calendar.timegm(time.strptime(a.since, "%Y-%m-%d"))
    D = data_dir(a.wallet)
    for name in (STEPS if a.step == "all" else [a.step]):
        print(f"== {name}", flush=True)
        STEPS[name](a.wallet, D, since)
