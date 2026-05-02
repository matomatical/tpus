#!/usr/bin/env python3
"""For ross's tpu0 'missing' files, cross-check whether the file CONTENT
(size+mtime) appears anywhere in /storage/home/ross. If yes, it was
migrated successfully — ross just moved the file in /home post-migration.
"""
import datetime as dt
import subprocess
from collections import Counter

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


# tpu0 src
src = parse(run(0, "sudo find /home/ross -mindepth 1 -not -type d "
                  "-printf '%P\\t%s\\t%T@\\n' 2>/dev/null"))

# tpu0 dst (excluding tpuN subdirs)
dst = parse(run(0,
    "sudo find /storage/home/ross -mindepth 1 \\( "
    "-path /storage/home/ross/tpu1 -o -path /storage/home/ross/tpu2 -o "
    "-path /storage/home/ross/tpu3 \\) -prune -o -not -type d "
    "-printf '%P\\t%s\\t%T@\\n' 2>/dev/null"))

# Build a content-based index of /storage: (size, rounded_mtime) -> [paths]
from collections import defaultdict
dst_by_content = defaultdict(list)
for p, (sz, mt) in dst.items():
    # Round mtime to nearest second to allow filesystem-level rounding.
    dst_by_content[(sz, round(mt))].append(p)

missing_paths = sorted(set(src) - set(dst))
print(f"missing paths (path-based): {len(missing_paths)}")

# Stratify each missing path
moved = []
post_mig = []
truly_missing = []
for p in missing_paths:
    sz, mt = src[p]
    candidates = dst_by_content.get((sz, round(mt)), [])
    if mt > MIGRATION_TS:
        post_mig.append((p, sz, mt))
    elif candidates:
        moved.append((p, candidates))
    else:
        truly_missing.append((p, sz, mt))

print(f"  -> POST-migration writes to /home (genuinely never copied): "
      f"{len(post_mig)}")
print(f"  -> moved within /storage (content found at different path): "
      f"{len(moved)}")
print(f"  -> truly missing (no content match in /storage): "
      f"{len(truly_missing)}")

if moved:
    print("\nSample of 'moved' files (src path -> /storage candidate):")
    for p, cands in moved[:15]:
        print(f"  {p}")
        for c in cands[:2]:
            print(f"     -> /storage/.../{c}")
        if len(cands) > 2:
            print(f"     ... and {len(cands) - 2} more candidates")

if post_mig:
    print(f"\nPOST-migration writes to /home/ross (potentially lost work):")
    for p, sz, mt in sorted(post_mig, key=lambda x: x[2]):
        print(f"  {fmt(mt)}  {sz:>10d}  {p}")

if truly_missing:
    print(f"\nTRULY missing files (size+mtime not in /storage, sample 30):")
    for p, sz, mt in sorted(truly_missing, key=lambda x: x[2])[:30]:
        print(f"  {fmt(mt)}  {sz:>10d}  {p}")
    if len(truly_missing) > 30:
        print(f"  ... and {len(truly_missing) - 30} more")

    # Top-level dir summary
    top = Counter(p.split('/', 1)[0] for p, _, _ in truly_missing)
    print("\n  truly-missing by top-level entry:")
    for t, n in top.most_common():
        print(f"    {n:>5}  {t}")
