import json
import logging
from pathlib import Path
import time

from advisor.reward_data import open_live, remaining


LOG = logging.getLogger(__name__)


def attach_actions(corpus, record, row, generated):
    from decisions import hydrate

    entities = {e["snapshot_id"] % hydrate.MAX_ENTITIES: e for e in record["entities"]}
    for entity in entities.values():
        entity["offers"] = []
    action = row["action"]
    actor_seq = action["entity_seq"]
    if actor_seq is None:
        campaign_entities = [e for e in entities.values() if e["context_kind"] == "campaign"]
        if campaign_entities:
            actor = campaign_entities[0]
        else:
            actor = dict(context_kind="campaign", context_id=record["campaign"]["faction"],
                         state=record["campaign"], offers=[])
            record["entities"].append(actor)
        entities[None] = actor
    offers = corpus.offers([row["decision"]])
    if action["offer_seq"] is None:
        offers.append((row["decision"], None, actor_seq, action["action_type"], action["key"], action["slot_index"]))
    chosen_entry = None
    for _, seq, eseq, at, key, slot in offers:
        entity = entities[eseq]
        params = {}
        if at not in hydrate.SYNTHETIC_ACTIONS:
            matches = generated.get((entity["context_kind"], str(entity["context_id"]), at, str(key)), [])
            candidate = hydrate._pick_generated(matches, slot)
            if candidate is None:
                raise ValueError("cannot reconstruct live action parameters for decision %s offer %s" % (row["decision"], seq))
            params = candidate.get("params") or {}
        entry = dict(action_type=at, key=key, params=params)
        entity["offers"].append(entry)
        if seq == action["offer_seq"]:
            chosen_entry = entry
    ordered = [o for e in record["entities"] for o in e["offers"]]
    return record, next(i for i, offer in enumerate(ordered) if offer is chosen_entry)


def example(corpus, record, row, graph_config):
    from decisions import hydrate
    from advisor.mapgraph import build, net

    generated = hydrate._generated_index(record)
    record, selected = attach_actions(corpus, record, row, generated)
    graph = build.build_graph(record, graph_config)
    taken = [float(i == selected) for i in range(len(graph.action_nodes))]
    data = net.to_data(graph, y=row["y"], taken=taken)
    return dict(data=data, y=row["y"], campaign_id=row["campaign"], decision_id=row["decision"], counts=graph.counts)


def walk(corpus, graph_config, deadline, log=print):
    started = time.perf_counter()
    log("sequence live graph walk enter")
    examples = []
    for campaign, records in corpus.campaigns():
        for row, record in records:
            remaining(deadline)
            if row["y"] is not None:
                examples.append(example(corpus, record, row, graph_config))
        log("sequence live graphs campaign=%s rows=%d seconds=%.2f" % (campaign, len(examples), time.perf_counter() - started))
    metrics = dict(graphs=len(examples), seconds=time.perf_counter() - started,
        nodes=sum(e["counts"]["nodes"] for e in examples), edges=sum(e["counts"]["edges"] for e in examples))
    log("sequence live graph walk exit seconds=%.2f rows=%d" % (metrics["seconds"], len(examples)))
    return dict(examples=examples, metrics=metrics)


def fit(window, config, directory, deadline, device="cuda", seed=0, max_cache_bytes=None):
    from advisor.mapgraph import greedy_train as GT, sequence_train as ST

    cfg = dict(config["model"], device=device, seed=seed)
    with open_live(window, deadline) as corpus:
        walked = walk(corpus, config["graph"], deadline, LOG.info)
        ys, groups, decisions = ([r[key] for r in corpus.rows] for key in ("y", "campaign", "decision"))
    datas = [e["data"] for e in walked["examples"]]
    walked["examples"].clear()
    prep = GT.prepare(datas, ys, groups, cfg, free_datas=True, log=LOG.info, deadline=deadline)
    result = ST.fit_net(ys, groups, decisions, cfg, prep, str(directory), deadline, LOG.info)
    result["graph_metrics"] = walked["metrics"]
    (Path(directory) / "fit.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    remaining(deadline)
    return result
