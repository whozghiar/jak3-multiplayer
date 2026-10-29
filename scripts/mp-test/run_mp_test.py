#!/usr/bin/env python3
"""
Run an automated multiplayer test of the Jak 3 multiplayer mod with several game instances.

    python scripts/mp-test/run_mp_test.py <scenario> [--extra-seconds N]

A scenario is a folder scripts/mp-test/scenarios/<scenario>/ holding one script per instance
(host.txt, client.txt, ...). Each line is "<seconds> <command...>", the commands of
goal_src/jak3/pc/features/multiplayer/multiplayer-test.gc.

Every instance runs `gk -v --game jak3 --config-path <dir> -- -boot -fakeiso -debug` with
MP_TEST_SCRIPT set, in its own config folder out/mp-test/<scenario>/<instance>/ (a copy of the
user's Jak 3 saves and settings, small window, side by side, controller ignored when unfocused).
The debug boot loads save slot 1, so the game starts in game, where the save left it. --retail
boots as `task boot-game-retail` (no -debug): the title screen, without the debug segment.

--settings-from <scenario> starts each instance with the economy files and multiplayer.cfg (its
secret id) its namesake left in that scenario's folder: a session hosted again after a restart.

When the last command of every script is past (plus --extra-seconds), every instance is stopped.
Output in out/mp-test/<scenario>/:
  <instance>/gk.log        the instance's whole output
  screenshots/<instance>-<name>.png
  summary.txt              every "[mp-test]" and "[multiplayer]" line, per instance
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).resolve().parent / "scenarios"
GK = ROOT / "out" / "build" / "Release" / "bin" / ("gk.exe" if os.name == "nt" else "gk")
USER_JAK3 = Path(os.environ.get("APPDATA", Path.home() / ".config")) / "OpenGOAL" / "jak3"
WINDOW_W, WINDOW_H = 960, 540
# environment that hides every controller from an instance: each SDL joystick backend off (XInput's
# switch is SDL_XINPUT_ENABLED, not SDL_JOYSTICK_XINPUT)
NO_CONTROLLER = dict({f"SDL_JOYSTICK_{b}": "0" for b in ("HIDAPI", "RAWINPUT", "WGI", "DIRECTINPUT", "GAMEINPUT")},
                     SDL_XINPUT_ENABLED="0")


def script_length(script: Path) -> float:
    """Time of the last command of a script, in seconds."""
    last = 0.0
    for line in script.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            last = max(last, float(line.split()[0]))
    return last


def prepare_instance(folder: Path, index: int) -> None:
    """A config folder with the user's saves and settings, the window placed for this instance."""
    jak3 = folder / "OpenGOAL" / "jak3"
    if folder.exists():
        shutil.rmtree(folder)
    for sub in ("saves", "settings"):
        if (USER_JAK3 / sub).is_dir():
            shutil.copytree(USER_JAK3 / sub, jak3 / sub)
    settings = jak3 / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    for stale in ("multiplayer.cfg", "multiplayer-server.db", "multiplayer-server.db-wal", "multiplayer-server.db-shm",
                  "multiplayer-economy.json", "multiplayer-sessions.log"):
        (settings / stale).unlink(missing_ok=True)
    # each test starts without a multiplayer campaign of the user's
    (jak3 / "saves" / "multiplayer-campaign.bin").unlink(missing_ok=True)
    pc = settings / "pc-settings.gc"
    if pc.exists():
        text = pc.read_text(encoding="utf-8")
        text = re.sub(r"\(window-size [^)]*\)", f"(window-size {WINDOW_W} {WINDOW_H})", text)
        text = re.sub(r"\(game-size [^)]*\)", f"(game-size {WINDOW_W} {WINDOW_H})", text)
        text = re.sub(r"\(ignore-controller-win-unfocused\? [^)]*\)",
                      "(ignore-controller-win-unfocused? #t)", text)
        # no vsync: with the display asleep or the session locked, a frame swap waiting for the
        # screen blocks the game (its frame limiter keeps 60 frames a second)
        text = re.sub(r"\(vsync [^)]*\)", "(vsync #f)", text)
        pc.write_text(text, encoding="utf-8")
    display = settings / "display-settings.json"
    data = json.loads(display.read_text(encoding="utf-8")) if display.exists() else {"version": "1.2"}
    data.update({"display_mode": 0, "window_xpos": index * (WINDOW_W + 10), "window_ypos": 40})
    display.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("scenario")
    parser.add_argument("--extra-seconds", type=float, default=5.0)
    parser.add_argument("--latency", default="",
                        help="one-way delay of what each instance receives, '<ms>[:<jitter ms>]' (MP_TEST_LATENCY)")
    parser.add_argument("--loss", default="",
                        help="percentage of the datagrams each instance receives that are dropped (MP_TEST_LOSS)")
    parser.add_argument("--shot-size", default="",
                        help="screenshot size, '<width>x<height>' (MP_TEST_SHOT_SIZE, default 1280x720)")
    parser.add_argument("--retail", action="store_true",
                        help="boot as task boot-game-retail: the title screen, no debug segment")
    parser.add_argument("--fixed-cadence", action="store_true",
                        help="no network pressure: every send at its full rate (MP_TEST_FIXED_CADENCE, a baseline)")
    parser.add_argument("--settings-from", default="",
                        help="scenario whose instances' economy files and multiplayer.cfg to start with")
    args = parser.parse_args()

    scenario = SCENARIOS / args.scenario
    scripts = sorted(scenario.glob("*.txt"))
    if not scripts:
        sys.exit(f"no instance script in {scenario}")
    if not GK.exists():
        sys.exit(f"{GK} not found: task build-release-game")

    out = ROOT / "out" / "mp-test" / args.scenario
    shots = out / "screenshots"
    if shots.exists():
        shutil.rmtree(shots)
    shots.mkdir(parents=True)

    procs = []
    for index, script in enumerate(scripts):
        name = script.stem
        folder = out / name
        prepare_instance(folder, index)
        if args.settings_from:
            # the economy and the secret id of the same instance in an earlier run
            src = ROOT / "out" / "mp-test" / args.settings_from / name / "OpenGOAL" / "jak3" / "settings"
            dst = folder / "OpenGOAL" / "jak3" / "settings"
            for kept in ("multiplayer.cfg", "multiplayer-server.db", "multiplayer-server.db-wal",
                         "multiplayer-server.db-shm", "multiplayer-sessions.log"):
                if (src / kept).exists():
                    shutil.copy2(src / kept, dst / kept)
        env = dict(os.environ, MP_TEST_SCRIPT=str(script))
        if args.latency:
            env["MP_TEST_LATENCY"] = args.latency
        if args.loss:
            env["MP_TEST_LOSS"] = args.loss
        if args.fixed_cadence:
            env["MP_TEST_FIXED_CADENCE"] = "1"
        if args.shot_size:
            env["MP_TEST_SHOT_SIZE"] = args.shot_size
        # no real controller: the scripts drive pad 0 themselves (libpad), and a controller gone to
        # sleep once hung every instance in SDL's controller detection
        env.update(NO_CONTROLLER)
        log = open(folder / "gk.log", "w", encoding="utf-8", errors="replace")
        cmd = [str(GK), "-v", "--game", "jak3", "--config-path", str(folder),
               "--", "-boot", "-fakeiso"] + ([] if args.retail else ["-debug"])
        print(f"[run] {name}: {' '.join(cmd)}")
        procs.append((name, folder, log,
                      subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)))
        time.sleep(2.0)  # let the first window open before the next one

    duration = max(script_length(s) for s in scripts) + args.extra_seconds
    # a script that waits (wait-joined, wait-player) stops its clock meanwhile: the games run until
    # every script printed that it finished (each game logs it), within a cap
    waits = any("wait-" in s.read_text(encoding="utf-8") for s in scripts)
    # a retail boot plays the intro before the title screen's menu opens (the scripts skip it)
    cap = duration + (240.0 if waits else 0.0) + (120.0 if args.retail else 0.0)
    print(f"[run] waiting {duration:.0f} s" + (f" plus the scripts' waits (at most {cap:.0f} s)" if waits else ""))
    start = time.time()
    finished_at = None
    while time.time() < start + cap:
        if any(p.poll() is not None for _, _, _, p in procs):
            for name, _, _, p in procs:
                if p.poll() is not None:
                    print(f"[run] {name} exited early with code {p.returncode}")
            break
        if not waits:
            if time.time() >= start + duration:
                break
        elif all("[mp-test] script finished" in (folder / "gk.log").read_text(encoding="utf-8", errors="replace")
                 for _, folder, _, _ in procs):
            finished_at = finished_at or time.time()
            if time.time() - finished_at >= args.extra_seconds:
                break
        time.sleep(1.0)

    for name, _, log, p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log.close()

    summary = []
    for name, folder, _, p in procs:
        summary.append(f"=== {name} (exit code {p.returncode})")
        text = (folder / "gk.log").read_text(encoding="utf-8", errors="replace")
        summary += [line for line in text.splitlines()
                    if "[mp-test]" in line or "[multiplayer]" in line or "could not be allocated" in line]
        for png in sorted((folder / "OpenGOAL" / "jak3" / "screenshots").glob("*.png")):
            shutil.copy2(png, shots / f"{name}-{png.name}")
    (out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"[run] screenshots in {shots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
