from __future__ import annotations


import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from analytics import metrics as M
from analytics import store as S
from decisions import pg, pgtest


def reference_spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return (num / (dx * dy)) if dx and dy else None


def test_spearman_matches_the_reference(fail):
    rng = random.Random(20260812)
    worst = 0.0
    for case in range(500):
        n = rng.randint(3, 60)
        pool = rng.choice((3, 5, n))
        xs = [rng.randint(1, pool) for _ in range(n)]
        ys = [rng.randint(1, pool) for _ in range(n)]
        want, got = reference_spearman(xs, ys), M.spearman(xs, ys)
        if (want is None) != (got is None):
            fail("case %d n=%d: reference says %r, restoration says %r" % (case, n, want, got))
            continue
        if want is None:
            continue
        worst = max(worst, abs(want - got))
        if abs(want - got) > 1e-12:
            fail("case %d n=%d: reference %.17g vs restoration %.17g (delta %.3g)"
                 % (case, n, want, got, abs(want - got)))
    return "worst disagreement with the deleted implementation: %.3g" % worst


def test_reference_case_is_exact(fail):
    cat = [1, 2, 3, 4, 5]
    gnn = [2, 1, 4, 3, 5]
    checks = (
        ("rho", M.spearman(cat, gnn), 0.8),
        ("tau_b", M.kendall_tau_b(cat, gnn), 0.6),
        ("rbo", M.rbo(cat, gnn), 0.873),
        ("top3_overlap", M.topk_overlap(cat, gnn, 3), 2.0 / 3.0),
        ("top5_overlap", M.topk_overlap(cat, gnn, 5), 1.0),
    )
    for name, got, want in checks:
        if got is None or abs(got - want) > 1e-12:
            fail("%s on the reference case is %r, hand-computed as %.17g" % (name, got, want))
    if M.same_best(cat, gnn):
        fail("same_best is True on the reference case: the tree model's best is offer 0 "
             "and the graph model's is offer 1")
    if M.cross_ranks(cat, gnn) != (2, 2):
        fail("cross_ranks is %r, hand-computed as (2, 2)" % (M.cross_ranks(cat, gnn),))
    return "rho 0.8, tau_b 0.6, rbo 0.873, top3 2/3 -- all exact"


def test_too_few_returns_none_not_one(fail):
    for n in (0, 1, 2):
        xs, ys = list(range(1, n + 1)), list(range(n, 0, -1))
        for name, got in (("spearman", M.spearman(xs, ys)),
                          ("kendall_tau_b", M.kendall_tau_b(xs, ys)),
                          ("rbo", M.rbo(xs, ys))):
            if got is not None:
                fail("%s over %d offers returned %r -- it must decline" % (name, n, got))
    if M.spearman([1, 1, 1], [1, 2, 3]) is not None:
        fail("a model that gave every offer the same rank has no ordering to correlate, "
             "so rho must be None rather than 0.0")
    return "n<3 and constant rankings both decline"


def test_ties_are_averaged(fail):
    r = M.tie_averaged_ranks([1, 1, 3])
    if list(r) != [1.5, 1.5, 3.0]:
        fail("tie_averaged_ranks([1,1,3]) is %r, must be [1.5, 1.5, 3.0]" % (list(r),))
    tied = M.kendall_tau_b([1, 1, 3], [1, 2, 3])
    if tied is None or not (0.99 < tied <= 1.0000000001):
        pass
    want = 2.0 / (2.0 * 3.0) ** 0.5
    if tied is None or abs(tied - want) > 1e-12:
        fail("kendall_tau_b([1,1,3],[1,2,3]) is %r, hand-computed as %.17g" % (tied, want))
    return "ties averaged, and tau-b's denominator corrects for them"


def test_top_measures_are_tie_safe(fail):
    if not M.same_best([1, 1, 3], [1, 2, 3]):
        fail("both models rated offer 0 best, but same_best said no -- it is reporting a "
             "tiebreak instead of the models")
    if not M.same_best([1, 1, 3], [2, 1, 3]):
        fail("the graph model's best (offer 1) is tied-best for the tree model too")
    return "tied tops compare as sets"


class _Tenant:
    NAME = "probe"
    FORMULA_VERSION = 1
    SOURCE = "decisions"
    DEPENDS_ON = ()
    DDL = ("CREATE TABLE IF NOT EXISTS probe("
           " decision_id BIGINT PRIMARY KEY, doubled BIGINT NOT NULL);")

    def __init__(self, version=1, boom_at=None, drop=None, blind=False):
        self.FORMULA_VERSION = version
        self.boom_at = boom_at
        self.drop = drop
        self.blind = blind

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) FROM decisions").fetchone()
        return int(row[0] or 0)

    def source_stats(self, src, hi):
        if self.blind:
            return None
        r = src.execute("SELECT COUNT(*), MIN(decision_id) FROM decisions"
                        " WHERE decision_id <= %s", (hi,)).fetchone()
        return int(r[0] or 0), r[1]

    def step(self, src, an, lo, hi):
        rows = src.execute("SELECT decision_id FROM decisions WHERE decision_id > %s"
                           " AND decision_id <= %s ORDER BY decision_id", (lo, hi)).fetchall()
        written = 0
        for (did,) in rows:
            if self.boom_at is not None and did == self.boom_at:
                raise RuntimeError("injected failure at %d" % did)
            if self.drop is not None and did == self.drop:
                continue
            an.execute("INSERT INTO probe(decision_id, doubled) VALUES(%s, %s)"
                       " ON CONFLICT(decision_id) DO UPDATE SET doubled=excluded.doubled",
                       (did, did * 2))
            written += 1
        return hi, written


class _Sandbox:

    def __enter__(self):
        pgtest.fresh()
        self.cons = []
        self._n = 0
        return self

    def corpus(self, name, ids):
        schema = "src%d" % self._n
        self._n += 1
        con = pg.connect(autocommit=True, row_factory=pg.row_factory,
                         search_path=schema)
        con.execute("CREATE SCHEMA %s" % schema)
        con.execute("CREATE TABLE decisions(decision_id BIGINT PRIMARY KEY)")
        with con.cursor() as cur:
            cur.executemany("INSERT INTO decisions VALUES(%s)", [(i,) for i in ids])
        self.cons.append(con)
        return con

    def analytics(self, name="analytics"):
        schema = "an%d" % self._n
        self._n += 1
        con = S.connect(schema=schema)
        self.cons.append(con)
        return con

    def __exit__(self, *exc):
        for c in self.cons:
            try:
                c.close()
            except Exception:
                pass
        pgtest.drop()
        return False


def test_formula_version_bump_wipes_and_rebuilds(fail):
    with _Sandbox() as box:
        src = box.corpus("src", range(91, 111))
        an = box.analytics()
        t = _Tenant(version=1)
        S.run_tenant(t, src, an)
        an.execute("UPDATE probe SET doubled = -999 WHERE decision_id = 95")
        an.commit()
        t.FORMULA_VERSION = 2
        S.run_tenant(t, src, an)
        got = an.execute("SELECT doubled FROM probe WHERE decision_id = 95").fetchone()[0]
        if got != 190:
            fail("a FORMULA_VERSION bump left the old row behind (doubled=%r): two "
                 "definitions are now mixed in one table" % got)
        n = an.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
        if n != 20:
            fail("after the rebuild the table holds %d rows, expected 20" % n)
    return "a formula change wipes rather than mixing definitions"


def test_source_drift_forces_rebuild(fail):
    with _Sandbox() as box:
        src = box.corpus("src", range(91, 111))
        an = box.analytics()
        t = _Tenant()
        S.run_tenant(t, src, an)
        src.execute("DELETE FROM decisions WHERE decision_id < 101")
        src.commit()
        S.run_tenant(t, src, an)
        n = an.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
        if n != 10:
            fail("the source lost rows below the watermark and the table still holds %d "
                 "rows -- it is describing a corpus that no longer exists" % n)
    return "a shrunken or replaced source rebuilds instead of appending"


def test_a_watermark_ahead_of_its_source_rebuilds(fail):
    with _Sandbox() as box:
        src = box.corpus("src", range(91, 111))
        an = box.analytics()
        t = _Tenant(blind=True)
        S.run_tenant(t, src, an)
        src.execute("DELETE FROM decisions WHERE decision_id > 100")
        src.commit()
        S.run_tenant(t, src, an)
        st = S.state(an, "probe")
        n = an.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
        if st["watermark"] != 100 or n != 10:
            fail("the source shrank to 100 and the tenant still reports watermark=%r "
                 "rows=%r -- a rollup that cannot count its source would describe a "
                 "corpus that no longer exists for ever, because every later pass sees "
                 "a hi below its own watermark and does nothing"
                 % (st["watermark"], n))
    return "a watermark past the end of its source rebuilds, counted or not"


def test_no_source_row_below_the_watermark_is_uncomputed(fail):
    with _Sandbox() as box:
        src = box.corpus("src", [91, 92, 93, 97, 98])
        an = box.analytics()
        S.run_tenant(_Tenant(), src, an)
        st = S.state(an, "probe")
        if st["watermark"] != 98 or st["rows"] != 5:
            fail("a permanent gap stalled the fold at watermark=%r rows=%r; ids 94-96 do "
                 "not exist and never will" % (st["watermark"], st["rows"]))

        src2 = box.corpus("src2", range(91, 101))
        an2 = box.analytics("a2")
        try:
            S.run_tenant(_Tenant(drop=95), src2, an2)
            fail("a tenant dropped a source row and the pass was accepted -- every "
                 "aggregate built on it would be silently short")
        except RuntimeError as e:
            if "dropped" not in str(e):
                fail("dropped rows raised the wrong error: %s" % e)
        if S.state(an2, "probe")["watermark"] != 0:
            fail("the dropped-row pass still advanced the watermark")
    return "a gap does not stall the fold; a dropped row fails the pass"


def test_watermark_and_rows_commit_together(fail):
    with _Sandbox() as box:
        src = box.corpus("src", range(91, 111))
        an = box.analytics()
        try:
            S.run_tenant(_Tenant(boom_at=100), src, an)
        except RuntimeError:
            pass
        st = S.state(an, "probe")
        n = an.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
        if st["watermark"] != 0 or n != 0:
            fail("a failure mid-pass left watermark=%r rows=%r -- the pass must be one "
                 "transaction, so a crash can only ever leave the watermark BEHIND the "
                 "data, never ahead of it" % (st["watermark"], n))
        if not st["last_error"]:
            fail("the failure was not recorded in last_error, so it would be invisible")
    return "a failed pass advances nothing and records why"


def test_every_tenant_implements_the_contract(fail):
    from analytics.tenants import TENANTS
    need = ("NAME", "FORMULA_VERSION", "SOURCE", "DDL", "safe_hi", "step", "source_stats")
    for t in TENANTS:
        for attr in need:
            if not hasattr(t, attr):
                fail("tenant %r has no %s" % (getattr(t, "NAME", t), attr))
        name = getattr(t, "NAME", None)
        if name and "CREATE TABLE" in getattr(t, "DDL", "") and name not in t.DDL:
            fail("tenant %r declares a DDL that does not create a table called %r"
                 % (name, name))
        for dep in getattr(t, "DEPENDS_ON", ()):
            if dep not in [x.NAME for x in TENANTS]:
                fail("tenant %r depends on %r, which is not registered" % (name, dep))
    return "%d tenants implement the contract" % len(TENANTS)


TESTS = [
    test_spearman_matches_the_reference,
    test_reference_case_is_exact,
    test_too_few_returns_none_not_one,
    test_ties_are_averaged,
    test_top_measures_are_tie_safe,
    test_formula_version_bump_wipes_and_rebuilds,
    test_source_drift_forces_rebuild,
    test_a_watermark_ahead_of_its_source_rebuilds,
    test_no_source_row_below_the_watermark_is_uncomputed,
    test_watermark_and_rows_commit_together,
    test_every_tenant_implements_the_contract,
]


def main():
    problems = []
    for fn in TESTS:
        try:
            note = fn(problems.append)
        except Exception as e:
            problems.append("%s raised %s: %s" % (fn.__name__, type(e).__name__, e))
            note = None
        print("  %-46s %s" % (fn.__name__.replace("test_", ""), note or ""))
    for p in problems:
        print("  FAIL %s" % p)
    if problems:
        print("\n%d analytics gate(s) failed" % len(problems))
        return 1
    print("\nanalytics gates pass: the restored rho matches the deleted implementation, "
          "and the store cannot mix two definitions or outrun its source")
    return 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
