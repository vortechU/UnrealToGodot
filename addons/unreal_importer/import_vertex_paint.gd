@tool
extends RefCounted

# ==============================================================================
# Puts Unreal's per-placement vertex paint on a mesh. The paint belongs to one placed
# component, so no mesh file carries it; the layout does ("vertex_paint", see
# docs/SCHEMA_V2.md), and the shader of a painted material reads it as COLOR.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")

# Painted meshes and vertex matches, shared between identical placements. Lives on the
# instance, so it is scoped to one import run.
var _meshes: Dictionary = {}
var _matches: Dictionary = {}


func apply(instanced_mesh: Node, paint_key: String, points, colors_base64) -> int:
	"""Replaces each MeshInstance3D's mesh with a painted copy; returns how many."""
	if not (points is Array) or not (colors_base64 is String):
		return 0
	var colors := Marshalls.base64_to_raw(colors_base64)
	if points.is_empty() or colors.size() * 3 != points.size() * 4:
		return 0
	var painted := 0
	var pending: Array[Node] = [instanced_mesh]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		pending.append_array(node.get_children())
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var mi := node as MeshInstance3D
			var key := "%d|%s" % [mi.mesh.get_instance_id(), colors_base64]
			if not _meshes.has(key):
				_meshes[key] = _painted_copy(mi.mesh, paint_key, points, colors)
			# Surface overrides live on the instance and are kept.
			mi.mesh = _meshes[key]
			painted += 1
	return painted


func _painted_copy(source: Mesh, paint_key: String, points: Array, colors: PackedByteArray) -> ArrayMesh:
	# shortcut: the copy has no automatic Godot LODs; run ImporterMesh.generate_lods()
	# if a pack paints dense meshes.
	var out := ArrayMesh.new()
	for surface in source.get_surface_count():
		var arrays := source.surface_get_arrays(surface)
		var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var match_key := "%s|%d|%d" % [paint_key, source.get_instance_id(), surface]
		if not _matches.has(match_key):
			_matches[match_key] = _nearest(verts, points)
		var nearest: PackedInt32Array = _matches[match_key]
		var painted := PackedColorArray()
		painted.resize(verts.size())
		for i in verts.size():
			var at := nearest[i] * 4
			painted[i] = Color8(colors[at], colors[at + 1], colors[at + 2], colors[at + 3])
		arrays[Mesh.ARRAY_COLOR] = painted
		out.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
		out.surface_set_material(surface, source.surface_get_material(surface))
	return out


func _nearest(verts: PackedVector3Array, points: Array) -> PackedInt32Array:
	"""For each vertex, the paint point at its position, or the closest one (a LOD)."""
	var at := {}
	var positions := PackedVector3Array()
	for i in range(0, points.size(), 3):
		var p := Vector3(points[i], points[i + 1], points[i + 2])
		at[Vector3i((p * 1000.0).round())] = positions.size()
		positions.append(p)
	var result := PackedInt32Array()
	result.resize(verts.size())
	for v in verts.size():
		var index: int = at.get(Vector3i((verts[v] * 1000.0).round()), -1)
		if index < 0:
			var best := INF
			for i in positions.size():
				var d := positions[i].distance_squared_to(verts[v])
				if d < best:
					best = d
					index = i
		result[v] = index
	return result
