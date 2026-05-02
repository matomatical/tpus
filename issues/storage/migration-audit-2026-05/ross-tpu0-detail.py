#!/usr/bin/env python3
"""Detail-pass on the ross/tpu0 migration gap.

Bucket the missing-from-/storage files by:
  - whether mtime is pre- or post-migration (cutoff 2026-04-24 21:42 UTC,
    taken from /storage/home/ross dir mtime)
  - top-level path component, with size totals

Also peek at /storage/home/ross/projects/myproject vs /home/ross/projects/myproject
on tpu1 to characterise the 3 missing tpu1 files.
"""
import datetime as dt
import subprocess
from collections import Counter, defaultdict

MIGRATION_TS = dt.datetime(2026, 4, 24, 21, 42, 0).timestamp()


def run(node, cmd):
    argv = ["bash", "-c", cmd] if node == 0 else ["ssh", f"tpu{node}", cmd]
    res = subprocess.run(argv, capture_output=True)
    return res.stdout.decode("utf-8", "replace")


def parse(out):
    d = {}
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        p, s, t = parts
        try:
            d[p] = (int(s), float(t))
        except ValueError:
            continue
    return d


def src_list(node, user):
    cmd = (
        f"sudo find /home/{user} -mindepth 1 -not -type d "
        f"-printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
    )
    return parse(run(node, cmd))


def dst_list_tpu0(user):
    cmd = (
        f"sudo find /storage/home/{user} -mindepth 1 \\( "
        f"-path /storage/home/{user}/tpu1 -o "
        f"-path /storage/home/{user}/tpu2 -o "
        f"-path /storage/home/{user}/tpu3 "
        f"\\) -prune -o -not -type d -printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
    )
    return parse(run(0, cmd))


def fmt(t):
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def sizefmt(b):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if b < 1024:
            return f"{b:6.1f}{unit}"
        b /= 1024
    return f"{b:6.1f}TiB"


def main():
    print("Loading ross /home (tpu0) and /storage/home/ross ...")
    src = src_list(0, "ross")
    dst = dst_list_tpu0("ross")
    missing = sorted(set(src) - set(dst))
    only_in_dst = sorted(set(dst) - set(src))

    print(f"src files = {len(src)}")
    print(f"dst files = {len(dst)}")
    print(f"missing-in-dst = {len(missing)}")
    print(f"only-in-dst    = {len(only_in_dst)}")
    print(f"migration cutoff = {fmt(MIGRATION_TS)}")

    pre = [p for p in missing if src[p][1] <= MIGRATION_TS]
    post = [p for p in missing if src[p][1] > MIGRATION_TS]
    print(f"\n  pre-migration  missing files: {len(pre)}")
    print(f"  post-migration missing files: {len(post)}")

    # Top-level directory breakdown of missing files (with sizes).
    by_dir_count = Counter()
    by_dir_bytes = defaultdict(int)
    for p in missing:
        top = p.split("/", 1)[0]
        by_dir_count[top] += 1
        by_dir_bytes[top] += src[p][0]
    print("\n  Missing-in-dst by top-level entry:")
    for top, n in by_dir_count.most_common():
        print(f"    {n:>6}  {sizefmt(by_dir_bytes[top]):>10}  {top}")

    # Same for only-in-dst (reverse direction — files in /storage but not /home)
    print("\n  only-in-dst by top-level entry (these are EXPECTED for new work):")
    only_dir = Counter()
    only_bytes = defaultdict(int)
    for p in only_in_dst:
        top = p.split("/", 1)[0]
        only_dir[top] += 1
        only_bytes[top] += dst[p][0]
    for top, n in only_dir.most_common(20):
        print(f"    {n:>6}  {sizefmt(only_bytes[top]):>10}  {top}")

    # Show all post-migration missing files in detail (these are the ones
    # potentially still hot — recent activity that didn't make it across).
    if post:
        print(f"\n  All {len(post)} POST-migration missing files (oldest first):")
        for p in sorted(post, key=lambda p: src[p][1]):
            sz, mt = src[p]
            print(f"    {fmt(mt)}  {sz:>12d}  {p}")


if __name__ == "__main__":
    main()
