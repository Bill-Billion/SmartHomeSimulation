from __future__ import annotations

from pathlib import Path

import ezdxf

from backend.app.models import RoomType
from backend.app.parsers import parse_floorplan


def _write_line_split_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for start, end in [
        ((0, 0), (12, 0)),
        ((12, 0), (12, 8)),
        ((12, 8), (0, 8)),
        ((0, 8), (0, 0)),
        ((6, 0), (6, 8)),
    ]:
        msp.add_line(start, end)
    doc.saveas(path)


def _write_line_l_shape_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for start, end in [
        ((0, 0), (8, 0)),
        ((8, 0), (8, 3)),
        ((8, 3), (5, 3)),
        ((5, 3), (5, 8)),
        ((5, 8), (0, 8)),
        ((0, 8), (0, 0)),
    ]:
        msp.add_line(start, end)
    doc.saveas(path)


def _write_closed_polyline_l_shape_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (8, 0), (8, 3), (5, 3), (5, 8), (0, 8)], close=True)
    doc.saveas(path)


def _write_mixed_open_polyline_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (12, 0), (12, 8)], close=False)
    msp.add_line((12, 8), (0, 8))
    msp.add_line((0, 8), (0, 0))
    msp.add_line((6, 0), (6, 8))
    doc.saveas(path)


def _write_line_based_semantic_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for start, end in [
        ((0, 0), (12, 0)),
        ((12, 0), (12, 8)),
        ((12, 8), (0, 8)),
        ((0, 8), (0, 0)),
    ]:
        msp.add_line(start, end)
    label = msp.add_text("LIVING", dxfattribs={"height": 0.7})
    label.dxf.insert = (3.0, 4.0)
    doc.saveas(path)


def _write_degraded_line_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (12, 0))
    msp.add_line((12, 0), (12, 8))
    msp.add_line((12, 8), (0, 8))
    msp.add_line((0, 8), (1.2, 0))
    msp.add_arc(center=(6, 4), radius=1.0, start_angle=0, end_angle=90)
    doc.saveas(path)


def _write_closed_polyline_with_noise_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (12, 0), (12, 8), (0, 8)], close=True)
    msp.add_lwpolyline([(1.0, 1.0), (1.4, 1.0), (1.4, 1.4), (1.0, 1.4)], close=True)
    doc.saveas(path)


def test_line_split_dxf_recovers_two_rooms_and_inner_wall(tmp_path: Path) -> None:
    dxf_path = tmp_path / "line_split.dxf"
    _write_line_split_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) == 2
    assert any(room.room_type == RoomType.LIVING_ROOM for room in spec.rooms)
    assert spec.inner_walls
    assert not any("保守轮廓" in warning for warning in spec.warnings)


def test_line_l_shape_dxf_preserves_non_rectangular_polygon(tmp_path: Path) -> None:
    dxf_path = tmp_path / "line_l_shape.dxf"
    _write_line_l_shape_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) == 1
    assert len(spec.rooms[0].polygon) == 6
    assert spec.rooms[0].area_sqm == 49.0


def test_closed_polyline_l_shape_keeps_original_concave_outline(tmp_path: Path) -> None:
    dxf_path = tmp_path / "closed_polyline_l_shape.dxf"
    _write_closed_polyline_l_shape_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)
    polygon = [(point.x, point.z) for point in spec.rooms[0].polygon]

    assert len(spec.rooms) == 1
    assert len(polygon) == 6
    assert (1.0, -1.0) in polygon
    assert spec.rooms[0].area_sqm == 49.0


def test_mixed_open_polyline_and_line_still_recovers_rooms(tmp_path: Path) -> None:
    dxf_path = tmp_path / "mixed_open_polyline.dxf"
    _write_mixed_open_polyline_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) == 2
    assert spec.inner_walls
    assert not any("保守轮廓" in warning for warning in spec.warnings)


def test_line_based_dxf_text_keeps_semantic_assignment_after_geometry_recovery(tmp_path: Path) -> None:
    dxf_path = tmp_path / "line_semantic.dxf"
    _write_line_based_semantic_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) == 1
    assert spec.rooms[0].room_type == RoomType.LIVING_ROOM
    assert spec.rooms[0].name.startswith("客厅")


def test_invalid_linework_falls_back_with_natural_language_warnings(tmp_path: Path) -> None:
    dxf_path = tmp_path / "degraded_linework.dxf"
    _write_degraded_line_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert spec.rooms
    assert len(spec.rooms) == 1
    assert any("保守轮廓" in warning for warning in spec.warnings)
    assert any("当前 DXF 仅支持正交线段" in warning for warning in spec.warnings)


def test_small_closed_polyline_noise_is_not_promoted_to_room(tmp_path: Path) -> None:
    dxf_path = tmp_path / "closed_polyline_noise.dxf"
    _write_closed_polyline_with_noise_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) == 1
    assert spec.rooms[0].area_sqm == 96.0
