#!/usr/bin/env python3
"""
One followed account's last N callouts on pump.fun tokens, taken apart one by one: what happened before
the callout (the author's own buys, the move of the minute before), our USD-sized buy DETECT seconds
after it (execution model of callout_backtest.py), the path while holding, the exit on a take profit
(else at the end of the 15 min window), the author's sells and the bots that bought within 3 s.

Usage: kol_callout_report.py <username> [--n 10] [--usd 100] [--tp 0.30] [--detect 1]
Output: data/kol_report_<username>.json (also per-second prices for charts)
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import callout_backtest as CB  # noqa: E402
from fetch_callout_paths import AFTER_S, fetch_path, get  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
API = "https://frontend-api-v3.pump.fun"
LATENCY, MANUAL_S, FAST_S = 2, 15, 3


def arg(name, default, cast):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def path_for(c):
    f = os.path.join(DATA, "callout_paths", f"{c['calloutId']}.json")
    if os.path.exists(f):
        return json.load(open(f))
    tr, complete_to = fetch_path(c["coinMint"], c["createdAt"] / 1000)
    d = {"calloutId": c["calloutId"], "mint": c["coinMint"], "t_call": c["createdAt"] / 1000,
         "complete_to": complete_to, "venues": sorted({t["venue"] for t in tr}), "trades": tr}
    if complete_to >= d["t_call"] + AFTER_S:  # never cache a path cut short by a failed request
        json.dump(d, open(f, "w"))
    return d


def series(p, t0, t1, solusd):
    """Spot market cap (USD, 1e9 supply) at each second from t0 to t1 (state at the end of the second)."""
    out = []
    for s in range(int(t0), int(t1) + 1):
        i = int(np.searchsorted(p.ts, s, "right"))
        out.append(round(p.state(i)[0] * 1e9 * solusd))
    return out


def main():
    user = sys.argv[1].lstrip("@")
    n, usd, tp, detect = arg("--n", 10, int), arg("--usd", 100.0, float), arg("--tp", 0.30, float), arg("--detect", 1.0, float)
    following = {u["username"]: u for u in json.load(open(os.path.join(DATA, "callouts", "following.json")))}
    author = following[user]
    d = get(f"{API}/callout/list/{author['address']}?limit=40&sortBy=TIMESTAMP&sortOrder=DESC")
    if not d:
        raise SystemExit("liste des callouts indisponible (API)")
    calls = [c for c in d["callouts"] if c["createdAt"] / 1000 < time.time() - AFTER_S - 60
             and not c.get("perp") and not c["coinMint"].startswith("0x")]
    picked, skipped = [], []
    for c in calls:
        p = CB.Path(path_for(c))
        p.meta = {**c, "author": user, "author_address": author["address"]}
        (picked if p.ok else skipped).append(p)
        if len(picked) == n:
            break
    skipped = [s for s in skipped if s.t_call >= picked[-1].t_call]
    allp, _ = CB.load()
    clock = CB.Clock([q for q in allp if q.ok] + picked)
    rule = {"tp": tp, "sl": None, "trail": None, "max_s": AFTER_S}
    rows = []
    for p in reversed(picked):  # oldest first
        c = p.meta
        solusd = c["calloutPriceUsd"] / c["calloutPrice"]
        size = usd / solusd
        meta = get(f"{API}/coins-v2/{p.mint}", retries=2) or {}
        i_call = int(np.searchsorted(p.slot, clock.slot_at(p.t_call), "left"))
        p_call, amm_call = p.state(i_call)
        p_m60 = p.state(int(np.searchsorted(p.ts, p.t_call - 60, "left")))[0]
        me = author["address"]
        a_buys = [(round(clock.time_at(p.slot[j]) - p.t_call, 1), round(float(p.sol[j]), 3)) for j in range(len(p.user))
                  if p.user[j] == me and p.side[j]]
        a_sells = [(round(clock.time_at(p.slot[j]) - p.t_call, 1), round(float(p.sol[j]), 3)) for j in range(len(p.user))
                   if p.user[j] == me and not p.side[j]]
        s0, s3 = clock.slot_at(p.t_call), clock.slot_at(p.t_call + FAST_S)
        fast = {p.user[j] for j in range(len(p.user)) if p.side[j] and s0 <= p.slot[j] <= s3}
        def vol(a, b):
            m = (p.ts >= p.t_call + a) & (p.ts < p.t_call + b)
            return round(float(p.sol[m & p.side].sum()), 2), round(float(p.sol[m & ~p.side].sum()), 2)
        q = CB.Position(p, clock, detect, LATENCY, size)
        pnl, reason, hold = q.exit(rule)
        S_end = clock.slot_at(q.t_land + AFTER_S)
        live = q.S <= S_end
        P, S = q.P[live], q.S[live]
        jmax, jmin = (int(np.argmax(P)), int(np.argmin(P))) if len(P) else (None, None)
        manual = CB.Position(p, clock, MANUAL_S, LATENCY, size)
        m_pnl, m_reason, m_hold = manual.exit(rule)
        p_end = p.state(len(p.p))[0]
        rows.append({
            "calloutId": c["calloutId"], "mint": p.mint, "name": meta.get("name"), "symbol": meta.get("symbol"),
            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(p.t_call)), "t_call": p.t_call,
            "thesis": c.get("thesis"), "views": c.get("viewCount"), "sol_usd": round(solusd, 2),
            "venue_at_call": "PumpSwap" if amm_call else "courbe pump.fun",
            "mcap_call_usd": round(p_call * 1e9 * solusd), "pre60_pct": round(100 * (p_call / p_m60 - 1), 1),
            "author_buys": a_buys, "author_sells": a_sells, "fast_buyers_3s": len(fast),
            "flow_0_10s": vol(0, 10), "flow_10_60s": vol(10, 60), "flow_60_300s": vol(60, 300),
            "entry_delay_s": round(q.t_land - p.t_call, 2), "entry_vs_call_pct": round(100 * (q.p_in_spot / p_call - 1), 1),
            "mcap_entry_usd": round(q.p_in_spot * 1e9 * solusd), "size_sol": round(size, 4),
            "max_pct": round(100 * (P[jmax] / q.entry_px - 1), 1) if jmax is not None else None,
            "max_at_s": round(clock.time_at(S[jmax]) - p.t_call) if jmax is not None else None,
            "min_pct": round(100 * (P[jmin] / q.entry_px - 1), 1) if jmin is not None else None,
            "min_at_s": round(clock.time_at(S[jmin]) - p.t_call) if jmin is not None else None,
            "exit_reason": reason, "hold_s": round(hold), "exit_at_s": round(q.t_land - p.t_call + hold, 1),
            "tp_mcap_usd": round(q.entry_px * (1 + tp) * 1e9 * solusd), "pnl_usd": round(pnl * solusd, 2),
            "pnl_pct": round(100 * pnl / size, 1),
            "manual_pnl_usd": round(m_pnl * solusd, 2), "manual_reason": m_reason,
            "mcap_end_usd": round(p_end * 1e9 * solusd), "end_vs_call_pct": round(100 * (p_end / p_call - 1), 1),
            "series_t0": int(p.t_call) - 60, "series_mcap": series(p, p.t_call - 60, min(p.t_call + AFTER_S, p.complete_to), solusd),
        })
    report = {"author": user, "followers": author["followers"], "usd": usd, "tp": tp, "detect_s": detect,
              "latency_slots": LATENCY, "window_s": AFTER_S, "rows": rows,
              "skipped": [{"mint": s.mint, "time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(s.t_call)),
                           "scope": s.scope} for s in skipped]}
    json.dump(report, open(os.path.join(DATA, f"kol_report_{user}.json"), "w"), indent=1)
    print(f"{user}: {len(rows)} derniers callouts sur pump.fun, entrée {usd:.0f} $ {detect} s après le call, sortie à +{100 * tp:.0f}%")
    for s in report["skipped"]:
        print(f"  (ignoré {s['time_utc']} {s['mint'][:10]}…: {s['scope']})")
    for r in rows:
        print(f"\n{r['time_utc']}  ${r['symbol']}  mcap au call {r['mcap_call_usd']:,} $ ({r['venue_at_call']}), "
              f"{r['views']} vues, avant le call {r['pre60_pct']:+.0f}% sur 60 s")
        print(f"  KOL: achats {r['author_buys']} | ventes {r['author_sells']} (s, SOL)")
        print(f"  {r['fast_buyers_3s']} wallets achètent dans les 3 s; flux achats/ventes SOL 0-10 s {r['flow_0_10s']}, "
              f"10-60 s {r['flow_10_60s']}, 1-5 min {r['flow_60_300s']}")
        print(f"  entrée à +{r['entry_delay_s']} s: {r['entry_vs_call_pct']:+.1f}% vs call (mcap {r['mcap_entry_usd']:,} $) | "
              f"max {r['max_pct']:+.0f}% à {r['max_at_s']} s, min {r['min_pct']:+.0f}% à {r['min_at_s']} s")
        print(f"  sortie: {r['exit_reason']} après {r['hold_s']} s -> {r['pnl_usd']:+.2f} $ ({r['pnl_pct']:+.1f}%) | "
              f"à la main (+{MANUAL_S} s): {r['manual_pnl_usd']:+.2f} $ | 15 min après: {r['end_vs_call_pct']:+.0f}% vs call")
    tot, man = sum(r["pnl_usd"] for r in rows), sum(r["manual_pnl_usd"] for r in rows)
    print(f"\nTotal: {tot:+.2f} $ pour {usd * len(rows):.0f} $ engagés ({sum(r['pnl_usd'] > 0 for r in rows)}/{len(rows)} gagnants); "
          f"à la main: {man:+.2f} $")


if __name__ == "__main__":
    main()
