Sharing uv cache across users
==============================

Status: Not pursuing (JuiceFS will solve the underlying disk problem).

Problem
-------

PyTorch/XLA installs pull ~7 GiB of CUDA dependencies. Each user's uv cache
stores a separate copy. With 97 GiB boot disks (~32 GiB free) and potentially
several PyTorch users, this could exhaust disk space.


Within a single user, deduplication already works
-------------------------------------------------

uv uses hardlinks by default on ext4. Two venvs installing the same
`torch==2.8.0` share the same cache files (same inodes). The second install
takes <1 second and uses ~0 extra disk. Confirmed by checking inodes with
`stat -c '%i'` and verifying `df` didn't change.

With `link-mode = "symlink"`, a venv is only ~74 MiB of actual disk (directory
structure + symlinks) vs 7.1 GiB apparent.

However, this is only per-user using the default cache `~/.cache/uv`. So
duplication across users exists.

Approach 1: Shared cache
------------------------

Basic idea: Move cache to a global folder with r/w permissions for a new `uv`
user group; globally configure uv to use this folder as the cache.

The naive approach (shared writable cache directory with a common group) is
what the uv community recommends (see astral-sh/uv#5611). However, it means
any user can modify cached package files that get symlinked into other users'
venvs -- a cache poisoning vector.

`protected_hardlinks = 1` on Linux also prevents users from hardlinking to
files they don't own, ruling out the simplest sharing mechanisms.

Another con: Couples venvs, if someone tweaks underlying python files then all
users are affected.

**Why we're not pursuing it:** This violates our security invariants
(admin-handbook.md: non-admin users have no sudo, can't access each other's
files). The shared writable cache would be the first place where one user could
tamper with another's runtime environment.


Approach 2: Tiered cache approach
---------------------------------

We prototyped a tiered approach:

1. Admin creates a read-only shared cache at `/home/shared/uv-cache/`
   (root-owned, `chmod -R a+rX,a-w`).
2. For each user, admin runs `cp -al` (hardlink copy) from the shared cache
   into the user's `~/.cache/uv/`. This creates real directories (so uv's
   rename/install logic works) but the actual files are hardlinks to the
   root-owned originals (same inodes, shared disk blocks).
3. RECORD files (in each package's `.dist-info/`) must be replaced with
   user-owned copies rather than hardlinks, because uv needs write access to
   them during installation. These files are tiny (<2 KiB each).
4. Container directories (`archive-v0/`, `wheels-v6/pypi/`, etc.) are made
   user-writable so the user can install new packages beyond the shared set.
5. Inner package directories remain read-only -- users can't modify shared
   package content.

This was confirmed working: torch installs from the shared cache in ~1 second,
new packages (e.g., cowsay) install normally into the user's writable portion,
and users cannot modify the root-owned shared files.


**Why we're not pursuing it:** The approach depends on uv's internal cache
format (`archive-v0/`, `wheels-v6/`, `simple-v20/`, RECORD files), which is
undocumented and could change with any uv version bump. The permission setup
(hardlink everything, copy RECORD files, chown directories but not files,
chmod specific container dirs) is fiddly. And the underlying disk space problem
will be solved by JuiceFS shared storage (see storage-options.md), making this
unnecessary.

Notes
-----

### uv cache structure

uv's package cache lives at `~/.cache/uv/` and has three key parts:

* `archive-v0/<hash>/` -- unpacked package files, content-addressed by an
  opaque hash. This is where the bulk of the data lives (~7 GiB for a
  PyTorch/XLA install). When `link-mode = "symlink"`, every file in a venv's
  site-packages is a symlink into one of these directories.
* `wheels-v6/pypi/<package>/<version>/` -- wheel metadata (small `.msgpack`
  and `.http` files, ~500 KiB total). Files are hardlinked to `archive-v0/`.
* `simple-v20/pypi/<package>.rkyv` -- package index data, one `.rkyv` file
  per package.

### Alternative approaches considered

* **Symlinked cache entries** (symlinking individual `archive-v0/<hash>/`
  directories from user cache to shared cache): failed with `ENOTDIR` errors
  because uv's install process uses atomic renames that don't work when
  archive entries are symlinks to directories.
* **Read-only shared venv + `.pth` files**: secure and disk-efficient, but uv
  doesn't know about the shared packages, so dependency resolution would try
  to redundantly install them.
* **`/etc/uv/uv.toml` for system-wide config**: a clean way to set
  `cache-dir` and `link-mode` globally without environment variables (noted in
  astral-sh/uv#5611), but doesn't solve the security problem.

Decision
--------

Not implementing shared uv cache. JuiceFS shared storage will give ~1 TB
across all VMs, making per-user 7 GiB PyTorch caches trivial. The uv
`link-mode = "symlink"` setting with `/etc/uv/uv.toml` may still be useful on
JuiceFS to avoid duplicating cache contents into each venv, but that's a
simpler problem to solve once shared storage exists.
