"""The quality gates for the dashboard."""

from __future__ import annotations

import os
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

warnings.filterwarnings("ignore", message=r".*httpx.*")

from fastapi.testclient import TestClient

import arms
from advisor_api import analytics_db as adb, db, queries as q
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
    "/api/models/agreement/series?axis=window",
    "/api/models/agreement/series?axis=generation",
    "/api/models/agreement/breakdown?dim=action_type",
    "/api/models/correlations",
    "/api/analytics",
    "/api/models/training",
    "/api/infra",
]

import atexit

_client_cm = TestClient(app)
_client = _client_cm.__enter__()
atexit.register(_client_cm.__exit__, None, None, None)


def _await_first_probe(timeout=15.0):
    """Block until the process probe has taken a sample, or give up."""
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
    """Every dict AND every list in a response, with the path that reached it."""
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
    """A count without its population is the defect this rule exists to prevent."""
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
    """Every headline number, re-derived straight from SQL."""
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
    """target_rows.campaign_id is a KEY STRING; campaigns.campaign_id is an integer."""
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
    """A column with one distinct value is named so the client can hide it."""
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



def test_no_signed_column_is_one_signed():
    """A signed column that can only ever read one way is unreachable by construction."""
    SIGNED = [("/api/campaigns", "$.rows", "settlements_growth"),
              ("/api/campaigns", "$.rows", "lord_growth")]
    # A field that genuinely cannot go both ways, with the reason and the measurement.
    ONE_SIGNED_OK = {
        ("$.rows", "lord_growth"):
            "a legendary lord's level cannot decrease in WH3",
        # Growth is first -> PEAK, and the peak is never below the first point, so this is
        # non-negative by construction. A campaign that lost ground shows it in
        # final_settlements beside this, not here.
        ("$.rows", "settlements_growth"):
            "measured to the peak, which cannot be below the starting point",
    }
    MIN_N = 30
    bad = []
    for ep, jpath, field in SIGNED:
        node = _client.get(ep).json()[jpath.split(".")[-1]]
        vals = [r[field] for r in node if r.get(field) is not None]
        if len(vals) < MIN_N:
            continue
        varies = any(v != 0 for v in vals)
        if not varies or (jpath, field) in ONE_SIGNED_OK:
            continue
        if all(v <= 0 for v in vals):
            bad.append("%s %s.%s: %d values, none positive -- growth is unreachable upward"
                       % (ep, jpath, field, len(vals)))
        if all(v >= 0 for v in vals):
            bad.append("%s %s.%s: %d values, none negative -- loss is unrepresentable"
                       % (ep, jpath, field, len(vals)))
    assert not bad, ("columns that can only ever read one way:\n  " + "\n  ".join(bad)
                     + "\n\nEither the producer clamps the value, or the record is only "
                       "written in one case -- or it is genuinely one-signed, in which case "
                       "add it to ONE_SIGNED_OK with the measurement.")


def test_growth_state_partitions_every_row():
    """Every campaign says whether growth was measurable, and the states are exhaustive."""
    rows = _client.get("/api/campaigns").json()["rows"]
    states = {}
    for r in rows:
        st = r.get("growth_state")
        assert st in ("measured", "single_turn", "no_turn_rows"), \
            "campaign %s has growth_state %r" % (r.get("campaign", {}).get("raw"), st)
        states[st] = states.get(st, 0) + 1
        has = r.get("settlements_growth") is not None
        assert has == (st == "measured"), (
            "campaign %s is %r but %s a settlements delta -- the state and the value "
            "disagree, so a reader cannot tell 'flat' from 'no data'"
            % (r.get("campaign", {}).get("raw"), st, "has" if has else "has no"))
    assert sum(states.values()) == len(rows)
    cov = _client.get("/api/campaigns").json()["growth_coverage"]
    assert cov["n"] == states.get("measured", 0), \
        "growth_coverage says %d measured, the rows say %d" % (cov["n"],
                                                               states.get("measured", 0))
    assert cov["of"] == len(rows)


def test_growth_delta_equals_its_endpoints():
    """A derived delta cannot drift from the numbers it claims to describe."""
    for r in _client.get("/api/campaigns").json()["rows"]:
        if r.get("growth_state") != "measured":
            continue
        # Growth is measured to the PEAK, so these are the endpoints it claims.
        for d, a, b in (("settlements_growth", "first_settlements", "peak_settlements"),
                        ("lord_growth", "first_lord_level", "peak_lord_level")):
            if r.get(a) is None or r.get(b) is None:
                continue
            want = r[b] - r[a]
            assert abs(r[d] - want) < 1e-9, \
                "%s on %s is %r but %s - %s is %r" % (d, r["campaign"]["raw"], r[d], b, a, want)


def test_analytics_cannot_go_stale_silently():
    """The staleness gate."""
    page = _client.get("/api/models/agreement").json()
    f = page["freshness"]
    assert f["behind"]["population"] and f["rows"]["population"]
    con = db.connect()
    hi = con.execute("SELECT MAX(decision_id) FROM decisions").fetchone()[0] or 0
    acon = adb.connect()
    if acon is None:
        assert f["state"] == "bad", "no analytics database, but freshness is not bad"
        return
    st = acon.execute("SELECT watermark, rows FROM analytics_state"
                      " WHERE tenant='model_agreement'").fetchone()
    watermark, rows = (st[0], st[1]) if st else (0, 0)
    assert f["behind"]["value"] == max(0, hi - 1 - watermark), \
        "served behind=%r, re-derived %r" % (f["behind"]["value"], max(0, hi - 1 - watermark))
    if f["behind"]["value"] > 0:
        assert f["state"] != "ok", "analytics is behind but reports ok"
    # No holes: every corpus row at or below the watermark has a fact row.
    n_src = con.execute("SELECT COUNT(*) FROM decisions WHERE decision_id <= ?",
                        (watermark,)).fetchone()[0]
    n_fact = acon.execute("SELECT COUNT(*) FROM model_agreement").fetchone()[0]
    assert n_src == n_fact == rows, (
        "the precomputed table covers %d of %d decisions at or below its own watermark -- "
        "it is short, and every aggregate built on it is short with it" % (n_fact, n_src))


def test_rho_is_the_headline_not_a_secondary():
    """RBO and top-k are additions, never replacements."""
    page = _client.get("/api/models/agreement").json()
    if page.get("empty_reason"):
        return
    c = page["correlation"]
    assert c is not None and c["compared"]["value"] > 0
    assert c["rho_median"] is not None, "rho is the headline and it is missing"
    keys = set(c)
    for banned in ("rbo", "rbo_mean", "top3_overlap", "top5_overlap", "top10_overlap"):
        assert banned not in keys, \
            "%r is in the primary correlation block; secondary measures belong under " \
            "`secondary`, where the layout can subordinate them" % banned


def test_generation_view_is_labelled_an_alignment():
    """The generation axis is inferred from timestamps, and must never imply otherwise."""
    page = _client.get("/api/models/agreement/series?axis=generation").json()
    assert page["is_alignment"] is True
    assert page["ambiguous"]["population"]
    win = _client.get("/api/models/agreement/series?axis=window").json()
    assert win["is_alignment"] is False


def test_strategies_aggregate_by_strategy():
    """`ruleset(spread_out)` is the ruleset arm, not a strategy of its own."""
    seen = []
    for ep, jpath, key in (("/api/models/agreement", "$.rows", "picked_by"),
                           ("/api/decisions/actions", "$.policies", "policy")):
        for r in _client.get(ep).json()[jpath.split(".")[-1]]:
            raw = (r.get(key) or {}).get("raw") or ""
            seen.append((ep, raw))
    bad = [x for x in seen if "(" in x[1] or x[1].endswith("_random_fallback")]
    assert not bad, ("these rows are raw policy strings rather than strategies: %r -- "
                     "arms.arm_of folds them" % bad[:5])
    facets = _client.get("/api/decisions").json().get("facets") or {}
    for p in facets.get("policies") or []:
        raw = p.get("raw") if isinstance(p, dict) else p
        assert "(" not in str(raw), "the log filter offers %r, which is a rule not a strategy" % raw


def test_no_column_is_empty_in_every_row():
    """No field may be null on every row of a list that has rows to judge."""
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
        ("/api/models/agreement", "$.rows"),
        ("/api/models/agreement", "$.secondary"),
        ("/api/models/agreement/breakdown?dim=action_type", "$.rows"),
        ("/api/analytics", "$.tenants"),
        ("/api/models/training", "$.trials"),
        ("/api/models/training", "$.history"),
        ("/api/infra", "$.activity"),
    ]
    # Genuinely absent right now, each with the reason it is absent. A row here is a
    # statement about the data, not a licence to leave a column unwired.
    EXPECTED_EMPTY = {
        ("$.tenants", "last_error"):
            "no analytics tenant has failed a pass -- this is the field that would carry "
            "the reason if one had, and it is the good case",
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
    """The two tiles share arm names, so they must be addressable separately."""
    page = _client.get("/api/models/correlations").json()
    labels = [t["label"] for t in page["tiles"]]
    assert labels == ["action ranker", "interrupt model"], labels
    for tile in page["tiles"]:
        assert isinstance(tile["rows"], list)


def test_arm_with_decisions_is_not_zero():
    """An arm that played decisions may not render as zero campaigns and zero turns."""
    con = db.connect()
    page = _client.get("/api/models/correlations").json()
    # Folded onto strategies before comparing, because that is what the page now groups by.
    def _played(table):
        out = {}
        for raw, n in con.execute(
                "SELECT COALESCE(policy,'(unrecorded)'), COUNT(*) FROM %s GROUP BY 1" % table):
            arm = arms.arm_of(raw) or arms.UNRECORDED
            out[arm] = out.get(arm, 0) + n
        return out

    truth = {"action ranker": _played("action_taken"),
             "interrupt model": _played("interrupt_decisions")}
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
