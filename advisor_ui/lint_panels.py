"""Render every panel against the live run and flag defects only visible in the output.

Reading the code does not catch these: a column can be perfectly implemented and still
be empty on every row because nothing writes that field, an entity can be escaped twice
and render as literal "&mdash;", the game's own [[col:...]] markup can leak through, and
floats from the bus can print as "4.0" where a turn number belongs.

    D:\\totalwar_runner\\.venv\\Scripts\\python.exe advisor_ui/lint_panels.py

Run it after touching any panel. A column flagged "empty in every row" is either dead
weight or a field nobody populates -- both worth knowing before shipping the tab.
"""
import re
import sys

W = sys.argv[1] if len(sys.argv) > 1 else r"D:\tw_stack"
for p in (W + r"\advisor_ui", W + r"\advisor", r"D:\tw_stack\decisions", r"D:\tw_stack"):
    sys.path.insert(0, p)
import ui

run = r"D:/twdata/runs/human/run"
con = ui._con(run)

# ROW yields the content BETWEEN <tr> and </tr>, so </tr> is not in the string the cell
# pattern runs over: without the $ alternative the final cell of every row never matched
# and the rightmost column of all 14 panels went unchecked.
CELL = re.compile(r"<t[dh][^>]*>(.*?)(?=<t[dh]|</tr>|$)", re.S)
TAG = re.compile(r"<[^>]+>")
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TABLE = re.compile(r"<table>(.*?)</table>", re.S)
CARD = re.compile(r"<div class=card>(.*?)</div></div>", re.S)
KV = re.compile(r"<div class=k>(.*?)</div>\s*<div class='?v[^>]*>(.*?)$", re.S)


def text(s):
    return TAG.sub("", s).replace("&nbsp;", " ").strip()


problems = []
PANELS = list(ui.PANELS) + [("head", "head", lambda con, run, q: ui.render_head(con, run))]
for slug, title, fn in PANELS:
    try:
        h = fn(con, run, {})
    except Exception as e:
        problems.append((slug, "RENDER FAILED", repr(e)[:120]))
        continue
    # stat cards are rendered on every tab and were never checked -- a float where an
    # integer belongs is exactly as wrong in a card as in a column
    for card in CARD.findall(h):
        kv = KV.search(card)
        if not kv:
            continue
        k, v = text(kv.group(1)), text(kv.group(2))
        if re.fullmatch(r"-?\d+\.0", v):
            problems.append((slug, "card value is an integer rendered as a float",
                             "%r = %s" % (k, v)))
        if v and len(v) >= 24 and not v[-1].isalnum() and v[-1] not in ")%]":
            problems.append((slug, "card value looks truncated mid-value",
                             "%r = %r" % (k, v[-28:])))
    # literal entities: text that still shows &word; after tag stripping
    for m in set(re.findall(r"&amp;[a-z]+;", h)):
        problems.append((slug, "double-escaped entity renders literally", m))
    for m in set(re.findall(r"\[\[[^\]]{1,40}\]\]", h)):
        problems.append((slug, "raw game markup leaked into html", m[:40]))
    for t in TABLE.findall(h):
        rows = ROW.findall(t)
        if len(rows) < 3:
            continue
        # header rows are the leading rows built only from <th>
        hdrs = []
        for r in rows:
            if "<td" in r or "<th" not in r:
                break
            hdrs.append(r)
        if len(hdrs) > 1:
            # a group row whose colspans do not sum to the column count silently shifts
            # every vertical rule, so the grouping labels the wrong columns
            ncols = len(CELL.findall(hdrs[-1]))
            span = 0
            for cell in re.findall(r"<th[^>]*>", hdrs[0]):
                m = re.search(r"colspan=(\d+)", cell)
                span += int(m.group(1)) if m else 1
            if span != ncols:
                problems.append((slug, "group header colspans do not match columns",
                                 "%d vs %d columns" % (span, ncols)))
        header = [text(c) for c in CELL.findall(rows[len(hdrs) - 1 if hdrs else 0])]
        body = rows[len(hdrs):] if hdrs else rows[1:]
        cols = {}
        for r in body:
            for i, c in enumerate(CELL.findall(r)):
                # a cell holding only elements (a bar, a swatch) is not an empty cell
                if not text(c) and re.search(r"<span|<div|<img|<svg", c):
                    cols.setdefault(i, []).append("[element]")
                else:
                    cols.setdefault(i, []).append(text(c))
        for i, vals in cols.items():
            if not vals:
                continue
            name = header[i] if i < len(header) else "col%d" % i
            blank = sum(1 for v in vals if v in ("", "-", "n/a", "?"))
            if blank == len(vals) and len(vals) >= 3:
                problems.append((slug, "column is empty in every row", "%r" % name))
            floats = sum(1 for v in vals if re.fullmatch(r"-?\d+\.0", v))
            if floats and floats == len([v for v in vals if v not in ("", "-")]):
                problems.append((slug, "integers rendered as floats", "%r e.g. %s"
                                 % (name, vals[0])))

if problems:
    print("%d problem(s):\n" % len(problems))
    for slug, kind, detail in problems:
        print("  %-11s %-42s %s" % (slug, kind, detail))
else:
    print("no rendering defects found")
