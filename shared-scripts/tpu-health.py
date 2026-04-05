#!/usr/bin/python3
"""TPU cluster health check for the current VM."""

import json
import os
import subprocess
import time


# # #
# CHECKS


def check_disk():
    """Check root filesystem usage."""
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    pct = used / total * 100
    total_gb = total / (1024 ** 3)
    free_gb = free / (1024 ** 3)
    if pct >= 90:
        status = "CRIT"
    elif pct >= 75:
        status = "WARN"
    else:
        status = "OK"
    return status, f"disk {pct:.0f}% used ({free_gb:.0f} GiB free of {total_gb:.0f} GiB)"


def check_heartbeat():
    """Check heartbeat service and data freshness."""
    status_file = "/home/shared/heartbeat/status.json"
    try:
        with open(status_file) as f:
            data = json.load(f)
        age = time.time() - data.get("last_updated", 0)
        if age > 30:
            return "WARN", f"heartbeat data is {int(age)}s stale"
        return "OK", f"heartbeat updated {int(age)}s ago"
    except FileNotFoundError:
        return "CRIT", "heartbeat status file missing"
    except Exception as e:
        return "CRIT", f"heartbeat error: {e}"


def check_services():
    """Check systemd services are active."""
    services = ["tpu-heartbeat", "tpu-heartbeat-web"]
    problems = []
    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"{svc}.service"],
                capture_output=True, text=True,
            )
            if result.stdout.strip() != "active":
                problems.append(svc)
        except Exception:
            problems.append(svc)
    if problems:
        return "CRIT", f"services down: {', '.join(problems)}"
    return "OK", f"services running ({', '.join(services)})"


def check_healthagent():
    """Check healthAgent container memory usage."""
    try:
        result = subprocess.run(
            ["docker", "stats", "healthagent", "--no-stream",
             "--format", "{{.MemPerc}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            # try with sudo (for non-docker-group users in motd context)
            result = subprocess.run(
                ["sudo", "-n", "docker", "stats", "healthagent", "--no-stream",
                 "--format", "{{.MemPerc}}"],
                capture_output=True, text=True, timeout=5,
            )
        if result.returncode != 0:
            return "SKIP", "healthAgent (no docker access)"
        pct = float(result.stdout.strip().rstrip("%"))
        if pct >= 90:
            return "CRIT", f"healthAgent memory {pct:.0f}% (restart needed)"
        elif pct >= 70:
            return "WARN", f"healthAgent memory {pct:.0f}%"
        return "OK", f"healthAgent memory {pct:.0f}%"
    except Exception:
        return "SKIP", "healthAgent (check failed)"


# # #
# MAIN


STATUS_SYMBOLS = {
    "OK":   " \x1b[32mOK\x1b[0m  ",
    "WARN": "\x1b[33mWARN\x1b[0m ",
    "CRIT": "\x1b[31mCRIT\x1b[0m ",
    "SKIP": "\x1b[90mSKIP\x1b[0m ",
}


def main():
    checks = [
        check_disk,
        check_heartbeat,
        check_services,
        check_healthagent,
    ]

    print("Health:")
    for check in checks:
        status, message = check()
        symbol = STATUS_SYMBOLS.get(status, "???? ")
        print(f"  {symbol} {message}")
    print()


if __name__ == "__main__":
    main()
