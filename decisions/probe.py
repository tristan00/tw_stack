from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
from bus import Bus

META = ("local o=cm:get_local_faction(true) %s local m=getmetatable(o) "
        "if not m then return 'no metatable' end local k={} "
        "for n,v in pairs(m) do if type(v)=='function' then k[#k+1]=n end end "
        "table.sort(k) return #k..' methods: '..table.concat(k,' ')")

CCO = "return tostring(common.get_context_value(%s))"


def log(msg):
    sys.stderr.write("%.3f  probe %s\n" % (time.time(), msg))


def flatten(src):
    return " ".join(l.strip() for l in src.strip().splitlines() if l.strip())


def run(bus, lua, timeout, channel="eval"):
    t0 = time.time()
    reply = bus.send(channel, flatten(lua), timeout=timeout) or {}
    ms = (time.time() - t0) * 1000
    err = reply.get("error")
    if err:
        verdict, value = "EXC", str(err)
    else:
        v = reply.get("result")
        if v is None:
            verdict, value = "NIL", ""
        elif v == "false":
            verdict, value = "FALSE", "false"
        elif v == "":
            verdict, value = "EMPTY", ""
        else:
            verdict, value = "OK", v
    log("eval exit %.0f ms %s" % (ms, verdict))
    return verdict, value


def main():
    ap = argparse.ArgumentParser(
        prog="probe",
        description="run one expression against the live game and report EXC, NIL, FALSE "
                    "and EMPTY distinctly -- the eval channel returns nil for all four")
    ap.add_argument("expr")
    ap.add_argument("--cco", action="store_true",
                    help="wrap the expression in common.get_context_value, the "
                         "single-argument form that needs no context id")
    ap.add_argument("--meta", action="store_true",
                    help="enumerate the metatable methods of the expression's object, "
                         "written as a suffix to the local faction (empty for the faction "
                         "itself, e.g. ':faction_leader()')")
    ap.add_argument("--mod", action="store_true",
                    help="evaluate inside the mod environment, where core, "
                         "find_uicomponent and UIComponent exist; the eval sandbox "
                         "has none of them")
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()
    t0 = time.time()
    if a.meta:
        lua = META % (("o=o" + a.expr) if a.expr.strip() else "")
    elif a.cco:
        lua = CCO % ("'%s'" % a.expr.replace("'", "\\'"))
    else:
        lua = a.expr
    bus = Bus()
    verdict, value = run(bus, lua, a.timeout, "modeval" if a.mod else "eval")
    print("%-5s %s" % (verdict, value))
    log("exit %.0f ms" % ((time.time() - t0) * 1000))
    return 0 if verdict != "EXC" else 1


if __name__ == "__main__":
    sys.exit(main())
