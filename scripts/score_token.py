#!/usr/bin/env python3
"""
Score a pump.fun launch with the fitted model (data/scoring_model.json):
how likely the studied wallet is to buy it, from the first ~2 slots
(~1 s) of trading after creation.

Usage:
  score_token.py <mint> [<mint> ...]
  score_token.py --creator <address> [--last N]   # score a creator's N latest launches

For each token prints the score, which band it falls in (top 5/10/20% of
launches as calibrated on the study period), each feature's contribution,
and the notable early wallets (team/veto) that drove it.
"""
import argparse
import json
import math
import os
import sys
import time
import http.cookiejar
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from build_early_features import window_features  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
WINDOW_S = 3

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def get(url, retries=8):
    for _ in range(retries):
        try:
            with _opener.open(url, timeout=20) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 2)) + 0.3)
                continue
            return None
        except Exception:
            time.sleep(2)
    return None


def early_trades(mint, created_ts):
    cursor = f"{'9' * 22}-{int((created_ts + WINDOW_S) * 1000)}"
    trades = []
    for _ in range(5):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}")
        if not d or "trades" not in d:
            break
        for t in d["trades"]:
            trades.append({"slot": int(t["slotIndexId"][:12]), "idx": int(t["slotIndexId"][12:]),
                           "user": t["userAddress"], "type": t["type"],
                           "sol": float(t["amountSol"] or 0), "tokens": float(t["baseAmount"] or 0),
                           "price": float(t["fillPriceSol"]) if t.get("fillPriceSol") else None})
        if not d["pagination"]["hasMore"]:
            break
        cursor = d["pagination"]["nextCursor"]
        time.sleep(0.4)
    trades.sort(key=lambda t: (t["slot"], t["idx"]))
    return trades


_CACHE = None


def coin_info(mint):
    """creator + created_timestamp for a mint. pump.fun retired its single-coin route, so: local cache
    of followed creators' launches first, then a binary search for the mint's first trade (the dev buy)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {}
        d = os.path.join(DATA_DIR, "creator_launches_full")
        for fn in os.listdir(d) if os.path.isdir(d) else []:
            for c in json.load(open(os.path.join(d, fn))):
                _CACHE[c["mint"]] = c
    if mint in _CACHE:
        return _CACHE[mint]

    def before(t_ms):
        d = get(f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=1&cursor={'9' * 22}-{int(t_ms)}")
        time.sleep(0.3)
        return (d or {}).get("trades") or []

    hi = int(time.time() * 1000)
    if not before(hi):
        return None
    lo = hi - 60 * 86400 * 1000
    while hi - lo > 1000:  # smallest T with at least one trade strictly before it
        mid = (lo + hi) // 2
        if before(mid):
            hi = mid
        else:
            lo = mid
    first = before(hi)[0]
    return {"mint": mint, "creator": first["userAddress"], "name": None, "creator_inferred": True,
            "created_timestamp": int(hi // 1000) * 1000 - 1000}


def score_launch(coin, model):
    created_ts = coin["created_timestamp"] / 1000
    tr = early_trades(coin["mint"], created_ts)
    if not tr:
        return {"mint": coin["mint"], "error": "aucun trade trouvé"}
    s0 = tr[0]["slot"]
    f = window_features(tr, coin["creator"], s0, model["horizon_slots"])
    ws = model["wallet_scores"]
    vals = {b: ws[b] for b in f["buyers"] if b in ws}
    x = {
        "log_price_chg": math.log1p(max(0.0, f["price_change_pct"])),
        "has_3sol_buy": float(any(2.9 <= s < 3.1 for s in f["buy_sizes"])),
        "wallet_pos": sum(v for v in vals.values() if v > 0),
        "wallet_neg": -sum(v for v in vals.values() if v < 0),
    }
    contrib = {}
    z = model["intercept"]
    for i, k in enumerate(model["features"]):
        c = model["coef"][i] * (x[k] - model["mean"][i]) / model["std"][i]
        contrib[k] = c
        z += c
    p = 1 / (1 + math.exp(-z))
    vetoed = sorted(b for b in f["buyers"] if b in set(model.get("hard_veto_wallets", [])))
    if vetoed:
        p *= model.get("veto_factor", 0.05)
    th = model["thresholds"]
    band = ("top 5%" if p >= th["top_5pct"] else "top 10%" if p >= th["top_10pct"]
            else "top 20%" if p >= th["top_20pct"] else "hors top 20%")
    return {
        "mint": coin["mint"], "name": coin.get("name"), "creator": coin["creator"],
        "creator_followed": coin["creator"] in set(model["creators"]),
        "score": round(p, 4), "band": band,
        "features": {k: round(v, 3) for k, v in x.items()},
        "raw": {"price_change_pct": round(f["price_change_pct"], 1), "sol_bought_by_others": round(f["sol_buys"], 3),
                "n_buyers": f["n_buyers"], "dev_buy_sol": round(f["dev_buy_sol"], 3)},
        "contributions": {k: round(v, 3) for k, v in contrib.items()},
        "team_wallets": {b[:8]: round(v, 2) for b, v in sorted(vals.items(), key=lambda kv: -kv[1]) if v > 0.5},
        "veto_wallets": {b[:8]: round(v, 2) for b, v in sorted(vals.items(), key=lambda kv: kv[1]) if v < -0.5},
        "hard_veto": [b[:8] for b in vetoed],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mints", nargs="*")
    ap.add_argument("--creator")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    model = json.load(open(os.path.join(DATA_DIR, "scoring_model.json")))

    coins = []
    if a.creator:
        coins = get(f"https://frontend-api-v3.pump.fun/coins?creator={a.creator}&limit={min(a.last, 70)}"
                    f"&offset=0&sort=created_timestamp&order=DESC") or []
    for m in a.mints:
        c = coin_info(m)
        if c:
            coins.append(c)
        else:
            print(f"{m}: aucun trade trouvé pour ce mint")

    out = []
    for c in coins:
        r = score_launch(c, model)
        out.append(r)
        if a.json:
            continue
        if "error" in r:
            print(f"{r['mint']}: {r['error']}")
            continue
        print(f"\n{r['mint']} ({r['name']}) — créateur suivi: {'oui' if r['creator_followed'] else 'NON'}")
        print(f"  score = {r['score']}  -> {r['band']}")
        print(f"  premières ~1 s: prix {r['raw']['price_change_pct']:+}% | {r['raw']['sol_bought_by_others']} SOL "
              f"achetés par {r['raw']['n_buyers']} wallets | dev buy {r['raw']['dev_buy_sol']} SOL")
        print("  contributions:", ", ".join(f"{k} {v:+.2f}" for k, v in r["contributions"].items()))
        if r["team_wallets"]:
            print("  wallets 'équipe' présents:", r["team_wallets"])
        if r["veto_wallets"]:
            print("  wallets 'veto' présents:", r["veto_wallets"])
        if r["hard_veto"]:
            print("  VETO DUR (le bot n'achète quasiment jamais quand ils sont là):", r["hard_veto"])
        time.sleep(0.4)
    if a.json:
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
