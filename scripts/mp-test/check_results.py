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
            fx = counter(client, r"fx effects sent (\d+) shown (\d+) shots sent (\d+) shown (\d+)")
            em = counter(client, r"emitters sent \d+ received (\d+) shown (\d+)")
            c.check(scenario, "the host's shots replayed on the guest", bool(fx) and fx[-1][3] >= 1, f"shots shown {fx[-1][3] if fx else None}")
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

    elif scenario == "ride":
        states = grab(client, r"\] local pos \S+ \S+ \S+ hp \S+ state (\S+)")
        c.check(scenario, "the guest rides as passenger", any(st == "target-grab-ride" for (st,) in states), f"{[s for (s,) in states]}")
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
        need = 30 if scenario == "progress-late" else 1
        c.check(scenario, "the guest closed nodes after the host", bool(g) and max(v for _, v in g) >= need,
                f"{int(max(v for _, v in g)) if g else None} nodes closed after the other (at least {need})")

    elif scenario == "world-objects":
        hs = unwrap([(float(t), float(p)) for t, p in grab(host, r"sync t (\d+(?:\.\d+)?) phase " + NUM)])
        gs = [(float(t), float(p)) for t, p in grab(client, r"sync t (\d+(?:\.\d+)?) phase " + NUM)]
        diffs = []
        for t, p in gs:
            ref = interp(hs, t)
            if ref is not None:
                d = abs(p - ref) % 1.0
                diffs.append(min(d, 1.0 - d) * 4000.0)  # 4 s cycle -> ms
        c.check(scenario, "cycles in phase with the host", bool(diffs) and max(diffs) <= 17,
                f"largest {max(diffs):.0f} ms over {len(diffs)} samples" if diffs else "no sample at the same moment")
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
            far = grab(lines, r"level (\w+) loaded display #f far #t")
            c.check(scenario, f"{side} loads the other's level, not displayed", bool(far), f"{sorted(set(x for (x,) in far))}")
        last = grab(host, r"far level (\S+) loads")
        c.check(scenario, "the host lets it go when the guest is back", bool(last) and last[-1][0] == "#f", f"{last[-1] if last else None}")
        broken = grab(host, r"shader links \d+ broken (\d+)")
        c.check(scenario, "texture shader lists intact", bool(broken) and all(b == "0" for (b,) in broken), f"broken {broken}")

    elif scenario in ("far-city", "far-city-synth"):
        far = grab(host, r"far level ctywide loads \d+ drops \d+ restored (\d+)")
        c.check(scenario, "the host loads Haven's master level", bool(far), f"restored by the sandbox {far[-1][0] if far else None}")
        borrows = grab(host, r"far borrows (\S+) (\S+) (\S+)")
        c.check(scenario, "and the guest's borrow levels of it", any(b[0] != "#f" for b in borrows), f"{[b for b in borrows if b[0] != '#f'][:1]}")
        # what the guest sends depends on who walks by in Haven: every object whose art is loaded
        # here (the far levels included) must be drawn; the others are counted
        # per object, its last listing (an object listed once may have just arrived, its copy is made
        # within a few frames); every object the guest draws must be drawn here, its art loaded with
        # the far levels or alone
        last: dict[str, tuple] = {}
        seen: dict[str, int] = {}
        for sk, oid, hides, drawn, art in grab(host, r"\] copy 1 (skel-\S+) id (\d+) owner-hides (\S+) drawn (\S+) art (\S+)"):
            last[oid] = (sk, drawn, art, hides)
            seen[oid] = seen.get(oid, 0) + 1
        steady = {k: v for k, v in last.items() if seen[k] >= 2}
        shown = {k: v for k, v in steady.items() if v[3] != "#t"}
        missing = sorted(set(v[0] for v in shown.values() if v[1] != "#t"))
        c.check(scenario, "every guest object it draws is drawn here", bool(shown) and not missing,
                f"{sum(v[1] == '#t' for v in shown.values())} of {len(shown)} drawn "
                f"({sorted(set(v[0] for v in shown.values()))}); hidden by the guest "
                f"{len(steady) - len(shown)}; not drawn {missing}")
        kinds = grab(host, r"kinds announced (\d+) drawable (\d+) groups rebuilt (\d+)")
        c.check(scenario, "every kind of object the guest announced can be drawn here",
                bool(kinds) and int(kinds[-1][0]) > 0 and kinds[-1][0] == kinds[-1][1],
                f"{kinds[-1][1] if kinds else None} of {kinds[-1][0] if kinds else None} kinds drawable, "
                f"{kinds[-1][2] if kinds else None} skeleton-groups rebuilt from the guest's descriptions")
        if scenario == "far-city-synth":
            c.check(scenario, "copies built from rebuilt skeleton-groups (code in no level here)",
                    bool(kinds) and int(kinds[-1][2]) > 0, f"{kinds[-1][2] if kinds else None} groups rebuilt, all copies built from them")
        art = grab(host, r"far art loaded (\d+) failed (\d+)")
        c.check(scenario, "art groups loaded alone without failure", bool(art) and art[-1][1] == "0",
                f"loaded {art[-1][0] if art else None}, failed {art[-1][1] if art else None}")
        haven = grab(host, r"replica pos \S+ \S+ \S+ draw \S+ level (cty\w+)")
        c.check(scenario, "Haven objects drawn from the far levels", len(haven) >= 1, f"{len(haven)} drawn from {sorted(set(x for (x,) in haven))}")
        last = grab(host, r"far level (\S+) loads")
        c.check(scenario, "all let go when the guest is back", bool(last) and last[-1][0] == "#f", f"{last[-1] if last else None}")
        broken = grab(host, r"shader links \d+ broken (\d+)")
        c.check(scenario, "texture shader lists intact", bool(broken) and all(b == "0" for (b,) in broken), f"broken {broken}")


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
