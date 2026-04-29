"""TPU metrics sidecar — polls each user libtpu's gRPC metrics service and
writes per-chip HBM and duty-cycle data to /home/shared/heartbeat/metrics.json
every INTERVAL seconds. Consumed by tpups (joined with status.json) for the
UTIL column, and exposed by tpu-heartbeat-web on :8080 for cluster-wide
aggregation.

Design: one libtpu instance owns each user job. By the cluster convention
(tpu-device.sh), that instance binds gRPC metrics on localhost:(8431 + first
owned chip id). For each PID we group its owned chips ascending, query the
matching port with `get_chip_usage()`, and assign the returned Usage[i]
(sorted by process-local device_id 0..N-1) to chips_owned[i] (sorted ascending
global ids). gRPC is used in preference to libtpu.sdk.tpumonitoring because
the latter is libtpu-version-coupled (the sidecar's libtpu only sees data
from version-compatible peers); gRPC works across versions.

Runs under /opt/tpu-metrics.venv/bin/python (needs the venv's libtpu and
tpu_info packages — system python3 has no libtpu). Started by
tpu-metrics.service.
"""
import json
import os
import re
import socket
import sys
import time
import traceback
from collections import defaultdict

import grpc
from tpu_info import device, metrics as tpu_metrics

# # #
# CONFIGURATION

INTERVAL = 5
SCHEMA_VERSION = 1
NUM_CHIPS = 4              # TPU v4 — 4 chips per VM
BASE_PORT = 8431           # convention: per-chip port = BASE_PORT + first_owned_chip_id
ACCEL_RE = re.compile(r"/dev/accel(\d+)$")

OUTPUT_PATH = "/home/shared/heartbeat/metrics.json"
TEMP_PATH = OUTPUT_PATH + ".tmp"


# # #
# COLLECTION


def collect_devices(chip_type):
    """Return a list of NUM_CHIPS device records (see module docstring)."""
    devices = [{"id": i, "available": False, "reason": "idle"} for i in range(NUM_CHIPS)]

    # Group owned chips by PID (one libtpu instance per user process).
    owners = device.get_chip_owners()  # {'/dev/accelN': pid}
    chips_by_pid = defaultdict(list)
    for path, pid in owners.items():
        m = ACCEL_RE.match(path)
        if m:
            chips_by_pid[pid].append(int(m.group(1)))
    for pid in chips_by_pid:
        chips_by_pid[pid].sort()

    for pid, chips in chips_by_pid.items():
        port = BASE_PORT + chips[0]
        addr = f"localhost:{port}"
        try:
            usages = tpu_metrics.get_chip_usage(chip_type, addr=addr)
        except grpc.RpcError as e:
            # UNAVAILABLE = libtpu hasn't bound the port yet (job warming up).
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                for cid in chips:
                    devices[cid] = {"id": cid, "available": False, "reason": "warming"}
            else:
                err = f"{e.code().name}: {e.details() or ''}"
                for cid in chips:
                    devices[cid] = {"id": cid, "available": False, "reason": "error", "error": err}
            continue
        except AssertionError:
            # libtpu metric warm-up race (totals/usages/duty length mismatch).
            for cid in chips:
                devices[cid] = {"id": cid, "available": False, "reason": "warming"}
            continue
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            for cid in chips:
                devices[cid] = {"id": cid, "available": False, "reason": "error", "error": err}
            continue

        # Defensive: if port serves a different number of chips than this PID
        # owns (e.g. the user-set TPU_VISIBLE_DEVICES diverges from the
        # tpu-device convention), surface as warming rather than mis-attribute.
        if len(usages) != len(chips):
            for cid in chips:
                devices[cid] = {"id": cid, "available": False, "reason": "warming"}
            continue

        # usages is sorted by Usage.device_id (process-local, 0..N-1).
        # Map index -> global chip id via chips (sorted ascending).
        for i, cid in enumerate(chips):
            u = usages[i]
            devices[cid] = {
                "id": cid,
                "available": True,
                "hbm_used": int(u.memory_usage),
                "hbm_total": int(u.total_memory),
                "duty_cycle_pct": float(u.duty_cycle_pct),
            }

    return devices


# # #
# MAIN


def write_atomic(payload):
    with open(TEMP_PATH, "w") as f:
        json.dump(payload, f)
    os.rename(TEMP_PATH, OUTPUT_PATH)


def main():
    chip_type, _ = device.get_local_chips()
    node = socket.gethostname()
    while True:
        start = time.time()
        try:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "node": node,
                "last_updated": start,
                "devices": collect_devices(chip_type),
            }
            write_atomic(payload)
        except Exception:
            traceback.print_exc(file=sys.stderr)
        elapsed = time.time() - start
        time.sleep(max(0, INTERVAL - elapsed))


if __name__ == "__main__":
    main()
