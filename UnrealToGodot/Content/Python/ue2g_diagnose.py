"""Audits an Unreal-to-Godot export and reports what will break, before Godot
gets a chance to fail in a way that's hard to read.

The exporter runs this automatically and drops the report next to the export,
so normally there is nothing to run by hand. To re-run it later, from a
TERMINAL (not Unreal's Python console):

    python tools/ue2g_diagnose.py <export_dir> [--godot-project <dir>]

<export_dir> is the folder holding models/ + textures/ + level_layout.json.
This module imports nothing but the standard library -- no unreal, no Godot,
no PIL -- so it runs inside Unreal, from a shell, or on a machine that has
only the exported files and no engine at all.

Every check here exists because the failure it catches actually happened and
was expensive to diagnose from a screenshot.
"""
import argparse
import datetime
import json
import os
import struct
import sys
from collections import Counter

# Texture suffixes the UE glTF exporter emits when material baking is ON. Their
# presence in a .gltf means the export predates the baking fix (or ran with an
# older plugin): Godot then looks for <material>_<mesh>_BaseColor.png next to
# the model and reports "Can't open file from path".
BAKED_SUFFIXES = ("_BaseColor.png", "_MetallicRoughness.png", "_Normal.png",
                  "_Occlusion.png", "_Specular.png", "_Emissive.png")

# Godot clamps these to 0..1 and MULTIPLIES them with their texture, so a value
# outside the range silently flattens or blows out every surface using it.
UNIT_RANGE_PARAMS = ("roughness", "metallic", "specular", "clearcoat",
                     "normal_scale", "ao_light_affect")

# Godot's WebP packer exhausts memory and segfaults partway through a large
# texture set, leaving a half-populated .godot cache that looks like a
# toolchain bug. Thresholds are conservative and based on an observed crash at
# 3.1 GB / 4096px.
TOTAL_TEXTURE_BYTES_WARN = 1_500_000_000
TEXTURE_DIM_WARN = 4096


class Report:
    """Collects findings and renders them oldest-first, grouped by severity."""

    def __init__(self):
        self.sections = []
        self.errors = []
        self.warnings = []
        self.facts = {}
        self.written_to = None

    def section(self, title):
        self.sections.append((title, []))
        return self

    def line(self, text):
        if not self.sections:
            self.section("General")
        self.sections[-1][1].append(text)

    def fact(self, key, value):
        self.facts[key] = value
        self.line("  %-38s %s" % (key + ":", value))

    def error(self, text):
        self.errors.append(text)
        self.line("  ERROR   " + text)

    def warn(self, text):
        self.warnings.append(text)
        self.line("  WARNING " + text)

    def ok(self, text):
        self.line("  ok      " + text)

    def render(self):
        out = ["=" * 72, "UNREAL -> GODOT EXPORT DIAGNOSTIC", "=" * 72, ""]
        for title, lines in self.sections:
            out.append("--- %s ---" % title)
            out.extend(lines)
            out.append("")
        out.append("=" * 72)
        out.append("VERDICT: %d error(s), %d warning(s)" % (len(self.errors), len(self.warnings)))
        out.append("=" * 72)
        if self.errors:
            out.append("")
            out.append("ERRORS (these will visibly break the import):")
            for e in self.errors:
                out.append("  * " + e)
        if self.warnings:
            out.append("")
            out.append("WARNINGS (these may degrade the result):")
            for w in self.warnings:
                out.append("  * " + w)
        if not self.errors and not self.warnings:
            out.append("")
            out.append("No problems found in this export.")
        return "\n".join(out)


def png_size(path):
    """Reads width/height from a PNG's IHDR without decoding it (no PIL needed)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", head[16:24])
    except Exception:
        return None


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


FEATURE_SECTIONS = ("lights", "decals", "reflection_probes", "cameras", "audio",
                    "landscapes", "foliage", "navigation",
                    "post_process", "height_fog", "sky_light")


def audit_layout(rep, path, strict=True):
    """Reads level_layout.json: actor/mesh counts, material scalars, transforms.

    strict=False when called right after a mesh-only export, where the layout
    legitimately does not exist yet and an error would be a false alarm.
    """
    rep.section("Layout (level_layout.json)")
    if not os.path.isfile(path):
        msg = ("no level_layout.json at %s -- the layout export is a SEPARATE "
               "action from the mesh export; running one does not run the other."
               % path)
        if strict:
            rep.error(msg)
        else:
            rep.warn(msg)
        return None
    rep.fact("file size", human_bytes(os.path.getsize(path)))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        rep.error("level_layout.json is not valid JSON (%s)" % e)
        return None

    if not isinstance(data, dict):
        rep.error("level_layout.json root must be a JSON object (got %s)" % type(data).__name__)
        return None

    actors = data.get("actors") if isinstance(data.get("actors"), list) else []
    meshes = data.get("meshes") if isinstance(data.get("meshes"), dict) else {}
    rep.fact("format_version", data.get("format_version", "(absent)"))
    rep.fact("level_name", data.get("level_name", "(absent)"))
    rep.fact("actors", len(actors))
    rep.fact("mesh entries", len(meshes))
    if not actors and not any(data.get(key) for key in FEATURE_SECTIONS):
        rep.error("layout contains zero actors -- nothing will be imported.")

    # Which v2 feature sections this export actually carries. A section that is
    # empty when the level clearly has that feature means its export was either
    # switched off in the dock or failed silently.
    def _count(value):
        if isinstance(value, (list, dict)):
            return len(value)
        return "present" if value else "none"

    rep.line("  feature sections:")
    for key in FEATURE_SECTIONS:
        if key in data:
            rep.line("    %-22s %s" % (key, _count(data.get(key))))

    ws = data.get("world_streaming")
    if isinstance(ws, dict):
        unloaded = ws.get("unloaded_sublevels") or []
        if unloaded:
            rep.warn("%d streaming sublevel(s) were unloaded during export and omitted "
                     "(e.g. %s). Load them in Unreal and re-export to include them."
                     % (len(unloaded), ", ".join(str(x) for x in unloaded[:5])))
        if ws.get("is_world_partition"):
            rep.warn("Level uses World Partition: only cells loaded in the Unreal Editor "
                     "at export time were included.")

    # The importer must never compose actor * relative; it relies on this key.
    missing_world = 0
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        for comp in (actor.get("components") or []):
            if isinstance(comp, dict) and not comp.get("godot_world_transform"):
                missing_world += 1
    if missing_world:
        rep.error("%d component(s) lack godot_world_transform -- the importer "
                  "cannot place them without re-deriving a doubled transform."
                  % missing_world)
    else:
        rep.ok("every component carries godot_world_transform")

    # Scalars outside Godot's unit range. Godot multiplies these with their
    # texture, so 4.57 forces a surface fully matte and a negative is nonsense.
    bad_scalars = Counter()
    bad_examples = {}
    tinted = 0

    def _check_mat_params(params, label):
        nonlocal tinted
        if not isinstance(params, dict):
            return
        for pname in UNIT_RANGE_PARAMS:
            val = params.get(pname)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if val < 0.0 or val > 1.0:
                    bad_scalars[pname] += 1
                    bad_examples.setdefault(pname, (label, val))
        albedo = params.get("albedo_color")
        if isinstance(albedo, list) and any(
                isinstance(c, (int, float)) and c > 1.0 for c in albedo[:3]):
            tinted += 1

    for key, mesh in meshes.items():
        if not isinstance(mesh, dict):
            continue
        for mat in (mesh.get("materials") or []):
            if isinstance(mat, dict):
                _check_mat_params(mat.get("parameters"), key)
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        aname = actor.get("name") or "Actor"
        for comp in (actor.get("components") or []):
            if not isinstance(comp, dict):
                continue
            for ov in (comp.get("material_overrides") or []):
                if isinstance(ov, dict):
                    _check_mat_params(ov.get("parameters"), aname)
    for fol in (data.get("foliage") or []):
        if not isinstance(fol, dict):
            continue
        fname = fol.get("name") or fol.get("mesh_key") or "Foliage"
        for ov in (fol.get("material_overrides") or []):
            if isinstance(ov, dict):
                _check_mat_params(ov.get("parameters"), fname)

    if bad_scalars:
        for pname, count in bad_scalars.most_common():
            mesh_key, val = bad_examples[pname]
            # A warning, not an error: the importer clamps these on read, so
            # the import survives. It is still worth surfacing, because the
            # clamp is a guess at what an Unreal shader graph meant.
            rep.warn("%d material slot(s) have '%s' outside Godot's 0..1 range "
                     "(e.g. %s on %s). Unreal treats these as shader-graph "
                     "inputs; Godot multiplies them into the texture. The "
                     "importer clamps them, so check those surfaces look right."
                     % (count, pname, round(val, 3), mesh_key))
    else:
        rep.ok("all material scalars are within Godot's 0..1 range")

    if tinted:
        # Not an error: dark-authored albedo legitimately uses a >1 tint.
        rep.line("  note    %d material(s) use an albedo tint above 1.0. This is "
                 "normal for dark-authored textures; only suspect it if those "
                 "surfaces look washed out." % tinted)
    return data


def audit_models(rep, models_dir, layout, strict=True):
    """Reads every .gltf/.glb: image URIs, baked-texture leftovers, missing targets."""
    rep.section("Models (*.gltf / *.glb)")
    if not os.path.isdir(models_dir):
        msg = ("no models folder at %s -- the mesh export is a SEPARATE action "
               "from the layout export; running one does not run the other."
               % models_dir)
        if strict:
            rep.error(msg)
        else:
            rep.warn(msg)
        return
    all_entries = _listdir(models_dir)
    gltfs = sorted(f for f in all_entries if f.lower().endswith(".gltf"))
    glbs = sorted(f for f in all_entries if f.lower().endswith(".glb"))
    model_files = gltfs + glbs
    rep.fact("glTF/GLB files", len(model_files))
    if not model_files:
        rep.error("models/ contains no .gltf or .glb files.")
        return

    baked_hits, bare_uri, missing_target, no_texture = [], [], [], []
    referenced_sibling_pngs = set()
    uri_count = 0
    for name in gltfs:
        full = os.path.join(models_dir, name)
        try:
            with open(full, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            rep.error("%s is not readable as glTF JSON (%s)" % (name, e))
            continue
        if not isinstance(doc, dict):
            continue
        images = doc.get("images") or []
        if not images:
            no_texture.append(name)
        for img in images:
            if not isinstance(img, dict):
                continue
            uri = img.get("uri")
            if not uri or uri.startswith("data:"):
                continue
            if uri.startswith("./"):
                uri = uri[2:]  # How a texture beside the model is written.
            uri_count += 1
            if any(uri.endswith(sfx) for sfx in BAKED_SUFFIXES) and "/" not in uri:
                baked_hits.append((name, uri))
            elif "/" not in uri:
                bare_uri.append((name, uri))
                referenced_sibling_pngs.add(uri.lower())
            target = os.path.normpath(os.path.join(models_dir, uri))
            if not os.path.isfile(target):
                missing_target.append((name, uri))

    # Only flag PNGs inside models/ as stale leftovers when they carry baked
    # suffixes or are NOT intentionally referenced by sibling bare-URI glTFs
    # (which is the normal layout when separate_textures=False) or by the layout,
    # which also uses maps glTF has no slot for. With no layout only baked ones count.
    has_layout = isinstance(layout, dict)
    if has_layout:
        referenced_sibling_pngs.update(n.lower() + ".png" for n in layout_texture_names(layout))
    stray_png = [
        f for f in all_entries
        if f.lower().endswith(".png")
        and (any(f.endswith(sfx) for sfx in BAKED_SUFFIXES)
             or (has_layout and f.lower() not in referenced_sibling_pngs))
    ]
    if stray_png:
        rep.warn("%d unreferenced/baked PNG(s) sit inside models/ (e.g. %s). "
                 "These look like leftovers from an older export."
                 % (len(stray_png), stray_png[0]))

    rep.fact("image URIs referenced", uri_count)

    if baked_hits:
        name, uri = baked_hits[0]
        rep.error("%d image URI(s) still use BAKED texture names (e.g. '%s' in "
                  "%s). A current export cannot produce these -- this folder "
                  "holds .gltf files from an older export. Clear models/ and "
                  "re-run the mesh export, then delete the Godot project's "
                  ".godot/ cache." % (len(baked_hits), uri, name))
    else:
        rep.ok("no baked texture references (material baking is correctly off)")

    if bare_uri and missing_target:
        name, uri = bare_uri[0]
        rep.warn("%d image URI(s) are bare filenames (e.g. '%s' in %s). glTF "
                 "resolves URIs relative to the .gltf's own folder, so these "
                 "only work if the texture is a sibling of the model."
                 % (len(bare_uri), uri, name))

    if missing_target:
        name, uri = missing_target[0]
        rep.error("%d image URI(s) point at files that do not exist (e.g. '%s' "
                  "referenced by %s). Godot will report \"Can't open file from "
                  "path\" for each one." % (len(missing_target), uri, name))
    else:
        rep.ok("every referenced image URI resolves to a file on disk")

    if no_texture:
        rep.line("  note    %d glTF(s) reference no textures at all: %s"
                 % (len(no_texture), ", ".join(no_texture[:5])))

    # Cross-check the layout against what was actually exported (case-insensitive,
    # covering both .gltf and .glb and matching mesh_key or mesh_name fallback).
    if isinstance(layout, dict):
        available = {os.path.splitext(f)[0].lower() for f in model_files}
        wanted = {}
        for actor in (layout.get("actors") or []):
            if not isinstance(actor, dict):
                continue
            for comp in (actor.get("components") or []):
                if not isinstance(comp, dict):
                    continue
                key = comp.get("mesh_key") or comp.get("mesh_name")
                alt = comp.get("mesh_name")
                if key:
                    wanted.setdefault(str(key).lower(), (str(key), str(alt or "")))
        for foliage in (layout.get("foliage") or []):
            if isinstance(foliage, dict):
                key = foliage.get("mesh_key") or foliage.get("mesh_name")
                alt = foliage.get("mesh_name")
                if key:
                    wanted.setdefault(str(key).lower(), (str(key), str(alt or "")))
        absent = sorted(
            orig for low, (orig, alt) in wanted.items()
            if low not in available and (not alt or alt.lower() not in available)
        )
        if absent:
            rep.error("%d mesh(es) referenced by the layout have no .gltf/.glb "
                      "(e.g. %s). These import as MISSING_ placeholders -- the "
                      "two exports are out of sync; re-run the mesh export."
                      % (len(absent), ", ".join(absent[:5])))
        else:
            rep.ok("every mesh referenced by the layout has a .gltf/.glb")


IMAGE_EXTENSIONS = (".png", ".tga", ".jpg", ".jpeg", ".dds", ".exr", ".webp")


def layout_texture_names(layout):
    """Every texture filename stem the layout references across all sections."""
    names = set()

    def _add(params, keys):
        if not isinstance(params, dict):
            return
        for key in keys:
            value = params.get(key)
            if isinstance(value, str) and value:
                names.add(value)
        shader = params.get("shader")
        if isinstance(shader, dict):
            tpaths = shader.get("texture_paths")
            if isinstance(tpaths, dict):
                for slot in tpaths:
                    value = shader.get(slot)
                    if isinstance(value, str) and value:
                        names.add(value)

    slots = ("albedo_texture", "normal_texture", "roughness_texture",
             "metallic_texture", "packed_texture", "emissive_texture",
             "opacity_texture")
    for mesh in (layout.get("meshes") or {}).values():
        if not isinstance(mesh, dict):
            continue
        for mat in (mesh.get("materials") or []):
            if isinstance(mat, dict):
                _add(mat.get("parameters"), slots)
    for actor in (layout.get("actors") or []):
        if not isinstance(actor, dict):
            continue
        for comp in (actor.get("components") or []):
            if not isinstance(comp, dict):
                continue
            for override in (comp.get("material_overrides") or []):
                if isinstance(override, dict):
                    _add(override.get("parameters"), slots)
    for foliage in (layout.get("foliage") or []):
        if isinstance(foliage, dict):
            for override in (foliage.get("material_overrides") or []):
                if isinstance(override, dict):
                    _add(override.get("parameters"), slots)
    for decal in (layout.get("decals") or []):
        if isinstance(decal, dict):
            _add(decal.get("textures"), ("albedo", "normal", "orm", "emission"))
    for landscape in (layout.get("landscapes") or []):
        mat = landscape.get("material") if isinstance(landscape, dict) else None
        if isinstance(mat, dict):
            for tex in (mat.get("textures") or []):
                if isinstance(tex, dict):
                    value = tex.get("texture")
                    if isinstance(value, str) and value:
                        names.add(value)
    return names


def audit_texture_references(rep, tex_dir, layout, extra_dirs=None):
    """Checks that every texture the layout names actually exists on disk.

    Texture filenames are disambiguated per export run (a name shared by two
    assets gets a path-hash suffix), so a layout exported after a mesh export
    that saw a different set of textures can name a file that was never written.
    The material then imports without that map and nothing else notices.
    """
    if not isinstance(layout, dict):
        return
    wanted = layout_texture_names(layout)
    if not wanted:
        return
    search_dirs = [tex_dir] + [d for d in (extra_dirs or []) if d]
    present = set()
    for folder in search_dirs:
        if os.path.isdir(folder):
            for f in _listdir(folder):
                stem, ext = os.path.splitext(f)
                if ext.lower() in IMAGE_EXTENSIONS:
                    present.add(stem.lower())
    missing = sorted(n for n in wanted if n.lower() not in present)
    if missing:
        rep.warn("%d texture(s) named by the layout are not in %s (e.g. %s). "
                 "Those materials import without that map. Re-run the texture "
                 "export, or re-export the layout so the two agree."
                 % (len(missing), tex_dir, ", ".join(missing[:5])))
    else:
        rep.ok("every texture the layout names is present (%d)" % len(wanted))


def audit_textures(rep, tex_dir, layout=None, models_dir=None):
    """Sizes and dimensions -- predicts the Godot import segfault before it happens."""
    rep.section("Textures")
    audit_texture_references(rep, tex_dir, layout, extra_dirs=[models_dir] if models_dir else None)
    # Collect texture files from tex_dir, or fall back to models_dir when
    # separate_textures=False placed them beside the .gltf models.
    scan_dirs = []
    if os.path.isdir(tex_dir):
        scan_dirs.append(tex_dir)
    if models_dir and os.path.isdir(models_dir) and os.path.normcase(os.path.abspath(models_dir)) != os.path.normcase(os.path.abspath(tex_dir)):
        model_imgs = [f for f in _listdir(models_dir) if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]
        if model_imgs:
            scan_dirs.append(models_dir)
    if not scan_dirs:
        rep.warn("no textures folder at %s -- models will import untextured." % tex_dir)
        return
    file_paths = []
    for folder in scan_dirs:
        for f in _listdir(folder):
            full = os.path.join(folder, f)
            if os.path.isfile(full) and (folder == tex_dir or os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS):
                file_paths.append((f, full))
    total = sum(os.path.getsize(full) for _f, full in file_paths)
    rep.fact("texture files", len(file_paths))
    rep.fact("total size", human_bytes(total))

    oversized = []
    for f, full in file_paths:
        if not f.lower().endswith(".png"):
            continue
        dims = png_size(full)
        if dims and max(dims) >= TEXTURE_DIM_WARN:
            oversized.append((f, dims))
    if oversized:
        rep.fact("textures at/above %dpx" % TEXTURE_DIM_WARN, len(oversized))
        biggest = max(oversized, key=lambda t: t[1][0] * t[1][1])
        rep.fact("largest", "%s (%dx%d)" % (biggest[0], biggest[1][0], biggest[1][1]))

    if total > TOTAL_TEXTURE_BYTES_WARN or len(oversized) > 20:
        # Points at Godot rather than the exporter because Unreal has no way to
        # resize source art -- see docs/texture-sizing.md.
        rep.warn("This texture set is large enough to crash Godot's importer: "
                 "its WebP packer runs out of memory and segfaults partway "
                 "through, leaving a half-imported project that looks like a "
                 "toolchain bug. Set 'Texture size limit' in the Godot "
                 "importer dock (e.g. 1024) -- it caps textures already in the "
                 "project as well as new ones, and 'Also shrink texture files "
                 "on disk' rewrites the exported PNGs at that size.")
    else:
        rep.ok("texture set is within a size Godot imports reliably")


def audit_sidecars(rep, root, models_dir, layout, lods=True):
    """Checks on-disk existence of v2 sidecar assets (terrain, audio, LODs, physics).

    lods=False skips the LOD models: the layout lists them even when they were not
    asked for."""
    if not isinstance(layout, dict):
        return
    missing = []

    terrain_dir = os.path.join(root, "terrain")
    terrain_files = {f.lower() for f in _listdir(terrain_dir)}
    for ls in (layout.get("landscapes") or []):
        if not isinstance(ls, dict):
            continue
        hf = ls.get("heightmap_file")
        if isinstance(hf, str) and hf and os.path.basename(hf).lower() not in terrain_files:
            missing.append("terrain/" + os.path.basename(hf))
        for layer in (ls.get("layers") or []):
            if not isinstance(layer, dict):
                continue
            wf = layer.get("weightmap_file")
            if isinstance(wf, str) and wf and os.path.basename(wf).lower() not in terrain_files:
                missing.append("terrain/" + os.path.basename(wf))

    audio_dir = os.path.join(root, "audio")
    audio_files = {f.lower() for f in _listdir(audio_dir)}
    for aud in (layout.get("audio") or []):
        if not isinstance(aud, dict):
            continue
        sf = aud.get("sound_file")
        if isinstance(sf, str) and sf and os.path.basename(sf).lower() not in audio_files:
            missing.append("audio/" + os.path.basename(sf))

    model_entries = {f.lower() for f in _listdir(models_dir)}
    model_stems = {os.path.splitext(f)[0].lower() for f in model_entries
                   if f.endswith(".gltf") or f.endswith(".glb")}
    for mesh_entry in (layout.get("meshes") or {}).values():
        if not isinstance(mesh_entry, dict):
            continue
        pf = mesh_entry.get("physics_file")
        if isinstance(pf, str) and pf and os.path.basename(pf).lower() not in model_entries:
            missing.append("models/" + os.path.basename(pf))
        for lod in (mesh_entry.get("lods") or []) if lods else []:
            if not isinstance(lod, dict):
                continue
            if int(lod.get("lod_index") or 0) > 0:
                exp = lod.get("export_name")
                if isinstance(exp, str) and exp and exp.lower() not in model_stems:
                    missing.append("models/" + exp + ".gltf")

    if missing:
        rep.section("Sidecar Assets")
        rep.warn("%d referenced sidecar asset(s) are missing on disk (e.g. %s)."
                 % (len(missing), ", ".join(missing[:5])))


def audit_godot_project(rep, project_dir):
    """Catches the stale-cache and stale-model cases on the Godot side."""
    rep.section("Godot project")
    if not os.path.isdir(project_dir):
        rep.error("Godot project folder does not exist: %s" % project_dir)
        return
    rep.fact("path", project_dir)
    if not os.path.isfile(os.path.join(project_dir, "project.godot")):
        rep.warn("no project.godot here -- is this the project root?")

    models_dir = os.path.join(project_dir, "models")
    if os.path.isdir(models_dir):
        entries = _listdir(models_dir)
        baked = [f for f in entries
                 if any(f.endswith(sfx) for sfx in BAKED_SUFFIXES)]
        if baked:
            rep.error("%d baked texture PNG(s) still sit in the project's models/ "
                      "folder (e.g. %s). These are from an older export; delete "
                      "them or Godot keeps importing them."
                      % (len(baked), baked[0]))
        gltfs = [f for f in entries if f.lower().endswith(".gltf")]
        glbs = [f for f in entries if f.lower().endswith(".glb")]
        rep.fact("glTF/GLB files in project", len(gltfs) + len(glbs))

        # The check that matters: a .gltf's uri is resolved against its OWN
        # folder, and the transfer splits models/ from textures/.
        broken_bare = []
        broken_rel = []
        for name in gltfs:
            try:
                with open(os.path.join(models_dir, name), encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            for img in (doc.get("images") or []):
                if not isinstance(img, dict):
                    continue
                uri = img.get("uri")
                if not uri or uri.startswith("data:"):
                    continue
                if not os.path.isfile(os.path.normpath(os.path.join(models_dir, uri))):
                    if "/" not in uri and "\\" not in uri:
                        broken_bare.append((name, uri))
                    else:
                        broken_rel.append((name, uri))
        if broken_bare:
            name, uri = broken_bare[0]
            rep.error("%d image URI(s) in the project's models/ do not resolve "
                      "(e.g. '%s' in %s -- Godot looks for it at res://models/%s "
                      "and reports \"Can't open file from path\"). The .gltf "
                      "references textures beside itself, but the transfer puts "
                      "them in res://textures/. Re-export: the transfer now "
                      "rewrites these to ../textures/."
                      % (len(broken_bare), uri, name, os.path.basename(uri)))
        if broken_rel:
            name, uri = broken_rel[0]
            rep.error("%d image URI(s) in the project's models/ point to missing "
                      "texture files (e.g. '%s' referenced by %s). The texture "
                      "was not transferred or could not be exported."
                      % (len(broken_rel), uri, name))
        if not broken_bare and not broken_rel and (gltfs or glbs):
            rep.ok("every glTF in the project resolves its textures")

    cache = os.path.join(project_dir, ".godot")
    if os.path.isdir(cache):
        rep.line("  note    a .godot/ cache exists. If models were re-exported, "
                 "delete it -- Godot trusts the cache and will not notice that "
                 "a .gltf's textures changed.")


def _has_gltf(path):
    return any(f.lower().endswith((".gltf", ".glb")) for f in _listdir(path))


def find_models_dir(root):
    """Locates the exported .gltf files under an export root.

    The exporter's default is GLTF/, but the export directory is user-set and
    can be anything, so look rather than assume: an earlier version of this
    check hardcoded models/ -- a name that only ever existed in the test
    harness -- and reported a perfectly good export as empty.
    """
    if _has_gltf(root):
        return root
    for name in ("GLTF", "models", "Models", "gltf"):
        candidate = os.path.join(root, name)
        if _has_gltf(candidate):
            return candidate
    for name in sorted(_listdir(root)):
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate) and _has_gltf(candidate):
            return candidate
    return os.path.join(root, "GLTF")


def find_layout_json(root):
    """Locates the layout JSON. The exporter names it <LevelName>_layout.json,
    and falls back to level_layout.json when unnamed. When several *_layout.json
    files exist in the same folder, the most recently written one wins."""
    matches = sorted(
        f for f in _listdir(root)
        if f.lower().endswith("_layout.json") and os.path.isfile(os.path.join(root, f))
    )
    if matches:
        # Newest wins: an export folder accumulates one per level exported.
        matches.sort(key=lambda f: os.path.getmtime(os.path.join(root, f)), reverse=True)
        return os.path.join(root, matches[0])
    return os.path.join(root, "level_layout.json")


def resolve_paths(export_dir):
    """Works out where models/textures/layout live, given an export root.

    Tolerates being handed the models folder itself, since that is exactly what
    the exporter's 'export directory' setting points at (it defaults to
    Saved/Exports/GLTF), while the layout and textures sit one level up.
    """
    root = os.path.abspath(export_dir)
    models = find_models_dir(root)
    # Handed the models folder directly? Then the real root is its parent --
    # that is where textures/ and the layout json live.
    if os.path.normcase(models) == os.path.normcase(root):
        parent = os.path.dirname(root)
        if os.path.isdir(os.path.join(parent, "textures")) or _layout_in(parent):
            root = parent
    return root, models, os.path.join(root, "textures"), find_layout_json(root)


def _layout_in(path):
    return any(f.lower().endswith("_layout.json") or f.lower() == "level_layout.json"
               for f in _listdir(path))


def _listdir(path):
    try:
        return os.listdir(path)
    except Exception:
        return []


def diagnose(export_dir, godot_project=None, strict=True, out_path=None,
             layout_path=None, models_dir=None, lods=True):
    """Audits an export and writes the report. Returns (report_text, Report).

    This is the entry point the exporter calls, so it must never raise: a
    diagnostic that breaks the thing it diagnoses is worse than none.
    """
    rep = Report()
    root = os.path.abspath(export_dir)
    try:
        root, resolved_models, textures, resolved_layout = resolve_paths(export_dir)
        models = os.path.abspath(models_dir) if models_dir else resolved_models
        layout_file = os.path.abspath(layout_path) if layout_path else resolved_layout

        rep.section("Environment")
        rep.fact("generated", datetime.datetime.now().isoformat(timespec="seconds"))
        rep.fact("export folder", root)
        rep.fact("python", sys.version.split()[0])

        layout = audit_layout(rep, layout_file, strict=strict)
        audit_models(rep, models, layout, strict=strict)
        audit_textures(rep, textures, layout, models_dir=models)
        audit_sidecars(rep, root, models, layout, lods=lods)
        if godot_project:
            audit_godot_project(rep, godot_project)
    except Exception as exc:
        rep.error("diagnostic encountered an unexpected error (%s)" % exc)

    text = rep.render()
    target = out_path or os.path.join(root, "ue2g_report.txt")
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        rep.written_to = target
    except Exception as e:
        rep.written_to = None
        text += "\n\n(Could not write report to %s: %s)" % (target, e)
    return text, rep


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_dir", help="folder containing models/, textures/, level_layout.json")
    ap.add_argument("--godot-project", help="optional: the Godot project root to cross-check")
    ap.add_argument("-o", "--out", help="write the report here (default: <export_dir>/ue2g_report.txt)")
    args = ap.parse_args()

    if not os.path.isdir(args.export_dir):
        print("No such folder: %s" % args.export_dir)
        return 2

    text, rep = diagnose(args.export_dir, godot_project=args.godot_project,
                         strict=True, out_path=args.out)
    print(text)
    if getattr(rep, "written_to", None):
        print("\nReport written to: %s" % rep.written_to)
        print("Send that file over and it explains the export on its own.")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
