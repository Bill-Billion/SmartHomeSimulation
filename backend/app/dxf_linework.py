from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math

from .domain_rules import merge_warnings
from .geometry import edge_signature_xy


ORTHOGONAL_TOLERANCE_M = 0.03
MIN_ROOM_AREA_SQM = 1.0
MIN_ROOM_BBOX_SIDE_M = 0.35


@dataclass(frozen=True)
class AxisSegment:
    orientation: str
    axis: float
    start: float
    end: float


def recover_dxf_room_polygons(
    closed_polygons: list[list[tuple[float, float]]],
    open_polylines: list[list[tuple[float, float]]],
    line_segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[str]]:
    """把 DXF 的闭合轮廓和纯线段图元统一还原成房间 polygon。"""
    warnings: list[str] = []
    polygons: list[list[tuple[float, float]]] = []

    for polygon in closed_polygons:
        prepared_polygon = _prepare_closed_polygon(polygon)
        if prepared_polygon is None:
            warnings.append("DXF 中存在不合法的闭合轮廓，已忽略该候选。")
            continue
        polygons.append(prepared_polygon)

    raw_segments = [*line_segments]
    for polyline in open_polylines:
        raw_segments.extend(
            (polyline[index], polyline[index + 1])
            for index in range(len(polyline) - 1)
        )

    tolerance = _compute_linework_tolerance(raw_segments)
    normalized_segments, segment_warnings = _normalize_axis_segments(raw_segments, tolerance)
    warnings.extend(segment_warnings)

    if normalized_segments:
        merged_segments = _merge_axis_segments(normalized_segments, tolerance)
        recovered_polygons, recovery_warnings = _recover_polygons_from_segments(
            merged_segments,
            tolerance,
        )
        polygons.extend(recovered_polygons)
        warnings.extend(recovery_warnings)

    return _dedupe_polygons(polygons), merge_warnings(warnings)


def _prepare_closed_polygon(
    points: list[tuple[float, float]],
    tolerance: float = ORTHOGONAL_TOLERANCE_M,
) -> list[tuple[float, float]] | None:
    if len(points) < 4:
        return None

    polygon: list[tuple[float, float]] = []
    for point in points:
        candidate = _round_point(point)
        if polygon and _point_distance(candidate, polygon[-1]) <= tolerance * 0.2:
            continue
        polygon.append(candidate)

    if len(polygon) > 1 and _point_distance(polygon[0], polygon[-1]) <= tolerance * 0.2:
        polygon.pop()
    polygon = _simplify_orthogonal_polygon(polygon)

    if len(polygon) < 4:
        return None
    if not _is_orthogonal_polygon(polygon, tolerance):
        return None
    if _polygon_self_intersects(polygon, tolerance):
        return None
    if abs(_polygon_area(polygon)) < MIN_ROOM_AREA_SQM:
        return None
    if _polygon_bbox_min_side(polygon) < MIN_ROOM_BBOX_SIDE_M:
        return None

    if _polygon_area(polygon) < 0:
        polygon = list(reversed(polygon))
    return polygon


def _compute_linework_tolerance(
    line_segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> float:
    lengths = [
        math.dist(start, end)
        for start, end in line_segments
        if math.dist(start, end) > 1e-6
    ]
    if not lengths:
        return ORTHOGONAL_TOLERANCE_M
    return max(ORTHOGONAL_TOLERANCE_M, min(lengths) * 0.05)


def _normalize_axis_segments(
    raw_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    tolerance: float,
) -> tuple[list[AxisSegment], list[str]]:
    if not raw_segments:
        return [], []

    x_values = [point[0] for segment in raw_segments for point in segment]
    z_values = [point[1] for segment in raw_segments for point in segment]
    snapped_x = _cluster_coordinates(x_values, tolerance)
    snapped_z = _cluster_coordinates(z_values, tolerance)

    warnings: list[str] = []
    unsupported_found = False
    normalized: list[AxisSegment] = []
    for start, end in raw_segments:
        x1 = snapped_x[start[0]]
        z1 = snapped_z[start[1]]
        x2 = snapped_x[end[0]]
        z2 = snapped_z[end[1]]
        dx = abs(x2 - x1)
        dz = abs(z2 - z1)

        if max(dx, dz) <= tolerance * 0.5:
            continue
        if dx <= tolerance and dz > tolerance:
            normalized.append(
                AxisSegment(
                    orientation="vertical",
                    axis=round((x1 + x2) / 2, 4),
                    start=round(min(z1, z2), 4),
                    end=round(max(z1, z2), 4),
                )
            )
            continue
        if dz <= tolerance and dx > tolerance:
            normalized.append(
                AxisSegment(
                    orientation="horizontal",
                    axis=round((z1 + z2) / 2, 4),
                    start=round(min(x1, x2), 4),
                    end=round(max(x1, x2), 4),
                )
            )
            continue
        unsupported_found = True

    if unsupported_found:
        warnings.append("当前 DXF 仅支持正交线段，已忽略曲线或斜线图元。")
    return normalized, warnings


def _cluster_coordinates(values: list[float], tolerance: float) -> dict[float, float]:
    sorted_values = sorted(values)
    clusters: list[list[float]] = []
    current_cluster: list[float] = []

    for value in sorted_values:
        if not current_cluster or abs(value - current_cluster[-1]) <= tolerance:
            current_cluster.append(value)
            continue
        clusters.append(current_cluster)
        current_cluster = [value]

    if current_cluster:
        clusters.append(current_cluster)

    snapped: dict[float, float] = {}
    for cluster in clusters:
        center = round(sum(cluster) / len(cluster), 4)
        for value in cluster:
            snapped[value] = center
    return snapped


def _merge_axis_segments(
    segments: list[AxisSegment],
    tolerance: float,
) -> list[AxisSegment]:
    grouped: dict[tuple[str, float], list[tuple[float, float]]] = defaultdict(list)
    for segment in segments:
        grouped[(segment.orientation, segment.axis)].append((segment.start, segment.end))

    merged: list[AxisSegment] = []
    for (orientation, axis), ranges in grouped.items():
        current_start = None
        current_end = None
        for range_start, range_end in sorted(ranges):
            if current_start is None:
                current_start = range_start
                current_end = range_end
                continue
            if range_start <= current_end + tolerance:
                current_end = max(current_end, range_end)
                continue
            merged.append(
                AxisSegment(
                    orientation=orientation,
                    axis=axis,
                    start=round(current_start, 4),
                    end=round(current_end, 4),
                )
            )
            current_start = range_start
            current_end = range_end
        if current_start is not None and current_end is not None:
            merged.append(
                AxisSegment(
                    orientation=orientation,
                    axis=axis,
                    start=round(current_start, 4),
                    end=round(current_end, 4),
                )
            )
    return merged


def _recover_polygons_from_segments(
    segments: list[AxisSegment],
    tolerance: float,
) -> tuple[list[list[tuple[float, float]]], list[str]]:
    if not segments:
        return [], []

    x_coords, z_coords = _collect_grid_coordinates(segments, tolerance)
    if len(x_coords) < 3 or len(z_coords) < 3:
        return [], ["DXF 线段不足以恢复房间轮廓，已按保守轮廓继续生成。"]

    # 先把线段投影成网格阻塞边，再从 padding 外侧做 flood fill 区分室内外。
    blocked_vertical, blocked_horizontal = _build_blocked_edges(segments, x_coords, z_coords, tolerance)
    outside_cells = _flood_outside_cells(x_coords, z_coords, blocked_vertical, blocked_horizontal)
    room_components = _collect_room_components(
        x_coords,
        z_coords,
        blocked_vertical,
        blocked_horizontal,
        outside_cells,
    )

    polygons: list[list[tuple[float, float]]] = []
    warnings: list[str] = []
    degraded_count = 0
    for component in room_components:
        if _component_area(component, x_coords, z_coords) < MIN_ROOM_AREA_SQM:
            continue
        if _component_bbox_min_side(component, x_coords, z_coords) < MIN_ROOM_BBOX_SIDE_M:
            continue
        polygon = _trace_component_polygon(component, x_coords, z_coords)
        if polygon is None:
            polygon = _component_bbox_polygon(component, x_coords, z_coords)
            degraded_count += 1
        polygons.append(polygon)

    if polygons and degraded_count:
        warnings.append("部分 DXF 房间轮廓恢复不完整，已按局部保守轮廓继续生成。")
    if not polygons:
        warnings.append("DXF 线段未形成可恢复的完整房间，已按保守轮廓继续生成。")
    return polygons, warnings


def _collect_grid_coordinates(
    segments: list[AxisSegment],
    tolerance: float,
) -> tuple[list[float], list[float]]:
    x_coords: set[float] = set()
    z_coords: set[float] = set()
    for segment in segments:
        if segment.orientation == "vertical":
            x_coords.add(segment.axis)
            z_coords.add(segment.start)
            z_coords.add(segment.end)
        else:
            z_coords.add(segment.axis)
            x_coords.add(segment.start)
            x_coords.add(segment.end)

    if not x_coords or not z_coords:
        return [], []

    min_x = min(x_coords)
    max_x = max(x_coords)
    min_z = min(z_coords)
    max_z = max(z_coords)
    padding = max(0.5, tolerance * 4)
    x_coords.update({round(min_x - padding, 4), round(max_x + padding, 4)})
    z_coords.update({round(min_z - padding, 4), round(max_z + padding, 4)})
    return sorted(x_coords), sorted(z_coords)


def _build_blocked_edges(
    segments: list[AxisSegment],
    x_coords: list[float],
    z_coords: list[float],
    tolerance: float,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    x_index = {value: index for index, value in enumerate(x_coords)}
    z_index = {value: index for index, value in enumerate(z_coords)}
    blocked_vertical: set[tuple[int, int]] = set()
    blocked_horizontal: set[tuple[int, int]] = set()

    for segment in segments:
        if segment.orientation == "vertical":
            boundary_index = x_index.get(segment.axis)
            if boundary_index is None or boundary_index <= 0 or boundary_index >= len(x_coords) - 1:
                continue
            for cell_index in range(len(z_coords) - 1):
                if (
                    z_coords[cell_index] >= segment.start - tolerance
                    and z_coords[cell_index + 1] <= segment.end + tolerance
                ):
                    blocked_vertical.add((boundary_index, cell_index))
            continue

        boundary_index = z_index.get(segment.axis)
        if boundary_index is None or boundary_index <= 0 or boundary_index >= len(z_coords) - 1:
            continue
        for cell_index in range(len(x_coords) - 1):
            if (
                x_coords[cell_index] >= segment.start - tolerance
                and x_coords[cell_index + 1] <= segment.end + tolerance
            ):
                blocked_horizontal.add((cell_index, boundary_index))

    return blocked_vertical, blocked_horizontal


def _flood_outside_cells(
    x_coords: list[float],
    z_coords: list[float],
    blocked_vertical: set[tuple[int, int]],
    blocked_horizontal: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    max_x_index = len(x_coords) - 2
    max_z_index = len(z_coords) - 2
    visited: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()

    for x_index in range(max_x_index + 1):
        queue.append((x_index, 0))
        queue.append((x_index, max_z_index))
    for z_index in range(max_z_index + 1):
        queue.append((0, z_index))
        queue.append((max_x_index, z_index))

    while queue:
        cell = queue.popleft()
        if cell in visited:
            continue
        visited.add(cell)
        for neighbor in _iter_neighbor_cells(cell, max_x_index, max_z_index, blocked_vertical, blocked_horizontal):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


def _collect_room_components(
    x_coords: list[float],
    z_coords: list[float],
    blocked_vertical: set[tuple[int, int]],
    blocked_horizontal: set[tuple[int, int]],
    outside_cells: set[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    max_x_index = len(x_coords) - 2
    max_z_index = len(z_coords) - 2
    visited: set[tuple[int, int]] = set(outside_cells)
    components: list[list[tuple[int, int]]] = []

    for x_index in range(max_x_index + 1):
        for z_index in range(max_z_index + 1):
            start = (x_index, z_index)
            if start in visited:
                continue
            queue: deque[tuple[int, int]] = deque([start])
            component: list[tuple[int, int]] = []
            while queue:
                cell = queue.popleft()
                if cell in visited:
                    continue
                visited.add(cell)
                component.append(cell)
                for neighbor in _iter_neighbor_cells(
                    cell,
                    max_x_index,
                    max_z_index,
                    blocked_vertical,
                    blocked_horizontal,
                ):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if component:
                components.append(component)
    return components


def _iter_neighbor_cells(
    cell: tuple[int, int],
    max_x_index: int,
    max_z_index: int,
    blocked_vertical: set[tuple[int, int]],
    blocked_horizontal: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    x_index, z_index = cell
    neighbors: list[tuple[int, int]] = []
    if x_index > 0 and (x_index, z_index) not in blocked_vertical:
        neighbors.append((x_index - 1, z_index))
    if x_index < max_x_index and (x_index + 1, z_index) not in blocked_vertical:
        neighbors.append((x_index + 1, z_index))
    if z_index > 0 and (x_index, z_index) not in blocked_horizontal:
        neighbors.append((x_index, z_index - 1))
    if z_index < max_z_index and (x_index, z_index + 1) not in blocked_horizontal:
        neighbors.append((x_index, z_index + 1))
    return neighbors


def _component_area(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> float:
    return sum(
        (x_coords[x_index + 1] - x_coords[x_index]) * (z_coords[z_index + 1] - z_coords[z_index])
        for x_index, z_index in component
    )


def _component_bbox_min_side(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> float:
    min_x, min_z, max_x, max_z = _component_bounds(component, x_coords, z_coords)
    return min(max_x - min_x, max_z - min_z)


def _component_bounds(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> tuple[float, float, float, float]:
    min_x = min(x_coords[x_index] for x_index, _ in component)
    max_x = max(x_coords[x_index + 1] for x_index, _ in component)
    min_z = min(z_coords[z_index] for _, z_index in component)
    max_z = max(z_coords[z_index + 1] for _, z_index in component)
    return min_x, min_z, max_x, max_z


def _trace_component_polygon(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> list[tuple[float, float]] | None:
    # 连通单元先转成边集，再追出最大的外轮廓，避免 L 形房间退回整块 bbox。
    edges = _build_component_edges(component, x_coords, z_coords)
    cycles = _extract_edge_cycles(edges)
    if not cycles:
        return None
    polygon = max(cycles, key=lambda cycle: abs(_polygon_area(cycle)))
    polygon = _simplify_orthogonal_polygon(polygon)
    if len(polygon) < 4 or _polygon_self_intersects(polygon, ORTHOGONAL_TOLERANCE_M):
        return None
    if _polygon_area(polygon) < 0:
        polygon = list(reversed(polygon))
    return polygon


def _build_component_edges(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for x_index, z_index in component:
        x1 = x_coords[x_index]
        x2 = x_coords[x_index + 1]
        z1 = z_coords[z_index]
        z2 = z_coords[z_index + 1]
        cell_edges = [
            (_round_point((x1, z1)), _round_point((x2, z1))),
            (_round_point((x2, z1)), _round_point((x2, z2))),
            (_round_point((x2, z2)), _round_point((x1, z2))),
            (_round_point((x1, z2)), _round_point((x1, z1))),
        ]
        for start, end in cell_edges:
            reverse_edge = (end, start)
            if reverse_edge in edges:
                edges.remove(reverse_edge)
            else:
                edges.add((start, end))
    return edges


def _extract_edge_cycles(
    edges: set[tuple[tuple[float, float], tuple[float, float]]]
) -> list[list[tuple[float, float]]]:
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for start, end in edges:
        adjacency[start].add(end)

    cycles: list[list[tuple[float, float]]] = []
    while adjacency:
        start = min(adjacency)
        polygon = [start]
        current = start
        steps = 0
        while True:
            next_points = adjacency.get(current)
            if not next_points:
                return []
            next_point = min(next_points)
            next_points.remove(next_point)
            if not next_points:
                adjacency.pop(current, None)
            current = next_point
            steps += 1
            if current == start:
                break
            polygon.append(current)
            if steps > len(edges) + 4:
                return []
        if len(polygon) >= 4:
            cycles.append(polygon)
    return cycles


def _component_bbox_polygon(
    component: list[tuple[int, int]],
    x_coords: list[float],
    z_coords: list[float],
) -> list[tuple[float, float]]:
    min_x, min_z, max_x, max_z = _component_bounds(component, x_coords, z_coords)
    return [
        _round_point((min_x, min_z)),
        _round_point((max_x, min_z)),
        _round_point((max_x, max_z)),
        _round_point((min_x, max_z)),
    ]


def _simplify_orthogonal_polygon(
    polygon: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not polygon:
        return []

    simplified: list[tuple[float, float]] = []
    total = len(polygon)
    for index, point in enumerate(polygon):
        previous = polygon[index - 1]
        current = point
        nxt = polygon[(index + 1) % total]
        if _point_distance(previous, current) <= 1e-6:
            continue
        if _is_collinear(previous, current, nxt):
            continue
        simplified.append(_round_point(current))
    return simplified


def _is_collinear(
    previous: tuple[float, float],
    current: tuple[float, float],
    nxt: tuple[float, float],
) -> bool:
    return (
        abs(previous[0] - current[0]) <= 1e-6 and abs(current[0] - nxt[0]) <= 1e-6
    ) or (
        abs(previous[1] - current[1]) <= 1e-6 and abs(current[1] - nxt[1]) <= 1e-6
    )


def _is_orthogonal_polygon(
    polygon: list[tuple[float, float]],
    tolerance: float,
) -> bool:
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        dx = abs(end[0] - start[0])
        dz = abs(end[1] - start[1])
        if max(dx, dz) <= tolerance * 0.5:
            return False
        if dx > tolerance and dz > tolerance:
            return False
    return True


def _polygon_self_intersects(
    polygon: list[tuple[float, float]],
    tolerance: float,
) -> bool:
    segments = [
        (polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    ]
    total = len(segments)
    for first_index, first in enumerate(segments):
        for second_index in range(first_index + 1, total):
            if abs(first_index - second_index) <= 1:
                continue
            if first_index == 0 and second_index == total - 1:
                continue
            second = segments[second_index]
            if _segments_intersect(first[0], first[1], second[0], second[1], tolerance):
                return True
    return False


def _segments_intersect(
    start_a: tuple[float, float],
    end_a: tuple[float, float],
    start_b: tuple[float, float],
    end_b: tuple[float, float],
    tolerance: float,
) -> bool:
    def orientation(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])

    def on_segment(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> bool:
        return (
            min(first[0], third[0]) - tolerance <= second[0] <= max(first[0], third[0]) + tolerance
            and min(first[1], third[1]) - tolerance <= second[1] <= max(first[1], third[1]) + tolerance
        )

    o1 = orientation(start_a, end_a, start_b)
    o2 = orientation(start_a, end_a, end_b)
    o3 = orientation(start_b, end_b, start_a)
    o4 = orientation(start_b, end_b, end_a)

    if (o1 > tolerance and o2 < -tolerance or o1 < -tolerance and o2 > tolerance) and (
        o3 > tolerance and o4 < -tolerance or o3 < -tolerance and o4 > tolerance
    ):
        return True
    if abs(o1) <= tolerance and on_segment(start_a, start_b, end_a):
        return True
    if abs(o2) <= tolerance and on_segment(start_a, end_b, end_a):
        return True
    if abs(o3) <= tolerance and on_segment(start_b, start_a, end_b):
        return True
    if abs(o4) <= tolerance and on_segment(start_b, end_a, end_b):
        return True
    return False


def _dedupe_polygons(
    polygons: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    deduped: list[list[tuple[float, float]]] = []
    seen: set[frozenset[tuple[tuple[float, float], tuple[float, float]]]] = set()
    for polygon in polygons:
        signature = frozenset(
            edge_signature_xy(point, polygon[(index + 1) % len(polygon)])
            for index, point in enumerate(polygon)
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(polygon)
    return deduped


def _polygon_area(polygon: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, point in enumerate(polygon):
        nxt = polygon[(index + 1) % len(polygon)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return area / 2


def _polygon_bbox_min_side(polygon: list[tuple[float, float]]) -> float:
    min_x = min(point[0] for point in polygon)
    max_x = max(point[0] for point in polygon)
    min_z = min(point[1] for point in polygon)
    max_z = max(point[1] for point in polygon)
    return min(max_x - min_x, max_z - min_z)


def _point_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.dist(first, second)


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    return round(point[0], 4), round(point[1], 4)
