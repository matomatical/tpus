#!/usr/bin/python3

import argparse
import dataclasses
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


@dataclasses.dataclass
class BusyRow:
    label: str
    user: str
    pid: str
    time: str
    mem: str  # 3-char right-aligned string from util_cells
    dut: str
    cmd: str


@dataclasses.dataclass
class IdleRow:
    label: str


@dataclasses.dataclass
class ErrorRow:
    label: str
    msg: str


@dataclasses.dataclass
class Separator:
    pass


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


def collect(rows, this_node, now):
    """Walk fetched rows, return (records, time_diffs)."""
    records = []
    time_diffs = []
    for row in rows:
        host = row["host"]
        status = row["status"]
        metrics = row["metrics"]

        if "error" in status:
            # No node JSON available; derive node_id from the literal hostname.
            node_id = host.removeprefix("tpu")
            marker = "*" if host == this_node else " "
            records.append(ErrorRow(label=f"{marker}{node_id}", msg=status["error"]))
            records.append(Separator())
            continue

        node_name = status["node"]
        _, node_id = node_name.rsplit("-w-", 1)
        marker = "*" if node_name == this_node else " "

        time_diff = int(now - status.get("last_updated", 0))
        time_diffs.append(f"tpu{node_id}:{time_diff}s")

        for device in status["devices"]:
            label = f"{marker}{node_id}.{device['id']}"
            state = device.get("state", "-")
            if state == "BUSY":
                user = device.get("user", "-")
                pid = str(device.get("pid", "-"))
                dur = device.get("time", "-")
                mem, dut = util_cells(device, metrics, now)
                cmd = device.get("command") or "-"
                records.append(BusyRow(
                    label=label, user=user, pid=pid, time=dur,
                    mem=mem, dut=dut, cmd=cmd,
                ))
            else:
                records.append(IdleRow(label=label))

        records.append(Separator())

    return records, time_diffs


ANSI_BOLD = "\x1b[1m"
ANSI_RESET = "\x1b[0m"
ANSI_FG_RESET = "\x1b[39m"
ANSI_FG_GREEN = "\x1b[32m"
ANSI_FG_YELLOW = "\x1b[33m"
ANSI_FG_RED = "\x1b[31m"

HEADER_RULE_CHAR = "━"  # U+2501 heavy horizontal — under header
HOST_RULE_CHAR = "─"    # U+2500 light horizontal — between hosts


def render(rows, full=False, color=True, out=sys.stdout):
    this_node = socket.gethostname()
    now = time.time()
    records, time_diffs = collect(rows, this_node, now)

    busy = [r for r in records if isinstance(r, BusyRow)]
    label_widths = [
        len(r.label) for r in records
        if isinstance(r, (BusyRow, IdleRow, ErrorRow))
    ]

    w_chip = max(len("chip"), max(label_widths, default=0))
    w_user = max(len("user"), max((len(r.user) for r in busy), default=0))
    w_pid  = max(len("pid"),  max((len(r.pid)  for r in busy), default=0))
    w_time = max(len("time"), max((len(r.time) for r in busy), default=0))
    w_mem  = max(len("mem"),  max((len(r.mem.strip()) for r in busy), default=0))
    w_dut  = max(len("dut"),  max((len(r.dut.strip()) for r in busy), default=0))

    # 5 inter-column separators + 1 before command.
    w_fixed = w_chip + w_user + w_pid + w_time + w_mem + w_dut + 6

    cols = shutil.get_terminal_size((80, 24)).columns
    if full:
        cmd_width = 10**6
    else:
        cmd_width = max(20, cols - w_fixed)

    header = (
        f"{'chip':<{w_chip}} "
        f"{'user':<{w_user}} "
        f"{'pid':<{w_pid}} "
        f"{'time':<{w_time}} "
        f"{'mem':>{w_mem}} "
        f"{'dut':>{w_dut}} "
        f"command"
    )

    # Rule width hugs the longest visible line, capped at terminal width.
    content_lens = []
    for rec in records:
        if isinstance(rec, BusyRow):
            cmd = truncate_command(rec.cmd, cmd_width, full)
            content_lens.append(w_fixed + len(cmd))
        elif isinstance(rec, IdleRow):
            content_lens.append(w_chip + 1 + w_user)  # "*0.0 idle"
        elif isinstance(rec, ErrorRow):
            content_lens.append(w_chip + len(" ERROR  ") + len(rec.msg))
    rule_width = min(cols, max([len(header)] + content_lens))

    if color:
        print(f"{ANSI_BOLD}{header}{ANSI_RESET}", file=out)
        print(HEADER_RULE_CHAR * rule_width, file=out)
    else:
        print(header, file=out)

    for rec in records:
        if isinstance(rec, Separator):
            if color:
                print(HOST_RULE_CHAR * rule_width, file=out)
            continue

        chip_padded = f"{rec.label:<{w_chip}}"
        if isinstance(rec, BusyRow):
            chip_color = ANSI_FG_YELLOW
            cmd = truncate_command(rec.cmd, cmd_width, full)
            rest = (
                f" {rec.user:<{w_user}}"
                f" {rec.pid:<{w_pid}}"
                f" {rec.time:<{w_time}}"
                f" {rec.mem:>{w_mem}}"
                f" {rec.dut:>{w_dut}}"
                f" {cmd}"
            )
        elif isinstance(rec, IdleRow):
            chip_color = ANSI_FG_GREEN
            rest = f" {'idle':<{w_user}}"
        elif isinstance(rec, ErrorRow):
            chip_color = ANSI_FG_RED
            rest = f" ERROR  {rec.msg}"
        else:
            continue

        if color:
            print(f"{chip_color}{chip_padded}{ANSI_FG_RESET}{rest}", file=out)
        else:
            print(chip_padded + rest, file=out)

    legend = "* = current node | mem/dut = HBM/duty %"
    if time_diffs:
        legend += f" | Updates ago: {', '.join(time_diffs)}"
    print(legend, file=out)


# # #
# WATCH


def watch(interval, full, color):
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
            render(rows, full=full, color=color)
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
        help="don't truncate the command column (lines may overflow the ruler)",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="disable ANSI styling and host separators (also via NO_COLOR env)",
    )
    args = parser.parse_args()

    color = not args.no_color and not os.environ.get("NO_COLOR")

    if args.watch:
        watch(args.interval, args.full, color=color)
    else:
        print("Fetching status from cluster...", file=sys.stderr)
        rows = fetch_all()
        print(file=sys.stderr)
        render(rows, full=args.full, color=color)


if __name__ == "__main__":
    main()
