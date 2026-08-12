"""The quality gates for the dashboard.

The requirements here are the ones the previous dashboard's two linters existed to
enforce. Their MECHANISM was regexes over server-rendered HTML, which could only ever
check what happened to reach the markup; these check the same requirements one layer
earlier, against the data, where a violation is unambiguous.

  every view answers, none 404s, hangs or renders empty  -> test_every_endpoint_answers
  a view never contradicts the corpus                    -> test_api_agrees_with_sql
  every count says which population it counted           -> test_every_count_names_its_population
  no empty or constant column ships silently             -> test_constant_columns_are_reported
  the model tiles are told apart before being checked    -> test_correlation_tiles_are_separable
  an arm with decisions never renders as zero            -> test_arm_with_decisions_is_not_zero

Two more exist because they are corpus traps rather than UI rules, and both cost real
debugging time:

  test_campaign_id_join_trap -- `target_rows.campaign_id` is a key STRING while
  `campaigns.campaign_id` is an integer surrogate. The obvious join returns zero rows and
  raises nothing. The test asserts the wrong form stays empty, so the day someone
  "simplifies" the join they get a failure instead of an empty dashboard.

  test_outcome_join_is_a_key -- the endings log carries no campaign key, so campaigns are
  matched to their ending by (faction, most recent preceding campaign). If that ever
  stops being one-to-one it is not a key and the outcomes column is fiction.

Run: D:\\totalwar_runner\\.venv\\Scripts\\python.exe advisor_api/test_api.py
"""

from __future__ import annotations

import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Starlette warns that its test client will want httpx2. It is a library-version notice,
# not a result, and check.py reports each harness by its LAST output line -- so left
# alone it becomes the summary and hides whether the checks actually passed. Matched on
# the message rather than the module: the warning is attributed to fastapi.testclient,
# which re-exports it, not to the starlette module that raises it.
warnings.filterwarnings("ignore", message=r".*httpx.*")

from fastapi.testclient import TestClient

from advisor_api import db, queries as q
from advisor_api.app import app

# Every endpoint must answer within this, at the corpus size on disk. It is a ceiling on
# the SLOWEST call, not an average: the point is that no view is the one that hangs.
BUDGET_MS = 400.0

GET_ENDPOINTS = [
    "/api/health",
    "/api/run",
    "/api/campaigns",
    "/api/campaigns/starts",
    "/api/campaigns/matrix?kind=action",
    "/api/campaigns/matrix?kind=interrupt",
    "/api/decisions",
    "/api/decisions/actions",
    "/api/decisions/menus",
    "/api/decisions/timeline",
    "/api/models",
    "/api/models/forcing",
    "/api/models/agreement",
    "/api/models/correlations",
    "/api/models/training",
    "/api/infra",
]

import atexit

# Entered as a context manager so the app's LIFESPAN runs -- without it the process probe
# never starts, every service reports down, and the tests quietly exercise a different
# application than the one that ships. The empty-column gate is what surfaced that: the
# `started` field was empty on every service row here and populated in the real server.
_client_cm = TestClient(app)
_client = _client_cm.__enter__()
atexit.register(_client_cm.__exit__, None, None, None)


def _await_first_probe(timeout=15.0):
    """Block until the process probe has taken a sample, or give up.

    The probe is asynchronous by design, so a test that reads services immediately races
    it. Waiting is honest; asserting against a half-initialised probe is not.
    """
    from advisor_api import proc
    deadline = time.time() + timeout
    while time.time() < deadline:
        _procs, at = proc.snapshot()
        if at:
            return True
        time.sleep(0.25)
    return False


_await_first_probe()


def _walk(node, path="$"):
    """Every dict AND every list in a response, with the path that reached it.

    Yielding the list itself matters: the first version recursed into lists but only ever
    yielded dicts, so a check written as `if isinstance(node, list)` matched nothing and
    passed by inspecting zero rows. It was the empty-column check -- a check that silently
    checked nothing, which is the exact defect it exists to catch.
    """
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, "%s.%s" % (path, k))
    elif isinstance(node, list):
        yield path, node
        for i, v in enumerate(node):
            yield from _walk(v, "%s[%d]" % (path, i))


def _all_responses():
    out = {}
    for ep in GET_ENDPOINTS:
        r = _client.get(ep)
        out[ep] = r
    # the two keyed views, resolved from live data rather than hardcoded
    camps = _client.get("/api/campaigns").json()
    if camps.get("rows"):
        key = camps["rows"][0]["campaign"]["raw"]
        out["/api/campaigns/<key>"] = _client.get("/api/campaigns/%s" % key)
    decs = _client.get("/api/decisions").json()
    if decs.get("rows"):
        did = decs["rows"][0]["decision_id"]
        out["/api/decisions/<id>"] = _client.get("/api/decisions/%d" % did)
    return out


# ----------------------------------------------------------------------------------------

def test_every_endpoint_answers():
    bad = []
    for ep in GET_ENDPOINTS:
        t = time.time()
        r = _client.get(ep)
        ms = (time.time() - t) * 1000
        if r.status_code != 200:
            bad.append("%s -> HTTP %s" % (ep, r.status_code))
            continue
        body = r.text
        if "Traceback (most recent" in body:
            bad.append("%s -> traceback in body" % ep)
        if len(body) < 60:
            bad.append("%s -> nearly empty (%d chars)" % (ep, len(body)))
        if ms > BUDGET_MS:
            bad.append("%s -> %.0f ms, over the %.0f ms budget" % (ep, ms, BUDGET_MS))
    assert not bad, "endpoints that did not answer cleanly:\n  " + "\n  ".join(bad)


def test_keyed_views_answer():
    bad = []
    for ep, r in _all_responses().items():
        if "<" not in ep:
            continue
        if r.status_code != 200:
            bad.append("%s -> HTTP %s" % (ep, r.status_code))
    assert not bad, "\n  ".join(bad)


def test_unknown_ids_404_rather_than_500():
    """A missing object is a 404 with a sentence, never a stack trace."""
    for ep in ("/api/campaigns/no_such_campaign_key", "/api/decisions/999999999"):
        r = _client.get(ep)
        assert r.status_code == 404, "%s -> %s" % (ep, r.status_code)
        assert "Traceback" not in r.text


def test_every_count_names_its_population():
    """A count without its population is the defect this rule exists to prevent.

    The dashboard once showed campaigns as 117, 118, 124 and 126 on adjacent tabs, each
    correct for a different population and every one labelled just "campaigns".
    """
    bad = []
    for ep, r in _all_responses().items():
        if r.status_code != 200:
            continue
        for path, node in _walk(r.json()):
            if not isinstance(node, dict):
                continue
            if {"value", "noun", "population"} <= set(node):
                if not str(node.get("population") or "").strip():
                    bad.append("%s %s -> %r" % (ep, path, node))
    assert not bad, "counts with no population:\n  " + "\n  ".join(bad)


def test_every_rate_carries_its_denominator():
    bad = []
    for ep, r in _all_responses().items():
        if r.status_code != 200:
            continue
        for path, node in _walk(r.json()):
            if not isinstance(node, dict):
                continue
            if {"n", "of", "noun", "population"} <= set(node):
                if node.get("of") is None:
                    bad.append("%s %s -> no denominator" % (ep, path))
                if not str(node.get("population") or "").strip():
                    bad.append("%s %s -> no population" % (ep, path))
                if node.get("of") and node["n"] > node["of"]:
                    bad.append("%s %s -> n=%s exceeds of=%s"
                               % (ep, path, node["n"], node["of"]))
    assert not bad, "malformed rates:\n  " + "\n  ".join(bad)


def test_api_agrees_with_sql():
    """Every headline number, re-derived straight from SQL.

    This is the check the old dashboard could not make: a column wired to the wrong
    variable renders perfectly, so comparing the page against itself proves nothing.
    """
    con = db.connect()
    run = _client.get("/api/run").json()
    totals = {t["noun"]: t["value"] for t in run["totals"]}

    sql_campaigns = con.execute(
        "SELECT COUNT(DISTINCT campaign_id) FROM decision_points").fetchone()[0]
    sql_decisions = con.execute("SELECT COUNT(*) FROM decision_points").fetchone()[0]
    sql_offers = con.execute("SELECT COUNT(*) FROM action_offers").fetchone()[0]
    sql_confirmed = con.execute(
        "SELECT COUNT(*) FROM action_taken WHERE counted=1").fetchone()[0]

    assert totals["campaigns"] == sql_campaigns, (totals["campaigns"], sql_campaigns)
    assert totals["decisions"] == sql_decisions, (totals["decisions"], sql_decisions)
    assert totals["offers"] == sql_offers, (totals["offers"], sql_offers)
    assert totals["actions"] == sql_confirmed, (totals["actions"], sql_confirmed)

    # the campaign list must cover exactly the campaigns that recorded a decision
    rows = _client.get("/api/campaigns").json()["rows"]
    assert len(rows) == sql_campaigns, (len(rows), sql_campaigns)

    # and the log's unfiltered total must equal the action row count
    total = _client.get("/api/decisions").json()["total"]["value"]
    assert total == con.execute("SELECT COUNT(*) FROM action_taken").fetchone()[0]


def test_campaign_id_join_trap():
    """target_rows.campaign_id is a KEY STRING; campaigns.campaign_id is an integer.

    The natural-looking join silently returns nothing. Asserting that keeps anyone from
    "tidying" the correct join into the broken one -- it fails loudly instead.
    """
    con = db.connect()
    wrong = con.execute("SELECT COUNT(*) FROM target_rows t"
                        " JOIN campaigns c ON t.campaign_id = c.campaign_id").fetchone()[0]
    right = con.execute("SELECT COUNT(*) FROM target_rows t"
                        " JOIN campaigns c ON t.campaign_id = c.campaign_key").fetchone()[0]
    assert wrong == 0, ("the id/key join returned %d rows -- if the schema changed, this "
                        "test and every join in queries.py must be revisited together"
                        % wrong)
    assert right > 0, "the key join returned nothing; target_rows is not joinable"


def test_outcome_join_is_a_key():
    """No campaign may be claimed by two endings. If it can be, it is not a key."""
    con = db.connect()
    claimed = q.join_outcomes(con)
    assert claimed, "no ending joined to any campaign -- the outcome column would be empty"
    # join_outcomes is a dict keyed by campaign, so a collision cannot survive it; re-derive
    # the multiplicity from the source to prove the mapping is genuinely one-to-one.
    seen = {}
    for ckey, pm in claimed.items():
        sig = (pm.get("faction"), pm.get("ts"))
        seen.setdefault(sig, []).append(ckey)
    dupes = {s: c for s, c in seen.items() if len(c) > 1}
    assert not dupes, "one ending claimed several campaigns: %s" % dupes


def test_outcome_coverage_is_reported_not_hidden():
    """Endings that belong to earlier run dirs are counted, not quietly dropped."""
    page = _client.get("/api/campaigns").json()
    joined = sum(1 for r in page["rows"] if r.get("outcome"))
    assert joined > 0, "no campaign carries an outcome"
    assert page["unjoined"]["value"] >= 0
    assert page["unjoined"]["population"].strip()


def test_constant_columns_are_reported():
    """A column with one distinct value is named so the client can hide it.

    `allies` and `vassals` are zero on every recorded turn. A check that looks for blank
    cells never noticed, because a column full of real zeroes is full.
    """
    con = db.connect()
    rows = q.campaign_rows(con)
    checked = 0
    for row in rows[:25]:
        pts, constant = q.reward_series(con, row.campaign.raw)
        if len(pts) < 3:
            continue
        checked += 1
        for field in ("income", "settlements", "allies", "vassals", "power_rank"):
            vals = {getattr(p, field) for p in pts}
            if len(vals) <= 1:
                assert field in constant, (
                    "%s is constant across %d turns of %s but was not reported"
                    % (field, len(pts), row.campaign.raw))
    assert checked, "no campaign had enough turns to check"


def test_no_column_is_empty_in_every_row():
    """No field may be null on every row of a list that has rows to judge.

    A column wired to a field the producer never writes renders perfectly -- as a tidy
    column of dashes -- and nothing complains. It shipped here: the trial ledger was read
    with four guessed key names (`corpus`, `sett_per_camp`, `turns_per_camp`,
    `lord_per_camp`), none of which the session writes, so four columns were empty on all
    85 rows and looked exactly like "this run recorded no results".

    This is the rule the previous dashboard's linter enforced over rendered HTML. Checking
    it on the API instead makes it unambiguous: an empty column is a field that is null in
    every row, not a cell that happens to look blank.

    A field that is null in every row is only a defect when SOME row could have had it, so
    lists shorter than 3 are skipped, and fields that are legitimately absent everywhere
    right now are listed with the reason.
    """
    # The collections that back a TABLE, named explicitly. Scoped rather than universal
    # because the interesting failure is "a table column reads a key nobody writes", and
    # a blanket sweep instead flags every optional presentation field that happens to be
    # unset -- Ident.culture on a list of action types, Metric.unit on a unitless metric,
    # the conditional note on a policy row. A check that cries wolf gets exempted into
    # uselessness, so this one states exactly what it covers.
    TABLES = [
        ("/api/run", "$.collect_timing"),
        ("/api/run", "$.cycle_timing"),
        ("/api/run", "$.totals"),
        ("/api/run", "$.services"),
        ("/api/campaigns", "$.rows"),
        ("/api/campaigns/starts", "$.rows"),
        ("/api/campaigns/matrix?kind=action", "$.totals"),
        ("/api/decisions", "$.rows"),
        ("/api/decisions/actions", "$.by_type"),
        ("/api/decisions/actions", "$.denominators"),
        ("/api/decisions/menus", "$.rows"),
        ("/api/decisions/menus", "$.coverage"),
        ("/api/models/training", "$.trials"),
        ("/api/models/training", "$.history"),
        ("/api/infra", "$.activity"),
    ]
    # Genuinely absent right now, each with the reason it is absent. A row here is a
    # statement about the data, not a licence to leave a column unwired.
    EXPECTED_EMPTY = {
        ("$.rows", "gnn_impact"):
            "the log page's offers carry rank but impact is only stored for scored arms",
        ("$.trials", "cfg"):
            "no trial has run with a backend config override",
        ("$.trials", "notes"):
            "notes are only written for trials with a recorded outcome tally",
        ("$.services", "started"):
            "the process probe reports a start time only for processes it matched",
    }
    responses = _all_responses()
    bad = []
    checked = 0
    for ep, jpath in TABLES:
        r = responses.get(ep)
        if r is None or r.status_code != 200:
            bad.append("%s did not answer, so %s could not be audited" % (ep, jpath))
            continue
        node = next((n for p, n in _walk(r.json())
                     if p == jpath and isinstance(n, list)), None)
        if node is None:
            bad.append("%s has no list at %s -- the audit list is out of date" % (ep, jpath))
            continue
        if len(node) < 3 or not all(isinstance(x, dict) for x in node):
            continue
        checked += 1
        keys = set()
        for x in node:
            keys |= set(x)
        for k in sorted(keys):
            if (jpath, k) in EXPECTED_EMPTY:
                continue
            vals = [x.get(k) for x in node]
            if all(v is None or v == "" or v == [] or v == {} for v in vals):
                bad.append("%s %s -> every one of %d rows has %r empty"
                           % (ep, jpath, len(node), k))
    assert checked, "no table had enough rows to audit"
    assert not bad, ("fields that are empty on every row:\n  " + "\n  ".join(bad)
                     + "\n\nEither the field is wired to a key the producer does not "
                       "write, or it is legitimately absent -- in which case add it to "
                       "EXPECTED_EMPTY with the reason.")


def test_correlation_tiles_are_separable():
    """The two tiles share arm names, so they must be addressable separately.

    A check that searches a merged page for an arm finds the action table every time and
    never inspects the interrupt one -- which is precisely the table that was wrong.
    """
    page = _client.get("/api/models/correlations").json()
    labels = [t["label"] for t in page["tiles"]]
    assert labels == ["action ranker", "interrupt model"], labels
    for tile in page["tiles"]:
        assert isinstance(tile["rows"], list)


def test_arm_with_decisions_is_not_zero():
    """An arm that played decisions may not render as zero campaigns and zero turns."""
    con = db.connect()
    page = _client.get("/api/models/correlations").json()
    truth = {
        "action ranker": {r[0]: r[1] for r in con.execute(
            "SELECT COALESCE(policy,'(unrecorded)'), COUNT(*) FROM action_taken"
            " GROUP BY 1")},
        "interrupt model": {r[0]: r[1] for r in con.execute(
            "SELECT COALESCE(policy,'(unrecorded)'), COUNT(*) FROM interrupt_decisions"
            " GROUP BY 1")},
    }
    bad = []
    for tile in page["tiles"]:
        played = truth[tile["label"]]
        shown = {r["arm"]["raw"]: r for r in tile["rows"]}
        for arm, n in played.items():
            if n <= 0:
                continue
            row = shown.get(arm)
            if row is None:
                bad.append("%s/%s played %d decisions but has no row"
                           % (tile["label"], arm, n))
            elif row["campaigns"] == 0 and row["turns"] == 0:
                bad.append("%s/%s played %d decisions but reads 0 campaigns / 0 turns"
                           % (tile["label"], arm, n))
    assert not bad, "\n  ".join(bad)


def test_matrix_leads_with_totals_worst_first():
    """The totals row is the finding; a grid without it hides its own headline."""
    page = _client.get("/api/campaigns/matrix?kind=action").json()
    tot = page["totals"]
    assert tot, "the crosstab shipped without a totals row"
    pcts = []
    for t in tot:
        r = t["rate"]
        pcts.append((100.0 * r["n"] / r["of"]) if r["of"] else 999.0)
    assert pcts == sorted(pcts), "totals are not sorted worst-first: %s" % pcts[:6]
    con = db.connect()
    for t in tot[:3]:
        sql = con.execute(
            "SELECT SUM(CASE WHEN refusal IS 'awaiting_execution' THEN 0 ELSE 1 END),"
            "       SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END)"
            " FROM action_taken WHERE action_type = ?", (t["action_type"]["raw"],)).fetchone()
        assert t["rate"]["of"] == (sql[0] or 0), (t["action_type"]["raw"], t["rate"], sql)
        assert t["rate"]["n"] == (sql[1] or 0), (t["action_type"]["raw"], t["rate"], sql)


def test_starts_marks_single_sample_rows():
    """An average over one campaign is labelled as such rather than apologised for."""
    page = _client.get("/api/campaigns/starts").json()
    assert page["rows"], "no starts"
    for r in page["rows"]:
        assert r["single_sample"] == (r["n"] <= 2), r
    assert page["low_sample"]["population"].strip()


def test_identifiers_carry_both_forms():
    """Every identifier ships a readable label AND the raw id that gets grepped."""
    bad = []
    for ep, r in _all_responses().items():
        if r.status_code != 200:
            continue
        for path, node in _walk(r.json()):
            if isinstance(node, dict) and set(node) >= {"raw", "label"} and "noun" not in node:
                if not str(node.get("label") or "").strip():
                    bad.append("%s %s -> empty label for %r" % (ep, path, node.get("raw")))
    assert not bad, "\n  ".join(bad[:20])


def test_menu_option_scores_are_data_not_hover_text():
    """Per-option model scores must be fields, so they can be sorted and searched."""
    page = _client.get("/api/decisions/menus").json()
    assert page["rows"], "no blocking-screen rows"
    with_opts = [r for r in page["rows"] if r["options"]]
    assert with_opts, "no row carried its options as data"
    for r in with_opts[:5]:
        for o in r["options"]:
            assert "exploit" in o and "gnn" in o, o


def test_current_campaign_has_one_source():
    """Header and body cannot disagree, because there is exactly one accessor."""
    con = db.connect()
    cur = q.current(con)
    row = con.execute("SELECT campaign_id, turn FROM target_rows"
                      " ORDER BY ts DESC LIMIT 1").fetchone()
    if row is None:
        return
    assert cur.campaign.raw == row["campaign_id"], (cur.campaign.raw, row["campaign_id"])
    assert cur.turn == row["turn"]
    api = _client.get("/api/run").json()["current"]
    assert api["campaign"]["raw"] == row["campaign_id"]
    assert api["turn"] == row["turn"]


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("ok   %s" % name)
        except AssertionError as e:
            fails += 1
            print("FAIL %s\n     %s" % (name, str(e)[:900]))
        except Exception as e:                                   # noqa: BLE001
            fails += 1
            print("ERR  %s\n     %r" % (name, e))
    print("\n%d failure(s)" % fails)
    raise SystemExit(1 if fails else 0)
