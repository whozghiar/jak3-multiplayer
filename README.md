# Multiplayer — Jak 3

<p align="center">
  <img src="https://img.shields.io/badge/OpenGOAL-Mod-blue.svg" alt="OpenGOAL Mod">
  <img src="https://img.shields.io/badge/Game-Jak%203-orange.svg" alt="Target Game">
  <img src="https://img.shields.io/badge/AI--assisted-Modding-purple.svg" alt="AI Assisted">
</p>

> **Contents:** [Overview](#overview) · [Key Features](#key-features) · [How to Play](#how-to-play) · [Download & Play](#download--play-via-opengoal-launcher-players) · [Developer Setup](#developer-setup--local-compilation) · [Demo Video](#demonstration-video) · [Technical Documentation](#technical-documentation)

---

## Overview
Jak 3 online: host or join a game from the title screen, meet in a lobby, and play together as
Jak or Daxter: free roam at the end of the game with everything unlocked, or the story as a Jak and
Daxter co-op duo with its own multiplayer save.

- **Target Game:** Jak 3
- **Repository:** [`whozghiar/jak3-multiplayer`](https://github.com/whozghiar/jak3-multiplayer), created from the modding base [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project)

## Key Features
- **Host Game and Join Game on the title screen:** the host's lobby opens at once and the others
  join it by typing the host's address (`ip:port`) before anything starts. In the lobby, choose
  Jak or Daxter and say you are ready; the host chooses the game mode and, once everyone is
  ready, starts a 10-second countdown that every player sees and the host can cancel. Up to 8
  players (2 in co-op). Servers are hosted and joined from the title screen only, and the game
  mode is chosen in the lobby only.
- **Game modes, chosen by the host in the lobby:** Parkour, Egg hunt, PvP, Battlefront, Hide and
  seek and Races are listed for later (grayed out, coming soon); two can be played now:
  - **Free roam:** the end of the game. Every story mission is done and everything is unlocked
    for every player: all the guns and their upgrades, Dark and Light Jak's powers, the
    hoverboard, the vehicles. Everyone plays Jak or Daxter and goes where they want. Nothing is
    saved.
  - **Co-op (2 players):** the story as Jak and Daxter. The host starts a new campaign, continues
    it, or picks any mission of the story, act by act (everything before it done). In game, the
    host can start another mission for both players (START > Options > Multiplayer > Choose a
    mission). The campaign is saved on
    the host's computer as a multiplayer save of its own, apart from the memory card's save
    slots. A guest who picks the host's character is offered the other one, and in game either
    player can swap the roles (START > Options > Multiplayer > Character): there is always one
    Jak and one Daxter. In cutscenes, Jak
    wears the host's outfit and Daxter the outfit of the player who plays him. A player who dies
    on foot while the other one is alive nearby watches them for 5 seconds ("Spectating"), their
    body on the ground, then comes back next to them, and the mission goes on. When one player
    starts a mission (reaching its spot, talking to someone), the other is brought there and the
    mission starts for both. When a mission fails, the failure shows for both players at once and
    waits for both: when both chose to retry (or to quit), both are put back together, at the
    mission's start or at the respawn point. When one player starts a burning bush challenge,
    the other can join it for 30 seconds (D-pad up): both play it, each with their own score (the
    player playing Daxter plays it as Jak), the same in free roam, and only the best of those who complete it gets the
    reward. Players cannot hurt each other or each other's vehicles, and where the
    game leaves a lizard, two wait side by side, one for each player. A cutscene is skipped once both players asked
    (triangle); only the host can pause it. A waypoint (the yellow beam) waits for both players to
    stand on it ("Players here: 1/2"). In a race each player drives their own vehicle (no riding
    along, no shoulder): the start waits until both drive, the place on screen counts the other
    player (with six racers and two players you can be 7th), and the race is won only when both
    players finish first and second. Every story mission plays in co-op: each player gets its own
    leaper in the leaper race, its own gun in the turret missions, its own torpedo, bomb robot or
    mech, and Daxter takes them as Jak does.
- **The host's world for everyone:** a player who joins loads the host's game (its progress,
  equipment and place, after a loading screen) and appears next to the host. Their own save is
  neither loaded nor overwritten: nothing is saved while they play in the host's world.
- **Play as Jak or Daxter:** Daxter is the game's own playable Daxter, available everywhere. A
  Jak played online carries no Daxter of the game: Daxter is a player. Next to a crouching Jak, or
  a Jak driving, Daxter's triangle climbs onto his shoulder with the game's own animation, and he
  rides there, in vehicles too, until he jumps off (X). He fires every gun too: R1 fires the mod chosen with the D-pad, aimed at the nearest enemy ahead,
  with the mod and its ammo shown at the bottom of the screen, on foot, on Jak's shoulder, riding
  along or on a lizard. He holds the gun in his hands and aims and fires it with the game's own
  Daxter animations, on every screen. He gets in vehicles and drives them as Daxter, and rides
  the lizards (triangle next to one), drawn as Daxter on it. R2 takes the jet-board out, a board
  at his size that he rides as Jak does, with his own board animations.
- **Every weapon:** a Mods menu option gives the gun with its twelve mods, their upgrades and full
  ammo, for Jak and for Daxter alike.
- **See each other through walls:** a player hidden by a wall, a vehicle or anything else shows
  as an outline in their color.
- **Player list:** hold SELECT to see every player with their health and their ping.
- **Pause:** the host's SELECT pauses the game for everyone; SELECT again resumes it after a
  3-second countdown. START opens your menu while the game goes on, for you and for the others;
  your character stands still meanwhile. START > Restart restarts the mission for every player.
  Quitting the game (START > Quit) leaves the server.
- **Appearance:** Options > Multiplayer > Appearance colors each part of Jak's and Daxter's
  clothes (shirt, armor, pants, boots, scarf, hair, fur...), with a turning preview. Every player
  sees your outfit.
- **Your color:** ten colors, shown as a small square before your name and on the minimap, as
  a cone turned the way you face (like your own marker) and as a dot on its edge when you are far.
- **See each other:** every player appears with their name and health bar over their head, as a
  dot on the minimap, with Daxter on their shoulder, their gun and its laser sight, their shots,
  their hoverboard and the vehicle they drive, in their own outfit, moving exactly as they move
  on their own screen, with their effects and sounds. Their Dark Jak (with its grey skin) and
  Light Jak show too, and
  so do their wave concussor, mass inverter, arc wielder and needle lazer.
- **One shared world:** the host's citizens, guards, enemies and traffic are the same for everyone,
  with their movements, effects and sounds, and they react to every player's shots. A player who
  goes far from the others keeps a living world around them, which the players near them share,
  however far apart the players are.
  When players meet or part, the people and vehicles in front of you do not vanish at once: they
  leave when you look away, or fade out. Enemies killed, crates broken and pickups taken disappear for everybody.
- **Share vehicles:** next to another player's vehicle, press triangle to steal it, or L1 +
  triangle to ride with them in a free seat, seated like the driver (Daxter rides as Daxter): up
  to three passengers in a city car, one on the back of a one-seat buggy. Guests can also
  take the host's traffic vehicles. The prompt shows the buttons, like the game's own. The
  shells and shots of vehicle guns show and hurt on every screen.
- **Enemies fight everyone:** enemies can target any player, and the hits they land count.
- **Players can hit each other:** punches, shots and blasts between players (the wave concussor,
  the mass inverter, the arc wielder, the needle lazer too) hurt and knock back (the host can turn
  it off; never in co-op).
- **Cheats menu:** in the Mods menu (L3 + SELECT): every weapon, refill ammo, restore health, dark
  eco and light eco, invincibility, teleport to a zone or to a player.
- **Shared story:** a mission completed by one player is completed for everybody, with its
  reward; a player who joins catches up with the others; cutscenes play for the players together
  (the host can turn it off).
- **Same world:** doors open for whoever stands at them, an elevator is one elevator for
  everyone (where one player rides it, it rides on every screen), moving and floating platforms
  are in the same place on every screen, and they feel every player's weight.
- **Administration for the host:** kick or ban a player, teleport players to you, allow or forbid
  cheats for guests and players hitting each other, share story progress and your time of day,
  stop the server. When the host leaves, every player goes back to the title screen with "The
  host ended the session".
- **Adapts to a weak connection:** when a player's connection loses messages, the game sends
  them less of what can wait, as himstan's mod does, and goes back to full rate once it recovers.
- **Precursor orbs and Metal Head trophies:** every precursor orb you take and every Metal Head
  you kill counts for you. The host's game keeps every player's count, saves it every minute
  and when the session ends, and gives it back when it hosts that session again; each orb counts
  once. In free roam, spend your orbs at **Kleiver's shop** in Spargus: he stands there in person (he turns to face you), at one of four places, the
  same for every player and drawn at random for each session, marked by his icon on the minimap
  and by "Merchant" in yellow over his spot. Stand there and press triangle to buy and wear new
  outfits. The host's Mods menu can give the host 100 orbs or 100 trophies.

## How to Play
1. Open the Mods menu with **L3 + SELECT**, go to **multiplayer** and turn on **Enable
   multiplayer**. The setting is remembered.
2. On the title screen, the host picks **Host Game**: the lobby opens and the others can join at
   once. The others pick **Join Game**, type the host's address and **Connect**. Text fields (the
   address, your name) open an on-screen keyboard: move with the D-pad or the stick, X types the
   key, square deletes, L1 switches capitals, START (or the OK key) keeps the text, triangle
   cancels.
3. In the lobby, everyone picks Jak or Daxter (L1 / R1) and the guests say they are ready (X).
   The host opens the game modes with square: **Free roam**, or **Co-op**, which then asks for a
   new campaign, the campaign continued, or a mission of the story (an act, then one of its
   missions). The host starts the
   countdown (X) once everyone is ready, and the guests follow into its world when it ends. Circle opens the multiplayer options
   (name, color, address), triangle leaves the lobby.
4. In game, START > **Options > Multiplayer** changes your name, character (in free roam your
   own; in co-op it swaps the roles), color and
   appearance. In co-op the host also finds **Choose a mission** there: an act, then a mission,
   started for both players (the guest loads the host's world again). The server administration, hosting and joining are at the title screen and in the
   lobby.

| Action | Control |
|---|---|
| Mods menu (every weapon, cheats, teleports) | L3 + SELECT |
| Lobby: character / ready or start / game modes | L1 or R1 / X / Square |
| Keyboard: type / delete / capitals / done | X / Square / L1 / START |
| Daxter: fire / choose the gun mod | R1 / D-pad |
| Lobby: multiplayer options / leave | Circle / Triangle |
| Player list | Hold SELECT |
| Host: pause / resume for everyone | SELECT |
| Co-op: ask to skip a cutscene | Triangle |
| Steal a player's vehicle | Triangle next to it |
| Ride as passenger | L1 + Triangle next to it |
| Get off as passenger | Triangle |
| As Daxter: climb onto a crouching or driving Jak's shoulder / jump off | Triangle / X |
| Get on a lizard (Jak or Daxter) | Triangle next to it |
| Daxter: jump on Jak's shoulder (at any time) | L1 + triangle next to him (triangle alone next to a crouching Jak) |
| Daxter: choose a gun | D-pad (it shows in his hand), then R1 to fire |
| Kleiver's shop: open / buy or wear / leave | Triangle next to him / X / Triangle |

> [!NOTE]
> **Playing over the Internet:** the host must open (forward) UDP port 7777, or the port they
> chose, on their router, and give the others their public IP address. On a local network, use the
> host's local IP (for example `192.168.1.20:7777`). Every player must have the same version of the
> mod.

A player who joins plays in the host's world: its progress, items and place. Their own save stays
as it was: it is not loaded, and nothing is saved while they play in the host's world (load your
own save, or go back to the title screen, to save again). In co-op, the host's saves (the pause
menu's and the checkpoints') go into its multiplayer campaign, never into a memory card slot.
Story progress is shared: missions completed by anyone are completed for all.

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
- **Details:** no custom asset. The mod only moves the playable-Daxter animations (on foot,
  driving, holding the gun) into the always-loaded GAME package, which the normal build does.
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
The title screen's Host Game and Join Game, the lobby and its countdown, the SELECT player list,
the passengers' seats and binding (L1 + triangle), the Appearance page, the Jak and Daxter
co-op duo, Daxter riding on Jak's shoulder, the outfits in cutscenes, the return next to a
teammate, the session's end screen and the adaptive network delay follow himstan's
[Jak and Daxter II Multiplayer](https://github.com/himstan/Jak-And-Daxter-2-Multiplayer)
(ISC license), rewritten for Jak 3.

---
*(AI-assisted)*
