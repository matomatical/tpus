#!/bin/bash
# Generate and distribute intra-cluster SSH keys for an existing user.
# Run as matt (needs sudo access and inter-VM SSH).
#
# Usage: ./setup-cluster-keys.sh <username>

set -euo pipefail

USERNAME="${1:?Usage: $0 <username>}"
HOSTS=(tpu0 tpu1 tpu2 tpu3)

# Generate key pair in a temp directory
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
ssh-keygen -t ed25519 -f "$TMPDIR/cluster" -N "" -C "$USERNAME@cluster" -q
PUBKEY=$(<"$TMPDIR/cluster.pub")

echo "Generated cluster key for $USERNAME"

for host in "${HOSTS[@]}"; do
    echo "=== $host ==="

    # Check user exists on this host
    if ! ssh "$host" id "$USERNAME" &>/dev/null; then
        echo "User $USERNAME does not exist on $host, skipping"
        continue
    fi

    # Copy key files to temp location on remote host
    scp -q "$TMPDIR/cluster" "$TMPDIR/cluster.pub" "$host:/tmp/"

    # Install key pair and authorize
    ssh "$host" sudo bash <<REMOTE
        set -euo pipefail
        HOMEDIR="/home/$USERNAME"

        mkdir -p "\$HOMEDIR/.ssh"

        # Install key pair
        mv /tmp/cluster /tmp/cluster.pub "\$HOMEDIR/.ssh/"

        # Add public key to authorized_keys (idempotent)
        if ! grep -qF '$PUBKEY' "\$HOMEDIR/.ssh/authorized_keys" 2>/dev/null; then
            echo '$PUBKEY' >> "\$HOMEDIR/.ssh/authorized_keys"
        fi

        # Fix ownership and permissions
        chown -R "$USERNAME:$USERNAME" "\$HOMEDIR/.ssh"
        chmod 700 "\$HOMEDIR/.ssh"
        chmod 600 "\$HOMEDIR/.ssh/cluster" "\$HOMEDIR/.ssh/authorized_keys"
        chmod 644 "\$HOMEDIR/.ssh/cluster.pub"

        echo "Done on \$(hostname)"
REMOTE
done

echo ""
echo "Done! $USERNAME can now 'ssh tpuX' between cluster VMs."
echo "(Requires cluster-ssh.conf deployed to /etc/ssh/ssh_config.d/)"
