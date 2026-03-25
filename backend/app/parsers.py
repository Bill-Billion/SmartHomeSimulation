from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

from .config import DEFAULT_TARGET_SPAN_M, DEFAULT_WALL_THICKNESS_M
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


class UnsupportedFormatError(ValueError):
    """当前阶段不支持的格式。"""


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


@dataclass(frozen=True)
class RasterTransform:
    """记录像素坐标和米制坐标之间的双向映射。"""

    scale_m_per_px: float
    center_x_px: float
    center_z_px: float


def parse_floorplan(source_path: Path) -> FloorplanSpec:
    suffix = source_path.suffix.lower()
    if suffix == ".dwg":
        raise UnsupportedFormatError("首期暂不支持 DWG，请先转换为 DXF 或 PDF。")
    if suffix == ".dxf":
        return _parse_dxf_floorplan(source_path)
    if suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
        return _parse_raster_floorplan(source_path)
    raise UnsupportedFormatError(f"暂不支持 {suffix} 文件。")


def _parse_raster_floorplan(source_path: Path) -> FloorplanSpec:
    import cv2
    import fitz

    if source_path.suffix.lower() == ".pdf":
        doc = fitz.open(source_path)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        source_type = SourceType.PDF
    else:
        image = cv2.imread(str(source_path))
        if image is None:
            raise ValueError("无法读取上传的平面图文件。")
        source_type = _source_type_from_suffix(source_path.suffix.lower())

    structural_walls = _extract_structural_wall_mask(image)
    room_polygons_px = _extract_room_polygons(structural_walls)

    warnings: list[str] = []
    if not room_polygons_px:
        warnings.append("平面图识别置信度较低，已退化为单房间壳体。")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        room_polygons_px = [_rect_to_polygon(_fallback_rect(gray))]

    world_polygons, width_m, depth_m, scale, transform = _normalize_polygons(room_polygons_px)
    floorplan = _build_floorplan_from_polygons(
        world_polygons,
        source_type=source_type,
        bounds_width_m=width_m,
        bounds_depth_m=depth_m,
        scale_m_per_unit=scale,
        warnings=warnings,
    )
    floorplan.openings = _refine_raster_openings(image, floorplan, transform)
    return floorplan


def _parse_dxf_floorplan(source_path: Path) -> FloorplanSpec:
    import ezdxf
    from ezdxf import units

    doc = ezdxf.readfile(source_path)
    msp = doc.modelspace()
    raw_rects: list[tuple[float, float, float, float]] = []
    all_points: list[tuple[float, float]] = []

    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype == "LWPOLYLINE":
            points = [(point[0], point[1]) for point in entity.get_points()]
            all_points.extend(points)
            if entity.closed and len(points) >= 4:
                raw_rects.append(_bbox_from_points(points))
        elif dxftype == "POLYLINE":
            points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
            all_points.extend(points)
            if entity.is_closed and len(points) >= 4:
                raw_rects.append(_bbox_from_points(points))
        elif dxftype == "LINE":
            start = (entity.dxf.start.x, entity.dxf.start.y)
            end = (entity.dxf.end.x, entity.dxf.end.y)
            all_points.extend([start, end])

    if not all_points:
        raise ValueError("DXF 中没有可识别的线段或轮廓。")

    if not raw_rects:
        raw_rects = [_bbox_from_points(all_points)]

    unit_factor = _detect_dxf_unit_factor(doc.units)
    scaled_rects = [
        (min_x * unit_factor, min_y * unit_factor, max_x * unit_factor, max_y * unit_factor)
        for min_x, min_y, max_x, max_y in raw_rects
    ]
    world_rects, width_m, depth_m = _center_world_rects(scaled_rects)
    warnings: list[str] = []
    if doc.units not in {4, 5, 6}:
        warnings.append("DXF 未声明单位，已按启发式比例缩放。")

    polygons = [_rect_to_polygon(rect) for rect in world_rects]
    return _build_floorplan_from_polygons(
        polygons,
        source_type=SourceType.DXF,
        bounds_width_m=width_m,
        bounds_depth_m=depth_m,
        scale_m_per_unit=unit_factor,
        warnings=warnings,
    )


def _source_type_from_suffix(suffix: str) -> SourceType:
    mapping = {
        ".jpg": SourceType.JPG,
        ".jpeg": SourceType.JPEG,
        ".png": SourceType.PNG,
        ".pdf": SourceType.PDF,
        ".dxf": SourceType.DXF,
        ".dwg": SourceType.DWG,
    }
    return mapping[suffix]


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


def _normalize_rects(
    rects_px: list[tuple[int, int, int, int]]
) -> tuple[list[tuple[float, float, float, float]], float, float, float]:
    min_x = min(x for x, _, _, _ in rects_px)
    min_y = min(y for _, y, _, _ in rects_px)
    max_x = max(x + w for x, _, w, _ in rects_px)
    max_y = max(y + h for _, y, _, h in rects_px)
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)
    scale = DEFAULT_TARGET_SPAN_M / max(span_x, span_y)
    centered: list[tuple[float, float, float, float]] = []
    center_x = min_x + span_x / 2
    center_y = min_y + span_y / 2

    for x, y, w, h in rects_px:
        start_x = (x - center_x) * scale
        end_x = (x + w - center_x) * scale
        start_z = (y - center_y) * scale
        end_z = (y + h - center_y) * scale
        centered.append((start_x, start_z, end_x, end_z))

    return centered, span_x * scale, span_y * scale, scale


def _normalize_polygons(
    polygons_px: list[list[tuple[float, float]]]
) -> tuple[list[list[tuple[float, float]]], float, float, float, RasterTransform]:
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
) -> tuple[list[tuple[float, float, float, float]], float, float]:
    min_x = min(rect[0] for rect in rects)
    min_z = min(rect[1] for rect in rects)
    max_x = max(rect[2] for rect in rects)
    max_z = max(rect[3] for rect in rects)
    center_x = (min_x + max_x) / 2
    center_z = (min_z + max_z) / 2
    centered = [
        (rect[0] - center_x, rect[1] - center_z, rect[2] - center_x, rect[3] - center_z)
        for rect in rects
    ]
    return centered, max_x - min_x, max_z - min_z


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
) -> FloorplanSpec:
    rooms = _build_rooms(polygons)
    outer_walls, inner_walls, room_edge_map = _build_wall_segments_from_polygons(rooms)
    openings = _build_openings(rooms, outer_walls, inner_walls, room_edge_map)
    confidence = 0.88 if len(rooms) > 1 else 0.66

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
    typed = _assign_room_types(polygons)
    rooms: list[RoomSpec] = []

    for index, (room_type, raw_polygon) in enumerate(typed, start=1):
        sanitized_polygon = _sanitize_polygon(raw_polygon)
        polygon = [Point2D(x=point_x, z=point_z) for point_x, point_z in sanitized_polygon]
        area = abs(_polygon_area(sanitized_polygon))
        rooms.append(
            RoomSpec(
                room_id=f"room_{index:02d}",
                name=_room_name(room_type, index),
                room_type=room_type,
                polygon=polygon,
                area_sqm=round(area, 2),
                confidence=0.8 if room_type != RoomType.GENERIC else 0.55,
            )
        )
    return rooms


def _assign_room_types(
    polygons: list[list[tuple[float, float]]]
) -> list[tuple[RoomType, list[tuple[float, float]]]]:
    enriched = []
    for polygon in polygons:
        min_x = min(point[0] for point in polygon)
        max_x = max(point[0] for point in polygon)
        min_z = min(point[1] for point in polygon)
        max_z = max(point[1] for point in polygon)
        width = max_x - min_x
        depth = max_z - min_z
        area = abs(_polygon_area(polygon))
        aspect = width / max(depth, 0.01)
        enriched.append((polygon, area, aspect))
    enriched.sort(key=lambda item: item[1], reverse=True)

    result: list[tuple[RoomType, list[tuple[float, float]]]] = []
    for index, (polygon, area, aspect) in enumerate(enriched):
        room_type = RoomType.GENERIC
        if aspect > 2.4 or aspect < 0.42:
            room_type = RoomType.CORRIDOR
        elif index == 0:
            room_type = RoomType.LIVING_ROOM
        elif index == len(enriched) - 1 and area < 12:
            room_type = RoomType.BATHROOM
        elif index == 1:
            room_type = RoomType.BEDROOM
        elif index == 2:
            room_type = RoomType.KITCHEN
        elif area >= 10:
            room_type = RoomType.BEDROOM
        result.append((room_type, polygon))
    return result


def _room_name(room_type: RoomType, index: int) -> str:
    mapping = {
        RoomType.BEDROOM: "卧室",
        RoomType.LIVING_ROOM: "客厅",
        RoomType.KITCHEN: "厨房",
        RoomType.BATHROOM: "卫生间",
        RoomType.CORRIDOR: "走廊",
        RoomType.GENERIC: "通用房间",
    }
    return f"{mapping[room_type]} {index}"


def _build_wall_segments_from_polygons(
    rooms: list[RoomSpec],
) -> tuple[list[WallSegment], list[WallSegment], dict[str, list[str]]]:
    all_points = [point for room in rooms for point in room.polygon]
    min_x = min(point.x for point in all_points)
    max_x = max(point.x for point in all_points)
    min_z = min(point.z for point in all_points)
    max_z = max(point.z for point in all_points)
    bounds = {
        "min_x": min_x,
        "max_x": max_x,
        "min_z": min_z,
        "max_z": max_z,
    }
    tolerance = 0.25
    axis_edges = _collect_axis_edges(rooms)
    paired_fragments, remaining_ranges = _build_shared_wall_fragments(axis_edges)
    raw_fragments = [
        *paired_fragments,
        *_build_unpaired_wall_fragments(axis_edges, remaining_ranges, bounds, tolerance),
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
    bounds: dict[str, float],
    tolerance: float,
) -> list[WallFragment]:
    fragments: list[WallFragment] = []
    for edge in axis_edges:
        for range_start, range_end in remaining_ranges[edge.edge_id]:
            if range_end - range_start < 0.15:
                continue
            fragments.append(
                WallFragment(
                    orientation=edge.orientation,
                    axis=edge.axis,
                    start=round(range_start, 3),
                    end=round(range_end, 3),
                    kind=_classify_fragment_kind(edge, bounds, tolerance),
                    room_ids=(edge.room_id,),
                )
            )
    return fragments


def _classify_fragment_kind(
    edge: AxisEdge,
    bounds: dict[str, float],
    tolerance: float,
) -> WallKind:
    if edge.orientation == "vertical":
        if abs(edge.axis - bounds["min_x"]) < tolerance or abs(edge.axis - bounds["max_x"]) < tolerance:
            return WallKind.OUTER
        return WallKind.INNER

    if abs(edge.axis - bounds["min_z"]) < tolerance or abs(edge.axis - bounds["max_z"]) < tolerance:
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


def _refine_raster_openings(
    image: np.ndarray,
    floorplan: FloorplanSpec,
    transform: RasterTransform,
) -> list[OpeningSpec]:
    """优先用原图中的真实开口位置修正门窗；检测失败时回退到启发式结果。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_mask = (gray < 170).astype(np.uint8)
    heuristics_by_key = {
        (opening.wall_id, opening.kind): opening
        for opening in floorplan.openings
    }
    refined: list[OpeningSpec] = []

    for wall in floorplan.inner_walls:
        candidate = _detect_wall_opening_candidate(
            wall,
            dark_mask,
            transform,
            floorplan.wall_thickness_m,
            kind=OpeningType.DOOR,
        )
        opening = candidate or heuristics_by_key.get((wall.wall_id, OpeningType.DOOR))
        if opening is not None:
            refined.append(
                OpeningSpec(
                    opening_id=f"door_{len(refined) + 1}",
                    wall_id=wall.wall_id,
                    kind=OpeningType.DOOR,
                    center=opening.center,
                    width_m=opening.width_m,
                    base_height_m=0.0,
                    top_height_m=2.1,
                )
            )

    for wall in floorplan.outer_walls:
        candidate = _detect_wall_opening_candidate(
            wall,
            dark_mask,
            transform,
            floorplan.wall_thickness_m,
            kind=OpeningType.WINDOW,
        )
        opening = candidate or heuristics_by_key.get((wall.wall_id, OpeningType.WINDOW))
        if opening is None:
            continue
        refined.append(
            OpeningSpec(
                opening_id=f"window_{len(refined) + 1}",
                wall_id=wall.wall_id,
                kind=OpeningType.WINDOW,
                center=opening.center,
                width_m=opening.width_m,
                base_height_m=0.9,
                top_height_m=2.1,
            )
        )

    return refined


def _detect_wall_opening_candidate(
    wall: WallSegment,
    dark_mask: np.ndarray,
    transform: RasterTransform,
    wall_thickness_m: float,
    *,
    kind: OpeningType,
) -> OpeningSpec | None:
    """沿墙体中心线做一维剖面，找出明显偏亮的连续区间作为开口候选。

    当前规则只服务第一阶段的轴对齐住宅户型，所以这里显式限制了：
    - 门窗最小/最大宽度，避免把文字或小噪声误当开口
    - 端点边距，避免把墙端断笔直接识别成门窗
    - 平滑后再阈值分段，减少扫描噪声对单像素采样的影响
    """
    profile = _sample_wall_darkness_profile(wall, dark_mask, transform, wall_thickness_m)
    if profile is None:
        return None

    axis_values, densities = profile
    if len(axis_values) < 8:
        return None

    kernel = max(5, min(15, len(densities) // 8 * 2 + 1))
    smooth = np.convolve(densities, np.ones(kernel) / kernel, mode="same")
    threshold = min(0.24, float(smooth.mean()) * 0.8)
    min_width_m = 0.55 if kind == OpeningType.DOOR else 0.8
    max_width_m = 1.3 if kind == OpeningType.DOOR else 2.4
    min_pixels = max(int(round(min_width_m / transform.scale_m_per_px)), max(6, kernel // 2))
    edge_margin = max(int(round(0.18 / transform.scale_m_per_px)), kernel)
    runs = _find_low_density_runs(smooth, threshold, min_pixels, edge_margin)
    if not runs:
        return None

    best_start, best_end = max(
        runs,
        key=lambda item: ((item[1] - item[0]), -(float(smooth[item[0]:item[1] + 1].mean()))),
    )
    opening_start = axis_values[best_start]
    opening_end = axis_values[best_end]
    width_m = abs(opening_end - opening_start) + transform.scale_m_per_px
    if width_m < min_width_m or width_m > max_width_m:
        return None

    center_axis = (opening_start + opening_end) / 2
    if abs(wall.start.z - wall.end.z) <= abs(wall.start.x - wall.end.x):
        center = Point2D(x=round(center_axis, 3), z=round((wall.start.z + wall.end.z) / 2, 3))
    else:
        center = Point2D(x=round((wall.start.x + wall.end.x) / 2, 3), z=round(center_axis, 3))

    return OpeningSpec(
        opening_id=f"detected_{wall.wall_id}_{kind.value}",
        wall_id=wall.wall_id,
        kind=kind,
        center=center,
        width_m=round(width_m, 3),
        base_height_m=0.0 if kind == OpeningType.DOOR else 0.9,
        top_height_m=2.1,
    )


def _sample_wall_darkness_profile(
    wall: WallSegment,
    dark_mask: np.ndarray,
    transform: RasterTransform,
    wall_thickness_m: float,
) -> tuple[list[float], np.ndarray] | None:
    """把墙体映射回像素坐标，并沿主轴采样墙厚范围内的黑色密度。"""
    height, width = dark_mask.shape
    start_px = _world_to_pixel(wall.start, transform)
    end_px = _world_to_pixel(wall.end, transform)
    wall_px = max(int(round(wall_thickness_m / transform.scale_m_per_px)), 8)
    axis_values: list[float] = []
    densities: list[float] = []

    if abs(start_px[1] - end_px[1]) <= abs(start_px[0] - end_px[0]):
        y = int(round((start_px[1] + end_px[1]) / 2))
        for x in range(min(start_px[0], end_px[0]), max(start_px[0], end_px[0]) + 1):
            y0 = max(0, y - wall_px // 2)
            y1 = min(height, y + wall_px // 2 + 1)
            x0 = max(0, x - 1)
            x1 = min(width, x + 2)
            axis_values.append(round((x - transform.center_x_px) * transform.scale_m_per_px, 3))
            densities.append(float(dark_mask[y0:y1, x0:x1].mean()))
    else:
        x = int(round((start_px[0] + end_px[0]) / 2))
        for z in range(min(start_px[1], end_px[1]), max(start_px[1], end_px[1]) + 1):
            x0 = max(0, x - wall_px // 2)
            x1 = min(width, x + wall_px // 2 + 1)
            z0 = max(0, z - 1)
            z1 = min(height, z + 2)
            axis_values.append(round((z - transform.center_z_px) * transform.scale_m_per_px, 3))
            densities.append(float(dark_mask[z0:z1, x0:x1].mean()))

    if not axis_values:
        return None
    return axis_values, np.array(densities, dtype=np.float32)


def _find_low_density_runs(
    smooth: np.ndarray,
    threshold: float,
    min_pixels: int,
    edge_margin: int,
) -> list[tuple[int, int]]:
    """只保留满足最小宽度且避开墙端边距的低密度连续区间。"""
    runs: list[tuple[int, int]] = []
    start: int | None = None

    for index, value in enumerate(smooth):
        if value <= threshold:
            if start is None:
                start = index
            continue
        if start is None:
            continue
        if index - start >= min_pixels:
            run_start = max(start, edge_margin)
            run_end = min(index - 1, len(smooth) - edge_margin - 1)
            if run_end - run_start + 1 >= min_pixels:
                runs.append((run_start, run_end))
        start = None

    if start is not None and len(smooth) - start >= min_pixels:
        run_start = max(start, edge_margin)
        run_end = len(smooth) - edge_margin - 1
        if run_end - run_start + 1 >= min_pixels:
            runs.append((run_start, run_end))

    return runs


def _world_to_pixel(point: Point2D, transform: RasterTransform) -> tuple[int, int]:
    return (
        int(round(point.x / transform.scale_m_per_px + transform.center_x_px)),
        int(round(point.z / transform.scale_m_per_px + transform.center_z_px)),
    )


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


def _extract_structural_wall_mask(image: np.ndarray) -> np.ndarray:
    """提取户型图中的结构墙体。

    这里故意先做横纵向开运算，保留粗墙线，尽量去掉文字和尺寸标注。
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = (gray < 140).astype(np.uint8) * 255
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((25, 3), np.uint8))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 25), np.uint8))
    structural = cv2.bitwise_or(horizontal, vertical)
    structural = cv2.morphologyEx(structural, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return structural


def _extract_room_polygons(structural_walls: np.ndarray) -> list[list[tuple[float, float]]]:
    height, width = structural_walls.shape
    close_kernel_size = max(31, ((min(height, width) // 18) // 2) * 2 + 1)
    sealed_walls = cv2.morphologyEx(
        structural_walls,
        cv2.MORPH_CLOSE,
        np.ones((close_kernel_size, close_kernel_size), np.uint8),
    )
    free_space = 255 - sealed_walls
    flood = free_space.copy()
    flood_mask = np.zeros((height + 2, width + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 128)
    interior = (flood == 255).astype(np.uint8) * 255

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(interior, 8)
    min_area = max(1200, int(height * width * 0.006))
    polygons: list[list[tuple[float, float]]] = []
    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area < min_area:
            continue
        mask = (labels == label).astype(np.uint8) * 255
        polygon = _component_mask_to_polygon(mask)
        if len(polygon) >= 4:
            polygons.append(polygon)
    return polygons


def _component_mask_to_polygon(mask: np.ndarray) -> list[tuple[float, float]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.01 * perimeter, True).reshape(-1, 2)
    if len(approx) < 4:
        x, y, w, h = cv2.boundingRect(contour)
        return _rect_to_polygon((x, y, x + w, y + h))

    orthogonal = _orthogonalize_polygon(approx)
    simplified = _remove_collinear_points(orthogonal)
    if len(simplified) < 4:
        x, y, w, h = cv2.boundingRect(contour)
        return _rect_to_polygon((x, y, x + w, y + h))
    return simplified


def _orthogonalize_polygon(points: np.ndarray) -> list[tuple[float, float]]:
    orthogonal: list[tuple[float, float]] = [(float(points[0][0]), float(points[0][1]))]

    for raw_point in points[1:]:
        prev_x, prev_y = orthogonal[-1]
        next_x, next_y = float(raw_point[0]), float(raw_point[1])
        if abs(next_x - prev_x) >= abs(next_y - prev_y):
            candidate = (next_x, prev_y)
        else:
            candidate = (prev_x, next_y)
        if _point_distance_xy(candidate, orthogonal[-1]) > 2.0:
            orthogonal.append(candidate)

    if _point_distance_xy(orthogonal[0], orthogonal[-1]) < 2.0 and len(orthogonal) > 1:
        orthogonal.pop()
    return orthogonal


def _remove_collinear_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) <= 4:
        return points
    cleaned: list[tuple[float, float]] = []
    total = len(points)

    for index, point in enumerate(points):
        prev_point = points[index - 1]
        next_point = points[(index + 1) % total]
        if (
            abs(prev_point[0] - point[0]) < 2.0
            and abs(point[0] - next_point[0]) < 2.0
        ) or (
            abs(prev_point[1] - point[1]) < 2.0
            and abs(point[1] - next_point[1]) < 2.0
        ):
            continue
        cleaned.append(point)

    return cleaned or points


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
