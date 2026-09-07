from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

SCHEMAS = ("corpus", "dict", "ops", "analytics", "ref", "refc")


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
            " pg_total_relation_size(c.oid),"
            " coalesce(s.n_tup_ins, 0) + coalesce(s.n_tup_upd, 0)"
            " + coalesce(s.n_tup_del, 0)"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid"
            " WHERE c.relkind = 'r' AND n.nspname = ANY(%s)", (list(SCHEMAS),)).fetchall()
        con.cursor().executemany(
            "INSERT INTO ops.db_table_stat (tbl, n_rows, bytes, writes, refreshed)"
            " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (tbl) DO UPDATE SET"
            " n_rows = EXCLUDED.n_rows, bytes = EXCLUDED.bytes,"
            " writes = EXCLUDED.writes,"
            " last_write = CASE WHEN EXCLUDED.writes > db_table_stat.writes"
            " THEN EXCLUDED.refreshed ELSE db_table_stat.last_write END,"
            " refreshed = EXCLUDED.refreshed",
            [(name, rows, size, writes, now) for name, rows, size, writes in tables])
        cols = con.execute(
            "SELECT s.schemaname || '.' || s.tablename, s.attname,"
            " format_type(a.atttypid, a.atttypmod), s.null_frac,"
            " s.n_distinct, left(array_to_string(s.most_common_vals, ','), 200)"
            " FROM pg_stats s"
            " JOIN pg_namespace n ON n.nspname = s.schemaname"
            " JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = s.tablename"
            " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = s.attname"
            " WHERE s.schemaname = ANY(%s)", (list(SCHEMAS),)).fetchall()
        con.cursor().executemany(
            "INSERT INTO ops.db_column_stat (tbl, col, pg_type, null_frac, n_distinct,"
            " sample, refreshed) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (tbl, col) DO UPDATE SET pg_type = EXCLUDED.pg_type,"
            " null_frac = EXCLUDED.null_frac,"
            " n_distinct = EXCLUDED.n_distinct, sample = EXCLUDED.sample,"
            " refreshed = EXCLUDED.refreshed",
            [(tbl, col, pgt, nf, nd, sample, now)
             for tbl, col, pgt, nf, nd, sample in cols])
    finally:
        if own:
            con.close()
    log("refresh exit %.0f ms  %d tables  %d columns"
        % ((time.time() - t0) * 1000, len(tables), len(cols)))
    return len(tables), len(cols)


if __name__ == "__main__":
    refresh()
