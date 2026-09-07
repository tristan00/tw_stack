from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

SCHEMAS = ("corpus", "dict", "ops", "analytics")


def log(msg):
    sys.stderr.write("%.3f  dbstats %s\n" % (time.time(), msg))


def refresh(con=None):
    t0 = time.time()
    own = con is None
    if own:
        con = pg.connect(app_name="tw-dbstats", autocommit=True,
                         search_path=pg.CORPUS_PATH)
    try:
        now = time.time()
        tables = con.execute(
            "SELECT n.nspname || '.' || c.relname, c.reltuples::bigint,"
            " pg_total_relation_size(c.oid)"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind = 'r' AND n.nspname = ANY(%s)", (list(SCHEMAS),)).fetchall()
        for name, rows, size in tables:
            con.execute(
                "INSERT INTO ops.db_table_stat (tbl, n_rows, bytes, refreshed)"
                " VALUES (%s,%s,%s,%s) ON CONFLICT (tbl) DO UPDATE SET"
                " n_rows = EXCLUDED.n_rows, bytes = EXCLUDED.bytes,"
                " refreshed = EXCLUDED.refreshed", (name, rows, size, now))
        cols = con.execute(
            "SELECT s.schemaname || '.' || s.tablename, s.attname, s.null_frac,"
            " s.n_distinct, left(array_to_string(s.most_common_vals, ','), 200)"
            " FROM pg_stats s WHERE s.schemaname = ANY(%s)", (list(SCHEMAS),)).fetchall()
        for tbl, col, null_frac, n_distinct, sample in cols:
            con.execute(
                "INSERT INTO ops.db_column_stat (tbl, col, null_frac, n_distinct,"
                " sample, refreshed) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (tbl, col) DO UPDATE SET null_frac = EXCLUDED.null_frac,"
                " n_distinct = EXCLUDED.n_distinct, sample = EXCLUDED.sample,"
                " refreshed = EXCLUDED.refreshed",
                (tbl, col, null_frac, n_distinct, sample, now))
    finally:
        if own:
            con.close()
    log("refresh exit %.0f ms  %d tables  %d columns"
        % ((time.time() - t0) * 1000, len(tables), len(cols)))
    return len(tables), len(cols)


if __name__ == "__main__":
    refresh()
