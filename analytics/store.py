from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

SCHEMA = "analytics2"


def log(msg):
    sys.stderr.write("%.3f  astore %s\n" % (time.time(), msg))


def connect(readonly: bool = False):
    t0 = time.time()
    con = pg.connect(app_name="tw-analytics", autocommit=True, readonly=readonly,
                     row_factory=pg.row_factory,
                     search_path="%s,corpus,dict,ops,public" % SCHEMA)
    log("connect readonly=%s %.0f ms" % (readonly, (time.time() - t0) * 1000))
    return con


def executemany(con, sql, rows):
    with con.cursor() as cur:
        cur.executemany(sql, rows)


def state(an, tenant: str) -> dict:
    row = an.execute("SELECT * FROM state WHERE tenant=%s", (tenant,)).fetchone()
    if row is None:
        return {"tenant": tenant, "formula_version": None, "watermark": 0,
                "built_ts": None, "last_run_ts": None, "last_run_seconds": None,
                "last_error": None}
    return dict(row)


def all_state(an) -> list:
    return [dict(r) for r in an.execute("SELECT * FROM state ORDER BY tenant")]


def _set(an, tenant: str, **fields):
    fv = fields.pop("formula_version", 0)
    keys = sorted(fields)
    an.execute(
        "INSERT INTO state(tenant, formula_version%s) VALUES(%%s, %%s%s)"
        " ON CONFLICT(tenant) DO UPDATE SET %s"
        % ("".join(", " + k for k in keys), ", %s" * len(keys),
           ", ".join(["formula_version=excluded.formula_version"]
                     + ["%s=excluded.%s" % (k, k) for k in keys])),
        [tenant, fv] + [fields[k] for k in keys])


def reset(an, tenant):
    t0 = time.time()
    log("reset %s enter" % tenant.NAME)
    an.execute("BEGIN")
    try:
        for name in getattr(tenant, "TABLES", (tenant.NAME,)):
            an.execute("DELETE FROM %s" % name)
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             watermark=0, built_ts=time.time(), last_error=None)
        an.execute("COMMIT")
    except Exception:
        an.execute("ROLLBACK")
        raise
    log("reset %s exit %.0f ms" % (tenant.NAME, (time.time() - t0) * 1000))


def ensure(tenant, an) -> str | None:
    st = state(an, tenant.NAME)
    if st["formula_version"] is None:
        reset(an, tenant)
        return "first build"
    if int(st["formula_version"]) != int(tenant.FORMULA_VERSION):
        reset(an, tenant)
        return ("formula version moved %s -> %s"
                % (st["formula_version"], tenant.FORMULA_VERSION))
    return None


def run_tenant(tenant, src, an) -> dict:
    wiped = ensure(tenant, an)
    st = state(an, tenant.NAME)
    lo = int(st["watermark"])
    hi = int(tenant.safe_hi(src, an) or 0)
    t0 = time.time()
    if hi <= lo:
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             last_run_ts=time.time())
        return {"tenant": tenant.NAME, "wiped": wiped, "folded": 0,
                "watermark": lo, "seconds": 0.0}
    an.execute("BEGIN")
    try:
        watermark, written = tenant.step(src, an, lo, hi)
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             watermark=int(watermark),
             built_ts=st["built_ts"] or time.time(), last_run_ts=time.time(),
             last_run_seconds=round(time.time() - t0, 4), last_error=None)
        an.execute("COMMIT")
    except Exception as e:
        an.execute("ROLLBACK")
        _set(an, tenant.NAME, formula_version=int(tenant.FORMULA_VERSION),
             last_error=("%s: %s" % (type(e).__name__, e))[:500],
             last_run_ts=time.time())
        raise
    return {"tenant": tenant.NAME, "wiped": wiped, "folded": int(written),
            "watermark": int(watermark), "seconds": round(time.time() - t0, 4)}


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
