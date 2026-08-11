from __future__ import annotations

import collections
import glob
import html
import json
import math
import random
from urllib.parse import parse_qs, urlparse
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import dbopen

sys.path.insert(0, common.ADVISOR)

RUNS_ROOT = common.RUNS_ROOT
SEQ_PAGE = 50
TIMELINE_ROWS = 200


def newest_run():
    return common.RUN_DIR


def _con(run_dir):
    # dbopen, not sqlite3: the v1 table names are views over the interned store and are
    # written in terms of unz()/f32(), which only exist on a connection that has them.
    con = dbopen.connect(os.path.join(run_dir, "decisions.sqlite"))
    con.row_factory = sqlite3.Row
    return con


def summary(con):
    q = lambda s: con.execute(s).fetchone()[0]
    counted = q("SELECT COUNT(*) FROM action_taken WHERE counted=1")
    taken = q("SELECT COUNT(*) FROM action_taken WHERE refusal IS NOT 'awaiting_execution'")
    # newest by clock, not by turn number. The run dir is reused across hundreds of
    # campaigns, so "the highest turn ever reached" is normally a campaign that ended
    # days ago -- these cards sit on every tab and used to contradict the live tab
    # about which faction was being played.
    latest = con.execute("SELECT campaign_id, turn, settlements, power_rank, lord_level, ts"
                         " FROM target_rows ORDER BY ts DESC LIMIT 1").fetchone()
    out = {"turns": q("SELECT COUNT(*) FROM target_rows"),
           "decisions": q("SELECT COUNT(*) FROM decision_points"),
           "offers": q("SELECT COUNT(*) FROM action_offers"),
           "campaigns": q("SELECT COUNT(DISTINCT campaign_id) FROM decision_points"),
           "taken": taken, "counted": counted,
           "confirm_rate": (round(100.0 * counted / taken, 1) if taken else 0.0),
           "campaign": "-", "faction": "-", "turn_now": "-", "settlements": "-",
           "power_rank": "-", "lord_level": "-", "state_age": None}
    if latest:
        out.update(campaign=latest[0] or "-", faction=_faction_of(latest[0]),
                   turn_now=latest[1], settlements=latest[2], power_rank=latest[3],
                   lord_level=latest[4],
                   state_age=(time.time() - latest[5]) if latest[5] else None)
    return out


def _num(v, nd=4):
    return "-" if v is None else ("%%.%df" % nd) % float(v)


def _optional_cols(con, table, wanted):
    """Columns from `wanted` that this database actually has.

    Run databases are long-lived and are written by whatever code was running at the
    time, so newer columns are absent from older runs -- and from a run already in
    flight when the column was added. Selecting them unconditionally would break the
    page for exactly the runs worth looking at.
    """
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return ()
    return tuple(c for c in wanted if c in have)


def sequence(con, limit=SEQ_PAGE, offset=0):
    """One row per decision: what it chose, and what each model made of that choice.

    Two bounded queries instead of one join. The offer columns used to arrive through
    `LEFT JOIN action_offers o ON o.offer_id = (SELECT MIN(o2.offer_id) FROM action_offers
    o2 WHERE ...)` -- a correlated subquery evaluated once per row against a VIEW over
    offers+actions, so its cost grew with the corpus rather than with the page. Measured
    at ~3k decisions: base rows 0.01s, the same rows with that join 20s.

    The page is 50 decisions, so their offers are a small bounded set. Fetch them in one
    scan and match the taken action in python. MIN(offer_id) becomes "first row wins",
    which is the same tie-break: offer_id is decision_id * 1048576 + offer_seq, so
    ascending offer_id is offer order.
    """
    gnn = _optional_cols(con, "action_offers", ("gnn_impact", "gnn_rank"))
    rows = [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.decision_seq, d.n_entities, d.n_offers,"
        " t.context_kind, t.context_id, t.action_type, t.action_key, t.counted, t.refusal,"
        " t.policy"
        " FROM decision_points d LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " ORDER BY d.decision_id DESC LIMIT ? OFFSET ?", (limit, offset))]
    dids = [r["decision_id"] for r in rows]
    if not dids:
        return rows
    cols = ("score", "exploit", "pct_global", "pct_local", "rank") + tuple(gnn)
    taken = {}
    for o in con.execute(
            "SELECT decision_id, context_kind, context_id, action_type, action_key, %s"
            " FROM action_offers WHERE decision_id IN (%s) ORDER BY offer_id"
            % (", ".join(cols), ",".join("?" * len(dids))), tuple(dids)):
        key = (o[0], o[1], str(o[2]), o[3], str(o[4]))
        if key not in taken:                       # first offer_id wins, as MIN() did
            taken[key] = dict(zip(cols, o[5:]))
    for r in rows:
        hit = taken.get((r["decision_id"], r["context_kind"], str(r["context_id"]),
                         r["action_type"], str(r["action_key"])))
        r.update(hit or {c: None for c in cols})
    return rows


def _pctile(rank, n):
    """Where a 1-based rank sits among n items, 100 = the model's own top pick.

    Both models rank the same offer set, so n is the same for each and rank 5 means the
    same thing to both. The percentile is here so decisions with DIFFERENT OFFER COUNTS
    compare against each other -- rank 5 of 8 and rank 5 of 70 are not the same standing.
    """
    try:
        rank, n = float(rank), float(n)
    except (TypeError, ValueError):
        return None
    if rank <= 0 or n < 2:
        return None
    return 100.0 * (1.0 - (rank - 1.0) / (n - 1.0))


def _spearman(xs, ys):
    """Rank correlation, ties averaged. None when there is too little to say."""
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


def agreement(con, did):
    """Both models' rankings of the same decision, for the offers both scored."""
    if "gnn_rank" not in _optional_cols(con, "action_offers", ("gnn_rank",)):
        return None
    rows = con.execute(
        "SELECT rank, gnn_rank FROM action_offers WHERE decision_id=?"
        " AND rank IS NOT NULL AND gnn_rank IS NOT NULL", (did,)).fetchall()
    if len(rows) < 3:
        return None
    cat = [r[0] for r in rows]
    gnn = [r[1] for r in rows]
    top_cat = min(range(len(rows)), key=lambda i: cat[i])
    top_gnn = min(range(len(rows)), key=lambda i: gnn[i])
    return {"n": len(rows), "rho": _spearman(cat, gnn), "same_top": top_cat == top_gnn}


def sequence_total(con):
    try:
        return con.execute("SELECT COUNT(*) FROM decision_points").fetchone()[0]
    except Exception:
        return 0


def ranking(con, did, limit=80):
    taken = con.execute("SELECT context_kind,context_id,action_type,action_key,counted,refusal"
                        " FROM action_taken WHERE decision_id=?", (did,)).fetchone()
    rows = [dict(r) for r in con.execute(
        "SELECT context_kind,context_id,action_type,action_key,exploit,"
        "pct_global,pct_local,rank"
        + "".join(",%s" % c for c in _optional_cols(con, "action_offers",
                                                    ("gnn_impact", "gnn_rank"))) +
        " FROM action_offers WHERE decision_id=?"
        " ORDER BY (exploit IS NULL), exploit DESC LIMIT ?", (did, limit))]
    tk = (dict(taken) if taken else None)
    for r in rows:
        r["taken"] = bool(tk and r["context_kind"] == tk["context_kind"]
                          and str(r["context_id"]) == str(tk["context_id"])
                          and r["action_type"] == tk["action_type"]
                          and str(r["action_key"]) == str(tk["action_key"]))
    ents = [dict(r) for r in con.execute(
        "SELECT context_kind,context_id,features FROM entity_snapshots WHERE decision_id=?", (did,))]
    return {"decision_id": did, "taken": tk, "offers": rows, "entities": ents,
            "n_offers": con.execute("SELECT COUNT(*) FROM action_offers WHERE decision_id=?",
                                    (did,)).fetchone()[0]}


def timeline(con):
    rows = [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.campaign_id, d.turn, d.ts, d.timings, t.context_kind,"
        " t.context_id, t.action_type,"
        " t.action_key, t.counted, t.refusal, t.latency_ms FROM decision_points d"
        " LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " ORDER BY d.decision_id DESC LIMIT ?", (TIMELINE_ROWS,))]
    rows.reverse()
    out, prev, prev_camp = [], None, None
    for r in rows:
        # the gap across a campaign boundary is game teardown and reload, not think
        # time between two actions -- attributing it to the next action is a lie
        same = (r["campaign_id"] == prev_camp)
        r["gap"] = (round(r["ts"] - prev, 1) if same and prev and r["ts"] else None)
        prev, prev_camp = (r["ts"] or prev), r["campaign_id"]
        tm = json.loads(r["timings"]) if r.get("timings") else {}
        r["collect_ms"] = tm.get("collect_ms")
        r["queue_ms"] = (tm.get("pickup_lag_ms") if tm.get("pickup_lag_ms") is not None else
                         ((tm.get("roundtrip_ms") or 0) - (tm.get("collect_ms") or 0)
                          if tm.get("roundtrip_ms") is not None else None))
        r["score_ms"] = tm.get("score_ms")
        r["housekeep_ms"] = tm.get("housekeep_ms")
        r["verify_ms"] = r.get("latency_ms")
        r["offers_n"] = tm.get("offers")
        r["total_ms"] = sum(v for v in (r["collect_ms"], r["queue_ms"], r["score_ms"],
                                        r["verify_ms"]) if v) or None
        out.append(r)
    # lanes are per (campaign, turn): turn numbers restart every campaign, so keying on
    # the turn alone merged unrelated campaigns into one lane
    lanes = {}
    for r in out:
        lanes.setdefault((r["campaign_id"], r["turn"]), []).append(r)
    return out, lanes


def run_history(con, runs_root=RUNS_ROOT):
    """One row per campaign, over every run dir on disk.

    Two families of outcome live here and they are not interchangeable: *peak* is the
    best a campaign ever reached, *final* is where it stopped. They are gathered with
    grouped queries rather than two per campaign, which is what the old version cost.
    """
    seen = {}
    dbs = common.run_dbs(runs_root)
    for db in dbs:
        try:
            c = dbopen.connect(db, timeout=5.0)
        except sqlite3.Error:
            continue
        run = os.path.basename(os.path.dirname(db))
        try:
            acted = {}
            for camp, n_rows, counted, waiting in c.execute(
                    "SELECT d.campaign_id, COUNT(*), COALESCE(SUM(t.counted),0),"
                    " SUM(CASE WHEN t.refusal IS 'awaiting_execution' THEN 1 ELSE 0 END)"
                    " FROM action_taken t JOIN decision_points d ON d.decision_id=t.decision_id"
                    " GROUP BY d.campaign_id"):
                acted[camp] = (n_rows or 0, counted or 0, waiting or 0)
            peak = {}
            for camp, mx_set, mn_rank, mx_lvl in c.execute(
                    "SELECT campaign_id, MAX(settlements), MIN(power_rank), MAX(lord_level)"
                    " FROM target_rows GROUP BY campaign_id"):
                peak[camp] = (mx_set, mn_rank, mx_lvl)
            final = {}
            for camp, income, setl, rank in c.execute(
                    "SELECT campaign_id, income, settlements, power_rank FROM target_rows t"
                    " WHERE turn = (SELECT MAX(turn) FROM target_rows x"
                    "               WHERE x.campaign_id = t.campaign_id)"
                    " GROUP BY campaign_id"):
                final[camp] = (income, setl, rank)
            for camp, first_ts, last_ts, n_dec, max_turn in c.execute(
                    "SELECT campaign_id, MIN(ts), MAX(ts), COUNT(*), MAX(turn) FROM decision_points"
                    " GROUP BY campaign_id"):
                n_rows, counted, waiting = acted.get(camp, (0, 0, 0))
                pk, fn = peak.get(camp, (None,) * 3), final.get(camp, (None,) * 3)
                row = {"campaign": camp, "faction": _faction_of(camp),
                       "turns": int(max_turn or 0), "decisions": n_dec,
                       # a decision with no action_taken row at all is not an attempt that
                       # failed -- nothing was ever tried, and it must not enter the rate
                       "taken": n_rows - waiting, "counted": counted, "waiting": waiting,
                       "no_action": max(0, n_dec - n_rows),
                       "first_ts": first_ts or 0,
                       # decision span, NOT campaign wall clock: it excludes the game load
                       # before the first decision and the postmortem after the last
                       "span_min": round(((last_ts or 0) - (first_ts or 0)) / 60.0, 1),
                       "run": run,
                       "best_settlements": pk[0], "best_power_rank": pk[1],
                       "best_lord_level": pk[2],
                       "last_income": fn[0], "last_settlements": fn[1],
                       "last_power_rank": fn[2]}
                if camp not in seen or row["decisions"] > seen[camp]["decisions"]:
                    seen[camp] = row
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return sorted(seen.values(), key=lambda r: r["first_ts"])


def by_action_type(con):
    return [dict(r) for r in con.execute(
        "SELECT action_type, COUNT(*) n, SUM(counted) ok,"
        " GROUP_CONCAT(DISTINCT refusal) refusals FROM action_taken"
        " WHERE refusal IS NOT 'awaiting_execution' GROUP BY action_type ORDER BY n DESC")]


def turns(con, campaign_id=None):
    """The per-turn series for ONE campaign.

    Ordering the whole table by turn mixes every campaign in the run dir together --
    496 campaigns produced 496 rows all labelled "turn 1" -- which reads as a time
    series and is not one.
    """
    if campaign_id is None:
        row = con.execute("SELECT campaign_id FROM target_rows ORDER BY ts DESC"
                          " LIMIT 1").fetchone()
        campaign_id = row[0] if row else None
    if campaign_id is None:
        return []
    return [dict(r) for r in con.execute(
        "SELECT turn,income,settlements,allies,vassals,power_rank FROM target_rows"
        " WHERE campaign_id=? ORDER BY turn", (campaign_id,))]


_CSS = """
:root{--bg:#12141a;--fg:#e6e8ee;--dim:#8b93a7;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--line:#242832;--card:#181b23}
@media(prefers-color-scheme:light){:root{--bg:#fbfbfd;--fg:#1b1f27;--dim:#5d6470;--line:#e3e6ec;--card:#fff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
a{color:inherit}h1{font-size:16px;margin:0 0 4px}h2{font-size:13px;margin:22px 0 8px;color:var(--dim);
text-transform:uppercase;letter-spacing:.08em}
.tabs{display:flex;gap:2px;margin:16px 0 0;border-bottom:1px solid var(--line)}
.tab{appearance:none;background:none;border:0;border-bottom:2px solid transparent;color:var(--dim);
font:inherit;text-transform:uppercase;letter-spacing:.08em;padding:8px 14px;cursor:pointer}
.tab:hover{color:var(--fg)}
.tab.on{color:var(--fg);border-bottom-color:var(--ok)}
.panel[hidden]{display:none}
.panel>h2:first-child{margin-top:16px}
.wrap{max-width:min(2600px,98vw);margin:0 auto;padding:20px 16px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:104px}
.card .v{font-size:20px}.card .k{color:var(--dim);font-size:11px;text-transform:uppercase}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;white-space:nowrap}
th,td{padding:5px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;position:sticky;top:0;background:var(--card)}
tr:last-child td{border-bottom:none}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.dim{color:var(--dim)}
.muted{color:var(--dim);font-size:11px;line-height:1.5;max-width:105ch;white-space:normal}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.btn{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:6px;
     padding:7px 12px;color:var(--fg);text-decoration:none;font-size:12px}
.btn:hover{border-color:var(--ok);color:var(--ok)}
.take{background:rgba(63,185,80,.13)}
.bar{display:inline-block;height:7px;background:var(--ok);border-radius:3px;vertical-align:middle}
.seg{display:inline-block;height:9px;vertical-align:middle;border-radius:2px;margin-right:1px;min-width:2px}
.seg.p1{background:#4c8dff}.seg.p2{background:#8b5cf6}.seg.p3{background:#d29922}.seg.p4{background:#3fb950}
.legend{color:var(--dim);font-size:11px;margin-bottom:8px}
.legend .seg{width:14px;margin:0 4px 0 10px}
.lanehead{margin:14px 0 6px;font-weight:600}
.barcell{min-width:190px;position:relative}
.bar2{display:inline-block;height:14px;border-radius:3px;vertical-align:middle;min-width:2px}
.bar2.ok{background:#3fb950}.bar2.warn{background:#d29922}.bar2.bad{background:#f85149}
.bar2.dimbar{background:#4c8dff;opacity:.55}
.blabel{margin-left:6px;font-size:11px;color:var(--dim)}
.mcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px;margin:12px 0}
.mcard{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.mcard.alert{border-color:var(--bad)}
.mhead{display:flex;align-items:center;justify-content:space-between;gap:8px}
.mname{font-weight:600;font-size:14px}
.mrole{color:var(--dim);font-size:11px;margin:2px 0 9px;white-space:normal;line-height:1.4}
.badge{font-size:10px;text-transform:uppercase;letter-spacing:.06em;padding:2px 7px;
       border-radius:10px;border:1px solid currentColor;white-space:nowrap}
.mrow{display:flex;justify-content:space-between;gap:12px;padding:3px 0;
      border-bottom:1px solid var(--line)}
.mrow:last-child{border-bottom:none}
.mk{color:var(--dim)}
.mv{text-align:right;white-space:nowrap}
.note{margin-top:9px;font-size:11px;line-height:1.5;white-space:normal}
th.grp{text-align:center;color:var(--fg);border-left:1px solid var(--line)}
td.gsep,th.gsep{border-left:1px solid var(--line)}

/* model metrics: tiles in a grid rather than stacked full-width tables. Two of the three
   questions this tab answers are about distributions, and the version this replaced drew
   both as text. Tiles are a fixed 330px so they pack 2-3 across without a chart wrapping
   mid-row, and each is self-contained -- readable without its neighbours. */
.mmgrid{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0}
.mmtile{border:1px solid var(--line);border-radius:8px;background:var(--card);padding:10px 12px}
.mmt{color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-size:11px;margin-bottom:6px}
.mms{color:var(--dim);font-size:11px;margin-top:6px;white-space:normal;max-width:330px;line-height:1.5}
.mmtbl{border-collapse:collapse}.mmtbl th{color:var(--dim);font-weight:400;text-align:left;padding:2px 8px 2px 0}
.mmtbl td{padding:2px 8px 2px 0;white-space:nowrap}
.mmcat{fill:#4c8dff;color:#4c8dff}.mmgnn{fill:#e0609c;color:#e0609c}
text.mmlab{fill:var(--fg);font:10px ui-monospace,monospace}
text.mmdim{fill:var(--dim);font:10px ui-monospace,monospace}
.mmci{stroke:var(--fg);opacity:.55}.mmax{stroke:var(--dim);opacity:.6}
.mmdimc{color:var(--dim)}.mmhot{color:var(--ok)}

.mmok{fill:var(--ok)}.mmbad{fill:var(--bad)}.mmdim{fill:var(--dim)}
text.sl{fill:var(--fg);font:10px ui-monospace,monospace}
text.sd{fill:var(--dim);font:10px ui-monospace,monospace}
.verdict{border:1px solid var(--line);border-left-width:4px;border-radius:8px;background:var(--card);
padding:12px 14px;margin:16px 0 4px;white-space:normal;line-height:1.6;max-width:120ch}
.verdict .vw{font-size:17px;letter-spacing:.06em;text-transform:uppercase;display:block;margin-bottom:6px}
.verdict.hold{border-left-color:var(--warn)}.verdict.raise{border-left-color:var(--ok)}
.verdict.lower{border-left-color:var(--bad)}
ul.mmb{margin:6px 0 0;padding-left:18px;color:var(--dim)}ul.mmb li{margin:3px 0}
"""


def _page(body, title="advisor v7"):
    return ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<style>%s</style><div class=wrap>%s</div>"
            % (html.escape(title), _CSS, body))


def _esc(v):
    return html.escape("" if v is None else str(v))


def _int(v):
    """Turn numbers arrive as floats from the bus; 4.0 is noise in a table."""
    try:
        return "%d" % float(v)
    except (TypeError, ValueError):
        return "" if v is None else str(v)


_TAGS = re.compile(r"\[\[/?[^\]]*\]\]")


def _strip_tags(v):
    """The game's own colour markup, e.g. '[[col:dip_attitude_4]]23[[/col]]'."""
    return _TAGS.sub("", str(v)).strip() if v else ""


# campaign_id is "<faction>_<16 hex>" (decisions/store.py campaign_key); the hex only
# distinguishes repeat plays of the same lord and carries no meaning on its own.
_CAMP_SUFFIX = re.compile(r"_[0-9a-f]{8,}$")


def _faction_of(campaign_id):
    s = str(campaign_id or "")
    return _CAMP_SUFFIX.sub("", s) or s


def _camp_label(campaign_id):
    """Faction first, hex second: showing the tail alone made every row look identical."""
    s = str(campaign_id or "")
    f = _faction_of(s)
    tail = s[len(f):].lstrip("_")
    return ("%s <span class=dim>#%s</span>" % (_esc(f), _esc(tail[:6])) if tail else _esc(f))


def _pct_cell(counted, taken, cls_ok=70, cls_warn=40):
    """A confirm rate needs a denominator. taken==0 means nothing was ever attempted,
    which is not the same as "attempted and none confirmed" -- rendering both as 0%
    painted campaigns red for a failure that never happened."""
    if not taken:
        return "<td class=dim>&mdash;</td>"
    pct = 100.0 * (counted or 0) / taken
    cls = "ok" if pct >= cls_ok else ("warn" if pct >= cls_warn else "bad")
    return "<td class='%s num'>%.0f%%</td>" % (cls, pct)


def _mix_str(mix, epsilon=None):
    if isinstance(mix, str):
        try:
            mix = json.loads(mix)
        except ValueError:
            return mix
    if isinstance(mix, dict) and mix:
        return ",".join("%s=%.2f" % (k, float(v)) for k, v in sorted(mix.items()))
    if epsilon is not None:
        # legacy trials recorded only --epsilon; session.py maps it to this mix
        e = float(epsilon)
        return ("exploit_tree=%.2f,random=%.2f <span class=dim>(legacy --epsilon %g)</span>"
                % (1.0 - e, e, e))
    return None


# fixed display order so the same slot means the same strategy on every row
# gnn_marwil, not gnn: the strategy was renamed after its ALGORITHM in 129d4a7 and this
# table was not. The GN slot read 00 on every trial that ran the gnn arm, and the real
# weight was pushed into the overflow text as "+gnn_marwil=0.10" -- so the one column meant
# to make mixes comparable by eye was wrong for exactly the arm being evaluated.
MIX_ORDER = (("exploit_tree", "ET", "p1"), ("gnn_marwil", "GN", "p2"),
             ("random", "RD", "p3"), ("ruleset", "RS", "p4"))


def _mix_dict(mix, epsilon=None):
    if isinstance(mix, str):
        try:
            mix = json.loads(mix)
        except ValueError:
            return None
    if isinstance(mix, dict) and mix:
        return {k: float(v) for k, v in mix.items()}
    if epsilon is not None:
        e = float(epsilon)
        return {"exploit_tree": 1.0 - e, "random": e}
    return None


def _mix_cell(mix, epsilon=None):
    """A fixed-width mix signature.

    Rendering the raw dict gave a different-length string on every row, so trials
    could not be compared by eye. Every row now shows the same four slots in the
    same order, with a proportion bar; the tooltip keeps the exact values.
    """
    d = _mix_dict(mix, epsilon)
    if not d:
        return "<span class=dim>-</span>"
    total = sum(d.values()) or 1.0
    bar, nums, extra = [], [], []
    for key, short, seg in MIX_ORDER:
        v = d.get(key, 0.0)
        pct = 100.0 * v / total
        if pct >= 0.5:
            bar.append("<span class='seg %s' style='width:%dpx'></span>" % (seg, round(pct * 0.6)))
        nums.append("<span class=%s>%02d</span>" % ("" if pct >= 0.5 else "dim", round(pct)))
    for k in sorted(d):
        if k not in {m[0] for m in MIX_ORDER}:
            extra.append("%s=%.2f" % (k, d[k]))
    tip = ", ".join("%s=%.2f" % (k, d[k]) for k in sorted(d))
    if epsilon is not None and not isinstance(mix, dict):
        tip += " (legacy --epsilon %g)" % float(epsilon)
    return ("<span title='%s' style='white-space:nowrap'>%s <span class=dim>%s</span>%s</span>"
            % (_esc(tip), "".join(bar), "/".join(nums),
               (" <span class=warn>+%s</span>" % _esc(",".join(extra))) if extra else ""))


def _ruleset_str(rs):
    if isinstance(rs, dict) and rs.get("name"):
        return "%s@%s" % (rs["name"], str(rs.get("sha256") or "")[:8] or "?")
    if rs:
        return str(rs)
    return None


# written by the loop when it runs out of actions, not drawn from the strategy mix --
# counting it as a strategy would dilute every real share
NOT_A_DRAW = ("forced_end_turn",)


def _policy_tally(counts):
    agg, rules, other = {}, {}, {}
    for p, n in counts.items():
        s = str(p) if p else "-"
        if s in NOT_A_DRAW:
            other[s] = other.get(s, 0) + n
        elif s.startswith("ruleset(") and s.endswith(")"):
            agg["ruleset"] = agg.get("ruleset", 0) + n
            rule = s[len("ruleset("):-1]
            rules[rule] = rules.get(rule, 0) + n
        elif s == "ruleset->random":
            # a retired spelling of ruleset_random_fallback; left as its own row it
            # looked like a distinct strategy nobody could account for
            agg["ruleset_random_fallback"] = agg.get("ruleset_random_fallback", 0) + n
        else:
            agg[s] = agg.get(s, 0) + n
    return agg, rules, other


def _policy_tally_html(title, counts):
    agg, rules, other = _policy_tally(counts)
    if not agg and not other:
        return ""
    total = sum(agg.values())
    rows = []
    for p, n in sorted(agg.items(), key=lambda kv: -kv[1]):
        extra = ""
        if p == "ruleset" and rules:
            extra = " <span class=dim>(%s)</span>" % _esc(
                ", ".join("%s %d" % (r, c)
                          for r, c in sorted(rules.items(), key=lambda kv: -kv[1])))
        rows.append("<tr><td>%s%s</td><td class=num>%d</td><td class=dim>%.1f%%</td></tr>"
                    % (_esc(p), extra, n, 100.0 * n / total if total else 0.0))
    for p, n in sorted(other.items(), key=lambda kv: -kv[1]):
        rows.append("<tr><td class=dim>%s</td><td class=num>%d</td>"
                    "<td class=dim>not a strategy draw</td></tr>" % (_esc(p), n))
    # only describe what this tally actually contains: the shared prose used to explain
    # forced_end_turn on the interrupt path (where it never occurs) and gnn_delegated on
    # the action path (likewise), which reads as a column that failed to fill
    notes = ["ruleset(rule) picks are aggregated under <b>ruleset</b> with the per-rule "
             "split alongside."]
    if other:
        notes.append("<b>forced_end_turn</b> is written by the loop when it runs out of "
                     "actions, so it is listed but kept out of the share denominator.")
    if any(str(p).startswith("gnn_delegated") for p in agg):
        notes.append("<b>gnn_delegated_*</b> is a gnn draw on the interrupt path, where "
                     "there is no gnn model, handed to exploit_tree.")
    retired = [p for p in ("cold_random", "epsilon_random", "explore", "exploit",
                           "interrupt_exploit", "interrupt_explore") if p in agg]
    if retired:
        notes.append("Retired epsilon-era strings still present here from older campaigns: "
                     "<b>%s</b> &mdash; the novelty score that <i>explore</i> named has since "
                     "been deleted, so it can only appear on pre-retirement rows."
                     % _esc(", ".join(retired)))
    return ("<h2>%s</h2><p class=muted>%s</p>"
            "<div class=scroll><table><tr><th>policy<th class=num>picks<th>share</tr>"
            "%s</table></div>" % (_esc(title), " ".join(notes), "".join(rows)))


def render_interrupts(runs_root=RUNS_ROOT):
    import collections
    per_screen = collections.OrderedDict()
    chosen = collections.Counter()
    offered = collections.Counter()
    policies = collections.Counter()
    total = 0
    for db in common.run_dbs(runs_root):
        try:
            c = dbopen.connect(db, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(interrupt_decisions)")}
            pol = "policy" if "policy" in cols else "NULL"
            for kind, opts_json, pick, p in c.execute(
                    "SELECT kind, options_json, chosen, %s FROM interrupt_decisions" % pol):
                total += 1
                per_screen[kind] = per_screen.get(kind, 0) + 1
                policies[p or "-"] += 1
                if pick:
                    chosen[(kind, pick)] += 1
                try:
                    for o in (json.loads(opts_json) or {}):
                        offered[(kind, o)] += 1
                except Exception:
                    pass
        except sqlite3.Error:
            pass
        finally:
            c.close()

    try:
        import interrupt_model as IM
        import strategies as ST
        mix_note = ("the sampler draws each pick from the run's strategy mix over %s "
                    "(--strategies, recorded on the trial). <b>gnn has no interrupt-side "
                    "model</b>, so a gnn draw here is delegated to exploit_tree and recorded "
                    "as <code>gnn_delegated_*</code> &mdash; on this path the effective "
                    "exploit_tree share is exploit_tree + gnn" % "/".join(ST.NAMES))
        r = IM.InterruptRanker()
        if r.ready:
            sr = (r.meta or {}).get("screen_rows") or {}
            per = ", ".join("%s %s(%s)" % (s, "hot" if sr.get(s, 0) >= IM.MIN_ROWS
                                           else "cold", sr.get(s, "?"))
                            for s in sorted((r.meta or {}).get("screens") or []))
            state = ("%s; <b>model</b> &mdash; fitted on %s rows, exploit_tree draws use it "
                     "per screen (hot &ge; %d rows, cold screens fall back to random): %s"
                     % (mix_note, (r.meta or {}).get("rows", "?"), IM.MIN_ROWS, per or "&mdash;"))
        else:
            state = ("%s; <b>no interrupt model fitted</b> &mdash; exploit_tree draws fall back "
                     "to random (provenance exploit_tree_random_fallback) until %d labelled rows "
                     "per screen exist; a decision is only labelled once its campaign plays on "
                     "past it" % (mix_note, IM.MIN_ROWS))
    except Exception as e:
        state = "unavailable: %s" % _esc(repr(e)[:120])

    if not total:
        return ("<h2>blocking menus</h2><p class=muted>sampler: %s</p>"
                "<p class=muted>no interrupt decisions recorded yet</p>" % state)

    recent = []
    for db in common.run_dbs(runs_root)[:3]:
        try:
            c = dbopen.connect(db, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(interrupt_decisions)")}
            pol = "policy" if "policy" in cols else "NULL"
            for ts, kind, oj, pick, counted, ref, lat, p, cj in c.execute(
                    "SELECT ts, kind, options_json, chosen, counted, refusal, latency_ms, %s,"
                    " campaign_json FROM interrupt_decisions ORDER BY interrupt_id DESC LIMIT 120"
                    % pol):
                try:
                    opts = json.loads(oj) if oj else {}
                except Exception:
                    opts = {}
                try:
                    fac = (json.loads(cj) or {}).get("faction")
                except Exception:
                    fac = None
                recent.append((ts, kind, opts, pick, counted, ref, lat, p, fac))
        except sqlite3.Error:
            pass
        finally:
            c.close()
    recent.sort(key=lambda r: -(r[0] or 0))
    def _clean(opt):
        s = str(opt)
        for pre in ("CcoCdirEventsDilemmaChoiceDetailRecord", "button_", "captive_option_"):
            s = s.replace(pre, "")
        return s

    def _short(opt, n=28):
        s = _clean(opt)
        return s if len(s) <= n else "Ã¢â‚¬Â¦" + s[-(n - 1):]

    def _shortfac(fac):
        parts = str(fac or "?").split("_")
        return "_".join(parts[2:]) if len(parts) > 3 else str(fac or "?")

    rec_rows = []
    for ts, kind, opts, pick, counted, ref, lat, p, fac in recent[:40]:
        def _pred(k, field):
            v = opts.get(k) if isinstance(opts.get(k), dict) else {}
            x = v.get(field)
            return x if isinstance(x, (int, float)) else None

        def _lvl(k):
            fmt = lambda x: ("%.2f" % x) if isinstance(x, (int, float)) else "-"
            return "%s exploit=%s" % (_short(k), fmt(_pred(k, "exploit")))
        ordered = sorted(opts, key=lambda k: -(_pred(k, "exploit")
                                               if _pred(k, "exploit") is not None else -1e9))
        tip = ", ".join(_lvl(k) for k in ordered)
        res = ("<span class=ok>OK</span>" if counted
               else "<span class=bad>%s</span>" % _esc((ref or "fail")[:22]))
        pick_lbl = ("<b title='%s'>%s</b>" % (_esc(tip), _esc(_short(pick)))
                    if pick else "<span class=dim>-</span>")
        rec_rows.append(
            "<tr><td class=dim>%s</td><td>%s</td><td class=dim>%s</td><td>%s</td>"
            "<td>%s</td><td class=dim>%d</td><td class=dim>%s</td><td class=dim>%s</td></tr>"
            % (time.strftime("%H:%M:%S", time.localtime(ts or 0)), _esc(kind),
               _esc(_shortfac(fac)[:24]), res, pick_lbl, len(opts),
               _esc(p or "-"), ("%.1fs" % (lat / 1000.0)) if lat else "-"))
    recent_tbl = ("<h2>recent interrupt decisions <span class=dim>("
                  "hover chosen for all options and scores)</span></h2><div class=scroll><table>"
                  "<tr><th>time<th>screen<th>faction<th>result<th>chosen<th>n opts"
                  "<th>policy<th>latency</tr>%s</table></div>"
                  % ("".join(rec_rows) or "<tr><td class=dim colspan=8>none recorded</td></tr>"))

    rows = []
    for (kind, opt), n in sorted(chosen.items(), key=lambda kv: (kv[0][0], -kv[1])):
        seen = offered.get((kind, opt), 0)
        rate = (100.0 * n / seen) if seen else None
        rows.append("<tr><td>%s</td><td title='%s'>%s</td><td class=num>%d</td>"
                    "<td class=num>%d</td><td class=num>%s</td></tr>"
                    % (_esc(kind), _esc(_clean(opt)), _esc(_short(opt, 48)), n, seen,
                       "&mdash;" if rate is None else "%.0f%%" % rate))
    screens = " &middot; ".join("%s <b>%d</b>" % (_esc(k), v) for k, v in per_screen.items())
    head = ("<h2>blocking menus <span class=dim>(%d decisions)</span></h2>"
            "<p class=muted>sampler: %s</p>"
            "<p class=muted>%s</p>" % (total, state, screens))
    prov = _policy_tally_html("interrupt provenance (all runs)", policies)
    agg = ("<p class=muted>Dilemmas, pre-battle, post-battle and occupation. <b>taken</b> is how "
           "often we picked that option; <b>offered</b> is how often the screen showed it, so the "
           "rate exposes whether a choice ever actually gets picked.</p>"
           "<div class=scroll><table><tr><th>screen<th>option<th>taken<th>offered<th>rate</tr>"
           "%s</table></div>" % "".join(rows))
    return head + prov + recent_tbl + agg


def starts_summary(runs_root=RUNS_ROOT):
    camps = {}
    for db in common.run_dbs(runs_root):
        try:
            c = dbopen.connect(db, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            for camp, n_dec, max_turn, cjson in c.execute(
                    "SELECT campaign_id, COUNT(*), MAX(turn), MIN(campaign) FROM decision_points"
                    " GROUP BY campaign_id"):
                try:
                    faction = (json.loads(cjson) or {}).get("faction") if cjson else None
                except Exception:
                    faction = None
                if not faction:
                    continue
                taken, counted = c.execute(
                    "SELECT COUNT(*), COALESCE(SUM(t.counted),0) FROM action_taken t"
                    " JOIN decision_points d ON d.decision_id=t.decision_id"
                    " WHERE d.campaign_id=? AND t.refusal IS NOT 'awaiting_execution'",
                    (camp,)).fetchone()
                best = c.execute(
                    "SELECT MAX(settlements), MIN(power_rank), MAX(lord_level), MAX(vassals),"
                    " MAX(allies) FROM target_rows WHERE campaign_id=?",
                    (camp,)).fetchone() or (None,) * 5
                row = {"faction": faction, "turns": int(max_turn or 0), "decisions": n_dec,
                       "taken": taken or 0, "counted": counted or 0,
                       "settlements": best[0], "power_rank": best[1],
                       "lord_level": best[2], "vassals": best[3], "allies": best[4]}
                prev = camps.get(camp)
                if prev is None or row["decisions"] > prev["decisions"]:
                    camps[camp] = row
        except sqlite3.Error:
            pass
        finally:
            c.close()
    agg = {}
    for r in camps.values():
        a = agg.setdefault(r["faction"], {"faction": r["faction"], "n": 0, "turns": 0, "taken": 0,
                                          "counted": 0, "best_turns": 0, "settlements": None,
                                          "power_rank": None, "lord_level": None,
                                          "vassals": None, "allies": None,
                                          "ever_vassal": 0, "ever_ally": 0})
        a["n"] += 1
        a["turns"] += r["turns"]
        a["taken"] += r["taken"]
        a["counted"] += r["counted"]
        a["best_turns"] = max(a["best_turns"], r["turns"])
        a["ever_vassal"] += 1 if (r.get("vassals") or 0) > 0 else 0
        a["ever_ally"] += 1 if (r.get("allies") or 0) > 0 else 0
        for k, better in (("settlements", max), ("lord_level", max), ("vassals", max),
                          ("allies", max), ("power_rank", min)):
            v = r[k]
            if v is not None:
                a[k] = v if a[k] is None else better(a[k], v)
    out = []
    for a in agg.values():
        a["avg_turns"] = round(a["turns"] / a["n"], 1) if a["n"] else 0.0
        out.append(a)
    return sorted(out, key=lambda a: (-a["n"], -a["avg_turns"]))


def faction_action_stats(runs_root=RUNS_ROOT):
    import collections
    main = collections.defaultdict(lambda: [0, 0, 0.0])
    inter = collections.defaultdict(lambda: [0, 0, 0.0])
    for db in common.run_dbs(runs_root):
        try:
            c = dbopen.connect(db, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            rows = list(c.execute(
                "SELECT d.campaign_id, d.ts, d.campaign, t.action_type, t.counted, t.latency_ms"
                " FROM decision_points d JOIN action_taken t ON t.decision_id=d.decision_id"
                " WHERE t.refusal IS NOT 'awaiting_execution'"
                " ORDER BY d.campaign_id, d.decision_id"))
            for i, (camp, ts, cjson, at, counted, lat) in enumerate(rows):
                try:
                    fac = (json.loads(cjson) or {}).get("faction")
                except Exception:
                    fac = None
                if not fac:
                    continue
                true_s = (lat or 0) / 1000.0
                if i + 1 < len(rows) and rows[i + 1][0] == camp and rows[i + 1][1] and ts:
                    gap = rows[i + 1][1] - ts
                    if 0 <= gap <= 600:
                        true_s = gap
                s = main[(fac, at)]
                s[0] += 1
                s[1] += 1 if counted else 0
                s[2] += true_s
            for cjson, kind, counted, lat in c.execute(
                    "SELECT campaign_json, kind, counted, latency_ms FROM interrupt_decisions"):
                try:
                    fac = (json.loads(cjson) or {}).get("faction")
                except Exception:
                    fac = None
                if not fac:
                    continue
                s = inter[(fac, kind)]
                s[0] += 1
                s[1] += 1 if counted else 0
                s[2] += (lat or 0) / 1000.0
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return main, inter


def _matrix_tables(data, title):
    kinds = sorted({k for (_f, k) in data})
    facs = sorted({f for (f, _k) in data})
    if not kinds:
        return "<h2>%s</h2><p class=dim>no data recorded yet</p>" % _esc(title)
    head = "<tr><th>faction" + "".join("<th>%s" % _esc(k) for k in kinds) + "</tr>"
    rate_rows, time_rows = [], []
    for f in facs:
        rc, tc = [], []
        for k in kinds:
            t, ok, secs = data.get((f, k), (0, 0, 0.0))
            if not t:
                rc.append("<td class=dim>-</td>")
                tc.append("<td class=dim>-</td>")
                continue
            pct = 100.0 * ok / t
            cls = "ok" if pct >= 80 else ("warn" if pct >= 40 else "bad")
            rc.append("<td class=%s>%.0f%% <span class=dim>(%d/%d)</span></td>" % (cls, pct, ok, t))
            avg = secs / t
            tcls = "ok" if avg < 5 else ("warn" if avg < 15 else "bad")
            tc.append("<td class=%s>%.0fs <span class=dim>(%.1fs/try)</span></td>" % (tcls, secs, avg))
        rate_rows.append("<tr><td>%s</td>%s</tr>" % (_esc(f), "".join(rc)))
        time_rows.append("<tr><td>%s</td>%s</tr>" % (_esc(f), "".join(tc)))
    return ("<h2>%s &mdash; pass rate</h2><div class=scroll><table>%s%s</table></div>"
            "<h2>%s &mdash; total time (avg per try)</h2><div class=scroll><table>%s%s</table></div>"
            % (_esc(title), head, "".join(rate_rows), _esc(title), head, "".join(time_rows)))


def _tail_jsonl(path, n=400):
    rows = []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            pos = fh.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= n and len(data) < (64 << 20):
                step = min(4 << 20, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
    except OSError:
        return rows
    for ln in data.splitlines()[-n:]:
        try:
            rows.append(json.loads(ln.decode("utf-8", "replace")))
        except ValueError:
            pass
    return rows


def render_diplomacy(run_dir):
    rows = _tail_jsonl(os.path.join(run_dir, "diplomacy.jsonl"), 600)
    if not rows:
        return ("<h2>diplomacy stream</h2><p class=muted>no diplomacy.jsonl rows in this run "
                "yet &mdash; the stream fills as deals and checkpoints occur</p>")
    kinds = {}
    for r in rows:
        k = r.get("kind")
        k = "%s/%s" % (k, r.get("channel")) if k == "deal" else k
        kinds[k] = kinds.get(k, 0) + 1
    cards = "".join("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                    % (_esc(k), v) for k, v in sorted(kinds.items()))
    deals = [r for r in rows if r.get("kind") == "deal"][-60:]
    tr = []
    for d in reversed(deals):
        ch = d.get("channel")
        p = d.get("panel") or {}
        chance = gift = standing = ""
        chance_cls = "dim"
        if ch == "outgoing":
            who = d.get("faction")
            resp = p.get("response") or {}
            if p.get("sent"):
                # a sent deal is not a won deal -- the ai still answers it
                a = resp.get("answer")
                if resp.get("accepted"):
                    ans, cls = "accepted", "ok"
                elif a:
                    ans, cls = "declined", "warn"
                else:
                    ans, cls = "sent, no answer", "warn"
            elif p.get("failed_at") == "ai_would_refuse":
                ans, cls = "ai would refuse", "bad"
            else:
                # not the ai saying no: we never managed to stage the deal
                ans, cls = "not staged (%s)" % (p.get("failed_at") or "?"), "bad"
            sc = p.get("success_chance")
            if sc not in (None, ""):
                # the game's label_deal_success_chance is NOT a percentage: it runs to
                # -1021.5 here. What it carries is the sign -- negative means the ai
                # refuses (468/477 observed), positive means the deal was sendable
                # (578/578). Printing it with a % suffix produced "-1021.5%".
                try:
                    v = float(sc)
                    chance = "%+.1f" % v
                    chance_cls = "num ok" if v >= 0 else "num bad"
                except (TypeError, ValueError):
                    chance, chance_cls = str(sc), "num dim"
            tb = d.get("treaty_before") or {}
            if tb.get("standing") is not None:
                standing = "%g%s" % (tb["standing"], " at war" if tb.get("at_war") else "")
            detail = ",".join(d.get("terms") or [])
            if d.get("gift"):
                # folded in rather than given a column: most deals carry no gift
                detail += " + gift %s" % d["gift"]
        else:
            who = d.get("proposer") or ",".join(d.get("faction_keys") or [])[:40]
            ans = d.get("answer") or "-"
            ok = bool(d.get("confirmed")) and ans in ("accept", "acknowledge", "join")
            cls = "ok" if ok else ("warn" if ans == "decline" else "bad")
            # attitude is only ever recorded on incoming proposals
            att = _strip_tags(d.get("attitude"))
            detail = str(d.get("speech") or "")[:70]
            if att:
                detail = "<span class=dim>attitude %s</span> &middot; %s" % (_esc(att),
                                                                            _esc(detail))
            else:
                detail = _esc(detail)
        tr.append("<tr><td class=num>%s</td><td>%s</td><td>%s</td><td class=%s>%s</td>"
                  "<td class='%s'>%s</td><td class='num dim'>%s</td>"
                  "<td style='white-space:normal'>%s</td></tr>"
                  % (_int(d.get("turn")), _esc(ch), _esc(str(who)[:38]), cls, _esc(ans),
                     chance_cls, _esc(chance) or "<span class=dim>-</span>",
                     _esc(standing) or "-",
                     detail if ch != "outgoing" else _esc(detail)))
    return ("<h2>diplomacy stream <span class=dim>(last %d rows of this run)</span></h2>"
            "<div class=cards>%s</div>"
            "<h2>recent deal events</h2>"
            "<div class=legend><b>accepted / declined</b> are the ai's answer to a deal we "
            "actually sent. <b>ai would refuse</b> means the game told us up front it would "
            "say no, so nothing was sent. <b>not staged</b> is our own failure to build the "
            "deal, not a diplomatic outcome &mdash; worth separating when reading refusal "
            "rates. <b>deal score</b> is the game's own <code>success_chance</code> readout, "
            "and despite the name it is <b>not a percentage</b> &mdash; it runs past "
            "&minus;1000 here. Its sign is what carries: negative and the ai refuses "
            "(468 of 477 observed), positive and the deal went out (578 of 578). "
            "<b>standing</b> is the relationship before the deal.</div>"
            "<div class=scroll><table>"
            "<tr><th class=num>turn<th>channel<th>faction<th>outcome"
            "<th class=num title='the game&#39;s raw success_chance readout, not a percent -- "
            "the sign predicts whether the ai accepts'>deal score"
            "<th class=num>standing<th>terms / speech</tr>"
            "%s</table></div>"
            % (len(rows), cards,
               "".join(tr) or "<tr><td class=dim colspan=7>no deal rows yet</td></tr>"))


def _med(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)))
    return v[len(v) // 2] if v else None


def render_timing(run_dir):
    pts = [r for r in _tail_jsonl(os.path.join(run_dir, "decisions_stream.jsonl"), 800)
           if r.get("kind") == "decisions_point"]
    picks = [r for r in _tail_jsonl(os.path.join(run_dir, "decisions_requests.jsonl"), 800)
             if r.get("kind") == "pick"]
    out = []
    if pts:
        profs = [p.get("profile") or {} for p in pts]
        rows = [("collect total", _med([p.get("ms") for p in pts]))]
        for k, label in (("wave_a_ms", "wave A (world)"), ("wave_b_ms", "wave B (entities)"),
                         ("wave_c_ms", "wave C (moves)"),
                         ("campaign_offers/diplomacy", "diplomacy enum (turn-first spike)"),
                         ("lord_pools_ms", "lord pools")):
            rows.append((label, _med([pr.get(k) for pr in profs])))
        out.append("<h2>collect (recorder), median ms over last %d snapshots</h2>"
                   "<div class=scroll><table><tr><th>stage<th>median ms</tr>%s</table></div>"
                   % (len(pts), "".join("<tr><td>%s</td><td class=num>%s</td></tr>"
                                        % (_esc(a), _esc("%.0f" % b if b is not None else "-"))
                                        for a, b in rows)))
    tms = []
    for p in picks:
        t = p.get("timings")
        if isinstance(t, str):
            try:
                import ast
                t = ast.literal_eval(t)
            except Exception:
                t = None
        if isinstance(t, dict):
            tms.append(t)
    if tms:
        rows = []
        for k, label in (("roundtrip_ms", "snapshot roundtrip"), ("collect_ms", "collect"),
                         ("store_ms", "sqlite store"), ("pickup_lag_ms", "recorder pickup"),
                         ("score_ms", "scoring"), ("trace_ms", "trace write"),
                         ("housekeep_ms", "housekeeping")):
            rows.append((label, _med([t.get(k) for t in tms])))
        parts = {}
        for t in tms:
            for k, v in (t.get("housekeep_parts") or {}).items():
                parts.setdefault(k, []).append(v)
        prow = ", ".join("%s %sms" % (_esc(k), _esc("%.0f" % (_med(v) or 0)))
                         for k, v in sorted(parts.items()))
        out.append("<h2>advisor decision cycle, median ms over last %d picks</h2>"
                   "<div class=scroll><table><tr><th>component<th>median ms</tr>%s</table></div>"
                   "<p class=muted>housekeep parts (median): %s</p>"
                   % (len(tms), "".join("<tr><td>%s</td><td class=num>%s</td></tr>"
                                        % (_esc(a), _esc("%.0f" % b if b is not None else "-"))
                                        for a, b in rows), prow or "&mdash;"))
    if not out:
        return "<h2>timing</h2><p class=muted>no timing rows in this run yet</p>"
    return "".join(out)


def _throughput_from_log(lines):
    secs, turns, camps, pending = 0.0, 0, 0, 0
    for ln in lines:
        if ln.startswith("== turn ") and " done:" in ln:
            pending += 1
            continue
        m = re.match(r"campaign \d+ -> \S+ in ([\d.]+)s", ln)
        if m:
            camps += 1
            secs += float(m.group(1))
            turns += pending
            pending = 0
    if not camps or secs <= 0:
        return {}
    h = secs / 3600.0
    return {"campaigns_per_hour": round(camps / h, 2), "turns_per_hour": round(turns / h, 2),
            "turns_per_campaign": round(float(turns) / camps, 2), "campaigns": camps}


def _session_state():
    st = {"log": None, "campaign": None, "faction": None, "turn": None,
          "outcomes": [], "session": None, "tail": [], "batch_timeouts": 0,
          "rate": {}, "mix": None, "ruleset": None}
    try:
        lp = open(CURRENT_LOG, encoding="utf-8-sig").read().strip()
        if os.path.isfile(lp):
            st["log"] = lp
            lines = open(lp, encoding="utf-8", errors="replace").read().splitlines()
            st["tail"] = lines[-22:]
            st["batch_timeouts"] = sum(1 for ln in lines if "batch timeout" in ln)
            st["rate"] = _throughput_from_log(lines)
            for ln in lines:
                if st["mix"] is None and ln.startswith("strategy mix: "):
                    st["mix"] = ln[len("strategy mix: "):].strip()
                if st["ruleset"] is None and ln.startswith("ruleset: "):
                    st["ruleset"] = ln[len("ruleset: "):].strip()
            for ln in reversed(lines):
                if st["campaign"] is None and ln.startswith("CAMPAIGN "):
                    m = re.match(r"CAMPAIGN (\d+/\d+)\s+\(up to (\d+) turns, faction=(\S+)\)", ln)
                    if m:
                        st["campaign"] = "%s x%s" % (m.group(1), m.group(2))
                        st["faction"] = m.group(3).rstrip(")")
                if st["turn"] is None and st["campaign"] is None and ln.startswith("== TURN "):
                    tv = ln.split("== TURN ", 1)[1].split(" ")[0]
                    st["turn"] = tv[:-2] if tv.endswith(".0") else tv
                if len(st["outcomes"]) < 3 and re.match(r"campaign \d+ -> ", ln):
                    st["outcomes"].append(ln.strip())
                if st["campaign"] and st["turn"] and len(st["outcomes"]) >= 3:
                    break
    except OSError:
        pass
    return st


def _live_ruleset(raw):
    if not raw:
        return "-"
    m = re.match(r"(\S+) \(\d+ rules, sha256 ([0-9a-f]+)\)", raw)
    if m:
        return "%s@%s" % (m.group(1), m.group(2)[:8])
    return raw[:32]


def render_live(run_dir):
    procs, wh3 = _ps()
    st = _session_state()
    sess = next((p for p in procs if "session.py" in p[2]), None)
    mgr = next((p for p in procs if "manager.py" in p[2]), None)

    def card(k, v, cls=""):
        return ("<div class=card><div class=k>%s</div><div class='v %s'>%s</div></div>"
                % (_esc(k), cls, v))
    try:
        wh3n = int(wh3)
    except (TypeError, ValueError):
        wh3n = -1
    cards = "".join([
        card("session", "<span class=ok>up</span> pid %s" % sess[0] if sess
             else "<span class=bad>DOWN</span>"),
        card("game", "<span class=ok>up</span>" if wh3n == 1 else
             ("<span class=warn>down</span>" if wh3n == 0
              else "<span class=bad>%s procs</span>" % wh3)),
        card("recorder", "<span class=ok>up</span>" if mgr else "<span class=bad>DOWN</span>"),
        card("campaign", _esc(st["campaign"] or "-")),
        card("faction", _esc((st["faction"] or "-")[:24])),
        card("turn", _esc(st["turn"] or "-")),
        # the formatted mix is 50 chars, so the old [:48] cut the last weight in half and
        # the card read "...ruleset=0" -- the fixed-width signature fits and lines up
        card("mix ET/GN/RD/RS", _mix_cell(st["mix"])),
        card("ruleset", _esc(_live_ruleset(st["ruleset"]))),
    ] + [
        # n= matters: these are session-lifetime averages, and early on they rest on a
        # couple of campaigns. Hardcoding class=ok made a collapse look identical to health.
        card(k, "&mdash;" if (st["rate"] or {}).get(v) is None
             else "%s <span class=dim>(n=%s)</span>"
                  % (st["rate"][v], (st["rate"] or {}).get("campaigns", "?")))
        for k, v in (("campaigns/hr", "campaigns_per_hour"),
                     ("turns/hr", "turns_per_hour"),
                     ("turns/campaign", "turns_per_campaign"))
    ])

    alerts = []
    try:
        u = _tail_jsonl(os.path.join(RUNS_ROOT, "unhandled_screens.jsonl"), 1)
        if u:
            age_s = time.time() - (u[-1].get("ts") or 0)
            cls = "bad" if age_s < 3600 else "dim"
            alerts.append("<span class=%s>last unhandled screen: %s %s (%.1fh ago)</span>"
                          % (cls, _esc(u[-1].get("screen")), _esc(u[-1].get("unknown")),
                             age_s / 3600.0))
    except Exception:
        pass
    if st["log"]:
        try:
            err = st["log"][:-4] + ".err"
            tail_err = open(err, encoding="utf-8", errors="replace").read().splitlines()[-200:]
            n_to = sum(1 for ln in tail_err if "bus timeout" in ln)
            if n_to:
                alerts.append("<span class=warn>%d bus-timeout lines in recent stderr "
                              "(defeat screens produce these in pairs)</span>" % n_to)
        except OSError:
            pass
    if st["batch_timeouts"]:
        alerts.append("<span class=bad>%d BATCH timeouts -- the wave path is "
                      "losing replies</span>" % st["batch_timeouts"])
    for o in st["outcomes"]:
        cls = ("ok" if "completed" in o else
               "dim" if ("defeated" in o or "stagnant" in o) else "warn")
        alerts.append("<span class=%s>%s</span>" % (cls, _esc(o)))
    alert_html = ("<h2>signals</h2><p class=muted>%s</p>"
                  % (" &middot; ".join(alerts) or "nothing notable"))

    tail = ("<h2>session log <span class=dim>%s</span></h2><pre class=scroll>%s</pre>"
            % (_esc(os.path.basename(st["log"] or "none")),
               _esc("\n".join(st["tail"]) or "no session log")))
    return ("<div class=cards>%s</div>" % cards) + alert_html + tail


def render_endings(runs_root=RUNS_ROOT, limit=20):
    path = os.path.join(runs_root, "postmortems.jsonl")
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    outcomes, suspicious, total = {}, 0, 0
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        total += 1
        outcomes[r.get("outcome")] = outcomes.get(r.get("outcome"), 0) + 1
        v = (r.get("plausibility") or {}).get("verdict") or ""
        if "SUSPICIOUS" in v or "MISLABELED" in v:
            suspicious += 1
        rows.append(r)
    rows = rows[-limit:]
    if not rows:
        return ""
    tr = []
    for r in reversed(rows):
        pl = r.get("plausibility") or {}
        verdict = pl.get("verdict") or "-"
        # "consistent_with_real_defeat" means the death was genuine, which is not a
        # success -- reserve ok/warn/bad for harness health and mark this one neutrally
        cls = ("bad" if ("SUSPICIOUS" in verdict or "MISLABELED" in verdict) else
               "warn" if verdict.startswith("harness") else "dim")
        traj = r.get("trajectory") or []
        tline = " ".join("%s:%s" % (_int(t.get("turn")), _int(t.get("settlements")))
                         for t in traj[-4:])
        tr.append("<tr><td class=dim>%s</td><td>%s</td><td>%s</td><td class=num>%s</td>"
                  "<td class=%s style='white-space:normal'>%s</td>"
                  "<td class=dim style='white-space:normal'>%s</td><td class=dim>%s</td></tr>"
                  % (_esc(str(r.get("when", ""))[5:19]), _esc(str(r.get("faction") or "-")),
                     _esc(r.get("outcome")), _int(r.get("turns_played")),
                     cls, _esc(verdict),
                     _esc("; ".join(pl.get("evidence") or [])),
                     _esc(tline)))
    spread = ", ".join("%s %d" % (k, v) for k, v in
                       sorted(outcomes.items(), key=lambda kv: -kv[1]))
    return ("<h2>campaign endings &mdash; was it a real defeat? "
            "<span class=dim>(last %d of %d)</span></h2>"
            "<p class=muted>Verdicts are advisory: they argue from the trajectory, the engine "
            "death row, and final-turn battles. turn:settlements shows the death spiral or the "
            "healthy line that just stopped. A verdict is <b>not</b> a quality score &mdash; "
            "<i>consistent_with_real_defeat</i> means the death was genuine, so it is shown "
            "neutrally; only <span class=warn>harness_failure</span> and "
            "<span class=bad>SUSPICIOUS/MISLABELED</span> are flagged. Over all %d recorded "
            "endings: %s &mdash; and <b>%d</b> were flagged suspicious or mislabelled, so an "
            "absence of red below is a real signal rather than a column that never fills.</p>"
            "<div class=scroll><table><tr><th>when<th>faction<th>outcome<th class=num>turns"
            "<th>verdict<th>evidence<th>turn:settlements</tr>%s</table></div>"
            % (len(rows), total, total, _esc(spread), suspicious, "".join(tr)))


def render_faction_matrix():
    main, inter = faction_action_stats()
    intro = ("<p class=muted>Every campaign across every run dir. A cell far below its column's "
             "norm is a faction-specific gap; a slow cell is a faction-specific stall. Main-action "
             "time is TRUE cost &mdash; decision-to-next-decision wall clock, which includes the "
             "hidden waits the latency fields miss; interrupt time is click latency. Interrupt "
             "screens count the same confirmation law as actions.</p>")
    return intro + _matrix_tables(main, "main actions") + _matrix_tables(inter, "interrupt screens")


def render_starts():
    rows = starts_summary()
    if not rows:
        return "<p class=muted>no campaigns recorded yet</p>"
    tr = []
    for a in rows:
        def _c(v, fmt="%s"):
            return "&mdash;" if v is None else fmt % v

        def _ever(n):
            return ("%d/%d" % (n, a["n"])) if n else "<span class=dim>0</span>"
        tr.append("<tr><td>%s</td><td class=num>%d</td><td class=num>%.1f</td>"
                  "<td class=num>%d</td><td class=num>%s</td><td class=num>%s</td>"
                  "<td class=num>%s</td><td class=num>%s</td><td class=num>%s</td>%s</tr>"
                  % (_esc(a["faction"]), a["n"], a["avg_turns"], a["best_turns"],
                     _c(a["settlements"], "%g"), _c(a["power_rank"], "%g"),
                     _c(a["lord_level"], "%g"),
                     _ever(a["ever_ally"]), _ever(a["ever_vassal"]),
                     _pct_cell(a["counted"], a["taken"])))
    return ("<h2>starts <span class=dim>(%d factions, %d campaigns)</span></h2>"
            "<div class=legend>One row per playable start, most-played first &mdash; the "
            "per-campaign view is the <b>overview</b> tab. <b>best</b> columns are the peak "
            "that start ever reached across all its campaigns; power rank counts downwards, "
            "so lower is better. These peaks come from few samples: campaigns average about "
            "four recorded turns, so a start's best is usually drawn from its opening turns "
            "rather than anything it built. <b>ever allied / ever vassal</b> count how many "
            "of that start's campaigns ever reached one at all &mdash; they were previously "
            "a 'best vassals' column that could only ever print 0 or 1. Everything here pools "
            "every strategy era recorded in the run dir, so <b>confirmed</b> mixes cold-random "
            "campaigns with gnn ones.</div>"
            "<div class=scroll><table><tr><th>lord / faction<th class=num>campaigns"
            "<th class=num>avg turns"
            "<th class=num>best turns<th class=num>best settlements"
            "<th class=num title='lower is better'>best power rank"
            "<th class=num>best lord level"
            "<th class=num title='campaigns that ever had an ally'>ever allied"
            "<th class=num title='campaigns that ever had a vassal'>ever vassal"
            "<th class=num>confirmed</tr>%s</table></div>"
            % (len(rows), sum(a["n"] for a in rows), "".join(tr)))


def render_head(con, run_dir):
    """Two groups, because these are two different things. The first five describe one
    campaign at one moment; the rest are lifetime totals over every campaign ever
    recorded into this run dir. Rendering them as one undifferentiated row invited
    reading the totals as the current campaign's."""
    s = summary(con)
    age = s.get("state_age")
    stale = age is not None and age > 900
    state = "".join("<div class=card><div class=k>%s</div><div class='v %s'>%s</div></div>"
                    % (k, cls, v) for k, v, cls in
                    (("faction", _esc(s["faction"]), "dim" if stale else ""),
                     ("turn", _int(s["turn_now"]), ""),
                     ("settlements", _int(s["settlements"]), ""),
                     ("power rank", _int(s["power_rank"]), ""),
                     ("lord level", _int(s["lord_level"]), "")))
    totals = "".join("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                     % (k, v) for k, v in
                     (("campaigns", s["campaigns"]), ("turns", s["turns"]),
                      ("decisions", s["decisions"]), ("offers", s["offers"]),
                      ("taken", s["taken"]), ("confirmed", s["counted"]),
                      ("confirm %", "%.1f%%" % s["confirm_rate"])))
    when = ("<span class=%s>%s</span>" % ("warn" if stale else "dim", _age_words(age))
            if age is not None else "<span class=dim>never</span>")
    return ("<h1>advisor v7</h1><div class=dim>%s</div>"
            "<div class=cards>%s</div>"
            "<div class=dim style='margin:2px 0 0'>state above: <b>%s</b>, recorded %s "
            "&middot; totals below: every one of the %s campaigns ever recorded into this "
            "run dir, not just the live one</div>"
            "<div class=cards>%s</div>"
            % (_esc(run_dir), state, _camp_label(s["campaign"]), when,
               s["campaigns"], totals))


def _statcards(items):
    return ("<div class=cards>%s</div>"
            % "".join("<div class=card><div class='v %s'>%s</div><div class=k>%s</div></div>"
                      % (cls, v, _esc(k)) for k, v, cls in items))


def render_actions(con, q):
    """What the advisor tried and whether the game accepted it. The decision-by-decision
    log lives in its own tab -- mixing the forensic view in here buried these totals."""
    tot = con.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN counted THEN 1 ELSE 0 END) ok,"
        " SUM(CASE WHEN refusal IS 'awaiting_execution' THEN 1 ELSE 0 END) waiting"
        " FROM action_taken").fetchone()
    n, ok = (tot["n"] or 0), (tot["ok"] or 0)
    settled = n - (tot["waiting"] or 0)
    rate = (100.0 * ok / settled) if settled else 0.0
    rcls = "ok" if rate >= 80 else ("warn" if rate >= 40 else "bad")
    types = by_action_type(con)
    cards = _statcards([
        ("decisions acted on", "%d" % n, ""),
        ("confirmed by the game", "%d" % ok, ""),
        ("confirm rate", "%.0f%%" % rate, rcls),
        ("awaiting execution", "%d" % (tot["waiting"] or 0),
         "warn" if (tot["waiting"] or 0) else "dim"),
        ("action types seen", "%d" % len(types), ""),
    ])
    rows = []
    for r in types:
        pct = (100.0 * (r["ok"] or 0) / r["n"]) if r["n"] else 0
        cls = "ok" if pct >= 80 else ("warn" if pct >= 40 else "bad")
        rows.append("<tr><td>%s</td><td>%d</td><td class=%s>%d</td>"
                    "<td class=barcell><span class='bar2 %s' style='width:%dpx'></span>"
                    "<span class=blabel>%.0f%%</span></td>"
                    "<td class=dim style='white-space:normal'>%s</td></tr>"
                    % (_esc(r["action_type"]), r["n"], cls, r["ok"] or 0,
                       cls, max(2, int(pct * 1.2)), pct, _esc(r["refusals"])))
    per_type = ("<h2>confirm rate by action type</h2>"
                "<div class=legend>how often the game actually accepted each action the "
                "advisor picked. A low rate is a plumbing problem, not a policy one.</div>"
                "<div class=scroll><table>"
                "<tr><th>action<th>tried<th>confirmed<th>rate<th>refusals seen</tr>%s</table></div>"
                % ("".join(rows) or "<tr><td colspan=5 class=dim>nothing recorded yet</tr>"))
    # the run dir is fixed and reused across sessions, so this table is every session
    # ever recorded into it -- saying "this run" made retired eras look like live strategies
    span = con.execute(
        "SELECT COUNT(DISTINCT campaign_id), MIN(turn), MAX(turn) FROM decision_points"
    ).fetchone()
    prov = _policy_tally_html(
        "who is making the picks: every session recorded in this run dir (%s campaigns)"
        % (span[0] if span else "?"),
        dict(con.execute("SELECT policy, COUNT(*) FROM action_taken"
                         " WHERE refusal IS NOT 'awaiting_execution' GROUP BY policy")))
    return cards + per_type + prov


def render_decisions(con, q):
    seq = []
    seq_total = sequence_total(con)
    try:
        seq_offset = int(q.get("seq", ["0"])[0] or 0)
    except (TypeError, ValueError):
        seq_offset = 0
    seq_offset = max(0, min(seq_offset, max(0, seq_total - 1)))
    _rows = sequence(con, SEQ_PAGE, seq_offset)
    _dids = [r["decision_id"] for r in _rows]
    _rho = _rho_for_decisions(con, _dids)
    _ngnn = _gnn_counts(con, _dids)
    for r in _rows:
        r["n_gnn"] = _ngnn.get(r["decision_id"], 0)
        if r["action_type"] is None:
            mark, cls = "-", "dim"
        elif r["refusal"] == "awaiting_execution":
            mark, cls = "...", "warn"
        elif r["counted"]:
            mark, cls = "OK", "ok"
        else:
            mark, cls = "FAIL", "bad"
        pol = _esc(r["policy"])
        if r["policy"] and str(r["policy"]).endswith("_random_fallback"):
            pol = "<span class=warn>%s</span>" % pol
        gi = r.get("gnn_impact")
        # Both groups are bright. Dimming whichever model did not decide would say one of
        # them is the pipeline and the other an add-on; neither is. The other model's
        # opinion of the action taken is exactly what this table is for.
        cat_p = _pctile(r.get("rank"), r.get("n_offers"))
        gnn_p = _pctile(r.get("gnn_rank"), r.get("n_gnn"))
        delta = ("%+0.1f" % (gnn_p - cat_p)) if (cat_p is not None
                                                 and gnn_p is not None) else "-"
        # Whole-decision agreement, against delta's single-action agreement. The two
        # answer different questions and can disagree: the models can rank the chosen
        # action alike (delta ~ 0) while ordering everything else differently (rho low).
        rv = _rho.get(r["decision_id"])
        rho_cell = ("<span class=%s>%+0.2f</span>"
                    % ("ok" if rv >= 0.5 else ("bad" if rv < 0.0 else "warn"), rv)
                    if rv is not None else "<span class=dim>-</span>")
        seq.append("<tr><td><a href='/d/%d'>#%d</a></td><td>%s</td><td>%s</td>"
                   "<td class=gsep>%s:%s</td><td>%s</td><td>%s</td>"
                   "<td class='%s gsep'>%s</td><td class=dim style='white-space:normal'>%s</td>"
                   "<td class='num gsep'>%s</td><td class=num>%s</td><td class=num>%s</td>"
                   "<td class=num>%s</td>"
                   "<td class='num gsep'>%s</td><td class=num>%s</td>"
                   "<td class='num gsep'>%s</td><td class=num>%s</td>"
                   "<td class=gsep>%s</td></tr>"
                   % (r["decision_id"], r["decision_id"], _esc(r["turn"]), r["n_offers"],
                      _esc(r["context_kind"]), _esc(str(r["context_id"])[:26]),
                      _esc(r["action_type"]), _esc(str(r["action_key"])[:38]), cls, mark,
                      _esc(r["refusal"]),
                      _num(r.get("exploit")), _num(r.get("pct_global")),
                      (_num(r.get("pct_local")) if r.get("pct_local") is not None
                       else "<span class=dim>n/a</span>"),
                      _esc(r.get("rank") if r.get("rank") is not None else "-"),
                      ("%+0.4f" % float(gi)) if gi is not None else "-",
                      _esc(r.get("gnn_rank") if r.get("gnn_rank") is not None else "-"),
                      delta, rho_cell, pol))
    _first = seq_offset + 1 if seq else 0
    _last = seq_offset + len(seq)
    _prev = max(0, seq_offset - SEQ_PAGE)
    _next = seq_offset + SEQ_PAGE
    _pager = ("<div class=dim style='margin:6px 0'>%d-%d of %d &nbsp; "
              "<a class='btn pg' href='#' data-seq=%d>newer</a> "
              "<a class='btn pg' href='#' data-seq=%d>older</a> "
              "<a class='btn pg' href='#' data-seq=0>newest</a></div>"
              % (_first, _last, seq_total, _prev,
                 _next if _next < seq_total else seq_offset))
    if not seq:
        seq = ["<tr><td colspan=17 class=dim>no decisions recorded yet</tr>"]
    seqtbl = ("<h2>every decision, newest first</h2>"
              "<div class=legend>one row per decision point, showing what <b>both</b> models "
              "made of the action that was actually taken. Both score every offer on every "
              "decision, so each model's <b>rank</b> is that action's place in its own "
              "ranking of the same set. <b>&Delta;pct</b> puts each rank on a percentile so "
              "decisions with different offer counts still compare, and reports gnn minus "
              "catboost: <b>+</b> means the gnn rated the chosen action higher than catboost "
              "did, <b>-</b> means lower, near zero means they agreed about it. "
              "<b>&rho;</b> is the Spearman rank correlation between the two models over "
              "every offer both ranked on that decision &mdash; whole-ordering agreement, "
              "where &Delta;pct is agreement about one action. They can diverge: the models "
              "can place the chosen action alike and still order the rest differently. "
              "Older rows show <b>-</b> in the gnn columns because those decisions predate "
              "the gnn having any weights at all &mdash; not because it scores less than "
              "catboost. Both score the same offer set on every decision. "
              "<b>ruleset</b> and <b>random</b> picks rank on no score at all &mdash; the "
              "rule that fired is named in <b>picked by</b>, and "
              "<span class=warn>*_random_fallback</span> means that strategy was drawn but "
              "could not pick. Click a <b>#</b> for the full ranking behind a decision.</div>"
              + _pager +
              "<div class=scroll><table>"
              "<tr><th colspan=3>decision<th class=grp colspan=3>what it chose"
              "<th class=grp colspan=2>outcome"
              "<th class=grp colspan=4 title='E1-E2 impact percentiles and the rank of the "
              "taken action; computed for every offer on every decision'>catboost"
              "<th class=grp colspan=2 title='twin-head Q minus V and the rank of the taken "
              "action; scored for every offer on every decision, the same set catboost "
              "scores'>gnn"
              "<th class=grp colspan=2 title='&Delta;pct compares the two models on the "
              "action taken. rho compares their whole orderings of that decision'>agree"
              "<th class=grp>picked by</tr>"
              "<tr><th>#<th>turn<th>offers<th class=gsep>entity<th>action<th>key"
              "<th class=gsep>result<th>refusal"
              "<th class='gsep num'>exploit<th class=num>global<th class=num>local"
              "<th class=num>rank"
              "<th class='gsep num'>Q&minus;V<th class=num>rank"
              "<th class='gsep num'>&Delta;pct"
              "<th class=num title='Spearman rank correlation between the two models over "
              "the offers both ranked on this decision. +1 identical order, 0 unrelated, "
              "-1 reversed. Needs 3+ offers in common, otherwise -'>&rho;"
              "<th class=gsep>strategy</tr>"
              "%s</table></div>" % "".join(seq)) + _pager
    return seqtbl


REWARD_CAMPAIGNS = 10


def render_reward(con, limit=REWARD_CAMPAIGNS):
    """Turn-by-turn state, one lane per campaign.

    This used to be a single table of every target row in the run dir ordered by turn
    number. With 496 campaigns that put 496 unrelated rows under "turn 1" and read as
    one flatlined series. A reward input is only a series within a campaign, so the
    campaign has to be the lane.
    """
    n_camps = con.execute("SELECT COUNT(DISTINCT campaign_id) FROM target_rows").fetchone()[0]
    if not n_camps:
        return "<h2>reward inputs per turn</h2><p class=muted>no turns recorded yet</p>"
    recent = [(r[0], r[1]) for r in con.execute(
        "SELECT campaign_id, MAX(ts) mts FROM target_rows GROUP BY campaign_id"
        " ORDER BY mts DESC LIMIT ?", (limit,))]
    vas, tot = con.execute("SELECT SUM(vassals>0), COUNT(*) FROM target_rows").fetchone()
    lanes = []
    for camp, ts in recent:
        series = turns(con, camp)
        trows = "".join("<tr><td class=num>%s</td><td class=num>%s</td><td class=num>%s</td>"
                        "<td class=num>%s</td><td class=num>%s</td><td class=num>%s</td></tr>"
                        % (_int(t["turn"]), _int(t["income"]), _int(t["settlements"]),
                           _int(t["allies"]), _int(t["vassals"]), _int(t["power_rank"]))
                        for t in series)
        lanes.append("<div class=lanehead>%s<span class=dim> &nbsp;%d turns &middot; %s"
                     "</span></div>"
                     "<div class=scroll><table>"
                     "<tr><th class=num>turn<th class=num>income<th class=num>settlements"
                     "<th class=num>allies<th class=num>vassals"
                     "<th class=num title='lower is better'>power rank</tr>%s</table></div>"
                     % (_camp_label(camp), len(series),
                        _age_words(time.time() - ts if ts else None),
                        trows or "<tr><td class=dim colspan=6>no turns recorded</td></tr>"))
    return ("<h2>reward inputs per turn <span class=dim>(%d most recent campaigns of %d)"
            "</span></h2>"
            "<div class=legend>One lane per campaign, newest first. These values are only a "
            "series <b>within</b> a campaign &mdash; turn numbers restart each time, so the "
            "run dir's %d campaigns cannot share a turn axis. <b>power rank</b> counts "
            "downwards, so a falling number is improving. <b>allies</b> and <b>vassals</b> "
            "are 0/1 in practice: %d of %d recorded turns across the whole run dir have any "
            "vassal at all.</div>%s"
            % (len(recent), n_camps, n_camps, vas or 0, tot or 0, "".join(lanes)))


AGREE_LOOKBACK = 600


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _gnn_counts(con, dids):
    """decision_id -> how many of its offers carry a gnn rank.

    Normally exactly n_offers: policy.choose() scores both models over the same `ranked`
    list on every decision, so both ranks are 1..N over the same N (measured: equal on
    142 of 146 decisions since the gnn had weights). Counted rather than assumed only so
    a decision whose gnn pass partly failed is not given the wrong denominator -- it is
    NOT the gnn ranking a smaller set by design.
    """
    if not dids:
        return {}
    return dict(con.execute(
        "SELECT decision_id, COUNT(*) FROM action_offers WHERE decision_id IN (%s)"
        " AND gnn_rank IS NOT NULL GROUP BY decision_id" % ",".join("?" * len(dids)),
        tuple(dids)))


def _rho_for_decisions(con, dids):
    """decision_id -> Spearman rho between the two models' rankings of that decision.

    Over the offers both models ranked -- which is every offer on the decision. I wrote
    "the gnn ranks only the ones it scored, so the two ranks are out of different
    denominators" here and it was false, copied off a stale UI string rather than read off
    policy.py. policy.choose() scores BOTH models on the same `ranked` list on every
    decision, before eligibility and before the draw, so gnn_rank and rank are 1..N over
    the same N. Measured: n_gnn == n_offers on 142 of 146 decisions since the gnn had
    weights. The NULL gnn_rank rows are decisions from before it was ever trained.

    _spearman is the metric the agreement panel already reports -- one metric for one
    question, not a second.

    Scoped to the decision ids on the page rather than a lookback window: the decision log
    pages backwards through the whole run, so a fixed recent window would leave the column
    empty on every page but the first. len(pairs) < 3 returns None from _spearman, which
    renders as "-" -- two offers in common admit only rho = +1 or -1 and would read as
    perfect agreement or perfect inversion on no evidence.
    """
    out = {}
    if not dids:
        return out
    per = {}
    marks = ",".join("?" * len(dids))
    for did, crank, grank in con.execute(
            "SELECT decision_id, rank, gnn_rank FROM action_offers"
            " WHERE decision_id IN (%s) AND rank IS NOT NULL AND gnn_rank IS NOT NULL"
            % marks, tuple(dids)):
        per.setdefault(did, []).append((crank, grank))
    for did, pairs in per.items():
        out[did] = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
    return out


def _agreement_window(con):
    """Both models' rankings over the most recent AGREE_LOOKBACK decisions.

    Bounded by decision_id on purpose: an unbounded "WHERE gnn_rank IS NOT NULL" would
    walk all 7M+ offer rows on every page load. The bound keeps it on ix_offer_dp.
    """
    if "gnn_rank" not in _optional_cols(con, "action_offers", ("gnn_rank",)):
        return None
    top = con.execute("SELECT MAX(decision_id) FROM decision_points").fetchone()[0]
    if not top:
        return None
    lo = top - AGREE_LOOKBACK
    per = {}
    for did, crank, grank in con.execute(
            "SELECT decision_id, rank, gnn_rank FROM action_offers"
            " WHERE decision_id > ? AND rank IS NOT NULL AND gnn_rank IS NOT NULL", (lo,)):
        per.setdefault(did, []).append((crank, grank))
    # Same shape as sequence(): the correlated MIN(offer_id) subquery here cost seconds
    # because it ran once per taken row against a view. One pass over the window's offers,
    # matched in python, is the same answer -- first offer_id wins, as MIN() did.
    first_offer = {}
    for o in con.execute(
            "SELECT decision_id, context_kind, context_id, action_type, action_key,"
            " rank, gnn_rank FROM action_offers WHERE decision_id > ? ORDER BY offer_id",
            (lo,)):
        key = (o[0], o[1], str(o[2]), o[3], str(o[4]))
        if key not in first_offer:
            first_offer[key] = (o[5], o[6])
    taken = []
    for did, pol, n_offers, ck, cid, at, ak in con.execute(
            "SELECT t.decision_id, t.policy, d.n_offers, t.context_kind, t.context_id,"
            " t.action_type, t.action_key"
            " FROM action_taken t JOIN decision_points d ON d.decision_id=t.decision_id"
            " WHERE t.decision_id > ?", (lo,)):
        crank, grank = first_offer.get((did, ck, str(cid), at, str(ak)), (None, None))
        taken.append((did, pol, n_offers, crank, grank))
    return {"lo": lo, "top": top, "per": per, "taken": taken}


def render_agreement(con):
    w = _agreement_window(con)
    per = (w or {}).get("per") or {}
    usable = {d: p for d, p in per.items() if len(p) >= 3}
    if not usable:
        return ("<h2>do the two models agree?</h2>"
                "<div class=legend>Nothing to compare yet. Both models score every offer on "
                "every decision, so this fills as soon as the gnn has weights. A decision "
                "carries no gnn ranking only if it was recorded before the gnn was ever "
                "trained, or if a scoring pass failed.</div>")
    rhos, tops, sizes = [], [], []
    for pairs in usable.values():
        cat = [p[0] for p in pairs]
        gnn = [p[1] for p in pairs]
        r = _spearman(cat, gnn)
        if r is not None:
            rhos.append(r)
        sizes.append(len(pairs))
        tops.append(min(range(len(pairs)), key=lambda i: cat[i])
                    == min(range(len(pairs)), key=lambda i: gnn[i]))
    agree_pct = 100.0 * sum(1 for t in tops if t) / len(tops)
    summary = [("decisions compared", "%d" % len(usable)),
               ("offers both models ranked (median)", "%.0f" % (_median(sizes) or 0)),
               ("Spearman rho, median", "%+0.3f" % (_median(rhos) or 0.0)),
               ("Spearman rho, mean", "%+0.3f" % ((sum(rhos) / len(rhos)) if rhos else 0.0)),
               ("both picked the same best action", "%.0f%% (%d of %d)"
                % (agree_pct, sum(1 for t in tops if t), len(tops)))]
    stbl = ("<div class=scroll><table><tr><th>measure<th class=num>value</tr>%s</table></div>"
            % "".join("<tr><td>%s</td><td class=num>%s</td></tr>" % (_esc(k), _esc(v))
                      for k, v in summary))

    by = {}
    for did, pol, n_offers, crank, grank in (w.get("taken") or []):
        b = by.setdefault(str(pol), {"n": 0, "cat": [], "catp": [], "gnn": [], "gnnp": [],
                                     "delta": []})
        b["n"] += 1
        n_gnn = len(per.get(did, []))
        cp = _pctile(crank, n_offers)
        gp = _pctile(grank, n_gnn)
        if crank is not None:
            b["cat"].append(crank)
        if cp is not None:
            b["catp"].append(cp)
        if grank is not None:
            b["gnn"].append(grank)
        if gp is not None:
            b["gnnp"].append(gp)
        if cp is not None and gp is not None:
            b["delta"].append(gp - cp)
    dash = "<span class=dim>-</span>"

    def cell(v, fmt):
        return (fmt % v) if v is not None else dash
    rows = []
    for pol, b in sorted(by.items(), key=lambda x: -x[1]["n"]):
        rows.append("<tr><td>%s</td><td class=num>%d</td>"
                    "<td class='num gsep'>%s</td><td class=num>%s</td>"
                    "<td class='num gsep'>%s</td><td class=num>%s</td>"
                    "<td class='num gsep'>%s</td></tr>"
                    % (_esc(pol[:34]), b["n"],
                       cell(_median(b["cat"]), "%.0f"), cell(_median(b["catp"]), "%.1f%%"),
                       cell(_median(b["gnn"]), "%.0f"), cell(_median(b["gnnp"]), "%.1f%%"),
                       cell(_median(b["delta"]), "%+0.1f")))
    ptbl = ("<div class=scroll><table>"
            "<tr><th colspan=2>&nbsp;<th class=grp colspan=2>catboost"
            "<th class=grp colspan=2>gnn<th class=grp>agree</tr>"
            "<tr><th>picked by<th class=num>decisions"
            "<th class='gsep num'>rank<th class=num>pct"
            "<th class='gsep num'>rank<th class=num>pct"
            "<th class='gsep num'>&Delta;pct</tr>%s</table></div>" % "".join(rows))
    # If the gnn only ever scored the decisions it won, every row here is drawn from its
    # own picks and the per-strategy table cannot say anything about catboost's. Saying so
    # loudly beats letting the page read as a fair comparison when it is not one.
    shadow = sum(1 for _d, pol, _n, _cr, gr in (w.get("taken") or [])
                 if gr is not None and str(pol).split("(")[0] != "gnn")
    warn = ("" if shadow else
            "<div class=legend style='border-left:3px solid #b58900'><b>Every decision below "
            "is one the gnn was drawn to win.</b> It is not yet scoring decisions it did not "
            "win, so this sample can only show what catboost made of the gnn's picks &mdash; "
            "never what the gnn made of catboost's. The per-strategy rows below are blank in "
            "the gnn columns for exactly that reason, and &rho; here is measured only on the "
            "gnn's own decisions.</div>")
    return ("<h2>do the two models agree?</h2>"
            "<div class=legend>Rank correlation between catboost and the gnn over the last "
            "%d decisions (#%d onward), counting only offers <b>both</b> models ranked. "
            "&rho; near 0 means they are ranking on effectively unrelated criteria; near 1 "
            "means they are redundant. A decision the gnn never scored cannot appear here "
            "at all.</div>%s%s"
            "<h2>where each model ranked the action that was taken</h2>"
            "<div class=legend>Grouped by the strategy that actually chose. <b>rank</b> is "
            "the taken action's position in that model's ranking of every offer, and "
            "<b>pct</b> is the same as a percentile so decisions of different sizes compare. "
            "The row for a strategy shows what the <b>other</b> model thought of its pick "
            "&mdash; that is the point of the table. <b>&Delta;pct</b> is gnn minus "
            "catboost.</div>%s"
            % (AGREE_LOOKBACK, (w or {}).get("lo", 0) + 1, warn, stbl, ptbl))


PANELS = (
    ("live", "live", lambda con, run, q: render_live(run)),
    ("overview", "overview",
     lambda con, run, q: render_endings() + render_campaigns(con)),
    ("starts", "starts", lambda con, run, q: render_starts()),
    ("matrix", "action x faction", lambda con, run, q: render_faction_matrix()),
    ("interrupts", "blocking menus", lambda con, run, q: render_interrupts()),
    ("diplomacy", "diplomacy", lambda con, run, q: render_diplomacy(run)),
    ("timing", "timing", lambda con, run, q: render_timing(run)),
    ("actions", "actions", lambda con, run, q: render_actions(con, q)),
    ("decisions", "decision log", lambda con, run, q: render_decisions(con, q)),
    # the whole panel IS the calculation -- there is no cheap half to paint first, so the
    # tab paints its heading immediately and the body arrives behind it
    ("agreement", "model agreement", lambda con, run, q:
        "<div class=lazy data-src='/panel/agreement_body'>"
        "<h2>do the two models agree?</h2>"
        "<div class=legend>comparing both rankings over the recent window&hellip;</div>"
        "</div>"),
    ("timeline", "timeline", lambda con, run, q: render_timeline(con)),
    ("reward", "reward", lambda con, run, q: render_reward(con)),
    ("modelmetrics", "model metrics", lambda con, run, q: render_model_metrics(con, run, q)),
    ("models", "models", lambda con, run, q: render_models()),
    ("training", "training", lambda con, run, q: render_training()),
    ("infra", "infrastructure", lambda con, run, q: render_infra(run)),
)

# How often a panel re-fetches itself, in seconds. One blanket 10s reload for every panel
# was wrong in both directions: `live` is the run monitor and wants to be current, while
# `matrix` aggregates the whole run history and does not meaningfully change inside a
# minute -- reloading it every 10s spent query time to redraw the same pixels, and fought
# the lazy sections, which take longer to compute than the interval that discarded them.
# Cadence belongs to the panel, next to what the panel costs.
REFRESH = {
    "live": 5,            # the reason a refresh exists at all
    "decisions": 20,      # newest-first log; new rows land every few seconds
    "infra": 30, "training": 30, "models": 30,
    "overview": 60, "starts": 60, "actions": 60, "interrupts": 60,
    "diplomacy": 60, "timing": 60, "reward": 60, "timeline": 60, "matrix": 120,
    # these are mostly a lazy placeholder; the section inside has its own TTL
    "agreement": 120, "modelmetrics": 120,
}
REFRESH_DEFAULT = 60

PANEL_MAP = {s: f for s, _t, f in PANELS}
# Sections fetched by a panel AFTER it is on screen (see .lazy in _TABS_JS). They are
# served by the same /panel/ route but are deliberately NOT in PANELS, so they get no tab.
LAZY_PANELS = {
    "mm_forcing": lambda con, run, q: render_mm_forcing(con, run, q),
    "agreement_body": lambda con, run, q: render_agreement(con),
}
PANEL_MAP.update(LAZY_PANELS)


def render_index(con, run_dir):
    nav = "".join("<button class=tab data-t='%s'>%s</button>" % (s, _esc(t))
                  for s, t, _f in PANELS)
    bodies = "".join("<div class=panel id='p-%s' hidden><p class=dim>loading&hellip;</p></div>" % s
                     for s, _t, _f in PANELS)
    return _page("<div id=head>%s</div><div class=tabs>%s</div>%s%s"
                 % (render_head(con, run_dir), nav, bodies,
                    _TABS_JS % (json.dumps([s for s, _t, _f in PANELS]),
                                json.dumps(REFRESH), REFRESH_DEFAULT)))


def render_campaigns(con):
    """One row per campaign.

    This used to be two tables on the same tab -- "by legendary lord" and "run history"
    -- both keyed on campaign_id, both 513 rows, sharing turns/decisions/confirm. The
    first was not per lord at all: 513 campaigns cover 101 lords, so Nagarythe appeared
    31 times. render_starts is the per-lord view; this is the per-campaign one.
    """
    runs = run_history(con)
    if not runs:
        return "<h2>campaigns</h2><div class=dim>no campaigns recorded yet</div>"
    runs = sorted(runs, key=lambda r: r["first_ts"], reverse=True)
    mx = max(r["turns"] for r in runs) or 1
    mxd = max(r["decisions"] for r in runs) or 1
    multi_run = len({r["run"] for r in runs}) > 1
    out = []
    for i, r in enumerate(runs, 1):
        w = max(2, int(100.0 * r["turns"] / mx))
        wd = max(2, int(100.0 * r["decisions"] / mxd))
        cls = "bad" if r["turns"] <= 1 else ("warn" if r["turns"] < 0.4 * mx else "ok")
        noact = ("<td class='warn num gsep'>%d</td>" % r["no_action"] if r["no_action"]
                 else "<td class='dim num gsep'>0</td>")
        out.append(
            "<tr><td class=dim>%d</td><td title='%s'>%s</td>"
            "<td class=barcell><div class='bar2 %s' style='width:%d%%'></div>"
            "<span class=blabel>%d</span></td>"
            "<td class=barcell><div class='bar2 dimbar' style='width:%d%%'></div>"
            "<span class=blabel>%d</span></td>"
            "%s<td class=num>%d</td><td class=num>%d</td>%s"
            "<td class='dim num gsep'>%s</td>"
            "<td class='num gsep'>%s</td><td class=num>%s</td><td class=num>%s</td>"
            "<td class='num gsep'>%s</td><td class=num>%s</td><td class=num>%s</td>%s</tr>"
            % (i, _esc(r["campaign"]), _camp_label(r["campaign"]),
               cls, w, r["turns"], wd, r["decisions"],
               noact, r["taken"], r["counted"], _pct_cell(r["counted"], r["taken"]),
               _esc("%.1f" % r["span_min"]),
               _int(r["best_settlements"]), _int(r["best_power_rank"]),
               _int(r["best_lord_level"]),
               _int(r["last_settlements"]), _int(r["last_power_rank"]),
               _int(r["last_income"]),
               ("<td class=dim>%s</td>" % _esc(r["run"])) if multi_run else ""))
    return ("<h2>every campaign &mdash; how far each one got</h2>"
            "<div class=legend>Newest first, one row per campaign (not per lord &mdash; "
            "%d campaigns across %d lords; the per-lord view is the <b>starts</b> tab). "
            "<b>no action</b> counts decision points that produced no attempt at all; those "
            "are excluded from <b>attempted</b> so a campaign that tried nothing reads "
            "&mdash; rather than 0%%. <b>span</b> is first-to-last decision, which is less "
            "than campaign wall clock &mdash; game load and postmortem fall outside it. "
            "<b>peak</b> is the best the campaign ever reached, <b>final</b> where it "
            "stopped; power rank counts downwards, so lower is better. The turn bar is "
            "scaled to the longest campaign here (%d turns), not to the run's turn cap."
            "</div>"
            "<div class=scroll><table>"
            "<tr><th colspan=4>campaign<th class=grp colspan=4>decisions"
            "<th class=grp>&nbsp;<th class=grp colspan=3>peak"
            "<th class=grp colspan=3>final%s</tr>"
            "<tr><th>#<th>faction<th>turns<th>decisions"
            "<th class=gsep title='decision points that produced no action row at all'>"
            "no action<th class=num>attempted<th class=num>confirmed<th class=num>rate"
            "<th class='gsep dim num' title='first to last decision, not campaign wall clock'>"
            "span min"
            "<th class='gsep num'>settlements"
            "<th class=num title='lower is better'>power rank<th class=num>lord level"
            "<th class='gsep num'>settlements"
            "<th class=num title='lower is better'>power rank<th class=num>income%s</tr>"
            "%s</table></div>"
            % (len(runs), len({r["faction"] for r in runs}), mx,
               "<th class=grp>&nbsp;" if multi_run else "",
               "<th>run dir" if multi_run else "", "".join(out)))


_TABS_JS = """<script>
(function(){
  var slugs=%s, refresh=%s, key='v7tab', cur=sessionStorage.getItem(key), pq={};
  if(slugs.indexOf(cur)<0)cur=slugs[0];
  function paint(){
    document.querySelectorAll('.panel').forEach(function(p){p.hidden=(p.id!=='p-'+cur);});
    document.querySelectorAll('.tab').forEach(function(b){b.classList.toggle('on',b.dataset.t===cur);});
  }
  function swap(el,t){
    el.innerHTML=t;
    el.querySelectorAll('script').forEach(function(s){
      var n=document.createElement('script');n.textContent=s.textContent;
      s.parentNode.replaceChild(n,s);});
    lazy(el);
  }
  // A section marked .lazy is fetched AFTER its panel is on screen. Everything a panel
  // can answer cheaply paints immediately; only the expensive part waits, and it waits
  // in its own box instead of holding the whole tab behind it.
  //
  // The cache survives panel reloads. A lazy section takes 4-7s to compute, so any
  // reload during that window used to leave it blank -- under the old blanket 10s
  // interval it was blank about four seconds in every ten and looked broken. Per-panel
  // cadence (see schedule) mostly removes that, but a reload can still land mid-fetch, so
  // the last good HTML repaints instantly and a refetch only runs past the TTL, swapping
  // in when it arrives.
  // innerHTML, not outerHTML: the .lazy div has to survive as the target for the next
  // refresh.
  var lzHtml={}, lzAt={}, lzBusy={}, LZ_TTL=60000;
  function lazy(el){
    el.querySelectorAll('.lazy[data-src]').forEach(function(d){
      var src=d.dataset.src, now=Date.now();
      if(lzHtml[src])d.innerHTML=lzHtml[src];
      if(lzBusy[src])return;
      if(lzHtml[src]&&(now-lzAt[src])<LZ_TTL)return;
      lzBusy[src]=1;
      fetch(src,{cache:'no-store'})
        .then(function(r){return r.text()})
        .then(function(t){
          lzHtml[src]=t;lzAt[src]=Date.now();lzBusy[src]=0;
          var live=document.querySelector('.lazy[data-src="'+src+'"]');
          if(live)live.innerHTML=t;
        })
        .catch(function(e){
          lzBusy[src]=0;
          if(!lzHtml[src]){
            var live=document.querySelector('.lazy[data-src="'+src+'"]');
            if(live)live.innerHTML='<p class=bad>section fetch failed: '+e+'</p>';
          }
        });
    });
  }
  function editing(el){
    var a=document.activeElement;
    return a&&el.contains(a)&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
  }
  // Each panel schedules its OWN next reload, after the previous one has landed, at the
  // cadence declared for it. A shared setInterval could not do either: it fired on a wall
  // clock regardless of whether the last fetch had returned, and used one rate for panels
  // whose cost and volatility differ by two orders of magnitude. Only the visible panel is
  // ever scheduled, and switching tabs cancels the old timer.
  var timer=null;
  function schedule(name){
    if(name!==cur)return;
    if(timer)clearTimeout(timer);
    timer=setTimeout(function(){load(cur);},(refresh[name]||%d)*1000);
  }
  function load(name){
    var el=document.getElementById('p-'+name);
    if(timer){clearTimeout(timer);timer=null;}
    if(editing(el)){schedule(name);return;}
    fetch('/panel/'+name+(pq[name]?'?'+pq[name]:''),{cache:'no-store'})
      .then(function(r){return r.text()})
      .then(function(t){swap(el,t);schedule(name);})
      .catch(function(e){el.innerHTML='<p class=bad>panel '+name+' fetch failed: '+e+'</p>';
        schedule(name);});
    fetch('/panel/head',{cache:'no-store'})
      .then(function(r){return r.text()})
      .then(function(t){document.getElementById('head').innerHTML=t;})
      .catch(function(e){document.getElementById('head').innerHTML=
        '<p class=bad>head fetch failed: '+e+'</p>';});
  }
  document.querySelectorAll('.tab').forEach(function(b){
    b.addEventListener('click',function(){
      cur=b.dataset.t;sessionStorage.setItem(key,cur);paint();load(cur);});
  });
  document.addEventListener('click',function(e){
    var a=e.target.closest('a.pg');if(!a)return;e.preventDefault();
    pq[cur]='seq='+a.dataset.seq;load(cur);
  });
  paint();load(cur);
})();
</script>"""


def render_timeline(con):
    _rows, lanes = timeline(con)
    if not lanes:
        return ""
    scale = 1 / 40.0
    phases = (("collect_ms", "p1", "recorder reading the game"),
              ("queue_ms", "p2", "request round trip"),
              ("score_ms", "p3", "featurize + rank (with gnn drawn: graph build + forward)"),
              ("verify_ms", "p4", "execute + confirm"))
    out = []
    # newest lane first, and never split a campaign's turn across lanes
    order = sorted(lanes, key=lambda k: -max(r["decision_id"] for r in lanes[k]))
    for camp, turn in order:
        items = lanes[(camp, turn)]
        ok = sum(1 for r in items if r["counted"])
        lines = []
        for r in items:
            if r["action_type"] is None:
                cls, tip = "dim", "no pick recorded"
            elif r["refusal"] == "awaiting_execution":
                cls, tip = "warn", "awaiting execution"
            elif r["counted"]:
                cls, tip = "ok", "confirmed"
            else:
                cls, tip = "bad", (r["refusal"] or "failed")
            segs = "".join(
                "<span class='seg %s' style='width:%dpx' title='%s: %dms'></span>"
                % (css, max(2, int((r[key] or 0) * scale)), lbl, r[key] or 0)
                for key, css, lbl in phases if r.get(key))
            total = ("%dms" % r["total_ms"]) if r["total_ms"] else "&mdash;"
            gap = r.get("gap")
            unacc = (round(gap - (r["total_ms"] or 0) / 1000.0, 1) if gap else None)
            gcls = "bad" if (gap and gap >= 60) else ("warn" if (gap and gap >= 15) else "dim")
            lines.append(
                "<tr><td><a href='/d/%d'>#%d</a></td><td class=%s>%s</td>"
                "<td>%s</td><td class=dim>%s</td><td>%s</td>"
                "<td class=dim>%s</td><td class=dim>%s</td><td class=dim>%s</td>"
                "<td class=dim>%s</td><td>%s</td><td class=%s>%s</td><td class=dim>%s</td></tr>"
                % (r["decision_id"], r["decision_id"], cls, _esc(r["action_type"] or "-"),
                   _esc(str(r["action_key"] or "")[:34]), _esc(tip), segs,
                   _esc(r["collect_ms"]), _esc(r["queue_ms"]), _esc(r["score_ms"]),
                   _esc(r["verify_ms"]), total, gcls,
                   ("%.1fs" % gap) if gap is not None else "&mdash;",
                   ("%.1fs" % unacc) if unacc is not None else "&mdash;"))
        span = [r["gap"] for r in items if r.get("gap")]
        wall = (" &nbsp;%.0fs in-turn" % sum(span)) if span else ""
        out.append("<div class=lanehead>%s <span class=dim>&middot;</span> turn %s"
                   "<span class=dim> &nbsp;%d/%d confirmed%s</span></div>"
                   "<div class=scroll><table><tr><th>#<th>action<th>key<th>result"
                   "<th>phases<th>collect<th>queue<th>score<th>verify<th>total"
                   "<th title='wall clock since the previous action in this campaign'>gap"
                   "<th title='gap minus the measured phases -- time nothing accounts for'>"
                   "unaccounted</tr>%s</table></div>"
                   % (_camp_label(camp), _int(turn), ok, len(items), wall, "".join(lines)))
    legend = " ".join("<span class='seg %s'></span> %s" % (css, lbl) for _k, css, lbl in phases)
    camps = len({c for c, _t in lanes})
    return ("<h2>timeline &mdash; every action, phase by phase (ms)</h2>"
            "<div class=legend>%s<br>The last %d decisions, newest first. Turn numbers restart "
            "with every campaign, so lanes are keyed on <b>campaign and turn</b> &mdash; this "
            "window spans %d campaign(s). <b>gap</b> is left blank across a campaign boundary, "
            "where the elapsed time is game teardown and reload rather than anything this "
            "action did. <b>unaccounted</b> goes negative when a decision's own phases run "
            "longer than the interval since the previous decision &mdash; the phases overlap "
            "that window rather than fitting inside it.</div>%s"
            % (legend, len(_rows), camps, "".join(out)))


def render_decision(con, did):
    d = ranking(con, did)
    tk = d["taken"]
    head = ("<h1><a href='/'>&larr;</a> decision #%d</h1>"
            "<div class=dim>%d offers across %d entities &mdash; taken: %s</div>"
            % (did, d["n_offers"], len(d["entities"]),
               _esc("%s %s (%s)" % (tk["action_type"], tk["action_key"],
                                    "confirmed" if tk["counted"] else (tk["refusal"] or "?")))
               if tk else "nothing"))
    agr = agreement(con, did)
    if agr and agr["rho"] is not None:
        head += ("<div class=dim>both models ranked %d of these offers &mdash; Spearman "
                 "&rho; <b>%+0.3f</b>, and they %s on the best one</div>"
                 % (agr["n"], agr["rho"],
                    "agreed" if agr["same_top"] else "<b>disagreed</b>"))
    rows = []
    for o in d["offers"]:
        cls = "take" if o["taken"] else ""
        fmt = lambda v: ("%.4f" % v) if isinstance(v, float) else ("" if v is None else str(v))
        pl = o.get("pct_local")
        pl_cell = fmt(pl) if pl is not None else "<span class=dim>n/a</span>"
        gi = o.get("gnn_impact")
        rows.append("<tr class='%s'><td>%s</td><td>%s:%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td>"
                    "<td class=gsep>%s</td><td>%s</td></tr>"
                    % (cls, _esc(o["rank"]), _esc(o["context_kind"]),
                       _esc(str(o["context_id"])[:22]), _esc(o["action_type"]),
                       _esc(str(o["action_key"])[:44]),
                       fmt(o["exploit"]), fmt(o.get("pct_global")), pl_cell,
                       ("%+0.4f" % float(gi)) if gi is not None else "<span class=dim>-</span>",
                       _esc(o.get("gnn_rank") if o.get("gnn_rank") is not None else "-")))
    tbl = ("<h2>the ranking it produced over the whole faction</h2>"
           "<div class=legend>both models rank every offer on every decision, so the two "
           "rank columns are over the same set and can be read against each other directly. "
           "Neither model's rank 1 is necessarily what got picked: the gate runs before "
           "scoring now, so every row here already survived it and the top-ranked one can "
           "still lose the draw. On older "
           "decisions the gnn columns are blank &mdash; back then it only scored when it was "
           "the strategy drawn.</div>"
           "<div class=scroll><table>"
           "<tr><th colspan=4>offer<th class=grp colspan=3>catboost"
           "<th class=grp colspan=2>gnn</tr>"
           "<tr><th>rank<th>entity<th>action<th>key"
           "<th class=gsep>exploit<th>global<th>local"
           "<th class=gsep>Q&minus;V<th>rank</tr>"
           "%s</table></div>" % "".join(rows))
    ent = []
    for e in d["entities"]:
        st = json.loads(e["features"])
        ent.append("<tr><td>%s</td><td>%s</td><td class=dim>%s</td></tr>"
                   % (_esc(e["context_kind"]), _esc(e["context_id"]),
                      _esc(", ".join("%s=%s" % (k, v) for k, v in sorted(st.items()))[:400])))
    ents = ("<h2>entity state at that instant (what the features were built from)</h2>"
            "<div class=scroll><table><tr><th>context<th>id<th>raw state</tr>%s</table></div>"
            % "".join(ent))
    return _page(head + tbl + ents, "decision #%d" % did)


LOG_DIR = common.LOGS_ADVISOR
CURRENT_LOG = os.path.join(LOG_DIR, "CURRENT_SESSION_LOG.txt")
SERVICES = (("session.py", "advisor session"), ("manager.py", "recorder"), ("ui.py", "UI"))


_PS_CACHE = [0.0, ([], None)]


def _ps():
    import subprocess
    if time.time() - _PS_CACHE[0] < 5:
        return _PS_CACHE[1]
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | ForEach-Object "
           "{ '{0}|{1:yyyy-MM-dd HH:mm:ss}|{2}' -f $_.ProcessId,$_.CreationDate,$_.CommandLine }; "
           "'WH3|{0}' -f @(Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue).Count")
    procs, wh3 = [], None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("WH3|"):
                wh3 = ln.split("|", 1)[1].strip() or "0"
            elif ln.count("|") >= 2:
                pid, started, cl = ln.split("|", 2)
                procs.append((pid.strip(), started.strip(), cl.strip()))
    except Exception as e:
        procs, wh3 = [], "err: %s" % repr(e)[:60]
    _PS_CACHE[0], _PS_CACHE[1] = time.time(), (procs, wh3)
    return procs, wh3


def _age(path):
    import time
    try:
        st = os.stat(path)
    except OSError:
        return None, "-"
    return time.time() - st.st_mtime, time.strftime("%H:%M:%S", time.localtime(st.st_mtime))


def _fresh(secs, warn, bad):
    if secs is None:
        return "bad"
    return "ok" if secs < warn else ("warn" if secs < bad else "bad")


def _meta(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


_BASE_MODEL_MTIME = [0.0]


def _live_base_model():
    import base_model as BM
    try:
        mt = os.stat(BM.__file__.replace(".pyc", ".py")).st_mtime
    except OSError:
        return BM
    if mt != _BASE_MODEL_MTIME[0]:
        import importlib
        BM = importlib.reload(BM)
        _BASE_MODEL_MTIME[0] = mt
    return BM


def _session_alive():
    procs, _wh3 = _ps()
    return any("session.py" in p[2] for p in procs)


def render_infra(run_dir):
    import time
    procs, wh3 = _ps()
    running = {}
    for name, label in SERVICES:
        hit = next((p for p in procs if name in p[2]), None)
        running[name] = hit

    def pill(state, text):
        return "<span class=%s>%s</span>" % (state, _esc(text))

    rows = []
    for name, label in SERVICES:
        p = running.get(name)
        if p:
            rows.append("<tr><td>%s<td>%s<td>%s<td>%s</tr>"
                        % (_esc(label), pill("ok", "up"), _esc(p[0]), _esc(p[1])))
        else:
            rows.append("<tr><td>%s<td>%s<td class=dim>-<td class=dim>-</tr>"
                        % (_esc(label), pill("bad", "DOWN")))
    wh3n = 0
    try:
        wh3n = int(wh3)
    except (TypeError, ValueError):
        pass
    rows.append("<tr><td>Warhammer3<td>%s<td colspan=2 class=dim>%s process(es)</tr>"
                % (pill("ok" if wh3n == 1 else ("warn" if wh3n == 0 else "bad"),
                        "up" if wh3n == 1 else ("down" if wh3n == 0 else "DUPLICATE")), _esc(wh3)))
    svc = ("<h2>services</h2><div class=scroll><table>"
           "<tr><th>service<th>state<th>pid<th>started</tr>%s</table></div>" % "".join(rows))

    def ago(s):
        if s is None:
            return "never"
        if s < 90:
            return "%.0fs ago" % s
        if s < 5400:
            return "%.0fm ago" % (s / 60.0)
        return "%.1fh ago" % (s / 3600.0)

    try:
        import policy as _P
        live_mix = (_session_state() or {}).get("mix")
        dflt = ", ".join("%s=%.2f" % kv
                         for kv in sorted(_P.normalize_strategies(None).items()))
        pol_cfg = (("running mix <b>%s</b>" % _esc(_mix_str(live_mix) or live_mix))
                   if live_mix else
                   "<span class=warn>no running session &mdash; no live mix</span>")
        pol_cfg += ("<br><span class=dim>%s is only the fallback when --strategies is "
                    "omitted (policy.py DEFAULT_STRATEGIES). A run's own mix comes from "
                    "--strategies, is recorded on its trial, and drives both the action "
                    "sampler and the interrupt sampler &mdash; except that gnn has no "
                    "interrupt-side model, so gnn draws there are delegated to exploit_tree."
                    "</span>" % _esc(dflt))
    except Exception as e:
        pol_cfg = "unreadable: %s" % _esc(repr(e)[:60])
    policy_html = ("<h2>pick policy</h2><div class=muted>%s</div>" % pol_cfg)

    watch = [("session log", (open(CURRENT_LOG, encoding="utf-8-sig").read().strip()
                              if os.path.isfile(CURRENT_LOG) else ""), 90, 420)]
    arows = []
    for label, path, warn, bad in watch:
        if not path:
            arows.append("<tr><td>%s<td class=bad>missing<td class=dim>-</tr>" % _esc(label))
            continue
        s, t = _age(path)
        arows.append("<tr><td>%s<td class=%s>%s<td class=dim>%s</tr>"
                     % (_esc(label), _fresh(s, warn, bad), _esc(ago(s)), _esc(os.path.basename(path))))
    for fn, warn, bad in (("decisions_requests.jsonl", 120, 600),
                          ("decisions_responses.jsonl", 120, 600),
                          ("trace.jsonl", 180, 900),
                          ("decisions_stream.jsonl", 180, 900),
                          ("diplomacy.jsonl", 900, 7200)):
        p = os.path.join(run_dir, fn)
        s, t = _age(p)
        arows.append("<tr><td>%s<td class=%s>%s<td class=dim>%s</tr>"
                     % (_esc(fn), _fresh(s, warn, bad), _esc(ago(s)), _esc(t)))
    activity = ("<h2>activity</h2><div class=scroll><table>"
                "<tr><th>stream<th>last write<th></tr>%s</table></div>" % "".join(arows))

    tail = ""
    if os.path.isfile(CURRENT_LOG):
        lp = open(CURRENT_LOG, encoding="utf-8-sig").read().strip()
        if os.path.isfile(lp):
            try:
                lines = open(lp, encoding="utf-8", errors="replace").read().splitlines()[-14:]
                tail = ("<h2>session log tail</h2><pre class=scroll>%s</pre>"
                        % _esc("\n".join(lines)))
            except Exception:
                pass

    ctl = ("<h2>control</h2>"
           "<div class=dim style='margin-bottom:8px'>launch kills session + game + recorder, "
           "starts a fresh recorder (bus reset, fixed run dir D:/twdata/runs/human/run), then "
           "the session; each campaign's turn cap is drawn uniformly from [min, max]</div>"
           "<div style='display:flex;gap:16px;flex-wrap:wrap;align-items:center'>"
           "<a class=btn href='/ctl/kill' "
           "onclick=\"return ctl(this.href,'Kill the session and the game?')\">"
           "kill session + game</a>"
           "<form action='/ctl/restart' method='get' onsubmit='return launchRun(this)' "
           "style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0'>"
           "<label>campaigns <input name=campaigns type=number min=1 max=999 value=100 "
           "style='width:70px'></label>"
           "<label>turns min <input name=turns_min type=number min=1 max=999 value=2 "
           "style='width:70px'></label>"
           "<label>turns max <input name=turns_max type=number min=1 max=999 value=20 "
           "style='width:70px'></label>"
           "<label><input type=checkbox name=retrain value=1> retrain first</label>"
           "<label>retrain every <input name=retrain_every type=number min=0 max=999 value=0 "
           "style='width:60px' title='0 = never; N = retrain before every Nth campaign but NOT "
           "before campaign 1 -- tick retrain first for that. Leave it unticked to run the first "
           "N campaigns on the model already on disk. Each stretch between retrains is one trial "
           "in the ledger'></label>"
           "<label title='backend for the exploit_tree strategy, and the model whose scores "
           "are stored on every offer row. The gnn ranker is not selected here -- it is "
           "chosen by putting gnn in the strategy mix'>model </label>") + _model_select() + (
           "<label title='backend hyperparameters as KEY=VALUE pairs, e.g. bottleneck=64 lr=0.01"
           " -- recorded on the trial as backend_cfg'>cfg <input name=cfg type=text value='' "
           "placeholder='bottleneck=64 lr=0.01' style='width:170px'></label>"
           "<label title='per-decision strategy mix as name=weight pairs over random, "
           "exploit_tree, ruleset, gnn; weights are normalized; blank = policy.py default'>"
           "strategies <input name=strategies type=text value='' "
           "placeholder='exploit_tree=0.3,gnn=0.3,random=0.3,ruleset=0.1' "
           "style='width:190px'></label>"
           "<label title='rule file D:\\twdata\\rules\\NAME.json; required when ruleset is in "
           "the mix, forbidden otherwise'>ruleset <input name=ruleset type=text value='' "
           "placeholder='v1' style='width:70px'></label>"
           "<label title='firehose script-log tail + UI tree scrapes; gigabytes per run'><input type=checkbox name=dev value=1> dev logging</label>"
           "<button class=btn>launch run</button>"
           "</form></div>"
           "<div class=muted style='margin:14px 0 8px'>cold start: same kill+recorder sequence, "
           "but the session runs with NO model (--cold), on the default exploit_tree/random "
           "mix &mdash; no ranker is loaded, so every pick lands on random (provenance random / "
           "exploit_tree_random_fallback; the ledger labels the campaigns cold_random(forced)). "
           "A mix containing <b>gnn</b> is refused outright by session.py; <b>ruleset</b> needs "
           "no model and would still fire if one were requested. Never retrains.</div>"
           "<form action='/ctl/coldstart' method='get' onsubmit='return launchCold(this)' "
           "style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0'>"
           "<label>campaigns <input name=campaigns type=number min=1 max=9999 value=10 "
           "style='width:70px'></label>"
           "<label>max turns <input name=turns_max type=number min=1 max=999 value=40 "
           "style='width:70px'></label>"
           "<label title='which backend the cold run reports as -- the model is not consulted, "
           "but the trial is labelled with it'>model </label>") + _model_select() + (
           "<label title='firehose script-log tail + UI tree scrapes; gigabytes per run'><input type=checkbox name=dev value=1> dev logging</label>"
           "<button class=btn>launch cold start</button>"
           "</form>"
           "<pre id=ctlout class=dim style='margin-top:8px;white-space:pre-wrap'></pre>"
           "<script>"
           "function ctl(u,m){if(m&&!confirm(m))return false;"
           "var o=document.getElementById('ctlout');o.textContent='working\\u2026';"
           "fetch(u+(u.indexOf('?')>=0?'&':'?')+'ajax=1')"
           ".then(function(r){return r.text()}).then(function(t){o.textContent=t})"
           ".catch(function(e){o.textContent='request failed: '+e});return false}"
           "function launchRun(f){var q=new URLSearchParams(new FormData(f)).toString();"
           "return ctl('/ctl/restart?'+q,'Kill everything and launch this run?')}"
           "function launchCold(f){var q=new URLSearchParams(new FormData(f)).toString();"
           "return ctl('/ctl/coldstart?'+q,'Kill everything and launch a COLD START "
           "(no model, every pick random)?')}"
           "(function(){[['/ctl/restart','launch.'],['/ctl/coldstart','cold.']]"
           ".forEach(function(p){"
           "var f=document.querySelector(\"form[action='\"+p[0]+\"']\");if(!f)return;"
           "Array.prototype.forEach.call(f.elements,function(el){"
           "if(!el.name)return;var k=p[1]+el.name,v=localStorage.getItem(k);"
           "if(v!==null){if(el.type==='checkbox'){el.checked=(v==='1')}else{el.value=v}}"
           "var save=function(){localStorage.setItem(k,"
           "el.type==='checkbox'?(el.checked?'1':'0'):el.value)};"
           "el.addEventListener('change',save);el.addEventListener('input',save)})})})();"
           "</script>")
    # models moved to the models tab, the experiment ledger to training -- infra is the
    # box itself: what is running, what is writing, and the controls.
    return svc + policy_html + activity + ctl + tail


def _kill_session():
    import subprocess
    cmd = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
           "? { $_.CommandLine -like '*session.py*' } | % { Stop-Process -Id $_.ProcessId -Force "
           "-ErrorAction SilentlyContinue; $n++ }; "
           "Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue | "
           "Stop-Process -Force -ErrorAction SilentlyContinue; 'killed sessions={0}' -f $n")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=40,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        killed = (r.stdout or "").strip() or "killed"
    except Exception as e:
        return "kill failed: %s" % repr(e)[:120]
    return killed




def _kill_recorder():
    import subprocess
    cmd = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
           "? { $_.CommandLine -like '*manager.py*' } | % { Stop-Process -Id $_.ProcessId -Force "
           "-ErrorAction SilentlyContinue; $n++ }; 'killed recorders={0}' -f $n")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=40,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return (r.stdout or "").strip() or "recorder killed"
    except Exception as e:
        return "recorder kill failed: %s" % repr(e)[:120]




def _trial_row_html(r, live=False, show_cfg=True):
    s = r.get("settlements") or {}
    l = r.get("lord_level") or {}
    t = r.get("timing") or {}
    cfg = r.get("backend_cfg") or {}
    corpus = (r.get("corpus_at_train") or {}).get("rows")
    flags = []
    if live:
        flags.append("<span class=warn>RUNNING</span>")
    if r.get("stopped_short") and not live:
        flags.append("<span class=warn>cut short</span>")
    if r.get("baseline") != "pre_decision":
        flags.append("<span class=bad>end-of-turn</span>")
    outcomes = ", ".join("%s %s" % (v, k) for k, v in
                         sorted((r.get("outcomes") or {}).items(), key=lambda kv: str(kv[0])))
    sm, meas = s.get("mean"), s.get("campaigns_measured") or 0
    cls = ""
    n = r.get("campaigns", 0)
    return ("<tr><td%s>%s<td>%s%s<td>%s<td class=dim>%s<td>%s<td class=dim>%s<td%s>%s"
            "<td>%s<td>%s<td>%s<td>%s<td>%s<td>%s<td class=dim>%s</tr>"
            % (" class=warn" if live else "", _esc(r.get("trial", "?")),
               _esc(str(r.get("backend") or "-")),
               ("<td class=dim>%s"
                % _esc(", ".join("%s=%s" % kv for kv in sorted(cfg.items())) or "-"))
               if show_cfg else "",
               _mix_cell(r.get("strategies"), r.get("epsilon")),
               _esc(_ruleset_str(r.get("ruleset")) or "-"), n,
               corpus if corpus else "-",
               cls, "-" if sm is None else "%.3f" % sm,
               "-" if s.get("total") is None else "%g" % s["total"],
               "-" if s.get("campaigns_that_gained") is None
               else "%s/%s" % (s["campaigns_that_gained"], meas),
               "-" if l.get("mean") is None else "%.2f" % l["mean"],
               r.get("turns_per_campaign", "-"),
               t.get("s_per_campaign") if t.get("s_per_campaign") else "-",
               t.get("s_per_turn") if t.get("s_per_turn") else "-",
               "%s %s" % (" ".join(flags), _esc(outcomes))))


def _trials_table():
    import session as S
    try:
        with open(S.EXPERIMENTS, encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
    except OSError:
        rows = []
    except ValueError as e:
        return "<h2>experiment ledger</h2><div class=bad>ledger unreadable: %s</div>" % _esc(repr(e)[:160])
    latest = {}
    for r in sorted(rows, key=lambda x: (x.get("baseline") == "pre_decision", x.get("ts") or 0)):
        latest[r.get("trial")] = r
    rows = list(latest.values())
    try:
        live = S.live_trial()
    except Exception as e:
        live = None
        rows = rows or []
        note = "<div class=bad>live trial not readable: %s</div>" % _esc(repr(e)[:160])
    else:
        note = ""
    if live and not live.get("strategies") and live.get("session"):
        try:
            with open(live["session"], encoding="utf-8") as fh:
                req = (json.load(fh) or {}).get("requested") or {}
            live["strategies"] = req.get("strategies")
            live["ruleset"] = live.get("ruleset") or req.get("ruleset")
            if live.get("epsilon") is None:
                live["epsilon"] = req.get("epsilon")
        except (OSError, ValueError):
            pass
    if live:
        rows = [r for r in rows if r.get("trial") != live.get("trial")]
    if not rows and not live:
        return ("<h2>experiment ledger</h2>%s<div class=dim>no trials in %s</div>"
                % (note, _esc(S.EXPERIMENTS)))
    rows.sort(key=lambda r: r.get("started") or r.get("ts") or 0, reverse=True)
    # backend_cfg only fills for --nn-* tuning runs; when no trial carries one the column
    # is 66 rows of "-", which reads as a field that failed to populate
    show_cfg = any((r.get("backend_cfg") or {}) for r in ([live] if live else []) + rows)
    out = ([_trial_row_html(live, live=True, show_cfg=show_cfg)] if live else []) \
        + [_trial_row_html(r, show_cfg=show_cfg) for r in rows]
    return ("<h2>experiment ledger</h2>%s<div class=scroll><table>"
            "<tr><th>trial<th>backend%s"
            "<th title='per-decision strategy mix, as percentages in a fixed order: "
            "exploit_tree / gnn / random / ruleset. Hover a cell for exact weights; legacy "
            "--epsilon trials are shown as the mix they mean'>mix "
            "<span class=dim>ET/GN/RD/RS</span>"
            "<th title='rule set name@sha256 prefix'>ruleset"
            "<th>campaigns<th>corpus<th>sett/camp<th>sett total"
            "<th>grew<th title='legendary lord levels gained per campaign'>lord lvl/camp"
            "<th>turns/camp<th>s/camp<th>s/turn<th>notes</tr>%s</table></div>"
            % (note, "<th>cfg" if show_cfg else "", "".join(out)))


def _fmt(v, nd=3):
    if v is None:
        return "-"
    try:
        return ("%%.%df" % nd) % float(v)
    except (TypeError, ValueError):
        return _esc(str(v))


def _lift(e2_rmse, e1_rmse):
    if e2_rmse is None or e1_rmse is None:
        return "<td class=dim>-"
    d = float(e2_rmse) - float(e1_rmse)
    return "<td class=%s>%+.4f" % ("ok" if d > 0.02 else "bad" if d <= 0 else "warn", d)


MODEL_DIRS = (("global", common.MODEL_GLOBAL),
              ("local", common.MODEL_LOCAL),
              ("interrupt", common.MODEL_INTERRUPT),
              ("gnn", common.MODEL_MAPGRAPH))

_MODEL_ROLE = {
    "global": "catboost E1/E2 &mdash; E2 is the counterfactual baseline and E1&minus;E2 is the "
              "impact the <b>exploit_tree</b> strategy ranks on. The other three strategies "
              "ignore it, but its scores are still stored on every offer row",
    "local": "catboost &mdash; per-entity local value, blended into the global score",
    "interrupt": "catboost &mdash; ranks the options on blocking menus (battles, occupation)",
    "gnn": "mapgraph &mdash; every candidate action is a <b>node</b> in the decision "
           "graph, wired by typed edges to the actor, the target and the shared "
           "catalogue entry it instantiates (unit / building / tech / skill). The score "
           "is read off the action's own embedding after message passing; the ranking "
           "objective is a listwise softmax over the candidate set, not a regression",
}


def _live_gnn_schema():
    try:
        if common.ROOT not in sys.path:
            sys.path.insert(0, common.ROOT)
        from advisor.mapgraph import schema as GS
        return GS.SCHEMA_VERSION, GS.schema_hash()
    except Exception:
        return None, None


def _mrow(k, v, cls=""):
    return ("<div class=mrow><span class=mk>%s</span><span class='mv %s'>%s</span></div>"
            % (k, cls, v))


def _age_words(secs):
    if secs is None:
        return "-"
    if secs < 90:
        return "%.0fs ago" % secs
    if secs < 5400:
        return "%.0fm ago" % (secs / 60.0)
    if secs < 172800:
        return "%.1fh ago" % (secs / 3600.0)
    return "%.1fd ago" % (secs / 86400.0)


def _gnn_card(path, m, secs, when):
    """The gnn card carries the readiness gate: a schema mismatch silently voids the
    whole gnn share of the mix, so it has to be the loudest thing on the card."""
    fit = m.get("fit") or {}
    live_ver, live_hash = _live_gnn_schema()
    have_hash = m.get("schema_hash")
    weights = all(os.path.exists(os.path.join(os.path.dirname(path), f))
                  for f in ("encoder.pt", "head.pt"))
    if not m:
        state, cls, note = "missing", "bad", (
            "No model on disk. Every <b>gnn</b> draw falls back to random until the first "
            "retrain window trains one.")
    elif not weights:
        state, cls, note = "incomplete", "bad", (
            "meta.json is present but encoder.pt/head.pt are not &mdash; the ranker cannot "
            "load this; gnn draws fall back to random.")
    elif live_hash and have_hash != live_hash:
        state, cls, note = "stale schema", "bad", (
            "The graph schema in code has changed since this was trained, so "
            "<code>rank.py</code> refuses it and <b>every gnn draw falls back to random</b>. "
            "It clears itself at the next retrain window."
            "<br>on disk <code>v%s %s</code> &middot; code <code>v%s %s</code>"
            % (_esc(str(m.get("schema_version"))), _esc(str(have_hash)[:12]),
               _esc(str(live_ver)), _esc(str(live_hash)[:12])))
    else:
        state, cls, note = "ready", "ok", ""
    dev = fit.get("device")
    # v3 does not regress the score, so there is no rmse to show. The ranking metric is
    # the held-out listwise NLL, in nats, and it is only readable against the uniform
    # baseline log(candidate set size) -- about 5.9 at the corpus median of 376 offers.
    nll = fit.get("val_listwise_nll")
    rows = [
        _mrow("held-out listwise NLL",
              ("%s <span class=dim>nats &middot; uniform &asymp; 5.9</span>" % _fmt(nll, 4))
              if nll is not None else "<span class=dim>-</span>"),
        _mrow("rows / campaigns", "%s &middot; %s" % (_esc(str(m.get("rows", "-"))),
                                                      len(m.get("campaigns") or []) or "-")),
        _mrow("trained on", ("<span class=%s>%s</span>"
                             % ("ok" if dev == "cuda" else "warn", _esc(str(dev))))
              if dev else "<span class=dim>not recorded</span>"),
        _mrow("fit", "%ss &middot; %s epochs (%s)"
              % (_esc(str(fit.get("seconds", "-"))), _esc(str(fit.get("epochs_run", "-"))),
                 _esc(str(fit.get("stopped_by", "-"))))),
        _mrow("graph schema", "v%s <span class=dim>%s</span>"
              % (_esc(str(m.get("schema_version", "-"))), _esc(str(have_hash or "-")[:12]))),
        _mrow("aux labelled nodes", _esc(str((m.get("aux") or {}).get("n_labelled_nodes", "-")))),
        _mrow("trained at", "%s <span class=dim>%s</span>" % (_esc(when), _age_words(secs))),
    ]
    return state, cls, note, rows


def _catboost_card(name, m, secs, when, events):
    latest = None
    for e in events:
        if (e.get(name) or {}).get("e1", {}).get("val_rmse") is not None:
            latest = e
            break
    fam = (latest or {}).get(name) or {}
    e1 = (fam.get("e1") or {}).get("val_rmse")
    e2 = (fam.get("e2") or {}).get("val_rmse")
    state, cls, note = ("ready", "ok", "") if m else (
        "missing", "bad",
        "No model on disk. Every <b>exploit_tree</b> draw falls back to random "
        "(provenance <code>exploit_tree_random_fallback</code>); the gnn, ruleset and "
        "random shares of the mix are unaffected.")
    rows = [_mrow("held-out rmse (e1)", _fmt(e1, 4))]
    if e2 is not None:
        d = float(e2) - float(e1) if e1 is not None else None
        rows.append(_mrow("e2 baseline / lift", "%s <span class=%s>%s</span>"
                          % (_fmt(e2, 4),
                             "ok" if d and d > 0.02 else "bad" if d is not None and d <= 0
                             else "warn",
                             ("%+.4f" % d) if d is not None else "-")))
    rows.append(_mrow("rows / campaigns", "%s &middot; %s"
                      % (_esc(str(m.get("rows", "-"))),
                         len(m.get("campaigns") or []) or "-")))
    sd = m.get("sd_global") or m.get("sd_local")
    if sd is not None:
        rows.append(_mrow("target spread (sd)", _fmt(sd, 4)))
    if m.get("screens") is not None:
        rows.append(_mrow("screens covered", _esc(str(len(m.get("screens") or [])))))
    rows.append(_mrow("trained at", "%s <span class=dim>%s</span>" % (_esc(when),
                                                                      _age_words(secs))))
    return state, cls, note, rows


def _fit_config_table(events=()):
    body = []
    # the last measured fit, rather than a number frozen into the page: this cell used to
    # claim "14.6s vs 21.6s" while real fits on the grown corpus were taking ~3 minutes
    last = next((e for e in events if e.get("seconds")), None)
    cb_cost = ("cpu &mdash; last fit %ss over %s rows"
               % (_esc(str(last.get("seconds"))), _esc(str(last.get("rows", "?"))))
               if last else "cpu <span class=dim>(no fit recorded yet)</span>")
    try:
        BM = _live_base_model()
        body.append(
            "<tr><td>catboost<td>global / local / interrupt<td>lr %s &middot; "
            "early stop %s &middot; cap %s iters &middot; depth %s &middot; %s &middot; %s "
            "&middot; loss %s &middot; holdout %.0f%% of campaigns"
            "<br><span class=dim>tuned from %s</span>"
            "<td class=dim>%s</tr>"
            % (BM.CB_LEARNING_RATE, BM.CB_EARLY_STOPPING, BM.CB_ITERATIONS, BM.CB_DEPTH,
               _esc(str(BM.CB_PARAMS.get("grow_policy"))),
               _esc(str(BM.CB_PARAMS.get("bootstrap_type"))),
               BM.CB_LOSS, 100 * BM.VAL_FRACTION, _esc(str(BM.CB_TUNED_FROM)), cb_cost))
    except Exception as e:
        body.append("<tr><td>catboost<td colspan=3 class=bad>config unreadable: %s</tr>"
                    % _esc(repr(e)[:90]))
    try:
        if common.ROOT not in sys.path:
            sys.path.insert(0, common.ROOT)
        from advisor.mapgraph import train as _GT
        from advisor.mapgraph import schema as _GS
        c = _GT.CFG
        body.append(
            "<tr><td>gnn<td>graph action-scorer<td>hidden %s &middot; %s entity layers + "
            "%s action rounds &middot; lr %s &middot; weight decay %s &middot; "
            "batch %s graphs &middot; &le;%s epochs (patience %s) &middot; "
            "advantage weight exp(adv/%s) capped %s &middot; budget %ss &middot; "
            "campaign-grouped holdout &middot; %s raw scalars over %s node types "
            "and %s relations"
            "<td class=dim>%s &mdash; %s train threads, %s infer</tr>"
            % tuple(_esc(str(v)) for v in
                    (c["hidden"], c["entity_layers"], c["action_rounds"], c["lr"],
                     c["weight_decay"], c["batch"], c["epochs"], c["patience"],
                     c["adv_tau"], c["adv_clip"], c["time_budget_s"],
                     _GS.N_SCALARS, len(_GS.NODE_TYPES), len(_GS.RELATIONS),
                     c.get("device", "auto"), _GT.THREADS, 2)))
    except Exception as e:
        body.append("<tr><td>gnn<td colspan=3 class=bad>config unreadable: %s</tr>"
                    % _esc(repr(e)[:90]))
    return ("<h2>fit configuration</h2><div class=scroll><table>"
            "<tr><th>family<th>role<th>hyperparameters<th>compute</tr>%s</table></div>"
            % "".join(body))


# ======================================================================================
# MODEL METRICS -- primitives
#
# The tab this replaced led with a 14-column stratified table in which nearly every cell
# read p=1.000, followed by two more tables in which 12 of 14 rows read "no ranked
# offers". It was scaffolding for data that does not exist yet. This version puts the
# pooled numbers -- which always exist -- at the top, and lets the stratified detail be
# sparse at the bottom where sparseness is honest rather than dominant.
# ======================================================================================

# ======================================================================================
# MODEL METRICS -- deliberately two questions, not six.
#
# The previous version answered six and answered none of them legibly: six stacked
# sections, each a heading plus a paragraph plus a wide table, most cells empty or
# p=1.000. Model share, counted rates and campaign pacing are already answered on other
# tabs and are not this tab's job. What is left is what the tab was created for:
#
#   1. what each model wants to do, as a picture, two tiles side by side
#   2. whether a strategy's share of the play relates to how the campaign went, POOLED
#
# Everything else is shelved, not deleted from the project's intent -- per weight set,
# version drift, the draw audit and the campaigns-to-significance arithmetic all have a
# place once these two read well.
# ======================================================================================

MM_TOP_TYPES = 8              # action types drawn per tile; the tail folds into "other"
MM_TILE_W = 340
MM_GATE_Z = 2.807             # two-sided z at alpha=.005


def _mm_arm(p):
    """Which arm the DRAW assigned. Intention-to-treat.

    "<strategy>_random_fallback" means the arm was drawn and had nothing to say. That is
    state-dependent -- it fires exactly when the arm is stuck -- so dropping those rows
    would condition on a post-treatment variable. They belong to the arm that was drawn.
    forced_end_turn is not a randomised unit at all: the loop emits it, no draw happened.
    """
    p = p or ""
    if p == "forced_end_turn":
        return None
    if p.endswith("_random_fallback"):
        p = p[:-len("_random_fallback")]
    # "gnn_delegated_exploit_tree": the gnn was drawn and handed the choice to the tree.
    # Attributed to the model that DECIDED -- exploit_tree -- not the one that was drawn.
    # A delegation is not a failure to act like a fallback is: the gnn routed to another
    # real model and that model's ranking is what picked, so crediting the gnn would put
    # the tree's behaviour in the gnn's row. Without this branch it matched nothing and
    # was silently dropped.
    if "_delegated_" in p:
        p = p.split("_delegated_", 1)[1]
    if p == "gnn":
        p = "gnn_marwil"
    if p.startswith("ruleset"):
        return "ruleset"
    return p if p in ("random", "exploit_tree", "gnn_marwil") else None


MM_ARMS = ("exploit_tree", "gnn_marwil", "ruleset", "random")


def _mm_window(con):
    """(lo, hi) decision ids over which a model actually scored.

    Not `MIN/MAX(decision_id) FROM action_offers WHERE score IS NOT NULL`: action_offers
    is a VIEW that zlib-decompresses every blob and unpacks a float32 per row merely to
    test for NULL, which cost 1.7s and grew with the corpus. scores.packed holds the same
    numbers directly; an untrained decision stores NaN, which _f32 reports as None.
    Scoring starts once and never stops, so the boundary is monotone and a bisection finds
    it in a dozen single-row reads. Bisect the id LIST, not the id range -- ids are sparse
    and a snapped numeric midpoint can land on the upper bound and never terminate.
    """
    ids = [r[0] for r in con.execute("SELECT decision_id FROM scores ORDER BY decision_id")]
    if not ids:
        return (None, None)

    def scored(did):
        r = con.execute("SELECT packed FROM scores WHERE decision_id=?", (did,)).fetchone()
        return bool(r) and dbopen._f32(r[0], 0) is not None

    if not scored(ids[-1]):
        return (None, None)
    lo, hi = 0, len(ids) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if scored(ids[mid]):
            hi = mid
        else:
            lo = mid + 1
    return (ids[lo], ids[-1])


def _mm_wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / float(n)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _mm_first_picks(con, lo, hi):
    """Per action type: how many decisions OFFERED it, and how often each model put it
    first. Availability-conditioned, which is the only reading that means anything -- a
    model cannot pick what was never on the menu, so a raw count of first picks would
    mostly measure the offer generator.

    catboost's first pick counts only where a score exists. An untrained ranker returns
    no score, yet policy.choose still stamps rank = i + 1 over the list, so `rank == 1`
    alone would count enumeration order as an opinion. gnn_rank needs no such guard: it is
    only ever written alongside a value.
    """
    offered = collections.Counter()
    first = {"catboost": collections.Counter(), "gnn_marwil": collections.Counter()}
    seen = collections.defaultdict(set)
    for did, at, r, g, s in con.execute(
            "SELECT decision_id, action_type, rank, gnn_rank, score FROM action_offers"
            " WHERE decision_id BETWEEN ? AND ?", (lo, hi)):
        if at not in seen[did]:
            seen[did].add(at)
            offered[at] += 1
        if r == 1 and s is not None:
            first["catboost"][at] += 1
        if g == 1:
            first["gnn_marwil"][at] += 1
    return offered, first, len(seen)


def _mm_fold(offered, picks, keep=MM_TOP_TYPES):
    """Keep the most-offered action types, fold the tail into `other`.

    A tile cannot carry fourteen labelled rows legibly. The tail is folded rather than
    dropped so the bars still describe the whole menu, and `other` growing is itself the
    signal that a model has moved to types it used to ignore.
    """
    top = [t for t, _n in offered.most_common(keep)]
    rest = [t for t in offered if t not in top]
    rows = [(t, offered[t], picks.get(t, 0)) for t in top]
    if rest:
        rows.append(("other (%d)" % len(rest), sum(offered[t] for t in rest),
                     sum(picks.get(t, 0) for t in rest)))
    return rows


def _mm_bars_svg(rows, cls):
    """One tile's chart: for each action type, the share of the decisions that OFFERED it
    on which this model put it first. Wilson 95% hairline on each bar."""
    if not rows:
        return "<div class=dim>nothing offered in this window</div>"
    lab_w, bar_w, rowh = 132, 150, 20
    h = len(rows) * rowh + 22
    out = ["<svg width='%d' height='%d' role='img'>" % (MM_TILE_W, h)]
    for i, (t, n, k) in enumerate(rows):
        y = 12 + i * rowh
        f = (k / float(n)) if n else 0.0
        out.append("<text x='0' y='%d' class='mmlab'>%s</text>" % (y + 8, _esc(t[:19])))
        out.append("<rect x='%d' y='%d' width='%.1f' height='9' class='%s'/>"
                   % (lab_w, y, max(0.0, f * bar_w), cls))
        a, b = _mm_wilson(k, n)
        out.append("<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' class='mmci'/>"
                   % (lab_w + a * bar_w, y + 4.5, lab_w + b * bar_w, y + 4.5))
        out.append("<text x='%.1f' y='%d' class='%s'>%d%%</text>"
                   % (lab_w + max(f * bar_w, 2) + 5, y + 8,
                      "mmlab" if f >= 0.15 else "mmdim", round(100 * f)))
        out.append("<text x='%d' y='%d' class='mmdim'>%d</text>"
                   % (MM_TILE_W - 26, y + 8, n))
    out.append("<line x1='%d' y1='%d' x2='%d' y2='%d' class='mmax'/>"
               % (lab_w, h - 8, lab_w + bar_w, h - 8))
    out.append("</svg>")
    return "".join(out)


def _mm_campaign_table(con):
    """One row per campaign: each arm's share of the play, and how the campaign moved.

    JOIN TRAP, load-bearing: target_rows.campaign_id is the campaign KEY STRING
    ("wh2_dlc09_skv_clan_rictus_3cd4821..."), campaigns.campaign_id is an integer
    surrogate. Joining the identically-named columns returns ZERO rows, silently. The join
    is target_rows.campaign_id == campaigns.campaign_key.

    Outcomes come from target_rows and nowhere else: campaigns.outcome and
    campaigns.defeated are NULL on every row in the corpus.

    The two outcomes are SETTLEMENT GAIN and LORD LEVEL GAIN, because those are the
    objective the run is actually scored against -- the growth gate keeps a campaign alive
    on "+1 settlements over 4 turns OR +1 legendary lord level over 3 turns" and kills it
    otherwise. turns, income and power_rank were the wrong things to correlate: turns is
    mostly how long the gate tolerated the campaign, and income and power_rank both drift
    upward with turn count, so a faster arm scores on throughput alone.

    PEAK minus first, not last minus first, for the same reason the gate uses a peak: a
    campaign that takes a settlement and loses it again did the thing being measured.
    """
    rng = [(cid, key, lo, hi, turns) for cid, key, lo, hi, turns in con.execute(
        "SELECT campaign_id, campaign_key, first_decision_id, last_decision_id, turns"
        " FROM campaigns WHERE first_decision_id IS NOT NULL ORDER BY first_decision_id")]
    if not rng:
        return []
    arms = [collections.Counter() for _ in rng]
    i = 0
    for did, pol in con.execute("SELECT decision_id, policy FROM taken ORDER BY decision_id"):
        while i < len(rng) and did > rng[i][3]:
            i += 1
        if i >= len(rng):
            break
        if did >= rng[i][2]:
            a = _mm_arm(pol)
            if a:
                arms[i][a] += 1
    tgt = collections.defaultdict(list)
    for key, turn, setts, lord in con.execute(
            "SELECT campaign_id, turn, settlements, lord_level FROM target_rows"
            " ORDER BY campaign_id, turn"):
        tgt[key].append((turn, setts, lord))
    out = []
    for idx, (cid, key, lo, hi, turns) in enumerate(rng):
        c = arms[idx]
        n = sum(c.values())
        rows = tgt.get(key) or []
        if not n or len(rows) < 2:
            continue

        def gain(j):
            vals = [float(r[j]) for r in rows if r[j] is not None]
            return (max(vals) - vals[0]) if len(vals) >= 2 else None

        out.append({"cid": cid, "n": n, "turns": float(turns or 0),
                    "share": {a: c.get(a, 0) / float(n) for a in MM_ARMS},
                    "setts": gain(1), "lord": gain(2)})
    return out


def _mm_interrupt_shares(con):
    """campaign_id -> each arm's share of that campaign's INTERRUPT decisions.

    A separate model decides interrupts -- the blocking menus: pre_battle, battle_results,
    occupation, dilemma, diplomacy_proposal -- and it is trained and drawn separately from
    the action ranker, so its share deserves its own contrast rather than being pooled in
    with ordinary decisions. interrupts.campaign_id is the integer surrogate here and joins
    campaigns.campaign_id directly (343 of 343), unlike target_rows which keys on the
    campaign key string.
    """
    per = collections.defaultdict(collections.Counter)
    for cid, pol in con.execute("SELECT campaign_id, policy FROM interrupts"):
        a = _mm_arm(pol)
        if a and cid is not None:
            per[cid][a] += 1
    return {cid: {"n": sum(c.values()),
                  "share": {a: c.get(a, 0) / float(sum(c.values())) for a in MM_ARMS}}
            for cid, c in per.items() if sum(c.values())}


def _mm_corr_rows(camps, share_of):
    """The shared body of both correlation tables: rho of an arm's share against the
    campaign's PEAK gain, beside the gate that share size can resolve."""
    lanes = (("settlements", lambda c: c["setts"]),
             ("lord level", lambda c: c["lord"]))
    rows = []
    for arm in MM_ARMS + ("model pool",):
        get = ((lambda c: (share_of(c) or {}).get("exploit_tree", 0.0)
                + (share_of(c) or {}).get("gnn_marwil", 0.0))
               if arm == "model pool"
               else (lambda c, a=arm: (share_of(c) or {}).get(a, 0.0)))
        used = [c for c in camps if share_of(c) is not None and get(c) > 0]
        cells = []
        for _lab, out in lanes:
            pairs = [(get(c), out(c)) for c in used if out(c) is not None]
            if len(pairs) < 4 or len({round(q[0], 6) for q in pairs}) < 2:
                cells.append("<td class='num mmdimc'>-</td>")
                continue
            r = _spearman([q[0] for q in pairs], [q[1] for q in pairs])
            gate = MM_GATE_Z / math.sqrt(len(pairs) - 1)
            hot = r is not None and abs(r) >= gate
            cells.append("<td class=num><span class='%s'>%+0.2f</span>"
                         "<span class=mmdimc> /%.2f</span></td>"
                         % ("mmhot" if hot else "", r or 0.0, gate))
        rows.append("<tr><td>%s</td><td class=num>%d</td>%s</tr>"
                    % (_esc(arm), len(used), "".join(cells)))
    return ("<table class=mmtbl><tr><th>arm<th class=num>campaigns"
            "<th class=num>settlements<th class='num mmdimc'>lord level</tr>%s</table>"
            % "".join(rows))


def _mm_corr_tables(con):
    """Two contrasts side by side: the action ranker and the interrupt model.

    Both are POOLED across every campaign, deliberately -- these numbers exist even while
    nothing is conclusive, and the stratified-by-weight-set version was mostly empty cells.

    Read both with the same two caveats. They are OBSERVATIONAL: the draw is randomised
    per decision, but this aggregates to a campaign-level share and correlates against a
    campaign-level result, which throws the randomisation away, and a campaign that ended
    early has a different share partly BY LUCK -- so share is itself partly an outcome.
    And the corpus spans several configurations: mix, action cap, faction pool and feature
    set all moved mid-run, so a pooled number is partly an era contrast.
    """
    camps = _mm_campaign_table(con)
    if not camps:
        return "<div class=dim>no campaign has both a recorded share and an outcome yet</div>"
    ints = _mm_interrupt_shares(con)
    n_int = sum(v["n"] for v in ints.values())
    return ("<div class=mmgrid>"
            + ("<div class=mmtile><div class=mmt>action ranker</div>%s"
               "<div class=mms>share of the campaign's ordinary decisions</div></div>"
               % _mm_corr_rows(camps, lambda c: c["share"]))
            + ("<div class=mmtile><div class=mmt>interrupt model</div>%s"
               "<div class=mms>share of the campaign's blocking-menu decisions "
               "(pre_battle, battle_results, occupation, dilemma, diplomacy_proposal). "
               "%d interrupts over %d campaigns &mdash; a separate model, trained and "
               "drawn separately.</div></div>"
               % (_mm_corr_rows(camps, lambda c: (ints.get(c["cid"]) or {}).get("share")),
                  n_int, len(ints)))
            + "</div>"
            + "<div class=mms>each cell is Spearman rho of that arm's share against that "
              "campaign's PEAK gain, then <b>/gate</b> &mdash; the smallest |rho| separable "
              "from chance at p&lt;0.005 for that n. Nothing is significant until a rho "
              "exceeds its own gate. <b>Settlement gain is the objective</b>; lord level is "
              "a proxy that exists to support taking ground, so it is supporting evidence "
              "rather than a second result &mdash; a run that levels its lord and takes no "
              "ground has not done the thing. Settlement gain currently takes only 0 or 1 "
              "because campaigns end at 4-12 turns; that widens as campaigns run longer and "
              "is not a property of the measure. Both tables are observational and pooled "
              "across configurations.</div>")


def render_model_metrics(con, run, q):
    return ("<h2>what each model wants to do</h2>"
            "<div class=lazy data-src='/panel/mm_forcing'>"
            "<div class=mms>counting first picks against what was on the menu&hellip;</div>"
            "</div>"
            "<h2>does a strategy's share track how the campaign went?</h2>"
            + _mm_corr_tables(con))


def render_mm_forcing(con, run, q):
    lo, hi = _mm_window(con)
    if lo is None:
        return ("<div class=mms>no model has weights yet, so neither has expressed a "
                "preference.</div>")
    offered, first, n_dec = _mm_first_picks(con, lo, hi)
    if not offered:
        return "<div class=mms>no offers in the scored window.</div>"
    tiles = []
    for model, cls in (("catboost", "mmcat"), ("gnn_marwil", "mmgnn")):
        picks = first[model]
        top = picks.most_common(1)
        sub = ("most often puts <b>%s</b> first" % _esc(top[0][0])) if top else "no picks"
        tiles.append("<div class=mmtile><div class='mmt %s'>%s</div>%s"
                     "<div class=mms>%s</div></div>"
                     % (cls, _esc(model), _mm_bars_svg(_mm_fold(offered, picks), cls), sub))
    return ("<div class=mms>Share of the decisions that <b>offered</b> an action type on "
            "which the model put it first. Conditioned on availability because a model "
            "cannot pick what was never on the menu &mdash; an unconditioned count would "
            "mostly measure the offer generator. Both models score every offer on every "
            "decision, so this is what each <b>wants</b>, not what the draw let it do. "
            "Tail beyond the %d most-offered types folds into <b>other</b>; hairline is a "
            "Wilson 95%% interval, right-hand number is how many decisions offered it. "
            "%d decisions in the scored window.</div>"
            "<div class=mmgrid>%s</div>" % (MM_TOP_TYPES, n_dec, "".join(tiles)))


def render_models():
    import session as S
    try:
        events = S.train_events()
    except Exception:
        events = []
    cards, alerts = [], []
    for name, d in MODEL_DIRS:
        path = os.path.join(d, "meta.json")
        m = _meta(path)
        secs, when = _age(path)
        if name == "gnn":
            state, cls, note, rows = _gnn_card(path, m, secs, when)
        else:
            state, cls, note, rows = _catboost_card(name, m, secs, when, events)
        if cls == "bad":
            alerts.append("<b>%s</b>: %s" % (_esc(name), state))
        cards.append(
            "<div class='mcard%s'><div class=mhead><span class=mname>%s</span>"
            "<span class='badge %s'>%s</span></div><div class=mrole>%s</div>%s%s</div>"
            % (" alert" if cls == "bad" else "", _esc(name), cls, _esc(state),
               _MODEL_ROLE.get(name, ""), "".join(rows),
               ("<div class='note %s'>%s</div>" % (cls, note)) if note else ""))
    banner = ("<div class=note><span class=bad>needs attention</span> &mdash; %s</div>"
              % " &middot; ".join(alerts)) if alerts else ""
    return ("<h2>models on disk</h2>%s<div class=mcards>%s</div>%s"
            % (banner, "".join(cards), _fit_config_table(events)))


def render_training():
    import session as S
    try:
        events = S.train_events()
    except Exception as e:
        return ("<h2>training history</h2><div class=bad>unreadable: %s</div>"
                % _esc(repr(e)[:160]))
    out = []
    alive = _session_alive()
    for e in events:
        g, l, i, p = e["global"], e["local"], e["interrupt"], e["played"]
        gn = e.get("gnn") or {}
        gn_fit = gn.get("fit") or {}
        par = e.get("params") or {}
        badge = ""
        if p.get("running"):
            badge = " <span class=warn>RUNNING</span>"
            if not alive:
                badge += (" <span class=bad>STALE: flagged running, no session.py process "
                          "alive -- this generation was never flushed</span>")
        if e.get("error"):
            badge = " <span class=bad>FAILED</span>"
        gn_dev = gn_fit.get("device")
        if not gn:
            gnn_cells = "<td class='dim gsep'>-<td class=dim>-<td class=dim>-"
        elif gn.get("error"):
            gnn_cells = ("<td class='bad gsep' colspan=3>%s"
                         % _esc(str(gn.get("error"))[:70]))
        else:
            gnn_cells = ("<td class=gsep>%s<td class=dim>%s<td class=%s>%s"
                         % (_fmt(gn_fit.get("val_listwise_nll"), 4),
                            _esc(str(gn.get("rows", "-"))),
                            "ok" if gn_dev == "cuda" else "warn" if gn_dev else "dim",
                            _esc(str(gn_dev or "-"))))
        out.append(
            "<tr><td class=dim>%s<td>%s%s<td class=gsep>%s<td>%s<td class=dim>%s<td>%s"
            "<td class=dim>%s<td class=dim>%s"
            "<td class=gsep>%s<td>%s<td>%s%s<td class=dim>%s<td class=dim>%s"
            "<td class=gsep>%s<td>%s<td class=gsep>%s<td>%s%s"
            "<td class=gsep>%s<td>%s<td>%s<td>%s<td>%s<td>%s</tr>"
            % (_esc(e["when"][5:16]),
               _esc(e["trial"]), badge,
               _esc(str(e.get("rows", "-"))), _esc(str(e.get("campaigns", "-"))),
               _esc(str(e.get("n_decisions", "-"))),
               _esc(str(e.get("seconds", "-"))),
               _esc(str(par.get("learning_rate", "?"))),
               _esc(str(par.get("early_stopping_rounds", "?"))),
               _esc(str(g["e1"].get("val_rows", "-"))),
               _fmt(g["e1"].get("val_rmse"), 4), _fmt(g["e2"].get("val_rmse"), 4),
               _lift(g["e2"].get("val_rmse"), g["e1"].get("val_rmse")),
               _esc(str(g["e1"].get("best_iteration", "-"))),
               _fmt(e.get("mae_in_sample"), 4),
               _esc(str(l.get("rows", "-"))), _fmt(l["e1"].get("val_rmse"), 4),
               _esc(str(i.get("rows", "-"))), _fmt(i["e1"].get("val_rmse"), 4),
               gnn_cells,
               _esc(str(p.get("campaigns") if p.get("campaigns") is not None else "-")),
               _fmt(p.get("sett_per_campaign")),
               # the ledger directly above renders this same metric with %g; str() on the
               # float gave "4.0" here and "4" there for the identical number
               _int(p.get("sett_total")) if p.get("sett_total") is not None else "-",
               _fmt(p.get("lord_per_campaign"), 2),
               _fmt(p.get("turns_per_campaign")),
               "-" if p.get("grew") is None else "%s/%s" % (p["grew"], p.get("measured", "?"))))
    if not out:
        out = ["<tr><td colspan=20 class=dim>no training runs recorded</tr>"]
    return (_trials_table()
            + "<h2>training history</h2>"
            "<div class=legend>one row per retrain window, newest first &mdash; the fit half of "
            "each trial above, keyed on the same trial id. <b>lift</b> is e2&minus;e1: how much "
            "better the model is than its own counterfactual baseline &mdash; at or below zero "
            "the model adds nothing.</div>"
            "<div class=scroll><table>"
            "<tr><th colspan=2>run<th class=grp colspan=6>corpus"
            "<th class=grp colspan=6>catboost global<th class=grp colspan=2>local"
            "<th class=grp colspan=2>interrupt<th class=grp colspan=3>gnn"
            "<th class=grp colspan=6>what it played</tr>"
            "<tr><th>when<th>trial<th class=gsep>rows<th>camps<th>decisions<th>secs"
            "<th>lr<th>ES"
            "<th class=gsep>val rows<th>e1 rmse<th>e2 rmse<th>lift<th>best iter<th>MAE"
            "<th class=gsep>rows<th>e1 rmse<th class=gsep>rows<th>e1 rmse"
            "<th class=gsep title='held-out listwise NLL in nats over the candidate set; "
            "uniform baseline is log(n offers), about 5.9. NOT comparable to the catboost "
            "rmse columns -- different objective, different units'>list NLL<th>rows<th>device"
            "<th class=gsep>camps<th>sett/camp<th>sett total"
            "<th title='legendary lord levels gained per campaign'>lord lvl/camp<th>turns/camp"
            "<th>grew</tr>%s</table></div>"
            % "".join(out))


def _model_select():
    import backends as B
    opts = "".join("<option value='%s'%s>%s &mdash; %s</option>"
                   % (n, " selected" if n == B.DEFAULT else "", n, _esc(B.label(n)))
                   for n in B.names())
    return "<select name=model style='max-width:260px'>%s</select>" % opts


def _start_recorder(shots=60, dev=False):
    import runctl
    try:
        return runctl.start_recorder(shots=shots, dev=dev)
    except Exception as e:
        return "recorder start failed: %s" % repr(e)[:160]


def _start_session(retrain=True, campaigns=10, turns=40, retrain_every=0, cold=False, dev=False,
                   model=None, cfg=None, strategies=None, ruleset=None):
    import runctl
    try:
        log = runctl.start_session(campaigns, turns, model=model, cfg=cfg, retrain=retrain,
                                   retrain_every=retrain_every, cold=cold, dev=dev,
                                   strategies=strategies, ruleset=ruleset)
        return "started %s on %s%s%s%s -> %s" % (
            "COLD (no model; exploit_tree can never fire, every pick lands random)" if cold else
            "with retrain" if retrain else "no retrain",
            model or "default backend",
            (" cfg=%s" % json.dumps(cfg)) if cfg else "",
            (" strategies=%s" % strategies) if strategies else " strategies=default",
            (" ruleset=%s" % ruleset) if ruleset else "",
            os.path.basename(log))
    except Exception as e:
        return "start failed: %s" % repr(e)[:160]


def _trial_params(q):
    import backends as B
    model = (q.get("model", [""])[0] or "").strip().lower()
    if model and model not in B.names():
        return None, None, ("unknown model %r -- known backends: %s, nothing was killed or started"
                            % (model, ", ".join(B.names())))
    cfg, raw = {}, (q.get("cfg", [""])[0] or "").strip()
    for tok in raw.replace(",", " ").split():
        if "=" not in tok:
            return None, None, ("bad cfg %r -- want KEY=VALUE pairs, e.g. 'bottleneck=64 lr=0.01',"
                                " nothing was killed or started" % tok)
        k, v = tok.split("=", 1)
        k = k.strip().replace("-", "_")
        if not k:
            return None, None, "bad cfg %r -- empty key, nothing was killed or started" % tok
        try:
            cfg[k] = int(v)
        except ValueError:
            try:
                cfg[k] = float(v)
            except ValueError:
                cfg[k] = v.strip()
    return (model or None), (cfg or None), None


def _strategy_params(q):
    import policy as P
    raw = (q.get("strategies", [""])[0] or "").strip()
    name = (q.get("ruleset", [""])[0] or "").strip()
    mix = None
    if raw:
        parsed = {}
        for tok in raw.split(","):
            k, sep, v = tok.partition("=")
            k, v = k.strip(), v.strip()
            if not sep or not k or not v:
                return None, None, ("bad strategies %r -- want name=weight[,name=weight...], "
                                    "e.g. exploit_tree=0.8,random=0.2, nothing was killed or "
                                    "started" % tok)
            if k in parsed:
                return None, None, ("strategies names %r twice, nothing was killed or started" % k)
            try:
                parsed[k] = float(v)
            except ValueError:
                return None, None, ("strategies weight for %r is not a number: %r, nothing was "
                                    "killed or started" % (k, v))
        try:
            mix = P.normalize_strategies(parsed)
        except ValueError as e:
            return None, None, "%s, nothing was killed or started" % e
    if mix and "ruleset" in mix and not name:
        return None, None, ("strategy mix includes 'ruleset' but no ruleset name was given, "
                            "nothing was killed or started")
    if name and (not mix or "ruleset" not in mix):
        return None, None, ("ruleset %r given but 'ruleset' is not in the strategy mix, "
                            "nothing was killed or started" % name)
    if name:
        import ruleset as RS
        try:
            RS.RuleSet.load(name)
        except Exception as e:
            return None, None, ("ruleset %r not loadable: %s, nothing was killed or started"
                                % (name, str(e)[:200]))
    return (raw or None), (name or None), None


def _control_steps(path):
    import time
    from urllib.parse import parse_qs, urlparse
    u = urlparse(path)
    q = parse_qs(u.query or "")
    if u.path == "/ctl/kill":
        return _kill_session()
    if u.path == "/ctl/coldstart":
        try:
            campaigns = int(q.get("campaigns", [""])[0])
            tmax = int(q.get("turns_max", [""])[0])
        except ValueError:
            return ("invalid cold start: campaigns=%r turns_max=%r -- both must be integers, "
                    "nothing was killed or started"
                    % (q.get("campaigns"), q.get("turns_max")))
        if not (1 <= campaigns <= 9999 and 1 <= tmax <= 999):
            return ("invalid cold start: campaigns=%d turns_max=%d -- need 1 <= campaigns <= 9999 "
                    "and 1 <= turns_max <= 999, nothing was killed or started"
                    % (campaigns, tmax))
        dev = (q.get("dev", ["0"])[0] not in ("0", ""))
        model, cfg, err = _trial_params(q)
        if err:
            return "invalid cold start: %s" % err
        steps = [_kill_session(), _kill_recorder()]
        time.sleep(1.5)
        steps.append(_start_recorder(dev=dev))
        time.sleep(3.0)
        steps.append(_start_session(retrain=False, campaigns=campaigns, turns=tmax, cold=True,
                                    dev=dev, model=model, cfg=cfg))
        steps.append("give the recorder + session ~20s to appear in the tables above")
        return "\n".join(steps)
    if u.path == "/ctl/restart":
        retrain = (q.get("retrain", ["0"])[0] not in ("0", ""))
        try:
            campaigns = int(q.get("campaigns", [""])[0])
            tmin = int(q.get("turns_min", [""])[0])
            tmax = int(q.get("turns_max", [""])[0])
            every = int(q.get("retrain_every", ["0"])[0] or 0)
        except ValueError:
            return ("invalid launch: campaigns=%r turns_min=%r turns_max=%r retrain_every=%r "
                    "-- all must be integers, nothing was killed or started"
                    % (q.get("campaigns"), q.get("turns_min"), q.get("turns_max"),
                       q.get("retrain_every")))
        if not (1 <= campaigns <= 999 and 1 <= tmin <= tmax <= 999 and 0 <= every <= 999):
            return ("invalid launch: campaigns=%d turns_min=%d turns_max=%d retrain_every=%d "
                    "-- need 1 <= campaigns <= 999, 1 <= min <= max <= 999, 0 <= every <= 999, "
                    "nothing was killed or started" % (campaigns, tmin, tmax, every))
        turns = str(tmin) if tmin == tmax else "%d-%d" % (tmin, tmax)
        dev = (q.get("dev", ["0"])[0] not in ("0", ""))
        model, cfg, err = _trial_params(q)
        if err:
            return "invalid launch: %s" % err
        strategies, ruleset, err = _strategy_params(q)
        if err:
            return "invalid launch: %s" % err
        steps = [_kill_session(), _kill_recorder()]
        time.sleep(1.5)
        steps.append(_start_recorder(dev=dev))
        time.sleep(3.0)
        steps.append(_start_session(retrain=retrain, campaigns=campaigns, turns=turns,
                                    retrain_every=every, dev=dev, model=model, cfg=cfg,
                                    strategies=strategies, ruleset=ruleset))
        steps.append("give the recorder + session ~20s to appear in the tables above")
        return "\n".join(steps)
    return "unknown control: %s" % u.path


def _control(path):
    return ("<h1>control</h1><pre>%s</pre><p><a class=btn href='/'>back</a></p>"
            % _esc(_control_steps(path)))


def serve(run_dir, port=8777, follow=False):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            active = newest_run() if follow else run_dir
            u = urlparse(self.path)
            status, ctype = 200, "text/html; charset=utf-8"
            try:
                if u.path.startswith("/ctl/"):
                    if "ajax=1" in self.path:
                        body, ctype = _control_steps(self.path), "text/plain; charset=utf-8"
                    else:
                        body = _page(_control(self.path))
                elif u.path.startswith("/panel/"):
                    name = u.path[len("/panel/"):]
                    q = parse_qs(u.query or "")
                    con = _con(active)
                    try:
                        if name == "head":
                            body = render_head(con, active)
                        elif name in PANEL_MAP:
                            body = PANEL_MAP[name](con, active, q)
                        else:
                            status, body = 404, "<p class=bad>unknown panel: %s</p>" % _esc(name)
                    finally:
                        con.close()
                elif u.path.startswith("/d/"):
                    con = _con(active)
                    try:
                        body = render_decision(con, int(u.path[3:]))
                    finally:
                        con.close()
                elif u.path.startswith("/api/"):
                    con = _con(active)
                    try:
                        body, ctype = json.dumps(
                            {"summary": summary(con), "sequence": sequence(con)},
                            default=str), "application/json"
                    finally:
                        con.close()
                else:
                    con = _con(active)
                    try:
                        body = render_index(con, active)
                    finally:
                        con.close()
            except Exception:
                import traceback
                tb = traceback.format_exc()
                if u.path.startswith("/panel/"):
                    status, body = 500, "<pre class=bad>%s</pre>" % _esc(tb)
                else:
                    status, body = 500, _page("<h1>error</h1><pre>%s</pre>" % _esc(tb))
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    print("advisor v7 UI -> http://127.0.0.1:%d  (also http://localhost:%d)   run=%s"
          % (port, port, run_dir), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    explicit = len(sys.argv) > 1 and not sys.argv[1].isdigit()
    rd = sys.argv[1] if explicit else newest_run()
    pt = int(next((a for a in sys.argv[1:] if a.isdigit()), 8777))
    serve(rd, pt, follow=not explicit)
