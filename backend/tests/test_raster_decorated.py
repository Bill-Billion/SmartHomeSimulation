from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.parsers import parse_floorplan
from backend.app.parser_raster_decorated import DecoratedRasterPlan, classify_raster_style


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _max_room_area_ratio(spec) -> float:
    areas = [room.area_sqm for room in spec.rooms]
    if not areas:
        return 1.0
    return max(areas) / max(sum(areas), 1e-6)


@pytest.mark.parametrize(
    ("fixture_name", "min_rooms", "min_inner_walls", "max_room_ratio"),
    [
        ("decorated_plan_a.png", 5, 3, 0.55),
        ("decorated_plan_b.png", 6, 4, 0.55),
    ],
)
def test_decorated_plan_fixtures_recover_multiple_rooms(
    fixture_name: str,
    min_rooms: int,
    min_inner_walls: int,
    max_room_ratio: float,
) -> None:
    image_path = FIXTURE_ROOT / fixture_name
    image = cv2.imread(str(image_path))

    assert image is not None
    assert classify_raster_style(image) == "decorated"

    spec = parse_floorplan(image_path)

    assert len(spec.rooms) >= min_rooms
    assert len(spec.inner_walls) >= min_inner_walls
    assert _max_room_area_ratio(spec) < max_room_ratio
    assert not any("单房间壳体" in warning for warning in spec.warnings)


def test_decorated_branch_falls_back_with_natural_language_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.parser_raster as parser_raster_module

    image_path = tmp_path / "decorated_fallback.png"
    canvas = np.full((240, 320, 3), 245, dtype=np.uint8)
    cv2.rectangle(canvas, (40, 40), (280, 200), (80, 60, 35), 14)
    cv2.imwrite(str(image_path), canvas)

    monkeypatch.setattr(parser_raster_module, "classify_raster_style", lambda _image: "decorated")
    monkeypatch.setattr(
        parser_raster_module,
        "extract_decorated_plan",
        lambda _image, **_kwargs: DecoratedRasterPlan(
            structural_walls=np.zeros(_image.shape[:2], dtype=np.uint8),
            wall_thickness_px=10,
            room_polygons_px=[],
            warnings=("彩色装修图结构线不够稳定，已回退到保守轮廓。",),
        ),
    )

    spec = parse_floorplan(image_path)

    assert len(spec.rooms) == 1
    assert any("彩色装修图结构线不够稳定" in warning for warning in spec.warnings)
    assert any("单房间壳体" in warning for warning in spec.warnings)
