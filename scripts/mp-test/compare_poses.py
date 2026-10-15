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

HEAD = re.compile(r"\[mp-test\] (\S+) (\d+) bones (\d+) t (-?\d+(?:\.\d+)?)")
BONE = re.compile(r"\[mp-test\] (\S+) (\d+) bone (\d+) offset (\S+) (\S+) (\S+)")
PAIRS = (("player", "player"), ("npc", "*"), ("daxter", "sidekick"), ("gun", "gun"), ("vehicle", "v-toad"),
         ("wheel", "v-toad-wheel"))

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


PATH = re.compile(r"\[mp-test\] path (own|copy) t (-?\d+(?:\.\d+)?) pos (\S+) (\S+) (\S+)")


def paths(text: str, kind: str) -> list[tuple[float, tuple[float, float, float]]]:
    """log-path samples of one kind (own or copy), in time order."""
    out = [(float(m.group(2)), (float(m.group(3)), float(m.group(4)), float(m.group(5))))
           for m in PATH.finditer(text) if m.group(1) == kind]
    out.sort(key=lambda s: s[0])
    return out


def path_report(own: list, copy: list) -> dict | None:
    """The copy's root against the owner's at the same moments (the owner's interpolated between
    its two states around each copy stamp), and the copy's frame-to-frame jerk against the
    owner's: how far and how unevenly the copy follows. None without samples in common."""
    errors = []
    for t, p in copy:
        for (ta, a), (tb, b) in zip(own, own[1:]):
            if ta <= t <= tb and 0 < tb - ta <= 40:
                f = (t - ta) / (tb - ta)
                errors.append(math.dist(p, tuple(x + (y - x) * f for x, y in zip(a, b))))
                break
    if not errors:
        return None

    def jerk(series: list) -> float:
        """Median change of speed between frames (m/s per frame): 0 for an even motion."""
        speeds = [math.dist(a, b) / ((tb - ta) / 1000.0) for (ta, a), (tb, b) in zip(series, series[1:]) if tb > ta]
        return statistics.median(abs(v2 - v1) for v1, v2 in zip(speeds, speeds[1:])) if len(speeds) > 2 else 0.0

    moved = math.dist(own[0][1], own[-1][1])
    return {"samples": len(errors), "median": statistics.median(errors), "p95": sorted(errors)[int(0.95 * (len(errors) - 1))],
            "largest": max(errors), "moved": moved, "jerk_own": jerk(own), "jerk_copy": jerk(copy)}


SHOT = re.compile(r"\[mp-test\] path (own|copy) t (-?\d+(?:\.\d+)?) pos (\S+) (\S+) (\S+) id (\d+)")
LASER = re.compile(r"\[mp-test\] laser (own|copy) t (-?\d+(?:\.\d+)?) from (\S+) (\S+) (\S+) to (\S+) (\S+) (\S+)")


def shot_report(host: str, client: str) -> dict | None:
    """Every shot's copy against the shot itself at the same moments (log-path shot), each shot by
    its id: how far from where the shot is its copy is drawn. None without samples in common."""
    def by_id(text: str, kind: str) -> dict:
        out: dict = {}
        for m in SHOT.finditer(text):
            if m.group(1) == kind:
                out.setdefault(m.group(6), []).append((float(m.group(2)), tuple(float(m.group(i)) for i in (3, 4, 5))))
        for series in out.values():
            series.sort(key=lambda s: s[0])
        return out
    own, copy = by_id(host, "own"), by_id(client, "copy")
    errors, shots = [], 0
    for sid, series in copy.items():
        if sid not in own:
            continue
        r = path_report(own[sid], series)
        if r:
            shots += 1
            for t, p in series:
                for (ta, a), (tb, b) in zip(own[sid], own[sid][1:]):
                    if ta <= t <= tb and 0 < tb - ta <= 40:
                        f = (t - ta) / (tb - ta)
                        errors.append(math.dist(p, tuple(x + (y - x) * f for x, y in zip(a, b))))
                        break
    if not errors:
        return None
    return {"shots": shots, "samples": len(errors), "median": statistics.median(errors),
            "p95": sorted(errors)[int(0.95 * (len(errors) - 1))], "largest": max(errors)}


def laser_report(host: str, client: str) -> dict | None:
    """The laser sight drawn on the gun's copy against the owner's at the same moments (log-laser):
    where it starts, its direction (degrees) and where it ends. None without samples in common."""
    def series(text: str, kind: str) -> list:
        out = [(float(m.group(2)), tuple(float(m.group(i)) for i in (3, 4, 5)), tuple(float(m.group(i)) for i in (6, 7, 8)))
               for m in LASER.finditer(text) if m.group(1) == kind]
        out.sort(key=lambda s: s[0])
        return out
    own, copy = series(host, "own"), series(client, "copy")
    starts, angles, ends = [], [], []
    for t, fa, ta_ in copy:
        for (t0, f0, e0), (t1, f1, e1) in zip(own, own[1:]):
            if t0 <= t <= t1 and 0 < t1 - t0 <= 40:
                k = (t - t0) / (t1 - t0)
                f = tuple(x + (y - x) * k for x, y in zip(f0, f1))
                e = tuple(x + (y - x) * k for x, y in zip(e0, e1))
                d1 = tuple(b - a for a, b in zip(f, e))
                d2 = tuple(b - a for a, b in zip(fa, ta_))
                n1, n2 = math.hypot(*d1), math.hypot(*d2)
                if n1 > 0 and n2 > 0:
                    c = max(-1.0, min(1.0, sum(a * b for a, b in zip(d1, d2)) / (n1 * n2)))
                    angles.append(math.degrees(math.acos(c)))
                starts.append(math.dist(fa, f))
                ends.append(math.dist(ta_, e))
                break
    if not starts:
        return None
    med = statistics.median
    return {"samples": len(starts), "start": med(starts), "start_max": max(starts),
            "angle": med(angles) if angles else 0.0, "angle_p95": sorted(angles)[int(0.95 * (len(angles) - 1))] if angles else 0.0,
            "end": med(ends), "end_p95": sorted(ends)[int(0.95 * (len(ends) - 1))]}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "out/mp-test/pose-all/summary.txt")
    client, host = path.read_text(encoding="utf-8").split("=== host")
    report = path_report(paths(host, "own"), paths(client, "copy"))
    if report:
        print(f"path: {report['samples']} copy samples, the host moved {report['moved']:.1f} m; copy root"
              f" error median {report['median'] * 100:.1f} cm, 95% under {report['p95'] * 100:.1f} cm, largest"
              f" {report['largest'] * 100:.1f} cm; speed change per frame median {report['jerk_own']:.2f} m/s"
              f" (owner) {report['jerk_copy']:.2f} m/s (copy)")
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
