r"""Fit a CatBoost regressor to predict the VALUE of a choice (target = normalized gamestate value
delta over H turns) from the choice-value dataset. Reports a campaign-grouped validation error (so
no campaign leaks across train/val), feature importances, and a demo that scores candidate choices
for one fixed context -- the "value of each available option" the idea is after.

    python value_model.py [dataset.csv]
"""
import os
import sys

import pandas as pd
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupShuffleSplit

CAT = ["faction", "subculture", "lord", "action_type", "action_key"]
DROP = ["campaign", "value_now", "value_ahead", "value_bestfut", "target", "target_best"]


def load(csvp):
    df = pd.read_csv(csvp)
    for c in CAT:
        df[c] = df[c].astype(str).fillna("NA")
    return df


def main():
    csvp = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "exports", "choice_value_H8.csv")
    target_col = sys.argv[2] if len(sys.argv) > 2 else "target"     # "target" (delta H) or "target_best"
    df = load(csvp)
    y = df[target_col]
    X = df.drop(columns=DROP)
    print("target = %s" % target_col)
    cat_idx = [X.columns.get_loc(c) for c in CAT]

    # campaign-grouped split: the SAME campaign never appears in both train and val (no leakage)
    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=0)
    tr, va = next(gss.split(X, y, groups=df["campaign"]))
    m = CatBoostRegressor(iterations=500, depth=5, learning_rate=0.04, loss_function="RMSE",
                          cat_features=cat_idx, random_seed=0, verbose=False)
    m.fit(X.iloc[tr], y.iloc[tr], eval_set=(X.iloc[va], y.iloc[va]))

    pv = m.predict(X.iloc[va])
    base = [y.iloc[tr].mean()] * len(va)
    print("rows=%d  train=%d  val=%d  (campaign-grouped)" % (len(df), len(tr), len(va)))
    print("model    val MAE=%.4f  R2=%.3f" % (mean_absolute_error(y.iloc[va], pv), r2_score(y.iloc[va], pv)))
    print("baseline val MAE=%.4f  (predict train mean)" % mean_absolute_error(y.iloc[va], base))

    fi = sorted(zip(X.columns, m.get_feature_importance()), key=lambda z: -z[1])
    print("\ntop feature importances:")
    for name, imp in fi[:14]:
        print("  %-26s %5.1f" % (name, imp))

    # ---- demo: score candidate choices for ONE fixed context ----
    ctx = X.iloc[va[0]].copy()
    types = sorted(df["action_type"].unique())
    print("\ndemo -- predicted value of each action_type for context "
          "faction=%s lord=%s turn=%s income=%s regions=%s:" % (ctx["faction"], ctx["lord"],
          ctx["turn"], ctx["income"], ctx["regions"]))
    scored = []
    for at in types:
        row = ctx.copy()
        row["action_type"] = at
        row["action_key"] = ""                             # generic key for the demo
        scored.append((at, float(m.predict(pd.DataFrame([row]))[0])))
    for at, v in sorted(scored, key=lambda z: -z[1]):
        print("   %+.4f  %s" % (v, at))


if __name__ == "__main__":
    main()
