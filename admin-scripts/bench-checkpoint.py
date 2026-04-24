"""
Benchmark checkpoint save/load using strux (np.savez_compressed) with
transformer models of different sizes.

Requires the simplex project's dependencies (jax, strux, etc.) and its
transformer module on sys.path.

Usage:
    python bench-checkpoint.py /jfs
    python bench-checkpoint.py /tmp
"""

import os
import sys
import time

# simplex project provides the transformer module
sys.path.insert(0, os.path.expanduser("~/simplex"))

import jax
import strux
from transformer import SequenceTransformer

TRIALS = 3

configs = [
    {
        "label": "small (simplex-sized)",
        "kwargs": dict(
            num_symbols=3, sequence_length=10,
            num_blocks=4, embed_size=64,
            num_heads=1, head_size=8, mlp_size=256,
        ),
    },
    {
        "label": "large",
        "kwargs": dict(
            num_symbols=3, sequence_length=64,
            num_blocks=8, embed_size=256,
            num_heads=4, head_size=32, mlp_size=1024,
        ),
    },
]

if len(sys.argv) < 2:
    print(f"usage: {sys.argv[0]} <target-dir>")
    sys.exit(1)

base = sys.argv[1]
key = jax.random.key(0)

for cfg in configs:
    model = SequenceTransformer.init(key=key, **cfg["kwargs"])
    n_params = strux.tree_size(model)

    times_save = []
    times_load = []
    file_size = 0

    for trial in range(TRIALS):
        path = f"{base}/bench-ckpt-{trial}.npz"
        if os.path.exists(path):
            os.unlink(path)

        t0 = time.time()
        strux.save(path, model)
        t1 = time.time()
        times_save.append(t1 - t0)

        file_size = os.path.getsize(path)

        t0 = time.time()
        strux.load(path, template=model)
        t1 = time.time()
        times_load.append(t1 - t0)

        os.unlink(path)

    avg_save = sum(times_save) / TRIALS
    avg_load = sum(times_load) / TRIALS
    print(
        f"{cfg['label']}: {n_params:,} params, "
        f"{file_size / 1024:.0f} KB on disk, "
        f"save {avg_save:.3f}s, load {avg_load:.3f}s"
    )
