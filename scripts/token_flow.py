#!/usr/bin/env python3
"""
One token's chart read through scanner-style indicators, from a trigger (e.g. a wallet's buy), and a
"climax + retest" exit replayed on it second by second, without look-ahead.

Indicators, every second, over trailing windows (all trades from swap-api.pump.fun: pump.fun curve + PumpSwap):
  MC, liquidity (2 x SOL reserve: virtual reserve on the curve, sqrt(k x price) on PumpSwap, k from large
  buys' price impact), volume 1 min / 5 min, acceleration = 5 x vol 1 min / vol 5 min (1 = steady pace),
  swaps and buys/sells counts (5 min, 1 min), $ bought / $ sold (1 min), new wallets (1 min), mean buy size,
  round-trip share (volume of wallets that bought and sold within the minute), price change over 1 min.

Exit "climax + retest" (sells decided at second s fill at the price at s + 2 s, -2% slippage, 2.25% fee/side):
  1. climax: acceleration >= 2, price +50% in 1 min and >= 2x entry -> sell half;
  2. the climax high = highest price in the minute that follows;
  3. sell the rest when the price comes back to 90% of that high, at least 90 s after the climax;
  4. safety: sell everything 50% under the climax high, if entry is lost before any climax (-50%), or after 40 min.

Usage: token_flow.py <mint> <trigger UTC "YYYY-MM-DD HH:MM:SS"> [--minutes 32] [--before 5] [--tz 2] [--plot out.png]
Data: data/tokens/<mint>.json (all trades of the window)
"""
import argparse
import bisect
import calendar
import collections
import json
import math
import os
import time
import urllib.request

FEE, SLIP, DELAY = 0.0225, 0.02, 2


def utc(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def fetch(mint, a, b):
    path = os.path.join(os.path.dirname(__file__), "..", "data", "tokens", f"{mint}_{a}_{b}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return json.load(open(path))
    cursor, out = f"{'9' * 22}-{b * 1000}", []
    while True:
        for k in range(6):
            try:
                d = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://swap-api.pump.fun/v2/coins/{mint}/trades?limit=100&cursor={cursor}",
                    headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
                break
            except Exception:
                time.sleep(2 + 2 * k)
        else:
            raise SystemExit("swap-api.pump.fun en échec")
        out += d["trades"]
        if not d["trades"] or not d["pagination"]["hasMore"]:
            break
        cursor = d["pagination"]["nextCursor"]
        if int(cursor.split("-")[1]) / 1000 < a:
            break
        time.sleep(0.3)
    rows = []
    for t in out:
        s = calendar.timegm(time.strptime(t["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
        if a <= s <= b and t.get("priceUsd") and t.get("priceSol"):
            rows.append(dict(id=t["slotIndexId"], t=s, buy=t["type"] == "buy", usd=float(t["amountUsd"] or 0),
                             sol=float(t["amountSol"] or 0), px=float(t["priceUsd"]), pxs=float(t["priceSol"]),
                             amm=t["program"] == "pump_amm", user=t["userAddress"]))
    rows.sort(key=lambda r: r["id"])
    json.dump(rows, open(path, "w"))
    return rows


def indicators(rows, t0, t1):
    ts = [r["t"] for r in rows]
    sol_usd = sorted(r["px"] / r["pxs"] for r in rows[:200])[min(100, len(rows) // 2)]
    amm = [r for r in rows if r["amm"]]
    imp = [(b["t"], (b["sol"] / (math.sqrt(b["pxs"] / a["pxs"]) - 1)) ** 2 / a["pxs"])
           for a, b in zip(amm, amm[1:]) if b["buy"] and b["sol"] > 1.0 and b["pxs"] > a["pxs"] * 1.0001]
    imp_t, imp_k = [x[0] for x in imp], [x[1] for x in imp]
    first_seen = {}
    for r in rows:
        first_seen.setdefault(r["user"], r["t"])
    out = {}
    for s in range(t0, t1 + 1):
        j = bisect.bisect_right(ts, s)
        if j == 0:
            continue
        last = rows[j - 1]
        w1, w5 = rows[bisect.bisect_right(ts, s - 60):j], rows[bisect.bisect_right(ts, s - 300):j]
        if last["amm"] and imp_k:
            lo, hi = bisect.bisect_left(imp_t, s - 300), bisect.bisect_right(imp_t, s)
            ks = sorted(imp_k[lo:hi] or imp_k[:max(hi, 1)])
            reserve = math.sqrt(ks[len(ks) // 2] * last["pxs"])
        else:
            reserve = math.sqrt(last["pxs"] * 1e9 * 32.19)   # pump.fun curve: virtual SOL^2 = MC in SOL x 30 x 1.073
        buys1 = [r["usd"] for r in w1 if r["buy"]]
        sides = collections.defaultdict(set)
        for r in w1:
            sides[r["user"]].add(r["buy"])
        k1 = bisect.bisect_right(ts, s - 60) - 1
        out[s] = dict(
            mc=last["px"] * 1e9, liq=2 * reserve * sol_usd,
            vol1=sum(r["usd"] for r in w1), vol5=sum(r["usd"] for r in w5),
            swaps5=len(w5), buys5=sum(r["buy"] for r in w5), sells5=sum(not r["buy"] for r in w5),
            buys1=len(buys1), sells1=sum(not r["buy"] for r in w1),
            bvol1=sum(buys1), svol1=sum(r["usd"] for r in w1 if not r["buy"]),
            new1=len({r["user"] for r in w1 if r["buy"] and first_seen[r["user"]] > s - 60}),
            avgbuy=sum(buys1) / max(len(buys1), 1),
            rt=sum(r["usd"] for r in w1 if len(sides[r["user"]]) == 2) / max(sum(r["usd"] for r in w1), 1),
            chg1=last["px"] / (rows[k1]["px"] if k1 >= 0 else last["px"]) - 1)
        out[s]["acc"] = 5 * out[s]["vol1"] / max(out[s]["vol5"], 1)
    return out


def replay(ind, t0, t1, algo):
    """returns (net multiple of the stake, [(time, fraction, gross multiple)])"""
    entry = ind[t0]["mc"]
    st = dict(peak=1.0, max_vol5=0.0, max_new=0, mem={})
    left, cash, log = 1.0, 0.0, []
    for s in range(t0 + 1, t1):
        a = ind[s]
        x = a["mc"] / entry
        st["peak"], st["max_vol5"], st["max_new"] = max(st["peak"], x), max(st["max_vol5"], a["vol5"]), max(st["max_new"], a["new1"])
        st.update(s=s, x=x, a=a, t0=t0)
        f = min(left, algo(st))
        if f > 1e-9:
            fx = ind[min(s + DELAY, t1)]["mc"] / entry
            cash, left = cash + f * fx * (1 - SLIP), left - f
            log.append((s, f, fx))
            if left <= 1e-9:
                break
    if left > 1e-9:
        fx = ind[t1]["mc"] / entry
        cash += left * fx * (1 - SLIP)
        log.append((t1, left, fx))
    return cash * (1 - FEE) / (1 + FEE), log


def climax_retest(acc=2.0, chg=0.5, mult=2.0, first=0.5, retest=0.9, wait=90, stop=0.5, tmax=40):
    def algo(st):
        m, x, a = st["mem"], st["x"], st["a"]
        if not m.get("climax"):
            if a["acc"] >= acc and a["chg1"] >= chg and x >= mult:
                m["climax"], m["hi"] = st["s"], x
                return first
            return 1.0 if (x <= 0.5 or st["s"] - st["t0"] > tmax * 60) else 0.0
        if st["s"] - m["climax"] < 60:
            m["hi"] = max(m["hi"], x)
            return 0.0
        if st["s"] - m["climax"] >= wait and x >= retest * m["hi"]:
            return 1.0
        return 1.0 if (x <= (1 - stop) * m["hi"] or st["s"] - st["t0"] > tmax * 60) else 0.0
    return algo


def ladder(st):
    m, x = st["mem"], st["x"]
    if not m.get("a") and x >= 1.5:
        m["a"] = 1
        return 0.4
    if m.get("a") and not m.get("b") and x >= 2.0:
        m["b"] = 1
        return 0.3
    return 1.0 if (st["peak"] >= 2 and x <= 0.6 * st["peak"]) else 0.0


def trailing(st):
    return 1.0 if (st["peak"] >= 1.5 and st["x"] <= 0.7 * st["peak"]) else 0.0


def plot(ind, t0, t1, logs, tz, path):
    import datetime as dt
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    C = dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", grid="#e4e3df", s1="#2a78d6", s2="#eb6834", s3="#1baf7a", s7="#6250d6")
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": C["grid"], "axes.labelcolor": C["ink2"], "xtick.color": C["ink2"],
                         "ytick.color": C["ink2"], "axes.facecolor": C["surface"], "figure.facecolor": C["surface"]})
    D = lambda s: dt.datetime.fromtimestamp(s + tz * 3600, dt.UTC).replace(tzinfo=None)
    xs = [s for s in range(t0 - 60, t1 + 1, 5) if s in ind]
    fig, ax = plt.subplots(4, 1, figsize=(11, 9.5), sharex=True, gridspec_kw=dict(height_ratios=[2.6, 1.2, 1.2, 1.2], hspace=0.25))
    for a in ax:
        a.grid(axis="y", color=C["grid"], lw=0.6)
        a.spines[["top", "right"]].set_visible(False)
        a.axvline(D(t0), color=C["ink2"], lw=0.6, ls=":")
    ax[0].plot([D(s) for s in xs], [ind[s]["mc"] / 1e3 for s in xs], color=C["s1"], lw=1.1)
    ax[0].set_ylabel("Market cap (k$)")
    for t, f, x in logs:
        v = x * ind[t0]["mc"] / 1e3
        ax[0].plot(D(t + DELAY), v, "o", ms=7, color=C["s2"], mec=C["surface"], mew=1.5, zorder=5)
        ax[0].annotate(f"vend {int(round(100 * f))} %\n{v:.0f}K", (D(t + DELAY), v), xytext=(8, 10), textcoords="offset points", fontsize=8.5, color=C["ink"])
    ax[0].set_title("Signaux de l'algorithme « climax + retest » (points orange = ventes)", loc="left", fontsize=10.5, color=C["ink"])
    ax[1].plot([D(s) for s in xs], [ind[s]["acc"] for s in xs], color=C["s7"], lw=1.2)
    ax[1].axhline(2, color=C["ink2"], lw=0.7, ls="--")
    ax[1].set_ylabel("Accélération\n5 × vol 1 min / vol 5 min")
    ax[2].plot([D(s) for s in xs], [ind[s]["new1"] for s in xs], color=C["s3"], lw=1.2)
    ax[2].set_ylabel("Nouveaux wallets\n/ minute")
    ax[3].plot([D(s) for s in xs], [ind[s]["avgbuy"] for s in xs], color=C["s1"], lw=1.2)
    ax[3].set_ylabel("Achat moyen\n1 min ($)")
    ax[3].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax[3].xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, 2)))
    fig.savefig(path, dpi=150, bbox_inches="tight")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mint")
    ap.add_argument("trigger", help='UTC, "YYYY-MM-DD HH:MM:SS"')
    ap.add_argument("--minutes", type=int, default=32, help="replay length after the trigger")
    ap.add_argument("--before", type=int, default=5, help="minutes of history before the trigger")
    ap.add_argument("--tz", type=int, default=2, help="display offset from UTC, hours")
    ap.add_argument("--plot")
    a = ap.parse_args()
    trig = utc(a.trigger)
    t0, t1 = trig + 1, trig + 60 * a.minutes
    rows = fetch(a.mint, trig - 60 * a.before, t1 + 60)
    ind = indicators(rows, trig - 60, t1)
    L = lambda s: time.strftime("%H:%M:%S", time.gmtime(s + a.tz * 3600))
    print(f"{len(rows)} trades; entrée {L(t0)} à {ind[t0]['mc'] / 1e3:.1f}K de MC ; plus haut ensuite "
          f"x{max(ind[s]['mc'] for s in range(t0, t1)) / ind[t0]['mc']:.2f}")
    print("heure      MC    liq  vol1m  vol5m  accél swaps5 ach/ven5 ach/ven1 $a/$v1 nouv1m achat moy A/R%  var1m")
    for s in range(t0 - 1, t1 + 1, 30):
        r = ind[s]
        print(f"{L(s)} {r['mc'] / 1e3:6.1f}K {r['liq'] / 1e3:5.1f}K {r['vol1'] / 1e3:5.1f}K {r['vol5'] / 1e3:6.1f}K {r['acc']:5.2f} "
              f"{r['swaps5']:6d} {r['buys5'] / max(r['sells5'], 1):7.2f} {r['buys1'] / max(r['sells1'], 1):7.2f} "
              f"{r['bvol1'] / max(r['svol1'], 1):6.2f} {r['new1']:6d} {r['avgbuy']:7.0f}$ {100 * r['rt']:4.0f} {100 * r['chg1']:+5.0f}%")
    fmt = lambda log: ", ".join(f"{int(round(100 * f))}% à {L(t + DELAY)} ({x * ind[t0]['mc'] / 1e3:.0f}K)" for t, f, x in log)
    best_log = None
    for name, al in (("climax + retest", climax_retest()), ("paliers +50/+100 % + trailing -40 %", ladder), ("trailing -30 % dès x1,5", trailing)):
        m, log = replay(ind, t0, t1, al)
        best_log = best_log or log
        print(f"{name:38s} x{m:.2f} net ({100 * (m - 1):+.0f} %) : {fmt(log)}")
    if a.plot:
        plot(ind, t0, t1, best_log, a.tz, a.plot)


if __name__ == "__main__":
    main()
