from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import fitz
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

import backend.app.llm_enhancer as llm_enhancer
import backend.app.parsers as parsers
from backend.app.main import create_app
from backend.app.models import (
    CameraSpec,
    FloorplanSpec,
    OpeningSpec,
    OpeningType,
    Point2D,
    Point3D,
    RoomSpec,
    RoomType,
    SourceType,
    WallKind,
    WallSegment,
)
from backend.app.parsers import (
    AxisEdge,
    RasterTransform,
    _classify_fragment_kind,
    _detect_wall_opening_candidate,
    _extract_raster_semantic_hints,
    _normalize_room_semantic_text,
    _room_polygon_quality_score,
    parse_floorplan,
)
from backend.app.scene_builder import _collect_cutaway_wall_ids, build_scene_spec, export_scene_glb


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


def _write_l_shaped_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (80, 80), (920, 720), (0, 0, 0), 18)
    cv2.line(canvas, (620, 80), (620, 430), (0, 0, 0), 14)
    cv2.line(canvas, (620, 430), (920, 430), (0, 0, 0), 14)
    cv2.imwrite(str(path), canvas)


def _write_annotated_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (80, 80), (920, 720), (0, 0, 0), 18)
    cv2.line(canvas, (500, 80), (500, 720), (0, 0, 0), 14)
    cv2.line(canvas, (80, 430), (500, 430), (0, 0, 0), 14)
    cv2.putText(canvas, "4500", (180, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
    cv2.putText(canvas, "KITCHEN", (530, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.line(canvas, (120, 60), (460, 60), (0, 0, 0), 2)
    cv2.line(canvas, (120, 56), (120, 64), (0, 0, 0), 2)
    cv2.line(canvas, (460, 56), (460, 64), (0, 0, 0), 2)
    cv2.imwrite(str(path), canvas)


def _write_low_quality_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 252, dtype=np.uint8)
    cv2.line(canvas, (120, 160), (880, 160), (225, 225, 225), 3)
    cv2.line(canvas, (120, 160), (120, 660), (225, 225, 225), 3)
    cv2.line(canvas, (880, 260), (880, 660), (228, 228, 228), 3)
    cv2.putText(canvas, "scan", (360, 410), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (230, 230, 230), 2)
    noise = np.random.default_rng(12).integers(0, 12, size=canvas.shape, dtype=np.uint8)
    canvas = np.clip(canvas - noise, 0, 255)
    canvas = cv2.GaussianBlur(canvas, (9, 9), 0)
    cv2.imwrite(str(path), canvas)


def _write_semantic_label_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (80, 80), (920, 720), (0, 0, 0), 18)
    cv2.line(canvas, (380, 80), (380, 720), (0, 0, 0), 14)
    cv2.putText(canvas, "BATHROOM", (110, 410), cv2.FONT_HERSHEY_SIMPLEX, 1.25, (0, 0, 0), 3)
    cv2.putText(canvas, "KITCHEN", (470, 410), cv2.FONT_HERSHEY_SIMPLEX, 1.45, (0, 0, 0), 4)
    cv2.imwrite(str(path), canvas)


def _write_corridor_image(path: Path) -> None:
    canvas = np.full((800, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(canvas, (80, 80), (920, 720), (0, 0, 0), 18)
    cv2.line(canvas, (80, 520), (920, 520), (0, 0, 0), 14)
    cv2.line(canvas, (350, 80), (350, 520), (0, 0, 0), 14)
    cv2.line(canvas, (650, 80), (650, 520), (0, 0, 0), 14)
    cv2.imwrite(str(path), canvas)


def _write_conflict_label_image(path: Path) -> None:
    _write_corridor_image(path)
    canvas = cv2.imread(str(path))
    cv2.putText(canvas, "KITCHEN", (340, 655), cv2.FONT_HERSHEY_SIMPLEX, 1.45, (0, 0, 0), 4)
    cv2.imwrite(str(path), canvas)


def _write_semantic_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (6, 0), (6, 4), (0, 4)], close=True)
    msp.add_lwpolyline([(6, 0), (12, 0), (12, 4), (6, 4)], close=True)
    living = msp.add_text("LIVING", dxfattribs={"height": 0.5})
    living.dxf.insert = (1.6, 2.0)
    bedroom = msp.add_mtext("BEDROOM", dxfattribs={"char_height": 0.5})
    bedroom.set_location((7.2, 2.0))
    doc.saveas(path)


def _write_line_based_dxf_with_text(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (12, 0))
    msp.add_line((12, 0), (12, 8))
    msp.add_line((12, 8), (0, 8))
    msp.add_line((0, 8), (0, 0))
    label = msp.add_text("LIVING", dxfattribs={"height": 0.7})
    label.dxf.insert = (3.0, 4.0)
    doc.saveas(path)


def _make_single_room_floorplan(
    room_type: RoomType,
    openings: list[OpeningSpec],
    *,
    polygon: list[Point2D] | None = None,
) -> FloorplanSpec:
    room_polygon = polygon or [
        Point2D(x=-2.0, z=-2.0),
        Point2D(x=2.0, z=-2.0),
        Point2D(x=2.0, z=2.0),
        Point2D(x=-2.0, z=2.0),
    ]
    room = RoomSpec(
        room_id="room_single",
        name="卧室 1" if room_type == RoomType.BEDROOM else "通用房间 1",
        room_type=room_type,
        polygon=room_polygon,
        area_sqm=16.0,
        confidence=0.72,
    )
    outer_walls = [
        WallSegment(wall_id="wall_north", start=room_polygon[0], end=room_polygon[1], kind=WallKind.OUTER),
        WallSegment(wall_id="wall_east", start=room_polygon[1], end=room_polygon[2], kind=WallKind.OUTER),
        WallSegment(wall_id="wall_south", start=room_polygon[2], end=room_polygon[3], kind=WallKind.OUTER),
        WallSegment(wall_id="wall_west", start=room_polygon[3], end=room_polygon[0], kind=WallKind.OUTER),
    ]
    return FloorplanSpec(
        source_type=SourceType.PNG,
        bounds_width_m=4.0,
        bounds_depth_m=4.0,
        scale_m_per_unit=1.0,
        outer_walls=outer_walls,
        inner_walls=[],
        openings=openings,
        rooms=[room],
        confidence=0.72,
        warnings=[],
    )


def _ocr_runtime_available() -> bool:
    try:
        import pytesseract
    except ImportError:
        return False

    try:
        languages = set(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001
        return False
    return {"eng", "chi_sim"}.issubset(languages)


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


def test_parse_l_shaped_raster_preserves_non_rectangular_room(tmp_path: Path) -> None:
    image_path = tmp_path / "l_shaped.png"
    _write_l_shaped_image(image_path)

    spec = parse_floorplan(image_path)

    assert len(spec.rooms) == 2
    assert any(len(room.polygon) > 4 for room in spec.rooms)
    assert spec.outer_walls
    assert spec.inner_walls


def test_l_shaped_floorplan_builds_scene_and_glb(tmp_path: Path) -> None:
    image_path = tmp_path / "l_shaped_scene.png"
    _write_l_shaped_image(image_path)

    floorplan = parse_floorplan(image_path)
    scene = build_scene_spec("scene_l_shape", floorplan)
    glb_bytes = export_scene_glb(scene)

    assert any(len(room.polygon) > 4 for room in scene.rooms)
    assert glb_bytes[:4] == b"glTF"


def test_scene_export_contains_ceiling_and_opening_detail_nodes(tmp_path: Path) -> None:
    image_path = tmp_path / "scene_details.png"
    _write_demo_image(image_path)

    floorplan = parse_floorplan(image_path)
    scene = build_scene_spec("scene_detail_nodes", floorplan)
    glb_bytes = export_scene_glb(scene)
    loaded = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", force="scene")
    node_names = set(loaded.graph.nodes_geometry)

    assert any(name.startswith("ceiling_") for name in node_names)
    assert any("door_leaf" in name for name in node_names)
    assert any("_glass" in name for name in node_names)


def test_scene_builder_places_furniture_against_clear_wall() -> None:
    floorplan = _make_single_room_floorplan(
        RoomType.BEDROOM,
        openings=[
            OpeningSpec(
                opening_id="window_north",
                wall_id="wall_north",
                kind=OpeningType.WINDOW,
                center=Point2D(x=0.0, z=-2.0),
                width_m=1.2,
                base_height_m=0.9,
                top_height_m=2.0,
            )
        ],
    )

    scene = build_scene_spec("scene_wall_furniture", floorplan)
    bed = scene.furnitures[0]

    assert bed.kind == "bed"
    assert bed.position.x > 0.8
    assert abs(bed.position.z) < 0.6
    assert not any("回退为居中摆放" in warning for warning in scene.warnings)


def test_scene_builder_falls_back_to_center_when_no_clear_wall() -> None:
    floorplan = _make_single_room_floorplan(
        RoomType.GENERIC,
        openings=[
            OpeningSpec(
                opening_id="open_north",
                wall_id="wall_north",
                kind=OpeningType.WINDOW,
                center=Point2D(x=0.0, z=-2.0),
                width_m=1.2,
                base_height_m=0.9,
                top_height_m=2.0,
            ),
            OpeningSpec(
                opening_id="open_east",
                wall_id="wall_east",
                kind=OpeningType.DOOR,
                center=Point2D(x=2.0, z=0.0),
                width_m=1.1,
                base_height_m=0.0,
                top_height_m=2.1,
            ),
            OpeningSpec(
                opening_id="open_south",
                wall_id="wall_south",
                kind=OpeningType.WINDOW,
                center=Point2D(x=0.0, z=2.0),
                width_m=1.2,
                base_height_m=0.9,
                top_height_m=2.0,
            ),
            OpeningSpec(
                opening_id="open_west",
                wall_id="wall_west",
                kind=OpeningType.DOOR,
                center=Point2D(x=-2.0, z=0.0),
                width_m=1.1,
                base_height_m=0.0,
                top_height_m=2.1,
            ),
        ],
    )

    scene = build_scene_spec("scene_center_fallback", floorplan)
    storage = scene.furnitures[0]

    assert abs(storage.position.x) < 0.2
    assert abs(storage.position.z) < 0.2
    assert any("回退为居中摆放" in warning for warning in scene.warnings)


def test_cutaway_wall_selection_is_camera_stable() -> None:
    walls = [
        WallSegment(wall_id="wall_north", start=Point2D(x=-2.0, z=-2.0), end=Point2D(x=2.0, z=-2.0), kind=WallKind.OUTER),
        WallSegment(wall_id="wall_east", start=Point2D(x=2.0, z=-2.0), end=Point2D(x=2.0, z=2.0), kind=WallKind.OUTER),
        WallSegment(wall_id="wall_south", start=Point2D(x=2.0, z=2.0), end=Point2D(x=-2.0, z=2.0), kind=WallKind.OUTER),
        WallSegment(wall_id="wall_west", start=Point2D(x=-2.0, z=2.0), end=Point2D(x=-2.0, z=-2.0), kind=WallKind.OUTER),
    ]
    camera = CameraSpec(
        position=Point3D(x=5.0, y=7.0, z=6.0),
        target=Point3D(x=0.0, y=0.8, z=0.0),
        fov=36.0,
    )

    first = _collect_cutaway_wall_ids(walls, camera)
    second = _collect_cutaway_wall_ids(walls, camera)

    assert first == second
    assert first == {"wall_east", "wall_south"}


def test_parse_annotated_raster_ignores_text_noise(tmp_path: Path) -> None:
    image_path = tmp_path / "annotated.png"
    _write_annotated_image(image_path)

    spec = parse_floorplan(image_path)

    assert len(spec.rooms) == 3
    assert spec.outer_walls
    assert spec.inner_walls
    assert not any("单房间壳体" in warning for warning in spec.warnings)


def test_parse_low_quality_raster_degrades_with_warning(tmp_path: Path) -> None:
    image_path = tmp_path / "low_quality.png"
    _write_low_quality_image(image_path)

    spec = parse_floorplan(image_path)

    assert spec.rooms
    assert spec.outer_walls
    assert spec.warnings
    assert any("保守" in warning or "退化" in warning for warning in spec.warnings)
    assert spec.confidence <= 0.52


@pytest.mark.skipif(not _ocr_runtime_available(), reason="本机缺少可用的 tesseract OCR 环境")
def test_parse_semantic_raster_uses_ocr_labels_for_kitchen_and_bathroom(tmp_path: Path) -> None:
    image_path = tmp_path / "semantic_labels.png"
    _write_semantic_label_image(image_path)

    spec = parse_floorplan(image_path)
    room_types = {room.room_type for room in spec.rooms}

    assert RoomType.KITCHEN in room_types
    assert RoomType.BATHROOM in room_types
    assert not any(room.room_type == RoomType.GENERIC for room in spec.rooms)


def test_parse_dxf_text_assigns_living_room_and_bedroom(tmp_path: Path) -> None:
    dxf_path = tmp_path / "semantic_text.dxf"
    _write_semantic_dxf(dxf_path)

    spec = parse_floorplan(dxf_path)
    room_types = {room.room_type for room in spec.rooms}

    assert RoomType.LIVING_ROOM in room_types
    assert RoomType.BEDROOM in room_types


def test_parse_line_based_dxf_with_text_keeps_semantic_path_alive(tmp_path: Path) -> None:
    dxf_path = tmp_path / "line_based_semantic.dxf"
    _write_line_based_dxf_with_text(dxf_path)

    spec = parse_floorplan(dxf_path)

    assert spec.rooms
    assert spec.rooms[0].room_type == RoomType.LIVING_ROOM
    assert spec.outer_walls


def test_parse_corridor_geometry_prefers_corridor_without_text(tmp_path: Path) -> None:
    image_path = tmp_path / "corridor.png"
    _write_corridor_image(image_path)

    spec = parse_floorplan(image_path)

    assert any(room.room_type == RoomType.CORRIDOR for room in spec.rooms)


@pytest.mark.skipif(not _ocr_runtime_available(), reason="本机缺少可用的 tesseract OCR 环境")
def test_parse_text_priority_overrides_corridor_geometry(tmp_path: Path) -> None:
    image_path = tmp_path / "corridor_kitchen.png"
    _write_conflict_label_image(image_path)

    spec = parse_floorplan(image_path)
    largest_room = max(spec.rooms, key=lambda room: room.area_sqm)

    assert largest_room.room_type == RoomType.KITCHEN
    assert largest_room.name.startswith("厨房")


def test_parse_raster_ocr_failure_falls_back_to_geometry_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "ocr_failure.png"
    _write_semantic_label_image(image_path)

    def _raise_ocr_failure(_image):
        raise RuntimeError("ocr boom")

    monkeypatch.setattr(parsers, "_run_tesseract_semantic_scan", _raise_ocr_failure)

    spec = parse_floorplan(image_path)

    assert spec.rooms
    assert any(room.room_type != RoomType.GENERIC for room in spec.rooms)
    assert any("房间文字识别失败" in warning for warning in spec.warnings)


def test_extract_raster_semantic_hints_warns_when_tesseract_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTesseractNotFoundError(RuntimeError):
        pass

    fake_module = SimpleNamespace(
        get_languages=lambda config="": (_ for _ in ()).throw(FakeTesseractNotFoundError("missing")),
        TesseractNotFoundError=FakeTesseractNotFoundError,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)

    hints, warnings = _extract_raster_semantic_hints(
        np.full((120, 120, 3), 255, dtype=np.uint8),
        RasterTransform(scale_m_per_px=0.05, center_x_px=60, center_z_px=60),
        np.zeros((120, 120), dtype=np.uint8),
        8,
    )

    assert hints == []
    assert warnings == ["系统未找到文字识别引擎，已回退到几何语义规则。"]


def test_extract_raster_semantic_hints_warns_when_languages_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = SimpleNamespace(
        get_languages=lambda config="": ["eng"],
        TesseractNotFoundError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)

    hints, warnings = _extract_raster_semantic_hints(
        np.full((120, 120, 3), 255, dtype=np.uint8),
        RasterTransform(scale_m_per_px=0.05, center_x_px=60, center_z_px=60),
        np.zeros((120, 120), dtype=np.uint8),
        8,
    )

    assert hints == []
    assert warnings == ["房间文字识别语言包不完整，已回退到几何语义规则。"]


def test_extract_raster_semantic_hints_warns_when_get_languages_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTesseractNotFoundError(RuntimeError):
        pass

    fake_module = SimpleNamespace(
        get_languages=lambda config="": (_ for _ in ()).throw(RuntimeError("boom")),
        TesseractNotFoundError=FakeTesseractNotFoundError,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)

    hints, warnings = _extract_raster_semantic_hints(
        np.full((120, 120, 3), 255, dtype=np.uint8),
        RasterTransform(scale_m_per_px=0.05, center_x_px=60, center_z_px=60),
        np.zeros((120, 120), dtype=np.uint8),
        8,
    )

    assert hints == []
    assert warnings == ["房间文字识别初始化失败，已回退到几何语义规则。"]


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


def test_classify_fragment_kind_keeps_concave_exterior_as_outer() -> None:
    room = RoomSpec(
        room_id="room_l",
        name="L 形房间",
        room_type=RoomType.GENERIC,
        polygon=[
            Point2D(x=0.0, z=0.0),
            Point2D(x=4.0, z=0.0),
            Point2D(x=4.0, z=2.0),
            Point2D(x=2.0, z=2.0),
            Point2D(x=2.0, z=4.0),
            Point2D(x=0.0, z=4.0),
        ],
        area_sqm=12.0,
        confidence=0.6,
    )
    edge = AxisEdge(
        edge_id="edge_concave_outer",
        room_id=room.room_id,
        orientation="vertical",
        axis=2.0,
        start=2.0,
        end=4.0,
    )

    kind = _classify_fragment_kind(edge, 2.0, 4.0, {room.room_id: room})

    assert kind == WallKind.OUTER


def test_room_polygon_quality_score_prefers_non_rectangular_room_over_degraded_box() -> None:
    adaptive_polygons = [
        [(0.0, 0.0), (800.0, 0.0), (800.0, 400.0), (400.0, 400.0), (400.0, 800.0), (0.0, 800.0)],
    ]
    fallback_polygons = [
        [(0.0, 0.0), (800.0, 0.0), (800.0, 800.0), (0.0, 800.0)],
    ]

    adaptive_score = _room_polygon_quality_score(adaptive_polygons, [])
    fallback_score = _room_polygon_quality_score(
        fallback_polygons,
        ["部分房间轮廓不够完整，已用保守轮廓继续生成。"],
    )

    assert adaptive_score > fallback_score


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("WC", RoomType.BATHROOM),
        ("toilet", RoomType.BATHROOM),
        ("洗手间", RoomType.BATHROOM),
        ("客厅", RoomType.LIVING_ROOM),
        ("KITCHEN", RoomType.KITCHEN),
        ("hallway", RoomType.CORRIDOR),
    ],
)
def test_normalize_room_semantic_text_aliases(raw_text: str, expected: RoomType) -> None:
    assert _normalize_room_semantic_text(raw_text) == expected


def test_llm_enhancement_disabled_keeps_floorplan_unchanged(tmp_path: Path) -> None:
    image_path = tmp_path / "llm_disabled.png"
    _write_demo_image(image_path)
    floorplan = parse_floorplan(image_path)
    draft_scene = build_scene_spec("scene_llm_disabled", floorplan)

    updated_floorplan, furniture_hints = llm_enhancer.apply_scene_llm_enhancements(
        image_path,
        floorplan,
        draft_scene,
        llm_enhancer.LlmRequestConfig(enabled=False),
    )

    assert updated_floorplan == floorplan
    assert furniture_hints == {}


def test_llm_enhancement_missing_config_falls_back_with_warning(tmp_path: Path) -> None:
    image_path = tmp_path / "llm_missing_config.png"
    _write_demo_image(image_path)
    floorplan = parse_floorplan(image_path)
    draft_scene = build_scene_spec("scene_llm_missing", floorplan)

    updated_floorplan, furniture_hints = llm_enhancer.apply_scene_llm_enhancements(
        image_path,
        floorplan,
        draft_scene,
        llm_enhancer.LlmRequestConfig(enabled=True, base_url="https://api.openai.com/v1"),
    )

    assert furniture_hints == {}
    assert any("AI 辅助配置不完整" in warning for warning in updated_floorplan.warnings)


def test_llm_enhancement_invalid_result_falls_back_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "llm_invalid.png"
    _write_demo_image(image_path)
    floorplan = parse_floorplan(image_path)
    draft_scene = build_scene_spec("scene_llm_invalid", floorplan)

    def _raise_invalid(_config, _evidence):
        raise ValueError("geometry override is not allowed")

    monkeypatch.setattr(llm_enhancer, "_request_llm_enhancement", _raise_invalid)

    updated_floorplan, furniture_hints = llm_enhancer.apply_scene_llm_enhancements(
        image_path,
        floorplan,
        draft_scene,
        llm_enhancer.LlmRequestConfig(
            enabled=True,
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            api_key="secret-key",
        ),
    )

    assert furniture_hints == {}
    assert updated_floorplan.rooms == floorplan.rooms
    assert any("AI 辅助处理异常" in warning for warning in updated_floorplan.warnings)


def test_llm_enhancement_can_update_room_type_and_furniture_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "llm_semantic.png"
    _write_demo_image(image_path)
    floorplan = _make_single_room_floorplan(RoomType.GENERIC, openings=[])
    draft_scene = build_scene_spec("scene_llm_semantic", floorplan)

    monkeypatch.setattr(
        llm_enhancer,
        "_request_llm_enhancement",
        lambda _config, _evidence: llm_enhancer.LlmEnhancementResult(
            room_overrides=[
                llm_enhancer.RoomOverride(
                    room_id="room_single",
                    room_type=RoomType.KITCHEN,
                    name="厨房 1",
                    confidence=0.88,
                )
            ],
            furniture_hints=[
                llm_enhancer.FurnitureHint(
                    room_id="room_single",
                    wall_preference="east",
                    confidence=0.78,
                )
            ],
            extra_warnings=["AI 把这个房间更像厨房处理。"],
        ),
    )

    updated_floorplan, furniture_hints = llm_enhancer.apply_scene_llm_enhancements(
        image_path,
        floorplan,
        draft_scene,
        llm_enhancer.LlmRequestConfig(
            enabled=True,
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
            api_key="secret-key",
        ),
    )
    scene = build_scene_spec("scene_llm_semantic_updated", updated_floorplan, furniture_hints=furniture_hints)

    assert updated_floorplan.rooms[0].room_type == RoomType.KITCHEN
    assert updated_floorplan.rooms[0].name == "厨房 1"
    assert updated_floorplan.rooms[0].confidence == 0.88
    assert furniture_hints == {"room_single": "east"}
    assert scene.furnitures[0].position.x > 0.8
    assert any("AI 辅助提示" in warning for warning in scene.warnings)


def test_api_job_flow_with_llm_does_not_persist_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime_llm"
    app = create_app(runtime_root)
    client = TestClient(app)
    image_path = tmp_path / "upload_llm.png"
    _write_demo_image(image_path)

    monkeypatch.setattr(
        llm_enhancer,
        "_request_llm_enhancement",
        lambda _config, _evidence: llm_enhancer.LlmEnhancementResult(
            extra_warnings=["AI 已检查过语义歧义。"],
        ),
    )

    with image_path.open("rb") as file_handle:
        response = client.post(
            "/api/floorplans:generate",
            files={"file": ("upload.png", file_handle, "image/png")},
            data={
                "llm_enabled": "true",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model": "gpt-4.1-mini",
                "llm_api_key": "top-secret-key",
            },
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

    job_text = (runtime_root / "jobs" / job_id / "job.json").read_text(encoding="utf-8")
    scene_text = (runtime_root / "scenes" / payload["scene_id"] / "scene.json").read_text(encoding="utf-8")

    assert "top-secret-key" not in job_text
    assert "top-secret-key" not in scene_text
    assert "llm_api_key" not in job_text
    assert "llm_api_key" not in scene_text
