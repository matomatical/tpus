# Tiny TPU Cluster admin repo

Matt's admin repo for a 4-node TPU v4-32 cluster on GCP (`tpu0`–`tpu3`),
allocated via the TPU Research Cloud programme. This is the source of truth
for scripts, configs, and handbooks deployed to the VMs. This working
directory lives on `tpu0`.

Canonical references:

- `user-handbook.md` — for users (access, setup, policies, `tpups`/`tpu-device`).
- `admin-handbook.md` — for provisioning, deployment commands, and known
  troubles. Prefer running its commands over improvising.
- `README.md` — repo overview and a Roadmap that doubles as a work log.

## Repo layout

- `admin-scripts/` — admin utilities run from the repo (not deployed):
  `adduser.sh`, `fetch-logs.sh`, `bench-*.py`.
- `shared-scripts/` — deployed to `/home/shared/` on each VM and symlinked
  into `/usr/local/bin/`: `tpu-device.sh`, `tpups.py`, `tpu-usage.py`,
  `tpu-heatmap.py`, `tpu-health.py`, `tpu-heartbeat.py`,
  `tpu-handbook.sh`. The `dashboard/` subdir is deployed to tpu0 only (web
  UI on `:8082`).
- `conf/` — deployed under `/etc/` on each VM: systemd units (heartbeat,
  heartbeat-web, healthagent-restart), logrotate configs, `tpu-defaults.sh`,
  `tpu-logs.conf`, `cluster-hosts`, `cluster-ssh.conf`.
- `home-stuff/` — Matt's personal dotfiles (`init.vim`, `zshrc.zshrc`).
- `issues/` — bug investigations and design docs: `healthagent-oom/`,
  `docker-api-version/`, `storage/` (JuiceFS options + benchmarks),
  `shared-uv-cache.md`.
- `secrets/` — gitignored credentials (`redis.env`, JuiceFS SA key).
- `tests/` — `test-tpu-device.sh`.
- `users.md` — the `adduser.sh` invocations used for each user.

## Deployment model

Edits to `shared-scripts/` and `conf/` only take effect after deployment to
every VM. `admin-handbook.md` has the exact commands; the typical pattern is:

```
for t in 0 1 2 3; do
  scp <file> tpu$t:
  ssh tpu$t 'sudo install -m 644 <file> /destination/path && rm <file>'
done
```

The trailing-colon form (`tpu$t:`) is load-bearing — drops `<file>` in the
remote home dir with the same basename. If you need an explicit destination
filename, brace the variable (`tpu${t}:dest.name`); zsh treats `$t:t/h/r/e/...`
as the pathname-modifier syntax and silently mangles the path otherwise.

Files stage in `~/` (which is `drwxr-x---`), not `/tmp/`. `/tmp/` is
world-writable and the `scp` → `sudo cp` pattern through it is vulnerable to
a symlink TOCTOU race that lets another user inject content into `/etc/`
with root privileges. Staging in `~/` closes this off since no other user
can plant bait there.

For systemd units, also `daemon-reload` and enable/restart. The MOTD script
(`/etc/update-motd.d/99-tpups`) is installed as a **copy, not a symlink** for
security — keep it that way, since `/home/shared/` is writable by root only
and MOTD scripts run as root on login.

## Running things from this machine

- We are on `tpu0`. Run commands locally — do **not** `ssh tpu0 '...'`. SSH
  only to `tpu1`, `tpu2`, `tpu3`. Deployment loops over all four are fine
  (they use a loopback SSH hop to `tpu0` for symmetry).
- Default TPU env vars target device 0 on the current VM, so plain
  `python script.py` works for TPU code. Use `tpu-device N` for a different
  device, a pair/quad (`0,1`, `2,3`, `0,1,2,3`), or `tpu-device cpu`.
- `ssh tpuN 'cmd'` runs a non-interactive non-login shell and sources
  neither `/etc/profile.d/` nor `~/.zshrc`, so TPU defaults appear missing.
  Use `ssh tpuN 'bash -lc "cmd"'` or `ssh tpuN 'zsh -ic "cmd"'` to pick them
  up. Don't conclude from this that defaults are broken on a given VM.
- Use `tpups` before anything that might disrupt users (service restarts,
  loops, resource-heavy commands).

## When making changes

- User-facing change (policy, feature, how users interact): update
  `user-handbook.md`.
- Admin/ops change (new deployment step, new service, new trouble entry):
  update `admin-handbook.md`.
- Before each commit: add a one-line summary to the `README.md` Roadmap
  section (high-level, not a step-by-step list), then commit.
- Commit messages: short imperative, often prefixed with the subsystem
  (`tpups:`, `tpu-device:`, `heartbeat-web:`). Example:
  `heartbeat-web: log to tmpfs to avoid ext4 journal contention`.
- Don't edit diagnostic/interactive snippets in the handbooks into SSH loops
  — those are meant to be run manually on one VM at a time. Only
  deployment/setup commands get loop-ified. Preserve code-block comments
  when refactoring.

## Security

Threat model is clumsy users and loose AI agents, not malicious insiders.
Before touching anything under `/home/shared/`, `/etc/`, or any file that
ends up on another user's execution path, re-check
`admin-handbook.md` → "Security goals". Invariants to preserve:

- Home dirs are `drwxr-x---` (owner-only access).
- No sudo for non-admin users.
- `/home/shared/` is root-owned, not user-writable.
- Nothing a non-admin user can write ends up being executed by `matt` or
  another user.
- Secrets live in `secrets/` (gitignored); never commit them, and don't pass
  them as CLI args (visible via `ps`).

## Active work

See the Roadmap in `README.md` for the authoritative state. At time of
writing, the live initiative is **JuiceFS shared storage**: filesystem
is formatted, benchmarked across all 4 nodes, and manually mounted at
`/jfs` on each. Next steps are a systemd `storage.mount` unit at
`/storage` (the production path — `/jfs` was for benchmarking only),
home-directory migration, and monitoring crons. Design in
`issues/storage/storage-options.md`; benchmark results in
`issues/storage/juicefs-benchmark-results.md`.
