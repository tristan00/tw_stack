from __future__ import annotations

"""Persistent cache of tensorized decision graphs.

A retrain rebuilds a graph for every decision ever recorded, and almost none of them
changed since the last retrain. decision_points, entity_snapshots and action_offers are
append-only (the only DELETE in store.py is on action_taken, for the decision currently
being attached), so every decision at or below a frozen max(decision_id) is immutable,
and its graph is a pure function of the record plus build.py/schema.py/the reference DB.
Cache that, and a retrain's front half costs O(new decisions) instead of O(corpus).

WHAT IS NOT CACHED, deliberately:

  the label y      decision_deltas reads FUTURE turns (base_model.future_best_at), so the
                   label of an already-stored decision changes as its campaign advances.
                   Caching it would silently freeze stale targets. Recomputed every walk;
                   the whole label pass is ~0.5s because target_series is ~2k rows.

  the taken mask   store.attach_taken can rewrite which action was taken for a decision.
                   What is cached is the resolved index plus a hash of the
                   (kind, id, type, key) tuple it was resolved against; if the current
                   row hashes differently that one graph is rebuilt. The hash only ever
                   detects change, it never selects an action, so a collision cannot
                   mislabel anything -- it can only fail to notice an edit.

CACHE KEY. schema_hash() is NOT sufficient: it covers TYPE_FIELDS, RELATIONS and the
vocabularies, but not KNN_K, not the normalisation scales, and not a single line of
build.py. Change KNN_K from 4 to 6 and every graph changes while schema_hash does not.
The fingerprint therefore hashes the source of the modules that construct a graph, plus
the size and mtime of the reference DB whose contents become catalogue edges. A
mismatch discards the whole cache and does a cold walk -- it never merges vintages.
"""

import hashlib
import json
import os
import shutil

try:
    from mapgraph3 import schema as S
except ImportError:
    import schema as S

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(os.path.dirname(S.MODEL_DIR), "gnn3_corpus")
SHARD = 2000


def _graph_sources():
    """Every module in this package that a graph's contents can depend on.

    Found by walking imports STATICALLY from the two entry points -- build.build_graph
    and net.to_data -- so the answer is a property of the code, not of the process.

    An earlier version read sys.modules instead. That was wrong in a way that cost a
    full cache: the set depended on what the CALLER had already imported, so invoking
    this from train.py pulled train.py into the fingerprint, and editing the training
    loop -- which cannot change a single node or edge -- discarded 2.7GB and forced a
    134s cold walk. A hand-written list has the opposite failure (add an import, forget
    to list it, and the cache silently serves graphs built by code that no longer
    exists), so neither of those; walk the import graph.
    """
    import ast
    seen, stack = set(), ["build.py", "net.py"]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        path = os.path.join(_HERE, name)
        if not os.path.exists(path):
            continue
        seen.add(name)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # covers both `import schema` and `from mapgraph3 import schema`
                names = ([node.module] if node.module else []) + [a.name for a in node.names]
            for mod in names:
                leaf = mod.split(".")[-1] + ".py"
                if os.path.exists(os.path.join(_HERE, leaf)):
                    stack.append(leaf)
    return sorted(seen)


def fingerprint():
    h = hashlib.sha1(S.schema_hash().encode())
    for name in _graph_sources():
        p = os.path.join(_HERE, name)
        if not os.path.exists(p):
            continue
        h.update(name.encode())
        with open(p, "rb") as fh:
            h.update(hashlib.sha1(fh.read()).digest())
    # catalogue nodes and their edges come out of the reference DB, so a rebuilt
    # reference changes graphs while every .py file stays byte-identical
    ref = getattr(S, "REFERENCE_DB", None)
    for db in [p for p in (ref,) if p]:
        st = os.stat(db) if os.path.exists(db) else None
        h.update(("%s|%s|%s" % (os.path.basename(db), st.st_size if st else 0,
                                int(st.st_mtime) if st else 0)).encode())
    return h.hexdigest()[:16]


def taken_hash(t):
    return hashlib.sha1(("\x00".join(str(v) for v in t)).encode()).hexdigest()[:16]


def root(run_key, fp=None):
    return os.path.join(CACHE_ROOT, fp or fingerprint(), run_key)


def _shard_name(did):
    """Shards are keyed by decision_id RANGE, not by position in the sorted list.

    That is what makes an append cheap: 700 new decisions land in one or two shards, so
    a retrain rewrites those and leaves the other ~3GB untouched. Position-keyed shards
    would renumber on every append and force a full rewrite.
    """
    return "shard_%08d.pt" % (did // SHARD)


def load(run_key, log=print):
    """Return ({decision_id: slot}, cache_dir) for the current fingerprint.

    A slot is (did, camp_id, campaign, turn, drop, data, counts, thash). An empty dict
    means a cold walk -- a fingerprint mismatch has no directory, so a stale vintage can
    never be partially reused.
    """
    d = root(run_key)
    man = os.path.join(d, "manifest.json")
    if not os.path.exists(man):
        return {}, d
    import torch
    try:
        meta = json.load(open(man, encoding="utf-8"))
        out = {}
        for name in meta["shards"]:
            for rec in torch.load(os.path.join(d, name), weights_only=False):
                out[rec[0]] = rec
        log("mapgraph3.corpus: %d cached graphs from %d shard(s)"
            % (len(out), len(meta["shards"])))
        return out, d
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        log("mapgraph3.corpus: cache unreadable (%s) -- rebuilding" % repr(e)[:80])
        shutil.rmtree(d, ignore_errors=True)
        return {}, d


def save(d, slots, dirty, log=print):
    """Persist `slots`, rewriting only the shards containing `dirty` decision ids.

    Each shard is written beside its target and moved into place, so a crash mid-write
    leaves the previous shard intact rather than a truncated one that would read as
    complete.
    """
    import torch
    os.makedirs(d, exist_ok=True)
    by_shard = {}
    for did in slots:
        by_shard.setdefault(_shard_name(did), []).append(did)
    touched = {_shard_name(i) for i in dirty}
    for name, ids in by_shard.items():
        if name not in touched and os.path.exists(os.path.join(d, name)):
            continue
        tmp = os.path.join(d, name + ".tmp")
        torch.save([slots[i] for i in sorted(ids)], tmp)
        os.replace(tmp, os.path.join(d, name))
    json.dump({"fingerprint": os.path.basename(os.path.dirname(d)),
               "shards": sorted(by_shard), "n": len(slots),
               "watermark": max(slots) if slots else 0},
              open(os.path.join(d, "manifest.json"), "w"))
    log("mapgraph3.corpus: cache holds %d graphs; rewrote %d of %d shard(s)"
        % (len(slots), len(touched & set(by_shard)), len(by_shard)))


def ranges(ids, gap=64):
    """Contiguous-ish (lo, hi) windows covering `ids`, for after=lo-1/before=hi reads.

    Small gaps are bridged: reading a few extra rows is much cheaper than issuing another
    indexed range query against a 9M-row table.
    """
    out = []
    for i in sorted(ids):
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(lo, hi) for lo, hi in out]
