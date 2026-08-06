from __future__ import annotations

import json
import os
import sys
import threading
import time

_LOCK = threading.Lock()
_STATE = {"path": None, "run_dir": None, "seq": 0}
_FALLBACK = r"D:\twdata\logs\launcher\trace_prerun.jsonl"


def set_run_dir(run_dir):
    try:
        if not run_dir:
            return
        path = os.path.join(str(run_dir), "trace.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            _STATE["run_dir"], _STATE["path"] = str(run_dir), path
        emit("trace_opened", run_dir=str(run_dir), pid=os.getpid())
    except Exception as e:
        sys.stderr.write("trace: could not open %s -> %s\n" % (run_dir, repr(e)[:120]))


def _path():
    p = _STATE["path"]
    if p:
        return p
    try:
        os.makedirs(os.path.dirname(_FALLBACK), exist_ok=True)
    except Exception:
        pass
    return _FALLBACK


def emit(kind, **fields):
    try:
        with _LOCK:
            _STATE["seq"] += 1
            rec = {"seq": _STATE["seq"], "ts": round(time.time(), 3), "kind": kind}
            rec.update(fields)
            line = json.dumps(rec, default=str)
            with open(_path(), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as e:
        try:
            sys.stderr.write("trace: emit(%s) failed -> %s\n" % (kind, repr(e)[:100]))
        except Exception:
            pass


def click(mechanism, target, result=None, **extra):
    emit("click", mechanism=mechanism, target=target, result=result, **extra)


def advisor(pick, ranked_top=None, **extra):
    emit("advisor_pick", pick=pick, ranked_top=ranked_top, **extra)


def launcher(stage, action_type=None, key=None, **extra):
    emit("launcher", stage=stage, action_type=action_type, key=key, **extra)


def screen(name, roots=None, **extra):
    emit("screen", name=name, roots=roots, **extra)
