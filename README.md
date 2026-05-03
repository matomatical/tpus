MFR's Tiny TPU Cluster
======================

Admin repo for Matt's 4-node TPU v4-32 cluster on Google Cloud,
allocated via the [TPU Research Cloud](https://sites.research.google/trc/about/)
programme. The cluster provides 16 TPU v4 devices across 4 VMs
(tpu0–tpu3), each with 240 vCPUs, 400 GiB RAM, and 100 GiB disk,
running Ubuntu 22.04 with uv and JAX.

Handbooks
---------

* **[User handbook](user-handbook.md)** — for students and researchers using
  the cluster. Covers access, setup, running JAX on TPUs, and cluster policies.
* **[Admin handbook](admin-handbook.md)** — provisioning, configuration, and
  maintenance notes.

Repo contents
-------------

* `admin-scripts/` — scripts for admin use (adduser, etc.)
* `shared-scripts/` — scripts deployed to `/home/shared/` on each VM
  (`tpu-device`, `tpups`, `tpu-usage`, `tpu-heatmap`, `tpu-heartbeat`)
* `conf/` — config files to deploy to VMs (logrotate, etc.)
* `home-stuff/` — dotfiles to deploy to VMs
* `issues/` — bug reports for TPU VM image issues
* `users.md` — cluster user info

Roadmap
-------

Most features built with help from Gemini and/or Claude.

Late 2025:

* [x] Basic setup of important software.
* [x] Automate user creation given a public key.
* [x] tpu-heartbeat service for logging usage status and statistics.
* [x] tpups utility for checking cluster usage status.
* [x] tpu-usage utility for viewing usage statistics.
* [x] tpu-device wrapper for managing environment variables.
* [x] Original NFS based system (disk filled up, cluster died, RIP).

March 2026, feature upgrades:

* [x] tpu-device: support for multi-tpu job launching.
* [x] tpu-heatmap utility visualising usage calendar. Let's keep the TPUs warm!
* [x] Streamlined markdown user handbook for onboarding students to the
  cluster.
* [x] PyTorch/XLA support (instructions in handbook).

March 2026, critical storage issue fixes:

* [x] Configured logrotate and journald to cap log file size
* [x] Investigated and reported a healthAgent OOM bug to Google (see
  `issues/healthagent-oom/`).

April 2026, feature upgrades:

* [x] tpu-device: optional, default env vars equivalent to `tpu-device 0`.
* [x] Inter-VM SSH configured by default for all users.
* [x] `tpups` in MOTD.
* [x] tpu-device: CPU mode (`tpu-device cpu`) for JAX and PyTorch/XLA.
* [x] tpups: fail gracefully when servers are unreachable, speed improvements.
* [x] tpups: --watch variant for live reloading, dynamic table width, dynamic
  column widths, ANSI colours and unicode rules.

April 2026, backend stability improvements:

* [x] Reverted system Python from 3.14 back to 3.10.
* [x] Persistent fix for TPU log directory permission issue.
* [x] tpu-heartbeat: promote to systemd service.
* [x] healthAgent bug: Automatic weekly restarts.
* [x] Hardened installation method for shared scripts.
* [x] `tpu-health` utility for monitoring disk usage, heartbeat freshness,
  service status, and healthAgent memory.
* [x] `fetch-logs.sh` admin script for backing up heartbeat history.
* [x] Streamlined admin handbook.
* [x] tpu-heartbeat-web: log to tmpfs to avoid ext4 journal contention
* [x] Project CLAUDE.md with repo layout, deployment pattern, and security
  pointers.
* [x] Deploy configs via `~/` staging instead of `/tmp/` (closes a symlink
  TOCTOU race)
* [x] Configure automatic 14-day `/tmp` age-based cleanup.
* [x] Silence `rsyslog` `/dev/console` omfile suspension spam.
* [x] Deploy `user-handbook.md` to `/usr/local/share/doc/tpus/` on each VM
  for stable reference from user shells / CLAUDE.md files.
* [x] Security review and follow-up.
* [x] Service configuration stability improvements.
* [x] tpu-device: pin libtpu metrics service to a per-chip port.

Major feature: Persistent shared storage

* [x] Consider various options (see issues/storage/storage-options.md)
  -> try JuiceFS + GCS
* [x] Research deployment rollout plan, cost estimates etc.
* [x] Create bucket, install and configure JuiceFS
* [x] Run benchmarks and validate acceptable performance
  (see issues/storage/juicefs-benchmark-results.md)
* [x] Mount `/storage` via systemd on all 4 nodes.
* [x] Port user home directories to `/storage/home/`.
* [x] `tpu-warmup` utility for on-demand cache warming on a chosen node.
* [x] Rewrite `tpu-health` as admin-only cluster-wide tool with /storage
  signals (mount, capacity, cache, rawstaging, GCS errors) and Redis ping.
* [x] Validate and automate juicefs redis database backups.
* [x] Weekly `juicefs gc --compact`; manual `gc` and `fsck` baseline clean.
* [x] `tpu-health`: `gc timer` and `gc fresh` rows for the weekly compact job.
* [x] Migration cleanup: per-node `/home/<u>` backups deleted (~114 GiB freed),
  uv-cache and venv-python symlinks repointed to `/storage` paths.

May 2026, more backend stability improvements

* [x] Cap docker container json log growth (logrotate copytruncate + daemon.json log-opts).
* [x] Fix ConTeXt MarkIV format build cluster-wide (lua-socket + cpath symlinks under `/usr/local`).

TPU monitoring improvements

* [x] upgrade tpups with memory/processor utilisation
* [x] tpu-device: always pin per-chip metrics port (was tripping on
  tpu-defaults' default flag, leaving multi-launches collided on 8431).
* [x] tpu-dashboard: live tpups + HBM/duty time-series web UI on tpu0:8082.
* [ ] also tensorcore utilization?
* [ ] add tpups / usage / heatmap views into tpu-dashboard
* [ ] public web dashboard (rather than ssh port forward)

Major feature: TPU queueing system (prerequisite, persistent shared storage)

* [ ] install or develop some simple system that makes it easier to launch
  large numbers of TPU job scripts for each user, and then they will
  automatically be launched when TPUs are free; users only have to interact
  with a single VM.

For fun: Bring the server to life with AI agents

* [ ] Autonomous AI agent account on the cluster, basic sysadmin/monitoring
* [ ] Integrate agents with Slack channel, etc.?
* [ ] AI agents on the cluster can communicate and run their own research
* [ ] Pixel art dashboard in the style of https://github.com/pablodelucca/pixel-agents

Scaling up:

* [ ] A single reprovisioning script to set up the entire TPU cluster
* [ ] Enable the use of pre-emptable TPU VMs
* [ ] Learn how to make full use of the TPU VMs for a single big training run
* [ ] Get better at using bfloat16 ([see here](https://docs.cloud.google.com/tpu/docs/bfloat16))
