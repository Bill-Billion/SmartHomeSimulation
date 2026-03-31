from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.parser_raster as parser_raster
from backend.app.floorplan_builder import _build_floorplan_from_polygons
from backend.app.models import FloorplanSpec, RoomType, SourceType
from backend.app.parsers import parse_floorplan


def _build_no_text_floorplan(polygons: list[list[tuple[float, float]]]) -> FloorplanSpec:
    return _build_floorplan_from_polygons(
        polygons,
        source_type=SourceType.PNG,
        bounds_width_m=32.0,
        bounds_depth_m=32.0,
        scale_m_per_unit=1.0,
        warnings=[],
    )


def _room_type_counts(spec: FloorplanSpec) -> dict[RoomType, int]:
    counts: dict[RoomType, int] = {room_type: 0 for room_type in RoomType}
    for room in spec.rooms:
        counts[room.room_type] += 1
    return counts


def test_no_text_layout_keeps_single_living_and_limits_generic() -> None:
    spec = _build_no_text_floorplan(
        [
            [(0, 0), (8, 0), (8, 6), (0, 6)],  # 客厅候选
            [(8, 0), (12, 0), (12, 3), (8, 3)],  # 厨房候选
            [(8, 3), (10, 3), (10, 6), (8, 6)],  # 卫生间候选
            [(10, 3), (12, 3), (12, 6), (10, 6)],  # 卧室候选
            [(0, 6), (4, 6), (4, 10), (0, 10)],  # 卧室候选
            [(4, 6), (8, 6), (8, 10), (4, 10)],  # 卧室候选
        ]
    )
    counts = _room_type_counts(spec)

    assert counts[RoomType.LIVING_ROOM] == 1
    assert counts[RoomType.KITCHEN] <= 1
    assert counts[RoomType.BATHROOM] <= 2
    assert counts[RoomType.GENERIC] <= 1


def test_no_text_layout_keeps_corridor_role_without_text() -> None:
    spec = _build_no_text_floorplan(
        [
            [(0, 2), (12, 2), (12, 3), (0, 3)],  # 走廊候选
            [(0, 0), (4, 0), (4, 2), (0, 2)],
            [(4, 0), (8, 0), (8, 2), (4, 2)],
            [(8, 0), (12, 0), (12, 2), (8, 2)],
            [(0, 3), (6, 3), (6, 7), (0, 7)],
            [(6, 3), (9, 3), (9, 5), (6, 5)],
            [(9, 3), (12, 3), (12, 7), (9, 7)],
        ]
    )
    counts = _room_type_counts(spec)

    assert counts[RoomType.CORRIDOR] == 1
    assert counts[RoomType.LIVING_ROOM] == 1
    assert counts[RoomType.KITCHEN] <= 1
    assert counts[RoomType.GENERIC] <= 1


def test_no_text_layout_limits_bathroom_capacity_to_two() -> None:
    spec = _build_no_text_floorplan(
        [
            [(0, 0), (7, 0), (7, 5), (0, 5)],  # 客厅候选
            [(7, 1), (8.2, 1), (8.2, 6.5), (7, 6.5)],  # 走廊候选
            [(0, 5), (3.2, 5), (3.2, 8.5), (0, 8.5)],  # 厨房候选
            [(3.2, 5), (7, 5), (7, 8.5), (3.2, 8.5)],  # 卧室候选
            [(8.2, 1), (10.0, 1), (10.0, 3.2), (8.2, 3.2)],  # 卫生间候选
            [(8.2, 3.2), (10.0, 3.2), (10.0, 5.4), (8.2, 5.4)],  # 卫生间候选
            [(8.2, 5.4), (10.0, 5.4), (10.0, 6.5), (8.2, 6.5)],  # 小房间，避免第三卫生间
        ]
    )
    counts = _room_type_counts(spec)

    assert counts[RoomType.BATHROOM] <= 2
    assert counts[RoomType.LIVING_ROOM] == 1
    assert counts[RoomType.KITCHEN] <= 1


def test_decorated_fixture_a_without_ocr_keeps_semantic_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parser_raster,
        "_extract_raster_semantic_hints",
        lambda *_args, **_kwargs: ([], []),
    )
    fixture = Path(__file__).resolve().parent / "fixtures" / "decorated_plan_a.png"
    spec = parse_floorplan(fixture)
    counts = _room_type_counts(spec)

    assert counts[RoomType.LIVING_ROOM] == 1
    assert counts[RoomType.KITCHEN] <= 1
    assert counts[RoomType.CORRIDOR] <= 1
    assert counts[RoomType.BATHROOM] <= 2
    assert counts[RoomType.GENERIC] <= 1


def test_decorated_fixture_b_without_ocr_keeps_semantic_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parser_raster,
        "_extract_raster_semantic_hints",
        lambda *_args, **_kwargs: ([], []),
    )
    fixture = Path(__file__).resolve().parent / "fixtures" / "decorated_plan_b.png"
    spec = parse_floorplan(fixture)
    counts = _room_type_counts(spec)

    assert counts[RoomType.LIVING_ROOM] == 1
    assert counts[RoomType.KITCHEN] <= 1
    assert counts[RoomType.CORRIDOR] <= 1
    assert counts[RoomType.BATHROOM] <= 2
    assert counts[RoomType.GENERIC] <= 1
