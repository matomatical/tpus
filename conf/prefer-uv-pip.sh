# Discourage interactive use of `pip` / `pip3` in favour of `uv pip` so that
# packages land in the active venv via uv's resolver rather than going to
# system site-packages or the wrong environment. This file installs shell
# functions, so only interactive shells (and login-shell scripts that source
# /etc/profile) are affected — tools that shell out to `pip` from a script
# with its own shebang are unchanged.
#
# If `pip` resolves into the active $VIRTUAL_ENV, we run it normally — that's
# a real venv pip and presumably intentional. Otherwise we refuse and point
# at `uv pip`. Escape hatch: `raw-pip` / `raw-pip3`.

_tpu_uv_pip_warn() {
    cat >&2 <<EOF
'$1' is discouraged on this cluster — please use 'uv pip' instead, so
packages install into the active venv via uv's resolver.

  install into current venv:    uv pip install <pkg>
  one-off ephemeral run:         uvx <tool>
  really want pip anyway:        raw-$1 <args>

See \`tpu-handbook\` for details.
EOF
}

pip() {
    local real
    real=$(unset -f pip 2>/dev/null; command -v pip)
    if [ -n "$VIRTUAL_ENV" ] && [ -n "$real" ] && [ "${real#$VIRTUAL_ENV/}" != "$real" ]; then
        "$real" "$@"
        return $?
    fi
    _tpu_uv_pip_warn pip
    return 1
}

pip3() {
    local real
    real=$(unset -f pip3 2>/dev/null; command -v pip3)
    if [ -n "$VIRTUAL_ENV" ] && [ -n "$real" ] && [ "${real#$VIRTUAL_ENV/}" != "$real" ]; then
        "$real" "$@"
        return $?
    fi
    _tpu_uv_pip_warn pip3
    return 1
}

raw-pip()  { command pip  "$@"; }
raw-pip3() { command pip3 "$@"; }
