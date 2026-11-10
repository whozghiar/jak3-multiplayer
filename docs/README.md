# OpenGOAL Documentation Hub

Welcome to the documentation tree for the OpenGOAL project and modding framework (`jak-project`).

> ### Summary
>
> [1. Structure Overview](#1-structure-overview) · [2. Key Entry Points](#2-key-entry-points) · [3. Environment & Toolchain](#3-environment--toolchain-setup) · [4. Engine Architecture & Historical Notes](#4-engine-architecture--historical-notes)

---

## 1. Structure Overview

```text
docs/
├── modding/                # Complete modding documentation, verified references & tool guides
├── setup/                  # Environment, toolchain & IDE setup (VSCode, VS, Linux, Windows, macOS)
├── progress-notes/         # Upstream decompilation logs, assembly notes & reverse-engineering records
├── scratch/                # Upstream developer scratchpad and temporary notes
└── project-overview.md     # Architecture overview of OpenGOAL compiler, runtime & decompiler
```

---

## 2. Key Entry Points

### Modding & AI-Assisted Development
- **[Lisp Wiki (`.agents/skills/goal-lisp/wiki/`)](../.agents/skills/goal-lisp/wiki/index.md)**: verified Lisp instructions in the knowledge-base submodule — patterns and engine model shared by the three games in `common.md`, each game's specifics in `jak1.md`, `jak2.md` and `jak3.md`, and background pages on GOAL's concepts, the engine and build, the REPL, custom assets and textures.
- **[How to Create a Mod](modding/guides/how_to_create_a_mod.md)**: the step-by-step procedure, from your own fork and the first build to the release, with the resources to use, the AI-agent workflow and how to update the knowledge base.
- **[Repository Workflow Guide](modding/guides/repository_workflow.md)**: how the mother repository, the mod repositories and the knowledge base fit together, what switching between them in one working directory implies, and the day-to-day commands (switch, sync, release, record knowledge).
- **[AI Agent & Developer Guide (`AGENTS.md`)](../AGENTS.md)**: what every AI agent follows: compile but never launch the game, golden rules for mods, knowledge base, language and documentation standards, commands and Git.
- **[GitHub Actions Workflows Guide](modding/guides/github_workflows.md)**: detailed pedagogical guide to repository CI/CD, upstream synchronization, releases, and issue triaging.
- **[Task Commands & Modding Scripts Reference](modding/guides/task_scripts_reference.md)**: pedagogical reference for all Taskfile commands and `scripts/modding/*.py` automations.
- **[Giving a Mod Its Textures](modding/guides/texture_packs.md)**: textures inside the mod, or a texture pack published next to it.
- **[Modular Skills (`.agents/skills/`)](../.agents/skills/)**: high-density engineering skills loaded on demand:
  - [`smoke-tests`](../.agents/skills/smoke-tests/SKILL.md): in-game smoke tests and each mod's test notebook.
  - [`documentalist`](../.agents/skills/documentalist/SKILL.md): documentation standards enforcement.
  - [`kb`](../.agents/skills/kb/SKILL.md): recording a verified discovery in the knowledge base.

---

## 3. Environment & Toolchain Setup

- **[Setup Guides (`docs/setup/`)](setup/)**:
  - **Operating Systems:** [Windows](setup/system/windows.md) · [Linux](setup/system/linux.md) · [macOS](setup/system/macos.md) · [Docker](setup/system/docker.md)
  - **IDEs & Editors:** [VSCode](setup/dev/vscode.md) · [Visual Studio](setup/dev/vs.md) · [Zed](setup/dev/zed.md)

---

## 4. Engine Architecture & Historical Notes

- **[Project Overview (`docs/project-overview.md`)](project-overview.md)**: Upstream architecture and component roles.
- **[Progress Notes (`docs/progress-notes/`)](progress-notes/)**: Decompilation notes, bone systems, joint decompression, and VU assembly documentation.
