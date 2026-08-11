from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import common

TW_STACK = common.ROOT
VENV_PY = common.VENV_PY
LOG_DIR = common.LOGS_ADVISOR
SERVICES_LOG_DIR = common.LOGS_SERVICES
CURRENT_LOG = common.CURRENT_SESSION_LOG

DEFAULT_PORT = 8777
DEFAULT_SHOTS = 60

_DETACH = (getattr(subprocess, "DETACHED_PROCESS", 0)
           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _spawn(args, log_path, merge_err=False):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    out = open(log_path, "w", encoding="utf-8")
    err = subprocess.STDOUT if merge_err else open(log_path[:-4] + ".err", "w", encoding="utf-8")
    subprocess.Popen(args, cwd=TW_STACK, stdout=out, stderr=err, creationflags=_DETACH)
    return log_path


def _ps_kill(match):
    cmd = ("$n=0; Get-CimInstance Win32_Process -Filter \"Name like '%%python%%'\" | "
           "? { $_.CommandLine -like '*%s*' } | %% { Stop-Process -Id $_.ProcessId -Force "
           "-ErrorAction SilentlyContinue; $n++ }; $n" % match)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                           text=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        return int((r.stdout or "0").strip() or 0)
    except Exception as e:
        sys.stderr.write("runctl: kill %s -> %s\n" % (match, repr(e)[:110]))
        return -1


def kill_game():
    cmd = ("Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue | "
           "Stop-Process -Force -ErrorAction SilentlyContinue")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                       text=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        sys.stderr.write("runctl: kill game -> %s\n" % repr(e)[:110])




def kill_session(game=True):
    n = _ps_kill("session.py")
    if game:
        kill_game()
    return "killed sessions=%d" % n


def kill_recorder():
    return "killed recorders=%d" % _ps_kill("manager.py")


def kill_ui():
    return "killed uis=%d" % _ps_kill("ui.py")


def start_recorder(shots=DEFAULT_SHOTS, dev=False):
    log = os.path.join(SERVICES_LOG_DIR, "manager_%s.log" % _stamp())
    args = [VENV_PY, "-u", "manager/manager.py", "--shots", str(shots)] + (["--dev"] if dev else [])
    _spawn(args, log, merge_err=True)
    return "recorder -> %s" % log


def start_ui(port=DEFAULT_PORT):
    log = os.path.join(SERVICES_LOG_DIR, "ui_%s.log" % _stamp())
    _spawn([VENV_PY, "-u", "advisor_ui/ui.py", str(port)], log)
    return "ui :%d -> %s" % (port, log)


def start_session(campaigns, turns, model=None, cfg=None, retrain=True, retrain_every=0,
                  cold=False, dev=False, epsilon=None, factions="all", strategies=None,
                  ruleset=None, campaign=None):
    if not str(factions or "").strip():
        raise SystemExit("--factions must be 'all' or a comma-separated list of faction keys")
    ts = _stamp()
    log = os.path.join(LOG_DIR, "session_%s%s%sx%s_%s.log"
                       % ("cold_" if cold else "", ("%s_" % model) if model else "",
                          campaigns, turns, ts))
    cfg_args = []
    for k, v in sorted((cfg or {}).items()):
        cfg_args += ["--nn-%s" % k, str(v)]
    args = ([VENV_PY, "-u", "advisor/session.py", str(campaigns), str(turns),
             "--factions", str(factions).strip()]
            + (["--model", model] if model else []) + cfg_args
            + (["--cold"] if cold else [])
            + (["--retrain"] if retrain and not cold else [])
            + (["--retrain-every", str(retrain_every)] if retrain_every and not cold else [])
            + (["--epsilon", str(epsilon)] if epsilon is not None else [])
            + (["--strategies", str(strategies)] if strategies else [])
            + (["--ruleset", str(ruleset)] if ruleset else [])
            + (["--campaign", str(campaign)] if campaign else [])
            + (["--dev"] if dev else []))
    _spawn(args, log)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(CURRENT_LOG, "w", encoding="utf-8", newline="") as fh:
        fh.write(log)
    return log


def up(campaigns, turns, model=None, cfg=None, retrain=True, retrain_every=0, cold=False,
       dev=False, shots=DEFAULT_SHOTS, port=DEFAULT_PORT, with_ui=True, epsilon=None,
       factions="all", strategies=None, ruleset=None, campaign=None):
    steps = [kill_session(), kill_recorder()]
    if with_ui:
        steps.append(kill_ui())
    time.sleep(1.5)
    steps.append(start_recorder(shots=shots, dev=dev))
    time.sleep(3.0)
    if with_ui:
        steps.append(start_ui(port=port))
        time.sleep(2.0)
    steps.append("session -> %s" % start_session(campaigns, turns, model=model, cfg=cfg,
                                                 retrain=retrain, retrain_every=retrain_every,
                                                 cold=cold, dev=dev, epsilon=epsilon,
                                                 factions=factions, strategies=strategies,
                                                 ruleset=ruleset, campaign=campaign))
    return steps


def down():
    return [kill_session(), kill_recorder(), kill_ui()]


def status():
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
           "? { $_.CommandLine -like '*session.py*' -or $_.CommandLine -like '*manager.py*' "
           "-or $_.CommandLine -like '*ui.py*' } | "
           "% { '{0}  {1}' -f $_.ProcessId, $_.CommandLine }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                           text=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        procs = [l for l in (r.stdout or "").splitlines() if l.strip()]
    except Exception as e:
        procs = ["status failed: %s" % repr(e)[:110]]
    try:
        with open(CURRENT_LOG, encoding="utf-8-sig") as fh:
            ptr = fh.read().strip()
        age = time.time() - os.path.getmtime(ptr)
        ptr = "%s (%.0fs old)" % (ptr, age)
    except OSError as e:
        ptr = "unreadable: %s" % repr(e)[:80]
    return procs + ["pointer: %s" % ptr]


def _cfg(pairs):
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        if not _:
            raise SystemExit("--cfg expects KEY=VALUE, got %r" % p)
        out[k.strip()] = v.strip()
    return out


def main():
    ap = argparse.ArgumentParser(prog="runctl", description="start and stop tw_stack runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "session"):
        s = sub.add_parser(name)
        s.add_argument("campaigns", type=int)
        s.add_argument("turns")
        s.add_argument("--model", default="catboost")
        s.add_argument("--factions", default="all")
        s.add_argument("--cfg", action="append")
        s.add_argument("--no-retrain", action="store_true")
        s.add_argument("--retrain-every", type=int, default=0)
        s.add_argument("--epsilon", type=float, default=None)
        s.add_argument("--strategies", default=None)
        s.add_argument("--ruleset", default=None)
        s.add_argument("--campaign", default=None)
        s.add_argument("--cold", action="store_true")
        s.add_argument("--dev", action="store_true")
        if name == "up":
            s.add_argument("--shots", type=int, default=DEFAULT_SHOTS)
            s.add_argument("--port", type=int, default=DEFAULT_PORT)
            s.add_argument("--no-ui", action="store_true")
    sub.add_parser("down")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "status":
        print("\n".join(status()))
        return
    if a.cmd == "down":
        print("\n".join(down()))
        return
    common = dict(model=a.model, cfg=_cfg(a.cfg), retrain=not a.no_retrain,
                  retrain_every=a.retrain_every, cold=a.cold, dev=a.dev, epsilon=a.epsilon,
                  factions=a.factions, strategies=a.strategies, ruleset=a.ruleset,
                  campaign=a.campaign)
    if a.cmd == "session":
        print("session -> %s" % start_session(a.campaigns, a.turns, **common))
        return
    print("\n".join(up(a.campaigns, a.turns, shots=a.shots, port=a.port,
                       with_ui=not a.no_ui, **common)))


if __name__ == "__main__":
    main()
