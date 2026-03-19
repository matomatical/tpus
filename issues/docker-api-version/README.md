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
