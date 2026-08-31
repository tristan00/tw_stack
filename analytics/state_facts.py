from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import store as _store

DF_STEP = 25000
ACQ_STEP = 5000
END_STEP = 50000

DF_DDL = """
CREATE TABLE IF NOT EXISTS decision_features(
  decision_id BIGINT PRIMARY KEY, campaign_id BIGINT NOT NULL,
  ts DOUBLE PRECISION, turn INTEGER,
  settlements DOUBLE PRECISION, income DOUBLE PRECISION,
  treasury DOUBLE PRECISION, armies DOUBLE PRECISION, heroes DOUBLE PRECISION,
  power_rank DOUBLE PRECISION, lord_level DOUBLE PRECISION,
  allies DOUBLE PRECISION, vassals DOUBLE PRECISION,
  is_researching SMALLINT NOT NULL DEFAULT 0,
  ll_wounded SMALLINT NOT NULL DEFAULT 0);

CREATE INDEX IF NOT EXISTS ix_df_campaign ON decision_features(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_df_turn ON decision_features(turn);

CREATE TABLE IF NOT EXISTS decision_resources(
  decision_id BIGINT NOT NULL, key TEXT NOT NULL, value DOUBLE PRECISION,
  PRIMARY KEY(decision_id, key));

CREATE INDEX IF NOT EXISTS ix_dr_key ON decision_resources(key);

CREATE TABLE IF NOT EXISTS decision_heroes(
  decision_id BIGINT NOT NULL, key TEXT NOT NULL, n DOUBLE PRECISION,
  PRIMARY KEY(decision_id, key));

CREATE INDEX IF NOT EXISTS ix_dh_key ON decision_heroes(key);
"""

ACQ_DDL = """
CREATE TABLE IF NOT EXISTS acquisitions(
  campaign_id BIGINT NOT NULL, family TEXT NOT NULL, key TEXT NOT NULL,
  ctx TEXT NOT NULL DEFAULT '', sub TEXT, kind TEXT,
  first_seen_turn INTEGER, first_seen_decision BIGINT,
  acquired_turn INTEGER, acquired_decision BIGINT,
  ranks DOUBLE PRECISION,
  PRIMARY KEY(campaign_id, family, key, ctx));

CREATE INDEX IF NOT EXISTS ix_acq_key ON acquisitions(family, key);
CREATE INDEX IF NOT EXISTS ix_acq_camp ON acquisitions(campaign_id, family);
"""

END_DDL = """
CREATE TABLE IF NOT EXISTS campaign_endings(
  campaign_key TEXT PRIMARY KEY, ts DOUBLE PRECISION, faction TEXT,
  outcome TEXT, when_text TEXT, error TEXT, verdict TEXT,
  suspicious SMALLINT NOT NULL DEFAULT 0, because TEXT);
"""


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _jload(z):
    if not z:
        return {}
    try:
        got = json.loads(z)
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


class _DecisionFeatures:
    NAME = "decision_features"
    TABLES = ("decision_features", "decision_resources", "decision_heroes")
    FORMULA_VERSION = 1
    SOURCE = "decisions"
    DEPENDS_ON = ()
    DDL = DF_DDL

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) m FROM decisions").fetchone()
        return int(row[0] or 0)

    def source_stats(self, src, hi):
        row = src.execute("SELECT COUNT(*) c, MIN(decision_id) m FROM decisions"
                          " WHERE decision_id <= %s", (hi,)).fetchone()
        return int(row[0] or 0), row[1]

    def step(self, src, an, lo, hi):
        hi2 = min(hi, lo + DF_STEP)
        feats, res, heroes = [], [], []
        for r in src.execute(
                "SELECT d.decision_id, d.campaign_id, d.ts, d.turn, d.settlements,"
                " d.income, d.power_rank, d.lord_level, d.allies, d.vassals, b.z"
                " FROM decisions d LEFT JOIN blobs b ON b.blob_id = d.campaign_blob"
                " WHERE d.decision_id > %s AND d.decision_id <= %s"
                " ORDER BY d.decision_id", (lo, hi2)):
            did = int(r["decision_id"])
            z = _jload(r["z"])
            hc = z.get("hero_type_counts") or {}
            feats.append((
                did, int(r["campaign_id"]), _f(r["ts"]),
                int(r["turn"]) if r["turn"] is not None else None,
                _f(r["settlements"]), _f(r["income"]), _f(z.get("treasury")),
                _f(z.get("armies")),
                _f(sum(_f(v) or 0.0 for v in hc.values())),
                _f(r["power_rank"]), _f(r["lord_level"]),
                _f(r["allies"]), _f(r["vassals"]),
                1 if z.get("is_researching") in (True, "True", "true") else 0,
                1 if z.get("ll_wounded") in (True, "True", "true") else 0))
            for k, v in (z.get("resources") or {}).items():
                fv = _f(v)
                if fv is not None:
                    res.append((did, str(k), fv))
            for k, v in hc.items():
                fv = _f(v)
                if fv is not None:
                    heroes.append((did, str(k), fv))
        if feats:
            _store.executemany(
                an,
                "INSERT INTO decision_features VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s,%s,%s) ON CONFLICT(decision_id) DO NOTHING", feats)
        if res:
            _store.executemany(
                an, "INSERT INTO decision_resources VALUES(%s,%s,%s)"
                " ON CONFLICT(decision_id, key) DO NOTHING", res)
        if heroes:
            _store.executemany(
                an, "INSERT INTO decision_heroes VALUES(%s,%s,%s)"
                " ON CONFLICT(decision_id, key) DO NOTHING", heroes)
        return hi2, len(feats)


def _truthy(v):
    return v in (True, "True", "true", 1, 1.0)


def _campaign_facts(z):
    out = []
    for t in z.get("tech") or []:
        if not isinstance(t, dict) or not t.get("key"):
            continue
        acquired = _truthy(t.get("researched"))
        seen = acquired or _truthy(t.get("can_research"))
        if seen:
            out.append(("research", str(t["key"]), "", None, None, acquired, None))
    for it in z.get("anc_pool") or []:
        if isinstance(it, dict) and it.get("key"):
            out.append(("items", str(it["key"]), "", None, None, False, None))
    for it in z.get("equipped_all") or []:
        if isinstance(it, dict) and it.get("key"):
            out.append(("items", str(it["key"]), "", None, None, True, None))
    return out


def _char_facts(z):
    out = []
    sub = z.get("subtype") or z.get("agent_type")
    sub = str(sub) if sub else None
    kind = "hero" if (z.get("agent_type") or _truthy(z.get("is_agent"))) else "lord"
    for s in z.get("skills") or []:
        if not isinstance(s, dict) or not s.get("key"):
            continue
        lv = _f(s.get("level")) or 0.0
        acquired = lv > 0
        seen = acquired or s.get("status") == "active"
        if seen:
            out.append(("skills", str(s["key"]), "@cqi", sub, kind, acquired,
                        lv if acquired else None))
    for it in z.get("equipped") or []:
        if isinstance(it, dict) and it.get("key"):
            out.append(("items", str(it["key"]), "", None, kind, True, None))
    return out


def _province_facts(z):
    out = []
    region = str(z.get("region") or "")
    for key in (z.get("built") or {}).values():
        if key:
            out.append(("building", str(key), region, None, None, True, None))
    now = z.get("building_now") or {}
    for key in (now.values() if isinstance(now, dict) else []):
        if key:
            out.append(("building", str(key), region, None, None, True, None))
    for b in z.get("buildable") or []:
        if isinstance(b, dict) and b.get("key") and _truthy(b.get("active")):
            out.append(("building", str(b["key"]), region, None, None, False, None))
    return out


def _world_facts(z):
    out = []
    for s in z.get("settlements") or []:
        key = s.get("region") if isinstance(s, dict) else s
        if key:
            out.append(("settlement", str(key), "", None, None, True, None))
    return out


_FACT_MEMO: dict = {}
_ENTITY_KINDS = ("campaign", "lord", "hero", "province")
_KIND_PARSE = {"campaign": _campaign_facts, "lord": _char_facts,
               "hero": _char_facts, "province": _province_facts,
               "world": _world_facts}


def _facts_for(blob_id, kind, z_text):
    memo_key = (blob_id, kind)
    hit = _FACT_MEMO.get(memo_key)
    if hit is not None:
        return hit
    got = _KIND_PARSE[kind](_jload(z_text))
    if len(_FACT_MEMO) > 200000:
        _FACT_MEMO.clear()
    _FACT_MEMO[memo_key] = got
    return got


class _Acquisitions:
    NAME = "acquisitions"
    TABLES = ("acquisitions",)
    FORMULA_VERSION = 2
    SOURCE = "decisions"
    DEPENDS_ON = ()
    DDL = ACQ_DDL

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) m FROM decisions").fetchone()
        return int(row[0] or 0)

    def source_stats(self, src, hi):
        row = src.execute("SELECT MIN(decision_id) m FROM decisions").fetchone()
        return None, row[0]

    def step(self, src, an, lo, hi):
        hi2 = min(hi, lo + ACQ_STEP)
        decs = {int(r["decision_id"]): (int(r["campaign_id"]),
                                        int(r["turn"]) if r["turn"] is not None
                                        else None)
                for r in src.execute(
                    "SELECT decision_id, campaign_id, turn FROM decisions"
                    " WHERE decision_id > %s AND decision_id <= %s", (lo, hi2))}
        if not decs:
            return hi2, 0
        agg: dict = {}

        def fold(did, facts, cqi):
            cid, turn = decs[did]
            for family, key, ctx, sub, kind, acquired, ranks in facts:
                ctx2 = str(cqi) if ctx == "@cqi" else ctx
                k = (cid, family, key, ctx2)
                e = agg.get(k)
                if e is None:
                    e = agg[k] = {"sub": sub, "kind": kind, "st": turn, "sd": did,
                                  "at": None, "ad": None, "rk": None}
                if e["sub"] is None and sub is not None:
                    e["sub"] = sub
                if e["kind"] is None and kind is not None:
                    e["kind"] = kind
                if acquired:
                    if e["at"] is None or (did < (e["ad"] or did + 1)):
                        e["at"], e["ad"] = turn, did
                    if ranks is not None and (e["rk"] is None or ranks > e["rk"]):
                        e["rk"] = ranks

        for r in src.execute(
                "SELECT e.decision_id, e.context_kind, e.context_id,"
                " e.features_blob, b.z"
                " FROM entities e JOIN blobs b ON b.blob_id = e.features_blob"
                " WHERE e.decision_id > %s AND e.decision_id <= %s"
                " AND e.context_kind = ANY(%s)"
                " ORDER BY e.decision_id",
                (lo, hi2, list(_ENTITY_KINDS))):
            did = int(r["decision_id"])
            if did not in decs:
                continue
            facts = _facts_for(int(r["features_blob"]), str(r["context_kind"]),
                               r["z"])
            if facts:
                fold(did, facts, r["context_id"])
        for r in src.execute(
                "SELECT d.decision_id, d.world_blob, b.z FROM decisions d"
                " JOIN blobs b ON b.blob_id = d.world_blob"
                " WHERE d.decision_id > %s AND d.decision_id <= %s"
                " ORDER BY d.decision_id", (lo, hi2)):
            did = int(r["decision_id"])
            facts = _facts_for(int(r["world_blob"]), "world", r["z"])
            if facts:
                fold(did, facts, "")
        batch = [(cid, family, key, ctx, e["sub"], e["kind"], e["st"], e["sd"],
                  e["at"], e["ad"], e["rk"])
                 for (cid, family, key, ctx), e in agg.items()]
        if batch:
            _store.executemany(
                an,
                "INSERT INTO acquisitions VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT(campaign_id, family, key, ctx) DO UPDATE SET"
                " sub = COALESCE(acquisitions.sub, excluded.sub),"
                " kind = COALESCE(acquisitions.kind, excluded.kind),"
                " acquired_turn = COALESCE(acquisitions.acquired_turn,"
                "                          excluded.acquired_turn),"
                " acquired_decision = COALESCE(acquisitions.acquired_decision,"
                "                              excluded.acquired_decision),"
                " ranks = GREATEST(COALESCE(acquisitions.ranks, 0),"
                "                  COALESCE(excluded.ranks, 0))", batch)
        return hi2, len(batch)


def _because(got):
    g = got.get("growth") or {}
    outcome = str(got.get("outcome") or "")
    if g.get("reason") == "legendary_lord_wounded":
        return ("growth gate: legendary lord wounded at turn %s -- an automatic "
                "stop that measures no growth at all" % g.get("turn"))
    mets = g.get("metrics") or {}
    if mets:
        parts = "; ".join(
            "%s %g -> %g over %s turns"
            % (m.get("label"), _f(m.get("then")) or 0.0, _f(m.get("now")) or 0.0,
               m.get("window"))
            for m in mets.values() if m.get("then") is not None)
        if parts:
            return ("growth gate at turn %s: %s -- needed +%s on either"
                    % (g.get("turn"), parts, g.get("min_gain")))
    if outcome == "defeated":
        return "the faction was destroyed"
    if outcome in ("stuck", "error", "unhandled_screen"):
        return str(got.get("error") or outcome)[:200]
    return None


class _CampaignEndings:
    NAME = "campaign_endings"
    TABLES = ("campaign_endings",)
    FORMULA_VERSION = 2
    SOURCE = "decisions"
    DEPENDS_ON = ()
    DDL = END_DDL

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(postmortem_id) m FROM postmortems").fetchone()
        return int(row[0] or 0)

    def source_stats(self, src, hi):
        row = src.execute("SELECT MIN(postmortem_id) m FROM postmortems").fetchone()
        return None, row[0]

    def step(self, src, an, lo, hi):
        hi2 = min(hi, lo + END_STEP)
        batch = []
        for r in src.execute(
                "SELECT postmortem_id, campaign_key, ts, payload FROM postmortems"
                " WHERE postmortem_id > %s AND postmortem_id <= %s"
                " ORDER BY postmortem_id", (lo, hi2)):
            got = _jload(r["payload"])
            ck = got.get("campaign_key") or r["campaign_key"]
            if not ck:
                continue
            verdict = str((got.get("plausibility") or {}).get("verdict") or "")
            suspicious = 1 if ("harness_failure_likely" in verdict
                               or "ambiguous" in verdict) else 0
            batch.append((str(ck), _f(got.get("ts")) or _f(r["ts"]),
                          got.get("faction"),
                          got.get("outcome"), got.get("when"), got.get("error"),
                          verdict or None, suspicious, _because(got)))
        if batch:
            _store.executemany(
                an,
                "INSERT INTO campaign_endings VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT(campaign_key) DO UPDATE SET"
                " ts=excluded.ts, faction=excluded.faction,"
                " outcome=excluded.outcome,"
                " when_text=excluded.when_text, error=excluded.error,"
                " verdict=excluded.verdict, suspicious=excluded.suspicious,"
                " because=excluded.because",
                batch)
        return hi2, len(batch)


DECISION_FEATURES = _DecisionFeatures()
ACQUISITIONS = _Acquisitions()
CAMPAIGN_ENDINGS = _CampaignEndings()

TENANTS = (DECISION_FEATURES, ACQUISITIONS, CAMPAIGN_ENDINGS)
