# SSH config "first byte ignored" — actually OpenSSH strict-modes

## TL;DR

When `~/.ssh/config` is group- or world-writable (mode `0664`, `0666`,
etc.), OpenSSH 8.9p1 (Ubuntu `1:8.9p1-3ubuntu0.15`) **silently drops the
first byte of the file** before parsing, instead of aborting with the
expected "Bad owner or permissions" error. The result is a confusing
parse error on line 1, e.g. `Bad configuration option: ost` from a file
whose first line really is `Host github.com`.

Fix: `chmod 0600 ~/.ssh/config`.

This had been documented in the admin handbook for months as "weird
first-byte bug, leading blank line workaround" and we couldn't reproduce
it reliably because all our admin testing happened on `~/.ssh/config`
files that already had tight modes. A user (`mfr`) hit it freshly when
creating `~/.ssh/config` for GitHub access; their editor's umask of
`0002` (Debian/Ubuntu user-private-group default) yielded mode `0664`,
which trips the bug deterministically.

## Repro

On any Ubuntu 22.04 box with `openssh-client 1:8.9p1-3ubuntu0.15`:

```sh
cp ~/.ssh/config ~/.ssh/config.bak
chmod 0664 ~/.ssh/config
ssh -G github.com 2>&1 | head -3
# /home/<u>/.ssh/config: line 1: Bad configuration option: <token-without-first-byte>
# /home/<u>/.ssh/config: terminating, 1 bad configuration options
chmod 0600 ~/.ssh/config
ssh -G github.com 2>&1 | head -3
# user git
# hostname github.com
# port 22
rm ~/.ssh/config.bak
```

The "missing-byte" token depends on the file's first line:

| First line of config | Reported bad token |
|----------------------|--------------------|
| `Host github.com`    | `ost`              |
| `# Hello, world`     | `hello,`           |

Worth noting: ssh's `-vv` produces no warning about the mode. There is
no "Bad ownership or modes for file" message anywhere — just the
mis-parsed line.

## Investigation

Symptom reported by `mfr`:

```
mfr@tpu0 $ git push
/storage/home/mfr/.ssh/config: line 1: Bad configuration option: ost
/storage/home/mfr/.ssh/config: terminating, 1 bad configuration options
fatal: Could not read from remote repository.
```

### Variables we ruled out

1. **File contents.** `od -c ~/.ssh/config` showed the file genuinely
   starting with `H o s t   g i t h u b . c o m \n`. No BOM, no `\r`,
   no leading whitespace, no junk byte.
2. **JuiceFS / `/storage`.** Reproduced on plain ext4 (`/home/matt/`)
   under matt's account just by `chmod 0664`. The original "first-byte
   bug" report in the handbook predated JuiceFS, which is consistent.
3. **VM image / kernel.** Reproduces in a clean shell under any user
   account on the cluster. The OpenSSH version (`8.9p1 Ubuntu-3ubuntu0.15`)
   is stock Ubuntu 22.04, not a TRC-specific build.
4. **Git's invocation.** `ssh -G github.com` (no `-F`, no git in the
   loop) reproduces by itself. Conversely, `ssh -F ~/.ssh/config -G
   github.com` parses correctly even with mode `0664`, because `-F`
   skips the strict-modes check entirely (`SSHCONF_CHECKPERM` is only
   set on the default-config code path).

### Variable that mattered

**File mode.** `0600` works; `0664` (or anything with `(mode & 022) != 0`)
fails. Switching only the mode flips the behaviour, on the same file,
in the same shell, on either filesystem, under either user account. So
the bug lives in OpenSSH's strict-modes path, not in the parser, the
read path, or anywhere else.

OpenSSH's documented behaviour for an over-permissive `~/.ssh/config`
is to `fatal()` with `Bad owner or permissions on <path>` (see
`readconf.c`, gated by `SSHCONF_CHECKPERM`). Empirically, what 8.9p1
Ubuntu actually does on this VM is: silently advance one byte, then
parse — no abort, no warning.

## Root cause: nss_systemd leaks a stray read onto fd 3

Tracing further confirmed the byte loss is **not** in OpenSSH itself.
It comes from glibc's NSS chain when Debian's `secure_permissions()`
patch calls `getpwent()`:

1. Debian's `user-group-modes.patch` (Colin Watson, 2014, adopted to
   support user-private-group `umask 002`) replaces the strict
   `(mode & 022) != 0` check with a `secure_permissions()` function
   that **calls `getpwent()`** to enumerate users in the file's group
   — to permit group-writable files where the group has only the
   owner as a member.
2. `getpwent()` walks every NSS source listed in
   `/etc/nsswitch.conf`. On Ubuntu 22.04 the default is
   `passwd: files systemd`, so after `/etc/passwd` is exhausted glibc
   loads `libnss_systemd.so.2` to enumerate systemd's user database.
3. `nss_systemd` connects to `/run/systemd/userdb/io.systemd.DynamicUser`
   over a unix socket, sends a `io.systemd.UserDatabase.GetUserRecord`
   JSON-RPC, reads `/proc/sys/kernel/random/boot_id`, sets up an
   epoll, walks several userdb directories, etc.
4. **Somewhere in that nss_systemd / libc / userdb / varlink pipeline,
   a stray 1-byte `read(fd, ?, 1)` syscall lands on fd 3** — which is
   the user's `~/.ssh/config` (ssh `fopen`'d it just before the
   permission check).
5. ssh's subsequent `getline()` on the same FILE\* therefore starts at
   offset 1, and the parser reports a bad keyword on line 1.

`strace` proof, abbreviated, shows the read appearing only after
nss_systemd has been loaded and the userdb queries have run:

```
openat("/home/matt/.ssh/config")            = 3
fstat(3, mode=0664) ; getuid()              # secure_permissions begins
openat("/etc/group")  = 4 ; read; close     # getgrgid()
openat("/etc/passwd") = 4 ; read 2405 bytes # getpwent enumerates files
openat("libnss_systemd.so.2") ...           # NSS loads systemd module
connect("/run/systemd/userdb/io.systemd.DynamicUser")
sendto("...UserDatabase.GetUserRecord...")
read("/proc/sys/kernel/random/boot_id")
... systemd userdb directory walks, epoll, mincore ...
read(3, "#", 1)        = 1                  # ← THE STRAY READ
read(3, " Hello, ...", 4096) = 121          # getline starts at offset 1
```

Confirmation: editing `/etc/nsswitch.conf` to drop `systemd` from the
`passwd` and `group` lines makes the bug disappear immediately, even
with mode `0664`. Restoring the original line brings it back.

So this is a **`nss_systemd` (or NSS-framework) bug** that only
manifests through OpenSSH on Debian/Ubuntu because the Debian-only
patch happens to call `getpwent()` from inside ssh's strict-modes
path. Upstream OpenSSH would `fatal()` cleanly without ever entering
the NSS chain.

## Deeper: it's actually libunwind + tcmalloc

`strace -k` (with stack backtraces) at the stray read reveals that
nss_systemd is not directly responsible. The frame stack is:

```
read+0x12                                         (libc — the syscall)
_ULx86_64_step                                    (libunwind — stack walk)
_ULx86_64_get_reg
... (more libunwind)
GetStackTrace                                     (tcmalloc captures a backtrace)
tcmalloc::PageHeap::GrowHeap                      (tcmalloc grows the heap)
tcmalloc::CentralFreeList::Populate
... (more tcmalloc)
tc_realloc                                        (tcmalloc allocator)
_nss_systemd_setsgent+0x2d82                      (nss_systemd allocates a buffer)
... (more nss_systemd internal)
_nss_systemd_getpwent_r                           (NSS-systemd entry)
__nss_lookup_function                             (libc NSS dispatcher)
getpwent_r                                        (the call from secure_permissions)
```

So nss_systemd just happens to be the thing requesting heap memory at
this moment. The actual stray `read()` comes from **libunwind**, called
from **tcmalloc's `GetStackTrace()`** during heap growth. The pattern
is a textbook **stale-fd-cache after fd reuse**: libunwind cached an fd
to (most likely) `/proc/self/maps` earlier in the program, that fd got
closed and the number recycled to ssh's `~/.ssh/config`, and then
libunwind blindly does `read(cached_fd, ?, 1)` — which now reads from
ssh's config.

## Why only on the TPU VMs

The TRC TPU VM image bakes `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libtcmalloc.so.4`
into `/etc/environment` (mtime 2023-05, present on all 4 nodes,
predates anything we set up). Google ships this so that JAX / libtpu
gets the tcmalloc allocator system-wide. As a side effect, **every
process** on the TPU VMs runs with tcmalloc and libunwind injected.

On a stock Ubuntu 22.04 box, `LD_PRELOAD` is empty, ssh links against
glibc's plain malloc, no `GetStackTrace()` happens during `getpwent()`,
and the bug never fires — even with a 0664 `~/.ssh/config`. We
confirmed by running `LD_PRELOAD= ssh -G github.com` on a 0664 config:
parses cleanly, exit 0.

So the manifestation requires three pieces to stack up:

1. `LD_PRELOAD=libtcmalloc.so.4` (TRC VM image default).
2. Debian's `user-group-modes.patch` adding the `getpwent()` call to
   ssh's strict-modes path (Debian/Ubuntu OpenSSH default).
3. `~/.ssh/config` mode `0664` so the patch enters the `getpwent()`
   branch (Debian/Ubuntu user-private-group umask default).

Remove any one and the bug doesn't surface. Most user files on a TPU
VM trip (1) and (2) automatically — only (3) is under user control,
which is why mode 0600 is the practical fix.

## Broader implication for the cluster

The mechanism is a stale fd cache in libunwind, not specific to ssh.
*Any* program on the TPU VMs that:

- opens a small-numbered fd,
- then triggers a tcmalloc heap growth that calls `GetStackTrace()`,

will silently consume a byte from that fd. Most programs won't notice
because they don't expect to read from offset 0 of a freshly-opened
file the way ssh's `getline()` does. But there could be other
manifestations lurking. Worth keeping in mind when debugging
mysterious "first byte missing" symptoms anywhere on the cluster.

## Why we never reproduced it before

The handbook entry from earlier this year tried `od -c` on the file
(found it clean), guessed at a TPU-image OpenSSH bug, and prescribed
"start config with a leading blank line". That was a *partial*
workaround — if the eaten byte is `\n`, the file still parses cleanly —
but it misidentified the cause. Our deployed `/etc/ssh/ssh_config` was
edited at the time to start with a leading blank line; that was
gratuitous (system configs don't go through `SSHCONF_CHECKPERM`) but
also harmless, so it survived.

When I (matt) couldn't reproduce in April, it was because my
`~/.ssh/config` was already at `0644` from earlier deliberate
hardening. The bug only fires on group/other-writable. With strict
modes, the entire chain stays cold.

## What we changed

1. **`adduser.sh`** now pre-seeds `~/.ssh/config` at mode `0600` so
   that subsequent edits inherit the mode (most editors preserve mode
   on save), and new users don't trip the bug on day one.
2. **`admin-handbook.md`** "Trouble: Bad configuration option" entry
   rewritten with the real cause and one-line fix.
3. **`user-handbook.md`** SSH setup section now tells the user to
   `chmod 600 ~/.ssh/config` after creating it on their laptop, with a
   one-liner explaining why.
4. **Cluster audit:** of the 9 user accounts on `/storage/home/`,
   only `mfr` had the loose-mode config (now fixed). `dafang` already
   had `0600`. The other 7 don't have a `~/.ssh/config` on the cluster
   (they only need one for things like cluster-side GitHub access).
5. **Memory** updated to reflect the real cause.

The vestigial leading blank line in deployed `/etc/ssh/ssh_config` was
left in place. It's harmless and removing it would mean re-touching
`/etc/` on all four nodes for purely cosmetic reasons.

## Prior reports

A search across Debian BTS, Launchpad, upstream openssh-bugzilla,
upstream systemd issues, and broader web search turned up **no public
reports** of this exact chain. Closest related items:

- Debian #314649 (2005, fixed) — original `~/.ssh/config` mode-check
  complaint that motivated the `user-group-modes.patch` itself.
- systemd/systemd#9585 (2018, closed) — different nss_systemd
  `getpwent` bug (errno clobbering, not stray fd reads).
- systemd/systemd#34803 — assertion failure in nss_systemd, unrelated.
- systemd/systemd#41883 — varlink connection-handle wedge, related
  IPC layer but different mechanism.

The chain `secure_permissions()` → `getpwent()` → `nss_systemd` →
stray read on an unrelated fd → mangled config parse appears to be
undocumented as of 2026-05-05.

## Where to file

The actual bug is in **libunwind** (or tcmalloc's use of it) — a stale
fd cache that reads from a recycled fd number. Repository:
<https://github.com/libunwind/libunwind/issues> and
<https://github.com/gperftools/gperftools/issues>.

A partial C reproducer is in `reproducer.c` here. It demonstrates
the chain (getpwent → nss_systemd → tcmalloc heap growth → libunwind
stack walk → stray fd read) but doesn't fire 100% deterministically,
because ssh's specific fd-numbering dance during startup is part of
what aligns libunwind's stale fd with the freshly-opened config.
The `chmod 0664 ~/.ssh/config && ssh -G github.com` repro above fires
every time. For an upstream report, the C reproducer is useful for
showing the call chain; the ssh repro is useful for showing the
symptom.

Secondary places worth a note (lower priority):

- **TRC team / Google** — the LD_PRELOAD in the TPU VM image's
  `/etc/environment` is what makes this latent libunwind bug
  user-visible on the cluster. Worth flagging when next reporting
  TPU image issues, since it's the ultimate trigger of *this*
  manifestation. (See `reference_trc_support.md` memory for how.)
- **Debian openssh** — `user-group-modes.patch` is the proximate
  cause of the parse error on Debian/Ubuntu. Not really wrong, but
  could defensively `ftell()`/`fseek()` around the `secure_permissions`
  call until libunwind is fixed.

The upstream OpenSSH project itself doesn't carry the patched code
path, so a mindrot.org bug isn't appropriate.

(Open: Matt to file when ready.)
