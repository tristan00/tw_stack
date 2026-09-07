from __future__ import annotations


import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

import common
from analytics import store
from analytics.tenants import TENANTS
from decisions import pg

POLL_S = 5.0
HEARTBEAT_S = 300.0
MAINTENANCE_S = 300.0
BUSY_BACKOFF = (1.0, 2.0, 5.0, 15.0, 30.0)


def _log(msg):
    sys.stdout.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def corpus(run_dir):
    return pg.connect(app_name="tw-analytics-read", autocommit=True, readonly=True,
                      row_factory=pg.row_factory, search_path=pg.CORPUS_PATH)


def one_pass(src, an, tenants, log=_log) -> dict:
    done, failed = [], []
    for t in store.order_tenants(list(tenants)):
        try:
            r = store.run_tenant(t, src, an)
            if r.get("wiped"):
                log("REBUILD %s: %s" % (t.NAME, r["wiped"]))
            if r["folded"]:
                log("folded %d into %s (through %d) in %.3fs"
                    % (r["folded"], t.NAME, r["watermark"], r["seconds"]))
            done.append(r)
        except psycopg.OperationalError as e:
            failed.append((t.NAME, str(e)))
            log("BUSY %s: %s" % (t.NAME, e))
        except Exception as e:
            failed.append((t.NAME, "%s: %s" % (type(e).__name__, e)))
            log("ERROR %s: %s: %s" % (t.NAME, type(e).__name__, e))
    return {"done": done, "failed": failed}


def maintenance(src, since, log=_log):
    t0 = time.time()
    from analytics import db_stats, diplo_changes
    ids = [r["campaign_id"] for r in src.execute(
        "SELECT DISTINCT campaign_id FROM corpus.snapshot WHERE ts > %s", (since,))]
    if ids:
        diplo_changes.refresh(campaign_ids=ids)
    db_stats.refresh()
    log("maintenance: %d campaigns rescanned for diplomacy, db stats refreshed, %.2fs"
        % (len(ids), time.time() - t0))


def rebuild(an, tenants, log=_log):
    for t in tenants:
        store.reset(an, t)
    log("cleared %d tenants -- the next pass rebuilds" % len(tenants))


def main(argv):
    common.install_stamped_logs()
    run_dir = common.RUN_DIR
    if "--run-dir" in argv:
        run_dir = argv[argv.index("--run-dir") + 1]
    once = "--once" in argv
    _log("analytics: corpus %s" % pg.dsn())
    _log("analytics: writing schema %s" % store.SCHEMA)
    src, an = corpus(run_dir), store.connect()
    if "--rebuild" in argv:
        rebuild(an, TENANTS)
    t0 = time.time()
    last_beat, last_maint, misses = 0.0, 0.0, 0
    if not once:
        _log("analytics: poll loop starting -- every %.0fs, busy backoff %s"
             % (POLL_S, list(BUSY_BACKOFF)))
    while True:
        try:
            res = one_pass(src, an, TENANTS)
            misses = 0 if not res["failed"] else min(misses + 1, len(BUSY_BACKOFF) - 1)
        except Exception as e:
            misses = min(misses + 1, len(BUSY_BACKOFF) - 1)
            _log("pass failed: %s: %s" % (type(e).__name__, e))
            res = {"done": [], "failed": [("pass", str(e))]}
        if time.time() - last_maint > MAINTENANCE_S:
            maintenance(src, last_maint)
            last_maint = time.time()
        if once:
            worked = sum(r["folded"] for r in res["done"])
            for st in store.all_state(an):
                _log("  %-22s watermark %-10s %s"
                     % (st["tenant"], st["watermark"],
                        ("ERROR " + st["last_error"]) if st["last_error"] else ""))
            _log("one pass: folded %d, %d tenant(s) failed, %.2fs total"
                 % (worked, len(res["failed"]), time.time() - t0))
            return 1 if res["failed"] else 0
        if time.time() - last_beat > HEARTBEAT_S:
            last_beat = time.time()
            st = store.state(an, "model_agreement")
            _log("alive: model_agreement at %s" % (st["watermark"],))
        if res["failed"]:
            common.wait("analytics_busy_backoff", BUSY_BACKOFF[misses],
                        "%d tenant(s) failed" % len(res["failed"]))
        else:
            time.sleep(POLL_S)


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
