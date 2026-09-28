#!/usr/bin/env python3
"""
Measure the WRITE side of our execution system: when and where do our transactions land?

  measure_landing.py --keypair burner.json [--n 20] [--routes rpc,jito] [--jito-tip 2000]
                     [--cu-price 20000] [--rpc-http URL] [--dry-run]

Sends N minimal test transactions per route from a DEDICATED burner wallet (a memo, no token trade):
  ComputeBudget(limit, price) + Memo ["latency-test <route> <i>"] (+ a tip to a Jito tip account on
  the jito route). Cost ~0.00001-0.00002 SOL per transaction. For each one: slot seen just before
  sending, landed slot, time to confirmation, and position inside its block (index / block size).
--dry-run builds and signs the transactions without sending anything.
Never point --keypair at a wallet holding real funds; fund the burner with ~0.01 SOL.
Output: data/measure_landing_<timestamp>.json
"""
import base64
import json
import os
import random
import ssl
import sys
import time
import urllib.request

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
RPC = arg("--rpc-http", "https://api.mainnet-beta.solana.com")
JITO = "https://mainnet.block-engine.jito.wtf/api/v1/transactions"
MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
JITO_TIPS = ["96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
             "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
             "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
             "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"]
CA = "/root/.ccr/ca-bundle.crt"
SSL = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def post(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=15, context=SSL))


def build(kp, blockhash, route, i, tip, cu_price):
    ixs = [set_compute_unit_limit(20_000), set_compute_unit_price(cu_price),
           Instruction(MEMO, f"latency-test {route} {i} {time.time():.3f}".encode(), [])]
    if route == "jito":
        ixs.append(transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Pubkey.from_string(random.choice(JITO_TIPS)),
                                           lamports=tip)))
    msg = MessageV0.try_compile(kp.pubkey(), ixs, [], blockhash)
    return VersionedTransaction(msg, [kp])


def main():
    dry = "--dry-run" in sys.argv
    kp_path = arg("--keypair", None)
    kp = Keypair.from_bytes(bytes(json.load(open(kp_path)))) if kp_path else Keypair()
    n, routes = int(arg("--n", "20")), arg("--routes", "rpc,jito").split(",")
    tip, cu_price = int(arg("--jito-tip", "2000")), int(arg("--cu-price", "20000"))
    print(f"wallet de test {kp.pubkey()} | {n} transactions par route {routes} | {'SIMULATION (rien envoyé)' if dry else 'ENVOI RÉEL'}")
    if not dry:
        bal = post(RPC, "getBalance", [str(kp.pubkey())])["result"]["value"] / 1e9
        print(f"solde {bal:.6f} SOL")
        if bal < 0.002:
            sys.exit("solde insuffisant pour les tests (~0.002 SOL minimum)")
    sent = []
    for i in range(n):
        for route in routes:
            bh = Hash.from_string(post(RPC, "getLatestBlockhash", [{"commitment": "processed"}])["result"]["value"]["blockhash"])
            tx = build(kp, bh, route, i, tip, cu_price)
            raw = base64.b64encode(bytes(tx)).decode()
            if dry:
                print(f"  {route} #{i}: transaction signée, {len(bytes(tx))} octets, signature {str(tx.signatures[0])[:20]}…")
                continue
            slot_before = post(RPC, "getSlot", [{"commitment": "processed"}])["result"]
            t0 = time.time()
            url = JITO if route == "jito" else RPC
            r = post(url, "sendTransaction", [raw, {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}])
            sent.append({"route": route, "i": i, "sig": str(tx.signatures[0]), "slot_before": slot_before, "t_send": t0,
                         "error": r.get("error")})
            time.sleep(0.8)
    if dry:
        return
    # wait for inclusion, then locate each transaction inside its block
    deadline = time.time() + 60
    pending = {s["sig"]: s for s in sent if not s["error"]}
    while pending and time.time() < deadline:
        sigs = list(pending)
        st = post(RPC, "getSignatureStatuses", [sigs, {"searchTransactionHistory": False}])["result"]["value"]
        for sg, v in zip(sigs, st):
            if v and v.get("slot"):
                pending[sg]["landed_slot"] = v["slot"]
                pending[sg]["t_seen"] = time.time()
                del pending[sg]
        time.sleep(0.5)
    for s in sent:
        if "landed_slot" not in s:
            continue
        tx = post(RPC, "getTransaction", [s["sig"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])["result"]
        blk = post(RPC, "getBlock", [s["landed_slot"], {"transactionDetails": "signatures", "rewards": False,
                                                          "maxSupportedTransactionVersion": 0}])["result"]
        sigs = blk["signatures"] if blk else []
        s["index"] = sigs.index(s["sig"]) if s["sig"] in sigs else (tx or {}).get("transactionIndex")
        s["block_size"] = len(sigs)
        time.sleep(0.3)
    out = {}
    for route in routes:
        rs = [s for s in sent if s["route"] == route]
        ok = [s for s in rs if "landed_slot" in s]
        d = sorted(s["landed_slot"] - s["slot_before"] for s in ok)
        pos = sorted(s["index"] / max(1, s["block_size"]) for s in ok if s.get("index") is not None and s.get("block_size"))
        pct = lambda a, p: a[min(len(a) - 1, int(p * len(a)))] if a else None
        out[route] = {"sent": len(rs), "landed": len(ok), "send_errors": sum(1 for s in rs if s["error"]),
                      "slots_after_send": {p: pct(d, p) for p in (0.1, 0.5, 0.9)},
                      "position_in_block": {p: round(pct(pos, p), 3) if pos else None for p in (0.1, 0.5, 0.9)}}
        print(route, json.dumps(out[route]))
    json.dump({"summary": out, "sent": sent}, open(os.path.join(DATA, f"measure_landing_{int(time.time())}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
