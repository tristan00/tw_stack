r"""item_choices.py -- OFFLINE derivation of the ITEM (ancillary / magic-item) decision.

The decision (per the user): at any moment a character's item option-set is
    (a) POOL items UNASSIGNED to anybody      -> each an ADD / SWAP-IN   (clickable)
    (b) the character's currently-EQUIPPED items -> each a REMOVE / SWAP-OUT
items already assigned to ANOTHER character are excluded (equipping one CTD'd the game; the
capture is read-only so recording is safe, but they never read clickable).

WHY A DEDICATED DERIVATION (not the generic menu_open->choice anchoring)
  Items are special on three axes that the generic path can't handle:
  * IDENTITY. The pool/equipped UI nodes are `CcoCampaignAncillary<numeric>` -- the component id is
    the ancillary CQI, NOT the item key. The real key must be RESOLVED. Two offline resolvers:
      SECONDARY (single-snapshot, most reliable): the node's GetStateText = the item's localised NAME
        (e.g. "Sword of Striking") -> reverse via loc `ancillaries_onscreen_name_<key>` -> the key.
      PRIMARY (event correlation): equipping an item makes its numeric id LEAVE the pool scrape AND
        fires `CharacterAncillaryGained` (field `ancillary` = the real key) at the same game-time. So
        the pool id that disappeared between two snapshots <-> the gain key at that time = id->key.
  * ADD is an EVENT (CharacterAncillaryGained.ancillary), not a click on a captured option.
  * REMOVE fires NO EVENT anywhere in the logs. Two independent offline signals recover it:
      SCRAPE-DIFF: diff the EQUIPPED set (equipment_equipped scrape) PER CHARACTER across snapshots --
        a key that leaves the set is a remove. (Primary; lights up once the recorder captures the
        equipped panel -- the ui_component_recorder `equipment_equipped` entry added this session.)
      REPEAT-GAIN inference (event-only, works with zero scrapes): a character holds only ONE of a
        given item at a time, so a SECOND CharacterAncillaryGained of the same (char, item) proves the
        first instance was removed in between. Each consecutive repeat = one remove.

Emits Decision dicts shaped like decision_instances' opens (type="items"), consumed by
build_decisions (which appends derive()'s output) + featurized by features.py's "items" path.

  python item_choices.py <run_dir>     # derive + print real item decisions with real keys
  python item_choices.py --selftest    # exercise the id->key map + remove-diff on synthetic scrapes
"""
import bisect
import glob
import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REF_DB = os.path.join(_HERE, "reference", "reference.sqlite")

# The reference DB has NO structured ancillary/item FEATURES table (checked: tables are building_chains,
# buildings, loc, rituals, skills, tech, units -- no ancillaries/effects). So item features come from
# the KEY (category token) + the loc DISPLAY NAME. This flag is exposed so callers/reports can state the
# gap honestly; add an `ancillaries` table (category / effect bundle / cost) to reference.sqlite to enrich.
HAS_ITEM_DB = False

# Known ancillary CATEGORY tokens (longest-match wins), the segment after `..._anc_`. WH3 magic-item /
# ancillary families: talisman / weapon / armour / banner(magic standard) / enchanted_item / arcane_item
# / follower / mount. Multi-word tokens (enchanted_item, arcane_item, magic_standard) precede single ones.
_ANC_CATS = ("enchanted_item", "arcane_item", "magic_standard", "talisman", "weapon", "armour",
             "banner", "standard", "follower", "mount")

_NUM_RE = re.compile(r"(\d+)$")
_GT_RE = re.compile(r"<([-0-9.]+)s>")
_GAIN_DEDUP = 1.2      # sec: two gains of the same (char,item) closer than this = ONE physical action
                       # logged twice (the game re-fires); collapsed like the recruit-click dedup.
_SWAP_WINDOW = 4.0     # sec: an ADD and a REMOVE on the same char within this = one SWAP


# ---------------------------------------------------------------- loc name<->key (identity resolver)
def _load_loc(db_path=_REF_DB):
    """(key->name, name_lower->[keys]) from loc `ancillaries_onscreen_name_<key>` rows.

    The reverse map (name->keys) is the SECONDARY id->key resolver: a scraped pool/equipped item's
    GetStateText is its display name, reversed here to the ancillary key. Ambiguous names (>1 key)
    are kept as a list so the caller resolves only when unique.
    """
    key2name, name2keys = {}, {}
    if not os.path.isfile(db_path):
        return key2name, name2keys
    pre = "ancillaries_onscreen_name_"
    try:
        db = sqlite3.connect(db_path)
        for k, txt in db.execute("select key,text from loc where key like ?", (pre + "%",)):
            if not txt:
                continue
            key = k[len(pre):]
            key2name[key] = txt
            name2keys.setdefault(txt.strip().lower(), []).append(key)
        db.close()
    except sqlite3.Error as e:
        sys.stderr.write("item_choices._load_loc: loc read failed -> %s\n" % repr(e)[:80])
    return key2name, name2keys


_KEY2NAME, _NAME2KEYS = _load_loc()


def item_name(key):
    """Localised display name for an ancillary key (falls back to the key)."""
    return _KEY2NAME.get(key, key)


def item_category(key):
    """The ancillary CATEGORY token parsed from the key (weapon/armour/talisman/enchanted_item/...)."""
    if not key:
        return "?"
    i = key.find("_anc_")
    tail = key[i + len("_anc_"):] if i >= 0 else key
    for c in _ANC_CATS:
        if tail.startswith(c + "_") or tail == c:
            return c
    return tail.split("_", 1)[0] or "?"


def key_from_text(text):
    """SECONDARY resolver: an item's GetStateText (display name) -> its ancillary key (unique match only)."""
    if not text:
        return None
    ks = _NAME2KEYS.get(str(text).strip().lower())
    return ks[0] if ks and len(ks) == 1 else None


def _num(s):
    """The trailing numeric id from a component id/key ('CcoCampaignAncillary137'->'137', '137'->'137')."""
    m = _NUM_RE.search(str(s) if s is not None else "")
    return m.group(1) if m else None


def _item_num(o):
    """The ITEM's numeric ancillary CQI from a scraped option -- `key` FIRST then `id`.

    Pool entries (enumerate_children) put the item number in BOTH id (`CcoCampaignAncillary137`) and
    key (`137`). EQUIPPED slots (enumerate_context_cards) put the item number in `key` (the bound
    CcoCampaignAncillary id) while `id` is the SLOT record (`CcoAncillariesCategoryRecord1`, a slot
    index -- NOT the item). So `key` must win, else an equipped item resolves to its slot index.
    """
    return _num(o.get("key")) or _num(o.get("id"))


# ---------------------------------------------------------------- read scrapes + gain events
def iter_item_scrapes(run_dir):
    """Yield (kind, t, options) for each item scrape in ui_components.jsonl, in capture order.

    kind is "pool" (panel `equipment`) or "equipped" (panel `equipment_equipped`); t is the recorder
    clock; options is the raw menu_open options list. Returns nothing when neither panel was captured
    (older runs / a run whose recorder predates the equipment poll wiring).
    """
    p = os.path.join(run_dir, "ui_components.jsonl")
    if not os.path.isfile(p):
        return
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "menu_open" not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue  # intentional: skip malformed line, hot per-line parse of ui_components.jsonl (no per-line log)
            if r.get("kind") != "menu_open":
                continue
            panel = r.get("panel")
            if panel == "equipment":
                yield ("pool", r.get("t"), r.get("options") or [])
            elif panel == "equipment_equipped":
                yield ("equipped", r.get("t"), r.get("options") or [])


def read_gains(events, player):
    """Player CharacterAncillaryGained as deduped (gt, char_cqi, char_subtype, key), sorted by gt.

    `events` is structurer.parse_events(log, player_only=True) -- already restricted to the player's
    faction (the event's char_faction). Duplicate log emissions of ONE physical equip (same char+key
    within _GAIN_DEDUP) are collapsed. Each surviving gain is one ADD/equip of a real item key.
    """
    raw = []
    for e in events:
        if e.get("event") != "CharacterAncillaryGained":
            continue
        key = e.get("ancillary")          # the REAL key (the sibling `ancillary_key` is null on this build)
        if not key:
            continue
        raw.append((e.get("t", 0.0), e.get("char_cqi"), e.get("char_subtype"), key))
    raw.sort()
    out, last = [], {}
    for t, cqi, sub, key in raw:
        lk = (cqi, key)
        if lk in last and t - last[lk] < _GAIN_DEDUP:
            last[lk] = t                  # extend the burst; still one physical equip
            continue
        last[lk] = t
        out.append((t, cqi, sub, key))
    return out


# ---------------------------------------------------------------- id -> key resolution
def build_id_key_map(pool_scrapes, gains, off, window=6.0):
    """Numeric-ancillary-id -> real-key map, by two offline resolvers. Returns (id2key, provenance).

    SECONDARY (text->loc): for every pool item with a GetStateText, reverse-map the name to the key.
      Single-snapshot, no event needed -- the workhorse once the pool is scraped.
    PRIMARY (pool-diff <-> gain): for each gain event, the pool item whose numeric id is present in the
      snapshot just BEFORE the gain and absent just AFTER is the equipped item -> id maps to the gain
      key. Only assigned when the disappearance is UNAMBIGUOUS (exactly one such id) so a wrong id is
      never mapped.

    Args:
        pool_scrapes: list of (recorder_t, options) for the `equipment` (pool) panel, in time order.
        gains: read_gains() output (gt, char_cqi, char_subtype, key), sorted by gt.
        off: recorder->game time offset (game_t = recorder_t + off).
        window: max |gt - snapshot_gt| for the PRIMARY correlation.

    Returns:
        (id2key {num_id: key}, provenance {num_id: "text"|"gain"}).
    """
    id2key, prov = {}, {}
    # SECONDARY: text -> loc
    for _t, opts in pool_scrapes:
        for o in opts:
            nid = _item_num(o)
            if not nid or nid in id2key:
                continue
            k = key_from_text(o.get("text") or o.get("text_label"))
            if k:
                id2key[nid] = k
                prov[nid] = "text"
    # PRIMARY: pool-diff disappearance <-> gain event (fills ids the text resolver missed)
    scr = sorted(pool_scrapes, key=lambda s: s[0] if s[0] is not None else 0.0)
    sgt = [(s[0] + off) if s[0] is not None else 0.0 for s in scr]       # snapshot game-times

    def _ids(opts, only_clickable=False):
        s = set()
        for o in opts:
            nid = _item_num(o)
            if not nid:
                continue
            if only_clickable and o.get("clickable") is False:
                continue                              # assigned-to-another; not an equippable-that-left
            s.add(nid)
        return s

    for gt, _cqi, _sub, key in gains:
        i = bisect.bisect_left(sgt, gt)
        before = None
        for j in range(i - 1, -1, -1):                # nearest snapshot before the gain (within window)
            if gt - sgt[j] <= window:
                before = j
            break
        after = None
        for j in range(i, len(sgt)):                  # nearest snapshot after the gain (within window)
            if sgt[j] - gt <= window:
                after = j
            break
        if before is None or after is None:
            continue
        disappeared = _ids(scr[before][1], only_clickable=True) - _ids(scr[after][1])
        cand = [nid for nid in disappeared if nid not in id2key]
        if len(disappeared) == 1 and cand:            # unambiguous -> map
            id2key[cand[0]] = key
            prov[cand[0]] = "gain"
    return id2key, prov


def _resolve_opt_key(o, id2key):
    """Real ancillary key for a scraped pool/equipped option: text->loc, then the id->key map, else num."""
    nid = _item_num(o)
    return (key_from_text(o.get("text") or o.get("text_label"))
            or id2key.get(nid)
            or nid)


# ---------------------------------------------------------------- REMOVE detection
def detect_removes_from_scrapes(equipped_scrapes, id2key):
    """Removes from the SCRAPE channel: diff the equipped set PER CHARACTER across snapshots.

    Each `equipment_equipped` scrape is one character's slots (each option carries `char` = the panel
    character's cqi). Comparing consecutive snapshots of the SAME character, a resolved item key that
    LEAVES the set is a remove (item removes fire no event, so this diff is the only scrape signal).

    Args:
        equipped_scrapes: list of (recorder_t, options); each option a slot dict with `char`.
        id2key: the numeric-id -> key map from build_id_key_map.

    Returns:
        list of (recorder_t, char_cqi, removed_key), in time order.
    """
    out = []
    prev = {}                                          # char_cqi -> set(resolved keys) last seen
    for t, opts in sorted(equipped_scrapes, key=lambda s: s[0] if s[0] is not None else 0.0):
        by_char = {}
        for o in opts:
            if o.get("key") is None and not (o.get("text") or o.get("text_label")):
                continue                               # empty slot
            cqi = o.get("char")
            by_char.setdefault(cqi, set()).add(_resolve_opt_key(o, id2key))
        for cqi, cur in by_char.items():
            if cqi in prev:
                for gone in (prev[cqi] - cur):
                    out.append((t, cqi, gone))
            prev[cqi] = cur
    return out


def infer_removes_from_gains(gains):
    """Removes from the REPEAT-GAIN channel (event-only): a second gain of the same (char,item) proves
    the first was removed. Places the inferred remove just before the later gain.

    Args:
        gains: read_gains() output (gt, char_cqi, char_subtype, key), sorted by gt.

    Returns:
        list of (gt, char_cqi, char_subtype, removed_key), one per consecutive repeat.
    """
    seen = {}                                          # (cqi,key) -> last gain gt
    out = []
    for gt, cqi, sub, key in gains:
        lk = (cqi, key)
        if lk in seen:                                 # already held once -> a remove happened before now
            out.append((gt - 0.01, cqi, sub, key))
        seen[lk] = gt
    return out


# ---------------------------------------------------------------- option-set assembly
def _nearest_scrape(scrapes_gt, gt, window=8.0):
    """Index of the scrape nearest `gt` (game-time) within window, else None. scrapes_gt = sorted list."""
    if not scrapes_gt:
        return None
    i = bisect.bisect_left(scrapes_gt, gt)
    best, bestd = None, window
    for j in (i - 1, i):
        if 0 <= j < len(scrapes_gt):
            d = abs(scrapes_gt[j] - gt)
            if d <= bestd:
                best, bestd = j, d
    return best


def _option_set(pool_at, equipped_at, id2key, char_cqi):
    """Build the decision option set from a pool snapshot + an equipped snapshot (either may be None).

    (a) available-UNASSIGNED pool items (clickable is not False) -> source="available", available=True.
        Items whose state is inactive (assigned to another character) read clickable=False and are
        EXCLUDED (never an option -- equipping them CTD'd the game).
    (b) the character's currently-equipped items -> source="equipped", available=True (removable).
    """
    opts = []
    if pool_at is not None:
        for o in pool_at:
            if o.get("clickable") is False:            # assigned-to-another -> not an add option
                continue
            opts.append({"key": _resolve_opt_key(o, id2key), "id": o.get("id"),
                         "state": o.get("state"), "available": True, "source": "available"})
    if equipped_at is not None:
        for o in equipped_at:
            if char_cqi is not None and o.get("char") not in (None, char_cqi):
                continue
            if o.get("key") is None and not (o.get("text") or o.get("text_label")):
                continue
            opts.append({"key": _resolve_opt_key(o, id2key), "id": o.get("id"),
                         "state": o.get("state"), "available": True, "source": "equipped"})
    return opts


# ---------------------------------------------------------------- the derivation entry point
def derive(run_dir, events, off, gts, turns, player, cid, ctx_by_turn, gmin=None, gmax=None):
    """Every ITEM decision for one campaign, as decision_instances-shaped opens (type="items").

    Adds come from player CharacterAncillaryGained (real key); removes from the scrape-diff AND the
    repeat-gain inference; each is paired to the nearest pool/equipped snapshot for its option set (real
    keys). An add + a remove on the same character within _SWAP_WINDOW is tagged as a swap.

    Args:
        run_dir: the run directory (for the item scrapes).
        events: structurer.parse_events(log, player_only=True) for this campaign's log.
        off: recorder->game offset.
        gts, turns: sorted game-times + turn-at-each (for turn lookup), from _turn_index.
        player: the player faction key.
        cid: the campaign id string used on the decisions.
        ctx_by_turn: {turn -> player faction record} for the decision context.
        gmin, gmax: optional game-time span of this campaign (drop out-of-span decisions).

    Returns:
        list of decision dicts.
    """
    gains = read_gains(events, player)
    pool_scrapes = [(t, opts) for kind, t, opts in iter_item_scrapes(run_dir) if kind == "pool"]
    equip_scrapes = [(t, opts) for kind, t, opts in iter_item_scrapes(run_dir) if kind == "equipped"]
    id2key, _prov = build_id_key_map(pool_scrapes, gains, off)

    pool_sorted = sorted(pool_scrapes, key=lambda s: s[0] if s[0] is not None else 0.0)
    equip_sorted = sorted(equip_scrapes, key=lambda s: s[0] if s[0] is not None else 0.0)
    pool_gt = [(s[0] + off) if s[0] is not None else 0.0 for s in pool_sorted]
    equip_gt = [(s[0] + off) if s[0] is not None else 0.0 for s in equip_sorted]

    def _in_span(gt):
        return (gmin is None or gt >= gmin - 5) and (gmax is None or gt <= gmax + 5)

    def _turn_at(gt):
        i = bisect.bisect_right(gts, gt) - 1
        return turns[i] if i >= 0 else None

    def _opts_at(gt, char_cqi):
        pi = _nearest_scrape(pool_gt, gt)
        ei = _nearest_scrape(equip_gt, gt)
        pool_at = pool_sorted[pi][1] if pi is not None else None
        eq_at = equip_sorted[ei][1] if ei is not None else None
        if pool_at is None and eq_at is None:
            return []
        return _option_set(pool_at, eq_at, id2key, char_cqi)

    # collect adds (from gains) and removes (both channels, de-duplicated by (cqi,key,~time))
    actions = []                                        # (gt, cqi, sub, key, action)
    for gt, cqi, sub, key in gains:
        actions.append((gt, cqi, sub, key, "add"))
    removes = list(infer_removes_from_gains(gains))
    scrape_rm = detect_removes_from_scrapes(equip_scrapes, id2key)
    for t, cqi, key in scrape_rm:                       # scrape removes -> game-time, look up subtype best-effort
        removes.append((t + off, cqi, None, key))
    rm_seen = set()
    for gt, cqi, sub, key in sorted(removes):
        bucket = round(gt, 0)
        sk = (cqi, key, bucket)
        if sk in rm_seen:                               # same remove seen by both channels -> once
            continue
        rm_seen.add(sk)
        actions.append((gt, cqi, sub, key, "remove"))

    # swap tagging: an add & a remove on the same char within _SWAP_WINDOW (different item)
    adds_by_char = {}
    for gt, cqi, sub, key, act in actions:
        if act == "add":
            adds_by_char.setdefault(cqi, []).append((gt, key))
    for c in adds_by_char:
        adds_by_char[c].sort()

    out = []
    order_ctr = {}
    for gt, cqi, sub, key, act in sorted(actions):
        if not _in_span(gt):
            continue
        turn = _turn_at(gt)
        if turn is None:
            continue
        swap_with = None
        if act == "remove":
            for agt, akey in adds_by_char.get(cqi, []):
                if akey != key and abs(agt - gt) <= _SWAP_WINDOW:
                    swap_with = akey
                    break
        opts = _opts_at(gt, cqi)
        linked = any(o.get("key") == key for o in opts) if opts else True
        order_ctr[turn] = order_ctr.get(turn, 0) + 1
        panel = "equipment_equipped" if act == "remove" else "equipment"
        out.append({"campaign": cid, "turn": turn, "order": order_ctr[turn], "type": "items",
                    "panel": panel, "t": gt - off, "gt": gt,
                    "context": ctx_by_turn.get(turn), "options": opts,
                    "chosen": key, "chosen_linked": linked, "chosen_source": None,
                    # per-CHOICE ledger (key, shown, linked, pool) so the standard report's per-choice
                    # link% counts items too -- each item action is exactly one authoritative choice.
                    "_choices": [(key, key, linked, None)],
                    "action": act, "item_name": item_name(key), "item_category": item_category(key),
                    "swap_with": swap_with, "_char_cqi": cqi, "_char_subtype": sub})
    return out


# ---------------------------------------------------------------- standalone verify / self-test
def _selftest():
    """Exercise the id->key map + BOTH remove channels on SYNTHETIC scrapes (this session's real run has
    no equipment scrapes -- the recorder poll wiring for the pool/equipped panels was added THIS session,
    so those channels can only be verified on synthetic data until a fresh recording exists)."""
    print("=== item_choices self-test (synthetic pool/equipped scrapes) ===")
    off = 100.0
    # a pool with two items: one resolvable by loc name-text, one only by the equip-diff correlation.
    real_key = "wh_main_anc_weapon_sword_of_striking"
    real_name = item_name(real_key)
    print("  loc resolves %r -> %r" % (real_key, real_name))
    pool_t0 = [{"id": "CcoCampaignAncillary137", "key": "137", "state": "active", "clickable": True,
                "text": real_name},                                   # resolvable by text
               {"id": "CcoCampaignAncillary200", "key": "200", "state": "active", "clickable": True,
                "text": None}]                                        # only resolvable by the gain-diff
    pool_t1 = [{"id": "CcoCampaignAncillary137", "key": "137", "state": "active", "clickable": True,
                "text": real_name}]                                   # id 200 LEFT the pool (equipped)
    # poll-rate spacing (~1.5s), matching the recorder's POLL_INTERVAL so the correlation window holds
    pool_scrapes = [(18.5, pool_t0), (20.0, pool_t1)]
    other_key = "wh2_dlc10_anc_talisman_stone_of_midnight"
    gains = [(19.5 + off, 56, "hef_lord", other_key)]                 # gain between the two snapshots
    id2key, prov = build_id_key_map(pool_scrapes, gains, off)
    print("  id->key map: %s   provenance: %s" % (id2key, prov))
    assert id2key.get("137") == real_key and prov.get("137") == "text", "text resolver failed"
    assert id2key.get("200") == other_key and prov.get("200") == "gain", "gain-diff resolver failed"

    # SCRAPE-DIFF removes: char 56 has {137,200} then {137} -> 200 removed.
    eq_t0 = [{"id": "CcoAncillariesCategoryRecord0", "key": "137", "char": 56, "text": real_name},
             {"id": "CcoAncillariesCategoryRecord1", "key": "200", "char": 56, "text": None}]
    eq_t1 = [{"id": "CcoAncillariesCategoryRecord0", "key": "137", "char": 56, "text": real_name}]
    rms = detect_removes_from_scrapes([(30.0, eq_t0), (40.0, eq_t1)], id2key)
    print("  scrape-diff removes: %s" % rms)
    assert any(c == 56 and k == other_key for _t, c, k in rms), "scrape-diff remove not detected"

    # REPEAT-GAIN removes: char 56 gains the same weapon twice -> one inferred remove between them.
    g2 = [(200.0, 56, "hef_lord", real_key), (260.0, 56, "hef_lord", real_key)]
    inf = infer_removes_from_gains(g2)
    print("  repeat-gain removes: %s" % inf)
    assert len(inf) == 1 and inf[0][1] == 56 and inf[0][3] == real_key, "repeat-gain inference failed"
    print("  category parse: %s->%s  %s->%s" % (real_key, item_category(real_key),
                                                other_key, item_category(other_key)))
    print("=== self-test PASSED ===  (HAS_ITEM_DB=%s -- no structured ancillary features table; "
          "identity+name from loc, category from key)" % HAS_ITEM_DB)


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    run_dir = sys.argv[1]
    # minimal standalone wiring (decision_instances does the full turn-index/correlate join)
    sys.path.insert(0, os.path.join(_HERE, "..", "structurer"))
    sys.path.insert(0, os.path.join(_HERE, "..", "correlation"))
    import structurer
    import correlation
    logs = sorted(glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail")),
                  key=os.path.getsize, reverse=True)
    total = []
    for log in logs:
        player = structurer.player_faction(log)
        if not player:
            continue
        events = structurer.parse_events(log, turn=None, player_only=True)
        gains = read_gains(events, player)
        if not gains:
            continue
        off = correlation.correlate(run_dir, only_files=[log]).get("offset")
        if off is None:
            off = 0.0
        # cheap turn timeline from the events themselves (game-time -> turn)
        tt = sorted((e.get("t", 0.0), e.get("turn")) for e in events if e.get("turn") is not None)
        gts = [t for t, _ in tt]
        turns = [tn for _, tn in tt]
        ctx = {}
        cid = "%s.%s" % (os.path.basename(run_dir.rstrip("/\\"))[-6:], player.split("_", 2)[-1][:12])
        ds = derive(run_dir, events, off, gts, turns, player, cid, ctx)
        total.extend(ds)
    _report(total)


def _report(ds):
    by_act = {}
    for d in ds:
        by_act.setdefault(d["action"], []).append(d)
    print("\nitem decisions: %d  (add=%d remove=%d)  swaps=%d"
          % (len(ds), len(by_act.get("add", [])), len(by_act.get("remove", [])),
             sum(1 for d in ds if d.get("swap_with"))))
    from collections import Counter
    keys = Counter((d["action"], d["chosen"]) for d in ds)
    print("by (action, key):")
    for (act, k), n in keys.most_common(20):
        print("  %-7s x%-3d %-52s [%s] %s" % (act, n, k, item_category(k), item_name(k)))
    print("\nsample decisions (real keys, not numeric ids):")
    shown = 0
    for d in sorted(ds, key=lambda d: d["gt"]):
        tag = "  SWAP<-%s" % d["swap_with"] if d.get("swap_with") else ""
        print("  turn=%s %-6s char=%s  %-50s (%s) opts=%d%s"
              % (d["turn"], d["action"], d["_char_cqi"], d["chosen"],
                 d["item_name"], len(d["options"]), tag))
        shown += 1
        if shown >= 12:
            break


if __name__ == "__main__":
    main()
