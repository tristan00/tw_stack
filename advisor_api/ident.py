
from __future__ import annotations

import functools
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common

_GAME = re.compile(r"^wh\d?_")
_PACK = re.compile(r"^(main|dlc\d+|pro\d+|twa\d+|sf\d+)_")
_CAMP_TAIL = re.compile(r"(?:_[0-9a-f]{8,})+$")

_KEEP_LOWER = {"of", "the", "and", "de", "du", "von", "der"}


def strip_affixes(key: str) -> str:
    s = _GAME.sub("", str(key or ""))
    return _PACK.sub("", s)


def titlecase(words: str) -> str:
    parts = [p for p in str(words or "").replace("-", "_").split("_") if p]
    out = []
    for i, p in enumerate(parts):
        out.append(p.capitalize() if (i == 0 or p not in _KEEP_LOWER) else p)
    return " ".join(out)


@functools.lru_cache(maxsize=1)
def culture_names() -> dict:
    path = os.path.join(common.LAUNCHER, "startable_factions.json")
    try:
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
    path = os.path.join(common.LAUNCHER, "startable_factions.json")
    try:
        data = json.load(io.open(path, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: str((v or {}).get("label") or k)
            for k, v in (data.get("maps") or {}).items()}


def campaign_map(key: str) -> dict:
    raw = str(key or "")
    if not raw:
        return {"raw": "", "label": "not recorded"}
    return {"raw": raw, "label": campaign_map_names().get(raw) or titlecase(raw)}


def split_campaign_key(campaign_key: str) -> tuple:
    s = str(campaign_key or "")
    m = _CAMP_TAIL.search(s)
    if not m:
        return s, ""
    return s[:m.start()], s[m.start() + 1:]


def faction(key: str) -> dict:
    raw = str(key or "")
    body = strip_affixes(raw)
    code, _, rest = body.partition("_")
    names = culture_names()
    if code in names and rest:
        label = titlecase(rest)
        culture = names[code]
        return {"raw": raw, "label": label,
                "culture": None if culture.lower() == label.lower() else culture}
    return {"raw": raw, "label": titlecase(body) or raw, "culture": None}


def campaign(campaign_key: str) -> dict:
    fkey, tail = split_campaign_key(campaign_key)
    out = faction(fkey)
    out["raw"] = str(campaign_key or "")
    out["tag"] = tail[:6]
    return out


def phrase(key: str) -> dict:
    raw = str(key or "")
    words = raw.replace("-", " ").replace("_", " ").strip()
    return {"raw": raw, "label": (words[:1].upper() + words[1:]) if words else raw}
