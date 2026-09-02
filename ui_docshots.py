from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8777"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ui")
WINDOW = {"width": 1600, "height": 1100}
CONTENT_TIMEOUT_MS = 20000
SETTLE_S = 1.6

RAIL = ("characters", "research", "realm", "diplomacy", "forces", "items",
        "events", "starts")


def _init(campaign, dev):
    return ("try { localStorage.setItem('tw.dev', '%s');"
            " localStorage.setItem('tw.campaign', %s); } catch (e) {}"
            % ("1" if dev else "0", json.dumps(campaign)))


def api_up():
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.load(r)


def q(v):
    return urllib.parse.quote(str(v), safe="")


def targets():
    frame = get("/api/dash/frame")
    camps = [c for c in (frame.get("campaigns") or []) if c.get("built_turn")]
    if not camps:
        raise SystemExit("/api/dash/frame has no materialised campaign -- let the "
                         "analytics runner fold at least one turn and rerun")
    best = max(camps, key=lambda c: c.get("built_turn") or 0)
    key = best["campaign_key"]
    turns = get("/api/dash/campaign/%s/turns" % q(key)).get("turns") or []
    turn = turns[-1] if turns else None
    pos = get("/api/dash/campaign/%s/position" % q(key))
    chars = pos.get("characters") or []
    research = (pos.get("research") or {}).get("rows") or []
    regions = (pos.get("realm") or {}).get("regions") or []
    records = get("/api/dash/records").get("records") or []
    record = max(records, key=lambda r: r.get("rows") or 0)["record"]
    return {"campaign": key, "turn": turn,
            "cqi": chars[0]["cqi"] if chars else None,
            "tech": research[0]["tech"] if research else None,
            "region": regions[0]["region"] if regions else None,
            "record": record,
            "start_id": best.get("start_id")}


def plan(t):
    turn = "?turn=%s" % t["turn"] if t["turn"] is not None else ""
    hist = ("&" if turn else "?") + "scope=history"
    out = [("home", "/campaign" + turn, "h1", False)]
    for name in RAIL:
        out.append(("campaign-%s" % name, "/campaign/%s%s" % (name, turn), "h1", True))
        out.append(("campaign-%s-history" % name,
                    "/campaign/%s%s%s" % (name, turn, hist), "h1", True))
    if t["cqi"]:
        out.append(("campaign-character",
                    "/campaign/characters%s&cqi=%s" % (turn or "?turn=1", t["cqi"]),
                    "h1", True))
    for fam, key in (("tech", t["tech"]), ("region", t["region"])):
        if key:
            out.append(("thing-%s" % fam, "/thing/%s/%s" % (fam, q(key)), "h1", True))
    out += [("analytics", "/analytics", "h1", True),
            ("analytics-builder", "/analytics/builder", "h1", True),
            ("explore", "/explore", "h1", True),
            ("explore-record", "/explore?record=%s" % t["record"], "h1", True),
            ("options", "/options", "h1", True)]
    return out


def dev_plan():
    return [("dev-run", "/run", "h1", True),
            ("dev-experiments", "/experiments", "h1", True),
            ("dev-decisions", "/decisions", "h1", True)]


def shoot(page, items, tag=""):
    failed = []
    for name, route, ready, full in items:
        out = os.path.join(OUT_DIR, "%s.png" % name)
        page.goto(BASE + route, wait_until="domcontentloaded")
        try:
            page.locator(ready).first.wait_for(timeout=CONTENT_TIMEOUT_MS)
        except Exception:
            print("WARN %s: %r never appeared -- shooting anyway" % (route, ready))
        time.sleep(SETTLE_S)
        page.screenshot(path=out, full_page=full)
        size = os.path.getsize(out) if os.path.isfile(out) else 0
        ok = size > 8000
        print("%s %-46s -> %-34s %d bytes" % ("ok  " if ok else "THIN", route,
                                              os.path.basename(out), size))
        if not ok:
            failed.append(name)
    return failed


def main():
    if not api_up():
        raise SystemExit("the API is not answering on %s -- start it (runctl up) and rerun"
                         % BASE)
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)
    t = targets()
    print("campaign %s at turn %s" % (t["campaign"], t["turn"]))
    from playwright.sync_api import sync_playwright
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for dev in (False, True):
            page = browser.new_page(viewport=WINDOW, color_scheme="dark")
            page.add_init_script(_init(t["campaign"], dev))
            failed += shoot(page, dev_plan() if dev else plan(t))
            page.close()
        browser.close()
    n = len(os.listdir(OUT_DIR))
    if failed:
        raise SystemExit("thin or missing: %s" % failed)
    print("done: %d screenshots in %s" % (n, OUT_DIR))


if __name__ == "__main__":
    sys.exit(main())
