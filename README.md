# Multiplayer — Jak 3

<p align="center">
  <img src="https://img.shields.io/badge/OpenGOAL-Mod-blue.svg" alt="OpenGOAL Mod">
  <img src="https://img.shields.io/badge/Game-Jak%203-orange.svg" alt="Target Game">
  <img src="https://img.shields.io/badge/AI--assisted-Modding-purple.svg" alt="AI Assisted">
</p>

> **Contents:** [Overview](#overview) · [Key Features](#key-features) · [How to Play](#how-to-play) · [Download & Play](#download--play-via-opengoal-launcher-players) · [Developer Setup](#developer-setup--local-compilation) · [Demo Video](#demonstration-video) · [Technical Documentation](#technical-documentation)

---

## Overview
Jak 3 online: host or join a game from the title screen, meet in a lobby, and play the host's
world together as Jak or Daxter, in free roam or as a Jak and Daxter co-op duo.

- **Target Game:** Jak 3
- **Repository:** [`whozghiar/jak3-multiplayer`](https://github.com/whozghiar/jak3-multiplayer), created from the modding base [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project)

## Key Features
- **Host Game and Join Game on the title screen:** the host picks a new game or a save, the
  others type the host's address (`ip:port`). Everyone meets in a lobby: choose Jak or Daxter and
  say you are ready; the host chooses the game mode and starts once everyone is ready. Hosting and
  joining from the pause menu (Options > Multiplayer) work too. Up to 8 players.
- **The host's world for everyone:** a player who joins loads the host's game (its progress,
  equipment and place, after a loading screen) and appears next to the host. Their own save is
  neither loaded nor overwritten: nothing is saved while they play in the host's world.
- **Two game modes, chosen by the host in the lobby,** with the same rules (shared world,
  vehicles, weapons, story):
  - **Free roam:** everyone plays the character they like, Jak or Daxter, and goes where they
    want; a player who goes far from the others keeps a living world around them.
  - **Co-op:** Jak and Daxter as a duo. A guest who picks the host's character is offered the
    other one, and Daxter can jump on a crouching Jak's shoulder (triangle) and ride along until
    he jumps off (X).
- **Play as Jak or Daxter:** Daxter is the game's own playable Daxter, available everywhere. He
  fights with the guns like Jak, and gets in vehicles and drives them as Daxter.
- **Player list:** hold SELECT to see every player with their ping. While online, SELECT no longer
  pauses the game (START does).
- **Your color:** ten colors, shown as a small square before your name and as your dot on the
  minimap.
- **See each other:** every player appears with their name and health bar over their head, as a
  dot on the minimap, with Daxter on their shoulder, their gun and its laser sight, their shots,
  their hoverboard and the vehicle they drive, in their own outfit, moving exactly as they move
  on their own screen, with their effects and sounds. Their Dark Jak and Light Jak show too, and
  so do their wave concussor, mass inverter, arc wielder and needle lazer.
- **One shared world:** the host's citizens, guards, enemies and traffic are the same for everyone,
  with their movements, effects and sounds, and they react to every player's shots. A player who
  goes far from the others keeps a living world around them, which the players near them share,
  however far apart the players are.
  When players meet or part, the people and vehicles in front of you do not vanish at once: they
  leave when you look away, or fade out. Enemies killed, crates broken and pickups taken disappear for everybody.
- **Share vehicles:** next to another player's vehicle, press triangle to steal it, or L1 +
  triangle to ride next to them when it has two seats (Daxter rides as Daxter). Guests can also
  take the host's traffic vehicles. The prompt shows the buttons, like the game's own. The
  shells and shots of vehicle guns show and hurt on every screen.
- **Enemies fight everyone:** enemies can target any player, and the hits they land count.
- **Players can hit each other:** punches, shots and blasts between players (the wave concussor,
  the mass inverter, the arc wielder, the needle lazer too) hurt and knock back (the host can turn
  it off).
- **Cheats menu:** in the Mods menu (L3 + SELECT): refill ammo, restore health, dark eco and
  light eco, invincibility, teleport to a zone or to a player.
- **Shared story:** a mission completed by one player is completed for everybody, with its
  reward; a player who joins catches up with the others; cutscenes play for the players together
  (the host can turn it off).
- **Same world:** doors open for whoever stands at them, an elevator is one elevator for
  everyone (where one player rides it, it rides on every screen), moving and floating platforms
  are in the same place on every screen, and they feel every player's weight.
- **Administration for the host:** kick or ban a player, teleport players to you, allow or forbid
  cheats for guests and players hitting each other, share story progress and your time of day,
  stop the server.

## How to Play
1. Open the Mods menu with **L3 + SELECT**, go to **multiplayer** and turn on **Enable
   multiplayer**. The setting is remembered.
2. On the title screen, the host picks **Host Game**, then **New Game** or **Load Game**; the
   others pick **Join Game**, type the host's address and **Connect**. To edit a text field:
   confirm, then up / down to change a letter, left / right to move, square to delete, confirm
   to save (triangle cancels).
3. In the lobby, everyone picks Jak or Daxter (L1 / R1) and the guests say they are ready (X).
   The host picks the game mode (square) and starts (X) once everyone is ready: its save menu
   opens, and the guests follow into its world. Circle opens the multiplayer options (name,
   color, address), triangle leaves the lobby.
4. In game, START > **Options > Multiplayer** holds the same settings, the server administration
   for the host, and hosting or joining without going back to the title screen.

| Action | Control |
|---|---|
| Mods menu (cheats, teleports) | L3 + SELECT |
| Lobby: character / ready or start / game mode | L1 or R1 / X / Square |
| Lobby: multiplayer options / leave | Circle / Triangle |
| Player list | Hold SELECT |
| Steal a player's vehicle | Triangle next to it |
| Ride as passenger | L1 + Triangle next to it |
| Get off as passenger | Triangle |
| Co-op, as Daxter: jump on a crouching Jak's shoulder / jump off | Triangle / X |

> [!NOTE]
> **Playing over the Internet:** the host must open (forward) TCP port 7777, or the port they
> chose, on their router, and give the others their public IP address. On a local network, use the
> host's local IP (for example `192.168.1.20:7777`). Every player must have the same version of the
> mod.

A player who joins plays in the host's world: its progress, items and place. Their own save stays
as it was: it is not loaded, and nothing is saved while they play in the host's world (load your
own save, or go back to the title screen, to save again). Story progress is shared: missions
completed by anyone are completed for all.

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

## Credits
The title screen's Host Game and Join Game, the lobby, the SELECT player list, the passenger's
seat and binding (L1 + triangle) and the Jak and Daxter co-op duo follow himstan's
[Jak and Daxter II Multiplayer](https://github.com/himstan/Jak-And-Daxter-2-Multiplayer)
(ISC license), rewritten for Jak 3.

---
*(AI-assisted)*
