from __future__ import annotations

import logging
import math

import trimesh
from trimesh.transformations import rotation_matrix

from .geometry import edge_signature, iter_polygon_edges
from .models import (
    OpeningSpec,
    OpeningType,
    Point2D,
    SceneSpec,
    SurfaceSpec,
    WallKind,
    WallSegment,
)
from .scene_layout import _collect_cutaway_wall_ids

LOGGER = logging.getLogger(__name__)


def export_scene_glb(scene_spec: SceneSpec) -> bytes:
    scene = trimesh.Scene()
    openings_by_wall: dict[str, list[OpeningSpec]] = {}
    cutaway_wall_ids = _collect_cutaway_wall_ids(scene_spec.walls, scene_spec.camera)
    trim_edges = _collect_trim_edges(scene_spec.ceilings)

    for surface in scene_spec.floors:
        _add_surface_polygon(scene, surface)

    for surface in scene_spec.ceilings:
        _add_surface_polygon(scene, surface)
        _add_ceiling_edge_trim(scene, surface, trim_edges)

    for opening in scene_spec.openings:
        openings_by_wall.setdefault(opening.wall_id, []).append(opening)

    for wall in scene_spec.walls:
        if wall.wall_id in cutaway_wall_ids:
            continue
        wall_openings = openings_by_wall.get(wall.wall_id, [])
        _add_wall_with_openings(
            scene,
            wall,
            wall_openings,
            thickness=scene_spec.wall_thickness_m,
            height=scene_spec.wall_height_m,
        )
        for opening in wall_openings:
            _add_opening_detail(scene, wall, opening, thickness=scene_spec.wall_thickness_m)

    for furniture in scene_spec.furnitures:
        size = [furniture.size.x, furniture.size.y, furniture.size.z]
        center = [furniture.position.x, furniture.position.y, furniture.position.z]
        _add_box(scene, size, center, furniture.rotation_deg, furniture.furniture_id, _furniture_color(furniture.kind))

    return scene.export(file_type="glb")


def _add_surface_polygon(scene: trimesh.Scene, surface: SurfaceSpec) -> None:
    vertices_top = [[point.x, surface.elevation_m + surface.thickness_m, point.z] for point in surface.polygon]
    vertices_bottom = [[point.x, surface.elevation_m, point.z] for point in surface.polygon]
    polygon_2d = [(point.x, point.z) for point in surface.polygon]
    triangles = _triangulate_polygon_indices(polygon_2d)
    vertices = vertices_top + vertices_bottom
    top_offset = 0
    bottom_offset = len(vertices_top)
    faces = []

    for triangle in triangles:
        faces.append([top_offset + triangle[0], top_offset + triangle[1], top_offset + triangle[2]])
        faces.append([bottom_offset + triangle[2], bottom_offset + triangle[1], bottom_offset + triangle[0]])

    for index in range(len(surface.polygon)):
        next_index = (index + 1) % len(surface.polygon)
        faces.append([index, next_index, bottom_offset + next_index])
        faces.append([index, bottom_offset + next_index, bottom_offset + index])

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.visual.face_colors = _surface_color(surface.material)
    scene.add_geometry(mesh, node_name=surface.surface_id)


def _add_ceiling_edge_trim(
    scene: trimesh.Scene,
    surface: SurfaceSpec,
    trim_edges: set[tuple[tuple[float, float], tuple[float, float]]],
) -> None:
    for index, point in enumerate(surface.polygon):
        next_point = surface.polygon[(index + 1) % len(surface.polygon)]
        if edge_signature(point, next_point) not in trim_edges:
            continue
        _add_wall_piece(
            scene,
            point,
            next_point,
            surface.elevation_m - 0.02,
            0.02,
            0.06,
            f"{surface.surface_id}_trim_{index}",
            [216, 211, 202, 220],
        )


def _add_box(
    scene: trimesh.Scene,
    extents: list[float],
    center: list[float],
    yaw_deg: float,
    node_name: str,
    color: list[int],
) -> None:
    mesh = trimesh.creation.box(extents=extents)
    mesh.visual.face_colors = color
    if yaw_deg:
        mesh.apply_transform(rotation_matrix(math.radians(yaw_deg), [0, 1, 0]))
    mesh.apply_translation(center)
    scene.add_geometry(mesh, node_name=node_name)


def _add_aligned_box(
    scene: trimesh.Scene,
    center: Point2D,
    y: float,
    size_x: float,
    size_y: float,
    size_z: float,
    yaw_deg: float,
    node_name: str,
    color: list[int],
) -> None:
    _add_box(scene, [size_x, size_y, size_z], [center.x, y, center.z], yaw_deg, node_name, color)


def _add_wall_with_openings(
    scene: trimesh.Scene,
    wall: WallSegment,
    openings: list[OpeningSpec],
    *,
    thickness: float,
    height: float,
) -> None:
    if not openings:
        _add_wall_piece(scene, wall.start, wall.end, 0.0, height, thickness, f"{wall.wall_id}_full", _wall_color(wall.kind))
        return

    ordered = sorted(openings, key=lambda opening: _distance_along_wall(wall, opening.center))
    total_length = _wall_length(wall)
    cursor = 0.0

    for opening in ordered:
        center_offset = _distance_along_wall(wall, opening.center)
        start_offset = max(0.0, center_offset - opening.width_m / 2)
        end_offset = min(total_length, center_offset + opening.width_m / 2)

        if start_offset > cursor + 0.05:
            start = _point_at_offset(wall, cursor)
            end = _point_at_offset(wall, start_offset)
            _add_wall_piece(
                scene,
                start,
                end,
                0.0,
                height,
                thickness,
                f"{opening.opening_id}_before",
                _wall_color(wall.kind),
            )

        if opening.kind == OpeningType.WINDOW and opening.base_height_m > 0.1:
            start = _point_at_offset(wall, start_offset)
            end = _point_at_offset(wall, end_offset)
            _add_wall_piece(
                scene,
                start,
                end,
                0.0,
                opening.base_height_m,
                thickness,
                f"{opening.opening_id}_window_bottom",
                _wall_color(wall.kind),
            )

        lintel_height = max(height - opening.top_height_m, 0.12)
        if lintel_height > 0.1:
            start = _point_at_offset(wall, start_offset)
            end = _point_at_offset(wall, end_offset)
            _add_wall_piece(
                scene,
                start,
                end,
                opening.top_height_m,
                lintel_height,
                thickness,
                f"{opening.opening_id}_lintel",
                _wall_color(wall.kind),
            )

        cursor = end_offset

    if cursor < total_length - 0.05:
        start = _point_at_offset(wall, cursor)
        end = _point_at_offset(wall, total_length)
        _add_wall_piece(scene, start, end, 0.0, height, thickness, f"{wall.wall_id}_after", _wall_color(wall.kind))


def _add_wall_piece(
    scene: trimesh.Scene,
    start: Point2D,
    end: Point2D,
    base_height: float,
    piece_height: float,
    thickness: float,
    node_name: str,
    color: list[int],
) -> None:
    length = math.dist((start.x, start.z), (end.x, end.z))
    if length <= 0.05 or piece_height <= 0.05:
        return
    yaw = math.degrees(math.atan2(end.z - start.z, end.x - start.x))
    center_x = (start.x + end.x) / 2
    center_z = (start.z + end.z) / 2
    center_y = base_height + piece_height / 2
    _add_box(
        scene,
        [length, piece_height, thickness],
        [center_x, center_y, center_z],
        yaw,
        node_name,
        color,
    )


def _add_opening_detail(
    scene: trimesh.Scene,
    wall: WallSegment,
    opening: OpeningSpec,
    *,
    thickness: float,
) -> None:
    try:
        if opening.kind == OpeningType.WINDOW:
            _add_window_detail(scene, wall, opening, thickness)
        else:
            _add_door_detail(scene, wall, opening, thickness)
    except Exception:  # noqa: BLE001
        LOGGER.warning("opening detail fallback triggered for %s", opening.opening_id, exc_info=True)
        return


def _add_window_detail(
    scene: trimesh.Scene,
    wall: WallSegment,
    opening: OpeningSpec,
    thickness: float,
) -> None:
    yaw = math.degrees(math.atan2(wall.end.z - wall.start.z, wall.end.x - wall.start.x))
    glass_height = max(0.24, opening.top_height_m - opening.base_height_m - 0.12)
    glass_center_y = opening.base_height_m + glass_height / 2
    glass_width = max(0.36, opening.width_m - 0.1)
    center = opening.center

    _add_aligned_box(
        scene,
        center,
        glass_center_y,
        glass_width,
        glass_height,
        max(0.03, thickness * 0.18),
        yaw,
        f"{opening.opening_id}_glass",
        [162, 196, 214, 110],
    )
    _add_frame_details(scene, center, yaw, opening.width_m, opening.base_height_m, opening.top_height_m, thickness, opening.opening_id)


def _add_door_detail(
    scene: trimesh.Scene,
    wall: WallSegment,
    opening: OpeningSpec,
    thickness: float,
) -> None:
    yaw = math.degrees(math.atan2(wall.end.z - wall.start.z, wall.end.x - wall.start.x))
    center = opening.center
    leaf_width = max(0.35, opening.width_m * 0.48)
    _add_aligned_box(
        scene,
        center,
        opening.top_height_m / 2,
        leaf_width,
        max(1.8, opening.top_height_m - 0.04),
        max(0.03, thickness * 0.16),
        yaw,
        f"{opening.opening_id}_door_leaf",
        [159, 126, 93, 255],
    )
    _add_frame_details(scene, center, yaw, opening.width_m, 0.0, opening.top_height_m, thickness, opening.opening_id)


def _add_frame_details(
    scene: trimesh.Scene,
    center: Point2D,
    yaw_deg: float,
    opening_width: float,
    base_height: float,
    top_height: float,
    thickness: float,
    node_prefix: str,
) -> None:
    yaw = math.radians(yaw_deg)
    along = (math.cos(yaw), math.sin(yaw))
    half_width = opening_width / 2
    jamb_half = max(0.03, thickness * 0.18)
    jamb_depth = max(0.04, thickness * 0.82)
    jamb_height = max(0.24, top_height - base_height)
    side_offsets = (-half_width + jamb_half, half_width - jamb_half)
    for index, offset in enumerate(side_offsets):
        jamb_center = Point2D(
            x=center.x + along[0] * offset,
            z=center.z + along[1] * offset,
        )
        _add_aligned_box(
            scene,
            jamb_center,
            base_height + jamb_height / 2,
            jamb_half * 2,
            jamb_height,
            jamb_depth,
            yaw_deg,
            f"{node_prefix}_jamb_{index}",
            [206, 199, 186, 255],
        )

    lintel_center = Point2D(x=center.x, z=center.z)
    _add_aligned_box(
        scene,
        lintel_center,
        top_height - jamb_half,
        max(0.18, opening_width),
        jamb_half * 2,
        jamb_depth,
        yaw_deg,
        f"{node_prefix}_frame_top",
        [206, 199, 186, 255],
    )


def _distance_along_wall(wall: WallSegment, point: Point2D) -> float:
    return math.dist((wall.start.x, wall.start.z), (point.x, point.z))


def _wall_length(wall: WallSegment) -> float:
    return math.dist((wall.start.x, wall.start.z), (wall.end.x, wall.end.z))


def _point_at_offset(wall: WallSegment, offset: float) -> Point2D:
    length = _wall_length(wall)
    if length <= 0.001:
        return wall.start
    ratio = max(0.0, min(1.0, offset / length))
    return Point2D(
        x=wall.start.x + (wall.end.x - wall.start.x) * ratio,
        z=wall.start.z + (wall.end.z - wall.start.z) * ratio,
    )


def _wall_color(kind: WallKind) -> list[int]:
    if kind == WallKind.OUTER:
        return [229, 222, 213, 255]
    return [214, 207, 198, 255]


def _furniture_color(kind: str) -> list[int]:
    mapping = {
        "bed": [181, 149, 118, 255],
        "sofa": [98, 126, 154, 255],
        "kitchen_counter": [135, 147, 162, 255],
        "vanity": [175, 185, 190, 255],
        "console": [132, 102, 77, 255],
        "storage_cube": [166, 145, 124, 255],
    }
    return mapping.get(kind, [160, 160, 160, 255])


def _surface_color(material: str) -> list[int]:
    mapping = {
        "floor_bedroom": [228, 203, 149, 255],
        "floor_living_room": [237, 226, 204, 255],
        "floor_kitchen": [223, 221, 214, 255],
        "floor_bathroom": [203, 226, 232, 255],
        "floor_corridor": [222, 212, 193, 255],
        "floor_generic": [219, 213, 200, 255],
        "matte_ceiling": [244, 241, 235, 120],
    }
    return mapping.get(material, [222, 218, 210, 255])


def _collect_trim_edges(ceilings: list[SurfaceSpec]) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    counts: dict[tuple[tuple[float, float], tuple[float, float]], int] = {}
    for ceiling in ceilings:
        for start, end in iter_polygon_edges(ceiling.polygon):
            signature = edge_signature(start, end)
            counts[signature] = counts.get(signature, 0) + 1
    return {signature for signature, count in counts.items() if count == 1}


def _triangulate_polygon_indices(points: list[tuple[float, float]]) -> list[list[int]]:
    if len(points) < 3:
        return []

    indices = list(range(len(points)))
    if _signed_area(points) < 0:
        indices.reverse()

    triangles: list[list[int]] = []
    guard = 0
    while len(indices) > 3 and guard < len(points) * len(points):
        ear_found = False
        total = len(indices)
        for local_index in range(total):
            prev_index = indices[(local_index - 1) % total]
            current_index = indices[local_index]
            next_index = indices[(local_index + 1) % total]
            a = points[prev_index]
            b = points[current_index]
            c = points[next_index]
            if not _is_convex(a, b, c):
                continue
            if _contains_any_point(points, indices, prev_index, current_index, next_index):
                continue
            triangles.append([prev_index, current_index, next_index])
            indices.pop(local_index)
            ear_found = True
            break
        if not ear_found:
            break
        guard += 1

    if len(indices) == 3:
        triangles.append([indices[0], indices[1], indices[2]])
    return triangles


def _signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return area / 2.0


def _is_convex(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 0


def _contains_any_point(
    points: list[tuple[float, float]],
    indices: list[int],
    prev_index: int,
    current_index: int,
    next_index: int,
) -> bool:
    triangle = (points[prev_index], points[current_index], points[next_index])
    for index in indices:
        if index in {prev_index, current_index, next_index}:
            continue
        if _point_in_triangle(points[index], *triangle):
            return True
    return False


def _point_in_triangle(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    denominator = ((b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]))
    if abs(denominator) < 1e-6:
        return False
    alpha = ((b[1] - c[1]) * (point[0] - c[0]) + (c[0] - b[0]) * (point[1] - c[1])) / denominator
    beta = ((c[1] - a[1]) * (point[0] - c[0]) + (a[0] - c[0]) * (point[1] - c[1])) / denominator
    gamma = 1.0 - alpha - beta
    return alpha > 1e-6 and beta > 1e-6 and gamma > 1e-6
