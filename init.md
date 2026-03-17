Initialising a TPU VM
=====================

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
# MFR's tiny TPU cluster
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

The login will be blocked if they same IPs are used as an old machine. Delete
the offending rows from `.ssh/config/known_hosts`.

Upgrading system packages
-------------------------

On each VM:

```
sudo apt update
sudo apt upgrade
sudo apt autoremove
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

Installing Python 3.14
----------------------

Installing a newer version of python
([guide](https://tecadmin.net/how-to-install-python-3-14-on-ubuntu/)).

```
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.14
```

Make 3.14 the default.

```
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.14 314
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 310
sudo update-alternatives --config python3 # only one alternative, not needed!
```

Install pip and uv (globally):

```
sudo apt install python3.14-venv
sudo python3.14 -m ensurepip --upgrade --default-pip
sudo python3.14 -m pip install --upgrade pip
sudo pip install uv
```

Fix some incompatible things

```
sudo apt remove command-not-found
```

Installing custom scripts
-------------------------

On each TPU VM:

```
sudo mkdir -p /home/shared
sudo chmod +777 /home/shared
```

From local to each TPU VM:

```
for t in 0 1 2 3; do scp admin-scripts/* tpu$t: ; done
for t in 0 1 2 3; do scp shared-scripts/* tpu$t:/home/shared/; done
```

On each TPU VM:

```
chmod +x adduser.sh
chmod +x /home/shared/tpu-device.sh
sudo ln -s /home/shared/tpu-device.sh /usr/local/bin/tpu-device
chmod +x /home/shared/tpups.py
sudo ln -s /home/shared/tpups.py /usr/local/bin/tpups
chmod +x /home/shared/tpu-usage.py
sudo ln -s /home/shared/tpu-usage.py /usr/local/bin/tpu-usage
chmod +x /home/shared/tpu-heatmap.py
sudo ln -s /home/shared/tpu-heatmap.py /usr/local/bin/tpu-heatmap
```

Set up heartbeat, tpups, and server:

```
nohup python3 /home/shared/tpu-heartbeat.py > heartbeat.log 2>&1 &
nohup python3 -m http.server 8080 --directory /home/shared/heartbeat > /dev/null 2>&1 &
```

Adding new users
----------------

Instruct users to select a username <USERNAME> and generate a public key as
follows:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpu -C <USERNAME>
```

The key will have format `ssh-ed25519 BLAHBLAH etc`, take the `BLAHBLAH` bit
only and their chosen username and run it through the user creation script on
each TPU VM. For example:

```
sudo ./adduser.sh afiq AAAAC3NzaC1lZDI1NTE5AAAAINmp4YYoMgXP8MEQsMjkla+o81pwI7hj9EN6eIbFZzvV
```

TPU logging permission
----------------------

The first time a user runs something on the TPUs, for some reason it claims the
tpu logs and subsequent users get lots of warnings.

Use the TPUs to create log folder:

```
uv venv venv
source venv/bin/activate
uv pip install "jax[tpu]"
tpu-device 0 python -c "import jax; print(jax.devices())"
rm -rf venv
```

Reset log folder ownership:

```
sudo chown -R tpu-runtime:tpu-runtime /tmp/tpu_logs/
sudo chmod +777 /tmp/tpu_logs/
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

### Trouble: System logs blowing up?

Last time I had some issues with system logs blowing up. Should be possible to
limit their size.

For syslog and kern.log, edit `/etc/rsyslog.d/50-default.conf` as follows:

```
# replace this line:
*.*;auth,authpriv.none          -/var/log/syslog
# with these two:
$outchannel mysyslog,/var/log/syslog,1048576 # 1MB
*.*;auth,authpriv.none          :omfile:$mysyslog

# and replace this line:
kern.*                          -/var/log/kern.log
# with these two:
$outchannel mykernlog,/var/log/kern.log,1048576 # 1MB
kern.*                          :omfile:$mykernlog
```

Don't forget to restart the service:
```
sudo systemctl restart  rsyslog.service
```

Let's see if that works?

Similar for journals:


https://linuxhandbook.com/clear-systemd-journal-logs/

Configuring intra-cluster keys
----------------------------

Locally:

```
ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpus-all -C "m@far.in.net"
for t in 0 1 2 3; do scp ~/.ssh/mfr-tpus-all{,.pub} tpu$t:.ssh; done
```

On each machine:

```
cat ~/.ssh/mfr-tpus-all.pub >> ~/.ssh/authorized_keys
```

Save this as `~/.ssh/config`:

```

# MFR's tiny TPU cluster
Host tpu0 tpu1 tpu2 tpu3
    IdentityFile ~/.ssh/mfr-tpus-all
    User matt
Host tpu0
    HostName 10.130.0.12
Host tpu1
    HostName 10.130.0.11
Host tpu2
    HostName 10.130.0.10
Host tpu3
    HostName 10.130.0.13
```

### Trouble: Bad configuration option

For some very weird reason, the ssh tool on the TPUs doesn't see the first byte
of the config file. This often led me to a parse error. The above has an empty
line as the first byte which seems to fix it.

I confirmed there were no weird bytes with `od -c ~/.ssh/config`.

Gemini guesses this is a bug in the SSH version bundled with the image. I guess
I think that's unlikely because I think the SSH version is up to date?

Anyway it doesn't seem to be a wider problem so I'll just leave it...

Miscelaneous issues
-------------------

### Trouble: bash shows a setlocale warning

The warning shows up thus:
```
/bin/bash: warning: setlocale: LC_ALL: cannot change locale (en_AU.UTF-8)
```

Fix it by installing the missing locale:

```
sudo locale-gen en_AU.UTF-8
sudo update-locale LANG=en_AU.UTF_8
```


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
# GitHub keys
Host github.com
  User git
  IdentityFile ~/.ssh/{name-of-your-key}
  IdentitiesOnly yes
```

Remember to watch out for the SSH parser bug.

### Installing neovim

On each vm:

```
sudo apt install neovim
mkdir -p ~/.config/nvim
```

Copy my config from local:
```
for t in 0 1 2 3; do scp -r home-stuff/init.vim tpu$t:.config/nvim/init.vim ; done
```


### Installing zsh

On each VM:

```
sudo apt install zsh
sudo chsh -s $(which zsh) $(whoami)
```

Copy my config from local:
```
for t in 0 1 2 3; do scp -r home-stuff/zshrc.zshrc tpu$t:.zshrc ; done
```

### Other packages:

Other packages (currently only installed on TPU0):

```
sudo apt install ffmpeg
```

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

