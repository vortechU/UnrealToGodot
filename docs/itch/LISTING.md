# itch.io listing (v0.5, waiting for the build)

Create the page at https://itch.io/game/new and leave it as Draft until you have previewed it.

| Field | Value |
| --- | --- |
| Title | Unreal to Godot Exporter |
| Project URL | `unreal-to-godot` |
| Short description | Free, open-source exporter that moves Unreal levels and 3D asset packs into Godot 4. |
| Classification | Tool |
| Kind of project | Downloadable |
| Release status | In development |
| Pricing | No payments (fully free). "$0 or donate" keeps it free and adds a tip prompt. |
| Upload | `UnrealToGodot-v0.5.zip`; display name "Unreal plugin + Godot addon v0.5"; platform: Windows |
| Description | Paste `description.html` into the editor's HTML (`<>`) view |
| Tags | `godot`, `unreal-engine`, `exporter`, `importer`, `level-design`, `3d`, `gltf`, `game-development`, `open-source`, `addon` |
| Code license | MIT |
| Generative AI disclosure | Your answer. It asks about AI results in the project, including hand-edited ones; "Yes" then lists Code / Text / Graphics. |
| External link | https://github.com/vortechU/UnrealToGodot |
| Community | Comments |
| Cover image | `docs/itch/cover.png` (630x500) |

## Screenshots, in order

1. `assets/social-card.jpg` - Unreal (left) and Godot (right)
2. `assets/hero-godot.jpg` - the imported scene in Godot
3. `assets/ui-exporter.png` - the Unreal exporter
4. `assets/ui-importer-dock.png` - the Godot importer dock

## Before publishing

The text covers roadmap phases 1-4, as committed on `main`.

- [ ] Run the tests with `--godot` and a real Unreal export.
- [ ] Set 0.5 in `UnrealToGodot.uplugin` and `addons/unreal_importer/plugin.cfg`.
- [ ] Update the `README.md` scope table (cameras and audio are still listed as not exported).
- [ ] Build `UnrealToGodot-v0.5.zip` with LICENSE inside; tag and push `v0.5` (the docs link needs the tag).
- [ ] Retake the Godot and dock screenshots, and rebuild the cover from the new Godot shot.

## Page theme

Banner `banner.jpg` (centered), background `background.jpg` (repeat horizontally, not fixed), screenshots in the sidebar.
Colors: BG `#0d1117`, BG 2 `#161b22`, text `#d7dee6`, links `#6cb2e8`, headers `#f0a878`, buttons `#2b73a8`.
