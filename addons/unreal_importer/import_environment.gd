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
# - UE exponential height fog density (~0.02 default) -> Godot fog_density * 0.5
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
		light.visible = bool(entry.get("visible", true))

		var radius := Common.get_num(entry, "attenuation_radius_m", 10.0)
		if light is OmniLight3D:
			(light as OmniLight3D).omni_range = maxf(0.01, radius)
		elif light is SpotLight3D:
			var spot := light as SpotLight3D
			spot.spot_range = maxf(0.01, radius)
			var outer := Common.get_num(entry, "outer_cone_angle_deg", 44.0)
			var inner := Common.get_num(entry, "inner_cone_angle_deg", 0.0)
			spot.spot_angle = clampf(outer, 0.5, 89.9)
			# Sharper falloff the closer the inner cone is to the outer cone
			if outer > 0.0 and inner > 0.0 and inner < outer:
				spot.spot_angle_attenuation = clampf(outer / maxf(outer - inner, 0.5), 0.5, 8.0)

		Common.add_owned_child(container, light, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _apply_environment(data: Dictionary, root: Node, scene_owner: Node, warnings: PackedStringArray) -> int:
	var volumes = data.get("post_process", [])
	var fog = data.get("height_fog")
	var sky_light = data.get("sky_light")
	var has_atmosphere := bool(data.get("has_sky_atmosphere", false))

	var best: Dictionary = {}
	if volumes is Array:
		for vol in volumes:
			if not (vol is Dictionary):
				continue
			if not bool(vol.get("unbound", false)):
				continue
			if best.is_empty() or Common.get_num(vol, "priority", 0.0) > Common.get_num(best, "priority", 0.0):
				best = vol

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
	if (sky_light is Dictionary) or has_atmosphere:
		env.background_mode = Environment.BG_SKY
		var sky := Sky.new()
		sky.sky_material = ProceduralSkyMaterial.new()
		env.sky = sky
		env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
		if sky_light is Dictionary:
			if sky_light.get("color") != null:
				env.ambient_light_color = Common.color_from_array(sky_light.get("color"), Color.WHITE)
			env.ambient_light_energy = clampf(Common.get_num(sky_light, "intensity", 1.0), 0.0, 16.0)
	else:
		# No sky: an explicit black background rather than whatever clear colour the
		# project happens to carry. Matches tscn_writer.py's else-branch.
		env.background_mode = Environment.BG_COLOR
		env.background_color = Color(0, 0, 0, 1)

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
		env.fog_density = clampf(Common.get_num(fog, "fog_density", 0.02) * 0.5, 0.0, 1.0)
		env.fog_light_color = Common.color_from_array(fog.get("color"), Color(0.62, 0.7, 0.8))
		env.fog_height_density = clampf(Common.get_num(fog, "fog_height_falloff", 0.2), 0.0, 1.0)

	var world_env := WorldEnvironment.new()
	world_env.name = "UnrealEnvironment"
	world_env.environment = env
	if camera_attributes != null:
		world_env.camera_attributes = camera_attributes
	Common.add_owned_child(root, world_env, scene_owner)
	return 1


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
	var bias_scale := pow(2.0, bias)

	var min_raw = settings.get("exposure_min_brightness")
	var max_raw = settings.get("exposure_max_brightness")
	if min_raw == null or max_raw == null:
		# Bias-only (or nothing): the pre-existing behaviour, deliberately kept.
		if has_bias:
			env.tonemap_exposure = bias_scale
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
		decal.size = Vector3(maxf(d_size.x, 0.01), maxf(d_size.y, 0.01), maxf(d_size.z, 0.01))
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
			_bind_decal_texture(decal, textures, "albedo", folders, Decal.TEXTURE_ALBEDO)
			_bind_decal_texture(decal, textures, "normal", folders, Decal.TEXTURE_NORMAL)
			_bind_decal_texture(decal, textures, "orm", folders, Decal.TEXTURE_ORM)
			_bind_decal_texture(decal, textures, "emission", folders, Decal.TEXTURE_EMISSION)
		if decal.get_texture(Decal.TEXTURE_ALBEDO) == null:
			warnings.append("Decal '%s': no albedo texture found on disk." % decal.name)

		Common.add_owned_child(container, decal, scene_owner)
		count += 1

	if count == 0:
		container.queue_free()
	return count


func _bind_decal_texture(decal: Decal, textures: Dictionary, key: String, folders: Array, slot: int) -> void:
	var tex_name := Common.get_str(textures, key, "")
	if tex_name == "":
		return
	var path := Common.find_texture_path(folders, tex_name)
	if path == "":
		return
	var tex := Common.load_texture_file(path)
	if tex:
		decal.set_texture(slot, tex)


func _find_world_environment(root: Node) -> WorldEnvironment:
	if root is WorldEnvironment:
		return root
	for child in root.get_children():
		if child is WorldEnvironment:
			return child
	return null


func _avg3(arr: Array) -> float:
	var total := 0.0
	var n := 0
	for i in mini(arr.size(), 3):
		if arr[i] is float or arr[i] is int:
			total += float(arr[i])
			n += 1
	return (total / n) if n > 0 else 1.0
