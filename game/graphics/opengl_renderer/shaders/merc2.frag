#version 410 core

out vec4 color;
in vec4 vtx_color;
in vec2 vtx_st;
in float fog;
in vec3 vtx_material_color;
in vec3 vtx_light_color;

uniform sampler2D tex_T0;

uniform vec4 fog_color;
uniform int ignore_alpha;
uniform vec4 light_dir0_fade;
uniform vec4 light_dir1_fade_en;

uniform int decal_enable;

uniform int gfx_hack_no_tex;

// MOD multiplayer: a player's outfit, one color per group of textures (Merc2: pc-merc-bits skin),
// rgb the color, a its strength (0: the stock texture). The tint keeps the texture's detail as a
// grayscale under the chosen color: player_tint_grayscale_detail of himstan's Jak and Daxter II
// Multiplayer (https://github.com/himstan/Jak-And-Daxter-2-Multiplayer,
// game/graphics/opengl_renderer/shaders/merc2.frag, ISC license).
uniform vec4 skin_tint;

const vec3 SKIN_LUMINANCE_WEIGHTS = vec3(0.2126, 0.7152, 0.0722);
const float SKIN_MATERIAL_NORMALIZATION = 0.5;
const float SKIN_DETAIL_CONTRAST = 0.65;
const float SKIN_DARK_DETAIL_MIX = 0.18;

vec3 skin_grayscale_detail(vec3 source_material, vec3 target_color) {
  float source_luminance = clamp(dot(source_material, SKIN_LUMINANCE_WEIGHTS) * SKIN_MATERIAL_NORMALIZATION, 0.0, 1.0);
  float target_luminance = dot(target_color, SKIN_LUMINANCE_WEIGHTS);
  float detail_luminance = mix(1.0, source_luminance, SKIN_DETAIL_CONTRAST);
  float neutral_detail_mix = SKIN_DARK_DETAIL_MIX * (1.0 - target_luminance);
  return mix(target_color, vec3(1.0), neutral_detail_mix) * detail_luminance;
}

void main() {
  if (gfx_hack_no_tex == 0) {
    vec4 T0 = texture(tex_T0, vtx_st);
    // all merc is tcc=rgba and modulate
    if (decal_enable == 0) {
      color = vtx_color * T0 * 2;
    } else {
      color = T0;
    }
    if (skin_tint.a > 0.0) {
      vec3 source_material = T0.rgb;
      vec3 scene_light = vec3(1.0);
      if (decal_enable == 0) {
        source_material = vtx_material_color * T0.rgb * 2.0;
        scene_light = vtx_light_color;
      }
      color.rgb = mix(color.rgb, skin_grayscale_detail(source_material, skin_tint.rgb) * scene_light,
                      clamp(skin_tint.a, 0.0, 1.0));
    }
    color.a *= 2;
  } else {
    color.rgb = vtx_color.rgb;

    if (decal_enable == 0) {
      color.a = vtx_color.a * 2;
    } else {
      color.a = 1;
    }
  }

  if (light_dir1_fade_en.w > 0) {
    color.a = light_dir0_fade.w;
  } else if (light_dir1_fade_en.w < 0) {
    color.a *= light_dir0_fade.w;
  }


  if (ignore_alpha == 0 && color.w < 0.128) {
    discard;
  }

   color.xyz = mix(color.xyz, fog_color.rgb, clamp(fog_color.a * fog, 0, 1));
}
