# Multiplayer — Jak 3

<p align="center">
  <img src="https://img.shields.io/badge/OpenGOAL-Mod-blue.svg" alt="OpenGOAL Mod">
  <img src="https://img.shields.io/badge/Game-Jak%203-orange.svg" alt="Target Game">
  <img src="https://img.shields.io/badge/AI--assisted-Modding-purple.svg" alt="AI Assisted">
</p>

> **Contents:** [Overview](#overview) · [Key Features](#key-features) · [How to Play](#how-to-play) · [Download & Play](#download--play-via-opengoal-launcher-players) · [Developer Setup](#developer-setup--local-compilation) · [Demo Video](#demonstration-video) · [Technical Documentation](#technical-documentation)

---

## Overview
Jak 3 online: host or join a server from the pause menu and explore the world together as Jak or Daxter.

- **Target Game:** Jak 3
- **Repository:** [`whozghiar/jak3-multiplayer`](https://github.com/whozghiar/jak3-multiplayer), created from the modding base [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project)

## Key Features
- **Host or join from the start menu:** START > Options > Multiplayer. Type the server address
  (`ip:port`) and your player name with the D-pad, then host or join. Up to 8 players.
- **Play as Jak or Daxter:** Daxter is the game's own playable Daxter, available everywhere.
- **Your color:** ten colors, shown as a small square before your name and as your dot on the
  minimap.
- **See each other:** every player appears with their name and health bar over their head, as a
  dot on the minimap, with Daxter on their shoulder, their gun, their shots and the vehicle they
  drive, in their own outfit, moving exactly as they move on their own screen, with their effects
  and sounds.
- **One shared world:** the host's citizens, guards, enemies and traffic are the same for everyone,
  with their movements, effects and sounds. A player who goes far from the others keeps a living
  world around them, which the players near them share. Enemies killed, crates broken and pickups
  taken disappear for everybody.
- **Share vehicles:** next to another player's vehicle, press triangle to steal it, or hold
  triangle to ride next to them when it has two seats. Guests can also take the host's traffic
  vehicles.
- **Enemies fight everyone:** enemies can target any player, and the hits they land count.
- **Players can hit each other:** punches and shots between players hurt and knock back (the host
  can turn it off).
- **Cheats menu:** in the Mods menu (L3 + SELECT): refill ammo, restore health, dark eco and
  light eco, invincibility, teleport to a zone or to a player.
- **Shared story:** a mission completed by one player is completed for everybody, with its
  reward; a player who joins catches up with the others; cutscenes play for the players together
  (the host can turn it off).
- **Same world:** doors open for whoever stands at them, elevators move for everyone, moving and
  floating platforms are in the same place on every screen, and they feel every player's weight.
- **Administration for the host:** kick or ban a player, teleport players to you, allow or forbid
  cheats for guests and players hitting each other, share story progress and your time of day,
  stop the server.

## How to Play
1. Open the Mods menu with **L3 + SELECT**, go to **multiplayer** and turn on **Enable
   multiplayer**. The setting is remembered.
2. Press **START**, open **Options > Multiplayer**.
3. Set **Player name**, **Character**, **Color** and **Server address**. To edit a text field:
   confirm, then up / down to change a letter, left / right to move, square to delete, confirm
   to save (triangle cancels).
4. The host picks **Host a server**; the others enter the host's address and pick **Join the
   server**.

| Action | Control |
|---|---|
| Mods menu (cheats, teleports) | L3 + SELECT |
| Steal a player's vehicle | Triangle next to it |
| Ride as passenger | Hold triangle next to it |
| Get off as passenger | Triangle |

> [!NOTE]
> **Playing over the Internet:** the host must open (forward) TCP port 7777, or the port they
> chose, on their router, and give the others their public IP address. On a local network, use the
> host's local IP (for example `192.168.1.20:7777`). Every player must have the same version of the
> mod.

Each player keeps their own save, health and items. Story progress is shared: missions completed
by anyone are completed for all.

## Download & Play via OpenGOAL Launcher (Players)

> [!TIP]
> **No developer environment required!** Players can install and play this mod directly using the official OpenGOAL Launcher:

### Option A — Add Custom Mod Source (Recommended)
1. In the **OpenGOAL Launcher**, navigate to **Settings ▸ Mods ▸ Add Custom Mod Source**.
2. Paste this catalog URL:
   ```text
   https://raw.githubusercontent.com/whozghiar/jak3-multiplayer/main/index.json
   ```
3. Go to the **Mods** tab, locate **Multiplayer**, and click **Install**.
4. Select your clean PS2 game ISO when prompted. The launcher will automatically extract assets and launch the game!

### Option B — Manual Installation from GitHub Releases
1. Download the pre-built package for your operating system from the [Releases](https://github.com/whozghiar/jak3-multiplayer/releases) tab (`windows-v*.zip` or `linux-v*.zip`).
2. Extract the archive into your OpenGOAL Launcher features directory:
   - **Windows:** `%APPDATA%\OpenGOAL-Launcher\features\jak3\mods\_local\multiplayer\`
   - **Linux:** `~/.config/OpenGOAL-Launcher/features/jak3/mods/_local/multiplayer/`
3. Launch the game from the OpenGOAL Launcher.

---

## Developer Setup & Local Compilation

If you want to modify or compile this mod locally from source:

### 0. Get the Source
Already working in a clone of [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project)? Switch to this mod there, keeping your extracted game data:
```bash
task modding-switch -- jak3-multiplayer
```
Otherwise clone it on its own; the knowledge base used by AI agents is a submodule, so clone with it:
```bash
git clone --recurse-submodules https://github.com/whozghiar/jak3-multiplayer.git
```
To pull the latest modding base into this mod later, run `task modding-sync-branch`.

### 1. Select the Active Game
Make sure your environment is targeting Jak 3:
```bash
task set-game-jak3
```

### 2. Binary Compilation
- **Status:** `task build-release-game` (the runtime `gk` changed).
- **Details:** the network layer lives in the game runtime (`game/kernel/jak3/kmultiplayer.cpp`).
```bash
# GOAL-only mod: nothing to build — go straight to the REPL below.
# Engine / compiler C++ changed:
task build-release-game
# Decompiler or decompiler/config changed (then step 3 is mandatory):
task build-release-decomp
```

### 3. Asset Extraction
- **Status:** Standard extraction sufficient.
- **Details:** no custom asset. The mod only moves the playable-Daxter animations into the
  always-loaded GAME package, which the normal build does.
```bash
task extract
```

### 4. Launch the Game
Run the game natively:
```bash
task boot-game
```
*(Or launch via the OpenGOAL REPL using `task repl`, then compile and run with `(mi)` and `(r)`).*

## Demonstration Video

[![Demonstration Video](https://img.youtube.com/vi/{YOUTUBE_ID}/maxresdefault.jpg)](https://youtu.be/{YOUTUBE_ID})

**[Watch the demonstration video on YouTube](https://youtu.be/{YOUTUBE_ID})**

> [!NOTE]
> *Demonstration videos must be hosted externally on YouTube to prevent repository bloating. Replace `{YOUTUBE_ID}` with your YouTube video ID (e.g. `MnqnybexhSA` from `https://youtu.be/MnqnybexhSA`).*

## Technical Documentation
For the complete technical breakdown, architecture, developer notes and change log, refer to:
- [`docs/modding/current_mod/multiplayer_readme.md`](docs/modding/current_mod/multiplayer_readme.md)

---
*(AI-assisted)*
