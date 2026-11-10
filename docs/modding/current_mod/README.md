# Mod Technical Deep-Dive Documentation (`docs/modding/current_mod/`)

This directory is the dedicated space for Tier 2 technical documentation for
active mod features and subsystems.

## Contents

- [1. Purpose & philosophy](#1-purpose--philosophy)
- [2. Naming convention](#2-naming-convention)
- [3. Recommended structure](#3-recommended-structure-for-mod-technical-readmes)
- [4. Texture packs for releases](#4-texture-packs-for-releases-texture_packs)

---

## 1. Purpose & philosophy

While the mod's root `README.md` is player/user-facing (overview, features,
how to compile, game controls, video demonstration), files in this
directory provide pedagogical, in-depth engineering documentation for
developers, AI agents, and future maintainers.

## 2. Naming convention

```
docs/modding/current_mod/<mod_slug>_readme.md
```

(e.g. `docs/modding/current_mod/my-mod_readme.md`)

## 3. Recommended structure for mod technical readmes

Each technical mod document should adopt a structured, educational approach:

1. **Architecture & subsystems impacted:** affected layers (C++ runtime,
   decompiler, compiler, GOAL game code, asset pipelines), core concepts
   and design decisions.
2. **Pedagogical walkthrough & data pipelines:** step-by-step breakdown of
   how data flows (e.g. glTF to `build-actor` to art-group to the Merc2
   renderer), skeletons, joints, animation mapping, or audio bank
   ingestion.
3. **What changed and why, described in prose.** Reference the exact GOAL
   syntax involved (a state, a hook, a macro) by name, and link to its
   entry in the Lisp wiki
   (`.agents/skills/goal-lisp/wiki/`) rather than pasting the
   Lisp code into this document. GOAL/Lisp code examples live only in the
   wiki, so that the same pattern is never explained twice in two places
   that can drift apart.
4. **State machine & logic diagrams:** process states, transition
   triggers, and conditions, described narratively or with a diagram — not
   as inlined code.
5. **Memory, heap & performance footprint:** which heap the mod allocates
   on and why, alignment requirements, and budget impact.
6. **Debugging & troubleshooting guide:** common traps, known edge cases,
   and REPL verification steps taken while building the feature.

No change log here: the author writes it by hand at release time (the release
workflow's `changelog` input).

## 4. Texture packs for releases (`texture_packs/`)

Any mod that provides a custom texture pack can store its packaged
archive(s) inside:

```
docs/modding/current_mod/texture_packs/<slug>-v<version>.zip
```

Why this directory:

- **Committed on purpose:** the folder's own `.gitignore` ignores `*.zip`, so
  a work-in-progress pack is never committed by accident. Commit the pack you
  ship with `git add -f docs/modding/current_mod/texture_packs/<file>.zip`:
  the release workflow runs on a fresh checkout and only finds committed
  archives.
- **Branch-sync protected:** classified as "ours" in
  `scripts/modding/sync_common.py`, so it is never wiped or overwritten
  when merging `master-dev`.
- **Automatic release asset upload:** the GitHub Actions release workflow
  automatically collects every `.zip` archive from this directory, computes
  its SHA-256 hash, registers it under `"texturePacks"` in `index.json`,
  and attaches it to the published GitHub Release.

The OpenGOAL Launcher does not link a texture pack to a mod: its mod-source schema has no such field, it applies texture packs to the base game only, from a `.zip` the player adds in its Texture Packs screen, and its texture support for installed mods is not finished (checked on its `main` branch, 2026-10-02). Textures a mod needs belong in its own
`custom_assets/<game>/texture_replacements/`, committed with `git add -f`.
