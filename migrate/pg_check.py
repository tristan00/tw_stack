import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

from decisions import pg


def log(msg):
    sys.stderr.write('%.3f  pg_check %s\n' % (time.time(), msg))


def kill_backend(pid):
    killer = pg.connect(app_name='tw-killer', autocommit=True)
    killer.execute("SELECT pg_terminate_backend(%s)", (pid,))
    killer.close()


def main():
    t0 = time.time()
    log('enter port=%s' % os.environ.get('TW_PG_PORT'))
    conn = pg.Conn('tw-recorder', search_path=pg.CORPUS_PATH)

    with conn.unit('u0') as c:
        pid = c.execute("SELECT pg_backend_pid()").fetchone()[0]
        assert c.execute("SELECT 1").fetchone()[0] == 1
    log('u0 ok pid=%d' % pid)

    name = conn.execute("SELECT application_name FROM pg_stat_activity"
                        " WHERE pid = pg_backend_pid()").fetchone()[0]
    path = conn.execute("SHOW search_path").fetchone()[0]
    assert name == 'tw-recorder', name
    assert path.startswith('corpus'), path
    log('application_name=%r search_path=%r' % (name, path))

    kill_backend(pid)
    log('backend %d terminated' % pid)

    failed = False
    try:
        with conn.unit('u1') as c:
            c.execute("SELECT 1")
    except psycopg.Error as exc:
        failed = True
        log('u1 failed as expected: %s' % type(exc).__name__)
    assert failed, 'u1 should have failed on the killed backend'
    assert conn.reconnects == 1, 'expected exactly one reconnect, got %d' % conn.reconnects

    with conn.unit('u2') as c:
        new_pid = c.execute("SELECT pg_backend_pid()").fetchone()[0]
        assert c.execute("SELECT 1").fetchone()[0] == 1
    assert new_pid != pid, 'backend pid did not change'
    log('u2 ok on new pid=%d after %d reconnect' % (new_pid, conn.reconnects))

    conn.close()
    log('exit %.0f ms  PASS' % ((time.time() - t0) * 1000))


if __name__ == '__main__':
    main()
