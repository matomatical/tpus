Security and performance review (April 2026)
============================================

Status: Completed. Six findings landed across two sessions (the other
session fixed S1 and S2; this session fixed S3, S4, S5, P2 in 844f5e2).
P1 deferred. Larger structural recommendations listed at the end.

Review by Claude Opus 4.7 [1m], prompted by MFR.

Scope
-----

Reviewed code and configs *deployed by this repo*: everything under
`shared-scripts/`, `conf/`, `admin-scripts/`, plus the deployment
patterns documented in `admin-handbook.md`. Threat model assumed is
the one stated in `CLAUDE.md`: clumsy users and loose AI agents, not
malicious insiders.

Explicitly out of scope this round:

* GCP perimeter (firewall rules, IAM, VPC).
* JuiceFS and Redis configuration beyond what this repo deploys.
* Resource isolation between users (cgroups, quotas, ulimits).
* Backup and disaster recovery.
* Third-party components (TPU runtime, `healthagent` docker container).
* `admin-scripts/bench-*.py`, `migrate-home.sh`, `fetch-logs.sh`.
* `home-stuff/` dotfiles distributed to users.

Methodology
-----------

Three Explore subagents in parallel — one each for the deployed/root-
execution surface, the admin/deploy surface, and performance — then
ground-truth verification against the live system before accepting any
finding. Several initial agent claims turned out wrong on inspection
(see "False positives" below); cross-checking is essential.

System-state assumptions verified at review time
------------------------------------------------

Re-check these if they could have changed since:

* `/proc` is mounted without `hidepid` → any user can read any other
  user's `/proc/<pid>/cmdline`. Drives both S1 and S4.
* `/etc/login.defs` has `HOME_MODE 0750`; all `/storage/home/*` directories
  are 0750 on disk. Drives the rejection of the "useradd creates 0755
  homes" agent claim.
* `tpu-heartbeat-web.service` binds `0.0.0.0:8080` with no auth — drives
  S4 (cross-VM argv exposure beyond what `ps` already does within a VM).

Findings and dispositions
-------------------------

Security findings, fixed in commit `844f5e2` (this session):

* **S3** — `admin-scripts/setup-cluster-keys.sh` had a chmod-follows-
  symlink TOCTOU through `/tmp/cluster` staging: `mv` runs as root, then
  `chmod 600` dereferences the destination symlink. **Fixed by deletion**
  (legacy script, no current users created via this path).
* **S4** — `tpu-heartbeat-web` publishes full job argv to all 4 VMs over
  HTTP. Within a single VM `ps` already exposed argv (no `hidepid`),
  but the HTTP endpoint extends that across VMs. **Mitigation: documented
  in `user-handbook.md`** ("don't pass secrets via argv; use env vars")
  rather than redacting the JSON, since the transparency of `tpups` is
  the design intent.
* **S5** — `admin-scripts/adduser-local.sh` was divergent from the
  canonical `adduser.sh` (used pre-`/storage` paths, looser umask
  ordering). **Fixed by deletion**; `users.md` confirms all current users
  were created via `adduser.sh`.

Security findings, fixed in a separate earlier session:

* **S1** — `tpu-health.py` invoked `redis-cli -h tpu0 -a $password ping`,
  putting the JuiceFS Redis password in `/proc/<pid>/cmdline` (world-
  readable on this system) for the duration of the call. Fixed via
  the `REDISCLI_AUTH` env var.
* **S2** — `admin-handbook.md` deploy loop used `install -m 755 ~/tpu-*.py`,
  whose glob included `tpu-health.py`. The file was correctly deployed
  as `0750 root:matt` *before* the review, but the next redeploy would
  have silently downgraded it. Fixed by splitting `tpu-health.py` onto
  its own `install -m 750 -o root -g matt` line.

Performance findings:

* **P1** — `tpups` (run by every SSH login as MOTD) does parallel HTTP
  fetches with a 2 s timeout per node, blocking the login shell up to
  2 s when any node is slow. **Deferred**; possible fix is a tmpfs cache
  populated by a timer with the live fetch as fallback. Matt to decide.
* **P2** — `tpu-heartbeat.py` spawned one `ps` subprocess per busy
  device every 5 s. **Fixed** (single `ps -p PID,PID,... -o pid=,user=,
  etime=,args=` per cycle, with the resulting line-per-pid mapped back to
  devices by pid). Verified `ps` deduplicates duplicate pids and exits
  non-zero only when *no* requested pid exists.

False positives worth recording
-------------------------------

So they aren't re-flagged next time:

* `adduser.sh` "authorized_keys TOCTOU" — `.ssh/` is created `0700`
  owned by the new user *before* `tee` writes the keys, so the brief
  umask-default file mode is unreachable by other users.
* "`useradd -m` creates 0755 homes" — Ubuntu 22.04's `HOME_MODE 0750` in
  `/etc/login.defs` makes the existing script correct on this system.
  A one-line `chmod 0750` after `useradd -m` would make adduser.sh
  enforce the invariant rather than rely on system config — defense-in-
  depth, not a current bug.
* `status.json` being world-readable on a single VM — by design (the
  whole point of `tpups` is showing who's using devices). The cross-VM
  HTTP exposure was the genuinely new surface (S4).

Defense-in-depth nits not addressed
-----------------------------------

Worth picking up in a future pass:

* **D1** - `tpu-heartbeat.service` and `tpu-heartbeat-web.service` run as root
  with no `User=`, `ProtectSystem=`, or `NoNewPrivileges=`. Could run as a
  system user with hardening directives.
* **D2** - `tpu-health.py` depends implicitly on a passwordless sudoers rule
  for `cat /etc/juicefs/redis.env` that isn't checked into this repo. If the
  rule drifts the Redis check silently `SKIP`s. Document the required sudoers
  entry in this repo, and consider making missing-sudo a loud `CRIT` for
  required checks rather than a quiet `SKIP`.

Recommendations for the next review
-----------------------------------

Ranked by likely yield against the stated threat model:

1. **Multi-tenant resource isolation** — highest-leverage gap. There
   are no cgroup limits, no `/storage` quotas, no `ulimits`. A single
   greedy user (or runaway agent) can exhaust 400 GiB RAM, all 240
   vCPUs, or fill JuiceFS for everyone. Concrete starting points:
   `user.slice`-level `MemoryHigh=` per user via systemd, `juicefs
   quota set --inodes/--capacity` per home directory, and ulimits via
   `/etc/security/limits.d/`.
2. **GCP perimeter audit** — outside this repo but in the admin's
   remit, and the most likely surface where an external attacker shows
   up. Check firewall rules (is port 8080 reachable outside the VPC?),
   IAM scope on the JuiceFS service account (bucket-only or project-
   wide?), and `ss -tnlp` for unexpected open ports.
3. **Deployment-drift audit** — S2 was an instance of a class: repo
   says X, live VMs say Y, next deploy quietly resets to X. A small
   script that asserts critical invariants on each VM (file modes,
   ownership, service status, mount points) and runs from cron or as
   part of `tpu-health` would catch this class generally.
4. **Disaster-recovery walk-through** — `user-handbook.md` already
   tells users not to treat the cluster as permanent storage, and the
   roadmap has `daily backups` open. Specific stress-test: if the Redis
   instance or the JuiceFS SA key is lost, can the GCS bucket be
   re-mounted from cold? A short "what would I do if X" exercise tends
   to surface gaps cheaply.
5. **Smaller untouched items** — `admin-scripts/bench-*.py`,
   `migrate-home.sh`, `fetch-logs.sh`, the `healthagent-restart.timer`
   flow, `home-stuff/` user dotfiles. None looked alarming on a glance
   but were not deep-dived.

How to re-run this review
-------------------------

What worked:

1. Three Explore subagents in parallel, one per surface area (deployed/
   root path, admin/deploy path, performance). Each agent prompt named
   the threat model and the specific files to consider.
2. Verify highest-severity claims against live state *before* writing up.
   Cheap commands like `ls -l`, `mount | grep proc`, `grep HOME_MODE
   /etc/login.defs`, and reading the actual referenced lines caught
   several agent over-claims.
3. Distinguish "by design" from "bug". The cluster's transparency
   features (`tpups` showing user and argv) trade privacy for
   accountability; that trade is often the right answer here.

MFR notes
---------

Thanks for the review! Here are some thoughts for next time:

* Would like to pick up D1 and D2 but not important enough for today.
* Multi-tenant resource isolation I'm on the fence, I don't want students to
  hit artificial limits, utilisation is low enough that at the moment there is
  no risk of crowding each other out.
* Rest of the next steps seem sensible to me, not high priority today.
