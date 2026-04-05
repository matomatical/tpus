# TPU Cluster Admin

Matt's 4-node TPU v4-32 cluster (tpu0–tpu3) on GCP, running JAX workloads.
Allocated via the TPU Research Cloud (TRC) programme.

## Repo structure

- `user-handbook.md` — handbook for cluster users (students)
- `admin-handbook.md` — step-by-step provisioning guide for setting up the cluster
- `conf/` — config files to deploy to VMs (logrotate, etc.)
- `admin-scripts/` — scripts for Matt's use (adduser, etc.)
- `shared-scripts/` — scripts deployed to `/home/shared/` on each VM
- `issues/` — bug reports for TPU VM image issues or other notes
- `users.md` — cluster user info
- `home-stuff/` — dotfiles to deploy to VMs

## Running commands across VMs

```bash
for t in 0 1 2 3; do echo "=== tpu$t ===" && ssh tpu$t '...'; done
```

## Past work with Claude Code

March 16, 2026:

- Generalise tpu-device script to allow running commands on multiple devices at
  once.
- Add tpu-heatmap shared utility for visualising and calendar. Let's keep the
  TPUs warm!

March 19, 2026:

- Configured logrotate and journald to prevent system logs filling up disks
- Investigated and reported a healthAgent OOM bug to Google (see
  `issues/healthagent-oom/`)
- Created a streamlined user handbook for onboarding students to the cluster.

March 26, 2026:

- Added PyTorch/XLA support: tested installation and TPU access, added
  `PJRT_DEVICE=TPU` to `tpu-device` wrapper, updated user handbook with
  setup instructions and hello world examples for both JAX and PyTorch/XLA.

April 5, 2026:

- Reverted system Python from 3.14 back to 3.10 and switched to standalone uv
  for Python version management.
- Made tpu-device optional by default for bash users via
  `/etc/profile.d/tpu-defaults.sh` to set TPU env vars (defaulting to device
  0).
- Fixed TPU log directory permissions permanently via `tmpfiles.d` so
  `/tmp/tpu_logs/` is created world-writable with sticky bit on every boot.
- Added inter-VM SSH for all users: system-wide SSH client config
  (`cluster-ssh.conf`), per-user cluster key generation
  (`setup-cluster-keys.sh`), and integrated key setup into `adduser.sh`
  so new users get inter-VM SSH automatically from a single VM.
- Replaced `nohup` heartbeat and HTTP server with systemd services
  (`tpu-heartbeat.service`, `tpu-heartbeat-web.service`) for automatic
  restart on crash or reboot.
- Added weekly systemd timer to restart healthAgent and prevent OOM
  memory leak from recurring.
- Created `tpu-health` script: checks disk usage, heartbeat freshness,
  service status, and healthAgent memory.
- Added `tpups` to login MOTD; cleaned up Ubuntu MOTD ads.
- Centralised cluster IPs in `/etc/hosts` (`conf/cluster-hosts`),
  simplified SSH config and removed hardcoded IPs from Python scripts.
- Hardened `/home/shared` to root-owned 755 (was world-writable 777).
- Added `fetch-logs.sh` admin script for backing up heartbeat history.
- Overhauled admin handbook: all commands now use SSH loops from the
  repo instead of "on each VM" manual steps.
