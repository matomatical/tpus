#!/usr/bin/python3.14

import json
import time
import socket
import requests
from concurrent.futures import ThreadPoolExecutor

# # # 
# CONFIGURATION

TPU_HOSTS = [
    "10.130.0.12",
    "10.130.0.11",
    "10.130.0.10",
    "10.130.0.13",
]

PORT = 8080

ENDPOINT = "status.json"


# # # 
# HELPER


def fetch_status(host):
    url = f"http://{host}:{PORT}/{ENDPOINT}"
    try:
        # 2-second timeout to prevent hanging on a dead node
        resp = requests.get(url, timeout=2)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {
            "host": host, 
            "error": str(e),
            "last_updated": 0,
            "devices": [],
        }

# # # 
# MAIN


def main():
    this_node_name = socket.gethostname()
    
    # Fetch statuses in parallel
    print("Fetching status from cluster...")
    with ThreadPoolExecutor(max_workers=len(TPU_HOSTS)) as executor:
        results = list(executor.map(fetch_status, TPU_HOSTS))

    # Sort results by node name
    # Filter out nodes that returned errors solely for sorting purposes (keep
    # them for display)
    results.sort(key=lambda x: x.get('node'))

    # Header
    print(f"\n{'NODE/DEV':<11} {'STAT':<6} {'USER':<7} {'PID':<8} {'TIME':<10} {'COMMAND'}")
    print("-" * 80)

    # Data
    time_diffs = []
    for data in results:
        # handle connection errors gracefully
        if "error" in data:
            print(f"{data['host']:<11} ERROR ({data['error']})")
            print("-" * 80)
            continue

        # parse node name (e.g., t1v-n-ab15a7e0-w-0 -> tpu0)
        node_name = data["node"]
        _, node_id = node_name.rsplit('-w-', 1)
        node_nick = f'tpu{node_id}'

        # marker for current node
        marker = "*" if node_name == this_node_name else " "

        # time calculation
        last_updated = data.get('last_updated', 0)
        time_diff = int(time.time() - last_updated)
        time_diffs.append(f"{node_nick}:{time_diff}s")

        # device statuses
        devices = data["devices"]
        for device in devices:
            dev_id = device["id"]
            name_str = f"{marker}{node_nick}/dev{dev_id}"

            state = device.get('state', '-')
            pid = str(device.get('pid', '-'))
            user = device.get('user', '-')
            dur = device.get('time', '-')
            
            cmd = device.get('command', '-')
            if len(cmd) > 33:
                cmd = cmd[:30] + "..."
            

            print(f"{name_str:<11} {state:<6} {user:<7} {pid:<8} {dur:<10} {cmd}")
        
        print("-" * 80)

    # Footer
    print(f"* = current node | Updates ago: {', '.join(time_diffs)}")


if __name__ == "__main__":
    main()
