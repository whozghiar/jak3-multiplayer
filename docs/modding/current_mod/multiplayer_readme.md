# Multiplayer (Jak 3): technical notes

How the online mod is built: the network layer in the runtime, the GOAL side, the vanilla hook
points, what each requirement maps to, how to test it, its limits, and the change log.
Player-facing description: the root [`README.md`](../../../README.md). GOAL patterns referenced
here live in the Lisp wiki ([`.agents/skills/goal-lisp/wiki/`](../../../.agents/skills/goal-lisp/wiki/index.md)).

## 1. Architecture

Two layers, one rule: C++ moves bytes, GOAL gives them meaning.

| Layer | File | Role |
|---|---|---|
| Runtime (C++) | `game/kernel/jak3/kmultiplayer.cpp`, `.h` | TCP session (host or client), relay, player slots, kick and ban, interpolation of remote players, config file. Exposed to GOAL as the `pc-mp-*` kernel functions, registered from `InitMachine_PCPort` (`game/kernel/jak3/kmachine.cpp`). |
| Test | `test/test_kmultiplayer.cpp` | Interpolation and a loopback host + client session (join, relay, event, ban). |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer.gc` | Local state, remote puppets, vehicle ghosts, passenger seat, shared enemy deaths, hit forwarding, Daxter mode, cheats state, teleports, overlay, per-frame hook. |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer-menu.gc` | START > Options > Multiplayer and its administration page, the D-pad text field, the Mods menu entries. |

### 1.1 Network

- **Topology:** star. The host is slot 0 and listens on the port of its address field (default
  7777); guests get slots 1 to 7. Every message from a guest goes to the host, which relays it.
- **Transport:** TCP with `TCP_NODELAY`, non-blocking sockets pumped once per frame from GOAL
  (`pc-mp-update`), no thread. A frame is `[u16 length][u8 type][payload]`; malformed or
  oversized frames drop the peer. Message types: `HELLO` (protocol version), `WELCOME` (slot),
  `REJECT` (reason, then close: refused, kicked, banned, server stopped), `STATE`, `LEAVE`, `EVENT`.
- **State:** each player sends its `mp-player-state` (192 bytes: position, orientation, velocity,
  animation index and frame, health, flags, driven vehicle, passenger seat, name, level, continue
  point) at 30 Hz.
- **Events:** `mp-event` (80 bytes), routed by slot (`to = -1` for everyone). The kinds are
  defined in GOAL (`mp-event-kind`); the C++ side only stamps the sender.
- **Timeouts:** 15 s of silence drops a peer, 8 s to connect. The pump runs from the per-frame
  hook even while paused, so a paused player does not time out.
- **Config:** `<settings>/multiplayer.cfg` (`%APPDATA%/OpenGOAL/jak3/settings/` on Windows):
  name, address, character, color, enabled.

### 1.2 Limiting desynchronization

| Mechanism | Where |
|---|---|
| Remote players drawn 100 ms in the past, interpolated between the two surrounding snapshots (positions lerped, rotations nlerped, animation frame lerped within the same clip). | `mp::Remote::sample` |
| Sender clocks matched by tracking the fastest observed delivery, so jitter does not stretch motion. | `mp::Remote::push` |
| Late packets: extrapolation along the last velocity for 250 ms, then hold. A jump of more than 10 m snaps (teleports never slide). | `mp::Remote::sample` |
| Enemy deaths shared by entity `aid`: the other machines play the same enemy's death (`instant-death`). Echoes are suppressed. | `mp-on-enemy-died`, `mp-apply-remote-kill` |
| Time of day shared by the host every 2 s (15 min tolerance). | `mp-apply-world` |
| A vehicle has one driver: stealing ejects the driver and removes their copy. | `mp-steal-now`, `mp-on-stolen` |
| Guests take the host's cheat permission. | `*mp-world*` |

Each player keeps their own story progress and their own copy of the world. Enemies other than
their deaths, pickups, crates and mission state are not synchronized.

## 2. Requirements and how they are met

| Requirement | Implementation |
|---|---|
| Create or join a server from the start menu | START > Options > Multiplayer: **Host a server**, **Join the server**, **Disconnect**, plus a status line. The ring of the start menu has exactly ten slots (36 degrees each, matching its 3D model), so the page hangs off the Options list, which gets a sixth entry while the mod is enabled. |
| IP address with port | **Server address (ip:port)** text field. Host names work too (resolved once when joining). |
| Nickname | **Player name** text field (15 characters). |
| Jak or Daxter | **Character** carousel. Daxter is the stock playable-Daxter mode (`indax`, `engine/target/indax/`), entered from the idle stance. Its code and animations were only in the FACC, VOCA and HALFPIPE level DGOs; they are now resident in GAME (section 3). |
| Color | **Color** carousel, ten tints applied to the player's model on the other screens (`draw-control` `color-mult`) and to its health bar. |
| Cheats menu per player (not the debug menu) | Mods menu (L3 + SELECT) > multiplayer: refill ammo, restore health, dark eco, light eco, invincibility, teleport to a zone (19 continue points), teleport to a player (live list). Greyed out for guests when the host disallows cheats. |
| Health bar above players | Name and bar drawn over every remote player in a level loaded here, within 80 m (`mp-draw-nameplate`). |
| Shared vehicle: steal or sit next to the driver | Next to a remote driver's vehicle on foot: press triangle to steal it, hold triangle to ride as passenger when it has a free second seat. See 4.3. |
| Limit desynchronization | Section 1.2. |
| Game entities may focus any player | Remote players are `process-focusable` puppets in the player collide list with `collide-spec jak`, which `enemy::find-best-focus` already considers. A hit on a puppet is forwarded to its player (once a second at most). |
| Players on the minimap in real time | Each puppet adds a minimap icon (class 124, green clamped dot) tracking it. |
| Administration page when hosting | Options > Multiplayer > **Server administration** (only selectable while hosting): pick a player, kick, ban (address refused until the server stops), teleport them or everyone to you, cheats for guests, share time of day, stop the server. |

## 3. Vanilla touch points

Each is marked `MOD multiplayer`.

| File | Change |
|---|---|
| `game/kernel/jak3/kmachine.cpp` | `InitMachine_PCPort` registers the `pc-mp-*` functions. |
| `game/CMakeLists.txt`, `test/CMakeLists.txt` | The new C++ file and its test. |
| `goal_src/jak3/dgos/game.gd` | The two mod files at the end of GAME, `target-indax.o`, `target-indax-hang.o` and `jak-indax+0-ag.go` (after the other `jak-*+0-ag` groups). |
| `goal_src/jak3/dgos/facc.gd`, `voca.gd`, `halfpipe.gd`, `vin.gd` | The same three objects removed. A copy in a level DGO would relink the indax states into that level's heap, and they would dangle once the level unloads. The levels use the resident copy. |
| `goal_src/jak3/engine/ui/text-h.gc` | Text ids `#x1500`-`#x1529` for the start menu labels. |
| `game/assets/jak3/text/game_custom_text_en-US.json`, `fr-FR.json` | Their English and French strings (other languages fall back to English). |

Two methods are wrapped at load time, each calling the stock method kept by `define-perm`:

- `enemy` `mark-as-dead` (`engine/common-obs/enemy-states.gc`): every enemy death goes through it,
  from `on-dying`. GAME loads with method-set propagation on, so child classes take the wrapper.
- `progress` `set-menu-options` (`pc/progress/progress-pc.gc`): adds the `'multiplayer` page, like
  `'camera-options` maps to a generic scrolling page.

The per-frame hook `*mods-menu-hook*` (`engine/game/main.gc`) is set to `mp-frame-hook`, which
calls `mods-menu-update` first.

## 4. How it works in game

### 4.1 One frame

1. `mp-frame-hook` (every frame, paused or not): `mp-build-local-state` describes the player,
   `pc-mp-update` sends it and pumps the network, `mp-read-remote-states` copies each remote
   player's interpolated state into `*mp-players*`, the manager is spawned if missing, then
   `mp-draw-overlay` draws names, health bars, the vehicle prompt and notifications.
2. `mp-manager` (`*pc-pool*`, frozen while paused): applies events, keeps one `mp-puppet` per
   remote player, switches the character, keeps invincibility, runs passenger and steal logic,
   finishes teleports.

### 4.2 Puppets

An `mp-puppet` uses the player's own Jak model (`target-skel-group`, so a puppet follows the local
outfit) or Daxter's (`skel-sidekick`). Animations travel as an index in the art group, which is
the same on every machine: `jakb-ag` for Jak, `daxter-ag` while in `indax` mode. A clip that is not
loaded on the receiving machine (streamed sets such as vehicle or board animations) falls back to
the idle stance. A puppet is hidden while its player stands in a level that is not active here,
but its minimap icon keeps moving. A model change (character, outfit) respawns the puppet.

### 4.3 Shared vehicles

- **The driver** sends its vehicle's type (matched by class name against `vehicle-type`), position,
  orientation, seat count and the world position of seat 1. Vehicle classes are level code that
  GAME cannot name, so seat count and seat position come from vehicle methods 65 and 66 called
  through the method table, after checking the process inherits from `vehicle`.
- **Other machines** draw a ghost of that vehicle: a `manipy` with `skel-<type>`, only while that
  art is loaded (the ghost is removed as soon as the lookup fails, before the level is freed).
  Buggy wheels are separate processes and are not drawn on the ghost.
- **Steal (press triangle):** the thief stands where the vehicle is and asks the target for a new
  vehicle of that type (`'change-mode 'pilot #f type #t`, stock spawn path in
  `target-handler.gc`). Only once that worked (the target is in `target-pilot-start`) does it send
  `steal`: the driver gets out (`'end-mode 'pilot`) and its copy is removed with `'go-die` half a
  second later. Only the player vehicles can be stolen: city bikes and cars (types 0-6, spawner in
  CWI) and Spargus buggies (12-19, WASALL). The others (catapult, marauders, factory car) have
  their art in levels that lack `vehicle-manager.o`, whose `vehicle-spawn` the stock path calls.
- **A passenger's puppet** sits in its driver's seat as each machine sees that vehicle (the
  driver's own machine uses its real vehicle), not at the passenger's reported position, which
  crosses the network twice and would trail behind.
- **Ride (hold triangle 0.5 s):** only with a second seat that no other player uses. The manager
  spawns an `mp-seat` (invisible, follows the driver's seat 1) and puts the player in the stock
  `target-grab-ride` state, which moves the player to that process every frame. The player's model
  is hidden and a local puppet shows it in the seat. Triangle, or the driver leaving the vehicle,
  ends the ride.
- Daxter leaves `indax` mode before stealing or riding, and goes back to it afterwards.

### 4.4 Enemies

Enemies pick their focus among the processes of `*collide-player-list*` whose collide spec matches,
so puppets compete with the local player by distance and awareness. Hits stay visual on the
attacker's machine; the victim receives a `hit` event and takes one point of damage through a
stock `'attack` (generic mode), once a second at most.

## 5. Building and testing

```bash
task build-release-game                                   # gk + goalc, after any C++ change
cmake --build out/build/Release --target goalc-test       # then:
out/build/Release/bin/goalc-test --gtest_filter=Multiplayer.*
task compile-check                                        # GOAL; first time in a worktree: (make-group "iso" :force #t)
```

The DGO lists changed, so the ISO build must be redone (`(mi)` does it).

Test with two game instances on one PC: the second needs its own settings folder or it shares
`multiplayer.cfg` (harmless, but both get the same name). Host with `127.0.0.1:7777` on one,
join `127.0.0.1:7777` on the other. Over the Internet, the host forwards TCP port 7777 (or the
port chosen) and guests use the host's public IP.

Checklist (by a human, the agent cannot launch the game):

1. Mods menu > multiplayer > Enable: START > Options shows Multiplayer.
2. Host, join: both see the other's character, color, name and health bar, and the minimap dot.
3. Character Daxter: the player turns into playable Daxter from the idle stance, back with Jak.
4. Cheats: each entry, then disallowed for guests from the administration page.
5. Teleport to a zone, to a player in the same area and in another area.
6. Vehicle: steal (driver ejected), ride (passenger seat, triangle to get off).
7. Enemies attacking a puppet; kill an enemy and check it dies on the other screen.
8. Kick, ban (rejoining fails), stop the server (guests see the reason).
9. Disable the mod: Options goes back to five entries, the session closes.

## 6. Known limits

- Each player plays their own story: a player in a mission area another player has not unlocked
  still sees and reaches them, but the world around may differ.
- Enemies are simulated on each machine; only deaths are shared.
- Streamed animations (vehicle, board, some powers) show as the idle stance on machines that have
  not loaded them.
- Vehicle ghosts have no wheels; hover vehicles look complete.
- Text fields use the D-pad (keyboard arrows work too); there is no typed text input.
- Remote Jaks wear the local player's outfit (`target-skel-group` reads the local `ext-geo`).
- Animation indices match across machines when every clip sits at its `master-art-group-index`;
  `link-art-to-master` falls back to any free slot when that one is taken, so a remote player can
  show a wrong clip (never a crash: the clip type is checked).
- Not audited: enemy code that assumes its focus is the `target` type could misbehave when it
  focuses a puppet.
- The resident indax code and `jak-indax+0-ag` (421 KB) add to the global heap; not measured
  (the PC global heap is about 300 MB).
- TCP head-of-line blocking can stall a player on a lossy link (interpolation hides short stalls).

## 7. Change log

- **2026-10-14:** first version. Network session in the runtime (TCP star, 30 Hz states,
  interpolation, events, kick and ban, config file) with its tests; START > Options > Multiplayer
  and the administration page; Mods menu entries (toggle, cheats, teleports); puppets with tint,
  name, health bar and minimap icon, focusable by enemies with hit forwarding; vehicle ghosts,
  steal and passenger seat; playable Daxter made resident in GAME; shared enemy deaths and time of
  day. Built and unit-tested; not yet played.
- **2026-10-14:** fixes from a code review. Text fields crashed on confirm (a static object's
  `mounted?` starts at 0, not `#f`, so the edit buffer was never allocated). Host and Join fired
  again on the confirm that deselects the button, and Host then dropped every guest; both now
  act only from offline. "Stop the server" stopped even on "No" (stock confirm option); a
  subclass now checks the choice. Refusal reasons (full, version, banned) were never sent; a
  refused or kicked connection now gets its reason, a half-close, and a second of drained input
  before closing, so no reset swallows the reason (covered by the test). Stealing is limited to
  the player vehicle types; the steal event is sent only after the new vehicle exists; passengers
  are drawn in their driver's seat. macOS build: `MSG_NOSIGNAL` fallback.
- **2026-10-14:** first in-game report: the log filled with `mp-manager could not be allocated,
  because *nk-dead-pool* was empty`, and the start menu (`progress`) could not open. The manager's
  handle was kept in a global symbol, which only holds 32 bits: the pid was cut off, the handle
  never resolved, and the hook spawned a new manager every frame until the process pool was full.
  The handle (and the notification timer) now live in the `mp-globals` structure, and a failed
  manager spawn is retried once a second.
