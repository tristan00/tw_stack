"""twapi.bus -- the commands.txt -> twcontrol.jsonl command-bus CLIENT."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

try:
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

from errors import TWError

try:
    import bus_stats as _bus_stats
except Exception as _stats_exc:  # pragma: no cover
    _bus_stats = None
    sys.stderr.write("bus: bus_stats instrumentation unavailable (running uninstrumented) -> %s\n"
                     % repr(_stats_exc)[:80])

# These absolute paths must match the ones hard-coded in mod/twcontrol.lua.
CMD_PATH = "D:/totalwar_runner/data/commands.txt"
OUT_PATH = "D:/totalwar_runner/data/twcontrol.jsonl"
POLL_SECONDS = 1.0

READ_POLL_SECONDS = 0.05
DEFAULT_TIMEOUT = 30

# Mirrors the mod's Lua line pattern `^%s*(%d+)%s+(%S+)%s*(.*)$` -- the two must stay in sync.
_CMD_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s*(.*)$")


def _game_alive() -> bool:
    """True if Warhammer3.exe is in tasklist output, or if the check itself errored (fail-open)."""
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
    """Cross-process mutex over a lock file (msvcrt byte-range lock); a no-op if locking is unavailable."""

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
            except OSError:
                if time.time() > deadline:
                    sys.stderr.write("bus: _ProcLock acquire timed out (10s) -> proceeding without lock\n")
                    return self
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
    """Synchronous client over the commands.txt -> twcontrol.jsonl command bus."""

    def __init__(self, cmd_path: str = CMD_PATH, out_path: str = OUT_PATH,
                 poll_seconds: float = READ_POLL_SECONDS) -> None:
        """Initialise the bus client and seed the seq counter from the command file."""
        self.cmd_path = cmd_path
        self.out_path = out_path
        self.poll_seconds = poll_seconds
        self._seq = self._max_existing_seq()
        self._seq_lock = threading.Lock()
        self._proc_lock = _ProcLock(cmd_path + ".lock")

    def _tail_seq(self) -> int:
        """Highest seq currently in the command file, read from its TAIL."""
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
        """Return the highest seq already present in the command file, or 0 if there is none."""
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
        """Increment and return the monotonic command sequence number."""
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _out_size(self) -> int:
        """Return the current byte size of the result file, or 0 if it cannot be stat'd."""
        try:
            return os.path.getsize(self.out_path)
        except OSError:
            return 0

    def out_offset(self) -> int:
        """Current byte offset of the mod's output stream, for wait_row."""
        return self._out_size()

    def wait_row(self, kinds, timeout: float, offset: int | None = None,
                 poll: float = 0.25, pred=None):
        """Block until the mod appends a row with cmd in `kinds` (and pred(row), if given); returns (row, new_offset), or (None, last_offset) on timeout."""
        kinds = frozenset(kinds)
        off = self._out_size() if offset is None else offset
        deadline = time.time() + timeout
        while True:
            try:
                size = os.path.getsize(self.out_path)
            except OSError:
                size = 0
            if size > off:
                with open(self.out_path, "rb") as f:
                    f.seek(off)
                    data = f.read()
                off = size
                for line in data.decode("utf-8", "replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("cmd") in kinds and (pred is None or pred(row)):
                        return row, off
            if time.time() >= deadline:
                return None, off
            time.sleep(min(poll, max(0.02, deadline - time.time())))

    def _scan_result(self, offset: int, seq: int, channel: str | None) -> dict | None:
        """Return the LAST JSON line appended to out_path after `offset` whose seq (and cmd) match, else None.

        Read binary: text mode's newline translation would skew the byte offset.
        """
        try:
            with open(self.out_path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return None
        match = None
        for raw in data.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("seq") == seq and (channel is None or obj.get("cmd") == channel):
                match = obj
        return match

    def send(self, channel: str, payload: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Append one command line and block until its result appears; returns the parsed result dict, raises TWError on timeout."""
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
            # The "bus timeout" prefix must stay in sync with the timeout raise in _send_impl.
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
        """Append one command line and block until its result appears; raises TWError on append failure, CTD, or timeout."""
        try:
            seq, offset = self._alloc_and_append(channel, payload)
        except OSError as exc:
            raise TWError("bus: cannot append to %s: %s" % (self.cmd_path, exc))
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

    def _alloc_and_append(self, channel: str, payload: str) -> tuple[int, int]:
        """Allocate the next seq and append the command line under both locks; returns (seq, result-file size before the append)."""
        with self._seq_lock, self._proc_lock:
            self._seq = max(self._seq, self._tail_seq()) + 1
            seq = self._seq
            offset = self._out_size()
            line = "%d %s %s\n" % (seq, channel, payload)
            with open(self.cmd_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        return seq, offset
