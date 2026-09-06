import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import canon, pg, sets, store2
from migrate.replay import text_of


def log(msg):
    sys.stderr.write('%.3f  extfill %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    store2.log = lambda m: None
    sets.log = lambda m: None
    st = store2.Store(app_name='tw-extfill')
    rc = pg.connect(app_name='tw-extfillread', readonly=True, autocommit=True)
    targets = [r[0] for r in rc.execute(
        "SELECT DISTINCT snapshot_id FROM migrate.mismatch"
        " WHERE stage = 'V1' AND role = 'EB' ORDER BY snapshot_id")]
    log('targets=%d' % len(targets))
    written = 0
    for sid in targets:
        rows = rc.execute(
            "SELECT e.entity_seq, c.character_id, b.z FROM entities e"
            " JOIN blobs b ON b.blob_id = e.features_blob"
            " JOIN corpus.char_state c ON c.snapshot_id = e.decision_id"
            " AND c.entity_seq = e.entity_seq"
            " WHERE e.decision_id = %s ORDER BY e.entity_seq", (sid,)).fetchall()
        with st.conn.unit('extfill'):
            for seq, character_id, z in rows:
                state = canon.normalise(json.loads(text_of(z)))
                if not st._has_ext(state):
                    continue
                have = st.conn.execute(
                    "SELECT 1 FROM corpus.char_state_ext WHERE snapshot_id = %s"
                    " AND character_id = %s", (sid, character_id)).fetchone()
                if have:
                    continue
                ids = st.setw.ensure(sets.prepare(state))
                st._row('char_state_ext', state, ids,
                        {'snapshot_id': sid, 'character_id': character_id})
                written += 1
    st.close()
    rc.close()
    log('exit %.0f ms snapshots=%d ext_rows=%d'
        % ((time.time() - t0) * 1000, len(targets), written))


if __name__ == '__main__':
    main()
