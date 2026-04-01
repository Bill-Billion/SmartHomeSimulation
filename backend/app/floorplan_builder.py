from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .config import DEFAULT_TARGET_SPAN_M, DEFAULT_WALL_THICKNESS_M
from .domain_rules import default_room_name
from .geometry import edge_signature_xy, point_in_polygon_xy, polygon_centroid_xy
from .models import (
    FloorplanSpec,
    OpeningSpec,
    OpeningType,
    Point2D,
    RoomSpec,
    RoomType,
    SourceType,
    WallKind,
    WallSegment,
)
from .semantic_rules import RoomSemanticHint, _apply_room_semantics


@dataclass(frozen=True)
class AxisEdge:
    """把房间边投影到水平/垂直坐标轴上，便于归一共享墙。"""

    edge_id: str
    room_id: str
    orientation: str
    axis: float
    start: float
    end: float


@dataclass(frozen=True)
class WallFragment:
    """墙体归一后的中间片段，可继续合并成最终墙段。"""

    orientation: str
    axis: float
    start: float
    end: float
    kind: WallKind
    room_ids: tuple[str, ...]


def _fallback_rect(gray: np.ndarray) -> tuple[int, int, int, int]:
    height, width = gray.shape
    return (int(width * 0.2), int(height * 0.2), int(width * 0.6), int(height * 0.6))


def _rect_to_polygon(rect: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    min_x, min_z, max_x, max_z = rect
    return [
        (min_x, min_z),
        (max_x, min_z),
        (max_x, max_z),
        (min_x, max_z),
    ]


def _normalize_polygons(
    polygons_px: list[list[tuple[float, float]]]
) -> tuple[list[list[tuple[float, float]]], float, float, float, object]:
    from .parser_raster import RasterTransform

    all_xs = [point[0] for polygon in polygons_px for point in polygon]
    all_zs = [point[1] for polygon in polygons_px for point in polygon]
    min_x = min(all_xs)
    max_x = max(all_xs)
    min_z = min(all_zs)
    max_z = max(all_zs)
    span_x = max(max_x - min_x, 1.0)
    span_z = max(max_z - min_z, 1.0)
    scale = DEFAULT_TARGET_SPAN_M / max(span_x, span_z)
    center_x = min_x + span_x / 2
    center_z = min_z + span_z / 2
    centered: list[list[tuple[float, float]]] = []

    for polygon in polygons_px:
        centered.append(
            [
                (
                    (point_x - center_x) * scale,
                    (point_z - center_z) * scale,
                )
                for point_x, point_z in polygon
            ]
        )

    return (
        centered,
        span_x * scale,
        span_z * scale,
        scale,
        RasterTransform(
            scale_m_per_px=scale,
            center_x_px=center_x,
            center_z_px=center_z,
        ),
    )


def _center_world_rects(
    rects: list[tuple[float, float, float, float]]
) -> tuple[list[tuple[float, float, float, float]], float, float, float, float]:
    polygons = [_rect_to_polygon(rect) for rect in rects]
    centered_polygons, width_m, depth_m, center_x, center_z = _center_world_polygons(polygons)
    centered_rects = [
        _bbox_from_points(polygon)
        for polygon in centered_polygons
    ]
    return centered_rects, width_m, depth_m, center_x, center_z


def _center_world_polygons(
    polygons: list[list[tuple[float, float]]]
) -> tuple[list[list[tuple[float, float]]], float, float, float, float]:
    """统一 DXF 世界坐标的居中逻辑，让矩形和任意正交 polygon 走同一套基准。"""
    all_points = [point for polygon in polygons for point in polygon]
    min_x, min_z, max_x, max_z = _bbox_from_points(all_points)
    center_x = (min_x + max_x) / 2
    center_z = (min_z + max_z) / 2
    centered = [
        [
            (round(point_x - center_x, 3), round(point_z - center_z, 3))
            for point_x, point_z in polygon
        ]
        for polygon in polygons
    ]
    return centered, max_x - min_x, max_z - min_z, center_x, center_z


def _bbox_from_points(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _detect_dxf_unit_factor(unit_code: int) -> float:
    unit_name = str(unit_code).lower()
    if "millimeter" in unit_name or unit_code == 4:
        return 0.001
    if "centimeter" in unit_name or unit_code == 5:
        return 0.01
    if "meter" in unit_name or unit_code == 6:
        return 1.0
    return 0.001


def _build_floorplan_from_polygons(
    polygons: list[list[tuple[float, float]]],
    *,
    source_type: SourceType,
    bounds_width_m: float,
    bounds_depth_m: float,
    scale_m_per_unit: float,
    warnings: list[str],
    semantic_hints: list[RoomSemanticHint] | tuple[RoomSemanticHint, ...] = (),
) -> FloorplanSpec:
    rooms = _build_rooms(polygons)
    outer_walls, inner_walls, room_edge_map = _build_wall_segments_from_polygons(rooms)
    rooms, semantic_warnings = _apply_room_semantics(
        rooms,
        outer_walls,
        inner_walls,
        room_edge_map,
        semantic_hints,
    )
    if semantic_warnings:
        warnings.extend(semantic_warnings)
    openings = _build_openings(rooms, outer_walls, inner_walls, room_edge_map)
    confidence = 0.88 if len(rooms) > 1 else 0.66
    if rooms:
        semantic_confidence = sum(room.confidence for room in rooms) / len(rooms) + 0.05
        confidence = round(min(confidence, min(0.92, semantic_confidence)), 3)

    return FloorplanSpec(
        source_type=source_type,
        bounds_width_m=round(bounds_width_m, 3),
        bounds_depth_m=round(bounds_depth_m, 3),
        scale_m_per_unit=scale_m_per_unit,
        outer_walls=outer_walls,
        inner_walls=inner_walls,
        openings=openings,
        rooms=rooms,
        confidence=confidence,
        warnings=warnings,
    )


def _build_rooms(polygons: list[list[tuple[float, float]]]) -> list[RoomSpec]:
    prepared: list[tuple[float, list[tuple[float, float]]]] = []
    for raw_polygon in polygons:
        sanitized_polygon = _sanitize_polygon(raw_polygon)
        if len(sanitized_polygon) < 4:
            continue
        prepared.append((abs(_polygon_area(sanitized_polygon)), sanitized_polygon))
    prepared.sort(key=lambda item: item[0], reverse=True)
    rooms: list[RoomSpec] = []

    for index, (area, sanitized_polygon) in enumerate(prepared, start=1):
        polygon = [Point2D(x=point_x, z=point_z) for point_x, point_z in sanitized_polygon]
        rooms.append(
            RoomSpec(
                room_id=f"room_{index:02d}",
                name=default_room_name(RoomType.GENERIC, index),
                room_type=RoomType.GENERIC,
                polygon=polygon,
                area_sqm=round(area, 2),
                confidence=0.55,
            )
        )
    return rooms


def _polygon_centroid(polygon: list[tuple[float, float]]) -> tuple[float, float]:
    return polygon_centroid_xy(polygon)


def _build_wall_segments_from_polygons(
    rooms: list[RoomSpec],
) -> tuple[list[WallSegment], list[WallSegment], dict[str, list[str]]]:
    rooms_by_id = {room.room_id: room for room in rooms}
    axis_edges = _collect_axis_edges(rooms)
    paired_fragments, remaining_ranges = _build_shared_wall_fragments(axis_edges)
    raw_fragments = [
        *paired_fragments,
        *_build_unpaired_wall_fragments(axis_edges, remaining_ranges, rooms_by_id),
    ]
    fragments = _merge_wall_fragments(raw_fragments)
    outer_walls: list[WallSegment] = []
    inner_walls: list[WallSegment] = []
    room_edge_map: dict[str, list[str]] = defaultdict(list)
    index = 1

    for fragment in fragments:
        wall = WallSegment(
            wall_id=f"wall_{index:03d}",
            start=_fragment_endpoint(fragment, start=True),
            end=_fragment_endpoint(fragment, start=False),
            kind=fragment.kind,
        )
        index += 1
        if fragment.kind == WallKind.OUTER:
            outer_walls.append(wall)
        else:
            inner_walls.append(wall)
        for room_id in fragment.room_ids:
            room_edge_map[room_id].append(wall.wall_id)

    return outer_walls, inner_walls, dict(room_edge_map)


def _segment_key(start: Point2D, end: Point2D) -> tuple[tuple[float, float], tuple[float, float]]:
    points = sorted(
        [(round(start.x, 3), round(start.z, 3)), (round(end.x, 3), round(end.z, 3))]
    )
    return points[0], points[1]


def _collect_axis_edges(rooms: list[RoomSpec]) -> list[AxisEdge]:
    edges: list[AxisEdge] = []
    counter = 1

    for room in rooms:
        points = room.polygon
        for current_index, start in enumerate(points):
            end = points[(current_index + 1) % len(points)]
            projection = _project_edge_to_axis(start, end)
            if projection is None:
                continue
            orientation, axis, range_start, range_end = projection
            edges.append(
                AxisEdge(
                    edge_id=f"edge_{counter:03d}",
                    room_id=room.room_id,
                    orientation=orientation,
                    axis=axis,
                    start=range_start,
                    end=range_end,
                )
            )
            counter += 1
    return edges


def _project_edge_to_axis(
    start: Point2D,
    end: Point2D,
) -> tuple[str, float, float, float] | None:
    dx = abs(start.x - end.x)
    dz = abs(start.z - end.z)
    if max(dx, dz) < 0.15:
        return None
    if dx >= dz:
        return "horizontal", round((start.z + end.z) / 2, 3), min(start.x, end.x), max(start.x, end.x)
    return "vertical", round((start.x + end.x) / 2, 3), min(start.z, end.z), max(start.z, end.z)


def _build_shared_wall_fragments(
    axis_edges: list[AxisEdge],
) -> tuple[list[WallFragment], dict[str, list[tuple[float, float]]]]:
    """把相邻房间的双边墙收敛成一条共享中心线墙段。"""
    pair_gap = max(DEFAULT_WALL_THICKNESS_M * 2.1, 0.28)
    min_overlap = 0.35
    fragments: list[WallFragment] = []
    remaining_ranges = {
        edge.edge_id: [(edge.start, edge.end)]
        for edge in axis_edges
    }

    for orientation in ("horizontal", "vertical"):
        oriented_edges = [edge for edge in axis_edges if edge.orientation == orientation]
        candidates: list[tuple[float, float, str, str, AxisEdge, AxisEdge]] = []
        for index, first in enumerate(oriented_edges):
            for second in oriented_edges[index + 1:]:
                if first.room_id == second.room_id:
                    continue
                overlap = min(first.end, second.end) - max(first.start, second.start)
                if overlap < min_overlap:
                    continue
                distance = abs(first.axis - second.axis)
                if distance > pair_gap:
                    continue
                candidates.append(
                    (
                        distance,
                        -overlap,
                        first.edge_id,
                        second.edge_id,
                        first,
                        second,
                    )
                )
        candidates.sort()

        for _, _, _, _, first, second in candidates:
            intersections = _intersect_ranges(
                remaining_ranges[first.edge_id],
                remaining_ranges[second.edge_id],
                min_overlap,
            )
            for range_start, range_end in intersections:
                fragments.append(
                    WallFragment(
                        orientation=orientation,
                        axis=round((first.axis + second.axis) / 2, 3),
                        start=round(range_start, 3),
                        end=round(range_end, 3),
                        kind=WallKind.INNER,
                        room_ids=tuple(sorted({first.room_id, second.room_id})),
                    )
                )
                remaining_ranges[first.edge_id] = _subtract_range(
                    remaining_ranges[first.edge_id],
                    range_start,
                    range_end,
                )
                remaining_ranges[second.edge_id] = _subtract_range(
                    remaining_ranges[second.edge_id],
                    range_start,
                    range_end,
                )

    return fragments, remaining_ranges


def _build_unpaired_wall_fragments(
    axis_edges: list[AxisEdge],
    remaining_ranges: dict[str, list[tuple[float, float]]],
    rooms_by_id: dict[str, RoomSpec],
) -> list[WallFragment]:
    fragments: list[WallFragment] = []
    for edge in axis_edges:
        for range_start, range_end in remaining_ranges[edge.edge_id]:
            if range_end - range_start < 0.15:
                continue
            kind = _classify_fragment_kind(
                edge,
                range_start,
                range_end,
                rooms_by_id,
            )
            fragments.append(
                WallFragment(
                    orientation=edge.orientation,
                    axis=edge.axis,
                    start=round(range_start, 3),
                    end=round(range_end, 3),
                    kind=kind,
                    room_ids=(edge.room_id,),
                )
            )
    return fragments


def _classify_fragment_kind(
    edge: AxisEdge,
    range_start: float,
    range_end: float,
    rooms_by_id: dict[str, RoomSpec],
) -> WallKind:
    """用房间两侧占用关系判断未配对墙段是否属于外墙。"""
    room = rooms_by_id.get(edge.room_id)
    if room is None:
        return WallKind.OUTER

    mid_axis = (range_start + range_end) / 2
    offset = max(DEFAULT_WALL_THICKNESS_M * 0.75, 0.12)
    if edge.orientation == "vertical":
        left_point = (edge.axis - offset, mid_axis)
        right_point = (edge.axis + offset, mid_axis)
    else:
        left_point = (mid_axis, edge.axis - offset)
        right_point = (mid_axis, edge.axis + offset)

    left_inside = any(
        point_in_polygon_xy(left_point, [(point.x, point.z) for point in candidate.polygon])
        for candidate in rooms_by_id.values()
    )
    right_inside = any(
        point_in_polygon_xy(right_point, [(point.x, point.z) for point in candidate.polygon])
        for candidate in rooms_by_id.values()
    )

    if left_inside != right_inside:
        return WallKind.OUTER
    return WallKind.INNER


def _merge_wall_fragments(fragments: list[WallFragment]) -> list[WallFragment]:
    """把同轴且相邻的墙片段合并，减少碎片化墙段。"""
    if not fragments:
        return []

    sorted_fragments = sorted(
        fragments,
        key=lambda fragment: (
            fragment.kind.value,
            fragment.orientation,
            round(fragment.axis, 3),
            fragment.room_ids,
            round(fragment.start, 3),
            round(fragment.end, 3),
        ),
    )
    merged: list[WallFragment] = [sorted_fragments[0]]

    for fragment in sorted_fragments[1:]:
        previous = merged[-1]
        same_track = (
            previous.kind == fragment.kind
            and previous.orientation == fragment.orientation
            and previous.room_ids == fragment.room_ids
            and abs(previous.axis - fragment.axis) < 0.02
            and fragment.start <= previous.end + 0.08
        )
        if same_track:
            merged[-1] = WallFragment(
                orientation=previous.orientation,
                axis=round((previous.axis + fragment.axis) / 2, 3),
                start=previous.start,
                end=max(previous.end, fragment.end),
                kind=previous.kind,
                room_ids=previous.room_ids,
            )
            continue
        merged.append(fragment)

    return merged


def _fragment_endpoint(fragment: WallFragment, *, start: bool) -> Point2D:
    value = fragment.start if start else fragment.end
    if fragment.orientation == "horizontal":
        return Point2D(x=value, z=fragment.axis)
    return Point2D(x=fragment.axis, z=value)


def _intersect_ranges(
    first_ranges: list[tuple[float, float]],
    second_ranges: list[tuple[float, float]],
    min_overlap: float,
) -> list[tuple[float, float]]:
    intersections: list[tuple[float, float]] = []
    for first_start, first_end in first_ranges:
        for second_start, second_end in second_ranges:
            overlap_start = max(first_start, second_start)
            overlap_end = min(first_end, second_end)
            if overlap_end - overlap_start >= min_overlap:
                intersections.append((overlap_start, overlap_end))
    return intersections


def _subtract_range(
    ranges: list[tuple[float, float]],
    remove_start: float,
    remove_end: float,
) -> list[tuple[float, float]]:
    updated: list[tuple[float, float]] = []
    for current_start, current_end in ranges:
        if remove_end <= current_start or remove_start >= current_end:
            updated.append((current_start, current_end))
            continue
        if remove_start > current_start + 0.02:
            updated.append((current_start, remove_start))
        if remove_end < current_end - 0.02:
            updated.append((remove_end, current_end))
    return updated


def _segment_length(segment: WallSegment) -> float:
    return math.dist((segment.start.x, segment.start.z), (segment.end.x, segment.end.z))


def _build_openings(
    rooms: list[RoomSpec],
    outer_walls: list[WallSegment],
    inner_walls: list[WallSegment],
    room_edge_map: dict[str, list[str]],
) -> list[OpeningSpec]:
    walls_by_id = {wall.wall_id: wall for wall in [*outer_walls, *inner_walls]}
    inner_wall_ids = {wall.wall_id for wall in inner_walls}
    outer_wall_ids = {wall.wall_id for wall in outer_walls}
    openings: list[OpeningSpec] = []
    window_walls_done: set[str] = set()
    door_walls_done: set[str] = set()

    for room in rooms:
        edge_ids = room_edge_map.get(room.room_id, [])
        inner_candidates = [walls_by_id[edge_id] for edge_id in edge_ids if edge_id in inner_wall_ids]
        outer_candidates = [walls_by_id[edge_id] for edge_id in edge_ids if edge_id in outer_wall_ids]
        door_wall = max(inner_candidates or outer_candidates, key=_segment_length, default=None)
        if door_wall is not None and door_wall.wall_id not in door_walls_done:
            door_length = _segment_length(door_wall)
            openings.append(
                OpeningSpec(
                    opening_id=f"door_{len(openings) + 1}",
                    wall_id=door_wall.wall_id,
                    kind=OpeningType.DOOR,
                    center=_mid_point(door_wall),
                    width_m=min(1.0, max(0.8, door_length * 0.3)),
                    base_height_m=0.0,
                    top_height_m=2.1,
                )
            )
            door_walls_done.add(door_wall.wall_id)
        for wall in outer_candidates:
            if wall.wall_id in window_walls_done:
                continue
            if door_wall is not None and wall.wall_id == door_wall.wall_id:
                continue
            wall_length = _segment_length(wall)
            if wall_length < 2.4:
                continue
            openings.append(
                OpeningSpec(
                    opening_id=f"window_{len(openings) + 1}",
                    wall_id=wall.wall_id,
                    kind=OpeningType.WINDOW,
                    center=_mid_point(wall),
                    width_m=min(1.8, wall_length * 0.4),
                    base_height_m=0.9,
                    top_height_m=2.1,
                )
            )
            window_walls_done.add(wall.wall_id)
    return openings


def _sanitize_polygon(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """清理重复点，避免共享边去重失败和零长度边进入建模阶段。"""
    sanitized: list[tuple[float, float]] = []

    for point in points:
        candidate = (round(point[0], 3), round(point[1], 3))
        if sanitized and _point_distance_xy(candidate, sanitized[-1]) < 0.01:
            continue
        sanitized.append(candidate)

    if len(sanitized) > 1 and _point_distance_xy(sanitized[0], sanitized[-1]) < 0.01:
        sanitized.pop()

    return sanitized or points


def _build_polygon_edge_usage(
    polygons: list[list[tuple[float, float]]]
) -> dict[tuple[tuple[float, float], tuple[float, float]], int]:
    edge_usage: dict[tuple[tuple[float, float], tuple[float, float]], int] = defaultdict(int)
    for polygon in polygons:
        total = len(polygon)
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % total]
            edge_usage[edge_signature_xy(start, end)] += 1
    return edge_usage


def _polygon_exterior_edge_count(
    polygon: list[tuple[float, float]],
    edge_usage: dict[tuple[tuple[float, float], tuple[float, float]], int],
) -> int:
    count = 0
    total = len(polygon)
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % total]
        if edge_usage[edge_signature_xy(start, end)] == 1:
            count += 1
    return count


def _polygon_area(polygon: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _point_distance(start: Point2D, end: Point2D) -> float:
    return math.dist((start.x, start.z), (end.x, end.z))


def _point_distance_xy(start: tuple[float, float], end: tuple[float, float]) -> float:
    return math.dist(start, end)


def _mid_point(wall: WallSegment) -> Point2D:
    return Point2D(
        x=round((wall.start.x + wall.end.x) / 2, 3),
        z=round((wall.start.z + wall.end.z) / 2, 3),
    )
