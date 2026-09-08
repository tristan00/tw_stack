import numpy as np

from .data import ActionQueries, History, NodeKind


def campaign(steps=32, streams=32, seed=17):
    if steps < 1 or streams < 2:
        raise ValueError("steps must be positive and streams must be at least two")
    rng = np.random.default_rng(seed)
    stream = np.tile(np.arange(streams), steps)
    step = np.repeat(np.arange(steps), streams)
    entities = max(2, streams // 3)
    entity = stream % entities
    clock = np.where(stream % 3 == 0, step // 4, step).astype(np.float32)
    phase = stream * 0.37
    values = np.column_stack((np.sin(clock * 0.3 + phase), np.cos(clock * 0.17 - phase),
                              (clock % 5) / 5, (stream % 7) / 7, np.sin(clock * 0.3 + phase + 0.15),
                              (stream % 2) * 2 - 1)).astype(np.float32)
    values += rng.normal(0, 0.005, (streams, values.shape[1]))[stream].astype(np.float32)
    xy = np.column_stack(((entity % 8) * 10, (entity // 8) * 10)).astype(np.float32)
    action_step = np.arange(steps)
    actions = np.zeros((steps, values.shape[1]), dtype=np.float32)
    actions[:, 0] = np.sin(action_step)
    actions[:, -1] = 1
    width = values.shape[1]
    history = History.from_arrays(np.concatenate((values, actions)), np.r_[stream, np.full(steps, streams)],
        np.r_[step, action_step], np.r_[step // 4, action_step // 4],
        names=("stock", "flow", "growth", "order", "units", "action_code"),
        entity=np.r_[entity, action_step % entities], reference=np.r_[(entity + 1) % entities, (action_step + 1) % entities],
        kind=np.r_[np.zeros(len(values), dtype=np.int64), np.full(steps, NodeKind.ACTION)],
        xy=np.vstack((xy, np.column_stack(((action_step % entities % 8) * 10, (action_step % entities // 8) * 10)))),
        episode="synthetic-campaign-%d" % seed)
    count = min(streams, 32)
    queries = ActionQueries.from_arrays(rng.normal(size=(count, width)).astype(np.float32), ids=np.arange(count),
        entity=np.arange(count) % entities, reference=(np.arange(count) + 1) % entities,
        xy=np.column_stack(((np.arange(count) % entities % 8) * 10, (np.arange(count) % entities // 8) * 10)))
    return history, queries
