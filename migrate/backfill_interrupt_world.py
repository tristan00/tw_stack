import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, pg, store2
from migrate.replay import text_of
from migrate.run import checkpoint

BATCH = 2000


def log(msg):
    sys.stderr.write('%.3f  backfill %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    store2.log = lambda m: None
    st = store2.Store(app_name='tw-backfill')
    st.conn.execute('SET synchronous_commit = off')
    rc = pg.connect(app_name='tw-backfillread', readonly=True, autocommit=True)
    lo, hi = rc.execute(
        "SELECT MIN(ci.interrupt_id), MAX(ci.interrupt_id) FROM corpus.interrupt ci"
    ).fetchone()
    done = armies = hostiles = 0
    for start in range(lo, hi + 1, BATCH):
        rows = rc.execute(
            "SELECT ci.interrupt_id, bw.z FROM corpus.interrupt ci"
            " JOIN interrupts li ON li.interrupt_id = ci.legacy_interrupt_id"
            " JOIN blobs bw ON bw.blob_id = li.world_blob"
            " WHERE ci.interrupt_id BETWEEN %s AND %s ORDER BY ci.interrupt_id",
            (start, min(start + BATCH - 1, hi))).fetchall()
        if not rows:
            continue
        ids = [r[0] for r in rows]
        with st.conn.unit('M4b'):
            st.conn.execute(
                "DELETE FROM corpus.world_army WHERE snapshot_id = ANY(%s)", (ids,))
            st.conn.execute(
                "DELETE FROM corpus.world_hostile WHERE snapshot_id = ANY(%s)", (ids,))
            for sid, wz in rows:
                world = canon.normalise(json.loads(text_of(wz)))
                a = world.get('armies') or []
                h = world.get('hostiles') or []
                st._ord_rows('world_army', sid, a)
                st._ord_rows('world_hostile', sid, h)
                armies += len(a)
                hostiles += len(h)
        done += len(rows)
        log('range %d..%d done=%d armies=%d hostiles=%d'
            % (start, min(start + BATCH - 1, hi), done, armies, hostiles))
    checkpoint(st.conn, 'M4b', 'done', done, armies + hostiles, t0)
    st.close()
    rc.close()
    log('exit %.1f min interrupts=%d armies=%d hostiles=%d'
        % ((time.time() - t0) / 60, done, armies, hostiles))


if __name__ == '__main__':
    main()
