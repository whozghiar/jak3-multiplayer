# Multiplayer (Jak 3): technical notes

How the online mod is built: the network layer in the runtime, the GOAL side, the shared world,
the vanilla hook points, how to test it in game automatically, its limits, and the change log.
Player-facing description: the root [`README.md`](../../../README.md). GOAL patterns referenced
here live in the Lisp wiki ([`.agents/skills/goal-lisp/wiki/`](../../../.agents/skills/goal-lisp/wiki/index.md)).

## 1. Architecture

Two layers, one rule: C++ moves bytes, GOAL gives them meaning.

| Layer | File | Role |
|---|---|---|
| Runtime (C++) | `game/kernel/jak3/kmultiplayer.cpp`, `.h` | The session over GameNetworkingSockets (host or client, five lanes), relay, player slots, kick and ban, interpolation of players, objects and poses, pose encoding, config file, the co-op campaign file, players' account ids and the server's economy database (4.20), the text chat (4.35), test scripts. Exposed to GOAL as the `pc-mp-*` kernel functions, registered from `InitMachine_PCPort` (`game/kernel/jak3/kmachine.cpp`). |
| Renderer (C++) | `game/graphics/opengl_renderer/foreground/Merc2.cpp`, `.h`, `shaders/merc2_outline.*` | Other players seen through walls: their models drawn again, at the end of the 3D scene, as an outline in their color where something hides them (4.9). |
| Renderer (C++) | `TextureAnimator.cpp`, `.h`, `foreground/Merc2.cpp`, `shaders/merc2.vert`, `.frag` | Another player's Dark Jak skin at its own level (4.11); each player's outfit colors (4.12). |
| Renderer (C++) | `game/graphics/pipelines/opengl.cpp` | The chat holds the keyboard while a line is typed: the game's keys released, SDL's text input on, every SDL event handed to the chat first (4.35). |
| Runtime (C++) | `game/kernel/jak3/kmultiplayer_nat.cpp`, `.h` | The host's UDP port opened on its router (UPnP IGD, NAT-PMP), himstan's port mapping. |
| Runtime (C++) | `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, both sticks). |
| Test | `test/test_kmultiplayer.cpp` | Interpolation, a loopback host + client session (join, relay, event, objects, a pose with an emitter and a sound, ban, refusal reason), a pose at scale 1 (12-byte bones) and a blob's id, events kept while not taken, the reliable channel under 20 % packet loss (a 200 KB world and 200 events, each once and in order), and account ids and chat lines (an id per server, a guest's line relayed with its name, the text cleaned, a player found by name). |
| GAME | `goal_src/jak3/pc/features/multiplayer/multiplayer.gc` | Local state, remote puppets (in their player's outfit) and their hurtboxes, passenger seat, shared enemy deaths and story progress, hit forwarding, player hits, Daxter mode, cheats state, teleports, overlay, Daxter on a shoulder (4.16), cutscene outfits (4.17), the co-op return next to a teammate (4.19), the economy and Kleiver's spot (4.20), missions started together and the host's choice in game (4.24), a lizard for every player (4.25), co-op saves and their icon (1.7), per-frame hook. |
| GAME | `.../multiplayer-world.gc` | The shared world: who simulates which NPCs, NPC and prop replication, full poses, particle emitters and sounds, sleeping NPCs, hits on replicas, NPC vehicles, effects, projectiles, world objects (cycles, doors, elevators), cutscenes, levels leaving, crates and pickups. |
| GAME | `.../multiplayer-guns.gc` | Every weapon (Mods menu), free roam's gun mods (4.20), and Daxter's guns (4.8). |
| GAME | `.../multiplayer-menu.gc` | The title screen's Host Game and Join Game, the lobby and its game mode, co-op and mission pages, the on-screen keyboard, START > Options > Multiplayer and its administration page, Kleiver's shop page (4.20), the START ring's Save Game and Load Game online (1.7), the Mods menu entries. |
| GAME | `.../multiplayer-chat.gc` | The text chat on the keyboard and the host's commands (4.35). |
| GAME | `.../multiplayer-test.gc` | Automated test commands (inert without `MP_TEST_SCRIPT`). |
| Tool | `scripts/mp-test/run_mp_test.py`, `scenarios/`, `compare_poses.py` | Runs several game instances on one PC with a script each, gathers logs and screenshots; compares an owner's bones with its copies'. |
| Tool | `scripts/mp-test/local_pair.py` | Starts two instances on one PC to play by hand, one with the controller, the other with the keyboard. |

### 1.1 Network

- **Topology:** star. The host is slot 0 and listens on the port of its address field (default
  7777); guests get slots 1 to 7. Every message from a guest goes to the host, which relays it.
- **Transport:** Valve's GameNetworkingSockets (GNS, `third-party/GameNetworkingSockets`, built
  static with the protobuf it needs), as himstan's Jak and Daxter II Multiplayer uses it
  (`game/multiplayer/platform/transport/session_platform.cpp`, ISC). Each guest holds one
  encrypted connection to the host's listen socket (open-source GNS: encrypted, not
  authenticated); GNS does the handshake, reliability, fragmentation, ordering, the rate limit and
  the timeouts. It is pumped once per frame from GOAL (`pc-mp-update`: its callbacks, then every
  message waiting). A message is `[u8 type][payload]` (at most 16 KB), sent on one of his five
  lanes (a lower priority waits for the higher ones; within a priority, bytes are shared by
  weight):

  | Lane | Priority, weight | Messages | Delivery |
  |---|---|---|---|
  | 0 control | 0, 1 | `HELLO`, `WELCOME`, `LEAVE` | reliable, in order |
  | 1 critical | 0, 4 | `STATE` | unreliable |
  | 2 gameplay | 1, 1 | `EVENT`, `BLOB` | reliable, in order |
  | 3 normal | 1, 1 | `ACTORS`, the `POSES` batches that carry effects | unreliable |
  | 4 bulk | 2, 1 | the `POSES` sent between two batches | unreliable |

  Unreliable messages go with `NoDelay`: one the link cannot carry now is dropped (a newer one
  follows within a frame), never queued; of each lane only a message newer than the last one
  delivered is handed on. GNS does not estimate the bandwidth (its rate is the clamp of
  `SendRateMin` and `SendRateMax`): both are 1 MB/s, the most we send a peer each second, and the
  pressure below thins what we send. A refusal (full, version, kicked, banned, the host stopped)
  closes the connection with its reason as the closing text, which the guest shows; "Connection
  refused" or "Connection timed out" (8 s) when nothing answers.

  Message types: `HELLO` (protocol version, now 11, and the player's secret id, 4.20), `WELCOME`
  (slot), `STATE`, `LEAVE`, `EVENT`, `ACTORS`, `POSES`, `BLOB` (`[u8 from][u8 to][u32 total][u32
  offset][u32 id][bytes]`, 15000 bytes a message: the host's world for a guest that joins, 1.7,
  tagged with the guest's request: a guest takes blobs from the host alone, loads only the one of
  its last request, and the host relays none).
- **Router port:** a host asks its router to forward its UDP port, through UPnP IGD, else NAT-PMP
  (`kmultiplayer_nat.cpp`, himstan's `game/multiplayer/platform/nat/port_mapping.cpp` with
  `third-party/miniupnpc` and `libnatpmp`), on a thread of its own (discovery takes seconds),
  renews it every hour and removes it when the server stops. Once open, the multiplayer menu's
  status line shows the internet address to give to the others ("Hosting: 1 player(s), internet
  address 203.0.113.7:7777", `pc-mp-public-address`). Not in automated tests (`MP_TEST_SCRIPT`):
  no router is touched.
- **Player state:** `mp-player-state` (224 bytes: position, orientation, velocity, animation,
  health, flags, character and outfit, body segments, driven vehicle, passenger seat, name, color,
  level, continue point; Dark and Light Jak in `powers`, the lobby's countdown, the time before a
  paused game resumes, and a driver's seats 2 and 3 in `seat-offset`) every frame (60 Hz): a
  driver's puppet blended between states 33 ms apart drifted on its vehicle, which moves every
  frame.
- **Objects:** `mp-actor-state` batches (96 bytes each, at most 64 per batch; the tint since protocol
  10, 4.28) at 15 Hz from every
  player: `[u8 source][u32 source time][u16 count][states]`; the states of what a player owns
  (props, the vehicle driven and its wheels, its shots) go again every frame between batches.
  Ids carry the owner's slot in the top byte and the owner's process pid below. The source time
  of object batches and poses is in tenths of a millisecond: the receiver's clock estimate and
  the blend between poses one frame apart lose up to a millisecond otherwise (about 3.5 mm on a
  running citizen).
- **Interpolation:** everything another player sends is drawn in the past by that sender's
  delay, at the same moment for its puppet and its objects (one clock estimate per sender: the
  fastest delivery of its states and batches alike). The delay follows how late its messages come
  (`Session::delay_of`, after the playout buffer of himstan's snapshot timeline,
  `game/multiplayer/platform/replication/snapshot_timeline.h`, ISC): the mean lateness of its
  states and of its object batches behind the fastest delivery (moving average, weight 0.05),
  delay = 66.7 ms (one 15 Hz batch) + 10 ms + 2.5 times that lateness, 300 ms at most. About 76 ms
  on a LAN, 95 to 102 ms under 50 ms of delay and 30 of jitter (a fixed 100 ms before; `log-ping`
  prints it). An object keeps its last 500 ms of states; a late one goes on along its last
  velocity for 150 ms, then holds. A shot gone 250 ms without a state is gone.
- **Network pressure,** after the cadence scheduler of himstan's mod
  (`game/multiplayer/platform/session/cadence_scheduler.cpp`, `core/network_statistics.h`, ISC):
  once a second each side reads its connection's status from GNS (round trip, the share of our
  packets the peer gets, how long our normal lane queues) and classifies its link to that peer with
  his thresholds: severe when under 50 % of its messages arrive or 500 ms of queueing, congested
  under 75 % or 250 ms, degraded under 90 % or 100 ms, else normal; a higher level applies at once,
  a lower one after 3 s. As his priorities: bones sent between two 15 Hz batches (bulk, the heavy
  part) go to that peer every 2, 3 or 5 frames; the player state and the states of our objects
  between batches every 2 frames from congested on; the batches, which carry effects and sounds,
  and the reliable messages always go. The host thins what it relays to a guest the same way (a
  relayed pose message without effects is bulk). `[multiplayer] network to player N: <level>`
  logs each change.
- **Poses:** `[u8 source][u32 source time][u16 count]` then one entry per object:
  `[u32 id][u16 bones, bit 15 = effects, bit 14 = scaled][u8 emitters][u8 sounds]`. Each bone is
  relative to the object's frame: its rotation in 6 bytes (the three smallest components of the
  unit quaternion, 15 bits each, and which one is left out: it is rebuilt), its translation in 6
  (1 mm), and its per-axis scale in 6 more only in an entry with a bone not at scale 1 (bit 14):
  12 bytes a bone for most objects, 20 before. Each emitter 20 bytes, each sound 40. Split over
  several frames of at most 16 KB. Every object of a batch is sent with its effects at 15 Hz;
  between batches, the player, its props and the NPCs within 40 m of another player, bones only,
  60 times a second at most (at 144 or 240 frames a second the bytes sent grew with the frame
  rate). A receiver keeps 500 ms of an object's poses (as of its states), 64 at most.
- **Events:** `mp-event` (80 bytes), routed by slot (`to = -1` for everyone). The kinds are
  defined in GOAL (`mp-event-kind`); the C++ side only stamps the sender.
- **Timeouts:** 15 s of silence drops a peer, 8 s to connect (GNS's `TimeoutConnected` and
  `TimeoutInitial`). The pump runs from the per-frame hook even while paused, so a paused player
  does not time out.
- **Test network:** `MP_TEST_LATENCY` and `MP_TEST_LOSS` (and `net` in a test script) set GNS's
  simulated network for the process: lag, loss and jitter (averaging half the jitter given, at
  most all of it) on what arrives.
- **Config:** `<settings>/multiplayer.cfg` (`%APPDATA%/OpenGOAL/jak3/settings/` on Windows):
  name, address, character, color, enabled, mode (the game mode when hosting: 0 free roam, 1
  co-op, 1.7), skin (the outfit's 16 tints in hex, 4.12), uid (the player's secret id, drawn once:
  its economy account on any host, 4.20).
- **The host's lobby choices** travel in its state (`lobby-mode`, `lobby-start`, `lobby-mission`,
  in what was padding): a guest's lobby shows them before any game starts.

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
| Levels | every machine, around its own player | players in one place load the same levels; another place's levels are never loaded here, the art of its objects within 400 m is (1.6); a player in a place at the same coordinates (the volcano under Haven's ruins) is not seen at all (4.27) |
| Elevators | the machine whose player started the trip | the same elevator, placed where it is every frame, its scripts set aside unless the local player rides it (1.6) |
| The game itself (progress, equipment, place, entity states) | the host | a guest that joins loads the host's game as a save, without its memory card, and saves nothing while there (1.7) |
| Cutscenes | where they start | the same cutscene, for the players of that region (1.6) |

A replica is built from the owner's skeleton (`mp-skel-of` finds the skeleton-group from the
process's art group and joint geometry, since a process does not keep it) and plays the owner's
animation by index, which plays the animation's effects (footsteps, sounds) on the replica
itself. The owner's full pose then replaces its bones (1.4). Both players and objects are drawn
in the past by their sender's delay (1.1), interpolated with the owner's clock, so props stay on
the puppet that carries them.

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
  authority sends the characters within 100 m and the vehicles within 350 m
  (`MP_SEND_RANGE_VEHICLE`: of the barges the host saw on the port's water, the guest had one at
  100 m) of the players it serves (itself, and the players who are
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
  progress**. Its Jak wears the host's outfit and its Daxter the Daxter player's (4.17).
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
  loading screen) its state carries `hidden`. The request and the load run from the per-frame
  hook (`mp-update-join-request`), because a guest waiting in a menu (the lobby, 1.8) is paused,
  and the manager with it; the manager then brings it next to the host. A host answers only once
  in game: a guest in the lobby asks again every 10 s, so it follows within 10 s of the host's
  start, plus its loading screen.
- **No save in another player's world.** `auto-save-command` (every save: the pause menu's,
  checkpoints and missions' auto-saves, a new game's first save) is wrapped: while the player
  plays in another player's world (`*mp-join*` `foreign?`), no save is written, and the menu that
  asked is told it failed (`'notify 'error`). Loading its own save (`'restore`) or being back at
  the title screen ends it. The guest's memory card is never read or written for the session.
- **Progress on join.** Only the host sends its closed task nodes to a player who joins; a guest's
  own progress is not merged into the host's world.
- **Game modes** (host's choice in the lobby's mode list, 1.8; saved as `mode` in the config, 0
  free roam, 1 co-op; sent with the world settings as the `coop` flag, and in the host's state
  for guests still in the lobby). The list shows eight modes: Parkour, Egg hunt, PvP,
  Battlefront, Hide and seek and Races are grayed out (coming soon); free roam and co-op can be
  played. Both run the same rules: NPC authority by region (1.4), shared vehicles, weapons and
  story progress, and the host's weather when it shares its time of day. What the host's X
  starts (`mp-start-game`, `multiplayer.gc` "Game modes"):
  - *Free roam:* the end of the game. A new game at Spargus (`initialize!` with
    `wascitya-start`); once in game, `mp-unlock-everything` closes `city-win-resolution` with
    `task-node-close!`, whose `open!` closes every node it needs first (the whole story, 254
    nodes, with their rewards; not sent to the others, `*mp-progress-replaying*`), then adds the
    features the story does not give (`MP_FREE_FEATURES`: each gun color's first mod and the ammo
    upgrades, the others bought, 4.20; the hoverboard's tricks, Dark and Light Jak's powers,
    armor), the eight vehicles, the secrets
    shop's upgrades (not its cheats), full ammo and eco. The host answers no world request until
    that is done; a guest loads that world, and keeps `MP_FREE_FEATURES` while the host's world
    flag `free` is set (its loaded save leaves two out; a respawn restores the save's). Nothing
    is saved (`auto-save-command`, below). Everyone plays the character it picks. Only the
    activities that pay orbs remain, and each offers itself again once done (4.29).
  - *Co-op (two players):* Jak and Daxter. The server accepts two players
    (`pc-mp-set-max-players`, refusing a third with "The server is full"; the mode list grays
    co-op out while more than one guest is there). It starts in one of three ways, chosen on the
    co-op page: a new campaign (the stock New Game's `intro-start`), the campaign continued (its
    save loaded with `initialize!`), or a mission of the story (`initialize!` with a
    `resetter-spec` whose node is the mission's `play-node` and its `play-continue`, as the stock
    mission list's `play-task` starts one: that node opened, the nodes it needs closed). The
    lobby picks "continue" when the server starts and a campaign exists, else "new".
    - *The multiplayer campaign saves itself:* while it runs (`*mp-session*` `kind`
      `'campaign`), the host's game is written into `multiplayer-campaign.bin` next to the
      memory card saves (`pc-mp-campaign-write`: the `save-game` of the in-memory save, after an
      8-byte stamp `OGMPCOOP`), never into a card slot: after each story step (a mission's start
      or end: `mp-on-task-closed` marks a save due, `mp-coop-save-update` writes it once no
      cutscene plays and no menu is open) and wherever the stock auto-saves
      (`auto-save-command` `'save`, `'save-last`, `'auto-save`; the menu that asked is told it is
      done). Each save shows the stock auto-save icon (the checkpoint's, blinking at the right
      for 3 s, `mp-draw-save-icon`) on every screen (`coop-saved` event). The guest saves nothing
      (another player's world). Loading a card save, or the title screen (10 s after a start),
      ends the campaign.
    - *The START ring online:* its Save Game and Load Game open no memory card page (the
      `menu-main-menu-option` `respond-progress` wrapper, `mp-ring-save-load`): the host's Save
      Game in co-op saves the campaign at once ("Co-op progress saved"), a guest's says the host
      saves it on its own, free roam's that it is not saved, Load Game that loading waits for
      the session's end.
    - *Roles:* once per session, a guest playing the host's character is switched to the other
      one (`mp-update-coop-roles`); the lobby offers it first.
    - *Daxter on the shoulder:* a Jak player online carries no Daxter of the game, and a Daxter
      player climbs onto a Jak's shoulder (4.16).
  Until 2026-10-16 co-op held story steps until every player came (`coop-wait` event, now unused)
  and brought a guest more than 150 m away back to the host; both were removed so that the two
  modes play alike.

### 1.8 Title screen and lobby

The way into a session follows himstan's Jak and Daxter II Multiplayer
([repository](https://github.com/himstan/Jak-And-Daxter-2-Multiplayer),
`goal_src/jak2/multiplayer/ui/mp-lobby-progress.gc`, ISC license), written anew for Jak 3's menus
(`multiplayer-menu.gc`):

- **Title screen.** While the mod is enabled, `mp-update-title` (every frame, and when the title
  menu opens) gives `*title-pc*` a list with **Host Game** and **Join Game** after Load Game, the
  stock list otherwise. Until Secrets is unlocked the stock skips index 3 when moving and shifts
  the entries' heights (`progress-generic-pc.gc`, `progress-draw-pc.gc`), so the hidden Secrets
  entry stays at index 3 then, and the heights are set for the entries shown.
- **Pages,** routed by the `set-menu-options` wrapper: `mp-lobby-host` (Host Game: the server
  starts with the lobby, so guests join it before any game starts), `mp-join-game` (the address,
  then Connect: `mp-join!` and `mp-lobby-join`), the lobby, `mp-mode` (the eight game modes, as
  `menu-mp-mode-option` buttons grayed out by `should-disable?` with the reason after the name),
  `mp-coop-start` (the campaign's completion, New campaign, Continue the campaign (grayed out
  without a campaign), Choose a mission), `mp-mission` (two carousels: the act, 1 to 3
  (`*mp-mission-act*`), then that act's missions: the `play-list` entries with a `play-continue`
  and a name, as the stock mission list, whose play node carries the act's flag, `mp-mission-act`),
  `mp-mission-ingame` (the host's page in game, the same two carousels, 4.24),
  `mp-keyboard` and `mp-skin` (the Appearance page, 4.12). The mode page is reached with `push-state` from the lobby; the co-op and
  mission pages replace it (`set-next-state`), so their choice goes back to the lobby with one
  `pop-state`. Each page resets its selection and scroll when it opens (the generic store keeps
  them across pages).
- **On-screen keyboard** (`mp-keyboard`, a page of its own): a text field (name, address,
  `menu-mp-text-option`, now a link for the scrolling page) opens it on its value; a grid of four
  rows of ten keys (letters, `-_.!`, digits; for the address, digits, `.:`, lower case and `-_`)
  and an action row (capitals, space, delete, OK), moved over with the D-pad or the left stick
  (held: repeats every 0.15 s). X types the key, square deletes, L1 switches capitals, START or OK
  keeps the text (trailing spaces removed, `pop-state`), triangle cancels (the stock goes back).
  Before, a field was edited letter by letter with up / down.
- **Lobby** (`menu-mp-lobby-option`, one option that draws the page and reads its keys): the
  session status and game mode; the players in slot order (color, name, Host / Ready / Not
  ready) on the left; the one chosen with up / down on the right (character, ping, status); a
  hint and the keys at the bottom. The mode line reads "Game mode: Co-op - Mission: <name>" (or
  the campaign's start), the hint for free roam says what it is. X: a guest says it is ready
  (`*mp-lobby-ready*`, sent as `mp-flag ready`); once every guest is, the host's X starts a 10 s
  countdown (`mp-lobby-update-countdown`), "The game starts in N" on every player's lobby (the
  host's state carries the time left, `countdown`), then the game (`mp-start-game`, 1.7). The
  host's X again cancels it, and so do a guest no longer ready and the host opening another page;
  guests then read "The host cancelled the start". While it runs, guests can change neither their
  ready state nor their character. L1 / R1: the character (a guest only while not ready). Square: the game
  mode list (host). Circle: the multiplayer options page. Triangle: `mp-leave!`, then the stock
  goes back a page. The page's frame is computed with `adjust-game-x`: this page is handed the
  unadjusted x (70) and width (375), where the frame is drawn squeezed to the screen's ratio.
- **The host ends the session.** When the host stops its server, leaves its lobby, goes back to
  the title screen or is lost (its game closed), every guest goes back to the title screen, its
  menu on the first page, and "The host ended the session" shows at the top for 10 s (text
  `#x1581`), as himstan's `host-left` page tells its players. A kicked or banned guest goes there too, with
  that reason. A host back at the title screen after a game ends its session.
- **Texts that fit.** Names are shortened with "..." where they would run into the next column,
  and lobby lines shrink to fit (4.18); the session status line is localized.
- **Differences with himstan's lobby:** square opens the game mode list (his copies an invite),
  guests join by address (no room code, no LAN scan), and the multiplayer options keep this mod's
  settings plus the Appearance page (4.12; no nametag or map marker options). The countdown
  follows his (X to cancel).

### 1.9 Finding a crash

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

### 1.10 Compared with himstan's Jak and Daxter II Multiplayer

The user asked how himstan's Jak 2 mod
([repository](https://github.com/himstan/Jak-And-Daxter-2-Multiplayer), ISC) synchronizes its
world and has NPCs react to online players, and to take the best of both. Read in its
`goal_src/jak2/multiplayer/` and `game/multiplayer/` on 2026-10-19:

| Aspect | himstan's Jak 2 mod | This mod | Taken |
|---|---|---|---|
| Who simulates an NPC | Each enemy's combat owner: the player it focuses within 100 m, 2 s hysteresis, the host arbitrating; traffic by bubbles around players | A region's authority: the host for every player near it, a distant guest for itself and the guests near it (1.4) | Kept ours: a region's NPCs run on one machine, so their fights, deaths and scripts stay whole; per-enemy owners need each machine to run every NPC's code |
| What travels | About 22 bytes per enemy at 20 Hz (position, rotation, health, state id, focus, flags); the receiver drives its own NPC with forced states and plays walk or run itself | Full poses: every bone of each NPC near another player every frame, farther ones at 15 Hz, with its effects and sounds (1.4) | Kept ours: attacks, hit reactions and deaths look as on the owner's machine; it costs more bandwidth (6, about 0.85 MB/s with a crowd) |
| Playout delay | A C++ buffer whose delay follows the measured jitter | A fixed 100 ms until this round | **Taken:** the delay follows each sender's lateness (1.1) |
| A player's blow on an NPC | Predicted on the attacker (new health broadcast), knockback seeded alike everywhere | Sent to the authority, applied there on the real NPC; the attacker sees the reaction on the copy one round trip later (1.3) | Not yet: prediction would animate a copy without its code; a candidate for later |
| NPCs hitting players | Checked on the victim's machine | The same (1.3) | Same |
| Collectibles | One shared counter; the host deduplicates by entity and broadcasts the count; drops seeded by entity and index | Pickups and crates shared by entity (1.2) | **Taken for the economy:** the host alone credits an orb, once per entity (4.20) |
| Missions | The host owns the task masks; each machine runs its task managers; every player pulled into each cutscene; doors wait for every player; a dead player watches a teammate, then comes back near it; a mission fails only when every player is dead | Story nodes shared (1.5); waypoints and cutscene skips wait for every player (4.13, 4.14) | **Taken:** a dead player back next to a living teammate (4.19); every player in a mission's introduction (4.24), with a vote of at least half of the players; doors waiting (4.34) |
| Races | One race on the host: players in racer slots, the host validates laps and decides, "did not finish" rules | One race per machine until 2026-10-28 | **Taken:** one race, the host's, the other player in a racer slot, the host's verdict (4.15) |
| Transport | GameNetworkingSockets: encrypted, five priority lanes, router port mapping, Steam relay and lobbies | Our own UDP layer until 2026-10-28 | **Taken:** GNS with his lanes, his port mapping (1.1); not taken: Steam (its SDK and an app id) and the LAN browser |
| Daxter riding Jak | The rider hidden; Jak's own Daxter shown in the rider's skin; jump-on animation; stays there in vehicles | A perched Daxter drawn on the puppet's shoulder | **Taken:** the rider hidden, the Jak's own Daxter in the rider's outfit, the stock climb (4.16) |
| Cutscene actors | Jak and Daxter wear a player's skin | Stock actors | **Taken,** with the host's Jak (4.17) |
| Sending under pressure | Cadence lowered when the link is loaded; bit-packed values; relayed only to the peers concerned | Everything every frame, relayed to every guest | **Taken:** his pressure levels, thresholds, dwell and priorities, measured from GNS's connection status (1.1); bones in 12 bytes (1.1); not taken: relaying by interest (a few players, the host relays everything) |

## 2. Requirements and how they are met

| Requirement | Implementation |
|---|---|
| Create or join a server from the start menu | The title screen's **Host Game** and **Join Game**, then the lobby (1.8). In game, START > Options > Multiplayer only customizes the player: its status line, name, character, color and appearance; the game mode, hosting, joining and the server's administration are only at the title screen and in the lobby (4.21). The ring of the start menu has exactly ten slots (36 degrees each, matching its 3D model), so the page hangs off the Options list, which gets a sixth entry while the mod is enabled. |
| The same menus as himstan's Jak 2 mod to reach multiplayer | Host Game / Join Game, the lobby and its keys, the multiplayer options from the lobby, the SELECT player list (1.8, 4.7). |
| A guest uses the host's world, not its own save | Section 1.7: the host's game sent as a save in memory and loaded with a loading screen, the guest next to the host, its own save neither read nor written. |
| Free roam and co-op, the same rules, chosen in the lobby | Square in the lobby (the mode list), or the **Game mode** carousel (host). Section 1.7. |
| Player list with names, health and pings | Hold SELECT while online (4.7). |
| IP address with port, nickname, entered easily | **Server address (ip:port)** and **Player name** text fields, edited on the on-screen keyboard (1.8). |
| UDP | The session runs over UDP with a reliable channel for what must arrive (1.1). |
| Every weapon, for Jak and Daxter | Mods menu > multiplayer > **Give every weapon**; Daxter fires them (4.8). |
| Players outlined through walls in their color | 4.9. |
| Game modes in the lobby, those not ready grayed out | The mode list (1.8): six coming soon, free roam and co-op playable. |
| Join the host's lobby before it starts | Host Game opens the lobby with the server (1.8). |
| Free roam: the end of the game, everything unlocked for everyone | 1.7. |
| Co-op: two players, a mission chosen, a new campaign or the campaign continued, saved at the host's as multiplayer; no Daxter on Jak's shoulder when the other plays Daxter | 1.7. |
| Jak or Daxter | **Character** carousel, L1 / R1 in the lobby. Daxter is the stock playable-Daxter mode (`indax`), made resident in GAME (section 3). He fights with the guns like Jak, and drives vehicles as Daxter (4.3). |
| Color | **Color** carousel. A marker, not a tint: a small square of the player's color before its name, and its minimap icons in that color (own copies of stock classes): a cone (`*mp-minimap-cones*`, the racers' class 29: the marker our player has, `map-target-marker`, turned as the player faces, at twice the dot's intensity as ours; `draw-racer-2` draws it into the map's texture within 50 texels of us) and a dot (`*mp-minimap-nodes*`, the stock clamped dot, 117) on the minimap's edge for a player farther away, hidden nearer (`*mp-minimap-no-dot*`, no size: drawn after the cone, it covered it). On the big map the cone's class is drawn as that dot (its `bigmap` flag, icon and scale). |
| Cheats menu per player | Mods menu (L3 + SELECT) > multiplayer: every weapon, refill ammo, restore health, dark eco, light eco, invincibility, teleport to a zone, teleport to a player. Greyed out for guests when the host disallows cheats. |
| Health bar above players | Name, color square and health bar (green, yellow, red) over every remote player in view. |
| Shared vehicle: steal or sit next to the driver | Next to a remote driver's vehicle: press triangle to steal it, L1 + triangle to ride as passenger when it has a free seat (himstan's binding). A guest can also take the host's NPC vehicles. The prompts are the game's own kind: `print-game-text` with a `<PAD_TRIANGLE>` icon, where the game draws its vehicle prompt (`vehicle-method-144`). See 4.3. |
| Synchronize everything | Section 1.2. |
| Players hit each other, with a reaction | Section 1.3: an `mp-hurtbox` child of each puppet receives the local player's blows and shots; the victim plays the stock hit reaction, pushed away from the attacker. Host setting **Players can hit each other** (on by default). |
| Game entities focus any player | Puppets are `process-focusable` in the player collide list with `collide-spec jak`, which `enemy::find-best-focus` considers. |
| Players on the minimap in real time | Each puppet has two minimap icons in its player's color (`mp-puppet-icons-on`): a cone turned as it faces, as our own marker, near us, and a dot on the edge when it is farther (`mp-puppet-update`). |
| Administration page when hosting | Options > Multiplayer > **Server administration**, at the title screen and in the lobby: pick a player, kick, ban, teleport them or everyone to you, cheats for guests, players hitting each other (never in co-op, 4.23), share story progress, share time of day, stop the server. The host's Mods menu also credits it 100 precursor orbs or 100 Metal Head trophies (4.20). |
| A 10-second countdown in the lobby, the host can cancel it | 1.8. |
| The host's SELECT pauses everyone, the game resumes 3 s after the host asks | 4.10. |
| START no longer pauses the world for anyone | 4.10. |
| Passengers placed as in himstan's mod | Any free seat of the vehicle, the back of a one-seat vehicle (4.3). |
| Every player sees another player's Dark Jak | Its own Dark Jak skin, at its own level (4.11). |
| A character customization menu as himstan's | The Appearance page (4.12), himstan credited in the commit and the code. |
| Co-op: skip a cutscene once every player asked | 4.13. |
| Co-op: a vehicle for every player in a race, which starts once every player is aboard (driving, riding, on a Jak's shoulder) | 4.15. |
| Co-op: a waypoint (yellow beam) needs every player on it, "Players here: 1/x" | 4.14. |
| Menus: no text over another | Names shortened with "...", lines shrunk to fit (4.18). |
| The Appearance page as ergonomic as himstan's skin editor | The part between L2 and R2, each character back on its last part, held keys repeat (4.12). |
| Co-op races: a vehicle of one's own or a seat in another player's | Every player gets a vehicle and may leave it to ride with another or on a Jak's shoulder before the start (4.15). |
| Daxter in a vehicle with a Jak sits on his shoulder | 4.16. |
| Missions handled as in himstan's mod | Shared story nodes, waypoints, races and skips that wait for every player (1.5, 4.13 to 4.15), a dead player back next to a teammate (4.19). |
| Cutscenes: the host's Jak; the Daxter player's Daxter, the stock one when every player is a Jak | 4.17. |
| The host leaving sends every player to the title screen with a message | 1.8. |
| Daxter climbs onto a shoulder only next to a Jak, with the stock animation | 4.16. |
| No Jak player carries a Daxter that is not a player | 4.16. |
| The world's synchronization compared with himstan's, the best of both | 1.10. |
| Precursor orbs and Metal Head trophies per player, kept by the server alone, saved at once in its database, reloaded with the session | 4.20. |
| An economy proof of concept: free roam's gun colors at their first mod, the higher mods bought at Kleiver's | 4.20. |
| The economy at the server's level, each player identified | Account ids per server, derived from the player's secret (4.20). |
| Co-op saves apart from the single-player saves, on their own, with the save icon (a mission's end included) | 1.7. |
| A text chat between the players, the keyboard alone, commands for the administrator | 4.35. |
| Kleiver's shop in Spargus, opened from his line, trading orbs | 4.20. |

## 3. Vanilla touch points

Each is marked `MOD multiplayer`.

| File | Change |
|---|---|
| `game/kernel/jak3/kmachine.cpp` | `InitMachine_PCPort` registers the `pc-mp-*` functions. |
| `game/sce/libpad.cpp` | Pad 0 input forced by a test script (buttons with their pressure, both sticks). |
| `game/CMakeLists.txt`, `test/CMakeLists.txt` | The new C++ file and its test. |
| `goal_src/jak3/dgos/game.gd` | The six mod files at the end of GAME, `target-indax.o`, `target-indax-hang.o`, `jak-indax+0-ag.go`, `jak-pidax+0-ag.go` (Daxter's driving animations, for `pilot` with `as-daxter?`) and `jak-pilot-gun+0-ag.go` (Daxter holding and firing the gun, 4.8). |
| `goal_src/jak3/dgos/facc.gd`, `voca.gd`, `halfpipe.gd`, `vin.gd` | The same three objects removed: a copy in a level DGO would relink the indax states into that level's heap, and they would dangle once it unloads. |
| `goal_src/jak3/dgos/lctypatk.gd` | `jak-pidax+0-ag.go` removed (now in GAME): only that level had Daxter's driving art, so Daxter could not drive elsewhere (`process-drawable-art-error`). |
| `goal_src/jak3/dgos/comba.gd`, `cwi.gd`, `hga.gd`, `raila.gd` | `jak-pilot-gun+0-ag.go` removed (now in GAME, 88 KB): Daxter's clips holding the gun (`daxter-pilot-gun-*`) were only loaded in those city levels. |
| `goal_src/jak3/dgos/wwd.gd`, `voca.gd` | `jak-fldax+0-ag.go` added after `jak-flut+0-ag.go`: Daxter's lizard clips (`daxter-fldax-*`) and the lizard's under him (`flut-saddle-fldax-*`), which only the desert chase loaded (DESLIZ, unchanged). Its lizard clips link to the lizard of the same level, so it is not in GAME (4.22). |
| `goal_src/jak3/levels/common/race/race-manager.gc` | The manager's `active` post gives every racer's progress and bits (`mp-racer-progress`, `mp-racer-bits`, `mp-race-slot`, `mp-race-sync`), the place back to the stock rank, our score saved at our finish, then our result waits for the host's verdict (`mp-race-result-wait?`) and takes it (`mp-race-won?`); `update-lap-distance` starts with `mp-race-mirror!` (a guest's racers but its own follow the host's race) (4.15). |
| `goal_src/jak3/engine/ui/text-h.gc` | Text ids `#x1500`-`#x1514`, `#x1520`-`#x152c`, `#x1530`-`#x154e`, `#x1550`-`#x155f` and `#x1560`-`#x1597` for the menu labels, the prompts, the game modes, the player list, the lobby, the keyboard, the co-op pages, the countdowns, the Appearance page, the co-op lines, the session's end, the session status, Kleiver's shop and the race start's line (`#x1594`), the spectating lines (`#x1598`, `#x1599`), the merchant's name (`#x159a`), the result waiting for the others (`#x159b`) and the acts of the
mission pages (`#x159c`-`#x159f`), Daxter's L1 + triangle prompt (`#x15a0`), a failure's votes (`#x15a1`-`#x15a3`), the internet address, door and mission waits (`#x15a9`-`#x15ad`), the shop's mod answer and owned gun mod (`#x15ae`, `#x15c1`), the chat (`#x15af`-`#x15bc`) and the co-op saves (`#x15bd`-`#x15c0`); `#x1595`-`#x1597` removed with the ride options of a race start. `*fallback-text-heap*` raised from 80 KB to 128 KB: the English text, loaded as every language's fallback, outgrew it with the mod's strings (`0COMMON.TXT` 78.6 KB), and every boot stopped in `c_symlink2` after loading it. |
| `goal_src/jak3/engine/gfx/foreground/foreground.gc` | `*pc-merc-outlines*` (eight draw-controls, colors, Dark Jak levels, `off?`) and `pc-merc-bits outline`, `dark-skin`, `skin`: `pc-merc-draw-request` flags those models and puts their color at byte 20 of the flags, their Dark Jak level at byte 18 (4.9, 4.11); `*pc-merc-skins*` (24 draw-controls, 16 tints each): their tints after the flags (4.12). |
| `game/graphics/opengl_renderer/TextureAnimator.cpp`, `.h`, `OpenGLRenderer.cpp`, `.h`, `foreground/Merc2.cpp`, `.h`, `loader/common.h`, `shaders/merc2.vert`, `.frag` | Dark Jak's skin built at 17 levels for a model flagged `dark-skin` (4.11); the outfit tints on the textures of each group (4.12), the cutscenes' high resolution Jak and Daxter included (4.17). |
| `goal_src/jak3/levels/desert/race/course-race.gc`, `levels/desert/artifact-race/artifact-race.gc` | The race's start waits on `mp-race-hold?`; then `mp-race-bind-players!` (course-race.gc) puts the other player in a racer slot (4.15). |
| `goal_src/jak3/engine/scene/scene.gc` | The scene player's wait loop also waits on `mp-scene-hold?`: a mission's introduction held until every player is there (4.24). |
| `goal_src/jak3/levels/desert/wvehicle/wvehicle-race.gc` | The racers' AI reads every other racer's forward direction through `mp-racer-fvec!`: another player's puppet in a racer slot has no rigid body (its `rbody` read crashed every racer, 4.15). |
| `game/graphics/opengl_renderer/foreground/Merc2.cpp`, `.h`, `Shader.cpp`, `.h`, `OpenGLRenderer.cpp`, `shaders/merc2_outline.vert`, `.frag` | The outline pass (`Merc2::draw_outlines`), run after the `generic-warp` bucket (4.9). |
| `game/kernel/jak3/kmachine_extras.cpp` | `pc_set_levels` adds the levels kept for a distant player's models (`mp::extra_render_levels`). |
| `game/graphics/pipelines/opengl.cpp` | The renderer keeps 8 more levels for Jak 3 (`fr3_level_count`); the chat holds the keyboard while a line is typed (`mp::chat_wants_keyboard`, `mp::chat_sdl_event`, 4.35). |
| `game/graphics/opengl_renderer/foreground/Shadow2.cpp` | An engine fix: a shadow too big for the VU program is skipped to the next bucket (`!=`); upstream's skip loop tested `==`, skipped nothing, and the bucket dispatch then asserted (`dma.current_tag_offset() == m_render_state.next_bucket`): the guest's game stopped in the palace ruins (2026-10-20 play test). |
| `game/assets/jak3/text/game_custom_text_en-US.json`, `fr-FR.json` | Their English and French strings. |

Wrapped at load time, each calling the stock version kept by `define-perm`:

| Function or method | File | Why |
|---|---|---|
| `enemy` `mark-as-dead` | `engine/common-obs/enemy-states.gc` | Share enemy deaths; a Metal Head's trophy for the player whose blow killed it (4.20). |
| `progress` `set-menu-options` | `pc/progress/progress-pc.gc` | The `'multiplayer` page, the title screen's Host Game and Join Game pages, the lobby (1.8). |
| `determine-pause-mode` | `engine/draw/drawable.gc` | Online, SELECT is the host's pause for everyone (a tap) and the player list (held); a guest's SELECT and START do nothing while the host paused (4.7, 4.10), nor during a cutscene, where the host's START pauses it for everyone (4.21). |
| `blit-displays-work` `set-menu-mode` | `engine/gfx/blit-displays-h.gc` | No still copy of the screen while the world runs under the START menu (4.10). |
| `progress` `draw-bg-box-alpha-1` | `pc/progress/progress-draw-pc.gc` | The Appearance page's background leaves its right part open for the preview (4.12). |
| `target-pos` | `engine/target/target-util.gc` | A co-op waypoint's owner sees us away from it until every player is there (4.14). |
| `part-tracker-init-base` | `engine/common-obs/generic-obs.gc` | Every one-shot effect starts there. |
| `projectile-init-by-other` | `engine/common-obs/projectile.gc` | Every projectile starts there (the stock one never returns, so the shot is sent first). |
| `process-entity-status!` | `engine/entity/entity.gc` | Broken crates and taken pickups mark their entity dead there. |
| `sparticle-launch-control` `spawn` | `engine/gfx/sprite/particles/sparticle-launcher.gc` | Every running particle group goes through it (`spawn-from-mat` and `spawn-from-cspace` too). |
| `sp-launch-particles-var` | same file | Every particle launch; only the direct ones (no launch control) are noted. |
| `sound-play-by-name`, `sound-play-by-spec`, `sound-stop` | `engine/sound/gsound.gc` | Every sound a process plays or stops. |
| `game-task-node-info` `open!` | `engine/game/task/task-control.gc` | Every task node closure (story progress). |
| `sync-linear`, `sync-eased`, `sync-paused` `get-norm!`; `sync-info` `get-current-phase-no-mod`, `get-timeframe-offset!`, `sync-now!` | `engine/util/sync-info.gc` | The phase of every cycle (shifted to the host's on a guest). |
| `com-airlock` `want-cross-airlock?` | `engine/common-obs/airlock.gc` | Doors open for remote players too; in co-op an open door waits for a teammate behind it (4.34). |
| `scene-player-init` | `engine/scene/scene.gc` | Every cutscene starts there; a mission's introduction asks the others first (4.24). |
| `level` `deactivate` | `engine/level/level.gc` | Drop what refers to a leaving level first; the art heap goes with its host level. |
| `auto-save-command` | `engine/game/game-save.gc` | No save while in another player's world or in free roam; the co-op campaign's saves into its file, its icon on every screen (1.7). |
| `menu-main-menu-option` `respond-progress` | `engine/ui/progress/progress.gc` | Online, the START ring's Save Game and Load Game open no memory card page (1.7). |
| `target-gun-compute-pos` | `engine/target/target-gun.gc` | Where the gun is drawn: in Daxter's hand, opened to the mod, while he holds it (4.8). |
| `joint-control-remap!` | `engine/anim/joint.gc` | The sidekick (Daxter's model in his mode) calls it after copying the player's clips: Daxter's clip holding the gun goes on his upper body there (4.8); on Jak's shoulder, the climb of a Daxter player (4.16). |
| `target-death-reset` | `engine/target/target-death.gc` | Co-op: a dead player back next to a living teammate, no task manager told (4.19). |
| `game-info` `give` | `engine/game/game-info.gc` | Out of co-op, a precursor orb taken is claimed from the host for our account (4.20). |
| `merc-eye-anim` | `engine/gfx/foreground/eye.gc` | Our player's own model feeds no eyes while it plays Daxter online: it wrote Daxter's eyes into the eye slot every Jak shares (4.2). |
| `restart-mission` | `engine/game/task/task-control.gc` | START > Restart restarts every player (4.21). |
| `spawn-resetter!` (`resetter-control`) | same file | A mission's failure shown on every machine, no auto-reset in co-op, a new vote (4.26). |
| `task-manager-init-by-other` | same file | A mission starting tells the others (4.24). |
| `lightning-tracker-init` | `engine/common-obs/generic-obs.gc` | A bolt to a focusable without the joint asked follows its position (joint 256): the dark blast sent one to another player's hurtbox at joint 3 and the game stopped (4.23). |
| `target-indax-stance`, `target-indax-walk` `event` | `engine/target/indax/target-indax.gc` | Their handler is replaced (a state keeps the one it was defined with): a lizard offered to a Daxter player starts the stock ride (4.22). |

Every wrapper only notes the call when a session is running, for a process the others draw.

The per-frame hook `*mods-menu-hook*` (`engine/game/main.gc`) is set to `mp-frame-hook`, which
calls `mods-menu-update` first. The title screen's list (`*title-pc*` `options`) is swapped for one
with Host Game and Join Game while the mod is enabled (1.8).

## 4. How it works in game

### 4.1 One frame

1. `mp-frame-hook` (every frame, paused or not): runs the test script if any, describes the local
   player, pumps the network, copies the remote players' interpolated states, asks for and loads
   the host's world on a guest (1.7), spawns the manager if missing (at most once a second when
   the process pool is full), draws names, health bars, prompts, notifications and the player
   list, then keeps the title screen's list (1.8).
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
stance, or to the crouch for a driver. A passenger sits (4.3). Daxter's puppet is never posed (his
model has fewer bones than the player's skeleton the pose is taken from): it plays his clip by
index, and his clip holding the gun on his upper body (`top-index`, `top-frame` in his state, 4.8).
Its `mp-hurtbox` child sits in the actor hash, where the player's attacks look. A model change
(character, outfit) respawns the puppet.

Eyes: every model of Jak shares one eye slot (`*eye-control-array*`, found by art group name),
which the last model to animate fills. A Jak puppet animates its eyes (`joint-control-status
eye-anim`); our player as Daxter keeps Jak's model, hidden, playing Daxter's clips, and the
`merc-eye-anim` wrapper keeps it from writing Daxter's eyes into that slot (the other players'
Jaks showed them).

The hurtbox is a `process-focusable` of kind crate, in the actor hash: its collide shape is made
with `(collide-list-enum usually-hit-by-player)`, while the puppet's own `hit-by-others` shape is
not in it. The area weapons search the actor hash for focusables of given kinds (crate, enemy,
civilian...) and find the hurtbox, which hands every blow to the puppet. It answers no target
search (`get-search-info-flag` returns none: neither the guns' aim nor an enemy picks it) and
gives its sphere's center as its aim point (`get-trans`: the stock method reads a draw-control,
which the hurtbox has not). A blow sent by a mass inverter's spinner (a child of what it lifts)
counts as the player its attack-info names; a blow doing no damage (the lift itself) is no hit.

Dark Jak and Light Jak: the player's state carries, in its `powers` field, the Dark Jak skin's
level (bits 0-7), the Dark Jak and Light Jak blends (`darkjak-interp`, `lightjak-interp`), the
light look (no textures, rim lights), the invisible fade, the scarf and the goggles
(`mp-pack-powers`). The puppet applies them as `target-draw-process` does: blend-shape overrides
for the face and claws, the global effects, the fade, Dark Jak's skin at its player's level
(4.11), and draws the auras on its own body: dark sparks and lightning, Light Jak's glow
(`mp-puppet-apply-powers`). The override array lives in the puppet's heap and
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
  does it send `steal`: the driver gets out, hides its vehicle at once, never sends it again and
  removes it once out (`*mp-stolen-vehicle*`); the thief drops the copies of that vehicle it still
  receives for a moment (`mp-stolen-copy?`). Before, the stolen vehicle could show twice. Only the
  player vehicles: city bikes and cars (types 0-6) and Spargus buggies (12-19).
- **NPC vehicles:** a player presses triangle next to the replica of another authority's NPC
  vehicle, gets the same vehicle, and that authority removes its own (`vehicle-taken`).
- **Daxter at the wheel:** a player playing Daxter gets in with the stock
  `'change-mode 'pilot-daxter` (`pilot` with `as-daxter?`: the target's art becomes Daxter's
  driving art), whose animations are now in GAME (section 3); `mp-pilot-mode` picks it from the
  character. The indax mode ignores a pilot request, so Daxter first ends it and asks once
  standing (`take-id`, `board-veh` in the manager); next to a vehicle of his own world he gets in
  with triangle too. Getting off, he goes back to his mode. Others draw the vehicle with Daxter
  driving, from his state (`mp-playing-daxter?`).
- **Ride (L1 + triangle, himstan's binding):** from the ground (standing or crouched), in the
  lowest seat of the vehicle no other player rides (`mp-free-seat`): seats 1 to 3 (a city hover
  car carries three passengers), or, on a one-seat vehicle (the turtle, the toad...), its back,
  0.45 m above and 0.8 m behind the driver's seat. Seats are reserved as in himstan's
  `mp-vehicle-seat-reservation.gc`: a passenger's state carries its seat (flags bits 11-12), and
  when two players take the same one at once, the lowest slot keeps it and the other moves to the
  next free seat, or gets off when there is none. The driver's state carries seat 1 in
  `seat-trans` and seats 2 and 3 relative to the vehicle in `seat-offset` (cm, its frame;
  `mp-seat-pos`). An invisible `mp-seat` follows that seat and the player rides it with the stock
  `target-grab-ride` state, or Daxter with `target-indax-grab`. Each machine draws the passenger
  in its seat as it sees that vehicle, facing its way (Jak's `grab-ride` keeps the facing he got
  in with), our own model too (the others get its pose).
- **Seated passengers** (`mp-seated-index`): the passenger's model plays the clips a driver of that
  vehicle sits in (`target-pilot-stance`), steering straight (their middle frame), leaning neither
  forward nor back (the forward clip and the back clip at the next index, half each): Jak's by
  vehicle class (`jakb-pilot-bike-*` hover bikes, `jakb-pilot-car-*` hover cars,
  `jakb-pilot-wcar-*` wasteland cars), Daxter's in every vehicle the clips he drives with
  (`daxter-pidax-missile-turn-*`, resident). Both are placed from the seat point: Daxter's
  `daxter-pilot-*` clips are not, they seat him next to Jak from Jak's shoulder. Jak's pilot clips
  come with the levels that have vehicles; without them, Jak crouches and Daxter stands at the
  seat plus an offset (`mp-daxter-seat-offset`: 0.4 m up and 0.3 m back on a bike, 0.4 m back in
  a car, adapted from the `daxter-seat-offsets` of himstan's `target-pilot.gc`).
- **Vehicle guns:** a vehicle's shells and shots are spawned under `*rigid-body-queue-manager*`
  with the vehicle as owner, not under the player: they are collected there too
  (`mp-collect-vehicle-shot`), and `mp-from-local-player?` follows owners (shot, explosion,
  vehicle) up to our player, so their blows on another player count as ours.
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

### 4.7 Player list and ping

Holding SELECT while online shows every player, ourselves first, with its color, name, health (a
bar colored as over its head, and the value: `8/8`) and round trip to the host
(`mp-draw-player-list`). The binding and the layout (a title, NAME and PING
columns) follow himstan's `mp-player-list.gc`. The stock pauses on SELECT
(`determine-pause-mode`): the wrapper clears SELECT alone while online; the list shows once
SELECT is held 0.25 s, and a tap is the host's pause for everyone (4.10). L3 + SELECT still opens
the Mods menu.

The runtime measures the ping (protocol version 6): each side sends `MSG_PING` every second and
answers `MSG_PONG`; the round trip is smoothed (0.7 old, 0.3 new). A client puts its own round
trip in its state (`ping-ms`), so `pc-mp-player-ping` gives, on the host, each client's measured
round trip; on a client, its own and the others' reported values; 0 for the host.

### 4.8 Daxter's guns

The stock playable Daxter cannot use the gun: his surfaces (`*indax-walk-mods*`...) carry
`gun-off`, and the gun process's `use` state calls `target-gun-check` every frame, which pushes
Jak's gun animations onto the player's skeleton (Daxter's while he plays). So the stock gun stays
put away for him (`gun-type` 0) and `multiplayer-guns.gc` fires instead
(`mp-daxter-gun-update`, run as the player from the manager):

- *The mod:* the stock marking menu (`target-gun-marking-menu`, a pad engine connection) runs in
  his mode too and sets `using-gun-type` on the D-pad; R1 fires it. The ranges each mod's shots and
  aim use (`fire-range`, `track-*`), which `target-gun-type-set!` sets for Jak only, are set
  when the gun comes out (`mp-daxter-gun-setup`). A D-pad press takes the gun out too, the mod
  just chosen in his hand, without firing (2 s, as after R1); before, the mod changed unseen until
  he fired (`daxter-moves`). The mod's name and ammo show above the prompts (`mp-draw-daxter-gun`,
  y 292; the prompts are at 320).
- *Aim:* from his chest toward the nearest enemy, guard, civilian or crate within 40 m in a
  60-degree cone ahead (`fill-actor-list-for-box`, `find-nearest-focusable`, as
  `target-gun-find-track`), else straight ahead; the target found is the gun's tracked target
  for homing shots. `fire-point` is 0.6 m ahead, `fire-dir-out` the aim.
- *The gun in his hand:* while he holds it (2 s after R1 is let go), `target-gun-compute-pos`
  (wrapped, called by the gun's post) puts it at his gun joint (the sidekick's node 28) at that
  joint's scale with `gun-daxter` 1, as the stock does when he fires from a vehicle, and sets the
  gun's clip to the mod's open shape (`gun-idle-red-ja`...: the gun's `idle` state holds its folded
  clip, and its post runs after its code). The gun's post hides it while the player is hidden,
  which his mode is (`no-draw-bounds2`): the manager shows it again, after it (`*pc-pool*` runs
  after the other pools).
- *His clips:* the stock ones he fires from a vehicle with, by color: `daxter-pilot-gun-<color>-idle`
  while he holds it, `-fire` from a shot for its length (looping while the vulcan fury, the arc
  wielder or the needle lazer fire), from `jak-pilot-gun+0-ag`, moved to GAME. On his upper body
  only (upper_body to the gun joint, 3-27, and the thumbs, 44-47): the sidekick copies the
  player's clips then calls `joint-control-remap!` (wrapped), which adds a blend channel weighted 0
  in the first sub-frame and 1 in the second, selects those joints (`interp-select`) and computes
  the frame twice (`generate-frame-function` `create-interpolated2-joint-animation-frame`, as
  `top-anim` does). His legs keep his mode's clip. The others draw the same on his puppet from
  `top-index` and `top-frame`.
- *Firing:* the stock per-mod functions (`target-gun-fire-yellow`, `-red`, `-blue`, `-dark`), as
  `target-gun-fire` calls them but without Jak's animations, the ammo taken as it takes it, at
  the stock rates; the vulcan fury and the needle lazer fire while R1 is held, the arc wielder
  holds one arc (its tracker reads `using-gun-type` 33 and `gun active?`, set meanwhile), the
  others fire once per press.
- *The charge weapons:* the wave concussor's charging state goes off after 0.1 s in his mode, and
  the peace maker's shot fizzles at once (both check the player is not in `indax` mode). Daxter
  fires subtypes, `mp-daxter-shockwave` and `mp-daxter-dark-shot`, whose overridden states
  (`:virtual #t`) charge at `fire-point` while R1 is held and go off or fly on release, the
  dark shot along his aim (`moving` `:enter`: the parent's, then the velocity turned).
- *Feedback:* the mod's name and its ammo at the bottom of the screen while he holds the gun (his
  mode has no gun HUD).
- *Where:* on his feet in his mode, on a Jak's shoulder or riding along (his grab state,
  `*mp-perch-slot*` or `*mp-ride-slot*` set: the shots leave from the shoulder or the seat), and
  on a lizard (4.22). On a shoulder the others see the shots, not the gun: the Jak's own Daxter
  they draw there holds none.

Every weapon (Mods menu): the gun, its twelve mods and their ammo upgrades
(`MP_ALL_GUN_FEATURES`), full ammo; kept through a respawn (in another player's world a respawn
restores that player's save) until the title screen. Jak takes them out as usual.

### 4.9 Outlines through walls

Every remote player's puppet puts its draw-control and color in `*pc-merc-outlines*` (its slot,
each frame; cleared when it goes). `pc-merc-draw-request` flags those models (`pc-merc-bits
outline`, the color at byte 20 of the flags), and Merc2 keeps each one with its bones and camera
(`record_outline`). Once the 3D scene is drawn (after `generic-warp`, before the overlay's
`debug-no-zbuf1`), `draw_outlines` draws them again with `merc2_outline` (merc2's skinning and
projection, one color, an optional screen offset):

1. every silhouette into the stencil (1), whatever the depth;
2. where the model itself is what the depth buffer shows (its parts in view, not those it hides
   itself), stencil 2: depth `GEQUAL`, pulled toward the camera by a polygon offset (drawn again,
   a model comes out a hair behind its first drawing);
3. a faint fill (alpha 0.18) where the stencil is still 1: the silhouette hidden by something
   else;
4. the outline: the model shifted by 2 pixels (more on large screens) in eight directions, where
   the stencil is 0 (outside every silhouette) and the depth is behind what is drawn (`LESS`:
   depth is reversed here, `GEQUAL` is in front), each pixel once.

An occlusion query counts the pixels of steps 3 and 4 (`pc-mp-outline-pixels`, the last frame's).
Measured (`outline`): about 100 pixels for a player in the open 9 m away (its feet in the ground),
900 to 1850 for the same player behind a buggy, whose outline shows through it (screenshot).
`MP_OUTLINE_ALWAYS=1` draws them whatever the depth (a test of the drawing alone). Outlines are
not drawn while a menu is open (`*pc-merc-outlines*` `off?`).

### 4.10 Pause for everyone, START over a running world

Online, nobody stops the shared world alone (multiplayer.gc, "Pause for everyone"):

- **START** opens the menu while the game goes on behind it, for the player and for the others.
  The menu stops every process masked `progress` (its `process-mask` setting,
  `progress-init-by-other`); a setting of ours in `*setting-control*` `engine-hi`, applied after
  it, clears that bit again. Its sounds go on, and the screen behind it is the live game: the
  `set-menu-mode` wrapper leaves the still copy of `blit-displays` off. The world must not read
  the pad meanwhile: the `mp-input` process runs first every frame (its pool, `*mp-input-pool*`,
  is the newest child of `*active-pool*`, which the kernel runs first) and hides pad 0 from
  everything after it (the player, the camera, the NPCs); the hook gives it back before the menu
  reads it (the display process runs the hook, reads the pads, then runs the menu, its child).
  The PC port also keeps a high-fps history of the buttons (`*cpad-history-list*`, filled by
  `service-cpads` before any process runs), which the buffered checks read
  (`recently-pressed?`: the jump): it is emptied too. Before, X choosing a menu entry made the
  player jump, and the test pad never showed it until X was pressed in the menu (`menu-buttons`).
  The pad stays hidden while the menu process lives (not only in the `progress` master mode),
  and 0.3 s after it closes (the button that closed it).
- **The host's SELECT** (a tap; held 0.25 s, the player list) pauses every player's game: the
  stock pause (`'pause` master mode), "The host paused the game" on a guest's screen, "SELECT:
  resume the game for everyone" on the host's; START and SELECT do nothing there for a guest.
  SELECT again resumes it 3 s later, "The game resumes in 3, 2, 1" on every screen. The host's
  state carries both (flag `paused-all`, field `resume`).

### 4.11 Another player's Dark Jak

Dark Jak's grey skin is a texture morph of the `darkjak` group (`jakc-arm`, `-eyebrow`, `-face`,
`-finger`, `-hair`) that every model of Jak shares: the local player's
(`set-darkjak-texture-morph!`). The renderer builds that skin at 17 morph levels per texture, as
the group's blenders do (`TextureAnimator::setup_darkjak_variants`), and Merc2 draws a model
flagged `dark-skin` with its own level instead of the shared slot (`pc-merc-bits dark-skin`, the
level at byte 18 of the flags). Each remote puppet is flagged through `*pc-merc-outlines*` (its
`dark` field), its level taken from its player's state (`powers` bits 0-7: the morph the game
sets, 1 under the dark eco powerup). The face, claws, horns and arms follow from the blend shapes
and the pose (4.2). The local player's Dark Jak no longer darkens the other players' models.

### 4.12 Appearance page

Inspired by the skin editor of himstan's Jak and Daxter II Multiplayer
(`goal_src/jak2/multiplayer/ui/mp-lobby-skin-editor.gc` and its `merc2.frag` tint, ISC),
written anew for Jak 3 (`multiplayer-menu.gc`, page `'mp-skin`):

- **Page:** Options > Multiplayer > **Appearance**, also from the lobby's multiplayer options. L2 /
  R2 pick the part, shown between them as in himstan's editor ("<L2>  Jak: Shirt  <R2>"; Jak:
  shirt, armor, pants, boots, scarf, straps and belts, gloves and wraps, hair, goggles; Daxter:
  fur, light fur, belt); circle switches between Jak and Daxter, each back on the part last
  edited of his; up / down pick a slider (red, green, blue, tint strength, 0-255, "stock" at 0;
  held: repeats), left / right move it (held: repeats after 0.25 s), L1 / R1 by large steps. A
  swatch beside the sliders shows the color. X keeps the outfit, triangle puts back the one the
  page opened with; the keys are listed on two lines at the bottom.
- **Preview:** the character turns in the 3D scene, 2.6 m before the camera (a `manipy` of Jak's
  or Daxter's model under `*mp-input-pool*`, which runs under the menu; the title screen's light;
  no cloth; spawned with `:stack-size #x20000`, the room a puppet has: with `#x10000` Jak's model
  fails to set up). HUD models go through the generic renderer, which has
  no tint, so the page's background (`draw-bg-box-alpha-1`, wrapped) covers only its left 60 %:
  it writes depth and would hide the preview.
- **Renderer:** a part is a group of textures (`Merc2.cpp` `mp_skin_group_of`, by texture name;
  groups 1-9 Jak, 10-12 Daxter). A model listed in `*pc-merc-skins*` gets its 16 tints (rgb, the
  strength in the top byte) after its flags (`pc-merc-bits skin`), and `merc2.frag` draws each
  texture of a tinted group with himstan's grayscale-detail tint (`merc2.vert` passes the vertex
  color and the light apart).
- **Network:** the outfit is saved in `multiplayer.cfg` (`skin=`) and sent to every player who
  joins and on each change (`skin` event). Every frame our model and our Daxter, the puppets and
  the Daxter copies of the other players wear their player's outfit (`mp-update-skins`).

### 4.13 Co-op: skipping a cutscene together

Cutscenes are shared within a region (1.6). While another player watches the same one, triangle
no longer skips it at once as the stock does (`scene.gc`, `scf1`): it asks to skip it ("Skip the
cutscene (1/2)" at the bottom, then "Skip: waiting for the others (1/2)"), and once every player
watching it asked, each machine skips its own (the scene-player's `'abort`). Each player's state
says it watches a cutscene that can be skipped (flag `in-scene`) and whether it asked
(`skip-vote`). The triangle press is taken from pad 0 by `mp-input` before the scene-player reads
it. Alone, triangle skips at once.

### 4.14 Co-op: waypoints reached together

A mission marks where to go with a yellow beam (`task-arrow`). The beam only draws; the process
that spawned it (a task manager, an object of the mission) checks the arrival itself, almost
always with `(target-pos 0)` and a radius of 3 to 40 m. In co-op, until every player stands
within 6 m of the beam (xz), the `target-pos` wrapper answers that process alone with our
position pushed away from the beam, as far as the farthest player, 45 m at most: above every
arrival radius, below the distances that fail a mission for going too far. The mission goes on
once everyone is there. A player still loading the host's world counts as away. The beams are
listed every frame from the process tree (shown ones: not `taf4`). "Players here: 1/2" shows at the
bottom within 30 m of a beam, the count of the nearest one; the log says when a held beam is
released ("every player at the waypoint (2/2)").

### 4.15 Co-op: races

In co-op, every player races in a vehicle of its own; the players count in each other's place,
and the race is won only when they take the first places (multiplayer.gc, "Races"):

- **A vehicle each.** A race continue point (`continue-flags race`) gives its vehicle where the
  player stands at the end of `target-continue`: each machine running the race gives its own
  player one, Daxter included (he drives as Daxter). A guest's player is moved back first (6 m
  per slot): the player starts last on the grid, the racers stand ahead and beside it (moved
  aside, the guest's turtle fell over against a racer). A player on foot within 60 m of a player
  whose race start waits gets a vehicle of that player's type beside it (every 20 s at most, not
  when it drove in the last 30 s: its own waits where it left it); Daxter leaves his mode first
  (it ignores `'change-mode 'pilot`) and drives as Daxter. A guest never puts the vehicle it
  drove last to sleep with the NPCs it leaves to the host (`mp-suppress-actor`): it stays where
  the player left it, visible, and the player gets in again.
- **No other player's vehicle.** From the start's wait to the finish (`mp-race-context?`: the
  start waits, or the race reported within 1 s), no steal, no ride along, no shoulder: a
  passenger or a Daxter on a shoulder is put down, `mp-update-vehicle-interaction` offers no other
  player's vehicle (our own is still boarded, Daxter included) and `mp-update-perch` no Jak.
- **The start waits.** The race's task manager (`course-race.gc`: Kleiver's race, the beast
  hunters' time trial and rally; `artifact-race.gc`: Damas's races) loops on `mp-race-hold?` before
  the start: #t while, in co-op, a connected player does not drive; a player still loading the
  host's world does not. "Waiting for every player aboard: 1/2" shows at the bottom (flag
  `race-wait`), and above it "Every player races in their own vehicle" (`#x1594`). Once every
  player drives, "The race starts in 10" counts down (again when a player leaves its vehicle).
  Meanwhile the players may leave their vehicle (a `pilot-exit #t` setting in `engine-hi` over
  the race's). Offline, alone or in free roam, the stock start.
- **One race, the host's,** as in himstan's Jak and Daxter II Multiplayer (his
  `race-mp-sync.gc`: players in racer slots, the host validates). When the start is released
  (`course-race.gc`, `mp-race-bind-players!`, after `mp-race-hold?`), the other player takes the
  racer slot before ours on both machines: Kleiver's race and the rally have their player in slot
  4, so the guest takes slot 3, whose racer is removed (`'go-die`, as `kill-npc-racers` does). The
  host binds the guest's puppet there (`init-racer!`: a process-focusable, which the stock lap
  counting follows by its position; its death or its leaving, a puppet dead or gone, puts it out
  of the race, `rsf2`). A guest binds its own vehicle there, the host's puppet in slot 4, and
  becomes slot 3 (`i-player`). The `'force-start` the task manager sends skips the racers'
  `'test-ready` (`rf4`), which a puppet does not answer. The racers' AI steers clear of every
  other racer from its position, speed and forward direction (`wvehicle-race.gc`): a puppet's
  forward direction is its orientation's (`mp-racer-fvec!`; read as a rigid body's, it crashed
  every machine at the start). A race of one racer (the time trial) binds
  nothing: each player's own, with its stock result.
- **The host's race counts.** Every frame the manager's `active` post (`race-manager.gc`) gives
  each racer's progress (laps done and the part of the lap, or 1000 minus its finish order,
  `mp-racer-progress`) and bits (started, finished, out of the race, `mp-racer-bits`):
  `mp-race-slot`, `mp-race-sync`. The host sends them (`race-progress`, iarg 1 0: 3 bits a racer
  in iarg 2, its verdict above bit 24, the progress as 8 floats in the text) 10 times a second and
  at once when a racer's bits change. A guest's race takes them for every racer but its own
  (`mp-race-mirror!` at the top of `update-lap-distance`): laps, finishes and leavings, so its
  places (the stock HUD's 1st to 5th, `race-position = rank` again) are the host's, the players
  included. Its own racer it counts itself (its HUD, its laps, its turbo pads).
- **Result: the host decides** (`mp-race-decide`) once both players finished or left the race,
  or 60 s after the first finish: won when a player finished first (his rule); a race won by score
  (the rally, `rif6`), when a player's score was good enough: each player's own result, which a
  guest sends at its finish (`race-progress`, iarg 1 1). Every player's race, finished, waits for
  that verdict ("Waiting for the other players to finish", `mp-race-result-wait?`, 90 s at most,
  its own result then), and `mp-race-won?` gives it to the stock `win` or `lose`. The score is
  saved at our finish (before the wait).

Checked by `race-finish` and `race-finish-win` (Kleiver's race driven to the finish by the racers'
AI on both machines): one race, the same places on both screens, the host's verdict lost then won,
applied on both machines.

### 4.16 Daxter on a Jak's shoulder

After the ride-along of himstan's Jak and Daxter II Multiplayer
(`goal_src/jak2/engine/target/daxter/states/daxter-ride-along.gc`,
`goal_src/jak2/multiplayer/player/mp-player-visibility.gc`, ISC), multiplayer.gc "Daxter on
Jak's shoulder":

- **No Daxter of the game on a player Jak.** Online, a Jak player's own Daxter is taken off
  (`mp-update-session`: the manager holds `(set-setting! 'features 'clear 0 #x100000)`, the
  sidekick bit, as `target-death.gc` does; the saved features are untouched), in every mode,
  unless a Daxter player rides on that Jak's shoulder (`mp-rider-on`).
- **Climbing on.** A Daxter player standing or walking in his mode, on the ground (not in the air,
  grabbed, hit or dead), presses triangle within 2.5 m of a crouching Jak (`mp-jak-crouching?`:
  Jak's duck animations 32 to 34, on foot) or 3.5 m of a driving one; in co-op, or with L1 held,
  within 5 m of the vehicle a Jak drives (4.15). Next to any other Jak on foot (standing,
  walking), within 2.5 m: L1 + triangle, at any time ("Press L1 + triangle to jump on Jak's
  shoulder", `#x15a0`, `mp-jak-on-foot?`, `*mp-perch-l1?*`); triangle alone stays the vehicle's
  and the merchant's there. He goes into the stock grab state
  (`target-indax-grab`), following the puppet's root 1.25 m up and 0.22 m to its left
  (`mp-update-perch`), hidden: the Jak's own Daxter, shown again and wearing the rider's outfit
  (`mp-skin-add-copies`, `mp-update-skins`), sits there on every machine, so the camera and the
  pose are the stock ones. It plays the stock climb: Daxter's clips 420 then 427
  (`daxter-darkjak-get-off`, `-end`, the climb back onto Jak's shoulder after Dark Jak), forced on
  the sidekick's channel 0 at each clip's own speed (`mp-climb-anim!`, from the
  `joint-control-remap!` wrapper; 159 frames), started when a rider appears on a Jak not in a
  vehicle.
- **Off.** X, his death or the Jak hidden puts him down next to Jak. A perched Daxter is hidden
  but present: he counts as there for the players' distances (no longer taken for a player still
  loading).
- **Firing** from there: 4.8. **Not in a co-op race:** 4.15.

### 4.17 Cutscene outfits

After himstan's `goal_src/jak2/multiplayer/player/mp-cutscene-appearance.gc` (ISC), on every
machine (`mp-scene-skins`, called from `mp-update-skins`): the scene player's actors named
`jakc-highres`, `jak-highres`, `darkjak-highres` or `jakc-scarf` wear the host's outfit (the host
is the story's Jak), and `sidekick-highres` the outfit of the player who plays Daxter, or no tint
(the stock Daxter) when every player is a Jak. The actor's name and handle are checked before
`handle->process` (a handle of 0 crashed it). The renderer's tint groups list the cutscene
models' textures too (`Merc2.cpp`: `jakchires-*` in Jak's groups, the high resolution Daxter's in
his).

### 4.18 Texts that fit

After `fit-overlay-text` of himstan's `goal_src/jak2/multiplayer/ui/lobby-browser.gc` (ISC):
`mp-fit-text!` (multiplayer.gc) cuts a string to a width with "..." measured with the font
context it is drawn with. The player list fits each name in 130 px before its health bar; the
lobby fits a name before its status column and the chosen player's details to their panel;
`mp-lobby-print-fit` (multiplayer-menu.gc) draws a line of game text smaller, down to 60 %, rather
than letting it run into the next column. The session status line uses texts `#x1582` to `#x1585`.
Kleiver's shop right-aligns its prices at the page's right edge.

### 4.19 Co-op: back next to a teammate

After the respawn policy of himstan's mod (`goal_src/jak2/multiplayer/player/mp-respawn-policy.gc`,
`mp-death-spectate.gc`, ISC), multiplayer.gc "Co-op: back next to a teammate": the end of a
death, `target-death-reset`, is wrapped. Its stock version tells the task managers ('target 'die,
which fails a mission that fails on death), then resets the game at the continue point. In co-op,
a player who dies on foot while a teammate is alive in game within 300 m (`mp-living-teammate`,
the nearest) comes back 2 m from that teammate, health full, invulnerable for 3 s, in
`target-stance`, and no task manager is told: a mission goes on while one player lives. Dying
alone, or in a vehicle, stays the stock (the continue point; the mission fails if it does so).

Before coming back, the dead player watches that teammate for 5 s, as himstan's death spectate
does: its body stays where it fell, the camera follows the teammate's puppet (`'change-target`),
and "Spectating: <name>" with "Back in N" shows at the top (`mp-draw-spectate`, `#x1598`,
`#x1599`). A teammate who dies meanwhile is left for another; with none left, the stock death
follows.

### 4.20 Economy: orbs, trophies, Kleiver's shop and the gun's mods

multiplayer.gc "Economy", multiplayer-guns.gc "Every weapon", multiplayer-menu.gc "Kleiver's shop",
`kmultiplayer.cpp` `mp::Economy` and "Players' identity". A proof of concept of the economy the
user plans (rare items, customization, more features), for free roam alone:

- **Free roam only.** Co-op has no accounts: `mp-econ-update` opens them for a host out of co-op
  (free roam, session key `free`; a world hosted from a game, the tests' way, key `world`); a
  co-op guest claims no orb (`give`) and sees no Kleiver.
- **Players' identity.** Each player draws a secret id once (`uid=` in its `multiplayer.cfg`) and
  each server an id of its own (`server=`), both from GameNetworkingSockets' random source
  (`CCrypto::GenerateRandomBlock`). A guest's `HELLO` carries its protocol version alone; the
  host's `WELCOME` carries the slot and the server id; the guest answers `IDENTITY`: its account
  id there, the first 8 bytes of HMAC-SHA256 of the server id keyed by its secret
  (`mp::account_id`, GameNetworkingSockets' `CCrypto::GenerateHMAC256`). The secret never leaves
  the player's machine: the player finds its account on that server whatever its name, slot or
  address, and a server can neither tell its secret nor pass for it on another server. As on a
  Minecraft or Terraria server hosted at home, the host is trusted with what it keeps: it can
  change any account.
- **Accounts.** Every player has precursor orbs, Metal Head trophies and items bought. The host
  alone keeps them; a guest only shows what the host sends it (`econ` event). No event of a guest
  changes an account but an orb claim and a purchase, which the host checks.
- **Orbs.** Every orb taken goes through `game-info` `give` ('skill, with the pickup's handle),
  wrapped: the taker claims it from the host with the orb's entity (`econ-pick`). The host refuses
  a claim whose entity is not a precursor orb (type `skill`) in its world; otherwise it holds it
  (8 at most) until it sees the claimant within 8 m of the orb (it sees a guest a little late, so
  the claim can come first), then credits one orb if that orb was never credited in this session
  (`pc-mp-econ-take-orb`: the orbs credited are kept with the accounts, so each orb pays once,
  whoever took it, whenever). Not seen there within 2 s: refused. A claim from afar, of an orb
  not taken or taken before, credits nothing.
- **Trophies.** A Metal Head (an enemy that can carry a skull gem: `enemy-info` `gem-joint` above
  0) dying in the host's world from a player's blow (its attacker: the host's player and what it
  fired or drives, or a guest's puppet hitting it through `actor-hit`) gives that player a trophy
  (the `mark-as-dead` wrapper).
- **The server's database.** `<settings>/multiplayer-server.db` (SQLite, `third-party/sqlite3`,
  WAL journal): `accounts` (session key, account id, name, orbs, trophies, items), `orbs_taken`
  (session key, orb entity) and `ledger` (every change: when, who, what, the amount, the balances
  after). Each credit, purchase or orb taken is written at once, in one transaction: a crash or a
  game closed without ending the session loses nothing. Each closing also adds a line to
  `<settings>/multiplayer-sessions.log`. Hosting again loads the session's accounts, and each
  player who comes (or comes back) gets its balances once (`sent` bits). The JSON file of earlier
  versions (`multiplayer-economy.json`, accounts under the secret ids) is imported once, under the
  account ids, then renamed `.imported`.
- **The gun's mods.** Free roam starts every gun color at its first mod: `MP_FREE_FEATURES` holds
  `MP_FREE_GUN_FEATURES` (the first mods and the ammo upgrades), and `mp-free-gun-mods` takes the
  others the story's end gave away, every frame, on every machine for its own player. The second
  and third mods of each color are shop items 4 to 11 (`*mp-shop-gun-bits*`: red, yellow and blue
  15 then 30 orbs, dark 20 then 40); a third needs its second (the host answers 4: "Buy the mod
  before it first"). Bought, its `game-feature` bit is set for that player: the game offers a mod
  whose bit is in both `game-info` `features` and the setting's (`target-gun.gc`); pickup-type
  `gun-red-1` (26) to `gun-dark-3` (37) are bits 6 to 17. A mod chosen that went goes back to its
  color's first. In free roam the Mods menu's "Give every weapon" gives the mods bought only
  (`mp-allowed-gun-features`).
- **Kleiver's spot.** The host draws one of four spots of Kleiver's in Spargus when its accounts
  open (`*mp-merchant-spots*`: his garage at the doors, the lower city's square, his spot in the
  upper city, another place there) and sends it with the balances (`econ` event, `fvec x`): every
  player finds him at the same place, for the session. His own minimap icon (class 116, his face,
  without the missions' trail, kept on the minimap's edge when far, on the big map too) is owned
  by the manager at that spot, and "Merchant" ("Marchand") is drawn in yellow over it within
  80 m, as a player's name and above one standing there (`mp-draw-merchant-name`, `#x159a`). No
  accounts, no spot: a guest offline or in co-op drops one left from an earlier session.
- **Kleiver's model** stands at the spot, idle (`mp-update-merchant-model`). His art
  (`skel-kleever-highres`) is in the borrowed level `lkleever`, which his missions load into
  waswide's borrow slot 0 (`game-task.gc`: `(waswide 0 lkleever special)`): while his spot is
  out, the manager asks the same borrow with its own setting (`set-setting! 'borrow`;
  `apply-settings` runs `update-task-masks` when the borrow changes, which loads it) and drops it
  when the spot goes. The model is a `manipy` in `*entity-pool*`: when `lkleever` unloads, the
  level kills it with its art (`check-for-rougue-process`). He is solid (his `manipy-init`
  sphere, 1.2 m up and 0.9 m wide, made an `obstacle` with `solid` action, `mp-kleiver-solid!`):
  a player walking into him stops against him; the collision is off while our player stands
  within 1.2 m of him (a teleport or his spot moving onto it would trap it inside). He turns
  toward our player within 12 m, and his five idle clips (`*mp-kleiver-idles*`) follow one
  another every 6 s, blended 30 frames (`mp-kleiver-live`).
- **The host's money.** The Mods menu gives the host "Host: +100 precursor orbs" and "Host: +100
  Metal Head trophies" (`mp-econ-grant-self`): its own account only, while its accounts are open.
  The chat's `/give` and `/trophies` credit any player (4.35).
- **Kleiver's shop.** At Kleiver's spot, within 4 m, on foot: his line ("Kleiver: Got orbs? I've
  got just what you need.") and "Press triangle to trade". Triangle's other uses there come first
  (a cutscene, L1 + triangle to ride along, another player's vehicle or shoulder, one of our
  vehicles): his spot may be where the players start, by their vehicles. Triangle opens the
  START menu on one page, the shop (`activate-progress` with `'mp-shop`): the balances, then
  twelve items with their prices, as many rows as fit above the answer and the keys (8 in
  16:9, 5 in 4:3; the list scrolls with the choice): four outfits (5 to 10 orbs) and the eight gun mods by their stock names. X
  buys one (asked of the host, which checks the price, the account and a third mod's second) or
  wears an outfit owned (its tints over the outfit, saved and sent); a gun mod owned reads
  "Owned" (`#x15c1`). Triangle leaves.

### 4.21 Session flow: restart, quitting, the START page, cutscenes

multiplayer.gc "Restart for everyone", "Session ended", "Pause for everyone"; multiplayer-menu.gc:

- **Restart for everyone.** START > Restart (`restart-mission`, wrapped) on the host restarts its
  mission and sends `restart` to everyone: each player restarts too, then goes back next to the
  host. A guest's Restart asks the host, whose restart then goes to everyone.
- **A guest that quits leaves.** A guest that played in the host's world and is back at its title
  screen (START > Quit) leaves the server (`mp-update-session-title`, `guest-played?`); the others
  see it go. The host's own Quit ends the session (1.8).
- **The START page in game** keeps the first five options of `*mp-menu-all*` (status, name,
  character, color, appearance) in `*mp-menu-ingame*`: the `'multiplayer` route of the
  `set-menu-options` wrapper swaps the page's options by `(level-status? *level* 'title #f)`.
  The game mode, Host, Join and Leave entries are gone from that page: the lobby and the title
  screen do them.
- **The pad under the START menu.** The world reads no button and centered sticks while the menu
  is open (4.10): the player, its vehicle and its gun do not move (`menu-still`).
- **Cutscenes.** While another player may watch it too, a guest's START and SELECT do not pause a
  cutscene (its own fell behind the others'); the host's START pauses it for everyone, as its
  SELECT does, and resumes it (the `determine-pause-mode` wrapper).

### 4.22 Daxter on a lizard

The stock rides Daxter on a lizard only in the desert chase (`'wild` mode, the lizard running on
its own: `target-fldax-enter`, `engine/target/flut/target-flut.gc`). multiplayer.gc "Daxter on a
lizard":

- **Getting on.** A lizard waiting to be ridden offers itself with triangle (`'change-mode 'flut`,
  its idle state, `flut.gc`). Daxter's mode ignored it (`target-indax-handler`): his stance and
  walk now take it (`mp-indax-handler`, their state's `event`) and go to the stock ride
  (`target-flut-start`, mode `'normal`), so the lizard answers as it does for Jak.
- **Drawn as Daxter.** Once on, the player's art group is a copy of Daxter's (`mp-fldax-art`,
  made once: the art group's header and slots, its type included) whose slots of Jak's lizard
  clips (`jakb-flut-*`, 406 to 432) hold Daxter's (`daxter-fldax-*`, `*mp-fldax-map*`); the
  sidekick follows the player's bones (`'matrix 'indax`, `no-draw-bounds2`), as
  `target-fldax-enter` does. The lizard under him clones those clips by name (`joint-control-remap!`
  to `flut-saddle-fldax-*`). `mp-fldax-update` (from the manager, before his mode) puts that art
  on when the ride starts and the player's back once off; then his mode again.
- **His clips** are in `jak-fldax+0-ag`, which only DESLIZ loaded; WWD and VOCA, the levels with
  lizards to ride, load it too. In GAME its lizard clips found no lizard to link to ("could not
  find a master slot"), and the lizard under Daxter was hidden.
- **Firing:** R1, as on foot (4.8). **The others** see Daxter's puppet on the lizard (4.23).
- **Each player its own lizard.** A lizard is no NPC the host shares (`mp-actor-kind` 0): every
  machine runs its own. In co-op each lizard of a level near the player has a second one beside it
  (4.25).

### 4.23 Co-op: teammates and their vehicles

- **No friendly fire.** `mp-pvp?`: the server's PvP setting, never in co-op. A teammate's punch or
  shot on our puppet sends nothing, and a `pvp` event is ignored.
- **Their vehicles.** In co-op, a blow on a copy owned by a player (the vehicle it drives, its
  wheels: `mp-actor-flag owned`) is not sent (`mp-replica-hit`).
- **The dark blast** (Dark Jak's L1 + square, `target-darkjak-bomb1`) sends a lightning bolt to
  every focusable of kind crate, enemy, guard or vehicle within 80 m, at joint 3
  (`target-bomb1-fire-shot`), and the bolt follows that joint (`lightning-tracker` `update`).
  Another player's hurtbox (`mp-hurtbox`, a crate with no skeleton) is one: the bolt read a joint
  it does not have and the game of the player setting it off stopped (access violation in
  `vector<-cspace!` from `lightning-tracker`, reproduced by `dark-blast` before the fix). The
  `lightning-tracker-init` wrapper gives such a target joint 256: the bolt follows its position
  (`get-trans`, which the hurtbox gives). The blast then hits the other player (free roam, PvP
  on) or not (co-op), on both sides.
- **The lizard ridden** is a manipy under the player (`target-flut-start`) while the level's lizard
  hides: it is now sent with the player's props (`mp-collect-props`), drawn under its rider on
  every machine and never hit (props are not solid). Before, the others saw the rider on nothing,
  and the lizard they hit was their own copy left standing.

### 4.24 Co-op: missions started together

A mission starts when a player talks to whoever gives it: an open story node's `when-open` names
an NPC and the scene of its introduction (`engine/game/task/game-task.gc`); the NPC
(`process-taskable`) spawns the scene player, whose commands close the introduction at the scene's
end, and the mission's task manager starts then. In co-op, as himstan's scene pull-in, every player
watches that introduction (multiplayer-world.gc "a mission's introduction played by every
player"):

- **The vote.** Our scene player starting such a scene (`mp-pull-start` from `scene-player-init`,
  `mp-mission-intro-node`: the scene of an open node that is not a challenge's) holds it in its
  wait loop (`mp-scene-hold?`, one line in `engine/scene/scene.gc`) and asks the others
  (`mission-vote`, `mp-pull-msg` propose): "Host starts a mission: D-pad up to join, D-pad down to
  decline (15)" (`#x15ac`; D-pad up and down are taken from the pad before the game reads them,
  `mp-pull-input`). The mission goes on when at least half of the other players accept: with
  co-op's two players, the other one, so both players accept it. A no, or no answer within 15 s,
  counts against it. Meanwhile the starter reads "Waiting for the other players to accept the
  mission: 0/1 (12)" (`#x15ad`). Declined, the starter's player is released
  (`process-release?`) and its scene player dropped (`deactivate`, not `'abort`, which would run
  the scene's last commands and close the introduction): the mission does not start ("The mission
  was declined"), and its giver can be talked to again.
- **Everyone there.** Once it passed, every other player goes next to the starter
  (`*mp-request-teleport*`, through the starter's continue point when far) and says it is ready
  once the scene is loaded there (`scene-lookup`) and it stands within 40 m. Meanwhile the
  starter's screen shows "The mission waits for the other players: 0/1 ready" (`#x15ab`).
- **Together.** When every player is ready (30 s at most), the starter sends go and its scene
  plays; each other player starts the same scenes and continue point (sent as for any shared
  cutscene, 1.6, but held: `mp-apply-scene` does not start them itself), so both watch it and its
  commands run on both machines. A player not there in time is brought there afterwards, as
  below.

A mission's task manager starts when its node opens and its level is active
(`engine/util/script.gc`), or `update-task-masks` does once that level loads. In co-op:

- **Who announces.** `task-manager-init-by-other` (wrapped) sends `mission-start` with the node
  when our own play started the mission: a story step closed here, not from another player's
  progress (`*mp-progress-replaying*`), within 30 s (`mp-on-task-closed` keeps the time,
  `step-at`). A manager started by another player's step or by a level loading later says nothing.
- **Who moves.** A player more than 40 m away goes next to the announcer (`*mp-request-teleport*`,
  "A mission starts: you join the other player"), even when the shared progress already started
  the same mission there. If both announce it (each machine closed the step: a shared cutscene),
  the host stays and the guest comes.
- **When.** The teleport waits until no cutscene plays here and our player is not grabbed or dead
  (`mp-update-pending-teleport`). Beyond 150 m it goes through the other player's continue point,
  then to the player once that level is in (`mp-teleport-to-player`): far away, the level named in
  the other player's state can be active while the ground it stands on is not, and our player fell.
- **The intro on a far machine.** A shared cutscene (1.6) is only played where its scene is
  loaded (`scene-lookup` in `mp-apply-scene`): far from it, the scene player stopped on "can not
  find scene" and kept the player grabbed for good.

- **A guest joining** the host's world (`mp-joining?`: not yet next to the host in it, join stage
  below 4) announces no mission and follows no announcement; until it has the host's world (stage
  2) it skips the host's story steps too: that world holds them.
- **The host chooses a mission in game.** START > Options > Multiplayer shows the host in co-op a
  sixth entry, Choose a mission (`*mp-menu-ingame-host*`): an act, then one of its missions, as the
  lobby's page. X on a mission (`mp-host-play-mission`) sends `world-reset` to every guest first,
  then starts the host's game there as the lobby does (`mp-start-game`, co-op start 2). A guest
  (`mp-apply-world-reset`) loads the host's world again (join stage 0, its last story step
  forgotten) and comes next to the host. Sent first, the event reaches the guests before the story
  steps the new game closes, which they then skip.

Checked on a real start (`mission-trigger`): the host talks to Kleiver at the arena (the kanga-rat
hunt's introduction); its intro waits while the guest, at Spargus's doors, is brought next to it;
the intro then plays on both machines (on the guest, its own Kleiver started it as it arrived: a
"go" that finds the same scene playing counts as joined), both ask to skip it, and the hunt's
manager runs on both. `mission-start` (a mission started without a cutscene) still brings the
guest afterwards.
Checked in game (`mission-select`): the host, at the jump training, chooses act 2's second mission
(the Hip Hog); its game starts there, the guest loads it and stands 1.5 m from the host.

### 4.25 Co-op: a lizard for every player

The leaper lizards (`flut`) a level places are per machine (4.22). multiplayer.gc "A lizard for
every player":

- **A second lizard.** Twice a second, each lizard of the level (its entity's own process) waiting
  to be ridden within 60 m of our player gets a second one 5 m to its right (`mp-flut-scan-level`),
  on every machine, whoever is connected: the host sees two before the guest has loaded. The
  second is a stock lizard of the same entity (`flut-init`, level code found by name: `flut.gc` is
  in WWD, DESLIZ and VOCA, its type in GAME), named `flut-mp`, given a live handle so it waits at
  once (the stock otherwise waits until its spot is out of view). The level's lizard keeps it in its
  `rider` field, unused by the stock past its init; a second left from the lizard's last birth is
  taken back instead of a new one.
- **The stock's count.** A lizard of the level waits while 10 are out (`flut-count`, its
  `wait-for-start`). The seconds are taken out of that count (`mp-flut-cull`), so they never hold
  one of the level's lizards back; an idle second 100 m from our player goes, and comes back with
  the player.
- **Another player's ride.** The state carries `mp-flag flut` while its player rides a lizard.
  When it appears, our idle lizard nearest to that player (within 12 m) is the one it took: it
  hides as the stock hides a lizard ridden (`wait-for-return`: not drawn, no collision), off the
  minimap and out of the count (`mp-flut-hide`). When the flag goes, a lizard waits here where the
  player got off, as the stock leaves one, and the hidden one goes (a level's own lizard stays
  hidden until its next birth, as the stock's).

Checked by `mounts-coop`: at the kanga-rat hunt the host has two lizards at the hunt's spot (the
count stays at 10); the guest gets on one, the host's copy of it hides, the host rides the other;
the guest gets off and a lizard waits there on the host's screen.

### 4.26 Co-op: a mission failed, for every player

The game shows a failure with a resetter (`engine/game/task/task-control.gc`): `spawn-resetter!`
starts it; it grabs the player, slows the world down, then waits for X (retry, or go on when the
failure offers no choice) or triangle (quit, when it offers to), and resets: the mission's start
or its fail continue. Story missions fail without a choice (`mission-fail`, the game's fallback:
back to the mission's start, reset by itself after a delay); the minigames offer retry or quit
(`mission-fail-or-retry`, their `fail-info`). multiplayer.gc "A mission failed, for every player":

- **Shown everywhere.** The `spawn-resetter!` wrapper sends our failure (not a retry from the
  menu, not one another player sent) in two events: `fail-spec` (message, flags, task, text,
  delay, nodes, reset modes, the fail continue's name) and `fail-show` (the retry continue's
  name). Another player spawns the same resetter (`mp-apply-fail-show`), unless its game shows one
  already. A spec's `execute` script is not sent.
- **No reset by itself.** In co-op the `auto-reset` flag is taken off: the failure waits for every
  player.
- **The vote.** `mp-fail-input`, in the input process that runs first every frame, takes X and
  triangle from pad 0 (and its high-fps history) before the resetter reads them: they are our vote
  (1 retry or go on, 2 quit), carried by the state (`fail-shown`, `fail-retry`, `fail-quit`, bits
  18 to 20). Under the failure, the count shows ("Retry: 1/2" and "Quit: 0/2", `#x15a1`, `#x15a3`;
  "Waiting for the other players (1/2)" without a choice, `#x15a2`). Votes that differ decide
  nothing; a player may change its vote.
- **Together.** Once every player chose the same (`mp-update-fail`), each machine gives its
  resetter that button (X or triangle) on its next frame, once the resetter reads its choice (2 s
  after it grabbed the player, on the real clock: the game's clocks are frozen meanwhile). Every
  player is put back at once: the mission's start (retry) or the fail continue (quit). A guest
  then more than 40 m from the host goes next to it.
- **The decision reliably.** The votes travel in the players' states, which may be lost: the last
  voter decides and leaves its failure screen at once, and the other could miss the one or two
  states that showed its vote and wait forever. The machine that decides sends the decision
  (`fail-decided`, reliable); another machine whose failure still waits applies it
  (`mp-apply-fail-decided`).

Checked by `mission-fail` (the jump training's failure, no choice: it stays until both pressed X)
and `mission-fail-retry` (a failure offering retry or quit, `spawn-fail-or-retry`): the failure on
both machines, votes that differ deciding nothing, then retry (and quit) decided on both; each
machine gave its decision within 0.2 s of the other (host time), both players at the same spot.

### 4.27 Places at the same coordinates

Jak 3 puts several places at the coordinates of Haven, reached through a loading transition. Two
levels are two places when they are never loaded together (the levels each continue point loads,
`engine/level/level-info.gc`) and their entities (`decompiler_out/jak3/entities/<level>-actors.json`)
fill the same volume at heights that cannot belong to one ground. The volcano (`volcanoa`, about
-250..129 m by -321..255 m), the mine and the halfpipe share Haven's ground plan; the war factory
and the precursor ship stand 200 to 570 m above it, overlapping each other; the catacombs
(`comb*`) and their rail (`rail*`) run below it; the nest lies under the industrial quarter.
Within Haven, the ruins (`rubble*`, the stadium's far side `stadiumb`, the ruins' missions
`lpattack`, `lppatrol`), reached through the stadium, and the Metal Head city (`mhcity*`) with its
tower (`tower*`), reached from the port, both lie over the farms (`ctyfarm*`): a gate panel of the
ruins at 16 m stands 4 m from a farm emitter at 38 m. Haven, the wasteland and Spargus never
overlap one another; the sewers lie below the city, a consistent space. Before, a guest in the volcano 23.6 m from the host in Haven drew the host's model in the
volcano, put its own NPCs to sleep and showed the host's Haven NPCs instead (`places-before`).

multiplayer.gc "Places at the same coordinates": each player's place comes from the level it
stands in (`mp-level-space`, by the level name in its state: 1 volcano, 2 mine, 3 war factory, 4
precursor ship, 5 catacombs and rail, 6 halfpipe, 7 nest, 8 Haven's ruins, 9 the Metal Head city,
0 the rest). A player in another place, unless its level is loaded here (its ground drawn: the two
sides of a gate, `mp-slot-visible?`):

- is not drawn, hit nor marked on the minimap here; its effects and sounds are not played
  (`mp-puppet-update`, the `effect`, `projectile` and `weapon` events);
- sends no copy we keep (`mp-replica-wanted?`), and a copy whose owner goes there is removed;
- counts as very far (`mp-slot-distance`) for the co-op checks below; for the NPC authority the
  place alone counts, so that every machine decides alike (`mp-provider-of`,
  `mp-update-authority`, `mp-near-a-player?`, `mp-remote-distance`) and the co-op checks (the
  regroup after a failure, a mission's gathering, rides, the race's gift vehicle, the respawn next
  to a teammate): a mission started there brings the other player through its continue point.

Checked by `places`: the table (`log-places`: 25 levels, each in its place); the host at Haven's
stadium gate, the guest in the volcano 23.6 m away; on both machines the other player is not drawn, not on the minimap, none of its copies kept, and the
guest keeps simulating its own NPCs (`log-place`).

### 4.28 Tints of the copies

A traffic vehicle of Haven draws its paint from its type's color options (`draw color-mult`,
`vehicle-util.gc`); a citizen gets a random shade of 0.9 to 1 (`citizen-norm.gc`,
`citizen-chick.gc`, `citizen-fat.gc`), a guard one too (`guard.gc`); a civilian flashes a color
when hit (`civilian.gc`) and a wasteland vehicle glows taking a turbo (`color-emissive`,
`wvehicle.gc`). The others drew every copy in the default paint. An object's state carries both
colors (`color-mult`, `color-emissive`: one byte per channel, 128 = 1.0, `mp-pack-color`), applied
to its copy every frame (`mp-unpack-color`); 0 means none sent (world objects, shots). The body
variants (`seg-mask`) were already sent.

Checked by `tints`: in Haven's port, the guest has a copy of each of the host's barges within 340 m
(17; one before vehicles were sent to 350 m), and every copy of a tinted vehicle of the host wears
its tint within 1/100 (a car painted 0.6 1.2 1.2 drawn 0.601 1.203 1.203); `tints-before` (test
switch `tints-off`) shows them in the default paint.

### 4.29 Burning bush challenges, co-op and free roam

A burning bush (`levels/desert/des-burning-bush.gc`) offers a side challenge: a place to reach in
time (with a precursor orb there), rings, a chase, a jet-board or vehicle score, a time trial...
Talking to it closes the challenge's introduction node, which opens its resolution node, whose
task manager runs the challenge and gives its reward: precursor orbs (`give 'skill`, with the
manager's handle or the handle of the orb it left). Both nodes carry the `bbush` flag. In co-op
and in free roam alike (multiplayer.gc "Burning bush challenges, side by side"):

- **Each player's own.** The challenges' nodes are not shared (1.5) and their failures are not
  voted (4.26: `personal?`): a player who fails one chooses alone, the others play on.
- **The invitation.** When our play starts a challenge (`task-manager-init-by-other`, its node
  closed by us within 30 s), the others are invited (`challenge` event) for 30 s, when its
  introduction is open in their game: "Host started a challenge: D-pad up to join (28)". D-pad up
  is taken from the pad before the game reads it (`mp-challenge-input`: no gun change).
- **Joining.** Who accepts goes to the challenge's start (its retry continue, the one the game
  restarts it from), then closes its introduction there, as talking to the bush does: the same
  challenge runs in its game, from where the other player stands.
- **As Jak.** The challenges are made for Jak (the jet-board's needs his board): a Daxter player
  (the co-op duo) who starts or joins one plays it as Jak, Daxter again once it is decided (not
  saved). The jet-board's judge (`judge`, `engine/common-obs/voicebox.gc`) is spawned where the
  player stands and starts when the player touches it: spawned next to Daxter, it never got that
  touch; once our player is Jak, a waiting judge of our challenge is touched by it
  (`mp-challenge-touch-judge`, the event the stock collision sends).
- **Results.** Each plays on its own. Our task manager is followed every frame, its state and
  score kept: complete or resolution, reached; fail, not reached. Its handle can stop answering
  between two frames right after it gives its reward (seen with the jet-board's): then it reached
  when its reward was given or it was last seen past it. The result goes to the others: reached or
  not, and a score, higher is better: the jet-board's (`task-manager-bbush-board`) or a vehicle challenge's
  (`task-manager-vehicle-bbush`), read at their offsets from the decompiler's types (those types
  live in level code), else minus the seconds it took.
- **The reward.** `give` holds a reward from our challenge's manager or a child of it until every
  player taking part has its result (or left), and the invitations closed. Then every machine
  decides the same: the best score among the players who reached the objective wins (each
  winner of a tie); the winner gets its reward ("Host wins the challenge"), the others none. The
  reward is given with our player's handle: the stock's per-level count reads the parent of the
  handle's process, and the manager may be gone by then.
- **The winner's account.** Those orbs are the game's (`give 'skill`); the shop's money is the
  account the host keeps (4.20), which credits only what it checks: a reward has no orb entity to
  check. Every result goes to every player, so the host keeps them by challenge, playing or not
  (`mp-econ-challenge-result`) by run: each play of a challenge has its run (the starter's slot
  and a count, in every challenge message); the winner claims its orbs (`econ-pick` with the
  challenge's node and run, `mp-econ-challenge-claim`), credited when its player reached the
  objective with the best score the host saw, once per player and run (a start or a claim sent
  again pays nothing more; a claim of an unknown run is refused), 20 orbs at most (the challenges
  pay 3 to 18). A `give` without an entity no longer sends an orb claim the host refused.
- **Free roam: again and again.** Free roam is the end of the game with no mission left: only the
  activities that pay orbs remain (Ratchet's two gun courses, which pay none, are closed by
  `mp-unlock-everything`). Each offers itself again once decided (multiplayer.gc "Free roam: the
  orb activities, again and again", `mp-free-replay-note` from `mp-on-task-closed`,
  `mp-free-replay-update`): a challenge whose resolution closed has its introduction reopened
  (`task-node-open!`, as the bush's own menu does) 5 s later, once its reward is settled and no
  cutscene plays; a bush offering several challenges (the desert's burning-bush-desc-4: air time,
  jump distance, rolls) offers the first open one, so its challenges reopen once all are done. A
  medal of the gun courses, the turret game, the power game or the pre-game (its node closed when
  won, which pays it) is reopened 10 s later (the first of a chain of medals: the others with
  it). The scores are forgotten then (`*game-info*` `game-score`): a challenge paid by cups pays
  the cups reached, not their improvement over the best. Each machine reopens its own (a medal
  closed by another player's progress too).

Checked by `challenge`: the host starts the city's get-to challenge 26 at Haven's ruins bush, the
guest accepts and stands 7 m from it; both reach the orb (the host in 6.05 s, the guest in 8.81
s); the host gets the 3 orbs once the guest's result is in, the guest none. `challenge-one`: the
host fails and quits, the guest reaches it and gets the 3 orbs. `challenge-board`: the jet-board
challenge (city-jetboard-bbush); the guest, Daxter, joins it as Jak and stands still (its judge is
touched); the judges score 500000 trick points (host) and 300000 (guest), both gold cups: the host
gets its 18 orbs, the guest's are lost, and the guest is Daxter again. `challenge-ring` (Spargus's
rings, `wascity-bbush-ring-3`) and `challenge-eggs` (the desert's egg hunt,
`desert-bbush-egg-spider-1`, its prerequisite closed with `close-node`): each player ends it on its
own, the faster gets the manager's 10 (or 18) orbs. `challenge-free`: `challenge` in free roam;
in both the winner's account gains the 3 orbs, the other's nothing. A challenge's introduction is open only outside
an exclusive story mission (`game-task-node-info-method-12`): the bush offers nothing during one,
and an invitation finds it "not open here".

### 4.30 Events while paused

The network events wait in the runtime until GOAL takes them, once per unpaused frame
(`mp-manager-handle-events`): a player in the START menu or on a failure screen takes none.
Past 256 waiting, the newest were dropped: a story step, a vote or a challenge result could be
lost behind the effects the other players set off. Now the oldest event that only draws something
(an effect, an NPC's projectile, a weapon's visual) gives way first; the others are kept, up to
4096 (`Session::push_event`; unit test `EventsKeptWhileNotTaken`: 700 effects and a story step
reach a player who takes none; the story step and 255 effects are left).

Memory over a long session (`soak`, `log-mem`): both players in Haven's port, the host going away
and back twice (the guest's role changing each time), 15 samples over 5 minutes: the process heap
and the processes return to the same levels in the same place (host 742-747 KB in the port, 481 KB
away), the global heap lost 4.5 KB on the host (its first level change, then 224 bytes per new
skeleton name kept for the far art: bounded by the game's skeletons).

### 4.31 Daxter on the jet-board

Jak takes the jet-board out with R2 (`want-to-board?` in his stance, `target-board-start`);
Daxter's mode ignored R2. multiplayer.gc "Daxter on the jet-board":

- **Taking it out.** R2 in his stance or walk sends `'change-mode 'board` (`mp-bodax-update`); his
  handler (`mp-indax-handler`) goes to the stock `target-board-start`, which leaves his mode. The
  stock board runs from there: the same mechanics as Jak (jumps, ducks, tricks, grinds, the zap).
  R2 on the board puts it away; his mode comes back as after a vehicle (`mp-update-character`).
- **His clips.** The player's own model plays Jak's board clips, hidden (`no-draw-bounds2`), so
  the board stays under its feet as for Jak. The sidekick plays Daxter's board clips as when he
  rides on Jak's back (`sidekick.gc`, `'matrix 'board`: the clips `daxter-board-*` matched to Jak's,
  loaded with Jak's from `jak-board+0-ag`); its root is moved from Jak's joint 37 onto the board,
  oriented as the board (`mp-cspace<-board-daxter!`, set every frame: the get-on asks `'board`
  again). Those clips show him clinging to Jak's back: crouched while he rides, lying on the board
  at rest, stretched in the air (the rendering the user chose over his standing clips or Jak's
  poses copied joint by joint).
- **The board** (`board-util.gc`) hid with the player's model (`board-post`): its `use` state's
  post (`mp-board-post`) draws it under Daxter, at 60 % of its size (`board-scale`). Jak carries it
  folded on his back (its `idle` state, same post): a Daxter player's board is never drawn while he
  does not ride it, and it is not sent to the others then (`mp-daxter-board-away?`).
- **His camera.** Leaving his mode removed his closer camera (`target-indax-init`'s string length,
  height and head offset): it is set again while he rides.
- **The others** draw Daxter's model: for the player, the sidekick's bones are sent (`mp-send-poses`),
  with the player's effects and sounds (the board's); the board is one of his props.

Checked by `daxter-board`: in the industrial quarter, the guest (Daxter) takes the board out with R2,
rides it at 60 % (stance, X's jump, riding), puts it away with R2 and is Daxter on foot again,
his board drawn nowhere and not sent; the host draws his board while he rides.

### 4.32 Co-op: every story mission, special phases included

The user asked that every mission play in co-op, its special phases included (a vehicle of its
own, a turret, a missile steered...). `scripts/mp-test/mission_sweep.py` starts each of the 60
missions the lobby offers in co-op (host Jak, guest Daxter) and records what each player is put in
(5.3). What each phase does in co-op:

| Phase | Missions | In co-op |
|---|---|---|
| A vehicle | the desert's drives and races (turtle training, Kleiver's race, Damas's artifact races, the hover and rescue, the lizard roundup), the nest, the glider (`desert-glide`), the gunship (`factory-sky-battle`), the rhino (`palace-ruins-attack`), the catacombs' rail, the final boss | Each machine gives its player the mission's vehicle (4.15); Daxter drives as Daxter. |
| A gun to mount | Spargus's gun game and defense (`wascity-gungame`, `wascity-defend`), the beast battle's scorpion gun | Each player mounts the gun in its own world: the arrival is repeated for one who missed it (below). |
| Daxter's torpedo | `city-port-attack` | Each player steers its own torpedo; a Jak player gets his Daxter back to steer it (below). |
| The bomb robot | `city-blow-barricade` | Each player steers its own robot (`target-grab-ride`). |
| The mech | `precursor-tour` | Both in a mech. |
| The leaper race | `wascity-leaper-race` | A player on foot gets a racing lizard (below). |
| Minigames | the Spargus board game (`wascity-pre-game`), Vin's maze (`city-power-game`), the gun courses | Each plays its own copy; the first win closes the mission for both; a failure is everyone's (4.26). |
| On foot | the others | Nothing special. |

`city-start` (the intro movie) has no continue point: the lobby does not offer it.

What the sweep found, and the changes:

- **Modes only Jak has.** Daxter's handler took only `grab`, `normal`, `falling`, `hang` and
  `tube`: a mission putting the player in a turret, the torpedo or the robot waited for good.
  `mp-indax-handler` gives those modes to Jak's handler (`target-standard-event-handler`, his
  mode left on the way): `turret`, `pilot`, `pilot-race`, `pilot-daxter`, `grab-ride`, `mech`;
  `'pilot` becomes `'pilot-daxter`. Only those: the stock asks for others in any mode, `'gun`
  from the D-pad and `want-to-gun?` (`target-gun.gc`), which his mode must keep ignoring for his
  own guns (forwarding every mode broke them, 2026-10-28). His mode comes back once he stands on
  foot (`mp-update-character`).
- **Crash of the copies' death effect** (the guest, in Haven's KG missions: the hijack, the
  barricade, the HQ). Art without effects has no effect-control: `skel effect` is `#f`, which
  `nonzero?` let through, and `do-effect` on a symbol called address 0 (`mp-replica-update`).
- **Crash on the glider** (the host, as `desert-glide` began). A Jak player online without a
  Daxter rider has no sidekick (`mp-update-session`), but the stock hands Jak's gun to Daxter in a
  vehicle (`gun-daxter`) and `gun-post` reads his hand. `target-gun-joint-points`
  (multiplayer-guns.gc) keeps the gun in Jak's hands without a sidekick.
- **Crash on the torpedo** (the host, as it got on). `target-pilot-pidax-enter` draws the player
  with the sidekick's art. It calls `mp-pidax-sidekick!` first, which gives the sidekick back at
  once (the manager's features setting removed, `apply-settings`); `mp-update-session` keeps him
  while Daxter drives. `target-pilot.gc` is level code (HGA, LPATK, WASALL...): a redefinition from
  a GAME file would be replaced at each load of those levels, hence the call in the stock file.
- **The leaper race** seats Jak on his lizard through its continue point (flag `flut-racer`), which
  only the player who starts the mission reaches; the guest's task manager moved it to the start on
  foot. `mp-leaper-race-update`: a player on foot while the race's manager runs gets a racing lizard
  (`'change-mode 'flut #f 'racer`), once a second at most.
- **A gun to mount.** The continue point of those missions (flag `vehicle`) sends the gun `'pickup`
  on arrival; a guest arrived next to it on foot, and the turret shoved it away. `mp-arrival-update`:
  for 15 s after an arrival (`target-continue`, or the join's end), a player on foot is taken by
  the idle turret within 20 m, or by the gun its continue point names (hellcat 7, scorpion 14), as
  from our player.

Notes for the sweep: a minigame hides the guest (a script's `wait-player` then waits for good), and
the skip votes (triangle) also take players out of vehicles. `ERROR: object #x... is not a valid
object (misaligned)` when a turret goes is the stock's (`target-turret` `deactivate` checks its
HUD's handle with `valid?`). A goalc started elsewhere (another project's REPL) can connect to a
test instance's debug port and send it code: `[DECI2] Connected!` in its log, then
`call_method_of_type failed!` and crashes; such a run says nothing about the mod.

### 4.33 Jak or Daxter in game, and the duo

START > Options > Multiplayer > **Character** chooses Jak or Daxter in game too
(`mp-choose-character`); our mode follows once we stand (`mp-update-character`: Daxter's mode from
the stance, out of it from his stance).

- **Free roam:** each player plays the character it chooses, two Jaks or two Daxters alike.
- **Co-op:** the duo stays a Jak and a Daxter. A choice in game swaps the roles: the player sends
  the character it takes (`role` event); the other one, playing it too, takes the other one, saved,
  "Co-op: roles swapped, you play Jak" (`mp-apply-role`). A Daxter on a Jak's shoulder who becomes
  Jak jumps off (`mp-update-perch`). A Daxter player who plays a challenge as Jak stays Jak after
  it when the other player took Daxter meanwhile (`mp-challenge-reset`). At a game's start a guest
  with the host's character still gets the other one (`mp-update-coop-roles`).
- **The minimap:** a Daxter on a Jak's shoulder has no icon of his own: the Jak's stands for both.

Checked by `coop-roles`: in the sewers, the host (Jak) takes Daxter, the guest (Daxter) becomes Jak;
the guest takes Daxter back, the host is Jak again; always one of each, each player's mode
following; both minimaps mark the other with its cone in its color. `free-roles`: in free roam the
guest takes Jak, the host stays Jak. `minimap-cone`: the guest 30 m away shows as its cone alone on
the host's minimap, as our own marker.

### 4.34 Co-op: doors wait for everyone

A door (`com-airlock`, `engine/common-obs/airlock.gc`, engine code: every door of the game,
level subtypes included, inherits it) closes when its `want-cross-airlock?` says no (`open` state,
`:trans`). Closing runs its scripts when our player stands in front of it (`com-airlock-method-26
'front`): `on-exit`, `on-inside`, `on-deactivate`, which hide or unload the level behind it, and
for some (the temple's `tpl-door-a`) close a story step, after which the door never opens again
for anyone (the progress is shared). In co-op, as himstan's airlocks wait for every player
(`mp-door-hold?` in our `want-cross-airlock?` wrapper, multiplayer-world.gc):

- **The door waits** while it is open, our player stands in front of it (it went through), and a
  teammate within 100 m of it is still behind it, alive and not hidden (loading, in a cutscene; a
  Daxter on a shoulder counts). It never opens a closed door, and a door a mission or the game
  shuts (its close command, the `airlock` setting off) still closes.
- **Shown:** "The door waits for Guest" (`#x15aa`) while our player is within 30 m of it.
- **Released** once the teammate went through, died, left or walked off (beyond 100 m): the door
  closes the stock way.
- **A fix on the way:** the check that opens a door for a remote player in front of it called
  `check-crossing-distance` with `#t`, which runs the door's `on-cross` scripts and overwrites its
  `last-distance` (our side of it): it now reads the side only (`#f`).

Offline, alone or in free roam, the stock doors (opening for a remote player in front of them
online). Checked by `door-wait`: at Spargus, the host 30 m past the door, the guest 20 m behind
it: the door stays open; once the guest joined the host it closes.

### 4.35 Text chat and the host's commands

multiplayer-chat.gc, `kmultiplayer.cpp` "Chat", `game/graphics/pipelines/opengl.cpp`:

- **The keyboard alone.** Online, T opens a line (unbound by default). The renderer asks the chat
  every frame whether it holds the keyboard (`mp::chat_wants_keyboard`): the game's keys are then
  released and ignored, as for ImGui, and SDL's text input is on. It hands every SDL event to the
  chat first (`mp::chat_sdl_event`): while a line is typed, each key press is the chat's. Enter
  sends the line, Escape drops it, Backspace erases; the game gets the keyboard back once Enter or
  Escape is up (Enter is START by default). A pad has no way in.
- **What a line keeps.** Printable ASCII, 96 characters at most (`mp::chat_clean`): the accented
  Latin letters lose their accent, `~` (the font's escape) becomes `-`. The answers to commands
  are the game's own texts, accents and all.
- **Sent through the host.** A guest's line goes to the host (`MSG_CHAT`), which lists it and
  relays it with the guest's slot and name as it knows them, never as the guest claims, at most
  5 lines in a row from a guest, then one every 2 s. Fifty lines are kept; a session starts
  empty, with "Press T to chat (keyboard)" (`#x15af`).
- **Drawn.** At the left, above the eco meter (`mp-chat-draw`): the last 8 lines, each for 12 s
  (fading its last 2 s); while a line is typed, all 8 and the line under them after "> ", with a
  blinking cursor. Our lines light blue, the answers to commands yellow, the others' white.
- **Commands.** A line starting with `/` is a command, the host's alone: the server's
  administrator. A guest's is refused ("Only the host can use commands") and sent to nobody. The
  answers are the host's alone. A name may be the beginning of one, case ignored
  (`pc-mp-find-player`: an exact name first, else a unique beginning).

| Command | Effect |
|---|---|
| `/help` | The commands. |
| `/players` | Each player's slot, name and ping. |
| `/kick name`, `/ban name` | As the administration page: that player disconnected (and, banned, its address refused until the server stops). |
| `/tp name`, `/tpall` | That player, or every player, brought next to the host. |
| `/give name orbs`, `/trophies name n` | That account credited (debited, negative), while the accounts are open (free roam). |

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
python scripts/mp-test/run_mp_test.py <scenario> [--retail] [--latency <ms>[:<jitter ms>]] [--loss <percent>] [--shot-size <w>x<h>]
```

`--fixed-cadence` keeps every send at its full rate (`MP_TEST_FIXED_CADENCE`: no network
pressure, a baseline for 1.1). `--settings-from <scenario>` starts each instance with the economy
files and `multiplayer.cfg` (its secret id) its namesake left in that scenario: a session hosted
again after the games were closed.
`--retail` boots as `task boot-game-retail` (no `-debug`: the title screen, no debug segment;
`log-session` prints `debug-segment #f`). `--latency` holds what each instance receives that long, plus up to the jitter, before reading it,
in order (`MP_TEST_LATENCY`, `Session::receive_all` in `kmultiplayer.cpp`), and `--loss <percent>`
drops that share of the datagrams received (`MP_TEST_LOSS`): both instances run on one machine,
these stand in for the network between two. `--shot-size` renders the screenshots at that size
(`MP_TEST_SHOT_SIZE`, 1280x720 by default): 2560x1440 shows Daxter's hands from a few meters.

`python scripts/mp-test/compare_poses.py` compares, after a `pose-all` run, the bones the host
logged for its objects with those of their copies on the guest at the same moment: both log every
frame, stamped with the host's clock (`pc-mp-clock`), and each copy sample is compared with the
host's bones interpolated at its stamp. The host reads its bones from the manager, where the batch
reads them (the test hook runs after the frame's joint math, one frame later); a copy is stamped
with the time of the pose laid on it.

`python scripts/mp-test/mission_sweep.py [mission ...]` starts the lobby's story missions one after
the other in co-op (host Jak, guest Daxter, about 3 minutes each, every one by default): each
player's states traced, logged four times (`log-play`), two screenshots per player. The results go
to `out/mp-test/mission-sweep-results/`: one folder per mission (logs, screenshots) and
`summary.txt`, a line per mission with its crashes and each player's last state (4.32).

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
| `goto-door`, `log-door`, `goto-elevator`, `log-elevator` | Stand in front of the nearest door or on the nearest elevator; print the door or elevator nearest to player 0 (on a guest) or to us (on the host), its state and height, whether it waits for a teammate (`door hold`, 4.34), and the elevator trips sent and shown. |
| `goto-door-at <meters>` | Stand that far in front of the nearest door, along its axis (negative: behind it). |
| `wait-vote` | The script waits until another player's mission asks our answer (then `press up` or `press down`). |
| `chat-key <t\|enter\|escape\|backspace>`, `chat-text <text>`, `chat-say <text>` | A key or text as the keyboard gives them to the chat; T, the text, Enter. |
| `sdl-key <key>`, `sdl-text <text>` | The same through SDL's event queue, the renderer's way. |
| `os-key <key>`, `os-type <text>` | Keys pressed and released on the game's window as Windows messages (`WM_KEYDOWN`, `WM_KEYUP`): Windows makes each character, as for a keyboard; the text reaches only the window that has the focus. |
| `shot-save-icon <name>` | The next co-op save's icon is screenshot when it shows on this machine. |
| `log-chat` | Whether a line is typed (and what), whether SDL's text input is on for the window and the window has the focus, the last 8 lines (slot, ms since, text). |
| `log-guns`, `log-save`, `ring-save <save\|load>` | Free roam's higher gun mods (bits: red 2 and 3, yellow 2 and 3...), the mod chosen, the items owned; co-op saves written, one due, the icon shown; the START ring's Save Game or Load Game picked online. |
| `scene <name>`, `log-levels` | Start a cutscene as a mission would; print the levels loaded (`art-heap #t`: the art heap is in its memory), the art heap and the art groups loaded alone, and the cutscenes sent, shown and playing. |
| `log-shaders` | Walk the texture shader lists with bounds: links counted and broken (0 when every level is below 256 MB), and each level's heap. |
| `far-levels <0\|1>` | Load the art of distant players' objects (default 1); 0 for a counter-test. |
| `synth-skels <0\|1>` | Copies use the skeleton-groups rebuilt from their sender's description, even when the code defining ours is loaded (the path of a group whose code is in no level here). |
| `log-art-kinds` | Every kind of object (skeleton) another player announced: its group (ours or rebuilt), its art group loaded or loadable alone, drawable here or not. |
| `announce-all-kinds`, `log-kinds-coverage` | Describe to the others every kind of object of our place (every skeleton-group whose art is loaded here); count the kinds another player described, complete, with their art group file here. |
| `ride-shown-elevator` | Once this machine started an elevator trip because player 0 did, stand beside player 0 on it (once). |
| `wait-joined`, `wait-player <slot>` | The script's clock stops until this guest is in the host's world (next to it), or until player `slot` is in game for 2 s: the steps after keep their spacing from then. Every scenario's guest waits after `join`, its host before the guest arrives. |
| `wait-menu <page>`, `address <ip:port>`, `log-lobby` | The script's clock stops until the menu shows that page for 1.5 s (`title`: X is tapped every 2 s until the title screen opens its menu, which waits for a press); the server address field alone; the menu page shown, the title screen's entries, and the lobby as drawn (players, characters, statuses, game mode, ready). |
| `spawn-vehicle <type>`, `log-vehicles` | An empty vehicle 3 m in front of us (traffic spawn path); every vehicle within 30 m of us, ours and copies (a vehicle shown twice). |
| `log-ping` | Each player's round trip, health and playout delay (1.1) as the player list shows them, the list shown, and whether the game is paused. |
| `log-end` | Sessions ended without us, the note for the title screen, whether we are at the title screen and which menu page. |
| `log-scene-skins` | Each Jak or Daxter actor of the cutscene playing here, with the shirt and fur tints it is drawn with this frame (0: the stock textures). |
| `log-econ`, `goto-merchant`, `goto-orb` | Our balances as the host sent them, our last purchase's answer, whether we stand at Kleiver's, and on the host its accounts' state; stand at Kleiver's shop; stand on the nearest precursor orb (it is taken). |
| `econ-claim <aid>`, `econ-grant <slot> <orbs>`, `shop-buy <item>` | Claim the orb of that entity without taking it (0: the nearest, from where we stand), a cheat the host must refuse; the host credits a player (a test of its accounts; nothing on a guest); buy shop item n as the page's X does. |
| `kill-mh <slot>` | On the host: a lethal blow from player slot's character (ours or its puppet) to the nearest Metal Head, credited by the economy. |
| `die`, `log-respawn` | A blow that kills our player; the times we came back next to a teammate, our state and health, the teammate found. |
| `net <ms> <jitter> <loss %>`, `log-net` | The test network from now on (what this instance receives, as `--latency` and `--loss`); each peer's network pressure, the share of messages delivered both ways, the queueing and the bytes sent it last second. |
| `look-player <slot>` | Face that player, the camera put 6 m behind us, 2 m up and 1.5 m aside on the line to it (`'teleport-to-vector-start-string`): a screenshot shows that player ahead. |
| `log-outline` | Outlined models the renderer drew since boot, the pixels of fill and outline it drew last frame where walls hide players (`pc-mp-outline-pixels`), each player in the outline table with its color. |
| `cheat weapons`, `daxter-mod <pickup-type>`, `log-daxter-gun` | Every weapon (Mods menu); the mod Daxter fires, as the D-pad sets it; Daxter's gun: out, mod, shots fired, ammo, aim, the features, the gun's scale and state, its model's distance to his right hand, its draw status and clip, his clip holding it (index, frame), `gun-daxter`, the joints selected, the gun's distance to his gun joint; each other player's clip holding the gun as its state says. |
| `log-seat` | Our model in a vehicle, in the vehicle's axes (x left, y up, z forward) from the seat point: as a passenger the model in the seat (its root, bone 3, its bones' extent, its clip), driving the model drawn (Daxter's in his mode) from the player's position. |
| `log-joints`, `log-anim <daxter\|jak> <index>` | Daxter's joints (index, name, parent); the entry at that index of Daxter's or Jak's art group (a clip is loaded when it prints an `art-joint-anim`). |
| `log-name` | The player name kept and the on-screen keyboard's text. |
| `places-off`, `log-place` | Every level in one place, as before 4.27; our level, place and role, and each other player's level, place, whether its puppet is drawn and on our minimap, its distance and its copies. |
| `tints-off`, `log-tints` | Copies keep the default tint, as before 4.28; the tint of our vehicles and citizens (by pid) and of the copies here (by their owner's pid). |
| `challenge-start <task>`, `goto-challenge-orb`, `send-complete <type>`, `log-challenge`, `wait-challenge <phase>` | What talking to that task's burning bush does (its introduction closed, no skull gems); our player onto the orb our challenge's manager left; tell the first process of that type its objective is reached; our challenge (phase, players, results, winners, reward held and given, our orbs); wait until our challenge reaches a phase (1 invited, 3 playing, 4 our result in, 5 decided). |
| `trick-points <n>` | Our player's trick points, what the jet-board's judge scores. |
| `close-node <name>` | That task node closed, as the story closes it (a challenge's prerequisite). |
| `unlock-board`, `log-bodax` | The jet-board and its zap unlocked; our player on the jet-board as Daxter: state, on, camera, the board's scale and whether it is drawn, the sidekick drawn, his board clips loaded (56 of 67). |
| `choose-character <0\|1>`, `log-role` | Our character chosen in game, as the Character carousel does (co-op: the roles swap); our character, Daxter's mode or not, the swaps, each other player's character and its minimap dot and cone here (the cone's color and class). |
| `log-play`, `trace-play` | Our player's state, its modes (vehicle, board, lizard, Daxter, turret, rail, tube, mech, grabbed, dead), the task managers alive, the turrets (state, distance); each new state from now on. |
| `log-mem` | The process heap (free, used), the global heap's free room, the processes alive, our copies, the NPCs forced to compute every bone, the art groups loaded for others. |
| `send-fail <type>`, `spawn-fail-or-retry`, `log-fail` | Tell the first process of that type (a mission's task manager) to fail; a failure offering retry or quit at our continue point; our failure (resetter, its message, our vote, the decision, counters) and each other player's failure flags. |
| `goto-merchant [m]`, `walk-to-merchant <frames>` | Stand at Kleiver's spot, or m meters from it facing it; walk toward it (the stick follows the camera). `log-econ` prints his model (alive, level, drawn), whether he is solid, his idle clip, how much he faces us and how far we stand. |
| `host-play-mission <name>`, `log-input` | The host's Choose a mission in game on that play-list name (`mp-host-play-mission`); what our buttons did: state, gun mod, ammo of each color, Daxter's shots, the START menu open. |
| `log-session`, `coop-start <0\|1\|2> [n]`, `log-missions`, `log-acts` | The game the host started (free roam, campaign), unlocks, campaign saves and completion, story nodes closed, the server's status, the shoulder Daxter; how co-op starts (the n-th mission); the missions offered; the missions of each act (count, first three). |
| `gun [pickup-type]`, `board` | Every gun mod unlocked with ammo, the gun out, the mod `pickup-type` picked (26 red 1 ... 31 yellow 3 ... 35 dark 1 ... 37 dark 3); the hoverboard unlocked with its zap, the player on it. |
| `mode <0\|1>`, `title` | Game mode when hosting (free roam, co-op); back to the title screen as the pause menu's quit does. |
| `log-join`, `try-save` | Joining the host's world: stage, world sent or loaded and its size, saves refused, continue point, title screen or in game, language, distance to the host; an auto-save, as a checkpoint asks it. |
| `log-coop`, `log-weather` | Co-op: the mode, authority, character, the shoulder (player perched on, times), state, distance to the host, the host's animation and flags, and a second line on the shoulder (the rider on ours, the climbs our Daxter played and their frames, his clip, whether he is drawn); the weather (clouds, fog, special, the override). |
| `log-gone`, `trace-gone` | On the authority: the NPCs it sent, shown, that left the game or were hidden within 40 m of another player without dying (count; each, with why). |
| `log-vanished`, `trace-vanish` | What vanished on our screen within 40 m while alive: own NPCs put to sleep, copies removed or hidden at once (count; each, with why), and copies that faded out instead; each copy that dies here (`copy died <skeleton> [vehicle]`). |
| `log-path <skeleton\|shot> <frames>` | An object's root (every shot's, with its id) every frame: on its owner each state it sends, on a viewer each state drawn, stamped with the owner's clock (`compare_poses.py` compares the copy's path with the owner's, shot by shot). |
| `log-laser <frames>` | The gun's laser sight every frame, where it starts and ends: the owner's as its game computes it (`laser-point`, `laser-hit-point`), the copy's as drawn, stamped with the owner's clock (`compare_poses.py laser_report`). |
| `log-weapons` | Other players' area weapons drawn here (wave concussors, mass inverters, arc frames, needle trails), and each arc received: its points, its tracker, its bolts. |
| `log-aimable` | What the area weapons find within 20 m of us: each focusable of the actor hash, its process mask, focus, collision action and sphere. |
| `power <dark\|light>`, `log-power` | Turn into Dark Jak or Light Jak (unlocked, with its eco); our state and what each puppet shows (blend, dark, global effects, fade), and the models the renderer drew with their own Dark Jak skin (`pc-mp-dark-skin-draws`). |
| `wait-countdown` | The script's clock stops until the lobby's start countdown runs. |
| `log-pause`, `wait-pause <0\|1>`, `wait-resume` | The host's pause for everyone and the START menu over the world (process masks, menu mode, the pad hidden from the world, clocks); the script waits until the game is paused by the host (1) or not (0), or until its resume countdown runs. |
| `skin <group> <rrggbb> <percent>`, `skin-page`, `log-skin` | Our outfit's part `group` (1-12) tinted that color at that strength (saved, sent); open the Appearance page; each outfit sent and received, the models wearing one (`pc-mp-skin-draws`), the page's state and its preview. |
| `wait-driving <slot>` | The script waits until that player drives a vehicle (for 2 s). `log-seat` also prints the seat we ride. |
| `wait-scene`, `log-skip` | The script waits until a cutscene that can be skipped plays here; the vote to skip it (voted, votes, viewers, skips). |
| `beam-at-host <dx> <dz>`, `goto-beam`, `log-beams` | A test waypoint dx, dz meters from the host, whose process checks the arrival with `(target-pos 0)` within 3 m as missions do; stand at the nearest beam; the beams, the count, how far their owner sees us, the calls held. |
| `race-gate`, `log-race` | A race start of a test: a process sets the race's `pilot-exit #f` and waits on `mp-race-hold?` as the races' task managers do, then logs "race gate open"; the race state (waiting, aboard, starts, vehicles given, exit allowed) and ours (driving, riding, perched). |
| `coop-mission <name>` | Co-op starts at the story mission of that play-list name (`desert-course-race`, `arena-training-1`), as the lobby's mission list does. |
| `go-state <type> <state>`, `log-state <type>` | The first process of that type goes to that virtual state (`method-state`, `go-process`): a mission's task manager taken to a later step, its own code running from there; the state of the first process of that type. |
| `log-stretch`, `hide-puppets <0\|1>`, `hide-copies <0\|1>` | Each puppet's bone farthest from its root, and the copies with a bone over 5 m away (a stretched scarf); hide the puppets or the copies (counter-tests). |
| `log-motion [m]`, `log-paused [m]`, `run-near-others <0\|1>` | NPCs within 60 m (ours, copies) and how many moved since the last call; on the authority, its NPCs near another player, how many the stock rule pauses and how many run; whether they run (default 1). |
| `open-menu <page>`, `econ-grant-self <0\|1>` | A START menu page; the host's Mods menu entries, +100 precursor orbs (0) or Metal Head trophies (1). |
| `log-eyes` | Each remote puppet's eye slot and the process that wrote it last (the puppet, our player). |
| `coop-mission-intro`, `goto-type <type>` | The next `coop-mission` starts at the mission's introduction (its pre-play node and continue point); stand 1.5 m in front of the first process of that type, facing it (a mission giver: `kleever-npc`). |
| `eye-fix-off` | The eye fix undone: our model of Jak playing Daxter writes its eyes again, the puppets feed none (before / after screenshots). |
| `log-mount` | The lizard we ride as we send it: its process, the skeleton found for it, its draw status. |
| `trace-fx` | Log each effect another player's machine sent and we replay (group, parent, joint). |
| `dark-bomb`, `log-dark`, `restart`, `log-restart` | The dark bomb and the dark blast ready (features and stages, full dark eco; jump then square sets the bomb off, `press l1 600` then `press l1+square 200` the blast); what the blast needs (stage, features, eco, state) and the bolts that followed a target's position; START > Restart; the restarts done and asked. |
| `merchant-spot <i>` | Host: Kleiver at spot i, sent to every player with its balances. `log-econ` also prints his spot, whether his icon is out and the frames his name was drawn. |
| `ride-flut`, `end-flut`, `goto-flut`, `log-fldax`, `log-fluts` | Ride a lizard where we stand, as its offer does; get off; stand 2 m from the nearest lizard waiting, facing it; Daxter on a lizard: riding, rides, his art on the player, drawn as Daxter, the state; the lizards within 40 m (each one's name, state, distance, drawn), whether we ride, the co-op counters (seconds placed, hidden, left) and the stock's count. |
| `respawn-manager <type>` | That task manager killed, then spawned again by the task masks, as when its mission starts. |
| `race-autopilot`, `race-stop-racers` | Once a race runs (its timer going), the vehicle we drive is driven by the racers' AI to the finish (`'set-control-hook-race-ai`); every vehicle of ours but our player's removed (a race's racers: the race drives them every frame, a speed of 0 does not hold), their places frozen. |
| `goto-own-vehicle` | Stand beside the vehicle we drove last. `log-race` prints our place, progress and bits, the race's binding (our slot, the other player's) and verdict, the host's race received, and the five racers' progress. |

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
| `ride` | Passenger seat (L1 + triangle) | The guest presses L1 + triangle beside the host's two-seat buggy and rides in it (`target-grab-ride`): carried 21.9 m, at most 1.14 m from the driver. How passengers sit: `ride-seat`, `ride-seat-daxter` (round of 2026-10-19, afternoon). |
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
| `coop` | Co-op duo | The host plays Jak in co-op; the guest, joining as Jak, is switched to Daxter. Next to the crouching host it presses triangle: held in `target-indax-grab` on the shoulder, it stays within 1.2 m while the host walks; X puts it down in `target-indax-stance`. |
| `world-objects` | Cycles, doors, elevators | Test cycle: the guest's phase is the host's within one frame (16 ms; 39 ms in the last run, where one guest frame came 273 ms late). The host stands at a door 200 m from the guest: the guest's copy of that door opens (it stays closed with the door wrapper off, the counter-test). The host rides the palace elevator: the trip starts on the host ("sent 1") and on the guest ("shown 1", "sent 0"); the guest then jumps beside the host on the elevator its own machine moves and rides up with it (screenshots: the guest in the street, then in the shaft). |
| `scene` | Cutscenes | The host starts a Spargus cutscene: it plays on both machines (screenshots of the same shot), both players placed in it. |
| `float` | Physics objects | The volcano's lava platforms, 270 m from both players: at the same stamp, the guest's are within 7 cm in height and 0.002 in tilt of the host's (they were 2.25 s out of phase before). |
| `float-weight` | A player standing on a remote-driven platform | The guest stands on a lava platform its machine moves where the host's is: carried 18 m in 2 s with it; each machine sends the other player's weight to its platform (counters); both slide into the lava as the platform sinks, as in the stock game. |
| `guns-fx` | The area weapons, both ways | Each player fires the wave concussor, mass inverter, arc wielder and needle lazer at the other 7 m away, health topped up before each (the Wastelanders hit by them shoot back): no crash; each draws the other's (1 explosion, 1 field, 165 arc frames, 9 to 24 needle trails) and takes their blows (`pvp blow ... by gun-red-2-shockwave`, `gravity-spinner`, `gun-blue-shot-2`, `gun-blue-shot-3`). |
| `powers` | Dark Jak and Light Jak on another machine | The guest turns into Dark Jak, then Light Jak, 7 m from the host: the host's puppet shows the dark blend shapes (1.00), then Light Jak's look (rim lights, effect `#x90`) and glow. |
| `far-split`, `far-split-host` | Players in two regions | One goes from Spargus to Haven City's port and back while the other stays: no part of a model stretched across the world (farthest bone 2.6 m from its root), NPCs moving on both sides. |
| `npc-far-run` | NPCs near a distant guest | The guest 60 m from the host: the host's NPCs near it run (none paused) and the guest sees them move. |
| `lobby` | Title screen and lobby | Both go back to the title screen (7 entries, Host Game and Join Game at 2 and 3). Host: Host Game > Load Game opens the lobby, circle the multiplayer options and triangle back; X is refused while the guest is not ready. Guest: Join Game > Connect, R1 (Daxter), X (ready); the host sees it (Daxter, ready). Square switches to co-op, shown in the guest's lobby; the host's X opens its save menu, X loads it, and the guest follows into its world, 1.6 m from the host, as Daxter in co-op. |
| `steal-dup` | A stolen vehicle shown once | After the steal, the thief has one vehicle of its own and no copy of the stolen one; the victim has none and draws the thief's (body and 4 wheels). |
| `daxter-drive`, `daxter-drive-own` | Daxter at the wheel | Playing Daxter, the guest steals the host's buggy, drives it (`target-pilot-stance`) and gets off as Daxter; the host draws Daxter at the wheel. The same with an empty buggy of Daxter's own world (`spawn-vehicle`). |
| `daxter-guns` | Guns on Daxter, Daxter's guns | The host fires its 12 gun mods at the guest playing Daxter 7 m away: no crash, 9 blows taken from 5 kinds of shots; Daxter then drives the host's buggy and fires its cannon, whose shots the host draws. |
| `vehicle-guns` | A vehicle's cannon on another player | The host fires its buggy's cannon at the guest 26 m ahead: the shell's explosion hurts it (1 hit), and the guest draws the shells. |
| `daxter-ride` | Daxter as a passenger | Playing Daxter, the guest presses L1 + triangle and rides in `target-indax-grab`: the host draws Daxter riding, carried 22.9 m, at most 1.17 m from the driver. |
| `player-list` | SELECT list and ping | Holding SELECT shows the list without pausing; ping 17 ms on the guest, 16 to 17 ms measured by the host. |

Round of 2026-10-17 (after the title screen, lobby, Daxter in vehicles, co-op duo and player
list; 18 scenarios, `check_results.py`: 144 checks passed, 0 failed, no crash): `smoke`,
`join-title`, `join-world`, `lobby`, `coop`, `ride`, `daxter-ride`, `steal-dup`, `daxter-drive`,
`daxter-drive-own`, `daxter-guns`, `vehicle-guns`, `player-list`, `pvp`, `powers`, `vehicle`,
`progress` and `guns-fx` pass every check (`vehicle` once a name clash in `check_results.py` was
fixed). `guns-fx` first failed one check in each of two runs, a different one each time: each
blow pushes the target away, and the scenario only put it back at 7 m once per side, so the next
guns were fired from farther, where the arc wielder and needle lazer turn to the Wastelanders
around. The target now steps back to 7 m, facing the shooter, before each gun: 3 runs out of 3
passed (12 of 12), every gun of both players landing on the other in two of them.

Round of 2026-10-19 (UDP, outlines, Daxter's guns, keyboard, game modes, free roam, co-op
campaign; 21 scenarios, `check_results.py`: 170 checks passed, 0 failed, no crash; the unit tests
pass, the reliable channel's under 20 % loss included): `smoke`, `pvp`, `join-world`,
`join-title`, `coop`, `ride`, `daxter-ride`, `steal-dup`, `daxter-drive`, `daxter-drive-own`,
`daxter-guns`, `vehicle`, `vehicle-guns`, `powers`, `guns-fx`, `progress` as before, and:

| Scenario | Checks | Result |
|---|---|---|
| `lobby` (rewritten) | Host Game, joining before the start, keyboard, mode list, free roam | Host Game opens the lobby; the guest is listed there before any game starts (2 players); on the keyboard the host's name goes from "Host" to "Jak3"; square opens the mode list (six modes grayed out); X is refused until the guest is ready, then starts free roam: 254 story nodes closed, `city-win-resolution` done, every feature, the eight vehicles; the guest follows into it with the same. |
| `coop-campaign` | Co-op from the lobby, three players | Co-op from the mode list, the co-op page, the mission list (act 1's first mission: the arena's jump training): the mission starts with 4 nodes closed; a third player is refused ("The server is full"); the host's auto-save goes to `multiplayer-campaign.bin`, the guest's is refused; while the guest plays Daxter the host's Jak has no Daxter (sidekick off), he is back once the guest leaves; after 3 more nodes closed (7), each saving the campaign, "Continue the campaign" loads it: 7. |
| `daxter-fire` | Daxter fires every weapon | The weapons cheat gives the twelve mods; Daxter fires each one at the host 4 to 7 m away: 47 hits taken, by ten kinds of shots (scatter gun, wave concussor, blaster, beam reflexor, gyro burster, vulcan fury, needle lazer, peace maker, mass inverter, super nova); screenshots of the blaster and the arc wielder fired by Daxter. |
| `outline` | Another player through a vehicle | In the open 9 m away, about 107 pixels of fill and outline (its feet in the ground); behind a Rhino buggy spawned between the players, 1770 to 1848: its red outline shows through the buggy (screenshot). |
| `player-list` | Health | The list gives each player's health (8/8) next to its ping (16 ms). |
| `join-world --loss 10` | The world save over a lossy link | The 42928-byte world arrives whole: every check of `join-world` passes. |

Round of 2026-10-19, afternoon (seated passengers, Daxter's firing animations; 20 scenarios,
`check_results.py`: 157 checks passed, 0 failed, no crash): `smoke`, `daxter-fire`,
`daxter-guns`, `ride`, `daxter-ride`, `daxter-drive`, `daxter-drive-own`, `coop`, `pose`,
`guns-fx`, `vehicle-guns`, `outline`, `lobby`, `player-list`, `coop-campaign`, `drive` and
`powers` as before (129 checks), and three players in each of:

| Scenario | Checks | Result |
|---|---|---|
| `ride-seat` | Jak as a passenger, guest then host | The host drives a two-seat buggy, the guest rides in it; then the guest drives one and the host rides: each passenger in `target-grab-ride`, its model in the wasteland car clips (`jakb-pilot-wcar-turn-back` on channel 0) at the seat point, facing the vehicle's way (before: the facing it got in with, the same log 0.5 m apart from one run to the next); a third player beside the buggy (screenshot). Two runs. |
| `ride-seat-daxter` | Daxter as a passenger, guest then host | The same with Daxters: each passenger in `target-indax-grab`, in Daxter's driving clips (`daxter-pidax-missile-turn-*`) at the seat point; its upper body (bone 3) 0.58 m above the seat, the Daxter driver's 0.51 m above his. Before: standing, 0.4 m behind the seat, hidden by its back. |
| `daxter-fire-anim` | Daxter's clips holding the gun | Daxter fires a red, a yellow, a blue (held) and a dark mod 7 m from the host, the camera turned around him: his clips `daxter-pilot-gun-red-fire` (frame 5.2 after the shot), `-red-idle`, `-yellow-idle`, `-blue-fire` (looping), `-dark-fire`, then none once put away; upper body only (`interp-select` `#xf0000ffffff8`), `gun-daxter` 1, the gun at his gun joint and its model 0.16 to 0.26 m from his right hand, drawn (before: `no-draw`), opened to each mod's shape (`gun-idle-red` ...); the host draws his clip on his puppet; screenshots at 2560x1440 from behind, the side and the front. |

Round of 2026-10-19, evening (the user's lobby, pause, Dark Jak, Appearance, seats and co-op
request): each feature checked by its scenario as it was made, then a sweep of 27 scenarios,
`check_results.py`: 226 checks passed, 0 failed, no crash (`smoke`, `lobby`, `pause`,
`player-list`, `powers`, `skin`, `outline`, `ride`, `daxter-ride`, `ride-seat`,
`ride-seat-daxter`, `ride-back`, `ride-multi`, `scene`, `scene-skip`, `beam`, `race-board`,
`race-continue`, `coop`, `coop-campaign`, `join-world`, `join-title`, `pvp`, `vehicle`,
`steal-dup`, `daxter-drive`, `daxter-fire`); `skin` again after the Appearance page's layout
fix, 10 of 10. The scenarios new or extended in this round:

| Scenario | Checks | Result |
|---|---|---|
| `lobby` (extended) | The start countdown | Once the guest is ready, the host's X starts the countdown, shown in both lobbies; X again cancels it, the guest sees it cancelled and its X no longer unreadies it meanwhile; X once more starts it, and the game starts when it ends; 15 checks. |
| `pause` | Pause for everyone, START | The host's START opens the menu over the running world (no progress mask, no still copy, clocks on), the stick pushed meanwhile does not move its player, the guest is not paused and sees the host's NPCs move; the host's SELECT pauses the guest (stock pause, its clocks stopped, START refused); SELECT again: the 3 s countdown on both, then both play on; SELECT held shows the list without pausing; the guest's own START menu runs over its world and does not pause the host; 11 checks. |
| `powers` (3 m apart) | Another player's Dark Jak | The host shows the guest's Dark Jak (blend shapes), then its Light Jak; the host draws the guest's Dark Jak skin, no tint, while its own model stays normal; the guest's own Dark Jak does not darken the host's model there; 8 checks (screenshot). |
| `skin` | Outfits | The guest's outfit is saved in its config, the host receives it (shirt, pants, scarf, hair) and draws the guest's puppet with it; the host's Appearance page edits a draft with a turning preview of Jak, then of Daxter; X keeps it (shirt and Daxter's fur saved, the preview gone) and the guest receives it; 10 checks. |
| `ride-back` | A passenger on a one-seat vehicle | The guest rides the host's turtle: seat 1 of 1, on its back (0.5 to 2 m from the driver's seat), its model at the seat point. |
| `ride-multi` | Three passengers' seats | Two passengers in the host's three-seat car: seats 1 and 2, each drawn in its own. |
| `scene-skip` | Skipping a cutscene together | Both watch the host's cutscene (2 viewers); the host's triangle asks (1/2) and it goes on; the guest sees the vote, asks, and the cutscene is skipped on both machines; 8 checks. |
| `beam` | A waypoint reached together | The host at a test beam is held (1/2, its owner sees it 45 m away) while the guest is away; the guest sees the count, comes, and the mission goes on for both; 6 checks. |
| `race-board` | A race start waiting for every player | The guest on foot gets a turtle of its own beside the host, then the 10 s countdown and the start; riding on the back of the host's turtle counts as aboard (countdown, start); Daxter on foot holds the start (1/2, the host may leave its vehicle), then sits on the driving host's shoulder and it goes; 8 checks (screenshot). Rewritten on 2026-10-20 (own vehicles only, 5.3). |
| `race-continue` | Both players at a race continue point | `desertb-race-start`: each machine gives its own player a turtle, the guest's 6 m back; each player drives its own; 6 checks. |
| `race-real` | Kleiver's race, a real mission | Co-op from the lobby at `desert-course-race`: the host's race (its `task-manager-race`) waits (1/2) while the guest loads the host's world, each machine's race gives its player a turtle (the guest's 6 m back), the countdown, then the race starts (held 14 s): timer and laps running on both screens; 7 checks (screenshots). |
| `race-real-daxter` | The same race, the guest plays Daxter | Daxter arrives in a turtle of his own; during the countdown he leaves it (1/2 again), jumps on the driving host's shoulder (triangle), the countdown starts again, the race starts with him there (held 16 to 20 s); twice in a row, 8 checks each (screenshots). Before the fixes of this round: the race started while the guest was still loading; the guest's turtle, moved aside, fell over against a racer; Daxter's triangle beside the host's turtle stole it instead. |
| `retail-general` (`--retail`) | The general features from a retail boot, three players | The title screen without the debug segment (`debug-segment #f`; its intro skipped with triangle); Host Game, two guests join; the lobby's countdown on the three screens, the host's X cancels it (both guests see it cancelled), X starts it again; free roam with three players; the host's SELECT pauses both guests (stock pause, "The host paused the game"), SELECT again: the 3 s countdown, then both play on; START opens the menu over the running world on the host and on a guest; the first guest's Dark Jak skin drawn on the host and on the other guest (screenshots: its dark aura on both); the host's outfit (shirt, scarf) received by both guests; the host starts a Spargus cutscene: its triangle asks (1/3), the others ask, it is skipped on the three machines; 14 checks. |
| `beam-real` | The arena's jump training, a real mission | Co-op at `arena-training-1`; its task manager taken to its last step (`go-state ... done`), whose beam it checks itself with `(target-pos 0)` within 3 m: the host in the beam is held (1/2, 480 calls answered from 45 m away) while the guest (Daxter) is 63 m away; the guest comes: "every player at the waypoint (2/2)", the mission completes on both machines (`resolution`); "Players here: 1/2" drawn at the bottom of the host's screen while it waits (`log-beams`: frames drawn, its text), then 2/2; 8 checks (screenshot). |

Kleiver's race (`race-real`, `race-real-daxter`) and the arena training (`beam-real`) also pass from
a retail boot (`--retail`, the lobby reached from the title screen): 7, 8 and 8 checks.

Scenarios of the round of 2026-10-19 (late evening) for the user's request on menus, co-op,
the host leaving, world sync and the economy. Final sweep on its last commit, 16 scenarios (the
new ones and `smoke`, `daxter-ride`, `ride-seat-daxter`, `race-board`, `race-real-daxter`,
`beam-real`, `skin`, `lobby`, `drive`; `host-quit` from a retail boot): 130 checks, 129 passed,
no crash (the five rows above `pressure` come from it; `pressure` and the rows after it from the
second pass that day). Final sweep of the second pass on its last code commit (`fa12541f3`): 19
scenarios (`smoke`, `coop`, `daxter-ride`, `ride-seat-daxter`, `race-real-daxter`, `beam-real`,
`skin`, `lobby`, `drive`, `player-list`, `scene-skins`, `coop-respawn`, `pressure`, `race-board`,
`economy`, `economy-trophy`, `economy-persist`, `economy-reload`, `host-quit` from a retail
boot), 151 checks, 0 failed, no crash. The failure, `race-board`'s Daxter not
getting on the driving host's shoulder, came from
its script: the guest moved next to the host 5 s before pressing triangle, while the host's
turtle rolls down the start's slope (6); it now moves there 1 s before: 8 of 8 in both runs since
(and in one run before that change).

| Scenario | Checks | Result |
|---|---|---|
| `host-quit` (`--retail`) | The host ends the session | In the host's free roam, the host back at its title screen sends the guest to its own, "The host ended the session" at the top; in the host's lobby, the host leaving it does the same; 6 checks. |
| `coop` (extended) | Daxter's climb | Daxter's triangle next to the crouching host: the host's Daxter appears and plays the stock climb (159 frames, rider 1), the guest hidden; screenshots during the climb on both machines; 8 checks. |
| `daxter-ride` (rewritten) | Daxter with a driving Jak | The guest, Daxter, presses L1 + triangle next to the host's buggy: he sits on the driving host's shoulder (the host's own Daxter shown there for him), travels 25 m with the vehicle, within 0.8 m of the driver; 8 checks. |
| `scene-skins` | Cutscene outfits | A Spargus cutscene on both machines: its Jak wears the host's shirt (`99ff3010`), its Daxter the guest's fur (`cc2040ff`) while the guest plays Daxter, the stock Daxter once it plays Jak, on both; 6 checks (screenshot). Before the fix, reading a cutscene's actors crashed the host (`handle->process` of a 0 handle). |
| `player-list` (extended) | A long name | A 15-character guest name fits before its health bar, nothing overlaps (screenshot); the LAN playout delay logged (76 ms). |
| `economy` | Orbs, the shop, saves | The host and the guest each take an orb: credited after the host checked the entity (and saw the guest at it); a claim of an entity that is no orb, and the guest's claim of an orb 80 m away, refused; a purchase without the orbs refused, then the gold outfit bought (5 orbs paid, item owned); at Kleiver's spot his line, triangle opens the shop page, X wears the outfit; the server's database keeps the accounts and the 2 orbs credited, the closing is recorded, and hosting again loads "2 account(s) and 2 orb(s) taken"; triangle leaves the shop; 9 checks (screenshots). |
| `free-shop` | Free roam's gun mods | Free roam starts both players at each color's first mod; the host credits itself 100 orbs, asks the red third mod first (refused: its second first), buys the second then the third (55 orbs left, its gun has them); the host's chat `/give Guest 40` credits the guest, which at Kleiver's opens his shop, goes down to the yellow second mod and buys it with X ("Bought!", 25 left, its gun has it); the database keeps both accounts under their account ids, never the players' secret ids; Kleiver's shop scrolled to its last items; 9 checks. |
| `chat` | The text chat | Lines both ways with the sender's name; accents lose theirs, `~` becomes `-`; a line typed shows, Backspace erases, Escape drops it unsent; T, text and Enter through SDL's event queue, T turning SDL's text input on for the game's window and the line sent turning it off; keys pressed as Windows messages on the window that has the focus (T, the text, Enter: Windows makes each character) send the line; a guest's command refused; the host's `/tp` brings the guest, `/players`, `/help` and an unknown command answer the host alone; `/kick gue` kicks the guest; 12 checks (screenshots). |
| `coop-save` | Co-op saves itself | The arena's jump training in co-op: saved at the mission's start into `multiplayer-campaign.bin`; a story step closed: saved again, the icon on the host's screen and the guest's (screenshots `save-icon`, `guest-save-icon`), the guest writing nothing; the START ring: the host's Save Game saves, Load Game refused, the guest's Save Game says the host saves; 8 checks. |
| `economy-trophy` | Metal Head trophies | A Metal Head killed by the host's blow: a trophy for the host; one killed by the guest's puppet's blow: a trophy for the guest, sent to it; 6 checks. |
| `coop-respawn` | Back next to a teammate | The guest killed comes back next to the host (`target-stance`, health 8, no continue point), then the host next to the guest; 6 checks. |
| `beam-real` | Regression | Passes again after the sidekick pointer check (the arena's cutscene left `(-> *target* sidekick)` `#f`, a crash in the `joint-control-remap!` wrapper). |
| `pressure` | Network pressure (1.1) | The guest drops 40 % of what it receives from 60 s to 80 s (`net`): the host reads 32 to 49 % delivered in its pings, goes severe ("between batches every 5 frames"), sends it about 180 KB/s instead of 290 to 330, and is back to normal 3 s after the losses stop; 7 checks. |
| `drive --loss 30` | Driving under 30 % loss | The buggy's copy follows the owner's path: 4.7 cm at the 95th percentile with the pressure (links congested and severe), 1.4 cm with `--fixed-cadence`; both within the check (10 cm), both as smooth as the owner's. |
| `race-board` (extended) | The choice on screen | The line shown at each step: Jak's (own vehicle or L1 + triangle), riding's, Daxter's, the shoulder's (screenshots: above the count, clear of the prompts and the minimap); 9 checks. |
| `economy-persist` | Saved while running | An orb each, 7 more credited to the guest; each change is in the server's database at once, and the games are stopped without ending the session; 6 checks. |
| `economy-reload` (`--settings-from economy-persist`) | After a restart | Hosting again loads "2 account(s) and 2 orb(s) taken"; the guest finds its 8 orbs by its account id (its secret and the server's id kept in the configs), the host its 1; the orbs each takes again in its fresh world are refused as already credited; 7 checks, the pair passing twice in a row. |

Scenarios of 2026-10-20 (the user's bug list and co-op rules; `check_results.py`). Each feature's
scenarios and its regressions ran after its commit (`pvp`, `vehicle-hit` after the co-op
protections; `daxter-fire`, `coop`, `daxter-drive` after the lizard; `beam-real`, `race-real` after
the mission starts; `steal-dup` after the handle fix). After the races, `smoke`, `ride`,
`daxter-ride`, `session-flow`, `coop-respawn`, `race-continue`, `pause`, `skin`, `lobby` and
`retail-general` (retail boot) passed every check, no crash; `ride-seat-daxter` failed: Kleiver was
drawn at the start and his shop took the L1 + triangle of the ride. His triangle now gives way
(4.20); on that last code `ride-seat-daxter` (Kleiver put at the start), `merchant` and `economy`
pass. Final sweep on the round's last code (`bbc67269e`): `lobby-text`, `ui-ingame`, `menu-still`,
`eyes`, `weapons-fx`, `dark-smack`, `dark-blast`, `dark-blast-coop`, `pre-game`, `session-flow`,
`coop-respawn`, `merchant`, `economy`, `coop-protect`, `daxter-flut`, `daxter-perch-fire`,
`mission-start`, `mission-trigger`, `race-result`, `race-board`, `race-real`, `race-real-daxter`,
`race-finish-win`, `race-seventh`, `pause`, and `skin-title`, `host-quit` from a retail boot: 27
scenarios, 185 checks, 0 failed, no crash. Then `pre-game` (5 checks) and `mission-trigger` (8)
from a retail boot too (`--retail`: the title screen, the lobby, no debug segment): every check
passed, no crash.

Over GameNetworkingSockets (2026-10-28 evening): `coop-roles`, `free-roles`, `minimap-cone`,
`challenge-free`, `challenge`, `pvp`, `places` (67 checks), then `door-wait`, `mission-trigger`,
`mission-start`, `race-finish`, `race-finish-win`, `mission-fail`, `mission-fail-retry` (59
checks): every check passed, no crash; the 5 unit tests pass. Two scenarios run at once on one PC
(one port) disturb each other: run them one at a time.

| Scenario | Checks | Result |
|---|---|---|
| `skin-title`, `lobby-text`, `ui-ingame` | Menus | The Appearance preview at the title screen (Jak, then Daxter, tinted); the lobby's lines and typed names fit, in the game's font; START > Options > Multiplayer in game holds the five customization entries (screenshots). |
| `menu-still` | START over the world | The START menu open, the stick and the throttle held: Daxter and his vehicle do not move. |
| `eyes` | Eye slots | Each Jak puppet feeds its eye slot; our Daxter player's model writes none (close-up). |
| `weapons-fx`, `dark-smack` | Trails, dark blast | The beam reflexor's and the plasmite RPG's trails counted on the guest; the host's dark blast hits the guest, no crash. |
| `session-flow` | Restart, quitting | The host's Restart restarts the guest next to it; a guest's asks the host; a guest's Quit leaves the server; 7 checks. |
| `coop-respawn` (rewritten) | Spectating | The dead guest watches the host 5 s ("Spectateur : Host", "Retour dans 5", camera on the host), its body on the ground, then comes back next to it; then the host the same; 7 checks. Each machine logs the other player once a second while it lies dead: drawn, still once its fall is over, until it comes back (2026-10-20 evening). |
| `merchant` | Kleiver's spot | The host draws a spot, the guest gets the same; each of the four spots set by the host reaches the guest and stands on the ground; his icon on the minimap, "Marchand" in yellow over him (the label read as drawn), his line at his spot; his model at each spot; at the last one the host walks into him from 4 m and stops against him, he faces it, his idle clips change (screenshots); 11 checks. |
| `coop-protect` | Teammates | The guest's punch does not hurt the host; the host's lizard drawn under it on the guest; no blow on the host's buggy sent; 7 checks (`pvp` and `vehicle-hit` in free roam unchanged). |
| `daxter-flut` | Daxter on a lizard | Daxter gets on the lizard the host left (triangle), drawn as Daxter, fires the scatter gun from it, jumps, rides 22 m, gets off and is Daxter again; the host draws the guest's lizard under him; 8 checks (screenshots). Since 2026-10-20 evening he fires the blaster (yellow 1, a projectile): the host draws its shots. |
| `daxter-perch-fire` | Firing from a shoulder | Daxter on the host's shoulder fires the blaster, the host sees him there; X puts him down; 6 checks. The host logs him climbing on and its own Daxter standing for him (his puppet hidden), and draws his blaster shots (2026-10-20 evening). |
| `mission-start` | A mission started by one player | Co-op at the arena training; the guest goes to Spargus's doors; the host's mission manager starts again: the guest is told, taken next to the host, and its own manager runs; 7 checks. |
| `race-board` (rewritten) | Own vehicles | The guest on foot gets a turtle; L1 + triangle at the host's turtle does not ride along, the start waits (1/2) until the guest drives again; Daxter's triangle at the driving host gives no shoulder; he gets a turtle of his own and drives it; 10 checks. |
| `race-finish`, `race-finish-win` | Kleiver's race to the finish, one race | Co-op from the lobby; the guest takes slot 3 on both machines (the host slot 4); once the race runs, the racers' AI drives each player's turtle (`race-autopilot`) for the 3 laps. Both finish behind racers (the host 4th, the guest 5th on both screens): the host's verdict lost, on both machines. With the host's racers stopped (`race-stop-racers`): the host finishes first, the guest second, each waits for the verdict ("our result waits for the host's verdict"): won on both machines, and the mission's end saves the campaign, the save icon on both machines (screenshots `host-mission-end-icon`, `guest-mission-end-icon`); 8 checks (`race-finish`), 9 (`race-finish-win`). Before the `wvehicle-race.gc` hook both machines crashed at the start (the racers' AI read the puppet's rigid body). |
| `mission-decline` | A mission declined | As `mission-trigger`; asked, the guest declines (D-pad down): the vote is lost on both machines, the host's intro is dropped and its player released (not grabbed), the hunt does not start, the guest stays at Spargus's doors; 7 checks. |
| `mission-trigger` | A mission started by talking to its giver | Co-op from the lobby at the kanga-rat hunt's introduction (`coop-mission-intro`, next to Kleiver at the arena); the guest goes to Spargus's doors; the host stands at Kleiver (`goto-type kleever-npc`) and presses triangle: the intro waits for the guest's answer; asked, the guest accepts (`wait-vote`, D-pad up), is taken next to the host, says it is ready; the intro plays on both machines (the guest's Kleiver started it as the guest arrived: "plays here with player 0"), both ask to skip it, the hunt's manager starts on both; 9 checks. A guest ready while its own move was under way (a continue point) was ready too early: it now waits for its move to end. |
| `door-wait` | Co-op doors wait for everyone | The host and the guest at the door nearest the host (Spargus); the host goes through and on 30 m, the guest stays 20 m behind it: the door, open, waits (`door hold #t`); the guest joins the host: the door no longer waits and closes; 7 checks. |
| `eyes-before` | The eye bug, shown again | As `eyes`, with `eye-fix-off` (the fix undone): the host's Jak, seen by the Daxter player, has the small dark eyes our hidden model writes (slot written by our target); `eyes` on the same code: Jak's blue eyes, slot written by the puppet (close-ups at 2560x1440); 5 checks. |
| `lizard-attack` | The user's report: Daxter attacks Jak's mount | Co-op: the host rides a lizard of Spargus; the guest (Daxter), 1.5 m from it, punches it three times and fires the scatter gun at it: the host stays on it, unhurt, and the guest sees the lizard under Jak before, during and after (screenshot); 6 checks. |
| `places`, `places-before` | Places at the same coordinates | The host at Haven's stadium gate, the guest in the volcano 23.6 m away: not drawn, not on the minimap, no copy on either side, the guest keeps its own NPCs; before (`places-off`), each drew the other and the guest showed the host's NPCs; 4 checks each. |
| `tints`, `tints-before` | Tints of the copies | Haven's port: the copies of the host's tinted vehicles wear their tint on the guest within 1/100; before (`tints-off`), the default paint; 3 checks each. |
| `challenge-board` | Co-op: the jet-board challenge | The guest (Daxter) joins the host's jet-board challenge as Jak; the judges score 500000 and 300000 trick points (`trick-points`): the higher wins the 18 orbs of its gold cup; the guest is Daxter again; 6 checks. |
| `challenge-ring`, `challenge-eggs` | Co-op: rings and an egg hunt | Spargus's rings and the desert's egg hunt: invited, joined, each ended on its own (7.3 s and 8.5 to 9 s), the faster gets the manager's orbs (10, 18); 5 checks each. |
| `challenge`, `challenge-one` | Co-op: burning bush challenges | The host starts the city's get-to challenge 26, the guest is invited, accepts, comes to its start and plays it; both reach the orb, the faster (the host) gets the 3 orbs once both results are in; or the host fails and quits, the guest gets them; `challenge` also checks the winner's account (3 orbs more, the other's none); 7 and 6 checks. |
| `coop-roles` | Co-op roles swapped in game, the minimap cones | The host takes Daxter: the guest becomes Jak; the guest takes Daxter back: the host is Jak again; each mode follows; each minimap marks the other with a dot and a cone in its color; 5 checks. |
| `free-roles` | Characters in free roam | The guest takes Jak, the host stays Jak; 3 checks. |
| `minimap-cone` | The cone on the minimap | In Spargus the guest walks about 30 m away and faces the host: the host's minimap shows its blue cone alone (its dot hidden there), turned toward us (screenshot); 4 checks. |
| `challenge-free` | A burning bush challenge in free roam | As `challenge`, in free roam, with the winner's account credited (by run); the challenge, decided, offers itself again on both machines 5 s later; 12 checks. |
| `daxter-board` | Daxter on the jet-board | The guest (Daxter) takes the board out with R2: board states, the board at 60 % and drawn, X's jump, R2 puts it away (Daxter on foot); the host draws his board; 6 checks. |
| `soak` | Memory over a long session | 5 minutes in Haven's port, the host away and back twice: the heaps and the processes come back to the same levels; 4 checks. |
| `mission-fail`, `mission-fail-retry` | A mission failed, for every player | The jump training's failure (no choice) or a retry-or-quit one: shown on host and guest; it does not reset by itself; votes that differ decide nothing; both retry (or go on), then a second failure both quit (or go on): decided on both machines within 0.2 s (host time), both at the same spot; 8 checks each. |
| `menu-buttons` | Buttons under the START menu | The menu opened with START, R1, square, L1, the D-pad, X (choosing entries) and triangle pressed, X again right after it closes: no shot, no gun change, no jump or attack, host (Jak, gun out) and guest (Daxter); X made both jump before the history fix (reproduced); 6 checks. |
| `daxter-moves` | Daxter's D-pad and L1 + triangle | The D-pad takes his gun out, the mod chosen in his hand, no shot; next to the standing host's Jak the prompt asks L1 + triangle, triangle alone does nothing, L1 + triangle climbs on its shoulder (the host sees it), X puts him down; 6 checks (screenshots). |
| `mission-switch` | The host changes mission in game | Co-op at the palace ruins' patrol; the host chooses the ruins' attack (Jak in the Rhino, "rubblea-rhino"): both players at its start, each in its own Rhino (the host sees the guest drive vehicle 17), no game stops; 7 checks (screenshots). |
| `mission-select` | The host chooses a mission in game | Co-op at the jump training; START > Options > Multiplayer > Choose a mission, act 2, its second mission (the Hip Hog): the host's game starts there, the guest loads it again and stands 1.5 m from the host; no Kleiver in co-op; 7 checks (screenshots). |
| `mounts-coop` | A lizard for every player | The kanga-rat hunt (as `mounts-dup`): two lizards at the hunt's spot on the host (the stock's count unchanged), the guest gets on one and the host's copy of it hides, the host rides the other, the guest gets off and a lizard waits there on the host; 8 checks (screenshots). |
| `mounts-dup` | A lizard for each player in a mission | The kanga-rat hunt started by talking to Kleiver (as `mission-trigger`): the host gets on the hunt's lizard, the guest (Daxter) on its own copy of the same lizard and rides it 18 m away; the host draws the guest's lizard; 6 checks (screenshots). |
| `yellow2-shot` | The beam reflexor's shot | The host fires the yellow 2 mod 14 times at the guest 8 m away: 15 trails drawn on the guest, the beam from the host's gun seen in its screenshots (`--shot-size 2560x1440`); 5 checks. |
| `pre-game` | Spargus's pre-game in co-op | Co-op from the lobby at `wascity-pre-game`: both machines run the bubble game, the guest wins (R1, the game's debug win) and its resolution streams; the host's effects replayed on the guest (7, `trace-fx`). Before the fix the guest stopped as in the user's log of 2026-10-19 (`*mp-stock-launch-spawn*` +#x143, two runs out of two); after it, three runs without a crash; 6 checks. `combat` and `props` pass again (their Daxter check now expects no Daxter of the game on a Jak player). |
| `dark-blast`, `dark-blast-coop` | The dark blast | Dark Jak's L1 + square (`target-darkjak-bomb1`) 4 m from the other player: before the fix the host's game stopped (access violation in `vector<-cspace!` from `lightning-tracker`); now its bolt to the guest's hurtbox follows its position, the guest is hit (free roam), the guest's blast hits the host, no game stops; in co-op no damage; 7 and 6 checks. |
| `race-real`, `race-real-daxter` (rewritten) | Kleiver's race | Both races report place and progress (the guest counts the host's 4 racers); Daxter leaving his turtle during the countdown holds the start, gets no shoulder, gets back in his own turtle: the countdown again, then the race; 8 and 10 checks. |

Under a network's delay (2026-10-15, `--latency 50:30`: what each instance receives held 50 ms
plus up to 30 ms, so 100 to 160 ms there and back; 15 scenarios, `check_results.py`: 121 checks
passed, 0 failed, no crash):

| Scenario | Under 50 ms + 30 ms of jitter |
|---|---|
| `smoke`, `pvp`, `vehicle`, `coop`, `join-world` | Every check of the scenario passes: puppets and hits, the steal prompt, the co-op of that day (leash, held story steps, the host's weather), the host's world loaded and the guest 1.5 m from the host. |
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
| ![The host behind a buggy, outlined in red through it](multiplayer-screenshots/outline-through-vehicle.jpg) | ![Daxter firing the arc wielder at the host](multiplayer-screenshots/daxter-arc-wielder.jpg) |
| ![The game mode list, six modes grayed out](multiplayer-screenshots/lobby-modes.jpg) | ![The lobby in free roam](multiplayer-screenshots/lobby-free-roam.jpg) |
| ![The on-screen keyboard](multiplayer-screenshots/keyboard.jpg) | ![The player list with health and ping](multiplayer-screenshots/player-list-health.jpg) |
| ![Co-op: Jak without Daxter on his shoulder, Daxter played by the other player](multiplayer-screenshots/coop-no-shoulder-daxter.jpg) | |
| ![Two Jaks in a buggy: the host drives, the guest sits as a passenger](multiplayer-screenshots/passenger-seated-jak.jpg) | ![Two Daxters in a buggy: the host drives, the guest sits as a passenger](multiplayer-screenshots/passenger-seated-daxter.jpg) |
| ![Daxter holding the scatter gun, from the side](multiplayer-screenshots/daxter-gun-aim.jpg) | ![Daxter firing the vulcan fury, from the front](multiplayer-screenshots/daxter-gun-fire.jpg) |
| ![The lobby's start countdown, on the guest](multiplayer-screenshots/lobby-countdown.jpg) | ![The guest's game paused by the host](multiplayer-screenshots/pause-for-everyone.jpg) |
| ![The START menu over the running world](multiplayer-screenshots/start-menu-live.jpg) | ![The Appearance page and its preview](multiplayer-screenshots/appearance-page.jpg) |
| ![The host in its outfit, as the guest sees it](multiplayer-screenshots/outfit-remote.jpg) | ![The guest's Dark Jak skin on the host's screen](multiplayer-screenshots/dark-jak-remote-skin.jpg) |
| ![The vote to skip a cutscene](multiplayer-screenshots/scene-skip-vote.jpg) | ![Players at a waypoint](multiplayer-screenshots/beam-players-here.jpg) |
| ![A race start waiting for Daxter](multiplayer-screenshots/race-wait.jpg) | ![Two passengers in the host's car, seats 1 and 2](multiplayer-screenshots/ride-multi-seats.jpg) |
| ![Kleiver's race: Daxter on the host's shoulder, the start's countdown](multiplayer-screenshots/race-countdown-daxter.jpg) | ![A guest's Dark Jak seen by a third player, retail boot](multiplayer-screenshots/dark-jak-third-player.jpg) |
| ![Daxter climbing onto the host's shoulder](multiplayer-screenshots/coop-climb.jpg) | ![A cutscene: the host's outfit on Jak, the guest's on Daxter](multiplayer-screenshots/scene-outfits.jpg) |
| ![Kleiver's line and prompt in Spargus](multiplayer-screenshots/kleiver-merchant.jpg) | ![Kleiver's shop, the gold outfit worn](multiplayer-screenshots/kleiver-shop.jpg) |
| ![A guest back at the title screen: the host ended the session](multiplayer-screenshots/host-ended-session.jpg) | ![The player list with a long name](multiplayer-screenshots/player-list-long-name.jpg) |
| ![A race start: Daxter's choice above the count, the shoulder prompt below](multiplayer-screenshots/race-choice.jpg) | |

The screenshots come from scenario runs (`out/mp-test/<scenario>/screenshots`): the late evening
round of 2026-10-19 for the last three rows (`coop`, `scene-skins`, `economy`, `host-quit`,
`player-list`); the evening
round of 2026-10-19 for the five rows before the last, its night round (`race-real-daxter`, `retail-general`) for the last; the seated
passengers and Daxter holding the gun from the afternoon round of 2026-10-19 (`--shot-size
2560x1440`, cropped); the outline, Daxter's arc wielder, the lobby, the keyboard, the player list
and the co-op one from the round of 2026-10-19; `vehicle-prompt`, `remote-laser`, `remote-shot` and `join-*` from 2026-10-15; the others
from earlier rounds.

Over the Internet, the host forwards UDP port 7777 (or the port chosen) and guests use the host's
public IP.

## 6. Known limits

- The places at the same coordinates (4.27) come from a table of level names made from the
  entities' positions and the continue points' loads; a level not in it counts as Haven or the
  wasteland. Two places meet at a gate: across it, a player whose level is not loaded here is not
  seen.
- A challenge's invitation lasts 30 s, and its result waits for it: a player who plays alone in
  co-op gets the reward up to 30 s after the start. A player sitting on its failure screen (the
  game frozen until it chooses) takes the others' results once it leaves it.
- A challenge's score is read at fixed offsets in two level types (4.29); a challenge of another
  kind is ranked by its time.
- Past 256 events waiting in a paused game, the oldest effects are not replayed (4.30).
- The economy is a proof of concept: one server per host machine (its `server=` id), free roam
  alone, eight gun mods and four outfits to buy. Co-op does not share the orb and skull gem
  counters yet: each player counts what it takes.
- A player's account rests on its `multiplayer.cfg` (`uid=`): a player who loses that file loses
  its accounts on every server. The host can change any account (its database is on its machine).
- The chat keeps printable ASCII: accented letters lose their accent; another script's letters
  are dropped. T opens it even when the player bound T to a game action.

- A failure keeps the game's choices: a story mission's offers only to go on (the game puts the
  players back at the mission's start), a minigame's retry or quit. A failure's `execute` script
  runs only on the machine whose mission failed.
- Kleiver does not speak: his art group has no talking clip (only idles and a walk), so his line
  is text and the shop is the menu page.
- Co-op seconds are given to the lizards a level places. The leaper race seats its player through
  its continue point; a player on foot during it gets a racing lizard instead (4.32). The desert's
  lizard roundup gives its own by other means (`desert-lizard-task.gc`); the vehicles of the
  desert missions are spawned by the mission's manager, which runs on each machine: one per player
  already.
- A player arriving on foot within 20 m of an idle turret is mounted on it for 15 s (4.32): in
  practice the continue points of the turret missions.
- The mission sweep (4.32) plays no mission through: a phase is checked up to its start (each
  player in its vehicle, gun, torpedo, robot, lizard or mech), not to its end.
- Hovered, Choose a mission draws gray instead of white, in the lobby's co-op page as in game; it
  works.
- Effects drawn in an object's local space (`sp-group-flag sp12`) are not replayed on the other
  machines: they need the object's binding (see the change log of 2026-10-20).
- A guest's parked vehicle is not sent: the other players only see the vehicles one drives.
- One race, the host's (4.15): the guest's finish is judged on the host from its puppet, drawn
  about 100 ms plus half the round trip late: in a close finish against a racer the host sees it
  later than the guest did. The racers' speed follows the host's place in the race. A player who
  crashes out of a race fails the mission for both (the failure vote, 4.26), as before: no
  "did not finish" for one player while the other races on. The time trial stays each player's own.
- A mission's vote (4.24): only the scene of an open story node's `when-open` is held and asked:
  a mission started without a cutscene (reaching a spot) is not asked, and brings the others
  afterwards (4.24's teleport). A player away from the pad for 15 s declines.
- A door waits for a teammate behind it without a time limit (as himstan's): a teammate who stays
  behind within 100 m keeps it open, and the levels behind it loaded.
- The medal games' orbs (gun courses, turret, power game, pre-game) are the game's orbs: they do
  not reach the shop's accounts (4.20), which only credit an orb entity or a challenge's checked
  reward.
- The router port opens through UPnP or NAT-PMP only: a router with both off, or a carrier NAT,
  needs the port forwarded by hand (the menu then shows no internet address). Steam's relay and
  lobbies (himstan's) are not taken: they need Steam's SDK and an app id. A second server on the
  same PC and port is not refused (GNS's socket): only the tests run two on one PC, one at a time.
- Not done from the 2026-10-28 review: relaying by interest (the host relays everything to every
  guest: with two players, nothing to cut), string and process-tree work in the hot paths, the
  quadratic matching of copies, synchronous art loads, the test code in GAME (a retail boot runs the
  test scripts too), and splitting multiplayer.gc.

- Another place's objects (1.6) are drawn within 400 m of our player only, from their art loaded
  alone (32 MB at most, freed with the level whose memory holds it); farther, they are not drawn.
  Their sounds stay silent (sound banks follow our place). Art groups are read from
  `out/jak3/obj`, which a compiled install has.
- A guest joining loads the host's world through a loading screen: the session goes on meanwhile,
  its puppet is hidden until it is in game. Leaving the session, it stays in the host's world
  without saving until it loads its own save or goes back to the title screen.
- After the lobby's countdown, guests waiting there follow within 10 s of the host being in game
  (they ask again every 10 s), plus their loading screen. A guest that leaves the lobby with
  triangle disconnects; the scenarios do not press triangle in the lobby itself (they do on the
  multiplayer options page reached from it).
- While online, only the host's SELECT pauses (for everyone); START opens the menu over a running
  world, so a player in its menu can be hit there.
- Vehicle guns do not lock onto other players (the hurtbox answers no target search, 4.2): a
  buggy's shells hit a player only where they come down (26 m ahead in `vehicle-guns`).
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
- UDP without congestion control: the session sends what the frame produces (about 0.85 MB/s
  per other player with a crowd nearby, 1.1) whatever the link; a slow link loses states and
  poses (the next ones replace them) and resends events.
- Text fields use the on-screen keyboard; a PC keyboard drives it as a controller (no typing).
- Outlines show a player hidden by anything, our own player's model included, and a few pixels
  of a player's feet where they sink into the ground.
- Daxter's shots leave from 0.6 m before his chest, not from the gun's muzzle, and auto-aim at
  enemies, guards, civilians and crates, not at players (as Jak's). His clips holding the gun are
  the ones he fires from a vehicle with (the scatter gun's throws his head back), on his upper
  body over his running legs; the gun is Jak's size, as in the stock vehicles.
- A Jak passenger sits in the driver's clip, hands forward as on a steering wheel (the game has no
  passenger clip for Jak); without Jak's pilot clips (a level without vehicles), he crouches.
- Six game modes of the list are placeholders (grayed out).
- Free roam starts at Spargus; a guest's progress is the host's (it joins the host's world).
- Co-op: one multiplayer campaign per host (a new campaign or a mission replaces it at its first
  save).
- Another player's Dark Jak keeps its eyes (only the `darkjak` texture group has a skin per
  player); Light Jak's wings and shield are not drawn (their art is loaded only with our own Light
  Jak).
- The Appearance page's preview has no cloth (scarf, flaps).
- Co-op waypoints only wait for missions whose arrival check calls `target-pos` from the process
  that spawned the beam; those checking the vehicle, a cutscene region or a touch (the arena
  training's first beam: its judge is touched) go on with one player. The count at the bottom is
  the nearest beam's.
- Each machine runs its own copy of a race (its timer, laps and racers): the host's and a guest's
  start on their own once every player is aboard on each.
- Races: a vehicle left at a race start rolls down its slope while the start waits (no hand brake,
  as in the stock game; 30 m in 20 s at `desertb-race-start`). The race's opponents are simulated
  by the authority (1.4): in a race run on a guest's machine they sleep there.
- The wave concussor's charge glow is not drawn on its copy (its particles follow our own gun);
  its explosion darkens the screen of every player near it, as the shooter's.
- The arc wielder and the needle lazer lock onto enemies, civilians and vehicles, not onto
  players: they hurt a player whose way they cross, and turn to hostile NPCs near it first.
  Beyond 6 m the wave concussor needs a clear line over the ground (stock rules).
- NPCs frozen around a player far from the authority were not reproduced in the scenarios; the
  stock pause rule is extended to other players as a safeguard (4.6).
- Economy: the host is the server, so its own machine keeps the accounts; a modified host can
  credit anything, guests cannot. An orb is credited only where the host has its level loaded and
  sees its taker at it: an orb taken far from the host (another place, free roam) is refused.
  Each orb pays once per session, whoever takes it: a new campaign under the same session key
  gives nothing for orbs credited before. Trophies buy nothing yet; the shop has four outfits.
- Daxter's climb plays only onto a Jak on foot: onto a driving Jak's shoulder he appears there at
  once (the stock has no climb in a vehicle).
- A dead player comes back next to a teammate only on foot and within 300 m; in a vehicle, the
  stock death (a mission that fails on death fails).
- Network pressure (1.1) reads every loss as a loaded link, as himstan's does: on a link that drops
  datagrams at random without being full, sending less does not lower the losses and costs some
  precision (`drive` under 30 % loss: 4.7 cm at the 95th percentile against 1.4 cm at a fixed
  cadence, both within the check). Its levels follow pings, once a second.

## 7. Change log

- **2026-10-29:** the user asked for an economy proof of concept (free roam's gun colors at their
  first mod, the higher mods bought at Kleiver's), at the server's level with each player
  identified, co-op saves apart from the single-player ones and on their own with the save icon,
  and a keyboard chat with the administrator's commands. Account ids per server, derived from the
  player's secret by HMAC-SHA256, and the economy in a SQLite database written at once (4.20);
  co-op saves after each story step, the icon on every screen, the START ring's Save and Load
  Game online (1.7); the chat and its commands (4.35); protocol 12. Fixed on the way: a pickup
  with no parent any more crashed `mp-challenge-take` on every orb taken outside a challenge
  (`economy`). Scenarios `free-shop` 9/9 (the purchase from the shop page), `chat` 12/12 (SDL's text input on the window; keys pressed as Windows messages), `coop-save` 8/8; `coop-campaign` now
  continues the progress saved after each step (11/11); `economy` 9/9, `economy-persist` 6/6,
  `economy-reload` 7/7, `economy-trophy` 6/6, `merchant` 11/11, `challenge-free` 12/12,
  `mission-trigger` 9/9, `smoke` 9/9; `race-finish-win` 9/9: Kleiver's race won ends the mission,
  the campaign saves itself and the save icon shows on both machines (screenshots); unit tests 6/6.
  The chat was driven by keys pressed as Windows messages on the focused window; a person's own
  keyboard is the user's play test.

- **2026-10-28 (night):** the user asked that both players accept a mission: the others are always
  asked (D-pad up to join, D-pad down to decline, 15 s), the mission goes on when at least half of
  them accept (with two players, the other one); declined, the starter's intro is dropped and its
  player released (4.24). Scenario `mission-decline`; `mission-trigger` accepts with D-pad up;
  command `wait-vote`.
- **2026-10-28 (evening):** the user asked for himstan's network layer, his co-op doors and races,
  every player in a mission's cutscene with the agreement of at least half of the players, free
  roam at the end of the game with only the orb activities, endlessly, and the review's fixes.
  Transport: GameNetworkingSockets with his five lanes, his router port mapping, protocol 11
  (1.1). Co-op: one race, the host's, the other player in a racer slot (4.15); a mission's
  introduction held until every player is there (4.24); doors wait for a teammate behind them
  (4.34). Free roam: Ratchet's courses closed, every orb activity offered again once done, a
  challenge's orbs credited per run (4.29). Fixes from the review: the failure's decision sent
  reliably (4.26), Daxter's lizard clips copied for every ride (4.25), the economy, campaign and
  config files written whole or not at all and an unreadable economy kept aside, the world blob
  from the host alone and for the last request (1.7), the host's teleport only from the host and
  once the player is free, the elevator and art tables recycled, the art descriptions allowed to
  give way, the skeleton descriptions assembled per sender, bounded damage and world requests,
  poses in 12 bytes a bone and 60 a second at most, the level cleanup only once a session ran, a
  door's crossing check without its scripts (4.34). master-dev: the DECI2, REPL and debugger
  servers listen on 127.0.0.1 only. Scenario `door-wait`; `mission-trigger`, `race-finish`,
  `race-finish-win`, `challenge-free` checks changed; `race-result`, `race-seventh` and the
  commands `race-sim`, `race-extra-ahead` removed with the per-machine race.

- **2026-10-28 (afternoon):** review of the mod and of himstan's Jak 2 mod (his 250 bug fixes
  checked against ours). Daxter's mode forwards to Jak's only the modes missions put on him (a
  turret, a vehicle, the torpedo, the robot, the mech): forwarding every mode had broken his guns
  (`'gun`, as his `c0049af6`); a challenge's orbs credited once per player (a guest's repeated start
  could earn them again); after a challenge played as Jak the duo stays a Jak and a Daxter; a
  perched Daxter has no minimap icon (as his `ab82013c`).
- **2026-10-28:** the user asked for Jak or Daxter in game (co-op: always one of each, so a choice
  swaps the roles; free roam: each its own), the other players on the minimap in their color with
  the cone our player has, and the burning bush challenges in free roam too, the best winning the
  orbs (4.33, 4.29). The winner's orbs now reach its account in co-op too. Scenarios `coop-roles`,
  `free-roles`, `minimap-cone`, `challenge-free`; commands `choose-character`, `log-role`.
- **2026-10-27 (night):** the user asked that every mission play in co-op, special phases included
  (4.32): Daxter takes the modes only Jak had, three crashes fixed (the copies' death effect, the
  glider, the torpedo), a racing lizard for every player of the leaper race, the mission's gun
  mounted on arrival. Tool `mission_sweep.py`, commands `log-play`, `trace-play`.
- **2026-10-27 (evening):** the user asked that Daxter ride the jet-board alone, with his board
  clips, the board at his size, the same mechanics as Jak (4.31). Scenario `daxter-board`.
- **2026-10-27:** memory, events and sync. Vehicles are sent to 350 m (the port's barges on every
  screen). Players in places at the same coordinates (the
  volcano, the mine, the war factory and the precursor ship, the catacombs and their rail, the
  halfpipe, the nest, all over Haven's centre) never see each other and count as far apart (4.27).
  Copies wear their original's tint, a vehicle's paint or a citizen's shade (protocol 10, 4.28).
  Co-op burning bush challenges: the others are invited, each plays on its own (a Daxter player as
  Jak), the best of those who reached the objective gets the reward (4.29). A paused player keeps the events that matter
  (4.30); a 5 minute session measured stable. Scenarios `places`, `places-before`, `tints`,
  `tints-before`, `challenge`, `challenge-one`, `challenge-board`, `soak`.
- **2026-10-21:** after the user's play test: a mission's failure shows for every player at once,
  waits for every player's choice (retry or quit, or go on), and puts every player back together
  (4.26). Scenarios `mission-fail`, `mission-fail-retry`; commands `send-fail`,
  `spawn-fail-or-retry`, `log-fail`.
- **2026-10-20 (night):** the user's play test of the round below (two instances, `local_pair.py`).
  - *Crash.* The guest's game stopped in the palace ruins on a renderer assertion right after
    "shadow overflow detected": the engine's skip of an overflowing shadow tested `==` and skipped
    nothing (`Shadow2.cpp`, now `!=`; C++ table in 3).
  - *START menu.* X choosing a menu entry made the player jump: the jump reads the PC port's
    high-fps button history, now emptied with the pad (4.10). Square, R1, L1 and the D-pad were
    already blocked; `menu-buttons` reproduced the jump on both players, then showed none.
  - *Daxter.* The D-pad takes his gun out with the mod chosen (4.8); L1 + triangle climbs on any
    Jak on foot within 2.5 m, at any time (4.16).
  - *Kleiver* stands at his spot: his level `lkleever` borrowed into waswide while his spot is out;
    solid (a player walking into him from 4 m stops 1.6 m away), he turns toward the player and
    shifts between his idle clips (4.20). Daxter's gun name and ammo moved above the prompts, which
    they overlapped (4.8).
  - *Changing mission in game* (the palace ruins' attack, Jak in the Rhino): reproduced as played
    (`mission-switch`): both players at its start, each in its own Rhino, the host seeing the
    guest drive; the user's log shows the same (the guest's Rhino, "pilot-on") until the crash.
  - *Tests.* Scenarios `menu-buttons`, `daxter-moves`, `mission-switch`; `merchant` checks the
    model; commands `host-play-mission`, `log-input`. Sweep on the rebuilt engine: 18 scenarios,
    131 checks passed, 0 failed, no crash.
- **2026-10-20:** the user's list of bugs and co-op rules (menus, eyes, weapons, sessions, co-op
  missions, races, the merchant). The parts that follow himstan's Jak and Daxter II Multiplayer
  (ISC) say so in the commits and the code.
  - *Menus.* The lobby's character, ping and status lines shrink to fit (Daxter's ran over the
    ping); a name or address a player typed goes through the font's encoding (`mp-user-text`:
    raw, g, j, p, q and y drew with the wrong glyph, larger or out of line); the Appearance preview
    shows at the title screen (its model flagged `hud`, which the menu mode's foreground still
    draws). The START page in game only customizes the player (4.21).
  - *Eyes.* The Jak puppets animate their eyes; our player as Daxter no longer writes his eyes into
    the slot every Jak shares (`merc-eye-anim`, 3).
  - *Weapons.* The beam reflexor's shot and the plasmite RPG's grenade draw their light trail on the
    other machines; a replayed effect keeps to its copy's joints (6). The dark blast stopped the
    game of the player setting it off near another player (its bolt to the player's hurtbox read a
    joint it has not): the bolt follows the hurtbox's position (4.23).
  - *Sessions.* Restart for everyone, a guest's Quit leaves the server, only the host pauses a
    cutscene (4.21); a dead co-op player watches a teammate for 5 s, its body on the ground (4.19).
  - *Co-op.* No friendly fire, no blow on a teammate's vehicle, the ridden lizard seen by the
    others (4.23); Daxter rides lizards and fires from them and from a shoulder (4.22, 4.8); a
    mission one player starts brings the others there, checked on a real start (4.24; a shared
    cutscene is only played where it is loaded, a far teleport goes through the continue point);
    races with own vehicles, places
    counting the players and a result won only in the first places (4.15).
  - *Merchant.* Kleiver at a random spot of four, his icon on the minimap, "Merchant" in yellow;
    the host's +100 orbs and trophies in the Mods menu (4.20).
  - *Fix.* Two handles were kept in global symbols, which hold 32 bits: our stolen vehicle and the
    one we drove last now live in `*mp-globals*`.
  - *Crash of 2026-10-19 (Spargus's pre-game).* Reproduced by `pre-game` (co-op at
    `wascity-pre-game`, the guest wins with the game's debug R1): the guest stopped at the same
    address as in the user's log (`*mp-stock-launch-spawn*` +#x143, from a
    `part-tracker-subsampler`). The host's effects of the game's crystal (`group-wascity-pre-game-
    crystal-glow`...) are drawn in the crystal's local space (`sp-group-flag sp12`): a launch
    control copies a matrix from `local-space-binding`, which overlays `fade` and only the
    object's `set-local-space-info` sets; replayed on a tracker without it, the launch read
    address 0 + #x20. `mp-group-replayable?` refuses those groups: three runs, no crash.
  - *himstan's Jak and Daxter II Multiplayer (ISC)* inspired three parts of this round, credited in
    their commits: hosting only from the title screen and the in-game page limited to the player's
    own settings (49b868411), the host's restart for every player (cc508a1ba), the dead player
    watching a teammate (8d4d2046d). Missions, races, lizards, the merchant and the fixes are this
    mod's own.
  - *After the user's review.* Kleiver trades in free roam only (4.20). The mission pages list the
    missions by act, and the host chooses one in game, started for every player (4.24): a guest
    loading the host's world again skipped nothing before, so the steps the host's new game closed
    started missions in its old world and their announcements pulled the host to it; a guest
    joining now neither announces nor follows, nor applies steps before it has the world. In co-op
    every lizard of a level near the player has a second one beside it on every machine, the one
    another player takes hides, and one waits where it gets off (4.25). `coop-campaign` pressed
    up from Continue the campaign (where the lobby now starts) and chose a new one; `mission-start`
    counted the guest's announcement while joining, now silent (as did `pre-game`, which now checks
    the host's mission and the guest's resolution cutscene). Scenarios `mission-select`,
    `mounts-coop`. Full sweep of the bug list on cbb70bd3f: 34 scenarios, 233 checks passed,
    0 failed, no crash (`skin-title` and `host-quit` from a retail boot). Then, for evidence seen
    from the other machine: Daxter's blaster shots from a lizard and from a shoulder drawn on the
    host, the dead player's body still and drawn on the other machine, the "Marchand" label
    checked as drawn, and `mission-start` / `mission-trigger` passed three times each.
  - *Tests.* Scenarios `skin-title`, `lobby-text`, `ui-ingame`, `menu-still`, `eyes`,
    `weapons-fx`, `dark-smack`, `session-flow`, `merchant`, `coop-protect`, `daxter-flut`,
    `daxter-perch-fire`, `mission-start`, `mission-trigger`, `race-result`, `race-finish`, `race-finish-win`, `dark-blast`, `dark-blast-coop`, `pre-game`; `coop-respawn`, `race-board`,
    `race-real-daxter` and `economy` rewritten or extended; commands in 5.3.

- **2026-10-19 (night, second pass):** after the stop hook's review of the first pass.
  - *Network pressure,* after himstan's cadence scheduler: each link's losses and queueing,
    measured with our channel, thin the sends between batches (1.1, 1.10).
  - *Races:* the start says each player's choice on screen (4.15).
  - *Economy:* a player who comes back gets its balances (it saw 0 until it earned something);
    saved while running and reloaded after a restart, checked by `economy-persist` and
    `economy-reload` (`run_mp_test.py --settings-from`).
  - *Boot fix:* `*fallback-text-heap*` raised to 128 KB; the English text had outgrown its 80 KB and
    every boot stopped in `c_symlink2` (3).
  - *Tests:* scenarios `pressure`, `economy-persist`, `economy-reload`; `race-board` extended;
    commands `net`, `log-net`; `run_mp_test.py --fixed-cadence`.

- **2026-10-19 (late evening):** the user's request on menus, co-op, the host leaving, world sync
  and an economy. The parts that follow himstan's Jak and Daxter II Multiplayer (ISC) say so in
  the commits and the code.
  - *Host leaving.* Every guest goes back to the title screen with "The host ended the session"
    (1.8).
  - *Daxter on a Jak's shoulder,* after himstan's ride-along: no Daxter of the game on a player
    Jak; a Daxter player climbs on only next to a Jak (crouching or driving), with the stock
    climb, hidden while the Jak's own Daxter wears his outfit; in a Jak's vehicle he sits on the
    shoulder (4.16).
  - *Cutscenes:* the host's outfit on Jak, the Daxter player's on Daxter, the stock Daxter when
    every player is a Jak (4.17).
  - *Menus:* names shortened with "...", lines shrunk to fit, the status line localized (4.18);
    the Appearance page closer to himstan's editor: the part between L2 and R2, each character
    back on its last part, held keys repeat (4.12).
  - *Co-op missions,* after himstan's respawn policy: a dead player back next to a living
    teammate, the mission going on (4.19).
  - *World sync* compared with himstan's (1.10): the playout delay now adapts to each sender's
    lateness (1.1).
  - *Economy:* precursor orbs and Metal Head trophies per player, kept by the host alone under a
    secret id, each orb paying once; saved every minute and at the session's end, each end
    recorded, loaded back; Kleiver's shop in Spargus (4.20). Protocol 9.
  - *Fixes:* the sidekick remap wrapper and the cutscene actors' handles no longer crash.
  - *Tests.* Scenarios `host-quit`, `scene-skins`, `economy`, `economy-trophy`, `coop-respawn`;
    `coop`, `daxter-ride` and `player-list` extended; commands in 5.3. Each test instance starts
    without the user's economy files.

- **2026-10-19 (night):** the co-op features checked on real missions and from a retail boot.
  - *Races,* in Kleiver's race: the start waited only for the players already in game, so it went
    while the guest was still loading the host's world; it now waits for every connected player,
    in co-op only. Once every player is aboard, "The race starts in 10" leaves time to leave a
    vehicle and ride with another. The guest's vehicle appears 6 m behind the player's (moved
    aside, it fell over against a racer). In co-op, Daxter's triangle next to a driving Jak's
    vehicle puts him on that Jak's shoulder, never steals the vehicle (4.15).
  - *Waypoints:* a player still loading counts as away; the log says when a held beam is released.
  - *Tests.* Scenarios `race-real`, `race-real-daxter`, `beam-real` (real missions, `coop-mission`,
    `go-state`, `log-state`), `retail-general` (`run_mp_test.py --retail`, three players; the
    title screen's intro is skipped with triangle while the scripts wait for its menu).

- **2026-10-19 (evening):** the user's lobby, pause, Dark Jak, Appearance, seats and co-op
  request. The parts that follow himstan's Jak and Daxter II Multiplayer (ISC) are credited in the
  commits and the code.
  - *Lobby countdown.* Once every guest is ready, the host's X starts a 10 s countdown every player
    in the lobby sees; X again cancels it (1.8). Protocol 8: the player state grows to 224 bytes,
    and Dark and Light Jak move to their own field (packed from flag bit 8, they overlapped the
    lobby's ready and co-op flags).
  - *Pause.* The host's SELECT pauses every player's game, SELECT again resumes it after a 3 s
    countdown; START opens the menu over a running world, which no longer pauses anyone (4.10).
  - *Dark Jak.* Every player sees another player's Dark Jak skin at its own level, not a purple
    tint; the local player's Dark Jak no longer darkens the others (4.11).
  - *Appearance page,* after himstan's skin editor: a color for each part of Jak's and Daxter's
    clothes, a turning preview, saved and sent to every player (4.12).
  - *Seats,* after himstan's seat reservation: any free seat of the vehicle (three passengers in a
    city car), the back of a one-seat vehicle (4.3).
  - *Co-op.* Cutscenes skipped once every player watching asked (4.13); waypoints wait for every
    player, "Players here: 1/2" (4.14); races give every player a vehicle and start once every
    player is aboard (4.15).
  - *Tests.* Scenarios `pause`, `skin`, `ride-back`, `ride-multi`, `scene-skip`, `beam`,
    `race-board`, `race-continue`; `lobby` and `powers` extended; commands in 5.3.

- **2026-10-19 (afternoon):** passengers seated, Daxter's firing animations.
  - *Seated passengers.* A passenger, Jak or Daxter, host or guest, plays the clips a driver of
    that vehicle sits in, steering straight and leaning neither way; Daxter's are the ones he
    drives with, placed from the seat point (before: Jak crouched, Daxter stood). The passenger's
    own model faces the vehicle's way (before: the way Jak faced when getting in) (4.3).
  - *Daxter's firing animations.* He holds the gun in his hand, opened to the mod's shape (before:
    not drawn, his mode hides the gun), and his upper body plays the stock clips he fires from a
    vehicle with, aiming and firing by color, over his legs' clip, on every machine (the state
    carries the clip, `top-index` and `top-frame`, in its former padding). `jak-pilot-gun+0-ag`
    moved to GAME from four city levels (4.8).
  - *Tests.* Scenarios `ride-seat`, `ride-seat-daxter`, `daxter-fire-anim` (three players: one
    watches); commands `log-seat`, `log-joints`, `log-anim`; `log-daxter-gun` reports the gun in
    his hand and his clips; `--shot-size`.

- **2026-10-19:** the user's nine-point request.
  - *UDP.* The session runs over UDP: states, objects and poses unreliable (only newer than the
    last delivered), join, events and the world save on a reliable ordered channel (sequence
    numbers, selective acknowledgements, resends); protocol 7 (1.1). `--loss` drops received
    datagrams in tests; a unit test sends a 200 KB world and 200 events under 20 % loss.
  - *Every weapon, for Jak and Daxter.* Mods menu > Give every weapon. Daxter fires every mod
    with R1, chosen with the D-pad, aimed at the nearest enemy ahead; the wave concussor and the
    peace maker charge through subtypes of theirs (4.8).
  - *Outlines through walls,* in each player's color: a pass of the PC renderer after the 3D
    scene (4.9).
  - *On-screen keyboard* for the name and the address (1.8).
  - *Lobby:* Host Game opens the lobby with the server, which guests join before any game starts;
    the game mode list shows eight modes, six grayed out (coming soon) (1.8).
  - *Free roam* is the end of the game: the story closed, everything unlocked, for every player;
    nothing saved (1.7).
  - *Co-op:* two players; a new campaign, the campaign continued, or a mission of the story; the
    host's saves go into its multiplayer campaign file; Jak has no Daxter on his shoulder while
    the other player plays Daxter, who sits there himself when perched (1.7).
  - *Player list:* each player's health next to its ping (4.7).
  - *Tests.* Scenarios `outline`, `daxter-fire`, `coop-campaign`, `lobby` (rewritten); commands
    `look-player`, `log-outline`, `cheat weapons`, `daxter-mod`, `log-daxter-gun`, `log-name`,
    `log-session`, `coop-start`, `log-missions`; `log-ping` gives the health.

- **2026-10-16 (evening):** himstan's menus and duo, Daxter in vehicles. The parts that follow
  himstan's [Jak and Daxter II Multiplayer](https://github.com/himstan/Jak-And-Daxter-2-Multiplayer)
  (ISC) are credited in the code where they are used.
  - *Title screen and lobby.* Host Game (new game or save) and Join Game (address) on the title
    screen, then a lobby: players, character (L1 / R1), ready / start (X), game mode (square),
    multiplayer options (circle), leave (triangle) (1.8). A guest waiting in a menu asks for and
    loads the host's world from the per-frame hook (1.7): before, it waited for ever there.
  - *Game modes.* Free roam and co-op run the same rules; co-op is the Jak and Daxter duo (role
    offer, Daxter on a crouching Jak's shoulder). The leash and the held story steps are gone
    (1.7).
  - *Vehicles.* A stolen vehicle no longer shows twice. Daxter gets in vehicles (a stolen one, a
    taken NPC vehicle, one of his own world) and drives them as Daxter, its animations moved to
    GAME. Riding along is L1 + triangle; Daxter rides as Daxter, placed with himstan's seat
    offsets. Vehicle guns' shells and shots are drawn on the other machines and hurt the other
    players (4.3).
  - *Player list.* Hold SELECT for every player's name and ping (runtime ping, protocol 6); SELECT
    no longer pauses while online (4.7).
  - *Tests.* Scenarios `lobby`, `steal-dup`, `daxter-drive`, `daxter-drive-own`, `daxter-guns`,
    `vehicle-guns`, `daxter-ride`, `player-list`, `coop` (rewritten), `ride` (L1 + triangle);
    commands `wait-menu`, `address`, `log-lobby`, `spawn-vehicle`, `log-vehicles`, `log-ping`.

- **2026-10-16:** `scripts/mp-test/local_pair.py` starts two instances to play by hand on one
  PC, one with the controller, the other with the keyboard (5.2). The keyboard instance hides
  XInput controllers too (`SDL_XINPUT_ENABLED=0`).

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
