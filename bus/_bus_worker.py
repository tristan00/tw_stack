import sys

import bus

cmd_path, out_path, k = sys.argv[1], sys.argv[2], int(sys.argv[3])
b = bus.Bus(cmd_path=cmd_path, out_path=out_path)
for i in range(k):
    b._alloc_and_append("test", str(i))
