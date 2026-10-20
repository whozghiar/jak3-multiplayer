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


def pause_logs(lines):
    """Every log-pause, as a dict: master, applied, world, resume, live, prevent, menu, target, pads, list."""
    out = []
    for i, line in enumerate(lines):
        m = re.search(r"\] pause master (\S+) applied (\S+) world (\S+) resume (\d+) live (\S+)", line)
        if not m or i + 2 >= len(lines):
            continue
        n = re.search(r"\] pause prevent-progress (\S+) menu-mode (\S+)", lines[i + 1])
        k = re.search(r"\] pause clocks target (\d+) entity \d+ pad-frames (\d+) pauses \d+ list (\S+)", lines[i + 2])
        if n and k:
            out.append(dict(master=m[1], applied=m[2], world=m[3], resume=int(m[4]), live=m[5], prevent=n[1],
                            menu=n[2], target=int(k[1]), pads=int(k[2]), list=k[3]))
    return out


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
        # since 2026-10-19 a Jak played online carries no Daxter of the game (Daxter is a player, 4.16)
        c.check(scenario, "the host, a Jak player, carries no Daxter of the game: none drawn on the guest",
                "#t" not in dax, f"skel-sidekick drawn {dax}")
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
            climb = grab(host, r"\] climb rider (-?\d+) climbs \d+ frames \d+ playing \S+ sidekick (\S+)")
            c.check(scenario, "Daxter rides on the driving host's shoulder: the host's own Daxter is shown there for him",
                    any(f & 0x8020 == 0x8020 for f in flags) and climb[-1:] == [("1", "#t")], f"{[hex(f) for f in flags]}, {climb}")
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

    elif scenario == "pause":
        hp, gp = pause_logs(host), pause_logs(client)
        menu = [d for d in hp if d["master"] == "progress"]
        c.check(scenario, "START opens the menu over the running world (no progress mask, no still copy, clocks on)",
                len(menu) >= 2 and all(d["prevent"] == "#f" and d["menu"] == "#f" for d in menu)
                and menu[-1]["target"] > menu[0]["target"], f"{[(d['prevent'], d['menu'], d['target']) for d in menu]}")
        me = grab(host, r"\] me (\S+) \S+ (\S+) speed")
        c.check(scenario, "the stick pushed while the menu is open does not move the player",
                len(me) >= 3 and me[1] == me[2] and menu and menu[-1]["pads"] > 0, f"{me[:3]}, pad frames hidden {menu[-1:] and menu[-1]['pads']}")
        before = [d for d in gp if d["world"] == "#f"][:2]
        moved = [int(n) for (n,) in grab(client, r"\] motion own \d+ moved \d+ copies \d+ moved (\d+)")]
        c.check(scenario, "the guest is not paused by the host's menu, the host's NPCs move there",
                len(before) == 2 and all(d["master"] == "game" for d in before) and any(n > 0 for n in moved),
                f"{[d['master'] for d in before]}, copies moved {moved}")
        paused = [d for d in gp if d["applied"] == "#t" and d["resume"] == 0]
        c.check(scenario, "the host's SELECT pauses the guest (stock pause, its clocks stopped, START refused)",
                len(paused) >= 2 and all(d["master"] == "pause" for d in paused) and paused[0]["target"] == paused[-1]["target"]
                and bool(grab(host, r"the host pauses the game for everyone")), f"{[(d['master'], d['target']) for d in paused]}")
        resuming = [d["resume"] for d in gp if d["resume"] > 0]
        after = [d for d in gp if d["world"] == "#f" and d["master"] in ("game", "progress")][2:]
        c.check(scenario, "SELECT again: everyone sees the 3 s countdown, then plays on",
                bool(resuming) and 0 < resuming[0] <= 3000 and bool(after) and bool(grab(host, r"the game resumes(?! in)")),
                f"resume ms {resuming}, then {[d['master'] for d in after]}")
        held = [d for d in hp if d["list"] == "#t"]
        c.check(scenario, "SELECT held shows the player list without pausing",
                bool(held) and all(d["world"] == "#f" and d["master"] == "game" for d in held), f"{[(d['list'], d['world']) for d in held]}")
        gmenu = [d for d in gp if d["master"] == "progress" and d["live"] == "#t"]
        c.check(scenario, "the guest's own START menu runs over its world and does not pause the host",
                bool(gmenu) and gmenu[0]["prevent"] == "#f" and all(d["master"] != "pause" for d in hp[-3:]),
                f"guest {[(d['master'], d['prevent']) for d in gmenu]}, host {[d['master'] for d in hp[-3:]]}")

    elif scenario == "skin":
        cfg = ROOT / "out" / "mp-test" / "skin" / "client" / "OpenGOAL" / "jak3" / "settings" / "multiplayer.cfg"
        saved = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        c.check(scenario, "the guest's outfit is saved in its config",
                "skin=0,ffe02020,0,e5202020,0,ff20e020,0,0,ff3060ff" in saved, f"{[l for l in saved.splitlines() if l.startswith('skin=')]}")
        got = grab(host, r"\] skin player 1 (.*)")
        c.check(scenario, "the host receives the guest's outfit (shirt, pants, scarf, hair)",
                bool(got) and got[-1][0].split()[:9] == ["0", "ffe02020", "0", "e5202020", "0", "ff20e020", "0", "0", "ff3060ff"],
                f"{got[-1:]}")
        worn = grab(host, r"\] skin models (\d+) draws (\d+):(.*)")
        c.check(scenario, "the guest's puppet is drawn with it on the host",
                bool(worn) and "mp-puppet" in worn[0][2] and int(worn[-1][1]) > int(worn[0][1]), f"{worn[:1]}, draws then {worn[-1][1] if worn else None}")
        page = grab(host, r"\] skin editing #t group (\d+) slider \d+ draft-shirt (\w+) preview (\S+)")
        prev = grab(host, r"\] skin preview \"(\w+)\" status #x(\w+) .*")
        c.check(scenario, "the Appearance page edits a draft with a turning preview of Jak, then of Daxter",
                any(g == "1" and d != "0" and p == "#t" for g, d, p in page) and any(g == "10" for g, _, _ in page)
                and {"jakb", "daxter"} <= {n for n, _ in prev} and all(int(st, 16) & 8 for _, st in prev),
                f"{page[-2:]}, previews {prev}")
        own = grab(host, r"\] skin own (.*)")
        c.check(scenario, "X keeps it: saved (shirt and Daxter's fur) and the preview gone",
                bool(own) and own[-1][0].split()[1] == "68ff3010" and own[-1][0].split()[10] == "68ff3040"
                and bool(grab(host, r"outfit saved")) and grab(host, r"\] skin editing (\S+)")[-1] == ("#f",),
                f"{own[-1:]}")
        gseen = grab(client, r"\] skin player 0 (.*)")
        c.check(scenario, "the guest receives the host's new outfit",
                bool(gseen) and gseen[-1][0].split()[1] == "68ff3010", f"{gseen[-1:]}")

    elif scenario == "ride-back":
        g = grab(client, r"\] seat index (\d+) of (\d+), from the driver's seat " + NUM)
        root = grab(client, r"\] seat passenger root " + NUM + " " + NUM + " " + NUM)
        c.check(scenario, "the guest rides on the back of a one-seat vehicle (seat 1 of 1), in the seat",
                bool(g) and g[-1][:2] == ("1", "1") and 0.5 < float(g[-1][2]) < 2.0
                and bool(root) and all(abs(float(v)) < 0.05 for v in root[-1]), f"{g[-1:]}, root {root[-1:]}")

    elif scenario == "ride-multi":
        viewer = s.get("viewer", (None, []))[1]
        a = grab(client, r"\] seat index (\d+) of (\d+)")
        b = grab(viewer, r"\] seat index (\d+) of (\d+)")
        c.check(scenario, "two passengers ride the host's three-seat car, each in its own seat (1 and 2)",
                a[-1:] == [("1", "3")] and b[-1:] == [("2", "3")], f"first {a[-1:]}, second {b[-1:]}")

    elif scenario == "scene-skip":
        hs = grab(host, r"\] skip scene (\S+) voted (\S+) votes (\d+) viewers (\d+) skips (\d+)")
        gs = grab(client, r"\] skip scene (\S+) voted (\S+) votes (\d+) viewers (\d+) skips (\d+)")
        c.check(scenario, "the guest watches the host's cutscene too (2 viewers)",
                any(v == "2" for _, _, _, v, _ in gs) and any(v == "2" for _, _, _, v, _ in hs), f"host {hs[:1]}, guest {gs[:1]}")
        c.check(scenario, "the host's triangle asks (1/2): the cutscene goes on",
                any(n != "none" and vo == "#t" and v == "1" and w == "2" for n, vo, v, w, _ in hs), f"{hs}")
        c.check(scenario, "the guest sees the host's vote before voting",
                any(n != "none" and vo == "#f" and v == "1" for n, vo, v, _, _ in gs), f"{gs}")
        c.check(scenario, "once both asked, the cutscene is skipped on both machines",
                bool(hs) and bool(gs) and hs[-1][0] == "none" and hs[-1][4] == "1" and gs[-1][0] == "none" and gs[-1][4] == "1",
                f"host {hs[-1:]}, guest {gs[-1:]}")

    elif scenario == "beam":
        hb = grab(host, r"\] beams (\d+) here (\d+)/(\d+) near (\S+) held (\d+) reached (\d+)")
        hd = [float(d) for d, _ in grab(host, r"\] beam 0 at " + NUM + " from us, hold " + NUM)]
        c.check(scenario, "the host at the beam is not counted arrived while the guest is away (1/2)",
                any(h == "1" and p == "2" and int(held) > 0 and r == "0" for _, h, p, _, held, r in hb) and any(d < 1.0 for d in hd),
                f"{hb}, distances {hd}")
        gb = grab(client, r"\] beams (\d+) here (\d+)/(\d+) near (\S+) held (\d+) reached (\d+)")
        c.check(scenario, "the guest sees the count, comes, and the mission goes on for both",
                any(h == "1" and n == "#t" for _, h, _, n, _, _ in gb) and gb[-1:] and gb[-1][5] == "1" and hb[-1][5] == "1",
                f"guest {gb}, host {hb[-1:]}")

    elif scenario == "race-board":
        hr = grab(host, r"\] race waiting (\S+) aboard (\d+)/(\d+) starts (\d+) gifts (\d+) exit (\S+)")
        gm = grab(client, r"\] race me driving (\S+) riding (\S+) perched (\S+) state (\S+)")
        c.check(scenario, "the guest on foot gets a turtle of its own beside the host: the start goes (1)",
                any("a vehicle for the race start of player 0" in l for l in client) and bool(gm) and gm[0][0] == "#t"
                and len(hr) > 1 and hr[1][3] == "1", f"guest {gm[:1]}, host {hr[:2]}")
        c.check(scenario, "L1 + triangle at the host's turtle does not ride along in a race: the start waits (1/2) (2)",
                len(gm) > 2 and gm[2][1] == "#f" and gm[2][0] == "#f" and len(hr) > 2 and hr[2][0] == "#t" and hr[2][1] == "1",
                f"guest {gm[2:3]}, host {hr[2:3]}")
        c.check(scenario, "driving its own turtle again, the start goes (2)",
                len(gm) > 3 and gm[3][0] == "#t" and len(hr) > 4 and hr[4][3] == "2", f"guest {gm[3:4]}, host {hr[3:5]}")
        c.check(scenario, "Daxter on foot holds the start (1/2), the host may leave its vehicle meanwhile (3)",
                any(w == "#t" and a == "1" and p == "2" and e == "#t" for w, a, p, _, _, e in hr), f"{hr}")
        c.check(scenario, "Daxter's triangle at the driving host: no shoulder in a race (3)",
                len(gm) > 5 and gm[5][2] == "#f" and not any(p == "#t" for _, _, p, _ in gm), f"{gm[4:6]}")
        c.check(scenario, "Daxter gets a turtle of his own and drives it as Daxter: the start goes (3)",
                sum(1 for l in client if "a vehicle for the race start of player 0" in l) >= 2
                and any(d == "#t" and st.startswith("target-pilot") for d, _, _, st in gm[6:])
                and hr[-1][3] == "3", f"guest {gm[6:]}, host {hr[-1:]}")

    elif scenario == "race-continue":
        hm = grab(host, r"\] me " + NUM + " " + NUM + " " + NUM + r" speed \S+ state (\S+)")
        gm = grab(client, r"\] me " + NUM + " " + NUM + " " + NUM + r" speed \S+ state (\S+)")
        c.check(scenario, "the guest moves back before its turtle appears",
                any("race start: our vehicle 6.0 behind" in l for l in client), "")
        apart = math.dist((float(hm[-1][0]), float(hm[-1][2])), (float(gm[-1][0]), float(gm[-1][2]))) if hm and gm else 0.0
        c.check(scenario, "each player drives its own turtle, apart from the other's",
                bool(hm) and bool(gm) and hm[-1][3] == gm[-1][3] == "target-pilot-stance" and apart > 3.0,
                f"host {hm[-1:]}, guest {gm[-1:]}, {apart:.1f} m apart")

    elif scenario == "dark-blast":
        hd = grab(host, r"\] dark latch \S+ eco \S+ min \S+ state (\S+) bolts retargeted (\d+)")
        gd = grab(client, r"\] dark latch \S+ eco \S+ min \S+ state (\S+) bolts retargeted (\d+)")
        crashed = [l for l in host + client if "crash:" in l]
        c.check(scenario, "the host's dark blast goes off next to the guest, its bolt to the guest's hurtbox follows its position",
                any(st == "target-darkjak-bomb1" for st, _ in hd) and bool(hd) and int(hd[-1][1]) >= 1, f"{hd[-2:]}")
        c.check(scenario, "the guest is hit by it, and its own blast hits the host",
                any("pvp hit by player 0" in l for l in client) and any("pvp hit by player 1" in l for l in host)
                and any(st == "target-darkjak-bomb1" for st, _ in gd), f"guest {gd[-1:]}")
        c.check(scenario, "no game stops (before the fix: access violation in vector<-cspace! from lightning-tracker)",
                not crashed, f"{crashed[:2]}")

    elif scenario == "dark-blast-coop":
        hd = grab(host, r"\] dark latch \S+ eco \S+ min \S+ state (\S+) bolts retargeted (\d+)")
        hits = counter(client, r"pvp hits taken (\d+) reacted (\d+) last pushed " + NUM)
        crashed = [l for l in host + client if "crash:" in l]
        c.check(scenario, "co-op: the host's dark blast goes off next to the guest, its bolt follows the guest's position",
                any(st == "target-darkjak-bomb1" for st, _ in hd) and bool(hd) and int(hd[-1][1]) >= 1, f"{hd[-2:]}")
        c.check(scenario, "co-op: the teammate takes no damage, no game stops",
                bool(hits) and hits[-1][0] == 0 and not crashed, f"{hits[-1:]}, {crashed[:2]}")

    elif scenario in ("race-finish", "race-finish-win"):
        hp = grab(host, r"\] race place (\d+) progress (\S+) done (\d+)")
        gp = grab(client, r"\] race place (\d+) progress (\S+) done (\d+)")
        c.check(scenario, "Kleiver's real race runs to the finish on both machines (the racers' AI drives the players)",
                any(d != "0" for _, _, d in hp) and any(d != "0" for _, _, d in gp) and float(hp[-1][1]) > 900 and float(gp[-1][1]) > 900,
                f"host {hp[-1:]}, guest {gp[-1:]}")
        c.check(scenario, "the guest's place counts the host's racers and the host ahead of it",
                any(int(p) >= 1 for p, prog, d in gp if d == "0" and float(prog) > 0.5), f"{sorted(set(p for p, _, _ in gp))}")
        hres = grab(host, r"race result: (\S+)")
        gres = grab(client, r"race result: (\S+)")
        if scenario == "race-finish":
            c.check(scenario, "both finished behind a racer: lost on both machines",
                    hres[-1:] == [("lost",)] and gres[-1:] == [("lost",)], f"host {hres}, guest {gres}")
        else:
            c.check(scenario, "the racers gone, the first to finish waits for the other, then won on both machines",
                    any("our result waits for the other players" in l for l in host + client)
                    and hres[-1:] == [("won",)] and gres[-1:] == [("won",)]
                    and {hp[-1][0], gp[-1][0]} == {"0", "1"}, f"host {hres} {hp[-1:]}, guest {gres} {gp[-1:]}")

    elif scenario == "pre-game":
        crashed = [l for l in host + client if "crash:" in l]
        replays = [l for l in client if "fx replay" in l]
        c.check(scenario, "co-op at Spargus's pre-game: both machines play it, the guest wins (its resolution streams)",
                any("mission wascity-pre-game-resolution starts here" in l for l in host)
                and any("cutscene wascity-pre-game-res of player 1 starts here too" in l for l in host)
                and any("pre-game-res" in l for l in client),
                f"{len(replays)} effects replayed on the guest")
        c.check(scenario, "the host's effects replayed on the guest, none in an object's local space; no game stops (before the fix: access violation in *mp-stock-launch-spawn* +#x143)",
                bool(replays) and not crashed, f"{crashed[:1]}")

    elif scenario == "mission-trigger":
        hme = grab(host, r"\] me (\S+) (\S+) (\S+) speed \S+ state (\S+)")
        gme = grab(client, r"\] me (\S+) (\S+) (\S+) speed \S+ state (\S+)")
        hs = grab(host, r"\] state task-manager-kanga-lizard (\S+)")
        gs = grab(client, r"\] state task-manager-kanga-lizard (\S+)")
        c.check(scenario, "the guest went to Spargus's doors, far from Kleiver",
                any(abs(float(x) - 2359.3) + abs(float(z) + 376.4) > 300 for x, _, z, _ in gme[:1]), f"{gme[:1]}")
        c.check(scenario, "the host talks to Kleiver: no hunt before, its task manager runs after (a real start)",
                bool(hs) and hs[0][0] == "none" and hs[-1][0] == "active"
                and any("mission wascity-chase-resolution starts here" in l for l in host), f"{hs}")
        c.check(scenario, "the guest skips the intro it cannot load (no grab left), is told and taken next to the host",
                any("cutscene wascity-chase-intro of player 0 is not loaded here: not played" in l for l in client)
                and any("player 0 starts wascity-chase-resolution: we go there" in l for l in client)
                and bool(gme) and bool(hme)
                and abs(float(gme[-1][0]) - float(hme[-1][0])) + abs(float(gme[-1][2]) - float(hme[-1][2])) < 10
                and not gme[-1][3].endswith("grab"), f"guest {gme[-1:]}, host {hme[-1:]}")
        c.check(scenario, "the hunt runs on the guest too (its own task manager), the host stays (it started it)",
                bool(gs) and gs[-1][0] == "active" and not any("player 1 starts wascity-chase-resolution: we go there" in l for l in host),
                f"{gs[-1:]}")

    elif scenario == "race-seventh":
        gp = [int(p) for (p,) in grab(client, r"\] race place (\d+) progress \S+ done 0")]
        c.check(scenario, "with two more players counted ahead, the guest's HUD place reaches 7th and 8th (sprites 7 and 8)",
                6 in gp and 7 in gp, f"places {sorted(set(gp))}")

    elif scenario == "yellow2-shot":
        w = grab(client, r"\] weapons shown red2 \d+ dark2 \d+ arc \d+ blue3 \d+ yellow2 (\d+)")
        c.check(scenario, "the host's beam reflexor shots draw their trail on the guest (screens every second)",
                bool(w) and int(w[-1][0]) >= 10, f"{w}")

    elif scenario == "mounts-dup":
        hs = grab(host, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")
        gf = grab(client, r"\] fldax on (\S+) rides (\d+) art-on (\S+) daxter (\S+) state (\S+)")
        hc = grab(host, r"\] copy 1 skel-flut id \d+ owner-hides (\S+) drawn (\S+)")
        gm = grab(client, r"\] me (\S+) \S+ (\S+) speed")
        c.check(scenario, "in the kanga-rat hunt both players ride a lizard: Jak the hunt's, Daxter his own copy of it",
                any(st == "target-flut-stance" for (st,) in hs) and any(on == "#t" and st == "target-flut-stance" for on, _, _, _, st in gf),
                f"host {hs[-1:]}, guest {gf[-1:]}")
        c.check(scenario, "the host draws the guest's lizard, the guest rides it away from the host's",
                ("#f", "#t") in hc and len(gm) >= 2 and abs(float(gm[-1][0]) - float(gm[0][0])) + abs(float(gm[-1][1]) - float(gm[0][1])) > 5,
                f"{hc[-1:]}, guest {gm[:1]} -> {gm[-1:]}")

    elif scenario == "menu-buttons":
        for side, lines in (("host", host), ("guest", client)):
            inp = grab(lines, r"\] input state (\S+) gun (-?\d+) ammo (\S+) (\S+) (\S+) (\S+) daxter-shots (\d+) menu (\S+)")
            states = [st for (st,) in grab(lines, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")]
            under = [r for r in inp if r[7] == "#t"]
            c.check(scenario, f"{side}: under the START menu (opened with START), R1, square, L1, the D-pad, X and triangle do nothing: no shot, no gun change, no jump or attack, also with X just after it closes",
                    bool(inp) and len(under) >= 2 and all(r[1:7] == inp[0][1:7] for r in inp)
                    and not any("jump" in st or "attack" in st for st in states) and len(states) >= 4,
                    f"inputs {[(r[0], r[1], r[2], r[7]) for r in inp]}, states {states}")

    elif scenario == "daxter-moves":
        guns = grab(client, r"\] daxter-gun out (\S+) mod (\d+) shots (\d+)")
        c.check(scenario, "Daxter: the D-pad takes his gun out, the mod just chosen in his hand, without firing",
                len(guns) >= 2 and guns[0][0] == "#f" and guns[1][0] == "#t" and guns[1][1] != guns[0][1] and guns[1][2] == "0",
                f"{guns}")
        co = grab(client, r"\] coop #t authority \S+ character \S+ perch (-?\d+) perched \S+ state (\S+)")
        c.check(scenario, "next to the standing host's Jak, triangle alone does nothing, L1 + triangle climbs on its shoulder, X puts him down",
                len(co) >= 4 and co[0][0] == "-1" and co[1][0] == "-1" and co[2] == ("0", "target-indax-grab")
                and co[3] == ("-1", "target-indax-stance") and any("player 1 climbs on our shoulder" in l for l in host),
                f"{co}")

    elif scenario == "mission-switch":
        hme = grab(host, r"\] me (\S+) \S+ (\S+) speed \S+ state (\S+)")
        gme = grab(client, r"\] me (\S+) \S+ (\S+) speed \S+ state (\S+)")
        gj = grab(client, r"\] join continue (\S+) title")
        c.check(scenario, "the host chooses the palace ruins' attack in game: both players at its start, each in its own Rhino",
                any("host plays mission palace-ruins-attack" in l for l in host) and gj[-1:] == [("rubblea-rhino",)]
                and len(hme) >= 2 and hme[-1][2] == "target-pilot-stance" and len(gme) >= 2 and gme[-1][2] == "target-pilot-stance"
                and math.dist((float(hme[-1][0]), float(hme[-1][1])), (float(gme[-1][0]), float(gme[-1][1]))) < 40.0,
                f"host {hme[-1:]}, guest {gme[-1:]}, guest continue {gj}")
        hv = grab(host, r"\] remote 1 flags #x([0-9a-fA-F]+) anim \S+ veh (-?\d+) puppet \S+ drawn (\S+)")
        c.check(scenario, "the host sees the guest drive its Rhino (vehicle 17)",
                bool(hv) and int(hv[-1][0], 16) & 1 and hv[-1][1] == "17" and hv[-1][2] == "#t", f"{hv[-1:]}")
        c.check(scenario, "no game stops (the user's crash there was the shadow renderer's overflow skip, Shadow2.cpp)",
                not any("Assertion failed" in l for l in host + client), "")

    elif scenario == "mounts-coop":
        FL = r"\] fluts idle (\d+) hidden (\d+) seconds-near (\d+) riding (\S+) placed (\d+) hides (\d+) shows (\d+)"
        hf = [(int(i), int(h), int(sn), r, int(pl), int(hd), int(sh)) for i, h, sn, r, pl, hd, sh in grab(host, FL)]
        gf = [(int(i), int(h), int(sn), r, int(pl), int(hd), int(sh)) for i, h, sn, r, pl, hd, sh in grab(client, FL)]
        c.check(scenario, "co-op: the host has a second lizard beside the hunt's, two waiting before anyone rides",
                any(f[4] >= 1 and f[0] >= 2 and f[2] >= 1 for f in hf), f"{hf[:3]}")
        c.check(scenario, "the guest gets on a lizard: the host's copy of it hides, one still waits for the host",
                any("player 1 gets on a lizard: ours there hides" in l for l in host)
                and any(f[5] >= 1 and f[1] >= 1 and f[0] >= 1 and f[3] == "#f" for f in hf), f"{hf}")
        c.check(scenario, "both ride: the host gets on the other lizard; the guest's copy of it hides there",
                any(f[3] == "#t" for f in hf) and any(f[3] == "#t" for f in gf)
                and any("player 0 gets on a lizard: ours there hides" in l for l in client), f"host {hf}, guest {gf}")
        c.check(scenario, "the guest gets off: a lizard waits on the host's screen where it did",
                any("player 1 got off its lizard: one waits there" in l for l in host) and bool(hf) and hf[-1][6] >= 1,
                f"{hf[-1:]}")

    elif scenario == "eyes-before":
        ey = grab(client, r"\] eyes player 0 slot \d+ by \S+ puppet (\S+) target (\S+)")
        c.check(scenario, "with the fix undone, the host's Jak takes the eyes our hidden model writes (the bug, screenshot)",
                bool(ey) and ey[-1] == ("#f", "#t"), f"{ey}")

    elif scenario == "lizard-attack":
        hs = grab(host, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")
        gc = grab(client, r"\] copy 0 skel-flut id \d+ owner-hides (\S+) drawn (\S+)")
        hits = counter(host, r"pvp hits taken (\d+) reacted (\d+) last pushed " + NUM)
        c.check(scenario, "Daxter punches and shoots the lizard Jak rides: Jak keeps riding, unhurt",
                len(hs) >= 3 and all(st == "target-flut-stance" for (st,) in hs) and bool(hits) and hits[-1][0] == 0
                and not any("player 1 hit" in l for l in host), f"{hs}, {hits[-1:]}")
        c.check(scenario, "the guest keeps seeing the lizard under Jak, before, during and after",
                len(gc) >= 3 and all(x == ("#f", "#t") for x in gc), f"{gc}")

    elif scenario == "race-result":
        hs = grab(host, r"\] race sim place (\d+) wait (\S+) won (\S+)")
        gs = grab(client, r"\] race sim place (\d+) wait (\S+) won (\S+)")
        c.check(scenario, "the guest, 4th of its racers, is 5th with the host ahead; the host stays 1st",
                ("4", "#f", "racing") in gs and all(p == "0" for p, _, _ in hs), f"guest {sorted(set(gs))}")
        c.check(scenario, "the host finished first waits while the guest races",
                any(w == "#t" for _, w, _ in hs), f"{hs}")
        c.check(scenario, "both finished ahead of every racer: won on both machines",
                any(w == "#f" and won == "#t" for _, w, won in hs) and ("1", "#f", "#t") in gs, f"host {hs}, guest {gs}")
        c.check(scenario, "the guest finished behind a racer: lost on both machines",
                bool(hs) and hs[-1][2] == "#f" and gs[-1][2] == "#f", f"host {hs[-1:]}, guest {gs[-1:]}")

    elif scenario == "mission-start":
        me = grab(client, r"\] me (\S+) (\S+) (\S+) speed")
        hme = grab(host, r"\] me (\S+) (\S+) (\S+) speed")
        c.check(scenario, "the guest went far from the host's mission (another level)",
                len(me) >= 2 and abs(float(me[0][0]) - 2270.8) + abs(float(me[0][2]) + 447.0) > 100.0, f"{me[:1]}")
        c.check(scenario, "the host's mission starts: the guest is told and goes next to the host",
                any("mission arena-training-1-collect starts here" in l for l in host)
                and any("player 0 starts arena-training-1-collect: we go there" in l for l in client)
                and bool(hme) and len(me) >= 2
                and abs(float(me[-1][0]) - float(hme[-1][0])) + abs(float(me[-1][2]) - float(hme[-1][2])) < 10.0,
                f"guest {me[-1:]}, host {hme[-1:]}")
        st = grab(client, r"\] state task-manager-arena-training (\S+)")
        c.check(scenario, "the mission runs on the guest too (its own task manager)",
                sum(1 for l in client if "mission arena-training-1-collect starts here" in l) >= 1 and bool(st) and st[-1][0] != "none", f"{st}")

    elif scenario == "mission-select":
        acts = grab(host, r"\] act 2 missions (\d+) \S+ (\d+):")
        chosen = grab(host, r"the host starts mission (\d+) for every player")
        c.check(scenario, "the host picks act 2's second mission in START > Options > Multiplayer; its game starts there",
                bool(acts) and int(acts[-1][0]) >= 2 and chosen == [(acts[-1][1],)]
                and any(f"game started: campaign (co-op start 2, mission {acts[-1][1]})" in l for l in host),
                f"act 2 {acts[-1:]}, chosen {chosen}")
        hj = grab(host, r"\] join continue (\S+) title")
        gj = grab(client, r"\] join continue (\S+) title")
        gd = grab(client, r"\] join distance to the host ([\d.]+)")
        c.check(scenario, "the guest loads the host's world again and comes next to the host",
                any("we load its world again" in l for l in client)
                and sum(1 for l in client if "in the host's world" in l) >= 2
                and bool(hj) and bool(gj) and gj[-1] == hj[-1] and bool(gd) and float(gd[-1][0]) < 10.0,
                f"host {hj[-1:]}, guest {gj}, distance {gd}")
        hm = grab(host, r"\] merchant spot (-?\d+) ")
        cm = grab(client, r"\] merchant spot (-?\d+) ")
        c.check(scenario, "no Kleiver in co-op (no spot, host and guest)",
                hm == [("-1",)] and cm == [("-1",)], f"host {hm}, guest {cm}")

    elif scenario == "daxter-flut":
        fl = grab(client, r"\] fldax on (\S+) rides (\d+) art-on (\S+) daxter (\S+) state (\S+)")
        me = grab(client, r"\] me \S+ \S+ (\S+) speed \S+ state (\S+)")
        gun = grab(client, r"\] daxter-gun out (\S+) mod (\d+) shots (\d+)")
        c.check(scenario, "Daxter gets on the lizard the host left (triangle), drawn as Daxter on it",
                any("flut at" in l for l in client) and len(fl) > 1 and fl[1] == ("#t", "1", "#t", "#t", "target-flut-stance"), f"{fl[:2]}")
        c.check(scenario, "he fires from it, jumps and rides it forward",
                bool(gun) and gun[0][0] == "#t" and int(gun[0][2]) >= 1 and any(s == "target-flut-jump" for *_, s in fl)
                and len(me) >= 2 and abs(float(me[1][0]) - float(me[0][0])) > 5.0, f"gun {gun}, z {[z for z, _ in me]}")
        c.check(scenario, "off the lizard he is Daxter again, his art back",
                bool(fl) and fl[-1] == ("#f", "1", "#f", "#t", "target-indax-stance"), f"{fl[-1:]}")
        lizards = grab(host, r"\] copy 1 skel-flut id \d+ owner-hides (\S+) drawn (\S+)")
        c.check(scenario, "the host draws the guest's lizard under him", bool(lizards) and lizards[0] == ("#f", "#t"), f"{lizards}")
        shots = [int(v) for (v,) in grab(host, r"shot copies (\d+)")]
        c.check(scenario, "the host draws the blaster shots Daxter fires from the lizard", bool(shots) and shots[-1] >= 1, f"shot copies {shots[-1:]}")

    elif scenario == "daxter-perch-fire":
        co = grab(client, r"\] coop #t authority \S+ character \S+ perch (-?\d+) perched \S+ state (\S+)")
        gun = grab(client, r"\] daxter-gun out (\S+) mod (\d+) shots (\d+)")
        c.check(scenario, "Daxter on the host's shoulder fires the blaster from there",
                bool(co) and co[0] == ("0", "target-indax-grab") and bool(gun) and gun[0][0] == "#t" and int(gun[0][2]) >= 1,
                f"{co[:1]}, gun {gun}")
        c.check(scenario, "X puts him down, in his mode", bool(co) and co[-1] == ("-1", "target-indax-stance"), f"{co[-1:]}")
        rows = [l for l in host if "] remote 1 flags #x" in l]
        flags = [(int(l.split("flags #x")[1].split()[0], 16), "drawn #f" in l) for l in rows]
        c.check(scenario, "the host sees him climb on its shoulder: its own Daxter stands for him there (his puppet hidden)",
                any("player 1 climbs on our shoulder" in l for l in host) and any(f & 0x8000 and hidden for f, hidden in flags),
                f"flags {[(hex(f), hidden) for f, hidden in flags]}")
        shots = [int(v) for (v,) in grab(host, r"shot copies (\d+)")]
        c.check(scenario, "the host draws the blaster shots Daxter fires from its shoulder", bool(shots) and shots[-1] >= 1, f"shot copies {shots[-1:]}")

    elif scenario == "coop-protect":
        hits = counter(host, r"pvp hits taken (\d+) reacted (\d+) last pushed " + NUM)
        c.check(scenario, "co-op: the guest's punch does not hurt the host",
                bool(hits) and hits[-1][0] == 0 and not any("pvp blow" in l for l in client), f"{hits[-1:]}")
        flut = grab(client, r"\] copy 0 skel-flut id \d+ owner-hides (\S+) drawn (\S+)")
        c.check(scenario, "the lizard the host rides is drawn under it on the guest",
                any("state target-flut-stance" in l for l in host) and bool(flut) and flut[0] == ("#f", "#t"), f"{flut}")
        c.check(scenario, "co-op: no blow on the host's buggy reaches it",
                any("veh 15" in l for l in client) and not any("player 1 hit" in l for l in host), "")

    elif scenario == "merchant":
        hm = grab(host, r"\] merchant spot (-?\d+) at \S+ \S+ \S+ icon (\S+) name-drawn (\d+)")
        cm = grab(client, r"\] merchant spot (-?\d+) at \S+ \S+ \S+ icon (\S+) name-drawn (\d+)")
        me = grab(host, r"\] me \S+ (\S+) \S+ speed \S+ state (\S+)")
        ce = grab(client, r"\] econ orbs \d+ trophies \d+ items #x\w+ result \d+ near (\S+)")
        c.check(scenario, "the host draws Kleiver's spot when its accounts open, the guest gets the same",
                any("the merchant is at spot" in l for l in host) and bool(hm) and bool(cm) and hm[0][0] == cm[0][0] != "-1",
                f"host {hm[:1]}, guest {cm[:1]}")
        c.check(scenario, "each spot the host sets reaches the guest (0, 1, 2, 3)",
                [s for s, _, _ in hm[1:5]] == ["0", "1", "2", "3"] and [s for s, _, _ in cm[2:]] == ["1", "2", "3"]
                and cm[1][0] == "0", f"host {[s for s, _, _ in hm]}, guest {[s for s, _, _ in cm]}")
        c.check(scenario, "every spot is on the ground (the host stands there)",
                len(me) == 4 and all(st == "target-stance" for _, st in me), f"{me}")
        km = grab(host, r"\] kleiver model (\S+) borrow (\S+) lkleever (\S+) at (\S+) from the spot drawn (#t|#f)")
        c.check(scenario, "Kleiver's model stands at his spot, drawn (his level lkleever borrowed into waswide)",
                len(km) >= 4 and all(m[0] == "#t" and m[2] == "active" and float(m[3]) < 0.5 and m[4] == "#t" for m in km[-4:]),
                f"{km}")
        kl = grab(host, r"\] kleiver solid (\S+) clip (\d+) facing (\S+) we stand ([\d.]+)")
        c.check(scenario, "Kleiver: solid, the host walking into him from 4 m stops against him, he faces it, his idle clips change",
                len(kl) >= 6 and all(k[0] == "#t" for k in kl) and len({k[1] for k in kl}) >= 2
                and float(kl[-2][2]) > 0.8 and float(kl[-2][3]) > 3.5
                and float(kl[-1][2]) > 0.8 and 1.0 < float(kl[-1][3]) < 2.5, f"{kl}")
        labels = grab(host + client, r"\] merchant label \"?(.+?)\"? language (\d+)")
        c.check(scenario, "his name drawn over the spot reads \"Marchand\" in French (language 1), in yellow (font-color yellow)",
                bool(labels) and all(t == "Marchand" for t, lang in labels if lang == "1") and any(lang == "1" for _, lang in labels), f"{sorted(set(labels))}")
        c.check(scenario, "his icon is on the minimap, his name drawn, his shop's line at his spot (guest)",
                all(i == "#t" for _, i, _ in hm + cm) and int(hm[-1][2]) > int(hm[1][2]) > 0 and int(cm[1][2]) > 0
                and len(ce) > 1 and ce[1][0] == "#t", f"host {hm}, guest near {ce}")

    elif scenario == "economy":
        settings = OUT / "economy" / "host" / "OpenGOAL" / "jak3" / "settings"
        ge = grab(client, r"\] econ orbs (\d+) trophies (\d+) items #x(\w+) result (\d+) near (\S+)")
        c.check(scenario, "the server credits a precursor orb taken, after checking its entity (host and guest)",
                any("an orb for player 0" in l for l in host) and any("an orb for player 1" in l for l in host)
                and any(o == "1" for o, _, _, _, _ in ge), f"guest {ge[:4]}")
        c.check(scenario, "orbs claimed but not taken are refused (host and guest), one far from the claimant too",
                any("orb claim of player 0 refused (entity 12345)" in l for l in host)
                and any("orb claim of player 1 refused (entity 54321)" in l for l in host)
                and any("orb claim of player 1 refused" in l and "player not seen at the orb" in l for l in host), "")
        c.check(scenario, "a purchase without the orbs is refused, with them bought (orbs paid, item owned)",
                any(r == "2" and i == "0" for _, _, i, r, _ in ge) and any(r == "1" and i == "1" and o == "8" for o, _, i, r, _ in ge),
                f"{ge}")
        skin = grab(client, r"\] skin own (.*)")
        menus = grab(client, r"\] lobby menu (\S+)")
        c.check(scenario, "at Kleiver's spot the shop opens (triangle); X wears the item bought; triangle leaves it",
                any(n == "#t" for _, _, _, _, n in ge) and ("mp-shop",) in menus and menus[-1] != ("mp-shop",)
                and bool(skin) and skin[-1][0].split()[1] == "c0ffd040" and ge[-1][3] == "3", f"{skin[-1:]}, menus {menus}")
        saved = (settings / "multiplayer-economy.json").read_text(encoding="utf-8") if (settings / "multiplayer-economy.json").exists() else ""
        record = (settings / "multiplayer-sessions.log").read_text(encoding="utf-8") if (settings / "multiplayer-sessions.log").exists() else ""
        c.check(scenario, "the session's closing saves the accounts and the orbs credited, and is recorded; hosting it again loads them back",
                '"orbs": 8' in saved and '"items": 1' in saved and '"orbs_taken"' in saved and 'session "world" closed' in record
                and any('economy "world": 2 account(s) and 2 orb(s) taken loaded' in l for l in host), f"{record.strip()[-160:]}")

    elif scenario == "economy-persist":
        settings = OUT / "economy-persist" / "host" / "OpenGOAL" / "jak3" / "settings"
        hs = grab(host, r"\] econ host key (\S+) credits (\d+) refused (\d+) saves (\d+)")
        ge = grab(client, r"\] econ orbs (\d+) trophies (\d+)")
        saved = (settings / "multiplayer-economy.json").read_text(encoding="utf-8") if (settings / "multiplayer-economy.json").exists() else ""
        c.check(scenario, "the host credits its orb and the guest's, and 7 more to the guest (8)",
                bool(ge) and ge[-1][0] == "8" and any("an orb for player 0" in l for l in host), f"guest {ge}")
        c.check(scenario, "a minute after the change the accounts are saved while the session runs (no closing)",
                bool(hs) and int(hs[-1][3]) >= 1 and '"orbs": 8' in saved and '"orbs_taken"' in saved
                and not any("closed and recorded" in l for l in host), f"saves {hs[-1:]}")

    elif scenario == "economy-reload":
        ge = grab(client, r"\] econ orbs (\d+) trophies (\d+)")
        he = grab(host, r"\] econ orbs (\d+) trophies (\d+)")
        c.check(scenario, "after a restart, hosting the session loads the accounts its periodic save kept",
                any('economy "world": 2 account(s) and 2 orb(s) taken loaded' in l for l in host), "")
        c.check(scenario, "the guest, by its secret id, finds its 8 orbs; the host its 1",
                bool(ge) and ge[0][0] == "8" and bool(he) and he[0][0] == "1", f"guest {ge}, host {he}")
        c.check(scenario, "the orbs taken again in the fresh worlds are not credited again",
                any("refused" in l and "already credited" in l and "player 1" in l for l in host)
                and any("refused" in l and "already credited" in l and "player 0" in l for l in host)
                and ge[-1][0] == "8" and he[-1][0] == "1", f"guest {ge}, host {he}")

    elif scenario == "pressure":
        nets = grab(host, r"\] net player 1 pressure (\S+) tx (\S+) rx (\S+) queue (\S+) sent (\d+) B/s")
        c.check(scenario, "under the guest's losses the host sees the pressure on its link (congested or severe)",
                any(re.search(r"network to player 1: (congested|severe)", l) for l in host), f"{nets}")
        before = [int(n[4]) for n in nets[:2]]
        during = [int(n[4]) for n in nets[2:4]]
        c.check(scenario, "it sends the guest less meanwhile (between batches every 3 or 5 frames)",
                len(nets) >= 5 and all(n[0] == "normal" for n in nets[:2]) and all(n[0] in ("congested", "severe") for n in nets[2:4])
                and max(during) < 0.8 * min(before), f"before {before} B/s, during {during} B/s")
        c.check(scenario, "and goes back to normal once the losses stop",
                any("network to player 1: normal" in l for l in host) and nets[-1][0] == "normal", f"{nets[-1:]}")

    elif scenario == "skin-title":
        ed = grab(host, r"\] skin editing (\S+) group (\d+) slider (\d+) draft-shirt (\w+) preview (\S+)")
        pv = grab(host, r"\] skin preview \"(\w+)\" status #x(\w+)")
        c.check(scenario, "the Appearance page opened from the title screen draws its preview (a HUD model under the menu), Jak then Daxter",
                bool(ed) and ed[0][4] == "#t" and [k for k, _ in pv] == ["jakb", "daxter"] and all(int(st, 16) & 0x4008 == 0x4008 for _, st in pv), f"{pv}")

    elif scenario == "ui-ingame":
        he = grab(host, r"\] econ orbs (\d+) trophies (\d+)")
        ge = grab(client, r"\] econ orbs (\d+) trophies (\d+)")
        c.check(scenario, "the host gives itself 100 precursor orbs and 100 Metal Head trophies; a guest cannot",
                he[-1:] == [("100", "100")] and ge[-1:] == [("0", "0")], f"host {he}, guest {ge}")

    elif scenario == "menu-still":
        hm = [(float(x), float(z), float(v)) for x, _, z, v in grab(host, r"\] me (\S+) (\S+) (\S+) speed (\S+)")]
        gm = [(float(x), float(z), float(v)) for x, _, z, v in grab(client, r"\] me (\S+) (\S+) (\S+) speed (\S+)")]
        c.check(scenario, "under the START menu Daxter pushing the stick does not move; after it he does",
                len(gm) >= 3 and math.dist(gm[0][:2], gm[1][:2]) < 0.5 and math.dist(gm[1][:2], gm[2][:2]) > 2.0, f"{gm}")
        c.check(scenario, "under the START menu the throttle does not drive the buggy (it only rolls); after it, it does",
                len(hm) >= 3 and hm[1][2] <= hm[0][2] + 0.5 and hm[2][2] > 5.0, f"{hm}")

    elif scenario == "eyes":
        ey = grab(client, r"\] eyes player 0 slot (\d+) by (\S+) puppet (\S+) target (\S+)")
        c.check(scenario, "the guest playing Daxter: the host's Jak has its eyes from its own clips (its puppet feeds the slot, not our hidden model)",
                bool(ey) and all(p == "#t" and t == "#f" for _, _, p, t in ey), f"{ey}")

    elif scenario == "weapons-fx":
        ws = grab(client, r"\] weapons shown red2 (\d+) dark2 (\d+) arc (\d+) blue3 (\d+) yellow2 (\d+) red3 (\d+)")
        c.check(scenario, "the host's beam reflexor (yellow 2) shots and plasmite RPG (red 3) grenade draw their light trails on the guest",
                bool(ws) and int(ws[-1][4]) >= 1 and int(ws[-1][5]) >= 1, f"{ws[-1:]}")
        me = grab(host, r"\] me \S+ \S+ \S+ speed \S+ state (\S+)")
        c.check(scenario, "the host's dark bomb goes off next to the guest, which is hit and does not crash",
                any(st == "target-darkjak-bomb0" for (st,) in me) and any("pvp hit by player 0" in l for l in client), f"{me}")

    elif scenario == "dark-smack":
        c.check(scenario, "Dark Jak's blows next to the guest: their trail replayed there, no crash", any("pvp hit by player 0" in l for l in client), "")

    elif scenario == "session-flow":
        gp = grab(client, r"\] pause master (\S+) applied")
        hp = grab(host, r"\] pause master (\S+) applied")
        c.check(scenario, "a cutscene: the guest's START does not pause it; the host's pauses it for everyone, then resumes",
                bool(gp) and gp[0] == ("game",) and [m for (m,) in hp][:3] == ["game", "pause", "game"], f"guest {gp}, host {hp}")
        hr = grab(host, r"\] restart count (\d+)")
        gr = grab(client, r"\] restart count (\d+)")
        hm = grab(host, r"\] me (\S+) \S+ (\S+) speed")
        gm = grab(client, r"\] me (\S+) \S+ (\S+) speed")
        c.check(scenario, "START > Restart restarts every player: the host's, then a guest's (asked of the host); the guest back next to the host",
                any("the host restarted: we restart" in l for l in client) and any("player 1 asks for a restart" in l for l in host)
                and hr[-1:] == [("2",)] and gr[-1:] == [("2",)] and bool(hm) and bool(gm)
                and math.dist((float(hm[-1][0]), float(hm[-1][1])), (float(gm[-1][0]), float(gm[-1][1]))) < 5.0, f"host {hr}, guest {gr}")
        c.check(scenario, "a guest back at its title screen leaves the server (the host sees it go)",
                any("we leave the server" in l for l in client) and any("player 1 left" in l for l in host), "")

    elif scenario == "coop-respawn":
        gr = grab(client, r"\] respawn (\d+) state (\S+) health (\S+)")
        hr = grab(host, r"\] respawn (\d+) state (\S+) health (\S+)")
        c.check(scenario, "co-op: the guest who died comes back next to the host, health full, no continue point",
                any("back next to player 0" in l for l in client) and ("1", "target-stance", "8.0") in gr, f"{gr}")
        c.check(scenario, "and the host next to the guest",
                any("back next to player 1" in l for l in host) and hr[-1:] == [("1", "target-stance", "8.0")], f"{hr}")

        gs = grab(client, r"\] spectate slot \S+ left \S+ ms shown (\d+)")
        hs = grab(host, r"\] spectate slot \S+ left \S+ ms shown (\d+)")
        c.check(scenario, "before coming back each dead player watches its teammate 5 s (\"Spectating: <name>\" drawn about 300 frames)",
                any("dead, watching player 0 for 5 s" in l for l in client) and any("dead, watching player 1 for 5 s" in l for l in host)
                and bool(gs) and int(gs[-1][0]) >= 250 and bool(hs) and int(hs[-1][0]) >= 250, f"guest {gs[-1:]}, host {hs[-1:]}")
        def bodies(lines, slot):
            """The remote player's rows while dead: (x, y, z, drawn)."""
            out, pos = [], None
            for l in lines:
                m = re.search(r"\] remote %d \S+ pos (\S+) (\S+) (\S+) hp" % slot, l)
                if m:
                    pos = tuple(float(v) for v in m.groups())
                m = re.search(r"\] remote %d flags #x([0-9a-fA-F]+) .* drawn (\S+)" % slot, l)
                if m and pos and int(m.group(1), 16) & 0x4:
                    out.append(pos + (m.group(2),))
            return out
        hb, gb = bodies(host, 1), bodies(client, 0)
        # the death clip moves the body about 1 m (a fall), then it lies still until the player is back
        still = lambda b: (len(b) >= 4 and all(d == "#t" for *_, d in b)
                           and max(math.dist(b[-4][:3], r[:3]) for r in b[-4:]) < 0.1
                           and math.dist(b[0][:3], b[-1][:3]) < 2.0)
        c.check(scenario, "the dead player's body stays on the ground, drawn where it fell, on the other machine (logged each second)",
                still(hb) and still(gb), f"host sees the guest {len(hb)} s dead {hb[0][:3] if hb else None} -> {hb[-1:]}, "
                f"guest sees the host {len(gb)} s dead {gb[0][:3] if gb else None} -> {gb[-1:]}")

    elif scenario == "economy-trophy":
        c.check(scenario, "a Metal Head killed by the host's blow: a trophy for the host",
                any("a trophy for player 0" in l for l in host), "")
        gt = grab(client, r"\] econ orbs \d+ trophies (\d+)")
        c.check(scenario, "one killed by the guest's (its puppet's) blow: a trophy for the guest, sent to it",
                any("a trophy for player 1" in l for l in host) and gt[-1:] == [("1",)], f"{gt}")

    elif scenario == "scene-skins":
        hs = grab(host, r"\] scene actor (\S+) shirt #x(\w+) fur #x(\w+)")
        gs = grab(client, r"\] scene actor (\S+) shirt #x(\w+) fur #x(\w+)")
        hj = [r for r in hs if r[0].startswith(("jak", "darkjak"))]
        hd = [r for r in hs if r[0].startswith("sidekick")]
        gj = [r for r in gs if r[0].startswith(("jak", "darkjak"))]
        gd = [r for r in gs if r[0].startswith("sidekick")]
        c.check(scenario, "a cutscene's Jak wears the host's shirt on both machines",
                bool(hj) and bool(gj) and all(r[1] == "99ff3010" for r in hj + gj), f"host {hj[:2]}, guest {gj[:2]}")
        c.check(scenario, "its Daxter wears the guest's fur while the guest plays Daxter, the stock one once it plays Jak",
                bool(hd) and bool(gd) and hd[0][2] == "cc2040ff" and gd[0][2] == "cc2040ff" and hd[-1][2] == "0" and gd[-1][2] == "0",
                f"host {hd}, guest {gd}")

    elif scenario == "host-quit":
        ends = grab(client, r"\] end count (\d+) pending (\S+) note .* title (\S+) menu (\S+)")
        c.check(scenario, "the host back at its title screen ends the session: the guest in game goes back to its title screen",
                any("the host is back at the title screen: the session ends" in l for l in host)
                and bool(ends) and ends[0] == ("1", "#t", "#t", "title"), f"{ends[:1]}")
        c.check(scenario, "the host leaving its lobby: the guest in it goes back to the title screen's first page",
                len(ends) > 1 and ends[1] == ("2", "#t", "#t", "title"), f"{ends[1:2]}")

    elif scenario == "retail-general":
        viewer = s.get("viewer", (None, []))[1]
        c.check(scenario, "a retail boot: no debug segment, three players in the host's free roam",
                grab(host, r"session debug-segment (\S+)")[-1:] == [("#f",)]
                and grab(host, r"session status .* players (\d+)")[-1:] == [("3",)], "")
        cd = [[int(ms) for (ms,) in grab(lines, r"\] lobby countdown (\d+) ms") if int(ms) > 0] for lines in (host, client, viewer)]
        c.check(scenario, "the lobby's countdown on the host and on both guests", all(cd), f"{cd}")
        gone = [grab(lines, r"\] lobby countdown 0 ms, cancelled (-?\d+) s ago") for lines in (client, viewer)]
        c.check(scenario, "the host's X cancels it (both guests see it cancelled), X starts it again",
                bool(grab(host, r"lobby countdown cancelled")) and all(any(0 <= int(t) <= 5 for (t,) in g) for g in gone)
                and len(grab(host, r"lobby countdown started")) >= 2, f"{gone}")
        votes = [grab(lines, r"\] skip scene (\S+) voted (\S+) votes (\d+) viewers (\d+) skips (\d+)") for lines in (host, client, viewer)]
        c.check(scenario, "a cutscene the three watch is skipped once the three asked (1/3, 2/3, then skipped on all)",
                any(v == "3" for _, _, _, v, _ in votes[0]) and any(n != "none" and vo == "#t" and int(k) < 3 for n, vo, k, _, _ in votes[0])
                and all(x and x[-1][0] == "none" and x[-1][4] == "1" for x in votes), f"{votes}")
        ok = True
        for lines in (client, viewer):
            p = pause_logs(lines)
            first = next((i for i, d in enumerate(p) if 0 < d["resume"] <= 3000), None)
            ok = ok and any(d["applied"] == "#t" and d["master"] == "pause" for d in p) \
                and first is not None and any(d["master"] == "game" for d in p[first:])
        c.check(scenario, "the host's SELECT pauses both guests, SELECT again resumes after the 3 s countdown", ok, "")
        hm = [d for d in pause_logs(host) if d["master"] == "progress"]
        gm = [d for d in pause_logs(client) if d["master"] == "progress"]
        c.check(scenario, "START opens the menu over the running world, on the host and on a guest",
                bool(hm) and bool(gm) and all(d["live"] == "#t" and d["prevent"] == "#f" for d in hm + gm),
                f"{[(d['live'], d['prevent']) for d in hm + gm]}")
        dark = [grab(lines, r"power puppet \d+ skin (\d+) own-morph \S+ skin-draws (\d+)") for lines in (host, viewer)]
        c.check(scenario, "the first guest's Dark Jak skin is drawn on the host and on the other guest",
                all(any(k == "255" and int(n) > 0 for k, n in d) for d in dark), f"{dark}")
        seen = [grab(lines, r"\] skin player 0 (.*)") for lines in (client, viewer)]
        c.check(scenario, "the host's outfit reaches both guests",
                all(x and x[-1][0].split()[1] == "99ff3010" and x[-1][0].split()[5] == "cc20ff40" for x in seen), f"{seen}")

    elif scenario == "beam-real":
        hb = grab(host, r"\] beams (\d+) here (\d+)/(\d+) near (\S+) held (\d+)")
        hs = grab(host, r"\] state task-manager-arena-training (\S+)")
        c.check(scenario, "the arena training's last step: the host in its beam is held (1/2) while the guest is away",
                any(h == "1" and p == "2" and int(held) > 0 for _, h, p, _, held in hb) and "done" in [s for (s,) in hs],
                f"{hb}, states {hs}")
        c.check(scenario, "the guest comes: every player there (2/2), the mission completes on the host",
                any("every player at the waypoint (2/2)" in l for l in host) and bool(hs) and hs[-1][0] == "resolution",
                f"states {hs}")
        gs = grab(client, r"\] state task-manager-arena-training (\S+)")
        c.check(scenario, "and on the guest", bool(gs) and gs[-1][0] == "resolution", f"{gs}")
        shown = grab(host, r"\] beam count shown (\d+) line (.*)")
        c.check(scenario, "the count at the bottom of the host's screen: \"Players here: 1/2\" while it waits, then 2/2",
                any(int(n) > 0 and line.endswith("1/2") for n, line in shown) and bool(shown) and shown[-1][1].endswith("2/2"),
                f"{shown}")

    elif scenario in ("race-real", "race-real-daxter"):
        held = grab(host, r"\] the race starts \(held " + NUM + " s\\)")
        c.check(scenario, "Kleiver's race on the host waits while the guest loads its world (1/2)",
                any("the race start waits for every player aboard (1/2)" in l for l in host), "")
        c.check(scenario, "the race starts once every player is aboard, after the 10 s countdown",
                bool(held) and float(held[0][0]) >= 10.0
                and any("every player aboard (2): the race starts in 10 s" in l for l in host), f"held {held}")
        gm = grab(client, r"\] race me driving (\S+) riding (\S+) perched (\S+) state (\S+)")
        gr = grab(client, r"\] race waiting (\S+) aboard (\d+)/(\d+) starts (\d+)")
        hr = grab(host, r"\] race waiting (\S+) aboard (\d+)/(\d+) starts (\d+)")
        hp = grab(host, r"\] race place (\d+) progress \S+ done \d+ node (\d+) in-race (\S+)")
        gp = grab(client, r"\] race place (\d+) progress \S+ done \d+ node (\d+) in-race (\S+)")
        c.check(scenario, "both races report their place and progress (the players counted in each other's place)",
                any(i == "#t" and n != "0" for _, n, i in hp) and any(i == "#t" and n != "0" for _, n, i in gp),
                f"host {hp[-1:]}, guest {gp[-1:]}")
        if scenario == "race-real":
            c.check(scenario, "each machine's race gives its own turtle, the guest's 6 m back; both drive",
                    any("race start: our vehicle 6.0 behind" in l for l in client)
                    and bool(gm) and gm[-1][0] == "#t" and hr[-1:] and hr[-1][3] == "1",
                    f"guest {gm[-1:]}, host {hr[-1:]}")
        else:
            c.check(scenario, "Daxter leaves his turtle during the countdown: the start waits again (1/2)",
                    any(w == "#t" and a == "1" for w, a, _, _ in gr), f"{gr}")
            c.check(scenario, "no shoulder in a race: Daxter's triangle next to the host leaves him on foot",
                    len(gm) > 2 and gm[2][0] == "#f" and not any(p == "#t" for _, _, p, _ in gm), f"guest {gm}")
            c.check(scenario, "back in his own turtle: the countdown starts again, then the race",
                    sum("every player aboard (2): the race starts in 10 s" in l for l in host) >= 2
                    and bool(gm) and gm[-1][0] == "#t" and hr[-1:] and hr[-1][3] == "1",
                    f"guest {gm[-1:]}, host {hr[-1:]}")

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
        c.check(scenario, "online, Jak has no Daxter of his own on his shoulder (a Daxter player stands there), before and after the guest leaves",
                bool(hidden) and all(h == ("#t", "#f") for h in hidden), f"{hidden}")
        c.check(scenario, "continuing restores the campaign saved (story steps closed after it open again)",
                len(sess) >= 3 and sess[-1][1] < max(cl for _, cl in sess) and sess[-1][1] == sess[1][1],
                f"closed {[cl for _, cl in sess]}")

    elif scenario == "powers":
        shown = grab(host, r"power puppet 1 blend (\S+) dark (\S+) effect #x(\w+) fade (\S+)")
        c.check(scenario, "the host shows the guest's Dark Jak (blend shapes)",
                bool(shown) and float(shown[0][0]) > 0.9 and float(shown[0][1]) > 0.9, f"{shown[:1]}")
        c.check(scenario, "then its Light Jak (no dark shape, its glow)",
                len(shown) >= 2 and float(shown[1][1]) < 0.1 and int(shown[1][2], 16) != 0, f"{shown[1:2]}")
        skin = grab(host, r"power puppet 1 skin (\d+) own-morph (\S+) skin-draws (\d+) color-mult (\S+)")
        c.check(scenario, "the host draws the guest's Dark Jak skin while its own stays normal, no tint",
                bool(skin) and skin[0][0] == "255" and float(skin[0][1]) == 0.0 and int(skin[0][2]) > 0
                and float(skin[0][3]) == 1.0, f"{skin[:1]}")
        gskin = grab(client, r"power puppet 0 skin (\d+) own-morph (\S+)")
        c.check(scenario, "the guest's own Dark Jak skin does not darken the host's model there",
                bool(gskin) and gskin[0][0] == "0" and float(gskin[0][1]) > 0.9, f"{gskin[:1]}")

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
