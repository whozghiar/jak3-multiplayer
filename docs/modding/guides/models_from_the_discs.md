# Models made from the player's discs

This guide explains how a mod uses models taken from a game (Jak 2's Krimzon Guard in Jak 3, for
example) without keeping them in its repository: a manifest describes each model, and a tool makes
the `.glb` files from the player's own discs, in development and at a launcher install.

## Why

A model ripped from a game is the game's content. A mod repository keeps only the recipe
(`custom_assets/<game>/models/models.jsonc`); every player makes the models from discs they own.

## The manifest

`custom_assets/<game>/models/models.jsonc` lists:

| Key | What it says |
|---|---|
| `games` | The discs the models need, the mod's own game included. At an install, the extractor checks the player has each one. |
| `rips` | Each rip folder the models read (`jak2/forresca`) and the DGO it comes from (`FRA.DGO`). |
| `models` | One entry per model: `level` (the level whose `.fr3` gets the mesh), `mesh` (`<game>/<rip folder>/<rip file>`), and optionally `prims`, `skeleton`, `padding`, `clips`, `clip_prefix`, `textures_from`, `texture_prefix`, `recolor` (a color variant: `{"hues": [-15, 15], "rotate": 217, "min_saturation": 0.3}` turns the texels of that hue window and saturation by 217 degrees, red to blue; `"textures"` limits it to some images; a changed image is renamed `<image>-<model>`, so two variants of one rip can share a level). |
| `files` | Other files copied from a disc, such as a music: `{"custom_assets/jak3/music/X.MUS": "jak2/MUS/DANGER4.MUS"}`. |

Every key is described at the top of [`scripts/modding/mod_models.py`](../../../scripts/modding/mod_models.py).
The mods Battlefront and Praxis KG Invasion have complete examples.

Each model is written twice with the same content:

- `custom_assets/<game>/models/custom_levels/<name>.glb`, which `build-actor` reads (art group
  `<name>-ag`);
- `custom_assets/<game>/models/<level>/<name>-lod0.glb`, which the level extraction bakes into
  `<level>.fr3` so the renderer has the mesh.

List those outputs in the mod's `.gitignore`: they are made, never committed.

## In development

```bash
task modding-models     # rips what is missing (only those DGOs), then writes the models
task extract            # bakes them into their levels
task compile-check
```

The tool needs both games' disc files in `iso_data/<game>`, the decompiler built
(`task build-release-decomp`) and numpy. `task modding-models -- --check` lists what is missing
without writing anything.

## At a launcher install

The release workflow builds the tool as a one-file executable, `mod_models`, when the mod has a
manifest, and ships it next to the extractor. Before its decompile, the mod's extractor runs it on
the player's extracted discs (`run_mod_models`, `decompiler/extractor/main.cpp`): it rips the
needed DGOs with the extractor itself, writes the models and files, then the decompile bakes them
and the compile builds them.

If a disc of `games` isn't installed in the launcher, the install stops with a message asking the
player to install that game first (error 4060), then to reinstall the mod.
