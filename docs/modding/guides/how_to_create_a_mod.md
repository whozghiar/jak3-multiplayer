# How to Create a Mod

The whole procedure for a new mod, from your own copy of the project to a release players
install from the OpenGOAL Launcher: which commands to run, which resources to read, how to work
with an AI agent, and how to feed the knowledge base. How the repositories fit together, and
what switching between them implies, is explained in
[`repository_workflow.md`](repository_workflow.md).

`<owner>` below is the GitHub account that holds your mother repository, and `<you>` your own
account: the same one unless you are a collaborator on someone else's.

## 0. Set up once

### Get your own mother repository

The original project is `whozghiar/jak-project`. If it is not yours, fork it: the tooling reads
the owner from your clone's `origin` remote and creates your mods under your account, so a fork
works without editing any script or workflow.

1. **Fork it** on GitHub and keep the name `jak-project`: the mother-only workflows (upstream
   sync, catalog, issue triage) run only in a repository with that name, and mod repositories find
   their mother at `<owner>/jak-project`. Copying only the default branch (`master-dev`) is fine:
   when `master` is missing, the upstream sync starts from upstream directly.
2. **Enable GitHub Actions** in the fork's Actions tab: GitHub disables the workflows of a new
   fork, the scheduled ones included. Without them there is no lint, no release, no upstream sync
   and no catalog. The upstream sync pushes upstream's own workflow changes only with a `GH_PAT`
   secret (a token with the `repo` and `workflow` scopes); without it, it warns and leaves
   `master` behind.
3. **Point players at your catalog.** In your `README.md`, replace the catalog URL of the player
   section with `https://raw.githubusercontent.com/<you>/jak-project/master-dev/index.json`. Your
   `index.json` still lists the original's mods: the catalog workflow (daily, or run it from the
   Actions tab) rebuilds it from your own mod repositories' releases, so they drop out at its
   first run and yours appear with your first release.
4. **The knowledge base** (`.agents/skills`, a submodule) points at the original's public
   repository: reading works as is. To record your own discoveries (section 7), fork
   `whozghiar/opengoal-modding-kb` too, then point the submodule at your fork:
   ```bash
   git submodule set-url .agents/skills https://github.com/<you>/opengoal-modding-kb.git
   task kb-update
   git commit -m "chore: use my knowledge-base fork" .gitmodules
   git push
   ```
5. **Later, to take the original's improvements**, merge its `master-dev` into yours and keep
   your own `index.json` and `README.md` on a conflict:
   ```bash
   git remote add original https://github.com/whozghiar/jak-project.git
   git fetch original master-dev
   git switch master-dev
   git merge original/master-dev
   git push
   ```
   Then bring the change into your mods with `task modding-sync-all` (section 8).

### Install the tools

1. **Clone with the knowledge base:**
   ```bash
   git clone --recurse-submodules https://github.com/<owner>/jak-project.git
   ```
   In an existing clone, `task kb-update` initialises it.
2. **Install** the C++ toolchain from [`docs/setup/system/windows.md`](../../setup/system/windows.md)
   (or `linux.md` / `macos.md`), [Task](https://taskfile.dev/), Python 3, and the GitHub CLI
   (`scoop install gh` on Windows), then log in with `gh auth login`. `sccache` is optional and
   makes rebuilds after a switch near-instant.
3. **Build the tools:**
   ```bash
   task gen-cmake-release
   task build-release
   ```
4. **Extract each game you mod.** Copy the content of your own game disc into
   `iso_data/<game>/` (for example `iso_data/jak2/`), then:
   ```bash
   task set-game-jak2
   task extract
   ```
   `iso_data/`, `decompiler_out/` and `out/` are shared by every mod you switch to in this clone.
5. **Install the git hooks:** `task git-hooks`. Its pre-push hook refuses to push a
   `mods/<name>` branch anywhere but its own repository, so a typed `git push origin` cannot
   create a mod branch in the mother repository.

## 1. Create the mod repository

```bash
task modding-new-mod     # from the main folder, on master-dev
```

The task asks for:

| Question | What it decides |
| :--- | :--- |
| Game | `jak1`, `jak2` or `jak3`: the first part of the repository name. |
| Mod name | The repository name (`<game>-mod-<name>`, shown in the question) and the mod's launcher catalog key. Players' launchers know the mod by this key, so pick it for good. |
| One sentence | The README overview and the repository description. |
| Demo video | Optional YouTube link embedded in the README. |
| Visibility | `public` (default) or `private`. A private mod stays out of the launcher catalog until you make it public (see section 9). |

It shows a summary and asks for confirmation, then creates `<owner>/<game>-mod-<name>` from
`master-dev`, with a README from [`MOD_README.template.md`](../templates/MOD_README.template.md),
and the local branch `mods/<game>-mod-<name>`. Give it its own folder, a worktree, and open it:
[Add a mod's worktree](repository_workflow.md#add-a-mods-worktree) has the commands, game
selection and first build included. With a single folder instead:

```bash
task modding-switch -- jak2-mod-my-mod
task set-game-jak2
```

## 2. Learn the rules and find the code

Read these before writing code:

| Resource | Why |
| :--- | :--- |
| [`AGENTS.md`](../../../AGENTS.md), section 4 | The golden rules: every change off by default behind a runtime toggle, no edits to shared menu files, comments. |
| [`mods_menu.md`](mods_menu.md) | How the in-game Mods menu (L3 + SELECT) works and how a mod registers in it. |
| The Lisp wiki, [`.agents/skills/goal-lisp/wiki/`](../../../.agents/skills/goal-lisp/wiki/index.md) | Verified GOAL syntax and engine behavior: `common.md`, then the file of your game. The only place GOAL code examples live. Its background pages cover GOAL's concepts, the engine and build, custom assets and textures. |
| `goal_src/<game>/` | The decompiled game code you hook into. Search it for the system you change. |
| `decompiler_out/<game>/` | Extracted assets (textures, levels) to find names and IDs. |

The knowledge base in `.agents/skills/` also holds focused guides, which an AI agent loads by
itself when the task matches:

| Skill | Use it for |
| :--- | :--- |
| `smoke-tests` | Testing a feature in game with a scenario, and the mod's test notebook (section 4). |
| `kb` | Recording a discovery (section 7). |
| `documentalist` | The documentation rules. |

## 3. Wire the toggle and register your files

1. **Put your code in your own files.** Touch vanilla files only where a hook is unavoidable,
   and mark each touch point with a `;; MOD <slug> --` comment so it stays easy to find and
   to merge.
2. **Register in the Mods menu**: copy
   [`mod_menu.template.gc`](../templates/mod_menu.template.gc) into your mod's menu file,
   for example `goal_src/jak2/pc/features/<slug>-menu.gc`. Never make that file debug-only: a
   debug segment is not linked in a launcher boot. Names: config variables and helpers prefixed
   with the slug, builder named `mod-<slug>-build-menu` (see `mods_menu.md`).
3. **Register every new `.gc` file:** its `.o` in a `.gd` list
   (`goal_src/<game>/dgos/*.gd`, a menu file after `"mods-menu.o"`). Jak 1 also needs a compile
   step in `goal_src/jak1/game.gp`; Jak 2 and Jak 3 find the file by its name. A `build-actor`
   art group listed in a stock level's `.gd` must be pre-marked in `game.gp`. The exact lines
   are in the Lisp wiki,
   [Registering a new source file](../../../.agents/skills/goal-lisp/wiki/common.md#registering-a-new-source-file).

## 4. Iterate

You run the game; an AI agent never launches it.

```bash
task run-game     # terminal 1: the game, waiting for the REPL
task repl         # terminal 2: then (mi) after each edit to hot-reload
```

| Check | When |
| :--- | :--- |
| `task compile-check` | After each change: compiles the GOAL code headless, no game window. This is what agents run. |
| `task boot-game` | Before calling a change done: a cold boot catches what hot reload hides (old definitions left in memory, declaration order, a missing registration). |
| `task boot-game-retail` | Before a release: checks the Mods menu in the boot players get (L3 + SELECT). |
| `task build-release-game` | After a change to `game/`, `goalc/` or `common/` C++. |
| `task build-release-decomp`, then `task extract` | After a change to `decompiler/` or `decompiler/config/`. |

Two traps when testing: a cold boot loads save slot 1, and toggled cheats and settings persist
in `%APPDATA%/OpenGOAL/<game>/settings/pc-settings.gc`. Both are shared by every mod you switch
to.

After each big feature, smoke test it in game: a scenario file drives the game and prints its
results (`scripts/modding/smoke_test.py`, scenario template
[`smoke_test.template.gc`](../templates/smoke_test.template.gc)), and the mod's test notebook
(`docs/modding/current_mod/<slug>_tests.md`, from
[`TEST_NOTEBOOK.template.md`](../templates/TEST_NOTEBOOK.template.md)) keeps each test and its
last result. An AI agent follows the `smoke-tests` skill, and launches the game for it only with
your go-ahead.

## 5. Custom assets (optional)

- **Models, animations, sounds, levels:** sources under `custom_assets/<game>/`; the wiki's
  [assets page](../../../.agents/skills/goal-lisp/wiki/assets.md) walks through the Blender
  export and the build steps.
- **Models taken from a game** (another game's character, a music file): never commit them.
  Describe them in `custom_assets/<game>/models/models.jsonc`; `task modding-models` makes them
  from your discs, and the release makes them from the player's at install:
  [`models_from_the_discs.md`](models_from_the_discs.md).
- **Textures:** inside the mod (PNGs under `custom_assets/<game>/texture_replacements/`, baked
  at install), or as a texture pack published next to it, which the launcher applies to the base
  game only. Both, step by step: [`texture_packs.md`](texture_packs.md).

## 6. Document the mod

| File | For | Content |
| :--- | :--- | :--- |
| `README.md` at the root | Players | Overview, features, controls, demo video. No change log, no checklist. |
| `docs/modding/current_mod/<slug>_readme.md` | Developers and agents | Architecture, the hooks you placed and why, memory and performance notes. Link to the Lisp wiki instead of pasting GOAL code. |
| `docs/img/mod/mod_cover.png` | The launcher | Optional cover thumbnail. |

## 7. Record what you learned in the knowledge base

When you verify something another mod could reuse (a GOAL pattern, a trap, an engine behavior,
the cause of a crash), it goes into the knowledge base, not into the mod. A fact is verified
when it compiled, when you saw it in game, or when it is read in `goal_src/`. Recording needs
push access to the knowledge-base repository: in a fork, your own fork of it (section 0).

With an AI agent, ask it to record the fact: it follows the `kb` skill. By hand:

```bash
git -C .agents/skills switch main
git -C .agents/skills pull --ff-only
# edit the entry that covers the topic, or add one in the right file
git -C .agents/skills add -A
git -C .agents/skills commit -m "jak2: <topic>"
git -C .agents/skills push
```

- Search first (`grep -rniE "<keywords>" .agents/skills`) and edit the existing entry rather
  than adding a second one.
- When a new fact contradicts an entry, replace the old statement and say so in the commit.
- End each entry with its evidence: `Verified: <game>, <file>, <date>.`

Every repository picks the change up at its next Claude Code session, or with
`task kb-update`.

## 8. Commit, push and keep up with the base

- `git push` on `mods/<name>` goes to the mod repository's `main`.
- When the mod needs something newer from the base, run `task modding-sync-branch -- --push`: it
  merges `master-dev` into the current mod and pushes.
- An improvement every mod should get (an engine patch, the Mods menu framework, a script)
  belongs in `master-dev`: make it in the main folder, commit, push, then run
  `task modding-sync-all`, which merges `master-dev` into every mod repository and pushes them
  (see [`repository_workflow.md`](repository_workflow.md#bring-it-into-every-mod-at-once)).
  If you wrote it in a mod first, bring it over with `git cherry-pick`.

## 9. Release

1. **Check** before releasing:
   - native non-regression: with the mod compiled but its toggle off, the game plays as stock;
   - in-game Mods toggle: at least one entry in the Mods menu under the mod's slug (L3 + SELECT,
     in a retail boot);
   - no edit to `default-menu*.gc`, and every symbol prefixed with the mod's slug;
   - the GOAL patterns the mod relies on are in the Lisp wiki (recorded with the `kb` skill);
   - comments on every new or overridden type, method, state and macro;
   - optional: a cover at `docs/img/mod/mod_cover.png`.

   Then a cold `task boot-game-retail`.
2. **Make it public** if you created it private:
   ```bash
   gh repo edit <owner>/jak2-mod-my-mod --visibility public --accept-visibility-change-consequences
   ```
3. **Run the release** from the repository's Actions tab (`release.yml`), or:
   ```bash
   gh workflow run release.yml -R <owner>/jak2-mod-my-mod --ref main \
     -f mod_name="My Mod" -f mod_description="One sentence." -f tag_name="v1.0.0"
   ```
   It rebuilds Windows and Linux (30 to 60 minutes), tags `<slug>-v1.0.0`, and publishes the
   archives and the mod's `index.json`. Only the repository owner can run it. See
   [`mod_distribution_guide.md`](mod_distribution_guide.md).
4. **Catalog:** the global catalog on `master-dev` lists the release within a day, or at once
   with `gh workflow run sync-global-catalog.yml -R <owner>/jak-project --ref master-dev`.

## 10. Develop with an AI agent

### What every repository already provides

| File | What it does |
| :--- | :--- |
| `AGENTS.md` | The instructions every agent loads: the golden rules, "compile, never launch", the commands. Claude Code reads it through `CLAUDE.md`, Gemini CLI through `.gemini/settings.json`; Codex, Copilot and Cursor read it natively. |
| `.agents/skills/` | The knowledge base: skills an agent loads when the task matches, and the Lisp wiki. Claude Code reads them through links in `.claude/skills/`. |
| `.claude/settings.json` | Claude Code permissions: the compile tasks run without asking; launching the game is denied. A SessionStart hook refreshes the knowledge base and the links. |
| `.claude/hooks/block_game_launch.py` | Blocks any attempt to launch the game or attach the debugger. Other agents have no such hook: `AGENTS.md` forbids it, so say so again if one tries. |

### The session loop

1. **Open the mod's folder, then start the agent there**: `.worktrees/<name>.jak-project` (see
   [`repository_workflow.md`](repository_workflow.md#one-worktree-per-mod)). An agent works on the
   folder its session opens; with a single folder, switch first and not while it works.
2. **Ask for one change with its check**, for example: "Add <feature> to this mod, off by default
   behind a Mods menu toggle; run compile-check and give me the cold-boot command."
3. **The agent** reads the wiki and the code, edits, and runs `task compile-check` until it
   compiles. It may also rebuild the C++ (`task build-release-game`) or the decompiler.
4. **You run the cold boot** it gives you (`task boot-game`, or `task boot-game-retail` for the
   Mods menu) and tell it what you see. A change is done only after that.
5. **The agent documents** the change in `docs/modding/current_mod/<slug>_readme.md`; the README
   changes only when players need to know (a feature, a control). The change log is written by
   hand at release time (the release workflow's `changelog` input), not by the agent.
6. **Knowledge:** ask it to record what was verified and reusable with the `kb` skill; it
   commits and pushes from `.agents/skills`.
7. **Commit and push** the mod. Commit messages written with an agent end with `(AI-assisted)`.

### Good to know

- Claude Code keeps one auto memory per folder, shared by every mod of the working directory:
  ask the agent to put mod notes in `docs/modding/current_mod/` and general facts in the
  knowledge base rather than in its memory.
- Agents never open an issue or a pull request on their own, and say they made one when asked
  to.
- Requests that work well: "This crash happens when <steps>; find the cause in goal_src before
  changing anything." "Record what we verified about <topic> with the kb skill."
