extends SceneTree

const EnvImporter = preload("res://addons/unreal_importer/import_environment.gd")

var failures: Array = []

func check(label: String, ok: bool, detail: String = "") -> void:
	if ok:
		print("  PASS  ", label)
	else:
		print("  FAIL  ", label, "  ", detail)
		failures.append(label)

func build(settings: Dictionary, extra := {}) -> WorldEnvironment:
	var data := {
		"post_process": [{"unbound": true, "priority": 0.0, "settings": settings}],
		"height_fog": {"fog_density": 0.02, "fog_height_falloff": 0.2, "color": [0.6, 0.7, 0.8]},
		"sky_light": null, "has_sky_atmosphere": false,
	}
	data.merge(extra, true)
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
		check("locked: fog 0.02 -> 0.000961 (UE's own transmittance)",
			abs(e.fog_density - 0.02 * 0.1 * log(2.0) * log(2.0)) < 1e-6, str(e.fog_density))
		check("locked: no distance-independent height fog", e.fog_height_density == 0.0)
		check("locked: fog color is converted from linear",
			e.fog_light_color.is_equal_approx(Color(0.6, 0.7, 0.8).linear_to_srgb())
			and e.fog_light_energy == 1.0, str(e.fog_light_color))
		check("locked: shallow fog barely covers the sky",
			abs(e.fog_sky_affect - (1.0 - exp(-0.2))) < 1e-5, str(e.fog_sky_affect))

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

	# 3b. Gain and gamma brighten the exposure (Downtown_Alley: 0.432, 1.2, 1.15).
	we = build({"exposure_min_brightness": 0.432, "exposure_max_brightness": 0.432,
		"exposure_bias": 0.0, "gain": [1.2, 1.2, 1.2, 1.0], "gamma": [1.15, 1.15, 1.15, 1.0]})
	if we:
		check("grade: gain and gamma fold into the exposure",
			abs(we.environment.tonemap_exposure - 0.18 / 0.432 * 1.2 * pow(0.18, 1.0 / 1.15 - 1.0)) < 1e-4,
			str(we.environment.tonemap_exposure))

	# 4. An ungraded volume must STILL get a non-linear tonemapper.
	we = build({})
	if we:
		check("empty grade: still ACES, not linear",
			we.environment.tonemap_mode == Environment.TONE_MAPPER_ACES,
			str(we.environment.tonemap_mode))
		check("empty grade: exposure untouched at 1.0",
			abs(we.environment.tonemap_exposure - 1.0) < 1e-6,
			str(we.environment.tonemap_exposure))

	# 5. Ambient: the sky measured in Unreal, and SDFGI where Unreal uses Lumen.
	check("no Lumen: SDFGI stays off", not we.environment.sdfgi_enabled)
	we = build({"gi_method": "lumen"}, {
		"height_fog": {"fog_density": 0.17, "fog_height_falloff": 2.0, "color": [1, 1, 1], "start_distance_m": 5.0},
		"sky_light": {"intensity": 6.0, "color": [1.0, 0.5, 1.0],
			"gradient": {"top": [0.06, 0.085, 0.1], "horizon": [0.45, 0.55, 0.62], "ground": [0, 0, 0], "falloff": 10.0}}})
	var se := we.environment
	var mat := se.sky.sky_material as ShaderMaterial
	check("sky: the captured gradient is the sky",
		mat != null and mat.get_shader_parameter("top").is_equal_approx(Vector3(0.06, 0.085, 0.1)))
	check("sky: intensity and tint scale the light, not the visible sky", mat != null and
		mat.get_shader_parameter("light_scale").is_equal_approx(Vector3(6.0, 3.0, 6.0))
		and se.ambient_light_energy == 1.0)
	check("sky: the captured sky is not fogged twice", se.fog_sky_affect == 0.0)
	check("lumen: SDFGI on", se.sdfgi_enabled)
	check("lumen: reflections reach as far as Lumen's", se.ssr_enabled and se.ssr_fade_out == 0.0)
	check("fog: begins at UE's start distance",
		se.fog_mode == Environment.FOG_MODE_DEPTH and se.fog_depth_begin == 5.0, str(se.fog_mode))
	for d in [10.0, 25.0, 60.0, 140.0]:
		var ours: float = pow(smoothstep(se.fog_depth_begin, se.fog_depth_end, d), se.fog_depth_curve) * se.fog_density
		var ue := 1.0 - exp(-0.17 * 0.0480453 * (d - 5.0))
		check("fog: matches UE's at %d m" % d, abs(ours - ue) < 0.05, "%f vs %f" % [ours, ue])
	we = build({"gi_method": "other"}, {"gi_method": "lumen"})
	check("lumen: the volume's method wins over the project's", not we.environment.sdfgi_enabled)

	print("============================================")
	if failures.size() > 0:
		print("FAILURES (", failures.size(), "): ", failures)
		quit(1)
	else:
		print("ALL ENVIRONMENT CHECKS PASSED")
		quit(0)
