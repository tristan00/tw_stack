r"""runtime.py -- the advisor runtime. Score each menu-open and surface SCREEN-GROUPED combined
decision tables (screens.combine_screen) alongside the per-type ones.

Two entry points (both reuse models.score_decision + screens.combine_screen):
  replay(run_dir)  -- OFFLINE end-to-end: iterate a recorded run's decisions (the full join from
                      decision_instances + dilemmas), GROUP the grouped types by (campaign, turn,
                      screen), emit ONE combined cross-type table per screen-visit, and keep emitting
                      standalone types (research/dilemma) one table at a time. Writes advisor.log +
                      advisor.jsonl. Self-tested with NO game access.
  watch(run_dir)   -- LIVE (decoupled, no bus): tail EVERY script_log concurrently (per-campaign
                      last-known state -- never a single max-by-size pick, so no campaign is ignored)
                      and ui_components.jsonl; buffer menu-opens that map to one screen within a short
                      window and ACCUMULATE/REFRESH the combined table as more of that screen's
                      sub-menus open. Passive -- never forces anything open.

    python runtime.py replay <recorded_run_dir>
    python runtime.py watch  [live_run_dir]   # no dir -> AUTO-FOLLOW the freshest run (restart-safe)
    python runtime.py latest [runs_root]      # offline: print which run auto-follow would pick now
"""
import glob
import json
import os
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("structurer",):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _p)))
sys.path.insert(0, _HERE)
import decision_instances as DI   # noqa: E402
import models as M                # noqa: E402
import screens as S               # noqa: E402
try:
    import dilemmas as DL         # noqa: E402
except Exception as e:
    DL = None
    sys.stderr.write("runtime: dilemmas import unavailable -> %s\n" % repr(e)[:80])

NAME_DB = os.path.join(_HERE, "reference", "reference.sqlite")
TOP = 3                 # default top-K surfaced per table
SCREEN_WINDOW = 12.0    # sec: live sub-menus opening within this of each other belong to one screen-visit
RUNS_HUMAN = r"D:/twdata/runs/human"   # where recorded human runs live (auto-follow root for watch)
SWITCH_CHECK = 3.0      # sec: how often auto-follow watch() re-checks for a newer (restart) run dir
# The ex-poll decision types synthesized LIVE (recorder no longer emits their menu_open panels): they
# have trained models + screen groups but would NEVER score live without _synth_poll. building/edict/
# stance are grouped (province/army screens); dilemma is standalone (its own table, like research).
SYNTH_TYPES = ("building", "edict", "stance", "dilemma")


def _newest_shot_mtime(run_dir):
    """Newest mtime among `shots/*.jpg` in a run (0.0 if none). scandir gives st_mtime for free on
    Windows, so this stays cheap even for a run with thousands of frames."""
    best = 0.0
    try:
        with os.scandir(os.path.join(run_dir, "shots")) as it:
            for e in it:
                if e.name.endswith(".jpg"):
                    try:
                        best = max(best, e.stat().st_mtime)
                    except OSError:
                        continue  # a frame vanished mid-scan (recorder churn) -> just skip it
    except OSError:
        return 0.0            # no shots dir yet -> caller falls back to ui_components mtime
    return best


def _run_freshness(run_dir):
    """A run's liveness = the freshest FILE mtime among its data streams: ui_components.jsonl and the
    newest shots/*.jpg. Deliberately IGNORES the directory mtime -- tooling (incl. this advisor writing
    advisor.log) touches a stale dir and would otherwise make it masquerade as the live run."""
    best = 0.0
    try:
        best = os.path.getmtime(os.path.join(run_dir, "ui_components.jsonl"))
    except OSError:
        pass  # no ui_components.jsonl yet -> rely on shots
    return max(best, _newest_shot_mtime(run_dir))


def latest_run(root=RUNS_HUMAN):
    """The run dir under `root` whose live data files are freshest by FILE mtime (see _run_freshness).
    Returns None if `root` has no run with any data yet. NOT directory mtime -- a stale run dir touched
    by tooling must never win over the one actually being written."""
    best_dir, best_ts = None, 0.0
    try:
        entries = [e.path for e in os.scandir(root) if e.is_dir()]
    except OSError as e:
        sys.stderr.write("runtime: latest_run scandir %s -> %s\n" % (root, repr(e)[:80]))
        return None
    for path in entries:
        ts = _run_freshness(path)
        if ts > best_ts:
            best_dir, best_ts = path, ts
    return best_dir


# Option labels are DATA-DRIVEN: the EXACT on-screen text (recorder-captured for an option, or -- for
# the post-battle captive buttons, which have none -- resolved from the game DB by the captor's
# culture/faction in _label_captive_options) is the top-priority label source for every type (see
# _onscreen). No generic hard-coded outcome map lives here -- captive outcomes ("Execute Captives/Force
# Labour/Ransom" for High Elves, "Entice/Devour/Offer as Tribute" for Slaanesh's Masque, not a neutral
# Kill/Enslave/Release) are faction/start-specific and come from the game's real text, never a guess.
_TR_RE = None


def _deref(con, text, depth=0):
    """Resolve `{{tr:KEY}}` loc indirections to the referenced text (bounded depth). Several loc name
    families store a `{{tr:...}}` pointer instead of the literal string (e.g. a skill's _hero variant
    points at its _lord name); left unresolved that pointer would leak as a raw key, which the readable-
    name rule forbids."""
    global _TR_RE
    if not text or depth > 4 or "{{tr:" not in text:
        return text
    if _TR_RE is None:
        import re as _re
        _TR_RE = _re.compile(r"\{\{tr:([\w.\-]+)\}\}", _re.I)  # loc keys can contain '-' (e.g. wound-maker)

    def sub(m):
        row = con.execute("SELECT text FROM loc WHERE key=?", (m.group(1),)).fetchone()
        if row and row[0] is not None:
            return _deref(con, row[0], depth + 1)
        # pointer key absent from loc (a loc GAP, e.g. some {{tr:agent_action_name_*}} skill variants):
        # DON'T leak the raw {{tr:...}} (forbidden). Degrade to a readable title-cased inner key. This
        # only fires when the lookup already FAILED, so it never changes a name that currently resolves.
        return m.group(1).rsplit(".", 1)[-1].replace("_", " ").strip().title()

    return _TR_RE.sub(sub, text)


# ---- readable-name resolvers for the P1 per-option types (name resolution is UI-only; model keys are
# untouched). Every scored row must render as human text -- no raw faction/subtype/clause/dance keys.
# A single cached read-connection (bounded; the loc table is read-only) backs the exact-key lookups.
_NAME_CON = None


def _con():
    global _NAME_CON
    if _NAME_CON is None:
        import sqlite3
        _NAME_CON = sqlite3.connect(NAME_DB)
    return _NAME_CON


def _loc1(loc_key):
    """One exact loc lookup (deref'd), '' if absent/blank. Never fatal."""
    try:
        row = _con().execute("SELECT text FROM loc WHERE key=?", (loc_key,)).fetchone()
        if row and row[0]:
            return _deref(_con(), row[0])
    except Exception as e:
        sys.stderr.write("runtime: _loc1(%s) -> %s\n" % (loc_key, repr(e)[:80]))
    return ""


def _faction_name(key):
    """A faction key -> its on-screen name (diplomatic_target), e.g. wh3_dlc26_kho_skulltaker->Blooded Wanderers."""
    return _loc1("factions_screen_name_%s" % key)


def _subtype_name(key):
    """A character subtype -> its agent on-screen name (recruit_lord/hero), e.g. wh2_main_hef_prince->Prince.
    The `override_` family wins (it carries the specific variant name); derefs the {{tr:land_units_...}} pointers."""
    return (_loc1("agent_subtypes_onscreen_name_override_%s" % key)
            or _loc1("agent_subtypes_onscreen_name_%s" % key))


# Diplomacy clause tokens (the diplomatic_option_<clause> universe) -> readable labels. Small fixed set;
# the loc quick-deal family is incomplete (misses trade_regions/peace/...) and carries [[img:...]] markup,
# so a clean static map + a title-case fallback is the honest, complete resolver.
_CLAUSE_LABEL = {
    "trade_agreement": "Trade Agreement", "trade_regions": "Trade Regions",
    "nonaggression_pact": "Non-Aggression Pact", "non_aggression_pact": "Non-Aggression Pact",
    "payment": "Payment", "declare_war": "Declare War", "peace": "Peace",
    "vassal": "Vassalise", "confederation": "Confederation",
    "soft_access": "Military Access", "military_access": "Military Access",
    "military_alliance": "Military Alliance", "defensive_alliance": "Defensive Alliance",
    "state_gift_regions": "Gift Regions",
}


def _deal_name(key):
    """A diplomacy clause (or '+'-joined clause set) -> readable label(s) (diplomatic_deal)."""
    parts = [p for p in str(key).split("+") if p]
    labs = [_CLAUSE_LABEL.get(p, p.replace("_", " ").title()) for p in parts]
    return " + ".join(labs) if labs else ""


def _dance_name(key):
    """A btn_dance_<theme> component -> 'Dance of <Theme>' (eternal_dance). Deterministic, readable."""
    theme = str(key).replace("btn_dance_", "").replace("_", " ").strip()
    return ("Dance of " + theme.title()) if theme else ""


def _name(key):
    """Human name for an option key (best-effort, offline loc). Never fatal.

    Covers building/unit/tech/skill/RITE string keys (LIKE/exact patterns) PLUS the two NUMERIC-id
    families whose on-screen name is keyed by that id in loc: settlement-occupation options (the card
    id -> culture_settlement_occupation_options_tooltip_<id>, name before '||') and post-battle captive
    options (the event's captive record key -> campaign_post_battle_captive_options_onscreen_name_
    <id>). This is what lets the advisor print 'Mentor'/'Invocation of Asuryan'/'Occupy' instead of a
    raw key or numeric id. Faction-neutral captive-outcome buttons are NO LONGER guessed here -- their
    label must be the captured on-screen text (see _onscreen/_disp_name)."""
    ks = str(key)
    # ITEMS (D9 live path): an ancillary key (*_anc_*) -> its localised display name via the SAME loc
    # family item_choices uses (ancillaries_onscreen_name_<key>, preloaded/cached at item_choices import).
    # Without this the LIVE watch (and offline items rows lacking a label) leak the raw key, e.g.
    # wh_main_anc_armour_spellshield instead of "Spellshield".
    if "_anc_" in ks:
        try:
            import item_choices as _ic
            nm = _ic.item_name(ks)
            if nm and nm != ks:
                return nm
        except Exception as e:
            sys.stderr.write("runtime: _name items(%s) -> %s\n" % (key, repr(e)[:80]))
    try:
        import sqlite3
        con = sqlite3.connect(NAME_DB)
        try:
            # RITES first (before the numeric-id families): a ritual key -> its display name
            rit = con.execute("SELECT text FROM loc WHERE key=?",
                              ("rituals_display_name_%s" % key,)).fetchone()
            if rit and rit[0]:
                return _deref(con, rit[0])
            # exact numeric-id families (occupation card id / captive record key)
            occ = con.execute("SELECT text FROM loc WHERE key=?",
                              ("culture_settlement_occupation_options_tooltip_%s" % key,)).fetchone()
            if occ and occ[0]:
                return _deref(con, occ[0]).split("||", 1)[0]
            cap = con.execute("SELECT text FROM loc WHERE key=?",
                              ("campaign_post_battle_captive_options_onscreen_name_%s" % key,)).fetchone()
            if cap and cap[0]:
                return _deref(con, cap[0])
            for pat in ("building_culture_variants_name_%s%%", "land_units_onscreen_name_%s",
                        "technologies_onscreen_name_%s", "character_skills_localised_name_%s"):
                r = con.execute("SELECT text FROM loc WHERE key LIKE ? LIMIT 1", (pat % key,)).fetchone()
                if r:
                    return _deref(con, r[0])
        finally:
            con.close()
    except Exception as e:
        sys.stderr.write("runtime: _name(%s) loc lookup -> %s\n" % (key, repr(e)[:80]))
    return ""


def _readable(key):
    """LAST-RESORT readable label for a key that no dedicated resolver / scrape-label covered: strip the
    common UI/loc prefixes and title-case the token (btn_dance_x, button_sally_forth -> 'Sally Forth').
    Deterministic and derived-only. It is reached ONLY when every real resolver/label already returned
    nothing, so it never overrides a proper name -- it just GUARANTEES no raw key/id/path/{{tr leaks to
    the reader (the hard readable-name invariant), including for standalone/uncovered types (pre_battle)."""
    import re as _re
    s = str(key or "").strip()
    m = _re.match(r"\{\{tr:([\w.\-]+)\}\}", s)          # unwrap a stray {{tr:...}} pointer
    if m:
        s = m.group(1).rsplit(".", 1)[-1]
    for pre in ("button_captive_option_", "button_", "btn_dance_", "btn_", "component_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    s = _re.sub(r"^wh\d*_(?:main|dlc\d+|pro\d+|twa\d+|sfo\d+|camp)_", "", s)  # drop WH loc-namespace noise
    s = s.replace("/", " ").replace("_", " ").strip()
    if not s:
        return "(unnamed)"
    return ("Option " + s) if s.isdigit() else s.title()


# The recorder writes the option's REAL on-screen label (the icon-button tooltip TITLE, e.g.
# button_attack -> "Fight Battle") into the SINGLE `onscreen` field, for icon-button panels
# (pre_battle / captives / equipment). That one field is the sole captured-text contract with the
# recorder -- there is no multi-field cascade. Absent (keyed options, or pre-recorder-change data) ->
# fall back to the key->loc resolver.
def _onscreen(row):
    """The EXACT on-screen label the recorder captured for an icon-button option -- the tooltip TITLE
    the game renders ('Fight Battle', 'Execute'/'Force Labor'/'Ransom') -- in the single `onscreen`
    field, or '' if absent. TOP-PRIORITY label source: it is the one deterministic truth for icon-button
    panels, so it always beats a key-derived guess. Never fabricates: returns only real captured text."""
    v = row.get("onscreen")
    if v is not None:
        v = str(v).strip()
        if v:
            return v
    return ""


def _disp_name(dtype, row):
    """Display label for a scored option row -- ONE deterministic source per decision TYPE (an explicit
    per-type map, NOT a blind try-every-field cascade):
      (1) icon-button panels  -> the recorder-captured `onscreen` label (verbatim game text);
      (2) occupation          -> the card id -> authoritative DB name (_name);
      (3) diplomatic_target   -> _faction_name(key);   diplomatic_deal -> _deal_name(key);
          recruit_lord/hero   -> _subtype_name(key);   eternal_dance   -> _dance_name(key);
      (4) captives            -> the recorder scrape label, else EMPTY (its faction-correct text is not
          derivable from the key -- never a wrong guess; see below);
      (5) other KEYED types   -> the option's own DB/enum label (edict/stance) else the key->loc resolver.
    Every branch ends in _readable(key), the documented last resort, so an unresolved option can never
    render a raw key/id. The per-type resolver IS the found source for its type -- there is deliberately
    NO scrape-label middle fallback on (2)/(3), because a raw `label`/`name` on those rows is just the
    same key again (it would only mask a resolver miss, never improve it)."""
    txt = _onscreen(row)
    if txt:
        return txt                        # (1) verbatim captured game text wins over every key-derived guess
    key = str(row.get("key"))
    if dtype == "occupation":
        # the card id joins to the authoritative DB name -- the ONE source (a positional scrape label is
        # explicitly distrusted here); _readable(key) is the documented last resort.
        return _name(str(row.get("id") or key)) or _readable(key)
    # P1 per-option types: the option KEY is a faction / char-subtype / clause / dance token, each with a
    # dedicated resolver = its ONE source (a raw label on these rows is the same key, so no label middle).
    if dtype == "diplomatic_target":
        return _faction_name(key) or _readable(key)
    if dtype == "diplomatic_deal":
        return _deal_name(key) or _readable(key)
    if dtype in ("recruit_lord", "recruit_hero"):
        return _subtype_name(key) or _readable(key)
    if dtype == "eternal_dance":
        return _dance_name(key) or _readable(key)
    if dtype == "captives":
        # captive outcomes are faction/start-specific (HE: Execute Captives/Force Labour/Ransom). The
        # buttons carry empty text/tooltip, so the real name is bound from the DB by the captor's
        # culture/faction (features_db.captive_options) and written into `onscreen` upstream
        # (_label_captive_options) -- which _onscreen() above already returned. Reaching here means that
        # binding was unavailable (e.g. unknown culture): fall back to a real scrape label, else EMPTY --
        # never a generic guess ('Kill').
        return row.get("label") or row.get("name") or ""
    return row.get("label") or row.get("name") or _name(key) or _readable(key)


def _label_captive_options(dec):
    """Name ALL THREE post-battle captive BUTTONS (kill / enslave / release) from the game DB,
    the ONE deterministic method -- NO hover, NO on-screen scrape. The buttons carry empty text
    and empty tooltip; their real, faction-reskinned name ('Execute Captives', 'Devour Captives',
    ...) is bound in the DB by the captor's culture/faction/subculture (features_db.captive_options
    -> record_key -> onscreen loc). Fills each option's verbatim `onscreen` field (the top-priority
    label _disp_name/_onscreen use), so the whole option set names from the DB, not just the chosen.
    In place on dec['options']; a no-op unless this is a captives decision with features_db present."""
    if dec.get("type") != "captives" or DI._FDB is None:
        return
    ctx = dec.get("context") or {}
    try:
        names = DI._FDB.captive_options(culture=ctx.get("culture"),
                                        faction=ctx.get("faction"),
                                        subculture=ctx.get("subculture"))
    except Exception as e:   # never let naming kill scoring/emit
        sys.stderr.write("runtime: captive_options -> %s\n" % repr(e)[:80])
        return
    for o in dec.get("options") or []:
        if o.get("onscreen"):
            continue                         # a real recorder-captured label already wins
        outcome = str(o.get("key") or "").replace("button_captive_option_", "")
        info = names.get(outcome)
        if info and info.get("name"):
            o["onscreen"] = info["name"]


def _available_first(rows):
    """Stable AVAILABLE-first reordering for a standalone recommendation table (JOB 2). Rows the game
    marks available (clickable/enabled -> available is True) LEAD, keeping their existing relative order
    (by combined); not-available (locked/greyed=False) and unknown-availability (None) rows follow, also
    order-preserved. So the recommendation LEADS with an actionable option (e.g. research must not lead
    with a locked tech) while the full available/unavailable MIX still shows below. Pure presentation/
    selection -- NO score change, NO model touch. If NOTHING is available (availability not captured for
    this panel), the original order is kept unchanged (nothing to promote)."""
    avail = [r for r in rows if r.get("available") is True]
    if not avail:
        return list(rows)
    rest = [r for r in rows if r.get("available") is not True]
    return avail + rest


def format_table(dec, tbl, top=TOP):
    """Per-type table: TOP-N ENABLED (available) recommendations. Falls back to all options only if
    none are marked available (e.g. availability not captured for that panel)."""
    avail = [r for r in tbl if r.get("available") is True]
    shown = (avail if avail else tbl)[:top]
    head = "turn %s  %s  (top %d of %d available / %d shown)  chosen=%s" % (
        dec.get("turn"), str(dec["type"]).upper(), len(shown),
        len(avail) if avail else len(tbl), len(tbl), dec.get("chosen"))
    lines = [head, "   combined  exploit  explore  option"]
    dtype = str(dec.get("type"))
    for r in shown:
        key = str(r["key"])
        mark = "  <== chosen" if key == str(dec.get("chosen")) else ""
        if dtype == "captives" and mark and dec.get("chosen_name"):
            disp = dec["chosen_name"]        # faction-correct captive name from the event record key
        else:
            disp = _disp_name(dtype, r)
        lines.append("   %+8.3f %+8.3f %8.3f  %s%s" % (
            r["combined"], r["exploit"], r["explore"], str(disp)[:44], mark))
    return "\n".join(lines)


def format_combined(screen, turn, campaign, combined, top=TOP):
    """The SCREEN-GROUPED table: one merged ranking of every option across the screen's types, sorted
    by the cross-type key = IMPACT (f(s,a) - g(s), CHANGE B). Header + per-row [TYPE] tag + scores."""
    rows = combined["rows"]
    shown = combined["top"] if combined.get("top") else rows[:top]
    types = combined.get("types") or sorted({r.get("type") for r in rows})
    head = "%s turn %s (campaign %s) — top %d of %d across {%s}" % (
        str(screen).upper(), turn, campaign, len(shown), len(rows),
        ", ".join(str(t) for t in types))
    lines = [head, "    impact  combined  exploit  explore  option"]
    for r in shown:
        raw = r.get("raw_exploit")
        raws = ("%+8.4f" % raw) if raw is not None else "    --   "
        mark = "  <== chosen" if r.get("chosen_here") else ""
        na = "" if r.get("available") is not False else " (n/a)"
        lines.append("   %s %+8.3f %+8.3f %8.3f  [%-9s] %s%s%s" % (
            raws, r["combined"], r["exploit"], r["explore"], str(r.get("type")),
            str(_disp_name(r.get("type"), r))[:32], na, mark))
    return "\n".join(lines)


def _jrec(campaign, turn, screen, types, rows, ts, entity=None):
    """One advisor.jsonl record for a (combined or standalone) screen decision -- downstream browser UI.
    `entity` (ADDITIVE, may be None): the region/mf_cqi/char_cqi this decision belongs to, so
    per-entity consumers can attribute the table; absent for legacy/unknown-entity records."""
    return {"ts": ts, "campaign": campaign, "turn": turn, "screen": screen,
            "entity": entity,
            "types": list(types),
            "options": [{"type": r.get("type"), "key": r.get("key"),
                         "name": _disp_name(r.get("type"), r),
                         "raw_exploit": r.get("raw_exploit"), "exploit": r.get("exploit"),
                         "explore": r.get("explore"), "combined": r.get("combined"),
                         "available": r.get("available")} for r in rows]}


def _tag(tbl, dtype):
    """Tag each per-type scored row with its source type (so standalone rows share combined's shape)."""
    return [dict(r, type=dtype) for r in tbl]


def _grouped_options(d):
    """Options to score for a grouped decision. Real captured option set when present; otherwise the
    single action actually taken (chosen) as a one-item set, so a decision whose full menu was never
    scraped offline (edict/stance/rites synthesized from state) still competes on its screen instead
    of vanishing. The key is the REAL recorded chosen key; source='chosen' marks it as taken-only."""
    opts = d.get("options") or []
    if opts:
        return opts
    ch = d.get("chosen")
    if ch is not None:
        return [{"key": ch, "id": None, "state": None, "available": True,
                 "source": "chosen", "label": d.get("chosen_name")}]
    return []


# ------------------------------------------------------------------ OFFLINE replay ----------------
def replay(run_dir, out=None, jsonl_out=None):
    """Offline: score a recorded run's decisions; emit one combined table per (campaign, turn, screen)
    for grouped types + standalone tables for the rest. Writes advisor.log + advisor.jsonl."""
    models, explorers, meta, state_models = M.load_all()
    decs = DI.build_decisions(run_dir)
    if DL:
        try:
            decs += DL.build_dilemma_decisions(run_dir)
        except Exception as e:
            sys.stderr.write("runtime: replay dilemmas skipped -> %s\n" % repr(e)[:80])
    out = out or os.path.join(run_dir, "advisor.log")
    jsonl_out = jsonl_out or os.path.join(run_dir, "advisor.jsonl")

    # partition: grouped types -> (campaign, turn, screen) buckets; standalone -> as-is
    buckets = defaultdict(list)
    standalone = []
    for d in decs:
        screen = S.screen_of(d.get("type"))
        if screen is not None:
            buckets[(d.get("campaign"), d.get("turn"), screen)].append(d)
        else:
            standalone.append(d)

    items = []   # (sortkey, text_block, jsonl_record) -> written in chronological order
    for (campaign, turn, screen), ds in buckets.items():
        scored, rep_t = [], None
        for d in ds:
            opts = _grouped_options(d)
            if not opts:
                continue
            dd = dict(d); dd["options"] = opts
            _label_captive_options(dd)       # name the 3 captive buttons from the DB (no scrape)
            try:
                tbl = M.score_decision(dd, models, explorers, meta, state_models=state_models)
            except Exception as e:
                sys.stderr.write("runtime: score %s t%s -> %s\n" % (d.get("type"), turn, repr(e)[:80]))
                continue
            scored.append({"type": d.get("type"), "options": tbl, "chosen": d.get("chosen")})
            dt = d.get("t")
            rep_t = dt if rep_t is None else (min(rep_t, dt) if dt is not None else rep_t)
        if not scored:
            continue
        combined = S.combine_screen(scored, top=TOP)
        block = format_combined(screen, turn, campaign, combined)
        jrec = _jrec(campaign, turn, screen, combined["types"], combined["rows"], round(rep_t or 0.0, 3))
        items.append(((str(campaign), turn or 0, rep_t or 0.0), block, jrec))

    for d in standalone:
        if not d.get("options"):
            continue
        try:
            tbl = M.score_decision(d, models, explorers, meta, state_models=state_models)
        except Exception as e:
            sys.stderr.write("runtime: score %s t%s -> %s\n" % (d.get("type"), d.get("turn"), repr(e)[:80]))
            continue
        tbl = _available_first(tbl)   # JOB 2: recommendation LEADS with an available option (mix below)
        block = format_table(d, tbl)
        jrec = _jrec(d.get("campaign"), d.get("turn"), d.get("type"), [d.get("type")],
                     _tag(tbl, d.get("type")), round(d.get("t") or 0.0, 3))
        items.append(((str(d.get("campaign")), d.get("turn") or 0, d.get("t") or 0.0), block, jrec))

    items.sort(key=lambda it: it[0])
    with open(out, "w", encoding="utf-8") as f, open(jsonl_out, "w", encoding="utf-8") as jf:
        for _k, block, jrec in items:
            f.write(block + "\n\n")
            jf.write(json.dumps(jrec) + "\n")
    n_comb = sum(1 for it in items if it[2]["screen"] in S.SCREEN_GROUPS)
    print("replay: wrote %d tables (%d combined-screen, %d standalone) -> %s  (+ %s)"
          % (len(items), n_comb, len(items) - n_comb, out, jsonl_out))
    return out, len(items)


# ------------------------------------------------------------------ LIVE watch (decoupled tail) ----
def _tail_lines(path, state):
    """Yield new whole lines appended to `path` since last call (state = {pos, buf})."""
    try:
        sz = os.path.getsize(path)
    except OSError as e:
        sys.stderr.write("runtime: _tail_lines stat %s -> %s\n" % (os.path.basename(path), repr(e)[:60]))
        return
    if sz < state.get("pos", 0):
        state["pos"] = 0
    with open(path, "rb") as f:
        f.seek(state.get("pos", 0))
        chunk = f.read()
        state["pos"] = f.tell()
    buf = state.get("buf", "") + chunk.decode("utf-8", "replace")
    *whole, buf = buf.split("\n")
    state["buf"] = buf
    for ln in whole:
        yield ln


def _emit(block, logpath):
    """Print a table + append it to advisor.log. Console-encoding-safe (em dash etc. on Windows)."""
    try:
        print(block)
    except UnicodeEncodeError:
        print(block.encode("ascii", "replace").decode())
    sys.stdout.flush()
    try:
        with open(logpath, "a", encoding="utf-8") as lf:
            lf.write(block + "\n")
    except OSError as e:
        sys.stderr.write("runtime: advisor.log write -> %s\n" % repr(e)[:80])


def _append_jsonl(path, rec):
    try:
        with open(path, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(rec) + "\n")
    except OSError as e:
        sys.stderr.write("runtime: advisor.jsonl write -> %s\n" % repr(e)[:80])


def _opts_from_menu(mo):
    return [{"key": o.get("key"), "id": o.get("id"), "label": o.get("label"),
             "name": o.get("name"), "state": o.get("state"),
             "available": (True if o.get("clickable") is True else
                           False if o.get("clickable") is False else None),
             "source": o.get("source"),
             # the recorder's single captured on-screen label (icon buttons) -> _disp_name prefers it
             # verbatim (JOB 1). None until the recorder change lands, then the real tooltip title.
             "onscreen": o.get("onscreen")} for o in (mo.get("options") or [])]


def _new_run_state(run_dir):
    """Fresh per-run + per-campaign tail/buffer state for one run dir. Making this a plain dict (not
    closure cells) is what lets auto-follow drop the whole thing and re-seed on a restart-new-run."""
    return {
        "run_dir": run_dir,
        "ui": os.path.join(run_dir, "ui_components.jsonl"),
        "logpath": os.path.join(run_dir, "advisor.log"),
        "jsonlpath": os.path.join(run_dir, "advisor.jsonl"),
        "ui_st": {},          # ui_components.jsonl tail-state {pos, buf}
        # PER-CAMPAIGN tracking (multi-campaign invariant): tail ALL script_log_*.tail concurrently;
        # each log's is_human rows update its campaign's last-known state. No single max-by-size pick
        # -> a restart into another faction WITHIN the same run dir is tracked too, never ignored.
        "log_states": {},     # path -> tail-state {pos, buf}
        "campaigns": {},      # faction -> {"turn", "ctx", "seq"}
        "active": None,       # faction of the MOST-recently-advanced campaign (menu-opens attribute here)
        "gseq": 0,
        "buffers": {},        # (campaign, screen) -> {"types": {type: {...}}, "ts", "turn"}
        "last_sig": {},       # (campaign, panel) -> last (options,avail,turn) signature (re-dump de-dup)
        # LIVE synthesis of the ex-poll types (building/edict/stance/dilemma) -- see _synth_poll.
        "synth_last_turn": {},  # faction -> last turn we ran live synthesis for (per-turn cadence gate)
        "synth_emitted": set(),  # (campaign, turn, type, chosen) already synthesized+emitted (de-dup)
    }


def _synth_poll(st, models, explorers, meta, state_models=None):
    """LIVE synthesis of the ex-poll decision types (building/edict/stance/dilemma). The recorder no
    longer emits their menu_open panels (POLL_PANELS=()), so watch's menu_open loop never scores them
    and the browser UI would never show a building/edict/stance/dilemma card live.

    Fix (D6): once per NEW turn on the ACTIVE campaign, run the SAME offline synthesis the replay path
    already uses -- decision_instances.build_decisions (edict from region state-diff, stance from
    ForceAdoptsStance + state-diff + click, building from the click-path) and dilemmas.build_dilemma_
    decisions -- over the run's tail; keep only the active campaign's SYNTH_TYPES decisions, and feed
    each NEW one through the same score_decision + screen-group (combine_screen/format_combined) +
    advisor.log/advisor.jsonl emit path as menu_opens. De-dup by (campaign, turn, type, chosen) so a
    decision is never re-emitted on a later poll (build_decisions re-scans the whole run each call).

    Cadence: gated to the active campaign's turn changing, because build_decisions re-scans the run.
    Robust: any synthesis error is logged in one line and swallowed -- it must never kill watch()."""
    fac = st.get("active")
    if fac is None:
        return
    turn = (st["campaigns"].get(fac) or {}).get("turn")
    if turn is None:
        return
    if st["synth_last_turn"].get(fac) == turn:
        return                       # already synthesized this campaign at this turn (low cadence)
    st["synth_last_turn"][fac] = turn
    try:
        # the campaign id exactly as build_decisions / build_dilemma_decisions mint it, to filter to
        # the ACTIVE campaign (a run dir can hold several); re-labelled to the faction key on emit so
        # advisor.jsonl's `campaign` matches the menu_open path's.
        rid = os.path.basename(st["run_dir"].rstrip("/\\"))[-6:]
        want_cid = "%s.%s" % (rid, fac.split("_", 2)[-1][:12])
        decs = DI.build_decisions(st["run_dir"])
        if DL is not None:
            try:
                decs += DL.build_dilemma_decisions(st["run_dir"])
            except Exception as e:
                sys.stderr.write("runtime: synth dilemmas skipped -> %s\n" % repr(e)[:80])
        emitted = st["synth_emitted"]
        buckets = defaultdict(list)          # (turn, screen) -> [scored grouped decision dicts]
        standalone = []                      # [(decision, scored table)] for standalone types (dilemma)
        for d in decs:
            dtype = d.get("type")
            if dtype not in SYNTH_TYPES or d.get("campaign") != want_cid:
                continue
            dkey = (fac, d.get("turn"), dtype, str(d.get("chosen")))
            if dkey in emitted:
                continue
            opts = _grouped_options(d)       # real option set, else the chosen-only one-item set
            if not opts:
                continue
            dd = dict(d); dd["options"] = opts; dd["campaign"] = fac
            try:
                tbl = M.score_decision(dd, models, explorers, meta, state_models=state_models)
            except Exception as e:
                sys.stderr.write("runtime: synth score %s -> %s\n" % (dtype, repr(e)[:80]))
                continue
            emitted.add(dkey)
            screen = S.screen_of(dtype)
            ent = (d.get("chosen_region") or d.get("_mf_cqi") or d.get("_char_cqi"))
            if screen is None:               # standalone (dilemma) -> its own table, like research
                standalone.append((dd, tbl))
            else:                            # grouped -> combined screen table (replay-style batch);
                buckets[(d.get("turn"), screen, ent)].append(   # bucketed PER ENTITY (D-phase)
                    {"type": dtype, "options": tbl, "chosen": d.get("chosen")})
        for (bturn, screen, ent), scored in buckets.items():
            combined = S.combine_screen(scored, top=TOP)
            _emit("\n" + format_combined(screen, bturn, fac, combined), st["logpath"])
            _append_jsonl(st["jsonlpath"], _jrec(fac, bturn, screen, combined["types"],
                                                 combined["rows"], time.time(), entity=ent))
        for dd, tbl in standalone:
            tbl = _available_first(tbl)   # JOB 2: lead with an available option (mix still below)
            _emit("\n" + format_table(dd, tbl), st["logpath"])
            _append_jsonl(st["jsonlpath"], _jrec(fac, dd.get("turn"), dd.get("type"),
                                                 [dd.get("type")], _tag(tbl, dd.get("type")), time.time()))
    except Exception as e:
        # a synthesis failure must never kill the watch loop -- one line, continue.
        sys.stderr.write("runtime: synth poll (%s t%s) -> %s\n" % (fac, turn, repr(e)[:120]))


def _poll_once(st, models, explorers, meta, state_models=None):
    """One tail-and-score pass over the active run's logs + ui_components. Mutates `st` in place."""
    run_dir, logpath, jsonlpath = st["run_dir"], st["logpath"], st["jsonlpath"]
    # 1) advance EVERY campaign log -> refresh per-campaign last-known state
    for lp in sorted(glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail"))):
        ls = st["log_states"].setdefault(lp, {})
        for ln in _tail_lines(lp, ls):
            if "TWSTATE " not in ln:
                continue
            try:
                r = json.loads(ln.split("TWSTATE ", 1)[1])
            except Exception:
                continue  # intentional: skip malformed TWSTATE line, hot live-tail loop (no per-line log)
            if r.get("kind") == "faction" and r.get("is_human"):
                fac = r.get("faction")
                prev = st["campaigns"].get(fac)
                # seq bumps only on genuine ADVANCEMENT (new campaign or turn change), NEVER on a re-dump
                # of an already-seen (faction, turn) row. ctx is refreshed every row (latest state); seq
                # is not, so a late buffered flush that merely re-dumps an OLD session's already-seen
                # state cannot overtake the campaign that most-recently advanced.
                if prev is None or prev.get("turn") != r.get("turn"):
                    st["gseq"] += 1
                    seq = st["gseq"]
                else:
                    seq = prev.get("seq", st["gseq"])
                st["campaigns"][fac] = {"turn": r.get("turn"), "ctx": dict(r), "seq": seq}
    # active (menu-opens attribute here) = the MOST-RECENTLY-ADVANCED campaign (max seq) -- NOT the last
    # is_human row in glob-sort order, which a late buffered flush of an older session could mis-set (D11).
    if st["campaigns"]:
        st["active"] = max(st["campaigns"], key=lambda f: st["campaigns"][f]["seq"])
    # 1b) LIVE-synthesize the ex-poll types (building/edict/stance/dilemma) for the active campaign; these
    # have no menu_open (recorder poll dropped) so this is their only live scoring path. Reads logs, not
    # ui_components, so run it before the (possibly-missing) ui_components tail below.
    _synth_poll(st, models, explorers, meta, state_models)
    # 2) score each NEW menu-open against the active campaign; group into screen buffers
    ui = st["ui"]
    if not os.path.isfile(ui):
        return
    for ln in _tail_lines(ui, st["ui_st"]):
        if '"menu_open"' not in ln:
            continue
        try:
            mo = json.loads(ln)
        except Exception:
            continue  # intentional: skip malformed menu_open line, hot live-tail loop (no per-line log)
        if mo.get("kind") != "menu_open" or mo.get("panel") not in DI.PANEL_TYPE:
            continue
        opts = _opts_from_menu(mo)
        if not opts:
            continue
        dtype = DI.PANEL_TYPE[mo["panel"]]
        fac = st["active"]
        cs = st["campaigns"].get(fac) or {}
        turn, ctx = cs.get("turn"), (cs.get("ctx") or {})
        sig = (mo["panel"], tuple(str(o.get("key")) for o in opts),
               tuple(o.get("available") for o in opts), turn)
        if sig == st["last_sig"].get((fac, mo["panel"])):
            continue          # unchanged re-dump of a still-open panel -> not a new decision
        st["last_sig"][(fac, mo["panel"])] = sig
        dec = {"type": dtype, "turn": turn, "campaign": fac, "context": dict(ctx),
               "options": opts, "chosen": None}
        _label_captive_options(dec)          # name the 3 captive buttons from the DB (no scrape)
        try:
            tbl = M.score_decision(dec, models, explorers, meta, state_models=state_models)
        except Exception as e:
            sys.stderr.write("runtime: watch score %s -> %s\n" % (dtype, repr(e)[:80]))
            continue
        screen = S.screen_of(dtype)
        # the decision's entity where derivable (synth path carries region/mf/char ids) -- used to
        # tag records AND key buffers so two entities' same-screen tables never clobber each other
        ent = (dec.get("entity") or dec.get("chosen_region") or dec.get("_mf_cqi")
               or dec.get("_char_cqi"))
        if screen is None:
            # standalone type (research/dilemma) -> its own table, as before
            tbl = _available_first(tbl)   # JOB 2: lead with an available option (mix still below)
            _emit("\n" + format_table(dec, tbl), logpath)
            _append_jsonl(jsonlpath, _jrec(fac, turn, dtype, [dtype], _tag(tbl, dtype),
                                           time.time(), entity=ent))
            continue
        # grouped: accumulate into the screen's buffer (fresh if window elapsed or turn changed);
        # buffers are PER-ENTITY (D-phase fix: a single slot per screen made a second settlement's
        # building table overwrite the first within one window)
        now = time.time()
        bkey = (fac, screen, ent)
        buf = st["buffers"].get(bkey)
        if buf is None or (now - buf["ts"]) > SCREEN_WINDOW or buf["turn"] != turn:
            buf = {"types": {}, "ts": now, "turn": turn}
            st["buffers"][bkey] = buf
        buf["types"][dtype] = {"type": dtype, "options": tbl, "chosen": None}
        buf["ts"] = now
        combined = S.combine_screen(list(buf["types"].values()), top=TOP)
        _emit("\n" + format_combined(screen, turn, fac, combined), logpath)
        _append_jsonl(jsonlpath, _jrec(fac, turn, screen, combined["types"],
                                       combined["rows"], time.time(), entity=ent))


def watch(run_dir=None, poll=0.4, root=RUNS_HUMAN, switch_check=SWITCH_CHECK):
    """Live: tail EVERY script_log (per-campaign last-known state) + ui_components.jsonl; on each new
    menu_open score against its campaign's state and, for grouped types, accumulate/refresh a combined
    screen table within a short window. Decoupled from the recorder (reads the files it writes).

    AUTO-FOLLOW: with run_dir None or "auto", DISCOVER the freshest run under `root` (latest_run, by
    FILE mtime) and, while watching, re-check every `switch_check`s -- a campaign restart mints a NEW
    run dir, so when a fresher one appears we drop this run's state and re-seed on the new one (logging
    a one-line switch). An EXPLICIT run_dir pins to that run: no discovery, no switching."""
    models, explorers, meta, state_models = M.load_all()
    pinned = run_dir not in (None, "auto")
    if pinned:
        active_dir = run_dir
    else:
        active_dir = latest_run(root)
        while active_dir is None:
            sys.stderr.write("runtime: no run with data under %s yet; waiting...\n" % root)
            time.sleep(switch_check)
            active_dir = latest_run(root)
    st = _new_run_state(active_dir)
    print("advisor watching %s%s\n  -> %s  (+ %s)   (Ctrl-C to stop)" % (
        active_dir, "" if pinned else "  [auto-follow]", st["logpath"], st["jsonlpath"]))
    last_check = time.time()
    while True:
        _poll_once(st, models, explorers, meta, state_models)
        # AUTO-FOLLOW: periodically re-check for a newer (restart) run dir and switch onto it
        if not pinned and (time.time() - last_check) >= switch_check:
            last_check = time.time()
            newest = latest_run(root)
            if newest and os.path.abspath(newest) != os.path.abspath(st["run_dir"]):
                st = _new_run_state(newest)
                _emit("\n[advisor] switched to run %s" % os.path.basename(os.path.normpath(newest)),
                      st["logpath"])
        time.sleep(poll)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "replay":
        replay(sys.argv[2])
    elif cmd == "watch":
        # `watch` (no dir) -> AUTO-FOLLOW the freshest run; `watch <dir>` -> pin to that run.
        watch(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "latest":
        # offline probe: which run does auto-follow pick right now?
        root = sys.argv[2] if len(sys.argv) > 2 else RUNS_HUMAN
        pick = latest_run(root)
        if pick is None:
            print("latest_run(%s) -> (no run with data)" % root)
        else:
            print("latest_run(%s) -> %s  (freshness mtime=%.3f)"
                  % (root, pick, _run_freshness(pick)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
