r"""dilemmas.py -- the DILEMMAS/INCIDENTS decision handler for the offline advisor pipeline.

Dilemmas (and scripted incidents that resolve as dilemmas) are the odd one out among the advisor's
decision types: unlike the 7 panel menus there is NO UI `menu_open` to read the option set from.
Nothing is captured about the choices SHOWN -- only the choice the player MADE. So this handler is:

  EVENT-ANCHORED chosen  <- TWSTATE `DilemmaChoiceMadeEvent` (structurer.parse_events, player_only):
                            fields `dilemma` (key), `choice` (0-indexed int ordinal -- 0 is a REAL
                            choice, never dropped), `choice_key` (the ordinal TOKEN, e.g. "FIRST" or
                            "SCRIPTED_1"). Its `t` is the session game-time.
  RECONSTRUCTED option set <- reference.sqlite `loc` table. The choice labels live under
                            `cdir_events_dilemma_choice_details_localised_choice_label_<dilemma><TOKEN>`
                            where <TOKEN> is the per-option ordinal token. Two token schemes seen in
                            real data: word ordinals FIRST/SECOND/.../TENTH (-> 0..9) and SCRIPTED_<n>
                            (-> n-1). The suffix after the dilemma key is filtered to EXACTLY a known
                            token so a longer dilemma sharing the prefix can't leak options (real trap:
                            `wh3_main_dilemma_sla_2` vs `wh3_main_dilemma_sla_2_a`).
  CONTEXT               <- the player faction's `kind:faction` is_human snapshot most recent AT/BEFORE
                            the event (per tail -- game-time `t` RESETS each session, so never merged).

`build_dilemma_decisions(run_dir)` emits one Decision per player dilemma choice, in the pipeline shape:
    {campaign, turn, type:"dilemma", dilemma, dilemma_title, prompt, trigger_t, context,
     options:[{ordinal, key, label, title, available:True}], chosen (=chosen ordinal int),
     chosen_linked (=the chosen ordinal maps to a reconstructed option)}

    python dilemmas.py <run_dir> [<run_dir> ...]   # counts, link rate, and real samples

100% offline: reads only the run's logs + reference.sqlite. No game, no bus.
"""
import bisect
import glob
import os
import re
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("structurer", "runs"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _p)))
import structurer          # noqa: E402

# ---- loc key templates -----------------------------------------------------------------------
LABEL_PREFIX = "cdir_events_dilemma_choice_details_localised_choice_label_"
TITLE_PREFIX = "cdir_events_dilemma_choice_details_localised_choice_title_"
DIL_TITLE = "dilemmas_localised_title_"
DIL_DESC = "dilemmas_localised_description_"

# ordinal TOKEN -> 0-based index. Word ordinals + the SCRIPTED_<n> scheme are both handled.
_WORD_ORD = {w: i for i, w in enumerate(
    ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH",
     "NINTH", "TENTH", "ELEVENTH", "TWELFTH"])}
_SCRIPTED = re.compile(r"SCRIPTED_(\d+)$")
_TR = re.compile(r"\{\{tr:([a-z0-9_]+)\}\}", re.I)


def _token_ordinal(token):
    """A per-option loc suffix -> its 0-based ordinal, or None if it isn't a recognised option token
    (so prefix-shared dilemmas and template junk are excluded from the choice set)."""
    if token in _WORD_ORD:
        return _WORD_ORD[token]
    m = _SCRIPTED.fullmatch(token)
    if m:
        return int(m.group(1)) - 1
    return None


# ---- reference.sqlite (read-only) ------------------------------------------------------------
def _default_ref_db():
    for c in (os.path.join(_HERE, "reference", "reference.sqlite"),
              os.path.join(_HERE, "..", "reference", "reference.sqlite")):
        if os.path.isfile(c):
            return os.path.abspath(c)
    return os.path.join(_HERE, "reference", "reference.sqlite")


def _open_ref(db_path):
    """Open reference.sqlite read-only (other agents may be rebuilding it; never write, tolerate busy)."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True, timeout=10)
    except sqlite3.OperationalError:
        con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = None
    return con


class _Loc:
    """Thin cached accessor over the `loc` table for dilemma reconstruction."""

    def __init__(self, con):
        self.cur = con.cursor()
        self._dil_cache = {}

    def _resolve_tr(self, text, depth=0):
        """Resolve {{tr:KEY}} indirections in a loc value to the referenced text (bounded depth)."""
        if not text or depth > 4 or "{{tr:" not in text:
            return text

        def sub(m):
            row = self.cur.execute("select text from loc where key=?", (m.group(1),)).fetchone()
            return self._resolve_tr(row[0], depth + 1) if row else m.group(0)

        return _TR.sub(sub, text)

    def _one(self, key):
        row = self.cur.execute("select text from loc where key=?", (key,)).fetchone()
        return self._resolve_tr(row[0]) if row and row[0] is not None else None

    def reconstruct(self, dilemma):
        """(options, dilemma_title, prompt) for one dilemma key. options sorted by ordinal; each is
        {ordinal, key(token), label, title, available:True}. Cached per dilemma key."""
        if dilemma in self._dil_cache:
            return self._dil_cache[dilemma]
        label_full = LABEL_PREFIX + dilemma
        title_full = TITLE_PREFIX + dilemma
        # per-option flavour titles, keyed by ordinal token (guard against '_' LIKE-wildcard leakage)
        titles = {}
        for key, text in self.cur.execute("select key,text from loc where key like ? escape '\\'",
                                           (_like_prefix(title_full) + "%",)):
            if key.startswith(title_full):
                titles[key[len(title_full):]] = self._resolve_tr(text)
        opts = {}
        for key, text in self.cur.execute("select key,text from loc where key like ? escape '\\'",
                                           (_like_prefix(label_full) + "%",)):
            if not key.startswith(label_full):      # '_' in the key is a LIKE wildcard -> verify literal
                continue
            token = key[len(label_full):]
            ordv = _token_ordinal(token)
            if ordv is None:                        # not an option of THIS dilemma (prefix collision)
                continue
            opts[ordv] = {"ordinal": ordv, "key": token, "label": self._resolve_tr(text),
                          "title": titles.get(token), "available": True}
        options = [opts[o] for o in sorted(opts)]
        result = (options, self._one(DIL_TITLE + dilemma), self._one(DIL_DESC + dilemma))
        self._dil_cache[dilemma] = result
        return result


def _like_prefix(s):
    """Escape LIKE metacharacters so a literal prefix match doesn't treat '_'/'%' as wildcards."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---- the build --------------------------------------------------------------------------------
def _campaign_id(run_id, faction):
    return "%s.%s" % (run_id[-6:], faction.split("_", 2)[-1][:12])


def build_dilemma_decisions(run_dir, db_path=None):
    """Every player dilemma choice in a run, across its campaigns, as pipeline Decision dicts.

    A run dir often holds several campaigns (restarts into other factions) spread over many log tails,
    incl. reload-continuation tails whose session game-time `t` starts over. Each tail is processed on
    its OWN clock; identical choices captured in overlapping tails are de-duped by
    (faction, turn, dilemma, choice, choice_key). Campaign = player faction within the run.
    """
    con = _open_ref(db_path or _default_ref_db())
    try:
        loc = _Loc(con)
        run_id = os.path.basename(run_dir.rstrip("/\\"))
        seen = set()
        out = []
        for log in sorted(glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail"))):
            player = structurer.player_faction(log)
            if not player:
                continue
            # this tail's player faction snapshots on the tail's own clock (context source)
            snaps = [(r.get("t", 0.0), r.get("turn"), r) for r in structurer.iter_twstate(log)
                     if r.get("kind") == "faction" and r.get("faction") == player
                     and r.get("turn") is not None]
            snaps.sort(key=lambda s: s[0])
            snap_t = [s[0] for s in snaps]
            for e in structurer.parse_events(log, player_only=True):
                if e.get("event") != "DilemmaChoiceMadeEvent":
                    continue
                dilemma, choice = e.get("dilemma"), e.get("choice")
                if dilemma is None or choice is None:
                    continue
                turn, et, ckey = e.get("turn"), e.get("t", 0.0), e.get("choice_key")
                dedup = (player, turn, dilemma, choice, ckey)
                if dedup in seen:
                    continue
                seen.add(dedup)
                # context: latest player-faction snapshot at/before the event on THIS tail's clock
                ctx = None
                i = bisect.bisect_right(snap_t, et) - 1
                if i >= 0:
                    ctx = snaps[i][2]
                if ctx is None:
                    ctx = next((r for (_, tn, r) in snaps if tn == turn), None)
                options, dtitle, prompt = loc.reconstruct(dilemma)
                out.append({
                    "campaign": _campaign_id(run_id, player),
                    "turn": turn,
                    "type": "dilemma",
                    "dilemma": dilemma,
                    "dilemma_title": dtitle,
                    "prompt": prompt,
                    "trigger_t": et,
                    "context": ctx,
                    "options": options,
                    "chosen": choice,                       # the chosen ordinal (0-based int)
                    "chosen_linked": any(o["ordinal"] == choice for o in options),
                })
        out.sort(key=lambda d: (d["campaign"], d["turn"], d["trigger_t"]))
        return out
    finally:
        con.close()


# ---- CLI --------------------------------------------------------------------------------------
def _print_sample(d):
    ch = d["chosen"]
    chosen_label = next((o["label"] for o in d["options"] if o["ordinal"] == ch), None)
    print("  dilemma : %s   (turn %s, campaign %s)" % (d["dilemma"], d["turn"], d["campaign"]))
    if d["dilemma_title"]:
        print("  title   : %s" % d["dilemma_title"])
    print("  choices reconstructed from loc:")
    if not d["options"]:
        print("      (none -- dilemma not in loc)")
    for o in d["options"]:
        mark = "  <== CHOSEN" if o["ordinal"] == ch else ""
        print("      [%d] %-8s %s%s" % (o["ordinal"], o["key"], o["label"], mark))
    print("  chosen  : ordinal %s -> %r   (linked=%s)" % (ch, chosen_label, d["chosen_linked"]))
    print()


def _report(run_dir, db_path=None):
    ds = build_dilemma_decisions(run_dir, db_path=db_path)
    linked = sum(1 for d in ds if d["chosen_linked"])
    print("=" * 78)
    print("run: %s" % run_dir)
    print("  dilemmas found       : %d" % len(ds))
    print("  chosen ordinal linked: %d / %d (%.0f%%)"
          % (linked, len(ds), 100.0 * linked / max(1, len(ds))))
    by_camp = defaultdict(int)
    for d in ds:
        by_camp[d["campaign"]] += 1
    for c in sorted(by_camp):
        print("     campaign %-22s %d" % (c, by_camp[c]))
    # 2-3 real samples, preferring linked ones with the richest choice sets
    samples = sorted(ds, key=lambda d: (not d["chosen_linked"], -len(d["options"])))[:3]
    if samples:
        print("  --- samples ---")
        for d in samples:
            _print_sample(d)
    return ds


def main():
    if len(sys.argv) < 2:
        print("usage: python dilemmas.py <run_dir> [<run_dir> ...]")
        sys.exit(2)
    total = linked = 0
    for run_dir in sys.argv[1:]:
        ds = _report(run_dir)
        total += len(ds)
        linked += sum(1 for d in ds if d["chosen_linked"])
    if len(sys.argv) > 2:
        print("=" * 78)
        print("GRAND TOTAL: %d dilemmas, %d linked (%.0f%%)"
              % (total, linked, 100.0 * linked / max(1, total)))


if __name__ == "__main__":
    main()
