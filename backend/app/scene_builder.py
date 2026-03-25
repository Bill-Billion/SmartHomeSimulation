from __future__ import annotations

import math
from typing import Iterable

import trimesh
from trimesh.transformations import rotation_matrix

from .models import (
    CameraSpec,
    FloorplanSpec,
    FurnitureSpec,
    OpeningSpec,
    OpeningType,
    Point2D,
    Point3D,
    RoomSpec,
    RoomType,
    SceneSpec,
    SurfaceSpec,
    WallKind,
    WallSegment,
)


def build_scene_spec(scene_id: str, floorplan: FloorplanSpec) -> SceneSpec:
    floors = []
    ceilings = []
    for room in floorplan.rooms:
        floors.append(
            SurfaceSpec(
                surface_id=f"floor_{room.room_id}",
                room_id=room.room_id,
                polygon=room.polygon,
                elevation_m=0.0,
                thickness_m=0.06,
                material=_room_floor_material(room.room_type),
            )
        )
        ceilings.append(
            SurfaceSpec(
                surface_id=f"ceiling_{room.room_id}",
                room_id=room.room_id,
                polygon=room.polygon,
                elevation_m=floorplan.wall_height_m,
                thickness_m=0.04,
                material="matte_ceiling",
            )
        )

    furnitures = _build_furnitures(floorplan.rooms)
    camera = _build_camera(floorplan.bounds_width_m, floorplan.bounds_depth_m, floorplan.wall_height_m)

    return SceneSpec(
        scene_id=scene_id,
        resource_version="v0.1.1",
        source_type=floorplan.source_type,
        bounds_width_m=floorplan.bounds_width_m,
        bounds_depth_m=floorplan.bounds_depth_m,
        wall_height_m=floorplan.wall_height_m,
        wall_thickness_m=floorplan.wall_thickness_m,
        rooms=floorplan.rooms,
        walls=[*floorplan.outer_walls, *floorplan.inner_walls],
        openings=floorplan.openings,
        floors=floors,
        ceilings=ceilings,
        furnitures=furnitures,
        camera=camera,
        warnings=floorplan.warnings,
    )


def export_scene_glb(scene_spec: SceneSpec) -> bytes:
    scene = trimesh.Scene()
    openings_by_wall: dict[str, list[OpeningSpec]] = {}
    cutaway_wall_ids = _collect_cutaway_wall_ids(scene_spec.walls)

    for surface in scene_spec.floors:
        _add_floor_polygon(scene, surface)

    for opening in scene_spec.openings:
        openings_by_wall.setdefault(opening.wall_id, []).append(opening)

    for wall in scene_spec.walls:
        if wall.wall_id in cutaway_wall_ids:
            continue
        _add_wall_with_openings(
            scene,
            wall,
            openings_by_wall.get(wall.wall_id, []),
            thickness=scene_spec.wall_thickness_m,
            height=scene_spec.wall_height_m,
        )

    for furniture in scene_spec.furnitures:
        size = [furniture.size.x, furniture.size.y, furniture.size.z]
        center = [furniture.position.x, furniture.position.y, furniture.position.z]
        _add_box(scene, size, center, furniture.rotation_deg, furniture.furniture_id, _furniture_color(furniture.kind))

    return scene.export(file_type="glb")


def _collect_cutaway_wall_ids(walls: list[WallSegment]) -> set[str]:
    """预览模型主动去掉朝向相机的两面外墙，让房间构型能直接被看到。"""
    outer_walls = [wall for wall in walls if wall.kind == WallKind.OUTER]
    if not outer_walls:
        return set()

    max_x = max(max(wall.start.x, wall.end.x) for wall in outer_walls)
    max_z = max(max(wall.start.z, wall.end.z) for wall in outer_walls)
    tolerance = 0.18
    cutaway_wall_ids: set[str] = set()

    for wall in outer_walls:
        on_right_edge = abs(wall.start.x - max_x) < tolerance and abs(wall.end.x - max_x) < tolerance
        on_front_edge = abs(wall.start.z - max_z) < tolerance and abs(wall.end.z - max_z) < tolerance
        if on_right_edge or on_front_edge:
            cutaway_wall_ids.add(wall.wall_id)

    return cutaway_wall_ids


def _build_camera(width_m: float, depth_m: float, wall_height_m: float) -> CameraSpec:
    span = max(width_m, depth_m, 6.0)
    return CameraSpec(
        position=Point3D(x=span * 0.22, y=wall_height_m * 3.4, z=span * 0.78),
        target=Point3D(x=0.0, y=0.2, z=0.0),
        fov=38.0,
    )


def _build_furnitures(rooms: Iterable[RoomSpec]) -> list[FurnitureSpec]:
    furnitures: list[FurnitureSpec] = []
    for room in rooms:
        min_x, max_x, min_z, max_z = _polygon_bounds(room.polygon)
        width = max_x - min_x
        depth = max_z - min_z
        center_x = (min_x + max_x) / 2
        center_z = (min_z + max_z) / 2

        if room.room_type == RoomType.BEDROOM:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_bed",
                    room_id=room.room_id,
                    kind="bed",
                    position=Point3D(x=center_x, y=0.32, z=center_z),
                    size=Point3D(x=min(width * 0.55, 2.0), y=0.42, z=min(depth * 0.38, 1.8)),
                    rotation_deg=0.0 if width >= depth else 90.0,
                )
            )
        elif room.room_type == RoomType.LIVING_ROOM:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_sofa",
                    room_id=room.room_id,
                    kind="sofa",
                    position=Point3D(x=center_x, y=0.45, z=center_z),
                    size=Point3D(x=min(width * 0.45, 2.4), y=0.9, z=min(depth * 0.22, 0.95)),
                    rotation_deg=0.0,
                )
            )
        elif room.room_type == RoomType.KITCHEN:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_counter",
                    room_id=room.room_id,
                    kind="kitchen_counter",
                    position=Point3D(x=min_x + width * 0.3, y=0.45, z=max_z - depth * 0.18),
                    size=Point3D(x=min(width * 0.55, 2.2), y=0.9, z=0.65),
                    rotation_deg=0.0,
                )
            )
        elif room.room_type == RoomType.BATHROOM:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_vanity",
                    room_id=room.room_id,
                    kind="vanity",
                    position=Point3D(x=center_x, y=0.45, z=center_z),
                    size=Point3D(x=0.8, y=0.9, z=0.55),
                    rotation_deg=0.0,
                )
            )
        elif room.room_type == RoomType.CORRIDOR:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_console",
                    room_id=room.room_id,
                    kind="console",
                    position=Point3D(x=center_x, y=0.45, z=center_z),
                    size=Point3D(x=min(width * 0.35, 1.4), y=0.9, z=0.35),
                    rotation_deg=0.0 if width >= depth else 90.0,
                )
            )
        else:
            furnitures.append(
                FurnitureSpec(
                    furniture_id=f"furniture_{room.room_id}_storage",
                    room_id=room.room_id,
                    kind="storage_cube",
                    position=Point3D(x=center_x, y=0.3, z=center_z),
                    size=Point3D(x=min(width * 0.25, 0.9), y=0.6, z=min(depth * 0.25, 0.9)),
                    rotation_deg=0.0,
                )
            )
    return furnitures


def _polygon_bounds(polygon: list[Point2D]) -> tuple[float, float, float, float]:
    xs = [point.x for point in polygon]
    zs = [point.z for point in polygon]
    return min(xs), max(xs), min(zs), max(zs)


def _add_floor_polygon(scene: trimesh.Scene, surface: SurfaceSpec) -> None:
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


def _add_wall_with_openings(
    scene: trimesh.Scene,
    wall: WallSegment,
    openings: list[OpeningSpec],
    *,
    thickness: float,
    height: float,
) -> None:
    if not openings:
        _add_wall_piece(scene, wall.start, wall.end, 0.0, height, thickness, f"{wall.wall_id}_full")
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
            _add_wall_piece(scene, start, end, 0.0, height, thickness, f"{opening.opening_id}_before")

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
            )

        cursor = end_offset

    if cursor < total_length - 0.05:
        start = _point_at_offset(wall, cursor)
        end = _point_at_offset(wall, total_length)
        _add_wall_piece(scene, start, end, 0.0, height, thickness, f"{wall.wall_id}_after")


def _add_wall_piece(
    scene: trimesh.Scene,
    start: Point2D,
    end: Point2D,
    base_height: float,
    piece_height: float,
    thickness: float,
    node_name: str,
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
        [231, 228, 222, 255],
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


def _furniture_color(kind: str) -> list[int]:
    mapping = {
        "bed": [180, 151, 117, 255],
        "sofa": [100, 123, 162, 255],
        "kitchen_counter": [139, 153, 168, 255],
        "vanity": [177, 184, 188, 255],
        "console": [132, 102, 77, 255],
        "storage_cube": [167, 144, 122, 255],
    }
    return mapping.get(kind, [160, 160, 160, 255])


def _room_floor_material(room_type: RoomType) -> str:
    mapping = {
        RoomType.BEDROOM: "floor_bedroom",
        RoomType.LIVING_ROOM: "floor_living_room",
        RoomType.KITCHEN: "floor_kitchen",
        RoomType.BATHROOM: "floor_bathroom",
        RoomType.CORRIDOR: "floor_corridor",
        RoomType.GENERIC: "floor_generic",
    }
    return mapping.get(room_type, "floor_generic")


def _surface_color(material: str) -> list[int]:
    mapping = {
        "floor_bedroom": [230, 206, 144, 255],
        "floor_living_room": [238, 228, 207, 255],
        "floor_kitchen": [233, 228, 214, 255],
        "floor_bathroom": [212, 231, 236, 255],
        "floor_corridor": [232, 223, 205, 255],
        "floor_generic": [224, 218, 206, 255],
        "matte_ceiling": [240, 240, 238, 255],
    }
    return mapping.get(material, [222, 218, 210, 255])


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
