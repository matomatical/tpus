# Decommission notes — May 2026

Notes on the wind-down of the TRC TPU v4-32 cluster and patterns worth
carrying forward to the next setup (probably a NUC in the office, plus
cloud compute for heavy jobs).

## What the cluster was

- 4× TPU v4-2 VMs (`tpu0`–`tpu3`), 240 vCPU + 400 GiB RAM + 100 GiB
  boot disk each, Ubuntu 22.04. Allocated via the TPU Research Cloud
  programme.
- Lifetime: provisioned late 2025, decommissioned 2026-05-27 when TRC
  declined to extend.
- Users: matt (admin), mfr (research), and ~6 students. Multi-tenant
  per-user accounts with `/storage/home/<user>/` on JuiceFS-over-GCS.

## What worked (worth bringing forward)

### Utilities
- **`tpups`** — multi-node TPU usage display. Single command, "who is
  using what right now." Probably the single most-used utility.
- **`tpu-device`** — device pinning wrapper that sets the right env
  vars per device. Made device 0/1/2/3 access uniform.
- **`tpu-health`** — periodic health check dashboard with cell-shaded
  rows per VM. Read at a glance.
- **`tpu-heartbeat`** + **`tpu-dashboard`** — split design: heartbeat
  collects + writes file, dashboard serves time-series web UI on :8082.
  Decoupling the writer from the reader made each piece simple.
- **`tpu-handbook`** — `less`-paged user handbook reachable from any
  cwd and any node. Onboarding via docs, not chat.
- **`adduser.sh`** — single script, pre-seeds `~/.ssh/config` with
  mode 0600 (avoids the libunwind/strict-modes bug). Reproducible.

### Patterns
- **User-facing handbook + admin handbook** as the source of truth.
  Easy to point users at; easy to update; survives the cluster.
- **Roadmap-as-work-log** in `README.md`. One-line summary per commit
  group. Doubles as historical record after the cluster dies.
- **`issues/` directory** for investigations and design docs that
  don't fit either handbook (e.g. `bug-libunwind`, `storage/`).
- **`secrets/` gitignored + README pointing at its expected contents.**
  Bash, redis, juicefs creds all in one place, key locations grep-able.
- **`shared-scripts/` deployed via `scp + sudo install`**, copies (not
  symlinks) for things on the root execution path (e.g. MOTD).
- **JuiceFS-over-GCS for shared storage.** Worked extremely well —
  POSIX semantics, easy mounting, durability inherited from GCS, cheap
  to keep around post-decommission via the metadata-dump pattern.
- **Hourly metadata backup** to the same bucket the data lives in.
  Made revival trivial (see admin-handbook). For a single-node setup
  this is overkill but the principle (backup the small thing that
  loses data, not the bulk) transfers.
- **Admin home local, user homes on shared FS.** Boot resilience:
  if shared storage breaks, the admin can still log in and fix things.
- **`tpu0` as the metadata/services node.** One node carries the
  "control plane" (Redis, dashboard, backup timer). Other nodes are
  data-plane only. Simplified failure reasoning.

### Decommission tactics that worked
- **Keep the bucket alive instead of mass exfiltration.** Saved
  ~17h of upload. Revival from a cheap VM is the canonical path; the
  bucket pays for itself many times over relative to bandwidth costs.
- **Pre-decommission garbage collection** (`juicefs gc --delete`).
  Bucket halved (742 GiB → 353 GiB) and will halve ongoing cost.
- **`juicefs rmr --skip-trash`** for emptying trash without waiting
  on `TrashDays`. Critical for same-day decommission.
- **Per-user audit pass before shutdown.** Catches stale branches,
  unpushed work, "I was about to clean that up" moments.

## What I'd do differently

- **JuiceFS cold reads are very slow** (a metadata round-trip per
  file). Surfaced when we tried to tar 10G of olly's home and saw
  ~2.5 MB/s. For the NUC: if shared storage is needed across multiple
  containers/VMs, prefer plain NFS or a local filesystem unless the
  scale demands JuiceFS. The JuiceFS payoff was multi-node + durable
  + cheap, which a single NUC doesn't need.
- **Set tighter trash retention** (`TrashDays=0` or `1`) on
  experiment-heavy filesystems. We had `TrashDays=1` which was fine,
  but anything longer would have caught us out on cleanup day.
- **Default `*.json` gitignore overmatches** — caught log JSONs that
  someone later wanted to track. For research repos, prefer specific
  paths or use `!exception` rules from the start.
- **Two-checkout drift** (matomatical/minimax-add had a standalone
  clone diverging from the dz submodule). For solo repos, having two
  checkouts on the same machine is a footgun. Stick to one canonical
  working copy per repo.

## For the NUC

- Most multi-node utilities (`tpups`, `tpu-device`, `tpu-health`) are
  irrelevant on a single machine, but their *patterns* (one command,
  one-screen summary, devices as first-class) port directly.
- The agent-state pattern from this decommission (`~/agents/state/<tool>-<host>-<user>/`
  with `.claude` / `.gemini` symlinked in) seems worth keeping —
  let single-shot tools write to their usual paths but have the
  canonical store be a git repo.
- The `secrets/` + admin/user handbook + `issues/` pattern from this
  repo carried real weight. Worth replicating on the NUC repo from
  day one rather than retrofitting.
- Cloud compute for heavy jobs: think about the "launch + log" tooling
  as a first-class concern (the closest analogue here was Matt's
  `all-you-need` cron, which deployed a hobby site daily — minimal
  but worked).

## Pointers

- The cluster bucket: `gs://mfrs-tpu-cluster/` (us-central2), now
  ~353 GiB after final GC, ~$7/mo Standard or ~$0.42/mo Archive.
- Metadata dumps: `gs://mfrs-tpu-cluster/backups/dump-*.json.gz`,
  latest from 2026-05-27 ~10:33 UTC.
- SA key: `tpu-juicefs@ace-line-457306-p7.iam.gserviceaccount.com`
  (kept alive for revival; copy off-cluster).
- Revival procedure: `admin-handbook.md` → "Revival on a fresh VM
  (post-decommission)".
