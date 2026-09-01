from __future__ import annotations


import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

sys.path.insert(0, common.ADVISOR)
sys.path.insert(0, common.DECISIONS)

STUDY_PREFIXES = ("gnn_greedy_", "catboost_main_", "catboost_interrupt_")
VALUE_HEADER = (("gnn_greedy_", "val_mse"), ("catboost_", "val_rmse"))

PARAM_ORDER = ["hidden", "entity_layers", "action_rounds", "dst_dim", "depth", "lr",
               "learning_rate", "weight_decay", "l2_leaf_reg", "dropout", "grad_clip",
               "subsample", "random_strength", "border_count", "min_data_in_leaf",
               "one_hot_max_size", "leaf_estimation_iterations", "batch", "attn",
               "self_transform", "update", "conv", "conv_map", "conv_a2e", "conv_e2a",
               "map_aggr", "act_aggr"]
ATTR_ORDER = ["r2", "epochs", "stopped", "iterations", "best_iteration", "hit_cap",
              "seconds"]
ABBREV = {"hidden": "hid", "entity_layers": "ent", "action_rounds": "rnd",
          "dst_dim": "dst", "weight_decay": "wd", "dropout": "drop",
          "grad_clip": "clip", "self_transform": "self", "update": "upd",
          "conv_map": "cmap", "conv_a2e": "ca2e", "conv_e2a": "ce2a",
          "map_aggr": "magg", "act_aggr": "aagg", "learning_rate": "lr",
          "l2_leaf_reg": "l2", "subsample": "sub", "random_strength": "rstr",
          "border_count": "bord", "min_data_in_leaf": "minleaf",
          "one_hot_max_size": "onehot", "leaf_estimation_iterations": "leafit",
          "r2": "val_r2", "stopped": "stop", "seconds": "secs",
          "best_iteration": "bestit", "hit_cap": "cap", "epochs": "ep",
          "iterations": "trees"}
_NUMERIC = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?%?$")


def storage():
    from decisions import pg
    return "postgresql+psycopg://%s@%s:%d/optuna" % (pg.USER, pg.HOST, pg.PORT)


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return "%.4g" % v
    return str(v)


def _ordered(keys, order):
    known = [k for k in order if k in keys]
    return known + sorted(k for k in keys if k not in order)


def _value_head(name):
    for pre, head in VALUE_HEADER:
        if name.startswith(pre):
            return head
    return "value"


def table(study, out=print):
    import optuna
    st = optuna.trial.TrialState
    tag = {st.COMPLETE: "done", st.PRUNED: "pruned", st.FAIL: "fail",
           st.RUNNING: "running", st.WAITING: "queued"}
    trials = study.trials
    params = _ordered({k for t in trials for k in t.params}, PARAM_ORDER)
    attrs = _ordered({k for t in trials for k in t.user_attrs}, ATTR_ORDER)

    def rank(t):
        if t.state == st.COMPLETE and t.value is not None:
            return (0, 0, t.value)
        if t.state in (st.RUNNING, st.WAITING):
            return (2, t.number, 0.0)
        return (1, t.number, 0.0)

    head = (["trial"] + [ABBREV.get(k, k) for k in params]
            + ["state", _value_head(study.study_name)]
            + [ABBREV.get(k, k) for k in attrs])
    rows = []
    for t in sorted(trials, key=rank):
        rows.append([str(t.number)]
                    + [_cell(t.params.get(k)) for k in params]
                    + [tag.get(t.state, str(t.state)),
                       "%.5f" % t.value if t.value is not None else "-"]
                    + [_cell(t.user_attrs.get(k)) for k in attrs])
    if not rows:
        out("no trials yet")
        return
    w = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]
    num = [all(_NUMERIC.match(r[i]) or r[i] == "-" for r in rows)
           for i in range(len(head))]

    def line(cells):
        return "   ".join(c.rjust(w[i]) if num[i] else c.ljust(w[i])
                          for i, c in enumerate(cells)).rstrip()

    out(line(head))
    out("   ".join("-" * n for n in w))
    for r in rows:
        out(line(r))


def studies(store, prefix=None):
    import optuna
    names = [n for n in optuna.get_all_study_names(store)
             if n.startswith(prefix or STUDY_PREFIXES)]
    return sorted(names, key=lambda n: (n[-15:], n))


def main(a):
    import optuna
    store = storage()
    prefix = a[0] if a and not a[0].startswith("--") else None
    names = studies(store, prefix)
    if "--list" in a:
        for n in names:
            print(n)
        return 0
    name = a[a.index("--study") + 1] if "--study" in a else (names[-1] if names else None)
    if not name:
        print("no %s* studies in the optuna storage" % (prefix or "tuning"))
        return 1
    study = optuna.load_study(study_name=name, storage=store)
    print(name)
    table(study)
    return 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
