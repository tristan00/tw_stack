r"""runs -- valid recorded runs + their identity, derived from each run's own data, on demand.

No stored catalog. Every call reads each run directory's own files:
  * recorder_version    <- meta.json
  * player faction/turns <- the run's script_log (the mod's {"faction",...,"is_human":true} rows),
                            HEAD+TAIL scanned so a multi-GB log is read in a fraction of a second
  * required v5 streams  <- presence of events.jsonl, ui_components.jsonl, logs/script_log*.tail
The filesystem is the source of truth, re-derived each call, so nothing can go stale or be faked.

A run is VALID iff recorder_version == "v5" AND all required streams are present AND a player
faction is derivable. A run whose faction cannot be derived is marked UNKNOWN and excluded -- never
guessed. Multi-campaign runs (a restart into a different faction) are segmented with the same
is_human rule the extract pipeline uses.

    python runs.py            # print the valid/invalid partition of the corpus
    python runs.py --all      # (default already lists both sections)

API: list_valid_runs(roots=None), list_runs(roots=None), get_run_data(run_id, roots=None).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

VALID_VERSIONS = ("v5", "v6")   # v5 = monolith record.py; v6 = decomposed tw_stack recorder
_REQUIRED_FILES = ("events.jsonl", "ui_components.jsonl")
_REQUIRED_LOG_GLOBS = ("logs/script_log_*.tail", "logs/script_log_*.txt")

HEAD_BYTES = 2_000_000
TAIL_BYTES = 2_000_000

_FACTION_HUMAN = re.compile(rb'"faction":"([a-z0-9_]+)","is_human":true')
_FACTION_TURN = re.compile(rb'"kind":"faction","turn":(\d+),"faction":"[a-z0-9_]+","is_human":true')
_TURN = re.compile(rb'"turn":(\d+)')

_RACES = {"cth", "vmp", "emp", "grn", "dwf", "nor", "bst", "wef", "hef", "def", "skv",
          "kho", "tze", "nur", "sla", "dae", "lzd", "tmb", "ogr", "chd", "cst", "vco"}


def default_roots() -> list[str]:
    """Run roots to scan. Override via $TW_RUNS_ROOT (os.pathsep-separated) or the roots= param."""
    env = os.environ.get("TW_RUNS_ROOT")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [r"D:/twdata/runs/human"]


def _readable_lord(faction_key: str) -> str:
    """Best-effort human-readable label from a faction key (title-cased tail after the race code)."""
    if not faction_key:
        return "?"
    parts = faction_key.split("_")
    idx = 0
    for i, p in enumerate(parts):
        if p in _RACES:
            idx = i + 1
            break
    tail = parts[idx:] if idx and idx < len(parts) else parts[-2:]
    return " ".join(w.capitalize() for w in tail) or faction_key


def _session_logs(run: str) -> list[str]:
    """Every script_log belonging to a run dir (logs/*.tail or bare script_log_*.txt), sorted."""
    files = glob.glob(os.path.join(run, "logs", "script_log_*.tail"))
    files += glob.glob(os.path.join(run, "logs", "script_log_*.txt"))
    files += glob.glob(os.path.join(run, "script_log_*.txt"))
    return sorted(files)


def _scan_log(path: str) -> dict | None:
    """HEAD+TAIL scan of one script_log for {file, human, min_turn, max_turn}; None if no game-state."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
            if size > HEAD_BYTES + TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                tail = f.read(TAIL_BYTES)
            else:
                tail = b""
    except OSError as e:
        sys.stderr.write("runs._scan_log: %s -> %s\n" % (os.path.basename(path), repr(e)[:60]))
        return None
    bare = [int(t) for t in _TURN.findall(head)] + [int(t) for t in _TURN.findall(tail)]
    if not bare:
        return None
    mh = _FACTION_HUMAN.search(head) or _FACTION_HUMAN.search(tail)
    human = mh.group(1).decode() if mh else None
    ft_head = [int(t) for t in _FACTION_TURN.findall(head)]
    return {"file": path, "human": human,
            "min_turn": min(ft_head) if ft_head else 0, "max_turn": max(bare)}


def _segment(scans: list[dict]) -> list[dict]:
    """Merge chronological session scans into campaigns: same player faction continuing/reloading is
    one campaign; a faction change or a same-faction restart from turn 1 starts a new one."""
    camps: list[dict] = []
    for s in scans:
        cur = camps[-1] if camps else None
        if s["human"] is None:                       # reload-continuation log: attach to current
            if cur is not None:
                cur["files"].append(s["file"])
                cur["max_turn"] = max(cur["max_turn"], s["max_turn"])
            continue
        same = (cur is not None and s["human"] == cur["human"]
                and not (s["min_turn"] == 1 and cur["max_turn"] > 1))
        if same:
            cur["files"].append(s["file"])
            cur["max_turn"] = max(cur["max_turn"], s["max_turn"])
        else:
            camps.append({"files": [s["file"]], "human": s["human"],
                          "min_turn": s["min_turn"], "max_turn": s["max_turn"]})
    return camps


def _meta(run: str) -> dict:
    try:
        return json.load(open(os.path.join(run, "meta.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # intentional: meta.json legitimately absent/partial for some runs -> {} (not logged)


def scan_run(run: str) -> dict:
    """Derive one run directory's identity + validity from its own files. Identity is never guessed:
    a run with no derivable is_human faction is UNKNOWN and not valid."""
    meta = _meta(run)
    version = meta.get("recorder_version")
    version = str(version) if version is not None else None

    streams = {f: os.path.isfile(os.path.join(run, f)) for f in _REQUIRED_FILES}
    streams["logs/script_log"] = any(glob.glob(os.path.join(run, g)) for g in _REQUIRED_LOG_GLOBS)

    scans = [s for s in (_scan_log(f) for f in _session_logs(run)) if s]
    camps = [c for c in _segment(scans) if c["human"]]
    campaigns = [{"faction": c["human"], "lord": _readable_lord(c["human"]),
                  "min_turn": c["min_turn"], "max_turn": c["max_turn"]} for c in camps]

    reasons: list[str] = []
    if version not in VALID_VERSIONS:
        reasons.append("recorder_version=%r (need one of %r)" % (version, VALID_VERSIONS))
    for f, ok in streams.items():
        if not ok:
            reasons.append("missing stream: %s" % f)
    if not campaigns:
        reasons.append("no derivable player faction (is_human) -> identity UNKNOWN")

    return {
        "run": os.path.basename(run),
        "path": run,
        "recorder_version": version,
        "player_faction": campaigns[0]["faction"] if len(campaigns) == 1 else None,
        "campaigns": campaigns,
        "max_turn": max((c["max_turn"] for c in campaigns), default=0),
        "streams": streams,
        "valid": not reasons,
        "reasons": reasons,
    }


def _iter_run_dirs(roots: list[str] | None):
    for root in roots or default_roots():
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if os.path.isdir(d):
                yield d


def list_runs(roots: list[str] | None = None) -> list[dict]:
    """Every run in the roots, each with its derived record (valid + invalid + reasons)."""
    return [scan_run(d) for d in _iter_run_dirs(roots)]


def list_valid_runs(roots: list[str] | None = None) -> list[dict]:
    """Only runs that pass the v5 validity gate."""
    return [r for r in list_runs(roots) if r["valid"]]


def list_valid_campaigns(roots: list[str] | None = None, min_turns: int = 0) -> list[dict]:
    """Valid runs flattened to their campaigns -- the real 'which run' unit, since runs are often
    multi-campaign (a restart into a different faction). Each entry:
    {run, path, recorder_version, faction, lord, min_turn, max_turn}. Filter with min_turns."""
    out = []
    for r in list_valid_runs(roots):
        for c in r["campaigns"]:
            if c["max_turn"] >= min_turns:
                out.append({"run": r["run"], "path": r["path"],
                            "recorder_version": r["recorder_version"],
                            "faction": c["faction"], "lord": c["lord"],
                            "min_turn": c["min_turn"], "max_turn": c["max_turn"]})
    return out


def get_run_data(run_id: str, roots: list[str] | None = None) -> dict | None:
    """One run's record by directory name, or None if not found in the roots."""
    for d in _iter_run_dirs(roots):
        if os.path.basename(d) == run_id:
            return scan_run(d)
    return None


def main() -> None:
    recs = list_runs()
    valid = [r for r in recs if r["valid"]]
    invalid = [r for r in recs if not r["valid"]]
    print("VALID v5 runs (%d):" % len(valid))
    for r in sorted(valid, key=lambda x: x["run"]):
        print("  %-18s ver=%s  campaigns=%d" % (r["run"], r["recorder_version"], len(r["campaigns"])))
        for c in sorted(r["campaigns"], key=lambda c: -c["max_turn"]):
            print("      %-36s turns %s..%s" % (c["faction"], c["min_turn"] or "?", c["max_turn"]))
    print("\nINVALID / excluded (%d):" % len(invalid))
    for r in sorted(invalid, key=lambda x: x["run"]):
        print("  %-18s ver=%-4s -> %s" % (r["run"], r["recorder_version"], "; ".join(r["reasons"])[:90]))
    subst = list_valid_campaigns(min_turns=5)
    print("\nvalid campaigns >= 5 turns (%d):" % len(subst))
    for c in sorted(subst, key=lambda c: -c["max_turn"]):
        print("  %-18s %-36s t%s..%s" % (c["run"], c["faction"], c["min_turn"] or "?", c["max_turn"]))


if __name__ == "__main__":
    main()
