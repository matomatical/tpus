Storage options
===============

Options for storage on MFR's tiny TPU cluster.

Experience
----------

Previous solution:

* tpu0 hosted an NFS server serving a folder /cluster/home on its boot disk.
* tpu{1,2,3} mounted NFS drive at /cluster/home.
* All user home directories stored in /cluster/home, shared state.
* Eventually lost the cluster:
  * Due to a mix of experiment logs and syslogs blowing up (see healthagent-oom
    issue) tpu boot disks became full.
  * TPU VMs require creating a file in order to accept incoming SSH. So cluster
    became unreachable with full disk.
  * TPU VM disks are not detachable after TPU has been created.
  * TPU VMs are not restartable from GCP console, only way to recover cluster
    is to recreate TPUs, losing old data.

Current solution:

* Each VM has isolated 100GB boot disk.
  * System files take maybe ~25GB, the rest is free for venvs, code, experiment
    artifacts.
* Because storage is isolated, there is about 300 GB available for venvs and
  experiment outputs (venvs have to be duplicated, but experiment outputs do
  not).
* Because they are isolated, to run a training script on different VMs means
  manually moving code/venvs between VMs, and later pooling data from
  separate VMs.
* Because of the risk of data loss, students are advised not to treat the
  cluster as safe storage: Don't leave important files/code/outputs there
  without backup.
* We are now on top of the syslog explosion issue and the only major source of
  large files is from venvs and training outputs (see below for more details).

Describing cluster usage
---------------------

The kinds of files we store:
* Python virtual environments, Most JAX venvs are 0.5-1GB. A PyTorch/XLA venv
  is inexplicably 7GB+ I guess because of a bunch of cuda stuff.
* Python code, misc scripts (git repositories) for empirical machine learning
  research projects.
* When experiments run, they typically run for 0-4 hours and create various
  files (training files):
  * Appending to output logs e.g. storing loss and many other metrics every
    few steps.
  * Appending to W&B local cache; temporary files (unfortunately generally
    W&B seems to be quite inefficient in data storage, redundantly
    representing the same data many times on disk in its logs, plus our own
    logs it adds up; not all students use W&B though.)
  * Occasionally checkpoints of model weights, mostly but not necessarily for
    small models <100MB, which could be useful for resuming training or
    evaluating models from different points during training.
* Miscellaneous files for other personal projects, e.g. this repository for
  administering the cluster. These are not the focus and don't need much
  storage anyway.
* Thinking ahead: We might need to account for storing experiment *inputs.*
  * Training data (never so far, but maybe in the future). So far our
    experiments use synthetic data that is generated in memory on the fly.
  * Large model weight files (never so far, maybe in the future). So far our
    experiments use models trained from scratch and anyway they are small.
* We can periodically archive training files and clean up old venvs, but the
  most important thing is that we don't need to do this too often and run out
  of space in the middle of a routine sweep.

How we currently use the TPUs:

* TPU devices used for training runs like 10-15% of the time, ideally more in
  future but we're working towards that
* So far amost always we are using the 4 TPU devices on each VM independently
  and no communication between VMs. CPU is not being fully utilised. So if this
  continues, network and CPU are substantially free.
* The TPU VMs are persistent and run for weeks/months without needing restarts
  in my experience so far. So I think we don't need to worry too much about
  'what if one node goes down'. It's just that if they all get into an
  unreachable state, GCP doesn't provide nice tools for recovering the disk, so
  we might lose data. Losing the data on the cluster is not the end of the
  world because mostly it can be recomputed anyway.
* Eventually we might want to use preemptible TPU VMs which are also part of
  our TPU allocation; but currently we only use a single persistent TPU v4-32
  (4 VM) cluster that is available 24/7.

Some design considerations
--------------------------

API features:

* Shared working directories: Users can work on one machine and their file
  edits, venv installs etc. are available on other machines.
  * This is the core feature.
  * The reason we need this is to make it as easy as possible to launch sweeps
    across the whole cluster without having to carefully sync up environments
    by hand. If there is another way to do that, the shared storage requirement
    can be relaxed.
* (Ideally) The solution works transparently as a filesystem that just has a
  bunch of storage available and users don't have to think about it too often,
  sysadmin can do the hard work to make it intuitive for users.
* (Ideally) Having said that ideally not *too much* set-up time and ongoing
  maintenance overhead for sysadmins (me & you).

Performance features:

* Fast: Loading venvs, launching training runs, should not be a substantial
  bottleneck that it slows down training runs.
* (Ideally) Ability to have more total and per-VM storage than current cluster
  has.
* (Maybe) nice to support some kind of per-user storage quota to prevent a
  single user taking up the whole cluster, but this is not a priority at this
  stage.

Reliability features:

* (Ideally) At least some of the storage should be persistent, in that if the
  cluster goes down for whatever reason we can mount the drive somewhere else
  and recover the data. This is not true of TPU boot disks themselves.

Cost efficient:

* Should make good use of TPU boot disks (as cache if not for storage itself).
  They are valuable high-speed storage and don't want to pay for storage before
  using them to their fullest.
* Don't pay a lot for always-available high throughput when high traffic is
  very rare.
* Don't pay for high volume storage we are not using. Ideally expandable over
  time.


Options brainstorm
------------------

Read this: https://docs.cloud.google.com/tpu/docs/storage-options

### Managed storage

Filestore

* How much would this cost for hos much storage?
  * Minimum 1TB for "Basic" tier is ~$160-200/month.
* Could it be attached read/write to the entire cluster?
  * Yes, it is the "gold standard" for managed NFS on GCP.
* What would speeds be like?
  * Very high, but expensive for a small cluster.

### Bucket storage

GCS buckets:

* Object storage is maybe suitable for writing large files like saving model
  checkpoints, individual training file outputs.
* Not suitable for mutable files, since the file would be 
* As I understand this is the cheapest option per byte though so for bulk
  storage is a good solution.

GCS FUSE:

* This is the same as buckets but with a filesystem API.
* Better than buckets because more transparent for users.
* But, as above, not suitable for e.g. log files we append to frequently, since
  it recreates the files every time they are modified, also not suitable for
  code, git repositories generally, or venvs or anything with lots of small
  files or mutable files generally.

JuiceFS (GCS + Redis/SQL):

* POSIX filesystem built on top of GCS. Metadata is stored in a fast database
  (Redis), data in GCS.
* Pros:
  * Benefits of GCS including price and pay for what you use
  * Much faster than GCS FUSE for small files/venvs.
    * How?
    * What about appending to log files?
* Choice of where to run the metadata engine:
  * Managed Redis engine via GCP
    * More expensive
  * Provision a separate small VM
  * Could host it on the TPU VM itself:
    * Creates a risk of the cluster going down and losing the metadata
      * This could be managed by frequently backing up snapshots of the
        metadata so that a recent version can be restored, this probably can
        use GCP buckets and therefore it would be cheap
    * Uses the TPUs interconnect and CPU a lot
      * This is fine because they are mostly not at capacity with current
        workloads
      * Could potentially run a distributed metadata storage to soften any lag
        impact

### Self-managed

Persistent disk attached to TPU VM cluster directly:

* Unfortunately, persistent disks can't be attached to multi-VM cluster in
  write mode.
* This could help for read-only data like pretrained model weights or large
  training data sets, if we need those in future.

Persistent disk attached to tpu0, serve via NFS to other machines.

* This appears to solve the write mode issue since persistent disks can be
  attached to individual machines in write mode.
* However, unfortunately, there does not seem to be any way to attach a disk to
  a single TPU VM within the cluster.

Persistent disk attached to a small compute VM, NFS to all TPU VMs.

* This actually does solve the write mode issue.
* Unfortunately, the smaller and cheaper VMs have bandwidth limits that make
  read/write a major bottleneck. It might make loading venvs on the TPU VMs
  take ages when launching a training run; and when multiple jobs are writing
  large log files it could slow things down a lot (let's look into how bad!).

Persistent disk attached to a more powerful compute VM, NFS to all TPU VMs.

* Specifically something like an `e2-standard-2` with a Balanced Persistent
  Disk.
  * Gemini mentions to mount on TPUs with `soft,intr,timeo=50` to prevent VM
    lockups if the server goes down. Server going down seems rare?
* This solves the write mode issue and the bandwidth issue.
* However a lot of the time we would not be using the bandwidth, so it might
  not be cost effective (let's look into that!)
* If I understand correctly, this essentially recreates filestore; if so we
  should compare to that as a cost baseline.

### Distributed architectures

Syncthing / Mutagen:

* The basic idea is to set up certain folders to be bidirectionally synced
  automatically.
* Features:
  * Uses local disks for maximum speed. No network latency for file imports.
  * No single point of failure for training runs.
  * Still limited by the 100GB local boot disk size.
* Can lead to new issues:
  * Conflict resolution (editing the same file on two VMs).
* Could be good for code and small venvs.

Distributed storage cluster options (Ceph maybe?):

* Set up a tiered system
  * TPU VMs, like 70GB each for 280GB total minus some redundancy
  * Cheap VM-driven larger NFS storage (or block-based like JuiceFS)
  * Use a cluster FS like Ceph to transparently manage this
* Features:
  * This makes good use of the fast TPU storage space on the boot disks, should
    expect commonly-used files (stored in cache) to be immediately available at
    local or TPU-interconnect speeds, while transparently failing over to
    larger storage
  * Sometimes the cluster COULD lag because it would become bottlenecked on the
    underlying storage.
* Raises new issues:
  * "Quorum fragility": The disk can freeze up if some TPUs become inaccessible
    or lag due to heavy computation
    * This seems rare and acceptable risk, TPU interconnects and CPUs are
      heavily underutilised with current experiment loads

### Other options

TODO: Consider alternative set-ups I haven't thought of.

TODO: Hybrid options:

* As long as maintenance burden is not too demanding and usability is simple
  for users, we could consider combining different options e.g. GCS for
  checkpoint storage but something else for venvs.


Recommendation: JuiceFS CE over GCS
-----------------------------------

After reviewing the options above, JuiceFS Community Edition with a GCS backend
and Redis metadata engine looks like the best fit for our cluster. It gives us:

* Shared POSIX filesystem across all 4 VMs (the core requirement).
* Pay-per-use GCS storage (cheap, expandable).
* Local SSD caching on each VM for fast reads (venvs, code).
* Data survives cluster loss (lives in GCS, metadata backed up hourly).
* Single binary, manageable maintenance for a solo sysadmin.
* Open source (Apache 2.0), CNCF Sandbox project, good reputation in ML/HPC.
* Native GCS support (not an S3 shim).

The community edition lacks per-user quotas and a management UI (enterprise
features), but these aren't priorities for us.


### Benchmark outcome (2026-04-23)

Benchmarks on all 4 nodes confirmed this design: warm-cache ops are
near local-disk speed, remote-Redis metadata is ~1.5–2× slower than
localhost but still comfortable in absolute terms, and 4-node
concurrent load shows only ~5–10% per-node slowdown — Redis is nowhere
near saturation. Full results in
[`juicefs-benchmark-results.md`](juicefs-benchmark-results.md).

Mount path decision: **`/storage`** for production. `/jfs` was used for
benchmarking and was retired on 2026-04-24 when `storage.mount` went live.


### Architecture

```
tpu0 ──┐                              ┌── Redis (metadata, on tpu0)
tpu1 ──┤── juicefs mount at /storage ─┤
tpu2 ──┤   (FUSE client on each VM)   └── GCS bucket (data)
tpu3 ──┘
         each VM also has a local
         cache dir on its boot disk
```

* Redis on tpu0: single-instance, AOF+RDB persistence. Memory needs are tiny
  (~300 bytes per file, so 100K files = ~30 MB).
* GCS bucket in same region as TPU VMs. TPU VM service account needs
  `roles/storage.objectAdmin` on the bucket — no key files needed.
* Each VM mounts the same filesystem. Local cache (LRU, configurable size)
  keeps hot files at local-disk speed.
* Automatic hourly metadata backup to GCS by the JuiceFS client.


### Installation plan

#### Prerequisites (all nodes)

```bash
# FUSE support
sudo apt install -y fuse

# JuiceFS binary (single static Go binary)
curl -sSL https://d.juicefs.com/install | sh -
# installs to /usr/local/bin/juicefs
```

#### Redis (tpu0 only)

```bash
sudo apt install -y redis-server
```

Edit `/etc/redis/redis.conf`:

```
bind 127.0.0.1 <tpu0-internal-ip>  # both localhost and internal network
requirepass <REDIS_PASSWORD>
maxmemory-policy noeviction  # CRITICAL: JuiceFS requires this
appendonly yes
appendfsync everysec
```

```bash
sudo systemctl enable --now redis-server
```

Verify: `redis-cli -a <password> ping` should return PONG.

#### GCS bucket

```bash
gcloud storage buckets create gs://mfrs-tpu-cluster \
  --location=<TPU_REGION> \
  --uniform-bucket-level-access
```

Verify TPU VM service account has access:
```bash
gcloud storage ls gs://mfrs-tpu-cluster/
```

#### Format filesystem (once, from any node)

```bash
juicefs format \
  --storage gs \
  --bucket mfrs-tpu-cluster \
  --capacity 1000 \
  "redis://:${REDIS_PASSWORD}@tpu0:6379/0" \
  clusterhome
```

This sets a 1000 GiB filesystem limit. Writes fail with ENOSPC when full.
To expand later (no downtime, no reformat):
```bash
juicefs config "redis://:${REDIS_PASSWORD}@tpu0:6379/0" --capacity 2000
```

#### Mount (each node)

For interactive testing:
```bash
sudo mkdir -p /jfs
sudo juicefs mount \
  "redis://:${REDIS_PASSWORD}@tpu0:6379/0" \
  /jfs \
  --cache-dir /var/jfsCache \
  --cache-size 40960 \
  --buffer-size 600 \
  --writeback \
  --upload-delay 1m \
  --allow-other \
  -d
```

Cache size rationale: 40 GB cache + ~25 GB system + ~10 GB safety margin +
~25 GB local scratch = 100 GB boot disk. Adjust based on benchmarking.

#### Systemd unit (each node, for production)

Create symlink for the FUSE mount helper. The PPA installs the binary at
`/usr/bin/juicefs` (not `/usr/local/bin/juicefs`, which is where the curl
installer drops it); `mount(8)` looks up `/sbin/mount.juicefs` as the helper:
```bash
sudo ln -sfn /usr/bin/juicefs /sbin/mount.juicefs
```

Create `/etc/systemd/system/storage.mount` with mode `0600 root:root` (the
file contains the Redis password — don't let non-admin users read
`/etc/systemd/system/`). systemd derives the unit name from the mount point,
so a different mount point needs a different unit filename:
```ini
[Unit]
Description=JuiceFS cluster storage
Requires=network-online.target
After=network-online.target

[Mount]
What=redis://:PASSWORD@tpu0:6379/0
Where=/storage
Type=juicefs
Options=_netdev,allow_other,cache-dir=/var/jfsCache,cache-size=40960,buffer-size=600,writeback,upload-delay=1m

[Install]
WantedBy=remote-fs.target
WantedBy=multi-user.target
```

Note: the FUSE option is `allow_other` (underscore), not `allow-other`.

For tpu0 only, add a drop-in at `/etc/systemd/system/storage.mount.d/redis.conf`
(mode `0644`) that orders the mount after `redis-server.service`. Other nodes
reach Redis over the network and need only the shared unit.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now storage.mount
```

#### User setup

IMPORTANT: If Redis goes down, the JuiceFS mount freezes and any SSH login
that tries to access a path on `/storage/` will hang — same failure mode
as the old NFS setup. The admin account (`matt`) MUST keep its home
directory on the local boot disk (`/home/matt`) to ensure recovery
access.

User home directory strategy: **JuiceFS as actual home**. Use
`usermod -d /storage/home/<user>` so user home directories live on
JuiceFS. Dotfiles, SSH keys, shell config — everything is shared
automatically across all VMs. As a bonus, with the uv cache under
`~/.cache/uv`, it lives on the same filesystem as the venv, so
`uv pip install` can hardlink (the benchmark's 10s scipy slowdown went
away in this arrangement). If Redis goes down, non-admin users can't SSH
in, but they can't do useful work on a cluster with frozen storage
anyway.

Create per-user directories on JuiceFS:
```bash
sudo mkdir -p /storage/home
# For each user:
sudo mkdir /storage/home/<username>
sudo chown <username>:<username> /storage/home/<username>
```

### Benchmarking plan

Run these benchmarks BEFORE migrating user data to validate performance. Compare
against local disk baseline to understand the overhead.

#### Phase 1: Baselines

```bash
# Redis latency from each node (expect <0.1ms from tpu0, <1ms from others)
redis-cli -h tpu0 -a <password> --latency

# GCS backend speed (bypasses FUSE and metadata)
juicefs objbench gs://mfrs-tpu-cluster/objbench/

# Local disk baseline
juicefs bench /tmp/local-bench --big-file-size 256 --small-file-count 500
```

#### Phase 2: JuiceFS built-in benchmark

```bash
# Run on each node
juicefs bench /jfs/bench-$(hostname) \
  --big-file-size 256 \
  --small-file-count 500 \
  --threads 1
```

#### Phase 3: Realistic workloads

Small files / venv simulation (metadata-heavy):
```python
# bench_metadata.py — run on each node
import os, time, json
base = '/jfs/bench-meta'
os.makedirs(base, exist_ok=True)
N = 5000
for label, fn in [
    ("create", lambda i: open(f'{base}/f_{i:06d}', 'w').close()),
    ("stat",   lambda i: os.stat(f'{base}/f_{i:06d}')),
    ("delete", lambda i: os.unlink(f'{base}/f_{i:06d}')),
]:
    start = time.time()
    for i in range(N): fn(i)
    print(f'{label}: {N/(time.time()-start):.0f} ops/sec')
```

Append performance (training logs):
```python
# bench_append.py — run on each node
import time
N = 50000
with open(f'/jfs/bench-append-{os.uname().nodename}.log', 'w') as f:
    start = time.time()
    for i in range(N):
        f.write(f'step={i} loss=0.{i:06d} lr=1e-4 ts={time.time()}\n')
        if i % 100 == 0:
            f.flush()
    print(f'{N/(time.time()-start):.0f} appends/sec')
```

Cache cold vs warm:
```bash
# Cold: clear caches, then read
echo 3 | sudo tee /proc/sys/vm/drop_caches
sudo rm -rf /var/jfsCache/*
time python -c "import jax"

# Warm: same import again (should be much faster)
time python -c "import jax"

# Pre-warm a directory explicitly
juicefs warmup /jfs/home/alice/venv --threads 4
```

TODO: Also bench pytorch/xla venv.

Concurrent multi-node access:
```bash
# From tpu0, launch same benchmark on all nodes simultaneously
for i in 0 1 2 3; do
  ssh tpu$i 'python3 /jfs/bench_metadata.py' &
done
wait
```

#### What to look for

* Cache-warm reads should approach local disk speed.
* Metadata ops from tpu0 (where Redis lives) should be faster than from other
  nodes. If the gap is too large, consider whether Redis placement matters.
* Append throughput should handle our log write rates without bottlenecking
  training. Our training loops write a line every few seconds, not thousands
  per second, so even modest throughput is fine.
* Check `juicefs info /jfs/bench-append-*.log` after append tests to see slice
  fragmentation.

#### Performance expectations (rough)

| Operation            | JuiceFS (cold) | JuiceFS (warm) | Local disk |
|----------------------|----------------|----------------|------------|
| Sequential read      | 100-300 MB/s   | ~local speed   | 500+ MB/s  |
| Sequential write     | 150-400 MB/s   | —              | 500+ MB/s  |
| File create          | 1-5K ops/s     | —              | 10-50K     |
| File stat            | 5-20K ops/s    | —              | 50K+       |
| `import jax` (venv)  | 10-30s         | 1-3s           | <1s        |


### Monitoring and maintenance plan

#### Day-to-day monitoring

```bash
# Quick check: real-time stats (cache hits, metadata latency, GCS ops)
juicefs stats /storage

# Check connected clients and filesystem info
juicefs status redis://:${REDIS_PASSWORD}@tpu0:6379/0

# Check fragmentation on a specific file
juicefs info /storage/path/to/file

# Redis memory usage
redis-cli -a <password> INFO memory | grep used_memory_human
```

Each JuiceFS client exposes Prometheus metrics at `http://localhost:9567/metrics`.
Key metrics to watch:
* `juicefs_blockcache_hits` / `juicefs_blockcache_miss` — cache hit rate
* `juicefs_meta_ops_durations_histogram_seconds` — metadata latency
* `juicefs_object_request_errors` — GCS errors

Setting up Prometheus/Grafana is optional but JuiceFS provides a dashboard
template if we want it later.

#### Scheduled maintenance

| Frequency | Task                                              |
|-----------|---------------------------------------------------|
| Automatic | Metadata backup to GCS (hourly, by JuiceFS client)|
| Daily     | `juicefs dump` to local backup (cron on tpu0)     |
| Weekly    | `juicefs gc --compact` (defragment slices)         |
| Monthly   | `juicefs gc --delete` (clean orphaned GCS objects, run manually at first) |
| Monthly   | `juicefs fsck` (consistency check)                 |

Daily metadata dump cron (on tpu0):
```cron
0 3 * * * /usr/local/bin/juicefs dump \
  redis://:PASSWORD@localhost:6379/0 \
  /var/backups/juicefs-meta-$(date +\%Y\%m\%d).json.gz
```

Weekly GC cron (on tpu0):
```cron
0 4 * * 0 /usr/local/bin/juicefs gc redis://:PASSWORD@localhost:6379/0 --compact
```

#### Failure modes and recovery

| Failure                  | Impact                          | Recovery                        |
|--------------------------|---------------------------------|---------------------------------|
| Redis down               | ALL ops freeze (reads AND writes)| Restart Redis; clients reconnect|
| One client loses network | That VM's ops freeze            | Auto-reconnects when restored   |
| GCS transient error      | Cache misses fail, cached ok    | Self-healing; auto-retry        |
| Cache disk fills         | Evicts old entries, keeps working| Normal (auto-managed by JuiceFS)|
| Redis data loss          | UNRECOVERABLE without backup    | Restore from dump/backup        |

The critical risk is Redis data loss. Mitigations:
1. AOF+RDB persistence on tpu0.
2. Automatic hourly metadata backup to GCS (by JuiceFS).
3. Daily `juicefs dump` cron.
4. Redis data is tiny (<100 MB for our use case), so backups are fast.

#### Security

* Redis: bind to internal IP only, set `requirepass`, firewall port 6379.
* GCS: uses VM service account identity, no key files to manage.
* POSIX permissions enforced by FUSE client (standard uid/gid/mode).
* Data encryption available but optional (client-side AES-256-GCM). Probably
  not needed for ML research data, and adds overhead.


### Migration plan

1. **[done]** Install and format (all nodes).
2. **[done]** Run benchmarks, validate acceptable performance. See
   [`juicefs-benchmark-results.md`](juicefs-benchmark-results.md).
3. **[done]** Mount at `/storage` via systemd on all nodes (2026-04-24).
   The manual `/jfs` mounts from the benchmark phase were unmounted the
   same day after validating `/storage` worked (same Redis + GCS backend,
   so no data migration).
4. Create `/storage/home/<user>` directories.
5. Migrate user data to `/storage/home/<user>/`.
6. Update user home directories:
   `sudo usermod -d /storage/home/<user> <user>`.
   (Admin account `matt` stays at `/home/matt` for recovery access.)
7. Set up monitoring crons on tpu0.
8. Warm up each node's cache after mount: `juicefs warmup` on migrated
   venvs. The benchmark showed cold imports take 30+ seconds for JAX and
   5+ minutes for PyTorch/XLA, so ideally automate this via a
   `juicefs-warmup.service` oneshot that runs after `storage.mount` on
   each boot. Targets: any `venv/` directory under `/storage/home/`.
9. Observe for a week before considering any changes to boot disk usage.

### Cost estimate

Prices below are approximate as of early 2025. Verify against the GCP pricing
page before committing: https://cloud.google.com/storage/pricing

**Region note**: us-central2 is a TPU-specific region. GCS does allow creating
buckets there (region option in the console), though it doesn't show cost
estimates — probably because it's not a standard region. Since the bucket
and TPU VMs would be in the same region, egress should be free. Verify this
after setup by checking billing reports after a small test transfer. If
us-central2 turns out to be problematic, us-central1 is the fallback
(but may incur cross-region egress at ~$0.01/GB).

#### Storage (per 100 GB stored)

| Item                     | Rate            | Per 100 GB/month |
|--------------------------|-----------------|------------------|
| GCS Standard storage     | ~$0.020/GB/mo   | ~$2.00           |
| GCS multi-region storage | ~$0.026/GB/mo   | ~$2.60           |

#### Operations (per 100K operations)

JuiceFS splits files into 64 MB chunks stored as individual GCS objects.
Small file creates, appends, and compaction all generate GCS operations.

| Operation type              | Rate             | Per 100K ops |
|-----------------------------|------------------|--------------|
| Class A (write/create/list) | ~$0.005/1K ops   | ~$0.50       |
| Class B (read/get)          | ~$0.0004/1K ops  | ~$0.04       |
| DELETE                      | free             | $0.00        |

#### Network egress

| Route                                  | Rate         |
|----------------------------------------|--------------|
| Same region (e.g. both us-central1)    | free         |
| Cross-region, same continent (?)       | ~$0.01/GB    |
| TODO: verify which applies for us-central1 <-> us-central2-b |

#### Other costs

* Redis on tpu0: free (uses existing VM resources).
* JuiceFS CE: free (open source, Apache 2.0).

#### Scenario estimates

Assuming 100 GB stored, moderate usage (a few training runs per day):

| Scenario                     | Storage | Ops     | Egress    | Total     |
|------------------------------|---------|---------|-----------|-----------|
| Same-region (no egress cost) | ~$2.00  | ~$0.50  | $0        | ~$2.50/mo |
| Fallback (us-central1 bucket)| ~$2.00  | ~$0.50  | ~$1-10    | ~$4-13/mo |

If using a us-central2 bucket (same region as VMs), egress should be free
and total cost should be ~$2-3/month. Verify with billing reports after a
small test transfer.

### Resolved questions

* **`--writeback`**: Yes, use it. If a node hard-crashes, losing the last
  30-60s of a text log is acceptable — we'd restart from the last checkpoint
  anyway. The write performance boost is worth it.
* **`--upload-delay`**: Yes, use `--upload-delay 1m`. Requires `--writeback`.
  Batches small appends (e.g. W&B, training logs) into fewer GCS objects,
  reducing Class A operation costs and slice fragmentation. JuiceFS will
  still upload early if cache space runs low.
* **Hybrid checkpointing**: Not needed initially. If models grow past a few
  GB, write checkpoints directly to `gs://` via JAX/Orbax to avoid cache
  thrashing. Revisit later.
* **Mount path**: `/storage` for production (live via `storage.mount`
  systemd unit on all 4 nodes as of 2026-04-24). `/jfs` was retired the
  same day once `/storage` was validated.

### Open questions

* **`juicefs gc --delete`**: Run manually the first few times before putting
  it in a cron job, to verify it's not deleting anything unexpected. Running
  `juicefs gc` without `--delete` is already a check-only scan (there is no
  `--dry-run` flag).

Technical notes
---------------

Created a bucket with this config

```
gcloud storage buckets create gs://mfrs-tpu-cluster \
    --default-storage-class=STANDARD \
    --location=US-CENTRAL2 \
    --uniform-bucket-level-access \
    --public-access-prevention
```

Review
------

This is an exceptionally well-thought-out plan. It accurately captures the architecture, risks, and mitigations necessary for running a TPU-hosted JuiceFS cluster.

### 1. The "Genius" Moves
*   **The Admin Fallback:** Keeping `matt` on the local boot disk (`/home/matt`) while moving other users to `/jfs/home/` is the single most important safety feature in this doc. It perfectly neutralizes the "TPU Lockout" risk if Redis dies.
*   **Redis `maxmemory-policy noeviction`:** This is absolutely critical for JuiceFS. If Redis evicts a key to save memory, you lose a file. Good catch.
*   **Benchmarking Plan:** Separating cold vs. warm cache tests, and specifically testing small file (metadata) creation, will give exactly the data needed to tune the `--cache-size`.

### 2. Answers to "Open Questions"
*   **`--writeback` vs. safety:** Yes, use `--writeback`. In ML training, if a node hard-crashes, losing the last 30 seconds of a text log is usually fine (you'd just restart the job from the last checkpoint anyway). The massive performance boost for small file writes is worth it.
*   **`--upload-delay`:** Strongly recommend using this (e.g., `--upload-delay 1m`). Tools like W&B append tiny bits of data constantly. Without a delay, JuiceFS might create thousands of tiny objects in GCS (increasing Class A operation costs). A 60-second delay batches these appends into fewer, larger GCS objects, which is cheaper and less fragmented.
*   **Mount Path (`/jfs` vs `/cluster/home`):** `/cluster/home` is suggested. Since this was your previous NFS path, existing scripts or user muscle-memory might already expect it. It reduces friction during the migration. Either works though.
*   **Hybrid Checkpointing (JAX/Orbax to `gs://`):** Yes, if models grow past a few GBs, writing checkpoints directly to GCS bypasses the FUSE layer and saves the local boot disk cache from being instantly filled ("cache thrashing") by a single massive write.

### 3. Technical Corrections & Minor Warnings
*   **Redis Binding (`/etc/redis/redis.conf`):** On Ubuntu 22.04, Redis binds to `127.0.0.1` by default. When you add `bind <tpu0-internal-ip>`, make sure you comment out or replace the default `bind 127.0.0.1` line, or it might not listen on the network interface properly.
*   **Systemd Mount Dependencies:** In `jfs.mount`, you have `Wants=network-online.target`. Because FUSE mounts over a network database (Redis) can be finicky during boot, I recommend changing it to `Requires=network-online.target` to strictly enforce the boot order.
*   **JuiceFS Garbage Collection (`gc`):** Your weekly cron for `juicefs gc --compact` is perfect. However, for the monthly `juicefs gc --delete` (which actually deletes orphaned objects from GCS), I highly recommend running it manually the first few times, or adding the `--dry-run` flag to the cron job initially, just to ensure it's not deleting things you didn't expect.
*   **FUSE Permissions:** Your mount command includes `--allow-other`, which is correct. However, on Ubuntu, you may also need to ensure `user_allow_other` is uncommented in `/etc/fuse.conf`, otherwise the systemd mount might fail to apply that flag.
