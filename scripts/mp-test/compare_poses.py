#!/usr/bin/env python3
"""
Compare the bones an owner logged for its objects with the bones of their copies on another
machine, from a summary of scripts/mp-test/run_mp_test.py (scenario pose-all).

    python scripts/mp-test/compare_poses.py [out/mp-test/pose-all/summary.txt]

`log-pose-own <kind>` (owner, "host" section) and `log-pose-copy <slot> <name>` (viewer, "client"
section) print four bones relative to bone 0. Props and vehicles are logged once: the error is the
largest bone distance. The NPC is logged every frame on both sides: the copy is drawn 100 ms
later, so the two series are paired by the frame offset that fits best, and the samples next to a
gap in the owner's series (where consecutive lines are not consecutive frames) are left out.
"""
from __future__ import annotations

import math
import re
import statistics
import sys
from pathlib import Path

LINE = re.compile(r"\[mp-test\] (\S+) (\d+) bone (\d+) offset (\S+) (\S+) (\S+)")
PAIRS = (("daxter", "sidekick"), ("gun", "gun"), ("vehicle", "v-toad"))


def samples(text: str, label: str) -> list[tuple[int, dict[int, tuple[float, ...]]]]:
    """Successive samples of `label`: (object id, {bone: offset})."""
    out: list[tuple[int, dict[int, tuple[float, ...]]]] = []
    for m in LINE.finditer(text):
        if m.group(1) != label:
            continue
        oid, bone = int(m.group(2)), int(m.group(3))
        if not out or out[-1][0] != oid or bone in out[-1][1]:
            out.append((oid, {}))
        out[-1][1][bone] = tuple(float(x) for x in m.group(4, 5, 6))
    return out


def error(a: dict, b: dict) -> float:
    """Largest distance between the same bones, in meters."""
    return max(math.dist(a[k], b[k]) for k in a if k in b)


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "out/mp-test/pose-all/summary.txt")
    client, host = path.read_text(encoding="utf-8").split("=== host")
    for own, copy in PAIRS:
        for oid, bones in samples(client, copy):
            match = [error(bones, b) for i, b in samples(host, own) if i == oid]
            if match:
                print(f"{own} {oid}: largest bone error {min(match) * 1000:.1f} mm")
    owner, viewer = samples(host, "npc"), samples(client, "*")
    best = None
    for offset in range(-300, 300):
        pairs = [(j - offset, error(viewer[j][1], owner[j - offset][1]))
                 for j in range(len(viewer)) if 0 <= j - offset < len(owner)]
        if len(pairs) >= 20:
            median = statistics.median(e for _, e in pairs)
            if best is None or median < best[0]:
                best = (median, pairs)
    if best is None:
        print("npc: not enough samples")
        return 1
    steps = [0.0] + [error(owner[i][1], owner[i - 1][1]) for i in range(1, len(owner))]
    gap = 3 * statistics.median(steps[1:])
    kept = [e for i, e in best[1]
            if steps[i] < gap and (i + 1 >= len(owner) or steps[i + 1] < gap)]
    print(f"npc: {len(kept)} frames paired, bone error median {statistics.median(kept) * 1000:.1f} mm,"
          f" largest {max(kept) * 1000:.1f} mm; the owner's bones move"
          f" {statistics.median(steps[1:]) * 1000:.1f} mm per frame")
    return 0


if __name__ == "__main__":
    sys.exit(main())
