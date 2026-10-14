// Self-checks for the Jak 3 multiplayer session (game/kernel/jak3/kmultiplayer.cpp):
// interpolation between snapshots, and a host and a client talking over loopback.

#include <chrono>
#include <cstring>
#include <thread>

#include "game/kernel/jak3/kmultiplayer.h"
#include "gtest/gtest.h"

TEST(Multiplayer, InterpolatesBetweenSnapshots) {
  mp::Remote r;
  mp::PlayerState a{}, b{};
  a.time_ms = 1000;
  b.time_ms = 1100;
  b.trans[0] = 1000.f;
  a.quat[3] = b.quat[3] = 1.f;
  r.push(a, 5000);  // delivered 4000 ms after it was sent (sender clock is unrelated)
  r.push(b, 5100);
  mp::PlayerState out;
  // Drawn time = now - 4000 - 100 ms delay = 1050: halfway between a and b.
  ASSERT_TRUE(r.sample(5150, &out));
  EXPECT_NEAR(out.trans[0], 500.f, 1.f);
  // A teleport (more than 10 m) snaps instead of sliding.
  mp::PlayerState c = b;
  c.time_ms = 1200;
  c.trans[0] = 1000.f + 4096.f * 50;
  r.push(c, 5200);
  ASSERT_TRUE(r.sample(5225, &out));  // drawn time 1125, a quarter of the way to c
  EXPECT_EQ(out.trans[0], 1000.f);
}

TEST(Multiplayer, HostAndClientOverLoopback) {
  using namespace std::chrono;
  auto start = steady_clock::now();
  auto now = [&] { return duration<double, std::milli>(steady_clock::now() - start).count(); };

  mp::Session host, client;
  ASSERT_TRUE(host.host("0.0.0.0:47777", 4));
  ASSERT_TRUE(client.join("127.0.0.1:47777", now()));

  mp::PlayerState hs{}, cs{};
  strcpy(hs.name, "Host");
  strcpy(cs.name, "Guest");
  auto pump = [&](int frames) {
    for (int i = 0; i < frames; i++) {
      host.update(hs, now());
      client.update(cs, now());
      std::this_thread::sleep_for(milliseconds(10));
    }
  };
  pump(50);
  ASSERT_EQ(client.mode(), mp::Mode::Client);
  EXPECT_EQ(client.local_slot(), 1);

  mp::PlayerState seen;
  ASSERT_TRUE(host.get_player(1, now(), &seen));
  EXPECT_STREQ(seen.name, "Guest");
  ASSERT_TRUE(client.get_player(0, now(), &seen));
  EXPECT_STREQ(seen.name, "Host");

  // An event from the client to everyone reaches the host, stamped with the sender slot.
  mp::Event ev{};
  ev.kind = 42;
  ev.to = -1;
  client.send_event(ev);
  pump(10);
  mp::Event got;
  ASSERT_TRUE(host.pop_event(&got));
  EXPECT_EQ(got.kind, 42);
  EXPECT_EQ(got.from, 1);

  // Objects go both ways, their id prefixed with the owner's slot.
  mp::ActorState npc{}, prop{};
  npc.id = 5;
  npc.quat[3] = 1.f;
  strcpy(npc.skel, "skel-citizen-norm");
  prop.id = 7;
  prop.quat[3] = 1.f;
  host.send_actors(&npc, 1, now());
  client.send_actors(&prop, 1, now());
  pump(10);
  mp::ActorState got_actors[4];
  ASSERT_EQ(client.get_actors(now(), got_actors, 4), 1);
  EXPECT_EQ(got_actors[0].id, 5u);
  EXPECT_STREQ(got_actors[0].skel, "skel-citizen-norm");
  ASSERT_EQ(host.get_actors(now(), got_actors, 4), 1);
  EXPECT_EQ(got_actors[0].id, (1u << 24) | 7u);

  // A banned client is disconnected with a reason and cannot come back.
  host.kick(1, true);
  pump(10);
  EXPECT_EQ(client.mode(), mp::Mode::Offline);
  EXPECT_EQ(client.message(), "You were banned by the host");
  ASSERT_TRUE(client.join("127.0.0.1:47777", now()));
  pump(20);
  EXPECT_EQ(client.mode(), mp::Mode::Offline);
  EXPECT_EQ(client.message(), "You are banned from this server");
  host.leave("");
}
