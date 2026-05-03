# Bug: Docker API version mismatch in TPU VM monitoring agents

## Summary

The Google-provided monitoring containers on TPU VMs use Docker client API
versions (1.41 or 1.43) that are older than the minimum supported by the Docker
engine on the VM (1.44). This causes error messages every 3 seconds on every VM,
contributing to syslog bloat.

## Error messages

From `monitoringagent` container (via `sh` process):
```
E0319 18:51:03.545610  1 docker_manager.go:971] Couldn't fetch containers from docker:
  Error response from daemon: client version 1.41 is too old.
  Minimum supported API version is 1.44, please upgrade your client to a newer version

E0319 18:51:03.545699  1 monitoring.go:326] Failed to get container health:
  failed to list containers: docker command ContainerList() failed with error:
  Error response from daemon: client version 1.41 is too old.
  Minimum supported API version is 1.44, please upgrade your client to a newer version
```

From `instance_agent` container (tpu1, tpu3 use client v1.43):
```
E0319 18:51:06.783265  156703 docker_manager.go:1238] Couldn't fetch containers from docker:
  Error response from daemon: client version 1.43 is too old.
  Minimum supported API version is 1.44, please upgrade your client to a newer version
```

## Frequency

Every ~3 seconds on every VM, two error lines per occurrence.

## Affected VMs

All 4 VMs (tpu0–tpu3). tpu0 and tpu2 use client v1.41, tpu1 and tpu3 use v1.43.

## Cause

The TPU VM image ships a Docker engine that requires API v1.44+, but the
bundled monitoring agent container images use older Docker client libraries.
This is a version skew in the TPU VM image — the user cannot fix it without
Google updating the container images.

## Impact

- Log noise: ~2 error lines every 3 seconds per VM to syslog
- Container health monitoring is non-functional (cannot list containers)
- Contributes to syslog growth over time

## Related

The monitoring stack also produces errors due to assuming TensorFlow workloads
(polling `localhost:8472` for streamz, looking for `/tmp/tflogs/main.INFO`,
`/tmp/debugging`, and `localhost:8466` for profiling). These fail when running
JAX instead of TensorFlow. These are not version-related but are part of the
same general pattern of the monitoring stack not matching the actual workload.

## Update 2026-05-03: same noise also bloats docker container json logs

While auditing tpu0's disk in preparation for a JuiceFS cache-budget bump, we
found that Docker's per-container `json-file` log driver is configured with no
size limits (`/etc/docker/daemon.json` only sets `bip`), so each container's
`/var/lib/docker/containers/<id>/<id>-json.log` grows unbounded. The same
"client version 1.41 is too old" lines from `instance_agent` and the same
TF-assumption errors from `google-runtime-monitor` end up there as well as in
syslog.

Measured rates on tpu0 over its 80-day uptime:

| container               | total size | lines     | rate       |
|-------------------------|-----------:|----------:|------------|
| instance_agent          | 3.7 GiB    | 9.9 M     | ~46 MB/day |
| google-runtime-monitor  | 1.5 GiB    | 5.0 M     | ~19 MB/day |
| healthagent             | 249 MiB    | (varies)  | ~3 MB/day  |
| monitoringagent         | 164 MiB    | 0.9 M     | ~5 MB/day  |

Cluster-wide totals at the time of the audit: tpu0 5.7 GiB, tpu1 3.2 GiB,
tpu2 5.1 GiB, tpu3 3.0 GiB. The tpu0/tpu2 vs tpu1/tpu3 split simply reflects
VM uptime — tpu1/tpu3 were rebooted 26 days more recently, which truncated
the json log on container restart.

### Resolution

Two complementary controls deployed 2026-05-03:

- `conf/logrotate-docker.conf` (installed at `/etc/logrotate.d/docker`):
  rotates `/var/lib/docker/containers/*/*-json.log` daily or at 100 MiB,
  keeps 3 compressed rotations, uses `copytruncate` so docker keeps writing
  to the same fd. This caps existing containers' growth without restarting
  anything.
- `conf/docker-daemon.json` (installed at `/etc/docker/daemon.json`,
  preserves the existing `bip` value): adds `log-driver: json-file` and
  `log-opts: { max-size: 100m, max-file: 3 }`. To take effect, two
  steps are needed:
  - The docker daemon must re-read `daemon.json`. SIGHUP/`systemctl
    reload docker` does **not** reload `log-driver` / `log-opts` — only
    `systemctl restart docker` does. Existing running containers keep
    the in-memory daemon config they were created against, so the
    daemon-restart alone doesn't update them.
  - Each monitoring container must be recreated against the new daemon
    config (not just restarted). All six TPU-image monitoring services
    (`healthagent`, `monitoringagent`, `cloud-ssa-agents` →
    `instance_agent`, `tpu-runtime`, `google-collectd`,
    `google-runtime-monitor`) do `docker rm` + `docker run` in their
    systemd unit's `ExecStartPre` / `ExecStart`, so a `systemctl
    restart <unit>` recreates the container with the daemon's current
    log-opts.
  Once both have happened, `docker inspect <container> --format
  '{{.HostConfig.LogConfig}}'` reports
  `{json-file map[max-file:3 max-size:100m]}`. Done cluster-wide on
  2026-05-03.

Existing oversized logs were truncated cluster-wide once with
`sudo find /var/lib/docker/containers -name '*-json.log' -size +100M -exec
truncate -s 0 {} +`, reclaiming ~17 GiB total.

### Side note: stale "Restart counts" line

`google-runtime-monitor`'s log periodically prints lines like
`Restart counts of docker containers: ... healthagent=10 | monitoringagent=31`,
and the `monitoringagent=31` value briefly looked like an instability signal.
On inspection: `docker inspect monitoringagent` reports `RestartCount=0` on
all four nodes, the container has been continuously `Up` for 4+ weeks, and
the counter raced from 3 to 31 in 28 seconds on 2026-04-03 04:01 UTC (when
Google's management plane recreated the container) and has been frozen at 31
ever since. So the counter is not a live restart signal, just a stale
artifact of one container-recreation event.
