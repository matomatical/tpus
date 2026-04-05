#!/bin/bash
# Create a new user on all cluster VMs and set up intra-cluster SSH.
# Run as matt (needs sudo access and inter-VM SSH).
#
# Usage: ./adduser.sh <username> <ssh-key-string>

set -euo pipefail

USERNAME="${1:?Usage: $0 <username> <ssh-key-string>}"
KEYSTR="${2:?Usage: $0 <username> <ssh-key-string>}"
HOSTS=(tpu0 tpu1 tpu2 tpu3)

# Step 1: Create user and add external SSH key on all VMs
echo "Creating user $USERNAME on all VMs..."
for host in "${HOSTS[@]}"; do
    echo "=== $host ==="
    ssh "$host" sudo bash <<REMOTE
        set -euo pipefail
        HOMEDIR="/home/$USERNAME"

        # Create user (skip if already exists)
        if id "$USERNAME" &>/dev/null; then
            echo "User $USERNAME already exists, skipping creation"
        else
            useradd -m -d "\$HOMEDIR" -s /bin/bash "$USERNAME"
        fi

        # Add external SSH key
        mkdir -p "\$HOMEDIR/.ssh"
        if ! grep -qF '$KEYSTR' "\$HOMEDIR/.ssh/authorized_keys" 2>/dev/null; then
            echo "ssh-ed25519 $KEYSTR $USERNAME" >> "\$HOMEDIR/.ssh/authorized_keys"
        fi
        chmod 700 "\$HOMEDIR/.ssh"
        chmod 600 "\$HOMEDIR/.ssh/authorized_keys"
        chown -R "$USERNAME:$USERNAME" "\$HOMEDIR/.ssh"

        echo "Done on \$(hostname)"
REMOTE
done

# Step 2: Generate and distribute intra-cluster keys
echo ""
echo "Setting up intra-cluster SSH keys..."
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
ssh-keygen -t ed25519 -f "$TMPDIR/cluster" -N "" -C "$USERNAME@cluster" -q
PUBKEY=$(<"$TMPDIR/cluster.pub")

for host in "${HOSTS[@]}"; do
    echo "=== $host ==="

    # Copy key files to temp location on remote host
    scp -q "$TMPDIR/cluster" "$TMPDIR/cluster.pub" "$host:/tmp/"

    # Install key pair and authorize
    ssh "$host" sudo bash <<REMOTE
        set -euo pipefail
        HOMEDIR="/home/$USERNAME"

        # Install key pair
        mv /tmp/cluster /tmp/cluster.pub "\$HOMEDIR/.ssh/"

        # Add cluster public key to authorized_keys (idempotent)
        if ! grep -qF '$PUBKEY' "\$HOMEDIR/.ssh/authorized_keys" 2>/dev/null; then
            echo '$PUBKEY' >> "\$HOMEDIR/.ssh/authorized_keys"
        fi

        # Fix ownership and permissions
        chown -R "$USERNAME:$USERNAME" "\$HOMEDIR/.ssh"
        chmod 600 "\$HOMEDIR/.ssh/cluster" "\$HOMEDIR/.ssh/authorized_keys"
        chmod 644 "\$HOMEDIR/.ssh/cluster.pub"

        echo "Done on \$(hostname)"
REMOTE
done

echo ""
echo "Done! User $USERNAME created on all VMs with intra-cluster SSH."
