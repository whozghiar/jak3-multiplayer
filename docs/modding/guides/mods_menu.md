# Unified In-Game "Mods" Menu

> [!IMPORTANT]
> Every mod built from this repository must register into this menu.
> Mod features must ship off by default and be toggleable by players at
> runtime in retail boots via **L3 + SELECT**. The exact registration call
> is in [the Lisp wiki](../../../.agents/skills/goal-lisp/wiki/common.md#1211-register-an-in-game-mods-toggle)
> (see also [§3](#3-the-standardised-template)) — this file covers the
> architecture and the pitfalls, not the syntax.

The `mods-menu.gc` registry is live on `master-dev` for Jak 1, Jak 2 and
Jak 3, with an identical public API in all three. It is not part of the debug menu: it
opens in a normal launcher boot with L3 + SELECT.

| Game | Registry file | Wired in | Opens with |
|---|---|---|---|
| Jak 2 | `goal_src/jak2/pc/features/mods-menu.gc` | `goal_src/jak2/dgos/game.gd` | L3 + SELECT |
| Jak 3 | `goal_src/jak3/pc/features/mods-menu.gc` | `goal_src/jak3/dgos/game.gd` | L3 + SELECT |
| Jak 1 | `goal_src/jak1/pc/features/mods-menu.gc` | `goal_src/jak1/dgos/game.gd` | L3 + SELECT |

## Contents

- [1. Why the debug menu could never work for a shipped mod](#1-why-the-debug-menu-could-never-work-for-a-shipped-mod)
- [2. Solution: one popup menu + a runtime registry](#2-solution--one-popup-menu--a-runtime-registry)
- [3. The standardised template](#3-the-standardised-template)
- [4. Wiring the template into a build](#4-wiring-the-template-into-a-build)
- [5. Pitfalls](#5-pitfalls)
- [6. Jak 2 vs Jak 3 — the one real difference](#6-jak-2-vs-jak-3--the-one-real-difference)
- [7. Jak 1 differences](#7-jak-1-differences)
- [8. Migrating a branch from the old debug registry](#8-migrating-a-branch-from-the-old-debug-registry)

---

## 1. Why the debug menu could never work for a shipped mod

The OpenGOAL launcher starts the game with `-boot -fakeiso`. In
`game/kernel/jak2/kmachine.cpp`, `-boot` sets `MasterDebug = 0` and
`DebugSegment = 0`. That has three independent, each individually fatal,
consequences:

| Consequence | Where | Effect on a debug-menu mod toggle |
|---|---|---|
| DEBUG segments are not linked | `klink.cpp` skips a segment when `!DebugSegment` | A file marked as a debug-only segment does not exist at runtime |
| The debug heap is set to NULL | `kmachine.cpp` | The whole debug-menu framework, which allocates on the debug heap, has nowhere to allocate |
| The open gesture is gated | `main.gc` | L3+SELECT does nothing in retail unless the debug segment flag is set |

So a player who installs a mod from the launcher and starts it normally
could never reach a toggle that lived under a debug submenu. Asking them to
enable a "debug mode" in the launcher is not a fix — it changes how the
whole game boots and is not something a mod can rely on.

The useful side effect of the third row: because retail boots make
L3+SELECT inert, that gesture is guaranteed free, which is why the Mods
menu claims it.

## 2. Solution — one popup menu + a runtime registry

`goal_src/jak[2|3]/pc/features/mods-menu.gc` (registered in the matching
`goal_src/jak[2|3]/dgos/game.gd`, right after `speedruns.o`) builds on
`popup-menu` (`pc/util/popup-menu.gc`) — plain non-debug code that already
ships in `GAME.CGO` and is used by speedrunner mode. It installs a single
menu and a small registry:

```
[L3 + SELECT] -> Mods -> [mod-slug] -> [variant / sub-module] -> [options & toggles]
```

A mod never edits `mods-menu.gc` directly — it registers its own submenu
from one of its own already-compiled files. Entries are sorted
alphabetically by slug on every rebuild, so registration order never
changes what the player sees.

### Controls

| Button | Action |
|---|---|
| L3 + SELECT | open / close |
| Up / Down | move by 1 |
| Left / Right | move by 5 |
| X | confirm / enter a submenu |
| Circle | back (exits the menu at the root) |
| Select | close from anywhere |

The bind is configurable at load time by a mod that needs a different
combination — avoid the combinations reserved for speedrunner mode.

### Public API

Registering a mod's submenu and forcing a rebuild are both single calls —
see the [Lisp wiki, "Register an in-game Mods toggle"](../../../.agents/skills/goal-lisp/wiki/common.md#1211-register-an-in-game-mods-toggle),
for the exact signatures. A builder function takes no argument and returns a menu entry
(normally a submenu); it may return a false value to hide the mod
temporarily, e.g. when its level isn't loaded.

### Entry types available to a builder

| Type | Use |
|---|---|
| `popup-menu-button` | plain action |
| `popup-menu-flag` | toggle with a checkmark |
| `popup-menu-submenu` | nested page |
| `popup-menu-dynamic-submenu` | list computed at draw time |

Every entry also accepts a disabled-row predicate (`entry-disabled?`): while it answers `#t`, the
row is drawn grey (a flag without its check mark), the cursor skips it, and X on it does nothing.

In Jak 3, the root menu always ends with **Misc options**, the options of the whole game rather
than of one mod. It holds **Weather**: the game's changing weather (the default, nothing forced) or
a fixed one (sunny, cloudy, foggy, light rain, rain, thunderstorm, snow), held in every level until
another is picked. It is not saved: each boot starts on the changing weather.

## 3. The standardised template

Copy `docs/modding/templates/mod_menu.template.gc` into one of your mod's
own `.gc` files. It is a working example covering a master toggle, a nested
variant, a disabled-row condition, and an action button — the file itself
is the reference for the exact syntax, since it's a code template, not
documentation.

### Naming rules (what prevents collisions)

| Thing | Pattern |
|---|---|
| config var | prefixed with the mod slug |
| helper function | prefixed with the mod slug |
| builder function | named `mod-<slug>-build-menu` |
| registry label | the mod slug: the launcher catalog key, which is the repository name without its `<game>-mod-` prefix |

## 4. Wiring the template into a build

1. Put the menu code in a `.gc` file your mod already owns, or add a
   dedicated `<slug>-menu.gc`.
2. Register that `.o` in the right `.gd`, after `mods-menu.o`.
3. Do not mark that file as debug-only.
4. Rebuild and check it in a *retail* boot, not a debug one:
   `task boot-game-retail`.

## 5. Pitfalls

These are all real failures hit while building this system — each one
compiles or boots "fine" and then silently does the wrong thing.

- **Marking your mod's menu file as debug-only.** The file is not linked in
  a retail boot and your registration never runs. No error, no menu row.
- **Allocating debug-heap memory anywhere the menu reaches.** The debug
  heap is NULL in retail, so an allocation there silently falls back to the
  global heap and never frees. A per-frame allocation in a draw path is a
  permanent leak.
- **Spawning a process and sending it an event in the same frame.** A newly
  spawned process has no active state until the kernel dispatches it on the
  next frame — an event sent immediately is silently dropped. This is why
  spawning and opening the menu are deliberately two separate steps that
  run at different points in the frame.
- **Referencing a globally-defined symbol from inside a fully-static menu
  definition.** Both the entries array of a submenu and its function fields
  must be written inline in the same expression — a named helper function
  referenced by symbol fails to compile. Wrap a named helper in a one-line
  anonymous function instead.
- **An empty root menu.** The menu's navigation code indexes its entries
  array without a length guard, so a zero-length menu reads out of bounds —
  the registry always keeps at least one row for this reason.
- **Two popup menus at once.** The "a popup is open" flag is a single
  global shared with speedrunner mode — opening the Mods menu while another
  popup owns the screen is refused.
- **Letting the popup close itself without restoring the game's master
  mode.** On Jak 2 the popup's own close path does not restore the master
  mode by itself, so the game would stay frozen — the menu's per-frame
  update watches for this and hands control back. Jak 3's own handler does
  this itself, which is why the two games' files differ slightly here.
- **A toggle-state check that does work.** It runs every frame the menu is
  on screen — keep it a pure read with no side effects.

## 6. Jak 2 vs Jak 3 — the one real difference

`popup-menu` is not identical in the two games, so `mods-menu.gc` is not
either:

| | Jak 2 | Jak 3 |
|---|---|---|
| Who ticks the menu | nobody — the mods-menu update loop must drive it | the popup's own state, which also clears itself from the process's active mask |
| Who restores the master mode | `mods-menu.gc` | the popup's own event handler |
| Spawn convention | explicit init function passed to spawn | the popup's own by-other init |

Everything above the line — the registry, the API, the template, the bind —
is identical.

## 7. Jak 1 differences

Jak 1 had no `popup-menu`, so `goal_src/jak1/pc/util/popup-menu*.gc` is a
smaller port of Jak 2's. The registry, the template and the bind are the same;
what differs:

| | Jak 1 | Jak 2 / Jak 3 |
|---|---|---|
| The menu | a plain object ticked from `main.gc` | a process (spawned on the first frame) |
| Labels | drawn as is in the debug menu's small font: ASCII only | UTF-8 through the PC text encoder |
| Flag rows | `[x]` / `[ ]` prefix | green check mark |
| `popup-menu-dynamic-submenu` | not ported | available |

The old blocker, Jak 1's debug root menu crashing when an item is appended
at link time (see the [Lisp wiki](../../../.agents/skills/goal-lisp/wiki/jak1.md#25-the-debug-root-menu-is-fragile-at-link-time)),
does not apply: the Mods menu never touches the debug menu.

## 8. Migrating a branch from the old debug registry

The public registration function's name is unchanged; what changed is the
builder's signature and the node type it returns.

| Before (debug) | After (retail) |
|---|---|
| file marked debug-only | remove the marker |
| builder took a debug-menu context argument | builder takes no argument |
| returned a debug-menu node built from a template | returns a popup-menu entry, normally a static submenu |
| an s-expression menu template | a nested static popup-menu-submenu expression |
| a pick-function returning the flag | a toggle-check lambda plus a confirm lambda |
| registration call | unchanged |
