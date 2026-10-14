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
};

constexpr size_t kMaxFrame = 1024;             // larger frames are a protocol violation
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
