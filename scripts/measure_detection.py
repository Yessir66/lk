#!/usr/bin/env python3
"""
Measure the READ side of our execution system: how late do we see pump.fun activity?

  measure_detection.py [--minutes M] [--rpc-ws URL] [--rpc-http URL]

Two websocket subscriptions on the same RPC node:
  - slotSubscribe: the local time at which each slot first becomes visible (our clock for "slot start")
  - logsSubscribe(mentions pump.fun program, commitment processed): every pump.fun transaction, with the
    slot it landed in and whether it is a create
For each transaction: detection delay = receipt time - time its slot first became visible (ms), and the
slot lag = latest visible slot at receipt - its slot. Creates received are compared with the launch
rate from the census to estimate how many we miss. Also times a few HTTP round trips (RPC getSlot, Jito
block engine getTipAccounts) — no transaction is sent.
Run it on the machine that will host the bot: results depend on where it runs.
Output: data/measure_detection_<timestamp>.json
"""
import asyncio
import json
import os
import ssl
import statistics
import sys
import time
import urllib.request

import websockets

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
MINUTES = float(arg("--minutes", "5"))
WS = arg("--rpc-ws", "wss://api.mainnet-beta.solana.com")
HTTP = arg("--rpc-http", "https://api.mainnet-beta.solana.com")
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()

slot_seen = {}      # slot -> local time first visible
latest = [0]
events = []         # (tx_slot, recv_time, is_create, latest_slot)
create_sigs = []    # (signature, slot, recv_time) of pump.fun creates, to check completeness
drops = [0]


async def slots(ws):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"}))
    async for raw in ws:
        m = json.loads(raw)
        if m.get("method") == "slotNotification":
            s, now = m["params"]["result"]["slot"], time.time()
            slot_seen.setdefault(s, now)
            latest[0] = max(latest[0], s)


def pump_create(lines):
    """A pump.fun Create/CreateV2 instruction (not the token-account program's own 'Create' logs)."""
    stack = []
    for l in lines:
        if l.startswith("Program ") and " invoke [" in l:
            stack.append(l.split()[1])
        elif l.startswith("Program ") and (l.endswith(" success") or " failed" in l):
            if stack:
                stack.pop()
        elif stack and stack[-1] == PUMP and l.startswith("Program log: Instruction: Create") and "Pool" not in l:
            return True
    return False


async def logs(ws):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "logsSubscribe",
                              "params": [{"mentions": [PUMP]}, {"commitment": "processed"}]}))
    async for raw in ws:
        now = time.time()
        m = json.loads(raw)
        if m.get("method") != "logsNotification":
            continue
        v = m["params"]["result"]
        if v["value"].get("err"):
            continue
        is_create = pump_create(v["value"]["logs"] or [])
        events.append((v["context"]["slot"], now, is_create, latest[0]))
        if is_create:
            create_sigs.append((v["value"]["signature"], v["context"]["slot"], now))


async def runner(coro_fn):
    while True:
        try:
            async with websockets.connect(WS, ssl=SSL, open_timeout=15, max_size=2 ** 24, ping_interval=20) as ws:
                await coro_fn(ws)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            drops[0] += 1
            print(f"  connexion perdue ({type(e).__name__}), reconnexion", flush=True)
            await asyncio.sleep(1)


def http_rtt(url, body, n=10):
    out = []
    for _ in range(n):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10, context=SSL).read()
            out.append(1000 * (time.time() - t0))
        except Exception:
            pass
        time.sleep(0.3)
    return out


def q(a, p):
    a = sorted(a)
    return round(a[min(len(a) - 1, int(p * len(a)))], 1) if a else None


async def main():
    tasks = [asyncio.create_task(runner(slots)), asyncio.create_task(runner(logs))]
    t_end = time.time() + 60 * MINUTES
    while time.time() < t_end:
        await asyncio.sleep(30)
        print(f"  {len(events)} transactions pump.fun reçues, {sum(e[2] for e in events)} créations", flush=True)
    for t in tasks:
        t.cancel()
    # completeness: every pump.fun create references the mint authority; list its signatures over the window
    completeness = None
    if create_sigs:
        s_min, s_max = min(c[1] for c in create_sigs) + 2, max(c[1] for c in create_sigs) - 2
        truth, before = set(), None
        for _ in range(10):
            body = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                    "params": ["TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM", {"limit": 1000, **({"before": before} if before else {})}]}
            try:
                page = json.load(urllib.request.urlopen(urllib.request.Request(HTTP, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}), timeout=20, context=SSL))["result"]
            except Exception:
                break
            if not page:
                break
            truth |= {x["signature"] for x in page if x["err"] is None and s_min <= x["slot"] <= s_max}
            before = page[-1]["signature"]
            if page[-1]["slot"] < s_min:
                break
        got = {c[0] for c in create_sigs if s_min <= c[1] <= s_max}
        completeness = {"on_chain": len(truth), "received": len(got & truth), "extra": len(got - truth)}
    delays, lags, cdelays = [], [], []
    for s, recv, is_create, lat in events:
        if s in slot_seen:
            d = 1000 * (recv - slot_seen[s])
            delays.append(d)
            if is_create:
                cdelays.append(d)
        lags.append(lat - s)
    n_create = sum(e[2] for e in events)
    dur_h = MINUTES / 60
    rtt_rpc = http_rtt(HTTP, {"jsonrpc": "2.0", "id": 1, "method": "getSlot"})
    rtt_jito = http_rtt("https://mainnet.block-engine.jito.wtf/api/v1/getTipAccounts",
                        {"jsonrpc": "2.0", "id": 1, "method": "getTipAccounts", "params": []})
    res = {"ws": WS, "minutes": MINUTES, "n_tx": len(events), "n_create": n_create,
           "creates_per_hour": round(n_create / dur_h), "reconnections": drops[0], "completeness": completeness,
           "delay_ms_after_slot_visible": {p: q(delays, p) for p in (0.1, 0.5, 0.9, 0.99)},
           "create_delay_ms": {p: q(cdelays, p) for p in (0.1, 0.5, 0.9)},
           "slot_lag": {p: q(lags, p) for p in (0.1, 0.5, 0.9, 0.99)},
           "slot_interval_ms": round(statistics.median([1000 * (slot_seen[s + 1] - slot_seen[s]) for s in slot_seen if s + 1 in slot_seen]), 1) if len(slot_seen) > 2 else None,
           "rtt_ms_rpc_getSlot": {p: q(rtt_rpc, p) for p in (0.1, 0.5, 0.9)},
           "rtt_ms_jito_block_engine": {p: q(rtt_jito, p) for p in (0.1, 0.5, 0.9)}}
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(DATA, f"measure_detection_{int(time.time())}.json"), "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
