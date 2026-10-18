// Self-checks for the Jak 3 multiplayer session (game/kernel/jak3/kmultiplayer.cpp):
// interpolation between snapshots, and a host and a client talking over loopback.

#include <chrono>
#include <cmath>
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

  // A pose and an emitter come back laid on the receiver's frame: same bones relative to it.
  auto mul = [](float* out, const float* a, const float* b) {
    for (int i = 0; i < 4; i++) {
      for (int j = 0; j < 4; j++) {
        out[i * 4 + j] = 0;
        for (int k = 0; k < 4; k++) {
          out[i * 4 + j] += a[i * 4 + k] * b[k * 4 + j];
        }
      }
    }
  };
  const float c = std::cos(0.5f), s = std::sin(0.5f);
  const float frame[16] = {c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 1000, 200, 300, 1};
  const float rel[16] = {1.5f, 0,         0,        0, 0,    1.5f * c, 1.5f * s, 0,
                         0,    -1.5f * s, 1.5f * c, 0, 8000, -400,     1200,     1};
  const float identity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
  float bones[32];
  memcpy(bones, frame, sizeof(frame));
  mul(bones + 16, rel, frame);
  client.add_pose(9, frame, bones, 2, true);
  client.add_emitter(1, 1234, 3, bones + 16);
  mp::SoundIO snd{};
  memcpy(snd.name, "engine", 7);
  snd.id = 77;
  snd.vol = 1024;
  snd.flags = mp::kSoundPositional;
  snd.pos[0] = bones[28];  // at bone 1
  snd.pos[1] = bones[29];
  snd.pos[2] = bones[30];
  client.add_sound(snd);
  client.send_poses(now());
  pump(10);
  const u32 pose_id = (1u << 24) | 9u;
  float out[32];
  ASSERT_EQ(host.get_pose(pose_id, now(), identity, out, 8), 2);
  for (int i = 0; i < 16; i++) {
    EXPECT_NEAR(out[i], identity[i], 1e-3f) << i;
    EXPECT_NEAR(out[16 + i], rel[i], i >= 12 ? 4.f : 2e-3f) << i;
  }
  mp::EmitterOut em[2];
  ASSERT_EQ(host.get_emitters(pose_id, now(), identity, em, 2), 1);
  EXPECT_EQ(em[0].kind, 1);
  EXPECT_EQ(em[0].index, 1234);
  EXPECT_EQ(em[0].count, 3);
  EXPECT_NEAR(em[0].mat[12], 8000.f, 4.f);
  ASSERT_EQ(host.get_emitters(pose_id, now(), identity, em, 2), 1);
  EXPECT_EQ(em[0].count, 0);  // single launches are handed out once
  mp::SoundIO sounds[2];
  ASSERT_EQ(host.get_sounds(pose_id, now(), identity, sounds, 2), 1);
  EXPECT_STREQ((const char*)sounds[0].name, "engine");
  EXPECT_EQ(sounds[0].id, 77u);
  EXPECT_NEAR(sounds[0].pos[0], 8000.f, 4.f);
  EXPECT_NEAR(sounds[0].pos[2], 1200.f, 4.f);
  EXPECT_EQ(host.get_sounds(pose_id, now(), identity, sounds, 2), 0);  // played once
  // A pose sent between batches (bones only) keeps the effects of the last batch.
  client.add_pose(9, frame, bones, 2, false);
  client.send_poses(now());
  pump(10);
  ASSERT_EQ(host.get_pose(pose_id, now(), identity, out, 8), 2);
  ASSERT_EQ(host.get_emitters(pose_id, now(), identity, em, 2), 1);
  EXPECT_EQ(em[0].index, 1234);

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

// UDP: with a fifth of the datagrams lost, the join, a world save (blob) and every event still
// arrive, each event once and in order.
TEST(Multiplayer, ReliableChannelUnderLoss) {
  using namespace std::chrono;
  auto start = steady_clock::now();
  auto now = [&] { return duration<double, std::milli>(steady_clock::now() - start).count(); };
  mp::set_test_network(0, 0, 20);

  mp::Session host, client;
  ASSERT_TRUE(host.host("0.0.0.0:47778", 4));
  ASSERT_TRUE(client.join("127.0.0.1:47778", now()));
  mp::PlayerState hs{}, cs{};
  auto pump = [&](int frames) {
    for (int i = 0; i < frames; i++) {
      host.update(hs, now());
      client.update(cs, now());
      std::this_thread::sleep_for(milliseconds(5));
    }
  };
  pump(200);
  ASSERT_EQ(client.mode(), mp::Mode::Client);

  std::vector<u8> blob(200000);
  for (size_t i = 0; i < blob.size(); i++) {
    blob[i] = (u8)(i * 7 + i / 251);
  }
  host.send_blob(1, blob.data(), blob.size());
  for (int i = 0; i < 200; i++) {
    mp::Event ev{};
    ev.kind = 1000 + i;
    ev.to = -1;
    host.send_event(ev);
  }
  for (int i = 0; i < 400 && client.blob_ready() == 0; i++) {
    pump(1);
  }
  pump(100);
  ASSERT_EQ(client.blob_ready(), blob.size());
  std::vector<u8> got(blob.size());
  ASSERT_EQ(client.take_blob(got.data(), got.size()), blob.size());
  EXPECT_TRUE(got == blob);
  mp::Event ev;
  for (int i = 0; i < 200; i++) {
    ASSERT_TRUE(client.pop_event(&ev)) << i;
    EXPECT_EQ(ev.kind, 1000 + i);
  }
  EXPECT_FALSE(client.pop_event(&ev));

  mp::set_test_network(0, 0, 0);
  host.leave("");
  pump(5);
  EXPECT_EQ(client.mode(), mp::Mode::Offline);
  EXPECT_EQ(client.message(), "The host stopped the server");
}
