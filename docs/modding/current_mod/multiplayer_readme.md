# Multiplayer (Jak 3): technical notes

How the online mod is built: the network layer in the runtime, the GOAL side, the shared world,
the vanilla hook points, how to test it in game automatically, its limits, and the change log.
Player-facing description: the root [`README.md`](../../../README.md). GOAL patterns referenced
here live in the Lisp wiki ([`.agents/skills/goal-lisp/wiki/`](../../../.agents/skills/goal-lisp/wiki/index.md)).

## 1. Architecture

Two layers, one rule: C++ moves bytes, GOAL gives them meaning.

| Layer | File | Role |
|---|---|---|
| Runtime (C++) | `game/kernel/jak3/kmultiplayer.cpp`, `.h` | TCP session (host or client), relay, player slots, kick and ban, interpolation of players and objects, config file, test scripts. Exposed to GOAL as the `pc-mp-*` kernel functions, registered from `InitMachine_PCPort` (`game/kernel/jak3/kmachine.cpp`). |
| Runtime (C++) | `game/sce/libpad.cpp` | Pad 0 input forced by a test script. |
| Test | `test/test_kmultiplayer.cpp` | Interpolation and a loopback host + client session (join, relay, event, ban, refusal reason). |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer.gc` | Local state, remote puppets and their hurtboxes, passenger seat, shared enemy deaths, hit forwarding, player hits, Daxter mode, cheats state, teleports, overlay, per-frame hook. |
| GAME | `.../multiplayer-world.gc` | The shared world: NPC and prop replication, guests' NPCs asleep, hits on replicas, NPC vehicles, effects, projectiles, crates and pickups. |
| GAME | `.../multiplayer-menu.gc` | START > Options > Multiplayer and its administration page, the D-pad text field, the Mods menu entries. |
| GAME | `.../multiplayer-test.gc` | Automated test commands (inert without `MP_TEST_SCRIPT`). |
| Tool | `scripts/mp-test/run_mp_test.py`, `scenarios/` | Runs several game instances on one PC with a script each, gathers logs and screenshots. |

### 1.1 Network

- **Topology:** star. The host is slot 0 and listens on the port of its address field (default
  7777); guests get slots 1 to 7. Every message from a guest goes to the host, which relays it.
- **Transport:** TCP with `TCP_NODELAY`, non-blocking sockets pumped once per frame from GOAL
  (`pc-mp-update`), no thread. A frame is `[u16 length][u8 type][payload]`; malformed or
  oversized frames drop the peer. Message types: `HELLO` (protocol version), `WELCOME` (slot),
  `REJECT` (reason, then close), `STATE`, `LEAVE`, `EVENT`, `ACTORS`.
- **Player state:** `mp-player-state` (192 bytes: position, orientation, velocity, animation,
  health, flags, driven vehicle, passenger seat, name, color, level, continue point) at 30 Hz.
- **Objects:** `mp-actor-state` batches (80 bytes each, at most 64 per batch) at 15 Hz from every
  player: `[u8 source][u32 source time][u16 count][states]`. Ids carry the owner's slot in the
  top byte and the owner's process pid below.
- **Events:** `mp-event` (80 bytes), routed by slot (`to = -1` for everyone). The kinds are
  defined in GOAL (`mp-event-kind`); the C++ side only stamps the sender.
- **Timeouts:** 15 s of silence drops a peer, 8 s to connect. The pump runs from the per-frame
  hook even while paused, so a paused player does not time out.
- **Config:** `<settings>/multiplayer.cfg` (`%APPDATA%/OpenGOAL/jak3/settings/` on Windows):
  name, address, character, color, enabled.

### 1.2 The shared world

The host is the authority on the world; every player owns its own avatar and props.

| What | Simulated by | Seen by the others as |
|---|---|---|
| The player, its animation, health | its player | an `mp-puppet` (Jak, or Daxter in `indax` mode) |
| Daxter on the shoulder, the gun, the vehicle driven and its wheels | its player | replicas of those props |
| NPCs: the `enemy` family (citizens, guards, monsters, marauders...), vehicles and wheels near a player | the host | replicas; the guests' own NPCs sleep and their traffic is off |
| One-shot effects (part-trackers) | where they start | the same effect started at the same place |
| Projectiles | the shooter's machine (the host for NPCs) | a copy fired from the puppet or the replica |
| Enemy deaths, broken crates, taken pickups | where they happen | the same entity dying or removed |
| Time of day | the host | followed by the guests |

A replica is built from the owner's skeleton (`mp-skel-of` finds the skeleton-group from the
process's art group and joint geometry, since a process does not keep it) and plays the owner's
animation by index. Animation-carried effects (footsteps, sounds) play on the replica itself.
Both players and objects are drawn 100 ms in the past, interpolated with the owner's clock, so
props stay on the puppet that carries them.

### 1.3 Who applies damage

Each blow or shot counts once, on the machine that owns the victim:

| Blow | Detected on | Applied by |
|---|---|---|
| A player's punch or shot on another player | the attacker's machine (its hurtbox for that player) | the victim, as a stock hit with knockback (`pvp` event) |
| A player's punch or shot on an NPC replica | the attacker's machine | the host, on the real NPC, from the attacker's puppet (`actor-hit`) |
| An NPC's melee on a player's puppet | the host | that player (`hit` event) |
| An NPC's shot | every machine, on its own player, with its copy of the shot | locally; puppets ignore shots |

Copies of a player's shots collide with the background only: they are drawn, the shooter's machine
sends their hits.

## 2. Requirements and how they are met

| Requirement | Implementation |
|---|---|
| Create or join a server from the start menu | START > Options > Multiplayer: **Host a server**, **Join the server**, **Disconnect**, plus a status line. The ring of the start menu has exactly ten slots (36 degrees each, matching its 3D model), so the page hangs off the Options list, which gets a sixth entry while the mod is enabled. |
| IP address with port, nickname | **Server address (ip:port)** and **Player name** text fields (D-pad editing). |
| Jak or Daxter | **Character** carousel. Daxter is the stock playable-Daxter mode (`indax`), made resident in GAME (section 3). |
| Color | **Color** carousel. A marker, not a tint: a small square of the player's color before its name, and its minimap dot in that color (own copies of the stock dot class, `*mp-minimap-nodes*`). |
| Cheats menu per player | Mods menu (L3 + SELECT) > multiplayer: refill ammo, restore health, dark eco, light eco, invincibility, teleport to a zone, teleport to a player. Greyed out for guests when the host disallows cheats. |
| Health bar above players | Name, color square and health bar (green, yellow, red) over every remote player in view. |
| Shared vehicle: steal or sit next to the driver | Next to a remote driver's vehicle: press triangle to steal it, hold to ride as passenger when it has a free seat. A guest can also take the host's NPC vehicles. See 4.3. |
| Synchronize everything | Section 1.2. |
| Players hit each other, with a reaction | Section 1.3: an `mp-hurtbox` child of each puppet receives the local player's blows and shots; the victim plays the stock hit reaction, pushed away from the attacker. Host setting **Players can hit each other** (on by default). |
| Game entities focus any player | Puppets are `process-focusable` in the player collide list with `collide-spec jak`, which `enemy::find-best-focus` considers. |
| Players on the minimap in real time | Each puppet has a minimap icon in its player's color. |
| Administration page when hosting | Options > Multiplayer > **Server administration**: pick a player, kick, ban, teleport them or everyone to you, cheats for guests, players hitting each other, share time of day, stop the server. |

## 3. Vanilla touch points

Each is marked `MOD multiplayer`.

| File | Change |
|---|---|
| `game/kernel/jak3/kmachine.cpp` | `InitMachine_PCPort` registers the `pc-mp-*` functions. |
| `game/sce/libpad.cpp` | Pad 0 input forced by a test script. |
| `game/CMakeLists.txt`, `test/CMakeLists.txt` | The new C++ file and its test. |
| `goal_src/jak3/dgos/game.gd` | The four mod files at the end of GAME, `target-indax.o`, `target-indax-hang.o` and `jak-indax+0-ag.go`. |
| `goal_src/jak3/dgos/facc.gd`, `voca.gd`, `halfpipe.gd`, `vin.gd` | The same three objects removed: a copy in a level DGO would relink the indax states into that level's heap, and they would dangle once it unloads. |
| `goal_src/jak3/engine/ui/text-h.gc` | Text ids `#x1500`-`#x1529` for the start menu labels. |
| `game/assets/jak3/text/game_custom_text_en-US.json`, `fr-FR.json` | Their English and French strings. |

Wrapped at load time, each calling the stock version kept by `define-perm`:

| Function or method | File | Why |
|---|---|---|
| `enemy` `mark-as-dead` | `engine/common-obs/enemy-states.gc` | Share enemy deaths. |
| `progress` `set-menu-options` | `pc/progress/progress-pc.gc` | The `'multiplayer` page. |
| `part-tracker-init-base` | `engine/common-obs/generic-obs.gc` | Every one-shot effect starts there. |
| `projectile-init-by-other` | `engine/common-obs/projectile.gc` | Every projectile starts there (the stock one never returns, so the shot is sent first). |
| `process-entity-status!` | `engine/entity/entity.gc` | Broken crates and taken pickups mark their entity dead there. |

The per-frame hook `*mods-menu-hook*` (`engine/game/main.gc`) is set to `mp-frame-hook`, which
calls `mods-menu-update` first.

## 4. How it works in game

### 4.1 One frame

1. `mp-frame-hook` (every frame, paused or not): runs the test script if any, describes the local
   player, pumps the network, copies the remote players' interpolated states, spawns the manager
   if missing (at most once a second when the process pool is full), then draws names, health
   bars, prompts and notifications.
2. `mp-manager` (`*pc-pool*`, frozen while paused): applies events, keeps one `mp-puppet` per
   remote player, switches the character, keeps invincibility, runs passenger and steal logic,
   finishes teleports, then `mp-world-update`: sends this player's objects, keeps the guest's
   NPCs asleep, and keeps one `mp-replica` per object received.

### 4.2 Puppets

An `mp-puppet` uses the local player's Jak model (`target-skel-group`) or Daxter's
(`skel-sidekick`) and plays the remote player's animation by index. A clip not loaded here falls
back to the idle stance, or to the crouch for a driver or passenger (pilot animations stream in
only on the driver's machine). Its `mp-hurtbox` child sits in the actor hash, where the player's
attacks look. A model change (character, outfit) respawns the puppet.

### 4.3 Shared vehicles

- **Drawing:** the vehicle a player drives, and its wheels, are that player's props: the others
  draw replicas of them.
- **Steal (press triangle):** the thief stands where the vehicle is and asks the target for a new
  vehicle of that type (`'change-mode 'pilot #f type #t`, stock spawn path). Only once that worked
  does it send `steal`: the driver gets out and its vehicle is removed half a second later. Only
  the player vehicles: city bikes and cars (types 0-6) and Spargus buggies (12-19).
- **The host's NPC vehicles:** a guest presses triangle next to one, gets the same vehicle, and
  the host removes its own (`vehicle-taken`).
- **Ride (hold triangle 0.5 s):** with a second seat no other player uses. An invisible `mp-seat`
  follows the driver's seat 1 and the player rides it with the stock `target-grab-ride` state.
  Each machine draws the passenger in the driver's seat as it sees that vehicle.

### 4.4 Guests' own NPCs

While connected, a guest turns its ambient traffic off (`set-setting! 'traffic-spawn #f`, held by
the manager) and, four times a second, puts every own NPC to sleep: `sleep` process mask, not
drawn, collision off. Everything is restored when the session ends.

## 5. Testing in game

### 5.1 Unit tests and compile

```bash
task build-release-game                                   # gk + goalc, after any C++ change
cmake --build out/build/Release --target goalc-test
out/build/Release/bin/goalc-test --gtest_filter=Multiplayer.*
task compile-check                                        # first time in a worktree: (make-group "iso" :force #t)
```

### 5.2 Automated sessions

```bash
python scripts/mp-test/run_mp_test.py <scenario>
```

The runner starts one game per script of `scripts/mp-test/scenarios/<scenario>/`, each with its
own `--config-path` folder (a copy of the user's saves and settings, small window, side by side,
controller ignored when unfocused) and the `MP_TEST_SCRIPT` environment variable. The debug boot
loads save slot 1, so both games start in game at the same place. When the scripts are done, it
stops them and writes `out/mp-test/<scenario>/summary.txt` (every `[mp-test]` and `[multiplayer]`
line) and `screenshots/<instance>-<name>.png` (rendered by the game itself).

A script line is `<seconds> <command>`:

| Command | Effect |
|---|---|
| `enable`, `name <w>`, `color <n>`, `character <n>` | Mod settings. |
| `host <ip:port>`, `join <ip:port>`, `leave` | Session. |
| `press <button> <ms>`, `stick <lx> <ly> <ms>` | Force pad 0 (button names in PS2 order; stick 0-255, 128 centered). |
| `goto-player <slot> [m]`, `face-player <slot>` | Stand next to a remote player, turn to it. |
| `goto-npc <skeleton prefix>`, `goto-actor <n>` | Stand next to a replicated NPC, facing it. |
| `goto-crate`, `log-crates` | Stand at the nearest crate; count the live crates. |
| `gun`, `vehicle <type>`, `cheat <name>`, `zone <n>`, `continue <name>` | Gameplay helpers. |
| `screenshot <name>`, `log` | Evidence: the log shows the session, the local player, every remote player and puppet, the objects sent, received and drawn, the effects and shots sent and replayed. |

Scenarios and what they verified (2026-10-14, Spargus):

| Scenario | Checks | Result |
|---|---|---|
| `smoke` | Host, join, puppets, names, health bars | Both see each other. |
| `pvp` | A guest's punch on the host | Host health 8 to 5, pushed back 3 m, hit reaction seen by the guest. |
| `npc` | Host NPCs on the guest | 12 replicas drawn, 10 own NPCs asleep. |
| `npc-hit` | A guest's punch on a host NPC | The citizen goes to its knocked state on the host. |
| `props`, `combat` | Daxter, gun, shots, impacts | Daxter on the shoulder and the gun on the remote puppet; 3 shots and 2 impacts replayed. |
| `vehicle` | A driven buggy, then stolen | Body and wheels replicated; the driver is ejected and sees the thief's buggy. |
| `ride` | Passenger seat | The guest rides in the host's two-seat buggy. |
| `crate` | A crate broken by the host | The guest goes from 48 to 47 crates. |

| | |
|---|---|
| ![Name, color square and health bar](multiplayer-screenshots/pvp-nameplate.jpg) | ![The host's NPCs on the guest](multiplayer-screenshots/npc-replicas.jpg) |
| ![Remote player with gun and Daxter](multiplayer-screenshots/remote-gun-daxter.jpg) | ![A guest's punch on a host NPC](multiplayer-screenshots/npc-hit.jpg) |
| ![Steal prompt next to the host's buggy](multiplayer-screenshots/vehicle-prompt.jpg) | ![The host after the steal](multiplayer-screenshots/vehicle-stolen.jpg) |
| ![Riding as passenger](multiplayer-screenshots/passenger.jpg) | |

Over the Internet, the host forwards TCP port 7777 (or the port chosen) and guests use the host's
public IP.

## 6. Known limits

- Each player keeps its own story progress; missions are not shared. A guest's world is the host's
  wherever the host has the level loaded; elsewhere it is the guest's own world without its NPCs.
- Replicas show channel 0 of the owner's animation: blends, look-at joint mods and ragdolls are not
  sent.
- Streamed animations (pilot, board, some powers) show as the stance or the crouch on machines that
  have not loaded them. Remote Jaks wear the local player's outfit.
- Guests cannot hijack an NPC vehicle the host does not let go of: they get a copy and the host's
  disappears.
- Only one-shot effects (part-trackers) are shared; continuous emitters of an NPC are not.
- Text fields use the D-pad (keyboard arrows work too).
- TCP head-of-line blocking can stall a player on a lossy link (interpolation hides short stalls).

## 7. Change log

- **2026-10-14:** first version. Network session in the runtime with its tests; START > Options >
  Multiplayer and the administration page; Mods menu entries (toggle, cheats, teleports);
  puppets with name, health bar and minimap icon, focusable by enemies with hit forwarding;
  vehicle ghosts, steal and passenger seat; playable Daxter made resident in GAME; shared enemy
  deaths and time of day.
- **2026-10-14:** fixes from a code review: text fields crashed on confirm (a static object's
  `mounted?` starts at 0, not `#f`); Host and Join fired twice; "Stop the server" ignored "No";
  refusal reasons were never sent; steal limited to player vehicle types; passengers drawn in
  their driver's seat; macOS `MSG_NOSIGNAL` fallback.
- **2026-10-14:** first in-game report: the process pool filled up and the start menu could no
  longer open. The manager's handle was kept in a global symbol, which only holds 32 bits: the pid
  was cut off, the handle never resolved, and the hook spawned a manager every frame. Handles and
  timers now live in structures.
- **2026-10-14:** second round, tested in game with the automated sessions. Players hit each other
  (hurtbox, knockback, host setting). The color is a marker before the name and on the minimap, no
  longer a tint. Shared world: host-authoritative NPCs and vehicles drawn as replicas, guests' own
  NPCs asleep and traffic off, hits on replicas applied by their owner, NPC vehicles taken by
  guests. Every player's props (Daxter, gun, vehicle and wheels) replace the ghost vehicle. Shared
  effects, projectiles, broken crates and taken pickups. A driver or passenger crouches when its
  pilot animation is not loaded. Automated multi-instance tests with screenshots
  (`scripts/mp-test/`).
