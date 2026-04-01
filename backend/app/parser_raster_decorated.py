from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .domain_rules import merge_warnings


@dataclass(frozen=True)
class DecoratedRasterPlan:
    structural_walls: np.ndarray
    wall_thickness_px: int
    room_polygons_px: list[list[tuple[float, float]]]
    warnings: tuple[str, ...]


def classify_raster_style(image: np.ndarray) -> str:
    """轻量判断图片更像线稿还是彩色装修图，不引入模型分支。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    height, width = image.shape[:2]

    border = max(12, int(min(height, width) * 0.06))
    border_mask = np.zeros((height, width), dtype=np.uint8)
    border_mask[:border, :] = 1
    border_mask[-border:, :] = 1
    border_mask[:, :border] = 1
    border_mask[:, -border:] = 1
    border_dark_ratio = float(np.count_nonzero((value < 185) & border_mask.astype(bool))) / max(
        float(np.count_nonzero(border_mask)),
        1.0,
    )

    saturation_mean = float(np.mean(saturation))
    saturation_p75 = float(np.percentile(saturation, 75))
    dark_ratio = float(np.count_nonzero(value < 215)) / float(value.size)
    brown_ratio = _brown_component_area_ratio(hsv)

    decorated = (
        saturation_p75 >= 28.0
        and saturation_mean >= 18.0
        and dark_ratio >= 0.04
        and (brown_ratio >= 0.08 or border_dark_ratio >= 0.01)
    )
    return "decorated" if decorated else "line_art"


def extract_decorated_plan(
    image: np.ndarray,
    *,
    structural_wall_extractor,
    room_polygon_extractor,
    legacy_polygon_extractor,
    polygon_quality_scorer,
) -> DecoratedRasterPlan:
    """彩色装修图专用解析路径，优先裁掉边缘标注，再用更强闭合恢复房间。"""
    roi = _detect_plan_roi(image)
    cropped = image[roi[1] : roi[3], roi[0] : roi[2]]
    flattened = _flatten_decorated_image(cropped)
    variants = (
        ("cropped", cropped),
        ("flattened", flattened),
    )

    candidates: list[tuple[float, list[list[tuple[float, float]]], list[str], np.ndarray, int]] = []
    default_structural_walls = np.zeros(image.shape[:2], dtype=np.uint8)
    default_wall_thickness = 10

    for _variant_name, prepared_image in variants:
        wall_analysis = structural_wall_extractor(prepared_image)
        full_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        full_mask[roi[1] : roi[3], roi[0] : roi[2]] = wall_analysis.structural_walls
        default_structural_walls = full_mask
        default_wall_thickness = wall_analysis.wall_thickness_px

        for close_multiplier in (4, 5, 6, 7, 8, 9):
            polygons, warnings = room_polygon_extractor(
                wall_analysis.structural_walls,
                wall_analysis.wall_thickness_px,
                close_multiplier=close_multiplier,
            )
            adjusted = [_offset_polygon(polygon, roi[0], roi[1]) for polygon in polygons]
            merged_warnings = merge_warnings(
                wall_analysis.warnings,
                warnings,
            )
            score = polygon_quality_scorer(
                adjusted,
                merged_warnings,
                image_shape=image.shape[:2],
            )
            candidates.append((score, adjusted, list(merged_warnings), full_mask, wall_analysis.wall_thickness_px))

        legacy_polygons, legacy_warnings = legacy_polygon_extractor(prepared_image)
        if legacy_polygons:
            adjusted_legacy = [_offset_polygon(polygon, roi[0], roi[1]) for polygon in legacy_polygons]
            merged_legacy_warnings = merge_warnings(
                wall_analysis.warnings,
                legacy_warnings,
            )
            candidates.append(
                (
                    polygon_quality_scorer(
                        adjusted_legacy,
                        merged_legacy_warnings,
                        image_shape=image.shape[:2],
                    ),
                    adjusted_legacy,
                    list(merged_legacy_warnings),
                    full_mask,
                    wall_analysis.wall_thickness_px,
                )
            )

    best_polygons: list[list[tuple[float, float]]] = []
    best_warnings: list[str] = []
    best_structural_walls = default_structural_walls
    best_wall_thickness = default_wall_thickness
    if candidates:
        _, best_polygons, best_warnings, best_structural_walls, best_wall_thickness = max(candidates, key=lambda item: item[0])

    if not best_polygons:
        best_warnings = merge_warnings(
            best_warnings,
            ["彩色装修图结构线不够稳定，已回退到保守轮廓。"],
        )

    return DecoratedRasterPlan(
        structural_walls=best_structural_walls,
        wall_thickness_px=best_wall_thickness,
        room_polygons_px=best_polygons,
        warnings=tuple(best_warnings),
    )


def _brown_component_area_ratio(hsv: np.ndarray) -> float:
    brown_mask = _brown_structural_mask(hsv)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(brown_mask, 8)
    covered = 0
    total = float(hsv.shape[0] * hsv.shape[1])
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 700 or width < 40 or height < 40:
            continue
        covered += area
    return covered / max(total, 1.0)


def _detect_plan_roi(image: np.ndarray) -> tuple[int, int, int, int]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    brown_mask = _brown_structural_mask(hsv)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(brown_mask, 8)
    boxes: list[tuple[int, int, int, int]] = []

    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 800 or width < 40 or height < 40:
            continue
        boxes.append((x, y, x + width, y + height))

    if not boxes:
        return 0, 0, image.shape[1], image.shape[0]

    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[2] for box in boxes)
    max_y = max(box[3] for box in boxes)
    padding = max(18, int(min(image.shape[:2]) * 0.02))
    return (
        max(0, min_x - padding),
        max(0, min_y - padding),
        min(image.shape[1], max_x + padding),
        min(image.shape[0], max_y + padding),
    )


def _flatten_decorated_image(image: np.ndarray) -> np.ndarray:
    """压平彩色纹理和家具细节，让后续结构墙提取更偏向粗边界。"""
    smoothed = cv2.bilateralFilter(image, 9, 45, 45)
    hsv = cv2.cvtColor(smoothed, cv2.COLOR_BGR2HSV)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 0.42, 0, 255).astype(np.uint8)
    hsv[:, :, 2] = cv2.medianBlur(hsv[:, :, 2], 5)
    flattened = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return cv2.edgePreservingFilter(flattened, flags=1, sigma_s=45, sigma_r=0.3)


def _brown_structural_mask(hsv: np.ndarray) -> np.ndarray:
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    return (((hue > 4) & (hue < 28) & (saturation > 18) & (value < 230))).astype(np.uint8) * 255


def _offset_polygon(
    polygon: list[tuple[float, float]],
    offset_x: int,
    offset_y: int,
) -> list[tuple[float, float]]:
    return [
        (point_x + offset_x, point_y + offset_y)
        for point_x, point_y in polygon
    ]
