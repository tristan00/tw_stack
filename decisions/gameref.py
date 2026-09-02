from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from decisions import pg

SCHEMA = "reference"

DDL = """
CREATE SCHEMA IF NOT EXISTS reference;

CREATE TABLE IF NOT EXISTS reference.ref_region(
  region TEXT PRIMARY KEY,
  x DOUBLE PRECISION, y DOUBLE PRECISION,
  province TEXT, is_capital SMALLINT, climate TEXT,
  adjacent TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS reference.ref_tech(
  faction TEXT NOT NULL, tech TEXT NOT NULL,
  tier INTEGER, indent INTEGER, duration INTEGER,
  cost INTEGER, research_points INTEGER, required_parents INTEGER,
  PRIMARY KEY(faction, tech));

CREATE TABLE IF NOT EXISTS reference.ref_tech_effect(
  faction TEXT NOT NULL, tech TEXT NOT NULL, effect TEXT NOT NULL,
  value DOUBLE PRECISION, positive SMALLINT, scope TEXT);

CREATE TABLE IF NOT EXISTS reference.ref_tech_parent(
  faction TEXT NOT NULL, tech TEXT NOT NULL, parent TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS reference.ref_skill(
  subtype TEXT NOT NULL, skill TEXT NOT NULL,
  tier INTEGER, indent INTEGER, total_levels INTEGER, background SMALLINT,
  parents TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(subtype, skill));

CREATE TABLE IF NOT EXISTS reference.ref_skill_level(
  subtype TEXT NOT NULL, skill TEXT NOT NULL, level INTEGER NOT NULL,
  rank_required INTEGER,
  PRIMARY KEY(subtype, skill, level));

CREATE TABLE IF NOT EXISTS reference.ref_skill_effect(
  subtype TEXT NOT NULL, skill TEXT NOT NULL, level INTEGER NOT NULL,
  effect TEXT NOT NULL, value DOUBLE PRECISION, positive SMALLINT);

CREATE INDEX IF NOT EXISTS ix_ref_tech_effect ON reference.ref_tech_effect(faction, tech);
CREATE INDEX IF NOT EXISTS ix_ref_tech_parent ON reference.ref_tech_parent(faction, tech);
CREATE INDEX IF NOT EXISTS ix_ref_skill_effect ON reference.ref_skill_effect(subtype, skill, level);
CREATE INDEX IF NOT EXISTS ix_ref_region_province ON reference.ref_region(province);
"""


def ensure(con=None):
    own = con is None
    con = con or pg.connect(autocommit=True)
    for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
        con.execute(stmt)
    if own:
        con.close()


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write(ref, faction, con=None):
    own = con is None
    con = con or pg.connect(autocommit=True)
    ensure(con)
    cur = con.cursor()
    n = {}

    regions = ref.get("regions") or []
    if regions:
        cur.executemany(
            "INSERT INTO reference.ref_region VALUES(%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (region) DO UPDATE SET x=excluded.x, y=excluded.y,"
            " province=excluded.province, is_capital=excluded.is_capital,"
            " climate=excluded.climate, adjacent=excluded.adjacent",
            [(r["region"], _f(r.get("x")), _f(r.get("y")), r.get("province"),
              1 if r.get("is_capital") else 0, r.get("climate"),
              ",".join(r.get("adjacent") or ())) for r in regions])
        n["ref_region"] = len(regions)

    techs = ref.get("tech") or []
    if techs:
        cur.execute("DELETE FROM reference.ref_tech WHERE faction=%s", (faction,))
        cur.execute("DELETE FROM reference.ref_tech_effect WHERE faction=%s", (faction,))
        cur.execute("DELETE FROM reference.ref_tech_parent WHERE faction=%s", (faction,))
        cur.executemany(
            "INSERT INTO reference.ref_tech VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            [(faction, r["tech"], _i(r.get("tier")), _i(r.get("indent")),
              _i(r.get("duration")), _i(r.get("cost")), _i(r.get("research_points")),
              _i(r.get("required_parents"))) for r in techs])
        n["ref_tech"] = len(techs)
        fx = ref.get("tech_effect") or []
        if fx:
            cur.executemany(
                "INSERT INTO reference.ref_tech_effect VALUES(%s,%s,%s,%s,%s,%s)",
                [(faction, r["tech"], r["effect"], _f(r.get("value")),
                  1 if r.get("positive") else 0, r.get("scope")) for r in fx])
            n["ref_tech_effect"] = len(fx)
        pr = ref.get("tech_parent") or []
        if pr:
            cur.executemany(
                "INSERT INTO reference.ref_tech_parent VALUES(%s,%s,%s)",
                [(faction, r["tech"], r["parent"]) for r in pr])
            n["ref_tech_parent"] = len(pr)

    subtypes = sorted({r.get("subtype") for r in (ref.get("skill") or []) if r.get("subtype")})
    for st in subtypes:
        cur.execute("DELETE FROM reference.ref_skill WHERE subtype=%s", (st,))
        cur.execute("DELETE FROM reference.ref_skill_level WHERE subtype=%s", (st,))
        cur.execute("DELETE FROM reference.ref_skill_effect WHERE subtype=%s", (st,))
    rows = [r for r in (ref.get("skill") or []) if r.get("subtype")]
    if rows:
        cur.executemany(
            "INSERT INTO reference.ref_skill VALUES(%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (subtype, skill) DO NOTHING",
            [(r["subtype"], r["skill"], _i(r.get("tier")), _i(r.get("indent")),
              _i(r.get("total_levels")), 1 if r.get("background") else 0,
              ",".join(r.get("parents") or ())) for r in rows])
        n["ref_skill"] = len(rows)
    rows = [r for r in (ref.get("skill_level") or []) if r.get("subtype")]
    if rows:
        cur.executemany(
            "INSERT INTO reference.ref_skill_level VALUES(%s,%s,%s,%s)"
            " ON CONFLICT (subtype, skill, level) DO NOTHING",
            [(r["subtype"], r["skill"], _i(r.get("level")) or 0,
              _i(r.get("rank_required"))) for r in rows])
        n["ref_skill_level"] = len(rows)
    rows = [r for r in (ref.get("skill_effect") or []) if r.get("subtype")]
    if rows:
        cur.executemany(
            "INSERT INTO reference.ref_skill_effect VALUES(%s,%s,%s,%s,%s,%s)",
            [(r["subtype"], r["skill"], _i(r.get("level")) or 0, r["effect"],
              _f(r.get("value")), 1 if r.get("positive") else 0) for r in rows])
        n["ref_skill_effect"] = len(rows)

    if own:
        con.close()
    return n


def have_subtypes(subtypes, con=None):
    own = con is None
    con = con or pg.connect(autocommit=True)
    ensure(con)
    cur = con.cursor()
    cur.execute("SELECT DISTINCT subtype FROM reference.ref_skill WHERE subtype = ANY(%s)",
                (list(subtypes),))
    got = {r[0] for r in cur.fetchall()}
    if own:
        con.close()
    return got


def have_faction_tech(faction, con=None):
    own = con is None
    con = con or pg.connect(autocommit=True)
    ensure(con)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM reference.ref_tech WHERE faction=%s", (faction,))
    n = cur.fetchone()[0]
    if own:
        con.close()
    return n > 0


def region_count(con=None):
    own = con is None
    con = con or pg.connect(autocommit=True)
    ensure(con)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM reference.ref_region")
    n = cur.fetchone()[0]
    if own:
        con.close()
    return n
