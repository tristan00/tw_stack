r"""recruit_pool.py -- recover the POOL (local vs global) a chosen recruit came from. 100% OFFLINE.

GAP: the SAME unit is recruitable from BOTH pools; the panel shows global on the UPPER card row and
local on the LOWER row (same x, different y). The `<unit>_recruitable` click event and
RecruitmentItemIssuedByPlayer carry the unit but not the pool. We recover the pool as WHICH ROW the
recruit click landed on -- recruit clicks are bimodal in screen-Y; the menu_open rects (which carry
`source`) label the two bands (smaller UI-y = global = upper). No fragile UI->screen affine is needed
for the decision; one is fit afterward purely to corroborate with exact card-rect containment.

Verified v6 20260727_144203: 35/35 chosen recruits resolved to a pool = 100% (HEF nagarythe 22/22
across its two sessions, WEF wood_elves 13/13). Pool split local=29 / global=6; the two bands are
~244px apart on a 1440px screen. Correctly separates the same unit recruited from BOTH pools in one
session (HEF turn1 shadow_warriors: global click y=504 AND local click y=887).

Public: resolve_recruit_pools(run_dir) -> [{campaign,turn,unit,source,band,click,click_t,open_t,
                                            strict,reason}]  (source=None only when no click is found)
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("structurer", "correlation"):                       # reuse the sibling repos
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _p)))
    sys.path.insert(0, os.path.abspath(os.path.join(r"D:/tw_stack", _p)))
import structurer          # noqa: E402
import correlation         # noqa: E402

_norm = lambda k: re.sub(r"_\d+$", "", k or "")
_CARD_YMIN, _CARD_YMAX = 300, 1050     # click-y that can be a recruit card (excludes confirm bar ~1390)


def _load_mouse_downs(run_dir):
    """Physical mouse-press screen coords, recorder clock: [(t,x,y)] (duplicate samples de-bounced)."""
    raw = []
    _bad = 0
    with open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"mouse_down"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                _bad += 1           # hot per-line parse: count, don't log each
                continue
            if r.get("kind") != "mouse_down":
                continue
            sc = r.get("screen") or [r.get("x"), r.get("y")]
            if sc and sc[0] is not None and sc[1] is not None:
                raw.append((r.get("t"), sc[0], sc[1]))
    if _bad:
        sys.stderr.write("recruit_pool._load_mouse_downs: skipped %d malformed lines\n" % _bad)
    raw.sort()
    phys = []
    for (t, x, y) in raw:
        if phys and t - phys[-1][0] < 0.6 and abs(x - phys[-1][1]) < 15 and abs(y - phys[-1][2]) < 15:
            continue
        phys.append((t, x, y))
    return phys


def _load_recruit_opens(run_dir):
    """Every recruitment menu_open (recorder clock in `t`) with its per-source option rects."""
    out = []
    _bad = 0
    with open(os.path.join(run_dir, "ui_components.jsonl"), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"menu_open"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                _bad += 1           # hot per-line parse: count, don't log each
                continue
            if r.get("kind") == "menu_open" and r.get("panel") == "recruitment":
                out.append(r)
    if _bad:
        sys.stderr.write("recruit_pool._load_recruit_opens: skipped %d malformed lines\n" % _bad)
    out.sort(key=lambda r: r.get("t") or 0.0)
    return out


def _chosen_recruits(log, off):
    """Distinct chosen recruits per campaign: [(rt, norm_unit, turn)] on the recorder clock. Truth =
    `<unit>_recruitable` ComponentLClickUp; duplicate log lines for one click (same unit <1.2s) merged."""
    ev = structurer.parse_events(log, turn=None, player_only=True)
    rcl = sorted((e["t"] - off, _norm(structurer._strip_unit(e["component"])), e.get("turn"))
                 for e in ev
                 if e.get("event") == "ComponentLClickUp" and (e.get("component") or "").endswith("_recruitable"))
    phys = []
    for rt, key, turn in rcl:
        if phys and key == phys[-1][1] and rt - phys[-1][0] < 1.2:
            continue
        phys.append((rt, key, turn))
    return phys


def _source_row_order(recs):
    """Median UI-y-centre per source across all opens -> {source: uy}. Global row is above local."""
    rc = defaultdict(list)
    for o in recs:
        for c in (o.get("options") or []):
            if c.get("x") is not None:
                rc[c["source"]].append(c["y"] + c["h"] / 2.0)
    out = {}
    for s, v in rc.items():
        v = sorted(v)
        out[s] = v[len(v) // 2]
    return out


def _nearest_card_click(rt, downs, dt=8.0):
    for (t, x, y) in sorted((d for d in downs if abs(d[0] - rt) < dt), key=lambda d: abs(d[0] - rt)):
        if _CARD_YMIN < y < _CARD_YMAX:
            return (t, x, y)
    return None


def _lsq(u, s):
    n = len(u)
    mu = sum(u) / n
    ms = sum(s) / n
    den = sum((u[i] - mu) ** 2 for i in range(n)) or 1.0
    a = sum((u[i] - mu) * (s[i] - ms) for i in range(n)) / den
    return a, ms - a * mu


def resolve_recruit_pools(run_dir, return_meta=False):
    downs = _load_mouse_downs(run_dir)
    recs = _load_recruit_opens(run_dir)
    rows = _source_row_order(recs)                       # e.g. {'global':417,'local':698}
    sources_by_uy = sorted(rows, key=lambda s: rows[s])  # [0]=upper row, [-1]=lower row

    # PASS 1 -- each chosen recruit's nearest card click. NB: the two-band Y threshold below is computed
    # over ALL of the run's recruits (run-GLOBAL) -- do NOT scope this to a campaign's logs: doing so
    # shifts the threshold and flips real source labels (measured: 12/35 flip in 20260727_144203). This
    # is why gather() keeps the RUN as its parallel unit (campaign-split is not data-identical here).
    recruits = []
    for log in glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail")):
        player = structurer.player_faction(log)
        if not player:
            continue
        off = correlation.correlate(run_dir, only_files=[log]).get("offset")
        if off is None:
            continue
        for rt, key, turn in _chosen_recruits(log, off):
            recruits.append({"campaign": player, "turn": turn, "unit": key, "rt": rt,
                             "click": _nearest_card_click(rt, downs)})

    # threshold between the two Y-bands = the widest gap in the recruit-click Y distribution
    ys = sorted(r["click"][2] for r in recruits if r["click"])
    thr = None
    if len(ys) >= 2:
        gi = max(range(len(ys) - 1), key=lambda i: ys[i + 1] - ys[i])
        thr = (ys[gi] + ys[gi + 1]) / 2.0

    # PASS 2 -- classify by band; label band via the rects' row order
    def nearest_open(t):
        return min(recs, key=lambda o: abs((o.get("t") or 0) - t)) if recs else None
    for r in recruits:
        c = r["click"]
        if not c or thr is None or len(sources_by_uy) < 2:
            r.update(source=None, band=None, click=None,
                     reason="no_recruit_card_click" if not c else "insufficient_pool_geometry")
            continue
        band = "upper" if c[2] < thr else "lower"
        o = nearest_open(c[0])
        r.update(source=sources_by_uy[0] if band == "upper" else sources_by_uy[-1], band=band,
                 click=(c[1], c[2]), click_t=round(c[0], 2),
                 open_t=round(o.get("t"), 2) if o else None, reason=None)

    # PASS 3 -- corroboration: stable UI->screen affine from clean (chosen card -> click) pairs
    def card_of(unit, source, open_t):
        o = min((oo for oo in recs), key=lambda oo: abs((oo.get("t") or 0) - open_t), default=None)
        if not o:
            return None
        for c in (o.get("options") or []):
            if c.get("x") is not None and _norm(c["key"]) == unit and c["source"] == source:
                return c
        return None
    UX = []
    SX = []
    pairs = []
    for r in recruits:
        if not r.get("source"):
            continue
        c = card_of(r["unit"], r["source"], r["open_t"])
        if c:
            UX.append(c["x"] + c["w"] / 2.0)
            SX.append(r["click"][0])
            pairs.append((r, c))
    band_mean = {}
    for r in recruits:
        if r.get("band"):
            band_mean.setdefault(r["band"], []).append(r["click"][1])
    affine = None
    if len(UX) >= 4 and "upper" in band_mean and "lower" in band_mean:
        ax, bx = _lsq(UX, SX)
        ay, by = _lsq([rows[sources_by_uy[0]], rows[sources_by_uy[-1]]],
                      [sum(band_mean["upper"]) / len(band_mean["upper"]),
                       sum(band_mean["lower"]) / len(band_mean["lower"])])
        affine = (ax, bx, ay, by)
        for r in recruits:
            r["strict"] = False
        for (r, c) in pairs:
            x, y = r["click"]
            if (ax * c["x"] + bx <= x <= ax * (c["x"] + c["w"]) + bx and
                    ay * c["y"] + by <= y <= ay * (c["y"] + c["h"]) + by):
                r["strict"] = True

    for r in recruits:
        r.pop("rt", None)
    if return_meta:
        return recruits, {"threshold_y": thr, "sources_by_uy": sources_by_uy,
                          "row_uy": rows, "affine": affine, "n_affine_pairs": len(UX)}
    return recruits


if __name__ == "__main__":
    run = sys.argv[1] if len(sys.argv) > 1 else r"D:/twdata/runs/human/20260727_144203"
    res, meta = resolve_recruit_pools(run, return_meta=True)
    got = sum(1 for r in res if r.get("source"))
    print("recruits=%d resolved=%d  meta=%s" % (len(res), got, meta))
    for r in res:
        print("  %s t%s %-34s -> %-6s band=%s click=%s strict=%s" %
              (r["campaign"], r["turn"], r["unit"], r.get("source"), r.get("band"),
               r.get("click"), r.get("strict")))
