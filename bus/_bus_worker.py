r"""Subprocess worker for test_bus.py: append K commands to a shared cmd file via the bus's
cross-process-atomic allocate+append. Run as its own OS process to exercise the _ProcLock across
processes (threads alone would be covered by the in-process _seq_lock and wouldn't test it).

    python _bus_worker.py <cmd_path> <out_path> <k>
"""
import sys

import bus

cmd_path, out_path, k = sys.argv[1], sys.argv[2], int(sys.argv[3])
b = bus.Bus(cmd_path=cmd_path, out_path=out_path)
for i in range(k):
    b._alloc_and_append("test", str(i))
