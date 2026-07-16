"""Terminal entry point for the export diagnostic.

    python tools/ue2g_diagnose.py <export_dir> [--godot-project <dir>]

Run this from a SHELL (PowerShell, cmd, bash) -- not from Unreal's Python
console, which resolves a bare command as a script filename relative to the
engine's binaries folder.

You normally do not need this at all: the exporter runs the same audit
automatically and leaves ue2g_report.txt next to the export. Use this to
re-check a folder later, or on a machine with no engine installed.

The implementation lives in the plugin's Python folder so that Unreal can
import it directly; this file only puts it on the path.
"""
import os
import sys

_PLUGIN_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "UnrealToGodot", "Content", "Python")

if not os.path.isdir(_PLUGIN_PYTHON):
    sys.exit("Cannot find the plugin's Python folder at:\n  %s\n"
             "Run this from a checkout of the repository." % _PLUGIN_PYTHON)

sys.path.insert(0, _PLUGIN_PYTHON)

import ue2g_diagnose  # noqa: E402  (path must be set up first)

if __name__ == "__main__":
    sys.exit(ue2g_diagnose.main())
