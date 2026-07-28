"""Layout regression test for the Unreal-side exporter GUI.

Builds the real UnrealToGodotApp window with `unreal` and the exporter modules
stubbed out, then asserts the properties that were broken before the scroll
rework: the window used to open at a fixed 880px -- taller than the usable area
of a 1080p screen -- with every section in one long non-scrolling column, so the
Godot integration section and the status line were simply unreachable.

Skips itself (exit 0) when there is no display to open a window on.
"""
import os
import sys
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PY_DIR = os.path.join(os.path.dirname(TESTS_DIR), "UnrealToGodot", "Content", "Python")
sys.path.insert(0, PY_DIR)

try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
except Exception as e:                                   # no display / no tkinter
    print("SKIP: no usable Tk display (%s: %s)" % (type(e).__name__, e))
    sys.exit(0)

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")
u.log = u.log_warning = u.log_error = lambda *a, **k: None
u.register_slate_post_tick_callback = lambda cb: object()
u.unregister_slate_post_tick_callback = lambda h: None
u.Paths = type("Paths", (), {"project_dir": staticmethod(lambda: "C:/fake/Project/")})
u.SystemLibrary = type("SystemLibrary", (),
                       {"get_project_directory": staticmethod(lambda: "C:/fake/Project/")})
sys.modules["unreal"] = u

# The GUI only imports these; it does not call into them during layout.
for name in ("export_static_meshes_to_gltf", "export_level_to_json", "ue2g_diagnose"):
    sys.modules.setdefault(name, types.ModuleType(name))

import unreal_to_godot_gui as G

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        FAIL.append(name)


app = G.UnrealToGodotApp("C:/fake/Project")
root = app.root
root.update_idletasks()
root.update()

print("\n=== 1. The window fits the screen it opens on ===")
screen_h = root.winfo_screenheight()
win_h = root.winfo_height()
check("window height leaves room for title bar + taskbar", win_h <= screen_h - 100,
      "window %d, screen %d" % (win_h, screen_h))

print("\n=== 2. The body scrolls, and reaches the controls that were cut off ===")
canvas = app.canvas
region = canvas.bbox("all")
check("canvas has a scroll region", region is not None)
content_h = (region[3] - region[1]) if region else 0
view_h = canvas.winfo_height()
check("scroll region spans the whole body", content_h > view_h * 0.9,
      "content %d, viewport %d" % (content_h, view_h))

# Scroll to the bottom: the Godot auto-import controls must come into view.
canvas.yview_moveto(1.0)
root.update_idletasks()
root.update()
for label, widget in (("Godot Project Path entry", app.godot_path_entry),
                      ("Auto-transfer checkbox", app.auto_transfer_cb)):
    offset = widget.winfo_rooty() - canvas.winfo_rooty()
    check("%s is on screen at the bottom of the scroll" % label,
          0 <= offset <= view_h, "y=%d, viewport 0..%d" % (offset, view_h))

if content_h > view_h:
    check("scrollbar appears while the content overflows", app.scrollbar.winfo_ismapped())

print("\n=== 3. The status bar is pinned, not the last row of a long column ===")
# It must sit BELOW the scrolling body (so it is a footer, not just another row
# that happens to be visible), and stay on screen at every scroll position.
canvas_bottom = (canvas.winfo_rooty() - root.winfo_rooty()) + canvas.winfo_height()
status_top = app.status_lbl.winfo_rooty() - root.winfo_rooty()
check("status bar sits below the scroll area, not inside it",
      status_top >= canvas_bottom - 2, "status y=%d, canvas ends %d" % (status_top, canvas_bottom))
for pos in (0.0, 0.5, 1.0):
    canvas.yview_moveto(pos)
    root.update_idletasks()
    offset = app.status_lbl.winfo_rooty() - root.winfo_rooty()
    check("status bar on screen at scroll %.1f" % pos, 0 <= offset <= root.winfo_height(),
          "y=%d of %d" % (offset, root.winfo_height()))

app.show_status("Export check: 3 problem(s) found -- see "
                "C:/a/quite/long/path/to/an/export/folder/ue2g_report.txt", "error")
root.update_idletasks()
check("a long status message wraps instead of being clipped",
      app.status_lbl.winfo_reqheight() > 20, app.status_lbl.winfo_reqheight())
check("status colour tracks the message type", app.status_lbl.cget("fg") == "#ef4444")

print("\n=== 4. Wheel scrolling and a squeezed window ===")


class _Wheel:
    def __init__(self, delta=0, num=None):
        self.delta, self.num = delta, num


try:
    for ev in (_Wheel(120), _Wheel(-120), _Wheel(0, 4), _Wheel(0, 5), _Wheel(-3)):
        app._on_mousewheel(ev)          # Windows, X11 and macOS event shapes
    check("wheel handler accepts every platform's event shape", True)
except Exception as e:
    check("wheel handler accepts every platform's event shape", False, repr(e))

root.geometry("500x400")
root.update_idletasks()
root.update()
offset = app.status_lbl.winfo_rooty() - root.winfo_rooty()
check("status bar survives a 400px-tall window", 0 <= offset <= root.winfo_height(),
      "y=%d of %d" % (offset, root.winfo_height()))

app.is_closed = True
root.destroy()

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL EXPORTER GUI CHECKS PASSED")
