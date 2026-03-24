from __future__ import annotations

import time
from pathlib import Path

import cv2
import fitz
import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import OpeningType, Point2D, WallKind, WallSegment
from backend.app.parsers import (
    RasterTransform,
    _detect_wall_opening_candidate,
    parse_floorplan,
)


def _write_demo_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (80, 80), (920, 720), (0, 0, 0), 18)
    cv2.line(canvas, (500, 80), (500, 720), (0, 0, 0), 14)
    cv2.line(canvas, (80, 430), (500, 430), (0, 0, 0), 14)
    cv2.imwrite(str(path), canvas)


def _write_demo_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=1000, height=800)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(80, 80, 920, 720))
    shape.draw_line(fitz.Point(500, 80), fitz.Point(500, 720))
    shape.draw_line(fitz.Point(80, 430), fitz.Point(500, 430))
    shape.finish(color=(0, 0, 0), width=10)
    shape.commit()
    doc.save(path)
    doc.close()


def _write_demo_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (6, 0), (6, 4), (0, 4)],
        close=True,
    )
    msp.add_lwpolyline(
        [(6, 0), (12, 0), (12, 4), (6, 4)],
        close=True,
    )
    doc.saveas(path)


def test_parse_raster_sources_produce_floorplan(tmp_path: Path) -> None:
    png_path = tmp_path / "sample.png"
    jpg_path = tmp_path / "sample.jpg"
    pdf_path = tmp_path / "sample.pdf"
    _write_demo_image(png_path)
    _write_demo_image(jpg_path)
    _write_demo_pdf(pdf_path)

    for path in (png_path, jpg_path, pdf_path):
        spec = parse_floorplan(path)
        assert spec.rooms
        assert spec.outer_walls
        assert spec.inner_walls
        assert spec.openings
        assert spec.bounds_width_m > 0
        assert spec.bounds_depth_m > 0


def test_parse_dxf_produces_floorplan(tmp_path: Path) -> None:
    dxf_path = tmp_path / "sample.dxf"
    _write_demo_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert len(spec.rooms) >= 1
    assert spec.source_type.value == "dxf"
    assert spec.outer_walls
    assert spec.inner_walls
    assert any(abs(wall.start.x) < 0.01 and abs(wall.end.x) < 0.01 for wall in spec.inner_walls)


def test_api_job_flow_builds_scene_and_glb(tmp_path: Path) -> None:
    app = create_app(tmp_path / "runtime")
    client = TestClient(app)
    image_path = tmp_path / "upload.png"
    _write_demo_image(image_path)

    with image_path.open("rb") as file_handle:
        response = client.post(
            "/api/floorplans:generate",
            files={"file": ("upload.png", file_handle, "image/png")},
        )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    payload = None
    for _ in range(20):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)

    assert payload is not None
    assert payload["status"] == "completed", payload
    scene_id = payload["scene_id"]

    scene_response = client.get(f"/api/scenes/{scene_id}")
    assert scene_response.status_code == 200
    scene_payload = scene_response.json()
    assert scene_payload["rooms"]
    assert scene_payload["walls"]
    assert scene_payload["openings"]

    model_response = client.get(f"/api/scenes/{scene_id}/model.glb")
    assert model_response.status_code == 200
    assert model_response.content[:4] == b"glTF"


def test_detect_wall_opening_candidate_prefers_offset_door_gap() -> None:
    dark_mask = np.ones((200, 200), dtype=np.uint8)
    dark_mask[94:106, 20:180] = 1
    dark_mask[94:106, 45:75] = 0
    wall = WallSegment(
        wall_id="wall_inner",
        start=Point2D(x=-4.0, z=0.0),
        end=Point2D(x=4.0, z=0.0),
        kind=WallKind.INNER,
    )
    transform = RasterTransform(scale_m_per_px=0.05, center_x_px=100, center_z_px=100)

    opening = _detect_wall_opening_candidate(
        wall,
        dark_mask,
        transform,
        wall_thickness_m=0.2,
        kind=OpeningType.DOOR,
    )

    assert opening is not None
    assert opening.kind == OpeningType.DOOR
    assert -2.3 <= opening.center.x <= -1.7
    assert abs(opening.center.x) > 1.5
    assert 0.9 <= opening.width_m <= 1.2


def test_detect_wall_opening_candidate_prefers_offset_window_gap() -> None:
    dark_mask = np.ones((220, 220), dtype=np.uint8)
    dark_mask[20:200, 104:116] = 1
    dark_mask[130:176, 104:116] = 0
    wall = WallSegment(
        wall_id="wall_outer",
        start=Point2D(x=0.0, z=-4.5),
        end=Point2D(x=0.0, z=4.5),
        kind=WallKind.OUTER,
    )
    transform = RasterTransform(scale_m_per_px=0.05, center_x_px=110, center_z_px=110)

    opening = _detect_wall_opening_candidate(
        wall,
        dark_mask,
        transform,
        wall_thickness_m=0.2,
        kind=OpeningType.WINDOW,
    )

    assert opening is not None
    assert opening.kind == OpeningType.WINDOW
    assert 1.8 <= opening.center.z <= 2.4
    assert abs(opening.center.z) > 1.5
    assert 1.6 <= opening.width_m <= 2.1
