@tool
extends RefCounted

# ==============================================================================
# Deforms an instanced glTF mesh along an Unreal SplineMeshComponent's cubic
# Hermite spline (start_pos, start_tangent, end_pos, end_tangent, scale, roll).
#
# All spline vectors in the JSON are already converted into glTF mesh-local
# space ((ux, uz, uy) * 0.01), matching the vertex coordinates of the mesh.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")


static func deform_instance(instanced_mesh: Node, spline_data: Dictionary) -> bool:
	"""Replaces each MeshInstance3D's mesh with a spline-deformed ArrayMesh copy.
	Returns true if at least one mesh surface was deformed."""
	if instanced_mesh == null or spline_data.is_empty():
		return false

	var p0 := Common.vec3_from_array(spline_data.get("start_pos"), Vector3.ZERO)
	var m0 := Common.vec3_from_array(spline_data.get("start_tangent"), Vector3.RIGHT)
	var p1 := Common.vec3_from_array(spline_data.get("end_pos"), Vector3.RIGHT)
	var m1 := Common.vec3_from_array(spline_data.get("end_tangent"), Vector3.RIGHT)

	var s0_arr = spline_data.get("start_scale", [1.0, 1.0])
	var s1_arr = spline_data.get("end_scale", [1.0, 1.0])
	var s0 := Vector2(
		float(s0_arr[0]) if (s0_arr is Array and s0_arr.size() > 0) else 1.0,
		float(s0_arr[1]) if (s0_arr is Array and s0_arr.size() > 1) else 1.0)
	var s1 := Vector2(
		float(s1_arr[0]) if (s1_arr is Array and s1_arr.size() > 0) else 1.0,
		float(s1_arr[1]) if (s1_arr is Array and s1_arr.size() > 1) else 1.0)

	var roll0 := Common.get_num(spline_data, "start_roll_rad", 0.0)
	var roll1 := Common.get_num(spline_data, "end_roll_rad", 0.0)
	var fwd_axis := Common.get_str(spline_data, "forward_axis", "x").to_lower()
	var up_ref := Common.vec3_from_array(spline_data.get("up_dir"), Vector3.UP)
	if up_ref.length_squared() < 1e-6:
		up_ref = Vector3.UP
	else:
		up_ref = up_ref.normalized()

	var instances: Array[MeshInstance3D] = []
	_collect_meshes(instanced_mesh, instances)
	var deformed_any := false

	for mi in instances:
		if mi.mesh == null or mi.mesh.get_surface_count() == 0:
			continue
		var aabb := mi.mesh.get_aabb()
		var min_a: float
		var max_a: float
		match fwd_axis:
			"y":
				min_a = aabb.position.y
				max_a = aabb.end.y
			"z":
				min_a = aabb.position.z
				max_a = aabb.end.z
			_:
				min_a = aabb.position.x
				max_a = aabb.end.x
		var span := max_a - min_a
		if absf(span) < 1e-5:
			span = 1.0

		var out_mesh := ArrayMesh.new()
		for surf_idx in mi.mesh.get_surface_count():
			var mdt := MeshDataTool.new()
			var src_mesh: ArrayMesh
			if mi.mesh is ArrayMesh:
				src_mesh = mi.mesh as ArrayMesh
			else:
				src_mesh = ArrayMesh.new()
				src_mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, mi.mesh.surface_get_arrays(surf_idx))
			if mdt.create_from_surface(src_mesh, surf_idx if mi.mesh is ArrayMesh else 0) != OK:
				continue

			for vi in mdt.get_vertex_count():
				var v := mdt.get_vertex(vi)
				var along: float
				var lateral_u: float
				var lateral_v: float
				match fwd_axis:
					"y":
						along = v.y
						lateral_u = v.x
						lateral_v = v.z
					"z":
						along = v.z
						lateral_u = v.x
						lateral_v = v.y
					_:
						along = v.x
						lateral_u = v.z
						lateral_v = v.y

				var t := clampf((along - min_a) / span, 0.0, 1.0)
				var pos := _hermite_pos(p0, m0, p1, m1, t)
				var tan_vec := _hermite_tangent(p0, m0, p1, m1, t)
				if tan_vec.length_squared() < 1e-8:
					tan_vec = (p1 - p0)
				if tan_vec.length_squared() < 1e-8:
					tan_vec = Vector3.RIGHT
				var fwd := tan_vec.normalized()

				var ref := up_ref
				if absf(fwd.dot(ref)) > 0.995:
					ref = Vector3.FORWARD if absf(fwd.dot(Vector3.FORWARD)) < 0.9 else Vector3.RIGHT
				var right := fwd.cross(ref).normalized()
				var up := right.cross(fwd).normalized()

				# Apply smoothstep-interpolated roll around forward axis
				var st := t * t * (3.0 - 2.0 * t)
				var roll := lerpf(roll0, roll1, st)
				if absf(roll) > 1e-6:
					var cr := cos(roll)
					var sr := sin(roll)
					var r2 := right * cr + up * sr
					var u2 := -right * sr + up * cr
					right = r2
					up = u2

				var sc := s0.lerp(s1, st)
				var deformed := pos + right * (lateral_u * sc.x) + up * (lateral_v * sc.y)
				mdt.set_vertex(vi, deformed)

				var n := mdt.get_vertex_normal(vi)
				if n.length_squared() > 1e-8:
					var n_fwd: float
					var n_u: float
					var n_v: float
					match fwd_axis:
						"y":
							n_fwd = n.y; n_u = n.x; n_v = n.z
						"z":
							n_fwd = n.z; n_u = n.x; n_v = n.y
						_:
							n_fwd = n.x; n_u = n.z; n_v = n.y
					var dn := (fwd * n_fwd + right * n_u + up * n_v).normalized()
					mdt.set_vertex_normal(vi, dn)

			mdt.commit_to_surface(out_mesh)
			var mat := mi.get_active_material(surf_idx)
			if mat != null:
				out_mesh.surface_set_material(out_mesh.get_surface_count() - 1, mat)

		if out_mesh.get_surface_count() > 0:
			mi.mesh = out_mesh
			deformed_any = true

	if deformed_any:
		instanced_mesh.set_meta("unreal_spline_mesh", true)
	return deformed_any


static func _hermite_pos(p0: Vector3, m0: Vector3, p1: Vector3, m1: Vector3, t: float) -> Vector3:
	var t2 := t * t
	var t3 := t2 * t
	return (2.0 * t3 - 3.0 * t2 + 1.0) * p0 + (t3 - 2.0 * t2 + t) * m0 \
		+ (-2.0 * t3 + 3.0 * t2) * p1 + (t3 - t2) * m1


static func _hermite_tangent(p0: Vector3, m0: Vector3, p1: Vector3, m1: Vector3, t: float) -> Vector3:
	var t2 := t * t
	return (6.0 * t2 - 6.0 * t) * p0 + (3.0 * t2 - 4.0 * t + 1.0) * m0 \
		+ (-6.0 * t2 + 6.0 * t) * p1 + (3.0 * t2 - 2.0 * t) * m1


static func _collect_meshes(node: Node, out: Array[MeshInstance3D]) -> void:
	if node is MeshInstance3D:
		out.append(node as MeshInstance3D)
	for child in node.get_children():
		_collect_meshes(child, out)
