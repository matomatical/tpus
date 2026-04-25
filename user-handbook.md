MFR's Tiny TPU Cluster — Handbook
=================================

Welcome to my tiny little cluster! Let's do some cool research together!


About the cluster
=================

Hardware
--------

The "cluster" is a collection of 4 virtual machines (VMs) running on Google
Cloud. We call them by the following names:

* `tpu0`
* `tpu1`
* `tpu2`
* `tpu3`

All four of these VMs provide shared access to a single "TPU v4-32"
accelerator. This is a 32-core, 512 GiB memory, fourth generation TPU, that can
be used to run high-speed training and inference.

Each of the four TPU VMs has access to:

* Four "TPU devices", which are individual dual-core TPU v4 chips with 32 GiB
  memory (one dual-core device is 1/16 of the entire TPU v4-32).
* Two 60-core (120-thread) AMD EPYC 7B12 CPUs.
* 400 GiB memory (this is CPU-accessible memory; the above numbers are
  TPU-accessible memory).

Most experiments I usually work on are training small models that wouldn't make
good use of all 32 cores at once. Therefore, I normally think of the cluster as
being a collection of 16 dual-core devices, each with 32 GiB of memory.

The cluster runs 24/7, but I expect from time to time we will encounter access
issues, shifting IP addresses, or etc.; speak to me if you have issues
accessing the cluster.

The available storage is as follows:

* Each of the VMs has an independent 100 GiB local disk used to store the
  operating system and core software.
* All four VMs are also connected to a 1 TiB bulk storage virtual drive
  (JuiceFS running on Google Cloud Storage buckets), used for user files.

Software
--------

The cluster runs Ubuntu 22.04. I can install arbitrary software. Non-default
software installed already:

* neovim
* latex (texlive-full)
* latexmk
* ffmpeg
* pandoc
* entr
* uv

Let me know if you need something that isn't there. Except:

* For python, I recommend managing your own virtual environments in user-space
  with uv.
  * The system Python is 3.10, and uv can install can install other Python
    versions as needed (e.g. `uv venv --python 3.14`).
* For nodejs, I recommend installing nvm locally and managing your own
  installs.

The cluster supports both JAX and PyTorch/XLA for TPU workloads. See the setup
sections below for each framework.

Privacy and security
--------------------

I am an admin on all TPU nodes. **In principle, I can see anything you
do or store on the node, including e.g. GitHub keys, passwords, API
keys, and so on.** In practice, I won't hunt for these.

You and other users *don't* have admin rights, and can't access each
other's files unless you specifically set your permissions such that
they can.

Data safety
-----------

**Do not treat the cluster as permanent storage for valuable data.** I
sometimes have to destroy and recreate the cluster, and all data on the
VMs will be lost when this happens.

Only store things on the cluster if they can be easily regenerated or
if you have another copy elsewhere. For example:

* You can work on your code on the cluster, but remember to push to
  git regularly rather than leaving unpushed work there.
* If you have valuable experiment logs or results, copy them to your
  local machine after the experiments are done.

TODO: In future, I will implement ~daily backups of the shared storage part of
the cluster. But this is not yet live.

Creating an account
===================

To access the cluster, please follow these instructions:

1. Select a unique username.

2. Create an SSH key on your local device. You will use this SSH key to
   access the TPU VMs. I recommend using this command:

   ```
   ssh-keygen -t ed25519 -f ~/.ssh/mfr-tpu -C USERNAME
   ```

   where `USERNAME` is your username from step 1.

   You can skip the password prompts, should not be necessary. This
   will create two text files, `~/.ssh/mfr-tpu` and
   `~/.ssh/mfr-tpu.pub`. The former is your private key (don't share
   this) and the latter (.pub) is your public key (this is OK to
   share).

3. Send me your username and the contents of the `~/.ssh/mfr-tpu.pub`
   file.

At this point, you must wait for me to create your account on the TPU
VMs. I will do this and then let you know to proceed to the next step.


Getting started
===============

Accessing the VMs via SSH
-------------------------

The best way to access the VMs is via SSH. To configure SSH to easily
access the cluster, add the following lines to the text file
`~/.ssh/config`, where `USERNAME` is your username:

```
# MFR TPU Cluster
Host tpu0 tpu1 tpu2 tpu3
    IdentityFile ~/.ssh/mfr-tpu
    User USERNAME
Host tpu0
    HostName 35.186.109.61
Host tpu1
    HostName 35.186.93.73
Host tpu2
    HostName 173.255.125.97
Host tpu3
    HostName 35.186.140.33
```

After this, you can SSH into `tpuX` using the simple command `ssh tpuX`
where `X` is 0, 1, 2, or 3.

Note: These IP addresses might need to be updated from time to time. If
the login command suddenly stops working, let me know and I will double
check this.

### SSH between VMs

Your account is set up with intra-cluster SSH keys, so you can also SSH
between VMs directly. From any VM, just use:

```
ssh tpu2            # open a shell on tpu2
```

Working with files
------------------

There are several ways to transfer files to/from and edit files on the cluster.

1. **Copy files:** The most basic way to modify files on the server is to
   modify them locally and then copy files to the server. This is not an
   efficient workflow for editing code but it is sometimes very useful so I
   will explain how to do it. To copy files between your local machine and a VM
   you can use `scp` (secure copy), which transfers files over SSH. For
   example, to copy a file to `tpu0`:
   ```console
   scp myfile.py tpu0:~/myproject/
   ```
   Or to copy a file back from the cluster to your local machine:
   ```console
   scp tpu0:~/myproject/results.csv .
   ```

2. **Git + GitHub:** An easier way to copy a lot of files and keep them synced
   between your local environment and the VMs is to use `git` and GitHub. You
   can edit locally or on the VM, commit, push, and then pull where you want
   the code. More details on setting up GitHub authentication below.

3. **Terminal-based editors:** You can edit code directly on the cluster using
   a terminal-based editor. This can also be slow if you are not used to it but
   sometimes it is very useful so you should know how to do this too.

   The most beginner-friendly option is to use the program `nano`. It works
   just like a basic text editor but in the terminal. To start, use a command
   like `nano myfile.py`. To save the file, use command + O (`^O`). To exit,
   press command + X (`^X`). Other keybindings are displayed at the bottom of
   the screen.

   There is also `vim` and neovim `nvim` installed if you like, in which case
   you know what you are doing.

4. **Local VS Code + Remote SSH extension:** If you prefer a graphical editor,
   VS Code can connect directly to the cluster using the **Remote - SSH**
   extension. Once installed, it will use your `~/.ssh/config` to let you open
   files, edit, and run terminals on the VMs as if they were local.

   See the
    [VS Code Remote-SSH documentation](https://code.visualstudio.com/docs/remote/ssh)
   for setup instructions.

**Remember:** The cluster should not be treated as permanent storage for code
(see Data safety section above). If you develop code on the VMs, you should use
git and push to GitHub regularly. If you generate important files on the VM,
you should copy them to your local device or store them in git if they are not
too large.

Cluster storage
---------------

The good news is, your home directory `/storage/home/<username>` lives on the
shared filesystem, which means once you get your files to one of the VMs, they
will be accessible automatically from all of them.

However, there are some quirks:

* Changes aren't synced immediately. It can take a few seconds for small files
  to replicate across the cluster. Big files longer as they need to travel to
  GCS and back through the local network. Try to avoid assuming changes will be
  synced immediately.

* The underlying storage is a bit slow. The first time you want to access files
  from one of the VMs, it has to fetch those files from the local network,
  which is slower than using the boot disks. (In practice, this shouldn't be a
  big issue, because after the first time, the files will be cached---see the
  next point.)

* Most of the 100 GiB boot disk is configured to serve as a very fast cache for
  reads/writes. That means the first time you, e.g., load a big venv, it can
  take a minute (JAX) or 5 minutes (PyTorch) to load, but subsequent times it
  should be fast (unless the cache gets full, in which case you need to wait
  again).

  If you've just installed or updated a venv (or any other large directory)
  and you're about to launch a job on a different VM, you can pre-warm that
  VM's cache so the first run isn't slow:

  ```
  tpu-warmup -n tpuN ./venv      # warm ./venv on tpuN via SSH
  tpu-warmup ./venv              # warm on the current VM
  tpu-warmup --check ./venv      # just report whether it's cached
  ```

  Run `tpu-warmup -h` for the full options. Paths must be on `/storage`.

This is a new set-up and there might be teething issues---reach out if you
experience undue filesystem lag or other issues.

Setting up GitHub
-----------------

To authorise your account on the cluster you will have to configure each
VM with some SSH keys which you also add to GitHub. You can follow the
instructions
  [here](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent)
and then
  [here](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account)
on each VM. If you are happy to use keys without a passphrase, you can follow
the simpler steps below.

1. Generate an SSH key on one of the VMs:

   ```
   ssh-keygen -t ed25519 -f ~/.ssh/github -C "your@email"
   ```

2. Add the public key (`~/.ssh/github.pub`) to your GitHub account
   under Settings > SSH and GPG keys.

3. Add the following to `~/.ssh/config`:

   ```
   Host github.com
     User git
     IdentityFile ~/.ssh/github
     IdentitiesOnly yes
   ```

   Your home (and `.ssh/`) is shared across all 4 VMs, so this setup
   applies everywhere automatically.

Setting up a virtual environment
--------------------------------

You will need a Python virtual environment to install your framework
and other packages. Because your home directory is on shared storage,
a venv created once in your home is usable from all 4 VMs.

Remember to activate your venv (`source venv/bin/activate`) each time
you log in before running your code.

### JAX

Working with JAX on the cluster is just like working with JAX on a GPU or CPU.
You just need to install the correct version of JAX, namely `jax[tpu]` (rather
than `jax` or `jax[cuda12]`).

For example, these steps will create a Python virtual environment with JAX
installed.

```
uv venv venv
source venv/bin/activate
uv pip install "jax[tpu]"
```

For more detailed installation instructions, see the [JAX
documentation](https://docs.jax.dev/en/latest/installation.html).

### PyTorch/XLA

PyTorch doesn't natively support TPU accelerators. To run PyTorch models on the
TPUs, we need to install the [PyTorch/XLA](https://github.com/pytorch/xla)
library.

Last I checked, the library requires Python 3.8--3.11. The TPU VMs have Python
3.11 installed, so we can ask `uv` to use that version when creating the
virtual environment. PyTorch/XLA also works with a specific version of PyTorch,
so we need to request that version when we install it. For example:

```
uv venv venv --python 3.11
source venv/bin/activate
uv pip install torch==2.8.0 'torch_xla[tpu]==2.8.0'
```

For more detailed installation instructions and alternative options, see 
the [PyTorch/XLA repo](https://github.com/pytorch/xla).

**Note:** After installing PyTorch/XLA, you will also need to modify your
PyTorch training script to use it. See the official
  [getting started guide](https://github.com/pytorch/xla?tab=readme-ov-file#getting-started)
or [this tutorial of mine](https://far.in.net/tpu-go-brrr)
(the latter may be a bit out of date).

Using the TPUs
==============

Checking TPU status
-------------------

I set up a system so that everyone can see who is currently using the
TPUs. From any node, simply run the command `tpups` (short for "**tpu**
**p**rocesse**s**", like the standard `ps` utility). You will see output
that looks something like this:

```
NODE/DEV    STAT   USER    PID      TIME       COMMAND
--------------------------------------------------------------------------------
*tpu0/dev0  FREE   -       -        -          -
*tpu0/dev1  FREE   -       -        -          -
*tpu0/dev2  FREE   -       -        -          -
*tpu0/dev3  FREE   -       -        -          -
--------------------------------------------------------------------------------
 tpu1/dev0  BUSY   ross    1561589  01:06      python train_new.py --task-div...
 tpu1/dev1  BUSY   ross    1541262  18:31      python train_new.py --task-div...
 tpu1/dev2  BUSY   olly    1525067  32:22      /home/olly/slt-for-cl/.venv/bi...
 tpu1/dev3  BUSY   olly    1528223  30:57      /home/olly/slt-for-cl/.venv/bi...
--------------------------------------------------------------------------------
 tpu2/dev0  BUSY   matt    377715   3-12:24:58 venv/bin/python simplex2b.py -...
 tpu2/dev1  BUSY   matt    377715   3-12:24:58 venv/bin/python simplex2b.py -...
 tpu2/dev2  BUSY   matt    377715   3-12:24:58 venv/bin/python simplex2b.py -...
 tpu2/dev3  BUSY   matt    377715   3-12:24:58 venv/bin/python simplex2b.py -...
--------------------------------------------------------------------------------
 tpu3/dev0  BUSY   matt    349567   3-12:26:57 venv/bin/python simplex2b.py -...
 tpu3/dev1  BUSY   matt    349567   3-12:26:57 venv/bin/python simplex2b.py -...
 tpu3/dev2  BUSY   matt    349567   3-12:26:57 venv/bin/python simplex2b.py -...
 tpu3/dev3  BUSY   matt    349567   3-12:26:57 venv/bin/python simplex2b.py -...
--------------------------------------------------------------------------------
* = current node | Updates ago: tpu0:4s, tpu1:2s, tpu2:2s, tpu3:2s
```

Here you can see:

* User `matt` (that's me) is using all four devices on `tpu2` and `tpu3` (my
  jobs have been running for over 3 days). Users `ross` and `olly` are each
  using two devices on `tpu1`.
* You are calling `tpups` from `tpu0`, as indicated by the `*` next to those
  devices.
* All four devices on `tpu0` are free.
* This information was current as of a few seconds ago (the information
  refreshes every 5 seconds; if you see substantially longer times, this
  indicates an error, please let me know).

Two related tools:

* `tpu-usage` — shows daily TPU usage statistics and a leaderboard of
  who has been using the most chip-time.
* `tpu-heatmap` — shows a calendar heatmap of daily cluster utilisation.

I included these because I want you to feel encouraged to run a lot of
experiments. Let's keep the TPUs warm!

Hello, world!
-------------

Create a Python script called `hello.py` to verify your setup. Here are
examples for each framework.

JAX:
```python
# hello.py
import jax

print("platform:", jax.default_backend())
print("devices:", jax.devices())

x = jax.numpy.ones(3) + jax.numpy.ones(3)
print("computation: 1 + 1 =", x[0])

input("press enter to release devices...")
```

PyTorch/XLA:
```python
# hello.py
import torch
import torch_xla
import torch_xla.runtime as xr

print("platform:", xr.device_type())
print("devices:", torch_xla.core.xla_model.get_xla_supported_devices())

x = torch.ones(3, device=torch_xla.device()) + torch.ones(3, device=torch_xla.device())
print("computation: 1 + 1 =", x[0].item())

input("press enter to release devices...")
```

Remember to initialise your virtual environment following the instructions
above and make sure the venv is active.

Then, run the script:

```
python hello.py
```

By default, this will run on device 0 of the current VM. To run on a
different device (e.g. device 2), use the `tpu-device` wrapper:

```
tpu-device 2 python hello.py
```

Use `tpups` to check which devices are free before choosing one.

Choosing a TPU device
---------------------

Your shell is configured with default environment variables that target
device 0 on the current VM. This means you can run TPU programs
directly without any special prefix, and they will use device 0.

To use a different device, prefix your command with `tpu-device`:

```
tpu-device 2 python hello.py
```

If another user is already using device 0, your program will usually
crash immediately with an error (rather than hanging). Use `tpups` to
see which devices are free.

You can also run on CPU only (no TPU), which is useful for quick debugging or
testing, or when no TPU devices are free:

```
tpu-device cpu python hello.py
```

Policies
========

Sharing system resources
------------------------

The cluster is a shared resource between me and my students working on
research projects. There is currently no centralised job scheduling
system in place, allowing anyone to launch (queues of) jobs on any of
the free devices at any time, as well as using CPUs, memory, and
storage. Please be considerate with your usage. For example:

* Please don't run jobs on all 16 devices at once without my
  permission. I reserve the right to terminate your jobs to make space
  for higher priority stuff from other users, though I will usually
  discuss this with you first.

  Suggested policy: When running big sweeps, use at most 75% of the
  devices (12/16), so that there will be some left for people to test
  and debug their programs.

* Please be conscious of your program's memory and CPU utilisation.
  Programs running on devices on the same VM all share memory and CPUs.
  There are a lot of these resources and this is not normally a
  bottleneck, but it could be if the code is accidentally very
  inefficient.

* Disk space is limited. Please try not to store large artefacts or
  logs. PyTorch/XLA virtual environments are particularly large, let's try not
  to install too many of those.

To see what is currently happening on the TPUs, use standard tools like
`htop` and see the `tpups` section above for TPU-specific monitoring.

Reasonable uses
---------------

The TPUs are graciously provided by the
[TPU Research Cloud](https://sites.research.google/trc/about/) programme.
I am personally paying for other Google Cloud costs associated with the
cluster, including a small amount for data downloaded from the cluster.
If the Google Cloud bill trips over a certain threshold in a given month,
the cluster will be automatically shut down, which could be very
disruptive. Therefore:

* Try not to perform excessive outbound transfers (e.g. hundreds of GB)
  without talking to me first.

  Tools like Weights & Biases are fine for logging numeric metrics, but
  try not to upload excessive amounts of checkpoints, images, or videos
  during each training run.

* Using the cluster for things other than our research project is fine,
  but please ask me first.


Advanced
========

What is `tpu-device`?
---------------------

The cluster is configured so that the following environment variables are set
by default:

```
TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1
TPU_PROCESS_BOUNDS=1,1,1
TPU_VISIBLE_DEVICES=0
PJRT_DEVICE=TPU
```

When JAX or PyTorch/XLA launches, it checks these environment variables to
determine which parts of the cluster to target.

The `tpu-device` wrapper overrides these variables to target specific devices.
The wrapped command `tpu-device <DEVICE> <command>` is essentially equivalent
to:

```
TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1 TPU_PROCESS_BOUNDS=1,1,1 TPU_VISIBLE_DEVICES=<DEVICE> PJRT_DEVICE=TPU <command>
```

The `tpu-device cpu` option unsets the TPU variables and instead sets
`JAX_PLATFORMS=cpu` (for JAX) and `PJRT_DEVICE=CPU` (for PyTorch/XLA),
forcing both frameworks to use CPU only.

Putting `X=Y` before a command like this has the effect of setting environment
variable `X` to the value `Y` during the execution of this command.

You can also set these environment variables yourself if you prefer. For
example:

* Run commands like `export TPU_VISIBLE_DEVICES=2` etc., once per
  shell session.
* Add these `export` commands to your bash/zsh profile (the above defaults are
  set by adding these to the system-wide bash profile).
* Set them from within Python before you import JAX or PyTorch/XLA,
  using `os.environ`.

**Important:** If you customise your shell profile (`.bashrc`, `.zshrc`,
etc.), make sure you don't override these TPU environment variables.

Using multiple devices on one TPU VM
------------------------------------

The `tpu-device` wrapper also supports running on multiple devices at
once. You can use a pair of devices (`0,1` or `2,3`) or all four
devices on the current VM (`0,1,2,3`):

```
tpu-device 0,1 python hello.py       # use devices 0 and 1
tpu-device 2,3 python hello.py       # use devices 2 and 3
tpu-device 0,1,2,3 python hello.py   # use all four devices
```

Note that device pairs must be `0,1` or `2,3` — you can't use arbitrary
combinations like `0,2`.

If you want to actually use all four devices you would then need to write your
JAX code to use `jax.pmap`.

Using multiple devices on multiple TPU VMs
------------------------------------------

In principle it is also possible to run one command across multiple VMs,
however I haven't set this up before so don't know how to do it. If you need
this, feel free to look into it and try it (presuming the TPUs are free). You
will need to figure out the appropriate combination of environment variables
and override the defaults, which stop the TPUs from trying to talk to
each-other.

Customising your shell
----------------------

The default login shell is bash. You can switch to another installed shell if
you prefer. Custom shells installed:

* Zsh

Usually, to switch your login shell, you need sudo access or your password.
Both are disabled on this cluster. To change your shell (e.g. to zsh), I
recommend a user-space method. Namely, replace the default contents of your
.bashrc with something like this:

```bash
if [[ $- == *i* ]]; then
    # if this is an interactive session
    if command -v zsh >/dev/null 2>&1; then
        # if zsh is found, transform login shell into zsh
        export SHELL=$(command -v zsh)
        exec zsh -l
    else
        echo "Warning: zsh not found. Falling back to bash." >&2
    fi
fi
```

This method has the following benefits:

* As far as the user account system is concerned, your login shell is still
  bash; so you don't need root or a password to change that.
* Since your login shell is still bash, you pick up the default environment
  variables configured for bash before switching to zsh.
* This method technically also allows you to run any shell binary you have
  installed locally, so you don't even need my help to install a new shell.

If you want me to switch your actual login shell or install a new shell, I'm
still happy to do that. You can add the environment variables to your profile
yourself with something like this.

```bash
export TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1
export TPU_PROCESS_BOUNDS=1,1,1
export TPU_VISIBLE_DEVICES=0
export PJRT_DEVICE=TPU
```

Customising other tools
-----------------------

Most other changes are easier, make yourself at home via dotfiles like you
normally would. You can set up your path, default editor, etc., in your
profile. If you want me to install some missing standard software, just ask.

Graduating to your own cluster
------------------------------

If you need more compute, or want more space to run your own experiments
for other projects, or whatever, it's pretty easy to get access to your
own cluster just like mine through TPU Research Cloud.

It won't cost you anything for the first ~3 months because you will
likely get some free GCP starter credits if this is your first time
using GCP. After that, the TPU VMs will stay free and it will only cost
a little for things like outbound transfers.

Some resources:

* Google website: https://sites.research.google/trc/about/
* Step-by-step tutorial: https://github.com/ayaka14732/tpu-starter
* A related tutorial of mine: https://far.in.net/tpu-go-brrr

It helps to have a little sysadmin experience. I am happy to help where
I can if you have questions. You can also see `admin-handbook.md` in this repo for
notes on how I set up the current cluster.
