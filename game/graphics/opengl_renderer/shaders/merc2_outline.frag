#version 410 core

// MOD multiplayer: the color of another player's outline (Merc2::draw_outlines).

out vec4 color;
uniform vec4 outline_color;

void main() {
  color = outline_color;
}
