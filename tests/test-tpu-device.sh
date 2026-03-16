#!/bin/bash
SCRIPT=shared-scripts/tpu-device.sh
PY=venv/bin/python
CMD='import jax; print(f"devices: {jax.device_count()}, list: {jax.devices()}")'

configs=("0" "1" "2" "3" "0,1" "2,3" "0,1,2,3")

for cfg in "${configs[@]}"; do
    echo "=== Testing: $cfg ==="
    bash "$SCRIPT" "$cfg" "$PY" -c "$CMD" 2>&1
    echo ""
done

echo "=== Testing invalid pair (should fail): 0,2 ==="
bash "$SCRIPT" "0,2" "$PY" -c "$CMD" 2>&1
echo ""

echo "=== Testing invalid pair (should fail): 1,3 ==="
bash "$SCRIPT" "1,3" "$PY" -c "$CMD" 2>&1
echo ""

echo "=== Testing invalid pair (should fail): 0,3 ==="
bash "$SCRIPT" "0,3" "$PY" -c "$CMD" 2>&1
echo ""

echo "=== Testing invalid pair (should fail): 1,2 ==="
bash "$SCRIPT" "1,2" "$PY" -c "$CMD" 2>&1
