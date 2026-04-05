#!/usr/bin/python3

import csv
import collections
import datetime
import io
import requests

from concurrent.futures import ThreadPoolExecutor

# # # 
# CONFIGURATION

TPU_HOSTS = ["tpu0", "tpu1", "tpu2", "tpu3"]

PORT = 8080

ENDPOINT = "history.csv"


# # # 
# RETRIEVAL

def fetch_log(host):
    url = f"http://{host}:{PORT}/{ENDPOINT}"
    try:
        # 2-second timeout to prevent hanging on a dead node
        resp = requests.get(url, timeout=2)
        resp.raise_for_status()
        f = io.StringIO(resp.text)
        return list(csv.DictReader(f))
    except Exception as e:
        print(e)
        return []
    
print("Fetching logs from cluster...")
with ThreadPoolExecutor(max_workers=len(TPU_HOSTS)) as executor:
    logs = list(executor.map(fetch_log, TPU_HOSTS))


# # # 
# PARSING

usage = collections.defaultdict(lambda: collections.defaultdict(float))

for rows in logs:
    for a, b in zip(rows, rows[1:]):
        start = float(a['timestamp'])
        delta = float(b['timestamp']) - start
        date = datetime.date.fromtimestamp(start).strftime("daily %Y-%m-%d")

        for j in range(4):
            device = f'dev{j}'
            user = a[device]
            usage[date][user] += delta
            usage["forever"][user] += delta

# # # 
# PRINT SUMMARY

for date, daily_usage in sorted(usage.items()):
    # totals
    idle = datetime.timedelta(seconds=int(daily_usage["-"]))
    del daily_usage["-"]
    used = datetime.timedelta(seconds=int(sum(daily_usage.values())))
    percent_used = used / (used + idle)

    print(f"{date}:")
    leaderboard = sorted((s, u) for u, s in daily_usage.items())
    for place, (seconds, user) in enumerate(leaderboard[::-1], 1):
        duration = datetime.timedelta(seconds=int(seconds))
        print(f"{place:2d}.", user.ljust(6), str(duration).rjust(22))
    print("time used:", str(used).rjust(22), f"({percent_used:7.2%})")
    print("time idle:", str(idle).rjust(22), f"({1-percent_used:7.2%})")
    print()

