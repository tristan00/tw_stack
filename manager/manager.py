r"""manager -- the capture orchestrator: owns the run directory, the shared T0 clock, meta.json and
the thread-safe writers, and runs each capture stream as a daemon thread."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

RECORDER_VERSION = "v7"

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "campaigns"))
try:
    from splitter import CampaignTracker
except Exception as e:
    CampaignTracker = None
    sys.stderr.write("manager: CampaignTracker import failed (campaign-swap disabled) -> %s\n"
                     % repr(e)[:80])


class Ctx:
    """Per-stream context: a thin, game-free surface over the shared run state."""

    def __init__(self, out_dir, t0, stop_event, shot_req, emit, on_error,
                 on_state=None, swap=None):
        self.out_dir = out_dir
        self._t0 = t0
        self._stop = stop_event
        self.shot_req = shot_req
        self._emit = emit
        self._on_error = on_error
        self._on_state = on_state or (lambda *a, **k: False)
        self._swap = swap or (lambda: None)

    def emit(self, row):
        self._emit(row)

    def now(self):
        return round(time.time() - self._t0, 3)

    def is_running(self):
        return not self._stop.is_set()

    def on_error(self, where, exc):
        self._on_error(where, exc)

    def on_state(self, faction, subculture, turn):
        """Report one human-faction identity row to the tracker; True iff a dir swap is due."""
        return self._on_state(faction, subculture, turn)

    def swap(self):
        """Open a fresh run dir and re-point every stream's writer + out_dir to it."""
        self._swap()


class _Writer:
    """A thread-safe append-only JSONL writer for <out_dir>/<name>; every write flushes."""

    def __init__(self, out_dir, name):
        self.out_dir = out_dir
        self.name = name
        self._f = open(os.path.join(out_dir, name), "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._errs = 0

    def __call__(self, row):
        try:
            with self._lock:
                self._f.write(json.dumps(row) + "\n")
                self._f.flush()
        except Exception as e:
            self._errs += 1
            if self._errs == 1:
                sys.stderr.write("manager: writer(%s) write failed -> %s (further errors suppressed)\n"
                                 % (self.name, repr(e)[:80]))

    def close(self):
        try:
            with self._lock:
                self._f.close()
        except Exception as e:
            sys.stderr.write("manager: writer(%s) close failed -> %s\n" % (self.name, repr(e)[:80]))


def _writer(out_dir, name):
    """Returns a callable, closable _Writer for <out_dir>/<name>."""
    return _Writer(out_dir, name)


def _errlog(out_dir):
    """A never-fatal error sink -> <out_dir>/errors.log."""
    import traceback
    _errs = [0]

    def on_error(where, exc):
        try:
            with open(os.path.join(out_dir, "errors.log"), "a", encoding="utf-8") as f:
                f.write("%s %s: %s\n%s\n" % (time.strftime("%H:%M:%S"), where, exc,
                                             "".join(traceback.format_exception(exc))))
        except Exception as e:
            _errs[0] += 1
            if _errs[0] == 1:
                sys.stderr.write("manager: errors.log write failed -> %s (further errors suppressed)\n"
                                 % repr(e)[:80])
    return on_error


def _run_guarded(run, ctx, kwargs, name, on_error):
    """Run a stream, recording an unhandled crash to errors.log."""
    try:
        run(ctx, **kwargs)
    except Exception as e:
        on_error("stream:" + name, e)


CURRENT_POINTER = "CURRENT_RUN"


def write_current_pointer(out_root, out_dir):
    """Publish which run dir is LIVE, at a stable path the advisor can read."""
    try:
        with open(os.path.join(out_root, CURRENT_POINTER), "w", encoding="utf-8") as f:
            f.write(str(out_dir).replace("\\", "/"))
    except OSError as e:
        sys.stderr.write("manager: could not publish the current-run pointer -> %s\n" % repr(e)[:90])


def write_meta(out_dir, t0, meta_overrides=None):
    """Write <out_dir>/meta.json (geometry + environment + versions) and return the meta dict."""
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "t0_epoch": t0, "out": out_dir}
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.SetProcessDPIAware()
        meta["screen"] = [u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)]
    except Exception as e:
        meta["screen_error"] = str(e)
    meta.update(meta_overrides or {})
    try:
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
    except Exception as e:
        sys.stderr.write("manager: meta.json write failed -> %s\n" % repr(e)[:80])
    return meta


def _new_run_dir(out_root):
    """A fresh <out_root>/<YYYYmmdd_HHMMSS> run dir, numerically uniquified and already created."""
    base = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_root, base)
    n = 1
    while os.path.exists(out):
        out = os.path.join(out_root, "%s_%d" % (base, n))
        n += 1
    os.makedirs(out, exist_ok=True)
    return out


class Recording:
    """A live capture run: the run dir(s), the shared clock, the stream threads, stop() and the
    campaign-swap that re-points every stream to a fresh dir."""

    def __init__(self, out_dir, t0, stop_event, threads, events_writer, *,
                 out_root=None, writers=None, ctxs=None, on_error=None,
                 meta_overrides=None, restart_turn=1, reset_bus=None, swap_dirs=False):
        self.swap_dirs = swap_dirs
        self.out_dir = out_dir
        self.out_root = out_root
        self.t0 = t0
        self._stop = stop_event
        self._threads = threads
        self._events = events_writer
        self._writers = writers if writers is not None else {}
        self._all_writers = list(self._writers.values())
        self._ctxs = ctxs or []
        self._on_error = on_error
        self._meta_overrides = dict(meta_overrides or {})
        self._reset_bus = reset_bus or reset_bus_files
        self._tracker = CampaignTracker(restart_turn) if CampaignTracker is not None else None
        self._swap_lock = threading.Lock()
        self.campaign_index = 0
        self.swap_count = 0
        self.dirs = [out_dir]

    def observe_state(self, faction, subculture, turn):
        """Feed one human-faction identity row to the tracker; True iff a dir swap is due."""
        if self._tracker is None:
            return False
        try:
            started_new = self._tracker.observe(faction, subculture, turn)
        except Exception as e:
            sys.stderr.write("manager: tracker.observe failed -> %s\n" % repr(e)[:80])
            return False
        if not self.swap_dirs:
            return False
        return bool(started_new and self._tracker.index >= 1)

    def perform_swap(self):
        """Open a fresh run dir and re-point every stream's writer + out_dir to it; returns the
        new dir (or the current one if it could not be created)."""
        with self._swap_lock:
            try:
                new_dir = _new_run_dir(self.out_root)
            except OSError as e:
                sys.stderr.write("manager: campaign-swap could not create a run dir -> %s (staying in %s)\n"
                                 % (repr(e)[:80], self.out_dir))
                return self.out_dir
            prev_dir = self.out_dir
            self.swap_count += 1
            self.campaign_index = self._tracker.index if self._tracker is not None else self.campaign_index + 1

            self._events({"t": round(time.time() - self.t0, 3), "kind": "campaign_swap_out",
                          "to": new_dir, "campaign_index": self.campaign_index})

            new_writers = {name: _writer(new_dir, name) for name in self._writers}
            self._all_writers.extend(new_writers.values())

            new_on_error = _errlog(new_dir)

            for ctx, out_file in self._ctxs:
                ctx._emit = new_writers.get(out_file, new_writers["events.jsonl"])
                ctx.out_dir = new_dir
                ctx._on_error = new_on_error

            self._writers = new_writers
            self._events = new_writers["events.jsonl"]
            self._on_error = new_on_error
            self.out_dir = new_dir
            self.dirs.append(new_dir)
            write_current_pointer(self.out_root, new_dir)

            meta = dict(self._meta_overrides)
            meta["campaign_index"] = self.campaign_index
            meta["swapped_from"] = prev_dir
            write_meta(new_dir, self.t0, meta)
            self._events({"t": round(time.time() - self.t0, 3), "kind": "start",
                          "wall": time.strftime("%Y-%m-%d %H:%M:%S"), "out": new_dir,
                          "campaign_index": self.campaign_index, "swap": True})

            try:
                self._reset_bus()
            except Exception as e:
                sys.stderr.write("manager: reset_bus_files on swap failed -> %s\n" % repr(e)[:80])
            return new_dir

    def stop(self, join_timeout=3.0):
        """Flip the shared run flag, write the terminal 'stop' row, join the streams, close writers."""
        self._stop.set()
        time.sleep(0.8)
        self._events({"t": round(time.time() - self.t0, 3), "kind": "stop"})
        for th in self._threads:
            th.join(timeout=join_timeout)
        for w in self._all_writers:
            w.close()

    def alive(self):
        return [t.name for t in self._threads if t.is_alive()]


def start(out_root, streams, *, recorder_version, meta_overrides=None,
          restart_turn=1, reset_bus=None, swap_dirs=False):
    """Create the run dir + shared clock + meta, launch each stream ({"run": callable(ctx, **kwargs),
    "name", "out_file", "kwargs"}) as a daemon thread; returns a Recording handle."""
    t0 = time.time()
    out = _new_run_dir(out_root)
    stop_event = threading.Event()
    shot_req = threading.Event()
    on_error = _errlog(out)

    writers = {}

    def get_writer(name):
        if name not in writers:
            writers[name] = _writer(out, name)
        return writers[name]

    events = get_writer("events.jsonl")
    events({"t": 0, "kind": "start", "wall": time.strftime("%Y-%m-%d %H:%M:%S"), "out": out})

    base_meta = dict(meta_overrides or {})
    base_meta.setdefault("recorder_version", recorder_version)
    base_meta.setdefault("campaign_index", 0)
    write_meta(out, t0, base_meta)

    rec = Recording(out, t0, stop_event, [], events, out_root=out_root, writers=writers,
                    ctxs=[], on_error=on_error, meta_overrides=base_meta,
                    restart_turn=restart_turn, reset_bus=reset_bus, swap_dirs=swap_dirs)

    threads = []
    for s in streams:
        w = get_writer(s.get("out_file", "events.jsonl"))
        out_file = s.get("out_file", "events.jsonl")
        ctx = Ctx(out, t0, stop_event, shot_req, w, on_error,
                  on_state=rec.observe_state, swap=rec.perform_swap)
        rec._ctxs.append((ctx, out_file))
        nm = s.get("name", getattr(s["run"], "__name__", "stream"))
        th = threading.Thread(target=_run_guarded,
                              args=(s["run"], ctx, s.get("kwargs", {}), nm, on_error),
                              name=nm, daemon=True)
        threads.append(th)
    rec._threads = threads
    for th in threads:
        th.start()
    return rec


def reset_bus_files():
    """Truncate the two global append-only command-bus files; returns the bytes discarded."""
    import sys
    try:
        import bus
        paths = [bus.CMD_PATH, bus.OUT_PATH]
    except Exception as e:
        paths = ["D:/totalwar_runner/data/commands.txt", "D:/totalwar_runner/data/twcontrol.jsonl"]
        sys.stderr.write("manager: bus import failed, using hardcoded bus paths -> %s\n" % repr(e)[:80])
    total = 0
    for p in paths:
        try:
            if os.path.exists(p):
                total += os.path.getsize(p)
            open(p, "w", encoding="utf-8").close()
        except Exception as e:
            sys.stderr.write("manager: reset_bus_files(%s) failed -> %s (continuing)\n"
                             % (p, repr(e)[:80]))
    try:
        import bus_stats
        bus_stats.reset_suppression()
    except Exception as e:
        sys.stderr.write("manager: reset_suppression on bus reset failed -> %s (continuing)\n"
                         % repr(e)[:80])
    return total


def main():
    """CLI: assemble the real input/shots/logs streams and record until Ctrl-C."""
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    for repo in ("input", "shots", "logs", "ui-capture", "bus", "launcher", "decisions"):
        sys.path.insert(0, os.path.join(os.path.dirname(here), repo))
    import input_stream
    import shots_stream
    import logs_stream
    import ui_capture_stream
    import actions_stream
    import decisions_stream
    try:
        import config
        game_dir = config.GAME_DIR
        appdata = os.path.expandvars(r"%APPDATA%/The Creative Assembly/Warhammer3/logs")
        out_root = config.RUNS_ROOT
    except Exception as e:
        game_dir = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
        appdata = os.path.expandvars(r"%APPDATA%/The Creative Assembly/Warhammer3/logs")
        out_root = "D:/twdata/runs/human"
        sys.stderr.write("manager: config import failed, using hardcoded paths -> %s\n" % repr(e)[:80])

    argv = sys.argv[1:]
    shots_on = "--shots" in argv or "--debug" in argv
    input_on = "--input" in argv
    ui_on = "--ui" in argv
    actions_on = "--v6-actions" in argv
    decisions_on = "--no-decisions" not in argv
    shot_every = 60.0
    if "--shots" in argv:
        i = argv.index("--shots")
        if i + 1 < len(argv):
            try:
                shot_every = float(argv[i + 1])
            except ValueError:
                pass
    log_dirs = [game_dir, appdata]

    was = reset_bus_files()
    print("reset bus files (was %.1f MB)" % (was / (1024 * 1024)), flush=True)

    streams = [
        {"run": logs_stream.run, "name": "logs", "kwargs": {"log_dirs": log_dirs}},
    ]
    if decisions_on:
        streams.append({"run": decisions_stream.run, "name": "decisions",
                        "out_file": "decisions_stream.jsonl"})
    if input_on:
        streams.append({"run": input_stream.run, "name": "input"})
    if shots_on:
        streams.append({"run": shots_stream.run, "name": "shots", "kwargs": {"shot_every": shot_every}})
    if ui_on:
        streams.append({"run": ui_capture_stream.run, "name": "ui-capture",
                        "out_file": "ui_components.jsonl"})
    if actions_on:
        streams.append({"run": actions_stream.run, "name": "actions",
                        "out_file": "actions_stream.jsonl"})

    swap_dirs = "--swap-dirs" in argv
    rec = start(out_root, streams, recorder_version=RECORDER_VERSION, swap_dirs=swap_dirs,
                meta_overrides={"game_dir": game_dir, "appdata_logs": appdata,
                                "shots_enabled": shots_on, "ui_enabled": ui_on,
                                "actions_enabled": actions_on,
                                "decisions_enabled": decisions_on,
                                "input_enabled": input_on})
    write_current_pointer(out_root, rec.out_dir)
    print("RECORDING -> %s  (streams: %s)" % (rec.out_dir, [s["name"] for s in streams]), flush=True)
    print("  Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    rec.stop()
    print("DONE -> %s" % rec.out_dir, flush=True)


if __name__ == "__main__":
    main()
