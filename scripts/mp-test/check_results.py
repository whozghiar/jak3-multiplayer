#!/usr/bin/env python3
"""
Check, after `run_mp_test.py <scenario>` runs, that each scenario did what it is meant to: one line
per check, PASS or FAIL with the measured values, read from out/mp-test/<scenario>/summary.txt
(host = slot 0, client = the guest). Exit code 1 when a check fails or a summary is missing.

    python scripts/mp-test/check_results.py [scenario ...]     (default: every scenario run)

Every instance must end on the runner's stop (exit code 1), not on a crash, and leave no pose of a
drawn object out (`poses skipped 0`).
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "mp-test"
NUM = r"(-?\d+(?:\.\d+)?)"


def sections(text: str) -> dict[str, tuple[int, list[str]]]:
    """'client' and 'host' -> (exit code, lines)."""
    out: dict[str, tuple[int, list[str]]] = {}
    name = None
    for line in text.splitlines():
        m = re.match(r"=== (\w+) \(exit code (-?\d+)\)", line)
        if m:
            name = m.group(1)
            out[name] = (int(m.group(2)), [])
        elif name:
            out[name][1].append(line)
    return out


def grab(lines: list[str], pattern: str) -> list[tuple[str, ...]]:
    """Every match of `pattern` (its groups) in order."""
    rx = re.compile(pattern)
    return [m.groups() for line in lines if (m := rx.search(line))]


class Checker:
    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0

    def check(self, scenario: str, what: str, ok: bool, detail: str) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {what}: {detail}")
        if ok:
            self.passed += 1
        else:
            self.failed += 1


def hp_values(lines):
    return [float(v) for (v,) in grab(lines, r"\] local pos \S+ \S+ \S+ hp " + NUM)]


def remote_hp(lines, slot):
    return [float(v) for (v,) in grab(lines, rf"\] remote {slot} \S+ pos \S+ \S+ \S+ hp " + NUM)]


def counter(lines, pattern):
    vals = grab(lines, pattern)
    return [tuple(float(x) for x in g) for g in vals]


def look_checks(c: Checker, name: str, lines: list[str], slot: int) -> None:
    model = counter(lines, r"look model color-mult " + " ".join([NUM] * 3) + " emissive " + " ".join([NUM] * 3))
    marks = grab(lines, rf"look {slot} player color #x(\w+) minimap #x(\w+) markers drawn (\d+) last #x(\w+)")
    if not model or not marks:
        c.check(name, "color shown as a marker", False, "no log-look line")
        return
    mult, emis = model[-1][:3], model[-1][3:]
    c.check(name, "model not tinted", all(abs(v - 1.0) < 1e-3 for v in mult) and all(abs(v) < 1e-3 for v in emis),
            f"color-mult {mult}, emissive {emis}")
    color, minimap, drawn, last = marks[-1]
    c.check(name, "minimap icon in the player's color", color == minimap, f"player #x{color}, minimap #x{minimap}")
    c.check(name, "marker before the name in the player's color", int(drawn) > 0 and last == color,
            f"{drawn} markers drawn, last #x{last}")


def interp(series, t, max_step=None):
    """Value of a (t, v) series at t, linear, None outside it, across a jump larger than
    `max_step` (a platform back at the top of its cycle), or where the motion turns around next to
    the interval (a straight line between two samples cuts the turn)."""
    for i, ((ta, a), (tb, b)) in enumerate(zip(series, series[1:])):
        if ta <= t <= tb and tb > ta:
            if max_step is not None:
                if abs(b - a) > max_step:
                    return None
                slopes = [series[j + 1][1] - series[j][1] for j in (i - 1, i, i + 1) if 0 <= j < len(series) - 1]
                if any(x * slopes[0] < 0 for x in slopes):
                    return None
            return a + (b - a) * (t - ta) / (tb - ta)
    return None


def unwrap(series):
    """A cycle's phase (0 to 1) made continuous: each wrap adds 1."""
    out, turns, prev = [], 0.0, None
    for t, p in series:
        if prev is not None and p < prev - 0.5:
            turns += 1.0
        prev = p
        out.append((t, p + turns))
    return out


def copies(lines, slot):
    """Skeleton -> drawn flags of the copies of player `slot`'s objects (log-copies)."""
    out: dict[str, list[str]] = {}
    for sk, drawn in grab(lines, rf"\] copy {slot} (skel-\S+) (?:id \d+ )?(?:owner-hides \S+ )?drawn (\S+)"):
        out.setdefault(sk, []).append(drawn)
    return out


def run_checks(scenario: str, s: dict, c: Checker) -> None:
    host = s.get("host", (None, []))[1]
    client = s.get("client", (None, []))[1]
    for side, (code, lines) in s.items():
        c.check(scenario, f"{side} ran to the end", code == 1, f"exit code {code}")
        skipped = [int(v) for (v,) in grab(lines, r"poses skipped (\d+)")]
        if skipped:
            c.check(scenario, f"{side} no pose left out", max(skipped) == 0, f"poses skipped {max(skipped)}")

    if scenario == "smoke":
        for side, lines, slot in (("host", host, 1), ("client", client, 0)):
            drawn = grab(lines, rf"\] remote {slot} flags \S+ anim \S+ veh \S+ puppet (\S+) drawn (\S+)")
            c.check(scenario, f"{side} draws the other player", bool(drawn) and drawn[-1] == ("#t", "#t"),
                    f"puppet/drawn {drawn[-1] if drawn else None}")
            applied = [int(v) for (v,) in grab(lines, r"poses sent \d+ applied (\d+)")]
            c.check(scenario, f"{side} applies the other's poses", bool(applied) and applied[-1] > 0,
                    f"applied {applied[-1] if applied else None}")
        broken = grab(host, r"shader links (\d+) broken (\d+)")
        c.check(scenario, "texture shader lists intact", bool(broken) and all(b == "0" for _, b in broken),
                f"{broken}")

    elif scenario == "pvp":
        hits = counter(host, r"pvp hits taken (\d+) reacted (\d+) last pushed " + NUM)
        taken, reacted, pushed = hits[-1] if hits else (0, 0, 0)
        c.check(scenario, "host takes the guest's punch", taken >= 1, f"{int(taken)} hit(s) taken")
        c.check(scenario, "host plays the stock hit reaction (target-hit)", reacted >= 1, f"{int(reacted)} reaction(s)")
        c.check(scenario, "host knocked back", pushed >= 1.5, f"pushed {pushed:.2f} m")
        hp = hp_values(host)
        c.check(scenario, "host loses health", bool(hp) and hp[-1] < hp[0], f"hp {hp[0] if hp else None} -> {hp[-1] if hp else None}")
        rhp = remote_hp(client, 0)
        c.check(scenario, "the guest sees the host's health drop", bool(rhp) and rhp[-1] < rhp[0],
                f"host hp on the guest {rhp[0] if rhp else None} -> {rhp[-1] if rhp else None}")
        look_checks(c, scenario, host, 1)
        look_checks(c, scenario, client, 0)

    elif scenario == "pvp-probe":
        me = counter(client, r"\] me " + " ".join([NUM] * 3) + r" speed " + NUM)
        host_pos = counter(client, r"\] remote 0 \S+ pos " + " ".join([NUM] * 3))
        states = grab(client, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")
        c.check(scenario, "the guest's punch lunges", any(st == "target-running-attack" for (st,) in states),
                "running attack seen" if states else "no probe")
        if me and host_pos:
            hx, hy, hz = host_pos[0]
            closest = min(math.dist((x, z), (hx, hz)) for x, _, z, _ in me)
            c.check(scenario, "the lunge stops on the host's hurtbox", closest > 0.5, f"closest {closest:.2f} m from the host")

    elif scenario == "npc":
        w = counter(client, r"world sent (\d+) received (\d+) replicas (\d+) asleep (\d+)")
        sent, received, replicas, asleep = w[-1] if w else (0, 0, 0, 0)
        c.check(scenario, "the guest draws the host's NPCs", replicas >= 5, f"{int(replicas)} replicas of {int(received)}")
        c.check(scenario, "the guest's own NPCs sleep", asleep >= 5, f"{int(asleep)} asleep")

    elif scenario == "npc-hit":
        hits = grab(host, r"\[multiplayer\] player 1 hit (.*) \((\w+), " + NUM + r"\)")
        c.check(scenario, "the host applies the guest's blows to its NPC", len(hits) >= 1,
                f"{len(hits)} hit(s): {[h[1] for h in hits]}")

    elif scenario in ("props", "combat"):
        cp = copies(client, 0)
        dax = cp.get("skel-sidekick", [])
        c.check(scenario, "the guest draws the host's Daxter", "#t" in dax, f"skel-sidekick drawn {dax}")
        if scenario == "combat":
            # the host takes its gun out here (props does not)
            guns = {k: v for k, v in cp.items() if "gun" in k}
            c.check(scenario, "the guest draws the host's gun", any("#t" in v for v in guns.values()), f"{guns}")
            fx = counter(client, r"fx effects sent (\d+) shown (\d+) shots sent (\d+) shown (\d+) shot copies (\d+)")
            em = counter(client, r"emitters sent \d+ received (\d+) shown (\d+)")
            c.check(scenario, "the host's shots drawn on the guest (as objects)", bool(fx) and fx[-1][4] >= 1,
                    f"shot copies {int(fx[-1][4]) if fx else None}")
            c.check(scenario, "the host's impacts and gun effects replayed", bool(fx) and fx[-1][1] >= 1 and bool(em) and em[-1][1] >= 1,
                    f"effects shown {fx[-1][1] if fx else None}, emitters shown {em[-1][1] if em else None}")

    elif scenario == "vehicle":
        cp = {k: v for k, v in copies(client, 0).items() if k.startswith("skel-v-")}
        body = [v for k, v in cp.items() if not k.endswith("-wheel")]
        wheels = [v for k, v in cp.items() if k.endswith("-wheel")]
        c.check(scenario, "the guest draws the host's buggy and wheels",
                any("#t" in v for v in body) and any(v.count("#t") >= 4 for v in wheels), f"{cp}")
        em = counter(client, r"emitters sent \d+ received (\d+) shown (\d+)")
        snd = counter(client, r"sounds sent \d+ played (\d+)")
        c.check(scenario, "its dust and engine replayed on the guest", bool(em) and em[-1][1] > 0 and bool(snd) and snd[-1][0] > 0,
                f"emitters shown {em[-1][1] if em else None}, sounds played {snd[-1][0] if snd else None}")
        pilot = grab(host, r"local indax \S+ pilot (\S+)")
        c.check(scenario, "the stolen driver is out of its buggy", bool(pilot) and pilot[-1][0] == "#f", f"host pilot {[p[0] for p in pilot]}")
        thief = {sk for (sk,) in grab(host, r"\] actor 167\d+ (skel-v-\S+)")}
        c.check(scenario, "the driver sees the thief's buggy", bool(thief), f"{sorted(thief)}")

    elif scenario in ("ride", "daxter-ride"):
        states = grab(client, r"\] local pos \S+ \S+ \S+ hp \S+ state (\S+)")
        riding = "target-indax-grab" if scenario == "daxter-ride" else "target-grab-ride"
        c.check(scenario, f"the guest rides as passenger ({riding})", any(st == riding for (st,) in states), f"{[s for (s,) in states]}")
        if scenario == "daxter-ride":
            flags = [int(f, 16) for (f,) in grab(host, r"\] remote 1 flags #x(\w+)")]
            c.check(scenario, "the host sees Daxter riding (Daxter model, riding)", any(f & 0x22 == 0x22 for f in flags), f"{[hex(f) for f in flags]}")
        gpos = counter(client, r"\] local pos " + " ".join([NUM] * 3))
        hpos = counter(client, r"\] remote 0 \S+ pos " + " ".join([NUM] * 3))
        moved = math.dist(gpos[0], gpos[-1]) if gpos else 0
        c.check(scenario, "the passenger travels with the vehicle", moved >= 10, f"guest moved {moved:.1f} m")
        gaps = [math.dist(g, h) for g, h in zip(gpos, hpos)]
        c.check(scenario, "it stays in its seat next to the driver", bool(gaps) and max(gaps) < 2.5,
                f"largest gap to the driver {max(gaps):.2f} m" if gaps else "no sample")

    elif scenario == "crate":
        hc = [int(v) for (v,) in grab(host, r"\] crates (\d+) ")]
        cc = [int(v) for (v,) in grab(client, r"\] crates (\d+) ")]
        c.check(scenario, "the host breaks crates", bool(hc) and hc[-1] < max(hc), f"host {max(hc) if hc else None} -> {hc[-1] if hc else None}")
        # the host's count is around it, and it walks to the crates: its drop is the last one
        hdrop = hc[-2] - hc[-1] if len(hc) >= 2 else 0
        gdrop = cc[0] - cc[-1] if cc else 0
        c.check(scenario, "they disappear on the guest as many", hdrop > 0 and gdrop == hdrop,
                f"host broke {hdrop}, the guest lost {gdrop} ({cc[0] if cc else None} -> {cc[-1] if cc else None})")

    elif scenario == "pose":
        def samples(lines, slot):
            out, cur = [], None
            for line in lines:
                m = re.search(rf"\] pose {slot} bones (\d+)", line)
                if m:
                    cur = {}
                    out.append(cur)
                    continue
                m = re.search(rf"\] pose {slot} bone (\d+) offset " + " ".join([NUM] * 3), line)
                if m and cur is not None:
                    cur[int(m.group(1))] = tuple(float(x) for x in m.group(2, 3, 4))
            return [x for x in out if x]
        # log-pose samples are not taken at the same moment on both machines: the driver's pose is
        # compared at the same moment in pose-all ("player"); here, its copy is drawn with bones
        n = len(samples(client, 0))
        c.check(scenario, "the guest lays the driver's bones on its copy", n > 0, f"{n} samples of 65 bones")
        em = counter(client, r"emitters sent \d+ received (\d+) shown (\d+)")
        snd = counter(client, r"sounds sent \d+ played (\d+)")
        c.check(scenario, "its dust and engine on the guest", bool(em) and em[-1][1] > 0 and bool(snd) and snd[-1][0] > 0,
                f"emitters shown {em[-1][1] if em else None}, sounds played {snd[-1][0] if snd else None}")

    elif scenario == "pose-all":
        # every copy sample against the owner's bones at the same moment (compare_poses.py), each
        # within the limit
        sys.path.insert(0, str(ROOT / "scripts/mp-test"))
        import compare_poses as cp
        text = (OUT / scenario / "summary.txt").read_text(encoding="utf-8", errors="replace")
        csec, hsec = text.split("=== host")
        own, cps = cp.samples(hsec), cp.samples(csec)
        for kind, copy_label, limit in (("player", "player", 5.0), ("npc", "*", 5.0), ("daxter", "sidekick", 3.0),
                                        ("gun", "gun", 3.0), ("vehicle", "v-toad", 3.0), ("wheel", "v-toad-wheel", 3.0)):
            errs = []
            for (label, oid), series in own.items():
                if label == kind and (copy_label, oid) in cps:
                    errs += [cp.error(b, r) * 1000 for t, b in cps[(copy_label, oid)] if (r := cp.at(series, t))]
            if not errs:
                c.check(scenario, f"{kind} copy has the owner's pose", False, "no sample at the same moment")
                continue
            errs.sort()
            off = sum(e > limit for e in errs)
            p98 = errs[min(len(errs) - 1, int(len(errs) * 0.98))]
            c.check(scenario, f"{kind} copy has the owner's pose", off == 0,
                    f"{len(errs)} samples, median {errs[len(errs) // 2]:.1f} mm, 98% within {p98:.1f} mm, "
                    f"largest {errs[-1]:.1f} mm, {off} beyond {limit} mm")

    elif scenario in ("progress", "progress-late"):
        h = counter(host, r"tasks closed (\d+) of \d+, after others (\d+)")
        g = counter(client, r"tasks closed (\d+) of \d+, after others (\d+)")
        c.check(scenario, "both end with the same story progress", bool(h) and bool(g) and h[-1][0] == g[-1][0],
                f"host {int(h[-1][0]) if h else None}, guest {int(g[-1][0]) if g else None} nodes closed")
        loaded = grab(client, r"loading the host's world \((\d+) bytes\)")
        c.check(scenario, "the guest got the host's progress with its world", bool(loaded),
                f"world loaded ({loaded[-1][0] if loaded else None} bytes)")
        if scenario == "progress":
            c.check(scenario, "a node the host closes later reaches the guest", bool(g) and max(v for _, v in g) >= 1,
                    f"{int(max(v for _, v in g)) if g else None} nodes closed after the host")

    elif scenario == "world-objects":
        hs = unwrap([(float(t), float(p)) for t, p in grab(host, r"sync t (\d+(?:\.\d+)?) phase " + NUM)])
        gs = [(float(t), float(p)) for t, p in grab(client, r"sync t (\d+(?:\.\d+)?) phase " + NUM)]
        # the samples are 250 ms apart; a frame that runs late is stamped late while the game clock,
        # which moves per frame, did not move further: one frame of tolerance plus that lateness
        diffs, over = [], []
        prev = None
        for t, p in gs:
            ref = interp(hs, t)
            late = max(0.0, (t - prev) - 250.0) if prev is not None else 0.0
            prev = t
            if ref is not None:
                d = abs(p - ref) % 1.0
                ms = min(d, 1.0 - d) * 4000.0  # 4 s cycle -> ms
                diffs.append(ms)
                if ms > 17 + late:
                    over.append(round(ms))
        c.check(scenario, "cycles in phase with the host", bool(diffs) and not over,
                f"largest {max(diffs):.0f} ms over {len(diffs)} samples (one frame, plus a late frame's lateness), "
                f"beyond {over}" if diffs else "no sample at the same moment")
        doors = grab(client, r"door aid \d+ state (\w+) at " + NUM)
        c.check(scenario, "the door at the host opens on the guest", bool(doors) and doors[0][0] != "open" and doors[-1][0] == "open",
                f"{[d[0] for d in doors]}")
        trips = counter(client, r"elevator trips sent (\d+) shown (\d+)")
        c.check(scenario, "the host's elevator trip runs on the guest", bool(trips) and trips[-1][1] >= 1, f"shown {trips[-1][1] if trips else None}")

    elif scenario == "scene":
        h = counter(host, r"scenes sent (\d+) shown (\d+) playing")
        g = grab(client, r"scenes sent (\d+) shown (\d+) playing (\S+)")
        c.check(scenario, "the host's cutscene sent", bool(h) and h[-1][0] >= 1, f"sent {h[-1][0] if h else None}")
        c.check(scenario, "it plays on the guest", bool(g) and int(g[-1][1]) >= 1 and any(x[2] == "#t" for x in g), f"{g[-1] if g else None}")

    elif scenario in ("float", "float-weight"):
        hser: dict[str, list] = {}
        for aid, t, h in grab(host, r"float aid (\d+) t (\d+) height " + NUM):
            hser.setdefault(aid, []).append((float(t), float(h)))
        diffs = []
        for aid, t, h in grab(client, r"float aid (\d+) t (\d+) height " + NUM):
            ref = interp(hser.get(aid, []), float(t), max_step=2.0)
            if ref is not None:
                diffs.append(abs(float(h) - ref))
        if scenario == "float":
            c.check(scenario, "the guest's lava platforms where the host's are", bool(diffs) and max(diffs) <= 0.10,
                    f"largest {max(diffs) * 100:.1f} cm over {len(diffs)} samples" if diffs else "no sample at the same moment")
        else:
            me = counter(client, r"\] me " + " ".join([NUM] * 3))
            moved = max((math.dist(me[0], p) for p in me), default=0)
            c.check(scenario, "the guest is carried by the platform", moved >= 5, f"carried {moved:.1f} m")
            ws = counter(host, r"weight sent (\d+)") + counter(client, r"weight sent (\d+)")
            c.check(scenario, "remote weight sent to the platforms", any(w[0] > 0 for w in ws), f"largest {max((w[0] for w in ws), default=0):.0f}")

    elif scenario == "far":
        auth = grab(client, r"authority (\S+) own npcs awake (\d+) asleep (\d+)")
        c.check(scenario, "the far guest simulates its own NPCs", any(a == "#t" and int(w) > 0 for a, w, _ in auth), f"{auth}")
        c.check(scenario, "back next to the host, its NPCs sleep again", bool(auth) and auth[-1][0] == "#f", f"{auth[-1] if auth else None}")
        drawn = grab(host, r"\] remote 1 flags \S+ anim \S+ veh \S+ puppet (\S+) drawn (\S+)")
        c.check(scenario, "the host draws the far guest", all(d == ("#t", "#t") for d in drawn) and bool(drawn), f"{drawn}")
        for side, lines in (("host", host), ("client", client)):
            levels = grab(lines, r"\] level (\w+) (?:loaded|active|alive)")
            c.check(scenario, f"{side} loads no level of the other's place",
                    not any(name == "wascityb" for (name,) in levels) or side == "client",
                    f"{sorted(set(x for (x,) in levels))}")
        broken = grab(host, r"shader links \d+ broken (\d+)")
        c.check(scenario, "texture shader lists intact", bool(broken) and all(b == "0" for (b,) in broken), f"broken {broken}")
        kinds = grab(client, r"kinds announced (\d+) drawable (\d+) groups rebuilt (\d+)")
        cov = grab(client, r"kinds described (\d+) complete (\d+) art files (\d+)")
        c.check(scenario, "every kind of the host's place can be drawn by the far guest",
                bool(kinds) and int(kinds[-1][0]) >= 10 and kinds[-1][0] == kinds[-1][1]
                and bool(cov) and cov[-1][0] == cov[-1][1] == cov[-1][2],
                f"{kinds[-1][1] if kinds else None} of {kinds[-1][0] if kinds else None} kinds drawable; "
                f"{cov[-1][0] if cov else None} described, {cov[-1][2] if cov else None} art files")

    elif scenario in ("far-city", "far-city-synth"):
        # no level of Haven loads in Spargus: the guest's objects come from their art alone
        levels = grab(host, r"\] level (cty\w+) (?:loaded|active|alive)")
        c.check(scenario, "no Haven level loads in Spargus", not levels, f"{sorted(set(x for (x,) in levels))}")
        heap = grab(host, r"art heap in .*? opened (\d+) closed (\d+) restored (\d+)")
        art = grab(host, r"art loaded (\d+) failed (\d+)")
        c.check(scenario, "the guest's objects' art loaded alone into the art heap",
                bool(heap) and int(heap[-1][0]) >= 1 and bool(art) and int(art[-1][0]) >= 1 and art[-1][1] == "0",
                f"heap opened {heap[-1][0] if heap else None}, art loaded {art[-1][0] if art else None} failed {art[-1][1] if art else None}")
        last: dict[str, tuple] = {}
        seen: dict[str, int] = {}
        for sk, oid, hides, drawn, artp in grab(host, r"\] copy 1 (skel-\S+) id (\d+) owner-hides (\S+) drawn (\S+) art (\S+)"):
            last[oid] = (sk, drawn, artp, hides)
            seen[oid] = seen.get(oid, 0) + 1
        steady = {k: v for k, v in last.items() if seen[k] >= 2}
        shown = {k: v for k, v in steady.items() if v[3] != "#t"}
        missing = sorted(set(v[0] for v in shown.values() if v[1] != "#t"))
        c.check(scenario, "every guest object it draws is drawn here", bool(shown) and not missing,
                f"{sum(v[1] == '#t' for v in shown.values())} of {len(shown)} drawn "
                f"({sorted(set(v[0] for v in shown.values()))}); not drawn {missing}")
        kinds = grab(host, r"kinds announced (\d+) drawable (\d+) groups rebuilt (\d+)")
        c.check(scenario, "every kind of object the guest announced can be drawn here",
                bool(kinds) and int(kinds[-1][0]) > 0 and kinds[-1][0] == kinds[-1][1],
                f"{kinds[-1][1] if kinds else None} of {kinds[-1][0] if kinds else None} kinds drawable, "
                f"{kinds[-1][2] if kinds else None} skeleton-groups rebuilt from the guest's descriptions")
        if scenario == "far-city-synth":
            c.check(scenario, "copies built from rebuilt skeleton-groups (code in no level here)",
                    bool(kinds) and int(kinds[-1][2]) > 0, f"{kinds[-1][2] if kinds else None} groups rebuilt, all copies built from them")
        broken = grab(host, r"shader links \d+ broken (\d+)")
        c.check(scenario, "texture shader lists intact", bool(broken) and all(b == "0" for (b,) in broken), f"broken {broken}")

    elif scenario in ("guns", "city-combat"):
        for side, lines in (("host", host), ("client", client)):
            fx = counter(lines, r"shot copies (\d+)")
            c.check(scenario, f"{side} draws the other's shots", bool(fx) and fx[-1][0] >= 1, f"shot copies {int(fx[-1][0]) if fx else None}")
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")

    elif scenario == "vehicle-hit":
        hits = grab(host, r"\[multiplayer\] player 1 hit #<(\S+) .*\((\w+), " + NUM + r"\)")
        board = [h for h in hits if h[1] == "board"]
        c.check(scenario, "the host applies the guest's zaps to its buggy", len(board) >= 1, f"{len(board)} zap hit(s)")
        c.check(scenario, "the guest's hoverboard drawn on the host", any("skel-board" in line for line in host), "skel-board copy")
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")

    elif scenario == "elevator-alone":
        rows = grab(client, r"elevator aid \d+ state (\w+) at \S+ height " + NUM)
        muted = grab(client, r"elevator trips sent \d+ shown (\d+) muted (\d+) continue (\S+)")
        heights = [float(h) for _, h in rows]
        c.check(scenario, "the guest's elevator rises with the host's", len(heights) >= 2 and heights[-1] - heights[0] > 100,
                f"heights {heights}")
        c.check(scenario, "its scripts set aside during the trip, back after",
                any(m[1] == "1" for m in muted) and bool(muted) and muted[-1][1] == "0", f"{muted}")
        c.check(scenario, "the guest's continue point unchanged", bool(muted) and all(m[2] == muted[0][2] for m in muted),
                f"{sorted(set(m[2] for m in muted))}")
        levels = grab(client, r"\] level (\w+) (?:loaded|active|alive)")
        c.check(scenario, "no level of the palace loaded for the guest", not any(n == "waspala" for (n,) in levels),
                f"{sorted(set(x for (x,) in levels))}")

    elif scenario in ("join-world", "join-title"):
        stage = grab(client, r"join stage (\d+) foreign (\S+) worlds sent \d+ loaded (\d+) bytes (\d+) saves refused (\d+)")
        cont = grab(client, r"join continue (\S+) title (\S+) in-game (\S+) language (\d+)")
        dist = [float(d) for (d,) in grab(client, r"join distance to the host " + NUM)]
        c.check(scenario, "the guest loads the host's world", bool(stage) and stage[-1][0] == "4" and stage[-1][2] == "1",
                f"stage {stage[-1][0] if stage else None}, loaded {stage[-1][2] if stage else None}, {stage[-1][3] if stage else None} bytes")
        c.check(scenario, "it comes next to the host", bool(dist) and dist[-1] < 10.0, f"{dist[-1] if dist else None} m")
        c.check(scenario, "at the host's checkpoint", bool(cont) and cont[-1][0] == "wascity-start", f"{cont[-1] if cont else None}")
        c.check(scenario, "its own settings kept", bool(cont) and len(set(x[3] for x in cont)) == 1, f"language {sorted(set(x[3] for x in cont))}")
        h = counter(host, r"tasks closed (\d+) of \d+")
        g = counter(client, r"tasks closed (\d+) of \d+")
        c.check(scenario, "the host's progress, not its own", bool(h) and bool(g) and g[-1][0] == h[-1][0],
                f"host {int(h[-1][0]) if h else None}, guest {int(g[-1][0]) if g else None} nodes closed")
        if scenario == "join-world":
            c.check(scenario, "the host's progress unchanged (the guest's not merged)", bool(h) and h[0][0] == h[-1][0],
                    f"{[int(x[0]) for x in h]}")
            c.check(scenario, "no save in the host's world", bool(stage) and int(stage[-1][4]) >= 1, f"saves refused {stage[-1][4] if stage else None}")
        else:
            c.check(scenario, "joined from the title screen", bool(cont) and cont[0][1] == "#t", f"{cont[0] if cont else None}")

    elif scenario == "coop":
        co = grab(client, r"coop (\S+) authority \S+ character (\d+) perch (-?\d+) perched (\d+) state (\S+)")
        c.check(scenario, "co-op: the guest, joining as Jak next to a Jak host, is offered Daxter",
                bool(co) and co[0][0] == "#t" and co[0][1] == "1", f"{co[:1]}")
        c.check(scenario, "Daxter jumps on the crouching Jak's shoulder and rides it",
                any(x[2] == "0" and x[4] == "target-indax-grab" for x in co), f"{[(x[2], x[4]) for x in co]}")
        dist = [float(d) for (d,) in grab(client, r"coop distance to the host " + NUM)]
        c.check(scenario, "he stays on the shoulder while Jak walks", len(dist) >= 3 and all(d < 2.0 for d in dist[1:3]),
                f"distances {dist}")
        c.check(scenario, "X puts him down, Daxter again", bool(co) and co[-1][2] == "-1" and co[-1][4] == "target-indax-stance",
                f"{co[-1:]}")

    elif scenario == "guard-hit":
        text = "\n".join(host)
        pairs = re.findall(r"player 1 hit #<crimson-guard[^>]*> \((\S+), [^)]*\)\s*\n\[multiplayer\]   taken (\S+), then (\S+)", text)
        reacted = [(m, n) for (m, t, n) in pairs if t == "#t" and n in ("hit", "knocked", "die", "die-falling", "knocked-recover", "hostile")]
        c.check(scenario, "the host's guard takes the guest's blows and reacts", len(reacted) >= 1, f"{pairs[:6]}")

    elif scenario in ("shot-path", "laser"):
        sys.path.insert(0, str(ROOT / "scripts" / "mp-test"))
        import compare_poses
        if scenario == "shot-path":
            r = compare_poses.shot_report("\n".join(host), "\n".join(client))
            c.check(scenario, "the copies of the host's shots are drawn where the shots are", bool(r) and r["shots"] >= 3 and r["p95"] < 0.5,
                    f"{r['shots'] if r else 0} shots, {r['samples'] if r else 0} samples, median {r['median'] * 100 if r else 0:.1f} cm, "
                    f"95% under {r['p95'] * 100 if r else 0:.1f} cm, largest {r['largest'] * 100 if r else 0:.1f} cm")
        else:
            r = compare_poses.laser_report("\n".join(host), "\n".join(client))
            c.check(scenario, "the laser sight drawn on the gun's copy follows the owner's", bool(r) and r["samples"] >= 60 and r["start"] < 0.1 and r["angle_p95"] < 3.0,
                    f"{r['samples'] if r else 0} samples, start {r['start'] * 100 if r else 0:.1f} cm (largest {r['start_max'] * 100 if r else 0:.1f}), "
                    f"direction {r['angle'] if r else 0:.2f} deg (95% under {r['angle_p95'] if r else 0:.2f}), end {r['end'] * 100 if r else 0:.1f} cm (95% under {r['end_p95'] * 100 if r else 0:.1f})")

    elif scenario == "npc-watch":
        gone = [int(v) for (v,) in grab(host, r"npcs gone near another player (\d+)")]
        w = counter(client, r"world sent \d+ received (\d+) replicas (\d+)")
        c.check(scenario, "the guest sees the host's NPCs", bool(w) and w[-1][1] >= 1, f"replicas {w[-1][1] if w else None}")
        c.check(scenario, "no NPC vanishes near the guest", bool(gone) and gone[-1] == 0, f"{gone}")

    elif scenario == "npc-handoff":
        roles = grab(client, r"\[multiplayer\] this player now (simulates|shows)")
        c.check(scenario, "the guest simulates its own NPCs while the host is away, then shows the host's",
                [r for (r,) in roles][-2:] == ["simulates", "shows"], f"{[r for (r,) in roles]}")
        w = counter(client, r"world sent \d+ received (\d+) replicas (\d+)")
        c.check(scenario, "the host's NPCs shown again", bool(w) and w[-1][1] >= 1, f"replicas {w[-1][1] if w else None}")
        gone = grab(client, r"copies vanished near us (\d+) faded (\d+)")
        each = [f"{a} {b}" for (a, b) in grab(client, r"\[mp-test\] vanished (\S+) (\S+)")]
        c.check(scenario, "nothing vanishes at once on the guest's screen (copies left there fade out)",
                bool(gone) and gone[-1][0] == "0", f"vanished {[int(a) for (a, b) in gone]}, faded {gone[-1][1] if gone else None} {each[:6]}")

    elif scenario.startswith("v1-"):
        # scripts that crashed multiplayer-v1.0.0 (its sources put back): the same blows, no crash
        if scenario == "v1-city-dark1-host":
            own = grab(host, r"\[mp-test\] goto-own (\S+)")
            c.check(scenario, "the host fires the peace maker at its own traffic", len(own) >= 1, f"{[o for (o,) in own]}")
        else:
            hits = grab(host, r"\[multiplayer\] player 1 hit #<(\S+) ")
            c.check(scenario, "the host applies the guest's blows to its vehicles", len(hits) >= 1,
                    f"{len(hits)} hits ({', '.join(sorted(set(h for (h,) in hits)))})")
        if scenario == "v1-city-dark1-host":
            died = [d for (d,) in grab(client, r"\[mp-test\] copy died (\S+ vehicle)")]
            c.check(scenario, "a vehicle's copy dies on the guest (what crashed v1.0.0)", len(died) >= 1, f"{died[:4]}")
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")

    elif scenario == "guns-fx":
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")
        for side, lines in (("host", host), ("client", client)):
            shown = counter(lines, r"weapons shown red2 (\d+) dark2 (\d+) arc (\d+) blue3 (\d+)")
            red2, dark2, arc, blue3 = shown[-1] if shown else (0, 0, 0, 0)
            c.check(scenario, f"{side} draws the other's wave concussor, mass inverter, arc and needle trails",
                    red2 >= 1 and dark2 >= 1 and arc >= 30 and blue3 >= 1,
                    f"red2 {int(red2)} dark2 {int(dark2)} arc frames {int(arc)} blue3 trails {int(blue3)}")
        kinds = {side: {k for (k,) in grab(lines, r"\[multiplayer\] pvp blow to player \d+ by (\S+)")}
                 for side, lines in (("host", host), ("client", client))}
        # the arc and the needle lazer lock onto enemies, not players: they hurt a player whose way
        # they cross, which the Wastelanders shooting back around can take from them in a run
        area = {"gun-red-2-shockwave", "gravity-spinner"}
        for side in ("host", "client"):
            c.check(scenario, f"{side}'s wave concussor and mass inverter hurt the other player",
                    area <= kinds[side], f"{sorted(kinds[side])}")
        both = kinds["host"] | kinds["client"]
        c.check(scenario, "an arc and a needle lazer hurt the other player",
                {"gun-blue-shot-2", "gun-blue-shot-3"} <= both, f"{sorted(both)}")
        for side, lines in (("host", host), ("client", client)):
            taken = grab(lines, r"\[multiplayer\] pvp hit by player \d+, damage (\S+)")
            c.check(scenario, f"{side} takes those blows", len(taken) >= 3, f"{len(taken)} hits")

    elif scenario == "steal-dup":
        def last_block(lines):
            blocks, cur = [], None
            for line in lines:
                if "vehicles near us:" in line:
                    cur = []
                    blocks.append(cur)
                elif cur is not None and "] vehicle #<" in line:
                    cur.append("mp-replica" if "#<mp-replica" in line else "own")
            return blocks[-1] if blocks else None
        thief, victim = last_block(client), last_block(host)
        c.check(scenario, "the thief drives one vehicle, no copy of the stolen one left",
                thief is not None and thief.count("own") == 1 and thief.count("mp-replica") == 0, f"{thief}")
        c.check(scenario, "the victim's vehicle is gone, the thief's shown (body and wheels)",
                victim is not None and victim.count("own") == 0 and victim.count("mp-replica") == 5, f"{victim}")

    elif scenario in ("daxter-drive", "daxter-drive-own"):
        driver, viewer, slot = (client, host, 1) if scenario == "daxter-drive" else (host, client, 0)
        states = [st for (st,) in grab(driver, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")]
        c.check(scenario, "Daxter gets in and drives, then is Daxter again on foot",
                "target-pilot-stance" in states and states[-1] == "target-indax-stance", f"{states}")
        flags = [(int(f, 16), int(v)) for f, v in grab(viewer, rf"\] remote {slot} flags #x(\w+) anim \S+ veh (\S+)")]
        c.check(scenario, "the other player sees Daxter at the wheel (Daxter model, driving)",
                any(f & 0x21 == 0x21 and v >= 0 for f, v in flags), f"{flags}")

    elif scenario == "daxter-guns":
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")
        taken = grab(client, r"\[multiplayer\] pvp hit by player \d+, damage (\S+)")
        kinds = sorted({k for (k,) in grab(host, r"\[multiplayer\] pvp blow to player \d+ by (\S+)")})
        c.check(scenario, "Daxter is hurt by the guns as Jak is", len(taken) >= 3 and len(kinds) >= 3,
                f"{len(taken)} hits, by {kinds}")
        states = [st for (st,) in grab(client, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")]
        c.check(scenario, "Daxter drives the stolen buggy", "target-pilot-stance" in states, f"{states}")
        shot_copies = [int(v) for (v,) in grab(host, r"shot copies (\d+)")]
        c.check(scenario, "the host draws the shots of Daxter's buggy", bool(shot_copies) and shot_copies[-1] > 0, f"shot copies {shot_copies[-1:] }")

    elif scenario == "vehicle-guns":
        blows = grab(host, r"\[multiplayer\] pvp blow to player \d+ by (\S+)")
        taken = grab(client, r"\[multiplayer\] pvp hit by player \d+, damage (\S+)")
        c.check(scenario, "a buggy's cannon hurts the other player (its shell's explosion)",
                len(taken) >= 1 and len(blows) >= 1, f"blows {[b for (b,) in blows]}, hits taken {len(taken)}")
        shot_copies = [int(v) for (v,) in grab(client, r"shot copies (\d+)")]
        c.check(scenario, "the guest draws the cannon's shells", bool(shot_copies) and shot_copies[-1] > 0, f"shot copies {shot_copies[-1:]}")

    elif scenario == "lobby":
        title = grab(host, r"\] lobby menu \S+ title (\d+) host-at (-?\d+) join-at (-?\d+)")
        c.check(scenario, "the title screen offers Host Game and Join Game",
                bool(title) and title[0][0] == "7" and "-1" not in title[0][1:], f"{title[:1]}")
        hm = [m for (m,) in grab(host, r"\] lobby menu (\S+) title")]
        gm = [m for (m,) in grab(client, r"\] lobby menu (\S+) title")]
        c.check(scenario, "Host Game opens the lobby at once, Join Game > Connect opens it too",
                "mp-lobby-host" in hm and "mp-join-game" in gm and "mp-lobby-join" in gm, f"host {hm}, guest {gm}")
        players = [int(n) for (n,) in grab(host, r"\] lobby mode 3 players (\d+)")]
        c.check(scenario, "the guest joins the host's lobby before any game is started",
                bool(players) and max(players) == 2, f"players in the host's lobby {players}")
        names = [n for (n,) in grab(host, r"\] name (\S+) keyboard")]
        c.check(scenario, "the on-screen keyboard types the new name (Host -> Jak3) and keeps it",
                names[:1] == ["Host"] and names[-1] == "Jak3", f"{names}")
        c.check(scenario, "square opens the game mode list", "mp-mode" in hm, f"{hm}")
        ready = [r for (r,) in grab(host, r"\] lobby mode 3 players \d+ coop \S+ all-ready (\S+)")]
        started = grab(host, r"game started: (\S+)")
        c.check(scenario, "X waits for the guest to be ready, then starts free roam",
                "#f" in ready and started[:1] == [("free",)], f"all-ready {ready}, started {started}")
        events = grab(host, r"\[multiplayer\] lobby countdown (started|cancelled|over)")
        c.check(scenario, "X starts the 10 s countdown, X again cancels it, the second one starts the game",
                [e for (e,) in events] == ["started", "cancelled", "started", "over"], f"{events}")
        hc = [int(ms) for (ms,) in grab(host, r"\] lobby countdown (\d+) ms")]
        gc = [int(ms) for (ms,) in grab(client, r"\] lobby countdown (\d+) ms")]
        c.check(scenario, "the host and the guest both see the countdown",
                any(5000 < ms <= 10000 for ms in hc) and any(5000 < ms <= 10000 for ms in gc), f"host {hc}, guest {gc}")
        gready = grab(client, r"\] lobby mode 2 players \d+ coop \S+ all-ready \S+ ready (\S+)")
        gcancel = [int(t) for (t,) in grab(client, r"\] lobby countdown 0 ms, cancelled (-?\d+) s ago")]
        c.check(scenario, "the guest sees the host cancel it, and X no longer unreadies it meanwhile",
                any(0 <= t <= 3 for t in gcancel) and gready[-3:] == [("#t",)] * 3, f"cancelled {gcancel}, ready {gready[-3:]}")
        sess = grab(host, r"session kind (\S+) unlock-pending (\S+) saves \d+ campaign \S+ closed (\d+)")
        win = grab(host, r"session sidekick-hidden \S+ sidekick \S+ character \d+ indax \S+ city-win (\S+)")
        c.check(scenario, "free roam is the end of the game: the story closed, everything unlocked (host)",
                bool(sess) and sess[-1][0] == "free" and int(sess[-1][2]) > 200 and win[-1:] == [("#t",)],
                f"{sess[-1:]}, city-win {win[-1:]}")
        gs = grab(client, r"session kind \S+ unlock-pending \S+ saves \d+ campaign \S+ closed (\d+) foreign (\S+)")
        gv = grab(client, r"session features (\S+) vehicles (\S+)")
        c.check(scenario, "the guest follows into it with everything unlocked too",
                bool(gs) and int(gs[-1][0]) > 200 and gs[-1][1] == "#t" and gv[-1:] and gv[-1][1] == "#xff",
                f"{gs[-1:]}, {gv[-1:]}")

    elif scenario == "player-list":
        own = grab(client, r"\] ping own (-?\d+) list (\S+) paused (\S+)")
        c.check(scenario, "holding SELECT shows the player list without pausing",
                any(l == "#t" and p == "#f" for _, l, p in own), f"{own}")
        c.check(scenario, "the guest knows its ping to the host", any(int(v) > 0 for v, _, _ in own), f"{[v for v, _, _ in own]}")
        seen = [int(v) for (v,) in grab(host, r"\] ping player 1 (-?\d+)")]
        c.check(scenario, "the host measures the guest's ping", bool(seen) and seen[-1] > 0, f"{seen}")
        health = grab(client, r"\] ping player 0 -?\d+ health " + NUM + "/" + NUM)
        c.check(scenario, "the list gives each player's health", bool(health) and float(health[-1][1]) > 0, f"{health[-1:]}")

    elif scenario == "daxter-fire":
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")
        feats = [int(f, 16) for (f,) in grab(client, r"daxter-gun at \S+ from Daxter, features #x([0-9a-f]+)")]
        guns = 0x3ffc0  # the gun's twelve mods (bits 6-17)
        c.check(scenario, "the weapons cheat gives every gun mod", bool(feats) and all(f & guns == guns for f in feats),
                f"features {[hex(f) for f in feats[:1]]}")
        shots = [(int(m), int(n)) for m, n in grab(client, r"daxter-gun out \S+ mod (\d+) shots (\d+)")]
        fired = sorted({m for i, (m, n) in enumerate(shots) if i > 0 and n > shots[i - 1][1]})
        c.check(scenario, "Daxter fires each of the 12 mods", fired == list(range(26, 38)), f"mods that fired {fired}")
        kinds = sorted({k for (k,) in grab(client, r"\[multiplayer\] pvp blow to player 0 by (\S+)")})
        taken = grab(host, r"\[multiplayer\] pvp hit by player 1")
        c.check(scenario, "his shots hurt the other player", len(kinds) >= 6 and len(taken) >= 6,
                f"{len(taken)} hits taken, by {kinds}")

    elif scenario in ("ride-seat", "ride-seat-daxter"):
        daxter = scenario == "ride-seat-daxter"
        riding = "target-indax-grab" if daxter else "target-grab-ride"
        # Jak: the wasteland car clips (front 268, back 269 on channel 0); Daxter: his torpedo's (541, 542)
        back = 542 if daxter else 269
        for side, lines in (("guest", client), ("host", host)):
            states = [st for (st,) in grab(lines, r"\] local pos \S+ \S+ \S+ hp \S+ state (\S+)")]
            c.check(scenario, f"the {side} rides as passenger ({riding})", riding in states, f"{states}")
            seat = grab(lines, r"seat passenger root " + " ".join([NUM] * 3) + r" anim (-?\d+)")
            c.check(scenario, f"the {side} sits in the driver's seated clip, on the seat point",
                    bool(seat) and int(seat[-1][3]) == back and max(abs(float(v)) for v in seat[-1][:3]) < 0.05,
                    f"{seat[-1:]}")
        if daxter:
            # bone 3 (upper_body) of each Daxter, passenger or driver, as high above his seat point
            sits = [float(y) for (y,) in grab(client + host, r"seat bone 3 \S+ " + NUM)]
            drives = grab(host + client, r"seat driver root .* anim (\d+)")
            c.check(scenario, "the driver sits in his torpedo's clips too", bool(drives) and all(int(a) == back for (a,) in drives),
                    f"{drives}")
            c.check(scenario, "both Daxters sit at one height (bone 3 within 0.15 m)",
                    len(sits) >= 3 and max(sits) - min(sits) < 0.15, f"bone 3 heights {sits}")

    elif scenario == "daxter-fire-anim":
        crashes = grab(host + client, r"\[multiplayer\] crash: (.*)")
        c.check(scenario, "no crash", not crashes, f"{crashes[:2]}")
        clips = [(int(i), float(f)) for i, f in grab(client, r"daxter-gun clip (-?\d+) frame " + NUM)]
        seen = [i for i, _ in clips]
        # red fire / aim, yellow aim (its shot is short), blue fire held, dark fire, put away
        c.check(scenario, "his clips: red fire, red aim, yellow, blue fire, dark fire, then none",
                len(seen) == 6 and seen[0] == 312 and seen[1] == 308 and seen[2] in (310, 313)
                and seen[3] == 296 and seen[4] == 314 and seen[5] == -1, f"{clips}")
        sel = grab(client, r"daxter-gun clip \S+ frame \S+ daxter " + NUM + r" select #x(\w+) hand " + NUM)
        held = [x for x in sel if x[1] != "0"]
        c.check(scenario, "upper body only, gun at his gun joint (gun-daxter 1)",
                bool(held) and all(x[1] == "f0000ffffff8" and float(x[0]) == 1.0 and float(x[2]) < 0.01 for x in held),
                f"{sel}")
        model = [(float(d), int(st, 16)) for d, st in grab(client, r"daxter-gun model " + NUM + r" from his right hand, status #x(\w+)")]
        c.check(scenario, "the gun model drawn in his hand while held",
                len(model) >= 5 and all(d < 0.4 for d, _ in model[:5]), f"{model}")
        remote = grab(host, r"daxter-gun player 1 clip (\d+)")
        c.check(scenario, "the host sees his clip on his upper body", bool(remote), f"{remote}")

    elif scenario == "outline":
        px = [int(v) for (v,) in grab(client, r"outlines drawn \d+ hidden pixels (\d+)")]
        table = grab(client, r"outline player 0 color (\S+)")
        c.check(scenario, "the other player is outlined in its color", table[-1:] == [("#x80181880",)], f"{table[-1:]}")
        c.check(scenario, "in the open: no outline drawn (a few pixels of feet in the ground)",
                len(px) >= 2 and max(px[:2]) < 400, f"hidden pixels {px[:2]}")
        c.check(scenario, "behind the buggy: its outline drawn through it", bool(px) and max(px[-3:]) > 600,
                f"hidden pixels {px[-3:]}")

    elif scenario == "coop-campaign":
        third = s.get("third", (None, []))[1]
        hm = [m for (m,) in grab(host, r"\] lobby menu (\S+) title")]
        c.check(scenario, "co-op from the mode list, then the co-op page and the mission list",
                "mp-coop-start" in hm and "mp-mission" in hm, f"{hm}")
        lines = [l for (l,) in grab(host, r"\] lobby character \d+ config-mode \d+ (coop-start \d+ mission -?\d+ max \d+)")]
        c.check(scenario, "co-op chosen: two players at most, a mission chosen in the list",
                any(l.startswith("coop-start 2 mission 10") and l.endswith("max 2") for l in lines), f"{sorted(set(lines))}")
        full = grab(third, r"session status (The server is full)")
        c.check(scenario, "a third player is refused", bool(full), f"{full[:1]}")
        started = [t for (t,) in grab(host, r"game started: campaign \(co-op start (\d+)")]
        c.check(scenario, "X starts the mission, then later continues the campaign", started[:1] == ["2"] and "1" in started,
                f"co-op starts {started}")
        sess = [(int(sv), int(cl)) for sv, cl in grab(host, r"session kind campaign unlock-pending \S+ saves (\d+) campaign \S+ closed (\d+)")]
        saved = grab(host, r"campaign saved \((\d+), continue (\S+)\)")
        c.check(scenario, "the host's save goes into the multiplayer campaign", bool(saved), f"{saved[:1]}")
        refused = grab(client, r"no save in another player's world")
        c.check(scenario, "the guest saves nothing", bool(refused), f"{len(refused)} refused")
        hidden = grab(host, r"session sidekick-hidden (\S+) sidekick (\S+) character 0")
        c.check(scenario, "Jak has no Daxter on his shoulder while the guest plays Daxter, and has him back after",
                ("#t", "#f") in hidden and hidden[-1] == ("#f", "#t"), f"{hidden}")
        c.check(scenario, "continuing restores the campaign saved (story steps closed after it open again)",
                len(sess) >= 3 and sess[-1][1] < max(cl for _, cl in sess) and sess[-1][1] == sess[1][1],
                f"closed {[cl for _, cl in sess]}")

    elif scenario == "powers":
        shown = grab(host, r"power puppet 1 blend (\S+) dark (\S+) effect #x(\w+) fade (\S+)")
        c.check(scenario, "the host shows the guest's Dark Jak (blend shapes)",
                bool(shown) and float(shown[0][0]) > 0.9 and float(shown[0][1]) > 0.9, f"{shown[:1]}")
        c.check(scenario, "then its Light Jak (no dark shape, its glow)",
                len(shown) >= 2 and float(shown[1][1]) < 0.1 and int(shown[1][2], 16) != 0, f"{shown[1:2]}")

    elif scenario in ("far-split", "far-split-host"):
        for side, lines in (("host", host), ("client", client)):
            far = [float(d) for (d,) in grab(lines, r"\] stretch \d+ bone \d+ at (\S+)")]
            c.check(scenario, f"{side}: no part of the other's model stretched across the world (cloth)",
                    all(d < 5.0 for d in far), f"farthest bone from the root {max(far) if far else None} m")
        motion = counter(host + client, r"motion own (\d+) moved (\d+) copies (\d+) moved (\d+)")
        c.check(scenario, "NPCs near each player keep moving (own and copies)",
                any(m[1] > 0 for m in motion) and any(m[3] > 0 for m in motion),
                f"{[tuple(int(v) for v in m) for m in motion[:8]]}")

    elif scenario == "npc-far-run":
        paused = counter(host, r"near another player (\d+) stock-paused \d+ run (\d+)")
        c.check(scenario, "the host's NPCs near the distant guest run", bool(paused) and all(n == r for n, r in paused) and any(n > 0 for n, r in paused),
                f"near/run {[(int(n), int(r)) for n, r in paused]}")
        motion = counter(client, r"motion own \d+ moved \d+ copies (\d+) moved (\d+)")
        c.check(scenario, "the guest sees them move", any(m[1] > 0 for m in motion), f"{[(int(a), int(b)) for a, b in motion]}")

    elif scenario == "drive":
        sys.path.insert(0, str(ROOT / "scripts" / "mp-test"))
        import compare_poses
        r = compare_poses.path_report(compare_poses.paths("\n".join(host), "own"), compare_poses.paths("\n".join(client), "copy"))
        c.check(scenario, "the copy of the driven buggy follows its path", bool(r) and r["moved"] > 20 and r["p95"] < 0.10,
                f"{r['samples'] if r else 0} samples over {r['moved'] if r else 0:.1f} m, median {r['median'] * 100 if r else 0:.1f} cm, "
                f"95% under {r['p95'] * 100 if r else 0:.1f} cm, largest {r['largest'] * 100 if r else 0:.1f} cm")
        c.check(scenario, "as smoothly as the owner's", bool(r) and r["jerk_copy"] <= 1.5 * r["jerk_own"] + 0.2,
                f"speed change per frame {r['jerk_own'] if r else 0:.2f} m/s (owner), {r['jerk_copy'] if r else 0:.2f} m/s (copy)")


def main() -> int:
    names = sys.argv[1:] or sorted(p.name for p in (ROOT / "scripts/mp-test/scenarios").iterdir() if p.is_dir())
    c = Checker()
    for name in names:
        path = OUT / name / "summary.txt"
        print(f"{name}")
        if not path.exists():
            c.check(name, "summary", False, f"{path} missing")
            continue
        run_checks(name, sections(path.read_text(encoding="utf-8", errors="replace")), c)
    print(f"\n{c.passed} passed, {c.failed} failed")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
