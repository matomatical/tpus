# Bug Report: healthAgent OOM loop floods kern.log on TPU VMs

## Summary

The `healthAgent` Docker container on TPU VMs appears to have a memory leak
that causes it to exhaust its 512MB cgroup limit. Because the container is
configured with `--oom-kill-disable=true` and `--oom-score-adj=-1000`, the
kernel OOM killer cannot resolve the situation, resulting in an infinite OOM
invocation loop that floods `kern.log` with messages multiple times per second.
The container becomes completely unresponsive (`docker exec` hangs
indefinitely).

I have observed this repeatedly across multiple TPU VMs over several months.
Restarting the VM resolves it temporarily, but the issue seems to recur. In the
latest recurrence, Claude Code and I looked into the issue together after I
noticed that kern.log grew to 16GB, and we wrote this report. In previous cases
the logs grew larger and in one instance even filled up the VM disk making it
impossible to SSH in and causing data loss.


## Environment

- **TPU VM image**: tpu-ubuntu2204-base
- **Kernel**: 5.19.0-1022-gcp #24~22.04.1-Ubuntu
- **Container image**: `gcr.io/cloud-tpu-v2-images/tpu_agents:cl_695344571`
- **Systemd unit**: `/etc/systemd/system/healthagent.service`

## When we observed it

- **TPU VM image**: tpu-ubuntu2204-base
- The container had been running for ~17 days when we noticed the issue, but we
  do not know exactly when the OOM loop began or what triggers the leak
- The VMs were running JAX training workloads on all 4 TPU devices, but we have
  not confirmed whether workload activity is relevant

## Key observations

The container is launched with `--memory=512m`, `--oom-kill-disable=true`, and
`--oom-score-adj=-1000`. When the memory limit is reached:

- The OOM killer is invoked but cannot kill the process
- The `failcnt` reached 142 million before we noticed
- The systemd unit has `Restart=on-failure`, which would recover the situation
  automatically, but the OOM kill protection prevents the container from dying,
  so the restart never triggers

At the time we captured the kern.log, the container's cgroup memory stats showed
all 512MB consumed, almost entirely by anonymous (heap) memory that was marked
inactive, which is consistent with a memory leak. The kern.log snippet and
`docker inspect` output are below.

## Workaround

```bash
sudo systemctl restart healthagent.service
```

## Appendix: kern.log snippet

This is one iteration of the OOM loop. This message repeats multiple times per
second indefinitely.

```
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925903] healthAgent invoked oom-killer: gfp_mask=0x100cca(GFP_HIGHUSER_MOVABLE), order=0, oom_score_adj=-1000
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925910] CPU: 144 PID: 841223 Comm: healthAgent Tainted: G           O      5.19.0-1022-gcp #24~22.04.1-Ubuntu
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925913] Hardware name: Google Google Compute Engine/Google Compute Engine, BIOS Google 10/25/2025
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925271] memory: usage 524288kB, limit 524288kB, failcnt 142151742
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925272] swap: usage 0kB, limit 524288kB, failcnt 0
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925274] Memory cgroup stats for /system.slice/docker-69522f1a69d3061926f9b1250c8bfe6a1ecd0a0cd4e3c2eb734b37dac0cea279.scope:
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925284] anon 528056320
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925284] file 0
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925284] kernel 7852032
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925284] inactive_anon 528052224
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925284] active_anon 4096
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925289] Tasks state (memory values in pages):
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925289] [  pid  ]   uid  tgid total_vm      rss pgtables_bytes swapents oom_score_adj name
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925295] [3738523]     0 3738523  3758148   128911  2760704        0         -1000 healthAgent
Mar 19 17:21:44 t1v-n-ab15a7e0-w-2 kernel: [3054438.925297] Out of memory and no killable processes...
```

## Appendix: healthagent.service

```
[Unit]
Description=TPU Health Agent
After=docker.service

[Service]
Environment="HOME=/home/tpu-runtime"
EnvironmentFile=/home/tpu-runtime/tpu-env
ExecStart=/usr/bin/docker run --memory=512m --cpus="1.0" --rm --name=healthagent --pid=host --net=host --privileged --oom-kill-disable=true --oom-score-adj=-1000 -v /usr/...
ExecStop=/usr/bin/docker stop healthagent
Restart=on-failure
RestartSec=5
OOMScoreAdjust=-1000

[Install]
WantedBy=multi-user.target
```

## Appendix: docker inspect (abridged)

```json
{
    "Id": "69522f1a69d3061926f9b1250c8bfe6a1ecd0a0cd4e3c2eb734b37dac0cea279",
    "Created": "2026-03-02T12:45:19.898758922Z",
    "Path": "tpu_agents/bin/healthAgent",
    "Args": ["--check-runtime-server-health=true", "--logtostderr", "unhealthy-maintenance"],
    "State": {
        "Status": "running",
        "Pid": 3738523,
        "OOMKilled": false
    },
    "HostConfig": {
        "Memory": 536870912,
        "OomScoreAdj": -1000,
        "OomKillDisable": null,
        "PidMode": "host",
        "Privileged": true,
        "NetworkMode": "host",
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}
    }
}
```
