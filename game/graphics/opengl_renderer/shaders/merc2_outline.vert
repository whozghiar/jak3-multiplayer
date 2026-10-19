#version 410 core

// MOD multiplayer: a merc model drawn in one color, shifted on screen by screen_offset (NDC), for
// the outline of another player seen through walls (Merc2::draw_outlines). Same skinning and
// projection as merc2.vert.

layout (location = 0) in vec3 position_in;
layout (location = 1) in vec3 normal_in;
layout (location = 2) in vec3 weights_in;
layout (location = 3) in vec2 st_in;
layout (location = 4) in vec4 rgba;
layout (location = 5) in uvec3 mats;

uniform vec4 hvdf_offset;
uniform vec4 fog_constants;
uniform mat4 perspective_matrix;
uniform vec2 screen_offset;

struct MercMatrixData {
  mat4 X;
  mat3 R;
  vec4 pad;
};

layout (std140) uniform ub_bones {
  MercMatrixData bones[128];
};

void main() {
  vec4 p = vec4(position_in, 1);
  vec4 vtx_pos = -bones[mats[0]].X * p * weights_in[0];
  if (weights_in[1] > 0) {
    vtx_pos += -bones[mats[1]].X * p * weights_in[1];
  }
  if (weights_in[2] > 0) {
    vtx_pos += -bones[mats[2]].X * p * weights_in[2];
  }

  vec4 transformed = perspective_matrix * vtx_pos;
  float Q = fog_constants.x / transformed[3];
  transformed.xyz *= Q;
  transformed.xyz += hvdf_offset.xyz;
  transformed.xy -= (2048.);
  transformed.z /= (8388608);
  transformed.z -= 1;
  transformed.x /= (256);
  transformed.y /= -(128);
  transformed.xyz *= transformed.w;
  transformed.y *= SCISSOR_ADJUST * HEIGHT_SCALE;
  transformed.xy += screen_offset * transformed.w;
  gl_Position = transformed;
}
