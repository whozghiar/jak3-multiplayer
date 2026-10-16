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
| Runtime (C++) | `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, both sticks). |
| Test | `test/test_kmultiplayer.cpp` | Interpolation and a loopback host + client session (join, relay, event, objects, a pose with an emitter and a sound, ban, refusal reason). |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer.gc` | Local state, remote puppets (in their player's outfit) and their hurtboxes, passenger seat, shared enemy deaths and story progress, hit forwarding, player hits, Daxter mode, cheats state, teleports, overlay, per-frame hook. |
| GAME | `.../multiplayer-world.gc` | The shared world: who simulates which NPCs, NPC and prop replication, full poses, particle emitters and sounds, sleeping NPCs, hits on replicas, NPC vehicles, effects, projectiles, world objects (cycles, doors, elevators), cutscenes, levels leaving, crates and pickups. |
| GAME | `.../multiplayer-menu.gc` | START > Options > Multiplayer and its administration page, the D-pad text field, the Mods menu entries. |
| GAME | `.../multiplayer-test.gc` | Automated test commands (inert without `MP_TEST_SCRIPT`). |
| Tool | `scripts/mp-test/run_mp_test.py`, `scenarios/`, `compare_poses.py` | Runs several game instances on one PC with a script each, gathers logs and screenshots; compares an owner's bones with its copies'. |
| Tool | `scripts/mp-test/local_pair.py` | Starts two instances on one PC to play by hand, one with the controller, the other with the keyboard. |

### 1.1 Network

- **Topology:** star. The host is slot 0 and listens on the port of its address field (default
  7777); guests get slots 1 to 7. Every message from a guest goes to the host, which relays it.
- **Transport:** TCP with `TCP_NODELAY`, non-blocking sockets pumped once per frame from GOAL
  (`pc-mp-update`), no thread. A frame is `[u16 length][u8 type][payload]`; malformed or
  oversized frames drop the peer. Message types: `HELLO` (protocol version, now 5), `WELCOME`
  (slot), `REJECT` (reason, then close), `STATE`, `LEAVE`, `EVENT`, `ACTORS`, `POSES`, `BLOB`
  (`[u8 from][u8 to][u32 total][u32 offset][bytes]`, 15000 bytes a frame, relayed by the host:
  the host's world for a guest that joins, 1.7).
- **Player state:** `mp-player-state` (192 bytes: position, orientation, velocity, animation,
  health, flags, character and outfit, body segments, driven vehicle, passenger seat, name, color,
  level, continue point) every frame (60 Hz): a driver's puppet blended between states 33 ms
  apart drifted on its vehicle, which moves every frame.
- **Objects:** `mp-actor-state` batches (80 bytes each, at most 64 per batch) at 15 Hz from every
  player: `[u8 source][u32 source time][u16 count][states]`; the states of what a player owns
  (props, the vehicle driven and its wheels, its shots) go again every frame between batches.
  Ids carry the owner's slot in the top byte and the owner's process pid below. The source time
  of object batches and poses is in tenths of a millisecond: the receiver's clock estimate and
  the blend between poses one frame apart lose up to a millisecond otherwise (about 3.5 mm on a
  running citizen).
- **Interpolation:** everything another player sends is drawn 100 ms in the past, at the same
  moment for its puppet and its objects (one clock estimate per sender: the fastest delivery of
  its states and batches alike). An object keeps its last 500 ms of states; a late one goes on
  along its last velocity for 150 ms, then holds. A shot gone 250 ms without a state is gone.
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
  name, address, character, color, enabled, mode (the game mode when hosting, 1.7).

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
| A player's shots | the shooter's machine | an object drawn from its state: its model, trail (emitters) and sounds; none of its code runs there (1.4) |
| NPCs' projectiles | the authority | a copy fired from the replica, which may hit that machine's player |
| Enemy deaths, broken crates, taken pickups | where they happen | the same entity dying or removed |
| Story progress: closed task nodes (missions done, with their rewards) | where they close | the same nodes closed; a player who joins gets the others' and gives its own (1.5) |
| Time of day | the host | followed by the guests |
| World objects that carry or block the player (lifts, platforms, doors) | every machine, for its own player (a lift must answer the player standing on it) | kept alike (1.6): cycles follow the host's clocks, doors open for any player in front of them, an elevator trip started on one machine runs on all |
| Physics objects of the level (floating platforms, objects on physics) | the region's authority, with the weight of every player on them | each machine's own object of that entity, moved every frame where the authority has it, still carrying its player (1.6) |
| Levels | every machine, around its own player | players in one place load the same levels; another place's levels are never loaded here, the art of its objects within 400 m is (1.6) |
| Elevators | the machine whose player started the trip | the same elevator, placed where it is every frame, its scripts set aside unless the local player rides it (1.6) |
| The game itself (progress, equipment, place, entity states) | the host | a guest that joins loads the host's game as a save, without its memory card, and saves nothing while there (1.7) |
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
| A player's area weapon (wave concussor, mass inverter, arc wielder, needle lazer) on another player | the attacker's machine: the stock weapon finds that player's hurtbox (4.2) | the victim (`pvp` event); the other machines draw a copy that hurts nobody (4.5) |
| A player's punch or shot on an NPC replica | the attacker's machine | the host, on the real NPC, from the attacker's puppet (`actor-hit`) |
| An NPC's melee on a player's puppet | the host | that player (`hit` event) |
| An NPC's shot | every machine, on its own player, with its copy of the shot | locally; puppets ignore shots |

A player's shots are drawn elsewhere from their state only; the shooter's machine sends their
hits. A replica is a `process-focusable` of its original's kind: process mask (enemy, guard,
civilian, crate, vehicle) and search-info flags (attackable, priority...) come with its state, so
auto-aim, homing shots and blasts (gyro burster, peace maker, wave concussor), which only consider
focusables of those kinds, pick it as they would the original. A blow keeps its attack id: the
owner gives each remote attack id one attack id of its own (`mp-local-attack-id`), so a zap
touching every part of a vehicle for several frames counts once, as stock code counts it.

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
- **One-shot effects** (`part-tracker-init-base`) go with what they belong to: the player (its
  puppet elsewhere) or an object sent (its copy), and the joint they follow (`effect` event, iarg
  2 and 3); the copy of the effect plays on that puppet or copy, the same joint. An effect of
  nothing sent, or of a shot (its impact: the shot's copy goes 250 ms after the shot), plays in
  the world. A particle's callback runs on the part-tracker that replays it:
  the groups whose callbacks read what the tracker has not (a collide-shape root, joints, a given
  type) or read the local player (`*target*`'s gun or joints) are never replayed
  (`*mp-unsafe-part-funcs*`), and `board-zap-track` only with a parent that has joints. The
  hoverboard's zap, replayed under `*entity-pool*`, crashed the host in `board-zap-track`
  (`vector<-cspace!` on a process without joints).
- **The gun's laser sight** is particles 207-212 launched by `draw-beam`, which takes their
  direction from globals of the machine that draws them: they are not replayed. The gun's state
  carries a `laser` flag and the owner's sight, every frame (`mp-pack-laser`, after the skeleton
  name: where it starts from the gun's root in mm, its direction in 1/32767): the aim, auto-aim
  included, not the barrel. The copy of the gun draws the laser itself (`mp-draw-laser`, as
  `gun-info-method-9` does), from there along that direction up to what it meets, passing through
  the owner's puppet and hurtbox as the owner's passes through its player. In motion (`laser`):
  its start within 0.1 cm of the owner's (median), its direction within 0.27 degree (95 %); from
  the barrel before, 14 cm and 8.5 degrees, and a sixth of the samples ended behind the gun on the
  owner's hurtbox.
- **Shots.** A player's shots in flight are objects of its batches (`mp-collect-shot`, flag
  `shot`): with their model when they have one, else their place alone (an `mp-fx-copy` playing
  their trail and sounds). Their code never runs elsewhere: a shot's code reads the local player
  (the peace maker charged with the other machine's R1, vanished unless its player held a gun and
  homed on its target; the gyro burster drained its ammo).
- **The hoverboard** is one of the player's props, like Daxter and the gun.
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
- **Nothing vanishes under a player's eyes.** The game removes what its player does not see:
  traffic off screen for 10 s goes back to its pool, a citizen off screen goes inactive
  (`vehicle` post, `citizen` `enemy-common-post`), both hidden (`no-draw`). The authority marks
  the NPCs it sends within 60 m of another player as on screen (`mp-keep-seen-npcs`). When the
  roles change (a guest meets or leaves the host), what the player sees would still change at once:
  its own NPCs put to sleep, the copies of NPCs their owner hides or stops sending removed. So
  nothing drawn on our screen within 60 m (`mp-in-our-view?`) goes at once: an own NPC, still
  alive, sleeps once out of view (60 s at most after the switch); a copy its owner hides or no
  longer sends stays where it is, intangible, 3 s (a late state brings it back), then fades out
  over 1 s (`mp-replica-fade`: `force-fade`, as debris fade), unless our player looks away first
  (`linger-since`). Only a copy whose art leaves with its level goes at once. Measured on the
  guest (`log-vanished`): what was on its screen within 40 m and vanished alive at once
  (`vanished-near`), and what faded out instead (`faded-near`).

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
  boot. The host sends its clocks with the world settings every 2 s (`*mp-synced-clocks*`), and
  the time it read them (`pc-mp-clock`); on a guest, the `sync-info` methods (`get-norm!` of the
  three cycle types, `get-current-phase-no-mod`, `get-timeframe-offset!`, `sync-now!`) see the
  object's offset shifted by the host's lead at the time the host's copies are drawn here
  (`mp-apply-clocks`: the host's clocks moved on by the host time from their reading to
  `pc-mp-clock` of the host, which carries the network's delay), put back after the call. The
  cycles show the host's phase as its copies are drawn, whatever the delay (a fixed 100 ms before:
  30 to 34 ms off under 50 ms of delay and 30 of jitter, 3 ms now), and no timer of the game moves.
- **Doors.** `com-airlock` decides to open in `want-cross-airlock?`, for the local player only. The
  wrapper also opens it for a remote player standing where the local one would (in front, within
  its distances and heights, the door allowed to open by its script), so a door is open on every
  machine when any player is at it.
- **Elevators.** One elevator for everyone. An elevator near a player that started a trip here
  (`running`) is announced (`elevator` event: entity, target stop), and its place goes in every
  batch while it runs (a world entry: stops, and how far along in the interpolated animation
  frame). The others start the same trip (`'move-to`, its `actor-pause` cleared first: waiting
  out of view, it would never run it) and place theirs where it is every frame
  (`mp-drive-elevator`: `waiting-to-ascend` cleared, `path-pos` and `basetrans` set), so its
  platform and whoever stands on it follow; it arrives when the states stop. Its scripts
  (`on-activate`, `on-deactivate`...) load levels and change the continue point and the camera,
  some without checking that the player rides it (Spargus' palace elevator runs `(want-vis
  'waspala) (want-continue "waspala-start")` on arrival): on a machine whose player is not on it
  they are set aside for the trip (`mp-elevator-mute`), and given back once it waits at a stop or
  as soon as that player rides it. A trip started on another player's word is not announced
  back.
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
  small-edge bank in the level-group heap", seen in a test). The levels of another place are never
  loaded here: a level's code links into the running game and its top-level code resets our
  globals. Haven's `traffic-manager.gc` runs `(define *traffic-engine* #f)`: loading `ctywide`
  for a distant player in Spargus left Spargus' traffic without its engine, and a guest crashed at
  the next citizen it spawned (a version of the mod loaded such levels, with a sandbox that put
  back the names they took, which cannot catch a `#f`). The objects of another place are drawn
  from their art alone ("Art of distant players' objects" in `multiplayer-world.gc`):
  - *Which objects:* those within 400 m of our player (`MP_ART_RANGE`): farther, nobody here
    sees them; a distant player's place is usually kilometers away.
  - *No code needed:* a copy runs none of its object's code; it only needs the skeleton-group
    (the data a `defskelgroup` fills: art group name, joint geometry and animation, levels of
    detail, bounds) and the art group. Each player sends the description of every skeleton-group
    it sends (`skel-def` event, three parts); when the code that defines a group is in no level
    here, the group is rebuilt from it (`mp-apply-skel-def`, a pool of 160). `far-city-synth`
    builds every copy that way.
  - *Art loaded alone:* the art group of an object no level of ours holds is loaded by name
    (`loado`, `out/jak3/obj/<name>-ag.go`: data, no code) into the art heap, and listed in the
    art directory of that heap's host level, where `initialize-skeleton` looks for it; a sandbox
    in the runtime (`pc-mp-sandbox-*`) puts back the particle, lightning and texture page entries
    the load changed (the load runs in one call: every change is its own). The renderer keeps the
    data of the level its model comes from (`pc-mp-render-level`, `fr3`, its cap raised by 8 for
    Jak 3), which the sender names (`art-level` event).
  - *The art heap:* 32 MB (8 MB at least) carved from the free top of one of our active levels
    (its host), below 256 MB, the host's `top` and `top-base` lowered to it, as the game does for
    borrow heaps (an address belongs to one level only, art login checks it). It goes, with the
    copies drawn from it, its texture links and pages, its art in the host's directory and the
    renderer data kept for it, when its host level leaves or the session ends.
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

### 1.7 The host's world, and the game modes

- **A guest plays in the host's world.** Once connected, a guest asks the host for its world
  (`world-request` event). The host writes its game into a save in memory (`save-game`, the same
  tags as a memory card save: progress, equipment, items, entity states, its checkpoint) and
  sends it (`BLOB`, about 40 to 120 KB). The guest loads it as the stock Load Game does
  (`initialize! *game-info* 'game save`): a loading screen, the host's checkpoint, the host's
  progress and equipment. Its own settings stay its own: the save's language, screen, subtitle,
  camera and hint tags are turned into tags `load-game` skips (`mp-save-keep-own-settings`).
  Once in game it goes next to the host (`*mp-request-teleport*`). While joining (title screen,
  loading screen) its state carries `hidden`. This works from the title screen too: Title >
  Options > Multiplayer > Join the server.
- **No save in another player's world.** `auto-save-command` (every save: the pause menu's,
  checkpoints and missions' auto-saves, a new game's first save) is wrapped: while the player
  plays in another player's world (`*mp-join*` `foreign?`), no save is written, and the menu that
  asked is told it failed (`'notify 'error`). Loading its own save (`'restore`) or being back at
  the title screen ends it. The guest's memory card is never read or written for the session.
- **Progress on join.** Only the host sends its closed task nodes to a player who joins; a guest's
  own progress is not merged into the host's world.
- **Game mode** (host's choice, Options > Multiplayer > **Game mode**, saved as `mode` in the
  config, sent with the world settings as the `coop` flag):
  - *Free roam:* every player goes where it wants (1.4: a player far from every authority
    simulates its own NPCs).
  - *Co-op:* the players share one place. A guest never simulates NPCs (`mp-update-authority`:
    the host's are shown wherever it goes). A guest farther than 150 m from the host for 3 s comes
    back next to it. Everyone follows the host's weather (also in free roam when the host shares
    its time of day): the host sends its clouds, fog and special weather with the world settings,
    a guest sets them as its `*mood-control*` override, which stops its own random weather. A
    story step only closes once every player stands within 25 m of where it closed: the `open!`
    wrapper holds a closure made by our player's doing while another player is away
    (`mp-coop-hold?`; not a closure received from another player, nor during a cutscene or a
    load), tells the others where to come (`coop-wait` event: "<name> waits for you here", with
    the distance), shows "Waiting for the other players to come here", and closes the held nodes
    once they are all there (`mp-update-coop`); shared progress then closes them everywhere.

### 1.8 Finding a crash

An access violation in GOAL code ends the game with no trace in the log. The runtime prints the
GOAL function holding the faulting instruction and the GOAL return addresses found on the stack
(`report_goal_crash`, a vectored exception handler in `kmultiplayer.cpp`):
`[multiplayer] crash: access violation at #x... in GOAL <function> +#x...`, then `from GOAL ...`
lines. It found the hoverboard zap's crash (`vector<-cspace!` from `board-zap-track`), the
copy of a destroyed vehicle playing a character's death effect (a call to address 0 from
`mp-replica-update`) and the test log's own (`mp-test-log` reading a copy without a model).

The crashes of the test session were reproduced on `multiplayer-v1.0.0` (bf43da5a3, the version
played), its sources put back in the worktree for the run with three test commands and this
report added (`gun [type]`, `board`, `stick-r`), then the same scripts run on the current version
(scenarios `v1-*`, 5.2).

## 2. Requirements and how they are met

| Requirement | Implementation |
|---|---|
| Create or join a server from the start menu | START > Options > Multiplayer: **Host a server**, **Join the server**, **Disconnect**, plus a status line. The ring of the start menu has exactly ten slots (36 degrees each, matching its 3D model), so the page hangs off the Options list, which gets a sixth entry while the mod is enabled. The same page from the title screen: Title > Options > Multiplayer. |
| A guest uses the host's world, not its own save | Section 1.7: the host's game sent as a save in memory and loaded with a loading screen, the guest next to the host, its own save neither read nor written. |
| Free roam and co-op | **Game mode** carousel (host). Section 1.7. |
| IP address with port, nickname | **Server address (ip:port)** and **Player name** text fields (D-pad editing). |
| Jak or Daxter | **Character** carousel. Daxter is the stock playable-Daxter mode (`indax`), made resident in GAME (section 3). |
| Color | **Color** carousel. A marker, not a tint: a small square of the player's color before its name, and its minimap dot in that color (own copies of the stock dot class, `*mp-minimap-nodes*`). |
| Cheats menu per player | Mods menu (L3 + SELECT) > multiplayer: refill ammo, restore health, dark eco, light eco, invincibility, teleport to a zone, teleport to a player. Greyed out for guests when the host disallows cheats. |
| Health bar above players | Name, color square and health bar (green, yellow, red) over every remote player in view. |
| Shared vehicle: steal or sit next to the driver | Next to a remote driver's vehicle: press triangle to steal it, hold to ride as passenger when it has a free seat. A guest can also take the host's NPC vehicles. The prompts are the game's own kind: `print-game-text` with a `<PAD_TRIANGLE>` icon, where the game draws its vehicle prompt (`vehicle-method-144`). See 4.3. |
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
| `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, both sticks). |
| `game/CMakeLists.txt`, `test/CMakeLists.txt` | The new C++ file and its test. |
| `goal_src/jak3/dgos/game.gd` | The four mod files at the end of GAME, `target-indax.o`, `target-indax-hang.o` and `jak-indax+0-ag.go`. |
| `goal_src/jak3/dgos/facc.gd`, `voca.gd`, `halfpipe.gd`, `vin.gd` | The same three objects removed: a copy in a level DGO would relink the indax states into that level's heap, and they would dangle once it unloads. |
| `goal_src/jak3/engine/ui/text-h.gc` | Text ids `#x1500`-`#x1514`, `#x1520`-`#x152c` and `#x1530`-`#x1535` for the start menu labels, the prompts and the game modes. |
| `game/kernel/jak3/kmachine_extras.cpp` | `pc_set_levels` adds the levels kept for a distant player's models (`mp::extra_render_levels`). |
| `game/graphics/pipelines/opengl.cpp` | The renderer keeps 8 more levels for Jak 3 (`fr3_level_count`). |
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
| `level` `deactivate` | `engine/level/level.gc` | Drop what refers to a leaving level first; the art heap goes with its host level. |
| `auto-save-command` | `engine/game/game-save.gc` | No save while in another player's world (1.7). |

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

The hurtbox is a `process-focusable` of kind crate, in the actor hash: its collide shape is made
with `(collide-list-enum usually-hit-by-player)`, while the puppet's own `hit-by-others` shape is
not in it. The area weapons search the actor hash for focusables of given kinds (crate, enemy,
civilian...) and find the hurtbox, which hands every blow to the puppet. It answers no target
search (`get-search-info-flag` returns none: neither the guns' aim nor an enemy picks it) and
gives its sphere's center as its aim point (`get-trans`: the stock method reads a draw-control,
which the hurtbox has not). A blow sent by a mass inverter's spinner (a child of what it lifts)
counts as the player its attack-info names; a blow doing no damage (the lift itself) is no hit.

Dark Jak and Light Jak: the player's state carries, in free bits of its flags, the Dark Jak and
Light Jak blends (`darkjak-interp`, `lightjak-interp`), the light look (no textures, rim lights),
the invisible fade, the scarf and the goggles (`mp-pack-powers`). The puppet applies them as
`target-draw-process` does: blend-shape overrides for the face and claws, the global effects, the
fade. It tints its model purple for Dark Jak, since the game's dark texture swap is one palette
for every model of Jak, and draws the auras on its own body: dark sparks and lightning, Light
Jak's glow (`mp-puppet-apply-powers`). The override array lives in the puppet's heap and
`mp-puppet` relocates it itself, because joint-control's relocate leaves `override` out. Left
behind after a heap compaction, the puppet's writes landed in other processes and crashed the
game when an arc wielder fired, its large heap forcing a compaction.

Cloth: Jak's scarf is a cloth strip drawn apart from the model and rebuilt only while the model
is on screen. A puppet or copy that jumps more than 10 m resets its cloth, and its strips are
emptied while it is off screen or hidden (`mp-cloth-follow`). Before, the scarf stayed stretched
from the old place to the new one, across the world.

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
NPC to sleep: `sleep` process mask, not drawn, collision off; one on its screen within 60 m sleeps
once out of view (60 s at most after the switch). Everything is restored when it becomes an
authority or the session ends.

### 4.5 Area weapons

The wave concussor (red 2), the mass inverter (dark 2) and the arc wielder (blue 2) are not
shots: they are a shockwave process, a gravity field and a lightning tracker. The shooter's
machine sends a `weapon` event:

- when one goes off (`mp-collect-weapon`: the shockwave entering `explode`, a new `gun-gravity`);
- every frame its arc is drawn (`setup-draw!` wrapped): the arc's first point and five more, in
  2 cm steps, in the event's 32 bytes of text.

The others draw copies made of the stock pieces, with their damage left out:

- `mp-red2-copy`: the explosion, its ring and scorch marks, without the pad buzz;
- `mp-dark2-copy`: the rings and rising particles, without spinners;
- one `gun-blue-2-lightning-tracker` per player, drawing the arc received (`mp-arc-shown`). Its
  six bolts are switched on as the stock `active` state does, and it gets the stock tracker's heap
  (`#x29400`): smaller, the bolts end in `process-drawable-art-error` "memory". Its skeleton is
  left out, because that art is loaded only with a machine's own arc wielder.

The needle lazer's (blue 3) shots are shots (1.4): their copy draws the light trail the shot
carries (`light-trail-tracker-projectile`), from the type name sent in its state.

### 4.6 Players far apart

Each machine loads the levels of its own place only, and a distant player's objects are drawn
from their art loaded alone (1.6), so two places never overlap.

An actor more than 50 m from the camera of the machine that simulates it stops running
(`run-logic?`, `*ACTOR-bank* pause-dist`). `mp-near-other-player?` also keeps running the NPCs
within that distance of another player (`run-near-others` test switch), so the NPCs around a
player far from the authority are not frozen.

## 5. Testing in game

### 5.1 Unit tests and compile

```bash
task build-release-game                                   # gk + goalc, after any C++ change
cmake --build out/build/Release --target goalc-test
out/build/Release/bin/goalc-test --gtest_filter=Multiplayer.*
task compile-check                                        # first time in a worktree: (make-group "iso" :force #t)
```

### 5.2 Two instances played by hand

```bash
python scripts/mp-test/local_pair.py [--retail] [--reset]
```

It starts two instances side by side, each with its own config folder in
`out/mp-local/<controller|keyboard>/`. The folders are made on the first run from the user's saves
and settings, without the multiplayer name and color, then kept; `--reset` makes them again. The
keyboard instance sees no controller (SDL joystick backends off for it alone), so its keyboard is
on. The controller instance keeps the controller while unfocused
(`ignore-controller-win-unfocused? #f`). Keep the keyboard window focused: keys only reach the
focused window. Default boot as `task boot-game`, `--retail` as `task boot-game-retail`.

### 5.3 Automated sessions

```bash
python scripts/mp-test/run_mp_test.py <scenario> [--latency <ms>[:<jitter ms>]]
```

`--latency` holds what each instance receives that long, plus up to the jitter, before reading it,
in order (`MP_TEST_LATENCY`, `Peer::receive` in `kmultiplayer.cpp`): both instances run on one
machine, this stands in for the network between two.

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
controllers are left out (`SDL_JOYSTICK_<backend>=0`, and `SDL_XINPUT_ENABLED=0` for XInput): the scripts drive pad 0 themselves, and a
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
| `press <button> <ms> [lx ly]`, `stick <lx> <ly> <ms>`, `stick-r <rx> <ry> <ms>` | Force pad 0 (button names in PS2 order, at full pressure: X drives a buggy; sticks 0-255, 128 centered; `press` with a left stick moves while it fires; the right stick turns the camera). |
| `goto-player <slot> [x z]`, `face-player <slot>`, `goto-behind <slot> <m>` | Stand next to a remote player (or x and z meters from it), turn to it; stand that far behind it, out of its view. |
| `goto-own <prefix>`, `kill-own <prefix>` | Stand 8 m from one of our NPCs we send whose skeleton starts with `prefix` (`h-bike`, `barge`), facing it; give the first such vehicle a blow from our player that leaves it no hit points (it crashes and blows up as the game makes it). |
| `goto-npc <prefix> [meters]`, `goto-actor <n>` | Stand 1.5 m (or that far) from a replicated NPC whose skeleton, without `skel-`, starts with `prefix`, facing it. |
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
| `scene <name>`, `log-levels` | Start a cutscene as a mission would; print the levels loaded (`art-heap #t`: the art heap is in its memory), the art heap and the art groups loaded alone, and the cutscenes sent, shown and playing. |
| `log-shaders` | Walk the texture shader lists with bounds: links counted and broken (0 when every level is below 256 MB), and each level's heap. |
| `far-levels <0\|1>` | Load the art of distant players' objects (default 1); 0 for a counter-test. |
| `synth-skels <0\|1>` | Copies use the skeleton-groups rebuilt from their sender's description, even when the code defining ours is loaded (the path of a group whose code is in no level here). |
| `log-art-kinds` | Every kind of object (skeleton) another player announced: its group (ours or rebuilt), its art group loaded or loadable alone, drawable here or not. |
| `announce-all-kinds`, `log-kinds-coverage` | Describe to the others every kind of object of our place (every skeleton-group whose art is loaded here); count the kinds another player described, complete, with their art group file here. |
| `ride-shown-elevator` | Once this machine started an elevator trip because player 0 did, stand beside player 0 on it (once). |
| `wait-joined`, `wait-player <slot>` | The script's clock stops until this guest is in the host's world (next to it), or until player `slot` is in game for 2 s: the steps after keep their spacing from then. Every scenario's guest waits after `join`, its host before the guest arrives. |
| `gun [pickup-type]`, `board` | Every gun mod unlocked with ammo, the gun out, the mod `pickup-type` picked (26 red 1 ... 31 yellow 3 ... 35 dark 1 ... 37 dark 3); the hoverboard unlocked with its zap, the player on it. |
| `mode <0\|1>`, `title` | Game mode when hosting (free roam, co-op); back to the title screen as the pause menu's quit does. |
| `log-join`, `try-save` | Joining the host's world: stage, world sent or loaded and its size, saves refused, continue point, title screen or in game, language, distance to the host; an auto-save, as a checkpoint asks it. |
| `log-coop`, `log-weather` | Co-op: authority, steps held and released, leash returns, the player waited for, distance to the host; the weather (clouds, fog, special, the override). |
| `log-gone`, `trace-gone` | On the authority: the NPCs it sent, shown, that left the game or were hidden within 40 m of another player without dying (count; each, with why). |
| `log-vanished`, `trace-vanish` | What vanished on our screen within 40 m while alive: own NPCs put to sleep, copies removed or hidden at once (count; each, with why), and copies that faded out instead; each copy that dies here (`copy died <skeleton> [vehicle]`). |
| `log-path <skeleton\|shot> <frames>` | An object's root (every shot's, with its id) every frame: on its owner each state it sends, on a viewer each state drawn, stamped with the owner's clock (`compare_poses.py` compares the copy's path with the owner's, shot by shot). |
| `log-laser <frames>` | The gun's laser sight every frame, where it starts and ends: the owner's as its game computes it (`laser-point`, `laser-hit-point`), the copy's as drawn, stamped with the owner's clock (`compare_poses.py laser_report`). |
| `log-weapons` | Other players' area weapons drawn here (wave concussors, mass inverters, arc frames, needle trails), and each arc received: its points, its tracker, its bolts. |
| `log-aimable` | What the area weapons find within 20 m of us: each focusable of the actor hash, its process mask, focus, collision action and sphere. |
| `power <dark\|light>`, `log-power` | Turn into Dark Jak or Light Jak (unlocked, with its eco); our state and what each puppet shows (blend, dark, global effects, fade). |
| `log-stretch`, `hide-puppets <0\|1>`, `hide-copies <0\|1>` | Each puppet's bone farthest from its root, and the copies with a bone over 5 m away (a stretched scarf); hide the puppets or the copies (counter-tests). |
| `log-motion [m]`, `log-paused [m]`, `run-near-others <0\|1>` | NPCs within 60 m (ours, copies) and how many moved since the last call; on the authority, its NPCs near another player, how many the stock rule pauses and how many run; whether they run (default 1). |

Scenarios and what they verified (2026-10-15, Spargus, Haven City and the volcano; `check_results.py`: 253 checks passed, 1 failed, 35 scenarios, no crash, before the last changes; the failure, `v1-city-combat` checking a vehicle's copy dying, no vehicle destroyed in that run, was dropped for that scenario (`v1-city-dark1-host` destroys one for sure) and the run again passed 6 of 6. The last changes (the laser sight sent by its owner and passing through it, each blow's answer logged, `goto-npc` with a distance, `press` with a stick) were checked by `guard-hit`, `shot-path` and `laser`, and the first 7 scenarios of a full run (`smoke` to `npc-handoff`, no crash) stopped there on the user's request; the others were not run again after them):

| Scenario | Checks | Result |
|---|---|---|
| `smoke` | Host, join, puppets, names, health bars | Both see each other; poses, sounds sent and applied both ways. |
| `pvp` | A guest's punch on the host; the color marker | The host takes 1 hit and goes through the stock hit reaction (`target-hit`, counted by `log`), pushed back 3.04 m, health 8 to 5, seen at 5 by the guest. `log-look` on both machines: the other player's model not tinted (color multiplier 1, emissive 0), its minimap icon and the marker before its name in its color (about 1000 markers drawn). |
| `pvp-probe` | The attacker's position and state every 0.1 s around the punch | The punch lunge stops on the host's hurtbox. |
| `npc` | Host NPCs on the guest | 13 replicas drawn, the guest's own NPCs (8 to 10 around it) asleep. |
| `npc-hit` | A guest's punch on a host NPC | The host applies both blows to its citizen (`player 1 hit ... punch`); in an earlier run the citizen struck back and the guest lost 2 health. |
| `props`, `combat` | Daxter, gun, shots, impacts, emitters, laser | Daxter on the shoulder and the gun in the aiming pose on the remote puppet; the host's shots drawn on the guest as objects (2 copies), their impacts and the gun's emitters replayed; the gun's laser drawn from the gun's copy, ending on the ground where it points (screenshot). |
| `vehicle` | A driven buggy, then stolen | Body and wheels replicated, its emitters (289 shown) and engine sound (932 updates played) on the guest; the driver is ejected and sees the thief's buggy. |
| `ride` | Passenger seat | The guest, standing 3 m beside the host's two-seat buggy, rides in it; the host drives 27 m with it. |
| `crate` | A stack of crates broken by the host | The host breaks 4 (34 to 30 around it), the guest goes from 48 to 44. |
| `pose` | The driver's pose (pilot animation), its dust and engine | The host's bones and their copy on the guest match within 5 mm (`log-pose`, 65 bones); 378 emitters shown and 1186 sounds played on the guest. |
| `pose-all` | Every object type at the same moment: the player's model, NPC, Daxter, gun, buggy, wheel | `compare_poses.py`, about 60 samples each (122 for the player, on foot then driving), three runs: the player's model median 0.7 to 0.8 mm, largest 1.6 to 1.8 mm; a running NPC whose bones move 37 to 54 mm per frame, median 0.8 to 1.2 mm, largest 1.2 to 2.7 mm; Daxter 0.6 mm (1.0 mm); gun 0.2 mm; buggy 0.5 mm (1.1 mm); wheel 0.0 mm: the 1 mm steps of the wire format. Before the 0.1 ms clock, 6.7 mm at most; before every frame of the owner was logged, one sample at 18.9 mm (a 43 ms frame left a gap in the owner's samples, a measurement artifact). Two joints of the driver once read 1.5 m off: their matrix had `w` 0.9992 and the log read the translation alone, at 9.2 million units from the origin; the pose sent was right (traced in the runtime), the log now takes `w` in. |
| `progress` | Story progress | The node the host closed before the guest joined reaches it on join, the one closed later live: 3 closed nodes and the same open ones on both. |
| `far` | Authority, a distant player | Next to the host the guest shows its NPCs (10 own asleep); 570 m away it simulates its own (37 awake) and the host draws it there; back next to the host its NPCs sleep again. No level of the other's place loads on either machine; `log-shaders`: 0 broken links. The host in Spargus describes its 62 kinds of objects: the guest, far away, can draw all 62. |
| `far-city` (kinds) | Every kind of object of a place, not only those passing by | The guest in Haven describes all 24 kinds whose art its levels hold (`announce-all-kinds`): the host has 24 described, complete, all drawable. |
| `far-city-synth` | Objects whose code is in no level here | The same as `far-city`, the host building every copy from the skeleton-groups the guest describes (`synth-skels 1`): 24 groups rebuilt, every object it draws drawn. |
| `far-city` | A distant player in another region | The guest goes to Haven City's port while the host stays in Spargus (art range raised to 100 km for the test). No Haven level loads in Spargus: the host opens the art heap in `wascitya`'s memory and loads the art groups of what passes by the guest alone (8 in the last run: barge, cars `cara` `carb` `carc`, bikes `bikea` `bikeb` `bikec`, `citizen-norm`; `crimson-guard` too in an earlier one), 0 failed; every object the guest draws is drawn here (3 of 3 in the last run: a barge, a citizen, Daxter; 11 of 11 in an earlier one, with guards and a car). The guest, in Haven, opens its own art heap in `ctywide`'s memory for the host's dog (`dogat`) and closes it when it leaves. `log-shaders`: 0 broken links. |
| `progress-late` | A guest 40 missions behind joins | It loads the host's world on joining: the same 42 closed nodes as the host, with their rewards, and plays on. |
| `guns` | Every gun mod, both ways | Each player fires all 12 mods (yellow 1-3, red 1-3, blue 1-3, dark 1-3) 7 m from the other, in Spargus: no crash on either machine; the guest draws 214 copies of the host's shots, the host 183 of the guest's, and the shots hit the other player (hit reactions on both). |
| `city-combat` | The crash report's weapons in Haven City | The guest fires the gyro burster (yellow 3), the plasmite RPG (red 3) and the peace maker (dark 1) at the port, then the host fires the same: no crash; the host draws 165 copies of the guest's shots, the guest 162 of the host's. The homing and blast weapons pick the host's barges through their copies (before, copies were not focusable). Its `goto-npc skel-...` lines never matched (the prefix goes without `skel-`): the guest fires from beside the host; `guard-hit` aims at a guard. |
| `guard-hit` | Guards react to another player's shots | The guest stands 5 m from one of the host's guards and fires the blaster at it: the host's guard takes each blow and reacts as the game makes it: three shots taken (`taken #t`), the guard goes to `hit`, then `die`, the next one to `hit`. Before, this script's shots never reached a guard (at 1.5 m a shot starts inside the guard's sphere; `goto-npc` took `skel-` prefixes that never matched). |
| `shot-path` | Shots where they fly | The host fires the blaster, the plasmite RPG and the gyro burster while running sideways; the guest draws each shot's copy: 163 shots, each copy within 0.2 cm of its shot at the same moment (median), 3.1 cm for 95 %, 5.5 cm at most. |
| `laser` | The laser sight in motion | The host walks and turns the camera with its gun out; the laser its gun's copy draws on the guest against the host's own: 767 samples, its start within 0.1 cm (median), its direction within 0.27 degree (95 %), its end 28 cm (median; farther where the two meet different things). Before the fix: 14 cm, 8.5 degrees, a sixth of the samples ending on the owner's hurtbox behind the gun. |
| `vehicle-hit` | The hoverboard's zap on a vehicle (the host's crash) | The guest on the hoverboard zaps the host's buggy and punches it: the host applies the zaps (`board`) and blows to its buggy and wheels, until the buggy blows up; no crash on the host (the zap's effect, replayed under `*entity-pool*`, crashed it in `board-zap-track`) nor on the guest (the copy of the dead buggy played a character's death effect, a call to address 0). The guest's hoverboard is drawn on the host (`skel-board` copy). |
| `elevator-alone` | Another player's elevator trip | The host rides the palace elevator up while the guest stays in the street 25 m away: the guest's elevator rises with it (34.6 m to 242.9 m, placed from the host's states), its scripts set aside for the trip and back after; the guest's continue point stays `wascity-start` and no palace level loads for it. Before: the elevator stayed at the bottom (paused out of view) and, arriving, its scripts changed the guest's continue point and levels. |
| `drive` | Smoothness of a driven vehicle | The host drives a buggy 80 m straight; the guest, 8 m away, logs its copy's root every frame: 351 samples, the copy within 0.1 cm of the host's path (median), 0.2 cm for 95 %, 0.4 cm at most; its speed changes 0.21 m/s per frame, the host's own 0.23 m/s (the copy moves as evenly as the original). |
| `npc-watch` | NPCs under another player's eyes | The host stands still at Haven's port, the guest 30 m behind it, out of the host's view, for 90 s, showing the host's NPCs (13 copies): none of the NPCs the host sends near the guest leaves or is hidden alive (`log-gone` 0). |
| `npc-handoff` | NPCs when players meet or part | At the port, the host goes to the slums (the guest simulates its own NPCs), then comes back next to the guest (the guest shows the host's again), while the guest turns its camera around every 8 s: nothing vanishes at once on the guest's screen within 40 m (`log-vanished` 0). The same script on `multiplayer-v1.0.0` (with the same count): 9, 3 and 5 vanished in three runs, own NPCs put to sleep in view when the host came back, copies of the host's NPCs hidden in view when it left. Now, three runs: 0, 0 and 0 vanished; in the third, 3 guards' copies the host left in view faded out instead. |
| `v1-vehicle-hit` | The hoverboard zap's crash, reproduced | The `vehicle-hit` script without waits. On `multiplayer-v1.0.0`: the host crashes in `board-zap-track` (`vector<-cspace!`), as in the test session. Now: the zaps and blows applied (96 hits on the buggy, its wheels and a wastelander), no crash. |
| `v1-city-combat`, `v1-city-yellow3` | The gun crashes, reproduced | The `city-combat` script without waits, and the gyro burster (yellow 3) alone at the port's guards. On `multiplayer-v1.0.0`: the guest crashes in both, while firing the plasmite RPG (red 3) and the gyro burster: the copy of a barge its shots destroyed plays a character's death effect, a call to address 0 from `mp-replica-update`. Now: the same blows applied (60 hits on barges and a car's copy dying on the guest; 133 hits on barges, a bike, citizens and a guard), no crash. |
| `v1-city-dark1-host` | The peace maker crash, reproduced | At the port the host fires the peace maker (dark 1) at its own bikes, car and barge (`goto-own`) while the guest stands by. On `multiplayer-v1.0.0`: the guest crashes (one run of two; in the other no vehicle was destroyed), the same call to address 0 from `mp-replica-update`: a bike's copy dying. Now: a vehicle's copy dies on the guest (`copy died ... vehicle`; the script ends with `kill-own`, one of the host's vehicles destroyed for sure), no crash. Fired by the guest, the peace maker of `multiplayer-v1.0.0` hits no copy (its copies were not focusable), hence no crash in that direction. |
| `join-world` | A guest with another save joins | The guest, in Haven with 3 more story steps than the host, joins: it loads the host's world (42928 bytes, 3.7 s), comes out at the host's checkpoint in Spargus 1.5 m from the host, with the host's progress (1 node); the host's progress is unchanged (the guest's 3 not merged); its language is kept; an auto-save is refused (`no save in another player's world`). |
| `join-title` | Joining from the title screen | The guest goes back to the title screen, joins from there: the host's world loads, the guest appears 1.5 m from the host at its checkpoint, with its progress (screenshots: the title screen, then next to the host). |
| `coop` | Co-op mode | The host hosts in co-op: the guest never simulates NPCs; sent 202 m away, it is back 1.5 m from the host within 3 s (leash). Standing 59 m away, it is told "Host waits for you here" while a story step the host reached is held; coming next to the host, the step closes on both (2 nodes each). The guest follows the host's weather (clouds 0.25, fog 0.5, its own random weather stopped). |
| `world-objects` | Cycles, doors, elevators | Test cycle: the guest's phase is the host's within one frame (16 ms; 39 ms in the last run, where one guest frame came 273 ms late). The host stands at a door 200 m from the guest: the guest's copy of that door opens (it stays closed with the door wrapper off, the counter-test). The host rides the palace elevator: the trip starts on the host ("sent 1") and on the guest ("shown 1", "sent 0"); the guest then jumps beside the host on the elevator its own machine moves and rides up with it (screenshots: the guest in the street, then in the shaft). |
| `scene` | Cutscenes | The host starts a Spargus cutscene: it plays on both machines (screenshots of the same shot), both players placed in it. |
| `float` | Physics objects | The volcano's lava platforms, 270 m from both players: at the same stamp, the guest's are within 7 cm in height and 0.002 in tilt of the host's (they were 2.25 s out of phase before). |
| `float-weight` | A player standing on a remote-driven platform | The guest stands on a lava platform its machine moves where the host's is: carried 18 m in 2 s with it; each machine sends the other player's weight to its platform (counters); both slide into the lava as the platform sinks, as in the stock game. |
| `guns-fx` | The area weapons, both ways | Each player fires the wave concussor, mass inverter, arc wielder and needle lazer at the other 7 m away, health topped up before each (the Wastelanders hit by them shoot back): no crash; each draws the other's (1 explosion, 1 field, 165 arc frames, 9 to 24 needle trails) and takes their blows (`pvp blow ... by gun-red-2-shockwave`, `gravity-spinner`, `gun-blue-shot-2`, `gun-blue-shot-3`). |
| `powers` | Dark Jak and Light Jak on another machine | The guest turns into Dark Jak, then Light Jak, 7 m from the host: the host's puppet shows the dark blend shapes (1.00), then Light Jak's look (rim lights, effect `#x90`) and glow. |
| `far-split`, `far-split-host` | Players in two regions | One goes from Spargus to Haven City's port and back while the other stays: no part of a model stretched across the world (farthest bone 2.6 m from its root), NPCs moving on both sides. |
| `npc-far-run` | NPCs near a distant guest | The guest 60 m from the host: the host's NPCs near it run (none paused) and the guest sees them move. |

Under a network's delay (2026-10-15, `--latency 50:30`: what each instance receives held 50 ms
plus up to 30 ms, so 100 to 160 ms there and back; 15 scenarios, `check_results.py`: 121 checks
passed, 0 failed, no crash):

| Scenario | Under 50 ms + 30 ms of jitter |
|---|---|
| `smoke`, `pvp`, `vehicle`, `coop`, `join-world` | Every check of the scenario passes: puppets and hits, the steal prompt, the leash, held story steps and the host's weather, the host's world loaded and the guest 1.5 m from the host. |
| `drive` | The buggy's copy within 0.1 cm of the owner's path (median), 0.2 cm at most, over 102 m; its speed changes 0.10 m/s per frame, the owner's 0.24. |
| `pose-all` | Copies within 0.8 mm (player), 0.9 mm (NPC), 0.7 mm (Daxter) of the owner's pose (median), 2.1 mm at most. |
| `guns`, `city-combat`, `v1-city-dark1-host` | No crash; 219 and 170 shot copies drawn; a vehicle's copy dies on the guest. (`guard-hit`, `shot-path` and `laser` were not run under a delay.) |
| `npc-handoff` | Nothing vanishes at once on the guest's screen; 2 copies left in view faded out. |
| `world-objects`, `float`, `float-weight`, `elevator-alone` | Cycles within 2 ms of the host's (30 to 34 ms before the clock fix); lava platforms within 7.6 cm; the elevator rides up and gives its scripts back. |

| | |
|---|---|
| ![Name, color square and health bar](multiplayer-screenshots/pvp-nameplate.jpg) | ![The host's NPCs on the guest](multiplayer-screenshots/npc-replicas.jpg) |
| ![Remote player with gun and Daxter](multiplayer-screenshots/remote-gun-daxter.jpg) | ![A guest's punch on a host NPC](multiplayer-screenshots/npc-hit.jpg) |
| ![Steal prompt next to the host's buggy, with the triangle icon](multiplayer-screenshots/vehicle-prompt.jpg) | ![The host after the steal](multiplayer-screenshots/vehicle-stolen.jpg) |
| ![Riding as passenger](multiplayer-screenshots/passenger.jpg) | ![The host aiming its gun, as the guest sees it](multiplayer-screenshots/remote-aiming.jpg) |
| ![A guest far from the host, in its own world](multiplayer-screenshots/far-own-world.jpg) | ![The host's view of the guest, blue marker, model not tinted](multiplayer-screenshots/pvp-host-view.jpg) |
| ![The host firing, as the guest sees it](multiplayer-screenshots/remote-firing.jpg) | ![Both players at the volcano](multiplayer-screenshots/volcano-together.jpg) |
| ![A cutscene on the host](multiplayer-screenshots/scene-host.jpg) | ![The same cutscene on the guest](multiplayer-screenshots/scene-guest.jpg) |
| ![The guest in the street before the host starts the elevator](multiplayer-screenshots/elevator-guest-before.jpg) | ![The guest carried up the shaft by the elevator the host started](multiplayer-screenshots/elevator-guest-riding.jpg) |
| ![The host's laser sight, drawn from its gun's copy](multiplayer-screenshots/remote-laser.jpg) | ![The host's shot in flight and its impact, as the guest sees them](multiplayer-screenshots/remote-shot.jpg) |
| ![The guest at the title screen, before joining](multiplayer-screenshots/join-title.jpg) | ![The same guest after joining: next to the host, in the host's world](multiplayer-screenshots/join-joined.jpg) |

The screenshots come from scenario runs (`out/mp-test/<scenario>/screenshots`): `vehicle-prompt`,
`remote-laser`, `remote-shot` and `join-*` from the last round (2026-10-15), the others from
earlier rounds.

Over the Internet, the host forwards TCP port 7777 (or the port chosen) and guests use the host's
public IP.

## 6. Known limits

- Another place's objects (1.6) are drawn within 400 m of our player only, from their art loaded
  alone (32 MB at most, freed with the level whose memory holds it); farther, they are not drawn.
  Their sounds stay silent (sound banks follow our place). Art groups are read from
  `out/jak3/obj`, which a compiled install has.
- A guest joining loads the host's world through a loading screen: the session goes on meanwhile,
  its puppet is hidden until it is in game. Leaving the session, it stays in the host's world
  without saving until it loads its own save or goes back to the title screen.
- Co-op holds every story step until every player is within 25 m of it, including steps closed
  by a script on arrival somewhere (an elevator, a trigger): the player who got there waits.
- The laser sight's far end can still differ from the owner's when they meet different things
  (another player's copies there, a small angle at long range).
- Sizes, above what the game spawns (at most 20 objects per batch were seen in the scenarios):
  128 objects per batch and 256 copies drawn, 8 particle groups per copy, 32 emitters and 32
  sounds per object and batch. Sounds of a sound bank the viewer has not loaded stay silent.
- Bandwidth to each other player: about 200 KB/s for a player's model, Daxter and gun at 60
  frames a second, plus 36 KB/s per NPC within 40 m of that player and 9 KB/s per NPC farther
  (15 Hz); with a crowd of 10 NPCs nearby and 30 farther, about 0.85 MB/s.
- A copy its owner no longer simulates holds 3 s where it is, then fades out over 1 s, when the
  player looks at it; an own NPC in view stays awake up to 60 s after another player's NPCs are
  shown: meanwhile both are seen.
- The scenarios run two instances on one machine; `--latency` delays what each receives (one
  way, with jitter), not a real link's losses or bandwidth limits.
- Text fields use the D-pad (keyboard arrows work too).
- TCP head-of-line blocking can stall a player on a lossy link (interpolation hides short stalls).
- Another player's Dark Jak has no dark texture swap (a purple tint instead) and keeps its eyes;
  Light Jak's wings and shield are not drawn (their art is loaded only with our own Light Jak).
- The wave concussor's charge glow is not drawn on its copy (its particles follow our own gun);
  its explosion darkens the screen of every player near it, as the shooter's.
- The arc wielder and the needle lazer lock onto enemies, civilians and vehicles, not onto
  players: they hurt a player whose way they cross, and turn to hostile NPCs near it first.
  Beyond 6 m the wave concussor needs a clear line over the ground (stock rules).
- NPCs frozen around a player far from the authority were not reproduced in the scenarios; the
  stock pause rule is extended to other players as a safeguard (4.6).

## 7. Change log

- **2026-10-16:** `scripts/mp-test/local_pair.py` starts two instances to play by hand on one
  PC, one with the controller, the other with the keyboard (5.2).

- **2026-10-15 (evening):** another player's area weapons, Dark and Light Jak, distant players.
  - *Area weapons.* The wave concussor, mass inverter and arc wielder of another player are drawn
    from `weapon` events (4.5), the needle lazer's trail on its shots' copies. They hurt the other
    players: the hurtbox is a crate-kind focusable the area weapons find (4.2).
  - *Dark Jak, Light Jak.* Their blend shapes, look, fade and auras show on the puppet (4.2).
  - *Players far apart.* A scarf left stretched across the world by a teleport (cloth) is reset
    and hidden off screen. NPCs near another player keep running (4.6).
  - *Crashes fixed.* The puppet's blend-shape override array, not relocated by a heap compaction,
    corrupted other processes (the arc wielder crashed the game); an arc event overflowed its
    32-byte text by 4 bytes.
  - *Tests.* Scenarios `guns-fx`, `powers`, `far-split`, `far-split-host`, `npc-far-run`; test
    commands `log-weapons`, `log-aimable`, `power`, `log-power`, `log-stretch`, `log-motion`,
    `log-paused`, `run-near-others`, `hide-puppets`, `hide-copies`; the receiver logs each PvP hit
    and the attacker each blow, with what dealt it.

- **2026-10-15:** a session of the user's crashes, sync and UX reports.
  - *Crashes.* Another place's levels are no longer loaded (their code reset Spargus' traffic
    engine and crashed a guest): their objects' art is loaded alone into an art heap, within 400 m
    (`art-range` test switch). The hoverboard zap no longer crashes the host: effects replay on the
    copy of what they belong to, and the groups whose particle callbacks cannot run on a copy are
    skipped. A player's shots are no longer fired again elsewhere (their code read the other
    machine's player): they are objects drawn from their state. An elevator trip of another player
    no longer runs the elevator's scripts on a machine whose player is not on it (they loaded the
    palace on a player in the street). The runtime prints the GOAL function of a crash.
  - *Sync.* States of what a player owns every frame, player states every frame, one clock per
    sender, objects extrapolated 150 ms and kept 500 ms: a driven buggy's copy follows its path
    within 0.4 cm. The hoverboard is a prop; the gun's laser is drawn from the gun's copy; shots
    are drawn where they fly. Replicas are focusable targets of their original's kind (homing and
    blast weapons hit them) and a remote blow counts once (its attack id). The authority keeps the
    NPCs near other players on screen (the game removed them off its own). One elevator for
    everyone: placed where the trip's machine has it.
  - *UX.* Vehicle prompts with the button icon, as the game's own (`print-game-text`,
    `<PAD_TRIANGLE>`), only for vehicles that can be taken, measured to the vehicle.
  - *The host's world, game modes.* A guest that joins (from the title screen too) loads the
    host's game sent as a save, comes next to the host, keeps its settings and saves nothing
    there; its own progress is not merged. Free roam and co-op (leash, the host's weather, story
    steps that wait for every player, no NPC authority for guests).
  - *Tests.* Protocol 5 (`BLOB`), `wait-joined`/`wait-player` in every scenario (the runner waits
    for the scripts' end), new scenarios `guns`, `city-combat`, `vehicle-hit`, `elevator-alone`,
    `drive`, `join-world`, `join-title`, `coop`, `npc-watch`.
  - *Reproductions on the version played.* `multiplayer-v1.0.0` rebuilt with its own sources:
    the hoverboard zap crashes the host (`board-zap-track`); the gyro burster and the plasmite RPG
    crash the guest in Haven (the copy of a barge they destroyed played a character's death
    effect), and so does the host's peace maker destroying its bikes. Scenarios `v1-vehicle-hit`,
    `v1-city-combat`, `v1-city-yellow3`, `v1-city-dark1-host` run the same scripts on the current
    version: no crash, vehicles' copies dying (`copy died`).
  - *NPCs vanishing.* Reproduced on `multiplayer-v1.0.0` (`npc-handoff`: 9, 3 and 5 on the
    guest's screen): own NPCs put to sleep and copies hidden or removed in view when the roles
    change. Nothing in view goes at once now (1.4): 0, 0 and 0, a copy left in view fading out.
    Measures `log-vanished` and `log-gone` (hidden NPCs counted too), test commands `stick-r`,
    `goto-behind`, `goto-own`, `trace-gone`, `trace-vanish`; `npc-watch` with the guest out of
    the host's view.
  - *Guards, shots, laser.* Each blow another player lands is logged with the object's answer
    and the state it goes to (`taken #t, then hit`). The laser sight is the owner's, sent with
    the gun (start and aim), passing through the owner's player. Scenarios `guard-hit`,
    `shot-path`, `laser`; `goto-npc` takes a distance (its prefix goes without `skel-`: the
    `skel-` prefixes of `city-combat` and the `v1-*` scripts never matched, kept as they were run);
    `press` takes a left stick.
  - *Network.* `run_mp_test.py --latency <ms>:<jitter>`: what each instance receives is held
    that long, in order, as over a link between two machines (15 scenarios pass, 5.2). Under 50 ms and
    30 ms of jitter, cycles were 30 to 34 ms off the host's: their clock shift now follows the
    host's copies (1.6), 3 ms. A copy's `linger-since` is set at its start (process memory is
    reused). Test command `kill-own`.
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
