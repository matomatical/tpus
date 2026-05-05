Initialising a TPU VM
=====================

## Budget alerts

Notes:

* I configured a "budget" for all GCP usage at 200 GBP per month.
  * I should get alerts at 10% and a few other larger thresholds each month.
* I tried to configure an automatic response to exceeding the budget to disable
  cloud billing, but I was unable to test it and unsure if it will work if this
  happens.


## Provisioning

In GCP console, go to compute > TPUs and create a new TPU resource. The
configuration should match the allocation from allocation email.

Here is an equivalent `gcloud` command:

```
gcloud compute tpus tpu-vm create \
  cluster \
  --accelerator-type v4-32 \
  --zone=us-central2-b \
  --version=tpu-ubuntu2204-base
```

Sometimes this fails if there are no free resources. In that case tick the
option to create a queued resource instead, which means it will be created as
soon as one becomes available.

### Verifying cluster hardware

After provisioning, verify the key stats from each VM:

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  # CPU model and core count
  ssh tpu$t 'cat /proc/cpuinfo | grep "model name" | head -1 && nproc'
  # Memory
  ssh tpu$t 'free -h'
  # Disk
  ssh tpu$t 'df -h /'
  # TPU devices (should show accel0-3 on each VM)
  ssh tpu$t 'ls /dev/accel*'
  # OS version
  ssh tpu$t 'lsb_release -d'
done
```

### Trouble: TPU version

Previously I tried `--version=tpu-vm-base`, but that turned out to be a mistake
because it was Ubuntu 20 instead of 22. I wasn't even able to install n updated
version of Python.

### Trouble: Service account and OAuth scopes

The current cluster was provisioned without specifying a service account or
OAuth scopes, so the VMs use the default Compute Engine service account with a
limited scope set (notably `devstorage.read_only`). This means user-space
programs running on the VMs (e.g. JuiceFS) cannot write to GCS via the VM's
metadata-server credentials — even if the underlying service account has the
right IAM roles, the OAuth scope caps the tokens the metadata server will
issue.

Scopes are baked in at VM creation and cannot be changed on a running TPU VM.
Since a TPU VM can't be stopped/restarted, we can't fix the scopes on the
existing cluster. The current workaround for JuiceFS is a service account key
file (see the shared storage section below).

**Next time we provision:** create the service account first (as described in
the shared storage section), then create the TPU with `--service-account` and
broad scopes:

```
gcloud compute tpus tpu-vm create \
  cluster \
  --accelerator-type v4-32 \
  --zone=us-central2-b \
  --version=tpu-ubuntu2204-base \
  --service-account=service-1054593878874@cloud-tpu.iam.gserviceaccount.com \
  --scopes=cloud-platform
```

Equivalently, in the GCP console TPU creation dialog, choose "Allow full access
to all Cloud APIs" under access scopes, rather than the default limited set.

TODO: This service account, created as described below, doesn't appear in the
console settings, maybe because it is a service agent. Will it work via gcloud?
Or can we use the default service account and attach the permissions to that?
We can figure this out next time we provision.

TODO: We will also have to figure out how to restrict access to the service
account to only root users. We don't want non-root users to be able to read the
contents of the GCS bucket as it ultimately contains sensitive data from each
user. Maybe using a service account key, as in the workaround below, is
actually easier here.

## SSH config

Create a key (if haven't already):

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpus -C matt
```

Add key to cluster with GCP console (compute > metadata > ssh keys).


Configure `~/.ssh/config` (get IP addresses from GCP console):

```ssh_config
# MFR's tiny TPU cluster
Host tpu0 tpu1 tpu2 tpu3
    IdentityFile ~/.ssh/mfr-tpus
    User matt
Host tpu0
    HostName 35.186.109.61
Host tpu1
    HostName 35.186.93.73
Host tpu2
    HostName 173.255.125.97
Host tpu3
    HostName 35.186.140.33
```

Now you can ssh into the TPU VMs with `ssh tpuX` for `X` in `0123`.

### Trouble: Identity verification issue

The login will be blocked if the same IPs are used as an old machine. Remove
the stale host keys with:

```
for t in 0 1 2 3; do ssh-keygen -R tpu$t; done
```

Upgrading system packages
-------------------------

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  ssh tpu$t 'sudo apt update && sudo apt upgrade -y && sudo apt autoremove -y'
done
# Restart remote VMs first, then local VM last
for t in 1 2 3; do ssh tpu$t 'sudo shutdown -r now'; done
sudo shutdown -r now
```

Wait for restart!

### Trouble: Waiting for cache lock

Sometimes one of the TPUs can't do `sudo apt upgrade` due to an error:

```
Waiting for cache lock: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 11794 (unattended-upgr)
```

I restarted these machines first, then it worked.

Actually, the second time this happened, one of the TPUs took a long time to
restart. But it did eventually come back after an hour or so.

### Trouble: Some upgrades held back

I am not sure why these were held back, perhaps deliberately by GCP people who
know better than me. At the time I thought worth trying to upgrade them to
since this was blocking me from `do-release-upgrade`. It was possible to
upgrade them forcefully:

```
sudo apt upgrade linux-gcp linux-headers-gcp linux-image-gcp
sudo shutdown -r now
```

However, I later realised I didn't want to `do-release-upgrade` and I should
just recreate the cluster instead...

## Installing uv

Install uv as a standalone binary
([docs](https://docs.astral.sh/uv/getting-started/installation/)):

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  ssh tpu$t 'curl -LsSf https://astral.sh/uv/install.sh | sudo sh'
done
```


### Trouble: Using newer versions of Python

Previously, I was installing python3.14 at system level. That breaks some
things, easier to let uv manage all Python versions. To use a newer Python in a
project, just specify it when creating a venv:

```
uv venv --python 3.14
```

Then uv manages Python versions and virtual environments without modifying the
system Python.

## Installing custom scripts

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo mkdir -p /home/shared && sudo chmod 755 /home/shared'
done
```

From local to each TPU VM. Public scripts go in as `755 root:root`;
`tpu-health` lands separately as `750 root:matt` so non-admin users can't
read or execute it. Installing each with the right mode upfront avoids any
window where the admin-only file is briefly world-readable in `/home/shared/`.

```
for t in 0 1 2 3; do
  scp shared-scripts/* tpu$t:
  ssh tpu$t '
    sudo install -m 755 \
      ~/tpu-device.sh ~/tpu-warmup.sh ~/tpu-heartbeat.py \
      ~/tpu-heatmap.py ~/tpu-usage.py ~/tpups.py \
      ~/tpu-metrics.py ~/tpu-handbook.sh \
      /home/shared/
    sudo install -m 750 -o root -g matt ~/tpu-health.py /home/shared/tpu-health.py
    rm ~/tpu-*.sh ~/tpu-*.py ~/tpups.py
  '
done
```

The `/usr/local/bin/tpu-health` symlink (created in the loop below) is still
useful for `matt`'s PATH; the file mode at the symlink target is what
enforces admin-only access.

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-device.sh /usr/local/bin/tpu-device'
  ssh tpu$t 'sudo ln -sf /home/shared/tpups.py /usr/local/bin/tpups'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-usage.py /usr/local/bin/tpu-usage'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-heatmap.py /usr/local/bin/tpu-heatmap'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-health.py /usr/local/bin/tpu-health'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-warmup.sh /usr/local/bin/tpu-warmup'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-handbook.sh /usr/local/bin/tpu-handbook'
done
```

Install `tpups` into the login MOTD so users see cluster status on SSH login.
This is a **copy** (not a symlink) so that `/home/shared/` modifications can't
get code execution as root via MOTD:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo cp /home/shared/tpups.py /etc/update-motd.d/99-tpups'
done
```

Set up heartbeat and status web server as systemd services:

```
for t in 0 1 2 3; do
  scp conf/tpu-heartbeat.service conf/tpu-heartbeat-web.service tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/tpu-heartbeat.service ~/tpu-heartbeat-web.service /etc/systemd/system/ && rm ~/tpu-heartbeat.service ~/tpu-heartbeat-web.service'
  ssh tpu$t 'sudo systemctl daemon-reload'
  ssh tpu$t 'sudo systemctl enable --now tpu-heartbeat.service tpu-heartbeat-web.service'
done
```

The web server logs to `/dev/shm/tpu-heartbeat-web.log` (tmpfs) rather than
disk to avoid ext4 journal contention when training jobs are writing heavily.
Install the logrotate config to cap the log size (see logrotate section below).

## TPU metrics sidecar

`tpu-metrics.service` polls each user libtpu's gRPC metrics service on
`localhost:(8431 + first_owned_chip_id)` every 5s and writes
`/home/shared/heartbeat/metrics.json`. The companion `tpu-heartbeat-web`
already serves the entire `heartbeat/` directory, so the new file is
automatically reachable on `:8080`. `tpups` joins the metrics with `status.json`
to populate the `MEM`/`DUT` columns and the `--watch` view.

Unlike `tpu-heartbeat.py` (stdlib only), the sidecar imports `tpu_info` and
`grpc`, so it runs out of a dedicated root-owned venv at
`/opt/tpu-metrics.venv/`. **One-time bootstrap** per VM:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo uv venv --python /usr/bin/python3.11 /opt/tpu-metrics.venv && \
             sudo uv pip install --python /opt/tpu-metrics.venv/bin/python \
               tpu-info==0.11.0 libtpu==0.0.40 && \
             sudo uv pip freeze --python /opt/tpu-metrics.venv/bin/python | \
               sudo tee /opt/tpu-metrics.venv/requirements.txt > /dev/null'
done
```

Pin libtpu (currently `0.0.40`) to a known-good release. The sidecar's gRPC
queries are version-agnostic (the protocol has been stable across observed user
libtpus 0.0.30..0.0.40), so this pin only affects the sidecar process itself.
`tpu-info` is installed alongside as a side effect of needing the
`tpu_info.metrics` library; the binary at `/opt/tpu-metrics.venv/bin/tpu-info`
is admin-invokable for ad-hoc debugging but is not on user PATH.

Tensorcore utilisation is **not** collected: the upstream `tpu_info` CLI
itself reports `N/A` on this cluster (the `vbar control agent` that publishes
that metric is not installed). Re-add `TCU` to the sidecar and `tpups` once
the underlying agent is available.

Install the unit and start the service:

```
for t in 0 1 2 3; do
  scp conf/tpu-metrics.service tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/tpu-metrics.service /etc/systemd/system/ && \
             rm ~/tpu-metrics.service && \
             sudo systemctl daemon-reload && \
             sudo systemctl enable --now tpu-metrics.service'
done
```

Restart cadence is `RestartSec=30` with `StartLimitBurst=5` —
intentionally laxer than heartbeat's 5s, since libtpu/tpu_info imports are
exactly the kind of code that breaks under version skew and we don't want a
crash loop spamming journald.

**Reprovisioning the venv** (e.g. to bump pinned versions): re-run the
bootstrap loop above with new pins and `sudo systemctl restart
tpu-metrics.service` on each VM. The lockfile at
`/opt/tpu-metrics.venv/requirements.txt` is the audit record.

## TPU dashboard

`tpu-dashboard.service` runs on **tpu0 only**. It polls each VM's
`tpu-heartbeat-web` (`http://tpu{0..3}:8080/{status,metrics}.json`) every
5s in parallel and serves an HTML page (vendored uPlot) plus a JSON API
on port `8082`. Stdlib only, no venv. The page shows:

* a live `tpups`-style table of per-chip occupancy + HBM% + duty%, and
* HBM and duty-cycle time-series plots for every chip across the
  cluster (selectable 1m / 10m / 1h / 6h / 24h windows).

The dashboard keeps a 24h rolling history of HBM and duty per chip in
memory. To keep all 16 chips on a shared X axis (so charts render with
contiguous lines), each poll cycle stamps every chip with the
dashboard's poll timestamp rather than the upstream sidecar's
`last_updated`; stale upstreams record `(ts, None, None)` gap sentinels.

Port `8082` was chosen because `8081` is informally claimed by user
`python -m http.server` instances in the wild on this cluster. Users
reach the dashboard via `ssh -L 8082:localhost:8082 tpu0`.

History is **in-memory only** — a service restart wipes it. Acceptable
because this is a convenience tool, not telemetry of record. The first
data point appears within ~5s of startup, the chart fills out
progressively over the 24h window.

The vendored uPlot files in `shared-scripts/dashboard/` are pinned at
release `1.6.32` from
[cdn.jsdelivr.net/npm/uplot@1.6.32/dist/](https://cdn.jsdelivr.net/npm/uplot@1.6.32/).
SHA256s:

```
19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f  uPlot.iife.min.js
df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04  uPlot.min.css
```

To re-vendor on upgrade, replace these with new files and update the
hashes here.

Install (tpu0 only — no cluster-wide loop):

```
sudo install -d -m 755 /home/shared/dashboard
sudo install -m 755 shared-scripts/dashboard/tpu-dashboard.py /home/shared/dashboard/
sudo install -m 644 \
    shared-scripts/dashboard/index.html \
    shared-scripts/dashboard/uPlot.iife.min.js \
    shared-scripts/dashboard/uPlot.min.css \
    /home/shared/dashboard/
sudo install -m 644 conf/tpu-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tpu-dashboard.service
```

(Run from `/home/matt/tpus/` on tpu0. No `scp` since deployment is
local-only — tpu0 is the only target.)

To verify: `curl -s http://localhost:8082/api/timeseries?since=0 | jq
'.nodes'` should show all four nodes with `stale: false` once the
poller has had ~10s to do its first round.

## Installing user handbook

`user-handbook.md` is deployed to `/usr/local/share/doc/tpus/user-handbook.md`
on each VM (FHS-standard location for locally-installed documentation). This
gives every user a stable, absolute path to reference from their shell
profile, `CLAUDE.md`, etc. — stable across home-dir migrations and not
dependent on whether they've cloned this repo.

Mode 644 owned by `root:root`: world-readable, only admin can modify. Per-VM
install (not `/storage`) so it survives a JuiceFS outage.

```
for t in 0 1 2 3; do
  scp user-handbook.md tpu$t:
  ssh tpu$t 'sudo install -d -m 755 /usr/local/share/doc/tpus && sudo install -m 644 ~/user-handbook.md /usr/local/share/doc/tpus/user-handbook.md && rm ~/user-handbook.md'
done
```

**Keep in sync:** when you edit `user-handbook.md` in this repo, re-run the
loop above so the deployed copies match. The checked-in version is the source
of truth; `/usr/local/share/doc/tpus/user-handbook.md` is a deployment
artifact, not an editing target. (`admin-handbook.md` is *not* deployed —
it's admin-only and lives in the repo.)

## Adding new users

Instruct users to select a username <USERNAME> and generate a public key as
follows:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpu -C <USERNAME>
```

The key will have format `ssh-ed25519 BLAHBLAH etc`, take the `BLAHBLAH` bit
only and their chosen username and run it through `adduser.sh` from tpu0.
The script creates the user with matching UID/GID on all 4 VMs, home at
`/storage/home/<username>` (on shared JuiceFS), installs the external SSH
key, and generates intra-cluster SSH keys so they can `ssh tpuX` between
VMs. For example:

```
./adduser.sh afiq AAAAC3NzaC1lZDI1NTE5AAAAINmp4YYoMgXP8MEQsMjkla+o81pwI7hj9EN6eIbFZzvV
```

Remember to append the invocation to `users.md`.

## TPU logging permission

The TPU runtime writes logs to `/tmp/tpu_logs/`. Whichever user first triggers
this directory's creation ends up owning it, and other users get "Permission
denied" warnings. To fix this, install a `tmpfiles.d` config that ensures the
directory is created with mode 1777 (world-writable + sticky bit, like `/tmp`
itself) on every boot:

```
for t in 0 1 2 3; do
  scp conf/tpu-logs.conf tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/tpu-logs.conf /etc/tmpfiles.d/tpu-logs.conf && rm ~/tpu-logs.conf'
  ssh tpu$t 'sudo systemd-tmpfiles --create'
done
```

## System log size limits

Check storage:

```
# quick check
df -h
# investigate
sudo du -hd1 /
sudo du -h /var/log/* | sort -h
```

### Configuring logrotate

The default logrotate config for rsyslog only rotates weekly, which is not
enough---`kern.log` and `syslog` can blow up due to noisy kernel messages (e.g.
from the TPU gasket driver or the healthAgent Docker container hitting its OOM
limit). Install a custom logrotate config that rotates these daily and also at
100MB:

```
for t in 0 1 2 3; do
  scp conf/logrotate-rsyslog.conf conf/logrotate-heartbeat-web.conf tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/logrotate-rsyslog.conf /etc/logrotate.d/rsyslog'
  ssh tpu$t 'sudo install -m 644 ~/logrotate-heartbeat-web.conf /etc/logrotate.d/heartbeat-web'
  ssh tpu$t 'rm ~/logrotate-rsyslog.conf ~/logrotate-heartbeat-web.conf'
done
```

### Configuring journalctl

Alongside rsyslog, the system logs most messages also to journalctl's binary
database, which lives at `/var/log/journal/`. The same kinds of issues cause it
to blow up to 4GB which is the default max size. This seems unnecessary, can
lower the size limit to 500MB and then restart the service as follows.

```
for t in 0 1 2 3; do
    ssh tpu$t 'sudo mkdir -p /etc/systemd/journald.conf.d && echo -e "[Journal]\nSystemMaxUse=500M" | sudo tee /etc/systemd/journald.conf.d/size.conf && sudo systemctl restart systemd-journald'
done
```

### Configuring docker container log rotation

Docker's `json-file` log driver writes one JSON object per stdout/stderr line
to `/var/lib/docker/containers/<id>/<id>-json.log`. With no size limits set,
these files grow unbounded for the life of the container. The TPU image's
monitoring containers — particularly `instance_agent` (Docker API v1.41/1.43
mismatch noise) and `google-runtime-monitor` (TF-assumption errors when
running JAX) — write at ~50–70 MB/day combined per VM, accumulating gigabytes
over a typical multi-month uptime. See `issues/docker-api-version/` for the
underlying noise source.

Two complementary controls:

- A logrotate rule that caps existing containers' logs via `copytruncate`
  (docker keeps writing to the same fd; the file is just emptied in place).
  Active immediately on next daily logrotate run, no daemon reload required.
- `daemon.json` log driver defaults (`max-size`, `max-file`). Applied at
  container creation time, by the docker daemon's in-memory config. Two
  things are needed for this to take effect on a given container:
  1. The docker daemon must be **restarted** (`systemctl restart docker`)
     so it re-reads `daemon.json`. SIGHUP / `systemctl reload docker`
     does **not** cover `log-driver` / `log-opts`.
  2. The container must be **recreated** against the new daemon (not
     just started against an existing image). All six monitoring
     services (`healthagent`, `monitoringagent`, `cloud-ssa-agents`,
     `tpu-runtime`, `google-collectd`, `google-runtime-monitor`)
     `docker rm` + `docker run` in their systemd units, so a
     `systemctl restart <unit>` is enough — but it has to happen
     after the daemon restart.

Step 1 — drop in the config files. Logrotate is now active for existing
containers; `daemon.json` is staged but inert until the daemon restarts:

```
for t in 0 1 2 3; do
  scp conf/logrotate-docker.conf conf/docker-daemon.json tpu$t:
  ssh tpu$t 'sudo install -m 644 -o root -g root ~/logrotate-docker.conf /etc/logrotate.d/docker \
    && sudo install -m 644 -o root -g root ~/docker-daemon.json /etc/docker/daemon.json \
    && rm ~/logrotate-docker.conf ~/docker-daemon.json'
done
```

Step 2 — apply `daemon.json` to existing containers. Disruptive: stops
the monitoring containers (incl. tpu-runtime) for a few seconds while the
docker daemon and units cycle. Run `tpups` first; only proceed if no users
have TPU jobs in flight:

```
for t in 0 1 2 3; do
  ssh tpu$t '
    sudo systemctl stop tpu-runtime.service healthagent.service monitoringagent.service \
                        cloud-ssa-agents.service google-runtime-monitor.service google-collectd.service \
      && sudo systemctl restart docker.service \
      && sleep 2 \
      && sudo systemctl start google-collectd.service google-runtime-monitor.service cloud-ssa-agents.service \
                              monitoringagent.service healthagent.service tpu-runtime.service
  '
done
```

Verify: `cat /etc/docker/daemon.json` shows `bip` preserved at
`169.254.123.1/24` plus the new `log-driver` / `log-opts` keys.
`sudo logrotate -d /etc/logrotate.d/docker` parses cleanly and reports
`Handling 1 logs`. Per-container check that the new opts are active:

```
for c in healthagent monitoringagent instance_agent tpu-runtime google-collectd google-runtime-monitor; do
  sudo docker inspect $c --format "{{.Name}} {{.HostConfig.LogConfig}}"
done
# expect: {json-file map[max-file:3 max-size:100m]} on every line
```

For a fresh provision: deploy both files in step 1 as part of cluster setup,
then run step 2. Step 2 has to come after step 1 and the daemon restart
must happen *between* stopping the units and starting them — that ordering
is what makes the new container creations pick up the new log-opts.

### Trouble: Truncating bloated logs

If the logs have already grown too large, truncate them:

```
for t in 0 1 2 3; do ssh tpu$t 'sudo truncate -s 0 /var/log/kern.log /var/log/syslog'; done
```

For oversized docker container json logs (truncating in place is safe — docker
keeps writing to the same inode):

```
for t in 0 1 2 3; do
  ssh tpu$t "sudo find /var/lib/docker/containers -name '*-json.log' -size +100M -exec truncate -s 0 {} +"
done
```

### Trouble: healthAgent OOM

The Google-provided `healthAgent` Docker container (part of the default TPU VM
image) appears to have a memory leak. It gradually fills its 512MB cgroup limit
and then floods `kern.log` with OOM messages because it is configured with
`--oom-kill-disable=true`. Restart it with:

```
sudo systemctl restart healthagent.service
```

To prevent this from recurring, a weekly systemd timer automatically restarts
the container. Deploy it:

```
for t in 0 1 2 3; do
  scp conf/healthagent-restart.service conf/healthagent-restart.timer tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/healthagent-restart.service ~/healthagent-restart.timer /etc/systemd/system/ && rm ~/healthagent-restart.service ~/healthagent-restart.timer'
  ssh tpu$t 'sudo systemctl daemon-reload'
  ssh tpu$t 'sudo systemctl enable --now healthagent-restart.timer'
done
```

See `issues/healthagent-oom/` for a detailed bug report.

### Trouble: rsyslog `/dev/console` spam

The GCP-default `/etc/rsyslog.d/90-google.conf` routes `daemon,kern.*`
messages to `/dev/console` for boot-time crash diagnostics. rsyslog runs
as user `syslog`, which is not in the `tty` group, so it can't open
`/dev/console` (mode 0620, `root:tty`). Every matching message trips the
omfile action, flooding journald with `action-8-builtin:omfile suspended`
lines at thousands per hour per VM. Disable the rule by deploying a
commented-out replacement:

```
for t in 0 1 2 3; do
  scp conf/rsyslog-90-google.conf tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/rsyslog-90-google.conf /etc/rsyslog.d/90-google.conf && rm ~/rsyslog-90-google.conf'
  ssh tpu$t 'sudo systemctl restart rsyslog'
done
```

The daemon/kern messages still reach the usual `/var/log/*` and journal
destinations via other rsyslog rules; we're only dropping the console
fan-out, which was never working anyway.

## /tmp age-based cleanup

The upstream `/usr/lib/tmpfiles.d/tmp.conf` only clears `/tmp` on boot. TPU
VMs don't reboot, so `/tmp` accumulates indefinitely (stale ssh sockets,
rotated tpu-runtime logs, old wandb artifacts, etc.). We override it with a
14-day age so the daily `systemd-tmpfiles-clean.timer` prunes old entries.

```
for t in 0 1 2 3; do
  scp conf/tmpfiles-tmp.conf tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/tmpfiles-tmp.conf /etc/tmpfiles.d/tmp.conf && rm ~/tmpfiles-tmp.conf'
done
```

Our file is named `tmp.conf` at the destination to override the upstream
file of the same name in `/usr/lib/tmpfiles.d/`. Managed subdirs like
`.X11-unix` and `/tmp/tpu_logs/` have their own tmpfiles.d rules and are
unaffected.

To flush the existing backlog immediately rather than waiting for the next
daily run:

```
sudo systemd-tmpfiles --clean
```

## Disabling needrestart auto-restart

`needrestart` runs as a `DPkg::Post-Invoke` hook (`/etc/apt/apt.conf.d/99needrestart`)
after every `apt` operation. Its default behaviour is to scan running services
for stale library mappings (e.g. an old `libsystemd0.so` still mapped after the
package was upgraded) and **automatically restart them**. On this cluster that
has caused cascading restarts of `systemd-resolved`, `systemd-networkd`,
`redis-server`, `sshd`, `dbus`, etc. as a side effect of an unrelated
`apt install`. Side effects observed: ~90 s DNS outages, brief Redis outages
that nearly unmounted `/storage`, and a flurry of "GCS" errors counted by
JuiceFS while DNS was down.

Override needrestart to list-only mode so the post-apt scan still runs (and
its findings show up via `tpu-health` → `stale libs`) but no service is
touched. Reboots are scheduled manually.

```
for t in 0 1 2 3; do
  scp conf/needrestart-list-only.conf tpu$t:
  ssh tpu$t 'sudo install -m 644 -o root -g root ~/needrestart-list-only.conf /etc/needrestart/conf.d/list-only.conf && rm ~/needrestart-list-only.conf'
done
```

The override file sets `$nrconf{restart} = 'l'` (list mode). needrestart
loads any `*.conf` in `/etc/needrestart/conf.d/` after the main config, so
this overrides the upstream default without editing the package-managed
file.

## Configuring intra-cluster SSH

All users can `ssh tpuX` between VMs using `/etc/hosts` for name resolution,
a system-wide SSH client config for the identity file, and per-user cluster
keys.

Deploy cluster hostnames to `/etc/hosts` and the SSH config:

```
for t in 0 1 2 3; do
  scp conf/cluster-hosts conf/cluster-ssh.conf tpu$t:;
  ssh tpu$t 'cat ~/cluster-hosts | sudo tee -a /etc/hosts > /dev/null';
  ssh tpu$t 'sudo mkdir -p /etc/ssh/ssh_config.d';
  ssh tpu$t 'sudo install -m 644 ~/cluster-ssh.conf /etc/ssh/ssh_config.d/cluster-ssh.conf';
  ssh tpu$t 'rm ~/cluster-hosts ~/cluster-ssh.conf';
done
```

Per-user cluster keys are set up automatically by `adduser.sh`.

### Trouble: Bad configuration option (the "first byte" bug)

Symptom: `ssh` reports a bogus parse error on line 1 of `~/.ssh/config`,
e.g. `Bad configuration option: ost` — `Host` with the leading byte missing.

Cause: **overly-permissive modes on the user config file.** OpenSSH's
strict-modes check on `~/.ssh/config` does not abort cleanly on
group/other-writable mode (which is what the docs imply); instead it
silently drops the first byte before parsing, producing this confusing
off-by-one. Repro is one line:

```
chmod 0664 ~/.ssh/config && ssh -G github.com   # parse error: "ost"
chmod 0600 ~/.ssh/config && ssh -G github.com   # works
```

Fix: `chmod 0600 ~/.ssh/config`. New users trip this easily because the
Debian/Ubuntu user-private-group default umask `0002` yields `0664` on
files created by an editor. `adduser.sh` now pre-seeds `~/.ssh/config`
at `0600` so the user inherits the mode when they edit-and-save.

Note: the leading blank line currently in the deployed `/etc/ssh/ssh_config`
is a vestigial workaround from when this was misdiagnosed as a system-wide
"first byte ignored" bug. It's harmless — system configs don't go through
the strict-modes path — but no longer load-bearing.

Full investigation: `issues/ssh-config-strict-modes.md`.

## Miscelaneous issues

### Trouble: bash shows a setlocale warning

The warning shows up thus:
```
/bin/bash: warning: setlocale: LC_ALL: cannot change locale (en_AU.UTF-8)
```

Fix it by installing the missing locale:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo locale-gen en_AU.UTF-8 && sudo update-locale LANG=en_AU.UTF-8'
done
```

## Default TPU environment variables

By default, JAX and PyTorch/XLA try to coordinate across all 4 VMs in the pod,
which causes programs to hang if they aren't launched with the right environment
variables. To prevent this, we install a `/etc/profile.d/` script that sets
safe defaults (single device on the current VM) for all bash login shells.

Deploy the config to each VM:

```
for t in 0 1 2 3; do
  scp conf/tpu-defaults.sh tpu$t:
  ssh tpu$t 'sudo install -m 644 ~/tpu-defaults.sh /etc/profile.d/tpu-defaults.sh && rm ~/tpu-defaults.sh'
done
```

Note: `/etc/profile.d/` is only sourced by bash (and sh) login shells. Zsh does
not source it, so zsh users (i.e. me) need the same exports in their `.zshrc`.
These are already included in `home-stuff/zshrc.zshrc`.

Users can still use `tpu-device` to override these defaults and target specific
devices or multiple devices.

Note: a plain `ssh tpuN 'cmd'` runs a non-interactive non-login shell, which
sources neither `/etc/profile.d/` nor `~/.zshrc`. So `ssh tpuN 'env | grep TPU_'`
will show nothing — on *all* VMs, not just one. To pick up the defaults across
SSH, force a login/interactive shell, e.g. `ssh tpuN 'bash -lc "cmd"'` or
`ssh tpuN 'zsh -ic "cmd"'`.

## Other packages

Other system packages:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y ffmpeg pandoc entr xvfb libopenmpi-dev'
done
```

## Installing pandoc-katex

[`pandoc-katex`](https://github.com/xu-cheng/pandoc-katex) is a pandoc filter
that pre-renders `$math$` to KaTeX HTML — useful for users converting markdown
notes or papers to web-friendly HTML without a JS runtime in the browser.

Upstream ships standalone binaries with no checksum file alongside, so we pin
our own sha256 of the release tarball we vetted. Re-runs of the install
procedure verify against this digest, so all four nodes end up with
bit-identical binaries and any future tampering with the release asset is
caught.

```
SHA256=eddde83efe69656511e512f511221491d5b5c1bf6291c45e5c06a86744f9502f
URL=https://github.com/xu-cheng/pandoc-katex/releases/download/0.1.11/pandoc-katex-0.1.11-x86_64-unknown-linux-gnu.tar.gz
TARBALL=pandoc-katex-0.1.11-x86_64-unknown-linux-gnu.tar.gz
for t in 0 1 2 3; do
  ssh tpu$t "
    set -e
    cd ~
    curl -fsSL -O '$URL'
    echo '$SHA256  $TARBALL' | sha256sum -c -
    tar xzf '$TARBALL' ./bin/pandoc-katex
    sudo install -m 755 -o root -g root ./bin/pandoc-katex /usr/local/bin/pandoc-katex
    rm '$TARBALL' ./bin/pandoc-katex
    rmdir ./bin
  "
done
```

Verify: `pandoc-katex --version` prints `pandoc-katex 0.1.11` on each node.
End-to-end smoke test:

```
echo '$E=mc^2$' | pandoc -f markdown -t html --filter pandoc-katex
```

should emit a `<span class="katex">…` block.

To upgrade: fetch the new release URL, compute its sha256, update the three
constants, and re-run the loop.

## Installing LaTeX

LaTeX has various distributions with various sizes
  (see [here](https://tex.stackexchange.com/questions/245982/differences-between-texlive-packages-in-linux#answer-504566)
  for notes on different options).
While we are a bit short on space, that should hopefully clear up soon based on
the JuiceFS project, and I kept running into missing packages, fonts, and tools
even with `texlive-latex-extra`. So I'm installing the full `texlive-full`.

There's a known trap: `texlive-full` pulls in `context`, whose post-install
script pregenerates the ConTeXt MarkIV format (`cont-en.fmt`). That step uses
LuaTeX, which `require()`s the `socket.core` Lua module via
`util-soc-imp-socket.lua`. Debian's `texlive-context` does not depend on
`lua-socket`, and even if `lua-socket` is installed, its files land under
`/usr/lib/x86_64-linux-gnu/lua/5.3/` — outside the `/usr/local/.../5.3/` paths
that texlua's `package.cpath` searches. When `socket.core` isn't found, TeX
prints `?` and waits for keyboard input on stdin, which the apt frontend never
provides. The install hangs, holding the dpkg lock indefinitely. Symptom:
`apt` stuck at `Pregenerating ConTeXt MarkIV format. This may take some
time...` for hours, with the deepest `luatex` process showing TIME=0,
`voluntary_ctxt_switches: 1`, and a kernel stack ending in
`n_tty_read+...` on `/dev/pts/<N>`.

To install texlive-full cleanly, install `lua-socket` first and symlink it
into texlua's search paths. The same trick fixes a node where the install has
already hung (after killing the stuck `luatex` and letting `apt` finish, the
ConTeXt format will be missing — re-run `mtxrun --script base --make cont-en`
once the symlinks are in place).

```
for t in 0 1 2 3; do
  ssh tpu$t '
    set -e
    # 1. lua-socket provides socket.core for Lua 5.3 (and 5.1/5.2/5.4)
    sudo apt install -y lua-socket
    # 2. expose Debian'\''s lua-socket under the /usr/local/.../5.3/ paths
    #    that texlua searches by default (texlua ignores /usr/lib/x86_64-linux-gnu)
    sudo mkdir -p /usr/local/lib/lua/5.3 /usr/local/share/lua/5.3
    sudo ln -snfT /usr/lib/x86_64-linux-gnu/lua/5.3/socket /usr/local/lib/lua/5.3/socket
    sudo ln -snfT /usr/lib/x86_64-linux-gnu/lua/5.3/mime   /usr/local/lib/lua/5.3/mime
    sudo ln -snfT /usr/share/lua/5.3/socket /usr/local/share/lua/5.3/socket
    sudo ln -snfT /usr/share/lua/5.3/mime   /usr/local/share/lua/5.3/mime
    for f in ltn12.lua mime.lua socket.lua; do
      sudo ln -sfT /usr/share/lua/5.3/$f /usr/local/share/lua/5.3/$f
    done
    # 3. now texlive-full can complete the ConTeXt format pregen unattended
    sudo DEBIAN_FRONTEND=noninteractive apt install -y texlive-full
    # 4. (only needed for recovery) explicitly rebuild cont-en if a prior
    #    install was killed mid-format-gen:
    # sudo mtxrun --script base --make cont-en
  '
done
```

Verify: `find /var/lib/texmf/luatex-cache -name cont-en.fmt` should show a
freshly-built ~11 MB file on each node. Compile a one-line test doc with
`echo "\\starttext hi\\stoptext" > /tmp/t.tex && cd /tmp && context t.tex` to
confirm end-to-end (produces `t.pdf`).

TODO: Confirm `texlive-full` comes with `latexmk cm-super dvipng`, previously
installed manually.

Ideally could use tectonic, but students may find that confusing. For space
reasons, I don't want to install both. Note tectonic is not available via apt,
only snap and brew, which adds complexity. Could install manually following
their instructions.

## Installing NodeJS / node apps

Node available from apt is ridiculously old. I went with nvm. This means it's a
local install and I only did it on tpu0 so far.

```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.5/install.sh | bash
```

Note: it adds stuff to zshrc to get onto path.

Installing apps can then use -g because that is still only me:

```
npm install -g @google/gemini-cli # google gemini
npm install -g @openai/codex      # openai codex
```

If I needed to install node system packages globally? Hope I never face that
issue, but probably nvm can be configured to handle it. Ideally it is just like
uv and people can take care of their own environments.


## Making myself at home

### Configuring my GitHub

On my local machine:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpus-gh -C "m@far.in.net"
```

Don't forget to add it to my github profile.

Then copy to each TPU VM:

```
for t in 0 1 2 3; do scp ~/.ssh/mfr-tpus-gh{,.pub} tpu$t:.ssh; done
```

Then on each machine, add it to the ssh agent:

```
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/mfr-tpus-gh
```

Actually, seems like the above is for passworded keys, and for some reason this
ssh agent is dying every time I log out (this didn't used to happen and doesn't
happen on my laptop?). A simpler solution seems to be to add this to
~/.ssh/config on each VM:

```
# GitHub keys
Host github.com
  User git
  IdentityFile ~/.ssh/{name-of-your-key}
  IdentitiesOnly yes
```

### Installing neovim

Install neovim and load config:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y neovim && mkdir -p ~/.config/nvim'
  scp home-stuff/init.vim tpu$t:.config/nvim/init.vim
done
```

### Installing zsh

Install zsh and load config:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y zsh && sudo chsh -s $(which zsh) matt'
  scp home-stuff/zshrc.zshrc tpu$t:.zshrc
done
```

## Security goals

The user handbook promises that non-admin users can't access each other's files
and that only the admin (matt) has elevated privileges. The users themselves
are trusted---the threat model is not malicious insiders but rather:

* Innocent mistakes by inexperienced users
* AI coding agents that users run with too-broad permissions
* AI coding agents that get exposed to prompt injection attacks

Standard isolation (no sudo, no cross-user file access) limits the blast radius
of these kinds of incidents. Maintaining this posture is also deliberate
practice: a security mindset matters more in higher-stakes settings, and this
cluster is a good low-stakes environment to build the habit.

The key invariants:

* Home directories are `drwxr-x---` (owner-only access).
* Non-admin users have no sudo access.
* `/home/shared/` scripts are owned by root and not writable by users. The
  MOTD script (`/etc/update-motd.d/99-tpups`) is a copy, not a symlink, so
  modifications to `/home/shared/` can't get code execution as root via MOTD.
* No user can write to anything that another user executes.
* In particular, no user can write to anything that `matt` executes, since
  compromising the admin account grants full sudo access to the cluster.

When adding new shared infrastructure (e.g. shared caches, shared storage),
**preserve these invariants.** In particular, be cautious about shared writable
directories whose contents end up on other users' execution paths (e.g. Python
package caches with symlink mode).

Not currently hardened (standard Linux defaults, acceptable for now):

* `/proc` has no `hidepid`, so users can see each other's process command lines
  via `ps`. Avoid passing secrets as CLI arguments.
* No disk quotas or cgroups, so one user could fill the disk or exhaust memory.

## Secrets

Credentials are stored in `secrets/` (gitignored). That directory holds
credentials that should not be in the public repo.

Contents:

* `redis.env`: Redis password for JuiceFS metadata engine. Two variable
  names with the same value — `REDIS_PASSWORD` is sourced by admin scripts
  (e.g. `redis-cli -a "$REDIS_PASSWORD"`), `META_PASSWORD` is the
  juicefs-native env var loaded by `storage.mount` via `EnvironmentFile=`.
  ```
  REDIS_PASSWORD=<password>
  META_PASSWORD=<password>
  ```
  Generate:
  ```bash
  python3 -c "import secrets; pw = secrets.token_urlsafe(24); print(f'REDIS_PASSWORD={pw}\nMETA_PASSWORD={pw}')" > secrets/redis.env
  ```
  Deployed to `/etc/juicefs/redis.env` (mode `0600 root:root`) on each
  node by the Mount JuiceFS section. **Do not put this password in the
  unit's `What=` URL** — systemd stores the full mount argv in its
  runtime state, which is readable by any user via `systemctl show -p
  ExecMount` regardless of the unit file's mode.

* `tpu-juicefs-sa-private-key.json`: GCP service account key for the
  `tpu-juicefs@` service account, used by JuiceFS to authenticate with the
  GCS backend. Generated in the shared storage section below. Deployed to
  `/etc/juicefs/sa-private-key.json` (mode `0600 root:root`) on each node.
  The unit points at it via
  `Environment=GOOGLE_APPLICATION_CREDENTIALS=...`. Should only be needed
  on the current cluster due to the OAuth scopes issue; not needed when we
  re-provision with `--scopes=cloud-platform`.

These secrets are only needed on the running cluster. If the cluster is
re-provisioned from scratch, generate fresh secrets and redeploy.

## Shared storage (JuiceFS)

We use JuiceFS Community Edition to provide a shared POSIX filesystem across
all 4 VMs. Data lives in a GCS bucket, metadata in Redis on tpu0, and each VM
caches hot files on its local boot disk. See `issues/storage-options.md` for
the full design rationale.

Architecture:

```
tpu0 ──┐                                  ┌── Redis (metadata, on tpu0)
tpu1 ──┤── juicefs mount at /storage ─────┤
tpu2 ──┤   (FUSE client on each VM)       └── gs://mfrs-tpu-cluster (data, GCS)
tpu3 ──┘
         each VM also has a local
         cache dir on its boot disk
```

### Provisioning cloud storage bucket

One-time set up a bucket. Storage class standard, non-public:

```bash
gcloud storage buckets create gs://mfrs-tpu-cluster \
  --location=us-central2 \
  --uniform-bucket-level-access
```

Don't need to redo this if the bucket is already created.

### Create service account for TPU VMs

Having created a bucket, the TPU VMs don't yet have access. Follow the
instructions [here](https://docs.cloud.google.com/tpu/docs/storage-buckets).

First, create a service account.

```bash
gcloud beta services identity create --service tpu.googleapis.com --project ace-line-457306-p7
# -> service-1054593878874@cloud-tpu.iam.gserviceaccount.com
```

Then, authorize the service account to read from and write to the buckets:

```bash
gcloud storage buckets add-iam-policy-binding gs://mfrs-tpu-cluster --member=serviceAccount:service-1054593878874@cloud-tpu.iam.gserviceaccount.com --role=roles/storage.objectViewer
gcloud storage buckets add-iam-policy-binding gs://mfrs-tpu-cluster --member=serviceAccount:service-1054593878874@cloud-tpu.iam.gserviceaccount.com --role=roles/storage.objectCreator
```

Check via permissions tab on the gcloud console. Should only need to do this
once for the project.

These roles from the docs seem to be insufficient for file deletion. Based on
[docs](https://docs.cloud.google.com/storage/docs/access-control/iam-roles),
should use `storage.objectUser` probably.

### Trouble: Can't add service account to TPU cluster, use key instead

It seems there is no way to add this service account to the TPU after creation.
So we're going to remember to do that next time and in the mean time, use a
service account key as a workaround.

Create a dedicated service account:
```
gcloud iam service-accounts create tpu-juicefs
```

Grant (only) the necessary permissions:
```
gcloud storage buckets add-iam-policy-binding gs://mfrs-tpu-cluster --member=serviceAccount:tpu-juicefs@ace-line-457306-p7.iam.gserviceaccount.com --role=roles/storage.objectUser
```

Make a service account key:
```
gcloud iam service-accounts keys create secrets/tpu-juicefs-sa-private-key.json --iam-account=tpu-juicefs@ace-line-457306-p7.iam.gserviceaccount.com
```

### Trouble: Service account key creation disabled?

Docs warn that the above command should not work by default. It did seem to
work for me. If it fails, follow the (confusing!?) instructions
[here](https://docs.cloud.google.com/iam/docs/keys-create-delete) to create a
tag key, attach it to the project, and make a new organisational policy based
on this tag key that disables the service account key creation disabled
constraint. Or, maybe just disable the constraint directly via organisation
policies. (I couldn't figure out how to do either of these actually; but that
is a problem for another day...)


### Install and configure Redis (tpu0 only)

Install Redis:

```
sudo apt install -y redis-server
```

Edit `/etc/redis/redis.conf` — four changes from the Ubuntu 22.04 defaults:

```
source secrets/redis.env
# Listen on localhost and tpu0's internal IP (default: bind 127.0.0.1 ::1)
sudo sed -i 's/^bind 127.0.0.1 ::1$/bind 127.0.0.1 10.130.0.12/' /etc/redis/redis.conf
# Set password (default: # requirepass foobared)
sudo sed -i "s/^# requirepass foobared$/requirepass ${REDIS_PASSWORD}/" /etc/redis/redis.conf
# Prevent key eviction — CRITICAL, evicting a key loses a file
# (default: # maxmemory-policy noeviction)
sudo sed -i '/^# maxmemory-policy noeviction/a maxmemory-policy noeviction' /etc/redis/redis.conf
# Enable append-only file for durability (default: appendonly no)
sudo sed -i 's/^appendonly no$/appendonly yes/' /etc/redis/redis.conf
```

Enable, start, and restart (restart picks up config if Redis started before
edits):

```
sudo systemctl enable --now redis-server
sudo systemctl restart redis-server
```

Verify:

```
source secrets/redis.env
redis-cli -a "$REDIS_PASSWORD" ping
#-> Should print: PONG
redis-cli ping
#-> Should print: NOAUTH Authentication required.
```

### Install and configure JuiceFS (all nodes)

Install JuiceFS from the official PPA:

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  ssh tpu$t 'sudo add-apt-repository -y ppa:juicefs/ppa && sudo apt-get update && sudo apt-get install -y juicefs'
done
```

Uncomment `user_allow_other` in `/etc/fuse.conf` on all nodes (required for
the `--allow-other` mount option, so all users can access the mount):

```
for t in 0 1 2 3; do
  ssh tpu$t "sudo sed -i 's/^#user_allow_other$/user_allow_other/' /etc/fuse.conf"
done
```

### Install redis-tools on client nodes (tpu1, tpu2, tpu3)

Install `redis-tools` on the JuiceFS client nodes so we can run diagnostics
(e.g. `redis-cli -h tpu0 --latency`) from each VM. tpu0 already has it via
`redis-server`, so only the other nodes need this:

```
for t in 1 2 3; do
  ssh tpu$t 'sudo apt-get install -y redis-tools'
done
```

Currently installed on: tpu1, tpu2, tpu3.

### Mount JuiceFS at `/storage` (all nodes)

The cluster filesystem is mounted at `/storage` via a systemd `.mount` unit on
each node. Data lives in Redis+GCS; the mount point is just the POSIX view.

The unit file `conf/storage.mount` has a password-less Redis URL in `What=`
(`redis://tpu0:6379/0`). The password is supplied to the `juicefs` mount
process via `META_PASSWORD` (a JuiceFS-recognised env var), loaded from
`/etc/juicefs/redis.env` (mode `0600 root:root`) via the unit's
`EnvironmentFile=` directive.

This keeps the password out of:

- `systemd`'s stored `ExecMount` argv (otherwise visible via
  `systemctl show -p ExecMount` to any logged-in user);
- `/bin/mount`'s `/proc/<pid>/cmdline`;
- the mounted juicefs process's `/proc/<pid>/cmdline`.

It lives only in `/etc/juicefs/redis.env` (root-only) and in the mount
process's `/proc/<pid>/environ`, which is mode 0400 owner-only.
`systemctl show` shows only the *path* of `EnvironmentFile=`, not its
contents — verified by inspection on a throwaway service unit.

The unit also sets `Environment="GOOGLE_APPLICATION_CREDENTIALS=/etc/juicefs/sa-private-key.json"`
so the JuiceFS client can authenticate with GCS. Without this, uploads get
403 "Provided scope(s) are not authorized" because the VM's metadata-server
tokens are read-only (see "Trouble: Service account and OAuth scopes" above).
The SA key file must be installed on each node before first mount.

The FUSE mount helper is discovered by `mount(8)` at `/sbin/mount.juicefs`,
which is a symlink to `/usr/bin/juicefs` (the PPA binary). On Ubuntu 22.04
`/sbin` is a symlink to `usr/sbin`, so one symlink covers both lookup paths.

Install SA key, Redis env file, mount helper symlink, mount point, and unit
file on every node:

```
for t in 0 1 2 3; do
  scp secrets/tpu-juicefs-sa-private-key.json secrets/redis.env \
      conf/storage.mount tpu$t:
  ssh tpu$t 'bash -s' <<'REMOTE'
    set -euo pipefail
    umask 077
    sudo install -d -m 0755 /etc/juicefs
    sudo install -m 0600 -o root -g root ~/tpu-juicefs-sa-private-key.json /etc/juicefs/sa-private-key.json
    sudo install -m 0600 -o root -g root ~/redis.env /etc/juicefs/redis.env
    sudo install -m 0644 -o root -g root ~/storage.mount /etc/systemd/system/storage.mount
    rm ~/tpu-juicefs-sa-private-key.json ~/redis.env ~/storage.mount
    sudo ln -sfn /usr/bin/juicefs /sbin/mount.juicefs
    sudo mkdir -p /storage
REMOTE
done
```

On tpu0 only, install a drop-in that orders the mount after `redis-server.service`
(the other nodes reach Redis over the network and need only the shared unit).
The drop-in uses `Wants=` rather than `Requires=`: `Requires=` would propagate
a `redis-server` restart into a `/storage` unmount attempt, which can drop
every user's open file in `/storage`. JuiceFS reconnects to Redis on its
own across a brief restart, so the strict coupling buys nothing:

```
sudo mkdir -p /etc/systemd/system/storage.mount.d
sudo install -m 0644 -o root -g root conf/storage.mount.d-redis.conf \
    /etc/systemd/system/storage.mount.d/redis.conf
```

Reload systemd and enable the mount on every node:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo systemctl daemon-reload && sudo systemctl enable --now storage.mount'
done
```

Verify: `mount | grep /storage` on each node shows
`JuiceFS:clusterhome on /storage type fuse.juicefs`. Non-root
`systemctl show -p ExecMount storage.mount` should show the argv containing
`redis://tpu0:6379/0` (no password). Non-root `cat /etc/juicefs/redis.env`
should fail with permission denied.

To change a mount option (e.g. `cache-size`), edit `conf/storage.mount`,
re-deploy with the same install loop, then `sudo systemctl daemon-reload`
and `sudo systemctl restart storage.mount` per node in sequence. Verify
the new option is live with
`pgrep -af "/usr/bin/juicefs " | grep -oE "<option>=[^,[:space:]]+"`.
Mount options aren't visible in `mount | grep /storage` (FUSE only shows
generic flags) or `systemctl show -p ExecMount` (which is also stale on
already-mounted units) — go via the running juicefs process's argv.

### Trouble: /storage target is busy on remount

`tpups` reports TPU device usage only, not anything about who has files
open under `/storage`. So even when `tpups` shows everything idle, a
`systemctl restart storage.mount` can fail with
`umount: /storage: target is busy` if any user process is holding files
or has its cwd inside `/storage` (and every user's home is under
`/storage/home/<u>`, so this includes any forgotten background process,
any disconnected SSH session that left jobs running under nohup/screen/
tmux, and any MPI runtime / multiprocessing pool whose launcher died
without reaping its workers).

Before any planned remount, check who's holding it:

```
sudo fuser -m /storage    # PIDs holding files / cwd inside /storage
who                       # active interactive sessions
```

Inspect any holders that show up (`ps -p <pid> -o user,etime,time,comm`
— very low cumulative CPU `time` over multi-day `etime` suggests
orphaned workers). Decide what to do case by case: defer the remount,
ping the user, or clean up specific stale processes. Don't write a
catch-all kill script for it.

### Backups (tpu0 only)

Hourly metadata-only backups: `juicefs dump | gzip` → `gs://mfrs-tpu-cluster/backups/`,
with a tiered retention policy (8 hourly + 4 daily + 3 weekly slots,
calendar-aligned, ~3-week coverage). Driven by a systemd timer on tpu0;
status surfaced through `tpu-health` (`bk timer` and `bk fresh` rows).
We do not back up file data — chunks live in GCS already and are
durable; metadata in Redis is the single point of failure. See
`issues/storage/storage-options.md` for design rationale.

Components:

- `admin-scripts/juicefs-backup.py` — the script. Deployed to
  `/usr/local/sbin/juicefs-backup` (root:root, mode 0750). Pure stdlib
  Python; shells out to `juicefs dump`, `gzip`, and `rclone`.
- `conf/rclone-juicefs-backup.conf` — rclone GCS backend config.
  Deployed to `/etc/rclone/juicefs-backup.conf` (root:root, mode 0640).
  References the existing SA key at `/etc/juicefs/sa-private-key.json`;
  `bucket_policy_only = true` is required because the bucket has uniform
  bucket-level access (without it, rclone tries to set per-object ACLs
  and gets HTTP 400).
- `conf/juicefs-backup.service` + `conf/juicefs-backup.timer` —
  hourly oneshot, `Persistent=true`. Deployed to
  `/etc/systemd/system/`.

Install rclone (apt `1.53.x` is fine; we only use stable `copy`/`lsf`/
`deletefile` subcommands):

```
sudo apt install -y rclone
```

Deploy on tpu0:

```
# rclone config
scp conf/rclone-juicefs-backup.conf tpu0:
ssh tpu0 'sudo install -d -m 0755 /etc/rclone \
       && sudo install -m 0640 -o root -g root \
            ~/rclone-juicefs-backup.conf /etc/rclone/juicefs-backup.conf \
       && rm ~/rclone-juicefs-backup.conf'

# script
scp admin-scripts/juicefs-backup.py tpu0:
ssh tpu0 'sudo install -m 0750 -o root -g root \
            ~/juicefs-backup.py /usr/local/sbin/juicefs-backup \
       && rm ~/juicefs-backup.py'

# systemd units
scp conf/juicefs-backup.service conf/juicefs-backup.timer tpu0:
ssh tpu0 'sudo install -m 0644 -o root -g root \
            ~/juicefs-backup.service /etc/systemd/system/juicefs-backup.service \
       && sudo install -m 0644 -o root -g root \
            ~/juicefs-backup.timer   /etc/systemd/system/juicefs-backup.timer \
       && rm ~/juicefs-backup.service ~/juicefs-backup.timer \
       && sudo systemctl daemon-reload \
       && sudo systemctl enable --now juicefs-backup.timer'
```

Verify:

```
sudo /usr/local/sbin/juicefs-backup       # one manual run; ~40s
systemctl list-timers juicefs-backup.timer
sudo rclone --config=/etc/rclone/juicefs-backup.conf \
    lsl gcs:mfrs-tpu-cluster/backups/
tpu-health                                # `bk fresh` row should be < 2h
```

#### Recovery from a metadata loss

If the live Redis db is lost or corrupted, restore from the most recent
dump in GCS. Run on tpu0:

```
. /etc/juicefs/redis.env; export META_PASSWORD
LATEST=$(sudo rclone --config=/etc/rclone/juicefs-backup.conf \
         lsf --files-only gcs:mfrs-tpu-cluster/backups/ | sort | tail -1)
sudo rclone --config=/etc/rclone/juicefs-backup.conf cat \
     "gcs:mfrs-tpu-cluster/backups/${LATEST}" \
     | gunzip \
     | juicefs load redis://tpu0:6379/0    # or a fresh Redis db for testing
juicefs fsck redis://tpu0:6379/0
```

Caveat: `juicefs load` writes into whatever db you point it at. Use
db 0 only if you've already lost or wiped it; otherwise direct it at a
fresh db (e.g. db 15 for a test) and inspect first.

End-to-end recovery test (validated 2026-04-25): load = 33s, fsck = 60s,
rc=0, 688K blocks / 662K slices / ~150 GB consistent.

### Maintenance: gc, fsck, and the deletion model (tpu0 only)

JuiceFS deletion happens in three layers; understanding which layer you're
looking at avoids confusion when a cleanup command does "nothing":

1. **Trash** (`/storage/.trash/<HHMM-group>/`). Every `rm` moves the file
   here, indistinguishable from a regular file in storage. Default
   retention is `TrashDays=1`. While in trash, GCS objects are *valid*
   (referenced by metadata) — `gc` will not flag them. Filenames inside
   each group are rewritten as `<parent_inode>-<inode>-<basename>` so the
   original location can be reconstructed for a manual restore.
2. **Delayed deletion** (`delfiles` / `delslices` lists in Redis). Once
   trash expires, the live mount's background scanner removes the trash
   entries and pushes the freed slices/files onto these lists, then walks
   the lists and deletes the GCS objects asynchronously.
3. **Leaked objects.** GCS objects with no metadata reference at all.
   Should not normally occur, but can result from interrupted writeback
   uploads, mount crashes mid-deletion, etc.

The relevant commands:

| Command                | What it does                                                                | When to run                       |
|------------------------|-----------------------------------------------------------------------------|-----------------------------------|
| `juicefs fsck`         | Cross-checks metadata vs storage; flags lost blocks / broken files (read-only). | Manual, ad-hoc when investigating.|
| `juicefs gc`           | Scans for leaked objects + reports counters (read-only — no `--dry-run` flag). | Manual, ad-hoc.                  |
| `juicefs gc --compact` | Merges small slices into bigger chunks (reduces GCS object count).          | Weekly, automated (see below).    |
| `juicefs gc --delete`  | Forces processing of the delfiles/delslices lists + cleans leaked objects.  | Manual reconciliation backstop.   |

`gc --delete` is **not** what empties the trash — `TrashDays` + the
mount's background scanner do that. `--delete` is the recovery tool when
that scanner has fallen behind (mount crashed mid-deletion, GCS was
unreachable, etc.).

To run any of these by hand, env-load Redis password and GCS credentials
inside a sudo'd shell so neither hits the world-readable `/proc/<pid>/cmdline`:

```
sudo bash -c '
  . /etc/juicefs/redis.env
  export META_PASSWORD GOOGLE_APPLICATION_CREDENTIALS=/etc/juicefs/sa-private-key.json
  juicefs fsck   redis://tpu0:6379/0           # consistency
  juicefs gc     redis://tpu0:6379/0           # check-only scan
  juicefs gc     redis://tpu0:6379/0 --delete  # force-cleanup pass
'
```

Reading the `gc` summary line: `scanned`, `valid`, `pending delete`,
`compacted`, `leaked`, `delslices`, `delfiles`, `skipped`. In normal
operation `leaked` and `delfiles` are 0; `compacted` and `delslices` may
be non-zero between weekly compaction runs.

#### Weekly `gc --compact` automation

A systemd timer runs `juicefs gc --compact` weekly to merge accumulated
small slices. Mirrors the backup unit but loads env directly in the unit
(no wrapper script needed for a single command):

- `conf/juicefs-gc-compact.service` — oneshot, ordered after
  `redis-server.service` and `storage.mount`. `EnvironmentFile=/etc/juicefs/redis.env`
  for `META_PASSWORD`; `Environment=GOOGLE_APPLICATION_CREDENTIALS=...`
  for the GCS SA key. `TimeoutStartSec=30min`.
- `conf/juicefs-gc-compact.timer` — `OnCalendar=Sun 04:00:00 UTC`,
  `Persistent=true`.

Deploy on tpu0:

```
scp conf/juicefs-gc-compact.service conf/juicefs-gc-compact.timer tpu0:
ssh tpu0 'sudo install -m 0644 -o root -g root \
            ~/juicefs-gc-compact.service /etc/systemd/system/juicefs-gc-compact.service \
       && sudo install -m 0644 -o root -g root \
            ~/juicefs-gc-compact.timer   /etc/systemd/system/juicefs-gc-compact.timer \
       && rm ~/juicefs-gc-compact.service ~/juicefs-gc-compact.timer \
       && sudo systemctl daemon-reload \
       && sudo systemctl enable --now juicefs-gc-compact.timer'
```

Verify:

```
sudo systemctl start juicefs-gc-compact.service   # one manual run; ~50s
systemctl list-timers juicefs-gc-compact.timer
tpu-health                                        # `gc timer` / `gc fresh` rows
```

`tpu-health` reports `gc timer` (timer active + last result success) and
`gc fresh` (age since last completion, parsed from
`systemctl show -p ExecMainExitTimestamp`). Thresholds: WARN at >14d
(one missed run), CRIT at >21d.

Caveat: `systemctl enable --now <timer>` resets the *service* unit's
timestamps. If `gc fresh` shows `never` immediately after enabling the
timer, trigger one run by hand (`sudo systemctl start
juicefs-gc-compact.service`) to populate it.

### Migrate a user's home to `/storage`

One-time procedure per user, codified as `admin-scripts/migrate-home.sh
<username>` (run from tpu0). It does preflight checks, then runs all 4
rsyncs in parallel with progress lines, then flips the passwd entry and
verifies `sudo -u <user> -i pwd` on each node. The manual checklist below
documents the same steps in case you ever need to do it by hand.

## Rebooting the cluster

Aim for a maintenance reboot roughly monthly to clear stale processes and
pick up patched libraries. `tpu-health` flags this with `WARN` once a node's
uptime crosses 28 days.

The post-`apt upgrade` recipe above reboots tpu1–tpu3 in parallel. For a
routine reboot **without** a preceding upgrade, prefer doing one node at a
time and verifying each before moving on, so any regression is caught before
it propagates. Order: `tpu1` → `tpu2` → `tpu3`, then `tpu0` last (since
you're sitting on it).

Pre-flight: confirm the cluster is idle.

```
tpups
```

Per remote node (replace `N` with `1`, `2`, `3` in turn):

```
ssh tpuN 'sudo shutdown -r now'  # SSH closes (exit 255) — expected
# wait for it to come back (~100-130s typical):
until ssh -o ConnectTimeout=5 tpuN 'uptime' 2>/dev/null; do sleep 5; done
sleep 20  # let services settle
tpu-health
```

`tpu-health` should show, for the rebooted node: uptime 0d, stale libs 0,
`/storage` mounted, services running, redis PONG.

Finally, tpu0 — use `+1` instead of `now` so the SSH session can exit
cleanly:

```
sudo shutdown -r +1
```

The ~2-minute window during tpu0's reboot takes redis and `tpu-dashboard`
offline; remote VMs' heartbeat services reconnect once redis is back, and
JuiceFS on tpu1–tpu3 is independent (each has its own FUSE client to GCS).
Reconnect after ~3 minutes and run `tpu-health` to confirm. `gc fresh:
never` post-reboot is expected — the weekly timer will fire on its natural
cadence.

## TODO: Job management

See this discussion with gemini for hq configuration:

* https://gemini.google.com/share/379ddb7d8ea9

Plan:

* Provision maybe 14/16 nodes in this queue (save one or two for interactive
  use?)
* Needs a persistent disk for script and data storage? Or NFS again.
* Can use /path/to/venv/bin/python as the command to automatically import the
  venv.

