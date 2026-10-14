#pragma once

/*!
 * Jak 3 multiplayer mod: the network session and the kernel functions that expose it to GOAL
 * (goal_src/jak3/pc/features/multiplayer/).
 *
 * One player hosts (slot 0) and the others connect to them over TCP (star topology). Every
 * player sends its own state ~30 times a second; the host relays each state and each event to
 * the other players. Remote states are buffered and rendered 100 ms in the past, interpolated
 * between the two snapshots around that time, so remote players move smoothly despite jitter.
 *
 * The GOAL side owns all gameplay meaning: C++ only moves bytes, keeps the player list and
 * interpolates. The two structures below are mirrored field for field by GOAL types.
 */

#include <array>
#include <deque>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "common/common_types.h"

namespace mp {

constexpr int kMaxPlayers = 8;
// Bump when PlayerState, Event or the wire format change: mismatched peers are refused.
constexpr u32 kProtocolVersion = 1;
constexpr int kDefaultPort = 7777;
// Remote players are drawn this far in the past, so two snapshots surround the drawn time.
constexpr double kInterpDelayMs = 100.0;
// Local state is sent at most this often (30 Hz).
constexpr double kStateIntervalMs = 33.0;

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
  s8 character;         // 116 0 = Jak, 1 = Daxter
  s8 color;             // 117 color index
  s8 seat_count;        // 118 seats of the driven vehicle
  s8 ride_slot;         // 119 slot of the driver this player rides with, -1 = none
  u32 time_ms;          // 120 sender clock, filled by the session
  u32 pad;              // 124
  char name[16];        // 128 nickname, null-terminated
  char level[16];       // 144 name of the level the player stands in
  char cont[32];        // 160 name of the player's current continue point
};
static_assert(sizeof(PlayerState) == 192, "PlayerState must match mp-player-state");

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

// Mirrors the GOAL type `mp-actor-state`: one NPC (character or vehicle) simulated by the host and
// drawn as a replica by the other players.
struct ActorState {
  float trans[4];    // 0   root position, w = uniform scale
  float quat[4];     // 16  root orientation
  u32 id;            // 32  host process pid
  s16 anim_index;    // 36  index of the animation in the art group, -1 = none
  u16 flags;         // 38  mp-actor-flag bits (GOAL)
  float anim_frame;  // 40
  u32 seg_mask;      // 44  low half of the draw-control seg-mask (body variants)
  char skel[32];     // 48  skeleton-group name, e.g. "skel-citizen-norm"
};
static_assert(sizeof(ActorState) == 80, "ActorState must match mp-actor-state");

constexpr int kMaxActors = 64;

// Received snapshots of one actor, oldest first, with the sender time of each.
struct ActorTrack {
  std::deque<std::pair<double, ActorState>> snaps;
  double last_seen = 0;
};

// Mirrors the GOAL type `mp-config`: the settings kept between boots.
struct Config {
  char name[16];     // 0
  char address[32];  // 16 "ip:port"
  s32 character;     // 48
  s32 color;         // 52
  s32 enabled;       // 56
  s32 pad;           // 60
};
static_assert(sizeof(Config) == 64, "Config must match mp-config");

enum class Mode : s32 { Offline = 0, Connecting = 1, Client = 2, Host = 3 };

// One player's received snapshots, oldest first.
struct Remote {
  std::deque<PlayerState> snaps;
  double clock_offset = 0;  // local ms minus sender ms, tracks the fastest delivery
  void clear() { snaps.clear(); }
  void push(PlayerState s, double now_ms);
  bool sample(double now_ms, PlayerState* out) const;
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
  // Host: send the NPCs it simulates. Guests ignore it.
  void send_actors(const ActorState* actors, int count, double now_ms);
  // Guest: interpolated NPCs of the host, at most `max`. Returns the count.
  int get_actors(double now_ms, ActorState* out, int max);
  bool pop_event(Event* out);
  // Host only: disconnect a player, optionally refusing its address until the server stops.
  void kick(int slot, bool ban);

  Mode mode() const { return m_mode; }
  int local_slot() const { return m_local_slot; }
  const std::string& message() const { return m_message; }

  struct Peer;

 private:
  void handle_frame(Peer& p, u8 type, const u8* data, size_t len);
  void route_event(Event ev, int sender_slot);
  void drop(Peer& p, const std::string& reason);
  void refuse(Peer& p, const char* why);

  Mode m_mode = Mode::Offline;
  int m_local_slot = -1;
  int m_max_players = kMaxPlayers;
  std::string m_message;
  u64 m_listen = ~0ull;        // listening socket (host)
  std::vector<Peer*> m_peers;  // host: clients, client: the server
  std::array<Remote, kMaxPlayers> m_remotes;
  std::unordered_map<u32, ActorTrack> m_actors;
  double m_actor_clock_offset = 0;
  bool m_actor_clock_known = false;
  std::deque<Event> m_events;
  std::set<std::string> m_banned;
  double m_connect_start = 0;
  double m_last_state_sent = -1e9;
  double m_now = 0;  // time of the last update()
};

// Session used by the kernel functions below.
Session& session();

// Automated tests (MP_TEST_SCRIPT, see scripts/mp-test/): buttons and left stick forced on pad 0
// while active, read by scePadRead (game/sce/libpad.cpp).
struct TestPad {
  u32 buttons = 0;  // pad-buttons bits (select = bit 0 ... square = bit 15)
  u8 lx = 128;      // left stick, 128 = centered
  u8 ly = 128;
  bool active = false;
};
extern TestPad g_test_pad;

// Splits "ip:port". Returns false on a malformed address.
bool parse_address(const std::string& address, std::string* ip, int* port);

}  // namespace mp

namespace jak3 {
// Registers the pc-mp-* kernel functions. Called from InitMachine_PCPort.
void init_multiplayer_functions();
}  // namespace jak3
