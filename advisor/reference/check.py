from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def log(msg):
    sys.stderr.write('%.3f  refcheck %s\n' % (time.time(), msg))


def ensure(timeout=600):
    t0 = time.time()
    log('ensure enter')
    proc = subprocess.run(
        [sys.executable, '-m', 'advisor.reference.build_reference'],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    tail = [l for l in (proc.stderr or '').strip().split('\n') if l.strip()][-1:]
    for line in tail:
        log(line.split('refbuild ', 1)[-1])
    if proc.returncode != 0:
        raise RuntimeError('reference build failed: %s' % (tail or proc.stdout)[-1:])
    log('ensure exit %.0f ms' % ((time.time() - t0) * 1000))
    return proc.returncode


if __name__ == '__main__':
    sys.exit(ensure())
