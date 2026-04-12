Initialising a TPU VM
=====================

Budget alerts
-------------

Notes:

* I configured a "budget" for all GCP usage at 200 GBP per month.
  * I should get alerts at 10% and a few other larger thresholds each month.
* I tried to configure an automatic response to exceeding the budget to disable
  cloud billing, but I was unable to test it and unsure if it will work if this
  happens.


Provisioning
------------

In GCP console, go to compute > TPUs and create a new TPU resource. The
configuration should match the allocation from allocation email.

Here is an equivalent `gcloud` command:

```
gcloud compute tpus tpu-vm create \
  cluster \
  --accelerator-type v4-32 \
  --zone=us-central2-b \
  --version=tpu-ubuntu2204-base
```

Sometimes this fails if there are no free resources. In that case tick the
option to create a queued resource instead, which means it will be created as
soon as one becomes available.

### Verifying cluster hardware

After provisioning, verify the key stats from each VM:

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  # CPU model and core count
  ssh tpu$t 'cat /proc/cpuinfo | grep "model name" | head -1 && nproc'
  # Memory
  ssh tpu$t 'free -h'
  # Disk
  ssh tpu$t 'df -h /'
  # TPU devices (should show accel0-3 on each VM)
  ssh tpu$t 'ls /dev/accel*'
  # OS version
  ssh tpu$t 'lsb_release -d'
done
```

### Trouble: TPU version

Previously I tried `--version=tpu-vm-base`, but that turned out to be a mistake
because it was Ubuntu 20 instead of 22. I wasn't even able to install n updated
version of Python.

SSH config
----------

Create a key (if haven't already):

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpus -C matt
```

Add key to cluster with GCP console (compute > metadata > ssh keys).


Configure `~/.ssh/config` (get IP addresses from GCP console):

```ssh_config
## MFR's tiny TPU cluster
Host tpu0 tpu1 tpu2 tpu3
    IdentityFile ~/.ssh/mfr-tpus
    User matt
Host tpu0
    HostName 35.186.109.61
Host tpu1
    HostName 35.186.93.73
Host tpu2
    HostName 173.255.125.97
Host tpu3
    HostName 35.186.140.33
```

Now you can ssh into the TPU VMs with `ssh tpuX` for `X` in `0123`.

### Trouble: Identity verification issue

The login will be blocked if the same IPs are used as an old machine. Remove
the stale host keys with:

```
for t in 0 1 2 3; do ssh-keygen -R tpu$t; done
```

Upgrading system packages
-------------------------

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  ssh tpu$t 'sudo apt update && sudo apt upgrade -y && sudo apt autoremove -y'
done
# Restart remote VMs first, then local VM last
for t in 1 2 3; do ssh tpu$t 'sudo shutdown -r now'; done
sudo shutdown -r now
```

Wait for restart!

### Trouble: Waiting for cache lock

Sometimes one of the TPUs can't do `sudo apt upgrade` due to an error:

```
Waiting for cache lock: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 11794 (unattended-upgr)
```

I restarted these machines first, then it worked.

Actually, the second time this happened, one of the TPUs took a long time to
restart. But it did eventually come back after an hour or so.

### Trouble: Some upgrades held back

I am not sure why these were held back, perhaps deliberately by GCP people who
know better than me. At the time I thought worth trying to upgrade them to
since this was blocking me from `do-release-upgrade`. It was possible to
upgrade them forcefully:

```
sudo apt upgrade linux-gcp linux-headers-gcp linux-image-gcp
sudo shutdown -r now
```

However, I later realised I didn't want to `do-release-upgrade` and I should
just recreate the cluster instead...

Installing uv
-------------

Install uv as a standalone binary
([docs](https://docs.astral.sh/uv/getting-started/installation/)):

```
for t in 0 1 2 3; do
  echo "=== tpu$t ==="
  ssh tpu$t 'curl -LsSf https://astral.sh/uv/install.sh | sudo sh'
done
```


### Trouble: Using newer versions of Python

Previously, I was installing python3.14 at system level. That breaks some
things, easier to let uv manage all Python versions. To use a newer Python in a
project, just specify it when creating a venv:

```
uv venv --python 3.14
```

Then uv manages Python versions and virtual environments without modifying the
system Python.

Installing custom scripts
-------------------------

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo mkdir -p /home/shared && sudo chmod 755 /home/shared'
done
```

From local to each TPU VM:

```
for t in 0 1 2 3; do
  scp shared-scripts/* tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/tpu-*.sh /tmp/tpu-*.py /tmp/tpups.py /home/shared/'
done
```

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo chmod +x /home/shared/tpu-device.sh /home/shared/tpups.py /home/shared/tpu-usage.py /home/shared/tpu-heatmap.py /home/shared/tpu-health.py'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-device.sh /usr/local/bin/tpu-device'
  ssh tpu$t 'sudo ln -sf /home/shared/tpups.py /usr/local/bin/tpups'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-usage.py /usr/local/bin/tpu-usage'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-heatmap.py /usr/local/bin/tpu-heatmap'
  ssh tpu$t 'sudo ln -sf /home/shared/tpu-health.py /usr/local/bin/tpu-health'
done
```

Install `tpups` into the login MOTD so users see cluster status on SSH login.
This is a **copy** (not a symlink) so that `/home/shared/` modifications can't
get code execution as root via MOTD:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo cp /home/shared/tpups.py /etc/update-motd.d/99-tpups'
done
```

Set up heartbeat and status web server as systemd services:

```
for t in 0 1 2 3; do
  scp conf/tpu-heartbeat.service conf/tpu-heartbeat-web.service tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/tpu-heartbeat.service /tmp/tpu-heartbeat-web.service /etc/systemd/system/'
  ssh tpu$t 'sudo systemctl daemon-reload'
  ssh tpu$t 'sudo systemctl enable --now tpu-heartbeat.service tpu-heartbeat-web.service'
done
```

The web server logs to `/dev/shm/tpu-heartbeat-web.log` (tmpfs) rather than
disk to avoid ext4 journal contention when training jobs are writing heavily.
Install the logrotate config to cap the log size (see logrotate section below).

Adding new users
----------------

Instruct users to select a username <USERNAME> and generate a public key as
follows:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpu -C <USERNAME>
```

The key will have format `ssh-ed25519 BLAHBLAH etc`, take the `BLAHBLAH` bit
only and their chosen username and run it through the user creation script from
any VM (e.g. tpu0). The script creates the user on all 4 VMs, sets up their
external SSH key, and generates intra-cluster SSH keys so they can `ssh tpuX`
between VMs. For example:

```
./adduser.sh afiq AAAAC3NzaC1lZDI1NTE5AAAAINmp4YYoMgXP8MEQsMjkla+o81pwI7hj9EN6eIbFZzvV
```

To set up intra-cluster keys for an existing user (e.g. after re-provisioning):

```
./setup-cluster-keys.sh <username>
```

TPU logging permission
----------------------

The TPU runtime writes logs to `/tmp/tpu_logs/`. Whichever user first triggers
this directory's creation ends up owning it, and other users get "Permission
denied" warnings. To fix this, install a `tmpfiles.d` config that ensures the
directory is created with mode 1777 (world-writable + sticky bit, like `/tmp`
itself) on every boot:

```
for t in 0 1 2 3; do
  scp conf/tpu-logs.conf tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/tpu-logs.conf /etc/tmpfiles.d/tpu-logs.conf'
  ssh tpu$t 'sudo systemd-tmpfiles --create'
done
```

System log size limits
----------------------

Check storage:

```
# quick check
df -h
# investigate
sudo du -hd1 /
sudo du -h /var/log/* | sort -h
```

### Configuring logrotate

The default logrotate config for rsyslog only rotates weekly, which is not
enough---`kern.log` and `syslog` can blow up due to noisy kernel messages (e.g.
from the TPU gasket driver or the healthAgent Docker container hitting its OOM
limit). Install a custom logrotate config that rotates these daily and also at
100MB:

```
for t in 0 1 2 3; do
  scp conf/logrotate-rsyslog.conf conf/logrotate-heartbeat-web.conf tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/logrotate-rsyslog.conf /etc/logrotate.d/rsyslog'
  ssh tpu$t 'sudo cp /tmp/logrotate-heartbeat-web.conf /etc/logrotate.d/heartbeat-web'
done
```

### Configuring journalctl

Alongside rsyslog, the system logs most messages also to journalctl's binary
database, which lives at `/var/log/journal/`. The same kinds of issues cause it
to blow up to 4GB which is the default max size. This seems unnecessary, can
lower the size limit to 500MB and then restart the service as follows.

```
for t in 0 1 2 3; do
    ssh tpu$t 'sudo mkdir -p /etc/systemd/journald.conf.d && echo -e "[Journal]\nSystemMaxUse=500M" | sudo tee /etc/systemd/journald.conf.d/size.conf && sudo systemctl restart systemd-journald'
done
```

### Trouble: Truncating bloated logs

If the logs have already grown too large, truncate them:

```
for t in 0 1 2 3; do ssh tpu$t 'sudo truncate -s 0 /var/log/kern.log /var/log/syslog'; done
```

### Trouble: healthAgent OOM

The Google-provided `healthAgent` Docker container (part of the default TPU VM
image) appears to have a memory leak. It gradually fills its 512MB cgroup limit
and then floods `kern.log` with OOM messages because it is configured with
`--oom-kill-disable=true`. Restart it with:

```
sudo systemctl restart healthagent.service
```

To prevent this from recurring, a weekly systemd timer automatically restarts
the container. Deploy it:

```
for t in 0 1 2 3; do
  scp conf/healthagent-restart.service conf/healthagent-restart.timer tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/healthagent-restart.service /tmp/healthagent-restart.timer /etc/systemd/system/'
  ssh tpu$t 'sudo systemctl daemon-reload'
  ssh tpu$t 'sudo systemctl enable --now healthagent-restart.timer'
done
```

See `issues/healthagent-oom/` for a detailed bug report.

Configuring intra-cluster SSH
-----------------------------

All users can `ssh tpuX` between VMs using `/etc/hosts` for name resolution,
a system-wide SSH client config for the identity file, and per-user cluster
keys.

Deploy cluster hostnames to `/etc/hosts` and the SSH config:

```
for t in 0 1 2 3; do
  scp conf/cluster-hosts conf/cluster-ssh.conf tpu$t:/tmp/;
  ssh tpu$t 'cat /tmp/cluster-hosts | sudo tee -a /etc/hosts > /dev/null';
  ssh tpu$t 'sudo mkdir -p /etc/ssh/ssh_config.d';
  ssh tpu$t 'sudo cp /tmp/cluster-ssh.conf /etc/ssh/ssh_config.d/cluster-ssh.conf';
done
```

Per-user cluster keys are set up automatically by `adduser.sh`. For existing
users, use `setup-cluster-keys.sh`.

### Trouble: Bad configuration option

For some very weird reason, the ssh tool on the TPUs doesn't see the first byte
of the config file. This often led me to a parse error. Config files should
start with a leading blank line as a workaround (both `cluster-ssh.conf` and
any `~/.ssh/config` files).

I confirmed there were no weird bytes with `od -c ~/.ssh/config`.

Gemini guesses this is a bug in the SSH version bundled with the image. I guess
I think that's unlikely because I think the SSH version is up to date?

Anyway it doesn't seem to be a wider problem so I'll just leave it...

Update: Later, I can't seem to reproduce this. So just use blank line or double
comment for now.

Miscelaneous issues
-------------------

### Trouble: bash shows a setlocale warning

The warning shows up thus:
```
/bin/bash: warning: setlocale: LC_ALL: cannot change locale (en_AU.UTF-8)
```

Fix it by installing the missing locale:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo locale-gen en_AU.UTF-8 && sudo update-locale LANG=en_AU.UTF-8'
done
```

Default TPU environment variables
---------------------------------

By default, JAX and PyTorch/XLA try to coordinate across all 4 VMs in the pod,
which causes programs to hang if they aren't launched with the right environment
variables. To prevent this, we install a `/etc/profile.d/` script that sets
safe defaults (single device on the current VM) for all bash login shells.

Deploy the config to each VM:

```
for t in 0 1 2 3; do
  scp conf/tpu-defaults.sh tpu$t:/tmp/
  ssh tpu$t 'sudo cp /tmp/tpu-defaults.sh /etc/profile.d/tpu-defaults.sh'
done
```

Note: `/etc/profile.d/` is only sourced by bash (and sh) login shells. Zsh does
not source it, so zsh users (i.e. me) need the same exports in their `.zshrc`.
These are already included in `home-stuff/zshrc.zshrc`.

Users can still use `tpu-device` to override these defaults and target specific
devices or multiple devices.

Making myself at home
---------------------

### Configuring my GitHub

On my local machine:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpus-gh -C "m@far.in.net"
```

Don't forget to add it to my github profile.

Then copy to each TPU VM:

```
for t in 0 1 2 3; do scp ~/.ssh/mfr-tpus-gh{,.pub} tpu$t:.ssh; done
```

Then on each machine, add it to the ssh agent:

```
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/mfr-tpus-gh
```

Actually, seems like the above is for passworded keys, and for some reason this
ssh agent is dying every time I log out (this didn't used to happen and doesn't
happen on my laptop?). A simpler solution seems to be to add this to
~/.ssh/config on each VM:

```
## GitHub keys
Host github.com
  User git
  IdentityFile ~/.ssh/{name-of-your-key}
  IdentitiesOnly yes
```

### Installing neovim

Install neovim and load config:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y neovim && mkdir -p ~/.config/nvim'
  scp home-stuff/init.vim tpu$t:.config/nvim/init.vim
done
```

### Installing zsh

Install zsh and load config:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y zsh && sudo chsh -s $(which zsh) matt'
  scp home-stuff/zshrc.zshrc tpu$t:.zshrc
done
```

### Other packages

Other system packages:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y ffmpeg pandoc entr'
done
```

### Installing LaTeX

LaTeX has various distributions with various sizes, I went for something short
of the full set
  (see [here](https://tex.stackexchange.com/questions/245982/differences-between-texlive-packages-in-linux#answer-504566)
  for notes on different options).

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y texlive-latex-extra latexmk'
done
```

Ideally could use tectonic but that does not have an official distribution via
apt, only snap, and I seem to dislike snap? Could install manually or compile
it from source if I installed rust. Ah---it is also on brew.

Some extra packages for rendering plots:

```
for t in 0 1 2 3; do
  ssh tpu$t 'sudo apt install -y cm-super dvipng'
done
```

### Installing NodeJS / apps

Node available from apt is ridiculously old. I went with nvm. This means it's a
local install and I only did it on tpu0 so far.

```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.5/install.sh | bash
```

Note: it adds stuff to zshrc to get onto path.

Installing apps can then use -g because that is still only me:

```
npm install -g @google/gemini-cli # google gemini
npm install -g @openai/codex      # openai codex
```

If I needed to install node system packages globally? Hope I never face that
issue, but probably nvm can be configured to handle it. Ideally it is just like
uv and people can take care of their own environments.


Security goals
--------------

The user handbook promises that non-admin users can't access each other's files
and that only the admin (matt) has elevated privileges. The users themselves
are trusted---the threat model is not malicious insiders but rather:

* Innocent mistakes by inexperienced users
* AI coding agents that users run with too-broad permissions
* AI coding agents that get exposed to prompt injection attacks

Standard isolation (no sudo, no cross-user file access) limits the blast radius
of these kinds of incidents. Maintaining this posture is also deliberate
practice: a security mindset matters more in higher-stakes settings, and this
cluster is a good low-stakes environment to build the habit.

The key invariants:

* Home directories are `drwxr-x---` (owner-only access).
* Non-admin users have no sudo access.
* `/home/shared/` scripts are owned by root and not writable by users. The
  MOTD script (`/etc/update-motd.d/99-tpups`) is a copy, not a symlink, so
  modifications to `/home/shared/` can't get code execution as root via MOTD.
* No user can write to anything that another user executes.
* In particular, no user can write to anything that `matt` executes, since
  compromising the admin account grants full sudo access to the cluster.

When adding new shared infrastructure (e.g. shared caches, shared storage),
**preserve these invariants.** In particular, be cautious about shared writable
directories whose contents end up on other users' execution paths (e.g. Python
package caches with symlink mode).

Not currently hardened (standard Linux defaults, acceptable for now):

* `/proc` has no `hidepid`, so users can see each other's process command lines
  via `ps`. Avoid passing secrets as CLI arguments.
* No disk quotas or cgroups, so one user could fill the disk or exhaust memory.


TODO: External persistent storage
---------------------------------

External storage:

* Persistent disk?
* GCS FUSE?

TODO: Job management
--------------------

See this discussion with gemini for hq configuration:

* https://gemini.google.com/share/379ddb7d8ea9

Plan:

* Provision maybe 14/16 nodes in this queue (save one or two for interactive
  use?)
* Needs a persistent disk for script and data storage? Or NFS again.
* Can use /path/to/venv/bin/python as the command to automatically import the
  venv.

