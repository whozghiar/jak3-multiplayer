#pragma once

/*!
 * Jak 3 multiplayer mod: the network session and the kernel functions that expose it to GOAL
 * (goal_src/jak3/pc/features/multiplayer/).
 *
 * One player hosts (slot 0) and the others connect to them over UDP (star topology): states,
 * objects and poses go unreliably (a newer one follows within a frame), everything else on a
 * reliable ordered channel. Every player sends its own state every frame; the host relays each
 * state and each event to the other players. Remote states are buffered and rendered 100 ms in the past, interpolated
 * between the two snapshots around that time, so remote players move smoothly despite jitter.
 *
 * The GOAL side owns all gameplay meaning: C++ only moves bytes, keeps the player list and
 * interpolates. The two structures below are mirrored field for field by GOAL types.
 */

#include <array>
#include <atomic>
#include <deque>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "common/common_types.h"

namespace mp {

constexpr int kMaxPlayers = 8;
// Bump when PlayerState, Event or the wire format change: mismatched peers are refused.
constexpr u32 kProtocolVersion = 9;
constexpr int kDefaultPort = 7777;
// Remote players are drawn this far in the past, so two snapshots surround the drawn time.
constexpr double kInterpDelayMs = 100.0;
// The delay a sender's player and objects are drawn with adapts to how late its messages come
// (Session::delay_of), as the playout buffer of himstan's Jak and Daxter II Multiplayer
// (game/multiplayer/platform/replication/snapshot_timeline.h, ISC): the object batch interval
// (15 Hz) plus a margin plus 2.5 times the mean lateness, between kMinDelayMs and kMaxDelayMs.
constexpr double kActorBatchMs = 1000.0 / 15.0;
constexpr double kMinDelayMs = kActorBatchMs + 10.0;
constexpr double kMaxDelayMs = 300.0;
// Object batches (ACTORS, POSES) carry the sender clock in tenths of a millisecond (wraps after
// 119 hours): the receiver's clock estimate and the blend between poses a frame apart need better
// than the millisecond.
constexpr double kBatchTicksPerMs = 10.0;
// Local state is sent at most this often (every frame at 60 frames a second): a driver's puppet
// blended between states 33 ms apart drifted on the vehicle it sits in, which moves every frame.
constexpr double kStateIntervalMs = 15.0;
// A late object keeps moving along its last velocity this long at most, then holds.
constexpr double kMaxExtrapolateMs = 150.0;
// Object snapshots kept per object: the drawn time stays inside them at every send rate.
constexpr double kActorHistoryMs = 500.0;
// mp-actor-flag shot (GOAL): a shot in flight, gone 250 ms after its last state.
constexpr u16 kActorShot = 1 << 6;

// Mirrors the GOAL type `mp-player-state` (multiplayer.gc). Positions are game units.
struct PlayerState {
  float trans[4];       // 0   root position
  float quat[4];        // 16  root orientation
  float vel[4];         // 32  velocity (units/s), used to extrapolate a late player
  float veh_trans[4];   // 48  driven vehicle position
  float veh_quat[4];    // 64  driven vehicle orientation
  float seat_trans[4];  // 80  world position of the driven vehicle's passenger seat
  float anim_frame;     // 96  frame of the current animation
  float health;         // 100
  float health_max;     // 104
  u32 flags;            // 108 mp-flag bits (GOAL)
  s16 anim_index;       // 112 index of the animation in the art group, -1 = none
  s16 veh_type;         // 114 vehicle-type of the driven vehicle, -1 = on foot
  s8 character;         // 116 low 4 bits: 0 = Jak, 1 = Daxter; high 4 bits: outfit
  s8 color;             // 117 color index
  s8 seat_count;        // 118 seats of the driven vehicle
  s8 ride_slot;         // 119 slot of the driver this player rides with, -1 = none
  u32 time_ms;          // 120 sender clock, filled by the session
  u32 seg_mask;         // 124 body segments shown (low half of the draw-control seg-mask)
  char name[16];        // 128 nickname, null-terminated
  char level[16];       // 144 name of the level the player stands in
  char cont[32];        // 160 name of the player's current continue point
  u16 ping_ms;          // 192 sender's round trip to the host, 0 for the host, filled by the session
  u8 lobby_mode;        // 194 host: game mode chosen in the lobby
  u8 lobby_start;       // 195 host: co-op start (new campaign, continue, mission)
  s16 lobby_mission;    // 196 host: co-op mission chosen
  s16 top_index;        // 198 Daxter's gun clip on his upper body (art group index), -1 = none
  float top_frame;      // 200 its frame
  u32 powers;           // 204 Dark / Light Jak as the model shows them (mp-pack-powers)
  u16 countdown_ms;     // 208 host: ms before the lobby's game starts, 0 = no countdown
  u16 resume_ms;        // 210 host: ms before the paused game resumes, 0 = none
  s16 seat_offset[6];   // 212 driver: seats 2 and 3 from the vehicle's root, its frame, in cm
};
static_assert(sizeof(PlayerState) == 224, "PlayerState must match mp-player-state");

// Mirrors the GOAL type `mp-event`. `kind` and the arguments are defined in GOAL.
struct Event {
  s32 kind;       // 0
  s32 from;       // 4  sender slot, filled by the session
  s32 to;         // 8  destination slot, -1 = every other player
  s32 iarg[4];    // 12
  s32 pad;        // 28
  float farg[4];  // 32
  char text[32];  // 48
};
static_assert(sizeof(Event) == 80, "Event must match mp-event");

// Mirrors the GOAL type `mp-actor-state`: one object simulated by a player and drawn as a replica
// by the others: the host's NPCs (characters, vehicles), and every player's own props (Daxter on
// the shoulder, the gun, the driven vehicle and its wheels).
struct ActorState {
  float trans[4];    // 0   root position, w = uniform scale
  float quat[4];     // 16  root orientation
  u32 id;            // 32  owner slot << 24 | owner process pid
  s16 anim_index;    // 36  index of the animation in the art group, -1 = none
  u16 flags;         // 38  mp-actor-flag bits (GOAL)
  float anim_frame;  // 40
  u32 seg_mask;      // 44  low half of the draw-control seg-mask (body variants)
  char skel[32];     // 48  skeleton-group name, e.g. "skel-citizen-norm"
};
static_assert(sizeof(ActorState) == 80, "ActorState must match mp-actor-state");

constexpr int kMaxActors = 128;

// Received snapshots of one actor, oldest first, with the sender time of each.
struct ActorTrack {
  std::deque<std::pair<double, ActorState>> snaps;
  double last_seen = 0;
  int source = 0;  // slot of the player who simulates it
};

// A pose: every bone of an object, relative to the object's frame (its root position and
// orientation, without scale), so it can be laid on the copy of the object another player draws.
// This carries everything the owner's animation system produced: blended channels, streamed
// animations, joint mods (look-at, aiming), ragdolls. Wire encoding of one bone:
struct PoseJoint {
  s16 q[4];  // rotation, x 32767
  s16 t[3];  // translation, in units of 4 game units (1 mm), +-32 m
  s16 s[3];  // scale per axis, x 2048 (negative: mirrored axis)
};
static_assert(sizeof(PoseJoint) == 20, "PoseJoint is a wire format");

// Wire encoding of one particle emitter an object used since the previous batch.
struct PoseEmitter {
  u16 index;  // in *part-group-id-table* (kind 0) or *part-id-table* (kinds 1, 2)
  u8 kind;    // 0 group (launch control), 1 single launcher 2d, 2 single launcher 3d
  u8 count;   // launches since the previous batch
  s16 t[3];   // origin relative to the object's frame, units of 4
  u16 home;   // single launchers: hash of the level holding the launcher, 0 = global data
  s16 q[4];   // origin rotation relative to the frame, x 32767
};
static_assert(sizeof(PoseEmitter) == 20, "PoseEmitter is a wire format");

// Mirrors the GOAL type `mp-emitter`: one emitter to draw on a copy, in world space.
struct EmitterOut {
  s32 kind;
  s32 index;
  s32 count;      // single launchers: launches to replay now (0 until the next batch)
  s32 home;       // single launchers: PoseEmitter::home
  float mat[16];  // world origin
};
static_assert(sizeof(EmitterOut) == 80, "EmitterOut must match mp-emitter");

// Wire encoding of one sound an object played (or stopped) since the previous batch.
struct PoseSound {
  u8 name[16];  // sound-name
  u32 id;       // the owner's sound id: the same id over several batches is one lasting sound
  s32 vol;
  s32 pitch;
  s32 bend;
  s16 t[3];   // position relative to the object's frame, units of 4
  u16 flags;  // kSoundPositional, kSoundStopped
};
static_assert(sizeof(PoseSound) == 40, "PoseSound is a wire format");
constexpr u16 kSoundPositional = 1;
constexpr u16 kSoundStopped = 2;

// Mirrors the GOAL type `mp-sound`: a sound to send (position in world space), or to play on a
// copy (position on the copy's frame).
struct SoundIO {
  u8 name[16];
  u32 id;
  s32 vol;
  s32 pitch;
  s32 bend;
  s32 flags;
  s32 pad[3];
  float pos[4];
};
static_assert(sizeof(SoundIO) == 64, "SoundIO must match mp-sound");

constexpr int kMaxPoseJoints = 256;
constexpr int kMaxPoseEmitters = 32;
constexpr int kMaxPoseSounds = 32;

// Bit 15 of an entry's joint count: the entry carries the object's effects (emitters and sounds,
// with each batch). Entries sent between batches carry the bones only.
constexpr u16 kPoseHasEffects = 0x8000;

struct PoseSnap {
  std::vector<PoseEmitter> emitters;
  std::vector<PoseSound> sounds;
};

// Received poses of one object, oldest first, with the sender time of each: the bones (up to every
// frame) and the effects (every batch) separately.
struct PoseTrack {
  std::deque<std::pair<double, std::vector<PoseJoint>>> joints;
  std::deque<std::pair<double, PoseSnap>> snaps;
  double last_seen = 0;
  double consumed = -1;         // sender time of the batch whose single launches were handed out
  double consumed_sounds = -1;  // same for its sounds
  int source = 0;
};

// Mirrors the GOAL type `mp-config`: the settings kept between boots.
struct Config {
  char name[16];     // 0
  char address[32];  // 16 "ip:port"
  s32 character;     // 48
  s32 color;         // 52
  s32 enabled;       // 56
  s32 mode;          // 60 game mode when hosting: 0 free roam, 1 co-op
  u32 skin[16];      // 64 the player's outfit: per group, rgb and strength in the top byte
};
static_assert(sizeof(Config) == 128, "Config must match mp-config");

enum class Mode : s32 { Offline = 0, Connecting = 1, Client = 2, Host = 3 };

// One player's received snapshots, oldest first.
struct Remote {
  std::deque<PlayerState> snaps;
  double clock_offset = 0;  // local ms minus sender ms, tracks the fastest delivery
  double jitter = 0;        // mean lateness of its states behind the fastest delivery (ms)
  void clear() { snaps.clear(); }
  void push(PlayerState s, double now_ms);
  bool sample(double now_ms, PlayerState* out) const;
  // The state at sender time `t`, interpolated (extrapolated a little past the newest).
  bool sample_at(double t, PlayerState* out) const;
};

class Session {
 public:
  ~Session();
  // Start hosting on the port of `address` ("ip:port", only the port is used).
  bool host(const std::string& address, int max_players);
  // Start connecting to `address` ("ip:port" or "host:port"). Completes in update().
  bool join(const std::string& address, double now_ms);
  // Close everything. `reason` becomes message().
  void leave(const std::string& reason);
  // Pump the network once: accept, read, dispatch, send `local`, flush.
  void update(PlayerState local, double now_ms);
  // Interpolated state of `slot`, or false if no such remote player.
  bool get_player(int slot, double now_ms, PlayerState* out) const;
  void send_event(Event ev);
  // Send `size` bytes to player `to` (the host's world save for a guest that joins), in frames
  // of at most kBlobChunk bytes; the host relays a guest's blob.
  void send_blob(int to, const u8* data, size_t size);
  // A blob fully received: its size (0 while none or still arriving).
  size_t blob_ready() const { return m_blob_got && m_blob_got == m_blob.size() ? m_blob.size() : 0; }
  // Copy the received blob into `out` (at most `max` bytes) and forget it. Returns the size copied.
  size_t take_blob(u8* out, size_t max);
  // Send the objects this player simulates (the host relays a guest's batch to the others).
  void send_actors(const ActorState* actors, int count, double now_ms);
  // Interpolated objects of the other players, at most `max`. Returns the count.
  int get_actors(double now_ms, ActorState* out, int max);
  // Queue the pose of one object: `count` world bone matrices, relative to `frame` (rigid, 16
  // floats, GOAL layout). `count` may be 0 to send only effects. With `effects`, the entry also
  // carries the object's emitters and sounds (add_emitter, add_sound) and replaces the last ones.
  void add_pose(u32 id, const float* frame, const float* bones, int count, bool effects);
  // Queue an emitter of the object of the last add_pose. `origin` is a world matrix; `kind` holds
  // PoseEmitter::kind in its low byte and PoseEmitter::home above.
  void add_emitter(int kind, int index, int count, const float* origin);
  // Queue a sound of the object of the last add_pose.
  void add_sound(const SoundIO& snd);
  // Send the queued poses (the host relays a guest's poses to the others).
  void send_poses(double now_ms);
  // World bones of object `id` laid on `frame`, interpolated like its actor state. Returns the
  // count written (0 when no pose arrived).
  int get_pose(u32 id, double now_ms, const float* frame, float* out, int max);
  // Emitters object `id` uses now, in world space on `frame`. Returns the count written.
  int get_emitters(u32 id, double now_ms, const float* frame, EmitterOut* out, int max);
  // Sounds object `id` played in the batch reached at the drawn time, each batch handed out once,
  // positioned on `frame`. Returns the count written.
  int get_sounds(u32 id, double now_ms, const float* frame, SoundIO* out, int max);
  bool pop_event(Event* out);
  // Host only: disconnect a player, optionally refusing its address until the server stops.
  void kick(int slot, bool ban);
  // Host only: players accepted, the host included (those already in stay).
  void set_max_players(int max_players);
  // Round trip to the host of player `slot` in ms (0 for the host), -1 while unknown.
  int player_ping(int slot) const;
  // The secret id of player `slot` (ours for the local slot, the one its HELLO carried for a
  // guest, host side), 0 unknown. It names the player's account in the economy.
  u64 slot_uid(int slot) const;

  // Time `source`'s objects are drawn at now, in its clock (ms); the local clock for -1.
  double clock_of(int source, double now_ms) const {
    return source < 0 ? now_ms : drawn_time(source, now_ms);
  }
  Mode mode() const { return m_mode; }
  int local_slot() const { return m_local_slot; }
  const std::string& message() const { return m_message; }

  struct Peer;

 private:
  void handle_frame(Peer& p, u8 type, const u8* data, size_t len);
  void handle_ping(Peer& p, u8 type, const u8* data, size_t len);
  void route_event(Event ev, int sender_slot);
  void store_actors(int source, const u8* data, size_t len, double recv_ms);
  void store_poses(int source, const u8* data, size_t len, double recv_ms);
  void store_blob(const u8* data, size_t len);
  void note_actor_clock(int source, double sent_ms, double recv_ms);
  double drawn_time(int source, double now_ms) const;
  // Local ms minus `source`'s ms: the fastest delivery of its states and its batches alike, so its
  // puppet and its objects (the vehicle it drives) are drawn at the same moment.
  double offset_of(int source) const;
 public:
  // The delay player `source` and its objects are drawn with (ms), adapted to its lateness.
  double delay_of(int source) const;
 private:
  void drop(Peer& p, const std::string& reason);
  void refuse(Peer& p, const char* why);
  void receive_all(double now_ms);
  void dispatch_datagram(u32 ip, u16 port, const u8* data, size_t len);
  Peer* peer_for(u32 ip, u16 port, u32 conn, const u8* data, size_t len);
  void on_datagram(Peer& p, const u8* data, size_t len);

  Mode m_mode = Mode::Offline;
  int m_local_slot = -1;
  int m_max_players = kMaxPlayers;
  std::string m_message;
  u64 m_sock = ~0ull;          // the session's UDP socket
  // Datagrams received under a test latency, with the time they may be read (address in network
  // byte order).
  struct HeldDatagram {
    double at;
    u32 ip;
    u16 port;
    std::vector<u8> bytes;
  };
  std::deque<HeldDatagram> m_held;
  std::vector<Peer*> m_peers;  // host: clients, client: the server
  std::array<Remote, kMaxPlayers> m_remotes;
  std::unordered_map<u32, ActorTrack> m_actors;
  // Per sender: local ms minus sender ms, for its actor batches (see Remote::clock_offset).
  std::array<double, kMaxPlayers> m_actor_offset{};
  std::array<bool, kMaxPlayers> m_actor_offset_known{};
  std::array<double, kMaxPlayers> m_actor_jitter{};  // mean lateness of its object batches (ms)
  std::unordered_map<u32, PoseTrack> m_poses;
  std::vector<std::vector<u8>> m_pose_out;  // entries queued by add_pose, sent by send_poses
  float m_pose_frame[16] = {};              // frame of the last queued entry
  std::deque<Event> m_events;
  std::vector<u8> m_blob;  // blob being received (sized by its first chunk)
  size_t m_blob_got = 0;
  std::set<std::string> m_banned;
  double m_connect_start = 0;
  double m_last_state_sent = -1e9;
  double m_now = 0;  // time of the last update()
};

// Session used by the kernel functions below.
Session& session();

// This player's secret id (multiplayer.cfg uid=): its economy account on any host.
u64 player_uid();
void set_player_uid(u64 uid);

// Automated tests (MP_TEST_SCRIPT, see scripts/mp-test/): buttons and sticks forced on pad 0
// while active, read by scePadRead (game/sce/libpad.cpp).
struct TestPad {
  u32 buttons = 0;  // pad-buttons bits (select = bit 0 ... square = bit 15)
  u8 lx = 128;      // left stick, 128 = centered
  u8 ly = 128;
  u8 rx = 128;      // right stick (camera), 128 = centered
  u8 ry = 128;
  bool active = false;
};
extern TestPad g_test_pad;

// Test only: what every session receives is held `latency_ms` (plus up to `jitter_ms`) and
// `loss_percent` of the datagrams are dropped (MP_TEST_LATENCY and MP_TEST_LOSS set them at boot).
void set_test_network(double latency_ms, double jitter_ms, double loss_percent);

// Splits "ip:port". Returns false on a malformed address.
bool parse_address(const std::string& address, std::string* ip, int* port);

// Levels whose renderer data (fr3) is loaded although no level slot holds them: the models of a
// distant player's objects whose art was loaded alone (pc-mp-render-level). pc_set_levels adds
// them to the levels the renderer keeps.
std::vector<std::string>& extra_render_levels();

// Outlined models (other players seen through walls) the renderer drew so far, counted by
// Merc2::draw_outlines (render thread), read by the test log (pc-mp-outlines-drawn).
std::atomic<int>& outlines_drawn();
// Samples of fill and outline drawn last frame where walls hide those players
// (pc-mp-outline-pixels): 0 while every outlined player is in view.
std::atomic<int>& outline_pixels();
// Draws of another player's model with a Dark Jak skin of its own (morph above 0) since boot,
// counted by Merc2::do_draws, read by the test log (pc-mp-dark-skin-draws).
std::atomic<int>& dark_skin_draws();
// Draws of a player's model with an outfit tint since boot (Merc2), read by the test log
// (pc-mp-skin-draws).
std::atomic<int>& skin_draws();

}  // namespace mp

namespace jak3 {
// Registers the pc-mp-* kernel functions. Called from InitMachine_PCPort.
void init_multiplayer_functions();
}  // namespace jak3
