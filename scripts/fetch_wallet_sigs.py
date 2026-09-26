#!/usr/bin/env python3
"""Page getSignaturesForAddress back to the start of the study window (first signature of the studied
wallet) and save data/leader_<prefix>_sigs.json, the input of fetch_leader_trades.py.
Usage: fetch_wallet_sigs.py <wallet> [<wallet> ...]"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from fetch_leader_trades import rpc  # noqa: E402  (import-safe: guarded by __main__ below)

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch(w, since):
    out, before = [], None
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [w, p])
        if page is None:
            print(f"  {w[:8]}: RPC en échec, arrêt à {len(out)} signatures", flush=True)
            break
        if not page:
            break
        out += [s for s in page if (s.get("blockTime") or 0) >= since]
        before = page[-1]["signature"]
        if (page[-1].get("blockTime") or 0) < since:
            break
        if len(out) % 20000 < 1000:
            print(f"  {w[:8]}: {len(out)} signatures, {time.strftime('%m-%d %H:%M', time.gmtime(page[-1]['blockTime']))}", flush=True)
        time.sleep(0.25)
    return out


if __name__ == "__main__":
    sigs = json.load(open(os.path.join(DATA, "signatures.json")))
    since = min(s["blockTime"] for s in sigs if s.get("blockTime"))
    for w in sys.argv[1:]:
        out = fetch(w, since)
        json.dump(out, open(os.path.join(DATA, f"leader_{w[:8]}_sigs.json"), "w"))
        print(f"{w[:8]}: {len(out)} signatures dont {sum(1 for s in out if s['err'] is None)} réussies", flush=True)
