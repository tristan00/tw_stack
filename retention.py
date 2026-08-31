from __future__ import annotations

import fnmatch
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

DAY_S = 86400.0
SWEEP_MIN_GAP_S = 6 * 3600.0
ROLL_CAP_BYTES = 512 * 1024 * 1024
SWEEP_STAMP = os.path.join(common.LOGS_SERVICES, "retention_last_sweep.txt")

_DEV_LOGS = os.path.join(common.LOGS_DEV, "logs")
_STREAM_SHOTS = os.path.join(common.native(common.STREAM_ROOT), "shots")
_STREAM_LOGS = os.path.join(common.native(common.STREAM_ROOT), "logs")
_RUN_DIR = common.native(common.RUN_DIR)
_LOGS_LAUNCHER = common.native(common.LOGS_LAUNCHER)

SWEEP_CLASSES = (
    ("archive_script_logs", common.ARCHIVE_SCRIPT_LOGS, "*", 3),
    ("archive_bus", common.ARCHIVE_BUS, "*", 3),
    ("dev_tails", _DEV_LOGS, "*.tail", 3),
    ("run_shots", os.path.join(_RUN_DIR, "shots"), "*", 3),
    ("run_rolled", _RUN_DIR, "*.rolled", 3),
    ("dev_rolled", common.LOGS_DEV, "*.rolled", 3),
    ("stream_shots", _STREAM_SHOTS, "*", 3),
    ("v7_shots", common.V7_SHOTS_DIR, "*", 3),
    ("debug_timelines", common.DEBUG_DIR, "timeline_*.txt", 3),
    ("optuna_catboost", os.path.join(common.LOGS_SERVICES, "optuna_catboost"), "*", 3),
    ("optuna_gnn", os.path.join(common.LOGS_SERVICES, "optuna_gnn_greedy"), "*", 3),
    ("screens", common.native(common.SCREEN_DUMP_DIR), "*", 7),
    ("service_logs", common.LOGS_SERVICES, "*.log", 7),
    ("service_errs", common.LOGS_SERVICES, "*.err", 7),
    ("advisor_logs", common.LOGS_ADVISOR, "session_*.log", 7),
    ("advisor_errs", common.LOGS_ADVISOR, "session_*.err", 7),
    ("launcher_logs", _LOGS_LAUNCHER, "*.log", 7),
)

PROTECTED_NAMES = {"last_launch.json", "harness_last_relaunch.txt", "harness.log",
                   "current_session_log.txt", "meta.json",
                   "retention_last_sweep.txt"}

ROLL_FILES = tuple(os.path.join(_RUN_DIR, n) for n in (
    "trace.jsonl", "decisions_stream.jsonl", "events.jsonl", "turn_trail.jsonl",
    "locomotion.jsonl", "post_attack_trace.jsonl", "clear_screen_trace.jsonl",
    "errors.log", "loop_report.jsonl")) + tuple(
    os.path.join(common.LOGS_DEV, n) for n in (
        "ui_components.jsonl", "events.jsonl", "actions_stream.jsonl")) + (
    common.TRACE_PRERUN,)

DEAD = (os.path.join(_RUN_DIR, "decisions.sqlite"),
        os.path.join(_RUN_DIR, "decisions.sqlite-wal"),
        os.path.join(_RUN_DIR, "decisions.sqlite-shm"),
        os.path.join(_RUN_DIR, "analytics.sqlite"),
        os.path.join(_RUN_DIR, "analytics.sqlite-wal"),
        os.path.join(_RUN_DIR, "analytics.sqlite-shm"),
        os.path.join(common.LOGS_DEV, "actions.sqlite"),
        os.path.join(common.LOGS_DEV, "actions.sqlite-wal"),
        os.path.join(common.LOGS_DEV, "actions.sqlite-shm"),
        os.path.join(common.LOGS_DEV, "unhandled_panels.jsonl"),
        os.path.join(common.LOGS_DEV, "movie_overlay_capture.jsonl"))
DEAD_GLOBS = (os.path.join(common.LOGS_DEV, "bake_*.log"),)
DEAD_DIRS = (_STREAM_LOGS,)


def _pointer_target():
    try:
        with open(common.CURRENT_SESSION_LOG, encoding="utf-8-sig") as fh:
            p = fh.read().strip()
        return os.path.normcase(os.path.abspath(p)) if p else None
    except OSError:
        return None


def _protected(path, pointer):
    name = os.path.basename(path).lower()
    if name in PROTECTED_NAMES:
        return True
    if "battle_results" in name:
        return True
    if pointer and os.path.normcase(os.path.abspath(path)) == pointer:
        return True
    return False


def sweep(apply=False, log=print, force=False):
    now = time.time()
    if apply and not force:
        try:
            last = float(open(SWEEP_STAMP, encoding="utf-8").read().strip())
            if now - last < SWEEP_MIN_GAP_S:
                return None
        except (OSError, ValueError):
            pass
    pointer = _pointer_target()
    tally = {}
    for cls, root, pattern, days in SWEEP_CLASSES:
        root = common.native(root)
        if not os.path.isdir(root):
            continue
        cutoff = now - days * DAY_S
        n, b, kept = 0, 0, 0
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fnmatch.fnmatch(fn.lower(), pattern):
                    continue
                p = os.path.join(dirpath, fn)
                if _protected(p, pointer):
                    kept += 1
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime >= cutoff:
                    kept += 1
                    continue
                n += 1
                b += st.st_size
                if apply:
                    try:
                        os.remove(p)
                    except OSError:
                        n -= 1
                        b -= st.st_size
        if apply:
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                if dirpath != root and not dirnames and not filenames:
                    try:
                        os.rmdir(dirpath)
                    except OSError:
                        pass
        if n or kept:
            tally[cls] = (n, b, days)
            log("retention[%s] %s: %s %d files %.2fGB older than %dd, kept %d"
                % ("apply" if apply else "report", cls,
                   "deleted" if apply else "would delete", n, b / 1e9, days, kept))
    if apply:
        try:
            os.makedirs(os.path.dirname(SWEEP_STAMP), exist_ok=True)
            with open(SWEEP_STAMP, "w", encoding="utf-8") as fh:
                fh.write(str(now))
        except OSError:
            pass
    total_n = sum(v[0] for v in tally.values())
    total_b = sum(v[1] for v in tally.values())
    log("retention[%s] total: %d files %.2fGB"
        % ("apply" if apply else "report", total_n, total_b / 1e9))
    return tally


def roll_oversized(apply=True, log=print):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    rolled, skipped = [], []
    for p in ROLL_FILES:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size <= ROLL_CAP_BYTES:
            continue
        if not apply:
            rolled.append((p, size, "would roll"))
            continue
        dst = "%s.%s.rolled" % (p, stamp)
        try:
            os.replace(p, dst)
            rolled.append((p, size, "rolled"))
        except OSError:
            try:
                with open(p, "w", encoding="utf-8"):
                    pass
                rolled.append((p, size, "truncated (writer holds handle)"))
            except OSError as e:
                skipped.append((p, size, repr(e)[:60]))
    for p, size, how in rolled:
        log("retention roll: %s %.2fGB %s" % (os.path.basename(p), size / 1e9, how))
    for p, size, why in skipped:
        log("retention roll SKIPPED: %s %.2fGB %s" % (os.path.basename(p),
                                                      size / 1e9, why))
    return rolled, skipped


def purge_dead(apply=False, log=print):
    targets = list(DEAD)
    for g in DEAD_GLOBS:
        targets.extend(glob.glob(g))
    n, b, failed = 0, 0, []
    for p in targets:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        n += 1
        b += size
        log("dead[%s] %s %.2fGB" % ("apply" if apply else "report", p, size / 1e9))
        if apply:
            try:
                os.remove(p)
            except OSError as e:
                failed.append((p, repr(e)[:60]))
    for d in DEAD_DIRS:
        if not os.path.isdir(d):
            continue
        db, dn = 0, 0
        for dirpath, dirnames, filenames in os.walk(d):
            for fn in filenames:
                try:
                    db += os.path.getsize(os.path.join(dirpath, fn))
                    dn += 1
                except OSError:
                    pass
        n += dn
        b += db
        log("dead[%s] %s: %d files %.2fGB"
            % ("apply" if apply else "report", d, dn, db / 1e9))
        if apply:
            import shutil
            try:
                shutil.rmtree(d)
            except OSError as e:
                failed.append((d, repr(e)[:60]))
    for p, why in failed:
        log("dead FAILED: %s %s" % (p, why))
    log("dead[%s] total: %d files %.2fGB, %d failed"
        % ("apply" if apply else "report", n, b / 1e9, len(failed)))
    return n, b, failed


def boundary_pass(log=print):
    roll_oversized(apply=True, log=log)
    sweep(apply=True, log=log)


def main(argv):
    apply = "--apply" in argv
    dead = "--purge-dead" in argv
    if dead:
        purge_dead(apply=apply)
    roll_oversized(apply=apply)
    sweep(apply=apply, force=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
