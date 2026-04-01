from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from .domain_rules import FURNITURE_WALL_PREFERENCES, RESOURCE_VERSION, merge_warnings
from .geometry import (
    iter_polygon_edges,
    point_in_polygon,
    point_segment_distance,
    polygon_bounds,
    polygon_centroid,
    project_offset,
    room_center,
)
from .models import (
    CameraSpec,
    FloorplanSpec,
    FurnitureSpec,
    OpeningSpec,
    Point2D,
    Point3D,
    RoomSpec,
    RoomType,
    SceneSpec,
    SurfaceSpec,
    WallKind,
    WallSegment,
)


@dataclass(frozen=True)
class FurnitureTemplate:
    kind: str
    width: float
    height: float
    depth: float


@dataclass(frozen=True)
class EdgeCandidate:
    start: Point2D
    end: Point2D
    midpoint: Point2D
    length: float
    yaw_deg: float
    inward_normal: tuple[float, float]
    label: str


def build_scene_spec(
    scene_id: str,
    floorplan: FloorplanSpec,
    furniture_hints: Mapping[str, str] | None = None,
) -> SceneSpec:
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
                elevation_m=floorplan.wall_height_m - 0.04,
                thickness_m=0.04,
                material="matte_ceiling",
            )
        )

    furnitures, furniture_warnings = _build_furnitures(
        floorplan.rooms,
        floorplan.openings,
        furniture_hints or {},
    )
    camera = _build_camera(floorplan.bounds_width_m, floorplan.bounds_depth_m, floorplan.wall_height_m)
    warnings = merge_warnings(floorplan.warnings, furniture_warnings)

    return SceneSpec(
        scene_id=scene_id,
        resource_version=RESOURCE_VERSION,
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
        warnings=warnings,
    )


def _collect_cutaway_wall_ids(walls: list[WallSegment], camera: CameraSpec) -> set[str]:
    """按相机朝向选择最靠近镜头的外墙，避免切墙方向写死。"""
    outer_walls = [wall for wall in walls if wall.kind == WallKind.OUTER]
    if len(outer_walls) <= 2:
        return set()

    center_x = sum((wall.start.x + wall.end.x) / 2 for wall in outer_walls) / len(outer_walls)
    center_z = sum((wall.start.z + wall.end.z) / 2 for wall in outer_walls) / len(outer_walls)
    camera_dir = (
        camera.position.x - camera.target.x,
        camera.position.z - camera.target.z,
    )
    camera_length = math.hypot(*camera_dir) or 1.0
    camera_dir = (camera_dir[0] / camera_length, camera_dir[1] / camera_length)

    scored: list[tuple[float, str]] = []
    for wall in outer_walls:
        midpoint = ((wall.start.x + wall.end.x) / 2, (wall.start.z + wall.end.z) / 2)
        outward = (midpoint[0] - center_x, midpoint[1] - center_z)
        outward_length = math.hypot(*outward) or 1.0
        outward = (outward[0] / outward_length, outward[1] / outward_length)
        score = outward[0] * camera_dir[0] + outward[1] * camera_dir[1]
        scored.append((score, wall.wall_id))

    scored.sort(reverse=True)
    threshold = max(2, min(3, len(scored) // 3))
    return {wall_id for score, wall_id in scored[:threshold] if score > 0.2}


def _build_camera(width_m: float, depth_m: float, wall_height_m: float) -> CameraSpec:
    span = max(width_m, depth_m, 6.0)
    return CameraSpec(
        position=Point3D(x=span * 0.52, y=wall_height_m * 2.55, z=span * 0.74),
        target=Point3D(x=0.0, y=wall_height_m * 0.3, z=0.0),
        fov=36.0,
    )


def _build_furnitures(
    rooms: Iterable[RoomSpec],
    openings: list[OpeningSpec],
    furniture_hints: Mapping[str, str],
) -> tuple[list[FurnitureSpec], list[str]]:
    furnitures: list[FurnitureSpec] = []
    warnings: list[str] = []
    for room in rooms:
        template = _template_for_room(room)
        if template is None:
            continue

        preference = str(furniture_hints.get(room.room_id, "longest")).strip().lower()
        if preference not in FURNITURE_WALL_PREFERENCES:
            preference = "longest"

        furniture, warning = _place_template_in_room(room, template, openings, preference)
        if furniture is not None:
            furnitures.append(furniture)
        if warning:
            warnings.append(warning)
    return furnitures, warnings


def _template_for_room(room: RoomSpec) -> FurnitureTemplate | None:
    min_x, max_x, min_z, max_z = polygon_bounds(room.polygon)
    width = max_x - min_x
    depth = max_z - min_z

    if room.room_type == RoomType.BEDROOM:
        return FurnitureTemplate("bed", min(width * 0.55, 2.0), 0.42, min(depth * 0.38, 1.8))
    if room.room_type == RoomType.LIVING_ROOM:
        return FurnitureTemplate("sofa", min(width * 0.45, 2.4), 0.86, min(depth * 0.22, 0.95))
    if room.room_type == RoomType.KITCHEN:
        return FurnitureTemplate("kitchen_counter", min(width * 0.55, 2.2), 0.92, 0.68)
    if room.room_type == RoomType.BATHROOM:
        return FurnitureTemplate("vanity", 0.85, 0.9, 0.58)
    if room.room_type == RoomType.CORRIDOR:
        return FurnitureTemplate("console", min(width * 0.35, 1.4), 0.9, 0.35)
    return FurnitureTemplate("storage_cube", min(width * 0.28, 0.95), 0.62, min(depth * 0.28, 0.95))


def _place_template_in_room(
    room: RoomSpec,
    template: FurnitureTemplate,
    openings: list[OpeningSpec],
    preference: str,
) -> tuple[FurnitureSpec | None, str | None]:
    anchor_center = room_center(room.polygon)
    candidates = _build_edge_candidates(room.polygon, anchor_center, openings)

    if preference != "center":
        candidates.sort(
            key=lambda candidate: (
                0 if candidate.label == preference else 1,
                0 if preference == "longest" else 1,
                -candidate.length,
            )
        )
        for candidate in candidates:
            furniture = _place_template_against_edge(room, template, candidate)
            if furniture is not None:
                return furniture, None

    anchor = _find_room_anchor(room.polygon)
    if anchor is None:
        return None, f"{room.name} 暂时缺少稳定家具落点，已跳过该房间的家具摆放。"

    min_x, max_x, min_z, max_z = polygon_bounds(room.polygon)
    fallback_rotation = 0.0 if (max_x - min_x) >= (max_z - min_z) else 90.0
    return (
        FurnitureSpec(
            furniture_id=f"furniture_{room.room_id}_{template.kind}",
            room_id=room.room_id,
            kind=template.kind,
            position=Point3D(x=anchor.x, y=template.height / 2, z=anchor.z),
            size=Point3D(x=template.width, y=template.height, z=template.depth),
            rotation_deg=fallback_rotation,
        ),
        f"{room.name} 缺少稳定贴墙位，已回退为居中摆放。",
    )


def _build_edge_candidates(
    polygon: list[Point2D],
    room_center: Point2D,
    openings: list[OpeningSpec],
) -> list[EdgeCandidate]:
    candidates: list[EdgeCandidate] = []
    for start, end in iter_polygon_edges(polygon):
        length = math.dist((start.x, start.z), (end.x, end.z))
        if length < 0.9:
            continue
        if _edge_has_opening(start, end, openings):
            continue

        midpoint = Point2D(x=(start.x + end.x) / 2, z=(start.z + end.z) / 2)
        inward_normal = _resolve_inward_normal(polygon, start, end)
        if inward_normal is None:
            continue
        candidates.append(
            EdgeCandidate(
                start=start,
                end=end,
                midpoint=midpoint,
                length=length,
                yaw_deg=math.degrees(math.atan2(end.z - start.z, end.x - start.x)),
                inward_normal=inward_normal,
                label=_edge_label(start, end, midpoint, room_center),
            )
        )
    return candidates


def _place_template_against_edge(
    room: RoomSpec,
    template: FurnitureTemplate,
    candidate: EdgeCandidate,
) -> FurnitureSpec | None:
    available_width = max(0.72, min(template.width, candidate.length - 0.28))
    if available_width < 0.72:
        return None

    offset = template.depth / 2 + 0.12
    center = Point2D(
        x=candidate.midpoint.x + candidate.inward_normal[0] * offset,
        z=candidate.midpoint.z + candidate.inward_normal[1] * offset,
    )
    if not _footprint_fits(room.polygon, center, available_width, template.depth, candidate.yaw_deg):
        return None

    return FurnitureSpec(
        furniture_id=f"furniture_{room.room_id}_{template.kind}",
        room_id=room.room_id,
        kind=template.kind,
        position=Point3D(x=center.x, y=template.height / 2, z=center.z),
        size=Point3D(x=available_width, y=template.height, z=template.depth),
        rotation_deg=candidate.yaw_deg,
    )


def _edge_has_opening(start: Point2D, end: Point2D, openings: list[OpeningSpec]) -> bool:
    for opening in openings:
        if point_segment_distance(opening.center, start, end) > 0.26:
            continue
        projected = project_offset(start, end, opening.center)
        length = math.dist((start.x, start.z), (end.x, end.z))
        if 0.1 <= projected <= length - 0.1:
            return True
    return False


def _resolve_inward_normal(
    polygon: list[Point2D],
    start: Point2D,
    end: Point2D,
) -> tuple[float, float] | None:
    length = math.dist((start.x, start.z), (end.x, end.z))
    if length <= 1e-6:
        return None
    normals = [
        (-(end.z - start.z) / length, (end.x - start.x) / length),
        ((end.z - start.z) / length, -(end.x - start.x) / length),
    ]
    midpoint = Point2D(x=(start.x + end.x) / 2, z=(start.z + end.z) / 2)
    for normal in normals:
        probe = Point2D(x=midpoint.x + normal[0] * 0.18, z=midpoint.z + normal[1] * 0.18)
        if point_in_polygon(probe, polygon):
            return normal
    return None


def _edge_label(start: Point2D, end: Point2D, midpoint: Point2D, room_center: Point2D) -> str:
    if abs(end.x - start.x) >= abs(end.z - start.z):
        return "north" if midpoint.z <= room_center.z else "south"
    return "east" if midpoint.x >= room_center.x else "west"


def _find_room_anchor(polygon: list[Point2D]) -> Point2D | None:
    min_x, max_x, min_z, max_z = polygon_bounds(polygon)
    candidates = [
        polygon_centroid(polygon),
        Point2D(x=(min_x + max_x) / 2, z=(min_z + max_z) / 2),
    ]
    for candidate in candidates:
        if candidate and point_in_polygon(candidate, polygon):
            return candidate

    center_x = (min_x + max_x) / 2
    center_z = (min_z + max_z) / 2
    x_steps = [0.0, -0.12, 0.12, -0.24, 0.24, -0.36, 0.36]
    z_steps = [0.0, -0.12, 0.12, -0.24, 0.24, -0.36, 0.36]
    for dx in x_steps:
        for dz in z_steps:
            candidate = Point2D(x=center_x + dx * (max_x - min_x), z=center_z + dz * (max_z - min_z))
            if point_in_polygon(candidate, polygon):
                return candidate
    return None


def _footprint_fits(
    polygon: list[Point2D],
    center: Point2D,
    width: float,
    depth: float,
    yaw_deg: float,
) -> bool:
    yaw = math.radians(yaw_deg)
    along = (math.cos(yaw), math.sin(yaw))
    normal = (-math.sin(yaw), math.cos(yaw))
    half_width = width / 2
    half_depth = depth / 2
    corners = []
    for width_sign in (-1, 1):
        for depth_sign in (-1, 1):
            corners.append(
                Point2D(
                    x=center.x + along[0] * half_width * width_sign + normal[0] * half_depth * depth_sign,
                    z=center.z + along[1] * half_width * width_sign + normal[1] * half_depth * depth_sign,
                )
            )
    return all(point_in_polygon(corner, polygon) for corner in corners)


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
