from __future__ import annotations

import math
from typing import Sequence

from .models import Point2D


def iter_polygon_edges(polygon: Sequence[Point2D]) -> list[tuple[Point2D, Point2D]]:
    """统一边遍历，避免布局层和 AI 增强层重复维护同一套几何 helper。"""
    if len(polygon) < 2:
        return []
    return [
        (polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    ]


def point_segment_distance(point: Point2D, start: Point2D, end: Point2D) -> float:
    """返回点到线段的最短距离，供贴墙、开口和外墙暴露判断共用。"""
    length_sq = (end.x - start.x) ** 2 + (end.z - start.z) ** 2
    if length_sq <= 1e-9:
        return math.dist((point.x, point.z), (start.x, start.z))
    ratio = (
        ((point.x - start.x) * (end.x - start.x) + (point.z - start.z) * (end.z - start.z))
        / length_sq
    )
    ratio = max(0.0, min(1.0, ratio))
    projection = Point2D(
        x=start.x + (end.x - start.x) * ratio,
        z=start.z + (end.z - start.z) * ratio,
    )
    return math.dist((point.x, point.z), (projection.x, projection.z))


def point_in_polygon(
    point: Point2D,
    polygon: Sequence[Point2D],
    *,
    edge_tolerance: float = 0.06,
) -> bool:
    """统一射线法实现，并把贴边点视作命中，减少布局回退和命中抖动。"""
    inside = False
    for start, end in iter_polygon_edges(polygon):
        intersects = ((start.z > point.z) != (end.z > point.z)) and (
            point.x < (end.x - start.x) * (point.z - start.z) / ((end.z - start.z) or 1e-9) + start.x
        )
        if intersects:
            inside = not inside
    if inside:
        return True
    return any(
        point_segment_distance(point, start, end) <= edge_tolerance
        for start, end in iter_polygon_edges(polygon)
    )


def polygon_centroid(polygon: Sequence[Point2D]) -> Point2D | None:
    """优先返回多边形几何中心；退化多边形由上层做 bbox 回退。"""
    area = 0.0
    centroid_x = 0.0
    centroid_z = 0.0
    for index, point in enumerate(polygon):
        nxt = polygon[(index + 1) % len(polygon)]
        cross = point.x * nxt.z - nxt.x * point.z
        area += cross
        centroid_x += (point.x + nxt.x) * cross
        centroid_z += (point.z + nxt.z) * cross
    if abs(area) <= 1e-9:
        return None
    area *= 0.5
    factor = 1 / (6 * area)
    return Point2D(x=centroid_x * factor, z=centroid_z * factor)


def polygon_bounds(polygon: Sequence[Point2D]) -> tuple[float, float, float, float]:
    xs = [point.x for point in polygon]
    zs = [point.z for point in polygon]
    return min(xs), max(xs), min(zs), max(zs)


def room_center(polygon: Sequence[Point2D]) -> Point2D:
    centroid = polygon_centroid(polygon)
    if centroid is not None:
        return centroid
    min_x, max_x, min_z, max_z = polygon_bounds(polygon)
    return Point2D(x=(min_x + max_x) / 2, z=(min_z + max_z) / 2)


def segment_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    a1, a2 = sorted((start, end))
    b1, b2 = sorted((other_start, other_end))
    return max(0.0, min(a2, b2) - max(a1, b1))


def point_in_polygon_xy(
    point: tuple[float, float],
    polygon: Sequence[tuple[float, float]],
    *,
    edge_tolerance: float = 0.0,
) -> bool:
    x, y = point
    inside = False
    total = len(polygon)
    for index in range(total):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % total]
        denominator = y2 - y1
        if abs(denominator) < 1e-9:
            continue
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / denominator + x1
        )
        if intersects:
            inside = not inside
    if inside or edge_tolerance <= 0:
        return inside
    return any(
        point_segment_distance(
            Point2D(x=point[0], z=point[1]),
            Point2D(x=start[0], z=start[1]),
            Point2D(x=end[0], z=end[1]),
        ) <= edge_tolerance
        for start, end in (
            (polygon[index], polygon[(index + 1) % total])
            for index in range(total)
        )
    )


def polygon_centroid_xy(polygon: Sequence[tuple[float, float]]) -> tuple[float, float]:
    area = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    total = len(polygon)
    for index in range(total):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % total]
        cross = x1 * y2 - x2 * y1
        area += cross
        centroid_x += (x1 + x2) * cross
        centroid_y += (y1 + y2) * cross
    if abs(area) < 1e-6:
        avg_x = sum(point[0] for point in polygon) / len(polygon)
        avg_y = sum(point[1] for point in polygon) / len(polygon)
        return avg_x, avg_y
    factor = 1 / (6 * area)
    return centroid_x * factor, centroid_y * factor


def edge_signature_xy(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    precision: int = 3,
) -> tuple[tuple[float, float], tuple[float, float]]:
    first = (round(start[0], precision), round(start[1], precision))
    second = (round(end[0], precision), round(end[1], precision))
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def edge_signature(
    start: Point2D,
    end: Point2D,
    *,
    precision: int = 3,
) -> tuple[tuple[float, float], tuple[float, float]]:
    first = (round(start.x, precision), round(start.z, precision))
    second = (round(end.x, precision), round(end.z, precision))
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def project_offset(start: Point2D, end: Point2D, point: Point2D) -> float:
    length = math.dist((start.x, start.z), (end.x, end.z))
    if length <= 1e-9:
        return 0.0
    return (
        ((point.x - start.x) * (end.x - start.x) + (point.z - start.z) * (end.z - start.z))
        / length
    )
