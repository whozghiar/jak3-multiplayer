# OpenGOAL Mod Distribution & Release Guide

> - **Applies to:** Jak 1 / Jak 2 / Jak 3 (OpenGOAL PC Port) — every mod repository
> - **Origin:** `master-dev`
> - **Related Guide:** [`github_workflows.md`](github_workflows.md)

> ### Summary
>
> [1. C++ Pipeline Support](#1-why-this-pipeline-handles-c-changes-goalc-extractor-gk) · [2. Archive Layout](#2-mandatory-mod-archive-layout) · [3. CI/CD Release (`release.yml`)](#3-triggering-a-release-from-a-mod-repository) · [4. Launcher Catalog (`index.json`)](#4-opengoal-launcher-integration-indexjson) · [Cover Thumbnail](#mod-cover-thumbnail-mod_coverpng)

---

This document explains the architecture and mechanics of the automated release and packaging pipeline that lets an OpenGOAL mod be installed and played directly by any player through the **official OpenGOAL Launcher**.

---

## 1. Why this pipeline handles C++ changes (`goalc`, `extractor`, `gk`)

Unlike conventional mods that only touch LISP script files (`goal_src/`), some mods in this repository alter the native C++ tools and engine:
- **Decompiler / Extractor (`decompiler/`):** Custom asset extraction, glTF/FR3 3D model injection, collision conversions.
- **Compiler (`goalc/`):** New behaviors, custom-actor compiler support, x86-64 assembly optimizations.
- **Game runtime (`gk`):** OpenGL graphics engine (shaders), EE/IOP simulation, audio hooks and extended memory.

### Execution mechanism in the OpenGOAL Launcher

When a player installs a mod from the Launcher:
1. The Launcher downloads the matching archive (`windows-v*.zip` or `linux-v*.zip`) and extracts it into its internal folder:
   ```text
   %APPDATA%/OpenGOAL-Launcher/features/<jak1|jak2|jak3>/mods/<source_name>/<mod_name>/
   ```
2. **Absolute priority to the mod's own executables:** The Launcher does **not** run the vanilla OpenGOAL build. It runs the binaries sitting directly at the root of the extracted mod folder:
   - `extractor` to decompress the player's clean ISO, decompile the needed assets, and run the local LISP compile with your `goalc`.
   - `gk` to launch the game natively.
3. **Fully static linking:** Thanks to the official CMake presets `Release-windows-clang-static` and `Release-linux-clang-static`, every C++ runtime and third-party library (`SDL3`, `zlib`, `lzokay`, `OpenSSL`) is merged into the binaries. The player needs no extra Visual C++ runtime or system library installed.

---

## 2. Mandatory Mod Archive Layout

For the Launcher to hit zero extraction or local-compile errors, the ZIP archive must contain exactly:

```text
├── extractor.exe (or extractor on Linux)
├── gk.exe        (or gk on Linux)
├── goalc.exe     (or goalc on Linux)
└── data/
    ├── launcher/
    │   └── error-code-metadata.json
    ├── decompiler/
    │   └── config/                       # Decompilation rules for NTSC/PAL ISOs
    ├── goal_src/                         # Full LISP source compiled by goalc
    ├── game/
    │   ├── assets/                       # Text, font and sound metadata
    │   └── graphics/
    │       └── opengl_renderer/
    │           └── shaders/              # OpenGL shaders required for gk to boot
    └── custom_assets/                    # Mod's 3D models, textures and assets (if any)
```

> [!CAUTION]
> **OpenGL Shaders Trap:** If `data/game/graphics/opengl_renderer/shaders/` is missing from the archive, the extractor and compiler will *look* like they work, but clicking "Play" will crash `gk` immediately on a GLSL compile error.

---

## 3. Triggering a Release from a Mod Repository

The `.github/workflows/release.yml` workflow lives on `master-dev`, so every mod repository inherits it and gets its updates with `task modding-sync-branch -- --push`. In a mod repository the mod's catalog key and game come from its own `index.json`, so the release tag is `<slug>-vX.Y.Z` and the launcher keeps seeing the same mod.

> [!IMPORTANT]
> **`workflow_dispatch` only — no tag trigger.** This workflow does not listen for tag
> pushes (`git push origin v1.0.0` triggers **nothing**): it only ever runs manually,
> from the **Actions** tab or via `gh workflow run`. This is a deliberate choice — a
> release is an explicit action, never a side effect of a `git push`.
>
> **Always a full rebuild.** Every release recompiles Windows and Linux from source.
> `tag_name` is mandatory: you decide the version number that ships to players. Left
> empty, `mod_name` and `mod_description` keep the previous release's (read from the mod's
> `index.json`; the README's overview for a first release without a description).
>
> **Repository owner only.** The workflow checks `github.actor` against the
> repository owner before doing anything else — write access to push is
> not enough to trigger a release. See
> [GitHub Actions Workflows Guide § Access Control](github_workflows.md#2-access-control).

### Triggering it — via GitHub Actions or the `gh` CLI

**Web UI:**
1. Open the mod repository on GitHub and go to the **Actions** tab.
2. In the left menu, select **🚀 Build & Release OpenGOAL Mod Package**.
3. Click **Run workflow**:
   - **Branch:** `main`.
   - **Mod name (`mod_name`):** Display name (e.g. `Jak 3 JetBoard`). Empty: the previous release's.
   - **Short description (`mod_description`):** A 1-2 sentence summary embedded as-is into the `index.json` catalog. Empty: the previous release's.
   - **Version tag (`tag_name`):** Mandatory. An explicit tag (e.g. `v1.0.0`).
   - **Changelog (`changelog`):** Optional. The path of a Markdown file committed in the repository (e.g. `docs/changelog/v1.1.0.md`). Its text goes under a *Changelog* heading in the release notes, and the file is attached to the release as `CHANGELOG-<tag>.md`. A wrong path stops the release before anything is published.
   - **Mark as pre-release:** Check if the version is experimental.
4. Click **Run workflow**.

**Command line:**
```bash
gh workflow run release.yml --repo <owner>/jak2-mod-my-mod --ref main \
  -f mod_name="Jak 3 JetBoard" \
  -f mod_description="Adds Jak 3's jetboard to Jak 2." \
  -f tag_name="v1.0.0"

# a later release: same name and description, with a changelog
gh workflow run release.yml --repo <owner>/jak2-mod-my-mod --ref main \
  -f tag_name="v1.1.0" -f changelog="docs/changelog/v1.1.0.md"
```

---

## 4. OpenGOAL Launcher Integration (`index.json`)

The `scripts/modding/update_mod_catalog.py` script automatically generates an `index.json` file compliant with the official OpenGOAL Mod Source Schema v1:

```json
{
  "schemaVersion": "1.0.0",
  "sourceName": "Jak II - My Mod Source",
  "lastUpdated": "2026-09-13T15:00:00Z",
  "mods": {
    "my-mod": {
      "displayName": "Jak II - My Mod",
      "description": "Mod description...",
      "authors": ["MyHandle"],
      "tags": ["gameplay", "custom-engine"],
      "supportedGames": ["jak2"],
      "websiteUrl": "https://github.com/user/jak2-mod-my-mod",
      "coverArtUrl": "https://raw.githubusercontent.com/user/jak2-mod-my-mod/main/docs/img/mod/mod_cover.png",
      "thumbnailArtUrl": "https://raw.githubusercontent.com/user/jak2-mod-my-mod/main/docs/img/mod/mod_cover.png",
      "versions": [
        {
          "version": "1.0.0",
          "publishedDate": "2026-09-13T15:00:00Z",
          "supportedGames": ["jak2"],
          "assets": {
            "windows": "https://github.com/user/jak2-mod-my-mod/releases/download/my-mod-v1.0.0/windows-my-mod-v1.0.0.zip",
            "linux": "https://github.com/user/jak2-mod-my-mod/releases/download/my-mod-v1.0.0/linux-my-mod-v1.0.0.zip"
          },
          "checksums": {
            "windows": "a1b2c3d4...",
            "linux": "e5f6g7h8..."
          }
        }
      ]
    }
  },
  "texturePacks": {}
}
```

### Mod Cover Thumbnail (`mod_cover.png`)

For each mod, you can manually drop a cover thumbnail at `docs/img/mod/mod_cover.png` in the mod repository. It is automatically detected and injected into `index.json` (`coverArtUrl` and `thumbnailArtUrl`) for visual display in the OpenGOAL Launcher, as well as in GitHub release notes.

> [!NOTE]
> **Automated Master Catalog Maintenance:**
> The consolidated root catalog is rebuilt daily by the `sync-global-catalog.yml` workflow (`scripts/modding/sync_global_catalog.py`, `task modding-sync-catalog`) from the mod repositories alone, which it finds by their `opengoal-mod` topic: for each one, the `index.json` attached to its most advanced release.

> For a full screenshot-by-screenshot walkthrough (including in-game activation), see ["Installing a Mod (Players)"](../../../README.md#installing-a-mod-players) in the root README.
