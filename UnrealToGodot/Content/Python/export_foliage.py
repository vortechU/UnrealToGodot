"""
Unreal Engine Python Script
Exports instanced static mesh data (Foliage paint tool, HierarchicalInstanced-
StaticMeshComponents, and plain InstancedStaticMeshComponents) as packed
world-space Godot transform arrays for MultiMeshInstance3D reconstruction.
See docs/SCHEMA_V2.md ("foliage" section) for the exact packing layout:
12 floats per instance — basis column X, column Y, column Z, then origin.

The orchestrator excludes these component classes from regular per-component
actor export (get_instanced_component_classes) so instanced meshes are never
placed as single copies — which would be wrong for any painted foliage.

--------------------------------------------------------------------------
Per-component rendering state
--------------------------------------------------------------------------
Instances carry more than transforms. Three component properties change what a
foliage field actually looks like and cost, and all three used to be dropped:

  * `cast_shadow` -- grass and undergrowth are routinely authored with shadows
    OFF because per-blade shadows are ruinous and barely visible. Importing
    them ON is both a visual and a performance regression.
  * `instance_start_cull_distance` / `instance_end_cull_distance` -- UE culls
    dense foliage per instance, and the numbers are usually set. Godot has an
    exact counterpart in GeometryInstance3D's visibility range, so dropping
    them meant every blade of grass rendering to the horizon.
  * `visible` / `hidden_in_game` -- a field the level deliberately switched off
    came back on.

UE's cull pair maps onto Godot's visibility range directly: instances are gone
at `instance_end_cull_distance` and start fading at `instance_start_cull_distance`,
which is `visibility_range_end` with a `visibility_range_end_margin` of the
difference. With no start distance UE pops rather than fades, so the fade mode
stays DISABLED and the pop is reproduced faithfully.

Deliberately NOT mapped: `bounds_scale`, `min_lod`,
`world_position_offset_disable_distance` and `receives_decals` have no faithful
Godot counterpart, and per-instance custom data is unreachable -- UE 5.7 exposes
`num_custom_data_floats` but no getter for the values (verified by probing the
component API), so Godot's MultiMesh custom-data channel cannot be filled.
"""

import unreal
import ue2g_common


def get_instanced_component_classes():
    """
    Component classes the layout exporter must exclude from per-component export.
    InstancedStaticMeshComponent is the base of both HISM and foliage components,
    so it alone covers the whole family.
    """
    classes = []
    cls = getattr(unreal, "InstancedStaticMeshComponent", None)
    if cls is not None:
        classes.append(cls)
    return tuple(classes)


def _b(value, default=False):
    """Defensive bool coercion; None/failed conversions fall back to default."""
    if value is None:
        return default
    try:
        return bool(value)
    except Exception:
        return default


def _cull_distances_m(comp):
    """
    UE's per-instance cull pair (cm) as Godot metres, or (None, None) when the
    component is never culled by distance. Returns (begin_fade_m, end_m):
    `end_m` is where instances vanish, `begin_fade_m` where they start fading.
    """
    end_cm = ue2g_common.safe_get_prop(comp, "instance_end_cull_distance", 0)
    try:
        end_cm = float(end_cm or 0)
    except Exception:
        return None, None
    if end_cm <= 0.0:
        # UE treats 0 as "never cull", whatever the start distance says.
        return None, None

    start_cm = ue2g_common.safe_get_prop(comp, "instance_start_cull_distance", 0)
    try:
        start_cm = float(start_cm or 0)
    except Exception:
        start_cm = 0.0

    end_m = end_cm * ue2g_common.CM_TO_M
    if 0.0 < start_cm < end_cm:
        return start_cm * ue2g_common.CM_TO_M, end_m
    # No usable start distance: UE pops the instances out, so Godot should too.
    return None, end_m


def collect_foliage(all_actors, register_mesh, collected_textures=None):
    """
    Returns a list of schema foliage entries. register_mesh(static_mesh) -> str
    registers the mesh in the orchestrator's mesh library and returns its key.
    `collected_textures` is the orchestrator's texture set, so textures reached
    only through a foliage material override still get exported.
    Never raises, never shows dialogs.
    """
    entries = []
    used_names = set()

    # Imported lazily: export_level_to_json imports THIS module (for the
    # instanced-class exclusion list) before it finishes defining its own
    # helpers, so a module-level import here would be circular.
    try:
        from export_level_to_json import extract_component_material_overrides
    except Exception:
        extract_component_material_overrides = None

    ism_class = getattr(unreal, "InstancedStaticMeshComponent", None)
    if ism_class is None:
        return entries
    foliage_actor_class = getattr(unreal, "InstancedFoliageActor", None)

    for actor in (all_actors or []):
        try:
            if actor is None:
                continue
            comps = actor.get_components_by_class(ism_class)
        except Exception:
            continue

        for comp in comps:
            try:
                mesh = ue2g_common.safe_get_prop(comp, "static_mesh")
                if not mesh:
                    continue
                # Editor-only components (sprites, helper visualisations) never
                # ship with the game and must not ship with the export either.
                if _b(ue2g_common.safe_get_prop(comp, "is_editor_only", False), False):
                    continue
                count = int(comp.get_instance_count())
                if count <= 0:
                    continue

                transforms = []
                for i in range(count):
                    t = _instance_world_transform(comp, i)
                    if t is not None:
                        transforms.extend(ue2g_common.godot_transform_basis(t))
                if not transforms:
                    continue

                if register_mesh is not None:
                    mesh_key = register_mesh(mesh)
                else:
                    mesh_key = ue2g_common.sanitize_name(mesh.get_name())

                source = _classify_source(actor, comp, foliage_actor_class)
                if source == "foliage":
                    base_name = f"Foliage_{mesh_key}"
                else:
                    actor_label = ue2g_common.sanitize_name(actor.get_actor_label())
                    base_name = f"Instances_{actor_label}_{mesh_key}"
                name = base_name
                suffix = 1
                while name in used_names:
                    suffix += 1
                    name = f"{base_name}_{suffix}"
                used_names.add(name)

                cull_begin_m, cull_end_m = _cull_distances_m(comp)

                material_overrides = []
                if extract_component_material_overrides is not None:
                    try:
                        material_overrides = extract_component_material_overrides(
                            comp, collected_textures) or []
                    except Exception as e:
                        unreal.log_warning(
                            f"export_foliage: could not read material overrides on "
                            f"'{name}': {str(e)}")

                entries.append({
                    "name": name,
                    "mesh_key": mesh_key,
                    "mesh_name": mesh.get_name(),
                    "instance_count": len(transforms) // 12,
                    "source": source,
                    "visible": (_b(ue2g_common.safe_get_prop(comp, "visible", True), True)
                                and not _b(ue2g_common.safe_get_prop(
                                    comp, "hidden_in_game", False), False)),
                    "cast_shadow": _b(ue2g_common.safe_get_prop(comp, "cast_shadow", True), True),
                    "cull_begin_m": cull_begin_m,
                    "cull_end_m": cull_end_m,
                    "material_overrides": material_overrides,
                    "godot_transforms": transforms,
                })
            except Exception as e:
                try:
                    label = actor.get_actor_label()
                except Exception:
                    label = "<unknown>"
                unreal.log_warning(f"export_foliage: failed to read instances on '{label}': {str(e)}")

    return entries


def _instance_world_transform(comp, index):
    """
    Reads one instance's world-space transform defensively. The UE Python wrapper
    may return the transform directly or a (success, transform) tuple depending
    on engine version.
    """
    try:
        result = comp.get_instance_transform(index, True)
    except TypeError:
        try:
            result = comp.get_instance_transform(index, world_space=True)
        except Exception:
            return None
    except Exception:
        return None

    if isinstance(result, unreal.Transform):
        return result
    if isinstance(result, tuple):
        for item in result:
            if isinstance(item, unreal.Transform):
                return item
    return None


def _classify_source(actor, comp, foliage_actor_class):
    """Classifies the component origin: painted foliage, HISM, or plain ISM."""
    try:
        if foliage_actor_class is not None and isinstance(actor, foliage_actor_class):
            return "foliage"
        cls_name = comp.get_class().get_name()
        if "Foliage" in cls_name:
            return "foliage"
        if "Hierarchical" in cls_name:
            return "hism"
    except Exception:
        pass
    return "ism"
