#!/usr/bin/env python3
"""
Unbiased sample of ALL pump.fun launches. Every create transaction references pump.fun's mint
authority TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM (buys and sells do not), so its signatures are
the list of launches (~30k/day). Page them back to the start of the study window, count launches
per hour, and keep a deterministic ~0.6% sample (sha256 of the signature), reproducible and unbiased.

Usage: sample_launches.py [--dense RATE_PER_10000 FROM_TS TO_TS]
Output: data/launch_census.json {hour_counts: {hour_ts: n}, sample: [{sig, slot, bt}], rate}
--dense: a denser sample (same hash rule, so it contains the base sample) of the launches between
FROM_TS and TO_TS, written to data/launch_census_dense.json — used to measure precision where few
launches score high.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from fetch_leader_trades import rpc  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
RATE = 60  # per 10,000 launches


def sampled(sig):
    return int(hashlib.sha256(sig.encode()).hexdigest(), 16) % 10000 < RATE


def main():
    global RATE
    sigs = json.load(open(os.path.join(DATA, "signatures.json")))
    since = min(s["blockTime"] for s in sigs if s.get("blockTime"))
    until = None
    out_p = os.path.join(DATA, "launch_census.json")
    if "--dense" in sys.argv:
        i = sys.argv.index("--dense")
        RATE, since, until = int(sys.argv[i + 1]), float(sys.argv[i + 2]), float(sys.argv[i + 3])
        out_p = os.path.join(DATA, "launch_census_dense.json")
    counts, sample, before, pages = {}, [], None, 0
    while True:
        p = {"limit": 1000, **({"before": before} if before else {})}
        page = rpc("getSignaturesForAddress", [MINT_AUTHORITY, p])
        if page is None:
            print(f"RPC en échec après {pages} pages, arrêt", flush=True)
            break
        if not page:
            break
        pages += 1
        for s in page:
            if s["err"] is not None or not s.get("blockTime") or s["blockTime"] < since:
                continue
            if until is not None and s["blockTime"] >= until:
                continue
            h = str(int(s["blockTime"] // 3600 * 3600))
            counts[h] = counts.get(h, 0) + 1
            if sampled(s["signature"]):
                sample.append({"sig": s["signature"], "slot": s["slot"], "bt": s["blockTime"]})
        before = page[-1]["signature"]
        oldest = page[-1].get("blockTime") or 0
        if pages % 50 == 0:
            print(f"  {pages} pages, {sum(counts.values())} lancements, échantillon {len(sample)}, "
                  f"remonté au {time.strftime('%m-%d %H:%M', time.gmtime(oldest))}", flush=True)
            json.dump({"hour_counts": counts, "sample": sample, "rate": RATE / 10000, "complete": False},
                      open(out_p, "w"))
        if oldest < since:
            break
        time.sleep(0.1)
    json.dump({"hour_counts": counts, "sample": sample, "rate": RATE / 10000, "complete": True}, open(out_p, "w"))
    print(f"terminé: {pages} pages, {sum(counts.values())} lancements, échantillon {len(sample)}", flush=True)


if __name__ == "__main__":
    main()
