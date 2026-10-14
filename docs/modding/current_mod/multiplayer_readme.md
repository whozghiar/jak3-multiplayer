# Multiplayer (Jak 3): technical notes

How the online mod is built: the network layer in the runtime, the GOAL side, the shared world,
the vanilla hook points, how to test it in game automatically, its limits, and the change log.
Player-facing description: the root [`README.md`](../../../README.md). GOAL patterns referenced
here live in the Lisp wiki ([`.agents/skills/goal-lisp/wiki/`](../../../.agents/skills/goal-lisp/wiki/index.md)).

## 1. Architecture

Two layers, one rule: C++ moves bytes, GOAL gives them meaning.

| Layer | File | Role |
|---|---|---|
| Runtime (C++) | `game/kernel/jak3/kmultiplayer.cpp`, `.h` | TCP session (host or client), relay, player slots, kick and ban, interpolation of players, objects and poses, pose encoding, config file, test scripts. Exposed to GOAL as the `pc-mp-*` kernel functions, registered from `InitMachine_PCPort` (`game/kernel/jak3/kmachine.cpp`). |
| Runtime (C++) | `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, left stick). |
| Test | `test/test_kmultiplayer.cpp` | Interpolation and a loopback host + client session (join, relay, event, objects, a pose with an emitter and a sound, ban, refusal reason). |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer.gc` | Local state, remote puppets (in their player's outfit) and their hurtboxes, passenger seat, shared enemy deaths and story progress, hit forwarding, player hits, Daxter mode, cheats state, teleports, overlay, per-frame hook. |
| GAME | `.../multiplayer-world.gc` | The shared world: who simulates which NPCs, NPC and prop replication, full poses, particle emitters and sounds, sleeping NPCs, hits on replicas, NPC vehicles, effects, projectiles, world objects (cycles, doors, elevators), cutscenes, levels leaving, crates and pickups. |
| GAME | `.../multiplayer-menu.gc` | START > Options > Multiplayer and its administration page, the D-pad text field, the Mods menu entries. |
| GAME | `.../multiplayer-test.gc` | Automated test commands (inert without `MP_TEST_SCRIPT`). |
| Tool | `scripts/mp-test/run_mp_test.py`, `scenarios/`, `compare_poses.py` | Runs several game instances on one PC with a script each, gathers logs and screenshots; compares an owner's bones with its copies'. |

### 1.1 Network

- **Topology:** star. The host is slot 0 and listens on the port of its address field (default
  7777); guests get slots 1 to 7. Every message from a guest goes to the host, which relays it.
- **Transport:** TCP with `TCP_NODELAY`, non-blocking sockets pumped once per frame from GOAL
  (`pc-mp-update`), no thread. A frame is `[u16 length][u8 type][payload]`; malformed or
  oversized frames drop the peer. Message types: `HELLO` (protocol version, now 4), `WELCOME`
  (slot), `REJECT` (reason, then close), `STATE`, `LEAVE`, `EVENT`, `ACTORS`, `POSES`.
- **Player state:** `mp-player-state` (192 bytes: position, orientation, velocity, animation,
  health, flags, character and outfit, body segments, driven vehicle, passenger seat, name, color,
  level, continue point) at 30 Hz.
- **Objects:** `mp-actor-state` batches (80 bytes each, at most 64 per batch) at 15 Hz from every
  player: `[u8 source][u32 source time][u16 count][states]`. Ids carry the owner's slot in the
  top byte and the owner's process pid below. The source time of object batches and poses is in
  tenths of a millisecond: the receiver's clock estimate and the blend between poses one frame
  apart lose up to a millisecond otherwise (about 3.5 mm on a running citizen).
- **Poses:** `[u8 source][u32 source time][u16 count]` then one entry per object:
  `[u32 id][u16 bones, bit 15 = effects][u8 emitters][u8 sounds]`, each bone 20 bytes (rotation,
  translation to 1 mm, per-axis scale, relative to the object's frame), each emitter 20 bytes,
  each sound 40 bytes. Split over several frames of at most 16 KB. Every object of a batch is sent
  with its effects at 15 Hz; between batches, the player, its props and the NPCs within 40 m of
  another player are sent every frame, bones only. About 1.3 KB per Jak pose, 0.6 KB per NPC.
- **Events:** `mp-event` (80 bytes), routed by slot (`to = -1` for everyone). The kinds are
  defined in GOAL (`mp-event-kind`); the C++ side only stamps the sender.
- **Timeouts:** 15 s of silence drops a peer, 8 s to connect. The pump runs from the per-frame
  hook even while paused, so a paused player does not time out.
- **Config:** `<settings>/multiplayer.cfg` (`%APPDATA%/OpenGOAL/jak3/settings/` on Windows):
  name, address, character, color, enabled.

### 1.2 The shared world

Every player owns its avatar and props. The NPCs belong to an authority: the host for everyone
near it, and a guest far from every authority for itself and the guests near it (1.4).

| What | Simulated by | Seen by the others as |
|---|---|---|
| The player, its pose, outfit, health | its player | an `mp-puppet` (Jak in the player's outfit, or Daxter in `indax` mode) with the player's exact pose |
| Daxter on the shoulder, the gun, the vehicle driven and its wheels | its player | replicas of those props, with their exact pose |
| NPCs: the `enemy` family (citizens, guards, monsters, marauders...), vehicles and wheels near a player | that player's authority | replicas with their exact pose; a player who is not an authority keeps its own NPCs asleep and its traffic off |
| One-shot effects (part-trackers) | where they start | the same effect started at the same place |
| Continuous particle emitters and direct particle launches of a player, its props, the NPCs | their owner | the same emitter on the copy, at the same place on it |
| Sounds of a player, its props, the NPCs (one-shot and lasting, such as engines) | their owner | the same sound at the copy, lasting sounds updated and stopped like the owner's |
| Projectiles | the shooter's machine (the authority for NPCs) | a copy fired from the puppet or the replica |
| Enemy deaths, broken crates, taken pickups | where they happen | the same entity dying or removed |
| Story progress: closed task nodes (missions done, with their rewards) | where they close | the same nodes closed; a player who joins gets the others' and gives its own (1.5) |
| Time of day | the host | followed by the guests |
| World objects that carry or block the player (lifts, platforms, doors) | every machine, for its own player (a lift must answer the player standing on it) | kept alike (1.6): cycles follow the host's clocks, doors open for any player in front of them, an elevator trip started on one machine runs on all |
| Physics objects of the level (floating platforms, objects on physics) | the region's authority, with the weight of every player on them | each machine's own object of that entity, moved every frame where the authority has it, still carrying its player (1.6) |
| Levels | every machine, around its own player | players in one place load the same levels; the place of a distant player is loaded here too, not displayed, so its objects are drawn (1.6) |
| Cutscenes | where they start | the same cutscene, for the players of that region (1.6) |

A replica is built from the owner's skeleton (`mp-skel-of` finds the skeleton-group from the
process's art group and joint geometry, since a process does not keep it) and plays the owner's
animation by index, which plays the animation's effects (footsteps, sounds) on the replica
itself. The owner's full pose then replaces its bones (1.4). Both players and objects are drawn
100 ms in the past, interpolated with the owner's clock, so props stay on the puppet that carries
them.

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

### 1.4 Poses, emitters, sounds and authority

- **Poses.** With each 15 Hz batch, a player sends the bones of its own model, of its props and of
  every NPC it sends, relative to each object's frame (the root position and orientation of its
  state, without scale). Between batches, every frame, it sends the bones again for its own model,
  its props and the NPCs within 40 m of another player (`mp-send-near-poses`): their copies are
  drawn from every frame of the owner's, not blended between poses 66 ms apart. The objects of a
  batch are kept as handles, so one that died meanwhile is skipped. While an NPC is sent, its joint math runs in full whatever its level of
  detail on the owner's screen (`force-math`, set by `mp-force-math` and cleared once it is no
  longer sent), since another player may see it up close. An object its owner does not draw (a
  hidden prop, a player during a teleport) sends no pose: its copy is hidden too (`log`: `poses
  hidden`). A passenger's own model is hidden, so the seated model its machine draws is sent
  instead. An NPC whose joint math did not run lately (off the owner's screen, frozen by a
  cutscene) gets its bones computed on the spot (`do-joint-math`) before they are sent. `log`
  counts the poses left out of drawn objects (`poses skipped`): 0 in every scenario. The copy's `joint-control` has `mp-pose-postbind` as its
  `postbind-function`: `do-joint-math` computes the copy's bones from its own animation, then this
  function lays the owner's bones, interpolated, on the copy's frame (same bone count only). Blends,
  streamed animations, look-at and aiming joint mods, joint scales and ragdolls all come through.
- **Emitters.** Two wrapped entry points note the particle emitters of the objects sent: the
  launch-control `spawn` method (groups run every frame: flames, trails, auras) and
  `sp-launch-particles-var` without a launch control (direct launches: dust, glows). Each batch
  entry lists them, with their origin relative to the frame. The copy keeps a part-tracker per
  group (at most 4) while the owner keeps it running, and replays each direct launch as many times
  as the owner made it. `*part-id-table*` keeps the entries of unloaded levels, so a direct launcher
  carries a hash of the level holding it, and the copy uses it only from that loaded level.
- **Sounds.** `sound-play-by-name`, `sound-play-by-spec` and `sound-stop` note the sounds of the
  same objects, the latest call per sound id. The copy plays each one at its place with a sound id
  of its own per owner's id: the sound player updates a sound played again with the same id, so an
  engine stays one sound, and it stops when the owner stops it or stops updating it for 0.4 s.
- **Authority.** The host always simulates its NPCs. A guest shows the NPCs of a lower-slot
  authority within 60 m and simulates its own once farther than 90 m from all of them, after 2 s
  in the new situation (`mp-update-authority`); its state carries the `authority` flag. An
  authority sends the NPCs within 100 m of the players it serves (itself, and the players who are
  not authorities and whose nearest authority it is), and drops the copies of another authority's
  NPCs standing nearer to itself than to that player.

### 1.5 Story progress

Progress in Jak 3 is the set of closed task nodes (`*game-info* sub-task-list`). Every closure
goes through the node's `open!` method (which also closes the parents the node needs), wrapped in
`multiplayer.gc`: the node index goes to the others in a `progress` event (a 256-node bitmap), and
they close the same node with `open!`, rewards and on-close scripts included. When a player joins,
every player already there sends it all its closed nodes, and it sends its own: everyone ends with
the union. Host setting **Share story progress** (on by default).

### 1.6 World objects and cutscenes

- **Cycles.** Moving platforms, wheels, lights and every other object on a cycle compute their
  phase from their process's clock (`sync-info`), and each machine's clocks started at its own
  boot. The host sends its clocks with the world settings every 2 s (`*mp-synced-clocks*`); on a
  guest, the `sync-info` methods (`get-norm!` of the three cycle types, `get-current-phase-no-mod`,
  `get-timeframe-offset!`, `sync-now!`) see the object's offset shifted by the host's lead minus
  100 ms, put back after the call. The cycles show the host's phase as its copies are drawn, and no
  timer of the game moves.
- **Doors.** `com-airlock` decides to open in `want-cross-airlock?`, for the local player only. The
  wrapper also opens it for a remote player standing where the local one would (in front, within
  its distances and heights, the door allowed to open by its script), so a door is open on every
  machine when any player is at it.
- **Elevators.** Each frame batch, an elevator near a player that started a trip here (`running`)
  is announced (`elevator` event: entity, target stop); the others send their copy of it the
  elevator's own `'move-to 'top` or `'bottom` when it waits at a stop. A trip started on another
  player's word is not announced back.
- **Cutscenes.** Every cutscene starts in `scene-player-init`, wrapped: its scenes and continue
  point go to the others (`scene` events), and a player of the same region (same NPC authority)
  with no cutscene playing starts the same one, with its commands. Gated by **Share story
  progress**.
- **Physics objects.** A `rigid-body-object` of the level (not a vehicle) within 300 m of a player
  goes with the authority's batch, named the same on every machine: its entity's aid, plus its
  rank among the objects that entity's process spawned (the volcano's lava platforms are 5
  children of one spawner entity). Another machine of that region sets its own object's rigid body
  there at rest every frame (`rigid-body-control-method-28`, `reset-momentum!`), so it only moves
  by its own physics for a frame, and it still carries the player standing on it. Floating
  platforms take a player's weight from a `'ridden` message; on the authority, a remote player's
  puppet standing on one sends it (with the target mask the handler checks set for the call).
- **Levels.** Each machine loads the levels its own player's position calls for: players in one
  place load the same levels (`log-levels` lists the same five on both machines in `smoke`). The
  level heap holds one place: 18 banks, a large level takes 12 of them, and asking for one more
  level without a free bank stops the game on purpose (`break!` after "could not find free
  small-edge bank in the level-group heap", seen in a test). The place of a distant player is
  loaded another way ("Distant players' levels" in `multiplayer-world.gc`):
  - *Which levels:* the level the distant player stands in or, when its master level is not loaded
    here, the topmost master level not loaded here (Haven's `ctyport` gives `ctywide`, which
    holds the city's shared code and art); then the borrow levels of that level the distant
    player has (cars, guards), from the list of levels each player sends when it changes
    (`levels` event).
  - *Where:* the banks of the PC level heap are 12 times the PS2 page and mostly empty. The level
    gets a heap carved from the free top of one of our active levels (its host), below 256 MB,
    sized for its pages and the borrow heaps it carves at login; it is registered in a borrow slot
    of the host that none of the host's own borrow levels can claim, so the host unloads it first
    when it goes. A hook in `load-begin` (`*mp-far-level-hook*`) gives it that heap instead of a
    bank. Its borrow levels load in its borrow heaps through the stock path.
  - *How:* loaded, never displayed: its entities never start and its callbacks never run, so
    nothing changes for our place. The copies of the distant player's objects are drawn from its
    art, in the default level's draw slot and lighting (a level only loaded has neither).
  - *Its code:* a level's code is global. Its `defun`, `define` and `deftype` of names our levels
    use would point those names at its copy, and a `deftype` of an existing type resets that
    type's methods: two halves of Spargus (`wascitya`, `wascityb`) share their code and the game
    never loads them together. A sandbox in the runtime (`pc-mp-sandbox-*`) notes every symbol,
    the methods of every type and the particle, lightning and texture tables before the load, and
    puts back, every frame while it links, each entry that held a value and now points into its
    memory; names only it defines stay (2226 entries put back for `ctywide` in Spargus). It
    leaves before our place loads any level, so no level of ours links next to it.
  - *When:* only when our place waits for no load and two level slots are free (one for its
    borrow levels); it leaves when our place loads a level, wants that level itself (it then
    loads like any of ours), or no player stands in that place any more. Leaving, the texture
    lists keep none of its shaders: a page it used without loading it would keep a list in its
    memory, which the next level there would corrupt (a list looping forever in testing).
  - *Art loaded alone:* the levels of another place that hold its objects' art do not all fit in
    the 10 level slots next to our own place's (Haven's cars, guards, citizens and boats each come
    from a level of their own), nor in the renderer's 11. For an object whose skeleton is known
    here (its code is in the far level) but whose art is not, its art group alone is loaded by
    name (`loado`, `out/jak3/obj/<name>-ag.go`) into the far level's memory, inside the sandbox,
    and listed with that level's art; the renderer keeps the data of the level its model comes
    from (`pc-mp-render-level`, `fr3`, its cap raised by 8 for Jak 3), which the sender names
    (`art-level` event: for each skeleton it sends, the level whose art array holds it). It all
    goes with the far level. In `far-city`, Haven's boats (`ctyport`) and citizens (`ctypepa`)
    are drawn in Spargus this way.
  - *No code needed:* a copy runs none of its object's code; it only needs the skeleton-group
    (the data a `defskelgroup` fills: art group name, joint geometry and animation, levels of
    detail, bounds) and the art group. Each player also sends the description of every
    skeleton-group it sends (`skel-def` event, three parts); when the code that defines a group
    is in no level here, the group is rebuilt from it (`mp-apply-skel-def`, a pool of 32), and
    the art group is loaded alone as above. `far-city-synth` builds every copy that way.
  - The player's own model is resident and drawn wherever it stands.
- **Level heap below 256 MB.** The texture page dir links the shaders of each texture by address
  divided by 16 in 24 bits (`shader-ptr`): a level above 256 MB breaks the lists, and the next
  level unload crashes or hangs (`log-shaders`: 44 broken links in Spargus with the master-dev
  multiplier of 15). `DEBUG_LEVEL_HEAP_MULT` is 12 here (and on master-dev since 3e47edcc6): the
  heap ends near `#xf400000`, 0 broken links. This was likely the cause of the earlier crashes at
  a level unload.
- **Levels leaving.** A level that leaves kills its entities and their effects, then its memory is
  reused. Before that (`level` `deactivate`, wrapped; `unload!` calls it, also for a level only
  loaded), the copies drawn with its art, the part-trackers on copies playing its particle groups
  and the skeleton cache are dropped: they would point into freed memory (three crashes at a level
  unload in testing before this, the last one from a level only loaded; none in 30 unloads since).

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
| Administration page when hosting | Options > Multiplayer > **Server administration**: pick a player, kick, ban, teleport them or everyone to you, cheats for guests, players hitting each other, share story progress, share time of day, stop the server. |

## 3. Vanilla touch points

Each is marked `MOD multiplayer`.

| File | Change |
|---|---|
| `game/kernel/jak3/kmachine.cpp` | `InitMachine_PCPort` registers the `pc-mp-*` functions. |
| `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, left stick). |
| `game/CMakeLists.txt`, `test/CMakeLists.txt` | The new C++ file and its test. |
| `goal_src/jak3/dgos/game.gd` | The four mod files at the end of GAME, `target-indax.o`, `target-indax-hang.o` and `jak-indax+0-ag.go`. |
| `goal_src/jak3/dgos/facc.gd`, `voca.gd`, `halfpipe.gd`, `vin.gd` | The same three objects removed: a copy in a level DGO would relink the indax states into that level's heap, and they would dangle once it unloads. |
| `goal_src/jak3/engine/ui/text-h.gc` | Text ids `#x1500`-`#x1514` and `#x1520`-`#x1529` for the start menu labels. |
| `game/kernel/jak3/kmachine_extras.cpp` | `pc_set_levels` adds the levels kept for a distant player's models (`mp::extra_render_levels`). |
| `game/graphics/pipelines/opengl.cpp` | The renderer keeps 8 more levels for Jak 3 (`fr3_level_count`). |
| `goal_src/jak3/engine/level/level.gc` | `*mp-far-level-hook*`, called by `load-begin` before its bank search (a distant player's level takes no bank); `DEBUG_LEVEL_HEAP_MULT` 15 to 12 (level heap below 256 MB, 1.6). |
| `game/assets/jak3/text/game_custom_text_en-US.json`, `fr-FR.json` | Their English and French strings. |

Wrapped at load time, each calling the stock version kept by `define-perm`:

| Function or method | File | Why |
|---|---|---|
| `enemy` `mark-as-dead` | `engine/common-obs/enemy-states.gc` | Share enemy deaths. |
| `progress` `set-menu-options` | `pc/progress/progress-pc.gc` | The `'multiplayer` page. |
| `part-tracker-init-base` | `engine/common-obs/generic-obs.gc` | Every one-shot effect starts there. |
| `projectile-init-by-other` | `engine/common-obs/projectile.gc` | Every projectile starts there (the stock one never returns, so the shot is sent first). |
| `process-entity-status!` | `engine/entity/entity.gc` | Broken crates and taken pickups mark their entity dead there. |
| `sparticle-launch-control` `spawn` | `engine/gfx/sprite/particles/sparticle-launcher.gc` | Every running particle group goes through it (`spawn-from-mat` and `spawn-from-cspace` too). |
| `sp-launch-particles-var` | same file | Every particle launch; only the direct ones (no launch control) are noted. |
| `sound-play-by-name`, `sound-play-by-spec`, `sound-stop` | `engine/sound/gsound.gc` | Every sound a process plays or stops. |
| `game-task-node-info` `open!` | `engine/game/task/task-control.gc` | Every task node closure (story progress). |
| `sync-linear`, `sync-eased`, `sync-paused` `get-norm!`; `sync-info` `get-current-phase-no-mod`, `get-timeframe-offset!`, `sync-now!` | `engine/util/sync-info.gc` | The phase of every cycle (shifted to the host's on a guest). |
| `com-airlock` `want-cross-airlock?` | `engine/common-obs/airlock.gc` | Doors open for remote players too. |
| `scene-player-init` | `engine/scene/scene.gc` | Every cutscene starts there. |
| `level` `deactivate` | `engine/level/level.gc` | Drop what refers to a leaving level first; a distant player's level gives its host's memory back. |
| `load-state` `update!` | `engine/level/level.gc` | A distant player's level and its borrow levels among the levels to load, not displayed; the sandbox while they link. |

Every wrapper only notes the call when a session is running, for a process the others draw.

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
   finishes teleports, then `mp-world-update`: picks this player's role, sends its objects and
   their poses, emitters and sounds, keeps its NPCs asleep when it is not an authority (awake
   otherwise), keeps one `mp-replica` per object received, and stops the copies' lasting sounds
   their owner dropped.

### 4.2 Puppets

An `mp-puppet` is drawn wherever its player stands, even in a level not loaded here. It uses
Jak's model in the remote player's outfit (sent in the character byte, picked
like `target-skel-group` does, the local outfit when that model is not loaded here) or Daxter's
(`skel-sidekick`), shows the body segments the remote player shows, and takes the remote player's
exact pose (1.4). It also plays the remote player's animation by index, for the animation's
effects and for the time before the first pose: a clip not loaded here falls back to the idle
stance, or to the crouch for a driver or passenger. Its `mp-hurtbox` child sits in the actor hash,
where the player's attacks look. A model change (character, outfit) respawns the puppet.

### 4.3 Shared vehicles

- **Drawing:** the vehicle a player drives, and its wheels, are that player's props: the others
  draw replicas of them.
- **Steal (press triangle):** the thief stands where the vehicle is and asks the target for a new
  vehicle of that type (`'change-mode 'pilot #f type #t`, stock spawn path). Only once that worked
  does it send `steal`: the driver gets out and its vehicle is removed half a second later. Only
  the player vehicles: city bikes and cars (types 0-6) and Spargus buggies (12-19).
- **NPC vehicles:** a player presses triangle next to the replica of another authority's NPC
  vehicle, gets the same vehicle, and that authority removes its own (`vehicle-taken`).
- **Ride (hold triangle 0.5 s):** with a second seat no other player uses, from the ground
  (standing or crouched). An invisible `mp-seat` follows the driver's seat 1 and the player rides
  it with the stock `target-grab-ride` state. Each machine draws the passenger in the driver's seat
  as it sees that vehicle.
- **Collision:** a vehicle's copy is a solid sphere centered low (0.5 m under its root, 2.2 m): a
  sphere standing above the ground makes a player next to it crouch under its underside. A player's
  hurtbox is off while it drives or rides, else it would sit inside the vehicle and stop it.

### 4.4 Sleeping NPCs

While it is not an authority (1.4), a player turns its ambient traffic off
(`set-setting! 'traffic-spawn #f`, held by the manager) and, four times a second, puts every own
NPC to sleep: `sleep` process mask, not drawn, collision off. Everything is restored when it
becomes an authority or the session ends.

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

`python scripts/mp-test/compare_poses.py` compares, after a `pose-all` run, the bones the host
logged for its objects with those of their copies on the guest at the same moment: both log every
frame, stamped with the host's clock (`pc-mp-clock`), and each copy sample is compared with the
host's bones interpolated at its stamp. The host reads its bones from the manager, where the batch
reads them (the test hook runs after the frame's joint math, one frame later); a copy is stamped
with the time of the pose laid on it.

`python scripts/mp-test/check_results.py [scenario ...]` then checks what each scenario is meant
to show, one PASS or FAIL line per check with the measured values (both instances ran to the end,
no pose left out, the hit and its reaction, the color marker, the copies drawn, the poses within a
few millimeters, the platforms within 10 cm, the cycles within a frame...). It exits with 1 when a
check fails.

The runner starts one game per script of `scripts/mp-test/scenarios/<scenario>/`, each with its
own `--config-path` folder (a copy of the user's saves and settings, small window, side by side,
controller ignored when unfocused, vsync off) and the `MP_TEST_SCRIPT` environment variable. Real
controllers are left out (`SDL_JOYSTICK_<backend>=0`): the scripts drive pad 0 themselves, and a
controller gone to sleep once hung every instance in SDL's controller detection, halfway through a
round of scenarios. The debug boot
loads save slot 1, so both games start in game at the same place. When the scripts are done, it
stops them and writes `out/mp-test/<scenario>/summary.txt` (every `[mp-test]` and `[multiplayer]`
line) and `screenshots/<instance>-<name>.png` (rendered by the game itself).

A script line is `<seconds> <command>`:

| Command | Effect |
|---|---|
| `enable`, `name <w>`, `color <n>`, `character <n>` | Mod settings. |
| `host <ip:port>`, `join <ip:port>`, `leave` | Session. |
| `press <button> <ms>`, `stick <lx> <ly> <ms>` | Force pad 0 (button names in PS2 order, at full pressure: X drives a buggy; stick 0-255, 128 centered). |
| `goto-player <slot> [x z]`, `face-player <slot>` | Stand next to a remote player (or x and z meters from it), turn to it. |
| `goto-npc <skeleton prefix>`, `goto-actor <n>` | Stand next to a replicated NPC, facing it. |
| `goto-crate`, `log-crates` | Stand at the nearest crate; count the live crates. |
| `gun`, `vehicle <type>`, `cheat <name>`, `zone <n>`, `continue <name>` | Gameplay helpers. |
| `screenshot <name>`, `log` | Evidence: the log shows the session, the local player, the role (authority) and the own NPCs awake and asleep, every remote player and puppet, the objects sent, received and drawn, the poses sent and applied, the emitters and sounds sent and replayed, the effects and shots sent and replayed, the hits taken from players with the stock reaction seen after them (`target-hit`) and how far the last one pushed. |
| `log-look <slot>` | How player `slot`'s color shows here: its model's color multiplier and emissive color (1 and 0: not tinted), its minimap icon's color, the color markers drawn before names. |
| `log-copies <slot>` | Every object of player `slot` received here (`log` lists 8 at most): its skeleton and id, whether its owner hides it (its copy is hidden too), whether its copy is drawn, and whether its skeleton and art group are in levels loaded here. |
| `log-pose [slot]` | Four bones of player `slot`'s puppet relative to its root, or of the own model without a slot: the owner's line and the other machine's line compare the pose with its copy. |
| `log-part <index>` | Where entry `index` of `*part-id-table*` lives (which level heap). |
| `log-me` | One line: own position, speed and state (fine-grained probes). |
| `log-pose-own <npc\|daxter\|gun\|vehicle\|wheel> [frames]`, `log-pose-copy <slot> <skeleton name\|*>` | Four bones of one of our objects (the NPC nearest to player 1, Daxter, the gun, the vehicle driven, its first wheel), printed by the manager on every frame for `frames` frames, and of the copies of player `slot`'s objects with that skeleton (`*`: its NPC nearest to us), with the poses laid on each copy. Stamps in ms with a tenth. |
| `log-tasks`, `close-task [n]` | Story progress: closed nodes, nodes closed after another player, the open ones; close the first open node, n times. |
| `log-sync` | Phase of a 4-second test cycle computed in the manager's context, stamped with the host's clock: a guest's must follow the host's. |
| `goto-float`, `log-float` | Stand on the nearest floating platform (`rigid-body-platform`, volcano); print the one nearest to player 0 (guest) or to us (host): height, tilt, weight messages sent, states followed, stamped with the host's clock. |
| `goto-door`, `log-door`, `goto-elevator`, `log-elevator` | Stand in front of the nearest door or on the nearest elevator; print the door or elevator nearest to player 0 (on a guest) or to us (on the host), its state and height, and the elevator trips sent and shown. |
| `scene <name>`, `log-levels` | Start a cutscene as a mission would; print the levels loaded (`far #t`: loaded for a distant player), the distant player's level, its borrow levels, its host, the sandbox entries put back, and the cutscenes sent, shown and playing. |
| `log-shaders` | Walk the texture shader lists with bounds: links counted and broken (0 when every level is below 256 MB), and each level's heap. |
| `far-levels <0\|1>` | Load distant players' levels (default 1); 0 for a counter-test. |
| `synth-skels <0\|1>` | Copies use the skeleton-groups rebuilt from their sender's description, even when the code defining ours is loaded (the path of a group whose code is in no level here). |
| `log-art-kinds` | Every kind of object (skeleton) another player announced: its group (ours or rebuilt), its art group loaded or loadable alone, drawable here or not. |
| `announce-all-kinds`, `log-kinds-coverage` | Describe to the others every kind of object of our place (every skeleton-group whose art is loaded here); count the kinds another player described, complete, with their art group file here. |
| `ride-shown-elevator` | Once this machine started an elevator trip because player 0 did, stand beside player 0 on it (once). |

Scenarios and what they verified (2026-10-14, Spargus, Haven City and the volcano, every one run again after the last change; `check_results.py`: 159 checks passed, 0 failed, 21 scenarios):

| Scenario | Checks | Result |
|---|---|---|
| `smoke` | Host, join, puppets, names, health bars | Both see each other; poses, sounds sent and applied both ways. |
| `pvp` | A guest's punch on the host; the color marker | The host takes 1 hit and goes through the stock hit reaction (`target-hit`, counted by `log`), pushed back 3.04 m, health 8 to 5, seen at 5 by the guest. `log-look` on both machines: the other player's model not tinted (color multiplier 1, emissive 0), its minimap icon and the marker before its name in its color (about 1000 markers drawn). |
| `pvp-probe` | The attacker's position and state every 0.1 s around the punch | The punch lunge stops on the host's hurtbox. |
| `npc` | Host NPCs on the guest | 13 replicas drawn, the guest's own NPCs (8 to 10 around it) asleep. |
| `npc-hit` | A guest's punch on a host NPC | The host applies both blows to its citizen (`player 1 hit ... punch`); in an earlier run the citizen struck back and the guest lost 2 health. |
| `props`, `combat` | Daxter, gun, shots, impacts, emitters | Daxter on the shoulder and the gun in the aiming pose on the remote puppet; 3 shots and 2 impacts replayed; the gun's emitters replayed (122). |
| `vehicle` | A driven buggy, then stolen | Body and wheels replicated, its emitters (289 shown) and engine sound (932 updates played) on the guest; the driver is ejected and sees the thief's buggy. |
| `ride` | Passenger seat | The guest, standing 3 m beside the host's two-seat buggy, rides in it; the host drives 27 m with it. |
| `crate` | A stack of crates broken by the host | The host breaks 4 (34 to 30 around it), the guest goes from 48 to 44. |
| `pose` | The driver's pose (pilot animation), its dust and engine | The host's bones and their copy on the guest match within 5 mm (`log-pose`, 65 bones); 378 emitters shown and 1186 sounds played on the guest. |
| `pose-all` | Every object type at the same moment: the player's model, NPC, Daxter, gun, buggy, wheel | `compare_poses.py`, about 60 samples each (122 for the player, on foot then driving), three runs: the player's model median 0.7 to 0.8 mm, largest 1.6 to 1.8 mm; a running NPC whose bones move 37 to 54 mm per frame, median 0.8 to 1.2 mm, largest 1.2 to 2.7 mm; Daxter 0.6 mm (1.0 mm); gun 0.2 mm; buggy 0.5 mm (1.1 mm); wheel 0.0 mm: the 1 mm steps of the wire format. Before the 0.1 ms clock, 6.7 mm at most; before every frame of the owner was logged, one sample at 18.9 mm (a 43 ms frame left a gap in the owner's samples, a measurement artifact). Two joints of the driver once read 1.5 m off: their matrix had `w` 0.9992 and the log read the translation alone, at 9.2 million units from the origin; the pose sent was right (traced in the runtime), the log now takes `w` in. |
| `progress` | Story progress | The node the host closed before the guest joined reaches it on join, the one closed later live: 3 closed nodes and the same open ones on both. |
| `far` | Authority, distant players and their levels | Next to the host the guest shows its NPCs (10 own asleep); 570 m away it simulates its own (37 awake, 6 sent to the host) and the host draws it there. The host loads the guest's `wascityb` in the free top of `wascitya`'s heap, not displayed (`far #t`, 2 entries put back by the sandbox); the guest loads the host's `wascitya` in `waswide`'s heap (10 put back) and, back next to the host, unloads that copy and loads `wascitya` as its own. No crash at any of these unloads; `log-shaders`: 0 broken links. The error "object #x147d21 is not a valid object" printed when the guest's `wascityb` unloads is the stock game's: it appears with `far-levels 0` too. |
| `far-city`, `far` (kinds) | Every kind of object of a place, not only those passing by | The guest in Haven describes all 24 kinds whose art its levels hold (`announce-all-kinds`): the host has 24 described, complete, with their art file, all drawable. The host in Spargus describes its 65: the guest, far away, can draw all 65. (Skeleton-groups whose art no level ships, such as `mhcity-grunt-egg-a`, are no kind of object a player can see.) |
| `far-city-synth` | Objects whose code is in no level here | The same as `far-city`, the host building every copy from the skeleton-groups the guest describes (`synth-skels 1`): 9 groups rebuilt, every kind the guest announced drawable (`log-art-kinds`), every object it draws drawn. |
| `far-city` | A distant player in another region | The guest goes to Haven City's port while the host stays in Spargus. The host loads `ctywide` in `wascitya`'s heap (2226 entries put back), then the guest's borrow levels `ctypesa`, `ctycara`, `ctycarb` in its borrow heaps, and draws the guest's guards, cars and bikes from them (1 to 6 depending on who passes by; `log-copies 1` shows each object, drawn or not, and whether its skeleton and art group are loaded here). Every object the guest draws is drawn here, the parked traffic the guest hides is hidden here too: the boats (`ctyport`) and citizens (`ctypepa`), whose levels do not fit, from their art groups loaded alone (`far art loaded 2, failed 0`) with the renderer keeping `ctyport` and `ctypepa`. Back in Spargus, the four levels unload cleanly. |
| `progress-late` | A guest 40 missions behind joins | It closes the 40 nodes on joining (1 to 42 closed, like the host), gets their rewards (health 8 to 10), new mission markers on its minimap, and plays on. |
| `world-objects` | Cycles, doors, elevators | Test cycle: the guest's phase is the host's within one frame (16 ms; 39 ms in the last run, where one guest frame came 273 ms late). The host stands at a door 200 m from the guest: the guest's copy of that door opens (it stays closed with the door wrapper off, the counter-test). The host rides the palace elevator: the trip starts on the host ("sent 1") and on the guest ("shown 1", "sent 0"); the guest then jumps beside the host on the elevator its own machine moves and rides up with it (screenshots: the guest in the street, then in the shaft). |
| `scene` | Cutscenes | The host starts a Spargus cutscene: it plays on both machines (screenshots of the same shot), both players placed in it. |
| `float` | Physics objects | The volcano's lava platforms, 270 m from both players: at the same stamp, the guest's are within 7 cm in height and 0.002 in tilt of the host's (they were 2.25 s out of phase before). |
| `float-weight` | A player standing on a remote-driven platform | The guest stands on a lava platform its machine moves where the host's is: carried 18 m in 2 s with it; each machine sends the other player's weight to its platform (counters); both slide into the lava as the platform sinks, as in the stock game. |

| | |
|---|---|
| ![Name, color square and health bar](multiplayer-screenshots/pvp-nameplate.jpg) | ![The host's NPCs on the guest](multiplayer-screenshots/npc-replicas.jpg) |
| ![Remote player with gun and Daxter](multiplayer-screenshots/remote-gun-daxter.jpg) | ![A guest's punch on a host NPC](multiplayer-screenshots/npc-hit.jpg) |
| ![Steal prompt next to the host's buggy](multiplayer-screenshots/vehicle-prompt.jpg) | ![The host after the steal](multiplayer-screenshots/vehicle-stolen.jpg) |
| ![Riding as passenger](multiplayer-screenshots/passenger.jpg) | ![The host aiming its gun, as the guest sees it](multiplayer-screenshots/remote-aiming.jpg) |
| ![A guest far from the host, in its own world](multiplayer-screenshots/far-own-world.jpg) | ![The host's view of the guest, blue marker, model not tinted](multiplayer-screenshots/pvp-host-view.jpg) |
| ![The host firing, as the guest sees it](multiplayer-screenshots/remote-firing.jpg) | ![Both players at the volcano](multiplayer-screenshots/volcano-together.jpg) |
| ![A cutscene on the host](multiplayer-screenshots/scene-host.jpg) | ![The same cutscene on the guest](multiplayer-screenshots/scene-guest.jpg) |
| ![The guest in the street before the host starts the elevator](multiplayer-screenshots/elevator-guest-before.jpg) | ![The guest carried up the shaft by the elevator the host started](multiplayer-screenshots/elevator-guest-riding.jpg) |

The screenshots above come from the last round of scenarios (`out/mp-test/<scenario>/screenshots`),
except `far-own-world`, `npc-*`, `remote-*` (aiming, gun and Daxter) and `vehicle-*`.

Over the Internet, the host forwards TCP port 7777 (or the port chosen) and guests use the host's
public IP.

## 6. Known limits

- A distant player's place (1.6): one place at a time (one far level, its borrow levels, art
  groups loaded alone, skeleton-groups rebuilt). Our own place keeps a level slot, and any load
  of ours lets the distant levels go first. Their sounds stay silent (sound banks follow our
  place). Art groups are read from `out/jak3/obj`, which a compiled install has.
- Sizes, above what the game spawns (at most 20 objects per batch were seen in the scenarios):
  128 objects per batch and 256 copies drawn, 8 particle groups per copy, 32 emitters and 32
  sounds per object and batch. Sounds of a sound bank the viewer has not loaded stay silent.
- Bandwidth to each other player: about 200 KB/s for a player's model, Daxter and gun at 60
  frames a second, plus 36 KB/s per NPC within 40 m of that player and 9 KB/s per NPC farther
  (15 Hz); with a crowd of 10 NPCs nearby and 30 farther, about 0.85 MB/s.
- Text fields use the D-pad (keyboard arrows work too).
- TCP head-of-line blocking can stall a player on a lossy link (interpolation hides short stalls).

## 7. Change log

- **2026-10-14:** every kind of object of a place checked, not only those passing by: test
  commands `announce-all-kinds` and `log-kinds-coverage` (`pc-mp-art-file-exists`), art and
  skeleton tables raised to 160 kinds (24 of 24 in Haven, 65 of 65 in Spargus). Test command
  `ride-shown-elevator`: the guest rides the elevator the host started, with screenshots. The
  cycles check allows a late frame's lateness.
- **2026-10-14:** skeleton-groups described by their sender (`skel-def` event) and rebuilt when
  their code is in no level here: a copy needs no code. Scenario `far-city-synth`, test commands
  `synth-skels`, `log-art-kinds`; every kind a distant player announced is checked drawable.
- **2026-10-14:** art groups loaded alone for a distant player's objects whose levels do not fit
  (`art-level` event, `loado` into the far level's memory inside the sandbox, the renderer keeping
  their levels' data, `pc-mp-render-level`, 8 more renderer levels for Jak 3): every object a
  distant player draws is drawn. Test runner: vsync off, real controllers left out.
- **2026-10-14:** every scenario checked by `scripts/mp-test/check_results.py` (one PASS or FAIL
  per feature). Test commands `log-look` (the color marker, model not tinted), `log-copies`
  (every object received, drawn, art loaded here), `log` counts the hits taken from players and
  the stock reaction after them; the player's own model compared at the same moment in
  `pose-all` (its copy stamps the poses laid on it). Bone offsets in the test logs take the
  matrix's `w` in.
- **2026-10-14:** a distant player's place is loaded, not displayed: its level (or the master level
  of it) in the free top of one of our levels' heap, then its borrow levels, with a sandbox that
  keeps its code from taking the names of ours (`pc-mp-sandbox-*`), `levels` event, hook in
  `load-begin`. Level heap kept below 256 MB (`DEBUG_LEVEL_HEAP_MULT` 12): at 15 the top banks broke
  the texture shader lists. Batch clocks in tenths of a millisecond (protocol 4) and the owner's
  bones logged on every frame: a running NPC's copy within 1.6 mm. Test commands `log-shaders`,
  `far-levels`, `log-pose-own ... frames`; scenario `far-city`.
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
- **2026-10-14:** everything synchronized. Full poses (every bone, `POSES` message, protocol 2) for
  players, props and the nearest NPCs, laid on the copies by a joint-control postbind function:
  blends, streamed animations, joint mods and ragdolls now look the same everywhere. Continuous
  particle emitters, direct particle launches and sounds of players, props and NPCs replayed on
  the copies. NPC authority by region: a guest far from every authority simulates its own NPCs and
  shares them with the players near it. Body segments of the remote player. Test commands
  `log-pose`, `log-me`, `log-part`, `goto-player` with x and z offsets; face commands now set the
  player's heading (the punch lunged the other way, which looked like a bounce). Scenarios `pose`,
  `far`, `pvp-probe`.
- **2026-10-14:** every NPC sent gets its exact pose (no budget), with full joint math while sent.
  Shared story progress (task nodes, on join and live, host setting). Remote players in their own
  outfit. Test commands `log-pose-own`, `log-pose-copy`, `log-tasks`, `close-task`; scenarios
  `pose-all`, `progress`.
- **2026-10-14:** poses sent every frame for the player, its props and the NPCs near another
  player (the batch still carries every object and the effects at 15 Hz; protocol 3): a running
  NPC's copy within about 2 mm of the owner (median). Remote players drawn wherever they stand. The level cleanup
  also runs for a level only loaded (a crash at its unload).
- **2026-10-14:** physics objects of the level follow their authority (named by entity and rank,
  rigid body set every frame), floating platforms take remote players' weight; NPC bones
  computed on the spot when stale instead of leaving the pose out. Scenarios `float`,
  `float-weight`; `log-levels` in `smoke` and `far`.
- **2026-10-14:** world objects kept alike: cycles follow the host's clocks (sync-info), doors open
  for remote players, elevator trips run on every machine; cutscenes shared within a region.
  Copies and part-trackers of a leaving level are dropped before it goes (two crashes at a level
  unload in testing). Sizes raised (128 objects per batch, 256 copies, 8 particle groups per
  copy). Level copying tried and dropped: an extra level crashes the game without a free bank.
  Pose comparison at the same moment (stamps from `pc-mp-clock`). Scenarios `world-objects`,
  `scene`, `progress-late`.
- **2026-10-14:** a player standing next to another player's vehicle crouched under the underside
  of its collision sphere and could not get in as passenger: the sphere is now centered low, and
  getting in works crouched too. A passenger's hurtbox stopped the driver's vehicle: hurtboxes are
  off in vehicles. The test pad now presses buttons at full pressure (a buggy's throttle reads X's
  pressure; before, the test buggies were only pushed by the passenger's hurtbox).
