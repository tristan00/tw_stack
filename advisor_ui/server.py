#!/usr/bin/env python3
"""
WH3 Decision-Advisor localhost dashboard (P4).

READ-ONLY over existing run files. It does NOT touch the game, the bus, the
recorder, or any run data (only reads). Zero third-party dependencies: uses the
Python standard library `http.server` (FastAPI/uvicorn are not installed in the
project venv, so we deliberately avoid any pip install).

Run:
    D:\\totalwar_runner\\.venv\\Scripts\\python.exe D:\\tw_advisor_ui\\server.py

Then open http://127.0.0.1:8770/  (bound to localhost only).

Endpoints:
    GET /                -> the dashboard HTML page
    GET /api/state       -> fresh JSON snapshot of the current run
    GET /api/state?run=<run_id>  -> snapshot of a specific run
"""

import json
import os
import sqlite3
import sys
import glob
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8770
RUNS_ROOT = r"D:\twdata\runs\human"

# Freshness threshold (seconds): a stream older than this is considered stale/red.
FRESH_SEC = 90.0
# How much of each (potentially huge) script_log tail to scan for the latest
# faction row. Faction rows are emitted frequently, so a few MB is ample.
SCRIPTLOG_TAIL_BYTES = 8 * 1024 * 1024
# Cap on how many recent menu_open records we surface.
MENU_LIMIT = 12
# Cap on how many runs we list in the selector.
RUN_LIST_LIMIT = 25
# Max |menu.t - advisor.ts| (seconds) allowed when joining a menu to a scored
# table. Beyond this the nearest same-campaign, same-type advisor row is treated
# as stale/absent and we render "no scored table" rather than a misleading one.
# (Legit joins are typically <40s; cross-decision/cross-turn ones are minutes.)
MAX_MATCH_DELTA_SEC = 120.0

# HARDCODED: options that leave the campaign UI for the battle UI (pre_battle "Fight Battle" /
# "Spectate") are ALWAYS unavailable -- the battle UI is out of scope and not automatable, so the
# advisor must never rank them as a selectable pick.
FORBIDDEN_BATTLE_KEYS = frozenset({"button_attack", "button_spectate"})

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")

# --- v0 /api/advise: reuse the advisor's models + DB to score a menu ON REQUEST (the "launcher/UI tells
# the advisor it is at menu X" button path). Import lazily + tolerate absence (dashboard still serves).
sys.path.insert(0, r"D:\tw_stack\advisor")
try:
    import models as _ADV_M                                  # noqa: E402
    from reference import features_db as _ADV_FDB           # noqa: E402
except Exception as _e:                                      # pragma: no cover
    _ADV_M = None
    _ADV_FDB = None
    sys.stderr.write("[advisor-ui] advisor scoring import failed (advise disabled): %s\n" % _e)
_ADV_BUNDLE = None

# advisor decision TYPE -> recorder PANEL(s) whose freshest menu_open carries that type's options+coords.
ADVISE_TYPE_PANELS = {
    "research": ["technology"], "recruit": ["recruitment"], "skills": ["skills"],
    # items = the EQUIP decision -> the POOL panel only. equipment_equipped is the REMOVE/SWAP set
    # whose cards are CcoAncillariesCategoryRecord SLOT records, not equippable options -- joining it
    # here made the driver click a category header (live E2E 20260729_154706).
    "items": ["equipment"], "occupation": ["occupation"],
    "captives": ["post_battle_captives", "captives"], "pre_battle": ["pre_battle"],
    "recruit_lord": ["recruit_panel"],
    # building has NO captured menu_open -> DB-synthesized option-set (see advise_menu).
}


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def _log(msg):
    """Brief one-line diagnostic to stderr (never silent, never a crash)."""
    sys.stderr.write("[advisor-ui] %s\n" % msg)
    sys.stderr.flush()


def _mtime(path):
    """File/dir mtime or None if it does not exist."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _age(mtime, now):
    """Seconds since mtime, rounded; None if mtime is None."""
    if mtime is None:
        return None
    return round(now - mtime, 1)


def tail_text(path, nbytes):
    """Read at most the last `nbytes` bytes of a file as text (utf-8/replace)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - nbytes))
        data = f.read()
    return data.decode("utf-8", "replace")


def _campaign_key(value):
    """
    Canonical, FORMAT-AGNOSTIC campaign key for equality across the two ways the
    `campaign` field is written:
      * LIVE watch     -> the faction KEY, e.g. "wh2_main_hef_nagarythe"
      * OFFLINE replay -> "<runid6>.<factionshort12>", e.g. "181345.hef_nagaryth"
    (the short is faction.split("_",2)[-1][:12] -- see advisor decision_instances.py).
    Both collapse to the same 12-char faction suffix so a menu/row still only
    matches its OWN campaign (D3 intact), just comparably across formats:
      "wh2_main_hef_nagarythe" -> "hef_nagaryth"
      "181345.hef_nagaryth"    -> "hef_nagaryth"
    Callers only ever pass a run-tagged replay id (has ".") or a faction key
    (has "_", no "."), never a bare short.
    """
    if not value:
        return None
    s = str(value)
    if "." in s:
        s = s.rsplit(".", 1)[-1]     # replay "<runid>.<short>" -> "<short>"
    else:
        s = s.split("_", 2)[-1]      # faction key -> faction suffix
    return s[:12]


# ----------------------------------------------------------------------------
# Run discovery
# ----------------------------------------------------------------------------
def _run_freshness(run_path):
    """
    Wall-clock freshness of a run = max mtime over its actively-written files
    (ui_components.jsonl, events.jsonl, shots dir). Deliberately NOT the run dir
    mtime, which tooling can bump on an old run.
    """
    candidates = [
        _mtime(os.path.join(run_path, "ui_components.jsonl")),
        _mtime(os.path.join(run_path, "events.jsonl")),
        _mtime(os.path.join(run_path, "shots")),
    ]
    vals = [c for c in candidates if c is not None]
    return max(vals) if vals else None


def list_runs():
    """
    All runs under RUNS_ROOT sorted by freshness desc: [(run_id, freshness)].

    Two layouts are recognized (#10 / R3):
      * FLAT       RUNS_ROOT/<run>/           (run holds the data files directly)
      * PER-CAMPAIGN  RUNS_ROOT/<run>/<campaign>/   (run is a parent; each child
                    holds one campaign's data + a meta.json with campaign_index)
    A per-campaign child is surfaced with a composite id "<run>/<campaign>" so it
    can be selected and resolved like any other run. Freshness is the data-file
    mtime in both cases (identical rule), so the freshest child/flat run wins.
    """
    out = []
    try:
        entries = os.listdir(RUNS_ROOT)
    except OSError as e:
        _log("cannot list runs root %s: %s" % (RUNS_ROOT, e))
        return out
    for name in entries:
        p = os.path.join(RUNS_ROOT, name)
        if not os.path.isdir(p):
            continue
        fresh = _run_freshness(p)
        if fresh is not None:
            out.append((name, fresh))
            continue
        # No data files at the top level: this may be an R3 parent whose
        # campaigns live in child subdirs. Surface each data-bearing child.
        try:
            children = os.listdir(p)
        except OSError:
            children = []
        for child in children:
            cp = os.path.join(p, child)
            if not os.path.isdir(cp):
                continue
            cfresh = _run_freshness(cp)
            if cfresh is not None:
                out.append((name + "/" + child, cfresh))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def resolve_run(requested):
    """Return (run_id, run_path) for `requested` or the freshest run."""
    if requested:
        p = os.path.join(RUNS_ROOT, requested)
        if os.path.isdir(p):
            return requested, p
        # Fall through to freshest if the requested run is bogus.
    runs = list_runs()
    if not runs:
        return None, None
    run_id = runs[0][0]
    return run_id, os.path.join(RUNS_ROOT, run_id)


# ----------------------------------------------------------------------------
# Readers
# ----------------------------------------------------------------------------
def read_meta(run_path, notes):
    try:
        with open(os.path.join(run_path, "meta.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        notes.append("meta.json unreadable: %s" % e)
        return {}


def scan_faction_sessions(run_path, notes):
    """
    Scan every script_log_*.txt.tail for its latest human faction TWSTATE row.

    A run dir can hold MULTIPLE sessions (the user restarted into a different
    campaign), each written to its own script_log_*.txt.tail. Returns one entry
    per session that carries a human faction row, sorted by file mtime ASC:

        {"mtime": float, "faction": str, "row": dict, "basename": str}

    The newest by mtime is the CURRENT session/campaign. The ordered mtimes also
    delimit each session's wall-clock span, which lets us attribute every
    menu_open (timestamped as seconds-since-t0) to its owning campaign.
    """
    pattern = os.path.join(run_path, "logs", "script_log_*.txt.tail")
    files = glob.glob(pattern)
    if not files:
        notes.append("no script_log_*.txt.tail found (state stream absent)")
        return []
    sessions = []
    for lp in files:
        mt = _mtime(lp)
        if mt is None:
            continue
        try:
            text = tail_text(lp, SCRIPTLOG_TAIL_BYTES)
        except Exception as e:
            notes.append("tail failed for %s: %s" % (os.path.basename(lp), e))
            continue
        row = None
        for line in reversed(text.splitlines()):
            if "TWSTATE" not in line or '"kind":"faction"' not in line:
                continue
            marker = line.find("TWSTATE ")
            if marker < 0:
                continue
            frag = line[marker + len("TWSTATE "):]
            brace = frag.find("{")
            if brace < 0:
                continue
            frag = frag[brace:].strip()
            try:
                obj = json.loads(frag)
            except json.JSONDecodeError:
                continue  # partial/truncated tail line; skip quietly
            if obj.get("kind") == "faction" and obj.get("is_human") is True:
                row = obj
                break  # newest human faction row in this session
        if row is not None:
            sessions.append({
                "mtime": mt,
                "faction": row.get("faction"),
                "row": row,
                "basename": os.path.basename(lp),
            })
    sessions.sort(key=lambda s: s["mtime"])
    if not sessions:
        notes.append("no human faction row in any script_log tail")
    return sessions


def latest_faction_row(sessions):
    """Newest-by-mtime session's faction row -> (row_dict, source_basename)."""
    if not sessions:
        return None, None
    latest = sessions[-1]
    return latest["row"], latest["basename"]


# ---------------------------------------------------------------------------
# Per-entity TWSTATE rows + actions.sqlite (the cco actionable-state surface)
# ---------------------------------------------------------------------------
# Context trims MIRROR decision_instances (_REGION_KEEP/_FORCE_KEEP/_CHAR_KEEP) so the
# per-entity context blocks fed to score_decision match the training-time shapes exactly.
_REGION_KEEP = ("region", "owner", "owner_is_human", "public_order", "growth_per_turn", "gdp",
                "num_buildings", "province", "is_capital", "is_abandoned", "climate",
                "garrison_str", "active_edict", "selected_edict")
_FORCE_KEEP = ("mf_cqi", "stance", "region", "region_garrison", "is_army", "is_navy", "is_horde",
               "units", "strength", "morale", "upkeep", "mp_pct")
_CHAR_KEEP = ("char_cqi", "subtype", "type", "rank", "wounded", "loyalty", "is_governor",
              "faction_leader", "region", "in_settlement")


def _trim(row, keep):
    return {k: row.get(k) for k in keep} if row else None


def latest_entity_rows(run_path, notes):
    """Newest TWSTATE row per entity from the CURRENT session's tail.

    Returns {"region": {name: row}, "force": {mf_cqi: row}, "units": {mf_cqi: [unit rows]},
    "char": {char_cqi: row}} -- the live per-entity state the training-time _enrich_context
    joined offline. Forward scan keeps the newest row per key; unit lists rebuild whenever a
    fresh force dump for that mf appears (a dump group re-lists the force's units)."""
    out = {"region": {}, "force": {}, "units": {}, "char": {}}
    sessions = scan_faction_sessions(run_path, notes)
    if not sessions:
        return out
    lp = os.path.join(run_path, "logs", sessions[-1]["basename"])
    try:
        text = tail_text(lp, SCRIPTLOG_TAIL_BYTES)
    except Exception as e:
        notes.append("entity tail failed: %s" % e)
        return out
    unit_seen_turn = {}
    for line in text.splitlines():
        m = line.find("TWSTATE ")
        if m < 0:
            continue
        frag = line[m + len("TWSTATE "):]
        brace = frag.find("{")
        if brace < 0:
            continue
        try:
            r = json.loads(frag[brace:].strip())
        except json.JSONDecodeError:
            continue
        k = r.get("kind")
        if k == "region" and r.get("region"):
            out["region"][r["region"]] = r
        elif k == "force" and r.get("mf_cqi") is not None:
            out["force"][r["mf_cqi"]] = r
        elif k == "unit" and r.get("mf_cqi") is not None:
            mf = r["mf_cqi"]
            if unit_seen_turn.get(mf) != r.get("turn"):   # new dump group -> fresh list
                out["units"][mf] = []
                unit_seen_turn[mf] = r.get("turn")
            out["units"][mf].append(r)
        elif k == "char" and r.get("char_cqi") is not None:
            out["char"][r["char_cqi"]] = r
    return out


def read_actions(run_path, entity_kind=None, entity_id=None, action_type=None, history=False):
    """Rows from the actions stream's actions.sqlite (latest by default). Read-only.

    Returns {"rows": [{entity_kind, entity_id, action_type, ts, turn, payload}], "error": str|None}.
    """
    dbp = os.path.join(run_path, "actions.sqlite")
    if not os.path.exists(dbp):
        return {"rows": [], "error": "no actions.sqlite (actions stream not running?)"}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % dbp.replace("\\", "/"), uri=True, timeout=5.0)
    except sqlite3.Error as e:
        return {"rows": [], "error": "sqlite open failed: %s" % e}
    try:
        tbl = "snapshots" if history else "latest"
        q = "SELECT entity_kind, entity_id, action_type, ts, turn, payload FROM %s" % tbl
        cond, args = [], []
        for col, val in (("entity_kind", entity_kind), ("entity_id", entity_id),
                         ("action_type", action_type)):
            if val is not None:
                cond.append("%s=?" % col)
                args.append(str(val))
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY ts DESC" + (" LIMIT 500" if history else "")
        rows = []
        for ek, ei, at, ts, turn, payload in con.execute(q, args):
            try:
                p = json.loads(payload)
            except json.JSONDecodeError:
                p = {"_unparseable": payload[:120]}
            rows.append({"entity_kind": ek, "entity_id": ei, "action_type": at,
                         "ts": ts, "turn": turn, "payload": p})
        return {"rows": rows, "error": None}
    except sqlite3.Error as e:
        return {"rows": [], "error": "sqlite read failed: %s" % e}
    finally:
        con.close()


def stance_whitelist(run_path, notes):
    """The faction's LEGAL stance keys, from the recorder's army_stances UI-stack captures.

    ⚠ REQUIRED before any stance is executed: the cco StanceList includes faction-ILLEGAL
    stances and `Activate` sets them anyway (verified rule breach: HEF army entered TUNNELING).
    The UI stack only ever renders the faction's real stances. Returns a set (empty = no
    capture yet -> callers must mark stances unavailable, never guess)."""
    menus = read_ui_components(run_path, notes)[0]
    keys = set()
    for m in menus:
        if m.get("panel") != "army_stances":
            continue
        for o in m.get("options") or []:
            k = str(o.get("key") or o.get("id") or "")
            if k:
                keys.add(k if k.startswith("MILITARY_FORCE_ACTIVE_STANCE_TYPE_")
                         else "MILITARY_FORCE_ACTIVE_STANCE_TYPE_" + k)
    return keys


_BCOST_DB = os.path.join(os.path.dirname(HERE), "advisor", "reference", "reference.sqlite")
_bcost_cache = None


def _building_cost(key):
    """create_cost from the advisor reference DB (None if unknown)."""
    global _bcost_cache
    if _bcost_cache is None:
        _bcost_cache = {}
        try:
            con = sqlite3.connect("file:%s?mode=ro" % _BCOST_DB.replace("\\", "/"), uri=True)
            for k, c in con.execute("SELECT key, create_cost FROM buildings"):
                _bcost_cache[k] = c
            con.close()
        except sqlite3.Error:
            pass
    return _bcost_cache.get(key)


def build_campaign_spans(sessions):
    """
    Per-session wall-clock spans as (start_epoch, end_epoch, faction), where each
    session owns the half-open interval (prev_mtime, this_mtime]. The earliest
    session opens at -inf and the newest (current) session runs to +inf, so every
    timestamp maps to exactly one campaign.
    """
    spans = []
    n = len(sessions)
    for i, s in enumerate(sessions):
        start = sessions[i - 1]["mtime"] if i > 0 else float("-inf")
        end = s["mtime"] if i < n - 1 else float("inf")
        spans.append((start, end, s["faction"]))
    return spans


def attribute_campaign(abs_t, spans):
    """Which campaign owned wall-clock time `abs_t` (epoch)? None if unknown."""
    if abs_t is None or not spans:
        return None
    for start, end, fac in spans:
        if start < abs_t <= end:
            return fac
    return None


def read_ui_components(run_path, notes):
    """
    Full scan of ui_components.jsonl. Returns:
      menus            list of menu_open dicts (chronological)
      decisions        total count of menu_open records (decisions captured)
      last_status      last ui_status.status seen (any)
      last_status_t    its t
      last_bus_status  last status in {bus_available, bus_unavailable}
      skipped          count of malformed lines skipped
    """
    path = os.path.join(run_path, "ui_components.jsonl")
    menus = []
    decisions = 0
    last_status = None
    last_status_t = None
    last_bus_status = None
    skipped = 0
    if not os.path.exists(path):
        notes.append("ui_components.jsonl missing")
        return menus, decisions, last_status, last_status_t, last_bus_status, skipped
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                kind = rec.get("kind")
                if kind == "menu_open":
                    decisions += 1
                    menus.append(rec)
                elif kind == "ui_status":
                    last_status = rec.get("status")
                    last_status_t = rec.get("t")
                    if last_status in ("bus_available", "bus_unavailable"):
                        last_bus_status = last_status
    except Exception as e:
        notes.append("ui_components.jsonl read error: %s" % e)
    if skipped:
        notes.append("ui_components: skipped %d malformed line(s)" % skipped)
    return menus, decisions, last_status, last_status_t, last_bus_status, skipped


def read_advisor(run_path, notes):
    """
    Parse advisor.jsonl if present -> list of scored tables (dicts with ts,
    screen, options[...]). Absent/partial is normal; return [] gracefully.
    """
    path = os.path.join(run_path, "advisor.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    skipped = 0
    try:
        # advisor.jsonl is modest; a full read is fine and keeps matching simple.
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
    except Exception as e:
        notes.append("advisor.jsonl read error: %s" % e)
    if skipped:
        notes.append("advisor.jsonl: skipped %d malformed line(s)" % skipped)
    return rows


# Recorder menu PANEL names differ from the advisor's decision-TYPE / game-SCREEN
# labels, so a naive screen==panel join misses almost everything. This maps each
# menu panel to the canonical advisor decision type it represents. It MUST mirror
# advisor/decision_instances.py PANEL_TYPE exactly -- any panel missing here renders
# "raw options" even when the advisor scored it (the pre_battle bug: pre_battle now
# HAS a trained model + live advisor rows, so it must map, not stay "unmatched").
PANEL_TO_TYPE = {
    "construction": "building",
    "technology": "research",
    "recruitment": "recruit",
    "skills": "skills",
    "occupation": "occupation",
    "post_battle_captives": "captives",
    "captives": "captives",
    "rites": "rites",
    "rites_great_game": "rites",
    "great_game_rituals": "rites",
    "army_stances": "stance",
    "edicts": "edict",
    "equipment": "items",
    "equipment_equipped": "items",
    "items": "items",
    # recruit_panel is the LORD/HERO recruitment POOL (its own type recruit_lord),
    # a DIFFERENT decision than unit recruitment ("recruit").
    "recruit_panel": "recruit_lord",
    "recruit_lord": "recruit_lord",
    "recruit_hero": "recruit_hero",
    "eternal_dance": "eternal_dance",
    "pre_battle": "pre_battle",
    # diplomacy — WHO to deal with vs WHAT deal.
    "diplomacy": "diplomatic_target",
    "diplomacy_options": "diplomatic_deal",
    # offices has no trained model yet -> renders "model pending" (mapped, not raw).
    "offices": "offices",
}

# Advisor-SYNTHESIZED decision types: the advisor scores these, but the recorder
# fires NO menu_open for them (HUD stacks / the construction sub-popup emit no
# PanelOpenedCampaign -- documented in ui-capture/ui_component_recorder.py), so
# they have no captured panel to attach to and were invisible in the UI. We
# surface them as authoritative SCREEN cards instead (build_synth_cards).
SYNTH_TYPES = {"building", "edict", "stance", "dilemma"}


def match_scored_table(menu, advisor_rows, current_campaign, model_types,
                       campaign_filter, t0_epoch=None):
    """
    Match a menu_open (panel + t) to an advisor scored table, WITHIN the current
    campaign. Returns (table_or_None, note_or_None):

      * (table, None)  a scored table for this menu's decision type, drawn only
                       from current-campaign advisor rows and within
                       MAX_MATCH_DELTA_SEC of the menu's timestamp.
      * (None, "no scored table (model pending)")  the panel maps to a decision
                       TYPE with no trained model (no advisor row of that type
                       exists anywhere), e.g. recruit_lord / diplomatic_target.
      * (None, "no scored table (no recent match)")  the type has a model but no
                       current-campaign row near this menu (stale/absent) — a
                       blank beats a wrong-campaign or stale table.
      * (None, None)   the panel is not a scored decision panel (pre_battle, …);
                       the caller falls back to raw options.

    D3: joining on type + nearest-ts ONLY (no campaign gate) let a menu match an
    advisor row from a DIFFERENT campaign in a multi-campaign run dir. We now (a)
    only consider rows whose `campaign` equals the current one, and (b) reject a
    nearest match farther than MAX_MATCH_DELTA_SEC. `campaign_filter=False`
    disables the campaign gate for legacy runs whose advisor.jsonl predates the
    `campaign` field (all rows untagged); type+ts matching still applies there.

    Timestamps: menu `t` is seconds-since-t0, advisor `ts` is absolute epoch; we
    align via t0_epoch + t. When alignment is unavailable the ts cutoff is skipped.
    """
    panel = str(menu.get("panel", "")).lower()
    want_type = PANEL_TO_TYPE.get(panel)
    if want_type is None:
        return None, None  # not a scored decision panel -> raw options
    if want_type not in model_types:
        return None, "no scored table (model pending)"
    if not advisor_rows:
        return None, "no scored table (no recent match)"
    mt = menu.get("t")
    m_abs = (t0_epoch + mt) if (t0_epoch is not None and mt is not None) else None
    best = None
    best_delta = None
    want_campaign = _campaign_key(current_campaign)
    for row in advisor_rows:
        if campaign_filter and _campaign_key(row.get("campaign")) != want_campaign:
            continue
        rtypes = [str(x).lower() for x in (row.get("types") or [])]
        if want_type not in rtypes:
            continue
        rts = row.get("ts")
        if m_abs is None or rts is None:
            delta = float("inf")
        else:
            delta = abs(rts - m_abs)
        if best is None or delta < best_delta:
            best = row
            best_delta = delta
    if best is None:
        return None, "no scored table (no recent match)"
    # Reject a stale/absent match: nearest same-campaign row too far in time.
    if best_delta not in (None, float("inf")) and best_delta > MAX_MATCH_DELTA_SEC:
        return None, "no scored table (no recent match)"
    # When the matched row bundles multiple decision types (e.g. items+skills on
    # the character screen), keep only the options of the panel's own type so the
    # equipment panel shows items and the skills panel shows skills.
    multi = len(best.get("types") or []) > 1
    opts = []
    for o in best.get("options", []) or []:
        if multi and str(o.get("type", "")).lower() != want_type:
            continue
        opts.append({
            "type": o.get("type"),
            "key": o.get("key"),
            "name": o.get("name"),
            "combined": o.get("combined"),
            "exploit": o.get("exploit"),
            "explore": o.get("explore"),
            "available": o.get("available"),
        })
    opts.sort(key=lambda o: (o["combined"] is None, -(o["combined"] or 0)))
    table = {
        "ts": best.get("ts"),
        "turn": best.get("turn"),
        "screen": best.get("screen"),
        "types": best.get("types"),
        "campaign": best.get("campaign"),
        "match_delta_sec": (round(best_delta, 1)
                            if best_delta not in (None, float("inf")) else None),
        "options": opts,
    }
    return table, None


def newest_shot(run_path):
    """(newest_shot_name, newest_shot_mtime, count) via a single scandir pass."""
    shots_dir = os.path.join(run_path, "shots")
    best_name = None
    best_mtime = None
    count = 0
    try:
        with os.scandir(shots_dir) as it:
            for e in it:
                if not e.is_file():
                    continue
                count += 1
                if best_name is None or e.name > best_name:
                    best_name = e.name
                    try:
                        best_mtime = e.stat().st_mtime
                    except OSError:
                        best_mtime = None
    except OSError:
        pass
    return best_name, best_mtime, count


def build_synth_cards(advisor_rows, cap):
    """
    Surface each advisor SCREEN row that contains an advisor-SYNTHESIZED type
    (building/edict/stance/dilemma) as an authoritative screen card carrying the
    row's FULL combined table (all types ranked together by impact) -- e.g. the
    province card shows building + recruit_lord + rites sorted as one table.

    These rows have NO menu_open (the recorder cannot emit one for them), so
    there is nothing to attribute to a wall-clock campaign span; every card IS a
    single advisor row (one campaign), so no cross-campaign mixing is possible.
    We therefore surface qualifying rows for EVERY campaign in the run, each card
    labelled with its own campaign. Screens WITHOUT a synth type (research,
    character=items+skills, post_battle=captives+occupation, pre_battle) are left
    entirely to the menu-card path and are untouched.

    Returns (cards, subsumed):
      cards     list of menu-card-shaped dicts (renderScoredTable-ready).
      subsumed  set of (campaign_key, type) a shown card already covers, so the
                caller can drop the now-redundant single-type menu cards
                (recruit_lord / rites / recruit) it subsumes -> zero duplication.
    """
    cards = []
    subsumed = set()
    try:
        # Dedup to the freshest row per (campaign, screen, turn) so re-opens of a
        # screen within a turn collapse to one card.
        best = {}
        for r in advisor_rows or []:
            types = [str(t).lower() for t in (r.get("types") or [])]
            if not any(t in SYNTH_TYPES for t in types):
                continue
            ck = _campaign_key(r.get("campaign"))
            key = (ck, str(r.get("screen")), r.get("turn"))
            ts = r.get("ts")
            prev = best.get(key)
            if prev is None or (ts is not None and
                                (prev.get("ts") is None or ts > prev["ts"])):
                best[key] = r
        rows = sorted(best.values(),
                      key=lambda r: (r.get("ts") is None, -(r.get("ts") or 0)))
        for r in rows[:cap]:
            ck = _campaign_key(r.get("campaign"))
            opts = []
            for o in (r.get("options") or []):
                opts.append({
                    "type": o.get("type"),
                    "key": o.get("key"),
                    "name": o.get("name"),
                    "combined": o.get("combined"),
                    "exploit": o.get("exploit"),
                    "explore": o.get("explore"),
                    "available": o.get("available"),
                })
            opts.sort(key=lambda o: (o["combined"] is None, -(o["combined"] or 0)))
            raw_camp = r.get("campaign")
            # Strip a leading "<runid>." so prettyFaction (JS) renders a clean
            # label for replay rows; live faction keys pass through unchanged.
            disp_camp = (str(raw_camp).rsplit(".", 1)[-1]
                         if (raw_camp and "." in str(raw_camp)) else raw_camp)
            table = {
                "ts": r.get("ts"),
                "turn": r.get("turn"),
                "screen": r.get("screen"),
                "types": r.get("types"),
                "campaign": disp_camp,
                "match_delta_sec": None,
                "options": opts,
            }
            cards.append({
                "panel": r.get("screen"),   # -> "<screen> menu received" in the UI
                "n": len(opts),
                "t": r.get("ts"),
                "campaign": disp_camp,
                "options": [],
                "deal": None,
                "scored": table,
                "scored_note": None,
                "synth": True,
            })
            for t in types:
                subsumed.add((ck, t))
    except Exception as e:
        _log("build_synth_cards failed: %s" % e)
    return cards, subsumed


# ----------------------------------------------------------------------------
# State assembly
# ----------------------------------------------------------------------------
def build_state(requested_run):
    now = time.time()
    notes = []

    run_id, run_path = resolve_run(requested_run)
    runs = list_runs()
    runs_out = [{"id": r, "fresh_age_sec": _age(f, now)} for r, f in runs[:RUN_LIST_LIMIT]]

    if run_id is None:
        return {
            "ok": False,
            "generated_at": round(now, 1),
            "error": "no runs found under %s" % RUNS_ROOT,
            "runs": runs_out,
            "notes": notes,
        }

    if requested_run and requested_run != run_id:
        notes.append("requested run '%s' not found; using freshest '%s'" % (requested_run, run_id))

    meta = read_meta(run_path, notes)
    sessions = scan_faction_sessions(run_path, notes)
    faction, source_log = latest_faction_row(sessions)
    current_campaign = faction.get("faction") if faction else None
    menus_raw, decisions, last_status, last_status_t, last_bus_status, _skip = \
        read_ui_components(run_path, notes)
    advisor_rows = read_advisor(run_path, notes)

    # --- campaign stats ---
    if faction:
        campaign = {
            "turn": faction.get("turn"),
            "faction": faction.get("faction"),
            "subculture": faction.get("subculture"),
            "culture": faction.get("culture"),
            "income": faction.get("income"),
            "net_income": faction.get("net_income"),
            "expenditure": faction.get("expenditure"),
            "treasury": faction.get("treasury"),
            "regions": faction.get("regions"),
            "rank": faction.get("rank"),  # power rank, if present
            "num_vassals": faction.get("num_vassals"),  # if present
            "num_allies": faction.get("num_allies"),
            "num_generals": faction.get("num_generals"),
            "forces": faction.get("forces"),
            "at_war": faction.get("at_war"),
            "source_log": source_log,
        }
    else:
        campaign = None

    # --- health ---
    ui_mtime = _mtime(os.path.join(run_path, "ui_components.jsonl"))
    events_mtime = _mtime(os.path.join(run_path, "events.jsonl"))
    shot_name, shot_mtime, shot_count = newest_shot(run_path)

    # Heartbeat: ui_status lines dominate ui_components and are frequent, so the
    # file's last write is an accurate wall-clock proxy for the last heartbeat.
    heartbeat_age = _age(ui_mtime, now)
    shots_age = _age(shot_mtime, now)
    events_age = _age(events_mtime, now)
    ui_age = _age(ui_mtime, now)

    def fresh_ok(age):
        return (age is not None) and (age <= FRESH_SEC)

    health = {
        "heartbeat_age_sec": heartbeat_age,
        "heartbeat_ok": (heartbeat_age is not None) and (heartbeat_age <= FRESH_SEC),
        "last_status": last_status,
        "last_status_t": last_status_t,
        "last_bus_status": last_bus_status,
        "shots_age_sec": shots_age,
        "shots_ok": fresh_ok(shots_age),
        "shots_count": shot_count,
        "newest_shot": shot_name,
        "events_age_sec": events_age,
        "events_ok": fresh_ok(events_age),
        "ui_components_age_sec": ui_age,
        "ui_components_ok": fresh_ok(ui_age),
        "decisions_captured": decisions,
        "decisions_ok": decisions > 0,
    }

    # --- menus (most recent first) + scored table match ---
    # D3: a run dir can interleave menu_opens from MULTIPLE campaigns. Attribute
    # every menu to its owning campaign (by wall-clock span), keep only the
    # current campaign's menus, and match each against current-campaign advisor
    # rows only, so the header campaign and every card belong to one campaign.
    t0_epoch = meta.get("t0_epoch")
    spans = build_campaign_spans(sessions)

    # Which decision types actually have a trained model (appear in advisor.jsonl
    # at all)? Types with none render "model pending" rather than a wrong table.
    model_types = set()
    for r in advisor_rows:
        for tp in (r.get("types") or []):
            model_types.add(str(tp).lower())

    # Only gate on `campaign` when advisor.jsonl actually carries it; legacy runs
    # (all rows untagged) fall back to type+ts matching so they keep working.
    campaign_filter = bool(current_campaign) and any(r.get("campaign") for r in advisor_rows)

    # Attribute each menu to a campaign, then keep only the current one.
    attributed = []
    for m in menus_raw:
        mt = m.get("t")
        m_abs = (t0_epoch + mt) if (t0_epoch is not None and mt is not None) else None
        attributed.append((m, attribute_campaign(m_abs, spans)))

    if current_campaign and spans:
        current_menus = [pair for pair in attributed if pair[1] == current_campaign]
        dropped = len(attributed) - len(current_menus)
        if dropped:
            notes.append("hid %d menu(s) from other campaigns in this run dir" % dropped)
    else:
        current_menus = attributed  # cannot attribute (no faction/spans) -> show all

    # Authoritative SCREEN cards for advisor-synthesized decisions (building/
    # edict/stance/dilemma), which have no menu_open to attach to. These carry
    # the full combined table; the (campaign, type) pairs they cover let us drop
    # the single-type menu cards they subsume so nothing renders twice.
    synth_cards, subsumed = build_synth_cards(advisor_rows, MENU_LIMIT)

    menus_out = []
    suppressed = 0
    for m, cam in reversed(current_menus[-MENU_LIMIT:]):
        want_type = PANEL_TO_TYPE.get(str(m.get("panel", "")).lower())
        if want_type is not None and (_campaign_key(cam), want_type) in subsumed:
            suppressed += 1  # subsumed by an authoritative screen card -> no dup
            continue
        table, note = match_scored_table(
            m, advisor_rows, current_campaign, model_types, campaign_filter, t0_epoch)
        menus_out.append({
            "panel": m.get("panel"),
            "n": m.get("n"),
            "t": m.get("t"),
            "campaign": cam,
            "options": m.get("options", []),
            "deal": m.get("deal"),
            "scored": table,
            "scored_note": note,
        })
    if suppressed:
        notes.append("merged %d single-type menu(s) into screen card(s)" % suppressed)
    # Screen cards (province/army/…) lead; captured-menu cards follow.
    menus_out = synth_cards + menus_out

    return {
        "ok": True,
        "generated_at": round(now, 1),
        "run": {
            "id": run_id,
            "dir": run_path,
            "started": meta.get("started"),
            "recorder_version": meta.get("recorder_version"),
            "screen": meta.get("screen"),
        },
        "runs": runs_out,
        "campaign": campaign,
        "current_campaign": current_campaign,
        "campaigns_in_run": [s["faction"] for s in sessions],
        "health": health,
        "menus": menus_out,
        "advisor_present": bool(advisor_rows),
        "notes": notes,
    }


# ----------------------------------------------------------------------------
# v0 ON-REQUEST advice: /api/advise?type=<menu_type>&run=<run_id>
# ----------------------------------------------------------------------------
def advise_menu(run_path, menu_type, notes, entity_kind=None, entity_id=None):
    """Rank `menu_type`'s options for run_path's CURRENT faction, ON DEMAND.

    PER-ENTITY data-side path (building/edict/stance, entity_id given): the option set comes
    from actions.sqlite (the cco actionable-state sweeps -- REAL game keys, real availability)
    and the context gains the entity's TWSTATE sub-blocks trimmed exactly like training-time
    _enrich_context. The old DB-synth building universe is RETIRED (superseded by exact
    per-slot sets). Other types keep the freshest-captured-menu_open path. Returns
    {type, entity, faction, n, options[ranked by combined]} or {error}."""
    if _ADV_M is None:
        return {"error": "advisor scoring unavailable (import failed)"}
    global _ADV_BUNDLE
    if _ADV_BUNDLE is None:
        _ADV_BUNDLE = _ADV_M.load_all()
    models, explorers, meta, state_models = _ADV_BUNDLE
    sessions = scan_faction_sessions(run_path, notes)
    faction, _src = latest_faction_row(sessions)
    ctx = dict(faction) if faction else {}
    entity = {"kind": entity_kind, "id": entity_id} if entity_kind else None
    opts = []
    if menu_type in ("building", "edict", "stance"):
        # ---- data-side option sets from the actions stream ----
        if not entity_id:
            return {"error": "%s advice is per-entity: pass entity_kind+entity_id "
                             "(settlement region key or lord cqi)" % menu_type, "type": menu_type}
        ents = latest_entity_rows(run_path, notes)
        treasury = ctx.get("treasury")
        if menu_type == "building":
            act = read_actions(run_path, "settlement", entity_id, "building_slots")
            if act["error"] or not act["rows"]:
                return {"error": act["error"] or "no building_slots row for %s" % entity_id,
                        "type": menu_type, "entity": entity}
            payload = act["rows"][0]["payload"]
            tier = 0
            for sl in payload.get("slots") or []:
                b = sl.get("building") or ""
                if sl.get("index") == 0 and b:
                    digits = [c for c in b.split("_")[-1] if c.isdigit()]
                    tier = int(digits[0]) if digits else 0
            seen = {}
            for sl in payload.get("slots") or []:
                if sl.get("building") or sl.get("is_building_new"):
                    continue
                if sl.get("activate_level") is not None and sl["activate_level"] > tier:
                    continue                              # slot locked at this settlement tier
                for p in sl.get("possibles") or []:
                    cost = _building_cost(p["key"])
                    avail = bool(p.get("req_met")) and (
                        cost is None or treasury is None or cost <= treasury)
                    if p["key"] not in seen or (avail and not seen[p["key"]]["available"]):
                        seen[p["key"]] = {"key": p["key"], "id": p["key"], "available": avail,
                                          "source": "cco", "slot_index": sl.get("index"),
                                          "cost": cost}
            opts = list(seen.values())
            rr = ents["region"].get(entity_id)
            ctx["region"] = _trim(rr, _REGION_KEEP)
            built = [sl.get("building") for sl in payload.get("slots") or [] if sl.get("building")]
            ctx["region_buildings"] = built
            ctx["region_free_slots"] = sum(1 for sl in payload.get("slots") or []
                                           if not sl.get("building") and not sl.get("is_building_new"))
        elif menu_type == "edict":
            act = read_actions(run_path, "settlement", entity_id, "edicts")
            if act["error"] or not act["rows"]:
                return {"error": act["error"] or "no edicts row for %s" % entity_id,
                        "type": menu_type, "entity": entity}
            payload = act["rows"][0]["payload"]
            can_set = bool(payload.get("can_set"))
            installed = payload.get("installed")
            opts = [{"key": k, "id": k, "available": can_set and k != installed, "source": "cco"}
                    for k in payload.get("options") or []]
            rr = ents["region"].get(entity_id)
            ctx["region"] = _trim(rr, _REGION_KEEP)
        else:  # stance
            act = read_actions(run_path, "lord", entity_id, "stances")
            if act["error"] or not act["rows"]:
                return {"error": act["error"] or "no stances row for lord %s" % entity_id,
                        "type": menu_type, "entity": entity}
            wl = stance_whitelist(run_path, notes)
            if not wl:
                notes.append("no army_stances capture yet -> ALL stances unavailable "
                             "(legality whitelist required; select an army once to capture it)")
            payload = act["rows"][0]["payload"]
            for s in payload.get("stances") or []:
                legal = s["key"] in wl
                opts.append({"key": s["key"], "id": s["key"],
                             "available": bool(s.get("can_activate")) and bool(s.get("can_afford"))
                             and legal and not s.get("active"),
                             "source": "cco", "active": s.get("active"), "legal": legal})
            cq = None
            try:
                cq = int(entity_id)
            except (TypeError, ValueError):
                pass
            cr = ents["char"].get(cq)
            ctx["char"] = _trim(cr, _CHAR_KEEP)
            reg = (cr or {}).get("region")
            fr = None
            for mf, f in ents["force"].items():
                if reg and (f.get("region") == reg or f.get("region_garrison") == reg):
                    fr = f
                    break
            if fr:
                ctx["army"] = _trim(fr, _FORCE_KEEP)
                ctx["army_units"] = ents["units"].get(fr.get("mf_cqi")) or []
                rr = ents["region"].get(fr.get("region") or fr.get("region_garrison"))
                ctx["region"] = _trim(rr, _REGION_KEEP)
    else:
        panels = set(ADVISE_TYPE_PANELS.get(menu_type, [menu_type]))
        menus = read_ui_components(run_path, notes)[0]
        mo = None
        for m in menus:
            if m.get("panel") in panels:
                mo = m                                       # freshest matching menu_open
        for o in (mo.get("options") if mo else []) or []:
            opts.append({
                "key": o.get("key"), "id": o.get("id"), "state": o.get("state"),
                "available": (True if o.get("clickable") is True else
                              False if o.get("clickable") is False else None),
                "source": o.get("source"), "onscreen": o.get("onscreen"),
                # the recorder's captured readable NAME (dy_name/dy_option/GetStateText) -- must ride
                # through to the ranked row (dropping it rendered raw keys, live E2E 20260729_154706)
                "name": o.get("name"), "label": o.get("label"),
                "x": o.get("x"), "y": o.get("y"), "w": o.get("w"), "h": o.get("h")})
    if not opts:
        return {"type": menu_type, "faction": ctx.get("faction"), "n": 0, "options": [],
                "entity": entity,
                "note": "no options (menu not currently open/captured, or entity has none)"}
    dec = {"type": menu_type, "context": ctx, "options": opts,
           "turn": ctx.get("turn"), "campaign": ctx.get("faction"), "chosen": None}
    try:
        ranked = _ADV_M.score_decision(dec, models, explorers, meta, state_models=state_models)
    except Exception as e:
        return {"error": "score_decision failed: %s" % e, "type": menu_type}
    cmap = {(o.get("key") or o.get("id")): o for o in opts}   # carry coords + a readable name onto rows
    for r in ranked:
        s = cmap.get(r.get("key")) or {}
        for c in ("x", "y", "w", "h"):
            if s.get(c) is not None:
                r[c] = s[c]
        r["name"] = (r.get("label") or r.get("onscreen") or s.get("name") or s.get("label")
                     or s.get("onscreen") or r.get("key"))
        for extra in ("slot_index", "cost", "active", "legal", "source"):
            if s.get(extra) is not None:
                r[extra] = s[extra]
        if (r.get("key") or r.get("id")) in FORBIDDEN_BATTLE_KEYS:
            r["available"] = False                       # hardcoded: battle-UI entries never selectable
    ranked.sort(key=lambda r: -(r.get("combined") or 0.0))
    return {"type": menu_type, "faction": ctx.get("faction"), "n": len(ranked),
            "entity": entity, "options": ranked}


# ----------------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "TWAdvisorUI/1.0"

    def log_message(self, fmt, *args):
        # Keep the console quiet; errors are logged explicitly elsewhere.
        pass

    def _send(self, code, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route == "/" or route == "/index.html":
                self._serve_index()
            elif route == "/api/state":
                qs = parse_qs(parsed.query)
                requested = (qs.get("run") or [None])[0]
                state = build_state(requested)
                self._send(200, json.dumps(state), "application/json; charset=utf-8")
            elif route == "/api/runs":
                now = time.time()
                runs = [{"id": r, "fresh_age_sec": _age(f, now)} for r, f in list_runs()]
                self._send(200, json.dumps({"runs": runs}), "application/json; charset=utf-8")
            elif route == "/api/advise":
                qs = parse_qs(parsed.query)
                mtype = (qs.get("type") or [None])[0]
                run_id, run_path = resolve_run((qs.get("run") or [None])[0])
                ek = (qs.get("entity_kind") or [None])[0]
                ei = (qs.get("entity_id") or [None])[0]
                notes = []
                if not mtype or not run_path:
                    res = {"error": "type + a resolvable run required", "type": mtype, "run": run_id}
                else:
                    res = advise_menu(run_path, mtype, notes, entity_kind=ek, entity_id=ei)
                    res["run"] = run_id
                res["notes"] = notes
                self._send(200, json.dumps(res), "application/json; charset=utf-8")
            elif route == "/api/actions":
                qs = parse_qs(parsed.query)
                run_id, run_path = resolve_run((qs.get("run") or [None])[0])
                if not run_path:
                    res = {"error": "no resolvable run", "run": run_id}
                else:
                    res = read_actions(run_path,
                                       (qs.get("entity_kind") or [None])[0],
                                       (qs.get("entity_id") or [None])[0],
                                       (qs.get("type") or [None])[0],
                                       history=(qs.get("history") or ["0"])[0] == "1")
                    res["run"] = run_id
                self._send(200, json.dumps(res), "application/json; charset=utf-8")
            elif route == "/healthz":
                self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            else:
                self._send(404, json.dumps({"error": "not found", "path": route}),
                           "application/json; charset=utf-8")
        except Exception as e:
            _log("request %s failed: %s" % (route, e))
            _log(traceback.format_exc().splitlines()[-1])
            try:
                self._send(500, json.dumps({"ok": False, "error": str(e)}),
                           "application/json; charset=utf-8")
            except Exception:
                pass

    def do_POST(self):
        """POST /api/actions/refresh {entity_kind, entity_id|'all', run?} -> appends a request row
        to <run>/actions_requests.jsonl (the actions stream tails it). This is the server's ONLY
        write -- a request note in the run dir; it never touches the game or the bus."""
        parsed = urlparse(self.path)
        try:
            if parsed.path != "/api/actions/refresh":
                self._send(404, json.dumps({"error": "not found", "path": parsed.path}),
                           "application/json; charset=utf-8")
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send(400, json.dumps({"error": "bad json: %s" % e}),
                           "application/json; charset=utf-8")
                return
            run_id, run_path = resolve_run(body.get("run"))
            if not run_path:
                self._send(400, json.dumps({"error": "no resolvable run", "run": run_id}),
                           "application/json; charset=utf-8")
                return
            req = {"entity_kind": body.get("entity_kind") or "all",
                   "entity_id": body.get("entity_id") or "all", "t": time.time()}
            with open(os.path.join(run_path, "actions_requests.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(req) + "\n")
            self._send(200, json.dumps({"ok": True, "queued": req, "run": run_id}),
                       "application/json; charset=utf-8")
        except Exception as e:
            _log("POST %s failed: %s" % (parsed.path, e))
            try:
                self._send(500, json.dumps({"ok": False, "error": str(e)}),
                           "application/json; charset=utf-8")
            except Exception:
                pass

    def _serve_index(self):
        try:
            with open(INDEX_HTML, "rb") as f:
                body = f.read()
            self._send(200, body, "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(500, "index.html not found next to server.py", "text/plain; charset=utf-8")


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d/" % (HOST, PORT)
    _log("WH3 advisor dashboard serving at %s  (Ctrl+C to stop)" % url)
    _log("reading runs from %s (READ-ONLY)" % RUNS_ROOT)
    print(url, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
