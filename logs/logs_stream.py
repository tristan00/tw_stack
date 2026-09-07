from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

BULK_ROOT = common.STREAM_ROOT

LOGTAIL_EVERY = 3.0


def _append_tail(ctx, base, data: bytes) -> str:
    d = os.path.join(ctx.out_dir, "logs")
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, base + ".tail")
    with open(dst, "ab") as f:
        f.write(data)
    return dst


def run(ctx, log_dirs, poll_every: float = LOGTAIL_EVERY, own_slack: float = 2.0) -> None:
    os.makedirs(os.path.join(ctx.out_dir, "logs"), exist_ok=True)
    off = {}
    cutoff = time.time() - own_slack

    def is_ours(src: str) -> bool:
        try:
            return os.path.getctime(src) >= cutoff
        except OSError as e:
            sys.stderr.write("logs: is_ours ctime %s -> %s\n" % (os.path.basename(src), repr(e)[:60]))
            return False

    def targets() -> list:
        out = []
        for base in log_dirs:
            try:
                for f in os.listdir(base):
                    if f.lower().endswith((".txt", ".log")):
                        out.append(os.path.join(base, f))
            except Exception as e:
                sys.stderr.write("logs: cannot list %s -> %s\n" % (base, repr(e)[:60]))
        return out

    t_loop = time.time()
    sys.stderr.write("logs: tail poll starting -- every %.1fs over %d dir(s)\n"
                     % (poll_every, len(log_dirs)))
    while ctx.is_running():
        try:
            for src in targets():
                try:
                    if not os.path.exists(src):
                        continue
                    sz = os.path.getsize(src)
                    start = off.get(src)
                    if start is None:
                        begin = 0 if is_ours(src) else sz
                        off[src] = begin
                        ctx.emit({"t": ctx.now(), "kind": "log_open", "src": src,
                                  "from_byte": begin, "size": sz, "ours": begin == 0})
                        if begin == sz:
                            continue
                    if sz < off[src]:
                        off[src] = 0
                    if sz > off[src]:
                        with open(src, "rb") as f:
                            f.seek(off[src])
                            chunk = f.read(sz - off[src])
                        dst = _append_tail(ctx, os.path.basename(src), chunk)
                        ctx.emit({"t": ctx.now(), "kind": "log_tail", "src": src,
                                  "bytes": len(chunk), "dst": dst})
                        off[src] = sz
                except Exception as e:
                    ctx.on_error("logs:" + str(src), e)
        except Exception as e:
            ctx.on_error("logs", e)
        time.sleep(poll_every)
    common.waitlog("logs_tail_poll", time.time() - t_loop, True, "stopped")
