#!/bin/bash
# Fetch heartbeat history logs from all cluster VMs.
# Run from your laptop (requires SSH access to tpu0-tpu3).
#
# Usage: ./fetch-logs.sh [output-dir]
#   output-dir defaults to the current directory.

set -euo pipefail

OUTDIR="${1:-.}"
REMOTE_PATH="/home/shared/heartbeat/history.csv"

for t in 0 1 2 3; do
    echo "Fetching from tpu$t..."
    scp "tpu$t:$REMOTE_PATH" "$OUTDIR/tpu$t-history.csv"
done

echo "Done. Logs saved to $OUTDIR/tpu{0,1,2,3}-history.csv"
