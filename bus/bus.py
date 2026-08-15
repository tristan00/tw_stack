from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

try:
    import msvcrt
except ImportError:
    msvcrt = None

from errors import TWError

try:
    import bus_stats as _bus_stats
except Exception as _stats_exc:
    _bus_stats = None
    sys.stderr.write("bus: bus_stats instrumentation unavailable (running uninstrumented) -> %s\n"
                     % repr(_stats_exc)[:80])

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

CMD_PATH = common.BUS_CMD_PATH
OUT_PATH = common.BUS_OUT_PATH
SEND_LOG_PATH = common.BUS_SEND_LOG
_send_log_lock = threading.Lock()


def _log_send(seq, channel, payload):
    try:
        row = '{"seq":%d,"ts":%.4f,"cmd":"%s","payload":%s}\n' % (
            seq, time.time(), channel, json.dumps(str(payload)[:160]))
        with _send_log_lock:
            with open(SEND_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(row)
    except OSError:
        pass

READ_POLL_SECONDS = 0.01
DEFAULT_TIMEOUT = 30

_CMD_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s*(.*)$")


def _game_alive() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if str(proc.info.get("name") or "").lower() == "warhammer3.exe":
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Warhammer3.exe", "/NH"],
            capture_output=True, text=True, timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if out.returncode != 0:
            sys.stderr.write("bus: _game_alive tasklist unreadable (rc=%s, %s); fail-open\n"
                             % (out.returncode, (out.stderr or out.stdout or "").strip()[:100]))
            return True
        return "warhammer3.exe" in (out.stdout or "").lower()
    except Exception as e:
        sys.stderr.write("bus: _game_alive tasklist check failed (fail-open) -> %s\n" % repr(e)[:80])
        return True


class _ProcLock:

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
        t0 = time.time()
        contended = False
        deadline = time.time() + 10.0
        while True:
            try:
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
                if contended:
                    common.waitlog("proc_lock_acquire", time.time() - t0, True)
                return self
            except OSError:
                if time.time() > deadline:
                    sys.stderr.write("bus: _ProcLock acquire timed out (10s) -> proceeding without lock\n")
                    return self
                contended = True
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

    def __init__(self, cmd_path: str = CMD_PATH, out_path: str = OUT_PATH,
                 poll_seconds: float = READ_POLL_SECONDS) -> None:
        self.cmd_path = cmd_path
        self.out_path = out_path
        self.poll_seconds = poll_seconds
        self._seq = self._max_existing_seq()
        self._seq_lock = threading.Lock()
        self._proc_lock = _ProcLock(cmd_path + ".lock")
        self.consec_timeouts = 0

    def _tail_seq(self) -> int:
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


    def _out_size(self) -> int:
        try:
            return os.path.getsize(self.out_path)
        except OSError:
            return 0

    def out_offset(self) -> int:
        return self._out_size()

    def wait_row(self, kinds, timeout: float, offset: int | None = None,
                 poll: float = 0.25, pred=None):
        kinds = frozenset(kinds)
        off = self._out_size() if offset is None else offset
        t0 = time.time()
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
                        common.waitlog("wait_row", time.time() - t0, True, str(row.get("cmd")))
                        return row, off
            if self.consec_timeouts >= 3:
                common.waitlog("wait_row", time.time() - t0, False,
                               "bus_silent x%d %s" % (self.consec_timeouts, ",".join(sorted(kinds))))
                return None, off
            if time.time() >= deadline:
                common.waitlog("wait_row", time.time() - t0, False,
                               "timeout %s" % ",".join(sorted(kinds)))
                return None, off
            time.sleep(min(poll, max(0.02, deadline - time.time())))

    def _scan_result(self, offset: int, seq: int, channel: str | None) -> dict | None:
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
        try:
            seq, offset = self._alloc_and_append(channel, payload)
        except OSError as exc:
            raise TWError("bus: cannot append to %s: %s" % (self.cmd_path, exc))
        if self.consec_timeouts >= 3:
            timeout = min(timeout, 1.5)
        t0 = time.time()
        deadline = time.time() + timeout
        next_alive = time.time() + 2.0
        while time.time() < deadline:
            result = self._scan_result(offset, seq, channel)
            if result is not None:
                self.consec_timeouts = 0
                common.waitlog("send_reply_poll", time.time() - t0, True,
                               "seq=%d %s" % (seq, channel))
                return result
            now = time.time()
            if now >= next_alive:
                if not _game_alive():
                    common.waitlog("send_reply_poll", time.time() - t0, False,
                                   "game_gone seq=%d %s" % (seq, channel))
                    raise TWError("bus: WH3 process gone while awaiting seq %d cmd %s "
                                  "-- failing fast (game CTD, not a %ss timeout)"
                                  % (seq, channel, timeout))
                next_alive = now + 2.0
            time.sleep(self.poll_seconds)
        self.consec_timeouts += 1
        common.waitlog("send_reply_poll", time.time() - t0, False,
                       "timeout seq=%d %s consec=%d" % (seq, channel, self.consec_timeouts))
        raise TWError("bus timeout: no result for seq %d cmd %s" % (seq, channel))

    def send_batch(self, requests, timeout: float = DEFAULT_TIMEOUT) -> list:
        if not requests:
            return []
        _t0 = time.perf_counter()
        _keys = None
        if _bus_stats is not None and _bus_stats.active():
            try:
                _keys = [(ch, _bus_stats.make_key(ch, pl)) for ch, pl in requests]
            except Exception:
                _keys = None

        def _note(outcome):
            if not _keys:
                return
            ms = (time.perf_counter() - _t0) * 1000.0
            for _ch, _k in _keys:
                try:
                    _bus_stats.record(_ch, _k, outcome, ms)
                    _bus_stats.note_outcome(_ch, _k, outcome)
                except Exception:
                    pass
        with self._seq_lock, self._proc_lock:
            t = self._tail_seq()
            if t + 1000 < self._seq:
                sys.stderr.write("bus: seq reseeded %d -> %d (command file rotated)\n"
                                 % (self._seq, t))
                self._seq = t
            self._seq = max(self._seq, t)
            offset = self._out_size()
            seqs = []
            lines = []
            for channel, payload in requests:
                self._seq += 1
                seqs.append(self._seq)
                lines.append("%d %s %s\n" % (self._seq, channel, payload))
            try:
                with open(self.cmd_path, "a", encoding="utf-8") as f:
                    for line in lines:
                        f.write(line)
                        f.flush()
                    os.fsync(f.fileno())
            except OSError as exc:
                raise TWError("bus: cannot append batch to %s: %s" % (self.cmd_path, exc))
        wanted = {s: requests[i][0] for i, s in enumerate(seqs)}
        found: dict = {}
        if self.consec_timeouts >= 3:
            timeout = min(timeout, 1.5)
        t_wait = time.time()
        deadline = time.time() + timeout
        next_alive = time.time() + 2.0
        while time.time() < deadline:
            for obj in self._scan_lines(offset):
                s = obj.get("seq")
                if s in wanted and s not in found and obj.get("cmd") == wanted[s]:
                    found[s] = obj
            if len(found) == len(wanted):
                _note("hit")
                common.waitlog("batch_reply_poll", time.time() - t_wait, True,
                               "%d replies" % len(seqs))
                return [found[s] for s in seqs]
            now = time.time()
            if now >= next_alive:
                if not _game_alive():
                    _note("error")
                    common.waitlog("batch_reply_poll", time.time() - t_wait, False,
                                   "game_gone missing=%s" % sorted(set(wanted) - set(found)))
                    raise TWError("bus: WH3 process gone while awaiting batch seqs %s"
                                  % sorted(set(wanted) - set(found)))
                next_alive = now + 2.0
            time.sleep(self.poll_seconds)
        _note("timeout")
        common.waitlog("batch_reply_poll", time.time() - t_wait, False,
                       "timeout %d/%d replies" % (len(found), len(wanted)))
        raise TWError("bus batch timeout: %d/%d replies, missing seqs %s"
                      % (len(found), len(wanted), sorted(set(wanted) - set(found))))

    def _scan_lines(self, offset: int):
        try:
            with open(self.out_path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return
        for line in data.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def _alloc_and_append(self, channel: str, payload: str) -> tuple[int, int]:
        with self._seq_lock, self._proc_lock:
            t = self._tail_seq()
            if t + 1000 < self._seq:
                sys.stderr.write("bus: seq reseeded %d -> %d (command file rotated)\n"
                                 % (self._seq, t))
                self._seq = t
            self._seq = max(self._seq, t) + 1
            seq = self._seq
            offset = self._out_size()
            line = "%d %s %s\n" % (seq, channel, payload)
            with open(self.cmd_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        _log_send(seq, channel, payload)
        return seq, offset
