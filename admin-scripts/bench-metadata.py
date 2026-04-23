"""
Benchmark metadata-heavy operations (file create, stat, delete) on a
target directory. Used for comparing local disk vs JuiceFS performance.

Usage:
    python bench-metadata.py /jfs/bench-meta
    python bench-metadata.py /tmp/bench-meta-local
"""

import os
import sys
import time

N = 5000

if len(sys.argv) < 2:
    print(f"usage: {sys.argv[0]} <target-dir>")
    sys.exit(1)

base = sys.argv[1]
os.makedirs(base, exist_ok=True)

for label, fn in [
    ("create", lambda i: open(f"{base}/f_{i:06d}", "w").close()),
    ("stat",   lambda i: os.stat(f"{base}/f_{i:06d}")),
    ("delete", lambda i: os.unlink(f"{base}/f_{i:06d}")),
]:
    start = time.time()
    for i in range(N):
        fn(i)
    elapsed = time.time() - start
    print(f"{label}: {N / elapsed:.0f} ops/sec ({elapsed:.2f}s)")

os.rmdir(base)
