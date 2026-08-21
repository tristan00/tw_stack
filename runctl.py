from __future__ import annotations

import argparse
import datetime
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

_DETACH = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def _spawn(args, log_path, merge_err=False, env=None):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    out = open(log_path, "w", encoding="utf-8")
    err = subprocess.STDOUT if merge_err else open(log_path[:-4] + ".err", "w", encoding="utf-8")
    subprocess.Popen(args, cwd=TW_STACK, stdout=out, stderr=err, creationflags=_DETACH,
                     env=env)
    return log_path


def _presave_radius(v):
    s = str(v).strip().lower()
    if s in ("none", "off"):
        raise argparse.ArgumentTypeError(
            "a run boots a baked start and the map comes with it; bake fresh campaigns "
            "with bake.py")
    return float(v)


def _selector_tag(width=0, ucb=None):
    if ucb is not None:
        return "ucb:%g" % ucb
    if width:
        return "width:%d" % width
    return "uniform"


def _env_with_presave(presave_radius, selector=None):
    e = dict(os.environ)
    e["TW_PRESAVE_RADIUS"] = str(float(presave_radius))
    if selector:
        e["TW_SELECTOR"] = selector
    else:
        e.pop("TW_SELECTOR", None)
    return e


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
    return "killed uis=%d" % _ps_kill("advisor_api")


def start_recorder(shots=DEFAULT_SHOTS, dev=True, presave_radius=None, selector=None):
    log = os.path.join(SERVICES_LOG_DIR, "manager_%s.log" % _stamp())
    args = ([VENV_PY, "-u", "manager/manager.py"]
            + (["--shots", str(shots)] if shots else [])
            + (["--dev"] if dev else []))
    _spawn(args, log, merge_err=True, env=_env_with_presave(presave_radius, selector))
    return "recorder -> %s" % log


def start_ui(port=DEFAULT_PORT):
    log = os.path.join(SERVICES_LOG_DIR, "ui_%s.log" % _stamp())
    _spawn([VENV_PY, "-u", "-m", "advisor_api.app", str(port)], log)
    return "ui :%d -> %s" % (port, log)


def kill_analytics():
    return "killed analytics=%d" % _ps_kill("analytics.runner")


def start_analytics():
    log = os.path.join(SERVICES_LOG_DIR, "analytics_%s.log" % _stamp())
    _spawn([VENV_PY, "-u", "-m", "analytics.runner"], log, merge_err=True)
    return "analytics -> %s" % log


def rebuild_analytics():
    steps = [kill_analytics()]
    common.wait("runctl_analytics_settle", 0.5)
    log = os.path.join(SERVICES_LOG_DIR, "analytics_rebuild_%s.log" % _stamp())
    _spawn([VENV_PY, "-u", "-m", "analytics.runner", "--rebuild"], log, merge_err=True)
    steps.append("analytics rebuilding -> %s" % log)
    return steps


def start_session(campaigns, turns, retrain_every=0,
                  cold=False, dev=True, factions="all", strategies=None,
                  ruleset=None, retrain_first=False, presave_radius=None,
                  width=0, ucb=None):
    if not str(factions or "").strip():
        raise SystemExit("--factions must be 'all' or a comma-separated list of faction keys")
    import presaves
    pool = presaves.list_presaves(radius=presave_radius)
    if not pool:
        raise SystemExit(
            "--presave-radius %s but no baked save at that radius: %s holds %s. "
            "Refusing to start a run that would silently fall back to fresh "
            "campaigns on the untrimmed map."
            % (presave_radius, presaves.presave_dir(),
               presaves.presave_radii() or "none"))
    ts = _stamp()
    log = os.path.join(LOG_DIR, "session_%s%sx%s_%s.log"
                       % ("cold_" if cold else "", campaigns, turns, ts))
    args = ([VENV_PY, "-u", "advisor/session.py", str(campaigns), str(turns),
             "--factions", str(factions).strip()]
            + (["--cold"] if cold else [])
            + (["--retrain-every", str(retrain_every)] if retrain_every and not cold else [])
            + (["--retrain-first"] if retrain_first and retrain_every and not cold else [])
            + (["--strategies", str(strategies)] if strategies else [])
            + (["--ruleset", str(ruleset)] if ruleset else [])
            + ["--presave-radius", str(presave_radius)]
            + (["--width", str(width)] if width else [])
            + (["--ucb", str(ucb)] if ucb is not None else [])
            + (["--dev"] if dev else []))
    _spawn(args, log, env=_env_with_presave(presave_radius,
                                            _selector_tag(width, ucb)))
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = CURRENT_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(log)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, CURRENT_LOG)
    return log


def up(campaigns, turns, retrain_every=0, cold=False,
       dev=True, shots=DEFAULT_SHOTS, port=DEFAULT_PORT, with_ui=True,
       factions="all", strategies=None, ruleset=None, retrain_first=False,
       presave_radius=None, width=0, ucb=None):
    steps = [kill_session(), kill_recorder()]
    if with_ui:
        steps.append(kill_ui())
        steps.append(kill_analytics())
    common.wait("runctl_kill_settle", 1.5)
    leftover = [row for row in status() if "session.py" in row]
    if leftover:
        raise SystemExit("REFUSING to start a second session -- session.py is still "
                         "alive after the kill pass: %s" % "; ".join(leftover)[:200])
    steps.append(start_recorder(shots=shots, dev=dev, presave_radius=presave_radius,
                                selector=_selector_tag(width, ucb)))
    common.wait("runctl_recorder_spawn_grace", 3.0)
    if with_ui:
        steps.append(start_ui(port=port))
        steps.append(start_analytics())
        common.wait("runctl_ui_spawn_grace", 2.0)
    steps.append("session -> %s" % start_session(campaigns, turns,
                                                 retrain_every=retrain_every,
                                                 cold=cold, dev=dev,
                                                 factions=factions, strategies=strategies,
                                                 ruleset=ruleset,
                                                 retrain_first=retrain_first,
                                                 presave_radius=presave_radius,
                                                 width=width, ucb=ucb))
    return steps


def down():
    return [kill_session(), kill_recorder(), kill_ui(), kill_analytics()]


HARNESS_EVERY_S = 300.0
HARNESS_STALL_S = 1200.0
HARNESS_PROGRESS_S = 1800.0
HARNESS_COOLDOWN_S = 1800.0
_PROGRESS_MARKS = ("== turn ", "mapgraph.train: epoch", "retrained before run")


def _harness_note(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(common.HARNESS_LOG), exist_ok=True)
        with open(common.HARNESS_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _pointer_log():
    try:
        with open(CURRENT_LOG, encoding="utf-8-sig") as fh:
            p = fh.read().strip()
        if p and os.path.exists(p):
            return p
    except OSError:
        pass
    best = None
    try:
        for f in os.listdir(LOG_DIR):
            if f.startswith("session_") and f.endswith(".log"):
                q = os.path.join(LOG_DIR, f)
                if best is None or os.path.getmtime(q) > os.path.getmtime(best):
                    best = q
    except OSError:
        pass
    return best or ""


def _first_stamp(path):
    try:
        with open(path, "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None
    for line in head.splitlines():
        try:
            return datetime.datetime.fromisoformat(line[:23]).timestamp()
        except ValueError:
            continue
    return None


def _progress_age(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 262144))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for line in tail.splitlines():
        if any(m in line for m in _PROGRESS_MARKS):
            try:
                last = datetime.datetime.fromisoformat(line[:23]).timestamp()
            except ValueError:
                continue
    if last is None:
        last = _first_stamp(path)
    if last is None:
        return None
    return time.time() - last


def _session_reports_complete():
    root = common.native(common.RUNS_ROOT)
    try:
        paths = [os.path.join(root, f) for f in os.listdir(root)
                 if f.startswith("session_") and f.endswith(".json")]
    except OSError:
        return False
    if not paths:
        return False
    try:
        rep = json.load(open(max(paths, key=os.path.getmtime), encoding="utf-8"))
    except (OSError, ValueError):
        return False
    want = (rep.get("requested") or {}).get("campaigns") or 0
    return bool(want) and len(rep.get("campaigns") or []) >= want


def _cooled_down():
    try:
        last = float(open(common.HARNESS_STAMP, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return True
    return time.time() - last >= HARNESS_COOLDOWN_S


def harness_tick():
    if os.path.exists(common.HARNESS_OFF):
        _harness_note("HARNESS_OFF present -- standing down")
        return "off"
    if _session_reports_complete():
        _harness_note("session complete -- nothing to supervise")
        return "complete"
    alive = any("session.py" in row for row in status())
    log = _pointer_log()
    log_age = (time.time() - os.path.getmtime(log)) if log else None
    prog_age = _progress_age(log) if log else None
    healthy = (alive and log_age is not None and log_age < HARNESS_STALL_S
               and prog_age is not None and prog_age < HARNESS_PROGRESS_S)
    if healthy:
        _harness_note("ok: session alive, log %.0fs old, last progress %.0fs ago"
                      % (log_age, prog_age))
        return "ok"
    reason = ("alive=%s log_age=%s progress_age=%s"
              % (alive,
                 "%.0fs" % log_age if log_age is not None else "unknown",
                 "%.0fs" % prog_age if prog_age is not None else "unknown"))
    if not _cooled_down():
        _harness_note("UNHEALTHY (%s) -- cooldown active, holding" % reason)
        return "cooldown"
    _harness_note("UNHEALTHY (%s) -- killing the stack and relaunching" % reason)
    try:
        with open(common.HARNESS_STAMP, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass
    from run_config import RUN
    for step in down():
        _harness_note(step)
    common.wait("harness_relaunch_settle", 2.0)
    try:
        for step in up(RUN["campaigns"], RUN["turns"],
                       retrain_every=RUN["retrain_every"],
                       retrain_first=RUN.get("retrain_first", False),
                       dev=RUN.get("dev", True), with_ui=True,
                       strategies=RUN["strategies"], ruleset=RUN["ruleset"],
                       factions=RUN["factions"],
                       presave_radius=RUN["presave_radius"]):
            _harness_note(step)
    except SystemExit as e:
        _harness_note("relaunch refused: %s" % e)
        return "refused"
    return "relaunched"


def _ui_ok(port=DEFAULT_PORT, timeout=8.0):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/run" % port,
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def harness(every_s=HARNESS_EVERY_S):
    _harness_note("harness starting: check every %.0fs, stall %.0fs, progress %.0fs, "
                  "cooldown %.0fs" % (every_s, HARNESS_STALL_S, HARNESS_PROGRESS_S,
                                      HARNESS_COOLDOWN_S))
    ui_fails = 0
    while True:
        try:
            r = harness_tick()
            if r in ("off", "complete"):
                return 0
            if r == "relaunched" or _ui_ok():
                ui_fails = 0
            else:
                ui_fails += 1
                if ui_fails >= 2:
                    _harness_note("UI unresponsive on %d consecutive checks -- restarting "
                                  "the UI service only, the session is untouched" % ui_fails)
                    _harness_note(kill_ui())
                    _harness_note(start_ui())
                    ui_fails = 0
        except Exception as e:
            _harness_note("tick failed (continuing): %r" % (e,))
        time.sleep(every_s)


def status():
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
           "? { $_.CommandLine -like '*session.py*' -or $_.CommandLine -like '*manager.py*' "
           "-or $_.CommandLine -like '*advisor_api*' -or $_.CommandLine -like '*analytics.runner*' } | "
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


def main():
    ap = argparse.ArgumentParser(prog="runctl", description="start and stop tw_stack runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    from run_config import RUN
    for name in ("up", "session"):
        s = sub.add_parser(name)
        s.add_argument("campaigns", type=int, nargs="?", default=RUN["campaigns"])
        s.add_argument("turns", nargs="?", default=str(RUN["turns"]))
        s.add_argument("--factions", default=RUN["factions"],
                       help="all | key,key,...  ('all' plays every start baked at "
                            "--presave-radius; each start carries its own map)")
        s.add_argument("--retrain-every", type=int, default=RUN["retrain_every"])
        s.add_argument("--retrain-first", action="store_true",
                       default=RUN["retrain_first"],
                       help="also take the retrain window at campaign 1, instead of first "
                            "at campaign N+1")
        s.add_argument("--no-retrain-first", dest="retrain_first",
                       action="store_false",
                       help="skip the campaign-1 retrain even though run_config wants it")
        s.add_argument("--strategies", default=None)
        s.add_argument("--ruleset", default=None)
        s.add_argument("--presave-radius", type=_presave_radius,
                       default=RUN["presave_radius"],
                       help="sample starts from the baked trimmed saves of exactly this "
                            "radius; fails if none are baked at that radius. Fresh "
                            "campaigns are baked by bake.py, never played here")
        s.add_argument("--cold", action="store_true")
        s.add_argument("--width", type=int, default=0,
                       help="sample only starts whose (map, faction) has fewer than N "
                            "recorded campaigns, re-checked per campaign; the session "
                            "ends when none remain")
        s.add_argument("--ucb", type=float, default=None,
                       help="UCB1 start selector over settlements and lord levels "
                            "gained; C is the exploration constant")
        s.add_argument("--no-dev", action="store_true",
                       help="turn the diagnostic streams OFF -- they are on by default")
        if name == "up":
            s.add_argument("--shots", type=int, default=DEFAULT_SHOTS)
            s.add_argument("--port", type=int, default=DEFAULT_PORT)
            s.add_argument("--no-ui", action="store_true")
    sub.add_parser("down")
    sub.add_parser("status")
    h = sub.add_parser("harness")
    h.add_argument("--every", type=float, default=HARNESS_EVERY_S)
    a = ap.parse_args()
    if a.cmd == "status":
        print("\n".join(status()))
        return
    if a.cmd == "down":
        print("\n".join(down()))
        return
    if a.cmd == "harness":
        raise SystemExit(harness(a.every))
    strategies = a.strategies if a.strategies is not None else (
        None if a.cold else RUN["strategies"])
    ruleset = a.ruleset if a.ruleset is not None else (None if a.cold else RUN["ruleset"])
    common = dict(retrain_every=a.retrain_every, retrain_first=a.retrain_first,
                  cold=a.cold, dev=not a.no_dev,
                  factions=a.factions, strategies=strategies, ruleset=ruleset,
                  presave_radius=a.presave_radius, width=a.width, ucb=a.ucb)
    if a.cmd == "session":
        print("session -> %s" % start_session(a.campaigns, a.turns, **common))
        return
    print("\n".join(up(a.campaigns, a.turns, shots=a.shots, port=a.port,
                       with_ui=not a.no_ui, **common)))


if __name__ == "__main__":
    main()
