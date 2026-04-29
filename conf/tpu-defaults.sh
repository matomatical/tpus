# Default TPU environment variables
# Bound TPU access to a single device on the current VM so that JAX and
# PyTorch/XLA don't try to coordinate across all VMs (which causes hangs).
# Users can override these with the tpu-device wrapper to select specific
# devices or use multiple devices.
export TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1
export TPU_PROCESS_BOUNDS=1,1,1
export TPU_VISIBLE_DEVICES=0
export PJRT_DEVICE=TPU
# Pin the libtpu metrics service to a per-chip port (default device 0 → 8431);
# tpu-device sets the matching port for other devices. See tpu-device.sh.
if [[ "$LIBTPU_INIT_ARGS" != *runtime_metric_service_port* ]]; then
    export LIBTPU_INIT_ARGS="${LIBTPU_INIT_ARGS:+$LIBTPU_INIT_ARGS }--runtime_metric_service_port=8431"
fi
