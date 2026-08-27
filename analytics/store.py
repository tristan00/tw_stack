from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

from decisions import pg

SCHEMA = "analytics"

SPINE_DDL = """
CREATE TABLE IF NOT EXISTS analytics_state(
  tenant           TEXT PRIMARY KEY,
  formula_version  INTEGER NOT NULL,
  input_version    INTEGER,
  watermark        BIGINT NOT NULL DEFAULT 0,
  rows             BIGINT NOT NULL DEFAULT 0,
  source_rows      BIGINT NOT NULL DEFAULT 0,
  source_floor     BIGINT,
  built_ts         DOUBLE PRECISION,
  last_run_ts      DOUBLE PRECISION,
  last_run_seconds DOUBLE PRECISION,
  last_folded      BIGINT NOT NULL DEFAULT 0,
  last_error       TEXT,
  last_error_ts    DOUBLE PRECISION);
"""


def analytics_path(run_dir: str) -> str:
    return "%s search_path=%s" % (pg.dsn(), SCHEMA)


def connect(path: str | None = None, readonly: bool = False, schema: str = SCHEMA):
    con = pg.connect(autocommit=True, readonly=readonly,
                     row_factory=pg.row_factory, search_path="%s, public" % schema)
    if not readonly:
        con.execute("CREATE SCHEMA IF NOT EXISTS %s" % schema)
        con.execute(SPINE_DDL)
    return con


def executemany(con, sql, rows):
    with con.cursor() as cur:
        cur.executemany(sql, rows)


def state(an, tenant: str) -> dict:
    row = an.execute("SELECT * FROM analytics_state WHERE tenant=%s", (tenant,)).fetchone()
    if row is None:
        return {"tenant": tenant, "formula_version": None, "input_version": None,
                "watermark": 0, "rows": 0, "source_rows": 0, "source_floor": None,
                "built_ts": None, "last_run_ts": None, "last_run_seconds": None,
                "last_folded": 0, "last_error": None, "last_error_ts": None}
    return dict(row)


def all_state(an) -> list:
    return [dict(r) for r in an.execute("SELECT * FROM analytics_state ORDER BY tenant")]


def _set(an, tenant: str, **fields):
    fv = fields.pop("formula_version", 0)
    keys = sorted(fields)
    an.execute(
        "INSERT INTO analytics_state(tenant, formula_version%s) VALUES(%%s, %%s%s)"
        " ON CONFLICT(tenant) DO UPDATE SET %s"
        % ("".join(", " + k for k in keys), ", %s" * len(keys),
           ", ".join(["formula_version=excluded.formula_version"]
                     + ["%s=excluded.%s" % (k, k) for k in keys])),
        [tenant, fv] + [fields[k] for k in keys])


def _table_rows(an, name: str) -> int:
    try:
        return int(an.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0])
    except psycopg.Error:
        an.execute("ROLLBACK")
        return -1


def _dep_version(an, tenant) -> int | None:
    deps = getattr(tenant, "DEPENDS_ON", ())
    if not deps:
        return None
    return sum(int(state(an, d)["formula_version"] or 0) for d in deps)


def rebuild_reason(tenant, src, an) -> str | None:
    st = state(an, tenant.NAME)
    if st["formula_version"] is None:
        return None
    if int(st["formula_version"]) != int(tenant.FORMULA_VERSION):
        return ("formula version moved %s -> %s"
                % (st["formula_version"], tenant.FORMULA_VERSION))
    dep = _dep_version(an, tenant)
    if dep is not None and int(st["input_version"] or -1) != int(dep):
        return ("an input's formula version moved %s -> %s"
                % (st["input_version"], dep))
    have = _table_rows(an, tenant.NAME)
    if have < 0:
        return "the tenant's table is missing"
    if have != int(st["rows"]):
        return "table holds %d rows, state recorded %d" % (have, st["rows"])
    hi = tenant.safe_hi(src, an)
    if hi is not None and int(st["watermark"]) > int(hi):
        return ("the watermark is at %s but the source reaches only %s -- it went backwards"
                % (st["watermark"], hi))
    stats = tenant.source_stats(src, int(st["watermark"])) if st["watermark"] else None
    if stats is not None and not isinstance(stats, tuple):
        raise TypeError("%s.source_stats must return (count, floor) or None" % tenant.NAME)
    if stats is not None:
        count, floor = stats
        if count is not None and int(count) != int(st["source_rows"]):
            return ("the source has %s rows at or below the watermark, %s when this was "
                    "built -- it is not the same corpus" % (count, st["source_rows"]))
        if floor is not None and st["source_floor"] is not None \
                and int(floor) != int(st["source_floor"]):
            return ("the source now starts at %s, started at %s when this was built"
                    % (floor, st["source_floor"]))
    return None


def ensure(tenant, src, an) -> str | None:
    try:
        an.execute(tenant.DDL)
    except psycopg.errors.InvalidTableDefinition as e:
        an.execute("ROLLBACK")
        why = "the tenant's DDL no longer fits the table on disk (%s)" % str(e)[:80]
    else:
        why = rebuild_reason(tenant, src, an)
    if why:
        for name in getattr(tenant, "TABLES", (tenant.NAME,)):
            an.execute("DROP TABLE IF EXISTS %s" % name)
        an.execute(tenant.DDL)
        an.execute("BEGIN")
        try:
            an.execute("DELETE FROM %s" % tenant.NAME)
            _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
                 input_version=_dep_version(an, tenant), watermark=0, rows=0,
                 source_rows=0, source_floor=None, built_ts=time.time(),
                 last_error=None, last_error_ts=None)
            an.execute("COMMIT")
        except Exception:
            an.execute("ROLLBACK")
            raise
    return why


def run_tenant(tenant, src, an) -> dict:
    wiped = ensure(tenant, src, an)
    st = state(an, tenant.NAME)
    full = bool(getattr(tenant, "REBUILD_EVERY_PASS", False))
    lo = 0 if full else int(st["watermark"])
    hi = int(tenant.safe_hi(src, an) or 0)
    t0 = time.time()
    if hi <= lo or (full and hi <= 0):
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             last_run_ts=time.time(), last_folded=0)
        return {"tenant": tenant.NAME, "wiped": wiped, "folded": 0, "rows": st["rows"],
                "watermark": lo, "seconds": 0.0}
    an.execute("BEGIN")
    try:
        watermark, written = tenant.step(src, an, lo, hi)
        watermark = int(watermark)
        rows = _table_rows(an, tenant.NAME)
        stats = tenant.source_stats(src, watermark)
        source_rows, floor = (stats if stats is not None else (None, None))
        if source_rows is not None and int(source_rows) != rows:
            raise RuntimeError(
                "%s folded to watermark %d but holds %d rows against %d source rows -- "
                "rows were dropped" % (tenant.NAME, watermark, rows, source_rows))
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             input_version=_dep_version(an, tenant), watermark=watermark, rows=rows,
             source_rows=int(source_rows or 0),
             source_floor=(int(floor) if floor is not None else None),
             built_ts=st["built_ts"] or time.time(), last_run_ts=time.time(),
             last_run_seconds=round(time.time() - t0, 4), last_folded=int(written),
             last_error=None, last_error_ts=None)
        an.execute("COMMIT")
    except Exception as e:
        an.execute("ROLLBACK")
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             last_error=("%s: %s" % (type(e).__name__, e))[:500], last_error_ts=time.time(),
             last_run_ts=time.time())
        raise
    return {"tenant": tenant.NAME, "wiped": wiped, "folded": int(written),
            "rows": rows, "watermark": watermark, "seconds": round(time.time() - t0, 4)}


def order_tenants(tenants: list) -> list:
    by_name = {t.NAME: t for t in tenants}
    out, seen = [], set()

    def visit(t, stack=()):
        if t.NAME in seen:
            return
        if t.NAME in stack:
            raise RuntimeError("analytics tenants form a dependency cycle: %s"
                               % " -> ".join(stack + (t.NAME,)))
        for dep in getattr(t, "DEPENDS_ON", ()):
            if dep not in by_name:
                raise RuntimeError("tenant %r depends on %r, which is not registered"
                                   % (t.NAME, dep))
            visit(by_name[dep], stack + (t.NAME,))
        seen.add(t.NAME)
        out.append(t)

    for t in tenants:
        visit(t)
    return out
