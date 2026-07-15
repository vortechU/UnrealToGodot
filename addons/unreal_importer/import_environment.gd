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
# - Exposure bias (EV) -> tonemap_exposure = 2^bias
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")


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

	# Sky / ambient
	if (sky_light is Dictionary) or has_atmosphere:
		env.background_mode = Environment.BG_SKY
		var sky := Sky.new()
		sky.sky_material = ProceduralSkyMaterial.new()
		env.sky = sky
		env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
		if sky_light is Dictionary:
			env.ambient_light_energy = clampf(Common.get_num(sky_light, "intensity", 1.0), 0.0, 16.0)

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
		if settings.get("exposure_bias") != null:
			env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
			env.tonemap_exposure = pow(2.0, Common.get_num(settings, "exposure_bias", 0.0))
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
	Common.add_owned_child(root, world_env, scene_owner)
	return 1


func _apply_decals(decals, root: Node, scene_owner: Node, options: Dictionary, warnings: PackedStringArray) -> int:
	if decals == null or not (decals is Array) or decals.is_empty():
		return 0
	var folders := [options.get("textures_folder", ""), options.get("models_folder", "")]
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
		decal.sorting_offset = Common.get_num(entry, "sort_order", 0.0)

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
