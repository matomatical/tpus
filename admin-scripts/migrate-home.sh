#!/bin/bash
# Migrate a user's home directory from per-node /home/$U to /storage/home/$U.
# Run as matt from tpu0 (needs sudo and inter-VM SSH).
#
# Layout after migration:
#   /storage/home/$U/           <- tpu0's /home/$U contents (merged root)
#   /storage/home/$U/tpu1/      <- tpu1's /home/$U contents (for user to reconcile)
#   /storage/home/$U/tpu2/      <- tpu2's /home/$U contents
#   /storage/home/$U/tpu3/      <- tpu3's /home/$U contents
#
# /home/$U is left intact on each node as a local backup.
#
# Usage: ./migrate-home.sh <username>

set -euo pipefail

U="${1:?Usage: $0 <username>}"

# Confirm we're on tpu0 by matching our IPs against tpu0's /etc/hosts entry.
# (The kernel hostname is `t1v-n-...`; `tpu0` is only a cluster-hosts alias.)
tpu0_ip=$(getent hosts tpu0 | awk '{print $1}')
if ! hostname -I | tr ' ' '\n' | grep -qxF "$tpu0_ip"; then
    echo "ERROR: run from tpu0 (tpu0=$tpu0_ip, my IPs: $(hostname -I))" >&2
    exit 1
fi

run_on() {
    # run_on NODE CMD -- runs CMD locally on tpu0 or via ssh on tpu1/2/3
    local t=$1; shift
    if [ "$t" = 0 ]; then bash -c "$*"
    else ssh "tpu$t" "$*"
    fi
}

# --- Preflight checks ------------------------------------------------------

echo "=== Preflight checks for user '$U' ==="

for t in 0 1 2 3; do
    run_on "$t" "mountpoint -q /storage" \
        || { echo "ERROR: /storage not mounted on tpu$t" >&2; exit 1; }
done
echo "  /storage mounted on all 4 nodes"

line0=$(getent passwd "$U") \
    || { echo "ERROR: user '$U' does not exist on tpu0" >&2; exit 1; }
uid0=$(echo "$line0" | cut -d: -f3)
gid0=$(echo "$line0" | cut -d: -f4)
home0=$(echo "$line0" | cut -d: -f6)
for t in 1 2 3; do
    linet=$(ssh "tpu$t" "getent passwd $U") \
        || { echo "ERROR: user '$U' not found on tpu$t" >&2; exit 1; }
    uidt=$(echo "$linet" | cut -d: -f3)
    gidt=$(echo "$linet" | cut -d: -f4)
    [ "$uidt" = "$uid0" ] \
        || { echo "ERROR: UID for '$U' differs on tpu$t ($uidt vs $uid0)" >&2; exit 1; }
    [ "$gidt" = "$gid0" ] \
        || { echo "ERROR: GID for '$U' differs on tpu$t ($gidt vs $gid0)" >&2; exit 1; }
done
echo "  user '$U' has uid=$uid0 gid=$gid0 on all 4 nodes"

if [ "$home0" != "/home/$U" ]; then
    echo "ERROR: passwd home for '$U' is '$home0', not '/home/$U' — already migrated?" >&2
    exit 1
fi

for t in 0 1 2 3; do
    active=$(run_on "$t" "who | awk -v u=$U '\$1==u'")
    if [ -n "$active" ]; then
        echo "ERROR: '$U' has active session(s) on tpu$t:" >&2
        echo "$active" >&2
        exit 1
    fi
done
echo "  no active sessions for '$U' on any node"

if [ -e "/storage/home/$U" ]; then
    echo "ERROR: /storage/home/$U already exists — refusing to overwrite" >&2
    exit 1
fi

# --- Pre-create destination directories -----------------------------------

echo
echo "=== Pre-creating destinations ==="
sudo install -d -m 0755 -o root -g root /storage/home
sudo install -d -m 0750 -o "$U" -g "$U" "/storage/home/$U"
for t in 1 2 3; do
    sudo install -d -m 0750 -o "$U" -g "$U" "/storage/home/$U/tpu$t"
done
echo "  /storage/home/$U/{,tpu1,tpu2,tpu3} ready (owner $U, mode 0750)"

# --- Parallel rsyncs ------------------------------------------------------

LOGDIR=$(mktemp -d "/tmp/migrate-home-$U-XXXXXX")
echo
echo "=== Running 4 rsyncs in parallel (logs: $LOGDIR/) ==="

declare -a PIDS
for t in 0 1 2 3; do
    log="$LOGDIR/tpu$t.log"
    if [ "$t" = 0 ]; then
        dst="/storage/home/$U/"
    else
        dst="/storage/home/$U/tpu$t/"
    fi
    cmd="sudo rsync -aHAX --numeric-ids --info=progress2 --no-inc-recursive /home/$U/ $dst"
    run_on "$t" "$cmd" > "$log" 2>&1 &
    PIDS[$t]=$!
done

# --- Progress monitor -----------------------------------------------------
#
# Prints a 4-line block (one per node) and refreshes it in place. The redraw
# uses ESC[4A (cursor up 4 lines) then ESC[J (erase from cursor to end of
# screen) to wipe the previous block cleanly before redrawing. Content is
# truncated to fit the terminal width so lines never wrap — if a status line
# wrapped onto a second physical row, ESC[4A would no longer line up with
# the top of the block and subsequent redraws would garble.

status_for() {
    local t=$1 log="$LOGDIR/tpu$t.log" line
    # rsync --info=progress2 uses \r to overwrite a single line; flatten
    # carriage returns into newlines and take the latest non-empty line.
    line=$(tr '\r' '\n' < "$log" 2>/dev/null | awk 'NF' | tail -1)
    echo "${line:-starting...}"
}

# Bound status content to fit terminal width (15-char prefix + a margin).
COLS=$(tput cols 2>/dev/null || echo 120)
CONTENT_MAX=$(( COLS > 30 ? COLS - 16 : 14 ))

print_block() {
    local t marker line
    for t in 0 1 2 3; do
        if kill -0 "${PIDS[$t]}" 2>/dev/null; then marker="[run ]"
        else marker="[done]"
        fi
        line=$(status_for "$t")
        # Leading \r forces col 0 (defensive — \n doesn't always imply CR).
        # Trailing \033[K clears to end of line so shorter new content wipes
        # any longer previous content cleanly.
        printf '\r  %s tpu%d  %.*s\033[K\n' "$marker" "$t" "$CONTENT_MAX" "$line"
    done
}

print_block
while :; do
    alive=0
    for t in 0 1 2 3; do
        if kill -0 "${PIDS[$t]}" 2>/dev/null; then alive=1; fi
    done
    [ $alive -eq 0 ] && break
    sleep 2
    printf '\r\033[4A'   # col 0, then cursor up 4 lines
    print_block
done
printf '\r\033[4A'
print_block

# --- Check rsync exit statuses --------------------------------------------

echo
echo "=== rsync exit statuses ==="
fail=0
for t in 0 1 2 3; do
    if wait "${PIDS[$t]}"; then
        echo "  tpu$t: OK"
    else
        rc=$?
        echo "  tpu$t: FAILED (exit $rc) — see $LOGDIR/tpu$t.log" >&2
        fail=1
    fi
done
if [ "$fail" != 0 ]; then
    echo "Aborting: /storage/home/$U is in a partial state; delete and retry." >&2
    exit 1
fi

# --- Post-migration fixup -------------------------------------------------

echo
echo "=== Normalizing ownership on /storage/home/$U ==="
sudo chown -R "$U":"$U" "/storage/home/$U"
echo "  done"

echo
echo "=== Updating passwd home entry on each node ==="
for t in 0 1 2 3; do
    run_on "$t" "sudo usermod -d /storage/home/$U $U"
    echo "  tpu$t: usermod -d /storage/home/$U"
done

echo
echo "=== Verifying login shell lands in /storage/home/$U ==="
verify_fail=0
for t in 0 1 2 3; do
    got=$(run_on "$t" "sudo -u $U -i pwd" 2>/dev/null || true)
    if [ "$got" = "/storage/home/$U" ]; then
        echo "  tpu$t: OK ($got)"
    else
        echo "  tpu$t: FAIL (got '$got')" >&2
        verify_fail=1
    fi
done
if [ "$verify_fail" != 0 ]; then
    echo "Verification failed on at least one node." >&2
    exit 1
fi

echo
echo "=== Migration of '$U' complete ==="
echo "  /home/$U is preserved on each node as a local backup."
echo "  User should reconcile /storage/home/$U/tpu{1,2,3}/ into their home."
echo "  Logs retained in $LOGDIR/"
