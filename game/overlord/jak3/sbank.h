#pragma once

#include "common/common_types.h"

#include "game/sound/sndshim.h"

namespace jak3 {

struct SoundBankInfo {
  // int m_name1[4];
  char m_name1[16];
  char m_name2[16];
  int m_nSpuMemLoc = 0;
  int m_nSpuMemSize = 0;
  // s32 snd_handle = 0;
  snd::BankHandle snd_handle = nullptr;
  u8 in_use = 0;
  u8 loaded = 0;
  u8 mode = 0;
  u8 idx = 0;
  int unk0 = 0;
};

// MOD multiplayer: the stock 8 banks, then mode banks kept loaded while online
// (mp::keep_mode_banks, game/kernel/jak3/kmultiplayer.h)
constexpr int kNumStockBanks = 8;
constexpr int kNumBanks = kNumStockBanks + 24;

void jak3_overlord_init_globals_sbank();
void InitBanks();
SoundBankInfo* LookupBank(const char* name);
SoundBankInfo* AllocateBankName(const char* name, u32 mode);
void PrintBanks();
extern SoundBankInfo* gBanks[kNumBanks];
}  // namespace jak3