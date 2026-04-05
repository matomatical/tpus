#!/usr/bin/python3
"""Calendar heatmap of daily TPU cluster utilization (standalone, no external plotting deps)."""

import csv
import collections
import calendar
import datetime
import io
import requests

from concurrent.futures import ThreadPoolExecutor


# # #
# CONFIGURATION

TPU_HOSTS = ["tpu0", "tpu1", "tpu2", "tpu3"]

PORT = 8080
ENDPOINT = "history.csv"

USED_DAY = "▟█"
NO_DATA_DAY = "▟█"
NOT_A_DAY = "  "


# # #
# INLINE PLOTTING HELPERS

def colored(text, fg=None, bg=None):
    """Wrap text in ANSI true-color escapes."""
    pre = ""
    if fg: pre += f"\x1b[38;2;{fg[0]};{fg[1]};{fg[2]}m"
    if bg: pre += f"\x1b[48;2;{bg[0]};{bg[1]};{bg[2]}m"
    return pre + text + "\x1b[0m" if pre else text


def cyber(frac):
    """Interpolate (255,0,255) -> (0,255,255) through frac 0.0-1.0."""
    t = max(0.0, min(1.0, frac))
    return (int(255 * (1 - t)), int(255 * t), 255)


# # #
# RETRIEVAL

def fetch_log(host):
    url = f"http://{host}:{PORT}/{ENDPOINT}"
    try:
        resp = requests.get(url, timeout=2)
        resp.raise_for_status()
        f = io.StringIO(resp.text)
        return list(csv.DictReader(f))
    except Exception as e:
        print(e)
        return []


# # #
# COMPUTE DAILY UTILIZATION

def compute_daily_utilization(logs):
    """Returns {date: fraction} where fraction is 0.0-1.0 of chip-time used."""
    daily_used = collections.defaultdict(float)
    daily_total = collections.defaultdict(float)

    for rows in logs:
        for a, b in zip(rows, rows[1:]):
            start = float(a['timestamp'])
            delta = float(b['timestamp']) - start
            date = datetime.date.fromtimestamp(start)

            for j in range(4):
                device = f'dev{j}'
                user = a[device]
                daily_total[date] += delta
                if user != "-":
                    daily_used[date] += delta

    utilization = {}
    hours = {}
    for date in daily_total:
        if daily_total[date] > 0:
            utilization[date] = daily_used[date] / daily_total[date]
            hours[date] = daily_used[date] / 3600

    return utilization, hours


# # #
# RENDER

def render_month(year, month, utilization):
    """Render a single month as a list of strings (one per line)."""
    lines = []
    lines.append(f"{calendar.month_name[month]:<9s} {year:4d}")
    lines.append("M T W t F S s ")

    for week in calendar.monthcalendar(year, month):
        cells = []
        for day in week:
            if day == 0:
                cells.append(NOT_A_DAY)
            else:
                date = datetime.date(year, month, day)
                if date in utilization:
                    frac = utilization[date]
                    cells.append(colored(USED_DAY, fg=cyber(frac), bg=(0, 0, 0)))
                else:
                    cells.append(colored(NO_DATA_DAY, fg=(80, 80, 80), bg=(0, 0, 0)))
        lines.append("".join(cells))

    return lines


def strip_ansi(s):
    """Remove ANSI escape sequences to get visible length."""
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', s)


def grid_layout(month_blocks, cols=4, gap=2):
    """Lay out month blocks in a grid, returning a single string."""
    rows_of_blocks = []
    for i in range(0, len(month_blocks), cols):
        rows_of_blocks.append(month_blocks[i:i + cols])

    # Width of each block (visual width, ignoring ANSI escapes)
    # Each month is 14 visible chars wide (7 days * 2 chars each)
    block_width = 14
    spacer = " " * gap

    output_lines = []
    for row in rows_of_blocks:
        # Pad all blocks to same height
        max_height = max(len(b) for b in row)
        for b in row:
            while len(b) < max_height:
                b.append("")

        # Zip lines across blocks
        for line_idx in range(max_height):
            parts = []
            for block in row:
                line = block[line_idx] if line_idx < len(block) else ""
                # Pad to block_width using visible length
                visible_len = len(strip_ansi(line))
                parts.append(line + " " * max(0, block_width - visible_len))
            output_lines.append(spacer.join(parts))

        # Blank line between rows of months
        output_lines.append("")

    return "\n".join(output_lines)


def main():
    """Calendar heatmap of daily TPU cluster utilization."""
    print("Fetching logs from cluster...")
    with ThreadPoolExecutor(max_workers=len(TPU_HOSTS)) as executor:
        logs = list(executor.map(fetch_log, TPU_HOSTS))

    utilization, hours = compute_daily_utilization(logs)

    if not utilization:
        print("No data found.")
        return

    min_date = min(utilization)
    max_date = max(utilization)
    max_hours = max(hours.values())

    # Build month blocks
    month_blocks = []
    year, month = min_date.year, min_date.month
    end_year, end_month = max_date.year, max_date.month

    while (year, month) <= (end_year, end_month):
        month_blocks.append(render_month(year, month, utilization))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    output = grid_layout(month_blocks, cols=4)
    print(output)
    print(f"max {max_hours:.0f} / 340 chip-hours in a day")


if __name__ == "__main__":
    main()
