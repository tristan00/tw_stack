import argparse
import io
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import common
import runctl
import verify
from decisions import pg

DUP_UUID_SQL = ("SELECT count(*) - count(DISTINCT decision_uuid) FROM corpus.decision")

UNANSWERED_SQL = (
    "SELECT count(*) FROM corpus.rpc_request q"
    " LEFT JOIN corpus.rpc_response r ON r.req_id = q.req_id"
    " WHERE r.req_id IS NULL AND q.ts < %s")

STALE_AWAITING_SQL = (
    "SELECT count(*) FROM corpus.taken t JOIN dict.enum e ON e.enum_id = t.refusal_id"
    " WHERE e.key = 'awaiting_execution' AND t.ts < %s")


def log(msg):
    sys.stderr.write('%.3f  k1 %s\n' % (time.time(), msg))


def manager_pids():
    out = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
         " | Where-Object { $_.CommandLine -like '*manager/manager.py*' }"
         " | ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True)
    return [int(x) for x in out.stdout.split()]


def kill_manager():
    pids = manager_pids()
    for pid in pids:
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        'Stop-Process -Id %d -Force' % pid], capture_output=True)
    return pids


def decisions_now(con):
    return con.execute('SELECT max(decision_id) FROM corpus.decision').fetchone()[0]


def wait_progress(con, floor, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if (decisions_now(con) or 0) > floor:
            return round(time.time() - t0, 1)
        common.wait('k1_progress_poll', 2.0)
    return None


def checks(con, cut):
    return {'duplicate_decision_uuid': con.execute(DUP_UUID_SQL).fetchone()[0],
            'unanswered_rpc': con.execute(UNANSWERED_SQL, (cut,)).fetchone()[0],
            'stale_awaiting': con.execute(STALE_AWAITING_SQL, (cut,)).fetchone()[0]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kills', type=int, default=5)
    ap.add_argument('--min-gap', type=float, default=8.0)
    ap.add_argument('--max-gap', type=float, default=30.0)
    ap.add_argument('--recover-timeout', type=float, default=300.0)
    ap.add_argument('--shots', type=int, default=runctl.DEFAULT_SHOTS)
    args = ap.parse_args()

    t0 = time.time()
    log('enter kills=%d port=%s' % (args.kills, pg.PORT))
    rec = json.load(io.open(runctl.LAUNCH_RECORD, encoding='utf-8'))
    rounds = []
    con = pg.connect(app_name='tw-k1', readonly=True, autocommit=True)
    for i in range(args.kills):
        gap = random.uniform(args.min_gap, args.max_gap)
        common.wait('k1_gap', gap)
        floor = decisions_now(con) or 0
        cut = time.time()
        pids = kill_manager()
        log('round %d killed pids=%s at decision %s' % (i + 1, pids, floor))
        common.wait('k1_restart_settle', 2.0)
        started = runctl.start_recorder(shots=args.shots, dev=rec['dev'],
                                        presave_radius=rec['presave_radius'],
                                        code_version=rec.get('code_version'))
        recovered = wait_progress(con, floor, args.recover_timeout)
        row = {'round': i + 1, 'gap_s': round(gap, 1), 'killed_pids': pids,
               'decision_floor': floor, 'recovered_s': recovered,
               'recorder': started}
        row.update(checks(con, cut))
        row['ok'] = (bool(pids) and recovered is not None
                     and row['duplicate_decision_uuid'] == 0
                     and row['unanswered_rpc'] == 0
                     and row['stale_awaiting'] == 0)
        log('round %d %s recovered=%ss dup=%d unanswered=%d stale=%d'
            % (i + 1, 'PASS' if row['ok'] else 'FAIL', recovered,
               row['duplicate_decision_uuid'], row['unanswered_rpc'],
               row['stale_awaiting']))
        rounds.append(row)
    con.close()

    v4 = verify.v4(None, None)
    out = {'ts': time.time(), 'kills': args.kills, 'rounds': rounds, 'v4': v4,
           'ok': all(r['ok'] for r in rounds) and v4['ok'],
           'min': round((time.time() - t0) / 60, 1)}
    path = os.path.join(HERE, 'k1_check.json')
    io.open(path, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, indent=2, default=str) + '\n')
    log('exit %.1f min %s -> %s' % (out['min'], 'PASS' if out['ok'] else 'FAIL', path))
    return 0 if out['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
