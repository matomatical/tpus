"""
Benchmark append-heavy writes simulating training log output. Writes
50,000 lines with periodic flushes, as a training loop might.

Usage:
    python bench-append.py /jfs/bench-append.log
    python bench-append.py /tmp/bench-append-local.log
"""

import os
import sys
import time

N = 50000

if len(sys.argv) < 2:
    print(f"usage: {sys.argv[0]} <target-file>")
    sys.exit(1)

path = sys.argv[1]

with open(path, "w") as f:
    start = time.time()
    for i in range(N):
        f.write(f"step={i} loss=0.{i:06d} lr=1e-4 ts={time.time()}\n")
        if i % 100 == 0:
            f.flush()
    elapsed = time.time() - start
    print(f"{N / elapsed:.0f} appends/sec ({elapsed:.2f}s)")

os.unlink(path)
