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


def collect_foliage(all_actors, register_mesh):
    """
    Returns a list of schema foliage entries. register_mesh(static_mesh) -> str
    registers the mesh in the orchestrator's mesh library and returns its key.
    Never raises, never shows dialogs.
    """
    entries = []
    used_names = set()

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

                entries.append({
                    "name": name,
                    "mesh_key": mesh_key,
                    "mesh_name": mesh.get_name(),
                    "instance_count": len(transforms) // 12,
                    "source": source,
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
