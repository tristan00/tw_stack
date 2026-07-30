r"""screens.py -- the SCREEN_GROUPS registry: which decision TYPES are surfaced together on one
in-game screen, and how their options are merged into ONE cross-type ranked list.

The player rarely faces a single decision type in isolation. Standing on a province they see the
building tree, the provincial edict, and (for rite factions) the rites panel at once -- three
DIFFERENT trained models, three option sets, all competing for the SAME turn's attention. To advise
"what is the single best thing to do on this screen" the advisor has to rank options ACROSS types on
a common scale. That common scale exists precisely because every per-type CatBoost predicts the SAME
target: the blended forward/best/survival value (normalised {income,settlements,power_rank,allies,
vassals}). So the cross-type key (models.score_decision -> option['raw_exploit']) is comparable between
a building option and an edict option. As of CHANGE B that key is the action's IMPACT = f(s,a) - g(s)
(prediction minus the per-type state-only baseline): inconsequential options cluster near 0, campaign-
shaping ones spread - / +. combine_screen sorts on that key. (The per-type exploit/explore/combined
stay for the per-type tables; they are min-max'd PER TYPE and NOT cross-type comparable -- do not sort
a merged list on them.)

This module is PURE PLUMBING: a static registry + a flatten/tag/sort. No model, no tuning, no
re-normalisation. Only types that have a trained model TODAY are listed as live members; everything
still on the roadmap is forward-declared in a comment so the registry is ready to grow.
"""

# screen -> the decision types scored together on that screen. LIVE members only (each has a model in
# models_store today); not-yet-wired members are named in comments so wiring them is a one-line add.
SCREEN_GROUPS = {
    # PROVINCE screen: building tree + provincial edict + rites/rituals + LORD recruitment share the
    # settlement/province view. recruit_lord is now LIVE (has a trained model). FORWARD-DECLARE (join
    # here as wired): demolish, repair, recruit_hero, pleasurable_acts.
    "province": ["building", "edict", "rites", "recruit_lord"],
    # CHARACTER screen: skill tree + magic-item loadout + Slaanesh eternal-dance tab. eternal_dance is now
    # LIVE (has a trained model, Slaanesh data).
    "character": ["skills", "items", "eternal_dance"],
    # ARMY screen: unit recruitment + march/camp stance.
    "army": ["recruit", "stance"],
    # DIPLOMACY screen: WHO to deal with (diplomatic_target) + WHAT deal to stage (diplomatic_deal). Both
    # are now LIVE (trained models). The two are ranked together on the one diplomacy screen-visit.
    "diplomacy": ["diplomatic_target", "diplomatic_deal"],
    # POST-BATTLE screen: captive outcome + settlement occupation decision.
    "post_battle": ["captives", "occupation"],
    # OFFICES screen: standalone, faction-specific (e.g. Nakai/Vampire-Coast/Skaven councils).
    # FORWARD-DECLARE: offices -- 0 trainable rows so NO model today, listed empty (a type with no model
    # must never be a live member: it would sink every row). "offices" joins once its model is wired.
    "offices": [],
}

# reverse index: decision type -> its screen. Types NOT present here (e.g. research, dilemma) are
# STANDALONE -- they have models but no shared screen, so runtime scores/emits them one table at a
# time, exactly as before. combine_screen is only for the grouped types above.
TYPE_TO_SCREEN = {t: screen for screen, types in SCREEN_GROUPS.items() for t in types}


def screen_of(dtype):
    """The screen a decision type belongs to, or None if it is standalone (emit it on its own)."""
    return TYPE_TO_SCREEN.get(dtype)


def combine_screen(scored_decisions, top=None):
    """Merge several already-scored decisions of ONE screen into a single cross-type ranked list.

    scored_decisions: iterable of {"type": <decision type>, "options": [scored option rows]} where
      each scored option row is exactly what models.score_decision emits (key/label/available/
      raw_exploit/exploit/explore/combined). An optional "chosen" field per decision is carried through
      onto its rows (row["chosen_here"]) so a caller can mark the historically chosen option.

    Every option across every grouped decision is flattened, tagged with its source `type`, and sorted
    DESCENDING by the cross-type key = raw_exploit (the action IMPACT f(s,a)-g(s), common scale across
    types). Options with no raw prediction (a type whose model is missing) sink to the bottom rather
    than being dropped -- the hard invariant is we never silently discard a decision.

    Returns {"rows": <full ranked list>, "top": <rows[:top]>, "types": <sorted types present>}.
    """
    rows = []
    types = set()
    for sd in scored_decisions or []:
        t = sd.get("type")
        types.add(t)
        chosen = sd.get("chosen")
        for o in (sd.get("options") or []):
            r = dict(o)                       # copy: never mutate the caller's scored rows
            r["type"] = t
            if chosen is not None:
                r["chosen_here"] = (str(r.get("key")) == str(chosen))
            rows.append(r)
    # DESC by raw_exploit; None (no model) sorts last via the (is_none, -val) key.
    rows.sort(key=lambda r: (r.get("raw_exploit") is None, -(r.get("raw_exploit") or 0.0)))
    return {"rows": rows, "top": (rows[:top] if top else rows), "types": sorted(types)}
