# Unreal to Godot — Feature Additions & Progress Tracker

This document tracks the planned feature additions across the Unreal exporter plugin, the layout JSON schema, the Godot importer addon, the direct `.tscn` writer, and the automated test suite.

> **Verification standard (per `.claude/commands/audit-feature.md`):**
> Every feature must stay in sync across **all four layers**:
> 1. **Unreal Python exporter** (`UnrealToGodot/Content/Python/`) — APIs probed against real Unreal, never guessed.
> 2. **Schema & docs** (`docs/SCHEMA_V2.md`, `README.md`, dock tooltips).
> 3. **Both Godot consumers** (`addons/unreal_importer/` **and** `tscn_writer.py`, pinned against drift in `tests/test_tscn_writer.py`).
> 4. **Tests** — Python unit tests, headless Godot harness (`tests/run_tests.py --godot ...`), and real Unreal smoke/probe checks where applicable.

---

## Overall Progress

- **Completed:** 6 / 15
- **In Progress:** 0 / 15
- **Pending:** 9 / 15

---

## Phase 1 — Visual Fidelity & Material Pipeline

Highest visual payoff across every imported asset pack.

- [x] **1. DirectX → OpenGL Normal Map Green-Channel Flip**
  - **Goal:** Prevent inverted surface relief caused by exporting Unreal's raw DirectX (`-Y`) normal maps into Godot's OpenGL (`+Y`) pipeline.
  - **Exporter:** Record normal-map texture flags (including Unreal's `Texture.flip_green_channel` state) in the layout JSON (`texture_metadata`).
  - **Godot Importer:** Set `process/normal_map_invert_y` non-destructively in `.import` via `texture_import_limit.gd` (with in-memory fallback for external non-`res://` textures and a dock toggle).
  - **Tests:** `test_materials.py`, `test_tscn_writer.py`, and Godot harness `.import` assertions.

- [x] **2. Material Blend Modes, Alpha Cutout & Two-Sided Rendering**
  - **Goal:** Foliage leaves, fences, grates, and glass import with proper alpha/culling instead of solid single-sided opaque surfaces.
  - **Exporter:** Read Unreal `blend_mode` (`opaque` / `masked` / `translucent` / `additive`), `two_sided`, `opacity_mask_clip_value`, and opacity/mask scalar & texture parameters in `export_level_to_json.py` + `export_static_meshes_to_gltf.py`.
  - **Godot Importer & glTF Preview:** Map to `transparency` (`ALPHA_SCISSOR` / `ALPHA`), `blend_mode` (`BLEND_MODE_ADD`), `alpha_scissor_threshold`, `cull_mode = CULL_DISABLED`, and alpha-channel merging for separate `opacity_texture`s.
  - **Tests:** `test_materials.py`, `test_tscn_writer.py`, and headless Godot harness assertions.

- [x] **3. Emissive Maps, Color & Intensity**
  - **Goal:** Lamps, screens, neon signs, and glowing panels emit light automatically under the imported ACES + bloom `WorldEnvironment`.
  - **Exporter:** Extract `emissive_texture`, `emissive_color`, and `emissive_energy` in `extract_material_parameters` (keeping emissive out of albedo tint) + wire into glTF preview injection.
  - **Godot Importer:** Bind `emission_enabled`, `emission`, `emission_texture` (with `EMISSION_OP_MULTIPLY`), and `emission_energy_multiplier`.
  - **Tests:** `test_materials.py`, `test_tscn_writer.py`, and headless Godot harness assertions.

- [x] **4. Optional External `.tres` Material Resources**
  - **Goal:** Keep `.tscn` files compact and let users share/customize materials across multiple imported scenes without re-importing.
  - **Godot Importer:** Added dock toggle (`save_external_materials`) saving shared `StandardMaterial3D` resources to `res://materials/<name>.tres` (with collision-safe disambiguation) and reusing existing `.tres` files on disk.
  - **Tests:** `test_tscn_writer.py` contract pinning + Godot harness support.

---

## Phase 2 — Complete Half-Wired Exporter Pipelines

Features whose Unreal export side already exists, waiting on full Godot consumption.

- [x] **5. Wire Up Exported `_LOD<n>.gltf` Chains in Godot**
  - **Goal:** Make the exporter's existing **"Export LOD Levels"** checkbox work end-to-end for heavy/Nanite asset packs.
  - **Exporter:** Export per-LOD Unreal `screen_size` thresholds and computed `switch_distance_m` under `meshes[key].lods` in `export_level_to_json.py`.
  - **Godot Importer & `.tscn` Writer:** Instance `_LOD1..N.gltf` meshes with non-overlapping `visibility_range_begin` / `visibility_range_end` bands (plus disk auto-discovery for older layouts) and `metadata/unreal_lod_files` handover in `tscn_writer.py`.
  - **Tests:** `test_materials.py` screen-size math + headless Godot harness LOD visibility assertions.

- [x] **6. Consume `<mesh>_physics.json` for Skeletal Meshes**
  - **Goal:** Complete the existing `extract_skeletal_mesh_physics` (`UPhysicsAsset`) pipeline on the Godot side.
  - **Exporter:** Stamps `physics_file` on skeletal mesh entries with a `UPhysicsAsset` in `export_level_to_json.py` (and `metadata/unreal_physics_file` in `tscn_writer.py`).
  - **Godot Importer:** Added `addons/unreal_importer/import_skeletal_physics.gd` building `PhysicalBoneSimulator3D` + `PhysicalBone3D` + `CollisionShape3D` (case-insensitive bone matching) during import and via a standalone dock button (**"Build Skeletal Physics (.tscn scenes)"**).
  - **Tests:** Headless Godot harness with real 2-bone skinned glTF + `_physics.json` fixture.

---

## Phase 3 — Scene Parity & Missing Level Primitives

Closing the remaining structural gaps between Unreal levels and Godot scenes.

- [ ] **7. Per-Component Visibility, Shadow Casting & Collision Flags on Static Meshes**
  - **Goal:** Stop decorative `NoCollision` props from getting physics bodies and stop `HiddenInGame` blockers/proxies from rendering.
  - **Exporter:** Export `visible` (`Visible && !HiddenInGame`), `cast_shadow`, `collision_enabled`, and `max_draw_distance` on regular mesh components in `export_level_to_json.py`.
  - **Godot Importer & `.tscn` Writer:** Apply `visible`, `cast_shadow`, `visibility_range_end`, and skip `StaticBody3D` generation when `collision_enabled` is false.
  - **Tests:** `test_layout.py`, `test_tscn_writer.py`, and Godot harness assertions.

- [ ] **8. Reflection Captures → `ReflectionProbe`**
  - **Goal:** Give metallic/interior scenes accurate local reflections out of the box.
  - **Exporter:** Collect `BoxReflectionCaptureComponent` and `SphereReflectionCaptureComponent` (including Blueprint components) in `export_environment.py`: bounds/radius, brightness/intensity, box projection, and interior flag.
  - **Godot Importer & `.tscn` Writer:** Create `ReflectionProbe` nodes under `UnrealReflectionProbes` (or `UnrealEnvironment` group).
  - **Tests:** `test_materials.py` / `test_tscn_writer.py` + headless Godot `env_test.gd` / harness assertions.

- [ ] **9. Preserve Unreal World Outliner Folders**
  - **Goal:** Turn thousands of flat nodes under `UnrealStaticMeshes` into a navigable hierarchy matching Unreal's World Outliner.
  - **Exporter:** Export `folder_path` (`actor.get_folder_path()`) on actors (and optionally lights/decals).
  - **Godot Importer & `.tscn` Writer:** Add an optional **"Mirror Unreal Outliner Folders"** toggle creating nested `Node3D` folder containers.
  - **Tests:** `test_layout.py`, `test_tscn_writer.py`, and Godot harness hierarchy check.

- [ ] **10. Spline Meshes (`SplineMeshComponent`)**
  - **Goal:** Properly migrate spline-deformed roads, pipes, cables, curbs, and fences instead of leaving them unbent or missing.
  - **Exporter:** Probe Unreal Python API for `SplineMeshComponent` (evaluate baking deformed geometry vs. exporting start/end positions, tangents, roll, scale, and forward axis).
  - **Godot Importer:** Reconstruct deformed meshes (baked glTF or Godot `MeshDataTool` cubic Hermite deformation along the spline axis).
  - **Tests:** Math/deformation unit tests + real Unreal spline probe + Godot harness check.

- [ ] **11. Opt-In `CameraActor` & `AmbientSound` Export**
  - **Goal:** Quickly reproduce Unreal showcase/demo camera framing and spatial ambient audio beds.
  - **Exporter:** Add opt-in toggles for:
    - `CameraComponent` / `CineCameraComponent` → FOV, projection, near/far clip, transform.
    - `AudioComponent` / `AmbientSound` → transform, attenuation radius, volume, pitch, and `.wav` export via `SoundWave`.
  - **Godot Importer & `.tscn` Writer:** Build `Camera3D` under `UnrealCameras` and `AudioStreamPlayer3D` under `UnrealAudio`.
  - **Tests:** Unit tests, `.tscn` pinning, and Godot harness checks.

---

## Phase 4 — Workflow, Iteration & Scale

Making the pipeline smooth for iterative level building and multi-map asset packs.

- [ ] **12. Incremental / Non-Destructive Re-Import (Stable Actor IDs)**
  - **Goal:** Allow re-exporting from Unreal and updating an existing Godot scene without duplicating actors or wiping user scripts/children.
  - **Exporter:** Stamp stable `actor_id` (Unreal actor GUID / persistent object path) on exported actors, lights, decals, and foliage entries.
  - **Godot Importer:** Store `meta("unreal_actor_id")` and add **"Update Existing Scene"** mode (update transforms/properties in place, add new actors, optionally prune removed ones).
  - **Tests:** Headless Godot test running two sequential imports (modify + add + remove) and verifying no duplicates and preserved custom child nodes/metadata.

- [ ] **13. Repeated Blueprints / Packed Level Actors → Instanced `.tscn` Prefabs**
  - **Goal:** Deduplicate repeated multi-mesh Blueprint props so editing one prefab updates every placed instance in Godot.
  - **Exporter:** Record Blueprint class/asset path (`blueprint_asset`) on multi-component actors and Packed Level Actors.
  - **Godot Importer:** Optional **"Extract Blueprints as Sub-Scenes"** toggle writing unique templates to `res://prefabs/<Name>.tscn` and instancing them at actor transforms.
  - **Tests:** Headless Godot test confirming shared sub-scene instancing and exact world placement.

- [ ] **14. World Partition / Streaming Sublevel Warnings & Multi-Level Batching**
  - **Goal:** Prevent silent partial exports when a map uses World Partition or unloaded streaming sublevels.
  - **Exporter & Diagnostics:** Query `WorldPartition` / streaming levels during export; warn in Tkinter status bar and `ue2g_report.txt` if unloaded cells/sublevels exist. Optionally support exporting each loaded sublevel as a separate Godot sub-scene.
  - **Tests:** `test_diagnose.py` and exporter unit tests with stubbed streaming sublevels.

- [ ] **15. Godot Dock UX Polish (`importer_dock.gd`)**
  - **Goal:** Bring the Godot dock up to parity with the Unreal GUI's polish and safety.
  - **Persist Dock Settings:** Save/restore paths, feature toggles, light energy scale, decal depth, and texture cap via `EditorSettings` or a config file.
  - **Pre-Import JSON Preview / Dry-Run:** Parse the selected layout JSON and show counts (actors, unique meshes, missing files, estimated texture size) before clicking Import.
  - **Editor `UndoRedo` Integration:** Wrap scene modifications in `EditorUndoRedoManager` so an import can be reverted with `Ctrl+Z`.
  - **Report Shortcut:** Add an **"Open Import Report"** button in the dock pointing to `res://ue2g_import_report.txt`.
  - **Tests:** Automated headless checks for config persistence and JSON dry-run summary logic.

---

## Schema Compatibility Notes

- **Decision:** Keep `format_version: 2`. All planned additions are **purely additive** JSON fields/sections with safe defaults when omitted, so existing v2 exports continue importing cleanly without requiring forced re-exports.
