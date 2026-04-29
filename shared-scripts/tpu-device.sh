#!/bin/bash

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <DEVICE_IDS|cpu> <COMMAND> [ARGS...]"
    echo "Example: $0 2 python hello.py"
    echo "Example: $0 0,1 python hello.py"
    echo "Example: $0 0,1,2,3 python hello.py"
    echo "Example: $0 cpu python hello.py"
    exit 1
fi

DEVICE_IDS=$1
shift # Remove device IDs from arguments, leaving the command and its args

# handle CPU mode
if [[ "${DEVICE_IDS,,}" == "cpu" ]]; then
    export JAX_PLATFORMS=cpu
    export PJRT_DEVICE=CPU
    unset TPU_VISIBLE_DEVICES
    unset TPU_CHIPS_PER_PROCESS_BOUNDS
    unset TPU_PROCESS_BOUNDS
    exec "$@"
fi

# validate: comma-separated list of 0-3
if [[ ! "$DEVICE_IDS" =~ ^[0-3](,[0-3])*$ ]]; then
    echo "Error: Device IDs must be comma-separated values 0-3 (e.g. 0 or 0,1 or 0,1,2,3)."
    exit 1
fi

# count devices
NUM_DEVICES=$(echo "$DEVICE_IDS" | tr ',' '\n' | wc -l)

# port convention for concurrent pair usage:
#   pair 0,1: 8474, pair 2,3: 8475
#   single device and all four: no port needed

case $NUM_DEVICES in
    1)
        BOUNDS=1,1,1
        ;;
    2)
        case $DEVICE_IDS in
            0,1) BOUNDS=1,2,1; PORT=8474 ;;
            2,3) BOUNDS=1,2,1; PORT=8475 ;;
            *) echo "Error: Invalid device pair '$DEVICE_IDS'. Valid pairs: 0,1; 2,3."; exit 1 ;;
        esac
        ;;
    4) BOUNDS=2,2,1 ;;
    *) echo "Error: Only 1, 2, or 4 devices supported (got $NUM_DEVICES)."; exit 1 ;;
esac

export TPU_CHIPS_PER_PROCESS_BOUNDS=$BOUNDS
export TPU_PROCESS_BOUNDS=1,1,1
export TPU_VISIBLE_DEVICES=$DEVICE_IDS
export PJRT_DEVICE=TPU

if [ "$NUM_DEVICES" -eq 2 ]; then
    export TPU_MESH_CONTROLLER_ADDRESS=localhost:$PORT
    export TPU_MESH_CONTROLLER_PORT=$PORT
fi

# Per-chip libtpu metrics-service port (8431 + first dev id). Without this,
# every libtpu instance binds the default 8431 and the kernel SO_REUSEPORTs
# them: a single monitoring client gets hash-routed to one listener, opaque
# to which chip it is. Explicit ports let the heartbeat collector enumerate
# chips deterministically. Skip if the caller already set the flag.
FIRST_DEV=${DEVICE_IDS%%,*}
# Match the flag regardless of -/-- prefix or =/space separator.
if [[ "$LIBTPU_INIT_ARGS" != *runtime_metric_service_port* ]]; then
    export LIBTPU_INIT_ARGS="${LIBTPU_INIT_ARGS:+$LIBTPU_INIT_ARGS }--runtime_metric_service_port=$((8431 + FIRST_DEV))"
fi

exec "$@"
