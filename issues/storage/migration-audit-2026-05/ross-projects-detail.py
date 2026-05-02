#!/usr/bin/env python3
"""Look at ross/projects across nodes, in /home (per-node) and /storage."""
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


def fmt(t):
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def list_path(node, root, glob_root):
    """Returns dict with relative path -> (size, mtime). glob_root is the
    base prefix that gets stripped (find -printf '%P')."""
    cmd = (
        f"sudo find {root} -mindepth 1 -not -type d "
        f"-printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
    )
    return parse(run(node, cmd))


def main():
    # /home/ross/projects on every node
    home_per_node = {}
    for n in range(4):
        home_per_node[n] = list_path(n, "/home/ross/projects", "")

    # /storage/home/ross/projects (the merged tpu0 set)
    storage_root = list_path(0, "/storage/home/ross/projects", "")
    # /storage/home/ross/tpuN/projects (the per-node side-stash)
    storage_pernode = {}
    for n in (1, 2, 3):
        storage_pernode[n] = list_path(0, f"/storage/home/ross/tpu{n}/projects", "")

    print("File counts:")
    print(f"  /storage/home/ross/projects = {len(storage_root)}")
    for n in (1, 2, 3):
        print(
            f"  /storage/home/ross/tpu{n}/projects = {len(storage_pernode[n])}"
        )
    for n in range(4):
        print(f"  /home/ross/projects on tpu{n} = {len(home_per_node[n])}")

    # For tpu0: missing = home_per_node[0] - storage_root
    print("\n=== tpu0: /home/ross/projects vs /storage/home/ross/projects ===")
    src = home_per_node[0]
    dst = storage_root
    missing = sorted(set(src) - set(dst), key=lambda p: src[p][1])
    print(f"  missing-in-storage = {len(missing)}")
    if missing:
        # Bucket by mtime pre/post migration
        pre = [p for p in missing if src[p][1] <= MIGRATION_TS]
        post = [p for p in missing if src[p][1] > MIGRATION_TS]
        print(f"    pre-migration:  {len(pre)}")
        print(f"    post-migration: {len(post)}")
        # Bucket by top-level project name
        proj = Counter()
        proj_bytes = defaultdict(int)
        for p in missing:
            top = p.split("/", 1)[0]
            proj[top] += 1
            proj_bytes[top] += src[p][0]
        print(f"    by project:")
        for top, n in proj.most_common():
            mb = proj_bytes[top] / (1024 * 1024)
            print(f"      {n:>4}  {mb:>8.2f} MiB  {top}")
        # Print first 30 missing files
        print(f"    Sample (oldest 30):")
        for p in missing[:30]:
            sz, mt = src[p]
            print(f"      {fmt(mt)}  {sz:>10d}  {p}")
        if len(missing) > 30:
            print(f"      ... and {len(missing) - 30} more")
        # Print all post-migration
        if post:
            print(f"    All post-migration ({len(post)}):")
            for p in post:
                sz, mt = src[p]
                print(f"      {fmt(mt)}  {sz:>10d}  {p}")

    # For tpu1/2/3: missing = home_per_node[n] - storage_pernode[n]
    for n in (1, 2, 3):
        print(
            f"\n=== tpu{n}: /home/ross/projects vs "
            f"/storage/home/ross/tpu{n}/projects ==="
        )
        src = home_per_node[n]
        dst = storage_pernode[n]
        missing = sorted(set(src) - set(dst), key=lambda p: src[p][1])
        print(f"  missing-in-storage = {len(missing)}")
        if missing:
            pre = [p for p in missing if src[p][1] <= MIGRATION_TS]
            post = [p for p in missing if src[p][1] > MIGRATION_TS]
            print(f"    pre-migration:  {len(pre)}")
            print(f"    post-migration: {len(post)}")
            for p in missing:
                sz, mt = src[p]
                print(f"      {fmt(mt)}  {sz:>10d}  {p}")


if __name__ == "__main__":
    main()
