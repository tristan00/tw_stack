r"""Exploration metric (explore-exploit): how much each choice has been explored in the historical
run data, so a policy can trade off predicted VALUE (exploit) against under-explored choices (explore).

For each choice (action_type, action_key):
  * n  = how many times it appears in the data
  * explore_bonus = sqrt(2*ln(N)/n)   -- UCB-style: HIGH for rarely-seen choices (the explore frontier)
Also per-faction coverage: a choice well-explored globally may be unexplored for a specific faction.

Combine downstream as:  score = predicted_value + beta * explore_bonus  (beta tunes explore vs exploit).

    python exploration.py [dataset.csv]
"""
import math
import os
import sys

import pandas as pd


def main():
    csvp = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "exports", "choice_value_H3.csv")
    df = pd.read_csv(csvp)
    df["action_key"] = df["action_key"].fillna("").astype(str)
    N = len(df)

    g = df.groupby(["action_type", "action_key"]).size().reset_index(name="n")
    g["explore_bonus"] = (2 * math.log(max(N, 2)) / g["n"]).pow(0.5)
    g = g.sort_values("n", ascending=False).reset_index(drop=True)

    print("total choice rows: %d   distinct choices: %d" % (N, len(g)))
    print("\nMOST-explored choices (high count -> low bonus, safe to exploit):")
    for _, r in g.head(12).iterrows():
        print("   n=%-4d bonus=%.3f  %s / %s" % (r["n"], r["explore_bonus"], r["action_type"], r["action_key"]))
    print("\nLEAST-explored choices (rare -> high bonus, the explore frontier):")
    for _, r in g.tail(12).iterrows():
        print("   n=%-4d bonus=%.3f  %s / %s" % (r["n"], r["explore_bonus"], r["action_type"], r["action_key"]))

    print("\nper-FACTION coverage (distinct action_types each faction/lord has explored):")
    cov = df.groupby("faction")["action_type"].nunique().sort_values(ascending=False)
    for fac, c in cov.items():
        print("   %-34s %d action-types" % (fac, c))


if __name__ == "__main__":
    main()
