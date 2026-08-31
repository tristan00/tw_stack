
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

BUDGET_MS = 1000.0

GET_ENDPOINTS = [
    "/api/health",
    "/api/run",
    "/api/campaigns",
    "/api/campaigns/starts",
    "/api/campaigns/picks",
    "/api/campaigns/matrix?kind=action",
    "/api/campaigns/matrix?kind=interrupt",
    "/api/decisions",
    "/api/decisions/actions",
    "/api/decisions/diplomacy",
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
    camps = _client.get("/api/campaigns").json()
    if camps.get("rows"):
        key = camps["rows"][0]["campaign"]["raw"]
        out["/api/campaigns/<key>"] = _client.get("/api/campaigns/%s" % key)
    starts = _client.get("/api/campaigns/starts").json()
    played = [s for s in starts.get("rows") or [] if s.get("n_window")]
    if played:
        s = played[0]
        out["/api/campaigns/starts/<map>/<faction>"] = _client.get(
            "/api/campaigns/starts/%s/%s" % ((s.get("campaign_map") or {}).get("raw", ""),
                                             s["faction"]["raw"]))
    picks = _client.get("/api/campaigns/picks").json()
    if picks.get("picks"):
        out["/api/campaigns/picks/<id>"] = _client.get(
            "/api/campaigns/picks/%d" % picks["picks"][0]["pick_id"])
    decs = _client.get("/api/decisions").json()
    if decs.get("rows"):
        did = decs["rows"][0]["decision_id"]
        out["/api/decisions/<id>"] = _client.get("/api/decisions/%d" % did)
    for fam in ("items", "buildings", "research", "skills"):
        idx = _client.get("/api/%s" % fam)
        out["/api/%s" % fam] = idx
        rows = (idx.json() or {}).get("rows") or []
        if rows:
            out["/api/%s/<key>" % fam] = _client.get(
                "/api/%s/%s" % (fam, rows[0]["key"]))
    out["/api/positions"] = _client.get("/api/positions")
    if played:
        s = played[0]
        base = "/api/campaigns/starts/%s/%s" % (
            (s.get("campaign_map") or {}).get("raw", ""), s["faction"]["raw"])
        out["/api/campaigns/starts/<map>/<faction>/buildings"] = _client.get(
            base + "/buildings")
        out["/api/campaigns/starts/<map>/<faction>/skills"] = _client.get(
            base + "/skills")
    return out


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
    for ep in ("/api/campaigns/no_such_campaign_key", "/api/decisions/999999999"):
        r = _client.get(ep)
        assert r.status_code == 404, "%s -> %s" % (ep, r.status_code)
        assert "Traceback" not in r.text


def test_every_count_names_its_population():
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
    con = db.connect()
    run = _client.get("/api/run").json()
    totals = {t["noun"]: t["value"] for t in run["totals"]}

    sql_campaigns = con.execute(
        "SELECT COUNT(*) FROM (SELECT campaign_id FROM decisions"
        " GROUP BY campaign_id HAVING COUNT(*) >= 2)").fetchone()[0]
    sql_listed = con.execute(
        "SELECT COUNT(DISTINCT campaign_id) FROM decision_points").fetchone()[0]
    sql_decisions = con.execute("SELECT COUNT(*) FROM decision_points").fetchone()[0]
    sql_offers = con.execute("SELECT COUNT(*) FROM action_offers").fetchone()[0]
    sql_confirmed = con.execute(
        "SELECT COUNT(*) FROM action_taken WHERE counted=1").fetchone()[0]

    assert totals["campaigns"] == sql_campaigns, (totals["campaigns"], sql_campaigns)
    assert totals["decisions"] == sql_decisions, (totals["decisions"], sql_decisions)
    assert totals["offers"] == sql_offers, (totals["offers"], sql_offers)
    assert totals["actions"] == sql_confirmed, (totals["actions"], sql_confirmed)

    rows = _client.get("/api/campaigns").json()["rows"]
    assert len(rows) == sql_listed, (len(rows), sql_listed)

    total = _client.get("/api/decisions").json()["total"]["value"]
    assert total == con.execute("SELECT COUNT(*) FROM action_taken").fetchone()[0]


def test_campaign_id_join_trap():
    import psycopg
    con = db.connect()
    try:
        con.execute("SELECT COUNT(*) FROM turn_open t"
                    " JOIN campaigns c ON t.campaign_id = c.campaign_id").fetchone()
        raise AssertionError(
            "the id/key join executed -- if the schema changed, this test and every join "
            "in queries.py must be revisited together")
    except psycopg.errors.UndefinedFunction:
        pass
    right = con.execute("SELECT COUNT(*) FROM turn_open t"
                        " JOIN campaigns c ON t.campaign_id = c.campaign_key").fetchone()[0]
    assert right > 0, "the key join returned nothing; turn_open is not joinable"


def test_outcome_join_is_a_key():
    con = db.connect()
    claimed = q.join_outcomes(con)
    assert claimed, "no ending joined to any campaign -- the outcome column would be empty"
    seen = {}
    for ckey, pm in claimed.items():
        sig = (pm.get("faction"), pm.get("ts"))
        seen.setdefault(sig, []).append(ckey)
    dupes = {s: c for s, c in seen.items() if len(c) > 1}
    assert not dupes, "one ending claimed several campaigns: %s" % dupes


def test_outcome_coverage_is_reported_not_hidden():
    page = _client.get("/api/campaigns").json()
    joined = sum(1 for r in page["rows"] if r.get("outcome"))
    assert joined > 0, "no campaign carries an outcome"
    assert page["unjoined"]["value"] >= 0
    assert page["unjoined"]["population"].strip()


def test_constant_columns_are_reported():
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
    SIGNED = [("/api/campaigns", "$.rows", "settlements_growth"),
              ("/api/campaigns", "$.rows", "lord_growth")]
    ONE_SIGNED_OK = {
        ("$.rows", "lord_growth"):
            "a legendary lord's level cannot decrease in WH3",
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
    for r in _client.get("/api/campaigns").json()["rows"]:
        if r.get("growth_state") != "measured":
            continue
        for d, a, b in (("settlements_growth", "first_settlements", "peak_settlements"),
                        ("lord_growth", "first_lord_level", "peak_lord_level")):
            if r.get(a) is None or r.get(b) is None:
                continue
            want = r[b] - r[a]
            assert abs(r[d] - want) < 1e-9, \
                "%s on %s is %r but %s - %s is %r" % (d, r["campaign"]["raw"], r[d], b, a, want)


def test_analytics_cannot_go_stale_silently():
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
    n_src = con.execute("SELECT COUNT(*) FROM decisions WHERE decision_id <= %s",
                        (watermark,)).fetchone()[0]
    n_fact = acon.execute("SELECT COUNT(DISTINCT decision_id) FROM model_agreement"
                          ).fetchone()[0]
    n_rows = acon.execute("SELECT COUNT(*) FROM model_agreement").fetchone()[0]
    assert n_src == n_fact and n_rows == rows, (
        "the precomputed table covers %d of %d decisions at or below its own watermark "
        "(%d pair rows, state says %d) -- it is short, and every aggregate built on it is "
        "short with it" % (n_fact, n_src, n_rows, rows))


def test_rho_is_the_headline_not_a_secondary():
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
    page = _client.get("/api/models/agreement/series?axis=generation").json()
    assert page["is_alignment"] is True
    assert page["ambiguous"]["population"]
    win = _client.get("/api/models/agreement/series?axis=window").json()
    assert win["is_alignment"] is False


def test_strategies_aggregate_by_strategy():
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
    TABLES = [
        ("/api/run", "$.collect_timing"),
        ("/api/run", "$.cycle_timing"),
        ("/api/run", "$.totals"),
        ("/api/run", "$.services"),
        ("/api/campaigns", "$.rows"),
        ("/api/campaigns/starts", "$.rows"),
        ("/api/campaigns/picks", "$.picks"),
        ("/api/campaigns/matrix?kind=action", "$.totals"),
        ("/api/decisions", "$.rows"),
        ("/api/decisions/actions", "$.by_type"),
        ("/api/decisions/actions", "$.denominators"),
        ("/api/decisions/menus", "$.rows"),
        ("/api/decisions/menus", "$.coverage"),
        ("/api/models/agreement", "$.rows"),
        ("/api/models/agreement", "$.secondary"),
        ("/api/models/agreement/breakdown?dim=action_type", "$.rows"),
        ("/api/items", "$.rows"),
        ("/api/buildings", "$.rows"),
        ("/api/research", "$.rows"),
        ("/api/skills", "$.rows"),
        ("/api/analytics", "$.tenants"),
        ("/api/models/training", "$.trials"),
        ("/api/models/training", "$.history"),
        ("/api/infra", "$.activity"),
    ]
    EXPECTED_EMPTY = {
        ("$.tenants", "last_error"):
            "no analytics tenant has failed a pass -- this is the field that would carry "
            "the reason if one had, and it is the good case",
        ("$.rows", "gnn_impact"):
            "the log page's offers carry rank but impact is only stored for scored arms",
        ("$.rows", "ggnn_score"):
            "greedy_gnn stores a per-offer score only for decisions made after it joined "
            "the mix; a run dir from before then has none",
        ("$.rows", "ggnn_rank"):
            "greedy_gnn stores a per-offer rank only for decisions made after it joined "
            "the mix; a run dir from before then has none",
        ("$.trials", "cfg"):
            "no trial has run with a backend config override",
        ("$.trials", "notes"):
            "notes are only written for trials with a recorded outcome tally",
        ("$.trials", "ruleset"):
            "no trial in the ledger played the ruleset arm in either mix -- the column "
            "carries a name only when a trial does",
        ("$.services", "started"):
            "the process probe reports a start time only for processes it matched",
        ("$.rows", "presave_radius"):
            "a campaign carries a radius only when the run booted it from a baked "
            "presave, which is newer than every campaign in this corpus",
        ("$.rows", "gnn_rank"):
            "the marwil_gnn arm was cut from the action mix; decisions played since then "
            "carry no marwil rank, and the audit window holds only such decisions",
        ("$.picks", "entropy"):
            "the selector stores entropy per pick only from the blend formula onward; a "
            "corpus of older picks carries none",
        ("$.picks", "std"):
            "the selector stores std per pick only from the blend formula onward; a "
            "corpus of older picks carries none",
        ("$.picks", "adjust"):
            "the selector stores the manual adjust per pick only from the "
            "rules/ucb_adjust.json feature onward; a corpus of older picks carries none",
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
    page = _client.get("/api/models/correlations").json()
    labels = [t["label"] for t in page["tiles"]]
    assert labels == ["action ranker", "interrupt model"], labels
    for tile in page["tiles"]:
        assert isinstance(tile["rows"], list)


def test_arm_with_decisions_is_not_zero():
    con = db.connect()
    page = _client.get("/api/models/correlations").json()
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
            "SELECT SUM(CASE WHEN refusal IN ('awaiting_execution','campaign_died')"
            " THEN 0 ELSE 1 END) att,"
            "       SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END) ok"
            " FROM action_taken WHERE action_type = %s",
            (t["action_type"]["raw"],)).fetchone()
        assert t["rate"]["of"] == (sql[0] or 0), (t["action_type"]["raw"], t["rate"], sql)
        assert t["rate"]["n"] == (sql[1] or 0), (t["action_type"]["raw"], t["rate"], sql)


def test_identifiers_carry_both_forms():
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
    page = _client.get("/api/decisions/menus").json()
    assert page["rows"], "no blocking-screen rows"
    with_opts = [r for r in page["rows"] if r["options"]]
    assert with_opts, "no row carried its options as data"
    for r in with_opts[:5]:
        for o in r["options"]:
            assert "exploit" in o and "gnn" in o, o


def test_current_campaign_has_one_source():
    con = db.connect()
    cur = q.current(con)
    row = con.execute("SELECT campaign_id, turn FROM turn_close"
                      " ORDER BY decision_id DESC LIMIT 1").fetchone()
    if row is None:
        return
    assert cur.campaign.raw == row["campaign_id"], (cur.campaign.raw, row["campaign_id"])
    assert cur.turn == row["turn"]
    api = _client.get("/api/run").json()["current"]
    assert api["campaign"]["raw"] == row["campaign_id"]
    assert api["turn"] == row["turn"]


def _diplomacy_page(version=None):
    ep = "/api/decisions/diplomacy"
    if version:
        ep += "?version=" + version
    r = _client.get(ep)
    assert r.status_code == 200, (ep, r.status_code)
    return r.json()


def test_diplomacy_every_row_partitions_across_arms():
    page = _diplomacy_page()
    n_src = len(page["sources"])
    assert page["attempts"]["value"] == sum(r["attempted"] for r in page["rows"])
    for r in page["rows"]:
        term = r["term"]["raw"]
        cells = r["by_source"]
        assert len(cells) == n_src, term
        assert sum(c["attempted"] for c in cells) == r["attempted"], term
        assert sum(c["confirmed"] for c in cells) == r["confirmed"], term
        assert r["confirmed"] <= r["attempted"], term
        assert r["share"]["n"] == r["attempted"], term
        assert r["share"]["of"] == (page["attempts"]["value"] or 1), term
        for c in cells:
            assert c["confirmed"] <= c["attempted"], (term, c["source"]["raw"])
            if c["attempted"]:
                assert c["share"] is not None, (term, c["source"]["raw"])
                assert abs(c["share"] - c["attempted"] / r["attempted"]) <= 5.1e-5, \
                    (term, c["source"]["raw"], c["share"])
            else:
                assert c["share"] is None, (term, c["source"]["raw"])
        if r["attempted"]:
            total = sum(c["share"] for c in cells if c["share"] is not None)
            assert abs(total - 1.0) <= 1e-3, \
                "row %s shares sum to %r, not 1 -- a cell is normalized against " \
                "something other than its own row" % (term, total)


def test_diplomacy_no_attempt_falls_between_versions():
    page = _diplomacy_page()
    versions = [v["version"] for v in page["versions"]]
    if not versions:
        return
    vpages = [_diplomacy_page(v) for v in versions]
    for v, p in zip(versions, vpages):
        assert p["version"] == v, (v, p["version"])
    assert sum(p["attempts"]["value"] for p in vpages) == page["attempts"]["value"], \
        "per-version attempts do not add up to the unfiltered total -- the version " \
        "windows have a gap or an overlap"
    split = {}
    for p in vpages:
        for r in p["rows"]:
            split[r["term"]["raw"]] = split.get(r["term"]["raw"], 0) + r["attempted"]
    whole = {r["term"]["raw"]: r["attempted"] for r in page["rows"]}
    assert {k: v for k, v in split.items() if v} == {k: v for k, v in whole.items() if v}, \
        "a proposal's attempts differ between 'every version' and the union of versions"


def test_diplomacy_unknown_version_serves_everything():
    page = _diplomacy_page("no_such_version")
    assert page["version"] is None
    assert page["attempts"]["value"] == _diplomacy_page()["attempts"]["value"]


def test_diplomacy_counts_match_the_database():
    con = db.connect()
    keys = q._campaign_keys(con)
    terms = {}
    for r in con.execute("SELECT action_id, action_key FROM actions"
                         " WHERE action_type = 'diplomacy'"):
        k = str(r["action_key"] or "")
        terms[r["action_id"]] = k.split(":", 1)[1] if ":" in k else k
    want_att, want_ok = {}, {}
    for r in con.execute(
            "SELECT campaign_id, action_id,"
            " SUM(CASE WHEN refusal IN ('awaiting_execution','campaign_died')"
            "     THEN 0 ELSE 1 END) a,"
            " SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END) c"
            " FROM taken WHERE action_id IN (SELECT action_id FROM actions"
            " WHERE action_type = 'diplomacy')"
            " GROUP BY campaign_id, action_id"):
        if r["campaign_id"] not in keys:
            continue
        t = terms[r["action_id"]]
        want_att[t] = want_att.get(t, 0) + (r["a"] or 0)
        want_ok[t] = want_ok.get(t, 0) + (r["c"] or 0)
    page = _diplomacy_page()
    got_att = {r["term"]["raw"]: r["attempted"] for r in page["rows"]}
    got_ok = {r["term"]["raw"]: r["confirmed"] for r in page["rows"]}
    assert {k: v for k, v in got_att.items() if v} == \
           {k: v for k, v in want_att.items() if v}
    assert {k: v for k, v in got_ok.items() if v} == \
           {k: v for k, v in want_ok.items() if v}


def test_catalog_indexes_are_keyed_and_resolve_details():
    for fam in ("items", "buildings", "research", "skills"):
        j = _client.get("/api/%s" % fam).json()
        rows = j["rows"]
        assert rows, "no %s row at all" % fam
        keys = [r["key"] for r in rows]
        assert len(keys) == len(set(keys)), "%s rows repeat a key" % fam
        labeled = sum(1 for r in rows if r.get("label"))
        assert labeled >= 0.8 * len(rows), \
            "%s: only %d of %d rows carry a localized label" % (fam, labeled, len(rows))
        r = _client.get("/api/%s/%s" % (fam, keys[0]))
        assert r.status_code == 200
        assert r.json()["key"] == keys[0]
        assert _client.get("/api/%s/no_such_key_at_all" % fam).status_code == 404


def test_item_means_show_whenever_a_side_has_campaigns():
    j = _client.get("/api/items").json()
    for r in j["rows"]:
        if r["equipped_in"]:
            assert r["avg_reward_equipped"] is not None, \
                "%s worn in %d campaigns but shows no worn mean" \
                % (r["key"], r["equipped_in"])
        if r["benched_in"]:
            assert r["avg_reward_benched"] is not None, \
                "%s benched in %d campaigns but shows no benched mean" \
                % (r["key"], r["benched_in"])


def test_item_effects_are_a_table_not_a_blob():
    j = _client.get("/api/items").json()
    key = j["rows"][0]["key"]
    d = _client.get("/api/items/%s" % key).json()
    assert isinstance(d["effects"], list)
    for e in d["effects"]:
        assert e["name"]
        assert e["state"] in ("ok", "warn", "bad", "neutral")
        assert e["scope"]


def test_positions_conditions_narrow_and_shares_sum():
    whole = _client.get("/api/positions").json()
    assert whole["decisions"] and whole["rows"]
    share = sum(r["share"] or 0 for r in whole["rows"])
    assert 99.0 <= share <= 101.0, share
    assert whole["takes"] == sum(r["n"] for r in whole["rows"])
    fac = whole["factions"][0]["key"]
    part = _client.get("/api/positions?faction=%s&c=turn::4" % fac).json()
    assert 0 < part["decisions"] < whole["decisions"]
    sett = whole["settlements"][0]["key"]
    held = _client.get("/api/positions?c=has:settlement:%s" % sett).json()
    assert 0 < held["decisions"] < whole["decisions"]
    assert held["campaigns"] == whole["settlements"][0]["campaigns"]
    inv = _client.get("/api/positions?c=not:settlement:%s" % sett).json()
    assert inv["decisions"] + held["decisions"] == whole["decisions"]
    rich = _client.get("/api/positions?c=treasury:20000:").json()
    assert 0 < rich["decisions"] < whole["decisions"]


def test_reward_weights_contract():
    j = _client.get("/api/reward-weights").json()
    keys = [c["key"] for c in j["components"]]
    assert keys == ["settlements", "lord_levels", "allies", "vassals"]
    assert set(j["weights"]) == set(keys)
    defaults = {c["key"]: c["default"] for c in j["components"]}
    assert j["is_default"] == all(j["weights"][k] == defaults[k] for k in keys)
    r = _client.get("/api/positions").json()
    assert r["mean_reward"] is not None
    assert r["mean_future"] is not None
    assert any(row["avg_future"] is not None for row in r["rows"])
    bad = _client.post("/api/reward-weights", json={"gold": 1.0})
    assert bad.status_code == 400
    bad = _client.post("/api/reward-weights", json={"settlements": -2})
    assert bad.status_code == 400


def test_start_skills_says_who_ranked_what():
    starts = _client.get("/api/campaigns/starts").json()
    played = [s for s in starts.get("rows") or [] if s.get("n_window")]
    if not played:
        return
    s = played[0]
    j = _client.get("/api/campaigns/starts/%s/%s/skills" % (
        (s.get("campaign_map") or {}).get("raw", ""), s["faction"]["raw"])).json()
    chars = j["characters"]
    assert chars, "no character summary for a played start"
    assert any(c["top"] for c in chars), \
        "no character carries its top ranked skills"
    assert j["subtype"] in {c["subtype"] for c in chars}


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
        except Exception as e:
            fails += 1
            print("ERR  %s\n     %r" % (name, e))
    print("\n%d failure(s)" % fails)
    raise SystemExit(1 if fails else 0)
