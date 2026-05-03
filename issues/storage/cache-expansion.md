JuiceFS cache expansion — follow-up
====================================

**RESOLVED 2026-05-03.** Bumped to 65 GiB uniform (`cache-size=66560`).
Below is the original planning note plus a resolution section at the
bottom describing what we actually found and did.

After the 2026-05-02 migration cleanup freed ~114 GiB across the cluster,
there's room to bump the JuiceFS cache budget. Plan for next session.

Current state (as of 2026-05-02)
--------------------------------

`Options=cache-size=40960` (40 GiB) in `conf/storage.mount`, uniform across
nodes. Live cache utilization:

| node | cache used | budget | disk used | disk free |
|------|-----------:|-------:|----------:|----------:|
| tpu0 | 28 G       | 40 G (70%) | 56 G  | 42 G      |
| tpu1 | 16 G       | 40 G (40%) | 43 G  | 55 G      |
| tpu2 | 32 G       | 40 G (**80%**) | 48 G | 50 G   |
| tpu3 | 16 G       | 40 G (40%) | 31 G  | 67 G      |

tpu2 is bumping its budget and likely evicting; tpu0 is warm; tpu1/tpu3
have headroom.

Open question — investigate first
---------------------------------

Non-cache disk on each node (= `disk used` − `cache used`):

- tpu0: 28 G
- tpu1: 27 G
- tpu2: 16 G
- tpu3: 15 G

tpu0/tpu1 carry ~12 G more non-cache state than tpu2/tpu3. Some of that
on tpu0 is Redis + the dashboard/heartbeat-web tmpfs and the JuiceFS
backups. tpu1's gap is less obvious. Want to understand it before
trusting our headroom analysis. Useful starting points:

```
sudo du -shx /var /opt /usr /root /home /tmp 2>/dev/null
sudo du -sh /var/lib /var/log /var/cache 2>/dev/null
sudo du -shx --max-depth=2 /var/lib 2>/dev/null
journalctl --disk-usage
```

…and same on tpu1 to compare.

Proposed change (pending the above)
-----------------------------------

Bump `cache-size` from 40 G → **60 G** uniformly. Headroom analysis
assuming non-cache stays at current levels:

| new budget | tpu0 free worst-case | tpu1 | tpu2 | tpu3 |
|-----------:|---------------------:|-----:|-----:|-----:|
| 50 G       | ~19 G                | ~20 G| ~31 G| ~32 G|
| **60 G**   | **~9 G**             | ~10 G| ~21 G| ~22 G|
| 65 G       | ~4 G                 | ~5 G | ~16 G| ~17 G|

If the tpu0/tpu1 non-cache investigation turns up something we can trim,
60 G stays comfortable. If not, 50 G is the safer pick.

Deployment recipe
-----------------

1. Edit `conf/storage.mount`: change `cache-size=40960` to `cache-size=61440`.
2. Per-node, in sequence (with `tpups` checks between):

   ```
   for t in 0 1 2 3; do
     scp conf/storage.mount tpu$t:
     ssh tpu$t 'sudo install -m 644 storage.mount /etc/systemd/system/storage.mount \
       && rm storage.mount \
       && sudo systemctl daemon-reload'
   done
   ```

3. To take the new size live without rebooting, remount each node
   sequentially:

   ```
   for t in 0 1 2 3; do
     # check no users on this node first via tpups; pause for any in-flight job
     ssh tpu$t 'sudo umount /storage && sudo mount /storage'
   done
   ```

   (Or do a `systemctl restart storage.mount` per node — same effect.)

4. JuiceFS doesn't pre-allocate; the cache just stops evicting until it
   reaches the new ceiling. No data migration needed.

5. `tpu-health` should still show `mount` as `mounted` and `cache` rows
   should converge upward over the next training day.

Notes
-----

- Remount briefly disrupts any user with files open under `/storage` on
  that node. Use `tpups` to spot in-flight work and pause if needed.
- The four nodes share the same Redis metadata, so remounting one at a
  time is safe (other nodes keep serving).
- If we want asymmetric per-node budgets (e.g. higher on tpu3), that
  needs per-node drop-ins under `/etc/systemd/system/storage.mount.d/`
  rather than editing the shared unit. Probably overkill — uniform is
  cleaner unless there's a real reason.

Resolution (2026-05-03)
-----------------------

Investigated the tpu0/tpu1 vs tpu2/tpu3 non-cache gap before bumping;
the picture is now substantially different from the table at the top.

Findings:

- The 12 G non-cache gap was real but explained by three separate
  things, not one: ~6 G of LaTeX (`/usr/share/{texlive,doc,texmf}`)
  installed only on tpu0/tpu1, ~6 G of legacy `/home/matt` left on
  tpu1 from before the storage migration, and ~3 G of admin-only state
  on tpu0 (Redis, /tmp TPU-runtime rotated logs, larger /root).
- Discovered an unrelated non-cache leak in the meantime: Docker's
  json-file driver had no log rotation, so `instance_agent` and
  `google-runtime-monitor` containers had been writing API-mismatch
  and TF-assumption noise to `/var/lib/docker/containers/*/*-json.log`
  at ~70 MB/day combined for ~80 days, totaling ~5.5 G on tpu0/tpu2
  and ~3 G on tpu1/tpu3. Capped via logrotate copytruncate plus
  `daemon.json` log-opts (commit `d74f718`).
- texlive-full's context postinst was hung interactively on tpu2/tpu3
  on a missing `socket.core` Lua module, and tpu0/tpu1 turned out to
  be silently in the same broken state from their original install.
  Fixed cluster-wide by installing `lua-socket` plus symlinking it
  under `/usr/local/.../5.3/` to match texlua's hardcoded cpath
  (commit `e9f53df`).
- After the cleanup pass (docker truncate, /home/matt on tpu1,
  texlive-full on tpu2/3, apt-get clean cluster-wide), the residual
  non-cache spread was much narrower: tpu0 22 G, tpu1 19 G,
  tpu2 17 G, tpu3 18 G. tpu0 stays the highest because of structural
  admin extras (Redis, matt's local home, accumulated /tmp).

Decision: 65 G uniform (`cache-size=66560`), giving 10–13 G of free
disk headroom on every node post-bump. Deployed via the recipe above.

`tpups` lesson learned: it only reports TPU device usage, not /storage
activity. tpu0's storage.mount restart hit `umount: target is busy`
because of 65+ orphaned dafang processes (multiprocessing workers and
`orted` daemons, parent PID 1 since their launcher had died) plus a
forgotten `mfr` http.server, all keeping /storage open. Before any
storage.mount restart, also check `sudo fuser -m /storage` and `who`,
not just `tpups`. See admin-handbook §"Trouble: /storage target is
busy".
