r"""ui.py -- the v7 advisor dashboard: what did it decide, and why.

    python advisor_v7/ui.py [run_dir] [port]      # default: newest run, :8777

Reads decisions.sqlite READ-ONLY (the recorder is the only writer) and shows the two things the v7
structure makes answerable and the v6 UI could not:

  SEQUENCE OF PICKS   every decision point in order -- turn, which entity, which action, whether it
                      was CONFIRMED, and the refusal class when it was not. This is the run's
                      actual history, not a reconstruction.
  PREDICTION HISTORY  for any decision point, the full ranking the model produced over the WHOLE
                      faction at that instant: every offer it considered, its score/exploit/explore,
                      and which one it took. Because v7 stores the ranking next to the offers it
                      ranked, "why did it do that" is a lookup, not an inference.

The v6 dashboard is not ported: it was built on decision_instances + the actions stream + per-type
models, none of which exist now.
"""
from __future__ import annotations

import glob
import html
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

RUNS_ROOT = "D:/twdata/runs/human"


def newest_run():
    """The LIVE run dir. Prefers the pointer the manager publishes, because the recorder opens a
    fresh dir on every campaign swap and "newest by mtime" can lag or pick an abandoned one."""
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


# ------------------------------------------------------------------------------- queries
def summary(con):
    q = lambda s: con.execute(s).fetchone()[0]
    counted = q("SELECT COUNT(*) FROM action_taken WHERE counted=1")
    taken = q("SELECT COUNT(*) FROM action_taken WHERE refusal IS NOT 'awaiting_execution'")
    return {"turns": q("SELECT COUNT(*) FROM target_rows"),
            "decisions": q("SELECT COUNT(*) FROM decision_points"),
            "offers": q("SELECT COUNT(*) FROM action_offers"),
            "taken": taken, "counted": counted,
            "confirm_rate": (round(100.0 * counted / taken, 1) if taken else 0.0)}


def sequence(con, limit=300):
    return [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.decision_seq, d.n_entities, d.n_offers,"
        " t.context_kind, t.context_id, t.action_type, t.action_key, t.counted, t.refusal, t.policy"
        " FROM decision_points d LEFT JOIN action_taken t ON t.decision_id=d.decision_id"
        " ORDER BY d.decision_id DESC LIMIT ?", (limit,))]


def ranking(con, did, limit=80):
    """The model's ranking for one decision point -- what it saw, scored, and chose."""
    taken = con.execute("SELECT context_kind,context_id,action_type,action_key,counted,refusal"
                        " FROM action_taken WHERE decision_id=?", (did,)).fetchone()
    rows = [dict(r) for r in con.execute(
        "SELECT context_kind,context_id,action_type,action_key,available,gate,score,exploit,explore,rank"
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
    """The run as it actually happened: decisions in order, grouped by turn, with the wall-clock
    gap between them. The gaps are the point -- a long one is where the loop was waiting on the
    game (a battle, an AI turn, a confirm poll timing out), which is what you want to see when a
    session feels stuck."""
    rows = [dict(r) for r in con.execute(
        "SELECT d.decision_id, d.turn, d.ts, d.timings, t.context_kind, t.context_id, t.action_type,"
        " t.action_key, t.counted, t.refusal, t.latency_ms FROM decision_points d"
        " LEFT JOIN action_taken t ON t.decision_id=d.decision_id ORDER BY d.decision_id")]
    out, prev = [], None
    for r in rows:
        r["gap"] = (round(r["ts"] - prev, 1) if prev and r["ts"] else None)
        prev = r["ts"] or prev
        tm = json.loads(r["timings"]) if r.get("timings") else {}
        # the five phases of one action, in ms: asking the recorder, the recorder reading the game,
        # the round trip on top of that, scoring, then execute+confirm.
        r["collect_ms"] = tm.get("collect_ms")
        r["queue_ms"] = ((tm.get("roundtrip_ms") or 0) - (tm.get("collect_ms") or 0)
                         if tm.get("roundtrip_ms") is not None else None)
        r["score_ms"] = tm.get("score_ms")
        r["verify_ms"] = r.get("latency_ms")
        r["offers_n"] = tm.get("offers")
        r["total_ms"] = sum(v for v in (r["collect_ms"], r["queue_ms"], r["score_ms"],
                                        r["verify_ms"]) if v) or None
        out.append(r)
    turns_ = {}
    for r in out:
        turns_.setdefault(r["turn"], []).append(r)
    return out, turns_


def run_history(con):
    """One row per CAMPAIGN in this store, oldest first.

    Several playthroughs share one decisions.sqlite now (identity lives in the minted campaign uuid,
    not in the directory), so "run history" is just a GROUP BY campaign_id -- no cross-file walking.
    """
    rows = []
    for camp, first_ts, last_ts, n_dec, max_turn in con.execute(
            "SELECT campaign_id, MIN(ts), MAX(ts), COUNT(*), MAX(turn) FROM decision_points"
            " GROUP BY campaign_id ORDER BY MIN(ts)"):
        taken, counted = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(t.counted),0) FROM action_taken t"
            " JOIN decision_points d ON d.decision_id=t.decision_id"
            " WHERE d.campaign_id=? AND t.refusal IS NOT 'awaiting_execution'", (camp,)).fetchone()
        reward = con.execute(
            "SELECT turn, income, settlements, power_rank FROM target_rows WHERE campaign_id=?"
            " ORDER BY turn DESC LIMIT 1", (camp,)).fetchone()
        rows.append({"campaign": camp, "turns": int(max_turn or 0), "decisions": n_dec,
                     "taken": taken or 0, "counted": counted or 0,
                     "minutes": round(((last_ts or 0) - (first_ts or 0)) / 60.0, 1),
                     "confirm_pct": round(100.0 * (counted or 0) / (taken or 1), 1),
                     "last_income": (reward[1] if reward else None),
                     "last_settlements": (reward[2] if reward else None),
                     "last_power_rank": (reward[3] if reward else None)})
    return rows


def by_action_type(con):
    return [dict(r) for r in con.execute(
        "SELECT action_type, COUNT(*) n, SUM(counted) ok,"
        " GROUP_CONCAT(DISTINCT refusal) refusals FROM action_taken"
        " WHERE refusal IS NOT 'awaiting_execution' GROUP BY action_type ORDER BY n DESC")]


def turns(con):
    return [dict(r) for r in con.execute(
        "SELECT turn,income,settlements,allies,vassals,power_rank FROM target_rows ORDER BY turn")]


# ------------------------------------------------------------------------------- rendering
_CSS = """
:root{--bg:#12141a;--fg:#e6e8ee;--dim:#8b93a7;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--line:#242832;--card:#181b23}
@media(prefers-color-scheme:light){:root{--bg:#fbfbfd;--fg:#1b1f27;--dim:#5d6470;--line:#e3e6ec;--card:#fff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
a{color:inherit}h1{font-size:16px;margin:0 0 4px}h2{font-size:13px;margin:22px 0 8px;color:var(--dim);
text-transform:uppercase;letter-spacing:.08em}
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
    # auto-refresh: this dashboard is watched DURING a run, and a first load that happened to catch
    # an empty DB used to just sit there looking broken
    return ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<meta http-equiv=refresh content=10>"
            "<style>%s</style><div class=wrap>%s</div>"
            % (html.escape(title), _CSS, body))


def _esc(v):
    return html.escape("" if v is None else str(v))


def render_index(con, run_dir):
    s = summary(con)
    cards = "".join("<div class=card><div class=k>%s</div><div class=v>%s</div></div>"
                    % (k, v) for k, v in
                    (("turns", s["turns"]), ("decisions", s["decisions"]), ("offers", s["offers"]),
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
        seq.append("<tr><td><a href='/d/%d'>#%d</a></td><td>%s</td><td>%s</td>"
                   "<td>%s:%s</td><td>%s</td><td>%s</td><td class=%s>%s</td>"
                   "<td class=dim>%s</td><td class=dim>%s</td></tr>"
                   % (r["decision_id"], r["decision_id"], _esc(r["turn"]), r["n_offers"],
                      _esc(r["context_kind"]), _esc(str(r["context_id"])[:26]),
                      _esc(r["action_type"]), _esc(str(r["action_key"])[:38]), cls, mark,
                      _esc(r["refusal"]), _esc(r["policy"])))
    seqtbl = ("<h2>sequence of picked decisions (newest first)</h2><div class=scroll><table>"
              "<tr><th>#<th>turn<th>offers<th>entity<th>action<th>key<th>result<th>refusal<th>policy</tr>"
              "%s</table></div>" % "".join(seq))
    head = ("<h1>advisor v7</h1><div class=dim>%s</div>" % _esc(run_dir)) + "<div class=cards>%s</div>" % cards
    return _page(head + render_history(con) + per_type + render_timeline(con) + seqtbl + reward)


def render_history(con):
    """Bar chart of run history: how far each campaign actually got.

    Turns-survived is the headline because it is the one number that says whether a run was a real
    campaign or died/stalled early -- and it is the survival leg of the reward, so a run that looks
    short here contributes a low target for every decision in it.
    """
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
            "<td class=dim>%s</td><td class=dim>%s</td></tr>"
            % (i, _esc(r["campaign"]), _esc(str(r["campaign"])[-14:]),
               cls, w, r["turns"], wd, r["decisions"],
               r["counted"], r["confirm_pct"], r["minutes"],
               _esc(r["last_settlements"]), _esc(r["last_income"])))
    return ("<h2>run history &mdash; how far each campaign got</h2>"
            "<div class=scroll><table>"
            "<tr><th>#<th>campaign<th title='max turn reached'>turns survived"
            "<th title='decision points recorded'>decisions<th>confirmed<th>rate"
            "<th>wall<th>settlements<th>income</tr>%s</table></div>" % "".join(bars))


def render_timeline(con):
    """Turn-by-turn swimlanes: each action in the order it was taken, with the wait before it."""
    _rows, per_turn = timeline(con)
    if not per_turn:
        return ""
    # one pixel per 40ms keeps a typical 4s collect readable next to a 200ms score
    scale = 1 / 40.0
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
            # GAP = wall clock since the previous action started. It is the column that finds
            # stalls: everything the phase timers do NOT cover lives here -- battles, the AI's
            # inter-turn, interrupt handling, and any wait nobody instrumented. A gap far larger
            # than `total` means the loop was blocked on the game, not on itself.
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
        rows.append("<tr class='%s'><td>%s</td><td>%s:%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td class=dim>%s</td></tr>"
                    % (cls, _esc(o["rank"]), _esc(o["context_kind"]),
                       _esc(str(o["context_id"])[:22]), _esc(o["action_type"]),
                       _esc(str(o["action_key"])[:44]), avail, fmt(o["score"]),
                       fmt(o["exploit"]), fmt(o["explore"]), _esc(o["gate"])))
    tbl = ("<h2>the ranking it produced over the whole faction</h2><div class=scroll><table>"
           "<tr><th>rank<th>entity<th>action<th>key<th>available<th>score<th>exploit<th>explore<th>gate</tr>"
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


# ------------------------------------------------------------------------------- server
def serve(run_dir, port=8777, follow=False):
    """`follow` re-resolves the live run dir on EVERY request, so a campaign swap (which makes the
    recorder open a fresh dir) moves the dashboard with it instead of stranding it on a dead one."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            active = newest_run() if follow else run_dir
            try:
                con = _con(active)
                try:
                    if self.path.startswith("/d/"):
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
    serve(rd, pt, follow=not explicit)      # no dir given -> track the live run across swaps
