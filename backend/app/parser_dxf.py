from __future__ import annotations

from pathlib import Path

from .domain_rules import merge_warnings
from .dxf_linework import recover_dxf_room_polygons
from .floorplan_builder import (
    _bbox_from_points,
    _build_floorplan_from_polygons,
    _center_world_polygons,
    _detect_dxf_unit_factor,
    _rect_to_polygon,
)
from .models import FloorplanSpec, SourceType
from .semantic_rules import RoomSemanticHint, _normalize_room_semantic_text


def _parse_dxf_floorplan(source_path: Path) -> FloorplanSpec:
    import ezdxf

    doc = ezdxf.readfile(source_path)
    msp = doc.modelspace()
    all_points: list[tuple[float, float]] = []
    raw_closed_polygons: list[list[tuple[float, float]]] = []
    raw_open_polylines: list[list[tuple[float, float]]] = []
    raw_line_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    raw_texts: list[tuple[float, float, str]] = []
    ignored_curves = False

    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype == "LWPOLYLINE":
            points = [(point[0], point[1]) for point in entity.get_points()]
            all_points.extend(points)
            if entity.closed:
                raw_closed_polygons.append(points)
            else:
                raw_open_polylines.append(points)
        elif dxftype == "POLYLINE":
            points = [(vertex.dxf.location.x, vertex.dxf.location.y) for vertex in entity.vertices]
            all_points.extend(points)
            if entity.is_closed:
                raw_closed_polygons.append(points)
            else:
                raw_open_polylines.append(points)
        elif dxftype == "LINE":
            start = (entity.dxf.start.x, entity.dxf.start.y)
            end = (entity.dxf.end.x, entity.dxf.end.y)
            all_points.extend([start, end])
            raw_line_segments.append((start, end))
        elif dxftype in {"TEXT", "MTEXT", "ATTRIB"}:
            text_anchor = _extract_dxf_text_anchor(entity)
            if text_anchor is not None:
                raw_texts.append(text_anchor)
        elif dxftype in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}:
            ignored_curves = True

    if not all_points:
        raise ValueError("DXF 中没有可识别的线段或轮廓。")

    unit_factor = _detect_dxf_unit_factor(doc.units)
    scaled_points = [
        (point_x * unit_factor, point_z * unit_factor)
        for point_x, point_z in all_points
    ]
    closed_polygons = [
        [(point_x * unit_factor, point_z * unit_factor) for point_x, point_z in polygon]
        for polygon in raw_closed_polygons
    ]
    open_polylines = [
        [(point_x * unit_factor, point_z * unit_factor) for point_x, point_z in polyline]
        for polyline in raw_open_polylines
    ]
    line_segments = [
        (
            (start[0] * unit_factor, start[1] * unit_factor),
            (end[0] * unit_factor, end[1] * unit_factor),
        )
        for start, end in raw_line_segments
    ]

    warnings: list[str] = []
    if doc.units not in {4, 5, 6}:
        warnings.append("DXF 未声明单位，已按启发式比例缩放。")
    if ignored_curves:
        warnings.append("当前 DXF 仅支持正交线段，已忽略曲线或斜线图元。")

    polygons, linework_warnings = recover_dxf_room_polygons(
        closed_polygons,
        open_polylines,
        line_segments,
    )
    warnings = merge_warnings(warnings, linework_warnings)
    if not polygons:
        polygons = [_rect_to_polygon(_bbox_from_points(scaled_points))]
    world_polygons, width_m, depth_m, center_x, center_z = _center_world_polygons(polygons)
    semantic_hints = _extract_dxf_semantic_hints(raw_texts, unit_factor, center_x, center_z)
    return _build_floorplan_from_polygons(
        world_polygons,
        source_type=SourceType.DXF,
        bounds_width_m=width_m,
        bounds_depth_m=depth_m,
        scale_m_per_unit=unit_factor,
        warnings=warnings,
        semantic_hints=semantic_hints,
    )


def _extract_dxf_text_anchor(entity) -> tuple[float, float, str] | None:
    """提取 DXF 文本锚点，统一交给房间语义层做归一。"""
    dxftype = entity.dxftype()
    try:
        if dxftype == "TEXT":
            raw_text = str(entity.dxf.text or "").strip()
            insert = entity.dxf.insert
        elif dxftype == "ATTRIB":
            raw_text = str(entity.dxf.text or "").strip()
            insert = entity.dxf.insert
        elif dxftype == "MTEXT":
            raw_text = str(entity.plain_text() or "").strip()
            insert = entity.dxf.insert
        else:
            return None
    except AttributeError:
        return None

    if not raw_text:
        return None
    return float(insert.x), float(insert.y), raw_text


def _extract_dxf_semantic_hints(
    raw_texts: list[tuple[float, float, str]],
    unit_factor: float,
    center_x: float,
    center_z: float,
) -> list[RoomSemanticHint]:
    hints: list[RoomSemanticHint] = []
    for raw_x, raw_z, raw_text in raw_texts:
        room_type = _normalize_room_semantic_text(raw_text)
        if room_type is None:
            continue
        hints.append(
            RoomSemanticHint(
                room_type=room_type,
                center=(
                    round(raw_x * unit_factor - center_x, 3),
                    round(raw_z * unit_factor - center_z, 3),
                ),
                raw_text=raw_text,
                source="dxf_text",
            )
        )
    return hints
