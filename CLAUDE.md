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
