#!/usr/bin/python3

import argparse
import json
import os
import shutil
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# # #
# CONFIGURATION

TPU_HOSTS = ["tpu0", "tpu1", "tpu2", "tpu3"]

PORT = 8080

STATUS_ENDPOINT = "status.json"
METRICS_ENDPOINT = "metrics.json"

# Fixed column widths (chars). Single-space separators between columns.
W_TPU = 7      # *tpu0.0 form
W_USER = 7
W_PID = 8
W_TIME = 10
W_MEM = 3
W_DUT = 3
# Total fixed width incl. 5 separators between 6 columns and 1 before COMMAND:
# 7+1+7+1+8+1+10+1+3+1+3+1 = 44.
W_FIXED = W_TPU + W_USER + W_PID + W_TIME + W_MEM + W_DUT + 6
W_COMMAND_DEFAULT = 36  # used if terminal size lookup fails (e.g. not a tty)

# Sidecar staleness threshold — beyond this we mark UTIL cells as `?`.
STALE_AFTER = 15  # seconds

# Schema version this client understands.
EXPECTED_SCHEMA = 1


# # #
# FETCH


def fetch(host, endpoint):
    url = f"http://{host}:{PORT}/{endpoint}"
    try:
        # 2-second timeout to prevent hanging on a dead node
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        return {"host": host, "error": reason}
    except socket.timeout:
        return {"host": host, "error": "timeout"}
    except Exception as e:
        return {"host": host, "error": str(e)}


def fetch_all():
    """Return list of {host, status, metrics} dicts, sorted by node."""
    with ThreadPoolExecutor(max_workers=2 * len(TPU_HOSTS)) as ex:
        futures = {
            host: (
                ex.submit(fetch, host, STATUS_ENDPOINT),
                ex.submit(fetch, host, METRICS_ENDPOINT),
            )
            for host in TPU_HOSTS
        }
        rows = [
            {"host": host, "status": s.result(), "metrics": m.result()}
            for host, (s, m) in futures.items()
        ]
    rows.sort(key=lambda r: r["status"].get("node") or r["host"])
    return rows


# # #
# RENDER


def util_cells(device, metrics_payload, now):
    """Return (mem_str, dut_str) — both 3 chars wide, right-aligned."""
    state = device.get("state", "-")
    if state != "BUSY":
        # Idle/EXIT: blank metric cells.
        return "   ", "   "

    if "error" in metrics_payload:
        return "  ?", "  ?"  # whole-host metrics fetch failed

    if metrics_payload.get("schema_version") != EXPECTED_SCHEMA:
        return "  ?", "  ?"

    if now - metrics_payload.get("last_updated", 0) > STALE_AFTER:
        return "  ?", "  ?"

    by_id = {d["id"]: d for d in metrics_payload.get("devices", [])}
    m = by_id.get(device["id"])
    if m is None:
        return "  ?", "  ?"

    if not m.get("available", False):
        reason = m.get("reason", "unknown")
        if reason == "warming":
            return "   ", "   "  # busy but no data yet — leave blank
        if reason == "error":
            return "err", "err"
        # idle is unexpected here (we already checked state==BUSY) but be safe.
        return "  ?", "  ?"

    hbm_used = m.get("hbm_used", 0)
    hbm_total = m.get("hbm_total", 0)
    mem_pct = round(100 * hbm_used / hbm_total) if hbm_total else 0
    dut_pct = round(m.get("duty_cycle_pct", 0))
    return f"{mem_pct:3d}", f"{dut_pct:3d}"


def truncate_command(cmd, width, full):
    if full or cmd is None:
        return cmd or "-"
    if len(cmd) <= width:
        return cmd
    return cmd[: max(0, width - 1)] + "…"  # …


def render(rows, full=False, out=sys.stdout):
    this_node = socket.gethostname()
    now = time.time()

    # Determine COMMAND column width based on terminal size.
    if full:
        cmd_width = 10**6  # effectively unlimited
        rule_width = 80    # ruler doesn't need to grow
    else:
        cols = shutil.get_terminal_size((80, 24)).columns
        cmd_width = max(20, cols - W_FIXED)
        rule_width = W_FIXED + cmd_width

    header = (
        f"{'TPU':<{W_TPU}} "
        f"{'USER':<{W_USER}} "
        f"{'PID':<{W_PID}} "
        f"{'TIME':<{W_TIME}} "
        f"{'MEM':>{W_MEM}} "
        f"{'DUT':>{W_DUT}} "
        f"COMMAND"
    )
    print(header, file=out)
    print("-" * rule_width, file=out)

    time_diffs = []
    for row in rows:
        host = row["host"]
        status = row["status"]
        metrics = row["metrics"]

        if "error" in status:
            marker = "*" if host == this_node else " "
            print(f"{marker}{host:<{W_TPU - 1}} ERROR  {status['error']}", file=out)
            print("-" * rule_width, file=out)
            continue

        node_name = status["node"]
        _, node_id = node_name.rsplit("-w-", 1)
        node_nick = f"tpu{node_id}"
        marker = "*" if node_name == this_node else " "

        time_diff = int(now - status.get("last_updated", 0))
        time_diffs.append(f"{node_nick}:{time_diff}s")

        for device in status["devices"]:
            tpu_label = f"{marker}{node_nick}.{device['id']}"
            state = device.get("state", "-")

            if state == "BUSY":
                user = device.get("user", "-")
                pid = str(device.get("pid", "-"))
                dur = device.get("time", "-")
                cmd = truncate_command(device.get("command", "-"), cmd_width, full)
                mem, dut = util_cells(device, metrics, now)
                print(
                    f"{tpu_label:<{W_TPU}} "
                    f"{user:<{W_USER}} "
                    f"{pid:<{W_PID}} "
                    f"{dur:<{W_TIME}} "
                    f"{mem:>{W_MEM}} "
                    f"{dut:>{W_DUT}} "
                    f"{cmd}",
                    file=out,
                )
            else:
                # Idle / EXIT: render only the TPU label, rest blank.
                print(f"{tpu_label:<{W_TPU}}", file=out)

        print("-" * rule_width, file=out)

    # Footer
    legend = "* = current node | MEM/DUT = HBM/duty %"
    if time_diffs:
        legend += f" | Updates ago: {', '.join(time_diffs)}"
    print(legend, file=out)


# # #
# WATCH


def watch(interval, full):
    # Restore cursor on exit.
    def cleanup(signum=None, frame=None):
        sys.stdout.write("\x1b[?25h")  # show cursor
        sys.stdout.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    sys.stdout.write("\x1b[?25l")  # hide cursor
    try:
        while True:
            rows = fetch_all()
            sys.stdout.write("\x1b[H\x1b[2J")  # cursor home + clear
            render(rows, full=full)
            sys.stdout.flush()
            time.sleep(interval)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


# # #
# MAIN


def main():
    parser = argparse.ArgumentParser(
        description="Show TPU device occupancy and live utilisation across the cluster."
    )
    parser.add_argument(
        "-w", "--watch", action="store_true",
        help="refresh continuously (Ctrl-C to exit)",
    )
    parser.add_argument(
        "-n", "--interval", type=float, default=5.0,
        help="watch interval in seconds (default 5, matching sidecar cadence)",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="don't truncate the COMMAND column (lines may overflow the ruler)",
    )
    args = parser.parse_args()

    if args.watch:
        watch(args.interval, args.full)
    else:
        print("Fetching status from cluster...", file=sys.stderr)
        rows = fetch_all()
        print(file=sys.stderr)
        render(rows, full=args.full)


if __name__ == "__main__":
    main()
