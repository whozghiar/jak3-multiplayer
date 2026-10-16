#!/usr/bin/env python3
"""
Start two Jak 3 instances on this PC: one played with the controller, the other with the keyboard.

For hands-on multiplayer tests.

    python scripts/mp-test/local_pair.py [--retail] [--reset]

Each instance has its own config folder, out/mp-local/<controller|keyboard>/, made on the first
run from the user's Jak 3 saves and settings (without the multiplayer name and color) and kept
afterwards: set each player's name and color once in game. --reset makes them again.

The keyboard instance sees no controller (SDL joystick backends off for it alone), so its
keyboard is on. The controller instance keeps taking the controller while its window is not
focused (ignore-controller-win-unfocused? #f). Keep the keyboard window focused: keys only reach
the focused window. Both windows are small and side by side; each instance's output goes to
gk.log in its folder.

Default boot as `task boot-game` (debug, in game at save slot 1); --retail as
`task boot-game-retail` (title screen).
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mp_test import GK, NO_CONTROLLER, ROOT, USER_JAK3, WINDOW_H, WINDOW_W  # noqa: E402

OUT = ROOT / "out" / "mp-local"


def prepare(folder: Path, index: int, controller: bool, reset: bool) -> None:
    """The instance's config folder: made from the user's saves and settings if missing, its window
    placed side by side, and the controller instance taking the controller in the background."""
    jak3 = folder / "OpenGOAL" / "jak3"
    settings = jak3 / "settings"
    if reset and folder.exists():
        shutil.rmtree(folder)
    if not jak3.exists():
        for sub in ("saves", "settings"):
            if (USER_JAK3 / sub).is_dir():
                shutil.copytree(USER_JAK3 / sub, jak3 / sub)
        # each player its own name and color
        (settings / "multiplayer.cfg").unlink(missing_ok=True)
    settings.mkdir(parents=True, exist_ok=True)
    pc = settings / "pc-settings.gc"
    if pc.exists():
        text = pc.read_text(encoding="utf-8")
        text = re.sub(r"\(window-size [^)]*\)", f"(window-size {WINDOW_W} {WINDOW_H})", text)
        text = re.sub(r"\(game-size [^)]*\)", f"(game-size {WINDOW_W} {WINDOW_H})", text)
        if controller:
            text = re.sub(r"\(ignore-controller-win-unfocused\? [^)]*\)",
                          "(ignore-controller-win-unfocused? #f)", text)
        pc.write_text(text, encoding="utf-8")
    display = settings / "display-settings.json"
    data = json.loads(display.read_text(encoding="utf-8")) if display.exists() else {"version": "1.2"}
    data.update({"display_mode": 0, "window_xpos": index * (WINDOW_W + 10), "window_ypos": 40})
    display.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--retail", action="store_true", help="boot to the title screen, without debug mode")
    parser.add_argument("--reset", action="store_true", help="make both config folders again from the user's")
    args = parser.parse_args()
    if not GK.exists():
        sys.exit(f"{GK} not found: task build-release-game")

    boot = ["-boot", "-fakeiso"] + ([] if args.retail else ["-debug"])
    for index, name in enumerate(("controller", "keyboard")):
        folder = OUT / name
        controller = name == "controller"
        prepare(folder, index, controller, args.reset)
        env = dict(os.environ) if controller else dict(os.environ, **NO_CONTROLLER)
        cmd = [str(GK), "-v", "--game", "jak3", "--config-path", str(folder), "--", *boot]
        print(f"[local] {name}: {' '.join(cmd)}  (log: {folder / 'gk.log'})")
        log = open(folder / "gk.log", "w", encoding="utf-8", errors="replace")
        subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        time.sleep(2.0)  # let the first window open before the next one
    return 0


if __name__ == "__main__":
    sys.exit(main())
