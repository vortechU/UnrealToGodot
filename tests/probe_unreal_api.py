"""Runs INSIDE Unreal (headless) to verify the glTF export option names the
exporter now sets actually exist on this engine version.

The texture bug came from set_editor_property("export_materials", False) --
a property that does not exist -- raising inside a shared try block and silently
skipping every option after it. This proves which names are real.
"""
import unreal

unreal.log("=== GLTF OPTION PROBE START ===")
unreal.log("Engine version: %s" % unreal.SystemLibrary.get_engine_version())

opts = unreal.GLTFExportOptions()

for name in [
    "export_materials",          # the bogus one that caused the bug
    "adjust_normalmaps",
    "export_vertex_colors",
    "export_animation_sequences",
    "bake_material_inputs",      # the real bake switch
    "texture_image_format",      # the real image-file switch
    "default_level_of_detail",
]:
    try:
        value = opts.get_editor_property(name)
        unreal.log("PROP OK      %-28s = %r" % (name, value))
    except Exception as e:
        unreal.log("PROP MISSING %-28s -> %s" % (name, str(e).splitlines()[0]))

# Confirm the enum members the exporter reaches for actually resolve.
for enum_name, member in [("GLTFMaterialBakeMode", "DISABLED"),
                          ("GLTFTextureImageFormat", "NONE")]:
    enum_type = getattr(unreal, enum_name, None)
    if enum_type is None:
        unreal.log("ENUM MISSING %s" % enum_name)
        continue
    value = getattr(enum_type, member, None)
    unreal.log("ENUM %-24s.%-10s -> %r" % (enum_name, member, value))

# Finally, apply them exactly the way the exporter does and read back.
try:
    opts.set_editor_property("bake_material_inputs", unreal.GLTFMaterialBakeMode.DISABLED)
    opts.set_editor_property("texture_image_format", unreal.GLTFTextureImageFormat.NONE)
    unreal.log("APPLIED bake=%r image_format=%r" % (
        opts.get_editor_property("bake_material_inputs"),
        opts.get_editor_property("texture_image_format")))
except Exception as e:
    unreal.log("APPLY FAILED: %s" % str(e))

unreal.log("=== GLTF OPTION PROBE END ===")
