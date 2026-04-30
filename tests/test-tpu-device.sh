#!/bin/bash
SCRIPT=shared-scripts/tpu-device.sh
PY=venv/bin/python
CMD='import jax; print(f"devices: {jax.device_count()}, list: {jax.devices()}")'

echo "=== Testing: cpu ==="
bash "$SCRIPT" "cpu" "$PY" -c "$CMD" 2>&1
echo ""

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
echo ""

# Regression: tpu-defaults.sh pre-sets --runtime_metric_service_port=8431.
# tpu-device must override per the chosen first device. Bug 2026-04-30: the
# old "skip if already set" guard tripped on this default, leaving every
# tpu-device launch on port 8431 → SO_REUSEPORT collisions in tpu-metrics.
echo "=== Port-pinning regression (LIBTPU_INIT_ARGS) ==="
fail=0
for cfg in 0 1 2 3 0,1 2,3 0,1,2,3; do
    expected_port=$((8431 + ${cfg%%,*}))
    actual=$(LIBTPU_INIT_ARGS="--runtime_metric_service_port=8431" \
        bash "$SCRIPT" "$cfg" bash -c 'echo "$LIBTPU_INIT_ARGS"' 2>&1)
    last_port=$(echo "$actual" | grep -oE 'runtime_metric_service_port=[0-9]+' | tail -1 | cut -d= -f2)
    if [ "$last_port" = "$expected_port" ]; then
        echo "  $cfg: ok (last port=$last_port)"
    else
        echo "  $cfg: FAIL — expected last port $expected_port, got '$last_port' from: $actual"
        fail=1
    fi
done
[ "$fail" = 0 ] && echo "  all passed" || { echo "  FAILURES"; exit 1; }
