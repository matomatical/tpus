Cross-node logging — design input for queueing system
======================================================

Notes captured 2026-05-08 during a JuiceFS triage that motivated thinking
about what a future TPU job-queueing system needs to do for stdout/stderr.
Not a design — input to one.

Context
-------

mfr is running multi-node training sweeps via a hand-rolled
`tpu_queue.py` that SSHes from tpu0 to a worker VM and launches a
training job there with shell-redirected stdout/stderr to a log file
on `/storage`:

```
nohup bash -lc '<full_cmd>' > '<log_prefix>.log' 2>&1 &
```

The `>` is interpreted by the **remote** bash, so the writer of the
`.log` file is the worker node (tpu1/2/3); tpu0 is purely a reader
when someone `tail`s a log to inspect a running job.

This produces a benign-but-noisy class of errors that surfaces in
`tpu-health` as `GCS errors` climbing into the thousands (4463 on
tpu0 by the time of the triage; 5616 in a single day on a previous
sweep). Diagnosing it clarified what a queueing system needs to do
for logs.

The cross-node read race
------------------------

JuiceFS splits a write into two halves on different timelines:

1. **Metadata commit** to Redis happens immediately when the writer's
   mount accepts the write — slices are visible cluster-wide as soon
   as they're committed to metadata.
2. **Data upload** to GCS is deferred by `upload-delay` (1s in our
   config) — the slice sits in the writer's local rawstaging dir
   during the delay window.

A reader on a *different* node walks metadata in Redis, sees a slice
ID, GETs from GCS, and 404s if the slice is still inside the writer's
upload-delay window. JuiceFS retries with backoff and the read almost
always eventually succeeds — but each failed attempt logs a
`WARNING: ... storage: object doesn't exist` and increments the
`juicefs_object_request_errors` counter. Reads on the *writer* node
hit local block cache and never see the race.

So the metric is essentially a "retries-fired" counter, not a
"reads-failed" counter. Data integrity is fine — `rawstaging` stays
near zero, no upload errors, no EIO surfaces to user processes. The
metric is just loud.

Why training stdout is the perfect storm
----------------------------------------

The combination of `python -u` + tqdm + a `.log` on `/storage` is the
worst case for this race:

* `-u` defeats Python's stdout block-buffering, so every `write()`
  hits the kernel immediately.
* tqdm rewrites the progress bar ~10×/sec via `\r`-terminated lines
  and calls `flush()` itself.
* Each flush becomes a tiny slice (~59-byte chunks were typical in
  this incident) committed to Redis at full rate.
* When you `cat`/`tail -f` from tpu0, JuiceFS reads every slice in
  order. Slices committed within the last ~1s 404 on the first GET,
  retry, and eventually succeed.

A single `cat` of an actively-written sweep log can fire thousands
of retries. Nine concurrent sweep jobs amplify this. The 5616
warnings in a day on 2026-05-05 were probably one or two manual
`cat`/`tail` rounds across that day's sweep.

Implications for queueing-system design
---------------------------------------

The right cleavage is to **separate the live monitoring stream from
the persistent archive**. Training stdout/stderr is two distinct
things conflated:

1. A **live stream** users want to inspect mid-run ("what's the
   loss now? did it crash?"). Latency-sensitive, throughput-trivial,
   ephemeral.
2. A **post-mortem archive** ("what did this job print before it
   finished?"). Latency-irrelevant, written once at the end,
   permanent.

JuiceFS is great at (2) and bad at (1). The current setup uses (2)
to serve (1), which is what generates the noise.

Sketch of what a queueing system could do:

* Capture worker stdout/stderr to a **local file on the worker node**
  (e.g. `/var/log/jobs/<id>.log` or `/dev/shm/jobs/<id>.log`), not
  `/storage`.
* Run a small per-node agent that exposes the live tail (HTTP
  endpoint, redis pubsub, or an SSH-stream pull) so tpu0 can answer
  inspection queries without touching `/storage`.
* Persist the final log to `/storage` at job completion as a single
  appendput — one big slice, no race.
* Keep the structured metrics file (`.jsonl` per-step) on `/storage`
  during the run. Line-oriented JSONL appends with normal Python
  buffering produce far fewer, larger slices than tqdm spam, so they
  don't trigger the same race meaningfully.

Feature brain-dump (Matt, 2026-05-08)
-------------------------------------

Park these for the design phase, not for resolution now:

* **Cluster-wide "last line" view.** A command (`tpu-jobs` or
  similar) that shows the last stdout/stderr line(s) of every
  current and completed job — optionally filtered to the calling
  user — as a rapid at-a-glance state summary. Training scripts
  already emit useful state via tqdm or per-step `print(loss)`, and
  errors fall out naturally as the last line on a crash, so the
  signal is essentially free if the plumbing exists.
* **`\r`-aware "last line" extraction.** Naively `tail -n 1` on a
  tqdm-spammed log returns the whole file as one logical line. The
  viewer needs to treat `\r` as a logical line break so the
  displayed "last line" is the most recent progress-bar update, not
  the entire history. Similar handling may apply to ANSI
  cursor-movement escapes if any scripts use them.
* **Composability.** Both features sit naturally on the per-node
  agent above: each agent already has the local live log; the
  aggregator on tpu0 just polls last-logical-line from each agent.

Stop-gap (no new infra needed)
------------------------------

Two changes would suppress most current noise without building the
queue:

1. Document the antipattern in `user-handbook.md` — tqdm progress +
   `/storage` is bad. Recommend either inspecting from the worker
   node (`ssh tpuN tail -f ...`) or redirecting stdout to a per-node
   `/tmp` log.
2. (Optional, user-side) Have `tpu_queue.py` redirect stdout to a
   per-node `/tmp/jobs/<id>.log` and rsync to `/storage` at job
   completion. Single-script change on the user side.

Neither is urgent — the issue is cosmetic in `tpu-health` and not
actually breaking anything.
