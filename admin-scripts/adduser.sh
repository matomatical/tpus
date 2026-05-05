#!/bin/bash
# Create a new cluster user with a shared home on /storage.
#
# - Creates matching UID and GID on all 4 VMs.
# - Creates the home directory once at /storage/home/<username> (visible on
#   every VM via JuiceFS).
# - Installs the provided external SSH key in ~/.ssh/authorized_keys.
# - Generates an intra-cluster SSH key pair (~/.ssh/cluster[.pub]) so the
#   user can `ssh tpuX` between VMs.
#
# Run as matt from tpu0.
#
# Usage: ./adduser.sh <username> <ssh-key-string>

set -euo pipefail

USERNAME="${1:?Usage: $0 <username> <ssh-key-string>}"
KEYSTR="${2:?Usage: $0 <username> <ssh-key-string>}"
HOME_NEW="/storage/home/$USERNAME"

# --- Preflight ------------------------------------------------------------

# Must be on tpu0 (UID/GID assignment + home creation happen here).
tpu0_ip=$(getent hosts tpu0 | awk '{print $1}')
if ! hostname -I | tr ' ' '\n' | grep -qxF "$tpu0_ip"; then
    echo "ERROR: run from tpu0 (tpu0=$tpu0_ip, my IPs: $(hostname -I))" >&2
    exit 1
fi

# /storage must be mounted on every node.
for t in 0 1 2 3; do
    if [ "$t" = 0 ]; then
        mountpoint -q /storage \
            || { echo "ERROR: /storage not mounted on tpu0" >&2; exit 1; }
    else
        ssh "tpu$t" "mountpoint -q /storage" \
            || { echo "ERROR: /storage not mounted on tpu$t" >&2; exit 1; }
    fi
done

# User must not yet exist on any node, and home path must be clear.
if id "$USERNAME" &>/dev/null; then
    echo "ERROR: user '$USERNAME' already exists on tpu0" >&2; exit 1
fi
for t in 1 2 3; do
    if ssh "tpu$t" "id $USERNAME" &>/dev/null; then
        echo "ERROR: user '$USERNAME' already exists on tpu$t" >&2; exit 1
    fi
done
if [ -e "$HOME_NEW" ]; then
    echo "ERROR: $HOME_NEW already exists" >&2; exit 1
fi

sudo install -d -m 0755 -o root -g root /storage/home

# --- 1. Create user + home on tpu0 (origin assigns UID/GID) ---------------

echo "=== Creating '$USERNAME' on tpu0 (origin) ==="
sudo useradd -m -d "$HOME_NEW" -s /bin/bash "$USERNAME"
UID_NEW=$(id -u "$USERNAME")
GID_NEW=$(id -g "$USERNAME")
echo "  uid=$UID_NEW gid=$GID_NEW home=$HOME_NEW"

# --- 2. Create matching user on tpu1, tpu2, tpu3 --------------------------
#
# -M on useradd: don't create a home dir (it already exists on /storage).
# groupadd -g pins the GID; without this, useradd would auto-create the
# user's primary group with the next free GID on each node, diverging from
# tpu0. Shared storage resolves ownership by numeric ID, so GIDs must match.

for t in 1 2 3; do
    echo "=== Creating '$USERNAME' on tpu$t (uid=$UID_NEW, gid=$GID_NEW) ==="
    ssh "tpu$t" "sudo groupadd -g $GID_NEW $USERNAME \
              && sudo useradd -M -u $UID_NEW -g $GID_NEW \
                    -d $HOME_NEW -s /bin/bash $USERNAME"
done

# --- 3. Install external SSH key -----------------------------------------

echo "=== Installing external SSH key ==="
sudo install -d -m 0700 -o "$USERNAME" -g "$USERNAME" "$HOME_NEW/.ssh"
echo "ssh-ed25519 $KEYSTR $USERNAME" \
    | sudo tee -a "$HOME_NEW/.ssh/authorized_keys" > /dev/null

# --- 4. Generate intra-cluster SSH key pair ------------------------------
#
# The redirect-read must happen inside sudo because .ssh/ is already 0700
# owned by the user — matt's shell would fail to open cluster.pub to feed
# it to tee.

echo "=== Generating intra-cluster SSH key pair ==="
sudo ssh-keygen -t ed25519 -f "$HOME_NEW/.ssh/cluster" \
    -N "" -C "$USERNAME@cluster" -q
sudo sh -c 'cat "$1" >> "$2"' -- \
    "$HOME_NEW/.ssh/cluster.pub" "$HOME_NEW/.ssh/authorized_keys"

# --- 5. Fix ownership and perms on .ssh ----------------------------------

sudo chown -R "$USERNAME:$USERNAME" "$HOME_NEW/.ssh"
sudo chmod 0600 "$HOME_NEW/.ssh/authorized_keys" "$HOME_NEW/.ssh/cluster"
sudo chmod 0644 "$HOME_NEW/.ssh/cluster.pub"

# --- 6. Pre-seed ~/.ssh/config at 0600 -----------------------------------
#
# The default umask under user-private-groups is 0002, so an editor-created
# ~/.ssh/config lands at 0664. OpenSSH's strict-modes path on user config
# does not abort cleanly on group-writable; it silently drops the first
# byte before parsing, producing a confusing "Bad configuration option"
# error on line 1. Pre-seeding with mode 0600 means edits inherit the mode
# and the user never trips this. See issues/ssh-config-strict-modes.md.

sudo install -m 0600 -o "$USERNAME" -g "$USERNAME" /dev/null \
    "$HOME_NEW/.ssh/config"

echo
echo "=== Done! ==="
echo "  User '$USERNAME' created with shared home at $HOME_NEW."
echo "  Don't forget to append the invocation to users.md."
