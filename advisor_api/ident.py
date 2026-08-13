"""Turning the game's raw identifiers into words, without inventing a name table."""

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
    """`cult_of_pleasure` -> `Cult of Pleasure`."""
    parts = [p for p in str(words or "").replace("-", "_").split("_") if p]
    out = []
    for i, p in enumerate(parts):
        out.append(p.capitalize() if (i == 0 or p not in _KEEP_LOWER) else p)
    return " ".join(out)


@functools.lru_cache(maxsize=1)
def culture_names() -> dict:
    """code -> display name, derived from the harvested culture list."""
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


@functools.lru_cache(maxsize=1)
def campaign_map_names() -> dict:
    """map key -> display name, read from the roster the launcher harvests."""
    path = os.path.join(common.LAUNCHER, "startable_factions.json")
    try:
        data = json.load(io.open(path, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: str((v or {}).get("label") or k)
            for k, v in (data.get("maps") or {}).items()}


def campaign_map(key: str) -> dict:
    """`wh3_main_chaos` -> Realm of Chaos."""
    raw = str(key or "")
    if not raw:
        return {"raw": "", "label": "not recorded"}
    return {"raw": raw, "label": campaign_map_names().get(raw) or titlecase(raw)}


def split_campaign_key(campaign_key: str) -> tuple:
    """`wh2_main_def_naggarond_4f36...` -> (`wh2_main_def_naggarond`, `4f36...`)."""
    s = str(campaign_key or "")
    m = _CAMP_TAIL.search(s)
    if not m:
        return s, ""
    return s[:m.start()], s[m.start() + 1:]


def faction(key: str) -> dict:
    """The display form of a faction key."""
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
    """The display form of any other snake_case identifier."""
    raw = str(key or "")
    words = raw.replace("-", " ").replace("_", " ").strip()
    return {"raw": raw, "label": (words[:1].upper() + words[1:]) if words else raw}
