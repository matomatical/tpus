Migration audit and cleanup — 2026-05-02
=========================================

One-time audit + cleanup of `/home/<u>` per-node backups left behind by
the JuiceFS shared-storage migration (2026-04-24). Done for `afiq`,
`olly`, `ross`; the other migrated users had already been cleaned up.

Outcome: all `/home/<u>` backups removed across all 4 nodes (~114 GiB
freed), uv-cache and venv-interpreter symlinks in the merged
`/storage/home/<u>` tree repointed from `/home/<u>/...` to
`/storage/home/<u>/...` so venvs continue to work after deletion.

Findings worth keeping
----------------------

- **uv stores absolute symlink targets.** Both the wheel cache
  (`~/.cache/uv/wheels-v6/<pkg> -> ~/.cache/uv/archive-v0/<hash>`) and
  the venv interpreter (`<venv>/bin/python ->
  ~/.local/share/uv/python/<cpython>/bin/python3.X`) used absolute
  paths. After `usermod -d` and the migration rsync, the *targets*
  still pointed at `/home/<u>/...`. Deleting `/home/<u>` would have
  broken every venv silently. The fix was to walk
  `/storage/home/<u>` for `-lname '/home/<u>/*'` symlinks and rewrite
  each, scope-by-scope (`fix-symlinks.py`).

- **ross's absolute-path muscle memory.** ross had `cd
  /home/ross/projects/myproject` written down and was typing it from a
  fresh `/storage` shell. The directory still existed (per-node
  backup), `cd` succeeded, his venv activated, his plot script's CWD
  ended up under `/home`, and outputs landed there. Five PDFs/.py
  files were rsync'd back into `/storage` before the deletion.

- **vscode-server self-purges old SHAs.** ~2700 of ross's "missing"
  files were a single VS Code server install (`Stable-591199df...`)
  that vscode purged from `/storage` post-migration when newer
  releases replaced it — not a migration failure.

- **`-maxdepth` in find(1) is a global option, not a local condition.**
  Putting it inside a `\(... -prune \)` branch silently caps the
  *whole* search depth. Use `-path /literal/path` for branch-local
  matches.

Files
-----

- `migrate-audit.py` — per-user, per-node diff between `/home/<u>` and
  the corresponding `/storage` location. Reports missing-from-storage
  and `/storage` mtime < `/home` mtime.
- `migrate-audit.out` — first run; `dst=9` for tpu0 was the
  `-maxdepth` bug, not real.
- `ross-tpu0-detail.py` / `.out` — bucket ross's missing files by
  pre/post-migration mtime and by top-level dir.
- `ross-projects-detail.py` / `.out` — drilled into `projects/` to
  expose the `Old Stuff/` reorg + 5 post-migration writes.
- `ross-content-check.py` / `.out` — content-based cross-check (size
  + mtime tuple) confirmed the 2645 "truly missing" were vscode-purged
  and only 2 files needed reconciliation on tpu0.
- `fix-symlinks.py` — generic symlink-target rewriter (merged tree by
  default; `--include-backups` for tpuN/ subtrees too). Works for any
  user.
