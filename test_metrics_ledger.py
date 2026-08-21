from __future__ import annotations


import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import metrics_db

FAILED = []


def check(cond, what):
    print("  %-4s %s" % ("ok" if cond else "FAIL", what))
    if not cond:
        FAILED.append(what)


def _trial(trial, uuids):
    return {"trial": trial, "campaign_uuids": uuids, "campaigns": len(uuids)}


def main():
    d = tempfile.mkdtemp(prefix="ledger_test_")
    orig = metrics_db.DB_PATH
    metrics_db.DB_PATH = os.path.join(d, "metrics.sqlite")
    try:
        metrics_db.write_trial(_trial("t1-g0", ["camp-a", "camp-b"]))
        metrics_db.write_trial(_trial("t2-g0", ["camp-c"]))
        metrics_db.write_trial(_trial("t3-g0", []))
        gone = metrics_db.prune_unmatched({"camp-a"})
        check(sorted(gone) == ["t2-g0", "t3-g0"],
              "trials whose campaigns are gone are pruned, a matching trial survives")
        check([r["trial"] for r in metrics_db.trials()] == ["t1-g0"],
              "the ledger keeps exactly the matched trial")
        con = sqlite3.connect(metrics_db.DB_PATH)
        rows = {t: (json.loads(p), why) for t, p, why in con.execute(
            "SELECT trial,payload,reason FROM trials_archive")}
        con.close()
        check(set(rows) == {"t2-g0", "t3-g0"}, "pruned trials land in trials_archive")
        check(rows["t2-g0"][0]["campaign_uuids"] == ["camp-c"],
              "the archived payload is preserved verbatim")
        check(all(why for _, why in rows.values()),
              "every archived trial records a reason")
        check(metrics_db.prune_unmatched({"camp-a"}) == [],
              "a second prune against the same disk state is a no-op")
        gone = metrics_db.prune_unmatched(set())
        check(gone == ["t1-g0"] and metrics_db.trials() == [],
              "no campaign data on disk prunes every trial")

        from advisor import session as SS
        metrics_db.write_trial(_trial("t4-g0", ["camp-x"]))
        metrics_db.write_trial(_trial("t5-g0", ["camp-y"]))
        run = os.path.join(d, "run")
        os.makedirs(run)
        logs = []
        SS._reconcile_ledger(run, logs.append)
        check(metrics_db.trials() == [],
              "session boot against a wiped run dir archives every stale trial")
        check(any("trials_archive" in l for l in logs),
              "the reconcile says what it moved")

        metrics_db.write_trial(_trial("t6-g0", ["camp-z"]))
        con = sqlite3.connect(os.path.join(run, SS.journal.DB_NAME))
        con.execute("CREATE TABLE campaigns"
                    "(campaign_id INTEGER PRIMARY KEY, campaign_key TEXT)")
        con.execute("INSERT INTO campaigns(campaign_key) VALUES('camp-z')")
        con.commit()
        con.close()
        logs = []
        SS._reconcile_ledger(run, logs.append)
        check([r["trial"] for r in metrics_db.trials()] == ["t6-g0"],
              "session boot keeps the trial whose campaigns are in the run dir store")
        check(logs == [], "an in-sync ledger reconciles silently")
    finally:
        metrics_db.DB_PATH = orig
        shutil.rmtree(d, ignore_errors=True)

    print("\n%s" % ("ledger OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
