---
description: Audit one exported feature end-to-end (Unreal -> schema -> Godot), fix what's broken, verify against the real engines
argument-hint: <feature> (e.g. decals, lights, foliage, collision, post-process, landscape)
---

Audit how **$ARGUMENTS** travels from Unreal to Godot through this toolchain. Find
the bugs, fix them, add the improvements that make the feature actually faithful,
and prove it against the real engines rather than by inspection.

Work through the phases in order. Do not skip the probing phase — every serious
bug found this way so far came from an assumption that the probe disproved.

## 1. Map the path

Read every stage the feature passes through, and note where each field is born,
transformed, written, and consumed:

- `UnrealToGodot/Content/Python/export_*.py` — the exporter that reads the UE API
- `UnrealToGodot/Content/Python/ue2g_common.py` — shared conversion helpers
- `docs/SCHEMA_V2.md` — the authoritative contract; treat any divergence from it
  as a bug in whichever side is wrong, and say which
- **Both** consumers, which are independent implementations of that contract:
  - `addons/unreal_importer/import_*.gd` — the runtime Godot addon
  - `UnrealToGodot/Content/Python/tscn_writer.py` — the direct `.tscn` writer

## 2. Probe both engines — never recall an API

Recall is how this project shipped a light curve that was 625x off. Both engines
are installed and scriptable; ask them.

**Unreal** (property names, CDO defaults, class hierarchy, enum members):

```bash
cd "V:/Gamedev/UnrealProjects/convertAssets" && "C:/Program Files/Epic Games/UE_5.7/Engine/Binaries/Win64/UnrealEditor-Cmd.exe" "V:/Gamedev/UnrealProjects/convertAssets/convertAssets.uproject" -run=pythonscript -script="<scratchpad>/probe.py" -unattended -nopause -nosplash
```

Output goes to `V:/Gamedev/UnrealProjects/convertAssets/Saved/Logs/convertAssets.log`
under `LogPython`, never stdout — and `unreal.log` only prefixes the FIRST line of a
multi-line string, so log one field per line. Read defaults off
`unreal.get_default_object(SomeClass)`, and check `issubclass` for anything you
plan to `isinstance`.

**Unreal C++ source** ships with the install and is the last word on any formula
(unit conversions, colour handling, how a property actually reaches the renderer):
`C:/Program Files/Epic Games/UE_5.7/Engine/Source/Runtime/`. Prefer the `ComputeX`
/ `GetX` implementation in `Private/` over any recollection of what it does. A
`BlueprintPure` static can often be called straight from Python to cross-check
your port of it.

**Godot** (real member names, types and defaults for the installed version):

```bash
"V:/Apps/Godot_v4.7.1/Godot_v4.7.1-stable_win64.exe" --headless --doctool "<scratchpad>/gddoc"
```

then read `<scratchpad>/gddoc/doc/classes/<Class>.xml`. Descriptions come out
empty — fetch the web docs for prose — but the member list and `default=` are
exact. Check inherited classes too; the property you want is often on the base.

## 3. Hunt these specific failure modes

They have all shipped in this repo at least once:

- **Actor-class dispatch dropping components.** `isinstance(actor, unreal.FooActor)`
  silently loses every Foo riding on a Blueprint prop — which is where a dressed
  level keeps most of them — and exports only the first when an actor has several.
  Scan `get_components_by_class` instead, name extras `"<actor>_<component>"`, and
  run the scan BEFORE any branch that `continue`s.
- **Wrong transform source.** `actor.get_actor_transform()` equals the component's
  world transform only when the component is the root. Use the component's own.
- **Unit and axis errors.** Check cm→m, half-angle vs full angle, half-extent vs
  full size, degrees vs radians, and which local axis each engine treats as
  "forward". Verify a converted transform numerically, not by reading it.
- **Colour space.** UE `FColor` is sRGB bytes and the engine decodes it via
  `FLinearColor(FColor)`; UE `FLinearColor` is already linear. The schema promises
  linear. Dividing by 255 is not a conversion.
- **Drift between the two consumers.** Diff `import_*.gd` against `tscn_writer.py`
  property by property. Every difference is a bug in one of them: fallback values,
  clamps, properties one sets and the other doesn't. They have drifted before.
- **Exported but never consumed.** Fields the exporter writes that neither importer
  reads are either dead weight or a missing feature — decide which and act.
- **Defaults that fight the target engine.** A UE default should land on the Godot
  default. If it doesn't, the mapping is miscalibrated, not "a heuristic".
- **Deliberately unmapped is fine — silently unmapped is not.** When a UE concept
  has no faithful Godot equivalent, say so in `SCHEMA_V2.md` with the reason.

## 4. Fix, and extend

Fix the bugs. Then look for UE properties with a real Godot counterpart that
nobody wired up yet — that is where most of the fidelity win lives. Apply
multiplier-style properties *relative* to Godot's own default so an untouched
Unreal asset keeps Godot's stock look. Keep every read defensive
(`ue2g_common.safe_get_prop`, per-item try/except): one unreadable item must never
cost the level its export.

## 5. Prove it

All four layers, not a subset:

- **Unit tests** — `tests/test_materials.py` (exporter logic against a stubbed
  `unreal`), `tests/test_math.py` (conversions). Seed stubs with the CDO defaults
  you just probed, and assert exact numbers, citing where they came from.
- **Cross-implementation pinning** — `tests/test_tscn_writer.py` reads
  `import_environment.gd` as text and pins shared constants and property names
  against it. Extend it so the next drift fails a test.
- **Real Godot** — add fixtures to `tests/godot_harness/build_harness.py` and
  checks to `tests/godot_harness/make_test_plugin.py`. This is the only thing that
  proves a Godot property name exists and accepts your value.
- **Real Unreal** — a scratchpad smoke script driving the production functions
  against real engine objects, since the unit tests stub `unreal` entirely.

Run:

```bash
python tests/run_tests.py --godot "V:/Apps/Godot_v4.7.1/Godot_v4.7.1-stable_win64.exe"
```

Treat a test you had to loosen as a finding, not a chore. If a check fails,
suspect the test's expected value as readily as the code — verify which is wrong
against the engine before changing either.

## 6. Document

- `docs/SCHEMA_V2.md` — the JSON shape, every new field, the conversion formulas
  with their engine source, and what is deliberately not mapped and why
- The exporter module docstring — the derivation, so the next reader can check it
- `README.md` feature bullet and any caveat it invalidates
- `addons/unreal_importer/importer_dock.gd` — the checkbox tooltip

## 7. Report

Lead with what was actually broken and what it did to real content, then the
additions, then how it was verified. Quantify where you can ("every default light
was 625x too bright"). Flag anything you found but deliberately left alone.
