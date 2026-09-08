from contextlib import contextmanager
from itertools import groupby
import logging
import time

import numpy as np


LOG = logging.getLogger(__name__)
METRIC = "raw_reward_mse"
HEAD_SQL = (
    "SELECT t.decision_id, c.campaign_key, s.turn, t.entity_seq, ty.key, a.action_key,"
    " o.slot_index, t.offer_seq, sc.settlements, sc.lord_level, sc.allies, sc.vassals"
    " FROM corpus.taken t JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
    " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
    " JOIN dict.action a ON a.action_id = t.action_id"
    " JOIN dict.action_type ty ON ty.id = a.action_type_id"
    " LEFT JOIN corpus.offer o ON o.decision_id = t.decision_id AND o.offer_seq = t.offer_seq"
    " LEFT JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = t.decision_id"
    " WHERE c.campaign_key = ANY(%s) ORDER BY c.campaign_key, t.decision_id"
)
OFFER_SQL = (
    "SELECT o.decision_id, o.offer_seq, o.entity_seq, ty.key, a.action_key, o.slot_index"
    " FROM corpus.offer o JOIN dict.action a ON a.action_id = o.action_id"
    " JOIN dict.action_type ty ON ty.id = a.action_type_id"
    " WHERE o.decision_id = ANY(%s) ORDER BY o.decision_id, o.offer_seq"
)


def remaining(deadline):
    seconds = deadline - time.perf_counter()
    if seconds <= 0:
        raise TimeoutError("trial deadline reached")
    return seconds


def partition(rows):
    from advisor.base_model import stable_split

    groups = [r["campaign"] for r in rows]
    if len(set(groups)) < 3:
        raise ValueError("at least three campaigns are required")
    validation, train = stable_split(len(rows), groups)
    if len(train) < 2 or not validation:
        raise ValueError("shared campaign split needs two training rows and a nonempty holdout")
    ys = np.asarray([r["y"] for r in rows], dtype=np.float64)
    if not np.isfinite(ys).all():
        raise ValueError("reward targets must be finite")
    return dict(train_indices=train, validation_indices=validation,
                reward_mean=float(ys[train].mean()), reward_sd=float(ys[train].std(ddof=1)) or 1.0)


def score(targets, predictions):
    started = time.perf_counter()
    targets = np.asarray(targets, dtype=np.float64)
    values = np.asarray(predictions, dtype=np.float64)
    if targets.ndim != 1 or targets.shape != values.shape:
        raise ValueError("reward targets and predictions must be matching vectors")
    if not len(values) or not np.isfinite(values).all() or not np.isfinite(targets).all():
        raise ValueError("validation predictions must be nonempty and finite")
    mse = float(np.mean((values - targets)**2))
    var = float(targets.var())
    result = dict(val_mse=mse, raw_val_mse=mse, val_r2=1 - mse / (var or 1.0))
    LOG.info("reward score exit seconds=%.6f rows=%d raw_mse=%.6f", time.perf_counter() - started, len(values), mse)
    return result


class LiveCorpus:

    def __init__(self, store, window, deadline):
        from advisor.base_model import decision_deltas, target

        self.store, self.deadline, self.window = store, deadline, window
        remaining(deadline)
        heads = store.con.execute(HEAD_SQL, (list(store.window_keys(window)),)).fetchall()
        series = store.target_series({h[1] for h in heads})
        self.heads, self.rows = [], []
        for campaign, group in groupby(heads, key=lambda h: h[1]):
            for step, (did, _, turn, eseq, at, key, slot, offer_seq, settlements, level, allies, vassals) in enumerate(group):
                baseline = dict(settlements=settlements, lord_level=level, allies=allies, vassals=vassals)
                y = target(decision_deltas(baseline, series.get(campaign, {}), turn))
                row = dict(campaign=campaign, decision=int(did), turn=int(turn), step=step, y=y,
                    action=dict(entity_seq=eseq, action_type=at, key=key, slot_index=slot, offer_seq=offer_seq))
                self.heads.append(row)
                if y is not None:
                    self.rows.append(row)
        self.split = partition(self.rows)
        remaining(deadline)

    def campaigns(self):
        from decisions import hydrate

        def records(group):
            for offset in range(0, len(group), hydrate.PREFETCH_CHUNK):
                remaining(self.deadline)
                chunk = group[offset:offset + hydrate.PREFETCH_CHUNK]
                ids = [r["decision"] for r in chunk]
                pre = hydrate.Prefetch(self.store.con, ids, include_offers=False)
                self._offers = {did: [] for did in ids}
                for offer in self.store.con.execute(OFFER_SQL, (ids,)):
                    self._offers[offer[0]].append(offer)
                for row in chunk:
                    rec = hydrate.record(self.store.con, row["decision"], legacy=False, pre=pre)
                    yield row, rec

        for campaign, group in groupby(self.heads, key=lambda r: r["campaign"]):
            yield campaign, records(list(group))

    def offers(self, ids):
        remaining(self.deadline)
        return [offer for did in ids for offer in self._offers[did]]


@contextmanager
def open_live(window=2500, deadline=float("inf")):
    from decisions.store import DecisionStore

    if type(window) is not int or window < 1:
        raise ValueError("campaign window must be positive")
    started = time.perf_counter()
    LOG.info("live reward read enter window=%d", window)
    store = DecisionStore(readonly=True)
    try:
        corpus = LiveCorpus(store, window, deadline)
        yield corpus
    finally:
        store.close()
        LOG.info("live reward read exit seconds=%.3f", time.perf_counter() - started)
