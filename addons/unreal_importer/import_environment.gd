@tool
extends RefCounted

# ==============================================================================
# Applies exported Unreal lighting, post-process, height fog, sky and decal
# data to the active scene. Consumes the "lights", "post_process", "height_fog",
# "sky_light", "has_sky_atmosphere" and "decals" schema sections.
# See docs/SCHEMA_V2.md.
#
# Conversion heuristics (documented so users can tune light_energy_scale):
# - light_energy = godot_energy (pre-converted by the exporter) * light_energy_scale
# - light_specular = UE SpecularScale * Godot's per-class default (1.0 directional,
#   0.5 omni/spot), so a default UE light keeps Godot's stock look
# - UE FogDensity -> Godot fog_density * FOG_DENSITY_SCALE
# - UE AO radius is exported in cm -> * 0.01 m; UE AO intensity (0..1) -> ssao_intensity * 2.0
# - Exposure: see _apply_exposure below.
#
# These heuristics are duplicated in tscn_writer.py (_build_environment), which
# produces the same WorldEnvironment for the .tscn export path. The two drifted
# apart once already -- fog was 10x denser on the .tscn side -- so any change
# here must be mirrored there, and tests/test_tscn_writer.py pins the shared
# constants below against it.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")
const TextureLimit = preload("res://addons/unreal_importer/texture_import_limit.gd")

# Godot's fog is e^-(density per m). Unreal's, measured on UE 5.8, is e^-(ln 2 * ln 2 *
# FogDensity / 1000 per cm): one ln 2 from its base-2 exponential, one from its line
# integral. 0.1 * ln 2 * ln 2.
const FOG_DENSITY_SCALE := 0.0480453

# Fog with a start distance uses Godot's depth fog, whose curve (0.5) follows Unreal's
# exponential within a few percent up to this optical depth, where it stops growing.
const FOG_DEPTH_RANGE := 1.2

# Twin of tscn_writer.py's SKY_SHADER: the sky measured in Unreal. A shader because
# ProceduralSkyMaterial cannot light the scene differently from how it looks.
const SKY_SHADER := """shader_type sky;

uniform vec3 top;
uniform vec3 horizon;
uniform vec3 ground;
uniform float falloff;
uniform vec3 light_scale;

void sky() {
	float elevation = asin(clamp(EYEDIR.y, 0.0, 1.0)) / (0.5 * PI);
	COLOR = mix(top, horizon, pow(1.0 - elevation, falloff));
	// Lighting only: Unreal scales the sky light and replaces its lower half, not the sky you see.
	if (AT_CUBEMAP_PASS) {
		COLOR = (EYEDIR.y >= 0.0 ? COLOR : ground) * light_scale;
	}
}
"""

# Fallback attenuation radius (m) for a light whose AttenuationRadius could not
# be read. UE's own default is 1000 cm, so that is the honest stand-in.
const DEFAULT_ATTENUATION_M := 10.0

# Godot rejects a spot_angle of 90 or more; UE's OuterConeAngle clamps at 89.9.
const MAX_SPOT_ANGLE_DEG := 89.9

# Godot renders linear by default; Unreal never does. Without this every import
# arrived flat and washed out no matter what the grade said -- and the old code
# only set a tonemapper when an exposure BIAS happened to be overridden, which
# most volumes do not do. ACES rather than AGX: UE5's default film curve is
# ACES-derived, so it is the closer match (AGX is noticeably more desaturating).
const UNREAL_TONEMAP := Environment.TONE_MAPPER_ACES

# UE's PostProcessSettings default for auto_exposure_bias, applied when a volume
# locks its exposure without also overriding the bias.
const UE_DEFAULT_EXPOSURE_BIAS := 1.0

# Scene-referred middle grey. UE's auto exposure drives the average scene
# luminance to this, so a volume locked at L cd/m^2 is asking for an exposure
# multiplier of MIDDLE_GREY / L. With no lock the baseline is 1.0 (auto exposure
# normalises for itself) and only the bias applies -- which is what the previous
# `2^bias` behaviour assumed, so bias-only volumes are unchanged by this.
const MIDDLE_GREY := 0.18

# Luminance (cd/m^2) that maps to ISO 100 when handing UE's adaptation RANGE to
# Godot's auto exposure. Sensitivity is inversely proportional to the target
# luminance, so UE's MIN brightness becomes Godot's MAX sensitivity.
const ISO_REFERENCE := 100.0

# UE's auto_exposure_speed_up/down default to 3.0 and Godot's auto_exposure_speed
# to 0.5; this factor lines the two defaults up so an unremarkable UE setup
# adapts at Godot's normal rate.
const EXPOSURE_SPEED_SCALE := 0.5 / 3.0

# Cropped decal atlas cells, keyed by source path + rect + flips. Lives on the
# instance, so it is scoped to one import run. See _crop_decal_texture.
var _decal_crop_cache: Dictionary = {}

# Minimum projection depth (m) for a decal, before the node's own scale.
#
# Unreal packs author astonishingly thin decal boxes -- SpaceshipInterior's
# Demonstration map runs from 16 mm to 400 mm, with DecalSize left at (1,1,1) cm
# and the actor scale doing all the work. Both engines clip a decal to its box,
# but a 16 mm slab only reaches surfaces within +/-8 mm of the decal's centre
# plane, and sci-fi wall panels have more relief than that. Rendered on a real
# GPU against a plane with 12 mm of relief, the 16 mm box shreds an Exit sign
# into vertical slivers and lands only 2103 px of decal where a 100 mm box lands
# 4503 -- less than half. That shredding, swimming as the camera moves over it,
# is the "decals z-fight constantly" report.
#
# 100 mm is the smallest floor that cleared that test outright. It is a
# deliberate DEVIATION from Unreal, so it is an option rather than a constant:
# raising a decal's depth lets it reach geometry Unreal would have clipped away,
# which on a cluttered prop can project the decal onto a neighbour. Set 0 to
# import Unreal's depths untouched.
#
# The floor applies to the EFFECTIVE depth (size.y * the node's Y scale), not to
# size.y alone: layouts from before v0.5 keep Unreal's scale in the transform, where
# size.y is 0.02 and the scale carries a factor of several hundred.
const DEFAULT_DECAL_MIN_DEPTH_M := 0.10


func apply(data: Dictionary, root: Node, scene_owner: Node, options: Dictionary) -> Dictionary:
	var created := 0
	var warnings := PackedStringArray()
	var energy_scale := float(options.get("light_energy_scale", 1.0))

	if options.get("apply_lights", true):
		created += _apply_lights(data.get("lights", []), root, scene_owner, energy_scale)
	if options.get("apply_environment", true):
		created += _apply_environment(data, root, scene_owner, warnings)
	if options.get("apply_decals", true):
		created += _apply_decals(data.get("decals", []), root, scene_owner, options, warnings)
	if options.get("apply_reflection_probes", true):
		created += _apply_reflection_probes(data.get("reflection_probes", []), root, scene_owner, _uses_lumen(data))
	if options.get("apply_cameras", true):
		created += _apply_cameras(data.get("cameras", []), root, scene_owner)
	if options.get("apply_audio", true):
		created += _apply_audio(data.get("audio", []), root, scene_owner, options)

	return {"created": created, "warnings": warnings}


func _apply_lights(lights, root: Node, scene_owner: Node, energy_scale: float) -> int:
	if lights == null or not (lights is Array) or lights.is_empty():
		return 0
	var container := Node3D.new()
	container.name = "UnrealLights"
	Common.add_owned_child(root, container, scene_owner)

	var count := 0
	for entry in lights:
		if not (entry is Dictionary):
			continue
		var light_type := Common.get_str(entry, "type", "point")
		var light: Light3D
		match light_type:
			"directional":
				light = DirectionalLight3D.new()
			"spot":
				light = SpotLight3D.new()
			"rect":
				light = OmniLight3D.new()
				light.set_meta("unreal_rect_light", true)
			_:
				light = OmniLight3D.new()

		light.name = Common.get_str(entry, "name", "Light")
		light.transform = Common.get_transform_from_dict(entry.get("godot_transform", {}))

		var color := Common.color_from_array(entry.get("color"), Color.WHITE)
		if bool(entry.get("use_temperature", false)):
			var kelvin := Common.get_num(entry, "temperature_kelvin", 6500.0)
			if kelvin > 0.0:
				color = color * Common.kelvin_to_color(kelvin)
		light.light_color = color

		light.light_energy = maxf(0.0, Common.get_num(entry, "godot_energy", 1.0) * energy_scale)
		light.shadow_enabled = bool(entry.get("cast_shadows", true))
		light.light_indirect_energy = Common.get_num(entry, "indirect_intensity", 1.0)
		light.light_volumetric_fog_energy = Common.get_num(entry, "volumetric_scattering", 1.0)
		light.visible = bool(entry.get("visible", true))

		# UE's SpecularScale and Godot's light_specular are both multipliers, but
		# their defaults differ (Godot omni/spot ship at 0.5, directional at 1.0),
		# so it is applied RELATIVE to whatever this light type already defaults
		# to. A UE light left alone therefore keeps Godot's own look, and one an
		# artist dulled to 0 still goes matte.
		light.light_specular *= maxf(0.0, Common.get_num(entry, "specular_scale", 1.0))

		# UE's MaxDrawDistance/MaxDistanceFadeRange, pre-converted to metres.
		var fade_begin = entry.get("distance_fade_begin_m")
		var fade_length = entry.get("distance_fade_length_m")
		if (fade_begin is float or fade_begin is int) and (fade_length is float or fade_length is int):
			light.distance_fade_enabled = true
			light.distance_fade_begin = maxf(float(fade_begin), 0.0)
			light.distance_fade_length = maxf(float(fade_length), 0.01)

		var radius := Common.get_num(entry, "attenuation_radius_m", DEFAULT_ATTENUATION_M)
		# Godot's decay 2 is UE's inverse-square falloff, window included.
		var inverse_square := bool(entry.get("inverse_squared_falloff", true))
		# UE SourceRadius -> Godot light_size, which is what drives soft shadows;
		# it was exported but dropped on the floor before.
		var source_radius := maxf(0.0, Common.get_num(entry, "source_radius_m", 0.0))
		if light is OmniLight3D:
			(light as OmniLight3D).omni_range = maxf(0.01, radius)
			if inverse_square:
				(light as OmniLight3D).omni_attenuation = 2.0
			light.light_size = source_radius
		elif light is SpotLight3D:
			var spot := light as SpotLight3D
			spot.spot_range = maxf(0.01, radius)
			if inverse_square:
				spot.spot_attenuation = 2.0
			spot.light_size = source_radius
			var outer := Common.get_num(entry, "outer_cone_angle_deg", 44.0)
			var inner := Common.get_num(entry, "inner_cone_angle_deg", 0.0)
			spot.spot_angle = clampf(outer, 0.5, MAX_SPOT_ANGLE_DEG)
			spot.spot_angle_attenuation = _spot_softness(inner, outer)
		elif light is DirectionalLight3D:
			var sun := light as DirectionalLight3D
			# UE LightSourceAngle and Godot light_angular_distance are the same
			# quantity in the same unit: the source's angular diameter in degrees.
			sun.light_angular_distance = clampf(
				Common.get_num(entry, "source_angle_deg", 0.0), 0.0, 90.0)
			var shadow_distance := Common.get_num(entry, "shadow_distance_m", 0.0)
			if shadow_distance > 0.0:
				sun.directional_shadow_max_distance = shadow_distance

		# Rect lights have no Godot equivalent, so the panel dimensions ride
		# along as metadata and drive light_size for a plausibly soft shadow.
		var rect_size = entry.get("rect_size_m")
		if rect_size is Array and rect_size.size() >= 2:
			light.set_meta("unreal_rect_size_m", Vector2(float(rect_size[0]), float(rect_size[1])))
			if source_radius <= 0.0:
				light.light_size = maxf(float(rect_size[0]), float(rect_size[1])) * 0.5
		if entry.get("mobility") != null:
			light.set_meta("unreal_mobility", Common.get_str(entry, "mobility", ""))

		Common.add_owned_child(container, light, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _main_post_process(data: Dictionary) -> Dictionary:
	"""The highest-priority unbound volume drives the WorldEnvironment."""
	var best: Dictionary = {}
	var volumes = data.get("post_process", [])
	if volumes is Array:
		for vol in volumes:
			if not (vol is Dictionary):
				continue
			if not bool(vol.get("unbound", false)):
				continue
			if best.is_empty() or Common.get_num(vol, "priority", 0.0) > Common.get_num(best, "priority", 0.0):
				best = vol
	return best


func _uses_lumen(data: Dictionary) -> bool:
	var settings = _main_post_process(data).get("settings")
	var method = settings.get("gi_method") if settings is Dictionary else null
	return str(method if method != null else data.get("gi_method")) == "lumen"


func _apply_environment(data: Dictionary, root: Node, scene_owner: Node, warnings: PackedStringArray) -> int:
	var fog = data.get("height_fog")
	var sky_light = data.get("sky_light")
	var has_atmosphere := bool(data.get("has_sky_atmosphere", false))

	var best := _main_post_process(data)

	var has_any: bool = (not best.is_empty()) or (fog is Dictionary) or (sky_light is Dictionary) or has_atmosphere
	if not has_any:
		return 0

	if _find_world_environment(root) != null:
		warnings.append("A WorldEnvironment already exists in the scene; skipped environment import.")
		return 0

	var env := Environment.new()
	# Unconditional: Unreal never renders with a linear tonemapper, so matching it
	# is not something only a graded level needs.
	env.tonemap_mode = UNREAL_TONEMAP
	var camera_attributes: CameraAttributes = null

	# Sky / ambient
	var gradient = sky_light.get("gradient") if sky_light is Dictionary else null
	if (sky_light is Dictionary) or has_atmosphere:
		env.background_mode = Environment.BG_SKY
		var sky := Sky.new()
		sky.sky_material = _sky_material(sky_light, gradient) if gradient is Dictionary else ProceduralSkyMaterial.new()
		env.sky = sky
		env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
		if sky_light is Dictionary and not (gradient is Dictionary):
			if sky_light.get("color") != null:
				env.ambient_light_color = Common.color_from_array(sky_light.get("color"), Color.WHITE)
			env.ambient_light_energy = clampf(Common.get_num(sky_light, "intensity", 1.0), 0.0, 16.0)
	else:
		# No sky: an explicit black background rather than whatever clear colour the
		# project happens to carry. Matches tscn_writer.py's else-branch.
		env.background_mode = Environment.BG_COLOR
		env.background_color = Color(0, 0, 0, 1)

	# Lumen keeps sky light out of enclosed places; SDFGI is Godot's closest match.
	env.sdfgi_enabled = _uses_lumen(data)
	# Lumen also reflects the scene in wet ground; the default fade stops a few meters out.
	env.ssr_enabled = env.sdfgi_enabled
	if env.ssr_enabled:
		env.ssr_fade_out = 0.0

	# Post-process settings from the winning unbound volume
	if not best.is_empty():
		var settings: Dictionary = best.get("settings", {}) if best.get("settings") is Dictionary else {}
		if settings.get("bloom_intensity") != null:
			env.glow_enabled = true
			env.glow_intensity = Common.get_num(settings, "bloom_intensity", 0.675)
			var threshold := Common.get_num(settings, "bloom_threshold", -1.0)
			if threshold >= 0.0:
				env.glow_hdr_threshold = threshold
		if settings.get("ao_intensity") != null:
			env.ssao_enabled = true
			env.ssao_intensity = clampf(Common.get_num(settings, "ao_intensity", 0.5) * 2.0, 0.0, 16.0)
			if settings.get("ao_radius") != null:
				env.ssao_radius = maxf(0.01, Common.get_num(settings, "ao_radius", 200.0) * 0.01)
		camera_attributes = _apply_exposure(settings, env)
		var white_temp := Common.get_num(settings, "white_temp", 6500.0)
		if absf(white_temp - 6500.0) > 1.0:
			warnings.append("Post-process white balance (%d K) has no Godot setting and was not applied; use an adjustment color-correction LUT." % int(white_temp))
		var saturation = settings.get("saturation")
		var contrast = settings.get("contrast")
		if saturation != null or contrast != null:
			env.adjustment_enabled = true
			if saturation is Array:
				env.adjustment_saturation = clampf(_avg3(saturation), 0.01, 8.0)
			if contrast is Array:
				env.adjustment_contrast = clampf(_avg3(contrast), 0.01, 8.0)
		if settings.get("vignette_intensity") != null:
			warnings.append("Vignette is not supported by Godot's Environment; skipped.")

	# Height fog
	if fog is Dictionary:
		env.fog_enabled = true
		var density := maxf(0.0, Common.get_num(fog, "fog_density", 0.02))
		var rate := density * FOG_DENSITY_SCALE
		var start := Common.get_num(fog, "start_distance_m", 0.0)
		if start > 0.0 and rate > 0.0:
			env.fog_mode = Environment.FOG_MODE_DEPTH
			env.fog_density = 1.0 - exp(-FOG_DEPTH_RANGE)
			env.fog_depth_curve = 0.5
			env.fog_depth_begin = start
			env.fog_depth_end = start + FOG_DEPTH_RANGE / rate
		else:
			env.fog_density = clampf(rate, 0.0, 1.0)
		# An inscattering color above 1 is a brightness, which Godot keeps apart.
		var fog_color = fog.get("color")
		if fog_color is Array and fog_color.size() >= 3:
			var peak := maxf(1.0, maxf(float(fog_color[0]), maxf(float(fog_color[1]), float(fog_color[2]))))
			env.fog_light_energy = peak
			fog_color = [float(fog_color[0]) / peak, float(fog_color[1]) / peak, float(fog_color[2]) / peak]
		env.fog_light_color = Common.color_from_array(fog_color, Color(0.62, 0.7, 0.8))
		# Godot's height fog ignores distance, so UE's falloff cannot map onto it;
		# what it does decide is how much fog lies between the ground and the sky.
		var falloff := Common.get_num(fog, "fog_height_falloff", 0.2)
		env.fog_sky_affect = 1.0 - exp(-2.0 * density / falloff) if falloff > 0.0 else 1.0
		if gradient is Dictionary:
			env.fog_sky_affect = 0.0  # The captured sky has its fog in it.

	var world_env := WorldEnvironment.new()
	world_env.name = "UnrealEnvironment"
	world_env.environment = env
	if camera_attributes != null:
		world_env.camera_attributes = camera_attributes
	Common.add_owned_child(root, world_env, scene_owner)
	return 1


func _sky_material(sky_light: Dictionary, gradient: Dictionary) -> ShaderMaterial:
	var shader := Shader.new()
	shader.code = SKY_SHADER
	var material := ShaderMaterial.new()
	material.shader = shader
	for key in ["top", "horizon", "ground"]:
		material.set_shader_parameter(key, Common.vec3_from_array(gradient.get(key), Vector3.ZERO))
	material.set_shader_parameter("falloff", Common.get_num(gradient, "falloff", 6.67))
	var tint := Common.vec3_from_array(sky_light.get("color"), Vector3.ONE)
	material.set_shader_parameter("light_scale", tint * Common.get_num(sky_light, "intensity", 1.0))
	return material


func _apply_exposure(settings: Dictionary, env: Environment) -> CameraAttributes:
	# Unreal expresses exposure three ways and they compose:
	#   bias                        an EV offset, always in play
	#   min/max brightness equal    exposure LOCKED to that luminance (manual)
	#   min/max brightness differ   auto exposure clamped to that range
	# Returns a CameraAttributes for the third case (Godot models auto exposure
	# there, not on Environment) and null otherwise, since Godot applies no auto
	# exposure at all unless one is attached -- which is exactly what a locked or
	# unspecified UE exposure means.
	var has_bias: bool = settings.get("exposure_bias") != null
	var bias := Common.get_num(settings, "exposure_bias", UE_DEFAULT_EXPOSURE_BIAS)
	var grade := _grade_scale(settings)
	var bias_scale := pow(2.0, bias) * grade

	var min_raw = settings.get("exposure_min_brightness")
	var max_raw = settings.get("exposure_max_brightness")
	if min_raw == null or max_raw == null:
		# Bias-only (or nothing): the pre-existing behaviour, deliberately kept.
		if has_bias or grade != 1.0:
			env.tonemap_exposure = bias_scale if has_bias else grade
		return null

	var min_lum := maxf(Common.get_num(settings, "exposure_min_brightness", 1.0), 0.0001)
	var max_lum := maxf(Common.get_num(settings, "exposure_max_brightness", 1.0), 0.0001)

	if is_equal_approx(min_lum, max_lum):
		# Locked. This is the case that used to vanish entirely.
		env.tonemap_exposure = clampf(MIDDLE_GREY / min_lum * bias_scale, 0.0, 64.0)
		return null

	# A real adaptation range -> Godot's auto exposure. Sensitivity is inverse to
	# target luminance, so the min/max swap sides here.
	env.tonemap_exposure = bias_scale
	var attrs := CameraAttributesPractical.new()
	attrs.auto_exposure_enabled = true
	attrs.auto_exposure_min_sensitivity = clampf(ISO_REFERENCE / max_lum, 0.0, 64000.0)
	attrs.auto_exposure_max_sensitivity = clampf(ISO_REFERENCE / min_lum, 0.0, 64000.0)
	if settings.get("exposure_speed_up") != null:
		attrs.auto_exposure_speed = maxf(
			Common.get_num(settings, "exposure_speed_up", 3.0) * EXPOSURE_SPEED_SCALE, 0.01)
	return attrs


func _apply_decals(decals, root: Node, scene_owner: Node, options: Dictionary, warnings: PackedStringArray) -> int:
	if decals == null or not (decals is Array) or decals.is_empty():
		return 0
	# Same ordered search the material binder uses (import_unreal_layout.
	# find_texture_path). Without the third entry, decal textures sitting in a
	# textures/ folder next to the layout JSON -- i.e. any export that was not
	# auto-transferred into the project -- resolve for meshes but not for decals.
	var folders := [options.get("textures_folder", ""), options.get("models_folder", "")]
	var json_dir := str(options.get("json_dir", ""))
	if json_dir != "":
		folders.append(json_dir.path_join("textures"))
	var container := Node3D.new()
	container.name = "UnrealDecals"
	Common.add_owned_child(root, container, scene_owner)

	var count := 0
	for entry in decals:
		if not (entry is Dictionary):
			continue
		var decal := Decal.new()
		decal.name = Common.get_str(entry, "name", "Decal")
		decal.transform = Common.get_transform_from_dict(entry.get("godot_transform", {}))
		var d_size := Common.vec3_from_array(entry.get("size_m"), Vector3.ONE).abs()
		d_size = Vector3(maxf(d_size.x, 0.01), maxf(d_size.y, 0.01), maxf(d_size.z, 0.01))
		# Thin Unreal boxes shred against surface relief in Godot; see
		# DEFAULT_DECAL_MIN_DEPTH_M. Applied to the effective depth, so the node's
		# own Y scale has to be divided back out.
		var min_depth := float(options.get("decal_min_depth_m", DEFAULT_DECAL_MIN_DEPTH_M))
		if min_depth > 0.0:
			var y_scale: float = absf(decal.transform.basis.get_scale().y)
			if y_scale > 0.0001:
				d_size.y = maxf(d_size.y, min_depth / y_scale)
		decal.size = d_size
		# UE SortOrder and Godot sorting_offset agree on direction: higher draws
		# on top of decals sharing the same spot.
		decal.sorting_offset = Common.get_num(entry, "sort_order", 0.0)
		decal.visible = bool(entry.get("visible", true))
		# UE's DecalColor times the material tint, opacity folded into alpha.
		# Godot multiplies this into the albedo, so white is a no-op.
		decal.modulate = Common.color_from_array(entry.get("modulate"), Color.WHITE)
		# UE fades a decal out by projected screen size; the exporter converted
		# that to a distance and leaves these null when it asked for no fade.
		var fade_begin = entry.get("distance_fade_begin_m")
		var fade_length = entry.get("distance_fade_length_m")
		if (fade_begin is float or fade_begin is int) and (fade_length is float or fade_length is int):
			decal.distance_fade_enabled = true
			decal.distance_fade_begin = maxf(float(fade_begin), 0.0)
			decal.distance_fade_length = maxf(float(fade_length), 0.01)

		var textures = entry.get("textures")
		if textures is Dictionary:
			_bind_decal_texture(decal, textures, "albedo", folders, Decal.TEXTURE_ALBEDO, options)
			_bind_decal_texture(decal, textures, "normal", folders, Decal.TEXTURE_NORMAL, options)
			_bind_decal_texture(decal, textures, "orm", folders, Decal.TEXTURE_ORM, options)
			_bind_decal_texture(decal, textures, "emission", folders, Decal.TEXTURE_EMISSION, options)
		if decal.get_texture(Decal.TEXTURE_ALBEDO) == null:
			warnings.append("Decal '%s': no albedo texture found on disk." % decal.name)

		Common.add_owned_child(container, decal, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _bind_decal_texture(decal: Decal, textures: Dictionary, key: String, folders: Array, slot: int, options: Dictionary = {}) -> void:
	var tex_name := Common.get_str(textures, key, "")
	if tex_name == "":
		return
	var path := Common.find_texture_path(folders, tex_name)
	if path == "":
		return
	var is_normal := (slot == Decal.TEXTURE_NORMAL)
	var tex: Texture2D = null
	if is_normal and not path.begins_with("res://"):
		var mat_helper = options.get("material_helper")
		if mat_helper != null and mat_helper.has_method("_load_normal_texture"):
			tex = mat_helper._load_normal_texture(path, tex_name)
		elif bool(options.get("flip_normal_green", true)):
			var raw_img := Common.load_image_file(path)
			if raw_img != null:
				raw_img.clear_mipmaps()
				tex = Common.texture_from_image(TextureLimit.invert_green_channel(raw_img))
	if tex == null:
		tex = Common.load_texture_file(path)
	if tex == null:
		return
	# Decal packs share one atlas across many materials and crop to a cell in the
	# Unreal material graph (see SCHEMA_V2.md "decals" -> uv_rect). Godot's Decal
	# has no UV transform, and AtlasTexture is no help either -- the decal atlas
	# keys off the texture's RID and ignores the region -- so the crop has to be
	# baked into pixels right here or the decal renders the whole sheet.
	var rect = textures.get("uv_rect")
	if rect is Array and rect.size() == 4:
		var cropped := _crop_decal_texture(
			path, tex, rect,
			bool(textures.get("flip_u", false)),
			bool(textures.get("flip_v", false)),
			is_normal)
		if cropped != null:
			tex = cropped
	decal.set_texture(slot, tex)


func _crop_decal_texture(
		path: String, tex: Texture2D, rect: Array,
		flip_u: bool, flip_v: bool, is_normal: bool = false) -> Texture2D:
	"""One atlas cell of `tex` as a standalone texture, or null if it cannot be cut."""
	# 15 of the sci-fi station's 29 decals share a single cell, and every distinct
	# ImageTexture costs its own slot in Godot's decal atlas, so caching here is
	# what keeps that from becoming 15 copies of the same crop.
	var cache_key := "%s|%s|%s|%s|%s" % [path, str(rect), flip_u, flip_v, is_normal]
	if _decal_crop_cache.has(cache_key):
		return _decal_crop_cache[cache_key]

	var img := tex.get_image()
	if img == null:
		return null
	if img.is_compressed():
		# A VRAM-compressed import cannot be region-copied as-is.
		if img.decompress() != OK:
			return null
	var w := img.get_width()
	var h := img.get_height()
	# UV v and Image row both count from the top, so v maps straight onto y.
	var region := Rect2i(
		int(roundf(float(rect[0]) * w)), int(roundf(float(rect[1]) * h)),
		mini(int(roundf(float(rect[2]) * w)), w), mini(int(roundf(float(rect[3]) * h)), h))
	if region.size.x <= 0 or region.size.y <= 0:
		return null

	# Crops dragged by hand often run a little past the sheet, where Unreal's wrap
	# addressing carries on from the opposite edge. blit_rect clips each copy.
	var out := Image.create_empty(region.size.x, region.size.y, false, img.get_format())
	for wrap in [Vector2i.ZERO, Vector2i(w, 0), Vector2i(0, h), Vector2i(w, h)]:
		out.blit_rect(img, Rect2i(region.position - wrap, region.size), Vector2i.ZERO)
	# A negative Unreal tiling walks the axis backwards, which is a mirror.
	if flip_u:
		out.flip_x()
	if flip_v:
		out.flip_y()
	# Mirroring a tangent-space normal map in pixels flips the tangent/bitangent
	# handedness, so invert the matching normal channel (R for X, G for Y).
	if is_normal and (flip_u or flip_v):
		for py in out.get_height():
			for px in out.get_width():
				var c := out.get_pixel(px, py)
				if flip_u:
					c.r = 1.0 - c.r
				if flip_v:
					c.g = 1.0 - c.g
				out.set_pixel(px, py, c)
	if img.has_mipmaps():
		out.generate_mipmaps()

	var result := Common.save_generated_texture(out, path, cache_key)
	_decal_crop_cache[cache_key] = result
	return result


func _apply_reflection_probes(probes, root: Node, scene_owner: Node, uses_lumen: bool) -> int:
	if probes == null or not (probes is Array) or probes.is_empty():
		return 0
	var container := Node3D.new()
	container.name = "UnrealReflectionProbes"
	Common.add_owned_child(root, container, scene_owner)

	var count := 0
	for entry in probes:
		if not (entry is Dictionary):
			continue
		var probe := ReflectionProbe.new()
		probe.name = Common.get_str(entry, "name", "ReflectionProbe")
		probe.transform = Common.get_transform_from_dict(entry.get("godot_transform", {}))
		var sz := Common.vec3_from_array(entry.get("size_m"), Vector3(10.0, 10.0, 10.0)).abs()
		probe.size = Vector3(maxf(sz.x, 0.1), maxf(sz.y, 0.1), maxf(sz.z, 0.1))
		probe.intensity = clampf(Common.get_num(entry, "intensity", 1.0), 0.0, 16.0)
		probe.box_projection = bool(entry.get("box_projection", true))
		var blend_dist = entry.get("blend_distance_m")
		if (blend_dist is float or blend_dist is int) and "blend_distance" in probe:
			probe.set("blend_distance", maxf(0.0, float(blend_dist)))
		probe.update_mode = ReflectionProbe.UPDATE_ONCE
		# Unreal's captures only reflect; a probe's own ambient would replace SDFGI's.
		if uses_lumen:
			probe.ambient_mode = ReflectionProbe.AMBIENT_DISABLED
		probe.visible = bool(entry.get("visible", true))
		probe.set_meta("unreal_probe_shape", Common.get_str(entry, "shape", "box"))
		Common.add_owned_child(container, probe, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _apply_cameras(cameras, root: Node, scene_owner: Node) -> int:
	if cameras == null or not (cameras is Array) or cameras.is_empty():
		return 0
	var container := Node3D.new()
	container.name = "UnrealCameras"
	Common.add_owned_child(root, container, scene_owner)

	var count := 0
	for entry in cameras:
		if not (entry is Dictionary):
			continue
		var cam := Camera3D.new()
		cam.name = Common.get_str(entry, "name", "Camera")
		cam.transform = Common.get_transform_from_dict(entry.get("godot_transform", {}))
		# Unreal FieldOfView is horizontal; Godot's KEEP_WIDTH interprets fov as horizontal.
		cam.keep_aspect = Camera3D.KEEP_WIDTH
		var proj := Common.get_str(entry, "projection", "perspective").to_lower()
		if proj == "orthogonal":
			cam.projection = Camera3D.PROJECTION_ORTHOGONAL
			cam.size = maxf(0.05, Common.get_num(entry, "ortho_width_m", 5.12))
		else:
			cam.projection = Camera3D.PROJECTION_PERSPECTIVE
			cam.fov = clampf(Common.get_num(entry, "fov_deg", 90.0), 1.0, 179.0)
		cam.near = maxf(0.001, Common.get_num(entry, "near_clip_m", 0.05))
		cam.far = maxf(cam.near + 0.1, Common.get_num(entry, "far_clip_m", 4000.0))
		Common.add_owned_child(container, cam, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _apply_audio(entries, root: Node, scene_owner: Node, options: Dictionary) -> int:
	if entries == null or not (entries is Array) or entries.is_empty():
		return 0
	var container := Node3D.new()
	container.name = "UnrealAudio"
	Common.add_owned_child(root, container, scene_owner)

	var json_dir := str(options.get("json_dir", "res://"))
	var audio_folders := ["res://audio/", json_dir.path_join("audio")]
	var count := 0
	for entry in entries:
		if not (entry is Dictionary):
			continue
		var is_spatial := bool(entry.get("is_spatialized", true))
		var player: Node
		if is_spatial:
			var p3d := AudioStreamPlayer3D.new()
			p3d.transform = Common.get_transform_from_dict(entry.get("godot_transform", {}))
			p3d.max_distance = maxf(0.1, Common.get_num(entry, "attenuation_radius_m", 40.0))
			player = p3d
		else:
			player = AudioStreamPlayer.new()
		player.name = Common.get_str(entry, "name", "Audio")
		player.set("volume_db", clampf(Common.get_num(entry, "volume_db", 0.0), -80.0, 24.0))
		player.set("pitch_scale", clampf(Common.get_num(entry, "pitch_scale", 1.0), 0.01, 4.0))
		player.set("autoplay", bool(entry.get("auto_play", true)))
		var loaded_stream: AudioStream = null
		var sound_file := Common.get_str(entry, "sound_file", "")
		if sound_file != "":
			var stream_path := ""
			for folder in audio_folders:
				var found := Common.find_file_case_insensitive(str(folder), sound_file.get_basename(), [".wav", ".ogg", ".mp3"])
				if found != "":
					stream_path = found
					break
			if stream_path != "":
				if stream_path.begins_with("res://") and ResourceLoader.exists(stream_path):
					var stream = ResourceLoader.load(stream_path)
					if stream is AudioStream:
						loaded_stream = stream as AudioStream
				elif stream_path.to_lower().ends_with(".wav") and ClassDB.class_has_method(&"AudioStreamWAV", &"load_from_file"):
					# Through a Variant: AudioStreamWAV.load_from_file() is a parse error before
					# Godot 4.4, and call("load_from_file") does not reach static methods.
					var wav_cls: Variant = AudioStreamWAV
					var ext_wav = wav_cls.load_from_file(stream_path)
					if ext_wav is AudioStream:
						loaded_stream = ext_wav as AudioStream
		if bool(entry.get("looping", false)):
			player.set_meta("unreal_looping", true)
			if loaded_stream is AudioStreamWAV:
				var wav := (loaded_stream as AudioStreamWAV).duplicate() as AudioStreamWAV
				wav.loop_mode = AudioStreamWAV.LOOP_FORWARD
				# Godot imports WAVs as QOA by default, so count frames from the length,
				# not the bytes: a loop_end left at 0 plays silence.
				if wav.loop_end <= 0:
					wav.loop_end = int(wav.get_length() * wav.mix_rate)
				loaded_stream = wav
		if loaded_stream != null:
			player.set("stream", loaded_stream)
		var sound_name := Common.get_str(entry, "sound_name", "")
		if sound_name != "":
			player.set_meta("unreal_sound_name", sound_name)
		Common.add_owned_child(container, player, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _find_world_environment(root: Node) -> WorldEnvironment:
	if root is WorldEnvironment:
		return root
	for child in root.get_children():
		var found := _find_world_environment(child)
		if found != null:
			return found
	return null


func _spot_softness(inner_deg: float, outer_deg: float) -> float:
	"""Godot fades a spot by 1 - rim^(1/attenuation), Unreal by the squared fraction
	between its cones; this makes both reach half brightness at the same angle."""
	var outer := cos(deg_to_rad(clampf(outer_deg, 0.5, MAX_SPOT_ANGLE_DEG)))
	var inner := maxf(cos(deg_to_rad(maxf(inner_deg, 0.0))), outer)
	var rim := (1.0 - (outer + sqrt(0.5) * (inner - outer))) / (1.0 - outer)
	return clampf(log(rim) / log(0.5), 0.05, 8.0)


func _grade_scale(settings: Dictionary) -> float:
	"""Unreal's color gain and gamma as an exposure factor. Godot has neither; gamma
	is matched at middle grey, where it is a plain multiplier."""
	var scale := 1.0
	var gain = settings.get("gain")
	if gain is Array:
		scale *= maxf(_avg3(gain) * (float(gain[3]) if gain.size() > 3 else 1.0), 0.0)
	var gamma = settings.get("gamma")
	if gamma is Array:
		var power := _avg3(gamma) * (float(gamma[3]) if gamma.size() > 3 else 1.0)
		scale *= pow(MIDDLE_GREY, 1.0 / maxf(power, 0.01) - 1.0)
	return scale


func _avg3(arr: Array) -> float:
	var total := 0.0
	var n := 0
	for i in mini(arr.size(), 3):
		if arr[i] is float or arr[i] is int:
			total += float(arr[i])
			n += 1
	return (total / n) if n > 0 else 1.0
