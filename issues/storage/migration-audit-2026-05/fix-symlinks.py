#!/usr/bin/env python3
"""Rewrite /storage/home/<u>/... symlinks whose target is /home/<u>/... so
they point at the corresponding /storage path instead. Used after rsyncing
each user's pre-migration /home into /storage/home/<u>/, just before
deleting the per-node /home/<u>/ backups.

For symlinks in the merged tree the new target is
    /storage/home/<u>/<rest>
For symlinks under tpuN/ subdirs the new target is
    /storage/home/<u>/tpuN/<rest>
(matches where the rsync placed the corresponding content during migration.)

Symlinks whose new target doesn't actually exist on /storage are left as-is
and reported, so a human can decide.

Usage:
    fix-symlinks.py [--dry-run] [--include-backups] <user> [<user> ...]

By default only merged-tree symlinks are rewritten; tpuN/ subtree symlinks
are listed but skipped (they are inside the migration's per-node backup
which is meant for the user to reconcile themselves). Pass
--include-backups to rewrite those too.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def find_links(user: str) -> list[tuple[str, str]]:
    """All symlinks under /storage/home/<user> whose target starts with
    /home/<user>/. Returns list of (link_path, target) tuples. We read the
    target inline via `find -printf` since walking into the user's
    root-only-readable dirs needs sudo."""
    # Use \0 as field separator to be robust to weird filenames.
    cmd = [
        "sudo", "find", f"/storage/home/{user}", "-type", "l",
        "-lname", f"/home/{user}/*",
        "-printf", "%p\\0%l\\0",
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        sys.stderr.write(f"find failed: {res.stderr.decode('utf-8', 'replace')}\n")
        sys.exit(1)
    out = res.stdout.decode("utf-8", "replace")
    fields = out.split("\0")
    pairs = []
    # fields is [link, target, link, target, ..., '']
    for i in range(0, len(fields) - 1, 2):
        if fields[i]:
            pairs.append((fields[i], fields[i + 1]))
    return pairs


def scope_root(link: str, user: str) -> str:
    """Where does this symlink live? Returns the directory that should
    replace `/home/<u>` in the symlink's target."""
    base = f"/storage/home/{user}"
    for n in (1, 2, 3):
        if link.startswith(f"{base}/tpu{n}/"):
            return f"{base}/tpu{n}"
    return base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("users", nargs="+")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be done without changing anything")
    ap.add_argument("--include-backups", action="store_true",
                    help="also rewrite symlinks under tpuN/ backup subtrees")
    args = ap.parse_args()

    for user in args.users:
        print(f"\n{'=' * 72}\nUser: {user}\n{'=' * 72}")
        links = find_links(user)
        merged_root = f"/storage/home/{user}"
        prefix = f"/home/{user}/"

        n_merged = n_backup = n_skipped = n_rewritten = n_missing = 0
        examples_missing: list[str] = []
        for link, target in links:
            if not target.startswith(prefix):
                n_skipped += 1
                continue
            rest = target[len(prefix):]
            sroot = scope_root(link, user)
            new_target = f"{sroot}/{rest}"

            in_backup = sroot != merged_root
            if in_backup:
                n_backup += 1
            else:
                n_merged += 1

            if in_backup and not args.include_backups:
                continue

            # Verify the new target exists.
            check = subprocess.run(
                ["sudo", "test", "-e", new_target], capture_output=False
            )
            if check.returncode != 0:
                n_missing += 1
                if len(examples_missing) < 5:
                    examples_missing.append(
                        f"{link}\n   currently -> {target}\n   would point to (missing) -> {new_target}"
                    )
                continue

            # Rewrite the symlink.
            if not args.dry_run:
                # ln -sfn: -s symbolic, -f force overwrite, -n don't deref existing dest dir
                rc = subprocess.run(
                    ["sudo", "ln", "-sfn", new_target, link]
                ).returncode
                if rc != 0:
                    sys.stderr.write(f"  WARN: failed to rewrite {link}\n")
                    continue
            n_rewritten += 1

        print(f"  total /home/{user}-targeting symlinks: {len(links)}")
        print(f"    merged tree: {n_merged}")
        print(f"    tpuN/ backups: {n_backup}")
        action = "would rewrite" if args.dry_run else "rewrote"
        scope = "merged + backups" if args.include_backups else "merged only"
        print(f"  {action} ({scope}): {n_rewritten}")
        print(f"  new target missing (skipped): {n_missing}")
        if examples_missing:
            print(f"  Examples of missing-target skips:")
            for ex in examples_missing:
                print(f"    {ex}")


if __name__ == "__main__":
    main()
