#!/bin/bash
# tpu-warmup — pre-fetch /storage paths into the local JuiceFS cache.
#
# Usage:
#   tpu-warmup [PATH ...]              # warm PATH(s) on this node (default: $PWD)
#   tpu-warmup -n tpuN PATH [PATH ...]  # warm PATH(s) on a different node via SSH
#   tpu-warmup --check PATH [...]       # report cached status, don't warm
#   tpu-warmup --evict PATH [...]       # drop PATH(s) from local cache
#
# Wraps `juicefs warmup`. Paths must resolve under /storage (the JuiceFS
# mount); local-disk paths are rejected since they aren't cacheable.

set -euo pipefail

usage() {
    cat <<EOF
Usage: tpu-warmup [PATH ...]
       tpu-warmup -n tpuN PATH [PATH ...]
       tpu-warmup --check PATH [...]
       tpu-warmup --evict PATH [...]

Pre-fetch /storage paths into the local JuiceFS cache so subsequent reads
hit local disk instead of GCS. Defaults to current directory if no PATH given.

Options:
  -n NODE        Run on NODE (e.g. tpu1) via SSH instead of locally.
  --check        Report whether each path is currently cached, don't warm.
  --evict        Drop the path(s) from the local cache.
  -h, --help     Show this help.

Paths must resolve to somewhere under /storage. Local paths (e.g. /home,
/tmp) are rejected — they aren't on JuiceFS.
EOF
}

NODE=""
MODE=""  # "" (warm) | "--check" | "--evict"

# Argument parsing: accept -n, --check, --evict, -h/--help; rest are paths.
paths=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n)
            [[ $# -ge 2 ]] || { echo "tpu-warmup: -n requires a node name" >&2; exit 2; }
            NODE="$2"
            shift 2
            ;;
        --check)
            MODE="--check"
            shift
            ;;
        --evict)
            MODE="--evict"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do paths+=("$1"); shift; done
            ;;
        -*)
            echo "tpu-warmup: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            paths+=("$1")
            shift
            ;;
    esac
done

if [[ ${#paths[@]} -eq 0 ]]; then
    paths=("$PWD")
fi

# If targeting a remote node, re-invoke ourselves there (so the same path
# validation runs against /storage on the remote node, not locally).
if [[ -n "$NODE" ]]; then
    remote_cmd="tpu-warmup"
    [[ -n "$MODE" ]] && remote_cmd+=" $MODE"
    for p in "${paths[@]}"; do
        remote_cmd+=" $(printf '%q' "$p")"
    done
    exec ssh "$NODE" "$remote_cmd"
fi

# Local invocation: validate each path resolves under /storage.
abs_paths=()
for p in "${paths[@]}"; do
    if [[ ! -e "$p" ]]; then
        echo "tpu-warmup: $p: no such path" >&2
        exit 1
    fi
    abs=$(readlink -f -- "$p")
    case "$abs" in
        /storage|/storage/*) ;;
        *)
            echo "tpu-warmup: $p resolves to $abs (not on /storage)" >&2
            exit 1
            ;;
    esac
    abs_paths+=("$abs")
done

# Hand off to juicefs warmup. juicefs's default --threads (50) is fine for
# our 240-vCPU nodes; not overriding.
if [[ -n "$MODE" ]]; then
    exec juicefs warmup "$MODE" "${abs_paths[@]}"
else
    exec juicefs warmup "${abs_paths[@]}"
fi
