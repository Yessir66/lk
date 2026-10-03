#!/usr/bin/env python3
"""
Every callout of the accounts a pump.fun user follows, from pump.fun's public frontend API (no login):
  GET /users/search-v2?searchTerm=<username>   username -> wallet address
  GET /following/v3/following/<address>        accounts followed
  GET /callout/list/<address>                  a user's callouts (pageToken pagination)

Usage: fetch_kol_callouts.py <username>
Output: data/callouts/following.json [{username, address, followers}]
        data/callouts/callouts.json  [{author, author_address, calloutId, coinMint, createdAt (ms),
                                       marketCap, calloutPrice, calloutPriceUsd, maxMultiplier, ...}]
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from fetch_early_trades import get  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(DATA, "callouts")
API = "https://frontend-api-v3.pump.fun"
PAGE = 50
KEEP = ("calloutId", "coinMint", "createdAt", "marketCap", "calloutPrice", "calloutPriceUsd", "maxPriceSol",
        "maxPriceUsd", "maxMultiplier", "maxMultiplierAt", "multiple", "viewCount", "thesis", "chain", "perp",
        "quotedCalloutId")


def resolve(username):
    users = get(f"{API}/users/search-v2?searchTerm={urllib.parse.quote(username)}&limit=10") or []
    for u in users:
        if u["username"].lower() == username.lower():
            return u["address"]
    raise SystemExit(f"utilisateur introuvable : {username}")


def user_callouts(address):
    out, token = [], None
    while True:
        q = f"limit={PAGE}&sortBy=TIMESTAMP&sortOrder=DESC" + (f"&pageToken={urllib.parse.quote(token)}" if token else "")
        d = get(f"{API}/callout/list/{address}?{q}")
        if not d:
            raise RuntimeError(f"échec /callout/list/{address}")
        out += d.get("callouts") or []
        token = d.get("nextPageToken")
        if not token or not d.get("callouts"):
            return out
        time.sleep(0.3)


def main():
    username = sys.argv[1].lstrip("@")
    os.makedirs(OUT, exist_ok=True)
    me = resolve(username)
    following = get(f"{API}/following/v3/following/{me}?limit=500") or []
    following = [{"username": u["username"], "address": u["address"], "followers": u.get("followers")}
                 for u in following]
    json.dump(following, open(os.path.join(OUT, "following.json"), "w"), indent=1)
    print(f"{username} ({me}) suit {len(following)} comptes", flush=True)
    rows = []
    for u in following:
        cs = user_callouts(u["address"])
        rows += [{"author": u["username"], "author_address": u["address"], "author_followers": u["followers"],
                  **{k: c.get(k) for k in KEEP}} for c in cs]
        print(f"  {u['username']:22} {len(cs):5} callouts", flush=True)
        time.sleep(0.3)
    rows.sort(key=lambda r: r["createdAt"])
    json.dump(rows, open(os.path.join(OUT, "callouts.json"), "w"))
    print(f"{len(rows)} callouts au total", flush=True)


if __name__ == "__main__":
    main()
