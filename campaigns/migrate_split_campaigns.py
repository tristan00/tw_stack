r"""migrate_split_campaigns -- offline, dry-run splitter for the whole run corpus.

Walks every run under a root (default D:/twdata/runs/human), EXCLUDING the currently-active
run, and computes how each would split into one-directory-per-campaign. It NEVER writes into an
original run dir and NEVER deletes anything. In the default dry-run it writes only a per-run
report + split PLAN (a manifest of which byte-ranges/lines/shots go to each campaign) into a
scratch preview area, and prints a reconciliation proving sum-of-parts == original for every
stream (the invariant: no campaign, no line, no byte, no shot is dropped).

    python migrate_split_campaigns.py                 # dry-run over the corpus, write reports
    python migrate_split_campaigns.py --run <id>      # one run only
    python migrate_split_campaigns.py --no-state-lines # skip newline counting on huge logs (fast)
    python migrate_split_campaigns.py --commit         # REFUSED (guarded; see below)

The --commit path is intentionally NOT implemented here. Its contract, for a future change, is:
per run, (1) materialise each campaign's streams into <run>/<campaign>/ (or a sibling run dir),
(2) re-run split_run() and require reconciliation all_ok AND sum_ok for every stream, and only
THEN (3) delete the big original. This module already computes and verifies (2); it performs
neither (1) nor (3).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from campaigns.splitter import split_run

DEFAULT_ROOT = r"D:/twdata/runs/human"
DEFAULT_PREVIEW = r"D:/twdata/_split_preview"
ACTIVE_RUN = "20260728_143603"


def verify_report(rep: dict) -> tuple[bool, list[str], list[str]]:
    """Check the drop-nothing invariant and collect honest caveats.

    Returns (invariant_ok, problems, notes):
      * problems -- HARD reconciliation failures (parts + unattributed != original). These must
        never happen; if they do, the split is buggy and a future --commit must abort.
      * notes    -- soft caveats that do NOT break the invariant but WOULD block a --commit that
        only materialises campaign dirs: identity-less bytes with no campaign home, or timed
        streams that could not be mapped (state split stays exact regardless).
    """
    problems: list[str] = []
    notes: list[str] = []
    rec = rep["reconciliation"]
    for base, f in rec["state_log"]["files"].items():
        if not f["bytes_ok"]:
            problems.append("state bytes mismatch %s (%d+%d vs %d)"
                            % (base, f["part_bytes"], f["unattributed_bytes"], f["orig_bytes"]))
        if not f["lines_ok"]:
            problems.append("state lines mismatch %s (%s+%s vs %s)"
                            % (base, f["part_lines"], f["unattributed_lines"], f["orig_lines"]))
    for name in ("events", "ui_components", "shots"):
        s = rec[name]
        if sum(s["per"].values()) + s["shared"] != s["total"]:
            problems.append("%s sum(parts+shared) != total" % name)
    sh = rec["shots"]
    if sh["total"] != sh["files_on_disk"]:
        notes.append("shot events(%d) != files_on_disk(%d)" % (sh["total"], sh["files_on_disk"]))
    ua = rec["state_log"].get("unattributed_bytes_total", 0)
    if ua:
        notes.append("%d state bytes carry no player identity (kept as run-level shared; no "
                     "derivable campaign there)" % ua)
    if not rep.get("timed_streams_resolved", True):
        notes.append("timed streams not fully mapped (no log_tail index) -> timed parts collapse "
                     "onto campaign 1; state split still exact")
    return (not problems), problems, notes


def _plan(rep: dict) -> dict:
    """The dry-run split PLAN: what each campaign dir WOULD contain. No files are created."""
    rec = rep["reconciliation"]
    campaigns = []
    for c in rep["campaigns"]:
        i = c["index"]
        campaigns.append({
            "dir": c["id"],
            "faction": c["faction"], "subculture": c["subculture"],
            "start": c["start_wall"] or c["start_stamp"],
            "turn_span": [c["min_turn"], c["max_turn"]],
            "t_window": [c["t_start"], c["t_end"]],
            "state_segments": c["state_segments"],
            "counts": {
                "state_bytes": sum(f["parts"].get(c["id"], {}).get("bytes", 0)
                                   for f in rec["state_log"]["files"].values()),
                "state_lines": sum((f["parts"].get(c["id"], {}) or {}).get("lines", 0) or 0
                                   for f in rec["state_log"]["files"].values()),
                "events": rec["events"]["per"][i],
                "ui_components": rec["ui_components"]["per"][i],
                "shots": rec["shots"]["per"][i],
            },
        })
    return {"run": rep["run"], "n_campaigns": rep["n_campaigns"],
            "shared": rep["shared"], "campaigns": campaigns}


def migrate_run(run_dir: str, preview_dir: str, count_state_lines: bool, write: bool = True) -> dict:
    """Dry-run one run: compute the partition, verify the invariant, write report+plan to preview."""
    rep = split_run(run_dir, count_state_lines=count_state_lines)
    ok, problems, notes = verify_report(rep)
    rep["verify_ok"] = ok
    rep["verify_problems"] = problems
    rep["verify_notes"] = notes
    plan = _plan(rep)
    if write:
        out = os.path.join(preview_dir, rep["run"])
        try:
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1, default=str)
            with open(os.path.join(out, "split_plan.json"), "w", encoding="utf-8") as fh:
                json.dump(plan, fh, indent=1, default=str)
        except OSError as e:
            sys.stderr.write("migrate: preview write for %s skipped -> %s\n" % (rep["run"], repr(e)[:80]))
    return rep


def _iter_target_runs(root: str, only: str | None):
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        if name == ACTIVE_RUN:
            continue
        if only and name != only:
            continue
        yield d


def main() -> None:
    ap = argparse.ArgumentParser(description="offline dry-run campaign splitter for the run corpus")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--preview-dir", default=DEFAULT_PREVIEW)
    ap.add_argument("--run", default=None, help="limit to one run id")
    ap.add_argument("--no-state-lines", action="store_true", help="skip newline counting (fast)")
    ap.add_argument("--limit", type=int, default=0, help="cap number of runs (0 = all)")
    ap.add_argument("--no-write", action="store_true", help="compute + print only, write no reports")
    ap.add_argument("--commit", action="store_true", help="REFUSED: destructive path not implemented")
    args = ap.parse_args()

    if args.commit:
        sys.stderr.write("migrate: --commit is not implemented; this tool is dry-run only "
                         "(it never writes into or deletes original run dirs).\n")
        raise SystemExit(2)

    count_lines = not args.no_state_lines
    n_runs = n_multi = n_ok = 0
    print("dry-run campaign split over %s (excluding active run %s)\n" % (args.root, ACTIVE_RUN))
    for d in _iter_target_runs(args.root, args.run):
        if args.limit and n_runs >= args.limit:
            break
        n_runs += 1
        try:
            rep = migrate_run(d, args.preview_dir, count_lines, write=not args.no_write)
        except Exception as e:
            sys.stderr.write("migrate: run %s skipped -> %s\n" % (os.path.basename(d), repr(e)[:80]))
            continue
        n_ok += 1 if rep["verify_ok"] else 0
        n_multi += 1 if rep["n_campaigns"] > 1 else 0
        facs = ", ".join("%s(t%s..%s)" % (c["faction"], c["min_turn"] or "?", c["max_turn"])
                         for c in rep["campaigns"]) or "(no derivable campaign)"
        flag = "OK " if rep["verify_ok"] else "!! "
        print("%s%-18s campaigns=%d  %s" % (flag, rep["run"], rep["n_campaigns"], facs))
        for p in rep["verify_problems"]:
            print("       !! %s" % p)
        for nte in rep["verify_notes"]:
            print("       -  note: %s" % nte)
    print("\nsummary: %d runs scanned, %d multi-campaign, %d verified sum-of-parts==original"
          % (n_runs, n_multi, n_ok))
    if not args.no_write:
        print("reports + split plans written under %s\\<run>\\" % args.preview_dir)


if __name__ == "__main__":
    main()
