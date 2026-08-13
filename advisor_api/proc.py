"""Which processes are alive, sampled off the request path."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common

POLL_S = 4.0
# Longer than the poll interval, so one slow sample does not blank the panel; short
# enough that a dead poller is visible rather than silently serving history.
STALE_S = 20.0

_PS = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%' or "
       "Name='Warhammer3.exe'\" | ForEach-Object { "
       "'{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.Name, $_.CreationDate, "
       "($_.CommandLine -replace '\\s+',' ') }")

_lock = threading.Lock()
_sample: dict = {"at": 0.0, "procs": []}
_thread: threading.Thread | None = None
_stop = threading.Event()


def _probe() -> list:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _PS],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) != 4:
            continue
        pid, name, started, cmd = parts
        try:
            pid_i = int(pid)
        except ValueError:
            continue
        out.append({"pid": pid_i, "name": name, "started": started, "cmd": cmd})
    return out


def _loop():
    while not _stop.wait(0.0 if _sample["at"] == 0.0 else POLL_S):
        procs = _probe()
        with _lock:
            _sample["procs"] = procs
            _sample["at"] = time.time()


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="proc-probe", daemon=True)
    _thread.start()


def stop():
    _stop.set()


def snapshot() -> tuple:
    with _lock:
        return list(_sample["procs"]), _sample["at"]


# What each service looks like in a command line. `ui.py` is deliberately absent: this
# process IS the dashboard, so it reports itself from the inside rather than hunting for
# a process that may or may not be the one answering.
_MATCH = (
    ("advisor session", "session.py"),
    ("recorder", "manager.py"),
    # The analytics builder. Unlike the dashboard it is a separate process, so it is
    # findable from here -- and it needs to be: every precomputed number the models pages
    # show is as current as this process last managed to run.
    ("analytics", "analytics.runner"),
)


def services() -> list:
    """One row per service, plus the game, plus this process."""
    from advisor_api.models import Service
    procs, at = snapshot()
    age = time.time() - at if at else None
    stale = (at == 0.0) or (age is not None and age > STALE_S)
    detail = None
    if at == 0.0:
        detail = "process probe has not completed its first sample yet"
    elif stale:
        detail = "process probe is stale (%.0fs old)" % age

    out = []
    for label, needle in _MATCH:
        hit = next((p for p in procs if needle in (p.get("cmd") or "")), None)
        out.append(Service(name=label, up=bool(hit) and not stale,
                           pid=hit.get("pid") if hit else None,
                           started=hit.get("started") if hit else None,
                           detail=detail))
    game = next((p for p in procs if (p.get("name") or "").lower() == "warhammer3.exe"), None)
    out.append(Service(name="game", up=bool(game) and not stale,
                       pid=game.get("pid") if game else None,
                       started=game.get("started") if game else None, detail=detail))
    out.append(Service(name="dashboard", up=True, pid=os.getpid(),
                       detail="this process"))
    return out


def kill_session_and_game() -> list:
    import runctl
    return [runctl.kill_session(game=True)]


def rebuild_analytics() -> list:
    """Ask the analytics service to rebuild. Delegates to runctl, like every other control,"""
    import runctl
    return runctl.rebuild_analytics()


def launch(kind: str, params: dict) -> list:
    """Start a run. Delegates to runctl so there is one definition of how a run starts."""
    import runctl
    turns = params.get("turns") or "%s-%s" % (params.get("turns_min", 2),
                                              params.get("turns_max", 20))
    cfg = {}
    for pair in str(params.get("cfg") or "").split():
        k, _, v = pair.partition("=")
        if _:
            cfg[k.strip()] = v.strip()
    steps = [runctl.kill_session(), runctl.kill_recorder()]
    time.sleep(1.5)
    steps.append(runctl.start_recorder(dev=bool(params.get("dev"))))
    time.sleep(3.0)
    steps.append("session -> %s" % runctl.start_session(
        int(params.get("campaigns") or 10), str(turns),
        model=params.get("model") or None,
        cfg=cfg,
        retrain=bool(params.get("retrain_first")) and kind != "cold",
        retrain_every=int(params.get("retrain_every") or 0),
        cold=(kind == "cold"),
        dev=bool(params.get("dev")),
        factions=params.get("factions") or "all",
        strategies=params.get("strategies") or None,
        ruleset=params.get("ruleset") or None))
    return steps
