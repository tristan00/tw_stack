from __future__ import annotations

import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8777"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ui")
SHOTS = (("run", "/run", "text=turns/hr", True),
         ("experiment-ledger", "/models?view=training", "text=experiment ledger", False),
         ("decisions", "/decisions", "text=every action", False),
         ("decision-detail", "/decisions/21558", "text=where the time went", False),
         ("campaigns-starts", "/campaigns?view=starts", "text=plays per start", False),
         ("campaigns-selector", "/selector", "text=winning score per pick", False),
         ("campaigns-start", "/starts/wh3_main_combi/wh2_main_def_har_ganeth",
          "text=rank over picks", False),
         ("campaigns-all", "/campaigns?view=campaigns", "text=lord levels", False),
         ("models-disk", "/models", "text=models on disk", True))
WINDOW = {"width": 1600, "height": 1100}
CONTENT_TIMEOUT_MS = 20000
SETTLE_S = 2.0


def api_up():
    try:
        with urllib.request.urlopen(BASE + "/api/run", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if not api_up():
        raise SystemExit("the API is not answering on %s -- start it (runctl up) and rerun" % BASE)
    os.makedirs(OUT_DIR, exist_ok=True)
    from playwright.sync_api import sync_playwright
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport=WINDOW)
        for name, route, ready, full in SHOTS:
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
    print("done: %d screenshots in %s" % (len(SHOTS), OUT_DIR))


if __name__ == "__main__":
    sys.exit(main())
