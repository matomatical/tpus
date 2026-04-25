#!/usr/bin/python3
"""JuiceFS metadata backup -> GCS, with tiered retention.

Runs `juicefs dump`, gzips, uploads to gs://mfrs-tpu-cluster/backups/, and
prunes older backups. Retention policy is tiered "newest in each slot":

    8 hourly slots (UTC hours), then 4 daily slots (UTC days),
    then 3 weekly slots (7-day windows) = up to 15 slots, ~3 weeks coverage.

Slots are calendar-aligned (the hourly slot a backup belongs to is determined
by the UTC hour it was taken in, not by its age relative to now). This makes
retention robust to prune-run jitter — two consecutive hourly backups that
straddle a slot boundary cannot both be assigned to slot 0 and have the
older one squeezed out.

A backup survives pruning iff it is the newest backup in any slot's bucket.

Auth:
    Redis password -- META_PASSWORD env var (sourced from /etc/juicefs/redis.env).
    GCS -- rclone config /etc/rclone/juicefs-backup.conf, referencing the
    existing SA key at /etc/juicefs/sa-private-key.json.

Designed to run under a systemd hourly timer; also safe to run by hand
as root (must be root to read the SA key and Redis env file).
"""

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

META_URL = "redis://tpu0:6379/0"
REDIS_ENV = "/etc/juicefs/redis.env"
RCLONE_CONFIG = "/etc/rclone/juicefs-backup.conf"
REMOTE = "gcs:mfrs-tpu-cluster/backups"

# Retention tiers: (slot count, slot width). For each slot, keep the newest
# backup whose calendar bucket index equals (now_bucket - k).
SLOTS = [
    (8, timedelta(hours=1)),
    (4, timedelta(days=1)),
    (3, timedelta(weeks=1)),
]

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

NAME_RE = re.compile(r"^dump-(\d{8}T\d{6}Z)\.json\.gz$")


def load_redis_env():
    """Source META_PASSWORD (and friends) from REDIS_ENV into os.environ."""
    with open(REDIS_ENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            os.environ[k] = v


def rclone(*args, capture=False):
    cmd = ["rclone", "--config", RCLONE_CONFIG, *args]
    if capture:
        return subprocess.run(
            cmd, check=True, capture_output=True, text=True
        ).stdout
    subprocess.run(cmd, check=True)


def dump_and_upload(now):
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    name = f"dump-{ts}.json.gz"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / name
        with open(path, "wb") as out:
            dump = subprocess.Popen(
                ["juicefs", "dump", META_URL],
                stdout=subprocess.PIPE,
            )
            gz = subprocess.Popen(
                ["gzip", "-c"], stdin=dump.stdout, stdout=out,
            )
            dump.stdout.close()
            gz_rc = gz.wait()
            dump_rc = dump.wait()
        if dump_rc != 0:
            sys.exit(f"juicefs dump failed (rc={dump_rc})")
        if gz_rc != 0:
            sys.exit(f"gzip failed (rc={gz_rc})")
        size = path.stat().st_size
        print(f"dumped {name} ({size:,} bytes gzipped)")
        rclone("copy", str(path), REMOTE)
        print(f"uploaded {name} -> {REMOTE}/")
    return name


def list_remote():
    out = rclone("lsf", "--files-only", REMOTE, capture=True)
    backups = []
    for line in out.splitlines():
        name = line.strip()
        m = NAME_RE.match(name)
        if m:
            ts = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ") \
                .replace(tzinfo=timezone.utc)
            backups.append((name, ts))
    backups.sort(key=lambda b: b[1], reverse=True)
    return backups


def _bucket(t, width_seconds):
    """Calendar-aligned bucket index. Two timestamps share a bucket iff they
    fall in the same width-aligned window from the UTC epoch."""
    return int((t - EPOCH).total_seconds()) // width_seconds


def select_keep(backups, now):
    keep = set()
    for n_slots, width in SLOTS:
        w = int(width.total_seconds())
        now_b = _bucket(now, w)
        for k in range(n_slots):
            target = now_b - k
            for name, ts in backups:  # newest first
                if _bucket(ts, w) == target:
                    keep.add(name)
                    break
    return keep


def prune(now):
    backups = list_remote()
    keep = select_keep(backups, now)
    delete = [name for name, _ in backups if name not in keep]
    print(f"retention: keeping {len(keep)} of {len(backups)} backup(s)")
    for name in delete:
        print(f"  deleting {name}")
        rclone("deletefile", f"{REMOTE}/{name}")


def main():
    load_redis_env()
    dump_and_upload(datetime.now(timezone.utc))
    # Re-read 'now' after upload so the just-uploaded backup is strictly older
    # than slot 0's right endpoint.
    prune(datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
