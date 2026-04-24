JuiceFS benchmark results
=========================

Dates:
* **tpu0 (local Redis):** 2026-04-16
* **tpu1 (remote Redis over intra-cluster network):** 2026-04-23
* **tpu2 + tpu3 (mounted, 4-node concurrent test):** 2026-04-23


Setup
-----

JuiceFS CE 1.3.1 with:
- **Metadata:** Redis on tpu0 (bound to `127.0.0.1` and the tpu0 internal IP).
- **Data backend:** GCS bucket `mfrs-tpu-cluster` (US-CENTRAL2, same region).
- **Auth:** Service account key file (see admin handbook for details).
- **Mount options (same on every node):**
  ```
  juicefs mount "redis://:${REDIS_PASSWORD}@<host>:6379/0" /jfs \
    -o allow_other \
    --cache-dir /var/jfsCache \
    --cache-size 40960 \
    --buffer-size 600 \
    --writeback \
    --upload-delay 1m \
    -d
  ```
  `<host>` is `localhost` on tpu0 and `tpu0` on the other nodes.

The `--writeback` and `--upload-delay 1m` options mean writes go to the
local cache first and are uploaded to GCS asynchronously. This is the
config we plan to run in production, so all benchmarks reflect this
behaviour.

### What this report compares

The original tpu0-only report established that JuiceFS is fast enough when
Redis is on localhost. The open question was how much that performance
degrades from the other VMs, where every metadata operation becomes a
network round-trip. This run extends the comparison to tpu1, then to
tpu2 and tpu3, so we cover the full cluster.

Per-node single-node benchmarks (Phases 1–4) were only re-run on tpu1
since tpu2 and tpu3 sit on the same intra-cluster network and behaved
identically to tpu1 under the 4-node concurrent test (Phase 5).

### Caveats

- **Local disk baselines vary between runs.** Both VMs have identical
  instance specs but the absolute numbers for `/tmp` can shift by a factor
  of 5+ depending on kernel page-cache warmth at the time of measurement.
  Compare relative ratios (JuiceFS / local) rather than raw numbers.
- **Warm vs cold cache.** "Warm" means the data is in the local JuiceFS
  cache and/or the kernel page cache on the VM being tested. Most writes
  land in cache first under `--writeback`, so reads immediately after a
  write are effectively warm. Cold tests flush both caches explicitly.
- **TPU env vars and non-interactive shells.** The TPU defaults
  (`TPU_VISIBLE_DEVICES=0`, etc.) are set via interactive shell init on
  every VM, but `ssh tpuN 'cmd'` runs a non-interactive shell that skips
  them — so JAX thinks it should coordinate multi-host init and hangs.
  This is unrelated to JuiceFS. For TPU benchmarks run over SSH, wrap in
  `tpu-device 0` or set `JAX_PLATFORMS=cpu`.


Phase 1: Baselines
------------------

### Redis latency

```
redis-cli -h <host> -a "$REDIS_PASSWORD" --latency-history -i 2
```

`<host>` is `localhost` on tpu0 and `tpu0` on tpu1. Numbers below are the
average ping round-trip over a few seconds of sampling.

| From | To (Redis host) | Avg latency   |
|------|-----------------|---------------|
| tpu0 | localhost       | ~0.03–0.05 ms |
| tpu1 | tpu0 (over LAN) | ~0.14–0.20 ms |

The remote path adds roughly **0.1–0.15 ms per Redis round-trip** — a ~3×
increase over localhost, but still well under the 1 ms target from the
original plan. Every JuiceFS metadata operation (file create, stat, open,
readdir, etc.) involves at least one Redis round-trip, so this is the
floor for metadata latency from each node.

### Local disk baseline (`juicefs bench` on `/tmp`)

```
juicefs bench /tmp/local-bench --big-file-size 256 --small-file-count 500
```

| Operation          | tpu0 /tmp  | tpu1 /tmp  |
|--------------------|------------|------------|
| Write big (256 MB) | 732 MB/s   | 793 MB/s   |
| Read big (256 MB)  | 1,077 MB/s | 1,286 MB/s |
| Write small file   | 1,087/s    | 1,211/s    |
| Read small file    | 8,246/s    | 9,465/s    |
| Stat file          | 19,805/s   | 101,275/s  |

The two VMs are the same instance type with identical boot disks; the
~5× stat gap is kernel page-cache variance from the measurement moment,
not a real hardware difference. Treat these as "local disk is in the
several-hundred-MB/s and tens-of-thousands-of-ops range on either VM."


Phase 2: JuiceFS built-in benchmark
------------------------------------

```
juicefs bench /jfs/bench-<hostname> --big-file-size 256 --small-file-count 500 --threads 1
```

| Operation          | Local disk (tpu0) | JFS (tpu0)  | JFS (tpu1)  | tpu1 / tpu0 |
|--------------------|-------------------|-------------|-------------|-------------|
| Write big (256 MB) | 732 MB/s          | 683 MB/s    | 1,064 MB/s  | 1.56×       |
| Read big (256 MB)  | 1,077 MB/s        | 1,567 MB/s* | 1,857 MB/s* | 1.19×       |
| Write small file   | 1,087/s           | 561/s       | 455/s       | 0.81×       |
| Read small file    | 8,246/s           | 1,217/s     | 1,088/s     | 0.89×       |
| Stat file          | 19,805/s          | 11,215/s    | 6,810/s     | 0.61×       |

*Reads were served from the JuiceFS cache immediately after writing, so
these reflect cache performance, not GCS throughput. `juicefs bench`
reports 0 GCS get/put operations for these runs.

Big-file I/O on tpu1 is actually a little **faster** than on tpu0 — this
is noise: big-file I/O goes through the local cache and page cache, not
Redis, so it's GCE disk throughput on that day. Small-file ops and stats
slow down on tpu1 in proportion to the ~3× Redis round-trip ratio, but
each op does more than one round-trip's worth of work (FUSE, local cache
writes, etc.), so the wall-clock slowdown is closer to 1.5–2× than 3×.


Phase 3: Realistic workloads
-----------------------------

### Metadata operations (file create/stat/delete)

```
python bench-metadata.py /jfs/bench-meta-<host>   # JuiceFS
python bench-metadata.py /tmp/bench-meta-local    # local /tmp
```

Creates, stats, and deletes 5,000 empty files sequentially.

| Operation   | Local (tpu0) | Local (tpu1) | JFS (tpu0) | JFS (tpu1) | tpu1 JFS / tpu0 JFS |
|-------------|--------------|--------------|------------|------------|---------------------|
| File create | 16,163/s     | 25,314/s     | 2,050/s    | 1,267/s    | 0.62×               |
| File stat   | 405,176/s    | 387,071/s    | 18,181/s   | 9,118/s    | 0.50×               |
| File delete | 108,229/s    | 112,766/s    | 2,576/s    | 1,380/s    | 0.54×               |

**This is the clearest signal of the remote-Redis penalty.** On pure
metadata work, tpu1 is ~1.6–2× slower than tpu0, as expected: each op is
dominated by Redis round-trip latency, and tpu1's round-trip is ~3× tpu0's.
(The full 3× doesn't show up because each op also has non-Redis costs —
FUSE, local inode bookkeeping, etc.)

Absolute numbers are still fine for our workload: 1,267 creates/sec means
a package install unpacking ~1,000 files takes under a second of
metadata-driven overhead.

### Training log appends

```
python bench-append.py /jfs/bench-append-<host>.log
python bench-append.py /tmp/bench-append-local.log
```

Writes 50,000 log lines with `flush()` every 100 lines.

|             | Local (tpu0) | Local (tpu1) | JFS (tpu0) | JFS (tpu1) |
|-------------|--------------|--------------|------------|------------|
| Appends/sec | 633K         | 580K         | 317K       | 477K       |

Appends are dominated by writes into the local write-back buffer rather
than Redis, so the "remote Redis" penalty is invisible here. Our training
loops write a line every few seconds at most; either node is many orders
of magnitude faster than needed.

### Checkpoint save/load (strux / np.savez_compressed)

```
python bench-checkpoint.py /jfs
python bench-checkpoint.py /tmp
```

Uses `strux.save` (which calls `np.savez_compressed`) and `strux.load`
to save/load transformer models. Three trials averaged.

| Model | Size   | Op   | Local (tpu0) | JFS (tpu0) | Local (tpu1) | JFS (tpu1) |
|-------|--------|------|--------------|------------|--------------|------------|
| Small | 494 KB | save | 0.037s       | 0.038s     | 0.041s       | 0.050s     |
| Small | 494 KB | load | 0.021s       | 0.008s     | 0.009s       | 0.039s     |
| Large | 18 MB  | save | 1.321s       | 1.319s     | 1.328s       | 1.340s     |
| Large | 18 MB  | load | 0.139s       | 0.133s     | 0.121s       | 0.131s     |

Checkpoint I/O is essentially identical across both VMs and both
filesystems. Save time is dominated by CPU-side `np.savez_compressed`
compression; under `--writeback` the compressed bytes hit the local
cache, so disk latency is effectively local on both nodes.

(tpu1 was run with `JAX_PLATFORMS=cpu` to sidestep the multi-host TPU
init issue described above; this affects JAX startup, not the file I/O
numbers we care about.)

### Package installation (uv pip install --reinstall --no-deps scipy)

|      | Local venv | JFS venv |
|------|------------|----------|
| tpu0 | 0.8s       | 10.3s    |
| tpu1 | 0.35s      | 11.5s    |

Two effects stack up in the JFS column: (1) uv cannot hardlink from its
`~/.cache/uv` (local ext4) to a JuiceFS target, so it falls back to a
full byte copy; (2) unpacking a wheel creates many small files, each a
Redis round-trip. tpu1 is only marginally slower than tpu0 despite the
higher Redis latency — the full-copy cost dominates.

`uv pip install jax[tpu]` (12 packages, cached): 2.4s on tpu0, similar
order on tpu1.

### Git clone (small repo)

```
git clone --depth 1 https://github.com/matomatical/strux.git <target>
```

|      | Local target | JFS target |
|------|--------------|------------|
| tpu0 | 0.3s         | 1.7s       |
| tpu1 | 0.34s        | 1.39s      |

Similar story: creating the git object files hits Redis once each.
Acceptable for small repos; large repos with tens of thousands of objects
would see proportionally more overhead.


Phase 4: Cold vs warm cache (venv imports)
------------------------------------------

This tests the most impactful scenario: importing a large library from a
venv stored on JuiceFS. Cold means all data must be fetched from GCS;
warm means it is already in the local JuiceFS block cache or the kernel
page cache.

### JAX venv (1.3 GB)

Test invocation:
```
# Cold: flush kernel page cache and JuiceFS block cache first
echo 3 | sudo tee /proc/sys/vm/drop_caches
sudo rm -rf /var/jfsCache/<uuid>/raw/

# Warm: run the import twice back-to-back

# Post-warmup: clear cache, run `juicefs warmup`, then import (jfs cache
# hot but kernel cache still cold)

time JAX_PLATFORMS=cpu <venv>/bin/python -c "import jax"
```

(`JAX_PLATFORMS=cpu` isolates filesystem performance — without it, TPU
init adds variable seconds that swamp the fs signal.)

| Scenario                   | tpu0  | tpu1       |
|----------------------------|-------|------------|
| Local disk (warm)          | 0.57s | 0.54s      |
| JFS warm                   | 0.69s | 0.69–0.78s |
| JFS cold                   | 41.7s | 33.3s      |
| JFS after `juicefs warmup` | 2.7s  | 2.2s       |

`juicefs warmup /jfs/bench-venvs/jax-venv --threads 4` pre-fetched the
venv from GCS in:
* **tpu0:** 45s for 3,653 files (1.2 GiB)
* **tpu1:** 69s for 4,460 files (1.2 GiB)

tpu1's warmup is slower largely because of metadata latency on many small
files, not GCS bandwidth — each file's metadata pre-fetch costs one
Redis round-trip.

Cold imports on tpu1 are ~20% **faster** than tpu0, which is noise:
cold-path cost is dominated by GCS `GetObject` throughput, and the same
bucket is equally reachable from both VMs.

### PyTorch/XLA venv (7.1 GB)

Only measured on tpu0 in the original report (warm 41.5s, cold 5m 18s
fetching at ~23 MB/s sustained from GCS). Not re-run on tpu1: warm time
is dominated by CUDA/XLA library CPU cost rather than filesystem, and
cold time is dominated by GCS bandwidth — neither should materially
differ between VMs. If this matters later, re-run.

### Real training workload: simplex

```
time <venv> simplex1.py --num-steps 1024 --vis-period 1024 --no-vis \
  --metrics-file <metrics-file>
```

| Setup                              | Wall time | Steady-state iter rate |
|------------------------------------|-----------|------------------------|
| tpu0 — local venv + local metrics  | 18.0s     | (not logged)           |
| tpu0 — JFS venv + JFS metrics      | 15.1s     | (not logged)           |
| tpu1 — JFS venv + JFS metrics*     | 20.8s     | ~195 it/s              |

*On tpu1 this was run with `tpu-device 0` to avoid the non-interactive
shell hang described in Setup.

Training is completely TPU-bound. The wall-clock variation between runs
(15s vs 18s vs 21s) is dominated by TPU initialisation and JAX trace /
compile variance, not filesystem overhead. The steady-state 195 it/s on
tpu1 is normal for this model on a single TPU v4 chip.


Phase 5: Multi-node concurrent metadata
---------------------------------------

To check whether multiple clients hammering Redis simultaneously degrade
each other, I ran `bench-metadata.py` on several nodes at the same time
and compared against the solo numbers.

```
# In one shell, all nodes simultaneously:
python3 /home/matt/tpus/admin-scripts/bench-metadata.py /jfs/bench-meta-<host> &
for t in 1 2 3; do
  ssh tpu$t 'python3 /tmp/bench-metadata.py /jfs/bench-meta-<host>' &
done
wait
```

### 2-way (tpu0 + tpu1)

| Operation   | tpu0 solo | tpu0 concurrent | tpu1 solo | tpu1 concurrent |
|-------------|-----------|-----------------|-----------|-----------------|
| File create | 2,050/s   | 2,341/s         | 1,267/s   | 1,251/s         |
| File stat   | 18,181/s  | 20,758/s        | 9,118/s   | 9,425/s         |
| File delete | 2,576/s   | 2,849/s         | 1,380/s   | 1,422/s         |

### 4-way (all nodes)

| Operation   | tpu0    | tpu1    | tpu2    | tpu3    | Combined |
|-------------|---------|---------|---------|---------|----------|
| File create | 2,093/s | 1,189/s | 1,196/s | 1,179/s | 5,657/s  |
| File stat   | 16,366/s | 8,344/s | 8,412/s | 8,343/s | 41,465/s |
| File delete | 2,458/s | 1,337/s | 1,306/s | 1,292/s | 6,393/s  |

### Assessment

- **Clean scaling, no meaningful contention.** Under full 4-node load,
  each remote node's throughput is within ~5–10% of its solo number —
  inside run-to-run noise. tpu0 is essentially unchanged.
- **tpu1, tpu2, and tpu3 are interchangeable.** All three non-tpu0 nodes
  land within ~2% of each other on every operation, so there's no need
  to individually re-run the single-node phases on tpu2/tpu3.
- **Redis on tpu0 is well within headroom.** At ~41K stats/sec combined
  it's nowhere near the tens-of-thousands-of-ops-per-core ceiling Redis
  comfortably handles.


Summary and assessment
----------------------

### What we learned about the remote case

- **Remote Redis adds ~0.1 ms per metadata round-trip.** The raw latency
  delta is small in absolute terms but makes metadata-heavy operations
  on non-tpu0 nodes run at roughly 50–60% of their tpu0 rate.
- **Data-path operations are unaffected.** Big-file I/O, appends,
  checkpoint save/load, and warm-cache reads perform the same on tpu1 as
  tpu0 — the cache layer absorbs everything and Redis is out of the
  critical path.
- **Four nodes concurrent ≈ each node solo.** Under full cluster load
  (~41K combined stats/sec on Redis), each remote node sees only ~5–10%
  throughput reduction vs. solo — well within noise. Redis on tpu0 is
  nowhere near saturation.
- **Cold-cache behaviour is set by GCS, not Redis.** The first pull of a
  big directory is the expensive step (33–45s for the JAX venv, 5+ min
  for PyTorch on tpu0) and happens once per node per file; `juicefs
  warmup` can hide this at boot.

### What still holds from the tpu0-only report

- Training workloads are TPU-bound; filesystem overhead is invisible.
- Warm-cache reads are near local disk speed.
- Checkpoint I/O matches local disk.
- Append throughput (hundreds of thousands/sec) vastly exceeds training
  log rates.

### Incidental findings during the tpu1 run

- **Redis client tools (`redis-tools`) are useful diagnostics** and should
  be installed on every client node (tpu1–3). Noted in the admin handbook
  under the JuiceFS install section.
- **TPU-touching commands via `ssh tpuN 'cmd'` need `tpu-device 0`** (or
  `JAX_PLATFORMS=cpu`) because interactive env-var defaults don't carry
  over non-interactive SSH. Confirmed working as intended.

### Coverage of planned workloads

| Workload                   | tpu0 | tpu1    | Verdict                            |
|----------------------------|------|---------|------------------------------------|
| JAX venv import            | ✓    | ✓       | Fine (warm ≈ local) on both        |
| PyTorch/XLA venv import    | ✓    | skipped | Expected similar; re-run if needed |
| Training loop (JAX)        | ✓    | ✓       | TPU-bound; no fs signal            |
| Training loop (PyTorch)    | —    | —       | Not measured directly              |
| Training log appends       | ✓    | ✓       | Vastly sufficient on both          |
| Checkpoint save/load       | ✓    | ✓       | Identical to local on both         |
| Package installs (uv)      | ✓    | ✓       | ~10s for scipy, ~acceptable        |
| Git operations             | ✓    | ✓       | Fine for small repos               |
| Multi-node concurrent (2×) | —    | ✓       | No measurable contention           |
| Multi-node concurrent (4×) | ✓    | ✓       | Clean scaling, ~5–10% penalty only |
| Large dataset reads        | N/A  | N/A     | Would be cold-cache/GCS-bound      |

### Conclusion

JuiceFS performance from a remote client (tpu1) is acceptable for our
use case. Metadata-heavy operations are ~1.5–2× slower than on tpu0
because of the extra Redis round-trip, but the absolute numbers remain
comfortable (~1,300 file creates/sec, ~9,000 stats/sec) and data-path
operations are indistinguishable from tpu0.

4-node concurrent load shows clean scaling — each remote node loses
only ~5–10% throughput vs. solo, and Redis on tpu0 is nowhere near
saturation. This clears the main open question from the earlier
tpu0-only report. Next steps: systemd mount deployment (at `/storage`
for the rollout), monitoring crons, and user home directory migration.
