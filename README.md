MFR's Tiny TPU Cluster
======================

Admin repo for Matt's 4-node TPU v4-32 cluster on Google Cloud,
allocated via the [TPU Research Cloud](https://sites.research.google/trc/about/)
programme. The cluster provides 16 TPU v4 devices across 4 VMs
(tpu0–tpu3), each with 240 vCPUs, 400 GiB RAM, and 100 GiB disk,
running Ubuntu 22.04 with Python 3.14 and JAX.

Handbooks
---------

* **[User handbook](user-handbook.md)** — for students and researchers using
  the cluster. Covers access, setup, running JAX on TPUs, and cluster policies.
* **[Admin handbook](admin-handbook.md)** — provisioning, configuration, and
  maintenance notes.

Repo contents
-------------

* `admin-scripts/` — scripts for admin use (adduser, etc.)
* `shared-scripts/` — scripts deployed to `/home/shared/` on each VM
  (`tpu-device`, `tpups`, `tpu-usage`, `tpu-heatmap`, `tpu-heartbeat`)
* `conf/` — config files to deploy to VMs (logrotate, etc.)
* `home-stuff/` — dotfiles to deploy to VMs
* `issues/` — bug reports for TPU VM image issues
* `users.md` — cluster user info

TODO
----

* [x] Set up intra-cluster SSH for all users (see `issues/inter-vm-ssh/plan.md`)
* [x] Instructions for making TPU VMs work with pytorch
* [x] Make tpu-device optional by default
* [ ] TPU queueing system: install or develop some simple system that makes it
  easier to launch large numbers of TPU job scripts for each user, and then
  they will automatically be launched then TPUs are free.
