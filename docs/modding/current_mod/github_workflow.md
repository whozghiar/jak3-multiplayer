# Multiplayer (Jak 3): GitHub workflow

How this mod's repository is run on GitHub: where the code lives, how a change goes from a local
commit to the players, and what the GitHub Actions workflows do. The rules shared by every mod
are in [`repository_workflow.md`](../guides/repository_workflow.md) and
[`github_workflows.md`](../guides/github_workflows.md); this page applies them to this mod.

## 1. Where the code lives

| Repository | Role |
|---|---|
| [`whozghiar/jak3-multiplayer`](https://github.com/whozghiar/jak3-multiplayer), branch `main` | This mod: game code, docs, releases and the launcher catalog (`index.json`). |
| [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project), branch `master-dev` | The modding base this mod was created from, merged into it from time to time. |
| [`whozghiar/opengoal-modding-kb`](https://github.com/whozghiar/opengoal-modding-kb) | The knowledge base used by AI agents, mounted as the submodule `.agents/skills/`. |

In a clone of the modding base, the mod is the branch `mods/jak3-multiplayer`, checked out in its
own worktree (`task modding-switch -- jak3-multiplayer`); its remote for `main` is named
`jak3-multiplayer`. In a clone of this repository alone, `main` is the branch and `origin` the remote.

## 2. From a change to a push

1. **Work and check locally.** After a C++ change, `task build-release-game`; then
   `task compile-check`, the unit tests and the scenarios the change touches
   ([technical overview, section 10](multiplayer_technical_overview.md#10-testing)).
2. **Document it.** A line in the change log of [`multiplayer_readme.md`](multiplayer_readme.md)
   (section 7), and the root `README.md` when players see the change.
3. **Commit.** One commit per feature or fix, with a scope: `feat(multiplayer): ...`,
   `fix(multiplayer): ...`, `test(multiplayer): ...`, `docs(multiplayer): ...`. A commit written
   with an AI agent ends with `(AI-assisted)`.
4. **Push to `main`.** Fetch first: the release workflow pushes its own commit to `main`
   (section 4), so a push can be refused until you rebase on it.
5. **Lint runs on its own** on every push (section 3). It only reports.

To bring the latest modding base into the mod, run `task modding-sync-branch -- --push`. The root
`README.md`, `index.json` and `docs/modding/current_mod/` (this folder) always keep the mod's
version; the shared guides and agent files take the base's.

## 3. The workflows

| Workflow | Runs | Does |
|---|---|---|
| `lint.yml` | On every push | Checks the sources in seconds (whitespace in `goal_src`, `og:preserve-this` lines kept, translation characters, no raw `assert()`). |
| `build.yml` | By hand, owner only | Builds `gk`, `goalc` and `extractor` for Windows and Linux, to check the build. Publishes nothing. |
| `release.yml` | By hand, owner only | Builds, packages and publishes a release, and updates the launcher catalog (section 4). |

The owner-only workflows refuse anyone else, whatever their access: a full build of both platforms
takes 30 to 60 minutes of CI.

## 4. Releasing a version

1. Make sure `main` holds what you want to ship and that it was played from a cold boot
   (`task boot-game-retail` for the Mods menu).
2. On GitHub: **Actions > Build & Release OpenGOAL Mod Package > Run workflow**, on `main`.
3. Fill in the inputs:

   | Input | Value for this mod |
   |---|---|
   | `mod_name` | `Jak 3 Multiplayer` |
   | `mod_description` | One sentence for the launcher (the current one is in `index.json`). |
   | `tag_name` | The new version, for example `v1.1.0`. |
   | `prerelease` | On for a test build. |

4. The workflow builds both platforms from clean sources and publishes the release
   `multiplayer-v1.1.0` with `windows-multiplayer-v1.1.0.zip`, `linux-multiplayer-v1.1.0.zip`,
   their checksums and `index.json`. It then commits the updated `index.json` to `main`
   (`chore(release): update index.json for multiplayer-v1.1.0`).
5. Players who added the catalog URL (`https://raw.githubusercontent.com/whozghiar/jak3-multiplayer/main/index.json`)
   see the new version in the launcher. The global catalog of the modding base lists it within a
   day.

> [!IMPORTANT]
> Players can only play together on the same network protocol (`kProtocolVersion` in
> `game/kernel/jak3/kmultiplayer.h`). When a release changes it, players of the previous release
> are refused with "Version mismatch: update the mod": say so in the release notes.

Add a row here with each release:

| Release | Date | Protocol |
|---|---|---|
| `multiplayer-v1.0.0` | 2026-10-14 | 4 |
| `multiplayer-v1.0.1` | 2026-10-16 | 5 |
