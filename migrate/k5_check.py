import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import journal, pg

BODIES = {
    'snapshot': {'active': ['lord:1']},
    'decide': {'decision_id': 1, 'offers': [{'key': 'a'}], 'pick': {'key': 'a'},
               'scores': [0.5], 'timings': {'store_ms': 1}},
    'verification': {'decision_id': 1, 'result': {'ok': True}},
    'interrupt': {'kind': 'dilemma', 'root': 'r', 'chosen': 'c', 'ts': 123.0,
                  'rpc_id': 'BODY', 'req_id': 'BODY'},
    'diplomacy': {'campaign_key': 'k', 'channel': 'outgoing'},
    'postmortem': {'campaign_key': 'k', 'turns_played': 12},
    'ucb_pick': {'c': 1.0, 'total_plays': 3},
}


def log(msg):
    sys.stderr.write('%.3f  k5 %s\n' % (time.time(), msg))


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    con = pg.connect(app_name='tw-k5', autocommit=True, search_path=pg.CORPUS_PATH)
    con.execute("TRUNCATE corpus.rpc_response, corpus.rpc_request")
    con.close()

    rd = 'k5'
    failures = []
    ids = {}
    for kind, body in BODIES.items():
        ids[kind] = journal._ask(rd, kind, body)
        journal._ask(rd, kind, body, req_id=ids[kind])

    con = pg.connect(app_name='tw-k5', autocommit=True, search_path=pg.CORPUS_PATH)
    n_req = con.execute("SELECT count(*) FROM corpus.rpc_request").fetchone()[0]
    if n_req != len(BODIES):
        failures.append('resend created rows: %d requests for %d kinds' % (n_req, len(BODIES)))
    log('resend: %d rows for %d kinds' % (n_req, len(BODIES)))

    rows, last = journal.read_requests(rd, 0)
    by_kind = {r['rpc_kind']: r for r in rows}
    for kind, body in BODIES.items():
        got = by_kind.get(kind)
        if got is None:
            failures.append('%s: no request row' % kind)
            continue
        if got['req_id'] != ids[kind]:
            failures.append('%s: req_id mismatch' % kind)
        for key, want in body.items():
            if key in ('rpc_id', 'req_id'):
                continue
            if got.get(key) != want:
                failures.append('%s: body key %r clobbered (%r != %r)'
                                % (kind, key, got.get(key), want))
    envelope_ok = by_kind['interrupt'].get('kind') == 'dilemma'
    if not envelope_ok:
        failures.append('interrupt body "kind" was overwritten by the envelope')
    log('envelope: interrupt body kind=%r rpc_kind=%r'
        % (by_kind['interrupt'].get('kind'), by_kind['interrupt'].get('rpc_kind')))

    if journal.cursor(rd) != 0:
        failures.append('cursor should be 0 with no responses, got %d' % journal.cursor(rd))

    for kind in BODIES:
        journal.respond(rd, ids[kind], snapshot_id=None, note=kind)
        journal.respond(rd, ids[kind], snapshot_id=None, note='SECOND')
    n_resp = con.execute("SELECT count(*) FROM corpus.rpc_response").fetchone()[0]
    if n_resp != len(BODIES):
        failures.append('double respond created rows: %d' % n_resp)
    for kind in BODIES:
        payload = con.execute("SELECT payload FROM corpus.rpc_response WHERE req_id=%s",
                              (ids[kind],)).fetchone()[0]
        if json.loads(payload).get('note') != kind:
            failures.append('%s: second response changed the content' % kind)
    log('responses: %d rows for %d kinds, content stable' % (n_resp, len(BODIES)))

    cur = journal.cursor(rd)
    if cur != last:
        failures.append('cursor should be %d once every kind is answered, got %d' % (last, cur))
    log('cursor after all answered: %d (last rpc_id %d)' % (cur, last))

    open_id = journal._ask(rd, 'snapshot', {'active': None})
    rows2, _ = journal.read_requests(rd, journal.cursor(rd))
    if len(rows2) != 1 or rows2[0]['req_id'] != open_id:
        failures.append('cursor did not resume at the one unanswered request')
    log('resume: %d unanswered request replayed' % len(rows2))

    con.close()
    journal.close(rd)
    if failures:
        for f in failures:
            log('FAIL %s' % f)
        raise SystemExit('K5 failed with %d problems' % len(failures))
    log('exit %.0f ms  K5 PASS on %d kinds' % ((time.time() - t0) * 1000, len(BODIES)))


if __name__ == '__main__':
    main()
