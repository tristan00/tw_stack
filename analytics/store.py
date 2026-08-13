from __future__ import annotations

"""The analytics database, and the contract every tenant implements.

WHY THIS IS A SEPARATE FILE FROM THE CORPUS. `decisions/store_schema.py` is the
collector's contract with the game. It carries its own SCHEMA_VERSION and its own migration
story, and the collector holds it open at roughly a decision a second. Putting a DERIVED
number in there couples a formula change to a corpus schema change, puts a second writer on
the file the collector is writing, and hands `test_store`'s layout invariant rows it does
not own. The API could not maintain it in any case: `advisor_api/db.py` opens the corpus
read-only by construction, and that property is worth keeping.

WHY THE RUN DIR AND NOT metrics.sqlite. The key's scope decides the file. `decision_id` is
unique only inside one run dir -- this corpus starts at 91, not 1 -- so a per-decision table
keyed in a cross-run database is ambiguous the moment a second run dir exists.
`common.run_dbs()` already documents that exact trap. Facts keyed by something global
(a trial id, a model sha) belong in metrics.sqlite; facts keyed by a corpus id belong here.
A fresh run dir starts with no analytics file, which is the correct behaviour: it is
impossible to serve a row derived from a different corpus.

THE WHOLE FILE IS DISPOSABLE. A full rebuild is seconds. That is what lets this module be
absolute about version drift -- when anything suggests the stored rows and the current
formula might disagree, it deletes and recomputes rather than reconciling. There is never a
`_v2` table and never two definitions in one place.

COMPLETENESS IS COUNTED, NOT ASSUMED. The watermark says how far the fold has reached; it
does not by itself prove nothing was dropped in between. So every pass also records how
many source rows sit at or below the watermark, and a tenant that stores fewer rows than
that has silently lost work. Note this is deliberately NOT an id-contiguity rule: decision
ids can carry permanent gaps, and refusing to advance past one would stall the whole
pipeline forever on a hole that is never going to be filled.
"""

import os
import sqlite3
import time

SPINE_DDL = """
CREATE TABLE IF NOT EXISTS analytics_state(
  tenant           TEXT PRIMARY KEY,
  formula_version  INTEGER NOT NULL,
  input_version    INTEGER,
  watermark        INTEGER NOT NULL DEFAULT 0,
  rows             INTEGER NOT NULL DEFAULT 0,
  source_rows      INTEGER NOT NULL DEFAULT 0,
  source_floor     INTEGER,
  built_ts         REAL,
  last_run_ts      REAL,
  last_run_seconds REAL,
  last_folded      INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT,
  last_error_ts    REAL);
"""

_PRAGMAS = ("PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA busy_timeout=5000")


def analytics_path(run_dir: str) -> str:
    return os.path.join(run_dir, "analytics.sqlite")


def connect(path: str, readonly: bool = False) -> sqlite3.Connection | None:
    """Open the sidecar. None when a readonly caller finds no file."""
    if readonly:
        if not os.path.isfile(path):
            return None
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True,
                              isolation_level=None, timeout=5.0)
        con.row_factory = sqlite3.Row
        return con
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, timeout=30.0)
    con.row_factory = sqlite3.Row
    for p in _PRAGMAS:
        con.execute(p)
    con.executescript(SPINE_DDL)
    return con


def state(an: sqlite3.Connection, tenant: str) -> dict:
    row = an.execute("SELECT * FROM analytics_state WHERE tenant=?", (tenant,)).fetchone()
    if row is None:
        return {"tenant": tenant, "formula_version": None, "input_version": None,
                "watermark": 0, "rows": 0, "source_rows": 0, "source_floor": None,
                "built_ts": None, "last_run_ts": None, "last_run_seconds": None,
                "last_folded": 0, "last_error": None, "last_error_ts": None}
    return dict(row)


def all_state(an: sqlite3.Connection) -> list:
    return [dict(r) for r in an.execute("SELECT * FROM analytics_state ORDER BY tenant")]


def _set(an: sqlite3.Connection, tenant: str, **fields):
    keys = sorted(fields)
    an.execute(
        "INSERT INTO analytics_state(tenant, formula_version, %s) VALUES(?, ?, %s)"
        " ON CONFLICT(tenant) DO UPDATE SET %s"
        % (", ".join(keys), ", ".join("?" * len(keys)),
           ", ".join("%s=excluded.%s" % (k, k) for k in keys)),
        [tenant, fields.get("formula_version", 0)] + [fields[k] for k in keys])


def _table_rows(an: sqlite3.Connection, name: str) -> int:
    try:
        return int(an.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0])
    except sqlite3.Error:
        return -1


def _dep_version(an, tenant) -> int | None:
    """The summed formula version of everything this tenant is derived from."""
    deps = getattr(tenant, "DEPENDS_ON", ())
    if not deps:
        return None
    return sum(int(state(an, d)["formula_version"] or 0) for d in deps)


def rebuild_reason(tenant, src, an) -> str | None:
    """Why the stored rows cannot be trusted, or None."""
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
    """Create the tenant's table, and wipe it if anything drifted. Returns the wipe reason."""
    an.executescript(tenant.DDL)
    why = rebuild_reason(tenant, src, an)
    if why:
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
    """One pass. Everything the pass produces commits together, or none of it does."""
    wiped = ensure(tenant, src, an)
    st = state(an, tenant.NAME)
    full = bool(getattr(tenant, "REBUILD_EVERY_PASS", False))
    lo = 0 if full else int(st["watermark"])
    # Both connections, because a rollup's "how far can I go" is its fact tenant's
    # watermark, which lives in the analytics database rather than the corpus.
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
            # Not a warning. A tenant that stores fewer rows than the source holds at or
            # below its own watermark has dropped work that nothing will ever come back
            # for, and every aggregate built on it would be quietly short.
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
    """Dependencies first, so a rollup never runs ahead of the facts it summarises."""
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
