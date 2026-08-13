from __future__ import annotations

"""The analytics layer's gates.

Two kinds of thing are checked here, and they fail for different reasons.

THE METRICS are checked against an independent oracle and against hand-computed constants.
The Spearman implementation is a RESTORATION -- the original was deleted with the old
dashboard in 7a2d3d0 -- so it is checked against a verbatim copy of that original, kept
below. Checking a restoration against itself proves nothing.

THE STORE is checked for the failure that precomputation introduces and querying does not:
serving a number derived from a corpus that has since changed. A cache that cannot expire
is worse than no cache, and a precomputed table that cannot notice its source moved is the
same defect with a longer memory. So: a formula-version bump must WIPE, source drift must
force a rebuild, the watermark must never step over a hole, and it must never commit ahead
of the rows it describes.

Offline. Builds its own temporary databases and needs no corpus and no game.
"""

import os
import random
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from analytics import metrics as M
from analytics import store as S


# ---------------------------------------------------------------- the oracle

def reference_spearman(xs, ys):
    """Verbatim from `git show 7a2d3d0^:advisor_ui/ui.py`, lines 152-176."""
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


# ---------------------------------------------------------------- metric gates

def test_spearman_matches_the_reference(fail):
    """500 vectors, deliberately tie-heavy, against the deleted implementation."""
    rng = random.Random(20260812)
    worst = 0.0
    for case in range(500):
        n = rng.randint(3, 60)
        # A small value pool forces ties; the graph model really does tie, and ties are
        # the only place the two implementations could plausibly diverge.
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
    """One hand-computed decision. Every constant below was worked out on paper."""
    cat = [1, 2, 3, 4, 5]
    gnn = [2, 1, 4, 3, 5]
    checks = (
        # sum d^2 = 4, rho = 1 - 6*4/(5*24)
        ("rho", M.spearman(cat, gnn), 0.8),
        # 8 concordant, 2 discordant, no ties: (8-2)/10
        ("tau_b", M.kendall_tau_b(cat, gnn), 0.6),
        # (1-p)*2.8251 + (5/5)*0.9^5 = 0.28251 + 0.59049
        ("rbo", M.rbo(cat, gnn), 0.873),
        # {0,1,2} & {1,0,3} = 2, over min(3, 5)
        ("top3_overlap", M.topk_overlap(cat, gnn, 3), 2.0 / 3.0),
        # {0,1,2,3} & {1,0,3,2} = 4, over min(4, 5)
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
    """Over two offers every correlation is +-1 by construction. Reporting that as"""
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
    # tau-b's denominator must shrink on the tied side, so a tied vector cannot reach 1.0
    tied = M.kendall_tau_b([1, 1, 3], [1, 2, 3])
    if tied is None or not (0.99 < tied <= 1.0000000001):
        # one tied pair on x, none on y: C=2, D=0, n0=3, Tx=1 -> 2/sqrt(2*3) = 0.8165
        pass
    want = 2.0 / (2.0 * 3.0) ** 0.5
    if tied is None or abs(tied - want) > 1e-12:
        fail("kendall_tau_b([1,1,3],[1,2,3]) is %r, hand-computed as %.17g" % (tied, want))
    return "ties averaged, and tau-b's denominator corrects for them"


def test_top_measures_are_tie_safe(fail):
    """A tied top must not depend on which index happened to sort first."""
    if not M.same_best([1, 1, 3], [1, 2, 3]):
        fail("both models rated offer 0 best, but same_best said no -- it is reporting a "
             "tiebreak instead of the models")
    if not M.same_best([1, 1, 3], [2, 1, 3]):
        fail("the graph model's best (offer 1) is tied-best for the tree model too")
    return "tied tops compare as sets"


# ---------------------------------------------------------------- store gates

class _Tenant:
    """A minimal tenant, used to exercise the contract without a corpus."""
    NAME = "probe"
    FORMULA_VERSION = 1
    SOURCE = "decisions"
    DEPENDS_ON = ()
    DDL = ("CREATE TABLE IF NOT EXISTS probe("
           " decision_id INTEGER PRIMARY KEY, doubled INTEGER NOT NULL) WITHOUT ROWID;")

    def __init__(self, version=1, boom_at=None, drop=None):
        self.FORMULA_VERSION = version
        self.boom_at = boom_at
        self.drop = drop

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) FROM decisions").fetchone()
        return int(row[0] or 0)

    def source_stats(self, src, hi):
        r = src.execute("SELECT COUNT(*), MIN(decision_id) FROM decisions"
                        " WHERE decision_id <= ?", (hi,)).fetchone()
        return int(r[0] or 0), r[1]

    def step(self, src, an, lo, hi):
        rows = src.execute("SELECT decision_id FROM decisions WHERE decision_id > ?"
                           " AND decision_id <= ? ORDER BY decision_id", (lo, hi)).fetchall()
        written = 0
        for (did,) in rows:
            if self.boom_at is not None and did == self.boom_at:
                raise RuntimeError("injected failure at %d" % did)
            if self.drop is not None and did == self.drop:
                continue          # simulate a tenant that silently loses a row
            an.execute("INSERT INTO probe(decision_id, doubled) VALUES(?, ?)"
                       " ON CONFLICT(decision_id) DO UPDATE SET doubled=excluded.doubled",
                       (did, did * 2))
            written += 1
        return hi, written


def _corpus(path, ids):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE decisions(decision_id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO decisions VALUES(?)", [(i,) for i in ids])
    con.commit()
    return con


class _Sandbox:
    """A temp directory whose connections are closed before it is removed."""

    def __enter__(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = self._d.name
        self.cons = []
        return self

    def corpus(self, name, ids):
        con = _corpus(os.path.join(self.dir, name), ids)
        self.cons.append(con)
        return con

    def analytics(self, name="analytics.sqlite"):
        con = S.connect(os.path.join(self.dir, name))
        self.cons.append(con)
        return con

    def __exit__(self, *exc):
        for c in self.cons:
            try:
                c.close()
            except Exception:
                pass
        self._d.cleanup()
        return False


def test_formula_version_bump_wipes_and_rebuilds(fail):
    with _Sandbox() as box:
        src = box.corpus("src.sqlite", range(91, 111))
        an = box.analytics()
        t = _Tenant(version=1)
        S.run_tenant(t, src, an)
        # Poison a row with a value the formula could never produce.
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
        src = box.corpus("src.sqlite", range(91, 111))
        an = box.analytics()
        t = _Tenant()
        S.run_tenant(t, src, an)
        # The corpus is replaced by a different one that happens to reach the same max id.
        src.execute("DELETE FROM decisions WHERE decision_id < 101")
        src.commit()
        S.run_tenant(t, src, an)
        n = an.execute("SELECT COUNT(*) FROM probe").fetchone()[0]
        if n != 10:
            fail("the source lost rows below the watermark and the table still holds %d "
                 "rows -- it is describing a corpus that no longer exists" % n)
    return "a shrunken or replaced source rebuilds instead of appending"


def test_no_source_row_below_the_watermark_is_uncomputed(fail):
    """The completeness invariant, and the reason it is counted rather than assumed."""
    with _Sandbox() as box:
        # A source with a real gap: folding must still reach the end.
        src = box.corpus("src.sqlite", [91, 92, 93, 97, 98])
        an = box.analytics()
        S.run_tenant(_Tenant(), src, an)
        st = S.state(an, "probe")
        if st["watermark"] != 98 or st["rows"] != 5:
            fail("a permanent gap stalled the fold at watermark=%r rows=%r; ids 94-96 do "
                 "not exist and never will" % (st["watermark"], st["rows"]))

        # And a tenant that loses one must be caught, not tolerated.
        src2 = box.corpus("src2.sqlite", range(91, 101))
        an2 = box.analytics("a2.sqlite")
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
        src = box.corpus("src.sqlite", range(91, 111))
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
