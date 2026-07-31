"""Tests the texture export path that used to crash the Unreal editor outright.

Forcing AssetExportTask.exporter = TextureExporterPNG() bypassed the engine's
SupportsObject() check, so a Texture2D with float source art (imported from
.exr) reached UTextureExporterGeneric::ExportBinary and tripped its
check(SupportsTexture(Texture)) assert -- an editor-killing crash, not an
exception. These checks pin the fix: the exporter is never forced, an
unsupported texture does not stop the rest of the batch, and it is reported.

Stubs the `unreal` module so the real exporter code runs outside the engine.
"""
import os
import sys
import tempfile
import types

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class _Texture(object):
    def __init__(self, name, writable=True):
        self._name = name
        self.writable = writable

    def get_name(self):
        return self._name


class _Texture2D(_Texture):
    pass


class _TextureCube(_Texture):
    pass


class _AssetExportTask(object):
    def __init__(self):
        self.object = None
        self.filename = None
        self.automated = False
        self.prompt = True
        self.replace_identical = False
        self.exporter = None


class _Exporter(object):
    """Stands in for the engine's task runner.

    A texture the PNG exporter cannot write produces no file -- which is exactly
    what the engine does once it is allowed to pick the exporter itself. The
    engine also keeps going after a failed task, so this does too.
    """
    forced_exporters = []

    @staticmethod
    def run_asset_export_tasks(tasks):
        ok = True
        for task in tasks:
            _Exporter.forced_exporters.append(task.exporter)
            if getattr(task.object, "writable", False):
                with open(task.filename, "wb") as f:
                    f.write(b"\x89PNG")
            else:
                ok = False
        return ok


class _TextureExporterPNG(object):
    pass


u.Texture = _Texture
u.Texture2D = _Texture2D
u.TextureCube = _TextureCube
u.AssetExportTask = _AssetExportTask
u.Exporter = _Exporter
u.TextureExporterPNG = _TextureExporterPNG
u.log = lambda *a: None
u.log_error = lambda *a: None

WARNINGS = []
u.log_warning = lambda msg, *a: WARNINGS.append(str(msg))
sys.modules["unreal"] = u

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))

import ue2g_common as C

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


tmp = tempfile.mkdtemp(prefix="ue2g_tex_")

print("\n=== 1. A float-source texture no longer kills the batch ===")
_Exporter.forced_exporters = []
textures = [
    _Texture2D("T_Concrete_1_N"),
    _Texture2D("T_Concrete_2_N", writable=False),   # imported from .exr
    _Texture2D("T_Concrete_3_N", writable=False),   # imported from .exr
    _Texture2D("T_Metal_A_BC"),
]
res = C.export_textures_to_png(textures, tmp)

check("supported textures still export",
      sorted(res["exported"]) == ["T_Concrete_1_N", "T_Metal_A_BC"], res["exported"])
check("unsupported textures are reported, not fatal",
      sorted(res["unsupported"]) == ["T_Concrete_2_N", "T_Concrete_3_N"], res["unsupported"])
check("supported files really landed on disk",
      os.path.isfile(os.path.join(tmp, "T_Concrete_1_N.png"))
      and os.path.isfile(os.path.join(tmp, "T_Metal_A_BC.png")))
check("a texture after the unsupported one still exported "
      "(the batch is not abandoned)",
      "T_Metal_A_BC" in res["exported"])

print("\n=== 2. The exporter is never forced (this is the crash) ===")
check("every task left AssetExportTask.exporter unset",
      _Exporter.forced_exporters and all(e is None for e in _Exporter.forced_exporters),
      _Exporter.forced_exporters)

print("\n=== 3. Task flags the engine needs for an unattended export ===")
_Exporter.forced_exporters = []
seen = {}


def _capture(tasks):
    for t in tasks:
        seen[t.object.get_name()] = (t.automated, t.prompt, t.replace_identical, t.filename)
    return _Exporter.run_asset_export_tasks(tasks)


real_runner = _Exporter.run_asset_export_tasks
u.Exporter = types.SimpleNamespace(run_asset_export_tasks=_capture)
C.export_textures_to_png([_Texture2D("T_Flags")], tmp)
u.Exporter = _Exporter
flags = seen.get("T_Flags")
check("automated=True, prompt=False, replace_identical=True",
      flags[:3] == (True, False, True), flags)
check("filename is <dir>/<texture name>.png",
      flags[3] == os.path.join(tmp, "T_Flags.png"), flags[3])

print("\n=== 4. Non-2D textures are filtered out before any task ===")
_Exporter.forced_exporters = []
res = C.export_textures_to_png([_TextureCube("EpicQuadPanorama"), _Texture2D("T_Ok"), None], tmp)
check("cubemap is reported as unsupported", res["unsupported"] == ["EpicQuadPanorama"], res)
check("cubemap never became an export task", len(_Exporter.forced_exporters) == 1)
check("None entries are ignored", res["exported"] == ["T_Ok"], res)

print("\n=== 5. skip_existing reuses what is already on disk ===")
_Exporter.forced_exporters = []
res = C.export_textures_to_png([_Texture2D("T_Concrete_1_N"), _Texture2D("T_Fresh")],
                               tmp, skip_existing=True)
check("already-written texture is reused, not re-encoded",
      res["reused"] == ["T_Concrete_1_N"], res)
check("only the missing one was queued", len(_Exporter.forced_exporters) == 1)
check("the missing one exported", res["exported"] == ["T_Fresh"], res)

print("\n=== 6. Skipped textures are named in the log ===")
del WARNINGS[:]
C.log_texture_export_result(
    {"exported": ["T_Ok"], "reused": [], "unsupported": ["T_Concrete_2_N"]}, tmp)
check("a warning is emitted for skipped textures", len(WARNINGS) == 1, WARNINGS)
check("the warning names the texture", "T_Concrete_2_N" in WARNINGS[0], WARNINGS)
check("the warning explains the .exr/.hdr cause", ".exr" in WARNINGS[0], WARNINGS)

del WARNINGS[:]
C.log_texture_export_result({"exported": ["T_Ok"], "reused": [], "unsupported": []}, tmp)
check("no warning when everything exported", WARNINGS == [], WARNINGS)

print("\n=== 6b. A name map keeps colliding texture names apart ===")
# Two packs shipping a T_Concrete_D both wrote T_Concrete_D.png: last write won,
# and every material naming that texture then bound whichever art survived.
name_tmp = tempfile.mkdtemp(prefix="ue2g_texnames_")
tex_a = _Texture2D("T_Concrete_D")
tex_b = _Texture2D("T_Concrete_D")
res = C.export_textures_to_png([tex_a, tex_b], name_tmp,
                               name_map={tex_a: "T_Concrete_D_aaaa1111",
                                         tex_b: "T_Concrete_D_bbbb2222"})
check("both colliding textures were written",
      sorted(res["exported"]) == ["T_Concrete_D_aaaa1111", "T_Concrete_D_bbbb2222"], res)
check("two separate files exist on disk",
      os.path.isfile(os.path.join(name_tmp, "T_Concrete_D_aaaa1111.png"))
      and os.path.isfile(os.path.join(name_tmp, "T_Concrete_D_bbbb2222.png")))

unmapped = _Texture2D("T_Un mapped!")
res = C.export_textures_to_png([unmapped], name_tmp, name_map={})
check("a texture missing from the map falls back to its sanitized asset name",
      res["exported"] == ["T_Un_mapped"], res)

print("\n=== 7. Empty input is a no-op ===")
res = C.export_textures_to_png([], tmp)
check("empty texture list returns empty buckets",
      res == {"exported": [], "reused": [], "unsupported": []}, res)

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL TEXTURE EXPORT CHECKS PASSED")
