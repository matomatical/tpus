"""
TPU Heartbeat Script. Log TPU status to disk every few seconds.
"""

import os
import glob
import time
import json
import socket
import subprocess


# # #
# Configuration


INTERVAL = 5 # Seconds between checks
MAX_LOG_WAIT = 3600 # Force log event even if nothing changed

SHARED_DIR = "/home/shared/heartbeat"

DEVICES = [
    '/sys/class/accel/accel0',
    '/sys/class/accel/accel1',
    '/sys/class/accel/accel2',
    '/sys/class/accel/accel3',
]


# # # 
# Entry point


def main():
    # filenames
    node_name = socket.gethostname()
    status_file = os.path.join(SHARED_DIR, f"status.json")
    temp_file = status_file + ".tmp"
    log_file = os.path.join(SHARED_DIR, f"history.csv")
    
    # log stats
    last_log_time = None
    last_log_users = None

    print(f"Initialising files...")
    os.makedirs(SHARED_DIR, exist_ok=True)

    if not os.path.exists(log_file):
         headers = ["timestamp"] + [f"dev{d}" for d in range(4)]
         with open(log_file, 'w') as f:
             f.write(",".join(headers) + "\n")

    print(f"Starting heartbeat...")

    while True:
        try:
            # Poll statuses
            devices = get_device_status()
            current_time = time.time()
            data = {
                "node": node_name,
                "last_updated": current_time,
                "devices": devices,
            }

            # Write statuses (atomic)
            with open(temp_file, 'w') as f:
                json.dump(data, f)
            os.rename(temp_file, status_file)

            # Log usage (if relevant)
            log_users = [dev['user'] for dev in devices]
            if (
                log_users != last_log_users
                or current_time - last_log_time > MAX_LOG_WAIT
            ):
                row = [str(current_time)] + log_users
                with open(log_file, 'a') as f:
                    f.write(",".join(row) + "\n")
                last_log_users = log_users
                last_log_time = current_time

        except Exception as e:
            print(f"Error in heartbeat loop: {e}")
        
        time.sleep(INTERVAL)


# # # 
# Helpers


def get_device_status():
    """
    Checks /sys/class/accel for TPU usage.
    """
    devices = []
    for dev_id, path in enumerate(DEVICES):
        status = {
            "id": dev_id,
            "state": "FREE",
            "pid": "-",
            "user": "-",
            "time": "-",
            "command": "-"
        }

        # Check ownership
        with open(os.path.join(path, 'is_device_owned')) as f:
            is_owned = f.read().strip() == '1'

        if is_owned:
            status["state"] = "BUSY"
            
            with open(os.path.join(path, 'device_owner')) as f:
                pid = f.read().strip()
            status["pid"] = pid

            try:
                # user, elapsed time, command
                out = subprocess.check_output(
                    ['ps', '-p', pid, '-o', 'user=,etime=,args='], 
                    text=True,
                ).strip()
                parts = out.split(None, 2)
                if len(parts) >= 3:
                    status["user"] = parts[0]
                    status["time"] = parts[1]
                    status["command"] = parts[2]
            except subprocess.CalledProcessError:
                status["pid"] = f"{pid}"
                status["state"] = "EXIT"
        devices.append(status)
    
    return devices


if __name__ == "__main__":
    main()

