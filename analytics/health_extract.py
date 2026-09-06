from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from decisions import pg

VERSION_NOTE = ("a campaign that played across a retrain or relaunch can appear under "
                "more than one code_version; decision and action rows always carry the "
                "version that recorded them")


def log(msg):
    sys.stderr.write("%.3f  health %s\n" % (time.time(), msg))


def _rows(con, sql, args=()):
    t0 = time.time()
    cur = con.execute(sql, args)
    cols = [c.name for c in cur.description]
    out = [dict(zip(cols, r)) for r in cur.fetchall()]
    log("query %.0f ms rows=%d" % ((time.time() - t0) * 1000, len(out)))
    return out


def versions_seen(con, since):
    return _rows(con, """
        SELECT COALESCE(cv.collector_sha, 'unversioned') AS code_version,
               MIN(cv.started_ts) AS started_ts,
               COUNT(DISTINCT s.campaign_id) AS campaigns, COUNT(*) AS decisions
        FROM corpus.snapshot s
        JOIN corpus.decision d ON d.decision_id = s.snapshot_id
        LEFT JOIN corpus.collector_version cv ON cv.version_id = s.version_id
        WHERE s.ts >= %s GROUP BY 1 ORDER BY 2 NULLS FIRST""", (since,))


def campaign_outcomes(con, since):
    return _rows(con, """
        SELECT code_version, outcome, COUNT(*) AS campaigns,
               ROUND(AVG(turns)::numeric, 1) AS avg_turns
        FROM (SELECT DISTINCT c.campaign_id,
                     COALESCE(cv.collector_sha, 'unversioned') AS code_version,
                     COALESCE(o.key, 'in_progress') AS outcome, c.turns
              FROM corpus.campaign c
              JOIN corpus.snapshot s ON s.campaign_id = c.campaign_id
              JOIN corpus.decision d ON d.decision_id = s.snapshot_id
              LEFT JOIN corpus.collector_version cv ON cv.version_id = s.version_id
              LEFT JOIN dict.enum o ON o.enum_id = c.outcome_id
              WHERE s.ts >= %s) x
        GROUP BY 1, 2 ORDER BY 1, 2""", (since,))


def action_stats(con, since):
    return _rows(con, """
        SELECT COALESCE(cv.collector_sha, 'unversioned') AS code_version,
               at.key AS action_type,
               COUNT(*) AS tried, COUNT(*) FILTER (WHERE t.executed) AS executed,
               COUNT(*) FILTER (WHERE t.confirmed) AS confirmed,
               COUNT(*) FILTER (WHERE t.counted) AS counted,
               COUNT(*) FILTER (WHERE t.refusal_id IS NOT NULL) AS refused,
               ROUND(AVG(t.latency_ms)::numeric, 1) AS latency_mean_ms,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY t.latency_ms) AS latency_p50_ms,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY t.latency_ms) AS latency_p90_ms
        FROM corpus.taken t
        JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id
        LEFT JOIN corpus.collector_version cv ON cv.version_id = s.version_id
        LEFT JOIN dict.action a ON a.action_id = t.action_id
        LEFT JOIN dict.action_type at ON at.id = a.action_type_id
        WHERE t.ts >= %s
        GROUP BY 1, 2 ORDER BY 1, 2""", (since,))


def refusal_stats(con, since):
    return _rows(con, """
        SELECT COALESCE(cv.collector_sha, 'unversioned') AS code_version,
               at.key AS action_type, rr.key AS refusal, COUNT(*) AS n
        FROM corpus.taken t
        JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id
        LEFT JOIN corpus.collector_version cv ON cv.version_id = s.version_id
        LEFT JOIN dict.action a ON a.action_id = t.action_id
        LEFT JOIN dict.action_type at ON at.id = a.action_type_id
        JOIN dict.enum rr ON rr.enum_id = t.refusal_id
        WHERE t.ts >= %s
        GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 200""", (since,))


def interrupt_stats(con, since):
    return _rows(con, """
        SELECT COALESCE(cv.collector_sha, 'unversioned') AS code_version,
               ek.key AS kind,
               COUNT(*) AS tried, COUNT(*) FILTER (WHERE i.counted) AS counted,
               COUNT(*) FILTER (WHERE i.refusal_id IS NOT NULL) AS refused,
               ROUND(AVG(i.latency_ms)::numeric, 1) AS latency_mean_ms,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY i.latency_ms) AS latency_p90_ms
        FROM corpus.interrupt i
        JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id
        JOIN dict.enum ek ON ek.enum_id = i.kind_id
        LEFT JOIN corpus.collector_version cv ON cv.version_id = s.version_id
        WHERE s.ts >= %s
        GROUP BY 1, 2 ORDER BY 1, 2""", (since,))


SUSPECT_OUTCOMES = ("error", "stuck", "unhandled_screen", "model_unavailable",
                    "retrain_failed")


def postmortem_stats(con, since):
    raw = _rows(con, """
        SELECT p.postmortem_id, c.campaign_key, p.ts, f.key AS faction,
               p.turns_played, o.key AS outcome, p.defeated, p.code_version,
               p.seconds, p.plausibility_verdict AS verdict, p.error, p.ended_by,
               p.wh3_running
        FROM corpus.postmortem p
        LEFT JOIN corpus.campaign c ON c.campaign_id = p.campaign_id
        LEFT JOIN dict.faction f ON f.id = p.faction_id
        LEFT JOIN dict.enum o ON o.enum_id = p.outcome_id
        WHERE p.ts >= %s ORDER BY p.ts""", (since,))
    by_key, verdicts, suspicious = {}, {}, []
    for r in raw:
        ver = r.get("code_version") or "unversioned"
        outcome = r.get("outcome") or "unknown"
        verdict = r.get("verdict")
        k = (ver, outcome)
        agg = by_key.setdefault(k, {"code_version": ver, "outcome": outcome, "n": 0,
                                    "seconds": [], "turns": []})
        agg["n"] += 1
        if r.get("seconds") is not None:
            agg["seconds"].append(float(r["seconds"]))
        if r.get("turns_played") is not None:
            agg["turns"].append(float(r["turns_played"]))
        if verdict:
            vk = verdicts.setdefault(ver, {})
            vk[verdict] = vk.get(verdict, 0) + 1
        odd_verdict = bool(verdict) and not (
            verdict.startswith("consistent") or verdict.startswith("turn_time_cap"))
        if odd_verdict or outcome in SUSPECT_OUTCOMES:
            suspicious.append({
                "when": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(r.get("ts") or 0)),
                "code_version": ver, "campaign_key": r.get("campaign_key"),
                "faction": r.get("faction"), "turn": r.get("turns_played"),
                "outcome": outcome, "verdict": verdict,
                "error": str(r.get("error") or "")[:240] or None,
                "ended_by": (r.get("ended_by") or [])[-4:] or None,
                "seconds": r.get("seconds"), "turns_played": r.get("turns_played"),
                "wh3_running": r.get("wh3_running")})
    def med(xs):
        s = sorted(xs)
        return round(s[len(s) // 2], 1) if s else None
    tallies = [{"code_version": a["code_version"], "outcome": a["outcome"], "n": a["n"],
                "median_seconds": med(a["seconds"]), "median_turns": med(a["turns"])}
               for a in by_key.values()]
    tallies.sort(key=lambda x: (x["code_version"], x["outcome"]))
    return {"tallies": tallies, "plausibility_by_version": verdicts,
            "suspicious": suspicious[-120:]}


def unhandled_screens(since):
    path = common.native(common.UNHANDLED_LOG)
    grouped, recent = {}, []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if (rec.get("ts") or 0) < since:
                    continue
                k = (str(rec.get("root")), str(rec.get("screen")))
                g = grouped.setdefault(k, {"root": rec.get("root"),
                                           "screen": rec.get("screen"), "n": 0,
                                           "unknown_sample": rec.get("unknown")})
                g["n"] += 1
                recent.append({"when": rec.get("when"), "screen": rec.get("screen"),
                               "root": rec.get("root"),
                               "unknown": (rec.get("unknown") or [])[:6],
                               "offered": (rec.get("offered") or [])[:6]})
    except OSError:
        pass
    return {"grouped": sorted(grouped.values(), key=lambda g: -g["n"]),
            "recent": recent[-20:]}


def extract(days):
    t0 = time.time()
    log("extract enter days=%s" % days)
    since = time.time() - days * 86400.0
    con = pg.connect(app_name="tw-health", autocommit=True, readonly=True,
                     search_path=pg.CORPUS_PATH)
    try:
        out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "window_days": days, "since_ts": since,
               "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since)),
               "version_note": VERSION_NOTE,
               "versions_seen": versions_seen(con, since),
               "campaign_outcomes": campaign_outcomes(con, since),
               "actions": action_stats(con, since),
               "refusals": refusal_stats(con, since),
               "interrupts": interrupt_stats(con, since),
               "postmortems": postmortem_stats(con, since),
               "unhandled_screens": unhandled_screens(since)}
    finally:
        con.close()
    log("extract exit %.0f ms" % ((time.time() - t0) * 1000))
    return out


def _jdefault(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


def main():
    ap = argparse.ArgumentParser(prog="health_extract")
    ap.add_argument("--days", type=float, required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    payload = json.dumps(extract(a.days), indent=1, default=_jdefault)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print("health_extract: %d bytes -> %s" % (len(payload), a.out))
    else:
        print(payload)


if __name__ == "__main__":
    main()
