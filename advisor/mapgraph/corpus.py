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
    from decisions import pg
    try:
        con = pg.connect(autocommit=True, readonly=True, search_path="reference")
        try:
            built = con.execute("SELECT v FROM meta WHERE k='built'").fetchone()
            n = con.execute("SELECT (SELECT COUNT(*) FROM buildings)"
                            " + (SELECT COUNT(*) FROM units)"
                            " + (SELECT COUNT(*) FROM agent_actions)").fetchone()[0]
        finally:
            con.close()
        h.update(("reference|%s|%s" % (built[0] if built else 0, n)).encode())
    except Exception:
        h.update(b"reference|absent")
    return h.hexdigest()[:16]


def taken_hash(t):
    return hashlib.sha1(("\x00".join(str(v) for v in t)).encode()).hexdigest()[:16]


def root(run_key, fp=None):
    return os.path.join(CACHE_ROOT, fp or fingerprint(), run_key)


def shard_name(did):
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
            for rec in torch.load(os.path.join(d, name), weights_only=False, mmap=True):
                out[rec[0]] = rec
        log("mapgraph.corpus: %d cached graphs from %d shard(s)"
            % (len(out), len(meta["shards"])))
        return out, d
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        log("mapgraph.corpus: cache unreadable (%s) -- rebuilding" % repr(e)[:80])
        shutil.rmtree(d, ignore_errors=True)
        return {}, d


def write_shard(d, name, recs):
    import torch
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    torch.save([recs[i] for i in sorted(recs)], tmp)
    os.replace(tmp, os.path.join(d, name))


def read_shard(d, name):
    import torch
    return {rec[0]: rec for rec in
            torch.load(os.path.join(d, name), weights_only=False, mmap=True)}


def write_manifest(d, shards, n, watermark, log=print):
    os.makedirs(d, exist_ok=True)
    json.dump({"fingerprint": os.path.basename(os.path.dirname(d)),
               "shards": sorted(shards), "n": n, "watermark": watermark},
              open(os.path.join(d, "manifest.json"), "w"))
    fp = os.path.basename(os.path.dirname(d))
    for stale in os.listdir(CACHE_ROOT):
        if stale != fp:
            shutil.rmtree(os.path.join(CACHE_ROOT, stale), ignore_errors=True)
            log("mapgraph.corpus: pruned stale cache generation %s" % stale)
    log("mapgraph.corpus: cache holds %d graphs across %d shard(s)"
        % (n, len(shards)))


def ranges(ids, gap=64):
    out = []
    for i in sorted(ids):
        if out and i - out[-1][1] <= gap:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(lo, hi) for lo, hi in out]
