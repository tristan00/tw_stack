"""Turning the game's raw identifiers into words, without inventing a name table.

Every id the game emits is a snake_case key with a fixed shape:

    wh2_main_def_cult_of_pleasure
    ^^^ ^^^^ ^^^ ^^^^^^^^^^^^^^^^
    game pack culture  the actual name

so the display name is derivable, and the culture name is derivable too -- the culture
keys in launcher/startable_factions.json have the SAME shape, so `wh2_main_def_dark_elves`
yields the mapping def -> "Dark Elves" on its own. Nothing here is typed by hand, which
matters because the faction list is regenerated from the game after every DLC and a
hand-written table would silently go stale against it.

The raw id is never discarded, only demoted: `Ident.raw` travels beside `Ident.label` in
every response, because the raw id is what gets grepped, logged and pasted into a query.
"""

from __future__ import annotations

import functools
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common

# wh_, wh2_, wh3_ -- which game the content shipped with. Never meaningful to a reader.
_GAME = re.compile(r"^wh\d?_")
# main_, dlc09_, dlc27_, pro01_, twa01_ -- which pack. Also never meaningful.
_PACK = re.compile(r"^(main|dlc\d+|pro\d+|twa\d+|sf\d+)_")
# the campaign_key tail: a faction key with a 16-hex-digit campaign uuid glued on.
_CAMP_TAIL = re.compile(r"_[0-9a-f]{8,}$")

# Words that must not be title-cased into something that reads wrong.
_KEEP_LOWER = {"of", "the", "and", "de", "du", "von", "der"}


def strip_affixes(key: str) -> str:
    """`wh2_main_def_cult_of_pleasure` -> `def_cult_of_pleasure`."""
    s = _GAME.sub("", str(key or ""))
    return _PACK.sub("", s)


def titlecase(words: str) -> str:
    """`cult_of_pleasure` -> `Cult of Pleasure`.

    Small words stay lowercase unless they lead, so `cult_of_pleasure` does not become
    the slightly wrong `Cult Of Pleasure`.
    """
    parts = [p for p in str(words or "").replace("-", "_").split("_") if p]
    out = []
    for i, p in enumerate(parts):
        out.append(p.capitalize() if (i == 0 or p not in _KEEP_LOWER) else p)
    return " ".join(out)


@functools.lru_cache(maxsize=1)
def culture_names() -> dict:
    """code -> display name, derived from the harvested culture list.

    A culture key has the same shape as a faction key, so `wh2_main_def_dark_elves`
    decomposes into the code `def` and the name `Dark Elves`. If the file is missing or
    unreadable the map is simply empty and callers fall back to showing no culture --
    a missing culture is cosmetic, and guessing one would not be.
    """
    path = os.path.join(common.LAUNCHER, "startable_factions.json")
    try:
        # utf-8-sig: the file is written by the game-side harvester and carries a BOM.
        data = json.load(io.open(path, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {}
    for key in data.get("cultures") or []:
        body = strip_affixes(key)
        code, _, rest = body.partition("_")
        if code and rest:
            out[code] = titlecase(rest)
    return out


def split_campaign_key(campaign_key: str) -> tuple:
    """`wh2_main_def_naggarond_4f36...` -> (`wh2_main_def_naggarond`, `4f36...`).

    A campaign key is a faction key with a run-unique hex tail. Splitting them is what
    lets one campaign be told from another campaign of the same faction without showing
    the reader 16 hex digits.
    """
    s = str(campaign_key or "")
    m = _CAMP_TAIL.search(s)
    if not m:
        return s, ""
    return s[:m.start()], s[m.start() + 1:]


def faction(key: str) -> dict:
    """The display form of a faction key.

    Returns label/culture/raw rather than a pre-joined string so the client decides how
    to lay it out -- and so the raw id stays a separate field that can be copied whole.
    """
    raw = str(key or "")
    body = strip_affixes(raw)
    code, _, rest = body.partition("_")
    names = culture_names()
    if code in names and rest:
        label = titlecase(rest)
        culture = names[code]
        # A culture's namesake faction decodes to its own culture name -- Bretonnia is the
        # Bretonnia faction of the Bretonnia culture -- and printing both reads as a
        # stutter rather than as information.
        return {"raw": raw, "label": label,
                "culture": None if culture.lower() == label.lower() else culture}
    # Unknown culture code: the whole remainder is the name. Better a correct name with
    # no culture than a culture invented from a three-letter prefix we do not recognise.
    return {"raw": raw, "label": titlecase(body) or raw, "culture": None}


def campaign(campaign_key: str) -> dict:
    """The display form of a campaign key: its faction, plus a short run-unique tag."""
    fkey, tail = split_campaign_key(campaign_key)
    out = faction(fkey)
    out["raw"] = str(campaign_key or "")
    out["tag"] = tail[:6]
    return out


def phrase(key: str) -> dict:
    """The display form of any other snake_case identifier.

    Outcome rules (`abandoned_on_the_growth_bar`), refusals (`command_silently_refused`),
    screen kinds (`battle_results`) and action types (`hero_action`) are all read by a
    person and all arrive as snake_case. They get sentence case, not title case: they are
    statements, not names.
    """
    raw = str(key or "")
    words = raw.replace("-", " ").replace("_", " ").strip()
    return {"raw": raw, "label": (words[:1].upper() + words[1:]) if words else raw}
