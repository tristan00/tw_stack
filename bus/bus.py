"""twapi.bus -- the commands.txt -> twcontrol.jsonl command-bus CLIENT
(moved text-identically from api.py, R1)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

try:
    import msvcrt  # Windows byte-range file lock for the cross-process bus mutex
except ImportError:  # pragma: no cover
    msvcrt = None

from errors import TWError

# Optional, low-overhead call-measurement layer. Import defensively: if it is missing or fails
# to import, the bus runs EXACTLY as before (instrumentation is purely additive and must never
# break the bus). See bus_stats.py.
try:
    import bus_stats as _bus_stats
except Exception as _stats_exc:  # pragma: no cover - defensive: bus must run without it
    _bus_stats = None
    sys.stderr.write("bus: bus_stats instrumentation unavailable (running uninstrumented) -> %s\n"
                     % repr(_stats_exc)[:80])

# --------------------------------------------------------------------------------------
# Paths / cadence — hard-coded exactly as documented in scaffold_bus.md ("Files / paths").
# Both are forward-slash absolute paths that must match the mod side.
# --------------------------------------------------------------------------------------
CMD_PATH = "D:/totalwar_runner/data/commands.txt"      # client APPENDS command lines here
OUT_PATH = "D:/totalwar_runner/data/twcontrol.jsonl"   # mod APPENDS result JSON here
POLL_SECONDS = 1.0                                      # the mod re-reads CMD_PATH ~every 1s

# Client-side read cadence + default per-command timeout budget (scaffold_bus.md (c)).
# 0.05 (was 0.25): with the mod polling at 0.1s a reply is ready ~0.1s after send, so a 0.25s client
# poll added up to 0.25s of pure waiting per read. 0.05 brings a round-trip to ~0.1-0.15s.
READ_POLL_SECONDS = 0.05
DEFAULT_TIMEOUT = 30

# Mirror of the mod's Lua line pattern `^%s*(%d+)%s+(%S+)%s*(.*)$` — used to recover the
# highest seq already present in commands.txt so appends strictly increase.
_CMD_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s*(.*)$")


# ======================================================================================
# Command-bus CLIENT  (scaffold_bus.md sections a, b, c) — fully implemented.
# ======================================================================================
def _game_alive() -> bool:
    """Check that the WH3 process still exists.

    Dependency-free check that the WH3 process still exists (fail-open on error).
    Used to abort a bus/UI wait the INSTANT the game CTDs, instead of ticking down a
    30s timeout while nothing is listening -- the '5-10 min unnoticed failure' bug.

    Returns:
        True if Warhammer3.exe appears in tasklist output, or if the check
        itself errored (fail-open); False only when the process is confirmed gone.
    """
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Warhammer3.exe", "/NH"],
            capture_output=True, text=True, timeout=4,
        )
        return "warhammer3.exe" in (out.stdout or "").lower()
    except Exception as e:
        sys.stderr.write("bus: _game_alive tasklist check failed (fail-open) -> %s\n" % repr(e)[:80])
        return True


class _ProcLock:
    """Cross-process mutex over a lock file (msvcrt byte-range lock). Serialises the allocate-seq +
    append-command critical section ACROSS processes (the recorder's t_ui and the agent are two
    separate bus clients) so their seqs never collide. Fail-safe: if locking is unavailable or times
    out, it degrades to a no-op (single-client behaviour) rather than hang -- never worse than before.
    """

    def __init__(self, path: str) -> None:
        self._f = None
        if msvcrt is not None:
            try:
                self._f = open(path, "a+")
            except OSError as e:
                self._f = None
                sys.stderr.write("bus: _ProcLock could not open %s (single-client mode) -> %s\n"
                                 % (path, repr(e)[:60]))

    def __enter__(self):
        if self._f is None:
            return self
        deadline = time.time() + 10.0
        while True:
            try:
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:              # intentional: expected lock contention -> retry, not logged per-attempt
                if time.time() > deadline:
                    sys.stderr.write("bus: _ProcLock acquire timed out (10s) -> proceeding without lock\n")
                    return self          # fail-safe: proceed without the lock, never hang a send
                time.sleep(0.003)

    def __exit__(self, *exc) -> None:
        if self._f is None:
            return
        try:
            self._f.seek(0)
            msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError as e:
            sys.stderr.write("bus: _ProcLock unlock failed -> %s\n" % repr(e)[:60])


class Bus:
    """Synchronous client over the commands.txt -> twcontrol.jsonl command bus.

    send(channel, payload, timeout=...) appends `<seq> <channel> <payload>` to CMD_PATH,
    then polls the region of OUT_PATH appended after the write for the FIRST/LAST JSON line
    whose "seq" matches (and "cmd" == channel), returning that parsed dict.
    """

    def __init__(self, cmd_path: str = CMD_PATH, out_path: str = OUT_PATH,
                 poll_seconds: float = READ_POLL_SECONDS) -> None:
        """Initialise the bus client and seed the seq counter from the command file.

        Args:
            cmd_path: Path of the command file the client appends to
                (default CMD_PATH).
            out_path: Path of the JSONL result file the mod appends to
                (default OUT_PATH).
            poll_seconds: Sleep between result-file polls, in seconds
                (default READ_POLL_SECONDS = 0.25).

        Returns:
            None
        """
        self.cmd_path = cmd_path
        self.out_path = out_path
        self.poll_seconds = poll_seconds
        # Seed the monotonic counter from the tail of commands.txt so we never reuse or
        # decrease a seq (scaffold_bus.md: "read the tail of commands.txt and use max(seq)+1").
        self._seq = self._max_existing_seq()
        # seq allocation is lock-guarded: the fast-fail deadline machinery (randagent)
        # may issue a bus echo from the main thread while a budget-exceeded verb is
        # still finishing a send on its worker thread; each send scans for its OWN seq
        # from its OWN recorded offset (concurrent scans are independent), so the only
        # shared mutable state is the counter itself.
        self._seq_lock = threading.Lock()
        # cross-process mutex so a concurrent bus client (e.g. the recorder's t_ui) cannot allocate
        # the same seq and get its reply dropped by the mod's strict-increase gate.
        self._proc_lock = _ProcLock(cmd_path + ".lock")

    # ---- sequencing -------------------------------------------------------------------
    def _tail_seq(self) -> int:
        """Highest seq currently in the command file, read from its TAIL (fast). Under the locked
        append protocol seqs strictly increase in file order, so the last line holds the max."""
        try:
            with open(self.cmd_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return 0
                f.seek(max(0, size - 4096))
                data = f.read().decode("utf-8", "replace")
            for line in reversed(data.splitlines()):
                m = _CMD_RE.match(line)
                if m:
                    return int(m.group(1))
        except OSError as e:
            sys.stderr.write("bus: _tail_seq read failed (seq seeds 0) -> %s\n" % repr(e)[:60])
        return 0

    def _max_existing_seq(self) -> int:
        """Return the highest seq already present in the command file.

        Returns:
            The maximum leading integer of any command line in cmd_path,
            or 0 if the file is missing/unreadable or holds no command lines.
        """
        try:
            with open(self.cmd_path, "r", encoding="utf-8", errors="replace") as f:
                last = 0
                for line in f:
                    m = _CMD_RE.match(line)
                    if m:
                        n = int(m.group(1))
                        if n > last:
                            last = n
                return last
        except OSError as e:
            sys.stderr.write("bus: _max_existing_seq read failed (seq seeds 0) -> %s\n" % repr(e)[:60])
            return 0

    def _next_seq(self) -> int:
        """Increment and return the monotonic command sequence number.

        Returns:
            The next (strictly increasing) seq to stamp on a command line.
        """
        with self._seq_lock:
            self._seq += 1
            return self._seq

    # ---- result-file offset scanning --------------------------------------------------
    def _out_size(self) -> int:
        """Return the current byte size of the result file.

        Returns:
            os.path.getsize(out_path), or 0 if the file does not exist /
            cannot be stat'd.
        """
        try:
            return os.path.getsize(self.out_path)
        except OSError:
            return 0  # intentional: OUT_PATH not yet created -> size 0 (expected pre-mod-start; not logged)

    def _scan_result(self, offset: int, seq: int, channel: str | None) -> dict | None:
        """Scan the appended tail of the result file for a matching result line.

        Read only the bytes of OUT_PATH appended after `offset`; return the LAST JSON
        line whose seq==seq (and cmd==channel), else None. Binary read avoids Windows
        newline-translation skewing the byte offset (scaffold_bus.md (c) step 3-4).

        Args:
            offset: Byte offset into out_path recorded before the command was
                appended; scanning starts there.
            seq: The command sequence number a result line must carry.
            channel: The command name a result line's "cmd" must equal, or
                None to match on seq alone.

        Returns:
            The parsed dict of the LAST matching JSON line in the appended
            region, or None if no line matched (or the file was unreadable).
        """
        try:
            with open(self.out_path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return None  # intentional: hot 50ms poll; a persistent read error surfaces loudly as the send() timeout
        match = None
        for raw in data.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue  # intentional: skip partial/non-JSON result line, hot per-poll parse (no per-line log)
            if obj.get("seq") == seq and (channel is None or obj.get("cmd") == channel):
                match = obj  # take the LAST matching line in the appended region
        return match

    # ---- public send ------------------------------------------------------------------
    def send(self, channel: str, payload: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Append one command line and block until its result appears (or timeout).

        Returns the parsed result dict (e.g. {"seq","cmd","result","rtype","error","turn"}
        for an eval). Raises TWError on timeout.

        Args:
            channel: Command name understood by the mod side (the second field
                of the "<seq> <channel> <payload>" line), e.g. "eval".
            payload: Argument string appended after the channel; "" for none.
            timeout: Per-command wait budget in seconds
                (default DEFAULT_TIMEOUT = 30).

        Returns:
            The parsed result dict for this seq -- the mod's JSON line, e.g.
            {"seq": int, "cmd": str, "result": ..., "rtype": str,
            "error": ..., "turn": ...} for an eval.

        Raises:
            TWError: If the command file cannot be appended to, if the WH3
                process disappears while awaiting the result (fail-fast on
                CTD), or if no result arrives within `timeout` seconds.
        """
        # --- optional call-measurement wrapper + ACTIVE junk-find barrier (additive) ---------
        # When BOTH stats and guard are off (env BUS_STATS=0 AND BUS_GUARD=0, or the module is
        # unavailable) this is a single boolean check then a direct delegate -- no timing, no
        # bookkeeping, no barrier -- so the hot path is exactly the original behaviour.
        # When engaged:
        #   1. BARRIER: if this is a `find`/`tree` for a component proven ABSENT on this faction
        #      (a "dead key": >= THRESHOLD prior calls, ZERO hits), short-circuit it -- return a
        #      well-formed synthetic MISS immediately WITHOUT calling the mod, so the recorder's
        #      enumeration does not burn a multi-second timeout re-finding a non-existent component.
        #      A periodic re-probe still lets one call through occasionally to auto-un-suppress if
        #      the component starts existing. short_circuit_reply never raises -> on any failure it
        #      returns None and we fall through to a normal send (the barrier can't break the bus).
        #   2. MEASURE: time the real call and record ONE (channel, key, outcome, elapsed_ms) tuple,
        #      and feed the same outcome to the guard so it learns dead keys. Neither call raises.
        if _bus_stats is None or not _bus_stats.active():
            return self._send_impl(channel, payload, timeout)
        _key = _bus_stats.make_key(channel, payload)
        _sc = _bus_stats.short_circuit_reply(channel, payload, _key)
        if _sc is not None:
            return _sc
        _t0 = time.perf_counter()
        try:
            reply = self._send_impl(channel, payload, timeout)
        except TWError as exc:
            # startswith("bus timeout") uniquely marks the real timeout raise; CTD ("...process
            # gone...") and append failures ("cannot append") are genuine errors, not empty junk.
            outcome = "timeout" if str(exc).startswith("bus timeout") else "error"
            _bus_stats.record(channel, _key, outcome, (time.perf_counter() - _t0) * 1000.0)
            _bus_stats.note_outcome(channel, _key, outcome)
            raise
        except Exception:
            _bus_stats.record(channel, _key, "error", (time.perf_counter() - _t0) * 1000.0)
            _bus_stats.note_outcome(channel, _key, "error")
            raise
        _outcome = _bus_stats.classify_outcome(channel, reply)
        _bus_stats.record(channel, _key, _outcome, (time.perf_counter() - _t0) * 1000.0)
        _bus_stats.note_outcome(channel, _key, _outcome)
        return reply

    def _send_impl(self, channel: str, payload: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
        """The real send: append one command line and block until its result appears (or timeout).

        Unchanged behaviour, factored out of send() so the optional call-measurement wrapper can
        time it and classify its outcome. Raises TWError on append failure, CTD, or timeout.
        """
        # MULTI-CLIENT SAFE: allocate the seq + append the command line ATOMICALLY across processes.
        # The recorder's t_ui and the agent are two separate clients on this bus; without this both
        # seed _seq from the file max and collide, and the mod's strict-increase gate DROPS the
        # duplicate -> a 30s timeout. Under the cross-process lock we re-read the file's current max
        # (tail read) so seqs stay globally monotonic in file order. The (slow) result scan is OUTSIDE
        # the lock so it never blocks another client.
        try:
            seq, offset = self._alloc_and_append(channel, payload)
        except OSError as exc:
            raise TWError("bus: cannot append to %s: %s" % (self.cmd_path, exc))
        # (3) poll only the newly-appended region of the result file for our seq.
        # Poll fast (0.25s) for the result; every ~2s also confirm the game is still
        # alive so a CTD aborts in ~2s instead of ticking down the full `timeout`.
        deadline = time.time() + timeout
        next_alive = time.time() + 2.0
        while time.time() < deadline:
            result = self._scan_result(offset, seq, channel)
            if result is not None:
                return result
            now = time.time()
            if now >= next_alive:
                if not _game_alive():
                    raise TWError("bus: WH3 process gone while awaiting seq %d cmd %s "
                                  "-- failing fast (game CTD, not a %ss timeout)"
                                  % (seq, channel, timeout))
                next_alive = now + 2.0
            time.sleep(self.poll_seconds)
        raise TWError("bus timeout: no result for seq %d cmd %s" % (seq, channel))

    # ---- testable seam: the cross-process-atomic seq allocation + command append -------
    def _alloc_and_append(self, channel: str, payload: str) -> tuple[int, int]:
        """Allocate the next globally-monotonic seq and append the command line, ATOMICALLY across
        processes (under _seq_lock + the cross-process _proc_lock). Re-reads the file tail max inside
        the lock so seqs strictly increase in file order even with several bus clients. Returns
        (seq, out_offset), out_offset = result-file size before the append. Factored out of send() so
        the multi-client-safety critical section is testable without a running game."""
        with self._seq_lock, self._proc_lock:
            self._seq = max(self._seq, self._tail_seq()) + 1
            seq = self._seq
            offset = self._out_size()
            line = "%d %s %s\n" % (seq, channel, payload)
            with open(self.cmd_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        return seq, offset
