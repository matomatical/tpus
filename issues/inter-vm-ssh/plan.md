Inter-VM SSH for all users
==========================

Goal: Allow all users to SSH between tpu0-tpu3 as themselves, without
sharing admin keys.

Approach
--------

1. System-wide SSH client config on each VM.

   Create `/etc/ssh/ssh_config.d/cluster.conf` on each VM with the
   internal IP mappings:

   ```
   Host tpu0
       HostName 10.130.0.12
   Host tpu1
       HostName 10.130.0.11
   Host tpu2
       HostName 10.130.0.10
   Host tpu3
       HostName 10.130.0.13
   ```

   This gives all users the `tpu0`-`tpu3` aliases automatically,
   without needing to edit their own `~/.ssh/config`.

2. Per-user intra-cluster key pair, generated during account creation.

   Extend `adduser.sh` to:
   - Generate an ed25519 key pair for the user (e.g.
     `~/.ssh/cluster` / `~/.ssh/cluster.pub`)
   - Add the public key to `~/.ssh/authorized_keys` on all 4 VMs
   - Add an IdentityFile line to the user's `~/.ssh/config` on all
     4 VMs (or to the system-wide config if preferred)

   Each user's key only authenticates as their own user, so there is
   no privilege escalation risk.

3. Update the handbook to tell users they can `ssh tpuX` and
   `scp file tpuX:path` between VMs.

Tasks
-----

- [ ] Create `/etc/ssh/ssh_config.d/cluster.conf` on each VM
- [ ] Extend `adduser.sh` to generate and distribute intra-cluster keys
- [ ] Test with an existing non-admin user
- [ ] Update handbook
