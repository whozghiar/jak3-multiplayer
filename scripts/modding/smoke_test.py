"""In-game smoke tests: install a scenario file into the GAME build, run the game until the
scenario reports, collect its log, screenshots and crash report.

  python scripts/modding/smoke_test.py install jak3 docs/modding/current_mod/my_mod_smoke.gc --scenario 2
  python scripts/modding/smoke_test.py run jak3 transport-height --timeout 300 [--retail]
  python scripts/modding/smoke_test.py remove jak3

install copies the scenario to goal_src/<game>/pc/features/smoke-test.gc (with its
SMOKE_SCENARIO constant set) and lists it last in GAME (dgos/game.gd, plus game.gp in Jak 1):
compile with `task compile-check` afterwards. remove undoes both; never commit them.
run starts out/build/Release/bin/gk with -boot -fakeiso (-debug unless --retail: a debug boot
loads save slot 1), prints the lines that start with SMOKE, stops at "SMOKE DONE" or the timeout,
and writes everything to out/smoke/<name>/. Exit code 0: DONE seen and no "SMOKE FAIL" line.
Run it only when the user authorized launching the game (AGENTS.md).
The template scenario: docs/modding/templates/smoke_test.template.gc.
"""
import argparse
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEST_FILE = "pc/features/smoke-test.gc"
GD_LINE = '  "smoke-test.o" ;; TEMPORARY smoke test (scripts/modding/smoke_test.py), never commit'
GP_LINE = '(goal-src "pc/features/smoke-test.gc" "{dep}") ;; TEMPORARY smoke test, never commit'


def edit_lines(path: Path, fn):
  text = path.read_bytes().decode("utf8")
  nl = "\r\n" if "\r\n" in text else "\n"
  path.write_bytes(nl.join(fn(text.replace("\r\n", "\n").split("\n"))).encode("utf8"))


def remove(game: str):
  src = REPO / "goal_src" / game
  (src / TEST_FILE).unlink(missing_ok=True)
  edit_lines(src / "dgos" / "game.gd", lambda lines: [l for l in lines if "smoke-test.o" not in l])
  edit_lines(src / "game.gp", lambda lines: [l for l in lines if "pc/features/smoke-test.gc" not in l])


def install(game: str, scenario_file: Path, number: int):
  remove(game)
  src = REPO / "goal_src" / game
  text = scenario_file.read_text(encoding="utf8")
  text = re.sub(r"\(defconstant SMOKE_SCENARIO \d+\)", f"(defconstant SMOKE_SCENARIO {number})", text)
  (src / TEST_FILE).write_text(text, encoding="utf8")
  last = {}

  def add_to_gd(lines):
    # GAME's list ends with " ))": the scenario goes last, after every mod file
    end = max(i for i, l in enumerate(lines) if l.strip() == "))")
    last["dep"] = re.search(r'"([^"]+)\.o"', next(l for l in reversed(lines[:end]) if ".o\"" in l)).group(1)
    return lines[:end] + [GD_LINE] + lines[end:]

  edit_lines(src / "dgos" / "game.gd", add_to_gd)
  if game == "jak1":  # Jak 1 lists each GAME source in game.gp too
    edit_lines(src / "game.gp", lambda lines: lines + [GP_LINE.format(dep=last["dep"])])
  print(f"installed scenario {number} as goal_src/{game}/{TEST_FILE}; now compile (task compile-check)")


def screenshots_dir(game: str) -> Path:
  if sys.platform == "win32":
    base = Path(os.environ["APPDATA"])
  else:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
  return base / "OpenGOAL" / game / "screenshots"


def run(game: str, name: str, timeout: float, retail: bool) -> int:
  exe = REPO / "out" / "build" / "Release" / "bin" / ("gk.exe" if sys.platform == "win32" else "gk")
  out = REPO / "out" / "smoke" / name
  out.mkdir(parents=True, exist_ok=True)
  shots = screenshots_dir(game)
  for old in shots.glob("smoke-*.png") if shots.exists() else []:
    old.unlink()
  args = [str(exe), "-v", "--game", game, "--", "-boot", "-fakeiso"] + ([] if retail else ["-debug"])
  # run from out/smoke/<name>: the runtime writes its log/ folder there
  proc = subprocess.Popen(args, cwd=out, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
  lines = queue.Queue()
  threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout] + [lines.put(None)], daemon=True).start()
  start, done, failed = time.time(), False, False
  try:
    with open(out / "stdout.log", "w", encoding="utf8", errors="replace") as log:
      while time.time() - start < timeout:
        try:
          raw = lines.get(timeout=1.0)
        except queue.Empty:
          continue  # a hung runtime prints nothing: the timeout still ends it
        if raw is None:
          break
        text = raw.decode("utf8", errors="replace")
        log.write(text)
        if text.startswith("SMOKE"):
          print(text.rstrip(), flush=True)
          failed |= text.startswith("SMOKE FAIL")
          if text.startswith("SMOKE DONE"):
            done = True
            time.sleep(3)  # a screenshot is written about 0.5 s after it is asked for
            break
      else:
        print("TIMEOUT")
  finally:
    if proc.poll() is None:
      proc.kill()
  print(f"exit {proc.wait()}, done {done}, {time.time() - start:.0f} s, log {out / 'stdout.log'}")
  crash = out / "log" / f"{game}-crash.txt"
  if crash.exists() and crash.stat().st_mtime > start:
    print(f"CRASH REPORT {crash}")
  for shot in shots.glob("smoke-*.png") if shots.exists() else []:
    shutil.copy(shot, out / shot.name)
    print("shot", out / shot.name)
  return 0 if done and not failed else 1


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  sub = parser.add_subparsers(dest="cmd", required=True)
  p = sub.add_parser("install")
  p.add_argument("game", choices=["jak1", "jak2", "jak3"])
  p.add_argument("file", type=Path)
  p.add_argument("--scenario", type=int, default=1)
  p = sub.add_parser("remove")
  p.add_argument("game", choices=["jak1", "jak2", "jak3"])
  p = sub.add_parser("run")
  p.add_argument("game", choices=["jak1", "jak2", "jak3"])
  p.add_argument("name")
  p.add_argument("--timeout", type=float, default=420)
  p.add_argument("--retail", action="store_true", help="retail boot, the launcher's: no debug segment")
  a = parser.parse_args()
  if a.cmd == "install":
    install(a.game, a.file, a.scenario)
  elif a.cmd == "remove":
    remove(a.game)
    print(f"removed the smoke test from goal_src/{a.game}; compile again before committing")
  else:
    return run(a.game, a.name, a.timeout, a.retail)
  return 0


if __name__ == "__main__":
  sys.exit(main())
