/*
 * reproducer.c — partial demonstration of the chain underlying the
 * "first-byte ignored on ~/.ssh/config" parser bug.
 *
 * The full chain (which fires deterministically inside ssh, see the
 * README's Repro section for `chmod 0664 ~/.ssh/config && ssh -G ...`)
 * is:
 *
 *     getpwent()                     -- as called by ssh's
 *     -> NSS dispatch                --   secure_permissions()
 *     -> nss_systemd                 --   from Debian's
 *     -> tc_realloc                  --   user-group-modes.patch
 *     -> tcmalloc::PageHeap::GrowHeap
 *     -> tcmalloc::GetStackTrace
 *     -> libunwind _ULx86_64_step
 *     -> read(fd_X, ?, 1)            -- on a STALE fd that's been
 *                                      closed and reused for an
 *                                      unrelated open file
 *
 * The interesting bit is the last hop: libunwind (or tcmalloc's use
 * of it) reads from a file descriptor it cached earlier, which has
 * since been closed and the number recycled to the application's
 * own open file. The byte is silently consumed.
 *
 * What this program does:
 *   1. Walk getpwent() once (loads nss_systemd, primes tcmalloc).
 *   2. Open a "victim" file at fd 3 (via dup2 — the fd that
 *      libunwind appears to cache in ssh's startup pattern).
 *   3. Walk getpwent() again with churn, in case heap growth fires.
 *   4. Check whether the victim fd's read position has moved.
 *
 * Caveats — why the C version is fiddly:
 *   - ssh's exact fd-numbering dance during startup (open/close
 *     /etc/passwd at fd 3, then fopen the config at fd 3) is what
 *     aligns libunwind's cached fd with ssh's freshly-opened config.
 *     A pure-C program does a different dance: by the time we
 *     dup2 onto fd 3, tcmalloc has already established its own
 *     self-pipe there (visible as fd 3 = pipe in strace), and the
 *     dup2 clobbers that pipe instead of replacing libunwind's
 *     cached file fd. So the bug *can* fire here (and has been
 *     observed firing through tcmalloc's self-pipe path on this
 *     machine), but it doesn't fire with the same reliability as
 *     in ssh.
 *   - For a deterministic reproducer of the actual ssh symptom,
 *     prefer the README's Repro section (`chmod 0664 ~/.ssh/config
 *     && ssh -G github.com`).
 *
 * Build:
 *   cc -Wall -O2 -o reproducer reproducer.c
 *
 * Run (preloading tcmalloc as on TRC TPU VMs):
 *   LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libtcmalloc.so.4 ./reproducer
 *
 * Without the preload, no tcmalloc → no GetStackTrace → no
 * libunwind in the call chain → bug cannot fire.
 *
 * Author: Matthew Farrugia-Roberts (with Claude). 2026-05-05.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define VICTIM_FD 3

/* Walk all entries via getpwent() — the call that pulls nss_systemd
 * into the address space and (with tcmalloc preloaded) eventually
 * dispatches to tcmalloc's GetStackTrace → libunwind. */
static void walk_passwd(const char *label) {
    int n = 0;
    setpwent();
    while (getpwent() != NULL) ++n;
    endpwent();
    fprintf(stderr, "[%s] walked %d passwd entries\n", label, n);
}

/* Allocate a batch of small blocks. Heap growth in tcmalloc happens
 * when the central freelist runs out of pages for a size class —
 * one small batch like this typically forces at least one growth,
 * which calls GetStackTrace → libunwind, where the bug fires.
 *
 * (Large mallocs (≳ 256 KiB) go straight to mmap and don't touch
 * the page heap, so they don't trigger the bug.) */
static void churn_small(int n) {
    void **bufs = malloc(n * sizeof(void*));
    if (!bufs) return;
    for (int i = 0; i < n; ++i) bufs[i] = malloc(64 + (i % 1024));
    for (int i = 0; i < n; ++i) free(bufs[i]);
    free(bufs);
}

/* Force the victim file onto VICTIM_FD, even if open() happens to
 * return that fd directly (in which case dup2 is a no-op and we
 * must NOT close the original). */
static int open_at_fd(const char *path, int fd) {
    int tmp = open(path, O_RDONLY);
    if (tmp < 0) return -1;
    if (tmp == fd) return fd;
    if (dup2(tmp, fd) < 0) {
        int e = errno;
        close(tmp);
        errno = e;
        return -1;
    }
    close(tmp);
    return fd;
}

int main(void) {
    /* Ignore SIGPIPE. Once libunwind's stale-fd cache fires, some
     * tcmalloc-internal machinery (it appears to maintain a self-pipe
     * at a low fd whose read end gets closed during heap churn) ends
     * up writing to a broken pipe. We just want the fd position
     * check to complete. */
    signal(SIGPIPE, SIG_IGN);

    /* Phase 1. Prime libunwind/tcmalloc by triggering one full
     * getpwent enumeration. After this, libunwind has internalised
     * whatever fd-cache state we're going to corrupt. */
    walk_passwd("phase1-prime");

    /* Phase 2. Open the victim file at fd 3. */
    const char *path = "/etc/hostname";
    int fd = open_at_fd(path, VICTIM_FD);
    if (fd < 0) { perror("open_at_fd"); return 2; }

    struct stat sb;
    if (fstat(fd, &sb) != 0) { perror("fstat"); return 2; }
    fprintf(stderr, "victim fd=%d, file=%s, size=%lld\n",
            fd, path, (long long)sb.st_size);

    off_t pos_before = lseek(fd, 0, SEEK_CUR);
    if (pos_before < 0) { perror("lseek (before)"); return 2; }
    fprintf(stderr, "fd %d position before phase3: %lld\n",
            fd, (long long)pos_before);

    /* Phase 3. Walk passwd, churn aggressively, walk again. The
     * single big churn forces tcmalloc to GrowHeap in size classes
     * it hasn't seen before, calling GetStackTrace → libunwind →
     * stray read on fd 3. Two walks bracket the churn so a stack
     * trace fires whether libunwind is invoked from the walk or
     * the malloc path. */
    walk_passwd("phase3-trigger-A");
    churn_small(65536);
    walk_passwd("phase3-trigger-B");

    off_t pos_after = lseek(fd, 0, SEEK_CUR);
    if (pos_after < 0) {
        fprintf(stderr,
            "fd %d no longer seekable (errno=%d, %s) — "
            "tcmalloc internals appear to have repurposed the slot. "
            "Bug status indeterminate after this point, but its "
            "firing inside the churn is what typically causes this.\n",
            fd, errno, strerror(errno));
        return 2;
    }
    fprintf(stderr, "fd %d position after phase3: %lld\n",
            fd, (long long)pos_after);

    if (pos_after == pos_before) {
        fprintf(stderr, "RESULT: file position unchanged — "
                "bug did NOT reproduce on this run.\n");
        return 0;
    } else {
        fprintf(stderr, "RESULT: file position advanced %lld → %lld — "
                "BUG REPRODUCED (stray read of %lld byte(s)).\n",
                (long long)pos_before, (long long)pos_after,
                (long long)(pos_after - pos_before));
        return 1;
    }
}
