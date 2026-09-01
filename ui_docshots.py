from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8777"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ui")
LEGACY = (("run", "/run", "text=turns/hr", True),
          ("experiment-ledger", "/models?view=training", "text=experiment ledger", False),
          ("decisions", "/decisions", "text=every action", False),
          ("campaigns-starts", "/campaigns?view=starts", 'h2:has-text("starts")', False),
          ("campaigns-selector", "/selector", 'h2:has-text("pick log")', False),
          ("campaigns-all", "/campaigns?view=campaigns", "text=lord levels", False))
WINDOW = {"width": 1600, "height": 1100}
CONTENT_TIMEOUT_MS = 20000
SETTLE_S = 2.0
INIT = """
try {
  localStorage.setItem('theme', 'dark');
  localStorage.setItem('devmode', '1');
} catch (e) {}
"""


def api_up():
    try:
        with urllib.request.urlopen(BASE + "/api/run", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def q(v):
    return urllib.parse.quote(str(v), safe="")


def first_key(path, field="key"):
    rows = get(path).get("rows") or []
    if not rows:
        raise SystemExit("%s returned no rows -- cannot pick a detail page" % path)
    return rows[0][field]


def targets():
    camps = get("/api/campaigns").get("rows") or []
    if not camps:
        raise SystemExit("/api/campaigns returned no rows")
    starts = get("/api/campaigns/starts").get("rows") or []
    if not starts:
        raise SystemExit("/api/campaigns/starts returned no rows")
    decs = get("/api/decisions").get("rows") or []
    if not decs:
        raise SystemExit("/api/decisions returned no rows")
    start = starts[0]
    return {"campaign": q(camps[0]["campaign"]["raw"]),
            "map": q(start["campaign_map"]["raw"]),
            "faction": q(start["faction"]["raw"]),
            "decision": decs[0]["decision_id"],
            "item": q(first_key("/api/items")),
            "building": q(first_key("/api/buildings")),
            "research": q(first_key("/api/research")),
            "skill": q(first_key("/api/skills")),
            "trait": q(first_key("/api/traits"))}


def h2(text):
    return 'h2:has-text("%s")' % text


def shots(t):
    camp = "/campaigns/%s" % t["campaign"]
    start = "/starts/%s/%s" % (t["map"], t["faction"])
    out = [("decision-detail", "/decisions/%s" % t["decision"], h2("where the time went")),
           ("campaigns-start", start + "?tab=performance", h2("reward per campaign")),
           ("models-disk", "/models?view=disk", h2("models on disk"))]
    for name, tab, ready in (("overview", "overview", h2("state")),
                             ("buildings", "buildings", h2("construction ledger")),
                             ("research", "research", h2("research timeline")),
                             ("skills", "skills", h2("skill ledger")),
                             ("items", "items", h2("item ledger"))):
        out.append(("now-%s" % name, "/?tab=%s" % tab, ready))
        out.append(("campaign-%s" % name, "%s?tab=%s" % (camp, tab), ready))
    for name, ready in (("openings", h2("openings over time")),
                        ("buildings", h2("buildings")),
                        ("research", h2("techs")),
                        ("skills", h2("who ranks what")),
                        ("items", h2("does wearing it pay")),
                        ("campaigns", h2("its campaigns"))):
        out.append(("start-%s" % name, "%s?tab=%s" % (start, name), ready))
    out += [("lookup", "/lookup", h2("campaigns")),
            ("items-index", "/items?tab=items", h2("items")),
            ("items-swaps", "/items?tab=swaps", h2("kept swaps")),
            ("item-detail", "/items/%s" % t["item"], h2("what it does")),
            ("status", "/status", h2("view settings")),
            ("positions", "/positions", h2("what gets taken")),
            ("log", "/log", h2("log")),
            ("infra", "/infra", h2("controls")),
            ("selector-picks", "/selector?view=picks", h2("pick log")),
            ("selector-window", "/selector?view=window", h2("window churn"))]
    for fam, noun, key in (("buildings", "building", t["building"]),
                           ("research", "tech", t["research"]),
                           ("skills", "skill", t["skill"])):
        out.append(("%s-index" % fam, "/%s?tab=all" % fam, h2(fam)))
        out.append(("%s-choices" % fam, "/%s?tab=choices" % fam, h2("%s choices" % noun)))
        out.append(("%s-overtime" % fam, "/%s?tab=overtime" % fam, h2("openings over time")))
        out.append(("%s-detail" % fam.rstrip("s"), "/%s/%s" % (fam, key), h2("by start")))
    out += [("traits-index", "/traits", h2("traits")),
            ("trait-detail", "/traits/%s" % t["trait"], h2("by start"))]
    for name, view, ready in (("log", "log", h2("every action")),
                              ("actions", "actions", h2("confirm rate")),
                              ("diplomacy", "diplomacy", h2("diplomacy")),
                              ("menus", "menus", h2("blocking screens")),
                              ("timeline", "timeline", h2("timeline"))):
        out.append(("decisions-%s" % name, "/decisions?view=%s" % view, ready))
    for name, view, ready in (("forcing", "forcing", h2("what each model wants to do")),
                              ("agreement", "agreement", h2("how alike the ranking arms are")),
                              ("drift", "drift", h2("has the agreement of")),
                              ("correlations", "correlations", h2("track how the campaign went")),
                              ("training", "training", h2("experiment ledger"))):
        out.append(("models-%s" % name, "/models?view=%s" % view, ready))
    return [(n, r, ready, True) for n, r, ready in out]


def main():
    if not api_up():
        raise SystemExit("the API is not answering on %s -- start it (runctl up) and rerun" % BASE)
    os.makedirs(OUT_DIR, exist_ok=True)
    plan = list(LEGACY) + shots(targets())
    from playwright.sync_api import sync_playwright
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport=WINDOW, color_scheme="dark")
        page.add_init_script(INIT)
        for name, route, ready, full in plan:
            out = os.path.join(OUT_DIR, "%s.png" % name)
            page.goto(BASE + route, wait_until="domcontentloaded")
            try:
                page.locator(ready).first.wait_for(timeout=CONTENT_TIMEOUT_MS)
            except Exception:
                print("WARN %s: %r never appeared in %dms -- shooting anyway"
                      % (route, ready, CONTENT_TIMEOUT_MS))
            time.sleep(SETTLE_S)
            page.screenshot(path=out, full_page=full)
            ok = os.path.isfile(out) and os.path.getsize(out) > 10_000
            print("%s %s -> %s (%d bytes)" % ("ok  " if ok else "FAIL", route, out,
                                              os.path.getsize(out) if os.path.isfile(out) else 0))
            if not ok:
                failed.append(name)
        browser.close()
    if failed:
        raise SystemExit("failed: %s" % failed)
    print("done: %d screenshots in %s" % (len(plan), OUT_DIR))


if __name__ == "__main__":
    sys.exit(main())
