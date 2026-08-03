r"""python advisor_ui/ui.py [run_dir] [port]      # default: newest run, :8777"""
from __future__ import annotations

import glob
import html
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "advisor"))

RUNS_ROOT = "D:/twdata/runs/human"


def newest_run():
    try:
        with open(os.path.join(RUNS_ROOT, "CURRENT_RUN"), encoding="utf-8") as f:
            d = f.read().strip()
        if d and os.path.isfile(os.path.join(d, "decisions.sqlite")):
            return d
    except OSError:
        pass
    dbs = sorted(glob.glob(os.path.join(RUNS_ROOT, "*", "decisions.sqlite")),
                 key=os.path.getmtime, reverse=True)
    if not dbs:
        raise SystemExit("no decisions.sqlite under %s -- run the manager first" % RUNS_ROOT)
    return os.path.dirname(dbs[0])


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
    """"-" for None, else v to nd decimals."""
    return "-" if v is None else ("%%.%df" % nd) % float(v)


def sequence(con, limit=300):
    return [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.decision_seq, d.n_entities, d.n_offers,"
        " t.context_kind, t.context_id, t.action_type, t.action_key, t.counted, t.refusal, t.policy,"
        " o.score, o.exploit, o.explore, o.pct_global, o.pct_local, o.rank"
        " FROM decision_points d LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " LEFT JOIN action_offers o ON o.decision_id=t.decision_id"
        "   AND o.context_kind=t.context_kind AND o.context_id=t.context_id"
        "   AND o.action_type=t.action_type AND o.action_key=t.action_key"
        " ORDER BY d.decision_id DESC LIMIT ?", (limit,))]


def ranking(con, did, limit=80):
    """Offers for one decision point, each flagged with whether it was the one taken."""
    taken = con.execute("SELECT context_kind,context_id,action_type,action_key,counted,refusal"
                        " FROM action_taken WHERE decision_id=?", (did,)).fetchone()
    rows = [dict(r) for r in con.execute(
        "SELECT context_kind,context_id,action_type,action_key,available,gate,score,exploit,explore,"
        "pct_global,pct_local,rank"
        " FROM action_offers WHERE decision_id=?"
        " ORDER BY (score IS NULL), score DESC, available DESC LIMIT ?", (did, limit))]
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
    """(rows, rows_by_turn) -- decisions in order with per-phase timings and the gap before each."""
    rows = [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.ts, d.timings, t.context_kind, t.context_id, t.action_type,"
        " t.action_key, t.counted, t.refusal, t.latency_ms FROM decision_points d"
        " LEFT JOIN action_taken t ON t.decision_id=d.decision_id ORDER BY d.decision_id")]
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
    """Every campaign across every run dir under runs_root, oldest first, deduped by campaign_id."""
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
.wrap{max-width:1180px;margin:0 auto;padding:20px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:104px}
.card .v{font-size:20px}.card .k{color:var(--dim);font-size:11px;text-transform:uppercase}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;white-space:nowrap}
th,td{padding:5px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;position:sticky;top:0;background:var(--card)}
tr:last-child td{border-bottom:none}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.dim{color:var(--dim)}
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
"""


def _page(body, title="advisor v7"):
    return ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<meta http-equiv=refresh content=10>"
            "<style>%s</style><div class=wrap>%s</div>"
            % (html.escape(title), _CSS, body))


def _esc(v):
    return html.escape("" if v is None else str(v))


def render_interrupts(runs_root=RUNS_ROOT):
    import collections
    per_screen = collections.OrderedDict()
    chosen = collections.Counter()
    offered = collections.Counter()
    total = 0
    for db in sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")),
                     key=os.path.getmtime):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5.0)
        except sqlite3.Error:
            continue
        try:
            for kind, opts_json, pick in c.execute(
                    "SELECT kind, options_json, chosen FROM interrupt_decisions"):
                total += 1
                per_screen[kind] = per_screen.get(kind, 0) + 1
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
        r = IM.InterruptRanker()
        if r.ready:
            sr = (r.meta or {}).get("screen_rows") or {}
            per = ", ".join("%s %s(%s)" % (s, "hot" if sr.get(s, 0) >= IM.MIN_ROWS
                                           else "cold", sr.get(s, "?"))
                            for s in sorted((r.meta or {}).get("screens") or []))
            state = ("<b>model</b> &mdash; fitted on %s rows; per-screen gate (hot &ge; %d rows): %s"
                     % ((r.meta or {}).get("rows", "?"), IM.MIN_ROWS, per or "&mdash;"))
        else:
            state = ("<b>cold_random</b> &mdash; no interrupt model fitted yet (needs %d labelled "
                     "rows per screen; a decision is only labelled once its campaign plays on "
                     "past it)" % IM.MIN_ROWS)
    except Exception as e:
        state = "unavailable: %s" % _esc(repr(e)[:120])

    if not total:
        return ("<h2>blocking menus</h2><p class=muted>policy: %s</p>"
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
                    " campaign_json FROM interrupt_decisions ORDER BY interrupt_id DESC LIMIT 40"
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
    rec_rows = []
    for ts, kind, opts, pick, counted, ref, lat, p, fac in recent[:40]:
        cells = []
        for k in sorted(opts, key=lambda k: -(opts[k] or {}).get("score") or 0
                        if isinstance((opts[k] or {}).get("score"), float) else 0):
            sc = (opts[k] or {}).get("score") if isinstance(opts[k], dict) else None
            label = "%s=%s" % (_esc(k.replace("button_", "").replace("captive_option_", "")),
                               ("%.3f" % sc) if isinstance(sc, (int, float)) else "-")
            cells.append("<b>%s</b>" % label if k == pick else label)
        res = ("<span class=ok>OK</span>" if counted
               else "<span class=bad>%s</span>" % _esc(ref or "fail"))
        rec_rows.append(
            "<tr><td class=dim>%s</td><td>%s</td><td class=dim>%s</td><td>%s</td>"
            "<td>%s</td><td class=dim>%s</td><td class=dim>%s</td></tr>"
            % (time.strftime("%H:%M:%S", time.localtime(ts or 0)), _esc(kind),
               _esc((fac or "?")[:26]), res,
               ", ".join(cells) or "<span class=dim>-</span>",
               _esc(p or "-"), ("%.1fs" % (lat / 1000.0)) if lat else "-"))
    recent_tbl = ("<h2>recent interrupt decisions &mdash; predicted values per option "
                  "(chosen in bold)</h2><div class=scroll><table>"
                  "<tr><th>time<th>screen<th>faction<th>result<th>options=score<th>policy"
                  "<th>latency</tr>%s</table></div>"
                  % ("".join(rec_rows) or "<tr><td class=dim colspan=7>none recorded</td></tr>"))

    rows = []
    for (kind, opt), n in sorted(chosen.items(), key=lambda kv: (kv[0][0], -kv[1])):
        seen = offered.get((kind, opt), 0)
        rate = (100.0 * n / seen) if seen else None
        rows.append("<tr><td>%s</td><td>%s</td><td class=num>%d</td><td class=num>%d</td>"
                    "<td class=num>%s</td></tr>"
                    % (_esc(kind), _esc(opt), n, seen,
                       "&mdash;" if rate is None else "%.0f%%" % rate))
    screens = " &middot; ".join("%s <b>%d</b>" % (_esc(k), v) for k, v in per_screen.items())
    head = ("<h2>blocking menus <span class=dim>(%d decisions)</span></h2>"
            "<p class=muted>policy: %s</p>"
            "<p class=muted>%s</p>" % (total, state, screens))
    agg = ("<p class=muted>Dilemmas, pre-battle, post-battle and occupation. <b>taken</b> is how "
           "often we picked that option; <b>offered</b> is how often the screen showed it, so the "
           "rate exposes whether a choice is genuinely being explored or never gets picked.</p>"
           "<div class=scroll><table><tr><th>screen<th>option<th>taken<th>offered<th>rate</tr>"
           "%s</table></div>" % "".join(rows))
    return head + recent_tbl + agg


def starts_summary(runs_root=RUNS_ROOT):
    """Per-faction aggregate over every campaign in every run dir, most-played first."""
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
                # power_rank is best at its minimum, hence MIN here and MAX for the rest
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
    """{(faction, action_type): [tried, ok, seconds]} for main picks and interrupt screens."""
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
    """Last n parsed rows of a jsonl; [] when absent. Read-only, error-tolerant."""
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh.readlines()[-n:]:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows


def render_diplomacy(run_dir):
    """The diplomacy analysis stream: deals, answers, checkpoints (diplomacy.jsonl)."""
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
    """Decision-cycle timing: collect waves (recorder profile) + advisor-side pick timings."""
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


def render_endings(runs_root=RUNS_ROOT, limit=20):
    """Campaign endings with their plausibility verdicts."""
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


def render_index(con, run_dir):
    s = summary(con)
    cards = "".join("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                    % (k, v) for k, v in
                    (("faction", s["faction"]), ("lord level", s["lord_level"]),
                     ("turn", s["turn_now"]), ("settlements", s["settlements"]),
                     ("power rank", s["power_rank"]),
                     ("turns", s["turns"]), ("decisions", s["decisions"]), ("offers", s["offers"]),
                     ("taken", s["taken"]), ("confirmed", s["counted"]),
                     ("confirm %", s["confirm_rate"])))
    rows = []
    for r in by_action_type(con):
        pct = (100.0 * (r["ok"] or 0) / r["n"]) if r["n"] else 0
        cls = "ok" if pct >= 80 else ("warn" if pct >= 40 else "bad")
        rows.append("<tr><td>%s</td><td>%d</td><td class=%s>%d</td>"
                    "<td><span class=bar style='width:%dpx'></span> <span class=%s>%.0f%%</span></td>"
                    "<td class=dim>%s</td></tr>"
                    % (_esc(r["action_type"]), r["n"], cls, r["ok"] or 0,
                       int(pct * 0.9), cls, pct, _esc(r["refusals"])))
    per_type = ("<h2>confirm rate by action type</h2><div class=scroll><table>"
                "<tr><th>action<th>tried<th>confirmed<th>rate<th>refusals seen</tr>%s</table></div>"
                % "".join(rows))
    trows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_esc(t["turn"]), _esc(t["income"]), _esc(t["settlements"]),
                       _esc(t["allies"]), _esc(t["vassals"]), _esc(t["power_rank"]))
                    for t in turns(con))
    reward = ("<h2>reward inputs per turn</h2><div class=scroll><table>"
              "<tr><th>turn<th>income<th>settlements<th>allies<th>vassals<th>power rank</tr>%s"
              "</table></div>" % (trows or "<tr><td class=dim colspan=6>no turns recorded</td></tr>"))
    seq = []
    for r in sequence(con):
        if r["action_type"] is None:
            mark, cls = "-", "dim"
        elif r["refusal"] == "awaiting_execution":
            mark, cls = "...", "warn"
        elif r["counted"]:
            mark, cls = "OK", "ok"
        else:
            mark, cls = "FAIL", "bad"
        pl = r.get("pct_local")
        pl_cell = _num(pl) if pl is not None else "<span class=dim>n/a</span>"
        seq.append("<tr><td><a href='/d/%d'>#%d</a></td><td>%s</td><td>%s</td>"
                   "<td>%s:%s</td><td>%s</td><td>%s</td><td class=%s>%s</td>"
                   "<td>%s</td><td class=dim>%s</td><td class=dim>%s</td><td class=dim>%s</td>"
                   "<td class=dim>%s</td>"
                   "<td class=dim>%s</td><td class=dim>%s</td></tr>"
                   % (r["decision_id"], r["decision_id"], _esc(r["turn"]), r["n_offers"],
                      _esc(r["context_kind"]), _esc(str(r["context_id"])[:26]),
                      _esc(r["action_type"]), _esc(str(r["action_key"])[:38]), cls, mark,
                      _num(r.get("score")), _num(r.get("exploit")),
                      _num(r.get("pct_global")), pl_cell, _num(r.get("explore")),
                      _esc(r["refusal"]), _esc(r["policy"])))
    seqtbl = ("<h2>sequence of picked decisions (newest first)</h2><div class=scroll><table>"
              "<tr><th>#<th>turn<th>offers<th>entity<th>action<th>key<th>result"
              "<th title='0.9*exploit+0.1*novelty -- row ordering only; selection uses exploit/explore/random'>sort<th>exploit<th>&nbsp;&nbsp;global<th>&nbsp;&nbsp;local<th>explore"
              "<th>refusal<th>policy</tr>"
              "%s</table></div>" % "".join(seq))
    head = ("<h1>advisor v7</h1><div class=dim>%s</div>" % _esc(run_dir)) + "<div class=cards>%s</div>" % cards
    panels = [("overview", render_endings() + render_leaders(con) + render_history(con)),
              ("starts", render_starts()),
              ("action x faction", render_faction_matrix()),
              ("blocking menus", render_interrupts()),
              ("diplomacy", render_diplomacy(run_dir)),
              ("timing", render_timing(run_dir)),
              ("actions", per_type + seqtbl),
              ("timeline", render_timeline(con)),
              ("reward", reward),
              ("infrastructure", render_infra(run_dir))]
    nav = "".join("<button class='tab%s' data-t='%s'>%s</button>"
                  % (" on" if i == 0 else "", name, name) for i, (name, _) in enumerate(panels))
    bodies = "".join("<div class=panel id='p-%s'%s>%s</div>"
                     % (name, "" if i == 0 else " hidden", htmlpart)
                     for i, (name, htmlpart) in enumerate(panels))
    return _page(head + "<div class=tabs>%s</div>%s%s" % (nav, bodies, _TABS_JS))


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
  var key='v7tab', want=sessionStorage.getItem(key);
  function show(n){
    document.querySelectorAll('.panel').forEach(function(p){p.hidden = (p.id !== 'p-'+n);});
    document.querySelectorAll('.tab').forEach(function(b){b.classList.toggle('on', b.dataset.t===n);});
    sessionStorage.setItem(key,n);
  }
  document.querySelectorAll('.tab').forEach(function(b){
    b.addEventListener('click', function(){ show(b.dataset.t); });
  });
  if (want && document.getElementById('p-'+want)) show(want);
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
    scale = 1 / 40.0                                  # 1px per 40ms
    phases = (("collect_ms", "p1", "recorder reading the game"),
              ("queue_ms", "p2", "request round trip"),
              ("score_ms", "p3", "featurize + rank"),
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
        rows.append("<tr class='%s'><td>%s</td><td>%s:%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td class=dim>%s</td></tr>"
                    % (cls, _esc(o["rank"]), _esc(o["context_kind"]),
                       _esc(str(o["context_id"])[:22]), _esc(o["action_type"]),
                       _esc(str(o["action_key"])[:44]), avail, fmt(o["score"]),
                       fmt(o["exploit"]), fmt(o.get("pct_global")), pl_cell,
                       fmt(o["explore"]), _esc(o["gate"])))
    tbl = ("<h2>the ranking it produced over the whole faction</h2><div class=scroll><table>"
           "<tr><th>rank<th>entity<th>action<th>key<th>available<th>score<th>exploit"
           "<th>&nbsp;&nbsp;global<th>&nbsp;&nbsp;local<th>explore<th>gate</tr>"
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


def _ps():
    """[(pid, started, cmdline)] for python processes, and the Warhammer3 count."""
    import subprocess
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | ForEach-Object "
           "{ '{0}|{1:yyyy-MM-dd HH:mm:ss}|{2}' -f $_.ProcessId,$_.CreationDate,$_.CommandLine }; "
           "'WH3|{0}' -f @(Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue).Count")
    procs, wh3 = [], None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=20)
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("WH3|"):
                wh3 = ln.split("|", 1)[1].strip() or "0"
            elif ln.count("|") >= 2:
                pid, started, cl = ln.split("|", 2)
                procs.append((pid.strip(), started.strip(), cl.strip()))
    except Exception as e:
        return [], "err: %s" % repr(e)[:60]
    return procs, wh3


def _age(path):
    """(seconds since last write, mtime string) or (None, '-')."""
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


def render_infra(run_dir):
    """Service status, model training state, and run activity."""
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

    g = _meta(r"D:\twdata\models\global\meta.json")
    l = _meta(r"D:\twdata\models\local\meta.json")
    ga, gt = _age(r"D:\twdata\models\global\meta.json")
    la, lt = _age(r"D:\twdata\models\local\meta.json")

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
        pol_cfg = ("random %.0f%% / novelty-directed %.0f%% / exploit %.0f%% (policy.py, live)"
                   % (100 * _P.EPSILON, 100 * _P.BETA, 100 * (1 - _P.EPSILON - _P.BETA)))
    except Exception as e:
        pol_cfg = "unreadable: %s" % repr(e)[:60]
    mrows = [
        "<tr><td>pick policy<td class=dim>-<td class=dim>-<td class=dim>-<td>%s</tr>" % _esc(pol_cfg),
        "<tr><td>global (E1/E2)<td>%s<td>%s<td>%s<td>%s</tr>"
        % (_esc(g.get("rows", "-")), _esc(gt), _esc(ago(ga)),
           _esc("w_local=%s; sort-blend beta=%s (display ordering only, never selects)"
                % (g.get("w_local"), g.get("beta")))),
        "<tr><td>local (E1/E2)<td>%s<td>%s<td>%s<td>%s</tr>"
        % (_esc(l.get("rows", "-")), _esc(lt), _esc(ago(la)),
           _esc("kinds=%s" % ",".join(l.get("kinds") or []) if l else "not trained")),
    ]
    im = _meta(r"D:\twdata\models\interrupt\meta.json")
    ia, it = _age(r"D:\twdata\models\interrupt\meta.json")
    sr = im.get("screen_rows") or {}
    mrows.append("<tr><td>interrupt<td>%s<td>%s<td>%s<td>%s</tr>"
                 % (_esc(im.get("rows", "-")), _esc(it), _esc(ago(ia)),
                    _esc(" ".join("%s:%s" % (k, v) for k, v in sorted(sr.items()))
                         or "screens=%s" % ",".join(im.get("screens") or []))))
    models = ("<h2>models</h2><div class=scroll><table>"
              "<tr><th>model<th>rows<th>trained at<th>age<th>config</tr>%s</table></div>"
              % "".join(mrows))

    watch = [("session log", (open(CURRENT_LOG).read().strip()
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
        lp = open(CURRENT_LOG).read().strip()
        if os.path.isfile(lp):
            try:
                lines = open(lp, encoding="utf-8", errors="replace").read().splitlines()[-14:]
                tail = ("<h2>session log tail</h2><pre class=scroll>%s</pre>"
                        % _esc("\n".join(lines)))
            except Exception:
                pass

    ctl = ("<h2>control</h2>"
           "<div class=dim style='margin-bottom:8px'>kills the running session and the game; "
           "a restart cold-starts a new campaign series</div>"
           "<div style='display:flex;gap:8px;flex-wrap:wrap'>"
           "<a class=btn href='/ctl/kill' onclick=\"return confirm('Kill the session and the game?')\">"
           "kill session + game</a>"
           "<a class=btn href='/ctl/restart?retrain=1' "
           "onclick=\"return confirm('Restart WITH retraining?')\">restart (retrain)</a>"
           "<a class=btn href='/ctl/restart?retrain=0' "
           "onclick=\"return confirm('Restart WITHOUT retraining?')\">restart (no retrain)</a>"
           "</div>")
    return svc + models + activity + ctl + tail


def _kill_session():
    """Kill session.py and the game. Returns a status line."""
    import subprocess
    cmd = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
           "? { $_.CommandLine -like '*session.py*' } | % { Stop-Process -Id $_.ProcessId -Force "
           "-ErrorAction SilentlyContinue; $n++ }; "
           "Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue | "
           "Stop-Process -Force -ErrorAction SilentlyContinue; 'killed sessions={0}' -f $n")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=40)
        return (r.stdout or "").strip() or "killed"
    except Exception as e:
        return "kill failed: %s" % repr(e)[:120]


def _start_session(retrain=True, campaigns=10, turns=40):
    """Spawn a detached session. Returns a status line."""
    import subprocess
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    log = os.path.join(LOG_DIR, "session_%dx%d_%s.log" % (campaigns, turns, ts))
    err = log[:-4] + ".err"
    args = [VENV_PY, "-u", "advisor/session.py", str(campaigns), str(turns),
            "--factions", "all"] + (["--retrain"] if retrain else [])
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fo, fe = open(log, "w", encoding="utf-8"), open(err, "w", encoding="utf-8")
        subprocess.Popen(args, cwd=TW_STACK, stdout=fo, stderr=fe,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        with open(CURRENT_LOG, "w", encoding="utf-8") as fh:
            fh.write(log)
        return "started %s -> %s" % ("with retrain" if retrain else "no retrain",
                                     os.path.basename(log))
    except Exception as e:
        return "start failed: %s" % repr(e)[:160]


def _control(path):
    """Handle /ctl/kill and /ctl/restart?retrain=0|1. Returns the result page body."""
    import time
    from urllib.parse import parse_qs, urlparse
    u = urlparse(path)
    q = parse_qs(u.query or "")
    steps = []
    if u.path == "/ctl/kill":
        steps.append(_kill_session())
    elif u.path == "/ctl/restart":
        retrain = (q.get("retrain", ["1"])[0] != "0")
        steps.append(_kill_session())
        time.sleep(1.5)
        steps.append(_start_session(retrain=retrain))
    else:
        steps.append("unknown control: %s" % u.path)
    return ("<h1>control</h1><pre>%s</pre>"
            "<p><a class=btn href='/'>back</a></p>"
            "<div class=dim>the page below refreshes on its own; give the session ~20s to "
            "appear</div>" % _esc("\n".join(steps)))


def serve(run_dir, port=8777, follow=False):
    """`follow` re-resolves the run dir on every request."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            active = newest_run() if follow else run_dir
            try:
                con = _con(active)
                try:
                    if self.path.startswith("/ctl/"):
                        body = _page(_control(self.path))
                    elif self.path.startswith("/d/"):
                        body = render_decision(con, int(self.path[3:]))
                    elif self.path.startswith("/api/"):
                        body = json.dumps({"summary": summary(con), "sequence": sequence(con)},
                                          default=str)
                    else:
                        body = render_index(con, active)
                finally:
                    con.close()
            except Exception as e:
                body = _page("<h1>error</h1><pre>%s</pre>" % _esc(repr(e)))
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path.startswith("/api/")
                             else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    print("advisor v7 UI -> http://127.0.0.1:%d  (also http://localhost:%d)   run=%s"
          % (port, port, run_dir), flush=True)
    HTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    explicit = len(sys.argv) > 1 and not sys.argv[1].isdigit()
    rd = sys.argv[1] if explicit else newest_run()
    pt = int(next((a for a in sys.argv[1:] if a.isdigit()), 8777))
    serve(rd, pt, follow=not explicit)
