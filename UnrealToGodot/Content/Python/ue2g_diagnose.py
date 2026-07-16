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

    actors = data.get("actors", [])
    meshes = data.get("meshes", {})
    rep.fact("format_version", data.get("format_version", "(absent)"))
    rep.fact("level_name", data.get("level_name", "(absent)"))
    rep.fact("actors", len(actors))
    rep.fact("mesh entries", len(meshes))
    if not actors:
        rep.error("layout contains zero actors -- nothing will be imported.")

    # Which v2 feature sections this export actually carries. A section that is
    # empty when the level clearly has that feature means its export was either
    # switched off in the dock or failed silently.
    def _count(value):
        if isinstance(value, (list, dict)):
            return len(value)
        return "present" if value else "none"

    rep.line("  feature sections:")
    for key in ("lights", "decals", "landscapes", "foliage", "navigation",
                "post_process", "height_fog", "sky_light"):
        if key in data:
            rep.line("    %-22s %s" % (key, _count(data.get(key))))

    # The importer must never compose actor * relative; it relies on this key.
    missing_world = 0
    for actor in actors:
        for comp in actor.get("components", []):
            if not comp.get("godot_world_transform"):
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
    for key, mesh in meshes.items():
        for mat in mesh.get("materials", []):
            params = mat.get("parameters", {}) or {}
            for pname in UNIT_RANGE_PARAMS:
                val = params.get(pname)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if val < 0.0 or val > 1.0:
                        bad_scalars[pname] += 1
                        bad_examples.setdefault(pname, (key, val))
            albedo = params.get("albedo_color")
            if isinstance(albedo, list) and any(
                    isinstance(c, (int, float)) and c > 1.0 for c in albedo[:3]):
                tinted += 1

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
    """Reads every .gltf: image URIs, baked-texture leftovers, missing targets."""
    rep.section("Models (*.gltf)")
    if not os.path.isdir(models_dir):
        msg = ("no models folder at %s -- the mesh export is a SEPARATE action "
               "from the layout export; running one does not run the other."
               % models_dir)
        if strict:
            rep.error(msg)
        else:
            rep.warn(msg)
        return
    gltfs = sorted(f for f in os.listdir(models_dir) if f.lower().endswith(".gltf"))
    rep.fact("glTF files", len(gltfs))
    if not gltfs:
        rep.error("models/ contains no .gltf files.")
        return

    stray_png = [f for f in os.listdir(models_dir) if f.lower().endswith(".png")]
    if stray_png:
        rep.warn("%d PNG(s) sit inside models/ (e.g. %s). With baking disabled "
                 "there should be none; these are leftovers from an older "
                 "export and mean this folder was not cleared."
                 % (len(stray_png), stray_png[0]))

    baked_hits, bare_uri, missing_target, no_texture = [], [], [], []
    uri_count = 0
    for name in gltfs:
        full = os.path.join(models_dir, name)
        try:
            with open(full, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            rep.error("%s is not readable as glTF JSON (%s)" % (name, e))
            continue
        images = doc.get("images", [])
        if not images:
            no_texture.append(name)
        for img in images:
            uri = img.get("uri")
            if not uri or uri.startswith("data:"):
                continue
            uri_count += 1
            if any(uri.endswith(sfx) for sfx in BAKED_SUFFIXES) and "/" not in uri:
                baked_hits.append((name, uri))
            elif "/" not in uri:
                bare_uri.append((name, uri))
            target = os.path.normpath(os.path.join(models_dir, uri))
            if not os.path.isfile(target):
                missing_target.append((name, uri))

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

    if bare_uri:
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

    # Cross-check the layout against what was actually exported.
    if layout:
        available = {os.path.splitext(f)[0] for f in gltfs}
        wanted = set()
        for actor in layout.get("actors", []):
            for comp in actor.get("components", []):
                key = comp.get("mesh_key") or comp.get("mesh_name")
                if key:
                    wanted.add(key)
        absent = sorted(wanted - available)
        if absent:
            rep.error("%d mesh(es) referenced by the layout have no .gltf "
                      "(e.g. %s). These import as MISSING_ placeholders -- the "
                      "two exports are out of sync; re-run the mesh export."
                      % (len(absent), ", ".join(absent[:5])))
        else:
            rep.ok("every mesh referenced by the layout has a .gltf")


def audit_textures(rep, tex_dir):
    """Sizes and dimensions -- predicts the Godot import segfault before it happens."""
    rep.section("Textures")
    if not os.path.isdir(tex_dir):
        rep.warn("no textures folder at %s -- models will import untextured." % tex_dir)
        return
    files = [f for f in os.listdir(tex_dir) if os.path.isfile(os.path.join(tex_dir, f))]
    total = sum(os.path.getsize(os.path.join(tex_dir, f)) for f in files)
    rep.fact("texture files", len(files))
    rep.fact("total size", human_bytes(total))

    oversized = []
    for f in files:
        if not f.lower().endswith(".png"):
            continue
        dims = png_size(os.path.join(tex_dir, f))
        if dims and max(dims) >= TEXTURE_DIM_WARN:
            oversized.append((f, dims))
    if oversized:
        rep.fact("textures at/above %dpx" % TEXTURE_DIM_WARN, len(oversized))
        biggest = max(oversized, key=lambda t: t[1][0] * t[1][1])
        rep.fact("largest", "%s (%dx%d)" % (biggest[0], biggest[1][0], biggest[1][1]))

    if total > TOTAL_TEXTURE_BYTES_WARN or len(oversized) > 20:
        # Deliberately does not suggest the exporter's own "Max Texture
        # Resolution": that sets max_texture_size, which drives the cooked
        # texture, while the PNG exporter writes the source art. Verified on
        # UE 5.7 -- the exported PNG is 4096x4096 either way. The cap that
        # works is Godot's.
        rep.warn("This texture set is large enough to crash Godot's importer: "
                 "its WebP packer runs out of memory and segfaults partway "
                 "through, leaving a half-imported project that looks like a "
                 "toolchain bug. Set 'Texture size limit' in the Godot "
                 "importer dock (e.g. 1024) -- it caps textures already in the "
                 "project as well as new ones.")
    else:
        rep.ok("texture set is within a size Godot imports reliably")


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
        baked = [f for f in os.listdir(models_dir)
                 if any(f.endswith(sfx) for sfx in BAKED_SUFFIXES)]
        if baked:
            rep.error("%d baked texture PNG(s) still sit in the project's models/ "
                      "folder (e.g. %s). These are from an older export; delete "
                      "them or Godot keeps importing them."
                      % (len(baked), baked[0]))
        gltfs = [f for f in os.listdir(models_dir) if f.lower().endswith(".gltf")]
        rep.fact("glTFs in project", len(gltfs))

        # The check that matters: a .gltf's uri is resolved against its OWN
        # folder, and the transfer splits models/ from textures/. An export
        # whose uris describe the export folder's layout instead of the
        # project's breaks here and nowhere else.
        broken = []
        for name in gltfs:
            try:
                with open(os.path.join(models_dir, name), encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:
                continue
            for img in doc.get("images", []):
                uri = img.get("uri")
                if not uri or uri.startswith("data:"):
                    continue
                if not os.path.isfile(os.path.normpath(os.path.join(models_dir, uri))):
                    broken.append((name, uri))
        if broken:
            name, uri = broken[0]
            rep.error("%d image URI(s) in the project's models/ do not resolve "
                      "(e.g. '%s' in %s -- Godot looks for it at res://models/%s "
                      "and reports \"Can't open file from path\"). The .gltf "
                      "references textures beside itself, but the transfer puts "
                      "them in res://textures/. Re-export: the transfer now "
                      "rewrites these to ../textures/."
                      % (len(broken), uri, name, os.path.basename(uri)))
        elif gltfs:
            rep.ok("every glTF in the project resolves its textures")

    cache = os.path.join(project_dir, ".godot")
    if os.path.isdir(cache):
        rep.line("  note    a .godot/ cache exists. If models were re-exported, "
                 "delete it -- Godot trusts the cache and will not notice that "
                 "a .gltf's textures changed.")


def _has_gltf(path):
    return any(f.lower().endswith(".gltf") for f in _listdir(path))


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
    and only falls back to level_layout.json when the level is unnamed."""
    for name in ("level_layout.json",):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    matches = sorted(f for f in _listdir(root) if f.lower().endswith("_layout.json"))
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


def diagnose(export_dir, godot_project=None, strict=True, out_path=None):
    """Audits an export and writes the report. Returns (report_text, Report).

    This is the entry point the exporter calls, so it must never raise: a
    diagnostic that breaks the thing it diagnoses is worse than none.
    """
    root, models, textures, layout_path = resolve_paths(export_dir)

    rep = Report()
    rep.section("Environment")
    rep.fact("generated", datetime.datetime.now().isoformat(timespec="seconds"))
    rep.fact("export folder", root)
    rep.fact("python", sys.version.split()[0])

    layout = audit_layout(rep, layout_path, strict=strict)
    audit_models(rep, models, layout, strict=strict)
    audit_textures(rep, textures)
    if godot_project:
        audit_godot_project(rep, godot_project)

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
