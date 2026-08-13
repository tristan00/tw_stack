from __future__ import annotations

"""The analytics process: fold new corpus rows into the precomputed tables.

    python -m analytics.runner                 poll forever (what runctl starts)
    python -m analytics.runner --once          one pass over every tenant, then exit
    python -m analytics.runner --rebuild       wipe every tenant first
    python -m analytics.runner --run-dir PATH  a corpus other than the live one

WHY A SEPARATE PROCESS.

  Not a thread in advisor_api: `runctl up` kills the UI on every launch and the dashboard is
  the process people restart when it misbehaves, so analytics would stop precisely when
  someone is looking at it. It would also make the API a WRITER to a sqlite file, turning
  `database is locked` into a latency defect inside the request path -- and `advisor_api/db.py`
  is read-only by construction, which is a property worth keeping.

  Not a step in the session loop: analytics would be as current as the last CAMPAIGN, would
  never run on a collection run started with --no-retrain, would spend the game's latency
  budget, and would die with a training crash. It also inverts the dependency, since the
  thing being measured would be computing its own scorecard.

  Not lazy-on-read: that puts variable-cost work back on the request path, which is the
  pattern this whole layer exists to replace.

SAFETY AGAINST THE COLLECTOR. The corpus is opened read-only, so this can never block the
collector's writer and can never corrupt the corpus. Each fact tenant holds back the newest
row so it never reads a decision whose scores have not landed yet. A busy or locked
analytics database is caught, recorded, backed off and retried; it does not advance a
watermark and it does not kill the process.

A pass that does nothing logs nothing except a heartbeat, so a log that has stopped growing
means a dead runner -- which is a fact you can see.
"""

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from analytics import store
from analytics.tenants import TENANTS
from decisions import dbopen

POLL_S = 5.0
HEARTBEAT_S = 300.0
BUSY_BACKOFF = (1.0, 2.0, 5.0, 15.0, 30.0)


def _log(msg):
    sys.stdout.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def corpus(run_dir):
    con = dbopen.connect(os.path.join(run_dir, "decisions.sqlite"))
    con.row_factory = sqlite3.Row
    return con


def one_pass(src, an, tenants, log=_log) -> dict:
    """Every tenant, dependencies first. One tenant's failure does not stop the others."""
    done, failed = [], []
    for t in store.order_tenants(list(tenants)):
        try:
            r = store.run_tenant(t, src, an)
            if r.get("wiped"):
                log("REBUILD %s: %s" % (t.NAME, r["wiped"]))
            if r["folded"]:
                log("folded %d into %s (through %d) in %.3fs; %d rows"
                    % (r["folded"], t.NAME, r["watermark"], r["seconds"], r["rows"]))
            done.append(r)
        except sqlite3.OperationalError as e:
            failed.append((t.NAME, str(e)))
            log("BUSY %s: %s" % (t.NAME, e))
        except Exception as e:
            failed.append((t.NAME, "%s: %s" % (type(e).__name__, e)))
            log("ERROR %s: %s: %s" % (t.NAME, type(e).__name__, e))
    return {"done": done, "failed": failed}


def rebuild(an, tenants, log=_log):
    """Force every tenant to recompute from scratch on the next pass."""
    for t in tenants:
        try:
            an.executescript(t.DDL)
            an.execute("BEGIN")
            an.execute("DELETE FROM %s" % t.NAME)
            an.execute("DELETE FROM analytics_state WHERE tenant=?", (t.NAME,))
            an.execute("COMMIT")
        except sqlite3.Error as e:
            an.execute("ROLLBACK")
            log("could not clear %s: %s" % (t.NAME, e))
    log("cleared %d tenants -- the next pass rebuilds" % len(tenants))


def main(argv):
    run_dir = common.RUN_DIR
    if "--run-dir" in argv:
        run_dir = argv[argv.index("--run-dir") + 1]
    once = "--once" in argv
    path = store.analytics_path(run_dir)
    _log("analytics: corpus %s" % os.path.join(run_dir, "decisions.sqlite"))
    _log("analytics: writing %s" % path)
    src, an = corpus(run_dir), store.connect(path)
    if "--rebuild" in argv:
        rebuild(an, TENANTS)
    t0 = time.time()
    last_beat, misses = 0.0, 0
    while True:
        try:
            res = one_pass(src, an, TENANTS)
            misses = 0 if not res["failed"] else min(misses + 1, len(BUSY_BACKOFF) - 1)
        except Exception as e:
            misses = min(misses + 1, len(BUSY_BACKOFF) - 1)
            _log("pass failed: %s: %s" % (type(e).__name__, e))
            res = {"done": [], "failed": [("pass", str(e))]}
        if once:
            worked = sum(r["folded"] for r in res["done"])
            for st in store.all_state(an):
                _log("  %-22s watermark %-8s rows %-8s %s"
                     % (st["tenant"], st["watermark"], st["rows"],
                        ("ERROR " + st["last_error"]) if st["last_error"] else ""))
            _log("one pass: folded %d, %d tenant(s) failed, %.2fs total"
                 % (worked, len(res["failed"]), time.time() - t0))
            return 1 if res["failed"] else 0
        if time.time() - last_beat > HEARTBEAT_S:
            last_beat = time.time()
            st = store.state(an, "model_agreement")
            _log("alive: model_agreement at %s, %s rows" % (st["watermark"], st["rows"]))
        time.sleep(BUSY_BACKOFF[misses] if res["failed"] else POLL_S)


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
