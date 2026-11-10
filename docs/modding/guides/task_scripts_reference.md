# Task Commands & Modding Scripts Reference

> OpenGOAL Reference Manual
>
> - **Applies to:** Jak 1 / Jak 2 / Jak 3 (OpenGOAL PC Port) — every mod
> - **Origin:** `master-dev`
> - **Scope:** Taskfile Automation (`Taskfile.yml`), Build Targets & Modding Python Scripts (`scripts/modding/*.py`)

## Contents

1. [Philosophy & Mental Model](#1-philosophy--mental-model)
2. [Game & Environment Configuration](#2-game--environment-configuration)
3. [C++ Build & Compilation Tasks](#3-c-build--compilation-tasks)
4. [Asset Baking & Game Execution](#4-asset-baking--game-execution)
5. [REPL & Live Code Iteration](#5-repl--live-code-iteration)
6. [Decompiling](#6-decompiling)
7. [Asset Ripping](#7-asset-ripping)
8. [Tools](#8-tools)
9. [Tests](#9-tests)
10. [Modding Automation Scripts (`scripts/modding/`)](#10-modding-automation-scripts-scriptsmodding)
11. [Common Developer Workflows](#11-common-developer-workflows)

---

## 1. Philosophy & Mental Model

In OpenGOAL, developer commands are unified under [Taskfile](https://taskfile.dev/) (`Taskfile.yml`). Instead of remembering long CMake flags, compiler paths, or Python scripts with complex arguments, `task <command>` provides cross-platform, deterministic shortcuts.

### Why use Task?
1. **Targeted Speed:** Never compile 20 binaries when you only need two (`gk` and `goalc`).
2. **Deterministic Environment:** Task manages environment variables (`.env`, active game selection) automatically.
3. **Reproducible CI/CD:** Modding Python scripts run identically locally and in automated GitHub Actions.

---

## 2. Game & Environment Configuration

| Command | When to use? | Why? | What it does under the hood |
| :--- | :--- | :--- | :--- |
| `task set-game-jak1` | When starting work on a Jak 1 mod. | Switches compiler target and asset search paths to Jak 1. | Runs `python ./scripts/tasks/update-env.py --game jak1`, updating `./scripts/tasks/.env`. |
| `task set-game-jak2` | When starting work on a Jak 2 mod. | Switches compiler target and asset search paths to Jak 2. | Updates `.env` to `GAME=jak2`. |
| `task set-game-jak3` | When starting work on a Jak 3 mod. | Switches compiler target and asset search paths to Jak 3. | Updates `.env` to `GAME=jak3`. |
| `task settings` | Whenever diagnosing configuration issues. | Displays the currently selected game, build dirs, and tool paths. | Runs `update-env.py --info`. |
| `task set-decomp-ntscv1` | When extracting assets from a Black Label NTSC ISO. | Sets the appropriate decompiler config for asset matching. | Updates `DECOMP_CONFIG` in `.env`. |
| `task set-decomp-pal` | When extracting from a European PAL ISO. | Sets PAL translation and asset mappings. | Updates `DECOMP_CONFIG` to PAL. |

---

## 3. C++ Build & Compilation Tasks

### Generator Configuration
- `task gen-cmake-release`:
  - **When?** Once after cloning the repo, after deleting `build/`, or after CMakeLists changes.
  - **Why?** Configures CMake with Clang and Ninja. Automatically detects and enables `sccache` compiler cache if installed on PATH, accelerating rebuilds by 10×.
- **sccache** (optional, recommended): install it (`scoop install sccache` on Windows, `sudo apt install sccache` or `cargo install sccache` on Linux, `brew install sccache` on macOS), then run `task gen-cmake-release` again, which wires it in when it is on `PATH`. Raise its cache size so several mods fit (`setx SCCACHE_CACHE_SIZE 25G` on Windows, `export SCCACHE_CACHE_SIZE=25G` elsewhere). `sccache --show-stats` shows the cache hits: a rebuild after switching mods should be almost all hits.
- `task clean-cmake`:
  - **When?** When CMake cache corruption occurs or after major upstream refactors.
  - **Why?** Deletes `build/` and `out/build/` cleanly.

### Compilation Targets (The 3-Layer Rule)
- `task build-release-game`:
  - **When?** Fast iteration when modifying engine C++ (`game/`), OpenGL shaders, or the compiler (`goalc/`).
  - **Why?** Compiles ONLY `gk` and `goalc`. Skips ~18 unneeded binaries (decompiler, LSP, unit tests). Takes seconds instead of 10+ minutes.
- `task build-release-decomp`:
  - **When?** When changing asset extraction logic, glTF model injection (`extra_art_groups_by_dgo`), or collision parsers in `decompiler/`.
  - **Why?** Compiles ONLY the decompiler binary. A decompiler change reaches the game only after `task extract` (or the decompile tasks of §6) runs it again.
- `task build-release`:
  - **When?** First setup or when cutting a full release.
  - **Why?** Builds all ~20 executables across the repository.
- `task build-debug`, `task build-debug-game`, `task build-debug-decomp`: the Debug equivalents, after `task gen-cmake-debug`.

---

## 4. Asset Baking & Game Execution

| Command | When to use? | Why? | What it does under the hood |
| :--- | :--- | :--- | :--- |
| `task extract` | After installing an ISO or changing decompiler config / injected assets. | Extracts 3D models, textures, animations, and level collision from `./iso_data` into `./decompiler_out`. | Runs `decompiler.exe` with `levels_extract: true`. |
| `task boot-game` | Cold launch testing of your mod. | Launches the game natively in debug mode (`-debug`), loading save slot 1. | Runs `gk.exe -v --game jak[x] -- -boot -fakeiso -debug`. |
| `task boot-game-retail` | Mandatory testing of in-game Mods menu (`mods-menu.gc`). | Reproduces retail boot (`-boot -fakeiso` without debug segment), proving the mod works for players. | Runs `gk.exe` without `-debug`. |
| `task run-game` | Launching runtime in listening mode for the REPL. | Keeps the game window waiting for REPL connection. | Runs `gk.exe -- -fakeiso -debug`. |

---

## 5. REPL & Live Code Iteration

- `task repl`:
  - **When?** During GOAL Lisp coding (`goal_src/**/*.gc`).
  - **Why?** Opens the interactive `goalc` compiler shell connected to the running game.
  - **Inside the REPL:**
    - `(mi)`: Incremental compile — re-reads modified `.gc` files and injects updated functions/states directly into game memory in milliseconds.
    - `(r)`: Restarts the active process or resets the level state.
- `task compile-check`:
  - **When?** To verify that the active game's GOAL code compiles, without a running game.
  - **Why?** Runs the same `(mi)` through `goalc --cmd` and exits, so it never opens a game window. This is the verification step AI agents are allowed to run on their own.

---

## 6. Decompiling

These tasks drive the same `decompiler` binary as `task extract` in §4, but target the code-decompilation and `goal_src/` authoring loop instead of level/asset baking. Most take a `FILE=<object-name>` argument (the bare object name, no extension) to scope the run to a single GOAL object instead of the whole game. They require `task build-release-decomp` (or `build-release`) to have been run first, and the active game/decomp config set via §2.

- `task disasm`
  - **When?** You need raw EE disassembly and per-function metadata for the whole game, without producing GOAL pseudo-code.
  - **Why?** Runs the decompiler with `--config-override '{"disassemble_code": true, "dump_function_metadata": true, "levels_extract": false}'` — disassembly only, no decompilation, no level extraction.
  - *Example:*
    ```bash
    task disasm
    ```

- `task decomp-no-override`
  - **When?** You want the decompiler to run using exactly the flags already baked into `decompiler/config/<game>_config.jsonc`, with no ad-hoc override forcing `decompile_code`/`levels_extract` one way or the other.
  - **Why?** It's the one decompiling task that passes no `--config-override`, useful for reproducing the config file's own committed defaults exactly.
  - *Example:*
    ```bash
    task decomp-no-override
    ```

- `task decomp`
  - **When?** Standard "redecompile the whole game" pass — after editing type hints/casts across many objects in the decompiler config, and you want fresh `.gc` output in `decompiler_out/<game>/` for everything.
  - **Why?** Runs with `{"decompile_code": true, "levels_extract": false}` — full code decompilation, skipping the slower level/texture extraction step.
  - *Example:*
    ```bash
    task decomp
    ```

- `task disasm-file`
  - **When?** Debugging one object's raw disassembly — e.g. tracing why the decompiler mis-detects a function's args, or crashes on it — without disassembling the whole game.
  - **Why?** Same override as `disasm`, plus `"allowed_objects": ["<FILE>"]` to scope it to a single object.
  - *Example:*
    ```bash
    task disasm-file FILE=vehicle-turret
    ```

- `task decomp-file`
  - **When?** The everyday decompiling loop: tweak a type hint or cast for one object in the decompiler config, re-decompile just that object, and inspect the result in `decompiler_out/<game>/`.
  - **Why?** Same override as `decomp`, scoped to a single object via `allowed_objects`.
  - *Example:*
    ```bash
    task decomp-file FILE=vehicle-turret
    ```

- `task decomp-clean`
  - **When?** Before a clean full re-decompile or re-extraction, so stale output doesn't pollute a diff or mask a regression.
  - **Why?** Runs `scripts/tasks/clean-decomp.py --game <game>`, which deletes every `*.gc` and `*.asm` file under `decompiler_out/<game>/`.
  - *Example:*
    ```bash
    task decomp-clean
    ```

- `task lint-gsrc-file`
  - **When?** After hand-editing a file that already lives in `goal_src/`, before committing it.
  - **Why?** Runs `scripts/gsrc/lint-gsrc-file.py`, which scans the file for known decompilation leftovers — unresolved `method-of-type`/`method-of-object` splits, raw `(t9-N ...)` function-pointer calls, missing args, `;; ERROR` markers — and prints them with file/line context. It only reports; it doesn't rewrite anything.
  - *Example:*
    ```bash
    task lint-gsrc-file FILE=vehicle-turret
    ```

- `task update-gsrc`
  - **When?** You've been editing the decompiler config and now have a pile of changed or new `*_REF.gc` files under `test/decompiler/reference/<game>/` that need folding back into `goal_src/`, and don't want to do it file by file.
  - **Why?** Runs `scripts/gsrc/update-gsrc-via-refs.py`, which finds every changed/untracked `*_REF.gc` via `git status`, re-decompiles the matching object with the current decompiler build, and merges the fresh output into the matching `goal_src/` file.
  - *Example:*
    ```bash
    task update-gsrc
    ```

- `task update-gsrc-glob`
  - **When?** Same as `update-gsrc`, but scoped to a subset of reference files by pattern instead of relying on git's changed-file list — e.g. re-running after those `_REF.gc` files are already committed.
  - **Why?** Same script, but selects files with `--file_pattern <GLOB>` matched against `test/decompiler/reference/<game>/` instead of git status.
  - *Example:*
    ```bash
    task update-gsrc-glob GLOB="vehicle-turret*"
    ```

- `task update-gsrc-file`
  - **When?** The single-file version of the port-and-clean loop: you're actively working on one object and want the decompiler's latest output folded into its `goal_src/` file — with your comments and `;; decomp deviation` blocks preserved — then linted, in one command.
  - **Why?** Chains three steps: `decomp-file FILE=<obj>` (re-decompile) → `scripts/gsrc/update-from-decomp.py` (merges the fresh decompilation into the existing `goal_src/` file using heuristics that try to keep 100% of comments and decomp-deviation blocks in their original spot) → `lint-gsrc-file` (reports any leftovers).
  - *Example:*
    ```bash
    task update-gsrc-file FILE=vehicle-turret
    ```

- `task copy-common-naming`
  - **When?** Syncing variable/function naming decisions for one shared "common" object across game versions.
  - **Why?** Runs `scripts/gsrc/copy-common-naming.py --file <FILE> --decompiler <path-to-decompiler>`, then reformats the touched `.jsonc` configs with `format-json`.
  - > [!IMPORTANT]
  - > This task currently points at `scripts/gsrc/copy-common-naming.py`, which no longer exists in the repo (removed during a decomp cleanup pass). It will fail immediately with a Python "file not found" error until the script is restored or the task is repointed.
  - *Example:*
    ```bash
    task copy-common-naming FILE=vehicle-turret
    ```

- `task copy-common-naming-from-refs`
  - **When?** Bulk-syncing naming across all common objects at once, instead of one file at a time.
  - **Why?** Same script as `copy-common-naming`, called with `--update-names-from-refs` instead of `--file`, then `format-json`.
  - > [!IMPORTANT]
  - > Same missing-script issue as `copy-common-naming` above — currently fails until `scripts/gsrc/copy-common-naming.py` is restored.
  - *Example:*
    ```bash
    task copy-common-naming-from-refs
    ```

---

## 7. Asset Ripping

These are `task extract` variants for pulling assets out in formats meant for use *outside* the engine (external 3D tools, standalone PNGs, playable audio), rather than the engine's internal `.fr3`/texture-page formats. Same prerequisites as `task extract` in §4: a populated `./iso_data`, and the decompiler built.

| Command | When to use? | Why? | What it does under the hood |
| :--- | :--- | :--- | :--- |
| `task rip-textures` | Building a texture pack, or previewing every texture the game uses outside the engine. | Extracts levels and additionally dumps every texture as a standalone `.png`. | Runs the decompiler with `{"levels_extract": true, "save_texture_pngs": true}`; PNGs land under `decompiler_out/<game>/textures/`. |
| `task rip-levels` | Bringing level geometry into an external 3D tool (e.g. Blender) instead of the engine's internal format. | Extracts levels and additionally exports each one as glTF. | Runs the decompiler with `{"levels_extract": true, "rip_levels": true}`; writes `<level>-background.glb` plus foreground art group glTFs under `decompiler_out/<game>/levels/`. |
| `task rip-collision` | Inspecting or reusing a level's collision mesh outside the engine. | Extracts levels and additionally dumps collision as `.obj`. | Runs the decompiler with `{"levels_extract": true, "extract_collision": true, "rip_collision": true}`; writes `collide-<name>.obj` under `decompiler_out/<game>/collision/`. |
| `task rip-audio` | Pulling voice lines or streamed SFX out of the game's audio files. | Extracts levels and additionally rips streamed audio. | Runs the decompiler with `{"levels_extract": true, "rip_streamed_audio": true}`; output lands under `decompiler_out/<game>/audio/voice_lines/` and `decompiler_out/<game>/audio/sfx/`. |

---

## 8. Tools

Standalone utilities and CI-style checks wrapped as tasks. Most need `task build-release` (or the specific lighter target that produces the binary in question) run first.

- `task analyze-ee-memory FILE=<path-to-.p2s>`
  - **When?** You have a PCSX2 savestate and want to search its Emotion Engine RAM dump for a known value or pattern — typically while reverse-engineering an in-memory struct.
  - **Why?** Unzips the savestate (`.p2s` files are zip archives) into `savestate_out/`, then runs `memory_dump_tool` against the extracted `eeMemory.bin`, writing results to `ee-analysis.log`.
  - *Example:*
    ```bash
    task analyze-ee-memory FILE=savestates/traffic-manager.p2s
    ```

- `task watch-pcsx2`
  - **When?** Iterating against live memory in PCSX2 and you want every savestate you take analyzed automatically, instead of running `analyze-ee-memory` by hand each time.
  - **Why?** Watches `SAVESTATE_DIR` (defaults to the current directory) with `watchmedo` and runs `task analyze-ee-memory FILE=<new file>` whenever a `.p2s` file appears.
  - > [!NOTE]
  - > Needs the `watchdog` package with its CLI extra: `pip install -U "watchdog[watchmedo]"`. Without it, `watchmedo` isn't on PATH and the task fails immediately.
  - *Example:*
    ```bash
    task watch-pcsx2 SAVESTATE_DIR="C:/Users/me/Documents/PCSX2/sstates"
    ```

- `task type-search`
  - **When?** You know a struct has, say, two `int16` fields at specific byte offsets and want to search every known type for a matching field layout, to help identify an unknown type.
  - **Why?** Runs `type_searcher` against every known type looking for that field layout (an array of `{type, offset}` pairs passed via `--fields`), writing matches to `search-results.json`. As the task's own description says, this is "just an example to show it running" — the `--fields` payload is hardcoded in `Taskfile.yml` itself (`int16` at offsets 2 and 4); a real search means editing that JSON in the task, or invoking `type_searcher` directly with your own `--fields`.
  - *Example:*
    ```bash
    task type-search
    ```

- `task update-treesitter`
  - **When?** After changing the OpenGOAL grammar in the separate `tree-sitter-opengoal` repo, and you want the parser tables used by editor tooling in this repo refreshed.
  - **Why?** Runs `yarn gen` in `../tree-sitter-opengoal`, then copies its generated `src/` and `grammar.js` into `third-party/tree-sitter/tree-sitter-opengoal` here.
  - > [!IMPORTANT]
  - > Requires a sibling checkout of `tree-sitter-opengoal` at `../tree-sitter-opengoal` (next to this repo's folder, not inside it), with `yarn` installed. Fails immediately otherwise.
  - *Example:*
    ```bash
    task update-treesitter
    ```

- `task fix-translations`
  - **When?** After editing any in-game text/subtitle string tables, before committing.
  - **Why?** Runs `scripts/ci/lint-autoglottonyms.py --fix` (makes sure each language's name for itself, e.g. "English"/"français", is never accidentally translated) then `scripts/ci/lint-characters.py --fix` (strips or replaces characters outside the game's supported glyph set), both in fix mode.
  - *Example:*
    ```bash
    task fix-translations
    ```

- `task lint`
  - **When?** Before committing changes under `goal_src/`, or to see why CI's lint step failed.
  - **Why?** Runs `scripts/ci/lint-trailing-whitespace.py` (no `--fix`) over every `.gc`/`.gs`/`.gd` file in `goal_src/`; lists offending files and exits non-zero instead of modifying anything. Use `task format` if you want it fixed instead of just reported.
  - *Example:*
    ```bash
    task lint
    ```

- `task run-gpu-test`
  - **When?** Verifying the runtime's OpenGL support on a machine before chasing a graphics bug, or for a headless GPU sanity check.
  - **Why?** Runs `gk -v --gpu-test opengl --gpu-test-out-path ./gpu-test.json` — the game's built-in self-test, without booting into a save.
  - *Example:*
    ```bash
    task run-gpu-test
    ```

---

## 9. Tests

These wrap the `goalc-test` and `offline-test` binaries. `unit-tests` uses the Debug build (`task build`); everything else uses the Release build (`task build-release`). The `offline-test`-based tasks additionally decompile straight from the ISO data and compare it against the checked-in `test/decompiler/reference/<game>/*_REF.gc` files.

- `task unit-tests`
  - **When?** Running the Debug-build GOAL compiler/runtime unit test suite, e.g. after touching `goalc/` internals.
  - **Why?** Runs `goalc-test` (Debug build) with whatever raw gtest flags you pass via `TEST_FILTER` — nothing is filtered by default.
  - *Example:*
    ```bash
    task unit-tests TEST_FILTER='--gtest_filter="*TypeSystem*"'
    ```

- `task offline-tests`
  - **When?** Running the full decompiler-output-vs-reference regression suite for the active `GAME`. This is what CI (Jenkins) runs.
  - **Why?** Runs `offline-test --iso_data_path ./iso_data/<game> --game <game> --fail-on-cmp`, which decompiles every object straight from the ISO data and fails if the output doesn't match the checked-in `_REF.gc` files.
  - > [!IMPORTANT]
  - > Needs the game's raw files extracted at `./iso_data/<game>/` — the same location `task extract` reads from. Without it, the tests can't even start.
  - *Example:*
    ```bash
    task offline-tests
    ```

- `task offline-test-file`
  - **When?** Checking whether a single object's decompilation still matches its reference file, without running the whole suite.
  - **Why?** Same as `offline-tests`, scoped to one object via `--file`.
  - *Example:*
    ```bash
    task offline-test-file FILE=vehicle-turret
    ```

- `task offline-tests-fast`
  - **When?** Running the full offline suite locally, trading a clean pass/fail summary for parallelism and dumped output.
  - **Why?** Same as `offline-tests`, plus `--pretty-print --num_threads 8 --dump_current_output` (multi-threaded, and writes each failing object's actual output to `./failures/` for inspection).
  - *Example:*
    ```bash
    task offline-tests-fast
    ```

- `task update-ref-tests`
  - **When?** You intentionally changed decompiler output for many objects (config or decompiler code change) and want to "bless" the new output as the new reference, in bulk.
  - **Why?** Clears `./failures/`, runs the offline suite tolerating failures (to populate `./failures/` with current output), copies those into `test/decompiler/reference/<game>/` via `scripts/update_decomp_reference.py`, then re-runs `offline-tests-fast` to confirm everything now passes.
  - *Example:*
    ```bash
    task update-ref-tests
    ```

- `task update-ref-file`
  - **When?** Same as `update-ref-tests`, but for a single object.
  - **Why?** Same pipeline, scoped to `--file <FILE>`, finishing with `offline-test-file` instead of the full suite.
  - *Example:*
    ```bash
    task update-ref-file FILE=vehicle-turret
    ```

- `task type-test`
  - **When?** Verifying GOAL type definitions (sizes, field offsets, method layouts) are internally consistent for the active game.
  - **Why?** Runs the Release `goalc-test` binary filtered to `*<TYPE_CONSISTENCY_TEST_FILTER>*` (set per-game by `update-env.py`, e.g. `Jak1TypeConsistency`) with `--gtest_break_on_failure`.
  - *Example:*
    ```bash
    task type-test
    ```

- `task tests-filtered`
  - **When?** Running a specific subset of the Release `goalc-test` suite by name — e.g. re-running just the test you're fixing.
  - **Why?** Runs `goalc-test --gtest_filter="*<FILTER>*" --gtest_break_on_failure`.
  - *Example:*
    ```bash
    task tests-filtered FILTER=TrafficManager
    ```

---

## 10. Modding Automation Scripts (`scripts/modding/`)

These tasks wrap specialized Python automation scripts located in `scripts/modding/` and `scripts/ai/`. You can pass parameters to any script after `--` (e.g. `task modding-new-mod -- --new jak2/my-mod --youtube https://youtu.be/...`).

---

### 1. `task modding-new-mod [-- options]`
- **Script:** [`create_mod_repo.py`](../../../scripts/modding/create_mod_repo.py)
- **When?** Starting a new mod (run it without arguments: it asks for the game, the mod name, one sentence for players, the visibility, then confirms), or moving a mod that still lives on a branch of this repository into its own repository.
- **Why?** Every mod lives in its own GitHub repository, `<owner>/<game>-mod-<slug>`, where the slug is the mod's launcher catalog key kept verbatim. In this clone that repository is the remote `<name>` and the local branch `mods/<name>` (see `task modding-switch`). Nothing is deleted: the source branch and `mods/<name>` both stay. Two steps, so a failed run can simply be re-run:
  1. **Prepare (local):** builds `mods/<name>` in a temporary worktree in the system temp folder (outside your editor's workspace): the mod merged with the local `master-dev` under the mod-repository rules, plus one commit with the repository-specific changes (README from the template or a "moved from" note, catalog name and `websiteUrl`). Re-running it merges newer `master-dev` commits into a prepared branch.
  2. **Publish:** creates the public repository with the `opengoal-mod` topic (how the global catalog finds it), pushes `mods/<name>` as its `main`, and sets `mods/<name>` to track and push to it. Needs `gh`, authenticated with `gh auth login`.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | *(none)* | | Ask for everything interactively. |
  | `--new <game>/<slug>` | String | Start a new mod from `master-dev` without questions, e.g. `jak2/my-mod`. |
  | `--from-branch <branch>...` | One or more strings | Move mod branches (`jak[1-3]/<type>/<slug>`, or their archive tags `archive/<branch>`) into their own repositories. |
  | `--description "<text>"` | String *(optional)* | One-line description (README overview of a new mod, and the repository description). |
  | `--youtube <url>` | String *(optional)* | Demo video URL embedded in a new mod's README. |
  | `--private` | Flag *(optional)* | Create private repositories; they stay out of the launcher catalog until made public. |
  | `--prepare-only` | Flag *(optional)* | Build the local branches and publish nothing. |
  | `--redo` | Flag *(optional)* | Rebuild prepared branches that were never published. |

*Example:*
```bash
task modding-new-mod
task modding-new-mod -- --new jak2/traffic-overhaul --description "Denser, smarter Haven City traffic." --private
```

---

### 2. `task modding-switch -- <target>`
- **Script:** [`switch_mod.py`](../../../scripts/modding/switch_mod.py)
- **When?** Moving this working directory to another mod or back to `master-dev`. With one worktree per mod ([`repository_workflow.md`](repository_workflow.md#one-worktree-per-mod)) you open the mod's folder instead; git refuses to switch to a mod checked out in a worktree.
- **Why?** Mod repositories share their history with this repository, so one clone holds them all and the extracted game data and build cache stay shared. A mod repository `<name>` becomes the remote `<name>` and the local branch `mods/<name>`, created and fetched on first use. The task also parks the knowledge-base submodule before switching to an old mod branch restored from its archive tag (those carry a plain copy of the skills at `.agents/skills`), then refreshes the knowledge base and the skill links. Those branches predate the task: to leave one, run `git switch master-dev`, then `task kb-update`. An archived branch named as the target gets the command that restores it.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | `<target>` | Positional | A mod repository name (`jak2-mod-my-mod`), `master-dev`, or a branch name. |
  | `--list` | Flag *(optional)* | List the mod repositories (here and on GitHub), and the old mod branches if any are left. |

*Example:*
```bash
task modding-switch -- jak2-mod-my-mod
```

---

### 3. `task modding-sync-branch -- [options]`
- **Script:** [`sync_branch_with_master_dev.py`](../../../scripts/modding/sync_branch_with_master_dev.py)
- **When?** Whenever your mod needs the latest modding base. On `mods/<name>` it pushes to the mod repository's `main`; on a restored mod branch, to that branch.
- **Why?** Merges `master-dev` into the current mod while preserving its root `README.md` and `index.json`, taking the shared agent configuration and docs from `master-dev`, and dropping the workflows and files that only belong to the mother repository.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | `--remote <name>` | String (`origin` here, `mother` in a standalone mod clone) | Remote holding `master-dev`; `mother` is added automatically on first use. |
  | `--branch <name>` | String (`current branch`) | Target branch to synchronize. |
  | `--rebase` | Flag *(optional)* | Uses `git rebase` instead of `git merge` (rewrites local commit history; use only on unpushed local commits). |
  | `--push` | Flag *(optional)* | Pushes the synchronized branch to its upstream (`mods/<name>` to the mod repository's `main`) if the merge succeeds cleanly. |
  | `--source <branch>` | String (`master-dev`) | Source branch to merge from. |
  | `--local-source` | Flag *(optional)* | Merge the local source branch without fetching (used by `create_mod_repo.py`). |
  | `--mod-repo` | Flag *(optional)* | Apply the mod-repository rules; automatic on `mods/*` branches and with the `mother` remote. |

*Example (on `mods/<name>`):*
```bash
task modding-sync-branch -- --push
```

---

### 4. `task modding-sync-all -- [options]`
- **Script:** [`sync_all_mods.py`](../../../scripts/modding/sync_all_mods.py)
- **When?** After a change on `master-dev` that every mod should get (an engine patch, the Mods menu framework, tooling, shared docs, the knowledge-base pointer).
- **Why?** Runs `task modding-sync-branch -- --push` for every mod repository at once, in the mod's own worktree (`.worktrees/<name>.jak-project`) when it has one, otherwise in a temporary worktree in the system temp folder, so the folder you run it from is never touched. The mod repositories are the local `mods/*` branches plus, when `gh` is installed, the account's repositories with the `opengoal-mod` topic. For each one it fetches it, merges `origin/master-dev` under the mod-repository rules and pushes to its `main`. It skips a mod already up to date, one whose `mods/<name>` holds commits that are not pushed, one whose worktree has uncommitted changes, and the one checked out here (sync that one with `task modding-sync-branch -- --push`). A real conflict leaves that mod untouched, nothing pushed, and the summary names the files; the task then exits with an error.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | `<name>...` | Positional *(optional)* | Only these mod repositories (default: all of them). |
  | `--dry-run` | Flag *(optional)* | Report what each mod needs; merge and push nothing. |

*Example:*
```bash
task modding-sync-all -- --dry-run
task modding-sync-all
```

---

### 5. `task kb-update` and `task ai-link`
- **Scripts:** [`kb_sync.py`](../../../scripts/ai/kb_sync.py), [`link_skills.py`](../../../scripts/ai/link_skills.py)
- **When?** Rarely by hand: the Claude Code SessionStart hook runs both at the start of every session.
- **Why?** `.agents/skills/` is the knowledge-base submodule (`opengoal-modding-kb`, URL in `.gitmodules`). `kb-update` initialises it, puts it on `main` and fast-forwards it, without ever discarding local commits or edits, then links each skill into `.claude/skills/`, the only folder Claude Code reads. `ai-link` does the linking alone. To record a discovery in the knowledge base, follow the `kb` skill.

*Example:*
```bash
task kb-update
```

---

### 6. `task modding-sync-catalog -- [options]`
- **Script:** [`sync_global_catalog.py`](../../../scripts/modding/sync_global_catalog.py)
- **When?** On `master-dev` to refresh and rebuild the unified root `index.json` catalog containing all published mods and versions.
- **Why?** Lists the public mod repositories (same owner, `opengoal-mod` topic), takes from each one the `index.json` attached to its most advanced release (highest version in the tag), and writes the consolidated Launcher v1 schema file. Branches, the `index.json` committed in a repository and the mother repository's releases are never read, so a mod with no release in its own repository is not listed. When GitHub cannot be reached, it stops and leaves `index.json` untouched.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | `--repo <owner/repo>` | String (`auto-detect`) | Mother repository in `owner/repo` format; the mod repositories of its owner are read. |
  | `--output <path>` | Path (`<repo_root>/index.json`) | Output path for the consolidated catalog file. |
  | `--source-name "<name>"` | String (`<Owner> OpenGOAL Mods Hub`) | Catalog display title shown in the OpenGOAL Launcher UI. |
  | `--dry-run` | Flag *(optional)* | Analyzes releases and prints summary statistics to terminal without modifying `index.json`. |

*Example:*
```bash
task modding-sync-catalog -- --dry-run
```

---

### 7. Per-Mod Catalog Tool: `update_mod_catalog.py`
- **When?** During release creation (`release.yml` calls it).
- **Why?** Generates and maintains an individual mod's `index.json` catalog.

---

### 8. `task modding-package-texture-pack` (Alias: `task modding-register-texture-pack`) `-- [options]`
- **Script:** [`package_texture_pack.py`](../../../scripts/modding/package_texture_pack.py)
- **When?** When registering a standalone texture pack into `index.json` after exporting it via the OpenGOAL Texture Pack Generator GUI (or when creating one via CLI with `--from-source`). On `mods/<name>`, its URLs point at the mod repository and its release. The OpenGOAL Launcher does not link a texture pack to a mod: its mod-source schema has no such field, it applies texture packs to the base game only, from a `.zip` the player adds in its Texture Packs screen, and its texture support for installed mods is not finished (checked on its `main` branch, 2026-10-02).
- **Why?** Recovers launcher-compliant `.zip` archives from `docs/modding/current_mod/texture_packs/` (generated by the GUI tool), inspects their internal `metadata.json`, computes SHA256 checksums, and automatically registers or updates the texture pack in `index.json` under `"texturePacks"`. Also supports building directly from raw PNG textures in `custom_assets/<game>/texture_replacements/` when run with `--from-source`.
- **CLI Parameters (`-- <args>`):**
  | Parameter | Type / Default | Description |
  | :--- | :--- | :--- |
  | `--dir <path>` | Path (`docs/modding/current_mod/texture_packs/`) | Directory to scan for `.zip` texture archives. |
  | `--zip <path>` | Path *(optional)* | Specific texture pack `.zip` file to register directly. |
  | `--no-index` | Flag *(optional)* | Inspects archive and computes SHA256 checksums without writing into `index.json`. |
  | `--release-url <url>` | String *(optional)* | Custom base URL where the texture pack `.zip` is hosted for downloads. |
  | `--game <id>` | Choice (`jak1|jak2|jak3|jakx`) | Target game identifier. |
  | `--from-source` | Flag *(optional)* | Compiles a new `.zip` archive directly from raw PNG files in `custom_assets/<game>/texture_replacements/`. |
  | `--slug <slug>` | String *(with `--from-source`)* | Unique identifier slug for the pack. |
  | `--display-name "<name>"` | String *(with `--from-source`)* | Human-readable display title in Launcher. |
  | `--description "<text>"` | String *(with `--from-source`)* | Detailed texture pack description. |
  | `--author "<name>"` | String *(with `--from-source`)* | Author or creator name. |
  | `--version <semver>` | String (`1.0.0`) | Semantic version string. |
  | `--tags <tag...>` | List *(with `--from-source`)* | Keywords/tags for filtering in Launcher. |
  | `--cover <path>` | Path *(with `--from-source`)* | Cover thumbnail image (`cover.png`). |
  | `--output <path>` | Path *(with `--from-source`)* | Destination `.zip` archive file path. |

*Example:*
```bash
# Register GUI-exported zip into index.json:
task modding-package-texture-pack

# Package directly from raw PNG assets:
task modding-package-texture-pack -- --from-source --game jak2 --slug blue-kg-textures --display-name "Blue KG Textures"
```

---

### 9. `task git-hooks`
- **Script:** [`install.py`](../../../scripts/git-hooks/install.py)
- **When?** Once per clone, and again after `scripts/git-hooks/` changes.
- **Why?** Copies the hooks of [`scripts/git-hooks/`](../../../scripts/git-hooks/) into the clone's hooks folder, which its worktrees share. `pre-push` refuses to push a `mods/<name>` branch anywhere but the repository `<name>`: a plain `git push` already goes there, and a typed `git push origin` would otherwise create a `mods/<name>` branch in the mother repository. A hook it did not install is left alone.

*Example:*
```bash
task git-hooks
```

### 10. `task modding-models [-- --check]`
- **Script:** [`mod_models.py`](../../../scripts/modding/mod_models.py)
- **When?** In a mod whose models come from the games' discs (`custom_assets/<game>/models/models.jsonc`), after a fresh clone or a manifest change; then `task extract`.
- **Why?** Makes the models (and the other files the manifest copies) from the discs in `iso_data/`, ripping only the DGOs it lacks: the repository keeps no model taken from a game. The mod's release runs the same tool at the player's install. See [Models made from the player's discs](models_from_the_discs.md).

### 11. In-game smoke tests: `smoke_test.py`
- **Script:** [`smoke_test.py`](../../../scripts/modding/smoke_test.py) (no task: run it with Python)
- **When?** After a big feature of a mod, and only when the user authorized launching the game (AGENTS.md).
- **Why?** `install` copies a scenario (from [`smoke_test.template.gc`](../templates/smoke_test.template.gc)) into the GAME build, `run` boots the game until the scenario prints `SMOKE DONE` (or a timeout) and collects its log, screenshots and crash report in `out/smoke/<name>/`, `remove` takes the scenario out again. Record each run in the mod's test notebook. The `smoke-tests` skill explains the whole procedure.

*Example:*
```bash
python scripts/modding/smoke_test.py install jak3 docs/modding/current_mod/my_mod_smoke.gc
task compile-check
python scripts/modding/smoke_test.py run jak3 my-feature --timeout 300
python scripts/modding/smoke_test.py remove jak3
```

---

## 11. Common Developer Workflows

### Scenario A: Fast LISP Gameplay Modding
1. `task set-game-jak2`
2. `task run-game` (in terminal 1)
3. `task repl` (in terminal 2)
4. Edit `.gc` files in `goal_src/jak2/`
5. In REPL: `(mi)` to hot reload instantly.

### Scenario B: Testing In-Game Mods Menu (Retail Boot)
1. Close REPL and game.
2. `task boot-game-retail`
3. Press **L3 + SELECT** on gamepad to ensure menu opens and toggle functions properly.

### Scenario C: Packaging & Distributing a Custom Texture Pack
1. Place or extract textures in `custom_assets/<game>/texture_replacements/`.
2. Open the [OpenGOAL Texture Pack Generator](https://github.com/whozghiar/open-goal-texture-pack-generator) desktop tool (separate repository).
3. Select textures, set name, version, author, and description, then export the `.zip` archive into `docs/modding/current_mod/texture_packs/`.
4. Register the pack into `index.json`: `task modding-package-texture-pack`.
5. When publishing, upload the `.zip` archive as a GitHub Release asset (or trigger `release.yml` which automatically packages it).

### Scenario D: Bringing the Latest Modding Base into the Mods
1. Every mod at once: `task modding-sync-all` (try `-- --dry-run` first). Only one mod: in its folder (or after `task modding-switch -- <name>`), `task modding-sync-branch -- --push`.
2. If a mod reports a conflict, open its folder (or switch to it), run `task modding-sync-branch`, resolve the reported files, commit and push.
3. Test with cold boot: `task boot-game-retail`.
