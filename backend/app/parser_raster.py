from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .domain_rules import merge_warnings
from .floorplan_builder import (
    _build_floorplan_from_polygons,
    _fallback_rect,
    _normalize_polygons,
    _point_distance_xy,
    _polygon_area,
    _rect_to_polygon,
)
from .models import FloorplanSpec, OpeningSpec, OpeningType, Point2D, SourceType, WallSegment
from .parser_raster_decorated import classify_raster_style, extract_decorated_plan
from .semantic_rules import RoomSemanticHint, _normalize_room_semantic_text


@dataclass(frozen=True)
class RasterTransform:
    """记录像素坐标和米制坐标之间的双向映射。"""

    scale_m_per_px: float
    center_x_px: float
    center_z_px: float


@dataclass(frozen=True)
class RasterWallAnalysis:
    """栅格图墙体提纯结果，供房间提取和 warning 生成复用。"""

    structural_walls: np.ndarray
    wall_thickness_px: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PolygonExtractionResult:
    """房间轮廓提取结果，记录是否发生了保守降级。"""

    polygon: list[tuple[float, float]]
    degraded: bool


def _parse_raster_floorplan(source_path: Path) -> FloorplanSpec:
    return _parse_raster_floorplan_with_options(source_path)


def _parse_raster_floorplan_with_options(
    source_path: Path,
    *,
    scan_func: Callable[[np.ndarray], list[tuple[str, tuple[float, float], float]]] | None = None,
) -> FloorplanSpec:
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

    style = classify_raster_style(image)
    if style == "decorated":
        decorated_plan = extract_decorated_plan(
            image,
            structural_wall_extractor=_extract_structural_wall_mask,
            room_polygon_extractor=_extract_room_polygons,
            legacy_polygon_extractor=_extract_legacy_room_polygons,
            polygon_quality_scorer=_room_polygon_quality_score,
        )
        wall_analysis = RasterWallAnalysis(
            structural_walls=decorated_plan.structural_walls,
            wall_thickness_px=decorated_plan.wall_thickness_px,
            warnings=decorated_plan.warnings,
        )
        room_polygons_px = decorated_plan.room_polygons_px
        geometry_warnings = merge_warnings(wall_analysis.warnings)
    else:
        wall_analysis = _extract_structural_wall_mask(image)
        room_polygons_px, extraction_warnings = _extract_room_polygons(
            wall_analysis.structural_walls,
            wall_analysis.wall_thickness_px,
        )
        geometry_warnings = merge_warnings(wall_analysis.warnings, extraction_warnings)
        if len(room_polygons_px) <= 1:
            legacy_polygons_px, legacy_warnings = _extract_legacy_room_polygons(image)
            adaptive_score = _room_polygon_quality_score(
                room_polygons_px,
                geometry_warnings,
                image_shape=image.shape[:2],
            )
            legacy_score = _room_polygon_quality_score(
                legacy_polygons_px,
                legacy_warnings,
                image_shape=image.shape[:2],
            )
            if legacy_score > adaptive_score + 0.75:
                room_polygons_px = legacy_polygons_px
                geometry_warnings = merge_warnings(wall_analysis.warnings, legacy_warnings)
    if not room_polygons_px:
        geometry_warnings = merge_warnings(
            geometry_warnings,
            ["平面图识别置信度较低，已退化为单房间壳体。"],
        )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        room_polygons_px = [_rect_to_polygon(_fallback_rect(gray))]

    world_polygons, width_m, depth_m, scale, transform = _normalize_polygons(room_polygons_px)
    semantic_hints, semantic_warnings = _extract_raster_semantic_hints(
        image,
        transform,
        wall_analysis.structural_walls,
        wall_analysis.wall_thickness_px,
        scan_func=scan_func,
    )
    warnings = merge_warnings(geometry_warnings, semantic_warnings)
    floorplan = _build_floorplan_from_polygons(
        world_polygons,
        source_type=source_type,
        bounds_width_m=width_m,
        bounds_depth_m=depth_m,
        scale_m_per_unit=scale,
        warnings=warnings,
        semantic_hints=semantic_hints,
    )
    if geometry_warnings:
        floorplan.confidence = min(floorplan.confidence, 0.72 if len(floorplan.rooms) > 1 else 0.52)
    floorplan.openings = _refine_raster_openings(image, floorplan, transform)
    return floorplan


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


def _extract_raster_semantic_hints(
    image: np.ndarray,
    transform: RasterTransform,
    structural_walls: np.ndarray,
    wall_thickness_px: int,
    *,
    scan_func: Callable[[np.ndarray], list[tuple[str, tuple[float, float], float]]] | None = None,
) -> tuple[list[RoomSemanticHint], list[str]]:
    """OCR 只服务房间语义，不参与几何分割，失败时必须无损回退。"""
    try:
        import pytesseract
    except ImportError:
        return [], ["房间文字识别组件不可用，已回退到几何语义规则。"]

    try:
        languages = set(pytesseract.get_languages(config=""))
    except pytesseract.TesseractNotFoundError:
        return [], ["系统未找到文字识别引擎，已回退到几何语义规则。"]
    except Exception:  # noqa: BLE001
        return [], ["房间文字识别初始化失败，已回退到几何语义规则。"]

    if not {"eng", "chi_sim"}.issubset(languages):
        return [], ["房间文字识别语言包不完整，已回退到几何语义规则。"]

    scanner = scan_func or _run_tesseract_semantic_scan
    try:
        text_layer = _build_raster_text_layer(image, structural_walls, wall_thickness_px)
        entries = scanner(text_layer)
    except Exception:  # noqa: BLE001
        return [], ["房间文字识别失败，已回退到几何语义规则。"]

    hints: list[RoomSemanticHint] = []
    for raw_text, center_px, _confidence in entries:
        room_type = _normalize_room_semantic_text(raw_text)
        if room_type is None:
            continue
        hints.append(
            RoomSemanticHint(
                room_type=room_type,
                center=(
                    round((center_px[0] - transform.center_x_px) * transform.scale_m_per_px, 3),
                    round((center_px[1] - transform.center_z_px) * transform.scale_m_per_px, 3),
                ),
                raw_text=raw_text,
                source="ocr",
            )
        )
    return hints, []


def _run_tesseract_semantic_scan(
    image: np.ndarray,
) -> list[tuple[str, tuple[float, float], float]]:
    """统一封装 Tesseract 调用，便于测试时单独打桩。"""
    import pytesseract

    scale_factor = 1.8
    if image.ndim == 3:
        prepared = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        prepared = image.copy()
    prepared = cv2.resize(prepared, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
    prepared = cv2.medianBlur(prepared, 3)
    data = pytesseract.image_to_data(
        prepared,
        lang="eng+chi_sim",
        config="--psm 11",
        output_type=pytesseract.Output.DICT,
    )

    grouped_tokens: dict[tuple[int, int, int], list[tuple[int, int, int, int, str, float]]] = {}
    total = len(data.get("text", []))
    for index in range(total):
        raw_text = str(data["text"][index]).strip()
        if not raw_text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < 25:
            continue
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        grouped_tokens.setdefault(key, []).append(
            (
                int(data["left"][index]),
                int(data["top"][index]),
                int(data["width"][index]),
                int(data["height"][index]),
                raw_text,
                confidence,
            )
        )

    entries: list[tuple[str, tuple[float, float], float]] = []
    for tokens in grouped_tokens.values():
        left = min(token[0] for token in tokens) / scale_factor
        top = min(token[1] for token in tokens) / scale_factor
        right = max(token[0] + token[2] for token in tokens) / scale_factor
        bottom = max(token[1] + token[3] for token in tokens) / scale_factor
        phrase = " ".join(token[4] for token in tokens).strip()
        confidence = sum(token[5] for token in tokens) / len(tokens)
        if phrase:
            entries.append(
                (
                    phrase,
                    ((left + right) / 2, (top + bottom) / 2),
                    confidence,
                )
            )
    return entries


def _build_raster_text_layer(
    image: np.ndarray,
    structural_walls: np.ndarray,
    wall_thickness_px: int,
) -> np.ndarray:
    """把结构墙从原图里剥掉，只把更像文字的暗色组件交给 OCR。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = _threshold_dark_pixels(gray)
    wall_mask = cv2.dilate(
        structural_walls,
        np.ones((_odd_size(max(3, wall_thickness_px // 2)),) * 2, np.uint8),
        iterations=1,
    )
    text_mask = cv2.bitwise_and(binary, cv2.bitwise_not(wall_mask))
    text_mask = cv2.morphologyEx(
        text_mask,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8),
    )
    text_mask = cv2.dilate(text_mask, np.ones((3, 3), np.uint8), iterations=1)
    return 255 - text_mask


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
    """沿墙体中心线做一维剖面，找出明显偏亮的连续区间作为开口候选。"""
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


def _odd_size(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def _room_polygon_quality_score(
    polygons: list[list[tuple[float, float]]],
    warnings: list[str] | tuple[str, ...],
    *,
    image_shape: tuple[int, int] | None = None,
) -> float:
    """在主链和保守回退链之间选出更可信的房间集合。"""
    if not polygons:
        return -1_000_000.0

    areas = [abs(_polygon_area(polygon)) for polygon in polygons if len(polygon) >= 4]
    if not areas:
        return -1_000_000.0

    total_area = sum(areas)
    non_rectangular_rooms = sum(1 for polygon in polygons if len(polygon) > 4)
    tiny_threshold = max(900.0, total_area * 0.02)
    tiny_rooms = sum(1 for area in areas if area < tiny_threshold)
    degraded_penalty = sum(2.5 for warning in warnings if "保守" in warning or "退化" in warning)
    dominant_room_ratio = max(areas) / max(total_area, 1.0)
    dominant_penalty = max(0.0, dominant_room_ratio - 0.55) * 48.0
    if dominant_room_ratio >= 0.75:
        dominant_penalty += 12.0

    bbox_penalty = 0.0
    image_area = None
    if image_shape is not None:
        image_area = float(image_shape[0] * image_shape[1])
        for polygon in polygons:
            if len(polygon) != 4:
                continue
            polygon_area = abs(_polygon_area(polygon))
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            bbox_area = max((max(xs) - min(xs)) * (max(ys) - min(ys)), 1.0)
            if polygon_area / bbox_area < 0.94:
                continue
            if polygon_area / image_area >= 0.82:
                bbox_penalty += 18.0

    coverage_denominator = image_area or 20_000.0
    coverage_score = min(total_area / coverage_denominator * 12.0, 12.0)
    room_count_bonus = min(len(polygons), 8) * 9.0
    if len(polygons) == 1:
        room_count_bonus -= 18.0

    return (
        room_count_bonus
        + non_rectangular_rooms * 2.8
        + coverage_score
        - tiny_rooms * 2.5
        - degraded_penalty
        - dominant_penalty
        - bbox_penalty
    )


def _polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    total = len(points)
    if total < 4:
        return False

    for index in range(total):
        a1 = points[index]
        a2 = points[(index + 1) % total]
        for other_index in range(index + 1, total):
            if abs(index - other_index) <= 1:
                continue
            if index == 0 and other_index == total - 1:
                continue
            b1 = points[other_index]
            b2 = points[(other_index + 1) % total]
            if _segments_intersect(a1, a2, b1, b2):
                return True
    return False


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    return o1 * o2 < 0 and o3 * o4 < 0


def _orientation(
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
) -> float:
    return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])


def _extract_structural_wall_mask(image: np.ndarray) -> RasterWallAnalysis:
    """提取户型图中的结构墙体，并尽量把文字标注从墙线里剥离出来。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = _threshold_dark_pixels(gray)
    wall_thickness_px = _estimate_wall_thickness_px(binary)
    line_length = _odd_size(max(wall_thickness_px * 4, 17))
    line_width = max(1, wall_thickness_px // 2)

    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        np.ones((line_width, line_length), np.uint8),
    )
    vertical = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        np.ones((line_length, line_width), np.uint8),
    )
    structural = cv2.bitwise_or(horizontal, vertical)
    structural = cv2.morphologyEx(
        structural,
        cv2.MORPH_CLOSE,
        np.ones((_odd_size(max(wall_thickness_px * 2, 5)),) * 2, np.uint8),
    )
    structural, removed_components, removed_area = _filter_structural_components(
        structural,
        wall_thickness_px,
    )

    warnings: list[str] = []
    if np.count_nonzero(structural) == 0:
        warnings.append("原图墙线不够稳定，已按保守策略补全结构。")
        structural = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            np.ones((_odd_size(max(wall_thickness_px, 5)),) * 2, np.uint8),
        )
    elif removed_components >= 8 or removed_area >= wall_thickness_px * wall_thickness_px * 8:
        warnings.append("检测到较多标注或零碎符号，已优先保留结构墙线。")

    return RasterWallAnalysis(
        structural_walls=structural,
        wall_thickness_px=wall_thickness_px,
        warnings=tuple(merge_warnings(warnings)),
    )


def _extract_room_polygons(
    structural_walls: np.ndarray,
    wall_thickness_px: int,
    *,
    close_multiplier: int = 3,
) -> tuple[list[list[tuple[float, float]]], list[str]]:
    height, width = structural_walls.shape
    close_kernel_size = _odd_size(max(wall_thickness_px * close_multiplier, 15))
    sealed_walls = cv2.morphologyEx(
        structural_walls,
        cv2.MORPH_CLOSE,
        np.ones((close_kernel_size, close_kernel_size), np.uint8),
    )
    sealed_walls = cv2.dilate(
        sealed_walls,
        np.ones((max(1, wall_thickness_px // 3),) * 2, np.uint8),
        iterations=1,
    )
    free_space = 255 - sealed_walls
    flood = free_space.copy()
    flood_mask = np.zeros((height + 2, width + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 128)
    interior = (flood == 255).astype(np.uint8) * 255
    interior = cv2.morphologyEx(
        interior,
        cv2.MORPH_OPEN,
        np.ones((max(3, wall_thickness_px // 2),) * 2, np.uint8),
    )

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(interior, 8)
    min_area = max(wall_thickness_px * wall_thickness_px * 18, int(height * width * 0.0025))
    polygons: list[list[tuple[float, float]]] = []
    degraded_components = 0

    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area < min_area:
            continue
        mask = (labels == label).astype(np.uint8) * 255
        polygon_result = _component_mask_to_polygon(mask, wall_thickness_px)
        if len(polygon_result.polygon) >= 4:
            polygons.append(polygon_result.polygon)
            if polygon_result.degraded:
                degraded_components += 1

    warnings: list[str] = []
    if degraded_components:
        warnings.append("部分房间轮廓不够完整，已用保守轮廓继续生成。")
    if num_labels > 2 and not polygons:
        warnings.append("原图墙线存在断裂，已尝试按保守方式补全户型。")
    return polygons, warnings


def _extract_legacy_room_polygons(image: np.ndarray) -> tuple[list[list[tuple[float, float]]], list[str]]:
    """保留上一版更保守的墙线闭合策略，作为复杂图纸的兜底路径。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = (gray < 140).astype(np.uint8) * 255
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((25, 3), np.uint8))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 25), np.uint8))
    structural = cv2.bitwise_or(horizontal, vertical)
    structural = cv2.morphologyEx(structural, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    height, width = structural.shape
    close_kernel_size = max(31, ((min(height, width) // 18) // 2) * 2 + 1)
    sealed_walls = cv2.morphologyEx(
        structural,
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
    wall_thickness_px = max(8, min(12, _estimate_wall_thickness_px(structural)))
    polygons: list[list[tuple[float, float]]] = []
    degraded_components = 0

    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area < min_area:
            continue
        mask = (labels == label).astype(np.uint8) * 255
        polygon_result = _component_mask_to_polygon(mask, wall_thickness_px)
        if len(polygon_result.polygon) >= 4:
            polygons.append(polygon_result.polygon)
            if polygon_result.degraded:
                degraded_components += 1

    warnings: list[str] = []
    if degraded_components:
        warnings.append("部分房间轮廓不够完整，已用保守轮廓继续生成。")
    return polygons, warnings


def _component_mask_to_polygon(mask: np.ndarray, wall_thickness_px: int) -> PolygonExtractionResult:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return PolygonExtractionResult(polygon=[], degraded=True)
    contour = max(contours, key=cv2.contourArea)
    contour_area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(
        contour,
        max(wall_thickness_px * 0.55, perimeter * 0.0045),
        True,
    ).reshape(-1, 2)
    if len(approx) < 4:
        return PolygonExtractionResult(polygon=_contour_bbox_polygon(contour), degraded=True)

    orthogonal = _orthogonalize_polygon(approx, wall_thickness_px)
    simplified = _remove_collinear_points(orthogonal, tolerance=max(1.5, wall_thickness_px * 0.35))
    simplified = _remove_short_edges(simplified, min_length=max(2.0, wall_thickness_px * 0.55))
    simplified = _remove_collinear_points(simplified, tolerance=max(1.5, wall_thickness_px * 0.35))
    if len(simplified) < 4 or _polygon_self_intersects(simplified):
        return PolygonExtractionResult(polygon=_contour_bbox_polygon(contour), degraded=True)

    polygon_area = abs(_polygon_area(simplified))
    area_ratio = polygon_area / max(contour_area, 1.0)
    if area_ratio < 0.45:
        return PolygonExtractionResult(polygon=_contour_bbox_polygon(contour), degraded=True)

    degraded = area_ratio < 0.72 or area_ratio > 1.28
    return PolygonExtractionResult(polygon=simplified, degraded=degraded)


def _threshold_dark_pixels(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    otsu_value, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    conservative_threshold = min(float(otsu_value), max(80.0, float(np.median(blurred)) * 0.9))
    _, binary = cv2.threshold(blurred, int(conservative_threshold), 255, cv2.THRESH_BINARY_INV)
    return binary


def _estimate_wall_thickness_px(binary: np.ndarray) -> int:
    positive_pixels = binary > 0
    if not np.any(positive_pixels):
        return 10

    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    positive_distance = distance[positive_pixels]
    foreground_ratio = float(np.count_nonzero(binary)) / float(binary.size)
    percentile = 20 if foreground_ratio > 0.25 else 80
    thickness = float(np.percentile(positive_distance, percentile)) * 2.0 if positive_distance.size else 4.0
    image_scale = min(binary.shape) / 120
    estimate = max(thickness, image_scale)
    return int(np.clip(round(estimate), 6, 20))


def _filter_structural_components(
    mask: np.ndarray,
    wall_thickness_px: int,
) -> tuple[np.ndarray, int, int]:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    filtered = np.zeros_like(mask)
    removed_components = 0
    removed_area = 0

    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        long_side = max(width, height)
        short_side = min(width, height)
        keep_component = (
            area >= max(wall_thickness_px * wall_thickness_px * 2, 80)
            or (
                long_side >= wall_thickness_px * 4
                and short_side >= max(2, wall_thickness_px // 3)
                and area >= wall_thickness_px * 2
            )
        )
        if keep_component:
            filtered[labels == label] = 255
        else:
            removed_components += 1
            removed_area += area

    if np.count_nonzero(filtered) == 0:
        return mask, 0, 0
    return filtered, removed_components, removed_area


def _contour_bbox_polygon(contour: np.ndarray) -> list[tuple[float, float]]:
    x, y, w, h = cv2.boundingRect(contour)
    return _rect_to_polygon((x, y, x + w, y + h))


def _orthogonalize_polygon(points: np.ndarray, wall_thickness_px: int) -> list[tuple[float, float]]:
    raw = [(float(point[0]), float(point[1])) for point in points]
    if len(raw) > 1 and _point_distance_xy(raw[0], raw[-1]) < 1.0:
        raw.pop()
    if len(raw) < 4:
        return raw

    tolerance = max(1.5, wall_thickness_px * 0.25)
    orthogonal: list[tuple[float, float]] = [raw[0]]
    total = len(raw)

    for index in range(1, total):
        current = raw[index]
        next_point = raw[(index + 1) % total]
        prev_x, prev_y = orthogonal[-1]
        dx = current[0] - prev_x
        dy = current[1] - prev_y
        if abs(dx) < tolerance and abs(dy) < tolerance:
            continue

        horizontal_first = abs(dx) >= abs(dy)
        elbow = (current[0], prev_y) if horizontal_first else (prev_x, current[1])
        if _point_distance_xy(elbow, orthogonal[-1]) > tolerance:
            orthogonal.append(elbow)

        next_dx = next_point[0] - current[0]
        next_dy = next_point[1] - current[1]
        next_horizontal = abs(next_dx) >= abs(next_dy)
        if horizontal_first != next_horizontal:
            corner = (current[0], current[1])
            if _point_distance_xy(corner, orthogonal[-1]) > tolerance:
                orthogonal.append(corner)

    return orthogonal


def _remove_short_edges(points: list[tuple[float, float]], min_length: float) -> list[tuple[float, float]]:
    if len(points) <= 4:
        return points

    simplified: list[tuple[float, float]] = []
    total = len(points)
    for index, point in enumerate(points):
        prev_point = points[index - 1]
        next_point = points[(index + 1) % total]
        if _point_distance_xy(prev_point, point) < min_length and _point_distance_xy(point, next_point) < min_length:
            continue
        simplified.append(point)
    return simplified or points


def _remove_collinear_points(
    points: list[tuple[float, float]],
    *,
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 4:
        return points
    cleaned: list[tuple[float, float]] = []
    total = len(points)

    for index, point in enumerate(points):
        prev_point = points[index - 1]
        next_point = points[(index + 1) % total]
        if (
            abs(prev_point[0] - point[0]) < tolerance
            and abs(point[0] - next_point[0]) < tolerance
        ) or (
            abs(prev_point[1] - point[1]) < tolerance
            and abs(point[1] - next_point[1]) < tolerance
        ):
            continue
        cleaned.append(point)

    return cleaned or points
