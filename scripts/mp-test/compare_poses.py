#!/usr/bin/env python3
"""
Compare the bones an owner logged for its objects with the bones of their copies on another
machine, at the same moment, from a summary of scripts/mp-test/run_mp_test.py (scenario pose-all).

    python scripts/mp-test/compare_poses.py [out/mp-test/pose-all/summary.txt]

`log-pose-own <kind>` (owner, "host" section) and `log-pose-copy <slot> <name>` (viewer, "client"
section) print four bones relative to bone 0, stamped with the owner's clock: the owner's own time,
and on the viewer the time the owner's objects are drawn at. Both log every frame, so each copy
sample is compared with the owner's bones interpolated at its stamp; the error is the largest
distance between the same bones.
"""
from __future__ import annotations

import math
import re
import statistics
import sys
from pathlib import Path

HEAD = re.compile(r"\[mp-test\] (\S+) (\d+) bones (\d+) t (-?\d+)")
BONE = re.compile(r"\[mp-test\] (\S+) (\d+) bone (\d+) offset (\S+) (\S+) (\S+)")
PAIRS = (("npc", "*"), ("daxter", "sidekick"), ("gun", "gun"), ("vehicle", "v-toad"), ("wheel", "v-toad-wheel"))

Sample = tuple[float, dict[int, tuple[float, ...]]]


def samples(text: str) -> dict[tuple[str, int], list[Sample]]:
    """(label, object id) -> samples in time order."""
    out: dict[tuple[str, int], list[Sample]] = {}
    current = None
    for line in text.splitlines():
        m = HEAD.search(line)
        if m:
            current = (m.group(1), int(m.group(2)))
            out.setdefault(current, []).append((float(m.group(4)), {}))
            continue
        m = BONE.search(line)
        if m and current == (m.group(1), int(m.group(2))):
            out[current][-1][1][int(m.group(3))] = tuple(float(x) for x in m.group(4, 5, 6))
    for series in out.values():
        series.sort(key=lambda s: s[0])
    return out


def error(a: dict, b: dict) -> float:
    """Largest distance between the same bones, in meters."""
    return max(math.dist(a[k], b[k]) for k in a if k in b)


def at(series: list[Sample], t: float) -> dict | None:
    """The owner's bones at time t, interpolated between the two frames around it."""
    for (ta, a), (tb, b) in zip(series, series[1:]):
        if ta <= t <= tb and 0 < tb - ta <= 40:
            f = (t - ta) / (tb - ta)
            return {k: tuple(x + (y - x) * f for x, y in zip(a[k], b[k])) for k in a if k in b}
    return None


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "out/mp-test/pose-all/summary.txt")
    client, host = path.read_text(encoding="utf-8").split("=== host")
    own, copies = samples(host), samples(client)
    for own_label, copy_label in PAIRS:
        for (label, oid), series in own.items():
            if label != own_label or (copy_label, oid) not in copies:
                continue
            errors = [error(b, ref) for t, b in copies[(copy_label, oid)] if (ref := at(series, t))]
            steps = [error(b, a) for (_, a), (_, b) in zip(series, series[1:])]
            if errors:
                print(f"{own_label} {oid}: {len(errors)} samples at the same moment, bone error median"
                      f" {statistics.median(errors) * 1000:.1f} mm, largest {max(errors) * 1000:.1f} mm;"
                      f" the owner's bones move {statistics.median(steps) * 1000:.1f} mm per frame")
            else:
                print(f"{own_label} {oid}: no copy sample inside the owner's window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
