# Agent Guide — OpenGOAL Modding Fork

Instructions for any AI coding agent working in this repository. Claude Code loads this file
through `CLAUDE.md`; Codex, Copilot and Cursor read it natively; Gemini CLI reads it through
`.gemini/settings.json`. Source of truth: `master-dev` of the mother repository, `<owner>/jak-project`.

## 1. Project

OpenGOAL ports the PS2 Jak & Daxter trilogy to native x86-64. `goalc` is the GOAL compiler and
REPL, `game/` builds the `gk` runtime, `decompiler/` extracts assets and code from the ISO,
`goal_src/jak[1-3]/` holds the GOAL source, and `custom_assets/` holds texture replacements and
custom models. This fork adds modding tooling on `master-dev`; every mod is derived from it.
Architecture details: [`docs/project-overview.md`](docs/project-overview.md).

## 2. Skills and knowledge base

Skills live in [`.agents/skills/`](.agents/skills/), the knowledge-base git submodule
(`opengoal-modding-kb`, URL in `.gitmodules`) shared by this repository and every mod repository: one folder per skill with a `SKILL.md`, and the Lisp
wiki in `.agents/skills/goal-lisp/wiki/`. Gemini CLI, Codex, Copilot and Cursor read
`.agents/skills/` natively. Claude Code reads `.claude/skills/`, which `task ai-link` fills with
links; a SessionStart hook refreshes the submodule (`task kb-update`) and the links. Load a skill
only when its description matches the task.

## 3. Verify before you claim

- **Compile, never launch.** Agents may run `task compile-check` (headless `(mi)` through
  `goalc --cmd`, no game window), `task build-release-game` and `task build-release-decomp`.
  Agents never launch the game (`gk`, `task boot-game*`, `task run-game`) and never attach the
  debugger (`(lt)`, `(dbg)`): give the user the exact command and ask what they see. In Claude
  Code, a PreToolUse hook (`.claude/hooks/block_game_launch.py`) enforces this. The one
  exception: when the user has authorized launches for tests, run smoke tests through
  `python scripts/modding/smoke_test.py run`, one scenario per call.
- **Several instances, one port each.** `gk --port <n>` gives an instance its own listener
  (default 8111 + the game version: 8112 Jak 1, 8113 Jak 2, 8114 Jak 3); a compiler reaches it
  with `(lt "127.0.0.1" <n>)`, and a second `goalc` takes its own `-p <nREPL port>` (default
  8181) and `--debug-port`. On one shared port, Windows leaves the second instance without a
  listener and Linux lets the compiler reach either instance.
- **Smoke test every big feature.** After a mod gains a big feature, test it in game with a
  scenario and record the result in the mod's test notebook,
  `docs/modding/current_mod/<slug>_tests.md`: the `smoke-tests` skill.
- **A cold boot is the final check.** Code hot-reloaded with `(mi)` into a running game can look
  fine while broken on a clean launch (stale definitions, declaration order, missing `.gp`
  registration). Ask the user for `task boot-game`, or `task boot-game-retail` for the Mods
  menu, before calling a gameplay change done.
- **Register every new `.gc` file:** its `.o` in a `goal_src/jak[1-3]/dgos/*.gd` list, after the
  files it depends on; Jak 1 also lists it in `game.gp` (the wiki's "Registering a new source
  file").
- **Saves and settings persist across boots.** A cold boot restores save slot 1
  (`%APPDATA%/OpenGOAL/jak[x]/saves/`), and toggled cheats and settings stay in
  `%APPDATA%/OpenGOAL/jak[x]/settings/pc-settings.gc`. Account for both when testing.

## 4. Golden rules for mods

1. **Consult the Lisp wiki first.** [`.agents/skills/goal-lisp/wiki/`](.agents/skills/goal-lisp/wiki/index.md)
   is the only place GOAL code examples live. Never invent an instruction.
2. **Native non-regression.** A mod must not change default game behavior unless asked: every
   change ships off by default, gated behind the mod's runtime toggle.
3. **In-game Mods toggle mandatory** for every mod, switchable from a retail boot.
   - Jak 1 / Jak 2 / Jak 3: the Mods menu opens with L3 + SELECT. See
     [`docs/modding/guides/mods_menu.md`](docs/modding/guides/mods_menu.md) and
     [`docs/modding/templates/mod_menu.template.gc`](docs/modding/templates/mod_menu.template.gc);
     the registration call is in the Lisp wiki.
   - Never edit shared menu files, and never mark your menu file debug-only (a debug segment is
     not linked in a retail boot).
4. **Mandatory in-code comments.** Comment every function, method, state, hook and type change in
   `.gc` (purpose, arguments, return values, side effects).
5. **Non-destructive changes.** Never delete or wipe original `.gc` files; prefer surgical
   overrides and modular extensions. Never reformat a whole upstream `.gc` file: it trips the
   `og:preserve-this` lint and buries the real diff.

## 5. Recording verified discoveries

A verified GOAL pattern, language trap, engine behavior or crash fix that could help another mod
goes into the knowledge base, never into a separate memory or scratch file. Follow the `kb` skill:
find the existing entry, edit it in place, cite the evidence, then commit and push from the
`.agents/skills` submodule. Notes about the current mod alone stay in its `README.md` and
`docs/modding/current_mod/`.

## 6. Language and documentation standards

**English for everything you write into the project**, whatever language the user speaks:
`.md` files, code comments, commit messages, and PR, issue and review text. Each text exists
once, in English alone. Reply to the user in their own language.

Documentation is concise and tutorial-toned; one scope per document, stated in its first
paragraph; no decorative icons (GitHub admonitions are fine); GOAL code only in the Lisp wiki,
except `.gc` templates meant to be copied; verified facts only. The `documentalist` skill is the
full checklist.

## 7. Commands and CI

`task --list` shows every task, and
[`docs/modding/guides/task_scripts_reference.md`](docs/modding/guides/task_scripts_reference.md)
explains them. The ones agents need most:

```bash
task set-game-jak2          # select the active game (jak1, jak2, jak3)
task compile-check          # headless GOAL compile of the active game
task build-release-game     # rebuild gk + goalc after C++ changes
task build-release-decomp   # rebuild the decompiler after decompiler/ changes
task modding-sync-branch    # merge master-dev into the current mod branch
```

Workflows live in `.github/workflows/`;
[`docs/modding/guides/github_workflows.md`](docs/modding/guides/github_workflows.md) documents
their triggers and access control. Workflows that only make sense in the mother repository are
guarded with `endsWith(github.repository, '/jak-project')`, so they also run in a fork of it.
Scripts read the GitHub owner from the `origin` remote: never hardcode an account.

## 8. Git

How the mother repository, the mod repositories and the knowledge base fit together, with the
day-to-day commands: [`docs/modding/guides/repository_workflow.md`](docs/modding/guides/repository_workflow.md).
Creating a mod step by step: [`docs/modding/guides/how_to_create_a_mod.md`](docs/modding/guides/how_to_create_a_mod.md).

- `master` mirrors `open-goal/jak-project`. Never commit to it.
- `master-dev` is the modding base. Every mod starts from it.
- **One repository per mod:** `<owner>/<game>-mod-<slug>`, the slug being the mod's launcher
  catalog key. In this clone it is the branch `mods/<name>`, checked out in its own worktree
  `.worktrees/<name>.jak-project` (or switched to with `task modding-switch -- <name>`, not a bare
  `git switch`). `git push` goes to its `main`, and
  `task modding-sync-branch -- --push` merges the latest `master-dev` into it.
  `task modding-sync-all` does it for every mod repository; run it only when the user asks.
- In a worktree, `iso_data/`, `decompiler_out/` and the `out/` game folders are junctions to the
  main folder's, shared by every mod. Force the session's first compile with
  `(make-group "iso" :force #t)`: `task compile-check` alone can keep the previous mod's compiled
  files. Delete the junctions with `rmdir` before `git worktree remove`, and keep `git clean -x`
  out of worktrees ([`repository_workflow.md`](docs/modding/guides/repository_workflow.md#one-worktree-per-mod)).
- `task modding-new-mod` creates a mod repository (it asks for the game, name, description and
  visibility); `-- --from-branch <branch>` moves an archived mod branch into one.
- The old mod branches (`jak[N]/[type]/[slug]`) are archived as tags `archive/<branch>`. Never
  delete a branch or a tag without the user's agreement.
- A mod has two documentation tiers: the root `README.md` for players (from
  [`docs/modding/templates/MOD_README.template.md`](docs/modding/templates/MOD_README.template.md))
  and `docs/modding/current_mod/<slug>_readme.md` for developers and agents.

## 9. Contributing

- Append `(AI-assisted)` to every commit, PR, issue or comment written with AI.
- Never delete or overwrite existing source files without explicit agreement.
- Never open an issue or PR on your own. When asked to, state in it that an AI agent made it and
  that a human may not have reviewed it.
