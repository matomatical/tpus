#!/bin/bash

# get input
USERNAME="$1"
KEYSTR="$2"
if [[ $EUID -ne 0 || -z "$USERNAME" || -z "$KEYSTR" ]]; then
    echo "Usage: sudo $0 <username> <ssh-key-string>"
    exit 1
fi

# add user
HOMEDIR="/home/$USERNAME"
useradd -m -d "$HOMEDIR" -s /bin/bash "$USERNAME"

# add key for user
mkdir -p "$HOMEDIR/.ssh"
echo "ssh-ed25519 $KEYSTR $USERNAME" >> $HOMEDIR/.ssh/authorized_keys
chmod 700 "$HOMEDIR/.ssh"
chmod 600 "$HOMEDIR/.ssh/authorized_keys"
chown -R "$USERNAME:$USERNAME" "$HOMEDIR"

