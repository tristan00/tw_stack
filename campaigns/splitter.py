r"""splitter -- detect campaign boundaries in a recorded run and partition every stream.

WHY
    One run directory (D:/twdata/runs/human/<run_id>/) can contain MORE THAN ONE campaign:
    the player quits to menu and starts a new faction, or the recorder keeps tailing across
    game relaunches. Downstream code that assumes "one run == one campaign" then mixes two
    factions' data together. This module cuts a run cleanly along campaign boundaries.

BOUNDARY DETECTION (pure, reusable -- the recorder can call the same logic live)
    The game's script_log carries the mod's per-faction summary rows:
        TWSTATE {"kind":"faction",...,"is_human":true,"faction":..,"subculture":..,"turn":..}
    The human faction's (faction, subculture) IS the campaign identity. A new campaign begins
    when that identity changes, or when the same faction restarts from turn 1 after having
    progressed past it (a fresh game, not a reload-continuation). This is exactly the rule the
    runs.py corpus scanner already uses; here it is generalised to also split WITHIN a single
    script_log file (quit-to-menu + new game in one process) when a file's head identity differs
    from its tail identity. `segment_blocks()` is the pure kernel; `CampaignTracker` is the
    incremental form a live recorder would feed one faction row at a time.

STREAM CORRELATION (offline)
    * state log  : each campaign owns exact BYTE RANGES in each script_log*.tail (line-aligned),
                   so state lines are attributed exactly, no clock involved.
    * events / ui / shots : these are keyed by recorder-seconds `t`. The recorder's own
                   log_tail events append `bytes` to each .tail, so the running cumulative sum of
                   `bytes` per .tail is an exact BYTE-OFFSET -> recorder-t map. Each campaign's
                   first state byte maps to a cut point in t; the resulting non-overlapping
                   t-windows tile the whole timeline, so every event/ui/shot lands in exactly one
                   campaign (the first campaign absorbs pre-session recorder preamble).

INVARIANT
    For every stream, sum(per-campaign parts) + shared/unattributed == original. Proven by
    split_run()'s reconciliation block. Auxiliary .tail logs (mp_log, lua_mod_log) and meta.json
    cannot be split by faction and are reported as run-level `shared`.

    python splitter.py <run-dir>        # print campaigns + reconciliation for one run

API: split_run(run), detect_campaigns(run), segment_blocks(blocks), CampaignTracker.
"""
from __future__ import annotations

import bisect
import glob
import json
import os
import re
import sys

HEAD_BYTES = 2_000_000
TAIL_BYTES = 2_000_000
INF = float("inf")

# The player-faction summary row. We require both markers on the same line before trusting it.
_HUMAN_MARK = b'"is_human":true'
_FACTION_MARK = b'"kind":"faction"'
_RE_FACTION = re.compile(rb'"faction":"([a-z0-9_]+)"')
_RE_SUBCULTURE = re.compile(rb'"subculture":"([a-z0-9_]+)"')
_RE_TURN = re.compile(rb'"turn":(\d+)')
_RE_GAME_T = re.compile(rb"<([\d.]+)s>")
_RE_LOGSTAMP = re.compile(r"script_log_(\d{6})_(\d{4})")


# --------------------------------------------------------------------------- #
# pure boundary kernel                                                         #
# --------------------------------------------------------------------------- #
def _is_new_campaign(cur: dict | None, ident: tuple, min_turn: int, restart_turn: int) -> bool:
    """The one boundary rule, shared by the batch segmenter and the live tracker.
    New campaign iff there is no current one, OR the human identity changed, OR the same
    faction restarted from an early turn after having progressed past it (fresh game)."""
    if cur is None:
        return True
    if ident != cur["id"]:
        return True
    if min_turn and min_turn <= restart_turn and cur["max_turn"] > restart_turn:
        return True
    return False


def segment_blocks(blocks: list[dict], restart_turn: int = 1) -> tuple[list[dict], list[dict]]:
    """Merge ordered identity BLOCKS into campaigns (pure; no I/O).

    Each block is {id:(faction,subculture)|None, min_turn, max_turn, ...extra}. A block whose id
    is None carries no player identity (a frontend/menu/reload fragment): it attaches to the
    CURRENT campaign as a continuation, or -- if it precedes any campaign -- forward to the first
    campaign, so its bytes are never lost yet no phantom 'unknown' campaign is minted.

    Returns (campaigns, orphan_blocks). `campaigns` are the real (faction,subculture) spans, each
    {id, faction, subculture, min_turn, max_turn, blocks:[...]}. `orphan_blocks` are leading
    identity-less blocks in a run that NEVER reaches a real identity (nothing to attach to); the
    caller accounts for them as unattributed. Every input block lands in exactly one place."""
    camps: list[dict] = []
    pending: list[dict] = []                     # leading identity-less blocks, attach forward
    for b in blocks:
        cur = camps[-1] if camps else None
        if b["id"] is None:                      # no identity -> continuation of current campaign
            if cur is not None:
                cur["blocks"].append(b)
                cur["max_turn"] = max(cur["max_turn"], b.get("max_turn", 0))
            else:
                pending.append(b)                # nothing to attach to yet; hold for first campaign
            continue
        if _is_new_campaign(cur, b["id"], b.get("min_turn", 0), restart_turn):
            nc = _new_camp(b)
            if not camps and pending:            # first real campaign absorbs leading fragments
                nc["blocks"] = pending + nc["blocks"]
                pending = []
            camps.append(nc)
        else:
            cur["blocks"].append(b)
            cur["max_turn"] = max(cur["max_turn"], b.get("max_turn", 0))
            if b.get("min_turn"):
                cur["min_turn"] = min(cur["min_turn"] or b["min_turn"], b["min_turn"])
    return camps, pending


def _new_camp(b: dict) -> dict:
    ident = b["id"] or (None, None)
    return {"id": b["id"], "faction": ident[0], "subculture": ident[1],
            "min_turn": b.get("min_turn", 0), "max_turn": b.get("max_turn", 0),
            "blocks": [b]}


class CampaignTracker:
    """Incremental form of the boundary kernel, for live use inside the recorder.

    Feed each observed human-faction row via observe(); it returns True on the row that STARTS
    a new campaign (the recorder would swap its output directory at that point). Same rule as
    segment_blocks(), so offline splits and live splits agree by construction."""

    def __init__(self, restart_turn: int = 1):
        self.restart_turn = restart_turn
        self.cur: dict | None = None
        self.index = -1

    def observe(self, faction: str | None, subculture: str | None, turn: int | None) -> bool:
        if faction is None:                      # continuation row -> stays in current campaign
            return False
        ident = (faction, subculture)
        mn = int(turn) if isinstance(turn, int) else 0
        if _is_new_campaign(self.cur, ident, mn, self.restart_turn):
            self.cur = {"id": ident, "min_turn": mn, "max_turn": mn}
            self.index += 1
            return True
        self.cur["max_turn"] = max(self.cur["max_turn"], mn)
        return False


def scan_state_rows(chunk: bytes):
    """Yield (faction, subculture, turn, line_offset) for every human-faction row in a raw byte
    `chunk`, where line_offset is the byte position of that line's START within the chunk.

    The live recorder feeds these to a CampaignTracker; line_offset lets a tail chunk be split
    byte-exactly at a campaign boundary (bytes before the boundary line -> old dir, from it on ->
    new dir), so live splits are byte-identical to what the offline splitter would compute. Uses
    the same _parse_human_line kernel as the offline path, so the two agree by construction."""
    off = 0
    for raw in chunk.splitlines(keepends=True):
        p = _parse_human_line(raw)
        if p is not None:
            yield p["faction"], p["subculture"], p["turn"], off
        off += len(raw)


# --------------------------------------------------------------------------- #
# state-log scanning -> identity blocks                                        #
# --------------------------------------------------------------------------- #
def _parse_human_line(raw: bytes) -> dict | None:
    """(faction, subculture, turn) from one is_human faction row, or None if not one."""
    if _HUMAN_MARK not in raw or _FACTION_MARK not in raw:
        return None
    mf = _RE_FACTION.search(raw)
    if not mf:
        return None
    ms = _RE_SUBCULTURE.search(raw)
    mt = _RE_TURN.search(raw)
    return {"faction": mf.group(1).decode(), "subculture": ms.group(1).decode() if ms else None,
            "turn": int(mt.group(1)) if mt else None}


def _scan_head_tail(path: str, size: int) -> dict:
    """Cheap HEAD+TAIL identity probe of one script_log. Returns head/tail identity, turn range,
    and whether the head identity differs from the tail identity (-> needs a full scan)."""
    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
        if size > HEAD_BYTES + TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            tail = f.read(TAIL_BYTES)
        else:
            tail = b""
    head_id = tail_id = None
    turns: list[int] = []
    hmin: list[int] = []
    for chunk, is_head in ((head, True), (tail, False)):
        for raw in chunk.split(b"\n"):
            p = _parse_human_line(raw)
            if not p:
                continue
            ident = (p["faction"], p["subculture"])
            if is_head and head_id is None:
                head_id = ident
            tail_id = ident
            if p["turn"] is not None:
                turns.append(p["turn"])
                if is_head:
                    hmin.append(p["turn"])
    # bare turn ints across head+tail widen the max-turn estimate cheaply
    for chunk in (head, tail):
        turns += [int(t) for t in _RE_TURN.findall(chunk)]
    return {"head_id": head_id, "tail_id": tail_id,
            "min_turn": min(hmin) if hmin else (min(turns) if turns else 0),
            "max_turn": max(turns) if turns else 0,
            "split": head_id is not None and tail_id is not None and head_id != tail_id}


def _full_scan_blocks(path: str) -> list[dict]:
    """Stream one script_log start-to-end and cut it into line-aligned identity blocks.
    Used only when HEAD+TAIL shows the file's identity changes mid-file (rare)."""
    blocks: list[dict] = []
    off = 0
    cur: dict | None = None
    try:
        with open(path, "rb") as f:
            for raw in f:
                ln = len(raw)
                p = _parse_human_line(raw)
                if p is not None:
                    ident = (p["faction"], p["subculture"])
                    tn = p["turn"] or 0
                    if cur is None or ident != cur["id"] or (
                            tn and tn <= 1 and cur["max_turn"] > 1):
                        cur = {"file": path, "byte_lo": off, "byte_hi": off, "id": ident,
                               "min_turn": tn, "max_turn": tn}
                        blocks.append(cur)
                    else:
                        cur["max_turn"] = max(cur["max_turn"], tn)
                off += ln
    except OSError as e:
        sys.stderr.write("splitter: full-scan %s skipped -> %s\n" % (os.path.basename(path), repr(e)[:80]))
        return []
    # extend each block's byte_hi to the next block's start (line-aligned), last to EOF
    for i, b in enumerate(blocks):
        b["byte_lo"] = 0 if i == 0 else blocks[i]["byte_lo"]
        b["byte_hi"] = blocks[i + 1]["byte_lo"] if i + 1 < len(blocks) else off
    if blocks:
        blocks[0]["byte_lo"] = 0
    return blocks


def _file_blocks(path: str) -> list[dict]:
    """Identity blocks for one script_log.tail. Fast path: one block for the whole file when the
    identity is constant (the usual case, one game session per log). Full-scan only on mid-file
    identity change. A file with no is_human row at all becomes a single continuation block."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        sys.stderr.write("splitter: stat %s skipped -> %s\n" % (os.path.basename(path), repr(e)[:80]))
        return []
    ht = _scan_head_tail(path, size)
    if ht["split"]:
        blk = _full_scan_blocks(path)
        if blk:
            return blk
    ident = ht["head_id"] or ht["tail_id"]      # head-or-tail, mirroring runs.py
    return [{"file": path, "byte_lo": 0, "byte_hi": size, "id": ident,
             "min_turn": ht["min_turn"], "max_turn": ht["max_turn"]}]


def _session_logs(run: str) -> list[str]:
    """Every state-stream script_log for a run, chronologically (filename carries launch time)."""
    files = glob.glob(os.path.join(run, "logs", "script_log_*.tail"))
    files += glob.glob(os.path.join(run, "logs", "script_log_*.txt"))
    files += glob.glob(os.path.join(run, "script_log_*.txt"))
    return sorted(set(files))


def _logstamp(path: str) -> str | None:
    """The DDMMYY_HHMM launch stamp embedded in a script_log filename, if present."""
    m = _RE_LOGSTAMP.search(os.path.basename(path))
    return "%s_%s" % (m.group(1), m.group(2)) if m else None


def _readable_start(stamp: str | None) -> str | None:
    """script_log stamp DDMMYY_HHMM -> 'YYYY-MM-DD HH:MM' wall label, best effort."""
    if not stamp:
        return None
    try:
        d, hm = stamp.split("_")
        return "20%s-%s-%s %s:%s" % (d[4:6], d[2:4], d[0:2], hm[0:2], hm[2:4])
    except Exception as e:                       # noqa: BLE001 -- label is cosmetic
        sys.stderr.write("splitter: start-label skipped -> %s\n" % repr(e)[:80])
        return None


# --------------------------------------------------------------------------- #
# byte-offset -> recorder-t index (from the recorder's own log_tail events)    #
# --------------------------------------------------------------------------- #
def build_byte_time_index(run: str) -> dict:
    """Per-.tail (cum_end_bytes, t) tables from log_tail events: the running size of each .tail at
    recorder-time t. Lets us map any byte offset in a .tail back to when the recorder wrote it."""
    idx: dict[str, dict] = {}
    p = os.path.join(run, "events.jsonl")
    if not os.path.isfile(p):
        return idx
    bad = 0
    try:
        for ln in open(p, encoding="utf-8", errors="replace"):
            try:
                o = json.loads(ln)
            except Exception:
                bad += 1
                continue
            if o.get("kind") != "log_tail":
                continue
            dst = os.path.basename(str(o.get("dst", "")))
            if "script_log" not in dst:
                continue
            t = o.get("t")
            nb = o.get("bytes")
            if t is None or nb is None:
                continue
            e = idx.setdefault(dst, {"cum": [], "t": [], "total": 0})
            e["total"] += int(nb)
            e["cum"].append(e["total"])
            e["t"].append(float(t))
    except OSError as e:
        sys.stderr.write("splitter: byte-time index skipped -> %s\n" % repr(e)[:80])
    if bad:
        sys.stderr.write("splitter: byte-time index skipped %d malformed event lines\n" % bad)
    return idx


def _byte_to_t(idx: dict, dst_basename: str, off: int) -> float | None:
    """recorder-t at which byte offset `off` of a .tail had been written; None if unmapped."""
    e = idx.get(dst_basename)
    if not e or not e["cum"]:
        return None
    i = bisect.bisect_left(e["cum"], off)        # first chunk whose cum_end >= off
    if i >= len(e["t"]):
        i = len(e["t"]) - 1
    return e["t"][i]


# --------------------------------------------------------------------------- #
# detection: campaigns + state byte-ranges + recorder-t windows               #
# --------------------------------------------------------------------------- #
def detect_campaigns(run: str) -> list[dict]:
    """Campaigns in a run: identity, turn span, exact state byte-ranges, and a recorder-t window.
    The t-windows are non-overlapping and tile [0, +inf) so every timed record maps to exactly one
    campaign (campaign 0 starts at t=0, absorbing pre-session recorder preamble)."""
    run = run.rstrip("/\\")
    blocks: list[dict] = []
    for f in _session_logs(run):
        blocks.extend(_file_blocks(f))
    camps, _orphans = segment_blocks(blocks)     # leading identity-less fragments (if any) are
                                                 # accounted as 'unattributed' by split_run's coverage
    idx = build_byte_time_index(run)
    # raw start-t of each campaign = when its first state byte was written by the recorder
    for i, c in enumerate(camps):
        b0 = c["blocks"][0]
        c["state_segments"] = [{"file": b["file"], "byte_lo": b["byte_lo"], "byte_hi": b["byte_hi"]}
                               for b in c["blocks"]]
        rt = _byte_to_t(idx, os.path.basename(b0["file"]), b0["byte_lo"])
        c["_raw_start"] = rt
        # campaign 0 is anchored at t=0 by construction; a later campaign whose first state byte
        # cannot be mapped to a recorder-t (no log_tail events) has no reliable timed-stream cut.
        c["t_resolved"] = (i == 0) or (rt is not None)
    # cut points: force monotonic; campaign 0 starts at 0.0 (absorbs preamble)
    starts = []
    prev = 0.0
    for i, c in enumerate(camps):
        if i == 0:
            starts.append(0.0)
            prev = 0.0
            continue
        rt = c["_raw_start"]
        rt = prev if rt is None else max(prev, rt)
        starts.append(rt)
        prev = rt
    for i, c in enumerate(camps):
        c["t_start"] = starts[i]
        c["t_end"] = starts[i + 1] if i + 1 < len(camps) else INF
        stamp = _logstamp(c["blocks"][0]["file"])
        c["start_stamp"] = stamp
        c["start_wall"] = _readable_start(stamp)
        c["index"] = i
        c["id_str"] = _campaign_id(i, c["faction"])
        c["label"] = "%s / %s @ %s (t%s..%s)" % (
            c["faction"], c["subculture"], c["start_wall"] or stamp or "?",
            c["min_turn"] or "?", c["max_turn"])
        c.pop("_raw_start", None)
    return camps


def _campaign_id(index: int, faction: str | None) -> str:
    """Filesystem-safe per-campaign id, e.g. '01_wh3_dlc27_sla_masque_of_slaanesh'."""
    safe = re.sub(r"[^a-z0-9_]+", "_", (faction or "unknown").lower())
    return "%02d_%s" % (index + 1, safe)


# --------------------------------------------------------------------------- #
# stream counting helpers (for reconciliation)                                #
# --------------------------------------------------------------------------- #
def _count_bytes_lines(path: str, lo: int = 0, hi: int | None = None) -> tuple[int, int]:
    """(#bytes, #lines) in [lo, hi) of a file, counted in chunks (memory-safe on multi-GB logs)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        sys.stderr.write("splitter: count %s skipped -> %s\n" % (os.path.basename(path), repr(e)[:80]))
        return 0, 0
    hi = size if hi is None else min(hi, size)
    if hi <= lo:
        return 0, 0
    nbytes = hi - lo
    nlines = 0
    try:
        with open(path, "rb") as f:
            f.seek(lo)
            remaining = nbytes
            while remaining > 0:
                chunk = f.read(min(1 << 22, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                nlines += chunk.count(b"\n")
    except OSError as e:
        sys.stderr.write("splitter: count %s skipped -> %s\n" % (os.path.basename(path), repr(e)[:80]))
    return nbytes, nlines


def _which_campaign(camps: list[dict], t: float | None) -> int | None:
    """Index of the campaign whose t-window contains t, or None if t is missing/out of range."""
    if t is None:
        return None
    for c in camps:
        if c["t_start"] <= t < c["t_end"]:
            return c["index"]
    return None


def _partition_jsonl_by_t(path: str, camps: list[dict]) -> dict:
    """Count lines of a t-keyed jsonl per campaign. Lines without a parseable `t` -> 'shared'."""
    per = {c["index"]: 0 for c in camps}
    shared = 0
    total = 0
    if not os.path.isfile(path):
        return {"total": 0, "per": per, "shared": 0}
    for ln in open(path, encoding="utf-8", errors="replace"):
        if not ln.strip():
            continue
        total += 1
        try:
            t = json.loads(ln).get("t")
        except Exception:
            t = None
        k = _which_campaign(camps, t)
        if k is None:
            shared += 1
        else:
            per[k] += 1
    return {"total": total, "per": per, "shared": shared}


def _partition_shots(run: str, camps: list[dict]) -> dict:
    """Count shot files per campaign via each shot event's `t`. Reconciled against files on disk."""
    per = {c["index"]: 0 for c in camps}
    shared = 0
    total = 0
    missing = 0
    p = os.path.join(run, "events.jsonl")
    shots_dir = os.path.join(run, "shots")
    if os.path.isfile(p):
        for ln in open(p, encoding="utf-8", errors="replace"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if o.get("kind") != "shot":
                continue
            total += 1
            base = os.path.basename(str(o.get("file", "")).replace("\\", "/"))
            if base and not os.path.exists(os.path.join(shots_dir, base)):
                missing += 1
            k = _which_campaign(camps, o.get("t"))
            if k is None:
                shared += 1
            else:
                per[k] += 1
    on_disk = len(os.listdir(shots_dir)) if os.path.isdir(shots_dir) else 0
    return {"total": total, "per": per, "shared": shared, "files_on_disk": on_disk,
            "events_without_file": missing}


# --------------------------------------------------------------------------- #
# top-level: full partition + reconciliation                                  #
# --------------------------------------------------------------------------- #
def split_run(run: str, count_state_lines: bool = True) -> dict:
    """Full per-campaign partition of a run + a reconciliation proving sum-of-parts == original
    for every stream. Reads only; writes nothing. `count_state_lines=False` skips newline counting
    on the (possibly multi-GB) state logs, reporting byte totals only."""
    run = run.rstrip("/\\")
    camps = detect_campaigns(run)

    # ---- state log: attribute exact byte ranges; reconcile per source file ----
    # Group segments by source file first, so each (possibly multi-GB) log is read at most once.
    by_file: dict[str, list[tuple]] = {}
    for c in camps:
        for seg in c["state_segments"]:
            by_file.setdefault(os.path.basename(seg["file"]),
                               []).append((c["index"], seg["file"], seg["byte_lo"], seg["byte_hi"]))
    state_files: dict[str, dict] = {}
    for f in _session_logs(run):
        base = os.path.basename(f)
        try:
            size = os.path.getsize(f)                       # ORIGINAL bytes: independent of segments
        except OSError as e:
            sys.stderr.write("splitter: stat %s skipped -> %s\n" % (base, repr(e)[:80]))
            continue
        segs = sorted(by_file.get(base, []), key=lambda s: s[2])
        rec = {"bytes": size, "lines": None, "parts": {},
               "unattributed_bytes": 0, "unattributed_lines": None}
        whole = len(segs) == 1 and segs[0][2] == 0 and segs[0][3] >= size
        if count_state_lines:
            rec["lines"] = _count_bytes_lines(f)[1]         # ORIGINAL lines: one whole-file read
        covered_b = 0
        covered_l = 0
        for cidx, path, lo, hi in segs:
            nb = min(hi, size) - lo                         # PARTS bytes: segment arithmetic
            if not count_state_lines:
                nl = None
            elif whole:
                nl = rec["lines"]                           # single full-file segment: no re-read
            else:
                nl = _count_bytes_lines(path, lo, hi)[1]    # real within-file split: count the range
            pr = rec["parts"].setdefault(cidx, {"bytes": 0, "lines": 0})
            pr["bytes"] += nb
            covered_b += nb
            if nl is not None:
                pr["lines"] += nl
                covered_l += nl
        # any bytes/lines no campaign claims (a run that never reaches a player identity) are
        # reported honestly as unattributed -- never silently dropped.
        rec["unattributed_bytes"] = size - covered_b
        if count_state_lines and rec["lines"] is not None:
            rec["unattributed_lines"] = rec["lines"] - covered_l
        state_files[base] = rec
    # aux .tail logs that carry no campaign identity -> run-level shared
    aux_tails = sorted(os.path.basename(x) for x in
                       glob.glob(os.path.join(run, "logs", "*.tail"))
                       if "script_log" not in os.path.basename(x))

    state_recon = _reconcile_state(state_files, camps, count_state_lines)
    events_recon = _partition_jsonl_by_t(os.path.join(run, "events.jsonl"), camps)
    ui_recon = _partition_jsonl_by_t(os.path.join(run, "ui_components.jsonl"), camps)
    shots_recon = _partition_shots(run, camps)

    return {
        "run": os.path.basename(run),
        "path": run,
        "n_campaigns": len(camps),
        "campaigns": [{
            "id": c["id_str"], "index": c["index"], "faction": c["faction"],
            "subculture": c["subculture"], "start_stamp": c["start_stamp"],
            "start_wall": c["start_wall"], "min_turn": c["min_turn"], "max_turn": c["max_turn"],
            "t_start": c["t_start"], "t_end": (None if c["t_end"] == INF else c["t_end"]),
            "state_segments": c["state_segments"], "label": c["label"],
        } for c in camps],
        "reconciliation": {
            "state_log": state_recon,
            "events": events_recon,
            "ui_components": ui_recon,
            "shots": shots_recon,
        },
        "shared": {"aux_tails": aux_tails, "meta": os.path.isfile(os.path.join(run, "meta.json"))},
        # False when a later campaign's start could not be mapped to recorder-t (no log_tail
        # events): state-log split stays exact, but timed streams collapse onto campaign 0.
        "timed_streams_resolved": all(c["t_resolved"] for c in camps),
    }


def _reconcile_state(state_files: dict, camps: list[dict], count_lines: bool) -> dict:
    """Per source script_log: original bytes/lines vs (summed parts + unattributed); flags mismatch.
    The invariant is part_bytes + unattributed_bytes == orig_bytes (likewise lines)."""
    by_index = {c["index"]: c["id_str"] for c in camps}
    files_out = {}
    ok = True
    tot_unattr_b = 0
    for base, rec in sorted(state_files.items()):
        part_bytes = sum(p["bytes"] for p in rec["parts"].values())
        part_lines = sum(p["lines"] for p in rec["parts"].values()) if count_lines else None
        ub = rec.get("unattributed_bytes", 0)
        ul = rec.get("unattributed_lines")
        tot_unattr_b += ub
        b_ok = part_bytes + ub == rec["bytes"]
        l_ok = True
        if count_lines and rec["lines"] is not None:
            l_ok = part_lines + (ul or 0) == rec["lines"]
        ok = ok and b_ok and l_ok
        files_out[base] = {
            "orig_bytes": rec["bytes"], "part_bytes": part_bytes,
            "unattributed_bytes": ub, "bytes_ok": b_ok,
            "orig_lines": rec["lines"], "part_lines": part_lines,
            "unattributed_lines": ul, "lines_ok": l_ok,
            "parts": {by_index[k]: v for k, v in sorted(rec["parts"].items()) if k in by_index},
        }
    return {"files": files_out, "all_ok": ok, "unattributed_bytes_total": tot_unattr_b}


# --------------------------------------------------------------------------- #
# cli                                                                         #
# --------------------------------------------------------------------------- #
def _fmt_t(t) -> str:
    return "inf" if t is None or t == INF else "%.1f" % t


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python splitter.py <run-dir> [--no-state-lines]")
        return
    run = sys.argv[1]
    r = split_run(run, count_state_lines="--no-state-lines" not in sys.argv)
    print("run=%s  campaigns=%d" % (r["run"], r["n_campaigns"]))
    for c in r["campaigns"]:
        print("  [%s] %s / %s  start=%s  turns %s..%s  t=[%s,%s)"
              % (c["id"], c["faction"], c["subculture"], c["start_wall"] or c["start_stamp"],
                 c["min_turn"] or "?", c["max_turn"], _fmt_t(c["t_start"]), _fmt_t(c["t_end"])))
    rec = r["reconciliation"]
    print("  state_log all_ok=%s  unattributed_bytes=%d"
          % (rec["state_log"]["all_ok"], rec["state_log"]["unattributed_bytes_total"]))
    for base, f in rec["state_log"]["files"].items():
        ux = "" if not f["unattributed_bytes"] else " +%d unattr" % f["unattributed_bytes"]
        print("    %-40s bytes %d%s==%d %s  lines %s==%s %s"
              % (base, f["part_bytes"], ux, f["orig_bytes"], "OK" if f["bytes_ok"] else "MISMATCH",
                 f["part_lines"], f["orig_lines"], "OK" if f["lines_ok"] else "MISMATCH"))
    for name in ("events", "ui_components", "shots"):
        s = rec[name]
        got = sum(s["per"].values()) + s["shared"]
        extra = ""
        if name == "shots":
            extra = " files_on_disk=%d events_without_file=%d" % (s["files_on_disk"], s["events_without_file"])
        print("    %-14s total=%d  parts=%s shared=%d  sum_ok=%s%s"
              % (name, s["total"], {camp["id"]: s["per"][camp["index"]] for camp in r["campaigns"]},
                 s["shared"], got == s["total"], extra))
    if r["shared"]["aux_tails"]:
        print("  shared aux logs (not campaign-split): %s" % ", ".join(r["shared"]["aux_tails"]))


if __name__ == "__main__":
    main()
