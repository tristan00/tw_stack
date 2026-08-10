from __future__ import annotations

import glob
import html
import json
from urllib.parse import parse_qs, urlparse
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "advisor"))

RUNS_ROOT = "D:/twdata/runs/human"
SEQ_PAGE = 50
TIMELINE_ROWS = 200


def newest_run():
    return 'D:/twdata/runs/human/run'


def _con(run_dir):
    p = os.path.join(run_dir, "decisions.sqlite")
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def summary(con):
    q = lambda s: con.execute(s).fetchone()[0]
    counted = q("SELECT COUNT(*) FROM action_taken WHERE counted=1")
    taken = q("SELECT COUNT(*) FROM action_taken WHERE refusal IS NOT 'awaiting_execution'")
    latest = con.execute("SELECT campaign_id, turn, settlements, power_rank, lord_level"
                         " FROM target_rows ORDER BY turn DESC LIMIT 1").fetchone()
    out = {"turns": q("SELECT COUNT(*) FROM target_rows"),
           "decisions": q("SELECT COUNT(*) FROM decision_points"),
           "offers": q("SELECT COUNT(*) FROM action_offers"),
           "taken": taken, "counted": counted,
           "confirm_rate": (round(100.0 * counted / taken, 1) if taken else 0.0),
           "faction": "-", "turn_now": "-", "settlements": "-", "power_rank": "-",
           "lord_level": "-"}
    if latest:
        out.update(faction=latest[0] or "-", turn_now=latest[1], settlements=latest[2],
                   power_rank=latest[3], lord_level=latest[4])
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
    gnn = _optional_cols(con, "action_offers", ("gnn_impact", "gnn_rank"))
    return [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.decision_seq, d.n_entities, d.n_offers,"
        " t.context_kind, t.context_id, t.action_type, t.action_key, t.counted, t.refusal, t.policy,"
        " o.score, o.exploit, o.pct_global, o.pct_local, o.rank"
        + "".join(", o.%s" % c for c in gnn) +
        " FROM decision_points d LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " LEFT JOIN action_offers o ON o.rowid ="
        "   (SELECT MIN(o2.rowid) FROM action_offers o2 WHERE o2.decision_id=t.decision_id"
        "    AND o2.context_kind=t.context_kind AND o2.context_id=t.context_id"
        "    AND o2.action_type=t.action_type AND o2.action_key=t.action_key)"
        " ORDER BY d.decision_id DESC LIMIT ? OFFSET ?", (limit, offset))]


def sequence_total(con):
    try:
        return con.execute("SELECT COUNT(*) FROM decision_points").fetchone()[0]
    except Exception:
        return 0


def ranking(con, did, limit=80):
    taken = con.execute("SELECT context_kind,context_id,action_type,action_key,counted,refusal"
                        " FROM action_taken WHERE decision_id=?", (did,)).fetchone()
    rows = [dict(r) for r in con.execute(
        "SELECT context_kind,context_id,action_type,action_key,available,gate,exploit,"
        "pct_global,pct_local,rank"
        + "".join(",%s" % c for c in _optional_cols(con, "action_offers",
                                                    ("gnn_impact", "gnn_rank"))) +
        " FROM action_offers WHERE decision_id=?"
        " ORDER BY (exploit IS NULL), exploit DESC, available DESC LIMIT ?", (did, limit))]
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
        "SELECT d.decision_id, d.turn, d.ts, d.timings, t.context_kind, t.context_id, t.action_type,"
        " t.action_key, t.counted, t.refusal, t.latency_ms FROM decision_points d"
        " LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " ORDER BY d.decision_id DESC LIMIT ?", (TIMELINE_ROWS,))]
    rows.reverse()
    out, prev = [], None
    for r in rows:
        r["gap"] = (round(r["ts"] - prev, 1) if prev and r["ts"] else None)
        prev = r["ts"] or prev
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
    turns_ = {}
    for r in out:
        turns_.setdefault(r["turn"], []).append(r)
    return out, turns_


def run_history(con, runs_root=RUNS_ROOT):
    seen, rows = {}, []
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")), key=os.path.getmtime)
    for db in dbs:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            for camp, first_ts, last_ts, n_dec, max_turn in c.execute(
                    "SELECT campaign_id, MIN(ts), MAX(ts), COUNT(*), MAX(turn) FROM decision_points"
                    " GROUP BY campaign_id"):
                taken, counted = c.execute(
                    "SELECT COUNT(*), COALESCE(SUM(t.counted),0) FROM action_taken t"
                    " JOIN decision_points d ON d.decision_id=t.decision_id"
                    " WHERE d.campaign_id=? AND t.refusal IS NOT 'awaiting_execution'",
                    (camp,)).fetchone()
                reward = c.execute(
                    "SELECT turn, income, settlements, power_rank FROM target_rows"
                    " WHERE campaign_id=? ORDER BY turn DESC LIMIT 1", (camp,)).fetchone()
                row = {"campaign": camp, "turns": int(max_turn or 0), "decisions": n_dec,
                       "taken": taken or 0, "counted": counted or 0, "first_ts": first_ts or 0,
                       "minutes": round(((last_ts or 0) - (first_ts or 0)) / 60.0, 1),
                       "confirm_pct": round(100.0 * (counted or 0) / (taken or 1), 1),
                       "run": os.path.basename(os.path.dirname(db)),
                       "last_income": (reward[1] if reward else None),
                       "last_settlements": (reward[2] if reward else None),
                       "last_power_rank": (reward[3] if reward else None)}
                if camp in seen:
                    if row["decisions"] > seen[camp]["decisions"]:
                        seen[camp] = row
                else:
                    seen[camp] = row
        except sqlite3.Error:
            pass
        finally:
            c.close()
    rows = sorted(seen.values(), key=lambda r: r["first_ts"])
    return rows


def by_action_type(con):
    return [dict(r) for r in con.execute(
        "SELECT action_type, COUNT(*) n, SUM(counted) ok,"
        " GROUP_CONCAT(DISTINCT refusal) refusals FROM action_taken"
        " WHERE refusal IS NOT 'awaiting_execution' GROUP BY action_type ORDER BY n DESC")]


def turns(con):
    return [dict(r) for r in con.execute(
        "SELECT turn,income,settlements,allies,vassals,power_rank FROM target_rows ORDER BY turn")]


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
"""


def _page(body, title="advisor v7"):
    return ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<style>%s</style><div class=wrap>%s</div>"
            % (html.escape(title), _CSS, body))


def _esc(v):
    return html.escape("" if v is None else str(v))


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
    return ("<h2>%s</h2>"
            "<p class=muted>ruleset(rule) picks are aggregated under <b>ruleset</b> with the "
            "per-rule split alongside. <b>forced_end_turn</b> is written by the loop when it "
            "runs out of actions, so it is listed but kept out of the share denominator. "
            "<b>gnn_delegated_*</b> is a gnn draw on the interrupt path, where there is no "
            "gnn model, handed to exploit_tree. Older campaigns carry retired epsilon-era "
            "strings (cold_random, epsilon_random, explore, exploit, interrupt_exploit, "
            "interrupt_explore) &mdash; the novelty score that <i>explore</i> named has since "
            "been deleted, so it can only appear on pre-retirement rows.</p>"
            "<div class=scroll><table><tr><th>policy<th class=num>picks<th>share</tr>"
            "%s</table></div>" % (_esc(title), "".join(rows)))


def render_interrupts(runs_root=RUNS_ROOT):
    import collections
    per_screen = collections.OrderedDict()
    chosen = collections.Counter()
    offered = collections.Counter()
    policies = collections.Counter()
    total = 0
    for db in sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")),
                     key=os.path.getmtime):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
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
    for db in sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")),
                     key=os.path.getmtime, reverse=True)[:3]:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
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
        return s if len(s) <= n else "…" + s[-(n - 1):]

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
    for db in sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")),
                     key=os.path.getmtime):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
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
                    "SELECT MAX(settlements), MIN(power_rank), MAX(lord_level), MAX(vassals)"
                    " FROM target_rows WHERE campaign_id=?", (camp,)).fetchone() or (None,) * 4
                row = {"faction": faction, "turns": int(max_turn or 0), "decisions": n_dec,
                       "taken": taken or 0, "counted": counted or 0,
                       "settlements": best[0], "power_rank": best[1],
                       "lord_level": best[2], "vassals": best[3]}
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
                                          "power_rank": None, "lord_level": None, "vassals": None})
        a["n"] += 1
        a["turns"] += r["turns"]
        a["taken"] += r["taken"]
        a["counted"] += r["counted"]
        a["best_turns"] = max(a["best_turns"], r["turns"])
        for k, better in (("settlements", max), ("lord_level", max), ("vassals", max),
                          ("power_rank", min)):
            v = r[k]
            if v is not None:
                a[k] = v if a[k] is None else better(a[k], v)
    out = []
    for a in agg.values():
        a["avg_turns"] = round(a["turns"] / a["n"], 1) if a["n"] else 0.0
        a["confirm_pct"] = round(100.0 * a["counted"] / (a["taken"] or 1), 1)
        out.append(a)
    return sorted(out, key=lambda a: (-a["n"], -a["avg_turns"]))


def faction_action_stats(runs_root=RUNS_ROOT):
    import collections
    main = collections.defaultdict(lambda: [0, 0, 0.0])
    inter = collections.defaultdict(lambda: [0, 0, 0.0])
    for db in sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")),
                     key=os.path.getmtime):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
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
        if ch == "outgoing":
            who = d.get("faction")
            ans = "sent" if (d.get("panel") or {}).get("sent") else \
                  "refused(%s)" % ((d.get("panel") or {}).get("failed_at"))
            ok = bool((d.get("panel") or {}).get("sent"))
        else:
            who = d.get("proposer") or ",".join(d.get("faction_keys") or [])[:40]
            ans = d.get("answer")
            ok = bool(d.get("confirmed")) and ans in ("accept", "acknowledge", "join")
        cls = "ok" if ok else ("warn" if ans == "decline" else "bad")
        tr.append("<tr><td class=dim>%s</td><td>%s</td><td>%s</td><td class=%s>%s</td>"
                  "<td>%s</td><td class=dim>%s</td></tr>"
                  % (_esc(d.get("turn")), _esc(ch), _esc(str(who)[:38]), cls, _esc(ans),
                     _esc(d.get("attitude") or "&mdash;"),
                     _esc(",".join(d.get("terms") or []) if ch == "outgoing" else
                          str(d.get("speech") or "")[:60])))
    return ("<h2>diplomacy stream <span class=dim>(last %d rows of this run)</span></h2>"
            "<div class=cards>%s</div>"
            "<h2>recent deal events</h2><div class=scroll><table>"
            "<tr><th>turn<th>channel<th>faction<th>answer<th>attitude<th>terms / speech</tr>"
            "%s</table></div>"
            % (len(rows), cards,
               "".join(tr) or "<tr><td class=dim colspan=6>no deal rows yet</td></tr>"))


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
        card("mix", _esc((_mix_str(st["mix"]) or "-")[:48])),
        card("ruleset", _esc(_live_ruleset(st["ruleset"]))),
    ] + [
        card(k, "&mdash;" if (st["rate"] or {}).get(v) is None
             else "<span class=ok>%s</span>" % st["rate"][v])
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
    for line in lines[-limit:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rows.append(r)
    if not rows:
        return ""
    tr = []
    for r in reversed(rows):
        pl = r.get("plausibility") or {}
        verdict = pl.get("verdict") or "-"
        cls = ("ok" if verdict.startswith("consistent") else
               "bad" if ("SUSPICIOUS" in verdict or "MISLABELED" in verdict) else
               "warn" if verdict.startswith("harness") else "dim")
        traj = r.get("trajectory") or []
        tline = " ".join("%s:%s" % (t.get("turn"), t.get("settlements")) for t in traj[-4:])
        tr.append("<tr><td class=dim>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                  "<td class=%s>%s</td><td class=dim>%s</td><td class=dim>%s</td></tr>"
                  % (_esc(r.get("when", "")[-8:]), _esc(str(r.get("faction"))[:30]),
                     _esc(r.get("outcome")), _esc(r.get("turns_played")),
                     cls, _esc(verdict[:60]),
                     _esc("; ".join((pl.get("evidence") or [])[:2])[:70]),
                     _esc(tline)))
    return ("<h2>campaign endings &mdash; was it a real defeat?</h2>"
            "<p class=muted>Verdicts are advisory: they argue from the trajectory, the engine "
            "death row, and final-turn battles. turn:settlements shows the death spiral or the "
            "healthy line that just stopped.</p>"
            "<div class=scroll><table><tr><th>when<th>faction<th>outcome<th>turns"
            "<th>verdict<th>evidence<th>turn:settlements</tr>%s</table></div>" % "".join(tr))


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
        tr.append("<tr><td>%s</td><td class=num>%d</td><td class=num>%.1f</td>"
                  "<td class=num>%d</td><td class=num>%s</td><td class=num>%s</td>"
                  "<td class=num>%s</td><td class=num>%s</td><td class=num>%.1f%%</td></tr>"
                  % (_esc(a["faction"]), a["n"], a["avg_turns"], a["best_turns"],
                     _c(a["settlements"], "%g"), _c(a["power_rank"], "%g"),
                     _c(a["lord_level"], "%g"), _c(a["vassals"], "%g"), a["confirm_pct"]))
    return ("<h2>starts <span class=dim>(%d lords, %d campaigns)</span></h2>"
            "<p class=muted>Most-played first. <b>Best</b> columns are the peak that start ever "
            "reached across all its campaigns &mdash; power rank counts downwards, so lower is "
            "better.</p>"
            "<div class=scroll><table><tr><th>lord / faction<th>campaigns<th>avg turns"
            "<th>best turns<th>best settlements<th>best power rank<th>best lord level"
            "<th>best vassals<th>confirmed</tr>%s</table></div>"
            % (len(rows), sum(a["n"] for a in rows), "".join(tr)))


def render_head(con, run_dir):
    s = summary(con)
    cards = "".join("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                    % (k, v) for k, v in
                    (("faction", s["faction"]), ("lord level", s["lord_level"]),
                     ("turn", s["turn_now"]), ("settlements", s["settlements"]),
                     ("power rank", s["power_rank"]),
                     ("turns", s["turns"]), ("decisions", s["decisions"]), ("offers", s["offers"]),
                     ("taken", s["taken"]), ("confirmed", s["counted"]),
                     ("confirm %", s["confirm_rate"])))
    return ("<h1>advisor v7</h1><div class=dim>%s</div><div class=cards>%s</div>"
            % (_esc(run_dir), cards))


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
        "who is making the picks &mdash; every session recorded in this run dir (%s campaigns)"
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
    for r in sequence(con, SEQ_PAGE, seq_offset):
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
        decider = str(r["policy"] or "").split("(")[0]
        gi = r.get("gnn_impact")
        # highlight the numbers the deciding strategy actually used; the rest are stored
        # for every offer regardless of who picked, so they stay legible but dim
        cb = "num" if decider == "exploit_tree" else "num dim"
        gn = "num" if decider == "gnn" else "num dim"
        seq.append("<tr><td><a href='/d/%d'>#%d</a></td><td>%s</td><td>%s</td>"
                   "<td class=gsep>%s:%s</td><td>%s</td><td>%s</td>"
                   "<td class='%s gsep'>%s</td><td class=dim style='white-space:normal'>%s</td>"
                   "<td class='%s gsep'>%s</td><td class='%s'>%s</td><td class='%s'>%s</td>"
                   "<td class='%s gsep'>%s</td><td class='%s'>%s</td>"
                   "<td class=gsep>%s</td></tr>"
                   % (r["decision_id"], r["decision_id"], _esc(r["turn"]), r["n_offers"],
                      _esc(r["context_kind"]), _esc(str(r["context_id"])[:26]),
                      _esc(r["action_type"]), _esc(str(r["action_key"])[:38]), cls, mark,
                      _esc(r["refusal"]),
                      cb, _num(r.get("exploit")), cb, _num(r.get("pct_global")),
                      cb, (_num(r.get("pct_local")) if r.get("pct_local") is not None
                           else "<span class=dim>n/a</span>"),
                      gn, ("%+0.4f" % float(gi)) if gi is not None else "-",
                      gn, _esc(r.get("gnn_rank") if r.get("gnn_rank") is not None else "-"),
                      pol))
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
        seq = ["<tr><td colspan=14 class=dim>no decisions recorded yet</tr>"]
    seqtbl = ("<h2>every decision, newest first</h2>"
              "<div class=legend>one row per decision point. The scores are grouped by which "
              "model produced them, and <b>the group belonging to the strategy that actually "
              "picked is shown bright</b> &mdash; the dim one was computed but not used. "
              "catboost scores every offer on every decision, so its columns are always "
              "filled; the gnn only scores when it is the strategy drawn, so its columns are "
              "blank otherwise. <b>ruleset</b> and <b>random</b> picks rank on no score at "
              "all &mdash; the rule that fired is named in <b>picked by</b>, and "
              "<span class=warn>*_random_fallback</span> means that strategy was drawn but "
              "could not pick. Click a <b>#</b> for the full ranking behind a decision.</div>"
              + _pager +
              "<div class=scroll><table>"
              "<tr><th colspan=3>decision<th class=grp colspan=3>what it chose"
              "<th class=grp colspan=2>outcome"
              "<th class=grp colspan=3 title='E1-E2 impact percentiles; stored for every "
              "offer whichever strategy picked'>catboost"
              "<th class=grp colspan=2 title='twin-head Q minus V; recorded only on decisions "
              "the gnn was drawn for'>gnn"
              "<th class=grp>picked by</tr>"
              "<tr><th>#<th>turn<th>offers<th class=gsep>entity<th>action<th>key"
              "<th class=gsep>result<th>refusal"
              "<th class='gsep num'>exploit<th class=num>global<th class=num>local"
              "<th class='gsep num'>Q&minus;V<th class=num>rank"
              "<th class=gsep>strategy</tr>"
              "%s</table></div>" % "".join(seq)) + _pager
    return seqtbl


def render_reward(con):
    trows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_esc(t["turn"]), _esc(t["income"]), _esc(t["settlements"]),
                       _esc(t["allies"]), _esc(t["vassals"]), _esc(t["power_rank"]))
                    for t in turns(con))
    return ("<h2>reward inputs per turn</h2><div class=scroll><table>"
            "<tr><th>turn<th>income<th>settlements<th>allies<th>vassals<th>power rank</tr>%s"
            "</table></div>" % (trows or "<tr><td class=dim colspan=6>no turns recorded</td></tr>"))


PANELS = (
    ("live", "live", lambda con, run, q: render_live(run)),
    ("overview", "overview",
     lambda con, run, q: render_endings() + render_leaders(con) + render_history(con)),
    ("starts", "starts", lambda con, run, q: render_starts()),
    ("matrix", "action x faction", lambda con, run, q: render_faction_matrix()),
    ("interrupts", "blocking menus", lambda con, run, q: render_interrupts()),
    ("diplomacy", "diplomacy", lambda con, run, q: render_diplomacy(run)),
    ("timing", "timing", lambda con, run, q: render_timing(run)),
    ("actions", "actions", lambda con, run, q: render_actions(con, q)),
    ("decisions", "decision log", lambda con, run, q: render_decisions(con, q)),
    ("timeline", "timeline", lambda con, run, q: render_timeline(con)),
    ("reward", "reward", lambda con, run, q: render_reward(con)),
    ("models", "models", lambda con, run, q: render_models()),
    ("training", "training", lambda con, run, q: render_training()),
    ("infra", "infrastructure", lambda con, run, q: render_infra(run)),
)

PANEL_MAP = {s: f for s, _t, f in PANELS}


def render_index(con, run_dir):
    nav = "".join("<button class=tab data-t='%s'>%s</button>" % (s, _esc(t))
                  for s, t, _f in PANELS)
    bodies = "".join("<div class=panel id='p-%s' hidden><p class=dim>loading&hellip;</p></div>" % s
                     for s, _t, _f in PANELS)
    return _page("<div id=head>%s</div><div class=tabs>%s</div>%s%s"
                 % (render_head(con, run_dir), nav, bodies,
                    _TABS_JS % json.dumps([s for s, _t, _f in PANELS])))


def render_leaders(con):
    rows = list(con.execute(
        "SELECT d.campaign_id, COUNT(*) AS decisions,"
        " COALESCE(SUM(t.counted),0) AS counted,"
        " SUM(CASE WHEN t.refusal IS NOT 'awaiting_execution' THEN 1 ELSE 0 END) AS taken,"
        " MAX(d.turn) AS max_turn"
        " FROM decision_points d LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " GROUP BY d.campaign_id ORDER BY decisions DESC"))
    if not rows:
        return "<h2>by legendary lord</h2><div class=dim>no campaigns recorded yet</div>"
    best = {}
    for camp, turn, setl, rank, lvl in con.execute(
            "SELECT campaign_id, MAX(turn), MAX(settlements), MIN(power_rank), MAX(lord_level)"
            " FROM target_rows GROUP BY campaign_id"):
        best[camp] = (turn, setl, rank, lvl)
    out = []
    for camp, dec, counted, taken, max_turn in rows:
        b = best.get(camp, (None, None, None, None))
        pct = (100.0 * (counted or 0) / taken) if taken else 0.0
        cls = "ok" if pct >= 70 else ("warn" if pct >= 40 else "bad")
        out.append("<tr><td>%s<td>%s<td>%s<td class=%s>%.0f%%<td>%s<td>%s<td>%s</tr>"
                   % (_esc(camp), _esc(max_turn), _esc(dec), cls, pct,
                      _esc(b[1]), _esc(b[2]), _esc(b[3])))
    return ("<h2>by legendary lord &mdash; per-start performance</h2><div class=scroll><table>"
            "<tr><th>faction / lord<th>turns<th>decisions<th>confirm %%"
            "<th>best settlements<th>best power rank<th>best lord level</tr>%s</table></div>"
            % "".join(out))


_TABS_JS = """<script>
(function(){
  var slugs=%s, key='v7tab', cur=sessionStorage.getItem(key), pq={};
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
  }
  function editing(el){
    var a=document.activeElement;
    return a&&el.contains(a)&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
  }
  function load(name){
    var el=document.getElementById('p-'+name);
    if(editing(el))return;
    fetch('/panel/'+name+(pq[name]?'?'+pq[name]:''),{cache:'no-store'})
      .then(function(r){return r.text()})
      .then(function(t){swap(el,t);})
      .catch(function(e){el.innerHTML='<p class=bad>panel '+name+' fetch failed: '+e+'</p>';});
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
  setInterval(function(){load(cur);},10000);
})();
</script>"""


def render_history(con):
    runs = run_history(con)
    if not runs:
        return ""
    mx = max(r["turns"] for r in runs) or 1
    mxd = max(r["decisions"] for r in runs) or 1
    bars = []
    for i, r in enumerate(runs, 1):
        w = max(2, int(100.0 * r["turns"] / mx))
        wd = max(2, int(100.0 * r["decisions"] / mxd))
        cls = "bad" if r["turns"] <= 1 else ("warn" if r["turns"] < 0.4 * mx else "ok")
        bars.append(
            "<tr><td>%d</td><td class=dim title='%s'>%s</td>"
            "<td class=barcell><div class='bar2 %s' style='width:%d%%'></div>"
            "<span class=blabel>%d</span></td>"
            "<td class=barcell><div class='bar2 dimbar' style='width:%d%%'></div>"
            "<span class=blabel>%d</span></td>"
            "<td>%s</td><td>%s%%</td><td class=dim>%s min</td>"
            "<td class=dim>%s</td><td class=dim>%s</td><td class=dim>%s</td></tr>"
            % (i, _esc(r["campaign"]), _esc(str(r["campaign"])[-14:]),
               cls, w, r["turns"], wd, r["decisions"],
               r["counted"], r["confirm_pct"], r["minutes"],
               _esc(r["last_settlements"]), _esc(r["last_income"]), _esc(r.get("run"))))
    return ("<h2>run history &mdash; how far each campaign got</h2>"
            "<div class=scroll><table>"
            "<tr><th>#<th>campaign<th title='max turn reached'>turns survived"
            "<th title='decision points recorded'>decisions<th>confirmed<th>rate"
            "<th>wall<th>settlements<th>income<th>run dir</tr>%s</table></div>" % "".join(bars))


def render_timeline(con):
    _rows, per_turn = timeline(con)
    if not per_turn:
        return ""
    scale = 1 / 40.0
    phases = (("collect_ms", "p1", "recorder reading the game"),
              ("queue_ms", "p2", "request round trip"),
              ("score_ms", "p3", "featurize + rank (with gnn drawn: graph build + forward)"),
              ("verify_ms", "p4", "execute + confirm"))
    out = []
    for turn in sorted(per_turn, key=lambda t: (t is None, t)):
        items = per_turn[turn]
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
        wall = (" &nbsp;%.0fs wall" % sum(span)) if span else ""
        out.append("<div class=lanehead>turn %s<span class=dim> &nbsp;%d/%d confirmed%s</span></div>"
                   "<div class=scroll><table><tr><th>#<th>action<th>key<th>result"
                   "<th>phases<th>collect<th>queue<th>score<th>verify<th>total"
                   "<th title='wall clock since the previous action'>gap"
                   "<th title='gap minus the measured phases -- time nothing accounts for'>"
                   "unaccounted</tr>%s</table></div>"
                   % (_esc(turn), ok, len(items), wall, "".join(lines)))
    legend = " ".join("<span class='seg %s'></span> %s" % (css, lbl) for _k, css, lbl in phases)
    return ("<h2>timeline &mdash; every action, phase by phase (ms)</h2>"
            "<div class=legend>%s</div>%s" % (legend, "".join(out)))


def render_decision(con, did):
    d = ranking(con, did)
    tk = d["taken"]
    head = ("<h1><a href='/'>&larr;</a> decision #%d</h1>"
            "<div class=dim>%d offers across %d entities &mdash; taken: %s</div>"
            % (did, d["n_offers"], len(d["entities"]),
               _esc("%s %s (%s)" % (tk["action_type"], tk["action_key"],
                                    "confirmed" if tk["counted"] else (tk["refusal"] or "?")))
               if tk else "nothing"))
    rows = []
    for o in d["offers"]:
        cls = "take" if o["taken"] else ""
        avail = "<span class=ok>yes</span>" if o["available"] else "<span class=dim>no</span>"
        fmt = lambda v: ("%.4f" % v) if isinstance(v, float) else ("" if v is None else str(v))
        pl = o.get("pct_local")
        pl_cell = fmt(pl) if pl is not None else "<span class=dim>n/a</span>"
        gi = o.get("gnn_impact")
        rows.append("<tr class='%s'><td>%s</td><td>%s:%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td>"
                    "<td class=gsep>%s</td><td>%s</td>"
                    "<td class=dim>%s</td></tr>"
                    % (cls, _esc(o["rank"]), _esc(o["context_kind"]),
                       _esc(str(o["context_id"])[:22]), _esc(o["action_type"]),
                       _esc(str(o["action_key"])[:44]), avail,
                       fmt(o["exploit"]), fmt(o.get("pct_global")), pl_cell,
                       ("%+0.4f" % float(gi)) if gi is not None else "<span class=dim>-</span>",
                       _esc(o.get("gnn_rank") if o.get("gnn_rank") is not None else "-"),
                       _esc(o["gate"])))
    tbl = ("<h2>the ranking it produced over the whole faction</h2>"
           "<div class=legend>catboost ranks every offer each decision; the gnn columns are "
           "filled only on decisions the gnn was drawn for, since that is when it scores.</div>"
           "<div class=scroll><table>"
           "<tr><th colspan=5>offer<th class=grp colspan=3>catboost"
           "<th class=grp colspan=2>gnn<th class=grp>&nbsp;</tr>"
           "<tr><th>rank<th>entity<th>action<th>key<th>available"
           "<th class=gsep>exploit<th>global<th>local"
           "<th class=gsep>Q&minus;V<th>rank<th class=gsep>gate</tr>"
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


TW_STACK = r"D:\tw_stack"
VENV_PY = r"D:\totalwar_runner\.venv\Scripts\python.exe"
LOG_DIR = r"D:\twdata\logs\advisor"
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
    return "%s; %s" % (killed, _bank_trials())


def _bank_trials():
    import session as S
    try:
        rows = S.rescore(log=lambda m: None)
    except Exception as e:
        return "!! TRIALS NOT BANKED: %s" % repr(e)[:140]
    return "trials banked: %s" % ", ".join(r["trial"] for r in rows)


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


SERVICES_LOG_DIR = r"D:\twdata\logs\services"


def _trial_row_html(r, live=False):
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
    return ("<tr><td%s>%s<td>%s<td class=dim>%s<td>%s<td class=dim>%s<td>%s<td class=dim>%s<td%s>%s"
            "<td>%s<td>%s<td>%s<td>%s<td>%s<td>%s<td class=dim>%s</tr>"
            % (" class=warn" if live else "", _esc(r.get("trial", "?")),
               _esc(str(r.get("backend") or "-")),
               _esc(", ".join("%s=%s" % kv for kv in sorted(cfg.items())) or "-"),
               _esc(_mix_str(r.get("strategies"), r.get("epsilon")) or "-"),
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
    out = ([_trial_row_html(live, live=True)] if live else []) \
        + [_trial_row_html(r) for r in rows]
    return ("<h2>experiment ledger</h2>%s<div class=scroll><table>"
            "<tr><th>trial<th>backend<th>cfg"
            "<th title='per-decision strategy mix; legacy trials that only recorded --epsilon "
            "show epsilon=E'>mix"
            "<th title='rule set name@sha256 prefix'>ruleset"
            "<th>campaigns<th>corpus<th>sett/camp<th>sett total"
            "<th>grew<th title='legendary lord levels gained per campaign'>lord lvl/camp"
            "<th>turns/camp<th>s/camp<th>s/turn<th>notes</tr>%s</table></div>"
            % (note, "".join(out)))


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


MODEL_DIRS = (("global", r"D:\twdata\models\global"),
              ("local", r"D:\twdata\models\local"),
              ("interrupt", r"D:\twdata\models\interrupt"),
              ("gnn", r"D:\twdata\models\gnn"))

_MODEL_ROLE = {
    "global": "catboost E1/E2 &mdash; E2 is the counterfactual baseline and E1&minus;E2 is the "
              "impact the <b>exploit_tree</b> strategy ranks on. The other three strategies "
              "ignore it, but its scores are still stored on every offer row",
    "local": "catboost &mdash; per-entity local value, blended into the global score",
    "interrupt": "catboost &mdash; ranks the options on blocking menus (battles, occupation)",
    "gnn": "GINE graph net &mdash; twin Q/V heads score offers over the "
           "province/region/settlement/character/faction graph",
}


def _live_gnn_schema():
    try:
        if r"D:\tw_stack" not in sys.path:
            sys.path.insert(0, r"D:\tw_stack")
        from mapgraph import schema as GS
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
    rows = [
        _mrow("held-out rmse", _fmt(fit.get("val_rmse_raw"), 4)),
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


def _fit_config_table():
    body = []
    try:
        BM = _live_base_model()
        body.append(
            "<tr><td>catboost<td>global / local / interrupt<td>lr %s &middot; "
            "early stop %s &middot; cap %s iters &middot; depth %s &middot; %s &middot; %s "
            "&middot; loss %s &middot; holdout %.0f%% of campaigns"
            "<br><span class=dim>tuned from %s</span>"
            "<td class=dim>cpu &mdash; measured faster than gpu on this corpus "
            "(14.6s vs 21.6s)</tr>"
            % (BM.CB_LEARNING_RATE, BM.CB_EARLY_STOPPING, BM.CB_ITERATIONS, BM.CB_DEPTH,
               _esc(str(BM.CB_PARAMS.get("grow_policy"))),
               _esc(str(BM.CB_PARAMS.get("bootstrap_type"))),
               BM.CB_LOSS, 100 * BM.VAL_FRACTION, _esc(str(BM.CB_TUNED_FROM))))
    except Exception as e:
        body.append("<tr><td>catboost<td colspan=3 class=bad>config unreadable: %s</tr>"
                    % _esc(repr(e)[:90]))
    try:
        if r"D:\tw_stack" not in sys.path:
            sys.path.insert(0, r"D:\tw_stack")
        from mapgraph import train as _GT
        c = _GT.CFG
        body.append(
            "<tr><td>gnn<td>graph offer-scorer<td>GINE hidden %s &middot; lr %s &middot; "
            "weight decay %s &middot; batch %s graphs &middot; &le;%s epochs "
            "(patience %s) &middot; aux weight %s &middot; budget %ss &middot; "
            "campaign-grouped holdout"
            "<td class=dim>%s &mdash; corpus collated once and held resident; "
            "%s train threads, %s infer</tr>"
            % tuple(_esc(str(v)) for v in
                    (c["hidden"], c["lr"], c["weight_decay"], c["batch"], c["epochs"],
                     c["patience"], c["aux_weight"], c["time_budget_s"],
                     c.get("device", "auto"), _GT.THREADS_TRAIN, 2)))
    except Exception as e:
        body.append("<tr><td>gnn<td colspan=3 class=bad>config unreadable: %s</tr>"
                    % _esc(repr(e)[:90]))
    return ("<h2>fit configuration</h2><div class=scroll><table>"
            "<tr><th>family<th>role<th>hyperparameters<th>compute</tr>%s</table></div>"
            % "".join(body))


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
            % (banner, "".join(cards), _fit_config_table()))


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
                         % (_fmt(gn_fit.get("val_rmse_raw"), 4),
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
               _esc(str(p.get("sett_total") if p.get("sett_total") is not None else "-")),
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
            "<th class=gsep>rmse<th>rows<th>device"
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
