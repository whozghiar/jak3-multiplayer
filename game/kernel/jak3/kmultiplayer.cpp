// Winsock must be included before anything that may pull in windows.h.
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <netdb.h>
#include <unistd.h>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/select.h>
#include <sys/socket.h>
#endif

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>

#include "kmultiplayer.h"

#include "common/log/log.h"
#include "common/symbols.h"
#include "common/util/FileUtil.h"
#include "common/util/string_util.h"

#include "game/graphics/screenshot.h"
#include "game/kernel/common/Ptr.h"
#include "game/kernel/common/kscheme.h"
#include "game/kernel/jak3/kscheme.h"
#include "game/runtime.h"

#include "fmt/format.h"

namespace mp {

// ---------------------------------------------------------------------------------------------
// Sockets: the few calls that differ between Winsock and BSD sockets.
// ---------------------------------------------------------------------------------------------

#ifdef _WIN32
using sock_t = SOCKET;
constexpr sock_t kNoSock = INVALID_SOCKET;
static int sock_error() {
  return WSAGetLastError();
}
static bool would_block(int e) {
  return e == WSAEWOULDBLOCK || e == WSAEINPROGRESS || e == WSAEALREADY;
}
static void close_sock(sock_t s) {
  closesocket(s);
}
static void set_nonblocking(sock_t s) {
  u_long on = 1;
  ioctlsocket(s, FIONBIO, &on);
}
constexpr int kSendFlags = 0;
static void shutdown_send(sock_t s) {
  shutdown(s, SD_SEND);
}
static void net_init() {
  static bool done = false;
  if (!done) {
    WSADATA wsa;
    done = WSAStartup(MAKEWORD(2, 2), &wsa) == 0;
  }
}
#else
using sock_t = int;
constexpr sock_t kNoSock = -1;
static int sock_error() {
  return errno;
}
static bool would_block(int e) {
  return e == EWOULDBLOCK || e == EAGAIN || e == EINPROGRESS;
}
static void close_sock(sock_t s) {
  close(s);
}
static void set_nonblocking(sock_t s) {
  fcntl(s, F_SETFL, fcntl(s, F_GETFL, 0) | O_NONBLOCK);
}
#ifdef MSG_NOSIGNAL
constexpr int kSendFlags = MSG_NOSIGNAL;
#else
constexpr int kSendFlags = 0;  // macOS: SO_NOSIGPIPE is set on each socket instead
#endif
static void shutdown_send(sock_t s) {
  shutdown(s, SHUT_WR);
}
static void net_init() {}
#endif

static void set_nodelay(sock_t s) {
  int on = 1;
  setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&on, sizeof(on));
#ifdef SO_NOSIGPIPE
  setsockopt(s, SOL_SOCKET, SO_NOSIGPIPE, (const char*)&on, sizeof(on));
#endif
}

// ---------------------------------------------------------------------------------------------
// Wire format: every frame is [u16 length][u8 type][payload], length = 1 + payload size.
// ---------------------------------------------------------------------------------------------

enum Msg : u8 {
  MSG_HELLO = 1,    // client -> host: u32 protocol version
  MSG_WELCOME = 2,  // host -> client: u8 slot given to the client
  MSG_REJECT = 3,   // host -> client: reason text (refused, kicked, banned, server closed)
  MSG_STATE = 4,    // both ways: u8 slot + PlayerState
  MSG_LEAVE = 5,    // host -> client: u8 slot of a player who left
  MSG_EVENT = 6,    // both ways: Event (the host fills `from`)
  MSG_ACTORS = 7,   // both ways: u8 source slot, u32 source time ms, u16 count, count x ActorState
  MSG_POSES = 8,    // both ways: u8 source slot, u32 source time ms, u16 count, count x entry:
                    // u32 id, u16 joints, u8 emitters, u8 sounds, PoseJoint..., PoseEmitter...,
                    // PoseSound...
};

constexpr size_t kMaxFrame = 16384;            // larger frames are a protocol violation
constexpr size_t kMaxOutBuffer = 1024 * 1024;  // a peer this far behind is dropped
constexpr double kTimeoutMs = 15000.0;         // silence before a peer is considered gone
constexpr double kConnectTimeoutMs = 8000.0;
constexpr size_t kMaxEvents = 256;

struct Session::Peer {
  sock_t fd = kNoSock;
  std::string ip;
  std::vector<u8> in, out;
  int slot = -1;          // host side: -1 until the HELLO is accepted
  bool connected = true;  // client side: false until the non-blocking connect completes
  double last_recv = 0;
  bool dead = false;
  std::string dead_reason;
  // Refused or kicked: kept open a moment, its input drained, so the reason is not lost to a reset.
  bool closing = false;
  double close_at = 0;

  void queue(u8 type, const void* a, size_t na, const void* b = nullptr, size_t nb = 0) {
    size_t len = 1 + na + nb;
    out.push_back(len & 0xff);
    out.push_back((len >> 8) & 0xff);
    out.push_back(type);
    out.insert(out.end(), (const u8*)a, (const u8*)a + na);
    if (b) {
      out.insert(out.end(), (const u8*)b, (const u8*)b + nb);
    }
  }

  // Sends what the socket accepts now; the rest stays queued for the next frame.
  void flush() {
    while (!dead && !out.empty()) {
      int n = send(fd, (const char*)out.data(), (int)out.size(), kSendFlags);
      if (n > 0) {
        out.erase(out.begin(), out.begin() + n);
      } else if (n < 0 && would_block(sock_error())) {
        break;
      } else {
        dead = true;
        dead_reason = "Connection lost";
      }
    }
    if (out.size() > kMaxOutBuffer) {
      dead = true;
      dead_reason = "Connection too slow";
    }
  }

  // Reads everything available. Returns false when the connection is closed.
  bool receive(double now_ms) {
    u8 buf[16384];
    while (true) {
      int n = recv(fd, (char*)buf, sizeof(buf), 0);
      if (n > 0) {
        in.insert(in.end(), buf, buf + n);
        last_recv = now_ms;
      } else if (n < 0 && would_block(sock_error())) {
        return true;
      } else {
        return false;
      }
    }
  }
};

static void copy_cstr(char* dst, size_t size, const std::string& src) {
  size_t n = std::min(size - 1, src.size());
  memcpy(dst, src.data(), n);
  memset(dst + n, 0, size - n);
}

static double now_ms() {
  static const auto start = std::chrono::steady_clock::now();
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start)
      .count();
}

bool parse_address(const std::string& address, std::string* ip, int* port) {
  auto colon = address.rfind(':');
  *ip = address.substr(0, colon);
  *port = kDefaultPort;
  if (colon != std::string::npos) {
    try {
      *port = std::stoi(address.substr(colon + 1));
    } catch (...) {
      return false;
    }
  }
  return *port > 0 && *port < 65536;
}

// ---------------------------------------------------------------------------------------------
// Interpolation
// ---------------------------------------------------------------------------------------------

void Remote::push(PlayerState s, double now) {
  // The sender clock is unrelated to ours; the smallest observed (receive - send) difference is
  // the fastest delivery. It may rise slowly so a lasting latency increase is followed.
  double offset = now - (double)s.time_ms;
  clock_offset = snaps.empty() ? offset : std::min(offset, clock_offset + 1.0);
  s.name[sizeof(s.name) - 1] = 0;
  s.level[sizeof(s.level) - 1] = 0;
  s.cont[sizeof(s.cont) - 1] = 0;
  if (!snaps.empty() && s.time_ms < snaps.back().time_ms) {
    snaps.clear();  // the player reconnected with a new clock
  }
  snaps.push_back(s);
  while (snaps.size() > 32) {
    snaps.pop_front();
  }
}

static void lerp4(float* out, const float* a, const float* b, float f) {
  for (int i = 0; i < 4; i++) {
    out[i] = a[i] + (b[i] - a[i]) * f;
  }
}

static void nlerp_quat(float* out, const float* a, const float* b, float f) {
  float dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  float sign = dot < 0 ? -1.f : 1.f;  // take the short way around
  float len = 0;
  for (int i = 0; i < 4; i++) {
    out[i] = a[i] + (sign * b[i] - a[i]) * f;
    len += out[i] * out[i];
  }
  len = std::sqrt(len);
  if (len > 1e-6f) {
    for (int i = 0; i < 4; i++) {
      out[i] /= len;
    }
  } else {
    memcpy(out, b, 16);
  }
}

static float dist(const float* a, const float* b) {
  float dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
  return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// Matrices are GOAL's: 16 floats, row vectors (v' = v * M), rows 0-2 the axes, row 3 the
// translation. out = a * b applies a, then b.
static void mat_mul(float* out, const float* a, const float* b) {
  float r[16];
  for (int i = 0; i < 4; i++) {
    for (int j = 0; j < 4; j++) {
      r[i * 4 + j] = a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] +
                     a[i * 4 + 3] * b[12 + j];
    }
  }
  memcpy(out, r, sizeof(r));
}

// Inverse of a rigid matrix (rotation and translation only).
static void rigid_inverse(float* out, const float* m) {
  float r[16] = {};
  for (int i = 0; i < 3; i++) {
    for (int j = 0; j < 3; j++) {
      r[i * 4 + j] = m[j * 4 + i];
    }
  }
  for (int j = 0; j < 3; j++) {
    r[12 + j] = -(m[12] * r[j] + m[13] * r[4 + j] + m[14] * r[8 + j]);
  }
  r[15] = 1.f;
  memcpy(out, r, sizeof(r));
}

// Quaternion (x y z w) of the orthonormal rotation in rows 0-2 of m, and back. The two functions
// only need to be each other's inverse.
static void mat_to_quat(float* q, const float* m) {
  float tr = m[0] + m[5] + m[10];
  if (tr > 0) {
    float s = std::sqrt(tr + 1.f) * 2.f;
    q[3] = 0.25f * s;
    q[0] = (m[6] - m[9]) / s;
    q[1] = (m[8] - m[2]) / s;
    q[2] = (m[1] - m[4]) / s;
  } else if (m[0] > m[5] && m[0] > m[10]) {
    float s = std::sqrt(1.f + m[0] - m[5] - m[10]) * 2.f;
    q[3] = (m[6] - m[9]) / s;
    q[0] = 0.25f * s;
    q[1] = (m[1] + m[4]) / s;
    q[2] = (m[8] + m[2]) / s;
  } else if (m[5] > m[10]) {
    float s = std::sqrt(1.f + m[5] - m[0] - m[10]) * 2.f;
    q[3] = (m[8] - m[2]) / s;
    q[0] = (m[1] + m[4]) / s;
    q[1] = 0.25f * s;
    q[2] = (m[6] + m[9]) / s;
  } else {
    float s = std::sqrt(1.f + m[10] - m[0] - m[5]) * 2.f;
    q[3] = (m[1] - m[4]) / s;
    q[0] = (m[8] + m[2]) / s;
    q[1] = (m[6] + m[9]) / s;
    q[2] = 0.25f * s;
  }
}

static void quat_to_mat(float* m, const float* q) {
  float x = q[0], y = q[1], z = q[2], w = q[3];
  m[0] = 1 - 2 * (y * y + z * z);
  m[1] = 2 * (x * y + z * w);
  m[2] = 2 * (x * z - y * w);
  m[4] = 2 * (x * y - z * w);
  m[5] = 1 - 2 * (x * x + z * z);
  m[6] = 2 * (y * z + x * w);
  m[8] = 2 * (x * z + y * w);
  m[9] = 2 * (y * z - x * w);
  m[10] = 1 - 2 * (x * x + y * y);
  m[3] = m[7] = m[11] = 0.f;
}

static s16 to_s16(float v) {
  return (s16)std::clamp<long>(std::lround(v), -32767, 32767);
}

// Rotation, translation and per-axis scale of an affine matrix.
static void decompose(const float* m, float* q, float* t, float* sc) {
  float rot[16] = {};
  for (int i = 0; i < 3; i++) {
    const float* row = m + i * 4;
    sc[i] = std::sqrt(row[0] * row[0] + row[1] * row[1] + row[2] * row[2]);
    for (int j = 0; j < 3; j++) {
      rot[i * 4 + j] = sc[i] > 1e-6f ? row[j] / sc[i] : (i == j ? 1.f : 0.f);
    }
    t[i] = m[12 + i];
  }
  // a mirrored joint: flip the last axis so the rest is a rotation
  float det = rot[0] * (rot[5] * rot[10] - rot[6] * rot[9]) -
              rot[1] * (rot[4] * rot[10] - rot[6] * rot[8]) +
              rot[2] * (rot[4] * rot[9] - rot[5] * rot[8]);
  if (det < 0) {
    sc[2] = -sc[2];
    for (int j = 0; j < 3; j++) {
      rot[8 + j] = -rot[8 + j];
    }
  }
  mat_to_quat(q, rot);
}

static void compose(float* m, const float* q, const float* t, const float* sc) {
  quat_to_mat(m, q);
  for (int i = 0; i < 3; i++) {
    for (int j = 0; j < 3; j++) {
      m[i * 4 + j] *= sc[i];
    }
    m[12 + i] = t[i];
  }
  m[15] = 1.f;
}

static void encode_quat(s16* out, const float* q) {
  for (int i = 0; i < 4; i++) {
    out[i] = to_s16(q[i] * 32767.f);
  }
}

static void decode_quat(float* q, const s16* in) {
  float len = 0;
  for (int i = 0; i < 4; i++) {
    q[i] = in[i] / 32767.f;
    len += q[i] * q[i];
  }
  len = std::sqrt(len);
  for (int i = 0; i < 4; i++) {
    q[i] = len > 1e-6f ? q[i] / len : (i == 3 ? 1.f : 0.f);
  }
}

static void encode_joint(PoseJoint* j, const float* rel) {
  float q[4], t[3], sc[3];
  decompose(rel, q, t, sc);
  encode_quat(j->q, q);
  for (int i = 0; i < 3; i++) {
    j->t[i] = to_s16(t[i] / 4.f);
    j->s[i] = to_s16(sc[i] * 2048.f);
  }
}

// Bone `a` blended toward `b` by f, as a matrix relative to the object's frame.
static void decode_joint(float* rel, const PoseJoint& a, const PoseJoint& b, float f) {
  float qa[4], qb[4], q[4], t[3], sc[3];
  decode_quat(qa, a.q);
  decode_quat(qb, b.q);
  nlerp_quat(q, qa, qb, f);
  for (int i = 0; i < 3; i++) {
    t[i] = (a.t[i] + (b.t[i] - a.t[i]) * f) * 4.f;
    sc[i] = (a.s[i] + (b.s[i] - a.s[i]) * f) / 2048.f;
  }
  compose(rel, q, t, sc);
}

bool Remote::sample(double now, PlayerState* out) const {
  if (snaps.empty()) {
    return false;
  }
  double t = now - clock_offset - kInterpDelayMs;  // drawn time, in the sender's clock
  const PlayerState& newest = snaps.back();
  if (t >= newest.time_ms) {
    // Late packets: keep moving along the last velocity for at most 250 ms, then hold.
    *out = newest;
    float dt = (float)std::min(t - newest.time_ms, 250.0) / 1000.f;
    for (int i = 0; i < 3; i++) {
      out->trans[i] += newest.vel[i] * dt;
    }
    return true;
  }
  if (t <= snaps.front().time_ms) {
    *out = snaps.front();
    return true;
  }
  size_t i = 1;
  while (snaps[i].time_ms <= t) {
    i++;
  }
  const PlayerState& a = snaps[i - 1];
  const PlayerState& b = snaps[i];
  float f = (float)((t - a.time_ms) / std::max(1.0, (double)b.time_ms - a.time_ms));
  *out = f < 0.5f ? a : b;  // discrete fields (animation, flags, names) from the nearest
  // 10 m in one step is a teleport: snap instead of sliding across the map.
  if (dist(a.trans, b.trans) < 40960.f) {
    lerp4(out->trans, a.trans, b.trans, f);
    lerp4(out->vel, a.vel, b.vel, f);
    nlerp_quat(out->quat, a.quat, b.quat, f);
  }
  if (a.veh_type == b.veh_type && a.veh_type >= 0 && dist(a.veh_trans, b.veh_trans) < 40960.f) {
    lerp4(out->veh_trans, a.veh_trans, b.veh_trans, f);
    lerp4(out->seat_trans, a.seat_trans, b.seat_trans, f);
    nlerp_quat(out->veh_quat, a.veh_quat, b.veh_quat, f);
  }
  if (a.anim_index == b.anim_index && b.anim_frame >= a.anim_frame) {
    out->anim_frame = a.anim_frame + (b.anim_frame - a.anim_frame) * f;
  }
  return true;
}

// ---------------------------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------------------------

Session::~Session() {
  leave("");
}

bool Session::host(const std::string& address, int max_players) {
  leave("");
  net_init();
  std::string ip;
  int port;
  if (!parse_address(address, &ip, &port)) {
    m_message = "Invalid address, expected ip:port";
    return false;
  }
  sock_t s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (s == kNoSock) {
    m_message = "Cannot create a socket";
    return false;
  }
#ifdef _WIN32
  int on = 1;
  setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&on, sizeof(on));
#else
  int on = 1;
  setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char*)&on, sizeof(on));
#endif
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons((u16)port);
  if (bind(s, (sockaddr*)&addr, sizeof(addr)) != 0 || listen(s, 8) != 0) {
    close_sock(s);
    m_message = "Port " + std::to_string(port) + " is already in use";
    return false;
  }
  set_nonblocking(s);
  m_listen = (u64)s;
  m_mode = Mode::Host;
  m_local_slot = 0;
  m_max_players = std::clamp(max_players, 2, kMaxPlayers);
  m_message = "Hosting on port " + std::to_string(port);
  lg::info("[multiplayer] {}", m_message);
  return true;
}

bool Session::join(const std::string& address, double now) {
  leave("");
  net_init();
  std::string ip;
  int port;
  if (!parse_address(address, &ip, &port) || ip.empty()) {
    m_message = "Invalid address, expected ip:port";
    return false;
  }
  addrinfo hints{};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo* res = nullptr;
  if (getaddrinfo(ip.c_str(), std::to_string(port).c_str(), &hints, &res) != 0 || !res) {
    m_message = "Unknown address " + ip;
    return false;
  }
  sock_t s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (s == kNoSock) {
    freeaddrinfo(res);
    m_message = "Cannot create a socket";
    return false;
  }
  set_nonblocking(s);
  set_nodelay(s);
  int r = connect(s, res->ai_addr, (int)res->ai_addrlen);
  freeaddrinfo(res);
  if (r != 0 && !would_block(sock_error())) {
    close_sock(s);
    m_message = "Cannot reach " + address;
    return false;
  }
  auto* p = new Peer();
  p->fd = s;
  p->ip = ip;
  p->last_recv = now;
  p->connected = false;
  m_peers.push_back(p);
  m_mode = Mode::Connecting;
  m_connect_start = now;
  m_message = "Connecting to " + address;
  lg::info("[multiplayer] {}", m_message);
  return true;
}

void Session::leave(const std::string& reason) {
  for (auto* p : m_peers) {
    if (m_mode == Mode::Host && p->slot >= 0) {
      const char* bye = "The host stopped the server";
      p->queue(MSG_REJECT, bye, strlen(bye));
      p->flush();
    }
    close_sock(p->fd);
    delete p;
  }
  m_peers.clear();
  if (m_listen != ~0ull) {
    close_sock((sock_t)m_listen);
    m_listen = ~0ull;
  }
  for (auto& r : m_remotes) {
    r.clear();
  }
  m_actors.clear();
  m_actor_offset_known.fill(false);
  m_events.clear();
  m_banned.clear();
  if (m_mode != Mode::Offline && !reason.empty()) {
    lg::info("[multiplayer] {}", reason);
  }
  m_mode = Mode::Offline;
  m_local_slot = -1;
  if (!reason.empty()) {
    m_message = reason;
  }
}

void Session::refuse(Peer& p, const char* why) {
  p.queue(MSG_REJECT, why, strlen(why));
  p.flush();
  if (p.out.empty()) {
    shutdown_send(p.fd);  // the client reads the reason, then the end of the stream
  }
  p.closing = true;
  p.close_at = m_now + 1000.0;
  drop(p, why);
}

void Session::drop(Peer& p, const std::string& reason) {
  if (!p.dead) {
    p.dead = true;
    p.dead_reason = reason;
  }
}

void Session::route_event(Event ev, int sender) {
  ev.from = sender;
  ev.text[sizeof(ev.text) - 1] = 0;
  if (m_mode == Mode::Client) {
    if (ev.to == -1 || ev.to == m_local_slot) {
      if (m_events.size() < kMaxEvents) {
        m_events.push_back(ev);
      }
    }
    return;
  }
  // Host: deliver to itself and/or forward.
  if ((ev.to == -1 || ev.to == 0) && sender != 0 && m_events.size() < kMaxEvents) {
    m_events.push_back(ev);
  }
  for (auto* p : m_peers) {
    if (p->slot >= 0 && p->slot != sender && (ev.to == -1 || ev.to == p->slot)) {
      p->queue(MSG_EVENT, &ev, sizeof(ev));
    }
  }
}

void Session::handle_frame(Peer& p, u8 type, const u8* data, size_t len) {
  if (m_mode == Mode::Host) {
    switch (type) {
      case MSG_HELLO: {
        if (p.slot >= 0 || len != 4) {
          return;
        }
        u32 version;
        memcpy(&version, data, 4);
        const char* reason = nullptr;
        int slot = -1;
        for (int i = 1; i < m_max_players && slot < 0; i++) {
          bool used =
              std::any_of(m_peers.begin(), m_peers.end(), [&](Peer* o) { return o->slot == i; });
          if (!used) {
            slot = i;
          }
        }
        if (version != kProtocolVersion) {
          reason = "Version mismatch: update the mod";
        } else if (slot < 0) {
          reason = "The server is full";
        }
        if (reason) {
          refuse(p, reason);
          return;
        }
        p.slot = slot;
        m_remotes[slot].clear();
        u8 s = (u8)slot;
        p.queue(MSG_WELCOME, &s, 1);
        lg::info("[multiplayer] {} joined as player {}", p.ip, slot);
        return;
      }
      case MSG_STATE: {
        if (p.slot < 0 || len != 1 + sizeof(PlayerState)) {
          return;
        }
        PlayerState st;
        memcpy(&st, data + 1, sizeof(st));
        m_remotes[p.slot].push(st, p.last_recv);
        u8 s = (u8)p.slot;
        for (auto* o : m_peers) {
          if (o != &p && o->slot >= 0) {
            o->queue(MSG_STATE, &s, 1, &st, sizeof(st));
          }
        }
        return;
      }
      case MSG_ACTORS: {
        if (p.slot < 0 || len < 7) {
          return;
        }
        // a guest's own objects: keep them, and pass them on with the guest as the source
        std::vector<u8> copy(data, data + len);
        copy[0] = (u8)p.slot;
        store_actors(p.slot, copy.data(), copy.size(), p.last_recv);
        for (auto* o : m_peers) {
          if (o != &p && o->slot >= 0) {
            o->queue(MSG_ACTORS, copy.data(), copy.size());
          }
        }
        return;
      }
      case MSG_POSES: {
        if (p.slot < 0 || len < 7) {
          return;
        }
        std::vector<u8> copy(data, data + len);
        copy[0] = (u8)p.slot;
        store_poses(p.slot, copy.data(), copy.size(), p.last_recv);
        for (auto* o : m_peers) {
          if (o != &p && o->slot >= 0) {
            o->queue(MSG_POSES, copy.data(), copy.size());
          }
        }
        return;
      }
      case MSG_EVENT: {
        if (p.slot < 0 || len != sizeof(Event)) {
          return;
        }
        Event ev;
        memcpy(&ev, data, sizeof(ev));
        route_event(ev, p.slot);
        return;
      }
      default:
        drop(p, "Protocol error");
        return;
    }
  }

  // Client side: frames from the host.
  switch (type) {
    case MSG_WELCOME:
      if (len == 1 && data[0] < kMaxPlayers) {
        m_local_slot = data[0];
        m_mode = Mode::Client;
        m_message = "Connected as player " + std::to_string(m_local_slot);
        lg::info("[multiplayer] {}", m_message);
      }
      return;
    case MSG_REJECT:
      drop(p, std::string((const char*)data, std::min<size_t>(len, 64)));
      return;
    case MSG_STATE:
      if (len == 1 + sizeof(PlayerState) && data[0] < kMaxPlayers && data[0] != m_local_slot) {
        PlayerState st;
        memcpy(&st, data + 1, sizeof(st));
        m_remotes[data[0]].push(st, p.last_recv);
      }
      return;
    case MSG_ACTORS:
      if (len >= 7 && data[0] < kMaxPlayers && data[0] != m_local_slot) {
        store_actors(data[0], data, len, p.last_recv);
      }
      return;
    case MSG_POSES:
      if (len >= 7 && data[0] < kMaxPlayers && data[0] != m_local_slot) {
        store_poses(data[0], data, len, p.last_recv);
      }
      return;
    case MSG_LEAVE:
      if (len == 1 && data[0] < kMaxPlayers) {
        m_remotes[data[0]].clear();
      }
      return;
    case MSG_EVENT:
      if (len == sizeof(Event)) {
        Event ev;
        memcpy(&ev, data, sizeof(ev));
        route_event(ev, ev.from);
      }
      return;
    default:
      drop(p, "Protocol error");
      return;
  }
}

void Session::update(PlayerState local, double now) {
  m_now = now;
  if (m_mode == Mode::Offline) {
    return;
  }

  // Host: accept new players.
  if (m_mode == Mode::Host) {
    while (true) {
      sockaddr_in addr{};
#ifdef _WIN32
      int addr_len = sizeof(addr);
#else
      socklen_t addr_len = sizeof(addr);
#endif
      sock_t s = accept((sock_t)m_listen, (sockaddr*)&addr, &addr_len);
      if (s == kNoSock) {
        break;
      }
      set_nonblocking(s);
      set_nodelay(s);
      char ip[INET_ADDRSTRLEN] = {};
      inet_ntop(AF_INET, &addr.sin_addr, ip, sizeof(ip));
      auto* p = new Peer();
      p->fd = s;
      p->ip = ip;
      p->last_recv = now;
      if (m_banned.count(p->ip)) {
        refuse(*p, "You are banned from this server");
      }
      m_peers.push_back(p);
    }
  }

  // Client: wait for the connection to complete.
  if (m_mode == Mode::Connecting && m_local_slot < 0 && !m_peers.empty()) {
    Peer& p = *m_peers[0];
    if (!p.connected) {
      fd_set wr, ex;
      FD_ZERO(&wr);
      FD_ZERO(&ex);
      FD_SET(p.fd, &wr);
      FD_SET(p.fd, &ex);
      timeval tv{0, 0};
      if (select((int)p.fd + 1, nullptr, &wr, &ex, &tv) > 0) {
        int err = 0;
#ifdef _WIN32
        int err_len = sizeof(err);
#else
        socklen_t err_len = sizeof(err);
#endif
        getsockopt(p.fd, SOL_SOCKET, SO_ERROR, (char*)&err, &err_len);
        if (err != 0 || FD_ISSET(p.fd, &ex)) {
          leave("Connection refused: is the server running?");
          return;
        }
        p.connected = true;
        p.queue(MSG_HELLO, &kProtocolVersion, 4);
        p.last_recv = now;
      }
    }
    if (now - m_connect_start > kConnectTimeoutMs) {
      leave("Connection timed out");
      return;
    }
  }

  // Read and dispatch.
  for (auto* p : m_peers) {
    if (p->dead && p->closing) {
      p->in.clear();
      if (!p->receive(now)) {
        p->close_at = 0;  // the client closed: no need to wait
      }
      p->in.clear();
      continue;
    }
    if (p->dead || !p->connected) {
      continue;
    }
    // Frames received before a close still count (a kick reason arrives just before the close).
    bool open = p->receive(now);
    size_t at = 0;
    while (!p->dead && p->in.size() - at >= 3) {
      size_t len = p->in[at] | (p->in[at + 1] << 8);
      if (len < 1 || len > kMaxFrame) {
        drop(*p, "Protocol error");
        break;
      }
      if (p->in.size() - at < 2 + len) {
        break;
      }
      handle_frame(*p, p->in[at + 2], p->in.data() + at + 3, len - 1);
      at += 2 + len;
    }
    p->in.erase(p->in.begin(), p->in.begin() + std::min(at, p->in.size()));
    if (!open) {
      drop(*p, "Connection closed");
    }
    if (now - p->last_recv > kTimeoutMs) {
      drop(*p, "Connection timed out");
    }
  }

  // Send the local state.
  if (m_local_slot >= 0 && now - m_last_state_sent >= kStateIntervalMs) {
    m_last_state_sent = now;
    local.time_ms = (u32)now;
    local.name[sizeof(local.name) - 1] = 0;
    u8 s = (u8)m_local_slot;
    for (auto* p : m_peers) {
      if (p->slot >= 0 || m_mode == Mode::Client) {
        p->queue(MSG_STATE, &s, 1, &local, sizeof(local));
      }
    }
  }

  for (auto* p : m_peers) {
    if (p->connected) {
      p->flush();
    }
  }

  // Remove dead connections.
  for (size_t i = 0; i < m_peers.size();) {
    Peer* p = m_peers[i];
    if (!p->dead) {
      i++;
      continue;
    }
    if (m_mode != Mode::Host) {
      std::string why = p->dead_reason;  // leave() deletes p
      leave(why);                        // lost the host: the session is over
      return;
    }
    if (p->slot >= 0) {
      lg::info("[multiplayer] player {} left: {}", p->slot, p->dead_reason);
      m_remotes[p->slot].clear();
      u8 s = (u8)p->slot;
      p->slot = -1;  // free the slot now, even if the socket lingers
      for (auto* o : m_peers) {
        if (o != p && o->slot >= 0) {
          o->queue(MSG_LEAVE, &s, 1);
        }
      }
    }
    if (p->closing && now < p->close_at) {
      i++;
      continue;
    }
    close_sock(p->fd);
    delete p;
    m_peers.erase(m_peers.begin() + i);
  }
}

bool Session::get_player(int slot, double now, PlayerState* out) const {
  if (slot < 0 || slot >= kMaxPlayers || slot == m_local_slot || m_mode == Mode::Offline) {
    return false;
  }
  return m_remotes[slot].sample(now, out);
}

void Session::send_event(Event ev) {
  if (m_mode == Mode::Host) {
    route_event(ev, 0);
  } else if (m_mode == Mode::Client) {
    ev.from = m_local_slot;
    m_peers[0]->queue(MSG_EVENT, &ev, sizeof(ev));
  }
}

void Session::store_actors(int source, const u8* data, size_t len, double recv) {
  u32 time;
  u16 count;
  memcpy(&time, data + 1, 4);
  memcpy(&count, data + 5, 2);
  if (len != 7 + count * sizeof(ActorState)) {
    return;
  }
  double sent = time / kBatchTicksPerMs;
  note_actor_clock(source, sent, recv);
  for (u16 i = 0; i < count; i++) {
    ActorState st;
    memcpy(&st, data + 7 + i * sizeof(ActorState), sizeof(st));
    st.skel[sizeof(st.skel) - 1] = 0;
    // ids carry their owner in the top byte: never trust another player's claim to ours
    st.id = ((u32)source << 24) | (st.id & 0xffffff);
    auto& track = m_actors[st.id];
    track.source = source;
    if (!track.snaps.empty() && sent < track.snaps.back().first) {
      track.snaps.clear();
    }
    track.snaps.emplace_back(sent, st);
    while (track.snaps.size() > 4) {
      track.snaps.pop_front();
    }
    track.last_seen = recv;
  }
}

void Session::send_actors(const ActorState* actors, int count, double now) {
  if (m_local_slot < 0 || count < 0 || (m_mode != Mode::Host && m_mode != Mode::Client)) {
    return;
  }
  u16 n = (u16)std::min(count, kMaxActors);
  std::vector<u8> payload(7 + n * sizeof(ActorState));
  u32 time = (u32)(now * kBatchTicksPerMs);
  payload[0] = (u8)m_local_slot;
  memcpy(payload.data() + 1, &time, 4);
  memcpy(payload.data() + 5, &n, 2);
  memcpy(payload.data() + 7, actors, n * sizeof(ActorState));
  for (auto* p : m_peers) {
    if (p->slot >= 0 || m_mode == Mode::Client) {
      p->queue(MSG_ACTORS, payload.data(), payload.size());
    }
  }
}

int Session::get_actors(double now, ActorState* out, int max) {
  int n = 0;
  for (auto it = m_actors.begin(); it != m_actors.end();) {
    auto& track = it->second;
    auto& snaps = track.snaps;
    if (now - track.last_seen > 1000.0 || snaps.empty()) {
      it = m_actors.erase(it);  // its owner stopped sending it: gone, out of range or left
      continue;
    }
    if (n < max) {
      // drawn at the same time as its owner's player state, so props stay on the puppet
      double t = drawn_time(track.source, now);
      if (t >= snaps.back().first) {
        out[n] = snaps.back().second;
      } else if (t <= snaps.front().first) {
        out[n] = snaps.front().second;
      } else {
        size_t i = 1;
        while (snaps[i].first <= t) {
          i++;
        }
        const auto& [ta, a] = snaps[i - 1];
        const auto& [tb, b] = snaps[i];
        float f = (float)((t - ta) / std::max(1.0, tb - ta));
        out[n] = f < 0.5f ? a : b;
        if (dist(a.trans, b.trans) < 40960.f) {
          lerp4(out[n].trans, a.trans, b.trans, f);
          nlerp_quat(out[n].quat, a.quat, b.quat, f);
        }
        if (a.anim_index == b.anim_index && b.anim_frame >= a.anim_frame) {
          out[n].anim_frame = a.anim_frame + (b.anim_frame - a.anim_frame) * f;
        }
      }
      n++;
    }
    ++it;
  }
  return n;
}

void Session::note_actor_clock(int source, double sent, double recv) {
  // same estimate as Remote::push, for the batches of one sender
  double offset = recv - sent;
  auto& known = m_actor_offset_known[source];
  auto& est = m_actor_offset[source];
  est = !known ? offset : std::min(offset, est + 1.0);
  known = true;
}

double Session::drawn_time(int source, double now) const {
  return now - m_actor_offset[source] - kInterpDelayMs;
}

void Session::add_pose(u32 id, const float* frame, const float* bones, int count, bool effects) {
  count = std::clamp(count, 0, kMaxPoseJoints);
  float inv[16];
  rigid_inverse(inv, frame);
  memcpy(m_pose_frame, frame, sizeof(m_pose_frame));
  std::vector<u8> entry(8 + count * sizeof(PoseJoint));
  u16 joints = (u16)count | (effects ? kPoseHasEffects : 0);
  memcpy(entry.data(), &id, 4);
  memcpy(entry.data() + 4, &joints, 2);
  for (int i = 0; i < count; i++) {
    float rel[16];
    mat_mul(rel, bones + 16 * i, inv);
    PoseJoint j;
    encode_joint(&j, rel);
    memcpy(entry.data() + 8 + i * sizeof(PoseJoint), &j, sizeof(j));
  }
  m_pose_out.push_back(std::move(entry));
}

void Session::add_emitter(int kind, int index, int count, const float* origin) {
  if (m_pose_out.empty() || !(m_pose_out.back()[5] & (kPoseHasEffects >> 8)) ||
      m_pose_out.back()[6] >= kMaxPoseEmitters || index < 0 || index > 0xffff) {
    return;
  }
  auto& entry = m_pose_out.back();
  float inv[16], rel[16], q[4], t[3], sc[3];
  rigid_inverse(inv, m_pose_frame);
  mat_mul(rel, origin, inv);
  decompose(rel, q, t, sc);
  PoseEmitter em{};
  em.index = (u16)index;
  em.kind = (u8)(kind & 0xff);
  em.home = (u16)((kind >> 8) & 0xffff);
  em.count = (u8)std::clamp(count, 0, 255);
  for (int i = 0; i < 3; i++) {
    em.t[i] = to_s16(t[i] / 4.f);
  }
  encode_quat(em.q, q);
  const u8* raw = (const u8*)&em;
  entry.insert(entry.end(), raw, raw + sizeof(em));
  entry[6]++;
}

void Session::add_sound(const SoundIO& snd) {
  if (m_pose_out.empty() || !(m_pose_out.back()[5] & (kPoseHasEffects >> 8)) ||
      m_pose_out.back()[7] >= kMaxPoseSounds) {
    return;
  }
  auto& entry = m_pose_out.back();
  PoseSound ps{};
  memcpy(ps.name, snd.name, sizeof(ps.name));
  ps.id = snd.id;
  ps.vol = snd.vol;
  ps.pitch = snd.pitch;
  ps.bend = snd.bend;
  ps.flags = (u16)snd.flags;
  float inv[16],
      at[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, snd.pos[0], snd.pos[1], snd.pos[2], 1};
  rigid_inverse(inv, m_pose_frame);
  mat_mul(at, at, inv);
  for (int i = 0; i < 3; i++) {
    ps.t[i] = to_s16(at[12 + i] / 4.f);
  }
  const u8* raw = (const u8*)&ps;
  entry.insert(entry.end(), raw, raw + sizeof(ps));
  entry[7]++;
}

void Session::send_poses(double now) {
  auto entries = std::move(m_pose_out);
  m_pose_out.clear();
  if (m_local_slot < 0 || (m_mode != Mode::Host && m_mode != Mode::Client)) {
    return;
  }
  u32 time = (u32)(now * kBatchTicksPerMs);
  size_t at = 0;
  while (at < entries.size()) {
    // as many entries as fit in one frame (one entry is at most ~5 KB)
    std::vector<u8> payload(7);
    payload[0] = (u8)m_local_slot;
    memcpy(payload.data() + 1, &time, 4);
    u16 n = 0;
    while (at < entries.size() && payload.size() + entries[at].size() < kMaxFrame - 16) {
      payload.insert(payload.end(), entries[at].begin(), entries[at].end());
      n++;
      at++;
    }
    if (n == 0) {
      at++;  // cannot happen with kMaxPoseJoints, but never loop forever
      continue;
    }
    memcpy(payload.data() + 5, &n, 2);
    for (auto* p : m_peers) {
      if (p->slot >= 0 || m_mode == Mode::Client) {
        p->queue(MSG_POSES, payload.data(), payload.size());
      }
    }
  }
}

void Session::store_poses(int source, const u8* data, size_t len, double recv) {
  u32 time;
  u16 count;
  memcpy(&time, data + 1, 4);
  memcpy(&count, data + 5, 2);
  double sent = time / kBatchTicksPerMs;
  note_actor_clock(source, sent, recv);
  size_t at = 7;
  for (u16 i = 0; i < count; i++) {
    if (at + 8 > len) {
      return;
    }
    u32 id;
    u16 raw;
    memcpy(&id, data + at, 4);
    memcpy(&raw, data + at + 4, 2);
    u16 joints = raw & ~kPoseHasEffects;
    bool effects = raw & kPoseHasEffects;
    u8 emitters = data[at + 6];
    u8 sounds = data[at + 7];
    size_t size = 8 + joints * sizeof(PoseJoint) + emitters * sizeof(PoseEmitter) +
                  sounds * sizeof(PoseSound);
    if (joints > kMaxPoseJoints || at + size > len) {
      return;
    }
    std::vector<PoseJoint> bones(joints);
    PoseSnap snap;
    snap.emitters.resize(emitters);
    snap.sounds.resize(sounds);
    const u8* body = data + at + 8;
    memcpy(bones.data(), body, joints * sizeof(PoseJoint));
    body += joints * sizeof(PoseJoint);
    memcpy(snap.emitters.data(), body, emitters * sizeof(PoseEmitter));
    body += emitters * sizeof(PoseEmitter);
    memcpy(snap.sounds.data(), body, sounds * sizeof(PoseSound));
    at += size;
    id = ((u32)source << 24) | (id & 0xffffff);  // the owner is the sender, whatever it claims
    auto& track = m_poses[id];
    track.source = source;
    if (joints > 0) {
      if (!track.joints.empty() && sent < track.joints.back().first) {
        track.joints.clear();
      }
      track.joints.emplace_back(sent, std::move(bones));
      while (track.joints.size() > 16) {  // 100 ms back at 60 entries a second, and some slack
        track.joints.pop_front();
      }
    }
    if (effects) {
      if (!track.snaps.empty() && sent < track.snaps.back().first) {
        track.snaps.clear();
        track.consumed = -1;
        track.consumed_sounds = -1;
      }
      track.snaps.emplace_back(sent, std::move(snap));
      while (track.snaps.size() > 4) {
        track.snaps.pop_front();
      }
    }
    track.last_seen = recv;
  }
  for (auto it = m_poses.begin(); it != m_poses.end();) {
    it = recv - it->second.last_seen > 1000.0 ? m_poses.erase(it) : std::next(it);
  }
}

int Session::get_pose(u32 id, double now, const float* frame, float* out, int max) {
  auto it = m_poses.find(id);
  if (it == m_poses.end() || now - it->second.last_seen > 1000.0 || it->second.joints.empty()) {
    return 0;
  }
  const auto& snaps = it->second.joints;
  double t = drawn_time(it->second.source, now);
  const std::vector<PoseJoint>* a = &snaps.front().second;
  const std::vector<PoseJoint>* b = a;
  float f = 0;
  if (t >= snaps.back().first) {
    a = b = &snaps.back().second;
  } else if (t > snaps.front().first) {
    size_t i = 1;
    while (snaps[i].first <= t) {
      i++;
    }
    a = &snaps[i - 1].second;
    b = &snaps[i].second;
    f = (float)((t - snaps[i - 1].first) / std::max(1.0, snaps[i].first - snaps[i - 1].first));
  }
  if (a->size() != b->size()) {
    a = b = f < 0.5f ? a : b;  // the skeleton changed: no blending across it
    f = 0;
  }
  int n = std::min((int)a->size(), max);
  for (int i = 0; i < n; i++) {
    float rel[16];
    decode_joint(rel, (*a)[i], (*b)[i], f);
    mat_mul(out + 16 * i, rel, frame);
  }
  return n;
}

int Session::get_emitters(u32 id, double now, const float* frame, EmitterOut* out, int max) {
  auto it = m_poses.find(id);
  if (it == m_poses.end() || now - it->second.last_seen > 1000.0) {
    return 0;
  }
  auto& track = it->second;
  double t = drawn_time(track.source, now);
  // the newest batch at or before the drawn time
  const std::pair<double, PoseSnap>* cur = nullptr;
  for (const auto& snap : track.snaps) {
    if (snap.first <= t) {
      cur = &snap;
    }
  }
  if (!cur) {
    return 0;
  }
  bool fresh = cur->first > track.consumed;
  track.consumed = cur->first;
  int n = 0;
  for (const auto& em : cur->second.emitters) {
    if (n >= max) {
      break;
    }
    float q[4], rel[16];
    float tr[3] = {em.t[0] * 4.f, em.t[1] * 4.f, em.t[2] * 4.f};
    float one[3] = {1.f, 1.f, 1.f};
    decode_quat(q, em.q);
    compose(rel, q, tr, one);
    EmitterOut& o = out[n++];
    o.kind = em.kind;
    o.index = em.index;
    o.count = fresh ? em.count : 0;
    o.home = em.home;
    mat_mul(o.mat, rel, frame);
  }
  return n;
}

int Session::get_sounds(u32 id, double now, const float* frame, SoundIO* out, int max) {
  auto it = m_poses.find(id);
  if (it == m_poses.end() || now - it->second.last_seen > 1000.0) {
    return 0;
  }
  auto& track = it->second;
  double t = drawn_time(track.source, now);
  int n = 0;
  // every batch reached since the last call, oldest first: a sound is an event, none is skipped
  for (const auto& [time, snap] : track.snaps) {
    if (time <= track.consumed_sounds || time > t) {
      continue;
    }
    for (const auto& ps : snap.sounds) {
      if (n >= max) {
        break;
      }
      SoundIO& o = out[n++];
      memset(&o, 0, sizeof(o));
      memcpy(o.name, ps.name, sizeof(o.name));
      o.id = ps.id;
      o.vol = ps.vol;
      o.pitch = ps.pitch;
      o.bend = ps.bend;
      o.flags = ps.flags;
      float at[16] = {
          1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, ps.t[0] * 4.f, ps.t[1] * 4.f, ps.t[2] * 4.f, 1};
      mat_mul(at, at, frame);
      o.pos[0] = at[12];
      o.pos[1] = at[13];
      o.pos[2] = at[14];
      o.pos[3] = 1.f;
    }
    track.consumed_sounds = time;
  }
  return n;
}

bool Session::pop_event(Event* out) {
  if (m_events.empty()) {
    return false;
  }
  *out = m_events.front();
  m_events.pop_front();
  return true;
}

void Session::kick(int slot, bool ban) {
  if (m_mode != Mode::Host) {
    return;
  }
  for (auto* p : m_peers) {
    if (p->slot == slot && slot > 0) {
      const char* why = ban ? "You were banned by the host" : "You were kicked by the host";
      if (ban) {
        m_banned.insert(p->ip);
      }
      refuse(*p, why);
    }
  }
}

Session& session() {
  static Session s;
  return s;
}

TestPad g_test_pad;

// ---------------------------------------------------------------------------------------------
// Test scripts: MP_TEST_SCRIPT names a file of "<seconds> <command...>" lines. GOAL polls the
// next due command every frame (multiplayer-test.gc) and runs it; the runner is
// scripts/mp-test/run_mp_test.py.
// ---------------------------------------------------------------------------------------------

namespace test {
struct Step {
  double at_ms;
  std::string command;
};

struct Script {
  std::vector<Step> steps;
  size_t next = 0;
  double start_ms = -1;
  bool loaded = false;

  Script() {
    const char* path = getenv("MP_TEST_SCRIPT");
    if (!path || !fs::exists(path)) {
      return;
    }
    for (auto& line : str_util::split_string(file_util::read_text_file(std::string(path)))) {
      auto trimmed = str_util::trim(line);
      if (trimmed.empty() || trimmed[0] == '#') {
        continue;
      }
      auto space = trimmed.find(' ');
      try {
        steps.push_back({std::stod(trimmed.substr(0, space)) * 1000.0,
                         space == std::string::npos ? "" : str_util::trim(trimmed.substr(space))});
      } catch (...) {
        lg::warn("[mp-test] ignored line: {}", line);
      }
    }
    loaded = true;
    lg::info("[mp-test] script {} with {} steps", path, steps.size());
  }
};

Script& script() {
  static Script s;
  return s;
}
}  // namespace test

}  // namespace mp

// ---------------------------------------------------------------------------------------------
// Kernel functions (GOAL declarations in goal_src/jak3/pc/features/multiplayer/multiplayer.gc)
// ---------------------------------------------------------------------------------------------

namespace jak3 {
namespace {

u64 to_symbol(bool val) {
  return val ? static_cast<u64>(s7.offset) + true_symbol_offset(g_game_version) : s7.offset;
}

std::string goal_string(u32 ptr) {
  return ptr ? std::string(Ptr<String>(ptr).c()->data()) : std::string();
}

fs::path config_path() {
  return file_util::get_user_settings_dir(g_game_version) / "multiplayer.cfg";
}

// (pc-mp-host address max-players) -> #t when the server is listening.
u64 pc_mp_host(u32 address, u32 max_players) {
  return to_symbol(mp::session().host(goal_string(address), (int)max_players));
}

// (pc-mp-join address) -> #t when the connection attempt started.
u64 pc_mp_join(u32 address) {
  return to_symbol(mp::session().join(goal_string(address), mp::now_ms()));
}

// (pc-mp-leave) closes the session.
void pc_mp_leave() {
  mp::session().leave("Disconnected");
}

// (pc-mp-update local-state) pumps the network once and returns the mode (mp-mode).
u64 pc_mp_update(u32 state) {
  auto& s = mp::session();
  if (state) {
    s.update(*Ptr<mp::PlayerState>(state).c(), mp::now_ms());
  }
  return (u64)(s64)s.mode();
}

// (pc-mp-get-player slot dest) fills dest with the interpolated state of a remote player.
u64 pc_mp_get_player(u32 slot, u32 dest) {
  mp::PlayerState st;
  if (!dest || !mp::session().get_player((int)slot, mp::now_ms(), &st)) {
    return to_symbol(false);
  }
  *Ptr<mp::PlayerState>(dest).c() = st;
  return to_symbol(true);
}

// (pc-mp-send-event event)
void pc_mp_send_event(u32 ev) {
  if (ev) {
    mp::session().send_event(*Ptr<mp::Event>(ev).c());
  }
}

// (pc-mp-pop-event dest) -> #t when an event was copied into dest.
u64 pc_mp_pop_event(u32 dest) {
  mp::Event ev;
  if (!dest || !mp::session().pop_event(&ev)) {
    return to_symbol(false);
  }
  *Ptr<mp::Event>(dest).c() = ev;
  return to_symbol(true);
}

// (pc-mp-kick slot ban?) host only.
void pc_mp_kick(u32 slot, u32 ban) {
  mp::session().kick((int)slot, ban != s7.offset);
}

// (pc-mp-local-slot) -> slot of this player, -1 when not connected.
u64 pc_mp_local_slot() {
  return (u64)(s64)mp::session().local_slot();
}

// (pc-mp-message dest) copies the last status message into a GOAL string.
void pc_mp_message(u32 dest) {
  if (dest) {
    auto* str = Ptr<String>(dest).c();
    const auto& msg = mp::session().message();
    size_t n = std::min<size_t>(msg.size(), str->len > 0 ? str->len - 1 : 0);
    memcpy(str->data(), msg.data(), n);
    str->data()[n] = 0;
  }
}

// (pc-mp-config-load dest) -> #t when a saved config was read into dest.
u64 pc_mp_config_load(u32 dest) {
  if (!dest || !fs::exists(config_path())) {
    return to_symbol(false);
  }
  auto* cfg = Ptr<mp::Config>(dest).c();
  for (auto& line : str_util::split_string(file_util::read_text_file(config_path()))) {
    auto eq = line.find('=');
    if (eq == std::string::npos) {
      continue;
    }
    auto key = line.substr(0, eq);
    auto val = line.substr(eq + 1);
    try {
      if (key == "name") {
        mp::copy_cstr(cfg->name, sizeof(cfg->name), val);
      } else if (key == "address") {
        mp::copy_cstr(cfg->address, sizeof(cfg->address), val);
      } else if (key == "character") {
        cfg->character = std::stoi(val);
      } else if (key == "color") {
        cfg->color = std::stoi(val);
      } else if (key == "enabled") {
        cfg->enabled = std::stoi(val);
      }
    } catch (...) {
      lg::warn("[multiplayer] ignored bad line in {}: {}", config_path().string(), line);
    }
  }
  return to_symbol(true);
}

// (pc-mp-config-save config)
void pc_mp_config_save(u32 src) {
  if (!src) {
    return;
  }
  auto* cfg = Ptr<mp::Config>(src).c();
  auto name = std::string(cfg->name, strnlen(cfg->name, sizeof(cfg->name)));
  auto address = std::string(cfg->address, strnlen(cfg->address, sizeof(cfg->address)));
  file_util::create_dir_if_needed_for_file(config_path());
  file_util::write_text_file(
      config_path(), fmt::format("name={}\naddress={}\ncharacter={}\ncolor={}\nenabled={}\n", name,
                                 address, cfg->character, cfg->color, cfg->enabled));
}

// (pc-mp-send-actors states count): send the `count` mp-actor-state this player simulates.
void pc_mp_send_actors(u32 states, u32 count) {
  if (states) {
    mp::session().send_actors(Ptr<mp::ActorState>(states).c(), (int)count, mp::now_ms());
  }
}

// (pc-mp-get-actors dest max): the other players' objects, interpolated. Returns the count.
u64 pc_mp_get_actors(u32 dest, u32 max) {
  if (!dest) {
    return 0;
  }
  return (u64)mp::session().get_actors(mp::now_ms(), Ptr<mp::ActorState>(dest).c(),
                                       std::min<int>((int)max, mp::kMaxActors * 2));
}

// (pc-mp-add-pose id frame bones count): queue the pose of one object this player simulates;
// bit 15 of count (mp::kPoseHasEffects): the entry carries its effects (add-emitter, add-sound).
void pc_mp_add_pose(u32 id, u32 frame, u32 bones, u32 count) {
  int joints = (int)(count & 0x7fff);
  if (frame && (bones || joints == 0)) {
    mp::session().add_pose(id, Ptr<float>(frame).c(), bones ? Ptr<float>(bones).c() : nullptr,
                           joints, (count & mp::kPoseHasEffects) != 0);
  }
}

// (pc-mp-add-emitter kind index count origin): an emitter of the object of the last add-pose.
void pc_mp_add_emitter(u32 kind, u32 index, u32 count, u32 origin) {
  if (origin) {
    mp::session().add_emitter((int)kind, (int)index, (int)count, Ptr<float>(origin).c());
  }
}

// (pc-mp-add-sound sound): a sound of the object of the last add-pose (mp-sound, world position).
void pc_mp_add_sound(u32 snd) {
  if (snd) {
    mp::session().add_sound(*Ptr<mp::SoundIO>(snd).c());
  }
}

// (pc-mp-get-sounds id frame dest max) -> sounds of object `id` to play now, written into dest.
u64 pc_mp_get_sounds(u32 id, u32 frame, u32 dest, u32 max) {
  if (!frame || !dest) {
    return 0;
  }
  return (u64)mp::session().get_sounds(id, mp::now_ms(), Ptr<float>(frame).c(),
                                       Ptr<mp::SoundIO>(dest).c(),
                                       std::min<int>((int)max, mp::kMaxPoseSounds * 4));
}

// (pc-mp-send-poses): send the queued poses.
void pc_mp_send_poses() {
  mp::session().send_poses(mp::now_ms());
}

// (pc-mp-get-pose id frame dest max) -> bones written into dest (world matrices), 0 when none.
u64 pc_mp_get_pose(u32 id, u32 frame, u32 dest, u32 max) {
  if (!frame || !dest) {
    return 0;
  }
  return (u64)mp::session().get_pose(id, mp::now_ms(), Ptr<float>(frame).c(), Ptr<float>(dest).c(),
                                     std::min<int>((int)max, mp::kMaxPoseJoints));
}

// (pc-mp-get-emitters id frame dest max) -> emitters written into dest (mp-emitter).
u64 pc_mp_get_emitters(u32 id, u32 frame, u32 dest, u32 max) {
  if (!frame || !dest) {
    return 0;
  }
  return (u64)mp::session().get_emitters(id, mp::now_ms(), Ptr<float>(frame).c(),
                                         Ptr<mp::EmitterOut>(dest).c(),
                                         std::min<int>((int)max, mp::kMaxPoseEmitters));
}

// (pc-mp-clock slot) -> tenths of a ms: this machine's clock for -1, else the time player `slot`'s
// objects are drawn at now, in that player's clock (evidence lines of the test logs are stamped
// with it).
u64 pc_mp_clock(u32 slot) {
  int source = (s32)slot;
  if (source >= mp::kMaxPlayers) {
    source = -1;
  }
  return (u64)(s64)(mp::session().clock_of(source, mp::now_ms()) * mp::kBatchTicksPerMs);
}

// ---------------------------------------------------------------------------------------------
// Level code sandbox
// ---------------------------------------------------------------------------------------------
// A level loaded for a distant player links its code into the running game: its defun, define
// and deftype of names our own levels already use point those names at its copy, and a deftype of
// an existing type resets that type's methods to its parent's (new_type in kscheme.cpp). The
// sandbox notes every symbol, the methods of every type and some global tables before that level
// loads, and puts back each entry that held a value and now points into the level's memory (or,
// for methods, changed at all: only that level links code meanwhile). Names only that level
// defines stay: its art and particle groups are what copies need from it.

struct SandboxType {
  u32 sym;   // symbol of the type
  u32 type;  // type object when noted
  std::vector<u32> methods;
};

struct Sandbox {
  bool active = false;
  std::vector<std::pair<u32, u32>> words;  // address, value: symbol values and table entries
  std::vector<SandboxType> types;
};

Sandbox& sandbox() {
  static Sandbox s;
  return s;
}

bool is_type_object(u32 v) {
  return v >= 0x84000 && v < (u32)EE_MAIN_MEM_SIZE && (v & 7) == 4 &&
         *Ptr<u32>(v - 4).c() == u32_in_fixed_sym(jak3_symbols::FIX_SYM_TYPE_TYPE);
}

// Symbol values live 1 byte below the (odd) symbol address (Symbol4).
u32& symbol_value(u32 sym) {
  return Ptr<Symbol4<u32>>(sym)->value();
}

// (pc-mp-sandbox-begin) notes every symbol value and the methods of every type.
void pc_mp_sandbox_begin() {
  auto& sb = sandbox();
  sb.words.clear();
  sb.types.clear();
  sb.active = true;
  auto note = [&](u32 sym) {
    u32 v = symbol_value(sym);
    sb.words.emplace_back(sym - 1, v);
    if (is_type_object(v)) {
      auto type = Ptr<Type>(v);
      SandboxType t{sym, v, std::vector<u32>(type->num_methods)};
      for (u32 i = 0; i < type->num_methods; i++) {
        t.methods[i] = type->get_method(i).offset;
      }
      sb.types.push_back(std::move(t));
    }
  };
  for (u32 sym = s7.offset; sym < LastSymbol.offset; sym += 4) {
    note(sym);
  }
  for (u32 sym = SymbolTable2.offset; sym < s7.offset; sym += 4) {
    note(sym);
  }
}

// (pc-mp-sandbox-watch addr count stride) notes `count` words, `stride` bytes apart (a table).
void pc_mp_sandbox_watch(u32 addr, u32 count, u32 stride) {
  auto& sb = sandbox();
  if (!sb.active || addr < 0x84000 || stride < 4) {
    return;
  }
  for (u32 i = 0; i < count; i++) {
    u32 at = addr + i * stride;
    if (at + 4 > (u32)EE_MAIN_MEM_SIZE) {
      break;
    }
    sb.words.emplace_back(at, *Ptr<u32>(at).c());
  }
}

// (pc-mp-sandbox-restore lo hi) -> entries put back: a noted word that held a value (not 0, not
// #f) and now points into [lo, hi), and any changed method of a noted type.
u64 pc_mp_sandbox_restore(u32 lo, u32 hi) {
  auto& sb = sandbox();
  if (!sb.active) {
    return 0;
  }
  u64 n = 0;
  for (auto& [at, old] : sb.words) {
    u32& cur = *Ptr<u32>(at).c();
    if (cur != old && cur >= lo && cur < hi && old != 0 && old != s7.offset) {
      cur = old;
      n++;
    }
  }
  for (auto& t : sb.types) {
    if (symbol_value(t.sym) != t.type) {
      continue;
    }
    auto type = Ptr<Type>(t.type);
    u32 count = std::min<u32>(type->num_methods, (u32)t.methods.size());
    for (u32 i = 0; i < count; i++) {
      if (type->get_method(i).offset != t.methods[i]) {
        type->get_method(i).offset = t.methods[i];
        n++;
      }
    }
  }
  return n;
}

// (pc-mp-sandbox-end) forgets the notes.
void pc_mp_sandbox_end() {
  auto& sb = sandbox();
  sb.active = false;
  sb.words = {};
  sb.types = {};
}

// (pc-mp-test-active?) -> #t when a test script drives this instance.
u64 pc_mp_test_active() {
  return to_symbol(mp::test::script().loaded);
}

// (pc-mp-test-poll dest) -> #t with the next due script command copied into dest.
u64 pc_mp_test_poll(u32 dest) {
  auto& s = mp::test::script();
  if (!s.loaded || !dest || s.next >= s.steps.size()) {
    return to_symbol(false);
  }
  double now = mp::now_ms();
  if (s.start_ms < 0) {
    s.start_ms = now;
  }
  if (now - s.start_ms < s.steps[s.next].at_ms) {
    return to_symbol(false);
  }
  auto* str = Ptr<String>(dest).c();
  const auto& cmd = s.steps[s.next++].command;
  size_t n = std::min<size_t>(cmd.size(), str->len > 0 ? str->len - 1 : 0);
  memcpy(str->data(), cmd.data(), n);
  str->data()[n] = 0;
  return to_symbol(true);
}

// (pc-mp-test-screenshot name) saves the next frame as <config>/screenshots/<name>.png.
void pc_mp_test_screenshot(u32 name) {
  auto label = goal_string(name);
  g_screen_shot_settings->width = 1280;
  g_screen_shot_settings->height = 720;
  g_screen_shot_settings->msaa = 2;
  mp::copy_cstr(g_screen_shot_settings->name, sizeof(g_screen_shot_settings->name), label);
  g_want_screenshot = true;
  lg::info("[mp-test] screenshot {}", label);
}

// (pc-mp-test-pad buttons lx ly) forces pad 0 input until called with 0 128 128.
void pc_mp_test_pad(u32 buttons, u32 lx, u32 ly) {
  auto& pad = mp::g_test_pad;
  pad.buttons = buttons & 0xffff;
  pad.lx = (u8)std::min<u32>(lx, 255);
  pad.ly = (u8)std::min<u32>(ly, 255);
  pad.active = pad.buttons != 0 || pad.lx != 128 || pad.ly != 128;
}

}  // namespace

void init_multiplayer_functions() {
  make_function_symbol_from_c("pc-mp-send-actors", (void*)pc_mp_send_actors);
  make_function_symbol_from_c("pc-mp-get-actors", (void*)pc_mp_get_actors);
  make_function_symbol_from_c("pc-mp-add-pose", (void*)pc_mp_add_pose);
  make_function_symbol_from_c("pc-mp-add-emitter", (void*)pc_mp_add_emitter);
  make_function_symbol_from_c("pc-mp-send-poses", (void*)pc_mp_send_poses);
  make_function_symbol_from_c("pc-mp-add-sound", (void*)pc_mp_add_sound);
  make_function_symbol_from_c("pc-mp-get-sounds", (void*)pc_mp_get_sounds);
  make_function_symbol_from_c("pc-mp-get-pose", (void*)pc_mp_get_pose);
  make_function_symbol_from_c("pc-mp-get-emitters", (void*)pc_mp_get_emitters);
  make_function_symbol_from_c("pc-mp-clock", (void*)pc_mp_clock);
  make_function_symbol_from_c("pc-mp-sandbox-begin", (void*)pc_mp_sandbox_begin);
  make_function_symbol_from_c("pc-mp-sandbox-watch", (void*)pc_mp_sandbox_watch);
  make_function_symbol_from_c("pc-mp-sandbox-restore", (void*)pc_mp_sandbox_restore);
  make_function_symbol_from_c("pc-mp-sandbox-end", (void*)pc_mp_sandbox_end);
  make_function_symbol_from_c("pc-mp-test-active?", (void*)pc_mp_test_active);
  make_function_symbol_from_c("pc-mp-test-poll", (void*)pc_mp_test_poll);
  make_function_symbol_from_c("pc-mp-test-screenshot", (void*)pc_mp_test_screenshot);
  make_function_symbol_from_c("pc-mp-test-pad", (void*)pc_mp_test_pad);
  make_function_symbol_from_c("pc-mp-host", (void*)pc_mp_host);
  make_function_symbol_from_c("pc-mp-join", (void*)pc_mp_join);
  make_function_symbol_from_c("pc-mp-leave", (void*)pc_mp_leave);
  make_function_symbol_from_c("pc-mp-update", (void*)pc_mp_update);
  make_function_symbol_from_c("pc-mp-get-player", (void*)pc_mp_get_player);
  make_function_symbol_from_c("pc-mp-send-event", (void*)pc_mp_send_event);
  make_function_symbol_from_c("pc-mp-pop-event", (void*)pc_mp_pop_event);
  make_function_symbol_from_c("pc-mp-kick", (void*)pc_mp_kick);
  make_function_symbol_from_c("pc-mp-local-slot", (void*)pc_mp_local_slot);
  make_function_symbol_from_c("pc-mp-message", (void*)pc_mp_message);
  make_function_symbol_from_c("pc-mp-config-load", (void*)pc_mp_config_load);
  make_function_symbol_from_c("pc-mp-config-save", (void*)pc_mp_config_save);
}

}  // namespace jak3
