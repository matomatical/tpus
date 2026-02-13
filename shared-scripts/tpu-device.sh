#!/bin/bash

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <DEVICE_ID> <COMMAND> [ARGS...]"
    echo "Example: $0 2 python hello.py"
    exit 1
fi

DEVICE_ID=$1
shift # Remove device ID from arguments, leaving the command and its args

if [[ ! "$DEVICE_ID" =~ ^[0-3]$ ]]; then
    echo "Error: Device ID must be between 0 and 3."
    exit 1
fi

export TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1
export TPU_PROCESS_BOUNDS=1,1,1
export TPU_VISIBLE_DEVICES=$DEVICE_ID

exec "$@"
