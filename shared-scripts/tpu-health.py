#!/usr/bin/python3
"""TPU cluster health check (admin-only).

Usage:
    tpu-health              # cluster-wide table (all 4 nodes, parallel SSH)
    tpu-health -l           # local node only, sentence-per-check
    tpu-health -n NODE      # one specific node via SSH
    tpu-health --json       # machine-readable JSON (implies --local)

Restricted to admin via filesystem permissions on /home/shared/tpu-health.py
(mode 0750 root:matt). Other users get permission denied at exec time.
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


CLUSTER = ["tpu0", "tpu1", "tpu2", "tpu3"]
METRICS_URL = "http://localhost:9567/metrics"
# Filesystem capacity (--capacity from `juicefs format`) is read from `df`;
# cache cap (cache-size= in storage.mount Options=) is read via `systemctl cat`.
# Both reflect live config without hardcoding.


# # #
# Per-node checks — each returns (status, short, full).
#   status: "OK" | "WARN" | "CRIT" | "SKIP"
#   short:  brief value for table cells (e.g. "11%", "26.4 GiB")
#   full:   sentence for sentence-per-check view


def check_disk():
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    pct = (total - free) / total * 100
    free_gb = free / (1024 ** 3)
    total_gb = total / (1024 ** 3)
    if pct >= 90:
        status = "CRIT"
    elif pct >= 75:
        status = "WARN"
    else:
        status = "OK"
    return status, f"{pct:.0f}%", \
        f"disk {pct:.0f}% used ({free_gb:.0f} GiB free of {total_gb:.0f} GiB)"


def check_heartbeat():
    try:
        with open("/home/shared/heartbeat/status.json") as f:
            data = json.load(f)
        age = int(time.time() - data.get("last_updated", 0))
        if age > 30:
            return "WARN", f"{age}s stale", f"heartbeat data is {age}s stale"
        return "OK", f"{age}s", f"heartbeat updated {age}s ago"
    except FileNotFoundError:
        return "CRIT", "missing", "heartbeat status file missing"
    except Exception as e:
        return "CRIT", "error", f"heartbeat error: {e}"


def check_services():
    services = ["tpu-heartbeat", "tpu-heartbeat-web"]
    down = []
    for svc in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip() != "active":
                down.append(svc)
        except Exception:
            down.append(svc)
    if down:
        return "CRIT", "down", f"services down: {', '.join(down)}"
    return "OK", "running", f"services running ({', '.join(services)})"


def check_healthagent():
    cmd = ["docker", "stats", "healthagent", "--no-stream", "--format", "{{.MemPerc}}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            r = subprocess.run(["sudo", "-n"] + cmd,
                               capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return "SKIP", "n/a", "healthAgent (no docker access)"
        pct = float(r.stdout.strip().rstrip("%"))
        if pct >= 90:
            return "CRIT", f"{pct:.0f}%", \
                f"healthAgent memory {pct:.0f}% (restart needed)"
        if pct >= 70:
            return "WARN", f"{pct:.0f}%", f"healthAgent memory {pct:.0f}%"
        return "OK", f"{pct:.0f}%", f"healthAgent memory {pct:.0f}%"
    except Exception as e:
        return "SKIP", "n/a", f"healthAgent (check failed: {e})"


def check_storage_mount():
    """Is /storage mounted as fuse.juicefs and responsive?"""
    try:
        with open("/proc/mounts") as f:
            mounted = any(
                parts[1] == "/storage" and "fuse.juicefs" in parts[2]
                for parts in (line.split() for line in f if line.strip())
            )
    except Exception as e:
        return "CRIT", "error", f"/storage cannot read /proc/mounts: {e}"
    if not mounted:
        return "CRIT", "unmounted", "/storage not mounted (no fuse.juicefs entry)"
    # liveness probe: stat a known path that requires the mount to be alive
    try:
        subprocess.run(
            ["stat", "/storage/.config"],
            capture_output=True, timeout=2, check=True,
        )
    except subprocess.TimeoutExpired:
        return "CRIT", "frozen", "/storage stat timed out (Redis or GCS unreachable?)"
    except subprocess.CalledProcessError as e:
        return "CRIT", "stat fail", f"/storage stat failed: {e.stderr.decode().strip()}"
    return "OK", "mounted", "/storage mounted and responsive"


def check_storage_capacity():
    """df-derived used / total. Total = the --capacity passed to `juicefs format`."""
    try:
        r = subprocess.run(
            ["df", "-B1", "/storage"],
            capture_output=True, text=True, timeout=2,
        )
    except subprocess.TimeoutExpired:
        return "CRIT", "frozen", "/storage df timed out"
    if r.returncode != 0:
        return "CRIT", "df fail", f"/storage df failed: {r.stderr.strip()}"
    lines = r.stdout.strip().splitlines()
    if len(lines) < 2:
        return "CRIT", "df fail", f"/storage df: unexpected output: {r.stdout!r}"
    fields = lines[1].split()
    try:
        total = int(fields[1])
        used = int(fields[2])
    except (IndexError, ValueError):
        return "CRIT", "df fail", f"/storage df: unparseable: {lines[1]!r}"
    pct = used / total * 100 if total else 0.0
    used_gib = used / (1024 ** 3)
    total_gib = total / (1024 ** 3)
    if pct >= 95:
        status = "CRIT"
    elif pct >= 80:
        status = "WARN"
    else:
        status = "OK"
    return status, f"{pct:.0f}%", \
        f"/storage capacity {pct:.0f}% used ({used_gib:.0f} of {total_gib:.0f} GiB)"


def check_cache_size(metrics, cache_cap):
    """Local block-cache size. Informational — near-full is normal LRU."""
    if metrics is None:
        return "SKIP", "n/a", "cache (metrics unreachable)"
    size = metrics.get("juicefs_blockcache_bytes")
    if size is None:
        return "SKIP", "n/a", "cache (juicefs_blockcache_bytes metric missing)"
    size_gib = size / (1024 ** 3)
    if cache_cap is None:
        return "OK", f"{size_gib:.1f} GiB", \
            f"cache {size_gib:.1f} GiB (cap unknown)"
    cap_gib = cache_cap / (1024 ** 3)
    pct = size / cache_cap * 100
    return "OK", f"{size_gib:.1f} GiB", \
        f"cache {size_gib:.1f}/{cap_gib:.0f} GiB ({pct:.0f}% of cap, near-full is normal)"


def check_rawstaging(metrics):
    """Writeback queue depth (bytes pending upload to GCS)."""
    if metrics is None:
        return "SKIP", "n/a", "rawstaging (metrics unreachable)"
    nbytes = metrics.get("juicefs_staging_block_bytes")
    if nbytes is None:
        return "SKIP", "n/a", "rawstaging (juicefs_staging_block_bytes missing)"
    if nbytes >= 10 * (1024 ** 3):
        status = "CRIT"
    elif nbytes >= 1 * (1024 ** 3):
        status = "WARN"
    else:
        status = "OK"
    return status, _human_bytes(nbytes), \
        f"rawstaging backlog {_human_bytes(nbytes)} (writeback queue depth)"


def check_gcs_errors(metrics):
    """Cumulative juicefs_object_request_errors since mount."""
    if metrics is None:
        return "SKIP", "n/a", "GCS errors (metrics unreachable)"
    n = metrics.get("juicefs_object_request_errors")
    if n is None:
        return "SKIP", "n/a", "GCS errors (juicefs_object_request_errors missing)"
    n = int(n)
    if n > 0:
        return "WARN", str(n), f"GCS object_request_errors total {n} since mount"
    return "OK", "0", "no GCS errors since mount"


BACKUP_TIMER_UNIT = "/etc/systemd/system/juicefs-backup.timer"
BACKUP_NAME_RE = re.compile(r"^dump-(\d{8}T\d{6}Z)\.json\.gz$")


def check_backup_timer():
    """juicefs-backup.timer active and last service run succeeded.

    Only runs on the node where the backup is deployed (tpu0). Other nodes
    see SKIP."""
    if not os.path.exists(BACKUP_TIMER_UNIT):
        return "SKIP", "n/a", "backup timer (not deployed on this node)"
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "juicefs-backup.timer"],
            capture_output=True, text=True, timeout=3,
        )
        active = r.stdout.strip() or "unknown"
        if active != "active":
            return "CRIT", active, f"backup timer is {active!r}"
        r2 = subprocess.run(
            ["systemctl", "show", "juicefs-backup.service",
             "-p", "Result", "-p", "ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=3,
        )
        result = {}
        for line in r2.stdout.splitlines():
            k, _, v = line.partition("=")
            result[k] = v
        last_result = result.get("Result", "")
        if last_result not in ("success", ""):
            return "CRIT", last_result or "?", \
                f"backup last result: {last_result!r}"
        return "OK", "active", "backup timer active, last run ok"
    except Exception as e:
        return "CRIT", "error", f"backup timer check error: {e}"


def check_backup_freshness():
    """Newest dump in gs://.../backups/ is recent enough.

    Only runs on the backup node (tpu0). Reads /etc/rclone/juicefs-backup.conf
    via sudo -n; requires admin sudo (which tpu-health users have)."""
    if not os.path.exists(BACKUP_TIMER_UNIT):
        return "SKIP", "n/a", "backup freshness (not on backup node)"
    try:
        r = subprocess.run(
            ["sudo", "-n", "rclone",
             "--config=/etc/rclone/juicefs-backup.conf",
             "lsf", "--files-only",
             "gcs:mfrs-tpu-cluster/backups/"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "CRIT", "timeout", "backup freshness rclone lsf timed out"
    except Exception as e:
        return "CRIT", "error", f"backup freshness error: {e}"
    if r.returncode != 0:
        err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "rclone failed"
        return "CRIT", "list fail", f"backup list failed: {err}"
    names = [n.strip() for n in r.stdout.splitlines()
             if BACKUP_NAME_RE.match(n.strip())]
    if not names:
        return "CRIT", "none", "no backups in gs://.../backups/"
    names.sort(reverse=True)
    newest = names[0]
    m = BACKUP_NAME_RE.match(newest)
    ts = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ") \
        .replace(tzinfo=dt.timezone.utc)
    age_h = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() / 3600
    if age_h >= 6:
        status = "CRIT"
    elif age_h >= 2:
        status = "WARN"
    else:
        status = "OK"
    return status, f"{age_h:.1f}h", \
        f"newest backup {age_h:.1f}h old ({newest}); {len(names)} kept"


def check_redis_ping():
    """Ping Redis on tpu0. Reads password from /etc/juicefs/redis.env via sudo,
    passes to redis-cli via REDISCLI_AUTH env var (not `-a`, which would expose
    the password via /proc/<pid>/cmdline — mode 0444, world-readable — for
    the duration of the ping). /proc/<pid>/environ is 0400 owner-only."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "cat", "/etc/juicefs/redis.env"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return "SKIP", "n/a", "redis (sudo not available for password)"
        password = None
        for line in r.stdout.splitlines():
            if line.startswith("META_PASSWORD=") or line.startswith("REDIS_PASSWORD="):
                password = line.split("=", 1)[1]
                break
        if not password:
            return "SKIP", "n/a", "redis (password not in /etc/juicefs/redis.env)"
    except Exception as e:
        return "SKIP", "n/a", f"redis (cannot read password: {e})"
    try:
        env = dict(os.environ, REDISCLI_AUTH=password)
        r = subprocess.run(
            ["redis-cli", "-h", "tpu0", "ping"],
            capture_output=True, text=True, timeout=3, env=env,
        )
        if r.returncode != 0 or r.stdout.strip() != "PONG":
            err = r.stderr.strip() or r.stdout.strip()
            return "CRIT", "down", f"redis on tpu0: {err}"
        return "OK", "PONG", "redis on tpu0 reachable (PONG)"
    except subprocess.TimeoutExpired:
        return "CRIT", "timeout", "redis on tpu0 ping timed out"
    except FileNotFoundError:
        return "SKIP", "n/a", "redis (redis-cli not installed)"
    except Exception as e:
        return "CRIT", "error", f"redis ping error: {e}"


# # #
# Helpers


def _human_bytes(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024


def _get_cache_cap_bytes():
    """Parse cache-size= from the live storage.mount unit. Returns bytes or None.

    Uses `systemctl cat` (not `systemctl show -p Options`): the latter only
    surfaces kernel-level mount flags after FUSE mounts strip JuiceFS-specific
    options, while `cat` returns the merged unit-file content (including
    drop-ins) where Options= is intact.
    """
    try:
        r = subprocess.run(
            ["systemctl", "cat", "storage.mount"],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        s = line.strip()
        if not s.startswith("Options="):
            continue
        for opt in s[len("Options="):].split(","):
            opt = opt.strip()
            if opt.startswith("cache-size="):
                try:
                    return int(opt.split("=", 1)[1]) * 1024 * 1024  # MiB → bytes
                except ValueError:
                    return None
    return None


def _scrape_metrics():
    """Return dict[name -> float] from localhost:9567/metrics, or None."""
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=2) as resp:
            text = resp.read().decode()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    out = {}
    pattern = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(\S+)')
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return out


# # #
# Output formatting

STATUS_COLORS = {
    "OK":   "\x1b[32m",  # green
    "WARN": "\x1b[33m",  # yellow
    "CRIT": "\x1b[31m",  # red
    "SKIP": "\x1b[90m",  # grey
}
RESET = "\x1b[0m"


def _colored(status, text, use_color):
    if not use_color:
        return text
    return f"{STATUS_COLORS.get(status, '')}{text}{RESET}"


# Order of checks in output. lambdas close over live config fetched in run_local.
def _build_check_list(metrics, cache_cap):
    return [
        ("disk",        check_disk),
        ("heartbeat",   check_heartbeat),
        ("services",    check_services),
        ("healthAgent", check_healthagent),
        ("storage",     check_storage_mount),
        ("capacity",    check_storage_capacity),
        ("cache",       lambda: check_cache_size(metrics, cache_cap)),
        ("rawstaging",  lambda: check_rawstaging(metrics)),
        ("GCS errors",  lambda: check_gcs_errors(metrics)),
        ("redis",       check_redis_ping),
        ("bk timer",    check_backup_timer),
        ("bk fresh",    check_backup_freshness),
    ]


def run_local():
    """Run all local checks. Returns list of dicts."""
    metrics = _scrape_metrics()
    cache_cap = _get_cache_cap_bytes()
    rows = []
    for name, fn in _build_check_list(metrics, cache_cap):
        status, short, full = fn()
        rows.append({"name": name, "status": status, "short": short, "full": full})
    return rows


def print_local(rows, use_color):
    print("Health:")
    for r in rows:
        sym = _colored(r["status"], f"{r['status']:<4}", use_color)
        print(f"  {sym}  {r['full']}")


def fetch_remote(node, timeout=15):
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", node,
             "/usr/local/bin/tpu-health --local --json"],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            err = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "ssh failed"
            return [{"name": "ssh", "status": "CRIT",
                     "short": "ssh fail", "full": f"ssh {node}: {err}"}]
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return [{"name": "ssh", "status": "CRIT",
                 "short": "timeout", "full": f"ssh {node}: timed out"}]
    except json.JSONDecodeError as e:
        return [{"name": "ssh", "status": "CRIT",
                 "short": "bad json", "full": f"ssh {node}: bad json: {e}"}]
    except Exception as e:
        return [{"name": "ssh", "status": "CRIT",
                 "short": "error", "full": f"ssh {node}: {e}"}]


def print_cluster_table(per_node, use_color):
    nodes = list(per_node.keys())
    # Preserve order of check names from the first node that has them.
    seen = []
    for rows in per_node.values():
        for r in rows:
            if r["name"] not in seen:
                seen.append(r["name"])
    cells = {}
    for node, rows in per_node.items():
        for r in rows:
            cells[(node, r["name"])] = (r["status"], r["short"])
    label_w = max((len(n) for n in seen), default=4)
    col_w = {}
    for n in nodes:
        widths = [
            len(cells.get((n, name), ("SKIP", "—"))[1])
            for name in seen
        ]
        col_w[n] = max(max(widths, default=0), len(n))
    # Header
    header = " " * label_w + "  " + "  ".join(f"{n:<{col_w[n]}}" for n in nodes)
    print(header)
    # Rows
    for name in seen:
        cells_text = []
        for n in nodes:
            status, short = cells.get((n, name), ("SKIP", "—"))
            padded = f"{short:<{col_w[n]}}"
            cells_text.append(_colored(status, padded, use_color))
        print(f"{name:<{label_w}}  " + "  ".join(cells_text))


def main():
    parser = argparse.ArgumentParser(
        description="TPU cluster health check (admin-only).",
    )
    parser.add_argument("-l", "--local", action="store_true",
                        help="show health for the local node only")
    parser.add_argument("-n", "--node", metavar="NODE",
                        help="show health for one specific node via SSH")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON (implies --local)")
    parser.add_argument("--no-color", action="store_true",
                        help="disable ANSI color")
    args = parser.parse_args()

    use_color = sys.stdout.isatty() and not args.no_color

    if args.json:
        args.local = True

    if args.local:
        rows = run_local()
        if args.json:
            print(json.dumps(rows))
        else:
            print_local(rows, use_color)
        return

    targets = [args.node] if args.node else CLUSTER
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as ex:
        results = dict(zip(targets, ex.map(fetch_remote, targets)))
    print_cluster_table(results, use_color)


if __name__ == "__main__":
    main()
