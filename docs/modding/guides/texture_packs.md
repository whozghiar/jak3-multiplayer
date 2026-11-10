# Giving a Mod Its Textures

This guide shows the two ways to ship textures with a mod: inside the mod itself, or as a texture
pack published next to it. Read it before your mod's first release.

## Which one?

| You want | Use |
|---|---|
| The mod looks right as soon as a player installs it | [Textures inside the mod](#textures-inside-the-mod) |
| An optional look the player chooses to add | [A texture pack next to the mod](#a-texture-pack-next-to-the-mod) |

> [!IMPORTANT]
> The OpenGOAL Launcher cannot tie a texture pack to a mod. A pack added in its **Texture Packs**
> screen changes the base game, not the installed mod (its mod-source format has no field for it;
> checked on the launcher's `main` branch, 2026-10-02). Textures the mod needs go inside the mod.

## Textures inside the mod

1. Put each PNG in `custom_assets/<game>/texture_replacements/<tpage-name>/<texture-name>.png`
   (or in `_all/` for a texture shared by several pages). The names and image rules are in the
   wiki's [textures page](../../../.agents/skills/goal-lisp/wiki/textures.md).
2. Run `task extract`, then compile and look at the result in game.
3. Commit the PNGs with `git add -f`: the folder is ignored by git.

That's all. The release copies `custom_assets/` into the mod's archive, and the mod's extractor
bakes the textures when a player installs it.

## A texture pack next to the mod

1. Make the pack's `.zip`, either way:
   - with the [OpenGOAL Texture Pack Generator](https://github.com/whozghiar/open-goal-texture-pack-generator),
     saved into `docs/modding/current_mod/texture_packs/`;
   - or from the PNGs of `custom_assets/<game>/texture_replacements/`:
     ```bash
     task modding-package-texture-pack -- --from-source --game jak2 --slug my-pack --display-name "My Pack"
     ```
2. Register it in the mod's `index.json` (the zips of `docs/modding/current_mod/texture_packs/`):
   ```bash
   task modding-package-texture-pack
   ```
3. Commit the `.zip` with `git add -f`, and `index.json`.
4. Release the mod ([`mod_distribution_guide.md`](mod_distribution_guide.md)): the release
   attaches every zip of that folder and lists it under `texturePacks` in `index.json`.

Players download the pack from the release page or the catalog, then add it in the launcher's
**Texture Packs** screen. Tell them in the mod's README that it applies to the base game.

All the options of the packaging task: [`task_scripts_reference.md`, section 8](task_scripts_reference.md#8-task-modding-package-texture-pack-alias-task-modding-register-texture-pack----options).
