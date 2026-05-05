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
parse — no abort, no warning. **This is either a downstream bug or a
buffer-handling subtlety upstream that I haven't traced into the
source yet** — open question, see below.

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

## Open question

Is this a known, reported OpenSSH bug? It looks like one of:

- A regression in Ubuntu's patched 8.9p1 (the `-3ubuntu0.15` series
  has accumulated security backports).
- A subtle interaction between `fopen("rb")` + `fstat()` ordering in
  `read_config_file_depth()` in some codepath I haven't traced.

Worth checking: bugs.openssh.com, Debian/Ubuntu BTS, and the
openssh-unix-dev mailing list archives. If unreported, we should file
upstream — the silent-misparse-instead-of-fatal behaviour wastes a lot
of debugging time, as this entry attests.

(Matt to investigate the upstream side.)
