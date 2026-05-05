#!/bin/bash
# tpu-handbook — view the cluster user handbook from the command line.
#
# Usage:
#   tpu-handbook         # page the handbook with `less` (or cat if not a tty)
#   tpu-handbook --path  # print the deployed handbook path and exit
#   tpu-handbook -h      # show this help
#
# When stdout is a terminal we hand off to `less` for paging; otherwise we
# `cat` the raw markdown so pipelines (e.g. `tpu-handbook | grep tpu-device`)
# and AI agents that capture process output get the full text.

set -euo pipefail

HANDBOOK=/usr/local/share/doc/tpus/user-handbook.md

usage() {
    cat <<EOF
Usage: tpu-handbook [--path|-h|--help]

View the TPU cluster user handbook. Pages through \`less\` when stdout is a
terminal; writes raw markdown to stdout when piped or redirected, so it
composes with grep/head/etc.

Options:
  --path      Print the deployed handbook path and exit.
  -h, --help  Show this help.
EOF
}

case "${1:-}" in
    "")
        ;;
    --path)
        echo "$HANDBOOK"
        exit 0
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        echo "tpu-handbook: unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
esac

if [[ ! -r "$HANDBOOK" ]]; then
    echo "tpu-handbook: $HANDBOOK not readable" >&2
    exit 1
fi

if [[ -t 1 ]]; then
    exec less -- "$HANDBOOK"
else
    exec cat -- "$HANDBOOK"
fi
