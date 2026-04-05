MFR's Tiny TPU Cluster
======================

Admin repo for Matt's 4-node TPU v4-32 cluster on Google Cloud,
allocated via the [TPU Research Cloud](https://sites.research.google/trc/about/)
programme. The cluster provides 16 TPU v4 devices across 4 VMs
(tpu0–tpu3), each with 240 vCPUs, 400 GiB RAM, and 100 GiB disk,
running Ubuntu 22.04 with uv and JAX.

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

Roadmap
-------

Backend stability improvements:

* [x] Heartbeat + HTTP server process supervision.
* [x] Disk space supervision [minimal via MOTD and poll tpu-health]
* [x] Investigate the SSH config byte bug... [can't reproduce]
* [x] Periodically backup history.csv
* [x] Healthagent OOM recurrence management
* [x] Configure internal cluster ips via /etc/hosts rather than ssh config,
  update scripts to use hostnames rather than ips
* [x] Fix security issues running shared scripts.
* [x] Streamline command blocks in admin handbook.

More ambitious:

* [ ] TPU queueing system: install or develop some simple system that makes it
  easier to launch large numbers of TPU job scripts for each user, and then
  they will automatically be launched then TPUs are free.
* [ ] Persistent and shared storage.
* [ ] More AI agents on the cluster

Much more ambitious:

* [ ] Command to run code on another TPU VM; all jobs can be managed from one
  VM.
* [ ] A single reprovisioning script to set up the entire TPU cluster
* [ ] Enable the use of pre-emptable TPU VMs
* [ ] Learn how to make full use of the TPU VMs for a single big training run.
