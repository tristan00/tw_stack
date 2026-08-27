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
BUSY_BACKOFF = (1.0, 2.0, 5.0, 15.0, 30.0)


def _log(msg):
    sys.stdout.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def corpus(run_dir):
    return pg.connect(autocommit=True, readonly=True, row_factory=pg.row_factory)


def one_pass(src, an, tenants, log=_log) -> dict:
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
        except psycopg.OperationalError as e:
            failed.append((t.NAME, str(e)))
            log("BUSY %s: %s" % (t.NAME, e))
        except Exception as e:
            failed.append((t.NAME, "%s: %s" % (type(e).__name__, e)))
            log("ERROR %s: %s: %s" % (t.NAME, type(e).__name__, e))
    return {"done": done, "failed": failed}


def rebuild(an, tenants, log=_log):
    for t in tenants:
        try:
            an.execute(t.DDL)
            an.execute("BEGIN")
            an.execute("DELETE FROM %s" % t.NAME)
            an.execute("DELETE FROM analytics_state WHERE tenant=%s", (t.NAME,))
            an.execute("COMMIT")
        except psycopg.Error as e:
            an.execute("ROLLBACK")
            log("could not clear %s: %s" % (t.NAME, e))
    log("cleared %d tenants -- the next pass rebuilds" % len(tenants))


def main(argv):
    common.install_stamped_logs()
    run_dir = common.RUN_DIR
    if "--run-dir" in argv:
        run_dir = argv[argv.index("--run-dir") + 1]
    once = "--once" in argv
    _log("analytics: corpus %s" % pg.dsn())
    _log("analytics: writing %s" % store.analytics_path(run_dir))
    src, an = corpus(run_dir), store.connect()
    if "--rebuild" in argv:
        rebuild(an, TENANTS)
    t0 = time.time()
    last_beat, misses = 0.0, 0
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
        if res["failed"]:
            common.wait("analytics_busy_backoff", BUSY_BACKOFF[misses],
                        "%d tenant(s) failed" % len(res["failed"]))
        else:
            time.sleep(POLL_S)


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main(sys.argv[1:]))
