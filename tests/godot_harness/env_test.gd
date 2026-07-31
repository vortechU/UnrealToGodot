extends SceneTree

const EnvImporter = preload("res://addons/unreal_importer/import_environment.gd")

var failures: Array = []

func check(label: String, ok: bool, detail: String = "") -> void:
	if ok:
		print("  PASS  ", label)
	else:
		print("  FAIL  ", label, "  ", detail)
		failures.append(label)

func build(settings: Dictionary) -> WorldEnvironment:
	var data := {
		"post_process": [{"unbound": true, "priority": 0.0, "settings": settings}],
		"height_fog": {"fog_density": 0.02, "fog_height_falloff": 0.2, "color": [0.6, 0.7, 0.8]},
		"sky_light": null, "has_sky_atmosphere": false,
	}
	var root := Node3D.new()
	var imp = EnvImporter.new()
	var warnings := PackedStringArray()
	imp._apply_environment(data, root, null, warnings)
	for c in root.get_children():
		if c is WorldEnvironment:
			return c
	return null

func _init():
	print("=== ENVIRONMENT IMPORT CHECKS ===")

	# 1. The TreatmentStation shape: exposure LOCKED at 3.0 cd/m^2, nothing else.
	var we := build({"exposure_min_brightness": 3.0, "exposure_max_brightness": 3.0})
	check("locked: WorldEnvironment created", we != null)
	if we:
		var e := we.environment
		check("locked: ACES tonemapper set", e.tonemap_mode == Environment.TONE_MAPPER_ACES,
			str(e.tonemap_mode))
		check("locked: tonemap_exposure = 0.18/3*2^1 = 0.12",
			abs(e.tonemap_exposure - 0.12) < 1e-5, str(e.tonemap_exposure))
		check("locked: no auto exposure attached", we.camera_attributes == null)
		check("locked: fog 0.02 -> 0.01", abs(e.fog_density - 0.01) < 1e-6, str(e.fog_density))

	# 2. A real adaptation range -> Godot auto exposure, sensitivities swapped.
	we = build({"exposure_min_brightness": 0.5, "exposure_max_brightness": 4.0,
		"exposure_bias": 0.0, "exposure_speed_up": 3.0})
	check("range: WorldEnvironment created", we != null)
	if we:
		var ca := we.camera_attributes
		check("range: CameraAttributesPractical attached", ca is CameraAttributesPractical,
			str(ca))
		if ca is CameraAttributesPractical:
			var p := ca as CameraAttributesPractical
			check("range: auto exposure enabled", p.auto_exposure_enabled)
			check("range: min_sensitivity = 100/4.0 = 25",
				abs(p.auto_exposure_min_sensitivity - 25.0) < 1e-4,
				str(p.auto_exposure_min_sensitivity))
			check("range: max_sensitivity = 100/0.5 = 200",
				abs(p.auto_exposure_max_sensitivity - 200.0) < 1e-4,
				str(p.auto_exposure_max_sensitivity))
			check("range: UE speed 3.0 -> Godot default 0.5",
				abs(p.auto_exposure_speed - 0.5) < 1e-4, str(p.auto_exposure_speed))
		check("range: tonemap_exposure stays 2^0 = 1",
			abs(we.environment.tonemap_exposure - 1.0) < 1e-6,
			str(we.environment.tonemap_exposure))

	# 3. Bias only -> unchanged legacy behaviour, but now with a real tonemapper.
	we = build({"exposure_bias": 0.5})
	if we:
		check("bias-only: tonemap_exposure = 2^0.5",
			abs(we.environment.tonemap_exposure - sqrt(2.0)) < 1e-5,
			str(we.environment.tonemap_exposure))
		check("bias-only: no auto exposure", we.camera_attributes == null)

	# 4. An ungraded volume must STILL get a non-linear tonemapper.
	we = build({})
	if we:
		check("empty grade: still ACES, not linear",
			we.environment.tonemap_mode == Environment.TONE_MAPPER_ACES,
			str(we.environment.tonemap_mode))
		check("empty grade: exposure untouched at 1.0",
			abs(we.environment.tonemap_exposure - 1.0) < 1e-6,
			str(we.environment.tonemap_exposure))

	print("============================================")
	if failures.size() > 0:
		print("FAILURES (", failures.size(), "): ", failures)
		quit(1)
	else:
		print("ALL ENVIRONMENT CHECKS PASSED")
		quit(0)
