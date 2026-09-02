from __future__ import annotations

import json
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import common
from advisor.reference.build_reference import (decode_db_table, load_db_schema, parse_pack,
                                               read_file)
from decisions import pg

GAME = common.GAME_DATA_DIR
CAMPAIGN = "wh3_main_combi"
MAX_POINTS = 120
MIN_PART = 0.05
TOLERANCES = (1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0, 32.0)
SPOT = ("hag_graef", "altdorf", "naggarond", "lothern", "karaz_a_karak", "couronne")

PLAYABLE_AREAS = {"12": [
    ["index", "StringU8"], ["sea_trade", "Boolean"], ["map_file", "OptionalStringU8"],
    ["overlay_file", "OptionalStringU8"], ["radar_file", "OptionalStringU8"],
    ["meaningful_id", "StringU8"], ["preview_width", "I32"], ["preview_height", "I32"],
    ["minx", "F32"], ["maxx", "F32"], ["mapname", "StringU8"],
    ["minimap_lookup_file", "OptionalStringU8"], ["is_available_in_custom_battle", "Boolean"],
    ["terrain_folder", "StringU8"], ["campaign_key", "StringU8"],
    ["frontend_image", "OptionalStringU8"], ["video", "StringU8"],
    ["is_mpc_available", "Boolean"], ["campaign_overlay_lookup", "StringU8"],
    ["campaign_overlay_map", "StringU8"], ["quadtree_margin", "F32"], ["sort_order", "I32"],
    ["campaign_overlay_map_text", "StringU8"]]}

DDL = ("ALTER TABLE reference.ref_region"
       " ADD COLUMN IF NOT EXISTS outline TEXT,"
       " ADD COLUMN IF NOT EXISTS cx DOUBLE PRECISION,"
       " ADD COLUMN IF NOT EXISTS cy DOUBLE PRECISION,"
       " ADD COLUMN IF NOT EXISTS area_px INTEGER,"
       " ADD COLUMN IF NOT EXISTS map_w INTEGER,"
       " ADD COLUMN IF NOT EXISTS map_h INTEGER")


def _schema():
    s = dict(load_db_schema())
    v = dict(s.get("campaign_map_playable_areas_tables") or {})
    v.update(PLAYABLE_AREAS)
    s["campaign_map_playable_areas_tables"] = v
    return s


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


def extract():
    schema = _schema()
    dfiles, dd = parse_pack(GAME + "/db.pack")
    areas, ameta = decode_db_table(dfiles, dd, "campaign_map_playable_areas_tables", schema)
    if not ameta["ok"]:
        raise RuntimeError("campaign_map_playable_areas_tables: " + ameta["reason"])
    hit = [a for a in areas if a["campaign_key"] == CAMPAIGN]
    if len(hit) != 1:
        raise RuntimeError("expected one playable area for %s, got %d" % (CAMPAIGN, len(hit)))
    mapname, overlay = hit[0]["mapname"], hit[0]["overlay_file"]

    regs, rmeta = decode_db_table(dfiles, dd, "regions_tables", schema)
    if not rmeta["ok"]:
        raise RuntimeError("regions_tables: " + rmeta["reason"])
    cmr, cmeta = decode_db_table(dfiles, dd, "campaign_map_regions_tables", schema)
    if not cmeta["ok"]:
        raise RuntimeError("campaign_map_regions_tables: " + cmeta["reason"])
    colour = {r["key"]: (r["r"], r["g"], r["b"]) for r in regs}
    on_map = [r["region"] for r in cmr if r["campaign_map"] == mapname]

    mfiles, md = parse_pack(GAME + "/data_maps.pack")
    want = ("campaign_maps/%s/%s" % (mapname, overlay)).lower()
    path = next((n for n, _o, _s, _c in mfiles if n.replace("\\", "/").lower() == want), None)
    if path is None:
        raise RuntimeError("no %s in data_maps.pack" % want)
    idx, pal, w, h, desc = _decode_tga(read_file(mfiles, md, path))

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
        mapname, CAMPAIGN, overlay, w, h, desc, len(pal)))
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


def store(geo, w, h):
    con = pg.connect(autocommit=True)
    con.execute(DDL)
    live = {r[0] for r in con.execute("SELECT region FROM reference.ref_region").fetchall()}
    rows, total = [], 0
    for key, g in geo.items():
        if key not in live:
            continue
        blob = json.dumps([[c for p in ring for c in p] for ring in g["rings"]],
                          separators=(",", ":"))
        total += len(blob)
        rows.append((blob, g["cx"], g["cy"], g["area_px"], w, h, key))
    con.cursor().executemany(
        "UPDATE reference.ref_region SET outline=%s, cx=%s, cy=%s, area_px=%s,"
        " map_w=%s, map_h=%s WHERE region=%s", rows)
    print("ref_region rows %d, geometry regions %d, updated %d" % (len(live), len(geo), len(rows)))
    print("geometry with no ref_region row: %s" % sorted(set(geo) - live))
    print("ref_region rows with no geometry: %s" % sorted(live - set(geo)))
    print("outline payload %d bytes (%.2f MB) over %d rows, %d points"
          % (total, total / 1e6, len(rows),
             sum(len(r) for g in geo.values() for r in g["rings"])))
    return con


def _near(a, b):
    best = None
    for ring in a:
        p = np.asarray(ring, np.float64)
        for other in b:
            q = np.asarray(other, np.float64)
            s, e = q, np.roll(q, -1, axis=0)
            d = e - s
            ll = (d * d).sum(1)
            ll[ll == 0] = 1.0
            t = np.clip(((p[:, None, 0] - s[None, :, 0]) * d[None, :, 0]
                         + (p[:, None, 1] - s[None, :, 1]) * d[None, :, 1]) / ll[None, :], 0, 1)
            dx = p[:, None, 0] - (s[None, :, 0] + t * d[None, :, 0])
            dy = p[:, None, 1] - (s[None, :, 1] + t * d[None, :, 1])
            m = float(np.sqrt(dx * dx + dy * dy).min())
            best = m if best is None else min(best, m)
    return best


def report(con, geo, w, h):
    print("\n== map %d x %d, origin top-left, y increases southward ==" % (w, h))
    cur = con.cursor()
    for name in SPOT:
        key = next((k for k in geo if k.endswith("_region_" + name)), None)
        if key is None:
            print("  %-16s MISSING" % name)
            continue
        g = geo[key]
        row = cur.execute("SELECT cx, cy, area_px, province, adjacent"
                          " FROM reference.ref_region WHERE region=%s", (key,)).fetchone()
        print("  %-16s %-46s centroid=(%7.1f,%7.1f) area=%6d px rings=%d points=%3d db=%s"
              % (name, key, g["cx"], g["cy"], g["area_px"], len(g["rings"]),
                 sum(len(r) for r in g["rings"]),
                 "-" if row is None else "(%.1f,%.1f) %d px %s" % (row[0], row[1], row[2], row[3])))
        if row is not None and row[4]:
            for nb in row[4].split(",")[:4]:
                if nb in geo:
                    print("      touches %-44s gap %.1f px" % (nb, _near(g["rings"], geo[nb]["rings"])))

    pairs, gaps = 0, []
    for r in cur.execute("SELECT region, adjacent FROM reference.ref_region"
                         " WHERE adjacent <> ''").fetchall():
        if r[0] not in geo:
            continue
        for nb in r[1].split(","):
            if nb in geo and nb > r[0]:
                pairs += 1
                gaps.append(_near(geo[r[0]]["rings"], geo[nb]["rings"]))
    if gaps:
        a = np.asarray(gaps)
        print("\nadjacency: %d declared neighbour pairs, gap median %.2f px, p90 %.2f px,"
              " max %.2f px, within 5 px %.1f%%"
              % (pairs, float(np.median(a)), float(np.percentile(a, 90)), float(a.max()),
                 100.0 * float((a <= 5.0).mean())))


def main():
    geo, w, h = extract()
    con = store(geo, w, h)
    report(con, geo, w, h)
    con.close()


if __name__ == "__main__":
    main()
