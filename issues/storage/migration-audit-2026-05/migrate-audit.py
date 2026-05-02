#!/usr/bin/env python3
"""Audit /home -> /storage/home migration for the named users.

For each (user, node), compares:
  - source: /home/<u> on the node (frozen post-migration backup)
  - dest:   /storage/home/<u>/        for tpu0 (excluding tpu[123]/ subdirs)
            /storage/home/<u>/tpuN/   for tpu1/2/3

Reports:
  - files present in source but missing in dest (with src mtime)
  - files in both where /storage mtime < /home mtime (anomaly)
  - files in both where size differs

All file-system reads are done via sudo. Runs from tpu0; SSHes to tpu1/2/3
to read their local /home; reads /storage destinations locally.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys

USERS = ["afiq", "ross", "olly"]
NODES = [0, 1, 2, 3]


def run(node: int, cmd: str) -> str:
    """Run shell cmd locally (node 0) or via ssh tpuN. Returns stdout (str)."""
    argv = ["bash", "-c", cmd] if node == 0 else ["ssh", f"tpu{node}", cmd]
    res = subprocess.run(argv, capture_output=True)
    if res.returncode != 0:
        sys.stderr.write(
            f"WARN: cmd failed on tpu{node} (rc={res.returncode}): {cmd}\n"
            f"      stderr: {res.stderr.decode('utf-8', 'replace')[:300]}\n"
        )
    return res.stdout.decode("utf-8", "replace")


def parse(out: str) -> dict[str, tuple[int, float]]:
    """Lines `path\\tsize\\tmtime` -> dict path -> (size, mtime_epoch)."""
    d: dict[str, tuple[int, float]] = {}
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


def src_list(node: int, user: str) -> dict[str, tuple[int, float]]:
    cmd = (
        f"sudo find /home/{user} -mindepth 1 -not -type d "
        f"-printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
    )
    return parse(run(node, cmd))


def dst_list(user: str, node: int) -> dict[str, tuple[int, float]]:
    if node == 0:
        # `-maxdepth` is a *global* option in find(1), so it can't gate the
        # prune branch. Use exact -path matches instead — they only fire on
        # the literal /storage/home/<u>/tpu{1,2,3} entries at depth 1.
        cmd = (
            f"sudo find /storage/home/{user} -mindepth 1 \\( "
            f"-path /storage/home/{user}/tpu1 -o "
            f"-path /storage/home/{user}/tpu2 -o "
            f"-path /storage/home/{user}/tpu3 "
            f"\\) -prune -o -not -type d -printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
        )
    else:
        cmd = (
            f"sudo find /storage/home/{user}/tpu{node} -mindepth 1 "
            f"-not -type d -printf '%P\\t%s\\t%T@\\n' 2>/dev/null"
        )
    return parse(run(0, cmd))


def fmt(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    for user in USERS:
        print(f"\n{'=' * 72}\nUser: {user}\n{'=' * 72}")
        for node in NODES:
            src = src_list(node, user)
            dst = dst_list(user, node)

            missing = sorted(set(src) - set(dst))
            common = set(src) & set(dst)
            mtime_anom: list[tuple[str, float, float]] = []
            size_diff: list[tuple[str, int, int]] = []
            for p in common:
                ssz, smt = src[p]
                dsz, dmt = dst[p]
                # Allow 2s tolerance on mtimes (filesystem rounding).
                if dmt < smt - 2.0:
                    mtime_anom.append((p, smt, dmt))
                if ssz != dsz:
                    size_diff.append((p, ssz, dsz))

            print(
                f"\n  tpu{node}: src={len(src)} dst={len(dst)} "
                f"missing={len(missing)} "
                f"storage_older_than_home={len(mtime_anom)} "
                f"size_differ={len(size_diff)}"
            )

            if missing:
                # mtime distribution summary
                mts = sorted(src[p][1] for p in missing)
                print(
                    f"    missing mtime span: "
                    f"{fmt(mts[0])} .. {fmt(mts[-1])}"
                )
                print(f"    Missing files (up to 50, oldest mtime first):")
                missing_by_mtime = sorted(missing, key=lambda p: src[p][1])
                for p in missing_by_mtime[:50]:
                    sz, mt = src[p]
                    print(f"      {fmt(mt)}  {sz:>12d}  {p}")
                if len(missing_by_mtime) > 50:
                    print(f"      ... and {len(missing_by_mtime) - 50} more")

            if mtime_anom:
                print(f"    /storage mtime < /home mtime (sample, up to 20):")
                for p, smt, dmt in sorted(mtime_anom, key=lambda x: x[2])[:20]:
                    print(
                        f"      home={fmt(smt)}  storage={fmt(dmt)}  {p}"
                    )

            if size_diff:
                print(f"    size differs (sample, up to 20):")
                for p, ssz, dsz in size_diff[:20]:
                    print(f"      home={ssz:>12d}  storage={dsz:>12d}  {p}")


if __name__ == "__main__":
    main()
