#!/usr/bin/env python3
"""
Study WHICH tokens the wallet chooses to buy (vs. the timing/sizing
already covered by analyze.py). Uses the pump.fun metadata cached by
fetch_token_metadata.py (creator address, social links, creation time)
to test whether selection is driven by the token's creator identity
rather than its content.
"""
import json
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
META_DIR = os.path.join(DATA_DIR, "coin_meta")
PRE_BUY_DIR = os.path.join(DATA_DIR, "pre_buy")


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    per_token = report["per_token"]
    mints = list(per_token.keys())

    meta = {}
    for mint in mints:
        p = os.path.join(META_DIR, f"{mint}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        if "__error__" not in d:
            meta[mint] = d

    n = len(meta)
    creator_count = Counter(d.get("creator") for d in meta.values())

    def is_repeat(m):
        return creator_count[meta[m].get("creator")] > 1

    repeat_mints = [m for m in meta if is_repeat(m)]
    oneoff_mints = [m for m in meta if not is_repeat(m)]

    def pct_twitter(mlist):
        return 100 * sum(1 for m in mlist if meta[m].get("twitter")) / len(mlist) if mlist else 0

    def delay_stats(mlist):
        vals = sorted(
            per_token[m]["sniper_delay_s"] for m in mlist
            if per_token[m]["sniper_delay_s"] is not None and per_token[m]["sniper_delay_s"] >= 0
        )
        if not vals:
            return None
        return {"n": len(vals), "median": vals[len(vals)//2]}

    top_creators = []
    for c, cnt in creator_count.most_common(10):
        if cnt < 2:
            continue
        cmints = [m for m in meta if meta[m].get("creator") == c]
        total_sol = sum(per_token[m]["first_position_sol"] for m in cmints)
        top_creators.append({
            "creator": c,
            "tokens_bought": cnt,
            "total_sol_committed": round(total_sol, 3),
            "entry_delay": delay_stats(cmints),
            "symbols": [meta[m].get("symbol") for m in cmints],
        })

    selection_report = {
        "wallet": report["wallet"],
        "tokens_with_metadata": n,
        "distinct_creators": len(creator_count),
        "tokens_from_repeat_creators_pct": round(100 * len(repeat_mints) / n, 1),
        "twitter_link_present_pct": round(pct_twitter(list(meta.keys())), 1),
        "twitter_repeat_vs_oneoff": {
            "repeat_creator": round(pct_twitter(repeat_mints), 1),
            "oneoff_creator": round(pct_twitter(oneoff_mints), 1),
        },
        "entry_delay_repeat_vs_oneoff": {
            "repeat_creator": delay_stats(repeat_mints),
            "oneoff_creator": delay_stats(oneoff_mints),
        },
        "top_repeat_creators": top_creators,
    }

    # ---- pre-buy on-chain context: does the wallet wait for a dev buy /
    # early buyer signal before entering, especially on one-off creators? ----
    pre = {}
    for mint in mints:
        p = os.path.join(PRE_BUY_DIR, f"{mint}.json")
        if os.path.exists(p):
            pre[mint] = json.load(open(p))

    def stat(mlist, key):
        vals = [pre[m][key] for m in mlist if m in pre and pre[m].get(key) is not None]
        if not vals:
            return None
        vals.sort()
        n = len(vals)
        return {"n": n, "median": vals[n // 2], "mean": round(sum(vals) / n, 2), "min": vals[0], "max": vals[-1]}

    def pct_with(mlist, key):
        avail = [m for m in mlist if m in pre]
        if not avail:
            return None
        have = sum(1 for m in avail if pre[m].get(key) is not None)
        return round(100 * have / len(avail), 1)

    pre_buy_section = {
        "prior_tx_count": {"repeat_creator": stat(repeat_mints, "prior_tx_count"),
                            "oneoff_creator": stat(oneoff_mints, "prior_tx_count")},
        "dev_buy_detected_pct": {"repeat_creator": pct_with(repeat_mints, "dev_buy_sol"),
                                  "oneoff_creator": pct_with(oneoff_mints, "dev_buy_sol")},
        "dev_buy_sol": {"repeat_creator": stat(repeat_mints, "dev_buy_sol"),
                         "oneoff_creator": stat(oneoff_mints, "dev_buy_sol")},
        "distinct_prior_buyers_first15tx": {"repeat_creator": stat(repeat_mints, "distinct_prior_buyers"),
                                             "oneoff_creator": stat(oneoff_mints, "distinct_prior_buyers")},
    }
    selection_report["pre_buy_context"] = pre_buy_section

    with open(os.path.join(DATA_DIR, "selection_report.json"), "w") as f:
        json.dump(selection_report, f, indent=2, default=str)

    print("=" * 70)
    print("CRITÈRES DE SÉLECTION — pourquoi ce token plutôt qu'un autre")
    print("=" * 70)
    print(f"Tokens analysés (metadata dispo) : {n}")
    print(f"Créateurs distincts              : {len(creator_count)}")
    print(f"Tokens dont le créateur revient   : {len(repeat_mints)}/{n} "
          f"({selection_report['tokens_from_repeat_creators_pct']}%)")
    print(f"Lien Twitter/X présent            : {selection_report['twitter_link_present_pct']}% "
          f"(récurrents: {selection_report['twitter_repeat_vs_oneoff']['repeat_creator']}%, "
          f"nouveaux: {selection_report['twitter_repeat_vs_oneoff']['oneoff_creator']}%)")
    print("\nTop créateurs suivis (adresse déployeur -> nb de tokens achetés) :")
    for c in top_creators[:8]:
        d = c["entry_delay"]
        med = f"{d['median']:.0f}s" if d else "?"
        print(f"  {c['creator']}  x{c['tokens_bought']:<3d} "
              f"{c['total_sol_committed']} SOL  délai médian {med}")
        print(f"    symboles: {c['symbols']}")

    print("\n-- Contexte on-chain juste avant l'achat (dev buy, acheteurs déjà présents) --")
    ptc = pre_buy_section["prior_tx_count"]
    if ptc["repeat_creator"] and ptc["oneoff_creator"]:
        print(f"Tx sur la bonding curve avant notre achat — récurrents: médiane {ptc['repeat_creator']['median']}, "
              f"nouveaux: médiane {ptc['oneoff_creator']['median']}  (environnement ultra-compétitif dans les 2 cas)")
    db_pct = pre_buy_section["dev_buy_detected_pct"]
    print(f"Dev buy détecté dans les ~15 1ères tx — récurrents: {db_pct['repeat_creator']}%, "
          f"nouveaux: {db_pct['oneoff_creator']}%")
    dbs = pre_buy_section["dev_buy_sol"]
    if dbs["repeat_creator"] and dbs["oneoff_creator"]:
        print(f"Taille médiane du dev buy — récurrents: {dbs['repeat_creator']['median']:.2f} SOL, "
              f"nouveaux: {dbs['oneoff_creator']['median']:.2f} SOL")
    pb = pre_buy_section["distinct_prior_buyers_first15tx"]
    if pb["repeat_creator"] and pb["oneoff_creator"]:
        print(f"Acheteurs distincts déjà présents (15 1ères tx) — récurrents: médiane {pb['repeat_creator']['median']}, "
              f"nouveaux: médiane {pb['oneoff_creator']['median']}")
    print("=> Pas de seuil net (dev buy / acheteurs précoces) qui distingue les deux groupes : "
          "ce n'est probablement pas un filtre on-chain qui explique les créateurs 'nouveaux'.")

    print(f"\nRapport JSON : data/selection_report.json")


if __name__ == "__main__":
    main()
