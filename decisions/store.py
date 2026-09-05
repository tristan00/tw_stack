from __future__ import annotations

import sys
import time

import psycopg

from decisions import hydrate, pg


class IncompatibleStore(RuntimeError):
    pass


def log(msg):
    sys.stderr.write('%.3f  store.%s\n' % (time.time(), msg))


def timed(name):
    def wrap(fn):
        def call(self, *a, **kw):
            t0 = time.time()
            log('%s enter' % name)
            try:
                return fn(self, *a, **kw)
            finally:
                log('%s exit %.1f ms' % (name, (time.time() - t0) * 1000))
        return call
    return wrap


class _SnapshotRead:

    def __init__(self, con):
        self.con = con
        self.entered = False

    def __enter__(self):
        try:
            self.con.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            self.entered = True
        except psycopg.Error as e:
            sys.stderr.write("store: could not open a read snapshot (%s) -- reads may "
                             "be torn\n" % repr(e)[:100])
        return self

    def __exit__(self, *exc):
        if self.entered:
            try:
                self.con.execute("COMMIT")
            except psycopg.Error:
                pass
        return False


_SKIP_REFUSALS = ('awaiting_execution', 'campaign_died')


class DecisionStore:

    def __init__(self, run_dir=None, readonly=True):
        try:
            self.con = pg.connect(app_name='tw-facade', autocommit=True,
                                  readonly=readonly, search_path=pg.CORPUS_PATH)
        except psycopg.OperationalError as e:
            raise IncompatibleStore('database unreachable: %s' % repr(e)[:120])
        if self.con.execute(
                "SELECT to_regclass('corpus.snapshot')").fetchone()[0] is None:
            self.con.close()
            raise IncompatibleStore('database %s has no corpus schema' % pg.DB)
        self.readonly = readonly

    def snapshot_read(self):
        return _SnapshotRead(self.con)

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass

    def _kind_id(self, domain, key):
        return self.con.execute(
            "SELECT enum_id FROM dict.enum WHERE domain = %s AND key = %s",
            (domain, key)).fetchone()[0]

    def _skip_refusal_ids(self):
        return [r[0] for r in self.con.execute(
            "SELECT enum_id FROM dict.enum WHERE domain = 'refusal' AND key = ANY(%s)",
            (list(_SKIP_REFUSALS),))]

    def max_decision_id(self):
        r = self.con.execute(
            "SELECT MAX(decision_id) FROM corpus.decision").fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    def window_floor(self, n):
        if not n or int(n) <= 0:
            return None
        r = self.con.execute(
            "SELECT MIN(first_snapshot_id) FROM"
            " (SELECT first_snapshot_id FROM corpus.campaign"
            " WHERE first_snapshot_id IS NOT NULL"
            " ORDER BY first_snapshot_id DESC LIMIT %s) w", (int(n),)).fetchone()
        return int(r[0]) if r and r[0] is not None else None

    def window_keys(self, n):
        if not n or int(n) <= 0:
            return None
        return {row[0] for row in self.con.execute(
            "SELECT campaign_key FROM corpus.campaign"
            " WHERE first_snapshot_id IS NOT NULL"
            " ORDER BY first_snapshot_id DESC LIMIT %s", (int(n),))}

    def read_decision(self, decision_id):
        return hydrate.record(self.con, decision_id)

    def stored_offers(self, decision_id):
        return [{'offer_seq': seq, 'entity_seq': eseq, 'action_type': at, 'key': ak,
                 'slot_index': slot, 'score': score, 'exploit': exploit}
                for seq, eseq, at, ak, slot, score, exploit in self.con.execute(
                    "SELECT o.offer_seq, o.entity_seq, ty.key, a.action_key,"
                    " o.slot_index, o.score, o.exploit FROM corpus.offer o"
                    " JOIN dict.action a ON a.action_id = o.action_id"
                    " JOIN dict.action_type ty ON ty.id = a.action_type_id"
                    " WHERE o.decision_id = %s ORDER BY o.offer_seq", (decision_id,))]

    def attach_offers(self, record):
        return hydrate.offers(self.con, record)

    def _taken_sql(self, extra_where, args):
        skip = self._skip_refusal_ids()
        return ("SELECT t.decision_id, ek.key, ch.cqi, dr.key, df.key,"
                " ty.key, a.action_key, t.counted, t.ts, t.entity_seq, c.campaign_key"
                " FROM corpus.taken t"
                " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
                " JOIN dict.faction df ON df.id = c.faction_id"
                " JOIN dict.action a ON a.action_id = t.action_id"
                " JOIN dict.action_type ty ON ty.id = a.action_type_id"
                " LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = t.decision_id"
                " AND se.entity_seq = t.entity_seq"
                " LEFT JOIN dict.enum ek ON ek.enum_id = se.kind_id"
                " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
                " LEFT JOIN dict.region dr ON dr.id = se.region_id"
                " WHERE (t.refusal_id IS NULL OR t.refusal_id != ALL(%s))"
                + extra_where + " ORDER BY t.decision_id"), [skip] + args

    @staticmethod
    def _identity(kind, cqi, region, faction, at, ak):
        if kind in ('lord', 'hero'):
            cid = str(cqi)
        elif kind == 'province':
            cid = region
        elif kind == 'campaign':
            cid = faction
        else:
            kind, cid = 'campaign', 'campaign'
        return (kind, str(cid), at, str(ak))

    @timed('taken_map')
    def taken_map(self, confirmed_only=False, min_decision=None):
        sql, args = self._taken_sql(" AND t.decision_id >= %s",
                                    [int(min_decision or 0)])
        out = {}
        for did, kind, cqi, region, faction, at, ak, counted, ts, eseq, ckey \
                in self.con.execute(sql, tuple(args)):
            if confirmed_only and not counted:
                continue
            out[did] = (self._identity(kind, cqi, region, faction, at, ak),
                        bool(counted))
        return out

    @timed('action_sequence')
    def action_sequence(self, min_decision=None):
        sql, args = self._taken_sql(
            " AND ty.key != 'noop' AND t.decision_id >= %s", [int(min_decision or 0)])
        return [(ckey, ts, at) for did, kind, cqi, region, faction, at, ak, counted,
                ts, eseq, ckey in self.con.execute(sql, tuple(args))]

    @timed('labelled_decisions')
    def labelled_decisions(self, confirmed_only=False, after=None, before=None):
        rng, args = "", []
        if after is not None:
            rng += " AND t.decision_id > %s"
            args.append(int(after))
        if before is not None:
            rng += " AND t.decision_id <= %s"
            args.append(int(before))
        sql, args = self._taken_sql(rng, args)
        out = []
        for did, kind, cqi, region, faction, at, ak, counted, ts, eseq, ckey \
                in self.con.execute(sql, tuple(args)):
            if confirmed_only and not counted:
                continue
            rec = hydrate.record(self.con, did)
            hydrate.offers(self.con, rec)
            out.append((rec, self._identity(kind, cqi, region, faction, at, ak),
                        bool(counted)))
        return out

    @timed('taken_rows')
    def taken_rows(self, min_decision=None):
        sql, args = self._taken_sql(" AND t.decision_id >= %s",
                                    [int(min_decision or 0)])
        rows = self.con.execute(sql, tuple(args)).fetchall()
        for did, kind, cqi, region, faction, at, ak, counted, ts, eseq, ckey in rows:
            rec = hydrate.record(self.con, did)
            hydrate.attach_taken(self.con, rec, eseq, at, ak)
            yield (rec, self._identity(kind, cqi, region, faction, at, ak),
                   bool(counted))

    @timed('campaign_snapshots')
    def campaign_snapshots(self, min_decision=None):
        dc = hydrate._dicts(self.con)
        types = hydrate.legacy_types()
        out = []
        for did, ts, ckey in self.con.execute(
                "SELECT d.decision_id, s.ts, c.campaign_key FROM corpus.decision d"
                " JOIN corpus.snapshot s ON s.snapshot_id = d.decision_id"
                " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
                " WHERE d.decision_id >= %s ORDER BY d.decision_id",
                (int(min_decision or 0),)).fetchall():
            camp = hydrate._campaign_dict(self.con, dc, did, ckey) or {}
            world = hydrate._world_dict(self.con, dc, did)
            out.append((ckey, ts or 0.0,
                        hydrate.canon.legacy_view(camp, types['CB']),
                        hydrate.canon.legacy_view(world, types['WB'])))
        return out

    @timed('target_series')
    def target_series(self):
        out = {}
        for camp, turn, inc, setl, allies, vass, rank, lvl in self.con.execute(
                "SELECT c.campaign_key, o.turn, o.income, o.settlements, o.allies,"
                " o.vassals, o.power_rank, o.lord_level FROM corpus.turn_open o"
                " JOIN corpus.campaign c USING (campaign_id)"):
            out.setdefault(camp, {})[int(turn)] = {
                "income": inc or 0.0, "settlements": setl or 0.0,
                "power_rank": (rank if rank is not None else -50.0),
                "allies": allies or 0.0, "vassals": vass or 0.0,
                "lord_level": lvl or 0.0}
        return out

    @timed('interrupt_rows')
    def interrupt_rows(self, campaign_keys=None):
        where, args = "", []
        if campaign_keys is not None:
            where = " AND c.campaign_key = ANY(%s)"
            args.append(sorted(campaign_keys))
        heads = self.con.execute(
            "SELECT i.interrupt_id, s.ts, c.campaign_key, s.turn, ik.key, sa.key,"
            " i.chosen, i.answer, i.executed, i.confirmed, i.counted, rf.key,"
            " dd.key, di.key, i.root_context, ps.turn"
            " FROM corpus.interrupt i"
            " JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id"
            " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
            " JOIN dict.enum ik ON ik.enum_id = i.kind_id"
            " JOIN dict.enum sa ON sa.enum_id = i.state_at_id"
            " LEFT JOIN dict.enum rf ON rf.enum_id = i.refusal_id"
            " LEFT JOIN dict.dilemma dd ON dd.id = i.dilemma_id"
            " LEFT JOIN dict.incident di ON di.id = i.incident_id"
            " LEFT JOIN corpus.snapshot ps ON ps.snapshot_id = i.prev_decision_id"
            " WHERE TRUE" + where + " ORDER BY i.interrupt_id",
            tuple(args)).fetchall()
        ids = [h[0] for h in heads]
        opts = {}
        for iid, key, text, oid, answer, payload, exploit, score, gnn \
                in self.con.execute(
                    "SELECT interrupt_id, option_key, text, option_id, answer,"
                    " payload, exploit, score, gnn FROM corpus.interrupt_option"
                    " WHERE interrupt_id = ANY(%s) ORDER BY interrupt_id, ord",
                    (ids,)):
            opts.setdefault(iid, {})[key] = {
                'text': text, 'option_id': oid, 'answer': answer,
                'payload': list(payload or []), 'exploit': exploit, 'score': score,
                'gnn': gnn}
        panels = {}
        for iid, ally, enemy, na, ne, rs, rt, cs, ct in self.con.execute(
                "SELECT interrupt_id, ally_cqi, enemy_cqi, n_ally_armies,"
                " n_enemy_armies, result_state, result_text, casualties_state,"
                " casualties_text FROM corpus.interrupt_battle_panel"
                " WHERE interrupt_id = ANY(%s)", (ids,)):
            p = {'result': {'state': rs, 'text': rt},
                 'casualties': {'state': cs, 'text': ct}}
            if ally is not None:
                p['ally_cqi'] = str(ally)
            if enemy is not None:
                p['enemy_cqi'] = str(enemy)
            if na is not None:
                p['n_ally_armies'] = na
            if ne is not None:
                p['n_enemy_armies'] = ne
            panels[iid] = p
        for (iid, att, attl, race, rel, ranks, setts, dem, off, tre, amtd, amto) \
                in self.con.execute(
                    "SELECT interrupt_id, attitude, attitude_label, race,"
                    " reliability, strength_ranks, settlements, demands, offers,"
                    " treaties, amount_demanded, amount_offered"
                    " FROM corpus.interrupt_diplo_panel WHERE interrupt_id = ANY(%s)",
                    (ids,)):
            panels[iid] = {
                'attitude': att, 'attitude_label': attl, 'race': race,
                'reliability': list(rel or []), 'strength_ranks': list(ranks or []),
                'settlements': setts, 'demands': list(dem or []),
                'offers': list(off or []), 'treaties': list(tre or []),
                'amount_demanded': amtd, 'amount_offered': amto}
        out = []
        for (iid, ts, ckey, turn, kind, state_at, chosen, answer, executed,
             confirmed, counted, refusal, dkey, ikey, root_context, prev_turn) \
                in heads:
            screen_id = dkey or ikey or root_context
            options = opts.get(iid) or {}
            for m in options.values():
                m.setdefault('dilemma_id', screen_id)
            out.append({'interrupt_id': iid, 'ts': ts, 'campaign_id': ckey,
                        'turn': turn, 'prev_turn': prev_turn, 'state_at': state_at,
                        'screen': kind, 'options': options, 'chosen': chosen,
                        'answer': answer, 'executed': executed,
                        'confirmed': confirmed, 'counted': counted,
                        'refusal': refusal, 'panel': panels.get(iid) or {}})
        return out
