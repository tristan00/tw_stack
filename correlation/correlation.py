r"""correlation -- align a run's input stream to its game-log stream on one timeline.

events.jsonl timestamps are recorder-seconds; the game's script_log timestamps are game-seconds
(the game clock restarts each launch). This finds the single offset `recorder_t + offset = game_t`
that lines the most clicks up with a logged UI component, then matches each click to the specific
component under it (time window AND inside the component's on-screen rect). That match is what lets
a click be attributed to what it acted on.

Ported verbatim from the monolith's tools/extract.py (solve_offset + match_click + the [ui] parser);
standalone (stdlib only), no monolith imports. Pure alignment only -- no lens/verb classification.

    python correlation.py <run-dir> [logfile ...]      # print offset, match rate, sample matches

API: correlate(run, only_files=None) -> {run, clicks, ui_events, offset, matched, match_rate, samples}.
"""
from __future__ import annotations

import bisect
import glob
import json
import os
import re
import sys

UI_VIRTUAL_W = 1984.0   # the log's "position on screen" coords are UI-virtual (1984 wide)

RE_UI_PATH = re.compile(r"path from root:\s*(.+?)\s*$")
RE_UI_TIME = re.compile(r"^\s*\[ui\]\s*<([\d.]+)s>")


def load_events(run: str) -> list[dict]:
    """Load <run>/events.jsonl, skipping unparseable lines."""
    rows = []
    _bad = 0
    p = os.path.join(run, "events.jsonl")
    for l in open(p, encoding="utf-8", errors="replace"):
        try:
            rows.append(json.loads(l))
        except Exception:
            _bad += 1           # hot per-line parse: count, don't log each
    if _bad:
        sys.stderr.write("correlation.load_events: skipped %d malformed lines\n" % _bad)
    return rows


def parse_ui_events(run: str, only_files: list[str] | None = None) -> list[dict]:
    """Every [ui] component event (path + gt + on-screen rect + state) from the run's script_logs.
    Lifecycle-only [ui] lines are skipped here (alignment needs the gt + rect events)."""
    out = []
    files = only_files or sorted(glob.glob(os.path.join(run, "logs", "script_log*.tail")))
    for f in files:
        gt, path = None, None
        for line in open(f, encoding="utf-8", errors="replace"):
            if "[ui]" not in line and "path from root" not in line and "position on screen" not in line:
                continue                                    # prefilter: ~0.3% of log lines are [ui]
            t = RE_UI_TIME.match(line)
            if t:
                gt = float(t.group(1))
            m = RE_UI_PATH.search(line)
            if m:
                path = m.group(1).strip()
                continue
            if path is not None and "position on screen" in line:
                st = re.search(r"state:\s*\[([^\]]*)\]", line)
                pos = re.search(r"position on screen:\s*\[\s*(-?\d+),\s*(-?\d+)\]", line)
                siz = re.search(r"size:\s*\[\s*(\d+),\s*(\d+)\]", line)
                out.append({"gt": gt, "path": path,
                            "state": st.group(1) if st else None,
                            "x": int(pos.group(1)) if pos else None,
                            "y": int(pos.group(2)) if pos else None,
                            "w": int(siz.group(1)) if siz else None,
                            "h": int(siz.group(2)) if siz else None})
                path = None
    return out


def solve_offset(clicks: list[dict], ui: list[dict]) -> tuple[float | None, int]:
    """Brute-force the recorder->game offset that lines the most clicks up with a UI event (coarse
    1s scan, then refined to 0.1s). Returns (offset, matched) or (None, 0) on empty input."""
    if not clicks or not ui:
        return None, 0
    uts = sorted(e["gt"] for e in ui if e["gt"] is not None)
    if not uts:
        return None, 0
    cts = [c["t"] for c in clicks]
    lo = int(uts[0] - cts[-1]) - 5
    hi = int(uts[-1] - cts[0]) + 5
    best, best_n = None, -1
    for off in range(lo, hi + 1):
        n = 0
        for ct in cts:
            g = ct + off
            i = bisect.bisect_left(uts, g)
            for j in (i - 1, i):
                if 0 <= j < len(uts) and abs(uts[j] - g) <= 0.5:
                    n += 1
                    break
        if n > best_n:
            best_n, best = n, off
    fine, fine_n = best, best_n
    for k in range(-10, 11):
        off = best + k / 10.0
        n = 0
        for ct in cts:
            g = ct + off
            i = bisect.bisect_left(uts, g)
            for j in (i - 1, i):
                if 0 <= j < len(uts) and abs(uts[j] - g) <= 0.4:
                    n += 1
                    break
        if n > fine_n:
            fine_n, fine = n, off
    return fine, fine_n


def match_click(c: dict, ui_sorted: list[dict], uts: list[float], off: float | None,
                scale: float, win: float = 0.8) -> dict | None:
    """The component a click hit: within `win` seconds of a UI event AND inside its rect (scaled by
    screen_w/1984). Returns the smallest containing rect (most specific), or None."""
    if off is None:
        return None
    g = c["t"] + off
    i = bisect.bisect_left(uts, g)
    best = None
    for j in range(max(0, i - 3), min(len(ui_sorted), i + 4)):
        e = ui_sorted[j]
        if abs(uts[j] - g) > win or e.get("x") is None:
            continue
        x, y = e["x"] * scale, e["y"] * scale
        w, h = e["w"] * scale, e["h"] * scale
        cx, cy = c["screen"]
        if x <= cx <= x + w and y <= cy <= y + h:
            a = w * h
            if best is None or a < best[0]:
                best = (a, e)
    return best[1] if best else None


def _screen_w(run: str) -> int:
    try:
        return json.load(open(os.path.join(run, "meta.json"), encoding="utf-8")).get("screen", [2560])[0]
    except Exception as e:
        sys.stderr.write("correlation._screen_w: meta.json unreadable, default 2560 -> %s\n" % repr(e)[:60])
        return 2560


_CORR_CACHE = {}           # (run, only_files, mtimes) -> result. PER-PROCESS memo: build_decisions and
_CORR_CACHE_MAX = 96       # recruit_pool call correlate(run, only_files=[log]) with identical args ->
                           # parse the UI logs ONCE, not 2x. mtime-keyed (events.jsonl + the scoped
                           # logs) so a live-growing run invalidates -> live-safe; bounded FIFO.


def _corr_key(run, only_files):
    if os.environ.get("ADVISOR_NO_MEMO"):                  # baseline-measurement / identity-proof switch
        return None
    try:
        parts = [os.path.abspath(run), os.path.getmtime(os.path.join(run, "events.jsonl"))]
    except OSError:
        return None
    if only_files is None:
        return None                                            # whole-run scan (rare) -> don't cache
    for f in only_files:
        try:
            parts.append((os.path.abspath(f), os.path.getmtime(f)))
        except OSError:
            return None
    return tuple(parts)


def correlate(run: str, only_files: list[str] | None = None) -> dict:
    """Align one run: solve the offset, match every click to its component, report the match rate.

    Memoized per (run, only_files, mtimes): a pure function of the run's events.jsonl + the scoped log
    bytes, so the cached dict is identical to recomputing; avoids re-parsing the UI logs per caller."""
    run = run.rstrip("/\\")
    ckey = _corr_key(run, only_files)
    if ckey is not None and ckey in _CORR_CACHE:
        return _CORR_CACHE[ckey]
    ev = load_events(run)
    clicks = [e for e in ev if e.get("kind") == "mouse_down"]
    ui = parse_ui_events(run, only_files)
    off, matched = solve_offset(clicks, ui)
    # A recording can hold several campaigns; the game clock restarts each launch. When scoped to a
    # target session's log(s), clip clicks to that session's game-time span (mapped back via off) and
    # re-solve, so clicks from other campaigns don't drag the rate down.
    ui_gts = [e["gt"] for e in ui if e["gt"] is not None]
    if only_files and off is not None and ui_gts:
        lo, hi = min(ui_gts) - off - 60.0, max(ui_gts) - off + 60.0
        clicks = [c for c in clicks if lo <= c["t"] <= hi]
        off, matched = solve_offset(clicks, ui)
    scale = _screen_w(run) / UI_VIRTUAL_W
    ui_sorted = sorted([e for e in ui if e["gt"] is not None], key=lambda x: x["gt"])
    uts = [e["gt"] for e in ui_sorted]
    samples = []
    for c in clicks:
        comp = match_click(c, ui_sorted, uts, off, scale)
        if comp is not None and len(samples) < 8:
            leaf = comp["path"].split(" > ")[-1]
            samples.append({"t": c["t"], "gt": round(c["t"] + off, 2), "hit": leaf})
    result = {"run": os.path.basename(run), "clicks": len(clicks), "ui_events": len(ui),
              "offset": off, "matched": matched,
              "match_rate": round(100.0 * matched / max(1, len(clicks)), 1), "samples": samples}
    if ckey is not None:
        if len(_CORR_CACHE) >= _CORR_CACHE_MAX:
            _CORR_CACHE.pop(next(iter(_CORR_CACHE)))            # bounded FIFO eviction
        _CORR_CACHE[ckey] = result
    return result


def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("usage: python correlation.py <run-dir> [logfile ...]")
        return
    run = sys.argv[1]
    only = sys.argv[2:] or None
    r = correlate(run, only)
    print("run=%s clicks=%d ui_events=%d offset=%s matched=%d match_rate=%.1f%%"
          % (r["run"], r["clicks"], r["ui_events"], r["offset"], r["matched"], r["match_rate"]))
    for s in r["samples"]:
        print("  t=%.2f gt=%.2f -> %s" % (s["t"], s["gt"], s["hit"]))


if __name__ == "__main__":
    main()
