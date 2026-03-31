from __future__ import annotations

from pathlib import Path

from .floorplan_builder import AxisEdge, _build_floorplan_from_polygons, _normalize_polygons
from .parser_dxf import _parse_dxf_floorplan as _parse_dxf_floorplan_impl
from .parser_raster import (
    RasterTransform,
    _detect_wall_opening_candidate,
    _parse_raster_floorplan_with_options,
    _room_polygon_quality_score,
    _run_tesseract_semantic_scan as _default_run_tesseract_semantic_scan,
)
from .models import WallKind
from . import parser_raster as parser_raster_module
from .semantic_rules import _normalize_room_semantic_text


class UnsupportedFormatError(ValueError):
    """当前阶段不支持的格式。"""


_run_tesseract_semantic_scan = _default_run_tesseract_semantic_scan


def parse_floorplan(source_path: Path):
    suffix = source_path.suffix.lower()
    if suffix == ".dwg":
        raise UnsupportedFormatError("首期暂不支持 DWG，请先转换为 DXF 或 PDF。")
    if suffix == ".dxf":
        return _parse_dxf_floorplan(source_path)
    if suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
        return _parse_raster_floorplan(source_path)
    raise UnsupportedFormatError(f"暂不支持 {suffix} 文件。")


def _extract_raster_semantic_hints(*args, **kwargs):
    if "scan_func" not in kwargs:
        kwargs["scan_func"] = _run_tesseract_semantic_scan
    return parser_raster_module._extract_raster_semantic_hints(
        *args,
        **kwargs,
    )


def _parse_raster_floorplan(source_path: Path):
    return _parse_raster_floorplan_with_options(
        source_path,
        scan_func=_run_tesseract_semantic_scan,
    )


def _parse_dxf_floorplan(source_path: Path):
    return _parse_dxf_floorplan_impl(source_path)


def _classify_fragment_kind(*args):
    if len(args) == 4:
        from .floorplan_builder import _classify_fragment_kind as _classify_fragment_kind_impl

        return _classify_fragment_kind_impl(*args)

    if len(args) == 3:
        edge, bounds, tolerance = args
        try:
            min_x, max_x, min_z, max_z = bounds
        except (TypeError, ValueError) as exc:
            raise TypeError("旧签名需要传入 (edge, bounds, tolerance)。") from exc

        if edge.orientation == "vertical":
            if abs(edge.axis - min_x) <= tolerance or abs(edge.axis - max_x) <= tolerance:
                return WallKind.OUTER
            return WallKind.INNER
        if abs(edge.axis - min_z) <= tolerance or abs(edge.axis - max_z) <= tolerance:
            return WallKind.OUTER
        return WallKind.INNER

    raise TypeError("_classify_fragment_kind 只支持 3 参旧签名或 4 参新签名。")


__all__ = [
    "AxisEdge",
    "RasterTransform",
    "UnsupportedFormatError",
    "parse_floorplan",
    "_build_floorplan_from_polygons",
    "_classify_fragment_kind",
    "_detect_wall_opening_candidate",
    "_extract_raster_semantic_hints",
    "_normalize_room_semantic_text",
    "_normalize_polygons",
    "_parse_dxf_floorplan",
    "_parse_raster_floorplan",
    "_room_polygon_quality_score",
    "_run_tesseract_semantic_scan",
]
