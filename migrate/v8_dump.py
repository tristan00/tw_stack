import hashlib
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'advisor'))

import common


def log(msg):
    sys.stderr.write('%.3f  v8dump %s\n' % (time.time(), msg))


def sha(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def main():
    out_path = sys.argv[1]
    window = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    full = len(sys.argv) > 3 and sys.argv[3] == 'full'
    t0 = time.time()
    log('enter port=%s window=%d' % (os.environ.get('TW_PG_PORT', '55432'), window))
    import model
    g = model.gather(window=window)
    out = {'model': {
        'n': len(g['full']), 'n_confirmed': g['n_confirmed'],
        'campaigns': g['campaigns'], 'skipped_unlabelled': g['skipped_unlabelled'],
        'columns': sorted({k for r in g['full'] for k in r}),
        'sha': sha([g['full'], g['y'], g['groups'], g['confirmed']])}}
    log('model n=%d sha=%s %.1f s'
        % (out['model']['n'], out['model']['sha'][:16], time.time() - t0))
    if full:
        io.open(out_path + '.model.json', 'w', encoding='utf-8').write(
            json.dumps([g['full'], g['y'], g['groups'], g['confirmed']]))
    t1 = time.time()
    import interrupt_model
    ig = interrupt_model.gather(window=window)
    excl = ('isc_option_label', 'isc_fc_result', 'isc_fc_casualties')
    rows2 = [{k: v for k, v in r.items() if k not in excl} for r in ig['rows']]
    out['interrupt'] = {
        'n': len(rows2), 'columns': sorted({k for r in rows2 for k in r}),
        'sha': sha([rows2, ig['groups']]), 'i_y': ig['y']}
    for col in excl:
        vals = sorted({str(r.get(col)) for r in ig['rows']})
        out['interrupt']['i_%s' % col] = [len(vals), sha(vals)]
    log('interrupt n=%d sha=%s %.1f s'
        % (out['interrupt']['n'], out['interrupt']['sha'][:16], time.time() - t1))
    if full:
        io.open(out_path + '.interrupt.json', 'w', encoding='utf-8').write(
            json.dumps([rows2, ig['y'], ig['groups']]))
    t2 = time.time()
    from store import DecisionStore
    from advisor.mapgraph import corpus as CO
    run_dir = os.path.dirname(common.run_dbs()[0])
    st = DecisionStore(run_dir, readonly=True)
    floor = st.window_floor(window)
    tm = {did: v for did, v in st.taken_map().items()
          if floor is None or did >= floor}
    hashes = sorted((did, CO.taken_hash(tup)) for did, (tup, c) in tm.items())
    counted_n = sum(1 for _, (tup, c) in tm.items() if c)
    st.close()
    out['walk'] = {'floor': floor, 'n': len(hashes), 'counted': counted_n,
                   'sha': sha(hashes)}
    log('walk n=%d floor=%s sha=%s %.1f s'
        % (out['walk']['n'], floor, out['walk']['sha'][:16], time.time() - t2))
    io.open(out_path, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, indent=2) + '\n')
    log('exit %.1f s -> %s' % (time.time() - t0, out_path))


if __name__ == '__main__':
    main()
