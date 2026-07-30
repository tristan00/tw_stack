r"""structurer -- FIRST PASS (exploratory).

Parse a captured WH3 run's raw log into structured, PLAYER-ATTRIBUTED events, and derive DATA VIEWS
from them on request. This is a discovery tool, NOT the final structurer/DB: each view we build
reveals what the eventual schema + capture must record (see TODO.md for gaps found so far).

Core:
    player_faction(run_dir)                     -> the is_human faction (never guessed)
    parse_events(run_dir, turn=, player_only=)  -> structured TWSTATE event dicts (adds 't' game-time)

Views:
    semantic_actions(run_dir, turn)             -> [{t, action, detail}] player's deliberate actions

CLI:
    python structurer.py <run_dir> [turn]       -> print the semantic-action table
"""
import glob
import json
import os
import re
import sys

GT = re.compile(r'<([\d.]+)s>')
HUMAN = re.compile(r'"faction":"([a-z0-9_]+)","is_human":true')

# Reuse the ONE campaign-boundary kernel (the same one runs.py and the recorder's live swap use)
# instead of duplicating a weaker per-file heuristic here. Imported best-effort so structurer keeps
# working (via the local fallback below) if the campaigns repo is not on the path.
_CAMPAIGNS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "campaigns")
if _CAMPAIGNS_DIR not in sys.path:
    sys.path.insert(0, _CAMPAIGNS_DIR)
try:
    import splitter as _splitter                            # noqa: E402
except Exception as e:                                      # noqa: BLE001 -- degrade to local scan
    _splitter = None
    sys.stderr.write("structurer: splitter unavailable (using local campaign scan) -> %s\n" % repr(e)[:80])


# ---- core -----------------------------------------------------------------------------------
def main_script_log(run_dir):
    """The SUBSTANTIAL campaign's log for this run: the LARGEST script_log tail. A run dir often
    holds several campaigns (restarts); the NEWEST is frequently a short restart, so pick the biggest
    (most bytes = most turns played) -- avoids the recurring multi-campaign 'wrong campaign' trap.
    (A run with two genuinely large campaigns is still ambiguous; then split by log -- see TODO.)"""
    c = glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail"))
    return max(c, key=os.path.getsize) if c else None


newest_script_log = main_script_log      # back-compat alias (now = the substantial campaign)


def _resolve_log(run_dir_or_log):
    """Accept an explicit script_log tail PATH, or a run dir (-> its substantial campaign log)."""
    if os.path.isfile(run_dir_or_log):
        return run_dir_or_log
    return main_script_log(run_dir_or_log)


def _scan_campaign(log):
    """Cheap HEAD+TAIL scan of one script_log tail -> (is_human faction, min turn, max turn),
    without reading the whole (possibly multi-GB) file."""
    fac = tmin = tmax = None
    try:
        size = os.path.getsize(log)
        with open(log, "rb") as f:
            head = f.read(2_000_000).decode("utf-8", "replace")
            m = HUMAN.search(head)
            fac = m.group(1) if m else None
            hts = re.findall(r'"turn":(\d+)', head)
            if hts:
                tmin = min(int(x) for x in hts)
            f.seek(max(0, size - 2_000_000))
            tail = f.read().decode("utf-8", "replace")
            tts = re.findall(r'"turn":(\d+)', tail)
            if tts:
                tmax = max(int(x) for x in tts)
    except OSError as e:
        sys.stderr.write("structurer._scan_campaign: %s -> %s\n" % (os.path.basename(log), repr(e)[:60]))
    return fac, tmin, tmax


def list_campaigns(run_dir):
    """EVERY distinct campaign captured in a run dir, ONE entry per campaign, largest first.

    A run dir can hold several campaigns (quit-to-menu + new faction, or the recorder tailing across
    game relaunches); an R3/R4 PER-CAMPAIGN dir holds exactly one. Boundaries are detected by the
    shared splitter kernel (campaigns/splitter.py) -- the SAME rule runs.py and the recorder's live
    swap use -- so structurer, the corpus scanner, and the migration all agree on which campaigns
    exist. A per-campaign dir therefore yields exactly its one campaign, and a flat multi-campaign
    run yields exactly the campaigns it would be split/migrated into.

    Each entry: {"log","logs","faction","subculture","min_turn","max_turn","bytes","campaign_id",
    "campaign_index"}.  `log` is the campaign's largest state-log file (back-compat: a whole-file
    reader uses it as the campaign's representative); `logs` is ALL of the campaign's files
    (largest-first) so a caller that wants every turn reads them all and drops nothing -- a campaign
    that spans several relaunch sessions owns several files. Falls back to a local per-file scan if
    the splitter is unavailable."""
    if _splitter is None:
        return _list_campaigns_local(run_dir)
    try:
        camps = _splitter.detect_campaigns(run_dir)
    except Exception as e:                                  # noqa: BLE001 -- never fail discovery
        sys.stderr.write("structurer.list_campaigns: splitter scan failed for %s -> %s; using local\n"
                         % (os.path.basename(str(run_dir).rstrip("/\\")), repr(e)[:80]))
        return _list_campaigns_local(run_dir)
    out = []
    for c in camps:
        fac = c.get("faction")
        if not fac:                                         # identity-less fragment -> not a campaign
            continue
        files, seen = [], set()
        for seg in c.get("state_segments", []):
            f = seg["file"]
            if f not in seen:
                seen.add(f)
                files.append(f)
        if not files:
            continue
        files.sort(key=lambda f: -(os.path.getsize(f) if os.path.isfile(f) else 0))
        out.append({"log": files[0], "logs": files, "faction": fac,
                    "subculture": c.get("subculture"),
                    "min_turn": c.get("min_turn"), "max_turn": c.get("max_turn"),
                    "bytes": sum(os.path.getsize(f) for f in files if os.path.isfile(f)),
                    "campaign_id": c.get("id_str"), "campaign_index": c.get("index")})
    out.sort(key=lambda c: -c["bytes"])
    return out


def _list_campaigns_local(run_dir):
    """Fallback boundary scan (used only if the shared splitter cannot be imported). Per-file
    heuristic: each script_log tail with an is_human faction is a campaign; a tail whose turn-span
    is a subset of a larger same-faction tail (the same campaign re-captured) is dropped. Kept for
    resilience -- the splitter path above is preferred and agrees with runs.py + the migration."""
    cands = []
    for log in glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail")):
        fac, tmin, tmax = _scan_campaign(log)
        if fac and tmax is not None:
            cands.append({"log": log, "logs": [log], "faction": fac,
                          "min_turn": tmin if tmin is not None else tmax,
                          "max_turn": tmax, "bytes": os.path.getsize(log)})
    cands.sort(key=lambda c: -c["bytes"])
    kept = []
    for c in cands:
        subset = any(k["faction"] == c["faction"] and k["min_turn"] <= c["min_turn"]
                     and k["max_turn"] >= c["max_turn"] for k in kept)
        if not subset:
            kept.append(c)
    return kept


def player_faction(run_dir):
    """The player's faction key, read from the first is_human:true row (never guessed/substringed).
    Head-bounded: the is_human faction is a session-start record (~1.2 MB in), so a 4 MB head read
    avoids streaming a whole multi-GB tail; full-scan fallback only if a header-less tail lacks it."""
    sl = _resolve_log(run_dir)
    if not sl:
        return None
    with open(sl, "rb") as f:
        m = HUMAN.search(f.read(4_000_000).decode("utf-8", "replace"))
    if m:
        return m.group(1)
    with open(sl, encoding="utf-8", errors="replace") as f:   # fallback: full scan (rare)
        for line in f:
            m = HUMAN.search(line)
            if m:
                return m.group(1)
    return None


_PARSE_CACHE = {}          # (abspath, mtime, turn, player_only) -> events list. PER-PROCESS memo so the
_PARSE_CACHE_MAX = 96      # 3 offline consumers (build_decisions/recruit_pool/dilemmas) parse each log
                           # ONCE, not 3x. mtime is in the key so a LIVE-growing .tail invalidates
                           # (runtime.py re-scans the active run) -> live-safe; bounded (FIFO) to cap
                           # memory. Callers iterate the list read-only (verified), so sharing is safe.


def parse_events(run_dir, turn=None, player_only=True):
    """Structured TWSTATE 'event' records in chronological (capture) order; each gets 't' = the
    game-time seconds from the log line prefix. When player_only, keep only the player's events:
    a *_faction field == the player faction, OR no faction field at all AND in_player_turn (the
    recorder attributes faction-less player events -- e.g. UI clicks -- via in_player_turn).

    Memoized per (log, mtime, turn, player_only): byte-identical to recomputing (pure function of the
    file bytes), it just returns the SAME list the first caller built instead of re-streaming the log.
    """
    sl = _resolve_log(run_dir)
    if not sl:
        return []
    ckey = None
    if not os.environ.get("ADVISOR_NO_MEMO"):              # baseline-measurement / identity-proof switch
        try:
            ckey = (os.path.abspath(sl), os.path.getmtime(sl), turn, player_only)
        except OSError:
            ckey = None
    if ckey is not None and ckey in _PARSE_CACHE:
        return _PARSE_CACHE[ckey]
    player = player_faction(run_dir)
    out = []
    _bad = 0
    with open(sl, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"kind":"event"' not in line or "TWSTATE " not in line:   # prefilter: ~18% of TWSTATE
                continue
            head, js = line.split("TWSTATE ", 1)
            try:
                e = json.loads(js)
            except Exception:
                _bad += 1           # hot per-line parse: count, don't log each
                continue
            if e.get("kind") != "event":
                continue
            if turn is not None and e.get("turn") != turn:
                continue
            if player_only:
                facs = [v for k, v in e.items() if "faction" in k and v]
                if facs:
                    if player not in facs:
                        continue
                elif not e.get("in_player_turn"):
                    continue
            m = GT.search(head)
            e["t"] = float(m.group(1)) if m else 0.0
            out.append(e)
    if _bad:
        sys.stderr.write("structurer.parse_events: skipped %d malformed TWSTATE lines\n" % _bad)
    if ckey is not None:
        if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
            _PARSE_CACHE.pop(next(iter(_PARSE_CACHE)))          # bounded FIFO eviction
        _PARSE_CACHE[ckey] = out
    return out


def iter_twstate(run_dir, turn=None):
    """EVERY TWSTATE record (any kind -- event / player_snapshot / resource change / ...) in capture
    order, each tagged with `seq` (1-based) and `t` (game-time seconds). The full game-state timeline;
    views filter it as needed. Not player-restricted."""
    sl = _resolve_log(run_dir)
    if not sl:
        return
    seq = 0
    with open(sl, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "TWSTATE " not in line:
                continue
            head, js = line.split("TWSTATE ", 1)
            try:
                r = json.loads(js)
            except Exception:
                continue  # intentional: skip malformed TWSTATE line, hot per-line generator (no per-line log)
            if turn is not None and r.get("turn") != turn:
                continue
            m = GT.search(head)
            r["t"] = float(m.group(1)) if m else 0.0
            seq += 1
            r["seq"] = seq
            yield r


# ---- view: semantic actions -----------------------------------------------------------------
def _strip_unit(component):
    u = component[:-len("_recruitable")] if component.endswith("_recruitable") else component
    return re.sub(r'_\d+$', '', u)


# event -> (label, detail). Raw/opaque values are kept raw (see TODO.md); no interpretation added.
ACTIONS = {
    "CharacterFinishedMovingEvent":
        lambda e: ("Move character", "%s -> (%s,%s)" % (e.get("char_subtype"), e.get("x"), e.get("y"))),
    "CharacterCharacterTargetAction":
        # `agent_action_key` is the specific action (e.g. wh2_main_agent_action_champion_hinder_agent_
        # assassinate) and is ALWAYS present on this event (verified: 100% across every recorded run).
        # `ability` is a COARSER family token ("hinder_agent") -- a different value, never a substitute --
        # so the detail reads the one authoritative field, not an ability-vs-key guess.
        lambda e: ("Character targeted action", "%s -> char %s" % (e.get("agent_action_key"), e.get("target_char"))),
    "BattleBeingFought":
        lambda e: ("Battle%s" % (" (autoresolved)" if e.get("autoresolved") else ""), ""),
    "CharacterBesiegesSettlement":
        lambda e: ("Besiege settlement", e.get("region")),
    "CharacterPostBattleCaptureOption":
        lambda e: ("Post-battle captive decision", e.get("captive_outcome_key")),
    "CharacterPerformsSettlementOccupationDecision":
        lambda e: ("Settlement occupation decision", "%s (option %s)" % (e.get("garrison"), e.get("occupation_decision"))),
    "ResearchStarted":
        lambda e: ("Start research", e.get("tech")),
    "BuildingConstructionIssuedByPlayer":
        lambda e: ("Construct building", "in %s" % e.get("garrison")),
    "ForceAdoptsStance":
        lambda e: ("Adopt army stance", "force %s, stance %s" % (e.get("mf_cqi"), e.get("stance"))),
    "CharacterSkillPointAllocated":
        lambda e: ("Allocate skill point", "%s (%s)" % (e.get("skill"), e.get("char_subtype"))),
    "FactionAboutToEndTurn":
        lambda e: ("End turn", ""),
}


def _classify_turn(events):
    """Classify ONE turn's player events into semantic-action dicts, applying the audit-fixed rules:
      * a CharacterSkillPointAllocated in the SAME game-second as a CharacterRankUp for the same
        character is an AUTO rank-up allocation, not a chosen skill -> dropped.
      * events after the player's FactionAboutToEndTurn are turn-end side-effects -> stop.
    Recruitment is read from the `_recruitable` click (pool local/global not recoverable -- see TODO).
    """
    rankups = {(e.get("char_cqi"), round(e["t"], 1)) for e in events if e.get("event") == "CharacterRankUp"}
    out = []
    for e in events:
        ev = e.get("event")
        if ev in ACTIONS:
            if ev == "CharacterSkillPointAllocated" and (e.get("char_cqi"), round(e["t"], 1)) in rankups:
                continue
            label, detail = ACTIONS[ev](e)
            out.append({"t": e["t"], "action": label, "detail": detail})
            if ev == "FactionAboutToEndTurn":
                break
        elif ev == "ComponentLClickUp" and (e.get("component") or "").endswith("_recruitable"):
            out.append({"t": e["t"], "action": "Recruit unit",
                        "detail": "%s (pool: unknown -- see TODO)" % _strip_unit(e["component"])})
    return out


def semantic_actions(run_dir, turn):
    """The player's deliberate semantic actions for `turn`, in order (see _classify_turn)."""
    return _classify_turn(parse_events(run_dir, turn=turn, player_only=True))


def semantic_actions_by_turn(run_dir):
    """{turn: [action dicts]} for every turn the player acted, parsed in a single pass."""
    from collections import defaultdict
    by_turn = defaultdict(list)
    for e in parse_events(run_dir, turn=None, player_only=True):
        by_turn[e.get("turn")].append(e)
    return {t: _classify_turn(evs) for t, evs in sorted(by_turn.items()) if t is not None}


def main():
    run_dir = sys.argv[1]
    turn = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rows = semantic_actions(run_dir, turn)
    print("run    : %s" % run_dir)
    print("player : %s   |   turn %d   |   %d semantic actions" % (player_faction(run_dir), turn, len(rows)))
    print()
    print("  #  | t(s)   | action                              | detail")
    print("-----+--------+-------------------------------------+" + "-" * 55)
    for i, r in enumerate(rows, 1):
        print(" %3d | %6.1f | %-35s | %s" % (i, r["t"], r["action"], r["detail"]))


if __name__ == "__main__":
    main()
