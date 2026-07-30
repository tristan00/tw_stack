r"""manager -- the capture orchestrator.

Ported from record.py:main + write_meta, but decomposed: the manager owns the run directory,
the single shared T0 clock, meta.json, the thread-safe writers, and the input->shots `shot_req`
coupling; each capture stream (input / shots / logs / -- later -- ui) is an independent module
it runs as a daemon thread, exactly record.py's "independent failsafe threads" design (so one
stream dying never stops the others, and the shared clock is trivially consistent).

Streams are passed IN (not hardcoded), so the manager has no game/bus dependency and is fully
testable offline; the CLI (`main`) is the only place that imports the concrete stream repos.

A stream is a dict:  {"run": callable(ctx, **kwargs), "out_file": "events.jsonl", "name": str,
                      "kwargs": {...}}
and receives a `Ctx` duck-typing:  emit(row) / out_dir / now() / is_running() / shot_req /
on_error(where, exc).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

# Recorder version stamped into meta.json. v6 = the decomposed tw_stack recorder (this pipeline);
# v5 was the monolith record.py. Bump on any change to capture behaviour.
RECORDER_VERSION = "v6"

# R3 campaign-swap: the live CampaignTracker lives in the campaigns/ repo (same boundary kernel the
# offline splitter uses, so live + offline splits agree). Imported best-effort: if unavailable the
# manager still records exactly as before, just single-campaign (observe_state becomes a no-op).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "campaigns"))
try:
    from splitter import CampaignTracker           # noqa: E402
except Exception as e:                             # noqa: BLE001 -- degrade to single-campaign
    CampaignTracker = None
    sys.stderr.write("manager: CampaignTracker import failed (campaign-swap disabled) -> %s\n"
                     % repr(e)[:80])


class Ctx:
    """Per-stream context: a thin, game-free surface over the shared run state.

    `out_dir` and `_emit` are MUTABLE: the R3 campaign-swap re-points them (to a fresh run dir and
    that dir's writer) when a new campaign starts, so a stream keeps calling ctx.emit()/reading
    ctx.out_dir and transparently lands in the new dir. Streams that cache os.path.join(ctx.out_dir,
    ...) once must re-read it each iteration to follow a swap (logs + shots do)."""

    def __init__(self, out_dir, t0, stop_event, shot_req, emit, on_error,
                 on_state=None, swap=None):
        self.out_dir = out_dir
        self._t0 = t0
        self._stop = stop_event
        self.shot_req = shot_req
        self._emit = emit
        self._on_error = on_error
        # R3 hooks (default: single-campaign no-ops, so a stream driven by a minimal FakeCtx or a
        # manager without a tracker behaves exactly as before).
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
        """Report one observed human-faction identity row to the run's CampaignTracker. Returns
        True iff this row STARTS a new campaign that requires an output-dir swap (never on the very
        first campaign). Advances the tracker but does NOT perform the swap -- the caller flushes
        pre-boundary bytes to the current dir, then calls swap()."""
        return self._on_state(faction, subculture, turn)

    def swap(self):
        """Perform the deferred campaign-swap: open a fresh run dir and re-point every stream's
        writer + out_dir to it. Called by the state stream at a byte-exact campaign boundary."""
        self._swap()


class _Writer:
    """A thread-safe append-only JSONL writer for <out_dir>/<name> (record.py:writer parity).

    A callable object (so existing `w(row)` call sites are unchanged) that additionally exposes
    close() -- needed by the R3 campaign-swap, which retires a run dir's writers when it opens a
    fresh dir. Every write flushes, so a swap never has to worry about buffered rows: whatever a
    writer has been handed is already on disk before it is retired."""

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
            if self._errs == 1:          # CRITICAL (silent data loss): log the FIRST failure only, suppress the rest
                sys.stderr.write("manager: writer(%s) write failed -> %s (further errors suppressed)\n"
                                 % (self.name, repr(e)[:80]))

    def close(self):
        try:
            with self._lock:
                self._f.close()
        except Exception as e:           # closing a writer must never be fatal to a swap/stop
            sys.stderr.write("manager: writer(%s) close failed -> %s\n" % (self.name, repr(e)[:80]))


def _writer(out_dir, name):
    """Factory kept for call-site parity; returns a callable, closable _Writer."""
    return _Writer(out_dir, name)


def _errlog(out_dir):
    """A never-fatal error sink -> <out_dir>/errors.log (record.py:log_err parity)."""
    import traceback
    _errs = [0]

    def on_error(where, exc):
        try:
            with open(os.path.join(out_dir, "errors.log"), "a", encoding="utf-8") as f:
                f.write("%s %s: %s\n%s\n" % (time.strftime("%H:%M:%S"), where, exc,
                                             "".join(traceback.format_exception(exc))))
        except Exception as e:
            _errs[0] += 1
            if _errs[0] == 1:            # last-resort sink itself failed: announce once on stderr so it is not invisible
                sys.stderr.write("manager: errors.log write failed -> %s (further errors suppressed)\n"
                                 % repr(e)[:80])
    return on_error


def _run_guarded(run, ctx, kwargs, name, on_error):
    """Run a stream, logging (never swallowing silently) an unhandled crash. record.py let a
    top-level raise kill a daemon thread with only a stderr traceback; here a dead stream is
    recorded to errors.log so a silently-missing stream is diagnosable."""
    try:
        run(ctx, **kwargs)
    except Exception as e:
        on_error("stream:" + name, e)


def write_meta(out_dir, t0, meta_overrides=None):
    """Write <out_dir>/meta.json (geometry + environment + versions) and return it. The caller
    supplies environment fields (recorder_version, game_dir, ...) via meta_overrides; the manager
    adds started / t0_epoch / out / screen. record.py:write_meta parity, minus the hardcoded env.

    Args:
        out_dir: The run directory.
        t0: The shared T0 epoch seconds.
        meta_overrides: Extra fields to merge (recorder_version, game_dir, shots_enabled, ...).

    Returns:
        The meta dict that was written.
    """
    meta = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "t0_epoch": t0, "out": out_dir}
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.SetProcessDPIAware()
        meta["screen"] = [u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)]
    except Exception as e:                       # non-Windows / headless -> record the reason
        meta["screen_error"] = str(e)
    meta.update(meta_overrides or {})
    try:
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
    except Exception as e:
        sys.stderr.write("manager: meta.json write failed -> %s\n" % repr(e)[:80])
    return meta


def _new_run_dir(out_root):
    """A fresh <out_root>/<YYYYmmdd_HHMMSS> run dir, uniquified with a numeric suffix so a swap in
    the same wall-clock second as the previous dir never collides. Created before returning."""
    base = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(out_root, base)
    n = 1
    while os.path.exists(out):
        out = os.path.join(out_root, "%s_%d" % (base, n))
        n += 1
    os.makedirs(out, exist_ok=True)
    return out


class Recording:
    """A live capture run: the run dir(s), the shared clock, the stream threads, stop(), and -- for
    R3 -- the live CampaignTracker + the campaign-swap that re-points every stream to a fresh dir.

    Swap design (why it works with the existing stream architecture):
      * Every stream writes through ctx.emit() -> ctx._emit (a _Writer), and file-writing streams
        (logs, shots) build their path from ctx.out_dir. Both are MUTABLE per-ctx attributes.
      * observe_state() feeds the tracker one human-faction row (fed by the logs stream, which is
        the only place the faction identity flows past). It returns True only on the row that starts
        a 2nd+ campaign.
      * The logs stream, holding that boundary line, flushes the pre-boundary bytes to the CURRENT
        dir, then calls ctx.swap() -> perform_swap(): a fresh dir is opened, new _Writers created,
        and EVERY ctx's _emit + out_dir re-pointed to it; the boundary line onward is written to the
        new dir. State bytes are thus split byte-exactly, matching the offline splitter.
      * Old writers are NOT closed on swap (only at stop): a concurrent emit from another stream that
        raced the swap still lands in the old dir rather than being dropped -- nothing is lost. The
        state stream drives the swap synchronously, so the campaign's own turn rows are never at
        risk. (Limitation: a handful of non-state input/ui rows within the sub-second swap window
        may land in the previous dir; documented, and never dropped.)"""

    def __init__(self, out_dir, t0, stop_event, threads, events_writer, *,
                 out_root=None, writers=None, ctxs=None, on_error=None,
                 meta_overrides=None, restart_turn=1, reset_bus=None):
        self.out_dir = out_dir
        self.out_root = out_root
        self.t0 = t0
        self._stop = stop_event
        self._threads = threads
        self._events = events_writer
        self._writers = writers if writers is not None else {}     # name -> current _Writer
        self._all_writers = list(self._writers.values())           # every writer ever opened (close at stop)
        self._ctxs = ctxs or []                                    # [(ctx, out_file_name)]
        self._on_error = on_error
        self._meta_overrides = dict(meta_overrides or {})
        self._reset_bus = reset_bus or reset_bus_files
        self._tracker = CampaignTracker(restart_turn) if CampaignTracker is not None else None
        self._swap_lock = threading.Lock()
        self.campaign_index = 0                                     # 0 == the initial dir
        self.swap_count = 0
        self.dirs = [out_dir]                                       # every run dir this session produced

    def observe_state(self, faction, subculture, turn):
        """Feed one human-faction identity row to the tracker (called from the logs stream).
        Returns True iff this row starts a 2nd+ campaign (i.e. a swap is due); advances the tracker
        but performs NO swap. No-op / False when the tracker is unavailable -> single-campaign."""
        if self._tracker is None:
            return False
        try:
            started_new = self._tracker.observe(faction, subculture, turn)
        except Exception as e:                       # a bad row must never break capture
            sys.stderr.write("manager: tracker.observe failed -> %s\n" % repr(e)[:80])
            return False
        # index 0 is the first campaign (keeps the initial dir); index >= 1 needs a fresh dir.
        return bool(started_new and self._tracker.index >= 1)

    def perform_swap(self):
        """Open a fresh run dir and re-point every stream's writer + out_dir to it. Idempotent under
        the swap lock; safe to call from a stream thread. Returns the new dir (or the current dir on
        failure -- capture continues regardless)."""
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

            # 1. mark the departure on the OLD events writer (still open) before re-pointing.
            self._events({"t": round(time.time() - self.t0, 3), "kind": "campaign_swap_out",
                          "to": new_dir, "campaign_index": self.campaign_index})

            # 2. fresh writers under the new dir (one per name the run uses); keep old ones OPEN so a
            #    racing emit lands in the old dir rather than dropping.
            new_writers = {name: _writer(new_dir, name) for name in self._writers}
            self._all_writers.extend(new_writers.values())

            # 3. a per-dir error sink so errors.log follows the current campaign.
            new_on_error = _errlog(new_dir)

            # 4. re-point every stream: its emitter -> the matching new writer, its out_dir -> new dir.
            for ctx, out_file in self._ctxs:
                ctx._emit = new_writers.get(out_file, new_writers["events.jsonl"])
                ctx.out_dir = new_dir
                ctx._on_error = new_on_error

            self._writers = new_writers
            self._events = new_writers["events.jsonl"]
            self._on_error = new_on_error
            self.out_dir = new_dir
            self.dirs.append(new_dir)

            # 5. fresh meta.json + a 'start' row for the new dir (so each dir reads like a run).
            meta = dict(self._meta_overrides)
            meta["campaign_index"] = self.campaign_index
            meta["swapped_from"] = prev_dir
            write_meta(new_dir, self.t0, meta)
            self._events({"t": round(time.time() - self.t0, 3), "kind": "start",
                          "wall": time.strftime("%Y-%m-%d %H:%M:%S"), "out": new_dir,
                          "campaign_index": self.campaign_index, "swap": True})

            # 6. the append-only bus files reset on a campaign boundary (mod resyncs on truncation).
            try:
                self._reset_bus()
            except Exception as e:                   # bus reset must never break a swap
                sys.stderr.write("manager: reset_bus_files on swap failed -> %s\n" % repr(e)[:80])
            return new_dir

    def stop(self, join_timeout=3.0):
        """Flip the shared run flag, write the terminal 'stop' row, join the streams, close writers."""
        self._stop.set()
        time.sleep(0.8)                          # let in-flight polls drain (record.py parity)
        self._events({"t": round(time.time() - self.t0, 3), "kind": "stop"})
        for th in self._threads:
            th.join(timeout=join_timeout)
        for w in self._all_writers:              # release every writer opened this session
            w.close()

    def alive(self):
        return [t.name for t in self._threads if t.is_alive()]


def start(out_root, streams, *, recorder_version, meta_overrides=None,
          restart_turn=1, reset_bus=None):
    """Create the run dir + shared clock + meta, then launch each stream as a daemon thread.

    Args:
        out_root: Parent directory; the run lands in <out_root>/<YYYYmmdd_HHMMSS>.
        streams: List of stream dicts (see module docstring).
        recorder_version: Stamped into meta.json (what `runs` gates on).
        meta_overrides: Extra meta fields (game_dir, appdata_logs, shots_enabled, ...).
        restart_turn: The CampaignTracker restart threshold (a same-faction restart from a turn
            <= this, after progressing past it, counts as a new campaign). Default 1.
        reset_bus: Callable invoked on each campaign-swap to reset the bus files; defaults to
            reset_bus_files. Injectable so an offline test can run swaps without touching the bus.

    Returns:
        A Recording handle (call .stop() to end it; it swaps run dirs on campaign boundaries).
    """
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
                    restart_turn=restart_turn, reset_bus=reset_bus)

    threads = []
    for s in streams:
        w = get_writer(s.get("out_file", "events.jsonl"))
        out_file = s.get("out_file", "events.jsonl")
        # R3: wire this ctx's state/swap hooks to the (already-constructed) Recording, so the logs
        # stream can drive the campaign-swap through ctx while the other streams follow the re-point.
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
    """Truncate the two GLOBAL append-only command-bus files to 0 bytes. These are transient RPC
    plumbing (client->mod commands + mod->client replies), NOT captured data, and are never rotated
    -> they grow unbounded (hit 2.46 GB and degraded the bus). Emptying them is safe because the mod
    supports TRUNCATION RESYNC (when the file's max seq drops below its last_seq it resets
    last_seq=0), so a fresh empty file re-syncs on the mod's next poll. Call at session START only.

    Paths come from the bus module's CMD_PATH/OUT_PATH when importable (so they stay in sync with the
    bus), else the two hard-coded defaults. Each file is truncated in its own try/except and is never
    fatal: a locked/unwritable file is logged to stderr (1 line) and skipped.

    Reusable by design so the future R3 campaign-swap can call it on a campaign switch too.

    Returns:
        Total bytes present across the files before truncation (for a size note); 0 if none/failed.
    """
    import sys
    try:
        import bus
        paths = [bus.CMD_PATH, bus.OUT_PATH]
    except Exception as e:                       # bus not on sys.path (e.g. reused standalone) -> defaults
        paths = ["D:/totalwar_runner/data/commands.txt", "D:/totalwar_runner/data/twcontrol.jsonl"]
        sys.stderr.write("manager: bus import failed, using hardcoded bus paths -> %s\n" % repr(e)[:80])
    total = 0
    for p in paths:
        try:
            if os.path.exists(p):
                total += os.path.getsize(p)
            open(p, "w", encoding="utf-8").close()   # truncate to 0 bytes (creates empty if missing)
        except Exception as e:                       # locked/unwritable -> log once and continue (never fatal)
            sys.stderr.write("manager: reset_bus_files(%s) failed -> %s (continuing)\n"
                             % (p, repr(e)[:80]))
    # A bus reset is a session/campaign boundary: clear the bus guard's SESSION-scoped dead-key
    # suppression too (a component absent for one faction may exist for the next campaign). Best-effort
    # hook, never hard-wired -- if bus_stats is unavailable the reset still succeeds.
    try:
        import bus_stats
        bus_stats.reset_suppression()
    except Exception as e:
        sys.stderr.write("manager: reset_suppression on bus reset failed -> %s (continuing)\n"
                         % repr(e)[:80])
    return total


def main():
    """CLI: assemble the real input/shots/logs streams and record until Ctrl-C (record.py parity).

    This is the ONLY place that imports the concrete stream repos + config; kept thin so the
    orchestrator itself stays game-free and testable.
    """
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    for repo in ("input", "shots", "logs", "ui-capture", "bus", "launcher"):  # launcher/ holds config.py
        sys.path.insert(0, os.path.join(os.path.dirname(here), repo))
    import input_stream
    import shots_stream
    import logs_stream
    import ui_capture_stream
    import actions_stream
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
    ui_on = "--no-ui" not in argv                          # ui-capture ON by default (record.py parity)
    actions_on = "--no-actions" not in argv                # cco actionable-state sweeps ON by default
    shot_every = 60.0                                       # an optional number after --shots overrides it
    if "--shots" in argv:
        i = argv.index("--shots")
        if i + 1 < len(argv):
            try:
                shot_every = float(argv[i + 1])
            except ValueError:
                pass  # intentional: non-numeric token after --shots (e.g. another flag) -> keep default
    log_dirs = [game_dir, appdata]

    was = reset_bus_files()                                 # empty the append-only bus files at START; mod resyncs
    print("reset bus files (was %.1f MB)" % (was / (1024 * 1024)), flush=True)

    streams = [
        {"run": input_stream.run, "name": "input"},
        {"run": logs_stream.run, "name": "logs", "kwargs": {"log_dirs": log_dirs}},
    ]
    if shots_on:
        streams.append({"run": shots_stream.run, "name": "shots", "kwargs": {"shot_every": shot_every}})
    if ui_on:
        streams.append({"run": ui_capture_stream.run, "name": "ui-capture",
                        "out_file": "ui_components.jsonl"})
    if actions_on:
        streams.append({"run": actions_stream.run, "name": "actions",
                        "out_file": "actions_stream.jsonl"})

    rec = start(out_root, streams, recorder_version=RECORDER_VERSION,
                meta_overrides={"game_dir": game_dir, "appdata_logs": appdata,
                                "shots_enabled": shots_on, "ui_enabled": ui_on,
                                "actions_enabled": actions_on})
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
