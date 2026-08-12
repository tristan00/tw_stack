r"""Fetch every panel the way a browser does, and cross-check it against the database.

lint_panels.py imports ui and calls the renderers directly, and it only inspects table
CELLS for formatting defects. That misses the two failure modes that actually reach the
screen:

  1. the panel raises, 404s, or hangs inside the SERVER -- never seen by an in-process
     linter that calls the render function itself
  2. the panel renders perfectly and says something FALSE -- a column wired to the wrong
     variable, so an arm with real decisions in the corpus renders as absent

(2) is the one that keeps getting shipped. The model-metrics table derived its
campaigns/turns/share columns from a set that only filled when a correlation was
computable, so gnn_marwil -- which had answered a live blocking screen -- rendered as
"0 campaigns, 0 turns, no share". Nothing was empty, nothing was malformed, no cell was
badly escaped: the panel was confidently wrong, and only a human staring at it noticed.

So this asks the database what must be visible, and fails if the page does not show it.

    D:\totalwar_runner\.venv\Scripts\python.exe advisor_ui/lint_live.py [port]

Needs the UI running (runctl up, or advisor_ui/ui.py <port>).
"""
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

for _p in (common.DECISIONS, common.ADVISOR, common.ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
BASE = "http://127.0.0.1:%d" % PORT
SLOW_S = 25.0


def _get(path, timeout=120):
    t0 = time.time()
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return None, repr(e)[:200], time.time() - t0


def _text(html):
    t = re.sub(r"<script.*?</script>", " ", html or "", flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _arms_in_corpus():
    """{arm: decisions} the corpus actually recorded, action path and interrupt path."""
    import dbopen
    import ui
    out = {"action": {}, "interrupt": {}}
    con = dbopen.connect(common.run_dbs()[0], timeout=20.0)
    try:
        for kind in ("action", "interrupt"):
            for _cid, _turn, c, _g1, _g2 in ui._mm_turn_blocks(con, kind):
                for arm, n in c.items():
                    out[kind][arm] = out[kind].get(arm, 0) + n
    finally:
        con.close()
    return out


def main():
    problems = []
    st, home, dt = _get("/")
    if st != 200:
        print("UI not reachable on %s -- %s" % (BASE, home))
        return 1
    tabs = [p[0] for p in __import__("ui").PANELS]
    print("checking %d tabs on %s" % (len(tabs), BASE))

    # The page is entirely JS-lazy: /?tab=X serves the SAME shell for every X and the
    # content arrives from /panel/X. Fetching /?tab= and checking it looked fine is how a
    # UI check passes 16 tabs in 1.6s while testing nothing at all -- every tab returned
    # byte-identical 13,905 bytes and the first version of this file called that healthy.
    bodies = {}
    lazy = set(re.findall(r"data-src=['\"]/panel/([a-z_0-9]+)", home))
    for t in tabs:
        st, body, dt = _get("/panel/%s" % t)
        txt = _text(body)
        bodies[t] = body or ""
        lazy |= set(re.findall(r"data-src=['\"]/panel/([a-z_0-9]+)", body or ""))
        flag = ""
        if st != 200:
            flag = "HTTP %s" % st
        elif "Traceback (most recent" in (body or ""):
            flag = "TRACEBACK"
        elif len(txt) < 60:
            flag = "nearly empty (%d chars)" % len(txt)
        elif dt > SLOW_S:
            flag = "slow %.0fs" % dt
        print("  tab   %-12s %s %6.1fs %8d bytes %s" % (t, st, dt, len(body or ""), flag))
        if flag:
            problems.append("tab %s: %s" % (t, flag))

    for p in sorted(lazy - set(tabs)):
        st, body, dt = _get("/panel/%s" % p)
        txt = _text(body)
        flag = ""
        if st != 200:
            flag = "HTTP %s" % st
        elif "Traceback (most recent" in (body or ""):
            flag = "TRACEBACK"
        elif not txt:
            flag = "empty"
        elif dt > SLOW_S:
            flag = "slow %.0fs" % dt
        print("  panel %-12s %s %6.1fs %8d bytes %s" % (p, st, dt, len(body or ""), flag))
        if flag:
            problems.append("panel %s: %s" % (p, flag))

    # ---- the part that catches a confidently WRONG panel -------------------
    # Every arm the corpus recorded must be visible as having played. A row of zeroes
    # for an arm that has decisions is the bug this file exists for.
    arms = _arms_in_corpus()
    mm = bodies.get("modelmetrics", "")
    # The panel holds TWO tables, action ranker and interrupt model, and they share arm
    # names. A single re.search over the whole body finds the ACTION row every time and
    # never looks at the interrupt one -- which is precisely the table that was wrong.
    # So split by tile first and check each against its own corpus counts.
    tiles = dict(re.findall(r"<div class=mmt[^>]*>(action ranker|interrupt model)</div>"
                            r"(.*?)</table>", mm or "", flags=re.S))
    kind_of = {"action ranker": "action", "interrupt model": "interrupt"}
    if not tiles and mm:
        problems.append("model metrics: neither the action nor the interrupt table rendered")
    for label, html in tiles.items():
        kind = kind_of[label]
        for arm, n in sorted(arms[kind].items()):
            if not n:
                continue
            row = re.search(r"<tr><td>%s</td>(.*?)</tr>" % re.escape(arm), html, flags=re.S)
            if not row:
                problems.append("model metrics/%s: %s played %d decisions but has no row"
                                % (kind, arm, n))
                continue
            nums = re.findall(r"<td class=num>(\d+)</td>", row.group(1))
            if len(nums) >= 2 and int(nums[0]) == 0 and int(nums[1]) == 0:
                problems.append(
                    "model metrics/%s: %s played %d decisions but its row reads 0 "
                    "campaigns / 0 turns" % (kind, arm, n))
    print("  cross-check %d table(s) against corpus arms %s"
          % (len(tiles), {k: sorted(v) for k, v in arms.items()}))

    # ---- two panels may not print the same label with different values -------
    # The largest trust failure found in the visual audit: campaigns rendered as 117, 118,
    # 124 and 126 on tabs one click apart, and decisions as 3689/3691/3692. Each number was
    # correct for its own population -- campaigns that recorded a decision, campaigns with
    # a turn series, lines in postmortems.jsonl -- and every one of them was labelled just
    # "campaigns", so they read as a dashboard contradicting itself. The rule is not "make
    # them equal" (they count different things) but "say which population", so a bare
    # unqualified count is the defect.
    QUALIFIERS = ("with", "recorded", "endings", "series", "this", "run dir", "of the",
                  "last", "blocking-menu", "acted on", "most recent", "scored", "window")
    for word in ("campaigns", "decisions"):
        seen = {}
        for tab, body in bodies.items():
            txt = _text(body)
            for m in re.finditer(r"([\d][\d,]*)\s+%s\b(.{0,40})" % word, txt):
                n, tail = m.group(1).replace(",", ""), m.group(2).lower()
                head = txt[max(0, m.start() - 60):m.start()].lower()
                if any(q in tail or q in head for q in QUALIFIERS):
                    continue
                seen.setdefault(n, set()).add(tab)
        if len(seen) > 1:
            problems.append(
                "%s printed as %s on tabs %s with no qualifier saying which population "
                "each counts" % (word, "/".join(sorted(seen)),
                                 sorted({t for v in seen.values() for t in v})))
    print("  cross-check bare counts: %s" % ("ok" if not problems else "see below"))

    print("\n%d problem(s)%s" % (len(problems), ":" if problems else ""))
    for p in problems:
        print("   ", p)
    return 1 if problems else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
