# Multiplayer — Jak 3

<p align="center">
  <img src="https://img.shields.io/badge/OpenGOAL-Mod-blue.svg" alt="OpenGOAL Mod">
  <img src="https://img.shields.io/badge/Game-Jak%203-orange.svg" alt="Target Game">
  <img src="https://img.shields.io/badge/AI--assisted-Modding-purple.svg" alt="AI Assisted">
</p>

> **Contents:** [Overview](#overview) · [Getting Started](#getting-started) · [Online Modes](#online-modes) · [Chat and Server Commands](#chat-and-server-commands) · [Controls](#controls) · [Features in Every Mode](#features-in-every-mode) · [Download & Play](#download--play-via-opengoal-launcher-players) · [Developer Setup](#developer-setup--local-compilation) · [Demo Video](#demonstration-video) · [Technical Documentation](#technical-documentation)

---

## Overview
Jak 3 online, for up to 8 players. One player hosts a server from the title screen, the others
join it by address, and everyone meets in a lobby before the game starts. Two online modes can be
played today:

- **Free roam:** the end of the game with everything unlocked, for up to 8 players, with an
  economy kept by the server: precursor orbs earned in the world buy outfits and the gun's higher
  mods at Kleiver's shop.
- **Co-op:** the story for 2 players, one as Jak and one as Daxter, with a campaign that saves
  itself apart from your single-player saves.

This page shows how to set up a session, then how each mode works.

- **Target Game:** Jak 3
- **Repository:** [`whozghiar/jak3-multiplayer`](https://github.com/whozghiar/jak3-multiplayer), created from the modding base [`whozghiar/jak-project`](https://github.com/whozghiar/jak-project)

## Getting Started

Every player needs the mod installed ([Download & Play](#download--play-via-opengoal-launcher-players)),
in the same version.

### 1. Enable multiplayer
Open the Mods menu with **L3 + SELECT**, go to **multiplayer** and turn on **Enable multiplayer
(START > Options)**. The title screen now lists **Host Game** and **Join Game**, and the pause
menu gains **START > Options > Multiplayer**. The setting is remembered.

### 2. Choose your name and look (optional)
In **START > Options > Multiplayer**:

| Option | What it does |
|---|---|
| Player name | Shown over your head, in the player list and in the chat. |
| Character | Jak or Daxter. |
| Color | One of ten colors: the square before your name and your marker on the minimap. |
| Appearance | A color for each part of Jak's and Daxter's clothes, with a turning preview. Everyone sees your outfit. |
| Server address | The host's address you join, or the port you host on. |

Text fields open an on-screen keyboard: move with the D-pad or the stick, X types the key, square
deletes, L1 switches capitals, START (or the OK key) keeps the text, triangle cancels.

### 3. Host a server
On the title screen, pick **Host Game**. The server starts and its lobby opens at once, so the
others can join right away. It listens on the port of your Server address (7777 by default).
Give the others the address they need:

| Where the others play | Address to give |
|---|---|
| Same local network | Your computer's local IP and the port, for example `192.168.1.20:7777`. |
| Over the Internet | The address in the status line: "Hosting: 1 player(s), internet address ...". |

> [!NOTE]
> **Playing over the Internet:** the game asks your router to open its UDP port by itself (UPnP,
> else NAT-PMP). Once the port is open, the status line at the top of the lobby and of START >
> Options > Multiplayer shows the internet address. If it never shows, forward the UDP port (7777,
> or the one you chose) on your router by hand, then give your public IP address with the port.
> Connections are encrypted.

### 4. Join a server
On the title screen, pick **Join Game**, type the host's address (`ip:port`) and pick **Connect**.
You arrive in the host's lobby; if its game has already started, you follow into it within about
10 seconds. If the connection fails, the message tells you why:

| Message | What to do |
|---|---|
| Connection refused / Connection timed out: is the server running? | Check the address and port, and that the host is in its lobby or game. Over the Internet, the host's port must be open. |
| The server is full | The server takes 8 players, 2 in co-op. |
| Version mismatch: update the mod | Install the same version of the mod as the host. |
| You are banned from this server | The host banned you until its server stops. |

### 5. The lobby
- Everyone picks **Jak or Daxter** with L1 / R1.
- The guests press **X** when they are ready.
- The host presses **square** to choose the game mode ([Online Modes](#online-modes)).
- Once every guest is ready, the host's **X** starts a 10-second countdown that every player
  sees; the host's X again cancels it. When it ends, every player loads the host's world.
- **Circle** opens the multiplayer options, **triangle** leaves the lobby.

When the host leaves, stops the server or quits, every player goes back to the title screen with
"The host ended the session".

## Online Modes

| | Free roam | Co-op |
|---|---|---|
| Players | Up to 8 | 2 |
| Characters | Each player picks Jak or Daxter | One Jak and one Daxter |
| World | The end of the game, everything unlocked | The story, from the start, your campaign or a chosen mission |
| Progress saved | The world is not saved; the server keeps every player's orbs and purchases | The campaign saves itself on the host's computer |
| Economy and Kleiver's shop | Yes | No |
| Players can hurt each other | Yes, unless the host turns it off | Never |

The game mode list also shows Parkour, Egg hunt, PvP, Battlefront, Hide and seek and Races, grayed
out: they are planned, not playable yet.

### Free roam

**Start it:** in the lobby, the host presses square, picks **Free roam**, then starts the
countdown. Everyone appears in Spargus at the end of the game.

**What you get:** every story mission is done, and everything is unlocked for every player: the
gun's four colors with their ammo upgrades, Dark and Light Jak's powers, the hoverboard and the
vehicles. Each gun color starts at its first mod; the second and third are bought at Kleiver's
shop.

**What to do:** go where you want, alone or together. The activities that pay precursor orbs (the
burning bush challenges, the gun courses, the turret game, the power game) offer themselves again
once done, and pay again. When a player starts a challenge, the others can join it within 30
seconds (D-pad up).

#### Earning orbs and trophies
- Every **precursor orb** you pick up counts for you. Each orb pays once on a server, to whoever
  takes it first: an orb already paid in an earlier session pays nothing again.
- Every **Metal Head** you kill gives you a **Metal Head trophy**. Trophies are counted on your
  account; nothing spends them yet.

#### Kleiver's shop
Kleiver stands in Spargus, at one of four places drawn at random for each session, the same for
every player. His face marks the place on the minimap, and "Merchant" shows in yellow over him.
Stand next to him and press **triangle** to trade: **X** buys an item or wears an outfit you own,
**triangle** leaves.

| Item | Price (precursor orbs) |
|---|---|
| Gold outfit | 5 |
| Shadow outfit | 10 |
| Desert outfit | 8 |
| Flame Daxter | 6 |
| Wave Concussor (red, second mod) | 15 |
| Plasmite RPG (red, third mod) | 30 |
| Beam Reflexor (yellow, second mod) | 15 |
| Gyro Burster (yellow, third mod) | 30 |
| Arc Wielder (blue, second mod) | 15 |
| Needle Lazer (blue, third mod) | 30 |
| Mass Inverter (dark, second mod) | 20 |
| Super Nova (dark, third mod) | 40 |

A third mod needs its color's second mod first. In free roam, the Mods menu's "Give every weapon"
gives only the mods you bought.

#### Your account on a server
- The host's computer is the server: it keeps every player's orbs, trophies and purchases in its
  own database, written at once, and finds them again each time it hosts.
- Your account follows you, not your name: it rests on a secret id that your game draws once and
  never sends. Each server sees a different account id for you, so another server starts you at
  zero.
- The secret id and the server's own id live in `multiplayer.cfg`, in the game's settings folder
  (`%APPDATA%\OpenGOAL\jak3\settings\` on Windows). Keep that file: without it, a player starts a
  new account everywhere, and a host's server no longer recognizes anyone. The server's database is
  `multiplayer-server.db` in the host's settings folder.
- The host is trusted with the accounts, as on a game server hosted at home: its Mods menu gives it
  100 orbs or 100 trophies, and its chat commands credit any player.

### Co-op

**Start it:** co-op takes 2 players, so the mode is grayed out while more than one guest is in the
lobby. The host presses square, picks **Co-op**, then one of:

| Choice | What starts |
|---|---|
| New campaign | The story from its start. |
| Continue the campaign | The co-op campaign where it was last saved. |
| Choose a mission | An act, then one of its missions, with everything before it done. |

In game, the host can start another mission for both players from **START > Options > Multiplayer
> Choose a mission**. There is always one Jak and one Daxter: a guest who picks the host's
character is offered the other one, and **START > Options > Multiplayer > Character** swaps the
roles.

#### The co-op save
- The campaign saves itself on the host's computer, into its own file (`multiplayer-campaign.bin`,
  next to the memory card saves in `%APPDATA%\OpenGOAL\jak3\saves\`), never into a single-player
  slot.
- It saves after each step of the story (a mission's start or end) and wherever the game saves by
  itself. The save icon shows on both screens each time.
- The pause menu's Save Game saves the campaign at once ("Co-op progress saved"); its Load Game
  waits for the end of the session.
- The guest's own save is neither loaded nor overwritten.

#### Playing the story together
- **Missions:** when one player talks to a mission giver, the other is asked to join (D-pad up to
  accept, D-pad down to decline, 15 seconds). Both must accept; the cutscene then waits while the
  other player is brought there.
- **Cutscenes:** both players watch them together. One is skipped once both press triangle; only
  the host can pause it. Jak wears the host's outfit, Daxter the outfit of the player who plays
  him.
- **Waiting for each other:** a waypoint (the yellow beam) waits for both players ("Players here:
  1/2"), and a door that unloads the place behind it stays open for the player still behind it.
- **Death and failure:** a player who dies on foot near a living partner watches them for 5
  seconds, then comes back next to them. A failed mission shows for both players and waits for
  both to choose retry or quit.
- **Races:** each player drives their own vehicle in the same race, and the race is won when one
  of the two finishes first.
- **Every mission plays in co-op:** each player gets their own leaper, turret gun, torpedo, bomb
  robot or mech, and where the game leaves a lizard, two wait side by side.
- **Burning bush challenges:** the other player can join for 30 seconds (D-pad up). Both play with
  their own score; the best of those who complete it gets the reward.
- Players cannot hurt each other or each other's vehicles.

## Chat and Server Commands
The chat works with a keyboard only. Press **T** to write, **Enter** to send, **Escape** to cancel,
Backspace to erase. The last lines show at the left of the screen. A line holds 96 characters at
most, and accented letters lose their accent.

The host administers the server from the chat. A name can be shortened to its beginning, and case
does not matter.

| Command | Effect |
|---|---|
| `/help` | Lists the commands. |
| `/players` | Each player's slot, name and ping. |
| `/kick name` | Disconnects that player. |
| `/ban name` | Disconnects that player and refuses them until the server stops. |
| `/tp name` | Brings that player next to the host. |
| `/tpall` | Brings every player next to the host. |
| `/give name orbs` | Credits that player's precursor orbs (a negative number takes some). Free roam only. |
| `/trophies name count` | Credits that player's Metal Head trophies. Free roam only. |

The same administration is in **START > Options > Multiplayer > Server administration** while you
host: kick, ban, teleports, cheats for guests, players hitting each other, sharing story progress
and your time of day, stopping the server.

## Controls

| Action | Control |
|---|---|
| Mods menu (every weapon, cheats, teleports) | L3 + SELECT |
| Lobby: character / ready or start / game modes | L1 or R1 / X / Square |
| Lobby: multiplayer options / leave | Circle / Triangle |
| On-screen keyboard: type / delete / capitals / done / cancel | X / Square / L1 / START / Triangle |
| Player list | Hold SELECT |
| Host: pause / resume for everyone | SELECT |
| Chat: open / send / cancel / erase (keyboard only) | T / Enter / Escape / Backspace |
| Daxter: choose the gun mod / fire | D-pad / R1 |
| Daxter: climb onto a crouching or driving Jak's shoulder / jump off | Triangle / X |
| Daxter: jump on Jak's shoulder at any time | L1 + Triangle next to him |
| Steal a player's vehicle | Triangle next to it |
| Ride as a passenger / get off | L1 + Triangle next to it / Triangle |
| Get on a lizard (Jak or Daxter) | Triangle next to it |
| Join a challenge another player started | D-pad up (30 s) |
| Free roam: Kleiver's shop: open / buy or wear / leave | Triangle next to him / X / Triangle |
| Co-op: accept / decline a mission the other player starts | D-pad up / D-pad down (15 s) |
| Co-op: ask to skip a cutscene | Triangle |

## Features in Every Mode
- **The host's world for everyone:** a player who joins loads the host's game (its progress,
  equipment and place, after a loading screen) and appears next to the host. Their own save is
  neither loaded nor overwritten, and nothing is saved into it while they play in the host's
  world.
- **Play as Jak or Daxter:** Daxter is the game's own playable Daxter. A Jak played online carries
  no Daxter of the game: Daxter is a player, who climbs onto Jak's shoulder and rides there, in
  vehicles too. Daxter fires every gun (R1 fires the mod chosen with the D-pad, aimed at the
  nearest enemy ahead), drives vehicles, rides the lizards and takes out a jet-board at his size
  (R2).
- **See each other:** every player shows with their name and health bar over their head, as a
  colored cone on the minimap, with their gun, shots, hoverboard, vehicle, Dark Jak and Light Jak,
  in their own outfit, moving as they move on their own screen. A player hidden behind a wall shows
  as an outline in their color.
- **One shared world:** the host's citizens, guards, enemies and traffic are the same for everyone
  and react to every player's shots. Enemies can target any player. Doors open for whoever stands
  at them, an elevator is one elevator for everyone, and platforms feel every player's weight.
  Enemies killed, crates broken and pickups taken disappear for everybody.
- **Share vehicles:** next to another player's vehicle, triangle steals it, L1 + triangle rides
  along in a free seat (up to three passengers in a city car). Guests can also take the host's
  traffic vehicles.
- **Shared story:** a mission completed by one player is completed for everybody, with its reward;
  cutscenes play for the players together (the host can turn it off).
- **Pause:** the host's SELECT pauses the game for everyone, and SELECT again resumes it after a
  3-second countdown. START opens your own menu while the game goes on. START > Restart restarts
  the mission for every player; START > Quit leaves the server.
- **Cheats menu:** the Mods menu (L3 + SELECT) gives every weapon, refills ammo, health, dark eco
  and light eco, makes you invincible and teleports you to a zone or a player. Guests use it only
  when the host allows cheats for guests.
- **Weak connections:** when a player's connection loses messages, the game sends them less of
  what can wait, and goes back to full rate once it recovers.

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

To try a session alone, `python scripts/mp-test/local_pair.py` starts two copies of the game side
by side, one played with the controller and one with the keyboard; join with `127.0.0.1:7777`.

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
