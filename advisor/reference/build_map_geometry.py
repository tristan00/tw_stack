from __future__ import annotations

import json
import os
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from advisor.reference import packs
from decisions import pg

CAMPAIGNS = ("wh3_main_combi", "wh3_main_chaos")
MAX_POINTS = 120
MIN_PART = 0.05
TOLERANCES = (1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0, 32.0)
SPOT = ("hag_graef", "altdorf", "naggarond", "lothern", "karaz_a_karak", "couronne")

DDL = ("CREATE TABLE IF NOT EXISTS ref.region_geometry ("
       " region_id INTEGER PRIMARY KEY, cx DOUBLE PRECISION NOT NULL,"
       " cy DOUBLE PRECISION NOT NULL, area_px INTEGER NOT NULL,"
       " map_w INTEGER NOT NULL, map_h INTEGER NOT NULL, outline TEXT NOT NULL)")


def _decode_tga(b):
    idlen, cmaptype, imgtype = b[0], b[1], b[2]
    cmfirst, cmlen, cmdepth = struct.unpack_from("<HHB", b, 3)
    w, h, pixdepth, desc = struct.unpack_from("<HHBB", b, 12)
    if cmaptype != 1 or imgtype not in (1, 9):
        raise RuntimeError("lookup tga is not colour-mapped: cmaptype=%d imgtype=%d"
                           % (cmaptype, imgtype))
    if cmdepth not in (24, 32) or pixdepth not in (8, 16):
        raise RuntimeError("unsupported tga depths: cmdepth=%d pixdepth=%d" % (cmdepth, pixdepth))
    p = 18 + idlen
    ent = cmdepth // 8
    cm = np.frombuffer(b, np.uint8, cmlen * ent, p).reshape(cmlen, ent)
    pal = np.ascontiguousarray(cm[:, [2, 1, 0]])
    p += cmlen * ent
    step = pixdepth // 8
    dt = np.dtype(np.uint8) if step == 1 else np.dtype("<u2")
    if imgtype == 1:
        idx = np.frombuffer(b, dt, w * h, p).reshape(h, w).astype(np.int32)
        p += w * h * step
    else:
        flat = np.empty(w * h, np.int32)
        o = 0
        while o < w * h:
            head = b[p]; p += 1
            n = (head & 0x7F) + 1
            if head & 0x80:
                flat[o:o + n] = int(np.frombuffer(b, dt, 1, p)[0]); p += step
            else:
                flat[o:o + n] = np.frombuffer(b, dt, n, p); p += n * step
            o += n
        idx = flat.reshape(h, w)
    if p > len(b):
        raise RuntimeError("tga pixel data overruns file (%d > %d)" % (p, len(b)))
    if cmfirst and int(idx.max()) >= cmlen:
        idx = idx - cmfirst
    if int(idx.min()) < 0 or int(idx.max()) >= cmlen:
        raise RuntimeError("tga index out of palette range: %d..%d for %d entries"
                           % (idx.min(), idx.max(), cmlen))
    return idx, pal, w, h, desc


def _components(mask):
    h, w = mask.shape
    pad = np.zeros((h, w + 2), np.int8)
    pad[:, 1:-1] = mask
    dif = np.diff(pad, axis=1)
    sr, sc = np.nonzero(dif == 1)
    _, ec = np.nonzero(dif == -1)
    sr = sr.tolist(); sc = sc.tolist(); ec = ec.tolist()
    parent = list(range(len(sr)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    rows = {}
    for i, r in enumerate(sr):
        rows.setdefault(r, []).append(i)
    for r, cur in rows.items():
        nxt = rows.get(r + 1)
        if not nxt:
            continue
        i = j = 0
        while i < len(cur) and j < len(nxt):
            a, b = cur[i], nxt[j]
            if sc[a] <= ec[b] and sc[b] <= ec[a]:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
            if ec[a] < ec[b]:
                i += 1
            else:
                j += 1
    groups = {}
    for i in range(len(sr)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        area = sum(ec[i] - sc[i] for i in g)
        out.append((area, [(sr[i], sc[i], ec[i]) for i in g]))
    out.sort(key=lambda t: -t[0])
    return out


def _rings(mask):
    pad = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), bool)
    pad[1:-1, 1:-1] = mask
    sides = ((pad & ~np.roll(pad, 1, axis=0), 0), (pad & ~np.roll(pad, -1, axis=1), 1),
             (pad & ~np.roll(pad, -1, axis=0), 2), (pad & ~np.roll(pad, 1, axis=1), 3))
    edges = {}
    for arr, kind in sides:
        rr, cc = np.nonzero(arr)
        for r, c in zip(rr.tolist(), cc.tolist()):
            if kind == 0:
                a, z = (c, r), (c + 1, r)
            elif kind == 1:
                a, z = (c + 1, r), (c + 1, r + 1)
            elif kind == 2:
                a, z = (c + 1, r + 1), (c, r + 1)
            else:
                a, z = (c, r + 1), (c, r)
            edges.setdefault(a, []).append(z)
    out = []
    while edges:
        start = next(iter(edges))
        cur, prev, ring = start, None, [start]
        while True:
            cand = edges.get(cur)
            if not cand:
                break
            if prev is None or len(cand) == 1:
                nxt = cand.pop(0)
            else:
                pick, best = 0, None
                for i, q in enumerate(cand):
                    k = prev[0] * (q[1] - cur[1]) - prev[1] * (q[0] - cur[0])
                    if best is None or k < best:
                        pick, best = i, k
                nxt = cand.pop(pick)
            if not cand:
                del edges[cur]
            prev = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                break
            ring.append(cur)
        if len(ring) >= 4:
            out.append(ring)
    return out


def _shoelace(ring):
    a = np.asarray(ring, np.float64)
    x, y = a[:, 0], a[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _peucker(pts, tol):
    n = len(pts)
    if n < 3:
        return list(range(n))
    a = np.asarray(pts, np.float64)
    keep = np.zeros(n, bool)
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        p, seg = a[i], a[j] - a[i]
        sub = a[i + 1:j]
        length = float(np.hypot(seg[0], seg[1]))
        if length == 0.0:
            d = np.hypot(sub[:, 0] - p[0], sub[:, 1] - p[1])
        else:
            d = np.abs(seg[0] * (p[1] - sub[:, 1]) - seg[1] * (p[0] - sub[:, 0])) / length
        k = int(np.argmax(d))
        if float(d[k]) > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return np.nonzero(keep)[0].tolist()


def _simplify(ring, tol):
    n = len(ring)
    if n < 4:
        return list(ring)
    a = np.asarray(ring, np.float64)
    far = int(np.argmax(np.hypot(a[:, 0] - a[0, 0], a[:, 1] - a[0, 1]))) or n // 2
    head = ring[:far + 1]
    tail = ring[far:] + [ring[0]]
    out = [head[i] for i in _peucker(head, tol)]
    out += [tail[i] for i in _peucker(tail, tol)[1:-1]]
    return out


def _centroid(rings):
    ax = ay = at = 0.0
    for ring in rings:
        a = np.asarray(ring, np.float64)
        x, y = a[:, 0], a[:, 1]
        xn, yn = np.roll(x, -1), np.roll(y, -1)
        cr = x * yn - xn * y
        area = 0.5 * float(cr.sum())
        if area == 0.0:
            continue
        ax += float(np.dot(x + xn, cr)) / 6.0
        ay += float(np.dot(y + yn, cr)) / 6.0
        at += area
    if at == 0.0:
        pts = np.asarray([p for ring in rings for p in ring], np.float64)
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())
    return ax / at, ay / at


def extract(con, campaign):
    hit = con.execute(
        "SELECT mapname, overlay_file FROM ref.campaign_map_playable_areas"
        " WHERE campaign_key = %s", (campaign,)).fetchall()
    if len(hit) != 1:
        raise RuntimeError("expected one playable area for %s, got %d" % (campaign, len(hit)))
    mapname, overlay = hit[0]

    colour = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT key, r, g, b FROM ref.regions")}
    on_map = [r[0] for r in con.execute(
        "SELECT region FROM ref.campaign_map_regions WHERE campaign_map = %s",
        (mapname,))]

    pack_path = os.path.join(packs.DATA_DIR, "data_maps.pack")
    _ptype, ents = packs.read_index(pack_path)
    want = ("campaign_maps/%s/%s" % (mapname, overlay)).lower()
    entry = next((e for e in ents if e[0].lower() == want), None)
    if entry is None:
        raise RuntimeError("no %s in data_maps.pack" % want)
    idx, pal, w, h, desc = _decode_tga(packs.extract(pack_path, entry))

    slot = {}
    for i, c in enumerate(pal.tolist()):
        slot.setdefault(tuple(c), i)
    land = [k for k in on_map if colour[k] != (0, 0, 0)]
    missing = [k for k in land if tuple(colour[k]) not in slot]
    if missing:
        raise RuntimeError("%d region colours absent from the lookup palette: %s"
                           % (len(missing), missing[:5]))
    extra = len(set(map(tuple, pal.tolist()))) - len({colour[k] for k in on_map})
    print("map %s (%s) overlay %s %dx%d desc=0x%02x palette=%d" % (
        mapname, campaign, overlay, w, h, desc, len(pal)))
    print("regions on map %d, land %d, water %d, palette colours unused by db %d" % (
        len(on_map), len(land), len(on_map) - len(land), extra))

    order = np.argsort(idx.ravel(), kind="stable")
    counts = np.bincount(idx.ravel(), minlength=len(pal))
    bounds = np.concatenate([[0], np.cumsum(counts)])
    out = {}
    empty, parts, tolmax = [], 0, 0.0
    for key in land:
        i = slot[tuple(colour[key])]
        if counts[i] == 0:
            empty.append(key)
            continue
        lin = order[bounds[i]:bounds[i + 1]]
        ys, xs = lin // w, lin % w
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
        sub = idx[y0:y1 + 1, x0:x1 + 1] == i
        comps = _components(sub)
        keep = [c for c in comps if c[0] >= MIN_PART * comps[0][0]]
        parts += len(keep) - 1
        raw = []
        for _area, runs in keep:
            m = np.zeros(sub.shape, bool)
            for (r, c0, c1) in runs:
                m[r, c0:c1] = True
            rs = _rings(m)
            if not rs:
                continue
            rs.sort(key=lambda t: -abs(_shoelace(t)))
            raw.append([(p[0] + x0, p[1] + y0) for p in rs[0]])
        for tol in TOLERANCES:
            simp = [s for s in (_simplify(r, tol) for r in raw) if len(s) >= 3]
            if sum(len(s) for s in simp) <= MAX_POINTS:
                break
        tolmax = max(tolmax, tol)
        cx, cy = _centroid(simp)
        out[key] = {"rings": simp, "area_px": int(counts[i]), "cx": cx, "cy": cy}
    if empty:
        print("!! %d region colours never appear in the raster: %s" % (len(empty), empty[:5]))
    print("traced %d regions, %d extra components kept, worst tolerance %.1f px"
          % (len(out), parts, tolmax))
    return out, w, h


def store(con, geo, w, h):
    con.execute(DDL)
    from decisions import dicts
    ids = dicts.Dicts(con).resolve("region", sorted(geo))
    missing = sorted(set(geo) - set(ids))
    if missing:
        raise RuntimeError("%d traced regions failed dict.region resolution: %s"
                           % (len(missing), missing[:5]))
    rows, total = [], 0
    for key, g in geo.items():
        blob = json.dumps([[c for p in ring for c in p] for ring in g["rings"]],
                          separators=(",", ":"))
        total += len(blob)
        rows.append((ids[key], g["cx"], g["cy"], g["area_px"], w, h, blob))
    con.cursor().executemany(
        "INSERT INTO ref.region_geometry(region_id, cx, cy, area_px, map_w, map_h, outline)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (region_id) DO UPDATE SET cx=excluded.cx, cy=excluded.cy,"
        " area_px=excluded.area_px, map_w=excluded.map_w, map_h=excluded.map_h,"
        " outline=excluded.outline", rows)
    print("geometry regions %d written to ref.region_geometry" % len(rows))
    print("outline payload %d bytes (%.2f MB) over %d rows, %d points"
          % (total, total / 1e6, len(rows),
             sum(len(r) for g in geo.values() for r in g["rings"])))


def report(con, geo, w, h):
    print("\n== map %d x %d, origin top-left, y increases southward ==" % (w, h))
    for name in SPOT:
        key = next((k for k in geo if k.endswith("_region_" + name)), None)
        if key is None:
            print("  %-16s MISSING" % name)
            continue
        g = geo[key]
        row = con.execute(
            "SELECT g.cx, g.cy, g.area_px FROM ref.region_geometry g"
            " JOIN dict.region d ON d.id = g.region_id WHERE d.key = %s",
            (key,)).fetchone()
        print("  %-16s %-46s centroid=(%7.1f,%7.1f) area=%6d px rings=%d points=%3d db=%s"
              % (name, key, g["cx"], g["cy"], g["area_px"], len(g["rings"]),
                 sum(len(r) for r in g["rings"]),
                 "-" if row is None else "(%.1f,%.1f) %d px" % (row[0], row[1], row[2])))


def main():
    t0 = time.time()
    con = pg.connect(app_name="tw-mapgeom", autocommit=True)
    try:
        for campaign in CAMPAIGNS:
            geo, w, h = extract(con, campaign)
            store(con, geo, w, h)
            report(con, geo, w, h)
    finally:
        con.close()
    print("map geometry exit %.1f s" % (time.time() - t0))


if __name__ == "__main__":
    main()
