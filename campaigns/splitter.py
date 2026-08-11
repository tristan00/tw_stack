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

_HUMAN_MARK = b'"is_human":true'
_FACTION_MARK = b'"kind":"faction"'
_RE_FACTION = re.compile(rb'"faction":"([a-z0-9_]+)"')
_RE_SUBCULTURE = re.compile(rb'"subculture":"([a-z0-9_]+)"')
_RE_TURN = re.compile(rb'"turn":(\d+)')
_RE_LOGSTAMP = re.compile(r"script_log_(\d{6})_(\d{4})")


def _is_new_campaign(cur: dict | None, ident: tuple, min_turn: int, restart_turn: int) -> bool:
    if cur is None:
        return True
    if ident != cur["id"]:
        return True
    if min_turn and min_turn <= restart_turn and cur["max_turn"] > restart_turn:
        return True
    return False


def segment_blocks(blocks: list[dict], restart_turn: int = 1) -> tuple[list[dict], list[dict]]:
    camps: list[dict] = []
    pending: list[dict] = []
    for b in blocks:
        cur = camps[-1] if camps else None
        if b["id"] is None:
            if cur is not None:
                cur["blocks"].append(b)
                cur["max_turn"] = max(cur["max_turn"], b.get("max_turn", 0))
            else:
                pending.append(b)
            continue
        if _is_new_campaign(cur, b["id"], b.get("min_turn", 0), restart_turn):
            nc = _new_camp(b)
            if not camps and pending:
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

    def __init__(self, restart_turn: int = 1):
        self.restart_turn = restart_turn
        self.cur: dict | None = None
        self.index = -1

    def observe(self, faction: str | None, subculture: str | None, turn: int | None) -> bool:
        if faction is None:
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
    off = 0
    for raw in chunk.splitlines(keepends=True):
        p = _parse_human_line(raw)
        if p is not None:
            yield p["faction"], p["subculture"], p["turn"], off
        off += len(raw)


def _parse_human_line(raw: bytes) -> dict | None:
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
    for chunk in (head, tail):
        turns += [int(t) for t in _RE_TURN.findall(chunk)]
    return {"head_id": head_id, "tail_id": tail_id,
            "min_turn": min(hmin) if hmin else (min(turns) if turns else 0),
            "max_turn": max(turns) if turns else 0,
            "split": head_id is not None and tail_id is not None and head_id != tail_id}


def _full_scan_blocks(path: str) -> list[dict]:
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
    for i, b in enumerate(blocks):
        b["byte_lo"] = 0 if i == 0 else blocks[i]["byte_lo"]
        b["byte_hi"] = blocks[i + 1]["byte_lo"] if i + 1 < len(blocks) else off
    if blocks:
        blocks[0]["byte_lo"] = 0
    return blocks


def _file_blocks(path: str) -> list[dict]:
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
    ident = ht["head_id"] or ht["tail_id"]
    return [{"file": path, "byte_lo": 0, "byte_hi": size, "id": ident,
             "min_turn": ht["min_turn"], "max_turn": ht["max_turn"]}]


def _session_logs(run: str) -> list[str]:
    files = glob.glob(os.path.join(run, "logs", "script_log_*.tail"))
    files += glob.glob(os.path.join(run, "logs", "script_log_*.txt"))
    files += glob.glob(os.path.join(run, "script_log_*.txt"))
    return sorted(set(files))


def _logstamp(path: str) -> str | None:
    m = _RE_LOGSTAMP.search(os.path.basename(path))
    return "%s_%s" % (m.group(1), m.group(2)) if m else None


def _readable_start(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        d, hm = stamp.split("_")
        return "20%s-%s-%s %s:%s" % (d[4:6], d[2:4], d[0:2], hm[0:2], hm[2:4])
    except Exception as e:
        sys.stderr.write("splitter: start-label skipped -> %s\n" % repr(e)[:80])
        return None


def build_byte_time_index(run: str) -> dict:
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
    e = idx.get(dst_basename)
    if not e or not e["cum"]:
        return None
    i = bisect.bisect_left(e["cum"], off)
    if i >= len(e["t"]):
        i = len(e["t"]) - 1
    return e["t"][i]


def detect_campaigns(run: str) -> list[dict]:
    run = run.rstrip("/\\")
    blocks: list[dict] = []
    for f in _session_logs(run):
        blocks.extend(_file_blocks(f))
    camps, _orphans = segment_blocks(blocks)
    idx = build_byte_time_index(run)
    for i, c in enumerate(camps):
        b0 = c["blocks"][0]
        c["state_segments"] = [{"file": b["file"], "byte_lo": b["byte_lo"], "byte_hi": b["byte_hi"]}
                               for b in c["blocks"]]
        rt = _byte_to_t(idx, os.path.basename(b0["file"]), b0["byte_lo"])
        c["_raw_start"] = rt
        c["t_resolved"] = (i == 0) or (rt is not None)
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
    safe = re.sub(r"[^a-z0-9_]+", "_", (faction or "unknown").lower())
    return "%02d_%s" % (index + 1, safe)


def _count_bytes_lines(path: str, lo: int = 0, hi: int | None = None) -> tuple[int, int]:
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
    if t is None:
        return None
    for c in camps:
        if c["t_start"] <= t < c["t_end"]:
            return c["index"]
    return None


def _partition_jsonl_by_t(path: str, camps: list[dict]) -> dict:
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


def split_run(run: str, count_state_lines: bool = True) -> dict:
    run = run.rstrip("/\\")
    camps = detect_campaigns(run)

    by_file: dict[str, list[tuple]] = {}
    for c in camps:
        for seg in c["state_segments"]:
            by_file.setdefault(os.path.basename(seg["file"]),
                               []).append((c["index"], seg["file"], seg["byte_lo"], seg["byte_hi"]))
    state_files: dict[str, dict] = {}
    for f in _session_logs(run):
        base = os.path.basename(f)
        try:
            size = os.path.getsize(f)
        except OSError as e:
            sys.stderr.write("splitter: stat %s skipped -> %s\n" % (base, repr(e)[:80]))
            continue
        segs = sorted(by_file.get(base, []), key=lambda s: s[2])
        rec = {"bytes": size, "lines": None, "parts": {},
               "unattributed_bytes": 0, "unattributed_lines": None}
        whole = len(segs) == 1 and segs[0][2] == 0 and segs[0][3] >= size
        if count_state_lines:
            rec["lines"] = _count_bytes_lines(f)[1]
        covered_b = 0
        covered_l = 0
        for cidx, path, lo, hi in segs:
            nb = min(hi, size) - lo
            if not count_state_lines:
                nl = None
            elif whole:
                nl = rec["lines"]
            else:
                nl = _count_bytes_lines(path, lo, hi)[1]
            pr = rec["parts"].setdefault(cidx, {"bytes": 0, "lines": 0})
            pr["bytes"] += nb
            covered_b += nb
            if nl is not None:
                pr["lines"] += nl
                covered_l += nl
        rec["unattributed_bytes"] = size - covered_b
        if count_state_lines and rec["lines"] is not None:
            rec["unattributed_lines"] = rec["lines"] - covered_l
        state_files[base] = rec
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
        "timed_streams_resolved": all(c["t_resolved"] for c in camps),
    }


def _reconcile_state(state_files: dict, camps: list[dict], count_lines: bool) -> dict:
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
