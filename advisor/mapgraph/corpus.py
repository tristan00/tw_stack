from __future__ import annotations


import hashlib
import json
import os
import shutil

from advisor.mapgraph import schema as S

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(os.path.dirname(S.MODEL_DIR), "mapgraph_corpus")
SHARD = 2000


def _graph_sources():
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
    return "shard_%08d.pt" % (did // SHARD)


def load(run_key, log=print):
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
        log("mapgraph.corpus: %d cached graphs from %d shard(s)"
            % (len(out), len(meta["shards"])))
        return out, d
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        log("mapgraph.corpus: cache unreadable (%s) -- rebuilding" % repr(e)[:80])
        shutil.rmtree(d, ignore_errors=True)
        return {}, d


def save(d, slots, dirty, log=print):
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
    fp = os.path.basename(os.path.dirname(d))
    for stale in os.listdir(CACHE_ROOT):
        if stale != fp:
            shutil.rmtree(os.path.join(CACHE_ROOT, stale), ignore_errors=True)
            log("mapgraph.corpus: pruned stale cache generation %s" % stale)
    log("mapgraph.corpus: cache holds %d graphs; rewrote %d of %d shard(s)"
        % (len(slots), len(touched & set(by_shard)), len(by_shard)))


def ranges(ids, gap=64):
    out = []
    for i in sorted(ids):
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(lo, hi) for lo, hi in out]
