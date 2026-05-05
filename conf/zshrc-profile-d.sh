
# === TPUS-ADMIN: source /etc/profile.d/*.sh in zsh ===
# zsh doesn't auto-source /etc/profile.d/ like bash does on login. Source
# them here so zsh users pick up TPU env defaults, the prefer-uv-pip
# warning, and any future entries. emulate sh -c runs each script in
# sh-emulation mode so any sh-isms (word splitting etc.) behave correctly.
for _f in /etc/profile.d/*.sh; do
    [ -r "$_f" ] && emulate sh -c '. "$_f"'
done
unset _f
# === END TPUS-ADMIN ===
