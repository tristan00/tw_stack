from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import store as _store

STEP = 5000

NAME = "item_event"
FORMULA_VERSION = 3
DEPENDS_ON = ()
TABLES = ("item_event",)


def log(msg):
    sys.stderr.write("%.3f  itemev %s\n" % (time.time(), msg))


class _ItemEvents:
    NAME = NAME
    FORMULA_VERSION = FORMULA_VERSION
    DEPENDS_ON = DEPENDS_ON
    TABLES = TABLES

    def __init__(self):
        self._sets: dict = {}

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) m FROM corpus.decision").fetchone()
        return int(row[0] or 0)

    def _members(self, src, set_ids):
        want = sorted({s for s in set_ids if s is not None
                       and s not in self._sets})
        if want:
            if len(self._sets) > 200000:
                self._sets.clear()
            for s in want:
                self._sets[s] = set()
            for set_id, anc_id in src.execute(
                    "SELECT set_id, ancillary_id FROM corpus.item_slot_set_member"
                    " WHERE set_id = ANY(%s) AND ancillary_id IS NOT NULL", (want,)):
                self._sets[set_id].add(anc_id)
        return {s: self._sets.get(s) or set() for s in set_ids if s is not None}

    def step(self, src, an, lo, hi):
        watermark = lo
        written = 0
        while watermark < hi:
            watermark, n = self._step_chunk(src, an, watermark, hi)
            written += n
        return watermark, written

    def _step_chunk(self, src, an, lo, hi):
        t0 = time.time()
        hi2 = min(hi, lo + STEP)
        rows = src.execute(
            "SELECT cs.snapshot_id, cs.character_id, ch.campaign_id, s.turn,"
            " cs.equipped_set_id, prev.equipped_set_id AS prev_set"
            " FROM corpus.char_state cs"
            " JOIN corpus.character ch USING (character_id)"
            " JOIN corpus.snapshot s ON s.snapshot_id = cs.snapshot_id"
            " JOIN corpus.decision d ON d.decision_id = cs.snapshot_id"
            " LEFT JOIN LATERAL (SELECT p.equipped_set_id FROM corpus.char_state p"
            "  JOIN corpus.decision pd ON pd.decision_id = p.snapshot_id"
            "  WHERE p.character_id = cs.character_id"
            "  AND p.snapshot_id < cs.snapshot_id"
            "  ORDER BY p.snapshot_id DESC LIMIT 1) prev ON true"
            " WHERE cs.snapshot_id > %s AND cs.snapshot_id <= %s"
            " AND prev.equipped_set_id IS DISTINCT FROM cs.equipped_set_id"
            " ORDER BY cs.snapshot_id", (lo, hi2)).fetchall()
        members = self._members(
            src, [r["equipped_set_id"] for r in rows]
            + [r["prev_set"] for r in rows])
        events = []
        for r in rows:
            now = members.get(r["equipped_set_id"]) or set()
            was = (members.get(r["prev_set"]) or set()
                   if r["prev_set"] is not None else set())
            for anc_id in sorted(now - was):
                events.append((r["campaign_id"], r["character_id"], anc_id, "on",
                               r["snapshot_id"], r["turn"]))
            for anc_id in sorted(was - now):
                events.append((r["campaign_id"], r["character_id"], anc_id, "off",
                               r["snapshot_id"], r["turn"]))
        if events:
            _store.executemany(
                an,
                "INSERT INTO item_event(campaign_id, character_id, ancillary_id,"
                " kind, snapshot_id, turn) VALUES(%s,%s,%s,%s,%s,%s)", events)
        log("step %d..%d exit %.0f ms events=%d"
            % (lo, hi2, (time.time() - t0) * 1000, len(events)))
        return hi2, len(events)


ITEM_EVENTS = _ItemEvents()

TENANTS = (ITEM_EVENTS,)
