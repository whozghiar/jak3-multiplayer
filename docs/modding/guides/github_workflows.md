# GitHub Actions Workflows Guide

What each workflow in `.github/workflows/` does, when it runs and who may run it. It covers
`<owner>/jak-project` (the mother repository, the original or a fork of it) and the mod
repositories created from its `master-dev`, which inherit some of the same files. The
day-to-day workflow around them is in [`repository_workflow.md`](repository_workflow.md).

## Contents

1. [Architecture](#1-architecture)
2. [Access control](#2-access-control)
3. [Upstream sync (`sync-upstream.yaml`)](#3-upstream-sync-sync-upstreamyaml)
4. [Source lint (`lint.yml`)](#4-source-lint-lintyml)
5. [Build check (`build.yml`)](#5-build-check-buildyml)
6. [Release (`release.yml`)](#6-release-releaseyml)
7. [Tooling release (`release-tooling.yml`)](#7-tooling-release-release-toolingyml)
8. [Mod suggestion triage (`mod-suggestion-triage.yml`)](#8-mod-suggestion-triage-mod-suggestion-triageyml)
9. [Global catalog (`sync-global-catalog.yml`)](#9-global-catalog-sync-global-catalogyml)
10. [Quick reference](#10-quick-reference)

---

## 1. Architecture

```text
[open-goal/jak-project] master
        |  daily at 10:00 UTC: sync-upstream.yaml
        v
[<owner>/jak-project] master        clean upstream mirror
        |  merged by sync-upstream.yaml
        v
[<owner>/jak-project] master-dev    modding base: engine patches, tooling, Mods menu
        |  by hand: release-tooling.yml --> vX.Y.Z tooling release (launcher, other tools)
        |
        +--> mod repositories, <owner>/<game>-mod-<slug>  (task modding-new-mod)
                on demand: task modding-sync-all, or task modding-sync-branch -- --push
                on push: lint.yml; by hand: build.yml, release.yml

Most advanced release of each public mod repository
        --> sync-global-catalog.yml --> index.json on master-dev (one launcher URL for every mod)
```

Nothing merges into a mod automatically: `sync-upstream.yaml` stops at `master-dev`, and a mod
picks up `master-dev` only when someone syncs it. The old mod branches are archived as tags
`archive/<branch>`.

## 2. Access control

Workflows that push, publish or spend real CI time start with an explicit actor check (an
`authorize` job, or an early step) instead of relying only on GitHub's write-access rule:

- **Repository owner only:** `sync-upstream.yaml`, `build.yml`, `release.yml`,
  `release-tooling.yml`.
- **Owner, or GitHub's own automation** (`github-actions[bot]`): `sync-global-catalog.yml`.

An unauthorized run fails at once with an `::error::` naming who triggered it.

Open by design:

- `mod-suggestion-triage.yml` reacts to issues opened by players. Its blast radius is narrow:
  `issues: write` only, and the issue text is read inside `actions/github-script`, never
  interpolated into YAML, which avoids script injection.
- `lint.yml` runs on `push`, which already requires write access, and is read-only
  (`contents: read`).

**Mother repository guard.** The workflows that only make sense in the mother repository —
`sync-upstream.yaml`, `release-tooling.yml`, `sync-global-catalog.yml` and
`mod-suggestion-triage.yml` — carry
`if: endsWith(github.repository, '/jak-project')`: they run in the mother repository and in any
fork of it that keeps the name, never in a mod repository. A mod repository also drops those
files when it syncs, and keeps only `release.yml`, `lint.yml` and `build.yml`
(`scripts/modding/sync_common.ALLOWED_MOD_REPO_WORKFLOWS`).

## 3. Upstream sync (`sync-upstream.yaml`)

- **Runs:** daily at 10:00 UTC, or by hand (owner only).
- **Does:** fast-forwards `master` from `open-goal/jak-project`, then merges `master` into
  `master-dev`. In a fork made without `master`, it starts from upstream directly.
- **Keeps the fork's own CI:** upstream's workflows and issue templates are pruned during the
  merge. Only the workflows in its `allowed_workflows` list and the `mod-bug-report.yml`,
  `mod-suggestion.yml` and `config.yml` issue templates stay. Upstream's `AI_POLICY.md`,
  `.github/CONTRIBUTING.md` and `.github/PULL_REQUEST_TEMPLATE/` are pruned too.
- **New workflow:** add it to `allowed_workflows`, or the next sync deletes it.
- **Stops on a real code conflict:** the merge is aborted with a warning; `master-dev` is left
  untouched until someone merges by hand.
- Pushing upstream workflow updates to `master` needs a `GH_PAT` secret with `repo` and
  `workflow` scopes; without it that push fails with a warning.

## 4. Source lint (`lint.yml`)

- **Runs:** on every push, in the mother repository and in every mod repository, or by hand.
- **Does:** checks that finish in seconds and need no build:
  `scripts/ci/lint-trailing-whitespace.py` (no trailing whitespace in `goal_src`),
  `scripts/ci/check-for-asserts.py` (no raw `assert()` in the C++ engine),
  `scripts/ci/lint-autoglottonyms.py` and `scripts/ci/lint-characters.py` (translation files),
  and `scripts/ci/lint-gsrc-removals.py` (fails if a diff against upstream
  `open-goal/jak-project` `master` removes a line tagged `og:preserve-this` from `goal_src`).
- Read-only: it reports, it never fixes or commits.

## 5. Build check (`build.yml`)

- **Runs:** by hand only, owner only, from the mother repository or a mod repository.
- **Does:** compiles `gk`, `goalc` and `extractor` for Windows (Clang-CL, static) and Linux
  (Clang, static), the same targets as `release.yml`, and uploads the binaries as 3-day build
  artifacts. No packaging, no release.
- **Manual on purpose:** a Release build of both platforms takes 30 to 60 minutes. Locally,
  `task compile-check` verifies the GOAL code in about a minute.

## 6. Release (`release.yml`)

- **Runs:** by hand only, owner only, from a mod repository, with the inputs `mod_name`,
  `mod_description`, `tag_name` (e.g. `v1.0.0`) and `prerelease`.
- **Does:**
  - builds Windows and Linux binaries from clean source with static libraries, so players need
    no redistributable;
  - packages `gk`, `goalc`, `extractor` and `data/`, plus any texture pack found in
    `docs/modding/current_mod/texture_packs/`;
  - writes `SHA256SUMS.txt`, updates the mod's own `index.json` and commits it back;
  - tags the release `<slug>-vX.Y.Z`. In a mod repository, the slug and game come from its
    `index.json` (else from its `<game>-mod-<slug>` name), so the launcher keeps the same
    catalog key.
- **Catalog:** the global catalog lists the release at its next daily run (section 9).
- **Permissions:** `contents: read` by default; only the `publish` job gets `contents: write`.

## 7. Tooling release (`release-tooling.yml`)

- **Runs:** by hand only, owner only, mother repository only, usually from `master-dev`, with
  the inputs `version` (e.g. `0.3.9`, no leading `v`) and `prerelease`. It refuses a version
  whose tag `vX.Y.Z` already exists.
- **Does:** builds `gk`, `goalc`, `extractor` and `lsp` for Windows (Clang-CL, static) and
  Linux (Clang, static), packages them with `.github/scripts/releases/extract_build_*.sh`, and
  publishes the release `vX.Y.Z`. The tag is created only once both builds succeed, and each
  build tags its own checkout first, so the binaries report `vX.Y.Z` as their version.
- **Assets:** the names and archive layout of `open-goal/jak-project` releases, so the OpenGOAL
  Launcher and other tools can point at this repository instead of upstream:

  | Asset | Content |
  | :--- | :--- |
  | `opengoal-windows-vX.Y.Z.zip` | `gk.exe`, `goalc.exe`, `extractor.exe`, `data/` |
  | `opengoal-linux-vX.Y.Z.tar.gz` | `gk`, `goalc`, `extractor`, `data/` |
  | `opengoal-lsp-windows-vX.Y.Z.exe` | OpenGOAL language server |
  | `opengoal-lsp-linux-vX.Y.Z.bin` | OpenGOAL language server |

- **Not built:** macOS. Upstream also runs the unit tests; this workflow only builds.
- **Catalog:** never listed, since the global catalog ignores the mother repository's releases.

## 8. Mod suggestion triage (`mod-suggestion-triage.yml`)

- **Runs:** when an issue made with the "Mod Suggestion" form (`mod-suggestion.yml`) is opened
  or edited. Mother repository only: suggestions for new mods are filed here.
- **Does:** labels the suggestion by game (`jak1`, `jak2`, `jak3`, `jakx`), category
  (`type:gameplay`, `type:entities`, `type:textures`, `type:audio`, `type:levels`, `type:qol`)
  and status (`enhancement`, `mod-suggestion`, `needs-triage`).

Bug reports go to each mod's own repository, through the generic `mod-bug-report.yml` form it
inherits.

## 9. Global catalog (`sync-global-catalog.yml`)

- **Runs:** daily at 10:30 UTC, or by hand from the Actions tab to list a new release at once.
  Owner or GitHub's automation only.
- **Does:** runs `scripts/modding/sync_global_catalog.py` on `master-dev`:
  1. lists the public mod repositories (same owner, `opengoal-mod` topic);
  2. picks the most advanced release of each one: the highest version in its tag
     (`<slug>-vX.Y.Z`, where `1.1.0-rc1` ranks below `1.1.0`). Drafts are skipped, a
     pre-release counts like any other release, and a release without an `index.json` asset is
     passed over for the next one;
  3. takes that release's `index.json` as the mod's whole entry (name, description, cover, and
     every version, which `release.yml` accumulates release after release), sends players to the
     repository, and keeps the texture packs whose archive the release carries;
  4. writes the root `index.json` in the OpenGOAL Launcher mod-source v1 schema and pushes it to
     `master-dev`, only when something changed. When GitHub cannot be reached, it fails and
     leaves the catalog as it was.
- **Never read:** branches, the `index.json` committed in a repository, private repositories,
  and the mother repository's own releases. A mod with no release in its own repository is not
  listed.
- **Why:** players add one URL to the launcher,
  `https://raw.githubusercontent.com/<owner>/jak-project/master-dev/index.json`, and get every
  published mod and texture pack.

## 10. Quick reference

| Workflow | Trigger | Who | Runs in mod repositories | Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `sync-upstream.yaml` | Daily 10:00 UTC, dispatch | Owner | No | Upstream into `master`, then `master-dev` |
| `lint.yml` | Push, dispatch | Write access | Yes | Fast source checks |
| `build.yml` | Dispatch | Owner | Yes | Windows and Linux compile check, 3-day artifacts |
| `release.yml` | Dispatch | Owner | Yes | Builds, packages and publishes a release, updates the mod's `index.json` |
| `release-tooling.yml` | Dispatch | Owner | No | Builds and publishes the tooling release `vX.Y.Z` in upstream's format |
| `mod-suggestion-triage.yml` | Issues opened or edited | Anyone | No | Labels mod suggestions |
| `sync-global-catalog.yml` | Daily 10:30 UTC, dispatch | Owner or automation | No | Rebuilds the global `index.json` on `master-dev` from each mod repository's latest release |
