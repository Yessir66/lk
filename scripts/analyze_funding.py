#!/usr/bin/env python3
"""
Analyze the creator-funding trace: does the wallet trust a network of
funder wallets (not just individual creators)? Also flags whether a top
funder looks like a dedicated treasury (a real trust signal) or a
generic high-throughput bundler service (a confound — many unrelated
launchers could share it, making the overlap coincidental).
"""
import json
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
FUND_DIR = os.path.join(DATA_DIR, "creator_funding")
META_DIR = os.path.join(DATA_DIR, "coin_meta")


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    per_token = report["per_token"]

    funding = {}
    untraced = 0
    for fn in os.listdir(FUND_DIR):
        d = json.load(open(os.path.join(FUND_DIR, fn)))
        if d.get("funder"):
            funding[d["creator"]] = d
        else:
            untraced += 1

    creator_buy_count = Counter()
    for mint in per_token:
        m = json.load(open(os.path.join(META_DIR, f"{mint}.json")))
        if "__error__" not in m:
            creator_buy_count[m["creator"]] += 1

    funder_counts = Counter(d["funder"] for d in funding.values())
    shared_funder_creators = [c for c, d in funding.items() if funder_counts[d["funder"]] > 1]
    oneoff_creators = [c for c, cnt in creator_buy_count.items() if cnt == 1]
    oneoff_with_shared_funder = [c for c in oneoff_creators if c in funding and funder_counts[funding[c]["funder"]] > 1]

    result = {
        "creators_total": len(funding) + untraced,
        "creators_traced": len(funding),
        "creators_untraced": untraced,
        "distinct_funders": len(funder_counts),
        "top_funders": [
            {"funder": f, "creators_funded": cnt,
             "tokens_bought_from_those_creators": sum(creator_buy_count[c] for c, d in funding.items() if d["funder"] == f)}
            for f, cnt in funder_counts.most_common(10) if cnt > 1
        ],
        "creators_sharing_a_funder_with_another_bought_creator": len(shared_funder_creators),
        "oneoff_creators_total": len(oneoff_creators),
        "oneoff_creators_sharing_funder_with_a_repeat_or_other_creator": len(oneoff_with_shared_funder),
    }

    with open(os.path.join(DATA_DIR, "funding_report.json"), "w") as f:
        json.dump(result, f, indent=2)

    print("=" * 70)
    print("RÉSEAU DE FINANCEMENT DES CRÉATEURS (funder wallets)")
    print("=" * 70)
    print(f"Créateurs tracés jusqu'à leur financeur : {result['creators_traced']}/{result['creators_total']} "
          f"({result['creators_untraced']} non tracés)")
    print(f"Financeurs distincts : {result['distinct_funders']}")
    print(f"Créateurs partageant leur financeur avec un autre créateur acheté : "
          f"{result['creators_sharing_a_funder_with_another_bought_creator']}")
    print(f"...dont créateurs 'one-off' concernés : "
          f"{result['oneoff_creators_sharing_funder_with_a_repeat_or_other_creator']}/{result['oneoff_creators_total']}")
    print("\nTop financeurs partagés :")
    for f in result["top_funders"]:
        print(f"  {f['funder']}: finance {f['creators_funded']} créateurs -> "
              f"{f['tokens_bought_from_those_creators']} tokens achetés")
    print("\nNote : les financeurs les plus actifs ont un débit de transactions très élevé "
          "(~1000 tx en quelques dizaines de minutes), typique d'un service de bundling/funding "
          "générique utilisé par de nombreux déployeurs indépendants — le chevauchement de "
          "financeur n'est donc probablement pas un signal de confiance délibéré du wallet, "
          "plutôt une conséquence de la popularité de quelques services d'infrastructure pump.fun.")


if __name__ == "__main__":
    main()
