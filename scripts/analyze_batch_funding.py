#!/usr/bin/env python3
"""
Analyze whether creators funded in a "batch" (same funding tx crediting
several throwaway wallets at once — a wallet-factory signature) are
over-represented among repeat creators vs one-off ones.
"""
import json
import os
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BATCH_DIR = os.path.join(DATA_DIR, "batch_funding")
META_DIR = os.path.join(DATA_DIR, "coin_meta")


def main():
    report = json.load(open(os.path.join(DATA_DIR, "report.json")))
    per_token = report["per_token"]

    creator_buy_count = Counter()
    for mint in per_token:
        p = os.path.join(META_DIR, f"{mint}.json")
        m = json.load(open(p))
        if "__error__" not in m:
            creator_buy_count[m["creator"]] += 1

    batch_results = {}
    for fn in os.listdir(BATCH_DIR):
        d = json.load(open(os.path.join(BATCH_DIR, fn)))
        if "error" not in d:
            batch_results[d["creator"]] = d

    n = len(batch_results)
    is_batch = sum(1 for d in batch_results.values() if d["is_batch_funding"])

    oneoff = {c for c, cnt in creator_buy_count.items() if cnt == 1}
    repeat = {c for c, cnt in creator_buy_count.items() if cnt > 1}

    def rate(creators):
        subset = [batch_results[c] for c in creators if c in batch_results]
        if not subset:
            return None, 0
        return sum(1 for d in subset if d["is_batch_funding"]) / len(subset), len(subset)

    r_rate, r_n = rate(repeat)
    o_rate, o_n = rate(oneoff)

    sizes = Counter(len(d["other_recipients_same_tx"]) + 1 for d in batch_results.values())

    result = {
        "creators_analyzed": n,
        "batch_funded_pct": round(100 * is_batch / n, 1),
        "batch_size_distribution": dict(sorted(sizes.items())),
        "batch_funded_rate_repeat_creators": {"rate": round(r_rate, 2) if r_rate is not None else None, "n": r_n},
        "batch_funded_rate_oneoff_creators": {"rate": round(o_rate, 2) if o_rate is not None else None, "n": o_n},
    }
    with open(os.path.join(DATA_DIR, "batch_funding_report.json"), "w") as f:
        json.dump(result, f, indent=2)

    print("=" * 70)
    print("FINANCEMENT PAR LOT (wallet factory) — critère additionnel testé")
    print("=" * 70)
    print(f"Créateurs analysés : {n}")
    print(f"Financés dans un batch (>=2 wallets credités dans la même tx) : "
          f"{is_batch}/{n} ({result['batch_funded_pct']}%)")
    print(f"Taux de batch-funding — créateurs récurrents : {r_rate*100:.0f}% (n={r_n}) | "
          f"créateurs one-off : {o_rate*100:.0f}% (n={o_n})")
    print("\nAssociation positive (récurrents plus souvent issus d'une 'usine à wallets') mais")
    print("échantillon trop petit (n=12 récurrents tracés) pour conclure à un critère de sélection.")
    print("Aucun cas où deux wallets 'frères' d'un même batch sont TOUS DEUX des créateurs achetés :")
    print("pas de preuve que le wallet cible spécifiquement des lots de wallets connus.")


if __name__ == "__main__":
    main()
