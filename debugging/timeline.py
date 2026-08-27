from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

STAMP_LEN = 23
_SEC_CACHE = {}


def _epoch_of_stamp(head):
    sec = head[:19]
    base = _SEC_CACHE.get(sec)
    if base is None:
        try:
            base = datetime.datetime.strptime(sec, "%Y-%m-%dT%H:%M:%S").timestamp()
        except ValueError:
            return None
        _SEC_CACHE[sec] = base
    try:
        return base + int(head[20:23]) / 1000.0
    except ValueError:
        return base


def parse_when(text, ref):
    s = str(text or "").strip()
    if not s or s == "now":
        return ref
    if s[0] in "+-":
        mult = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}.get(s[-1])
        if mult is None:
            raise SystemExit("relative time %r needs a unit: s, m, h or d" % text)
        return ref + float(s[:-1]) * mult
    try:
        v = float(s)
        if v > 1e9:
            return v
        raise SystemExit("bare number %r is not an epoch; use -5m, HH:MM:SS or "
                         "YYYY-MM-DDTHH:MM:SS" % text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%H:%M:%S.%f", "%H:%M:%S"):
        try:
            d = datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
        if d.year == 1900:
            today = datetime.date.fromtimestamp(ref)
            d = d.replace(year=today.year, month=today.month, day=today.day)
        return d.timestamp()
    raise SystemExit("cannot read %r as a time: use an epoch, -5m, HH:MM:SS[.mmm] or "
                     "YYYY-MM-DDTHH:MM:SS[.mmm]" % text)


def _first_stamp(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace")
    except OSError:
        return None
    for line in head.splitlines():
        t = _epoch_of_stamp(line[:STAMP_LEN])
        if t is not None:
            return t
    return None


def _overlapping(paths, t0, t1):
    out = []
    for p in paths:
        try:
            end = os.path.getmtime(p)
        except OSError:
            continue
        start = _first_stamp(p)
        if start is None:
            start = end
        if start <= t1 and end >= t0:
            out.append(p)
    return sorted(out)


def _classify_log(line):
    if line.startswith("WAIT "):
        return "wait"
    if line.startswith("TRY "):
        return "try"
    if line.startswith("PHASE "):
        return "phase"
    if line.startswith("== TURN"):
        return "turn"
    if line.startswith("CAMPAIGN ") or line.startswith("=" * 10):
        return "campaign"
    if line.startswith("ucb "):
        return "select"
    if line.startswith("[launch]"):
        return "launch"
    if "unhandled_screen" in line or line.startswith("screen "):
        return "panel"
    if line.startswith("interrupt") or "interrupts:" in line:
        return "interrupt"
    if "Traceback" in line or line.startswith("ERROR") or " FAILED" in line:
        return "error"
    return "log"


def from_stamped_log(path, t0, t1, source):
    rows = []
    last = None
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            t = _epoch_of_stamp(line[:STAMP_LEN])
            if t is None:
                if last is not None and t0 <= last <= t1:
                    rows.append((last, source, "log", "    " + line.strip()[:400]))
                continue
            last = t
            if t < t0 or t > t1:
                continue
            body = line[STAMP_LEN + 1:].strip()
            if body:
                rows.append((t, source, _classify_log(body), body[:400]))
    return rows


def _row_ts(row, anchor):
    ts = row.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    t = row.get("t")
    if isinstance(t, (int, float)) and anchor:
        return anchor + float(t)
    return None


def _brief(row, drop, limit=260):
    parts = []
    for k in row:
        if k in drop or row[k] is None:
            continue
        v = row[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"))
        v = str(v)
        parts.append("%s=%s" % (k, v if len(v) <= 90 else v[:87] + "..."))
    return " ".join(parts)[:limit]


def from_jsonl(path, t0, t1, source, anchor=None, kind=None):
    rows = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            t = _row_ts(row, anchor)
            if t is None or t < t0 or t > t1:
                continue
            k = kind or str(row.get("kind") or row.get("stage") or source)
            rows.append((t, source, k, _brief(row, ("ts", "t", "kind"))))
    return rows


def from_screens(dirpath, t0, t1):
    rows = []
    for p in glob.glob(os.path.join(dirpath, "*.json")):
        base = os.path.basename(p)
        head = base.split("_", 1)[0]
        t = None
        if head.isdigit():
            t = int(head) / 1000.0
        if t is not None and (t < t0 - 5 or t > t1 + 5):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        t = float(d.get("ts") or t or 0)
        if t < t0 or t > t1:
            continue
        rows.append((t, "screens", "panel",
                     "%s root=%s nodes=%d roots=%s"
                     % (d.get("why"), d.get("root"), len(d.get("nodes") or []),
                        ",".join((d.get("roots") or [])[:6]))[:400]))
    return rows


def _jloads(v):
    if not v:
        return {}
    try:
        d = json.loads(v)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def from_store(db, t0, t1):
    sys.path.insert(0, common.ROOT)
    from decisions import pg
    rows = []
    con = pg.connect(autocommit=True, readonly=True)
    try:
        for did, ts, turn, seq, policy, ne, no, timings in con.execute(
                "SELECT decision_id, ts, turn, decision_seq, policy, n_entities, n_offers,"
                " timings FROM decisions WHERE ts BETWEEN %s AND %s ORDER BY ts", (t0, t1)):
            rows.append((ts, "store", "decision",
                         "#%s turn=%s seq=%s policy=%s entities=%s offers=%s"
                         % (did, turn, seq, policy, ne, no)))
            tm = _jloads(timings)
            if tm.get("t_request"):
                rows.append((float(tm["t_request"]), "store", "rpc",
                             "#%s request sent" % did))
            if tm.get("t_received"):
                rows.append((float(tm["t_received"]), "store", "rpc",
                             "#%s reply received (roundtrip %sms, pickup lag %sms)"
                             % (did, tm.get("roundtrip_ms"), tm.get("pickup_lag_ms"))))
            if tm:
                rows.append((ts, "store", "timing",
                             "#%s collect=%sms score=%sms store=%sms housekeep=%sms %s"
                             % (did, tm.get("collect_ms"), tm.get("score_ms"),
                                tm.get("store_ms"), tm.get("housekeep_ms"),
                                json.dumps(tm.get("housekeep_parts") or {},
                                           separators=(",", ":")))[:380]))
        for did, ts, ex, cf, ct, refusal, lat, policy, timing, diag in con.execute(
                "SELECT decision_id, ts, executed, confirmed, counted, refusal, latency_ms,"
                " policy, timing, diagnostics FROM taken WHERE ts BETWEEN %s AND %s"
                " ORDER BY ts", (t0, t1)):
            tm = _jloads(timing)
            rows.append((ts, "store", "taken",
                         "#%s executed=%s confirmed=%s counted=%s refusal=%s latency=%sms "
                         "execute=%sms confirm=%sms snapshot=%sms policy=%s"
                         % (did, ex, cf, ct, refusal, lat, tm.get("execute_ms"),
                            tm.get("confirm_ms"), tm.get("snapshot_ms"), policy)))
            for t, line in _stderr_lines(_jloads(diag).get("stderr"), t0, t1):
                rows.append((t, "action", _classify_log(line), "#%s %s" % (did, line[:360])))
        for ts, turn, kind, root, nopt, chosen, ex, cf, ct, refusal, lat in con.execute(
                "SELECT ts, turn, kind, root, n_options, chosen, executed, confirmed,"
                " counted, refusal, latency_ms FROM interrupts WHERE ts BETWEEN %s AND %s"
                " ORDER BY ts", (t0, t1)):
            rows.append((ts, "store", "interrupt",
                         "%s root=%s turn=%s options=%s chose=%s executed=%s confirmed=%s "
                         "counted=%s refusal=%s latency=%sms"
                         % (kind, root, turn, nopt, chosen, ex, cf, ct, refusal, lat)))
        for ts, kind, payload in con.execute(
                "SELECT ts, kind, payload FROM rpc_requests WHERE ts BETWEEN %s AND %s"
                " ORDER BY ts", (t0, t1)):
            d = _jloads(payload)
            did = d.get("decision_id")
            if kind == "pick":
                pick = d.get("pick") or {}
                rows.append((ts, "store", "pick",
                             "#%s %s %s policy=%s score=%s rank=%s"
                             % (did, pick.get("action_type"), pick.get("key"),
                                pick.get("policy"), pick.get("score"), pick.get("rank"))))
                scored = d.get("scores") or []
                if scored:
                    rows.append((ts, "store", "score",
                                 "#%s %d scored: %s"
                                 % (did, len(scored),
                                    json.dumps(scored[:6], separators=(",", ":"))[:300])))
            else:
                rows.append((ts, "store", "rpc", "%s #%s" % (kind, did)))
        for ts, req, err in con.execute(
                "SELECT ts, req_id, error FROM rpc_responses WHERE ts BETWEEN %s AND %s"
                " ORDER BY ts", (t0, t1)):
            rows.append((ts, "store", "rpc", "reply %s%s"
                         % (req, " error=%s" % str(err)[:120] if err else "")))
        for ts, key, turn, outcome, defeated, reason in con.execute(
                "SELECT ts, campaign_key, turn, outcome, defeated, reason FROM postmortems"
                " WHERE ts BETWEEN %s AND %s ORDER BY ts", (t0, t1)):
            rows.append((ts, "store", "postmortem",
                         "%s turn=%s outcome=%s defeated=%s reason=%s"
                         % (key, turn, outcome, defeated, str(reason)[:160])))
        for ts, key, turn, kind, payload in con.execute(
                "SELECT ts, campaign_key, turn, kind, payload FROM diplomacy_events"
                " WHERE ts BETWEEN %s AND %s ORDER BY ts", (t0, t1)):
            rows.append((ts, "store", "diplomacy",
                         "%s %s turn=%s %s" % (kind, key, turn, str(payload)[:200])))
        for pid, ts, c, total, cmap, fac, n, mean, explore, score, tied in con.execute(
                "SELECT pick_id, ts, c, total_plays, campaign_map, faction, n, mean,"
                " explore, score, tied FROM ucb_picks WHERE ts BETWEEN %s AND %s ORDER BY ts",
                (t0, t1)):
            rows.append((ts, "store", "select",
                         "ucb c=%s picked %s on %s n=%s mean=%s explore=%s score=%s "
                         "plays=%s tied=%s (pick %s)"
                         % (c, fac, cmap, n, mean, explore, score, total, tied, pid)))
    finally:
        con.close()
    return rows


def _stderr_lines(blob, t0, t1):
    out = []
    for line in str(blob or "").splitlines():
        t = _epoch_of_stamp(line[:STAMP_LEN])
        if t is None or t < t0 or t > t1:
            continue
        out.append((t, line[STAMP_LEN + 1:].strip()))
    return out


def _anchor(run_dir):
    try:
        with open(os.path.join(run_dir, "meta.json"), encoding="utf-8") as fh:
            return float(json.load(fh).get("t0_epoch") or 0) or None
    except (OSError, ValueError, TypeError):
        return None


def _last_campaign_start(paths):
    best = None
    for p in paths:
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if line[STAMP_LEN + 1:].startswith("CAMPAIGN "):
                    t = _epoch_of_stamp(line[:STAMP_LEN])
                    if t is not None and (best is None or t > best):
                        best = t
    return best


def collect(t0, t1, run_dir, log_dir, services_dir, dev_dir, screens_dir):
    rows, seen = [], []

    def take(label, got):
        rows.extend(got)
        seen.append("%s(%d)" % (label, len(got)))

    session_logs = _overlapping(glob.glob(os.path.join(log_dir, "session_*.log"))
                                + glob.glob(os.path.join(log_dir, "session_*.err")), t0, t1)
    for p in session_logs:
        take("session:" + os.path.basename(p),
             from_stamped_log(p, t0, t1, "session"))
    for pat, label in (("manager_*.log", "manager"), ("ui_*.log", "ui"),
                       ("analytics_*.log", "analytics"), ("harness.log", "harness")):
        for p in _overlapping(glob.glob(os.path.join(services_dir, pat)), t0, t1):
            take("%s:%s" % (label, os.path.basename(p)),
                 from_stamped_log(p, t0, t1, label))

    errors = os.path.join(run_dir, "errors.log")
    if os.path.exists(errors):
        take("errors", from_stamped_log(errors, t0, t1, "errors"))

    take("store", from_store(None, t0, t1))

    anchor = _anchor(run_dir)
    for name, source in (("trace.jsonl", "trace"), ("turn_trail.jsonl", "turn_trail"),
                         ("loop_report.jsonl", "loop"), ("locomotion.jsonl", "locomotion"),
                         ("clear_screen_trace.jsonl", "clear_screen"),
                         ("post_attack_trace.jsonl", "post_attack"),
                         ("decisions_stream.jsonl", "dstream"),
                         ("events.jsonl", "events"),
                         ("ui_components.jsonl", "ui_components")):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            take(source, from_jsonl(p, t0, t1, source, anchor=anchor))
    for name, source in (("events.jsonl", "dev_events"),
                         ("actions_stream.jsonl", "dev_actions")):
        p = os.path.join(dev_dir, name)
        if os.path.exists(p):
            take(source, from_jsonl(p, t0, t1, source, anchor=anchor))

    unhandled = os.path.join(os.path.dirname(screens_dir), "unhandled_screens.jsonl")
    if os.path.exists(unhandled):
        take("unhandled", from_jsonl(unhandled, t0, t1, "screens", kind="unhandled_screen"))
    if os.path.isdir(screens_dir):
        take("screens", from_screens(screens_dir, t0, t1))
    return rows, seen


def build(start=None, end=None, out=None, run_dir=None, only=None, exclude=None,
          limit=0):
    now = time.time()
    run_dir = common.native(run_dir or common.RUN_DIR)
    log_dir = common.native(common.LOGS_ADVISOR)
    services_dir = common.native(common.LOGS_SERVICES)
    dev_dir = common.native(common.LOGS_DEV)
    screens_dir = common.native(common.SCREEN_DUMP_DIR)

    t1 = parse_when(end, now) if end else now
    if start:
        t0 = parse_when(start, t1)
    else:
        t0 = _last_campaign_start(_overlapping(
            glob.glob(os.path.join(log_dir, "session_*.log")), t1 - 86400, t1))
        if t0 is None:
            t0 = t1 - 600.0
    if t0 > t1:
        raise SystemExit("start %s is after end %s" % (common.stamp(t0), common.stamp(t1)))

    rows, seen = collect(t0, t1, run_dir, log_dir, services_dir, dev_dir, screens_dir)
    if only:
        keep = set(only)
        rows = [r for r in rows if r[2] in keep]
    if exclude:
        drop = set(exclude)
        rows = [r for r in rows if r[2] not in drop]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    dropped = 0
    if limit and len(rows) > limit:
        dropped = len(rows) - limit
        rows = rows[:limit]

    out = out or os.path.join(common.DEBUG_DIR,
                              "timeline_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    out = common.native(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# tw_stack timeline\n")
        fh.write("# window %s .. %s  (%.3fs)\n" % (common.stamp(t0), common.stamp(t1),
                                                   t1 - t0))
        fh.write("# run dir %s\n" % run_dir)
        fh.write("# rows %d from %d source(s)\n" % (len(rows), len(seen)))
        fh.write("# sources %s\n" % " ".join(seen))
        counts = {}
        for r in rows:
            counts[r[2]] = counts.get(r[2], 0) + 1
        fh.write("# kinds %s\n"
                 % " ".join("%s(%d)" % (k, n) for k, n
                            in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))))
        if dropped:
            fh.write("# TRUNCATED: --limit %d dropped %d later row(s)\n" % (limit, dropped))
        if only:
            fh.write("# only %s\n" % ",".join(sorted(only)))
        if exclude:
            fh.write("# excluding %s\n" % ",".join(sorted(exclude)))
        fh.write("#\n# time          delta  source        kind        detail\n")
        prev = None
        for t, source, kind, text in rows:
            fh.write("%s %7s  %-13s %-11s %s\n"
                     % (common.stamp(t)[11:], "+%.3f" % (t - prev) if prev else "  --",
                        source, kind, text))
            prev = t
    return out, len(rows), dropped, t0, t1


def _fold_values(argv):
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--start", "--end") and i + 1 < len(argv):
            out.append("%s=%s" % (a, argv[i + 1]))
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(
        prog="timeline",
        description="merge every stamped source of a run into one millisecond-ordered "
                    "file and print its path")
    ap.add_argument("--start", default=None,
                    help="epoch, YYYY-MM-DDTHH:MM:SS[.mmm], HH:MM:SS[.mmm] or -5m/-90s; "
                         "default: the last CAMPAIGN marker in the session log")
    ap.add_argument("--end", default=None, help="same forms; default: now")
    ap.add_argument("--out", default=None,
                    help="output file; default <TWDATA>/debug/timeline_<stamp>.txt")
    ap.add_argument("--run", default=None, help="run dir; default <TWDATA>/runs/human/run")
    ap.add_argument("--only", default=None, help="comma-separated kinds to keep")
    ap.add_argument("--exclude", default=None, help="comma-separated kinds to drop")
    ap.add_argument("--limit", type=int, default=0, help="cap rows (0 = no cap)")
    a = ap.parse_args(_fold_values(sys.argv[1:]))
    out, n, dropped, t0, t1 = build(
        start=a.start, end=a.end, out=a.out, run_dir=a.run,
        only=[k.strip() for k in a.only.split(",") if k.strip()] if a.only else None,
        exclude=[k.strip() for k in a.exclude.split(",")
                 if k.strip()] if a.exclude else None,
        limit=a.limit)
    sys.stderr.write("timeline: %d row(s) over %.1fs (%s .. %s)%s\n"
                     % (n, t1 - t0, common.stamp(t0), common.stamp(t1),
                        ", %d dropped by --limit" % dropped if dropped else ""))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
